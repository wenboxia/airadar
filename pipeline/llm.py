"""Provider 无关的 LLM 客户端 + 跨厂商降级链。

两层设计：
- Provider：单个供应商的连接（OpenAI 兼容 /chat/completions）。换供应商 = 换 base_url + model。
- LLMClient：按序持有 [主力, 备用]，主力永久性故障时自动切到备用；全挂则 available()=False，
  各 stage 走自己的降级路径（保守降级：宁可送人工审，不可错发）。

关键教训（decisions.md D11）：**429 不等于限流**。实测 Kimi/GLM 的"余额不足"也返回 429，
对这类永久性故障重试是纯粹浪费时间——必须按错误语义分类，而不是按状态码盲目重试。
"""
import json
import re
import threading

import requests

from .guards import Budget, retry


class LLMError(Exception):
    """LLM 调用失败。permanent=True 表示重试无意义（认证/余额/模型不存在）。"""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class TransientError(Exception):
    """临时性故障，值得退避重试（真限流、5xx、超时、网络抖动）。"""


# 余额/配额类错误的特征词：命中则判定为永久性故障，不再重试
_PERMANENT_HINTS = ("insufficient balance", "insufficient_quota", "suspended",
                    "余额不足", "欠费", "quota", "expired", "无可用资源包")


def classify_http_error(status: int, body: str) -> Exception:
    """把 HTTP 响应翻译成"该不该重试"。这是整个兜底逻辑的判断依据。"""
    low = body.lower()
    if status in (401, 403):
        return LLMError(f"认证失败 http {status}: {body[:150]}", permanent=True)
    if status == 402:
        return LLMError(f"余额不足 http {status}: {body[:150]}", permanent=True)
    if status == 404:
        return LLMError(f"模型或路径不存在 http {status}: {body[:150]}", permanent=True)
    if status == 429:
        # 关键分支：同样是 429，余额不足是永久性的，真限流才值得重试
        if any(h in low for h in _PERMANENT_HINTS):
            return LLMError(f"账户余额/配额问题（伪装成 429）: {body[:150]}", permanent=True)
        return TransientError(f"限流 http 429: {body[:120]}")
    if status >= 500:
        return TransientError(f"服务端错误 http {status}")
    return LLMError(f"http {status}: {body[:150]}", permanent=True)


# ============ 模型适配层 ============
# "OpenAI 兼容"并不真的兼容。各家的实际差异（实测于 2026-08-28，见 decisions.md D12）：
#   - kimi-k3：只接受 temperature=1，传别的值直接 400
#   - 推理模型（kimi-k3 / glm-5.x）：内部思考的 token 也算进 max_tokens。
#     实测 glm-5.3 在 max_tokens=300 时光思考就用掉 299，content 返回空字符串。
# 按模型名前缀匹配，新增模型只改这张表，不动调用逻辑。
#   - deepseek-v4：实测同样输出 reasoning_content（"输出 {"a":1}" 这种琐碎请求都要花 108
#     个思考 token）。摘要任务给 800 token 时思考吃掉大半，JSON 被截断 → 解析失败。
MODEL_QUIRKS = {
    "kimi-k3": {"temperature": 1.0, "token_multiplier": 3.0},
    "kimi-k2": {"token_multiplier": 1.5},
    "glm-5": {"token_multiplier": 3.0},
    "glm-4": {"token_multiplier": 1.5},
    "deepseek-v4": {"token_multiplier": 3.0},
}


def quirks_for(model: str) -> dict:
    """最长前缀匹配：glm-5.3 命中 glm-5，避免为每个小版本重复配置。"""
    best = {}
    best_len = -1
    for prefix, q in MODEL_QUIRKS.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = q, len(prefix)
    return best


class Provider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.quirks = quirks_for(self.model)
        self.dead = False          # 永久性故障后置位，本次运行不再尝试
        self.dead_reason = ""

    def usable(self) -> bool:
        return bool(self.api_key and self.base_url and self.model) and not self.dead

    def _post(self, system: str, user: str, temperature: float,
              max_tokens: int, timeout: int) -> dict:
        def _once():
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"model": self.model,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}],
                      "temperature": temperature,
                      "max_tokens": max_tokens},
                timeout=timeout)
            if resp.status_code != 200:
                raise classify_http_error(resp.status_code, resp.text)
            return resp.json()

        # 只对临时性故障退避重试；永久性故障（LLMError）直接抛出，交给上层换供应商。
        # 退避要够耐心：并发下真限流（429）需要几十秒才恢复，退太快只会把配额继续打满
        return retry(_once, attempts=4, base_delay=4.0,
                     retriable=(TransientError, requests.RequestException))

    def call(self, system: str, user: str, temperature: float,
             max_tokens: int, timeout: int) -> dict:
        temp = self.quirks.get("temperature", temperature)
        tokens = int(max_tokens * self.quirks.get("token_multiplier", 1.0))
        data = self._post(system, user, temp, tokens, timeout)

        # 自愈：推理模型的思考也吃 max_tokens，预算不够会截断——可能返回空 content，
        # 也可能返回半截 JSON（解析失败）。两种都靠 finish_reason=length 识别，
        # 加倍预算重试一次。这比手工给每个 stage 调 max_tokens 更稳（decisions.md D12）
        if _truncated(data):
            data = self._post(system, user, temp, tokens * 2, timeout)
            if _truncated(data):
                raise LLMError(
                    f"{self.name}({self.model}) 输出被 token 预算截断，加倍后仍不足")
        return data


def _truncated(data: dict) -> bool:
    try:
        return data["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError):
        return False


class LLMClient:
    """按 [主力 → 备用] 顺序调用，全部不可用时优雅退场。线程安全（stage 并发调用）。"""

    def __init__(self, providers: list, budget: Budget, stats: dict):
        self.providers = [p for p in providers if p.api_key and p.base_url and p.model]
        self.budget = budget
        self.stats = stats
        self.lock = threading.Lock()

    def available(self) -> bool:
        return any(p.usable() for p in self.providers) and self.budget.can_spend()

    def active_provider(self):
        for p in self.providers:
            if p.usable():
                return p
        return None

    def chat(self, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 1200, timeout: int = 90) -> str:
        if not self.budget.can_spend():
            raise LLMError("预算已用尽", permanent=True)
        last_err = None
        for p in self.providers:
            if not p.usable():
                continue
            try:
                data = p.call(system, user, temperature, max_tokens, timeout)
            except LLMError as e:
                # 永久性故障：标记该供应商本次运行作废，立刻换下一家（不浪费后续条目的时间）
                if e.permanent:
                    with self.lock:
                        already = p.dead
                        p.dead, p.dead_reason = True, str(e)[:150]
                        self.stats.setdefault("provider_dead", {})[p.name] = p.dead_reason
                    if not already:
                        print(f"  [降级] 供应商 {p.name} 永久性故障，切换下一家：{str(e)[:90]}")
                last_err = e
                continue
            except Exception as e:  # noqa: BLE001 重试耗尽的临时故障
                last_err = e
                with self.lock:
                    self.stats["provider_transient_fail"] = \
                        self.stats.get("provider_transient_fail", 0) + 1
                continue

            usage = data.get("usage", {})
            self.budget.record(usage.get("total_tokens", 0))
            with self.lock:
                self.stats["llm_calls"] = self.stats.get("llm_calls", 0) + 1
                self.stats["llm_tokens"] = self.stats.get("llm_tokens", 0) + \
                    usage.get("total_tokens", 0)
                self.stats.setdefault("calls_by_provider", {})
                self.stats["calls_by_provider"][p.name] = \
                    self.stats["calls_by_provider"].get(p.name, 0) + 1
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                last_err = LLMError(f"{p.name} 返回结构异常: {e}")
                continue
        raise LLMError(f"所有供应商均不可用（最后错误：{last_err}）", permanent=True)

    def json_chat(self, system: str, user: str, **kw) -> dict:
        """要求 JSON 输出并宽容解析；解析失败返回 {}，调用方按降级处理，不许裸崩。"""
        text = self.chat(system + "\n\n只输出一个 JSON 对象，不要任何其他文字。", user, **kw)
        return parse_json_loose(text)


def build_client(cfg, budget: Budget, stats: dict) -> LLMClient:
    return LLMClient([
        Provider("primary", cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model),
        Provider("fallback", cfg.fallback_base_url, cfg.fallback_api_key,
                 cfg.fallback_model),
    ], budget, stats)


def parse_json_loose(text: str) -> dict:
    """LLM 的 JSON 经常带 markdown 围栏或前后废话，这里做宽容提取。"""
    if not text:
        return {}
    text = re.sub(r"```(?:json)?", "", text).strip("` \n")
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            out = json.loads(text[start:end + 1])
            return out if isinstance(out, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
