"""论文入口与外链抽取的回归测试（D36 的入口 1/2）。

为什么值得写：论文一度占自动发布的 44%，根因是排序键错了——按提交时间从每天几百篇里
取 15 篇等于随机抽样。改成"别处提到才抓"之后，闸门就是这里的两条规则：
被 2 个以上独立信源提到，或本身是实验室技术报告。闸门松了，arXiv 就等于又接了回来。

另一半是外链抽取：`get_text()` 曾把所有 href 剥掉，于是"谁提到了哪篇论文"这个信号
根本不存在，交叉引用率也测不准（D36 里认领的第一个错误）。

跑：python3 -m unittest evals.test_paper_entry -v
"""
import pathlib
import unittest
import unittest.mock
from unittest.mock import Mock

from pipeline.models import Context, Item
from pipeline.stages import dedupe, fetch


class TestOutboundLinks(unittest.TestCase):
    def test_extracts_arxiv_ids_from_href_and_plain_text(self):
        html = ('<article>见 <a href="https://arxiv.org/abs/2601.01234">这篇</a>，'
                '另 https://arxiv.org/pdf/2602.05555v2 也相关</article>')
        out = fetch.outbound_links(html)
        self.assertEqual(out["arxiv"], ["2601.01234", "2602.05555"])

    def test_keeps_only_absolute_links(self):
        out = fetch.outbound_links('<a href="/relative">x</a><a href="https://a.com">y</a>')
        self.assertEqual(out["links"], ["https://a.com"])

    def test_empty_html_is_safe(self):
        self.assertEqual(fetch.outbound_links(""), {"arxiv": [], "links": []})


class TestLabReport(unittest.TestCase):
    """实验室技术报告本质是产品发布，不是普通预印本。"""

    def _item(self, title="x", authors=()):
        it = Item(title=title)
        it.extra["authors"] = list(authors)
        return it

    def test_technical_report_by_title(self):
        self.assertTrue(fetch._is_lab_report(self._item("DeepSeek-V3 Technical Report")))
        self.assertTrue(fetch._is_lab_report(self._item("GPT-5 System Card")))

    def test_many_authors(self):
        self.assertTrue(fetch._is_lab_report(self._item(authors=[f"a{i}" for i in range(25)])))

    def test_lab_affiliation_in_authors(self):
        self.assertTrue(fetch._is_lab_report(self._item(authors=["DeepSeek-AI"])))

    def test_ordinary_preprint_is_not(self):
        self.assertFalse(fetch._is_lab_report(
            self._item("A Study of Prompting", ["Alice", "Bob"])))


class TestSourceDispatch(unittest.TestCase):
    def test_mentions_source_is_not_dispatched_as_a_fetcher(self):
        """它不是"去哪里抓"，是跑完别的信源后才执行的闸门。
        第一次就漏了：run() 把它当普通信源派发，报"未知信源类型"，论文入口一次没跑过。"""
        src = (pathlib.Path(__file__).parent.parent / "pipeline" / "stages"
               / "fetch.py").read_text(encoding="utf-8")
        todo_line = next(l for l in src.splitlines() if "todo = [s for s in sources" in l)
        self.assertIn("arxiv_mentions", todo_line)
        self.assertNotIn("arxiv_mentions", fetch._FETCHERS)


class TestMentionGate(unittest.TestCase):
    """闸门：只有被 2 个以上独立信源提到、或是实验室报告的论文才进来。"""

    SRC = {"name": "论文（被提及）", "tier": "A", "max_results": 5}

    def _ctx(self):
        return Context(cfg=Mock(), llm=Mock(), db=Mock(), run_id="t")

    def _items(self, mentions: dict):
        """mentions = {信源名: [论文 id]}"""
        out = []
        for source, ids in mentions.items():
            it = Item(title=source, source=source, url=f"https://{source}.com/a")
            it.extra["outbound"] = {"arxiv": ids, "links": []}
            out.append(it)
        return out

    def _papers(self, *specs):
        def fake(ids, src):
            return [Item(title=t, url=f"https://arxiv.org/abs/{pid}",
                         extra={"authors": list(a)}) for pid, t, a in specs if pid in ids]
        return fake

    def test_single_mention_of_ordinary_paper_is_dropped(self):
        db = Mock(); db.existing_ids.return_value = set()
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00001", "A Study", ["Alice"]))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00001"]}), db, self.SRC, self._ctx())
        self.assertEqual(got, [], "只有一个信源提到的普通预印本不该进来")

    def test_two_independent_sources_pass(self):
        db = Mock(); db.existing_ids.return_value = set()
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00001", "A Study", ["Alice"]))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00001"], "Simon Willison": ["2601.00001"]}),
                db, self.SRC, self._ctx())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].extra["mentioned_by"], ["Simon Willison", "量子位"])

    def test_lab_report_passes_on_single_mention(self):
        db = Mock(); db.existing_ids.return_value = set()
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00002", "DeepSeek-V4 Technical Report", ["DeepSeek-AI"]))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00002"]}), db, self.SRC, self._ctx())
        self.assertEqual(len(got), 1)

    def test_already_in_db_is_skipped(self):
        db = Mock()
        db.existing_ids.return_value = {dedupe.item_id("https://arxiv.org/abs/2601.00003")}
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00003", "X Technical Report", ["OpenAI"]))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00003"]}), db, self.SRC, self._ctx())
        self.assertEqual(got, [])

    def test_no_mentions_costs_nothing(self):
        db = Mock()
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id",
                                        Mock(side_effect=AssertionError("不该调用"))):
            self.assertEqual(fetch.collect_mentions([], db, self.SRC, self._ctx()), [])


if __name__ == "__main__":
    unittest.main()
