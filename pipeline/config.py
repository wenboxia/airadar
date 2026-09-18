"""配置：全部可被环境变量覆盖（AIRADAR_ 前缀），.env 文件手动解析（不引第三方依赖）。"""
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """极简 .env 解析：只支持 KEY=VALUE 行，已存在的环境变量优先。"""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


@dataclass
class Config:
    # 主力 LLM（默认 DeepSeek，OpenAI 兼容协议，换供应商=换这三个值）
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    # 2026-09-18 从 deepseek-v4-pro 换成 V4.1 Flash（D48）：V4 Pro 原定 09-14 下线、后改为暂留，
    # 随时可能再下；六家对比里 Flash 分类与 Pro 打平、快 3 倍、成本约 1/4（docs/eval_report.md）
    llm_model: str = "deepseek-flash"
    # 备用 LLM（主力永久性故障时跨厂商切换，见 decisions.md D10）
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""
    # 评测裁判（须与主力不同家，避免 self-preference bias）
    judge_base_url: str = ""
    judge_api_key: str = ""
    judge_model: str = ""

    # 内容时效：时效类（horizon=short）内容超过这个天数就退出知识库视图（D30），
    # 同一个数也用来决定"旧的待审条目还值不值得占人的注意力"——两处必须是同一个数
    short_ttl_days: int = 14
    # 审批单里同一信源最多几条。时效出队复用上面的 short_ttl_days；长期价值类不受时效限制
    review_source_quota: int = 3      # 一张单子里同一信源最多几条（量子位限量的落点）

    # 置信度路由阈值（0-100）
    publish_threshold: float = 75.0
    review_threshold: float = 50.0
    # 信源 tier 基础分。D（待观察）已于 2026-09-16 停用，没有信源再用它；
    # 保留这一项只因库里历史条目仍带 D 标记，且未声明 tier 的信源会落到这个最保守的分数
    tier_base: dict = field(default_factory=lambda: {
        "S": 90, "A": 78, "B": 62, "C": 48, "D": 30, "X": 0})
    # 综合分 = tier_weight*tier基础分 + (1-w)*LLM价值分。
    # 0.55 是最初拍脑袋定的。拿黄金集 111 条做 5 折交叉验证（每折在训练部分选权重、
    # 留出部分验证）发现：信源等级分单独的判别力只有 0.63，是四个维度里最低的，
    # 却拿着最高的权重；长期价值 0.86 最高，却只占 0.113。改成下面这组后平均 AUC 0.82→0.84
    tier_weight: float = 0.4
    # LLM 价值分的三维权重：relevance / novelty / longterm
    value_weights: tuple = (0.25, 0.25, 0.5)

    # 预算与兜底（防止一次运行烧穿钱包/跑不完）
    max_items_per_run: int = 80      # 全局安全阀
    per_source_limit: int = 0        # 每信源上限，0=不限（--limit 设置它）
    max_llm_calls: int = 260
    llm_workers: int = 4         # 并发度：6 会把 GLM 打到限流(429 code 1302)，4 实测稳定
    token_budget: int = 400_000
    content_max_chars: int = 6000
    since_days: int = 2          # 只看最近 N 天发布的内容（首跑可调大）
    hallucination_check: bool = True  # 发布级条目做摘要自检

    # 关注方向（triage 打分里的**举例**，不是白名单——prompt 里已写明这一点）。
    # 以前这是一张白名单，黄金集里 3 条主人会收的具身智能内容因此被压到 51–59 分。
    focus: str = ("Agent 工程（agent loop/tool use/memory/context/harness/MCP/multi-agent/评测）、"
                  "大模型进展（新模型发布/后训练/推理能力）、"
                  "世界模型与具身智能（机器人、空间智能、3D 生成与重建）、"
                  "多模态生成（视频/图像/语音）、"
                  "头部公司动态（OpenAI/Anthropic/Google/DeepSeek/Moonshot/字节/阿里/腾讯）、"
                  "AI 产品与工程实践（coding agent/评测体系/RAG/安全对齐）")

    # 按内容类型给 LLM 价值分封顶。为什么是封顶不是扣分：tier 基础分是地板
    # （S 级价值分为 0 也有 0.4×90=36 分），扣分会被地板和其他维度抵消；
    # 封顶 45 后 S 级营销稿最高 36+0.6×45=63 分，结构上到不了 75 的发布线。
    # 先给 45——这个值不产生任何新的自动丢弃，等离线评测扫出安全区间再调
    kind_value_cap: dict = field(default_factory=lambda: {"营销通稿": 45.0, "仿造品": 45.0})


def load_config() -> Config:
    _load_dotenv()
    cfg = Config()
    env = os.environ
    cfg.llm_base_url = env.get("AIRADAR_LLM_BASE_URL", cfg.llm_base_url)
    cfg.llm_api_key = env.get("AIRADAR_LLM_API_KEY", cfg.llm_api_key)
    cfg.llm_model = env.get("AIRADAR_LLM_MODEL", cfg.llm_model)
    cfg.fallback_base_url = env.get("AIRADAR_FALLBACK_BASE_URL", cfg.fallback_base_url)
    cfg.fallback_api_key = env.get("AIRADAR_FALLBACK_API_KEY", cfg.fallback_api_key)
    cfg.fallback_model = env.get("AIRADAR_FALLBACK_MODEL", cfg.fallback_model)
    cfg.judge_base_url = env.get("AIRADAR_JUDGE_BASE_URL", cfg.judge_base_url)
    cfg.judge_api_key = env.get("AIRADAR_JUDGE_API_KEY", cfg.judge_api_key)
    cfg.judge_model = env.get("AIRADAR_JUDGE_MODEL", cfg.judge_model)
    if env.get("AIRADAR_SINCE_DAYS"):
        cfg.since_days = int(env["AIRADAR_SINCE_DAYS"])
    if env.get("AIRADAR_TOKEN_BUDGET"):
        cfg.token_budget = int(env["AIRADAR_TOKEN_BUDGET"])
    return cfg
