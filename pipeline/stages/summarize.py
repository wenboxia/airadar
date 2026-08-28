"""Stage 4: summarize —— 双层摘要 + 幻觉自检。

双层摘要（借鉴技术周刊的编辑方法论）：一句话（30秒判断要不要读）+ ~300字（3分钟掌握核心）。
幻觉自检：摘要生成后再让模型对照原文核验，发现无依据断言就打标（不拦发布，但留痕+计入评测指标）。

**信息不足不硬编**（decisions.md D14）：首次真实运行时，OpenAI 的 RSS 只给 150 字简介，
模型被要求写 300 字摘要 → 只能靠猜补足 → 产生了真实幻觉（把"让软件开发在企业内变得可及"
夸大成"全公司员工都能构建软件"）。因此原文过短时改用"简介模式"，只写一句话、不扩写长摘要。

降级路径：无 LLM 时仅标题+链接入库（notes 标记 no_summary），这就是"最终降级"。
"""
from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "summarize", "version": "0.2.0",
    "input": "list[Item]（published/review）", "output": "list[Item]（含双层摘要）",
    "eval_cases": "LLM-as-Judge 摘要忠实度（evals/run_eval.py）",
}

# 原文短于此长度就认定"信息不足"，走简介模式（实测 RSS-only 条目普遍 150-200 字）
THIN_CONTENT_CHARS = 400

# 三条铁律都是被真实幻觉打出来的（decisions.md D18）：
# ① 元数据不是内容——模型曾把信源名写成文章作者（"Simon Willison 引用了…"，原文根本没提他）
# ② 不许外推——"可能改变推理硬件格局"这类结论是模型自己加的
# ③ 不许补背景——模型曾从 HN 标题编出整段"CEO 解雇开发者"的故事
_RULES = """铁律（违反任意一条都算失败）：
1. 只依据【原文】部分写作。来源名、标题属于元数据，**不是原文内容**：
   绝不能把来源名当作文章的作者或发布方写进摘要（例如来源是某人的博客，不代表原文提到了这个人）。
2. 原文没有的信息一个字都不许补：不许推测背景故事、不许补充你记忆中的相关知识。
3. 不许外推结论。原文没下的judgment（"将改变格局""提供了新思路"）一律不许自己加。
   原文信息不足就写"原文未详述"，宁可短，不许编。"""

_SYS_SUM = f"""你是严谨的 AI 行业编辑，为中文读者写摘要。
{_RULES}
输出 JSON：
{{"one_liner": "一句话概括（≤40字，突出'什么东西+为什么值得看'）",
 "summary": "250-350字摘要（背景/核心内容/为什么重要，全部来自原文）",
 "key_points": ["要点1", "要点2", "要点3"]}}"""

_SYS_THIN = f"""你是严谨的 AI 行业编辑。**你拿到的只是一段简介，不是全文。**
{_RULES}
只能改写这段简介，绝对不许推测、扩写。
输出 JSON：{{"one_liner": "一句话概括（≤40字，忠实于简介）", "key_points": ["简介中明确提到的要点"]}}"""

_SYS_CHECK = """你是事实核查员。对照原文检查摘要是否有原文不支持的断言（编造数字、夸大结论、无中生有）。
输出 JSON：{"faithful": true/false, "problems": ["问题1（若有）"]}"""


def _summarize_one(it, ctx: Context) -> bool:
    thin = len(it.content or "") < THIN_CONTENT_CHARS
    if thin:
        out = ctx.llm.json_chat(
            _SYS_THIN,
            f"【元数据（仅供参考，不是文章内容）】标题：{it.title}｜来源：{it.source}\n"
            f"----------\n【简介】\n{it.content or '（无）'}",
            max_tokens=400)
        if not out.get("one_liner"):
            return False
        it.summary_short = str(out["one_liner"])[:80]
        it.summary_long = ""           # 故意留空：没有原文就不产出长摘要
        it.key_points = [str(p) for p in out.get("key_points", [])][:3]
        it.notes.append("summary_mode: brief（原文不可获取，仅据信源简介）")
        it.extra["summary_mode"] = "brief"
        return True

    # 元数据与原文用分隔线明确切开，降低模型把来源名当内容的概率
    out = ctx.llm.json_chat(
        _SYS_SUM,
        f"【元数据（仅供参考，不是文章内容）】标题：{it.title}｜来源：{it.source}\n"
        f"----------\n【原文】\n{it.content[:4000]}",
        max_tokens=800)
    if not out.get("one_liner") or not out.get("summary"):
        return False
    it.summary_short = str(out["one_liner"])[:80]
    it.summary_long = str(out["summary"])[:600]
    it.key_points = [str(p) for p in out.get("key_points", [])][:3]
    it.extra["summary_mode"] = "full"
    return True


def _check_one(it, ctx: Context) -> bool:
    out = ctx.llm.json_chat(
        _SYS_CHECK,
        f"原文：\n{(it.content or it.title)[:4000]}\n\n"
        f"摘要：{it.summary_short}\n{it.summary_long}",
        max_tokens=300)
    if out.get("faithful") is False:
        it.notes.append("hallucination_flagged: " + "; ".join(
            str(p) for p in out.get("problems", []))[:200])
        return False
    return True


def run(items: list, ctx: Context) -> list:
    todo = [it for it in items if it.status in ("published", "review")]
    counts = {"summarized": 0, "brief_mode": 0, "hallucination_flagged": 0,
              "degraded_title_only": 0}

    def _one(it) -> str:
        if not ctx.llm.available():
            it.notes.append("no_summary: llm_unavailable")
            return "degraded_title_only"
        try:
            if not _summarize_one(it, ctx):
                it.notes.append("no_summary: llm_json_invalid")
                return "degraded_title_only"
        except LLMError as e:
            it.notes.append(f"no_summary: {e}")
            return "degraded_title_only"
        # 幻觉自检只对"有完整原文且将自动发布"的条目做（简介模式没有原文可对照）
        if (ctx.cfg.hallucination_check and it.status == "published"
                and it.extra.get("summary_mode") == "full"):
            try:
                if not _check_one(it, ctx):
                    return "flagged"
            except LLMError:
                it.notes.append("hallucination_check_skipped")
        return "brief" if it.extra.get("summary_mode") == "brief" else "ok"

    outcomes = parallel_map(
        _one, todo, workers=ctx.cfg.llm_workers,
        on_error=lambda it, e: it.notes.append(f"no_summary: {type(e).__name__}"))

    for outcome in outcomes:
        if outcome in ("ok", "brief", "flagged"):
            counts["summarized"] += 1
            if outcome == "brief":
                counts["brief_mode"] += 1
            if outcome == "flagged":
                counts["hallucination_flagged"] += 1
        else:
            counts["degraded_title_only"] += 1

    ctx.stats["summarize"] = counts
    print(f"  summarize: 完成 {counts['summarized']}"
          f"（其中简介模式 {counts['brief_mode']}），"
          f"幻觉打标 {counts['hallucination_flagged']}，"
          f"降级仅标题 {counts['degraded_title_only']}")
    return items
