"""置信度路由的回归测试（HITL 的核心逻辑）。

为什么值得写：这段逻辑决定"哪些内容不经人看就发出去"。改错一个不等号，
要么把低质内容自动发布（破坏信任），要么把所有东西都推给人（回到人力瓶颈）。
尤其 D 级强制送审这条规则——它是"新信源没有信誉记录"这个设计的唯一执行点。

跑：python3 -m unittest evals.test_triage_routing -v
"""
import unittest
from unittest.mock import Mock

from pipeline.config import Config
from pipeline.models import Context, Item
from pipeline.stages import triage


def _ctx(llm_available: bool, llm_scores=None):
    """造一个假的运行上下文。llm_scores 决定模型给出的价值分。"""
    cfg = Config()
    cfg.llm_workers = 1          # 测试里串行，结果可预期
    llm = Mock()
    llm.available.return_value = llm_available
    llm.json_chat.return_value = llm_scores or {}
    return Context(cfg=cfg, llm=llm, db=Mock(), run_id="test")


def _item(tier="S", title="测试条目"):
    return Item(tier=tier, title=title, source=f"{tier}级信源", content="正文内容")


class TestConfidenceRouting(unittest.TestCase):
    """LLM 可用时的三分支路由。"""

    def _run_one(self, tier, scores):
        it = _item(tier)
        triage.run([it], _ctx(True, scores))
        return it

    def test_high_score_auto_publishes(self):
        it = self._run_one("S", {"relevance": 95, "novelty": 90, "longterm": 90})
        self.assertEqual(it.status, "published")

    def test_mid_score_goes_to_human(self):
        """中间区必须交给人——系统知道自己不确定。"""
        it = self._run_one("B", {"relevance": 55, "novelty": 50, "longterm": 45})
        self.assertEqual(it.status, "review")

    def test_low_score_discarded(self):
        it = self._run_one("C", {"relevance": 10, "novelty": 10, "longterm": 5})
        self.assertEqual(it.status, "discarded")

    def test_tier_D_never_auto_publishes_at_any_score(self):
        """核心不变量：待观察信源在任何分数下都不能自动发布。
        新信源在本系统里没有信誉记录，信誉必须靠人工通过率挣。"""
        for r in (100, 90, 75, 50, 20):
            it = self._run_one("D", {"relevance": r, "novelty": r, "longterm": r})
            self.assertNotEqual(it.status, "published",
                                f"D 级在 relevance={r} 时被自动发布了")

    def test_tier_D_guard_survives_threshold_changes(self):
        """这条测的是**安全网本身**，不是当前配置下的结果。

        当前参数下 D 级的算术上限只有 61.5 分（0.55×30 + 0.45×100），
        本来就够不到 75 的发布线——也就是说 triage 里那条显式的
        "D 级强制送审"规则目前从未执行过。

        但保护不能依赖算术巧合：只要有人调高 D 的基础分或调低发布线，
        算术保护就静默失效。所以这里模拟一次未来的配置变更，
        验证那条显式规则真的会兜住。"""
        it = _item("D")
        ctx = _ctx(True, {"relevance": 100, "novelty": 100, "longterm": 100})
        ctx.cfg.publish_threshold = 50.0     # 模拟有人调低了发布线
        triage.run([it], ctx)
        self.assertEqual(it.status, "review", "显式的 D 级守卫没有生效")
        self.assertIn("tier_D_forced_review", " ".join(it.notes),
                      "守卫生效时必须留痕，否则无法审计")

    def test_tier_S_beats_tier_D_on_same_content_score(self):
        """同样的内容质量，信源等级决定命运——这就是分层的意义。"""
        scores = {"relevance": 70, "novelty": 70, "longterm": 70}
        s_item, d_item = self._run_one("S", scores), self._run_one("D", scores)
        self.assertGreater(s_item.score, d_item.score)


class TestConservativeDegradation(unittest.TestCase):
    """LLM 不可用时的保守降级：宁可多麻烦人，不可错发内容。"""

    def _run_degraded(self, tier):
        it = _item(tier)
        triage.run([it], _ctx(False))
        return it

    def test_authoritative_sources_still_pass(self):
        for tier in ("S", "A"):
            self.assertEqual(self._run_degraded(tier).status, "published", tier)

    def test_everything_else_goes_to_human(self):
        """降级时收紧自动化权限，而不是放宽。"""
        for tier in ("B", "C", "D"):
            self.assertEqual(self._run_degraded(tier).status, "review", tier)

    def test_degradation_is_recorded(self):
        it = self._run_degraded("S")
        self.assertIn("triage_degraded", " ".join(it.notes),
                      "所有降级必须留痕，否则无法审计")


class TestErrorIsolation(unittest.TestCase):
    def test_scoring_crash_falls_back_to_review(self):
        """单条评分崩溃不能静默丢弃，也不能放行——必须落到人工审。"""
        ctx = _ctx(True)
        ctx.llm.json_chat.side_effect = RuntimeError("模拟崩溃")
        it = _item("S")
        triage.run([it], ctx)
        self.assertEqual(it.status, "review")

    def test_one_failure_does_not_block_others(self):
        """舱壁原则：一条炸了，同批其他条目照常处理。"""
        ctx = _ctx(True)
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("第一条炸了")
            return {"relevance": 95, "novelty": 90, "longterm": 90}

        ctx.llm.json_chat.side_effect = flaky
        items = [_item("S", "第一条"), _item("S", "第二条")]
        triage.run(items, ctx)
        self.assertEqual(items[1].status, "published", "后续条目不应受影响")


if __name__ == "__main__":
    unittest.main()
