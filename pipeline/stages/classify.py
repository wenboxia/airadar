"""Stage 5: classify —— 分类 + 时间维度（短期时效 / 长期方法论）。

分类是一个平铺的 8 类列表（D43）：6 个内容类由模型判断，「开源项目」「研究论文」按链接由代码判定。
降级路径：没有模型时不猜内容类，只按链接打上开源 / 论文，并留痕。
"""
from urllib.parse import urlparse

from ..guards import parallel_map
from ..llm import LLMError
from ..models import Context

MANIFEST = {
    "name": "classify", "version": "0.3.0",
    "input": "list[Item]", "output": "list[Item]（含 category/categories/topics/horizon）",
    "eval_cases": "evals/feed_tag_eval.py（官网自带标签）· tools/classify_stability.py（重跑一致率）",
}

# 2026-09-17 改为平铺 8 类（D43），取代 D39 的 13 类。
# 旧体系把两种问题塞在一张表里：「这是什么东西」（论文、开源项目）和「讲的是什么」（Agent、安全），
# 于是「Agent 工程 + 论文」共现 78 次，「工程实践」重分一次有 52% 会变。
# 这次按主人的决定：模型发布与训练合并为「模型」；两个安全类合并为「安全」——
# 调研的 9 家资讯产品没有一家拆成两个，OpenAI 虽拆了，Security 文章 31% 同时挂 Safety；
# 「开源项目」「研究论文」保留，但由链接判定，不让模型猜。
# 旧分类快照：categories_v1（D39 之前）、categories_v2（本次改版之前）。
CONTENT_CATEGORIES = ["模型", "Agent 与开发", "评测", "安全", "产品与应用", "行业动态"]
GENRE_CATEGORIES = ["开源项目", "研究论文"]
CATEGORIES = CONTENT_CATEGORIES + GENRE_CATEGORIES

# 分不出主类时的裁决顺序：越具体、越少见的越优先，「行业动态」永远最后
PRIORITY = ["安全", "评测", "Agent 与开发", "模型", "产品与应用", "行业动态"]

SUGGESTED_TOPICS = ["agent-loop", "tool-use", "mcp", "memory", "context-engineering",
                    "harness", "multi-agent", "eval", "rag", "post-training",
                    "coding-agent", "hallucination", "reasoning", "open-source", "product",
                    # 安全合并成一类后，对齐 / 防护的区分放在这里，趋势页照样能分开看
                    "alignment", "jailbreak", "interpretability",
                    "prompt-injection", "cybersecurity", "privacy"]

_SYS = f"""你是 AI 行业内容分类器。判断这条内容主要在讲什么。
从下面 6 个类里选 1 个主类，必要时再选 1 个副类：{CONTENT_CATEGORIES}

- 模型：新模型发布与能力、训练方法（强化学习、蒸馏、量化、微调）、推理加速与部署
  不放：System Card 和风险报告归安全；专门的榜单评测归评测；单独的调价、融资归行业动态
- Agent 与开发：Agent 架构、工具调用、MCP、多智能体、上下文与记忆、RAG；编程 Agent 怎么用；
  开发者 SDK / API；AI 系统的部署运维
  不放：面向普通用户的产品功能、AI 用在某个行业里，归产品与应用；Agent 被攻击或作恶归安全；Agent 基准归评测
- 评测：新基准、榜单争议、评测方法（模型当裁判、稳定性测量）、测模型能力的实验
  不放：发布稿里顺带报的跑分归模型；测"会不会出事"（红队、危险能力评估）归安全
- 安全：对齐（越狱、欺骗、可解释性、System Card）、防护（prompt 注入、数据泄漏、供应链攻击）、
  滥用治理（诈骗、青少年保护、安全框架与立法）
  不放：安全用途的模型或产品发布，主类归模型或产品与应用，安全只作副类
- 产品与应用：面向用户的产品和功能上线、企业落地案例、AI 用于科研 / 医疗 / 金融
  不放：给开发者用的工具归 Agent 与开发；模型本身的发布归模型；公司融资和竞争归行业动态
- 行业动态：融资、收购、人事、算力和芯片、调价、市场竞争、政策监管、诉讼、社会影响
  只有其他 5 类都不是主角时才选它；"这是一条新闻"不是选它的理由

三个容易混的地方：
- 测模型做安全任务的能力算评测；测模型或系统会不会出事算安全
- 讲"怎么搭、怎么用"算 Agent 与开发；讲"做成了什么产品、谁用在哪"算产品与应用
- 发布的东西本身是模型就归模型，哪怕它是给某个产品用的

规则：
- 主类只选一个。实在分不出时按这个顺序取前面的：{" > ".join(PRIORITY)}
- 副类只在正文花了相当篇幅讲第二件事时才加；只是提到，不加。宁缺毋滥
- 不要输出「开源项目」「研究论文」，那两个由系统按链接判断

topics 选 1-3 个细粒度标签（优先用建议列表，也可自造小写连字符标签）：{SUGGESTED_TOPICS}
horizon 二选一：short（时效新闻，两周后价值大降）/ long（方法论/原理，三个月后仍值得回看）
输出 JSON：{{"primary": "...", "secondary": "... 或 null", "topics": ["..."], "horizon": "short|long"}}"""

# github.com 下这些一级路径不是仓库
_GITHUB_NON_REPO = {"features", "blog", "about", "pricing", "topics", "trending", "orgs",
                    "sponsors", "marketplace", "collections", "enterprise", "security"}


def genre_tags(url: str) -> list:
    """按链接判定「开源项目」「研究论文」。确定性规则，不交给模型。"""
    try:
        u = urlparse(url or "")
    except ValueError:
        return []
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    tags = []
    if host == "github.com" and len(parts) >= 2 and parts[0].lower() not in _GITHUB_NON_REPO:
        tags.append("开源项目")
    if host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org", "openreview.net") \
            or (host == "huggingface.co" and parts[:1] == ["papers"]):
        tags.append("研究论文")
    return tags


def _pick(out: dict):
    """从模型输出里取主类、副类；只认 6 个内容类，主副相同只留一个。"""
    primary = out.get("primary")
    secondary = out.get("secondary")
    # 兼容模型偶尔仍按旧格式回一个列表
    if primary not in CONTENT_CATEGORIES and isinstance(out.get("categories"), list):
        cands = [c for c in out["categories"] if c in CONTENT_CATEGORIES]
        primary, secondary = (cands + [None, None])[:2]
    if primary not in CONTENT_CATEGORIES:
        return None, None
    if secondary not in CONTENT_CATEGORIES or secondary == primary:
        secondary = None
    return primary, secondary


def _classify_one(it, ctx: Context) -> bool:
    out = ctx.llm.json_chat(
        _SYS,
        f"标题：{it.title}\n摘要：{it.summary_long or it.content[:800]}",
        max_tokens=400)   # 推理模型的思考也吃 max_tokens，150 会被吃光导致 JSON 截断
    primary, secondary = _pick(out)
    if not primary:
        return False
    it.categories = [primary] + ([secondary] if secondary else []) + genre_tags(it.url)
    it.category = primary
    it.topics = [str(t).lower() for t in out.get("topics", [])][:3]
    it.horizon = out.get("horizon") if out.get("horizon") in ("short", "long") else "short"
    return True


def _fallback(it):
    # 不猜内容类：猜错的分类会污染筛选和趋势，留空只是少了信息（降级往收紧的方向走）
    it.categories = genre_tags(it.url)
    it.category = it.categories[0] if it.categories else ""
    it.horizon = "long" if "研究论文" in it.categories else "short"
    it.topics = [t for t in SUGGESTED_TOPICS
                 if t.replace("-", " ") in it.title.lower() or t in it.title.lower()][:3]
    it.notes.append("classify_degraded: content_category_left_empty")


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
