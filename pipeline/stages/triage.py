"""Stage 3: triage —— 信源分层评分 + 置信度路由（HITL 的核心）。

综合分 = tier_weight × tier基础分 + (1-tier_weight) × LLM 价值分
路由：>=publish_threshold 自动发布；中间区进人工审核；低于 review_threshold 丢弃（留审计日志）。

降级路径（无 LLM / 超预算）：只用 tier 基础分。S/A 发布、B/C/D 送审——
宁可多送审也不让低质内容自动发布（保守降级）。
"""
from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "triage", "version": "0.1.0",
    "input": "list[Item]", "output": "list[Item]（status: published/review/discarded）",
    "eval_cases": "evals/golden_set/golden.jsonl 的 include/tier 标注",
}

_SYSTEM = """你是 AI 行业情报分析师，为一个只收录高价值内容的知识雷达做价值评估。
关注领域：{focus}
打分标准：
- relevance 相关度（0-100）：与关注领域的贴合度，完全无关给 0-20
- novelty 新颖度（0-100）：是新信息/新观点，还是老生常谈或纯营销
- longterm 长期价值（0-100）：三个月后还值得回看吗？方法论>快讯>八卦"""

_USER = """标题：{title}
来源：{source}（信誉层级 {tier}）
正文节选：
{content}

输出 JSON：{{"relevance": int, "novelty": int, "longterm": int, "reason": "一句话理由"}}"""


def _llm_value(item, ctx: Context):
    out = ctx.llm.json_chat(
        _SYSTEM.format(focus=ctx.cfg.focus),
        _USER.format(title=item.title, source=item.source, tier=item.tier,
                     content=(item.content or "（无正文，仅标题）")[:1500]),
        max_tokens=500)   # 推理模型要给思考留预算
    r, n, lt = (out.get("relevance"), out.get("novelty"), out.get("longterm"))
    if not all(isinstance(v, (int, float)) for v in (r, n, lt)):
        return None, "llm_json_invalid"
    value = 0.5 * r + 0.25 * n + 0.25 * lt
    item.score_detail.update({"relevance": r, "novelty": n, "longterm": lt,
                              "llm_reason": out.get("reason", "")})
    return value, None


def run(items: list, ctx: Context) -> list:
    cfg = ctx.cfg
    counts = {"published": 0, "review": 0, "discarded": 0, "llm_scored": 0,
              "degraded": 0}

    def _score_one(it):
        base = cfg.tier_base.get(it.tier, 30)
        it.score_detail["tier_base"] = base
        value = None
        if ctx.llm.available():
            try:
                value, err = _llm_value(it, ctx)
                if err:
                    it.notes.append(f"triage_degraded: {err}")
            except LLMError as e:
                it.notes.append(f"triage_degraded: {e}")
        if value is None:
            # 保守降级：只有 tier 分，S/A 放行，其余送人工审
            it.score = float(base)
            it.notes.append("triage_degraded: no_llm")
            it.status = "published" if it.tier in ("S", "A") else "review"
            return "degraded"

        it.score = round(cfg.tier_weight * base + (1 - cfg.tier_weight) * value, 1)
        it.score_detail["llm_value"] = round(value, 1)
        if it.score >= cfg.publish_threshold:
            it.status = "published"
        elif it.score >= cfg.review_threshold:
            it.status = "review"
        else:
            it.status = "discarded"
        # 待观察信源（D）永远不允许直接发布
        if it.tier == "D" and it.status == "published":
            it.status = "review"
            it.notes.append("tier_D_forced_review")
        return "llm_scored"

    def _on_error(it, e):
        # 评分环节异常 → 保守处理为送人工审，绝不静默丢弃或放行
        it.score = float(cfg.tier_base.get(it.tier, 30))
        it.status = "review"
        it.notes.append(f"triage_error: {type(e).__name__}")
        ctx.note_error("triage", f"{it.title[:40]}: {e}")

    outcomes = parallel_map(_score_one, items, workers=cfg.llm_workers,
                            on_error=_on_error)
    for it, outcome in zip(items, outcomes):
        counts[outcome or "degraded"] = counts.get(outcome or "degraded", 0) + 1
        counts[it.status] = counts.get(it.status, 0) + 1

    ctx.stats["triage"] = counts
    print(f"  triage: 发布 {counts.get('published', 0)} / 待审 {counts.get('review', 0)} / "
          f"丢弃 {counts.get('discarded', 0)}"
          f"（LLM 评分 {counts.get('llm_scored', 0)}，降级 {counts.get('degraded', 0)}）")
    return items
