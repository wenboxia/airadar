"""错误分类与降级链的回归测试（标准库 unittest，零新依赖）。

为什么值得写测试：这段逻辑决定"该不该重试"，错了要么空转几十分钟，
要么把可恢复故障当成致命错误。真实案例见 docs/decisions.md D11。

跑：python3 -m unittest evals.test_llm_guards -v
"""
import unittest

from pipeline.guards import Budget
from pipeline.llm import (LLMClient, LLMError, Provider, TransientError,
                          classify_http_error, parse_json_loose)


class TestErrorClassification(unittest.TestCase):
    def test_402_is_permanent(self):
        e = classify_http_error(402, '{"error":{"message":"Insufficient Balance"}}')
        self.assertIsInstance(e, LLMError)
        self.assertTrue(e.permanent)

    def test_429_real_rate_limit_is_transient(self):
        """真限流：值得退避重试。"""
        e = classify_http_error(429, '{"error":"Rate limit exceeded, retry after 1s"}')
        self.assertIsInstance(e, TransientError)

    def test_429_balance_disguised_is_permanent(self):
        """实测坑：Kimi/GLM 用 429 表示余额不足，重试无意义。"""
        for body in ('{"error":{"message":"account is suspended due to insufficient balance"}}',
                     '{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}'):
            e = classify_http_error(429, body)
            self.assertIsInstance(e, LLMError, body)
            self.assertTrue(e.permanent, body)

    def test_auth_and_404_permanent(self):
        for status in (401, 403, 404):
            self.assertTrue(classify_http_error(status, "{}").permanent)

    def test_5xx_is_transient(self):
        self.assertIsInstance(classify_http_error(503, "bad gateway"), TransientError)


class _FakeProvider(Provider):
    """用假 provider 验证降级链的切换与"作废"行为，不打真实网络。"""

    def __init__(self, name, behavior):
        super().__init__(name, "https://fake", "key", "model")
        self.behavior = behavior
        self.attempts = 0

    def call(self, system, user, temperature, max_tokens, timeout):
        self.attempts += 1
        if self.behavior == "permanent":
            raise LLMError("余额不足", permanent=True)
        if self.behavior == "ok":
            return {"choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"total_tokens": 10}}
        raise TransientError("boom")


class TestFallbackChain(unittest.TestCase):
    def _client(self, *providers):
        return LLMClient(list(providers), Budget(10_000, 100), {})

    def test_switches_to_fallback_on_permanent_failure(self):
        p1, p2 = _FakeProvider("primary", "permanent"), _FakeProvider("fallback", "ok")
        out = self._client(p1, p2).json_chat("s", "u")
        self.assertEqual(out, {"ok": True})
        self.assertTrue(p1.dead, "永久性故障的供应商必须被标记作废")

    def test_dead_provider_not_retried_across_calls(self):
        """核心效率保证：作废的供应商在后续条目上不再浪费时间。"""
        p1, p2 = _FakeProvider("primary", "permanent"), _FakeProvider("fallback", "ok")
        client = self._client(p1, p2)
        for _ in range(5):
            client.json_chat("s", "u")
        self.assertEqual(p1.attempts, 1, "作废供应商只应被尝试一次，而非每条内容一次")

    def test_all_dead_reports_unavailable(self):
        p1, p2 = _FakeProvider("primary", "permanent"), _FakeProvider("fallback", "permanent")
        client = self._client(p1, p2)
        with self.assertRaises(LLMError):
            client.json_chat("s", "u")
        self.assertFalse(client.available(), "全挂后 available() 必须为 False，让 stage 走降级")

    def test_budget_exhaustion_blocks_calls(self):
        client = LLMClient([_FakeProvider("primary", "ok")], Budget(0, 0), {})
        self.assertFalse(client.available())


class TestJsonParsing(unittest.TestCase):
    def test_markdown_fence(self):
        self.assertEqual(parse_json_loose('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounded_by_prose(self):
        self.assertEqual(parse_json_loose('好的，结果如下：{"a": 1} 希望有帮助'), {"a": 1})

    def test_garbage_returns_empty_not_crash(self):
        for bad in ("", "完全不是 JSON", "{坏掉的", None):
            self.assertEqual(parse_json_loose(bad), {})


if __name__ == "__main__":
    unittest.main()
