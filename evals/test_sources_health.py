"""信源体检的回归测试。

为什么值得写：体检是「沉默的失败」的唯一报警器（D22）。2026-09-16 升级前，
它对四个真实问题全部显示「正常」——Anthropic 镜像漏掉了 Fable 5.1 发布文、
Karpathy 沉默 138 天、Eugene Yan 沉默 87 天、Import AI 云端 14 次运行全失败。

跑：python3 -m unittest evals.test_sources_health -v
"""
import json
import pathlib
import tempfile
import unittest

import yaml

from pipeline.sources_health import STALE_DAYS, cloud_failures, stale_limit

ROOT = pathlib.Path(__file__).parent.parent
SOURCES = yaml.safe_load((ROOT / "pipeline" / "sources.yaml").read_text(
    encoding="utf-8"))["sources"]


class TestSourcesHealth(unittest.TestCase):
    """体检的两类新判断。旧版对下面这些真实情况全部显示「正常」。"""

    def test_cadence_catches_silence_tier_threshold_missed(self):
        # Karpathy：A 级旧阈值 180 天，沉默 138 天不报警；按自身节奏 45 天 → 90 天就该报
        self.assertEqual(stale_limit({"tier": "A", "expected_cadence_days": 45}), 90)
        # Anthropic 镜像：S 级旧阈值 30 天，15 天不报警；节奏 7 天 → 14 天就该报
        self.assertLess(stale_limit({"tier": "S", "expected_cadence_days": 7}), 15)

    def test_daily_sources_get_a_floor(self):
        """日更源不能 2 天没发就报警——周末停更是常态。"""
        self.assertEqual(stale_limit({"tier": "B", "expected_cadence_days": 1}), 7)

    def test_falls_back_to_tier_without_cadence(self):
        self.assertEqual(stale_limit({"tier": "C"}), STALE_DAYS["C"])

    def test_every_rss_source_declares_cadence(self):
        """新加 rss 源时忘了声明节奏，就会悄悄退回粗阈值。"""
        for s in SOURCES:
            if s["type"] == "rss" and s["tier"] != "X":
                self.assertIn("expected_cadence_days", s, s["name"])

    def test_cloud_only_failures_are_counted(self):
        """Import AI 本地 200、云端 403——只有运行记录里看得到。"""
        d = tempfile.mkdtemp()
        for i, v in enumerate(["failed: HTTPError", "failed: HTTPError", 3]):
            with open(f"{d}/2026090{i}-000000.json", "w", encoding="utf-8") as f:
                json.dump({"stats": {"fetch": {"per_source": {"Import AI": v, "OK源": 2}}}}, f)
        got = cloud_failures(d)
        self.assertEqual(got["Import AI"], (2, 3))
        self.assertEqual(got["OK源"], (0, 3))

    def test_no_active_source_uses_retired_tier(self):
        """D 级（待观察）已停用：新信源按它本身是什么定级，不能再放进 D。"""
        for s in SOURCES:
            self.assertIn(s["tier"], ("S", "A", "B", "C", "X"), s["name"])


if __name__ == "__main__":
    unittest.main()
