"""置信度路由的回归测试（HITL 的核心逻辑）。

为什么值得写：这段逻辑决定"哪些内容不经人看就发出去"。改错一个不等号，
要么把低质内容自动发布（破坏信任），要么把所有东西都推给人（回到人力瓶颈）。

2026-09-16 取消了 D 级（待观察）和它的「强制送审」规则：D 级综合分上限 61.5，
那条规则一次都没执行过，而 D 级内容有一半低于 50 被直接丢弃、从没到过人手里。
原先锁定这条规则的两个测试一并删除；现在锁的是反面——不再有任何等级被特殊对待。

跑：python3 -m unittest evals.test_triage_routing -v
"""
import pathlib
import re
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

    def test_routing_depends_only_on_score(self):
        """取消 D 级后，路由只看分数，不再有按等级的特判。
        防止有人重新加回一条"某级永远送审"的规则却不去验证它会不会生效。"""
        src = (pathlib.Path(__file__).parent.parent / "pipeline" / "stages" / "triage.py"
               ).read_text(encoding="utf-8")
        body = src.split("def route(", 1)[1].split("\ndef ", 1)[0]
        # 降级路径里按等级放行是 D7 的保守降级，不算特判；只看正常打分之后那几行
        scored = body.split('if outcome == "degraded"', 1)[1].split("\n", 3)[3]
        self.assertNotRegex(scored, r"\.tier\s*==", "评分后的路由里出现了按等级的特判")

    def test_kind_cap_is_structural(self):
        """营销通稿要封顶，不是扣分——tier 基础分是地板，扣分会被它抵消掉。"""
        it = self._run_one("S", {"kind": "营销通稿", "relevance": 100,
                                 "novelty": 100, "longterm": 100})
        self.assertEqual(it.score_detail["llm_value"], 45.0)
        self.assertEqual(it.score_detail["value_capped_by"], "营销通稿")
        cfg = Config()
        self.assertEqual(it.score,
                         round(cfg.tier_weight * 90 + (1 - cfg.tier_weight) * 45.0, 1))

    def test_unknown_kind_does_not_cap(self):
        """模型偶尔乱答一个类型，不能因此把一条好内容压下去，只留痕。"""
        it = self._run_one("S", {"kind": "瞎编的类型", "relevance": 90,
                                 "novelty": 90, "longterm": 90})
        self.assertNotIn("value_capped_by", it.score_detail)
        self.assertIn("triage_kind_unknown", it.notes)
        self.assertEqual(it.status, "published")

    def test_kind_recorded_in_score_detail(self):
        it = self._run_one("A", {"kind": "一手发布", "relevance": 80,
                                 "novelty": 70, "longterm": 70})
        self.assertEqual(it.score_detail["kind"], "一手发布")

    def test_evaluate_never_writes_status(self):
        """离线评测和重打分只调 evaluate——它结构上就不该碰状态字段（D28）。"""
        it = _item("S")
        ctx = _ctx(True, {"kind": "一手发布", "relevance": 90, "novelty": 90, "longterm": 90})
        triage.evaluate(it, ctx.cfg, ctx.llm)
        self.assertGreater(it.score, 0)
        self.assertEqual(it.status, "new")
        self.assertEqual(it.auto_status, "")

    def test_focus_is_examples_not_whitelist(self):
        """关注方向是举例不是白名单——黄金集里 3 条具身智能就是被白名单口吻压低分的。"""
        self.assertIn("不是白名单", triage._SYSTEM)
        self.assertIn("方向窄不是低分的理由", triage._SYSTEM)
        for kw in ("具身智能", "世界模型", "多模态生成"):
            self.assertIn(kw, Config().focus)

    def test_tier_S_beats_tier_C_on_same_content_score(self):
        """同样的内容质量，信源等级决定命运——这就是分层的意义。"""
        scores = {"relevance": 70, "novelty": 70, "longterm": 70}
        s_item, c_item = self._run_one("S", scores), self._run_one("C", scores)
        self.assertGreater(s_item.score, c_item.score)


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
        for tier in ("B", "C"):
            self.assertEqual(self._run_degraded(tier).status, "review", tier)

    def test_degradation_is_recorded(self):
        it = self._run_degraded("S")
        self.assertIn("triage_degraded", " ".join(it.notes),
                      "所有降级必须留痕，否则无法审计")


class TestAutoStatusIsolation(unittest.TestCase):
    """auto_status 是评测唯一可信的对照面，必须与人工审批完全隔离（decisions.md D28）。"""

    def test_triage_always_records_auto_status(self):
        for tier, scores in [("S", {"relevance": 95, "novelty": 90, "longterm": 90}),
                             ("B", {"relevance": 55, "novelty": 50, "longterm": 45}),
                             ("C", {"relevance": 10, "novelty": 10, "longterm": 5})]:
            it = _item(tier)
            triage.run([it], _ctx(True, scores))
            self.assertEqual(it.auto_status, it.status,
                             f"{tier}: triage 结束时两者必须一致")
            self.assertIn(it.auto_status, ("published", "review", "discarded"))

    def test_auto_status_recorded_in_degraded_path(self):
        """降级路径（无 LLM）同样要记录，否则那批数据无法参与评测。"""
        it = _item("S")
        triage.run([it], _ctx(False))
        self.assertEqual(it.auto_status, "published")

    def test_auto_status_recorded_on_error(self):
        ctx = _ctx(True)
        ctx.llm.json_chat.side_effect = RuntimeError("崩了")
        it = _item("S")
        triage.run([it], ctx)
        self.assertEqual(it.auto_status, "review", "异常路径也必须留下系统原判")

    def test_human_approval_never_touches_auto_status(self):
        """核心不变量：人工审批只改 status。
        破坏它 = 评测退化成"人评人自己"（这个 bug 真实发生过，见 D28）。"""
        src = (pathlib.Path(__file__).parent.parent / "pipeline" / "hitl.py").read_text(
            encoding="utf-8")
        for stmt in re.findall(r"UPDATE items SET [^\"]+", src):
            self.assertNotIn("auto_status", stmt,
                             f"hitl.py 不允许写 auto_status：{stmt}")


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
