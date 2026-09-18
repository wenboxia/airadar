"""Stage 3: triage —— 信源分层评分 + 置信度路由（HITL 的核心）。

综合分 = tier_weight × tier基础分 + (1-tier_weight) × LLM 价值分
路由：>=publish_threshold 自动发布；中间区进人工审核；低于 review_threshold 丢弃（留审计日志）。

降级路径（无 LLM / 超预算）：只用 tier 基础分。S/A 发布、B/C 送审——
宁可多送审也不让低质内容自动发布（保守降级）。

**为什么拆成 evaluate / route / run 三段**（0.3.0）：离线评测和一次性重打分都要复用打分逻辑，
但它们绝不能写 status / auto_status（D28）。与其嘱咐"记得别写"，不如让它们拿到的函数
根本没有写状态的能力——同 D34：能从结构上拿掉的，就不要靠指令约束。
只有 run() 写状态。
"""
from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "triage", "version": "0.3.0",
    "input": "list[Item]", "output": "list[Item]（status: published/review/discarded）",
    "eval_cases": "evals/golden_set/golden.jsonl 的 include 标注 · evals/triage_prompt_eval.py",
}

# 打分标准的版本号。写进 extra.rescore 的结论只在同一版本下有效——
# 换了标准，旧结论必须自动失效，否则会拿过时的标准继续压着队列。
# 两处各写一份格式就会对不上（第一次就踩了：一处写 "0.3.0"、一处写 "triage-0.3.0"）
PROMPT_VERSION = f"triage-{MANIFEST['version']}"

# 打分标准就是黄金集的收录标准（D35）。以前这里只写四个领域名词，导致两件事：
# 主人会收的具身智能内容因为"领域不在清单里"被压到 51–59 分，而营销通稿照样进送审区。
_SYSTEM = """你是 AI 行业情报分析师，为一个只收录高价值内容的知识雷达做价值评估。
读者是 AI 产品经理，判断只有一把尺子：三个月后他还愿意在知识库里搜到这条吗？

关注方向（举例，不是白名单）：{focus}
没列到的 AI 技术或产业方向，只要符合下面的标准，同样给高分；
只有与 AI 技术、产业都无关的内容才给低分。**方向窄不是低分的理由。**

第一步，判断 kind，八选一：
- 一手发布：模型、产品、协议、工具的官方公告与工程博客，发布方自己写的
- 从业者判断：真正在做这件事的人公开讲的具体观点、实测、复盘
- 公司动态：收购、重大人事、算力投入、定价大改——会改变别人技术选型的那种
- 开源项目：有新做法、能直接拿来用或拿来学的（star 数不是依据）
- 论文：正在被实质讨论的工作，或实验室署名的技术报告 / System Card
- 二手转述：同一件事的第二、三次报道，信息全部来自别人的发布
- 营销通稿：厂商供稿、公关稿、活动招商、促销广告、获奖通知；
  关键特征是关键数字只有发布方单方说法、没有独立验证
- 仿造品：套壳、换皮，相对已有方案没有新做法

第二步，打三个分（0-100）：
- relevance 价值相关度：这条对读者的判断有多大帮助。
  注意"讲的是 AI"不等于相关——一篇只讲某公司又签了合作、又拿了奖的稿子，
  哪怕通篇都是 AI，也应低于 30
- novelty 新颖度：有没有别处看不到的东西——新方法、新数据、第一手细节。
  把已发布过的事再说一遍，20 以下
- longterm 长期价值：三个月后还值得回看吗。可复用的方法与踩坑 > 一次性快讯 >
  会过期的活动、促销和榜单名次

几条容易判错的：
- 来源权威不等于该收，官方账号也发营销稿
- 报道别人的发布时，如果补上了原文没有的方法、数据或上下文，算从业者判断，不算二手转述
- 无法核实的爆料、"知情人士"、未证实的 demo，novelty 再高，longterm 也要低"""

_USER = """标题：{title}
来源：{source}（信誉层级 {tier}）
正文节选：
{content}

输出 JSON：{{"kind": "八选一之一", "relevance": int, "novelty": int,
           "longterm": int, "reason": "一句话理由"}}"""


def _llm_value(item, cfg, llm):
    out = llm.json_chat(
        _SYSTEM.format(focus=cfg.focus),
        _USER.format(title=item.title, source=item.source, tier=item.tier,
                     content=(item.content or "（无正文，仅标题）")[:1500]),
        max_tokens=500)   # 推理模型要给思考留预算
    r, n, lt = (out.get("relevance"), out.get("novelty"), out.get("longterm"))
    if not all(isinstance(v, (int, float)) for v in (r, n, lt)):
        return None, "llm_json_invalid"
    wr, wn, wl = cfg.value_weights
    value = wr * r + wn * n + wl * lt
    kind = out.get("kind")
    item.score_detail.update({"relevance": r, "novelty": n, "longterm": lt,
                              "kind": kind, "llm_reason": out.get("reason", "")})
    # 营销通稿和仿造品要封顶，不是扣分：封顶 45 后 S 级最高 63 分、A 级 58.2 分，结构上到不了发布线；
    # 扣分会被 tier 地板和其他维度抵消，压不住高等级信源发的软文
    cap = cfg.kind_value_cap.get(kind)
    if cap is None and kind not in KINDS:
        item.notes.append("triage_kind_unknown")   # 模型偶尔乱答，不因此改变判断
    if cap is not None and value > cap:
        item.score_detail["value_capped_by"] = kind
        value = cap
    return value, None


KINDS = ("一手发布", "从业者判断", "公司动态", "开源项目", "论文",
         "二手转述", "营销通稿", "仿造品")


def evaluate(item, cfg, llm) -> str:
    """纯打分：只写 score / score_detail / notes，**不碰任何状态字段**。

    返回本条的处理结果（llm_scored / degraded），供调用方统计。
    离线评测与一次性重打分都只调它，因此结构上不可能写到 auto_status（D28）。
    """
    base = cfg.tier_base.get(item.tier, 30)
    item.score_detail["tier_base"] = base
    value = None
    if llm.available():
        try:
            value, err = _llm_value(item, cfg, llm)
            if err:
                item.notes.append(f"triage_degraded: {err}")
        except LLMError as e:
            item.notes.append(f"triage_degraded: {e}")
    if value is None:
        item.score = float(base)
        item.notes.append("triage_degraded: no_llm")
        return "degraded"
    item.score = round(cfg.tier_weight * base + (1 - cfg.tier_weight) * value, 1)
    item.score_detail["llm_value"] = round(value, 1)
    # 记下是哪一版标准打的分：抽查要只抽"现在这套标准"自动发布的，否则撤下率说不清在测谁
    item.score_detail["prompt"] = PROMPT_VERSION
    return "llm_scored"


def route(item, cfg, outcome: str) -> str:
    """纯路由：分数 → published / review / discarded。不写库，只返回结论。"""
    if outcome == "degraded":
        # 保守降级：只有 tier 分，S/A 放行，其余送人工审
        return "published" if item.tier in ("S", "A") else "review"
    if item.score >= cfg.publish_threshold:
        return "published"
    if item.score >= cfg.review_threshold:
        return "review"
    return "discarded"


def run(items: list, ctx: Context) -> list:
    """唯一允许写 status / auto_status 的地方。"""
    cfg = ctx.cfg
    counts = {"published": 0, "review": 0, "discarded": 0, "llm_scored": 0,
              "degraded": 0, "capped": 0}

    def _score_one(it):
        outcome = evaluate(it, cfg, ctx.llm)
        it.status = route(it, cfg, outcome)
        it.auto_status = it.status      # 冻结系统的自主判断，之后人工审批只改 status
        return outcome

    def _on_error(it, e):
        # 评分环节异常 → 保守处理为送人工审，绝不静默丢弃或放行
        it.score = float(cfg.tier_base.get(it.tier, 30))
        it.status = it.auto_status = "review"
        it.notes.append(f"triage_error: {type(e).__name__}")
        ctx.note_error("triage", f"{it.title[:40]}: {e}")

    outcomes = parallel_map(_score_one, items, workers=cfg.llm_workers,
                            on_error=_on_error)
    kinds = {}
    for it, outcome in zip(items, outcomes):
        counts[outcome or "degraded"] = counts.get(outcome or "degraded", 0) + 1
        counts[it.status] = counts.get(it.status, 0) + 1
        counts["capped"] += "value_capped_by" in it.score_detail
        k = it.score_detail.get("kind")
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    counts["by_kind"] = kinds      # 封顶生效多少、各类型占比，是调 kind_value_cap 的依据

    ctx.stats["triage"] = counts
    print(f"  triage: 发布 {counts.get('published', 0)} / 待审 {counts.get('review', 0)} / "
          f"丢弃 {counts.get('discarded', 0)}"
          f"（LLM 评分 {counts.get('llm_scored', 0)}，降级 {counts.get('degraded', 0)}，"
          f"价值分封顶 {counts['capped']}）")
    return items
