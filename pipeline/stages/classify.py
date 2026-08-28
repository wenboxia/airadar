"""Stage 5: classify —— 主题分类 + 时间维度（短期时效 / 长期方法论）。

降级路径：按信源类型的启发式规则兜底（arXiv→论文、GitHub→开源项目……）。
"""
from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "classify", "version": "0.1.0",
    "input": "list[Item]", "output": "list[Item]（含 category/topics/horizon）",
    "eval_cases": "evals/golden_set/golden.jsonl 的 category 标注",
}

CATEGORIES = ["模型发布", "Agent 工程", "评测与基准", "上下文与记忆", "工程实践",
              "开源项目", "论文", "行业动态", "安全与对齐", "产品与商业"]

SUGGESTED_TOPICS = ["agent-loop", "tool-use", "mcp", "memory", "context-engineering",
                    "harness", "multi-agent", "eval", "rag", "post-training",
                    "coding-agent", "hallucination", "reasoning", "open-source",
                    "safety", "product"]

_SYS = f"""你是 AI 行业内容分类器。
category 必须从这个列表里选一个：{CATEGORIES}
topics 选 1-3 个标签（优先用建议列表，也可自造小写连字符标签）：{SUGGESTED_TOPICS}
horizon 二选一：short（时效新闻，两周后价值大降）/ long（方法论/原理，三个月后仍值得回看）
输出 JSON：{{"category": "...", "topics": ["..."], "horizon": "short|long"}}"""

# 无 LLM 时的启发式兜底：信源类型 → 默认分类
_FALLBACK = {"arXiv Agent/LLM": ("论文", "long"),
             "GitHub Trending (AI)": ("开源项目", "short"),
             "Hacker News (AI 高分帖)": ("行业动态", "short")}


def _classify_one(it, ctx: Context) -> bool:
    out = ctx.llm.json_chat(
        _SYS,
        f"标题：{it.title}\n摘要：{it.summary_long or it.content[:800]}",
        max_tokens=400)   # 同上：150 会被思考吃光导致 JSON 截断
    cat = out.get("category")
    if cat not in CATEGORIES:
        return False
    it.category = cat
    it.topics = [str(t).lower() for t in out.get("topics", [])][:3]
    it.horizon = out.get("horizon") if out.get("horizon") in ("short", "long") else "short"
    return True


def _fallback(it):
    cat, horizon = _FALLBACK.get(it.source, ("行业动态", "short"))
    it.category, it.horizon = cat, horizon
    it.topics = [t for t in SUGGESTED_TOPICS
                 if t.replace("-", " ") in it.title.lower() or t in it.title.lower()][:3]
    it.notes.append("classify_degraded: heuristic")


def run(items: list, ctx: Context) -> list:
    todo = [it for it in items if it.status in ("published", "review")]

    def _one(it) -> bool:
        if ctx.llm.available():
            try:
                if _classify_one(it, ctx):
                    return True
            except LLMError:
                pass
        _fallback(it)
        return False

    outcomes = parallel_map(_one, todo, workers=ctx.cfg.llm_workers,
                            on_error=lambda it, e: _fallback(it))
    done = sum(1 for o in outcomes if o)
    degraded = len(todo) - done
    ctx.stats["classify"] = {"classified": done, "degraded_heuristic": degraded}
    print(f"  classify: LLM 分类 {done}，启发式兜底 {degraded}")
    return items
