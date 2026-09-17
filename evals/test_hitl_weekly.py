"""每周审批与过期单子的回归测试（D37）。

为什么值得写：collect 把「没勾」当成「否决」。旧单子如果直接关掉，
人没看过的条目会被批量记成人工否决——伪造的人类判断会进入 feedback.jsonl，
再被当成真实反馈用于评测和信源建议。这是比 D28 更隐蔽的污染。

跑：python3 -m unittest evals.test_hitl_weekly -v
"""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pipeline import hitl
from pipeline.config import Config
from pipeline.db import DB
from pipeline.stages import triage


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _body(checked: dict, lane: str = None) -> str:
    """模拟 open 生成的清单：{item_id: 是否勾选}。lane=None 模拟老单子（没有 lane 段）"""
    lines = []
    for item_id, on in checked.items():
        lines.append(f"- [{'x' if on else ' '}] **[A]** [t](u) · `60` · s")
        tail = f";lane={lane}" if lane else ""
        lines.append(f"      {hitl.MARK}{item_id}{tail} -->")
    return "\n".join(lines)


class _Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.db = DB(os.path.join(d, "k.db"))
        # 每条一个信源：默认每信源配额是 3，同源会被截断，那是另一组测试的事
        for i, score in enumerate([55, 70, 62, 58], 1):
            self.db.conn.execute(
                "INSERT INTO items (id, title, url, source, tier, score, status, auto_status, "
                "horizon, published_at, score_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"a{i}", f"t{i}", f"u{i}", f"s{i}", "A", score, "review", "review",
                 "long", _iso(1), "{}"))
        self.db.conn.commit()
        self.fb = os.path.join(d, "feedback.jsonl")
        self.patches = [
            mock.patch.object(hitl, "DB", return_value=self.db),
            mock.patch.object(hitl, "FEEDBACK_PATH", self.fb),
            mock.patch.object(hitl, "write_stats_safe"),
            mock.patch.object(hitl, "_source_hint"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def status(self, item_id):
        return self.db.conn.execute(
            "SELECT status, auto_status FROM items WHERE id=?", (item_id,)).fetchone()

    def feedback(self):
        if not os.path.exists(self.fb):
            return []
        return [json.loads(l) for l in open(self.fb, encoding="utf-8")]


class TestPending(_Base):
    def test_limit_takes_highest_scores(self):
        got = [r["id"] for r in hitl._pending(self.db, 2)]
        self.assertEqual(got, ["a2", "a3"])

    def test_no_limit_returns_all(self):
        self.assertEqual(len(hitl._pending(self.db)), 4)


class TestCollect(_Base):
    def _run(self, issue):
        calls = []

        def gh(method, path, **kw):
            calls.append((method, path))
            return [issue] if method == "GET" else {}
        with mock.patch.object(hitl, "_gh", side_effect=gh):
            hitl.cmd_collect()
        return calls

    def test_normal_issue_unchecked_means_reject(self):
        """正常关闭的单子：人看过了，没勾就是不要。"""
        self._run({"number": 5, "labels": [{"name": hitl.LABEL}],
                   "body": _body({"a1": True, "a2": False})})
        self.assertEqual(self.status("a1")["status"], "published")
        self.assertEqual(self.status("a2")["status"], "discarded")
        self.assertEqual(len(self.feedback()), 2)

    def test_expired_issue_unchecked_stays_in_review(self):
        """过期单子：没勾的是没人看过，不能记成人工否决。"""
        self._run({"number": 2, "labels": [{"name": hitl.LABEL}, {"name": hitl.EXPIRED}],
                   "body": _body({"a1": True, "a2": False, "a3": False})})
        self.assertEqual(self.status("a1")["status"], "published")
        self.assertEqual(self.status("a2")["status"], "review")
        self.assertEqual(self.status("a3")["status"], "review")
        fb = self.feedback()
        self.assertEqual([f["decision"] for f in fb], ["approve"],
                         "过期单子只应留下真的被勾选的那条")

    def test_auto_status_never_touched(self):
        self._run({"number": 5, "labels": [], "body": _body({"a1": True, "a2": False})})
        self.assertEqual(self.status("a1")["auto_status"], "review")
        self.assertEqual(self.status("a2")["auto_status"], "review")


class TestLanes(_Base):
    """三块清单：勾选的含义不一样，回收时绝不能混。"""

    def setUp(self):
        super().setUp()
        # 两条自动发布的，用来测抽查
        for i in (1, 2):
            self.db.conn.execute(
                "INSERT INTO items (id, title, url, source, tier, score, status, auto_status,"
                " horizon, published_at, score_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"b{i}", f"pub{i}", f"pu{i}", "s", "S", 85, "published", "published",
                 "long", _iso(2), "{}"))
        self.db.conn.commit()

    def _collect(self, issue):
        with mock.patch.object(hitl, "_gh", side_effect=lambda m, p, **kw: [issue] if m == "GET" else {}):
            hitl.cmd_collect()

    def test_legacy_marker_defaults_to_queue(self):
        """老单子没有 lane 段（Issue #3 还活着），必须按待审处理。"""
        self._collect({"number": 5, "labels": [], "body": _body({"a1": True})})
        self.assertEqual(self.status("a1")["status"], "published")
        self.assertEqual(self.feedback()[0]["lane"], "queue")

    def test_audit_checked_retracts_without_touching_auto_status(self):
        self._collect({"number": 5, "labels": [], "body": _body({"b1": True}, "audit")})
        row = self.status("b1")
        self.assertEqual(row["status"], "discarded")
        self.assertEqual(row["auto_status"], "published", "撤下是人的判断，不能改系统原判")
        self.assertEqual(self.feedback()[0]["decision"], "retract")

    def test_audit_unchecked_records_kept(self):
        self._collect({"number": 5, "labels": [], "body": _body({"b1": False}, "audit")})
        self.assertEqual(self.status("b1")["status"], "published")
        self.assertIn("kept_at", hitl._hitl(dict(self.db.conn.execute(
            "SELECT extra FROM items WHERE id='b1'").fetchone())))
        self.assertEqual(self.feedback()[0]["decision"], "keep")

    def test_expired_issue_never_records_audit_keep(self):
        """过期单里的"留空"分不清是看过没问题还是没看——记成认可就是替人背书。"""
        self._collect({"number": 2, "labels": [{"name": hitl.EXPIRED}],
                       "body": _body({"b1": False}, "audit")})
        self.assertEqual(hitl._hitl(dict(self.db.conn.execute(
            "SELECT extra FROM items WHERE id='b1'").fetchone())), {})
        self.assertEqual(self.feedback(), [])

    def test_mass_check_guard(self):
        """抽查区整列勾选多半是手滑（另两块的肌肉记忆是"勾好的"）。
        只有 1-2 条时全勾可能是真的都该撤，所以守卫从 3 条起才生效。"""
        for i in (3, 4):
            self.db.conn.execute(
                "INSERT INTO items (id, title, url, source, tier, score, status, auto_status,"
                " horizon, published_at, score_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"b{i}", f"pub{i}", f"pu{i}", "s", "S", 85, "published", "published",
                 "long", _iso(2), "{}"))
        self.db.conn.commit()
        self._collect({"number": 5, "labels": [],
                       "body": _body({"b1": True, "b2": True, "b3": True, "b4": False}, "audit")})
        for i in (1, 2, 3, 4):
            self.assertEqual(self.status(f"b{i}")["status"], "published")
        self.assertEqual(self.feedback(), [])

    def test_recall_unchecked_declines_without_rejecting(self):
        self._collect({"number": 5, "labels": [], "body": _body({"a1": False}, "recall")})
        self.assertEqual(self.status("a1")["status"], "review", "捞回没勾 ≠ 人工否决")
        self.assertIn("recall_declined_at", hitl._hitl(dict(self.db.conn.execute(
            "SELECT extra FROM items WHERE id='a1'").fetchone())))
        self.assertNotIn("a1", [r["id"] for r in hitl._recall_pick(self.db, 5)])

    def test_feedback_rows_carry_lane(self):
        self._collect({"number": 5, "labels": [],
                       "body": _body({"a1": True}, "queue") + "\n" + _body({"b1": False}, "audit")})
        self.assertEqual({f["lane"] for f in self.feedback()}, {"queue", "audit"})

    def test_source_hint_ignores_non_queue_lanes(self):
        """抽查是高分段均匀抽样，和边缘区反馈不是一个分布——混进去会推翻 D27 的前提。"""
        with open(self.fb, "w", encoding="utf-8") as f:
            f.write(json.dumps({"item_id": "a1", "decision": "approve", "lane": "queue"}) + "\n")
            for _ in range(5):
                f.write(json.dumps({"item_id": "b1", "decision": "retract", "lane": "audit"}) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.patches[-1].stop()          # 这组测试平时把 _source_hint 挡掉了
            try:
                hitl._source_hint(self.db)
            finally:
                self.patches[-1].start()
        self.assertNotIn("0 / 5", buf.getvalue(), "audit 的 5 条撤下不该进边缘区统计")

    def test_audit_sample_is_reproducible(self):
        a = [r["id"] for r in hitl._audit_sample(self.db, 2, 202638)]
        self.assertEqual(a, [r["id"] for r in hitl._audit_sample(self.db, 2, 202638)])
        self.assertTrue(a)


class TestQueueRules(_Base):
    """队列有时效：容量以外的条目不假装还会被审，但也不算被否决。"""

    def _row(self, item_id, **kw):
        cols = {"id": item_id, "title": item_id, "url": item_id, "source": "q",
                "tier": "A", "score": 60, "status": "review", "auto_status": "review",
                "horizon": "short", "published_at": _iso(1), "score_detail": "{}", "extra": "{}"}
        cols.update(kw)
        self.db.conn.execute(
            f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            list(cols.values()))
        self.db.conn.commit()

    def test_stale_short_leaves_queue_but_long_stays(self):
        self._row("c1", horizon="short", published_at=_iso(30))
        self._row("c2", horizon="long", published_at=_iso(30))
        ids = [r["id"] for r in hitl._pending(self.db)]
        self.assertNotIn("c1", ids)
        self.assertIn("c2", ids)
        self.assertEqual(self.status("c1")["status"], "review", "出队不是丢弃")

    def test_below_bar_stays_review_and_goes_to_recall_pool(self):
        rescore = json.dumps({"rescore": {"prompt": triage.PROMPT_VERSION,
                                          "verdict": "discarded", "score": 30}})
        self._row("c3", extra=rescore, score=70)
        self.assertNotIn("c3", [r["id"] for r in hitl._pending(self.db)])
        self.assertEqual(self.status("c3")["status"], "review")
        self.assertIn("c3", [r["id"] for r, _ in hitl._excluded(self.db)])

    def test_rescore_version_string_matches_the_writer(self):
        """队列读的版本号必须和重打分工具写的是同一个常量。
        第一次就写差了：一处 "0.3.0"、一处 "triage-0.3.0"，于是 115 条不达标的照常排队，
        而且没有任何报错——典型的"指标不会报错，只会说谎"。"""
        src = (pathlib.Path(__file__).parent.parent / "tools"
               / "rescore_review_backlog.py").read_text(encoding="utf-8")
        self.assertIn("PROMPT = triage.PROMPT_VERSION", src)
        self.assertIn("triage.PROMPT_VERSION", (
            pathlib.Path(__file__).parent.parent / "pipeline" / "hitl.py"
        ).read_text(encoding="utf-8"))

    def test_stale_rescore_verdict_is_ignored(self):
        """换了打分标准之后，旧结论不能继续压着队列。"""
        self._row("c4", extra=json.dumps(
            {"rescore": {"prompt": "triage-0.0.1", "verdict": "discarded"}}))
        self.assertIn("c4", [r["id"] for r in hitl._pending(self.db)])

    def test_source_quota_limits_one_source_per_issue(self):
        for i in range(5):
            self._row(f"d{i}", source="量子位", score=70 + i)
        got = [r for r in hitl._pending(self.db) if r["source"] == "量子位"]
        self.assertEqual(len(got), Config().review_source_quota)

    def test_sorts_by_rescore_score_when_present(self):
        self._row("e1", score=51, extra=json.dumps(
            {"rescore": {"prompt": triage.PROMPT_VERSION, "verdict": "review", "score": 99}}))
        self.assertEqual(hitl._pending(self.db)[0]["id"], "e1")


class TestOpen(_Base):
    def _run(self, open_issues):
        calls = []

        def gh(method, path, **kw):
            calls.append((method, path, kw.get("json")))
            if method == "GET":
                return open_issues
            if method == "POST" and path == "/issues":
                return {"number": 99}
            return {}
        with mock.patch.object(hitl, "_gh", side_effect=gh), \
                mock.patch.object(hitl, "LANE_QUOTA", {"queue": 2, "audit": 0, "recall": 0}):
            hitl.cmd_open()
        return calls

    def test_fresh_issue_blocks_new_one(self):
        calls = self._run([{"number": 7, "created_at": _iso(2)}])
        self.assertFalse(any(m == "POST" and p == "/issues" for m, p, _ in calls))

    def test_stale_issue_is_expired_before_close_then_new_opened(self):
        """顺序是关键：先打过期标签再关，否则 collect 可能按普通单子处理。"""
        calls = self._run([{"number": 2, "created_at": _iso(18)}])
        seq = [(m, p) for m, p, _ in calls]
        i_label = seq.index(("POST", "/issues/2/labels"))
        i_close = seq.index(("PATCH", "/issues/2"))
        i_new = seq.index(("POST", "/issues"))
        self.assertLess(i_label, i_close)
        self.assertLess(i_close, i_new)
        label_payload = calls[i_label][2]
        self.assertEqual(label_payload["labels"], [hitl.EXPIRED])

    def test_new_issue_lists_only_the_batch(self):
        calls = self._run([])
        body = next(j for m, p, j in calls if m == "POST" and p == "/issues")["body"]
        self.assertEqual(body.count(hitl.MARK), 2)
        self.assertIn("a2", body)
        self.assertIn("a3", body)
        self.assertNotIn(f"{hitl.MARK}a4 ", body)


if __name__ == "__main__":
    unittest.main()
