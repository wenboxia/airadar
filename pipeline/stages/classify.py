"""Stage 5: classify —— 主题分类 + 时间维度（短期时效 / 长期方法论）。

降级路径：按信源类型的启发式规则兜底（arXiv→论文、GitHub→开源项目……）。
"""
from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "classify", "version": "0.2.0",
    "input": "list[Item]", "output": "list[Item]（含 category/topics/horizon）",
    "eval_cases": "evals/golden_set/golden.jsonl 的 category 标注",
}

# 一条内容常常同时属于多个类别——比如一篇讲量化训练方法的 arXiv 论文，
# 既是「论文」（体裁）又是「模型训练」（主题）。所以分类是多选，1-3 个。
# 「模型训练」与「模型发布」分开：前者讲怎么练出来的（方法、蒸馏、量化、微调），
# 后者讲发布了什么。「工程实践」收窄为系统层内容（生产部署、架构运维、可观测性），
# 不再兜底一切工程话题。
#
# 2026-09-16 参照 OpenAI 新闻页的分类体系改了两处（D39）：
# ① 拆「安全与对齐」：对齐是模型会不会做坏事，防护是系统会不会被攻破——
#    对 PM 是两个决策领域。本项目 D34（结构防护优于指令防护）就是防护议题，
#    旧体系里却只能归进「安全与对齐」
# ② 加「落地案例」：谁在用、怎么用。旧体系没有这个格子，收进来的企业实践无处安放
# 旧分类存在 items.categories_v1，改动前后可对比。
CATEGORIES = ["模型发布", "模型训练", "Agent 工程", "评测与基准", "上下文与记忆",
              "工程实践", "开源项目", "论文", "行业动态", "安全与对齐", "安全与防护",
              "落地案例", "产品与商业"]

SUGGESTED_TOPICS = ["agent-loop", "tool-use", "mcp", "memory", "context-engineering",
                    "harness", "multi-agent", "eval", "rag", "post-training",
                    "coding-agent", "hallucination", "reasoning", "open-source",
                    "safety", "product"]

_SYS = f"""你是 AI 行业内容分类器。
categories：从这个列表里选 1-3 个（一条内容可以同时属于多个类别，但别硬凑）：{CATEGORIES}
  - 「模型训练」指怎么练出来的：预训练/微调/蒸馏/量化/后训练方法
  - 「模型发布」指发布了什么模型、有什么能力
  - 「论文」是体裁标签，arXiv 内容都该带上，且通常还有一个主题类别
  - 「工程实践」只给系统层内容：生产部署、架构运维、可观测性
  - 「安全与对齐」指模型本身的行为：价值观、拒答、越狱、欺骗、可解释性
  - 「安全与防护」指系统被攻击：prompt 注入、数据泄漏、权限越界、供应链、漏洞
  - 「落地案例」指某个组织把 AI 用进了实际工作并讲了怎么做、效果如何
  - 「产品与商业」指产品形态、定价、市场、融资——讲的是卖什么，不是谁用出了什么效果
topics 选 1-3 个细粒度标签（优先用建议列表，也可自造小写连字符标签）：{SUGGESTED_TOPICS}
horizon 二选一：short（时效新闻，两周后价值大降）/ long（方法论/原理，三个月后仍值得回看）
输出 JSON：{{"categories": ["...", "..."], "topics": ["..."], "horizon": "short|long"}}"""

# 无 LLM 时的启发式兜底：信源类型 → 默认分类
_FALLBACK = {"arXiv Agent/LLM": ("论文", "long"),
             "GitHub Trending (AI)": ("开源项目", "short"),
             "Hacker News (AI 高分帖)": ("行业动态", "short")}


def _classify_one(it, ctx: Context) -> bool:
    out = ctx.llm.json_chat(
        _SYS,
        f"标题：{it.title}\n摘要：{it.summary_long or it.content[:800]}",
        max_tokens=400)   # 同上：150 会被思考吃光导致 JSON 截断
    cats = [c for c in out.get("categories", []) if c in CATEGORIES][:3]
    if not cats:
        return False
    it.categories = cats
    it.category = cats[0]     # 主分类=第一个，保持向后兼容（旧数据只有单值）
    it.topics = [str(t).lower() for t in out.get("topics", [])][:3]
    it.horizon = out.get("horizon") if out.get("horizon") in ("short", "long") else "short"
    return True


def _fallback(it):
    cat, horizon = _FALLBACK.get(it.source, ("行业动态", "short"))
    it.category, it.categories, it.horizon = cat, [cat], horizon
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
