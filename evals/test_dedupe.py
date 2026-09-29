"""去重的回归测试：同一篇 arXiv 论文换个链接写法，不能再被当成新内容。

真实事故：论文闸门 09-23 抓的是 `arxiv.org/abs/2609.22978v1`（接口返回的链接带版本号），
HN 09-27 链的是不带版本号的同一篇，标题也不一样——两条都自动发布了。
条目 id 是规范化 URL 的哈希，规范化没去版本号，两条 id 就不同。

另一半是库里的旧条目：它们的 id 是按旧规则算的，改规则时不回头改数据，
所以比对"库里有没有"要同时认存下来的 id 和按新规则重算的 id。

跑：python3 -m unittest evals.test_dedupe -v
"""
import hashlib
import os
import tempfile
import unittest
import unittest.mock
from unittest.mock import Mock

from pipeline.db import DB
from pipeline.models import Context, Item
from pipeline.stages import dedupe, fetch

DSEC = "https://arxiv.org/abs/2609.22978"


def _old_rule_id(url: str) -> str:
    """修复前的 id 算法：只去跟踪参数和末尾斜杠，版本号原样保留"""
    return hashlib.sha1(url.rstrip("/").encode()).hexdigest()[:16]


class TestCanonicalUrl(unittest.TestCase):
    def test_arxiv_variants_collapse_to_one(self):
        variants = [
            DSEC, DSEC + "v1", DSEC + "v12/", "http://arxiv.org/abs/2609.22978",
            "https://arxiv.org/pdf/2609.22978v2", "https://arxiv.org/pdf/2609.22978v2.pdf",
            "https://export.arxiv.org/abs/2609.22978", "https://www.arxiv.org/abs/2609.22978",
            "https://arxiv.org/html/2609.22978v1",
        ]
        self.assertEqual({dedupe.canonical_url(u) for u in variants}, {DSEC})
        self.assertEqual(len({dedupe.item_id(u) for u in variants}), 1)

    def test_different_papers_stay_different(self):
        self.assertNotEqual(dedupe.item_id(DSEC), dedupe.item_id("https://arxiv.org/abs/2609.22979"))

    def test_other_arxiv_pages_are_left_alone(self):
        url = "https://arxiv.org/list/cs.AI/recent"
        self.assertEqual(dedupe.canonical_url(url), url)

    def test_non_arxiv_rules_unchanged(self):
        self.assertEqual(dedupe.canonical_url("https://Example.com/a/?utm_source=x&q=1"),
                         "https://example.com/a?q=1")


class TestAgainstExistingRows(unittest.TestCase):
    """库里那条是按旧规则存的 id——比对时必须也能认出来。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.tmp.name, "k.db"))

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def _store(self, url: str, item_id: str):
        self.db.upsert_items([Item(id=item_id, url=url, title="stored", source="s",
                                   status="published", auto_status="published")], "t")

    def test_dedupe_recognises_versioned_row_stored_under_old_rule(self):
        """复现 DSec：库里是论文闸门存的 v1 链接（旧规则 id），HN 又来了不带版本号的。"""
        self._store(DSEC + "v1", _old_rule_id(DSEC + "v1"))
        ctx = Context(cfg=Mock(), llm=Mock(), db=self.db, run_id="t")
        kept = dedupe.run([Item(url=DSEC, title="DSec on HN", source="Hacker News", tier="A")], ctx)
        self.assertEqual(kept, [])
        self.assertEqual(ctx.stats["dedupe"]["dropped_already_in_db"], 1)

    def test_paper_gate_skips_paper_already_linked_without_version(self):
        """反过来：库里已有不带版本号的，论文闸门从接口拿回的是带 v1 的。"""
        self._store(DSEC, _old_rule_id(DSEC))
        src = Item(title="post", source="量子位", url="https://a.com/1")
        src.extra["outbound"] = {"arxiv": ["2609.22978"], "links": []}
        paper = Item(title="DSec Technical Report", url=DSEC + "v1", extra={"authors": ["DeepSeek-AI"]},
                     published_at="2099-01-01T00:00:00+00:00")
        ctx = Context(cfg=Mock(short_ttl_days=14), llm=Mock(), db=self.db, run_id="t")
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id", lambda ids, s: [paper]):
            got = fetch.collect_mentions([src], self.db, {"name": "论文（被提及）", "tier": "A"}, ctx)
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
