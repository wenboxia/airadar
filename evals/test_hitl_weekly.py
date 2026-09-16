"""每周审批与过期单子的回归测试（D37）。

为什么值得写：collect 把「没勾」当成「否决」。旧单子如果直接关掉，
人没看过的条目会被批量记成人工否决——伪造的人类判断会进入 feedback.jsonl，
再被当成真实反馈用于评测和信源建议。这是比 D28 更隐蔽的污染。

跑：python3 -m unittest evals.test_hitl_weekly -v
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pipeline import hitl
from pipeline.db import DB


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _body(checked: dict) -> str:
    """模拟 open 生成的清单：{item_id: 是否勾选}。id 必须是十六进制，和真实 id 一致"""
    lines = []
    for item_id, on in checked.items():
        lines.append(f"- [{'x' if on else ' '}] **[A]** [t](u) · `60` · s")
        lines.append(f"      {hitl.MARK}{item_id} -->")
    return "\n".join(lines)


class _Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.db = DB(os.path.join(d, "k.db"))
        for i, score in enumerate([55, 70, 62, 58], 1):
            self.db.conn.execute(
                "INSERT INTO items (id, title, url, source, tier, score, status, auto_status, "
                "score_detail) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"a{i}", f"t{i}", f"u{i}", "s", "A", score, "review", "review", "{}"))
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
                mock.patch.object(hitl, "REVIEW_BATCH", 2):
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
