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
    llm_model: str = "deepseek-v4-pro"
    # 备用 LLM（主力永久性故障时跨厂商切换，见 decisions.md D10）
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""
    # 评测裁判（须与主力不同家，避免 self-preference bias）
    judge_base_url: str = ""
    judge_api_key: str = ""
    judge_model: str = ""

    # 置信度路由阈值（0-100）
    publish_threshold: float = 75.0
    review_threshold: float = 50.0
    # 信源 tier 基础分
    tier_base: dict = field(default_factory=lambda: {
        "S": 90, "A": 78, "B": 62, "C": 48, "D": 30, "X": 0})
    tier_weight: float = 0.55   # 综合分 = tier_weight*tier基础分 + (1-w)*LLM价值分

    # 预算与兜底（防止一次运行烧穿钱包/跑不完）
    max_items_per_run: int = 80      # 全局安全阀
    per_source_limit: int = 0        # 每信源上限，0=不限（--limit 设置它）
    max_llm_calls: int = 260
    llm_workers: int = 4         # 并发度：6 会把 GLM 打到限流(429 code 1302)，4 实测稳定
    token_budget: int = 400_000
    content_max_chars: int = 6000
    since_days: int = 2          # 只看最近 N 天发布的内容（首跑可调大）
    hallucination_check: bool = True  # 发布级条目做摘要自检

    # 关注领域（triage 的 LLM 评分以此为准绳）
    focus: str = ("Agent 工程（agent loop/tool use/memory/context/harness/MCP/multi-agent/评测）、"
                  "大模型进展（新模型发布/后训练/推理能力）、"
                  "头部公司动态（OpenAI/Anthropic/Google/DeepSeek/Moonshot/字节/阿里/腾讯）、"
                  "AI 产品与工程实践（coding agent/评测体系/RAG/安全对齐）")


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
