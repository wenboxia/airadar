"""论文入口与外链抽取的回归测试（D36 的入口 1/2）。

为什么值得写：论文一度占自动发布的 44%，根因是排序键错了——按提交时间从每天几百篇里
取 15 篇等于随机抽样。改成"别处提到才抓"之后，闸门就是这里的两条规则：
被 2 个以上独立信源提到，或是 14 天内发布的实验室技术报告。闸门松了，arXiv 就等于又接了回来。

另一半是外链抽取：`get_text()` 曾把所有 href 剥掉，于是"谁提到了哪篇论文"这个信号
根本不存在，交叉引用率也测不准（D36 里认领的第一个错误）。

跑：python3 -m unittest evals.test_paper_entry -v
"""
import pathlib
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
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

    def test_many_authors_survives_truncation(self):
        """名单入库只存前 8 个，人数另记。上一版直接数截断后的名单，
        「25 人以上」这条在线上永远是假——测试却因为直接传 25 个名字而一直通过。
        这里走真实的解析路径：30 个作者的 Atom 响应进去，判定必须还是 True。"""
        names = "".join(f"<author><name>Person {i}</name></author>" for i in range(30))
        atom = (b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<id>http://arxiv.org/abs/2609.00001v1</id>'
                b'<link href="http://arxiv.org/abs/2609.00001v1" rel="alternate"/>'
                b'<published>2026-09-15T00:00:00Z</published>'
                b'<title>A Big Model</title><summary>s</summary>'
                + names.encode() + b'</entry></feed>')
        resp = Mock(content=atom); resp.raise_for_status = Mock()
        with unittest.mock.patch.object(fetch.requests, "get", return_value=resp):
            papers = fetch.fetch_arxiv_by_id(["2609.00001"], {"name": "论文（被提及）", "tier": "A"})
        self.assertEqual(len(papers[0].extra["authors"]), 8)
        self.assertEqual(papers[0].extra["author_count"], 30)
        self.assertTrue(fetch._is_lab_report(papers[0]))

    def test_lab_affiliation_in_authors(self):
        for org in ("DeepSeek-AI", "Qwen Team", "Kimi Team", "OpenAI", "Google DeepMind", "Moonshot AI"):
            self.assertTrue(fetch._is_lab_report(self._item(authors=["Alice", org])), org)

    def test_person_names_are_not_labs(self):
        """机构名曾在拼起来的人名里做子串匹配：Kimia、Kimin、Baichuan 都被当成实验室，
        单个信源提到就放行。现在只认整个署名条目是机构名"""
        for name in ("Kimia Nadjahi", "Kimin Lee", "Baichuan Huang", "Kimihiro Hasegawa"):
            self.assertFalse(fetch._is_lab_report(self._item(authors=[name, "Pieter Abbeel"])), name)
        self.assertFalse(fetch._is_lab_report(self._item(authors=["Andrea Meta", "Aiden Smith"])),
                         "两个人名拼起来不能凑出 meta ai")

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
        return Context(cfg=Mock(short_ttl_days=14), llm=Mock(), db=Mock(), run_id="t")

    def _items(self, mentions: dict):
        """mentions = {信源名: [论文 id]}"""
        out = []
        for source, ids in mentions.items():
            it = Item(title=source, source=source, url=f"https://{source}.com/a")
            it.extra["outbound"] = {"arxiv": ids, "links": []}
            out.append(it)
        return out

    @staticmethod
    def _days_ago(n):
        return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")

    def _papers(self, *specs):
        """spec = (id, 标题, 作者[, 发布时间])；不给时间就当作昨天发布"""
        def fake(ids, src):
            return [Item(title=s[1], url=f"https://arxiv.org/abs/{s[0]}",
                         published_at=s[3] if len(s) > 3 else self._days_ago(1),
                         extra={"authors": list(s[2])}) for s in specs if s[0] in ids]
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

    def test_new_paper_is_not_crowded_out_by_old_references(self):
        """接口一次只取 30 个。按升序截断时名额先给了参考文献里的老论文——
        一篇长文引 36 篇旧作，当天被两个信源讨论的新论文根本没被请求"""
        db = Mock(); db.existing_ids.return_value = set()
        old = [f"2301.{i:05d}" for i in range(36)]
        mentions = {"Lilian Weng": old, "Hacker News": ["2609.00001"], "Interconnects": ["2609.00001"]}
        requested = []

        def fake(ids, src):
            requested.extend(ids[:30])
            return self._papers(("2609.00001", "A Study", ["Alice"]))(ids[:30], src)
        ctx = self._ctx()
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id", fake):
            got = fetch.collect_mentions(self._items(mentions), db, self.SRC, ctx)
        self.assertIn("2609.00001", requested)
        self.assertEqual([p.title for p in got], ["A Study"])
        self.assertEqual(ctx.stats["fetch"]["arxiv_mentions"]["not_requested"], 7)

    def test_old_lab_report_needs_two_sources(self):
        """技术报告那条只放新发布：09-18 HN 一帖顺手引用了 2025-01 的 DeepSeek-R1，
        它被当成新闻自动发布了。老报告要进来，得和普通论文一样被两个以上信源提到。"""
        db = Mock(); db.existing_ids.return_value = set()
        old = ("2501.12948", "DeepSeek-R1", ["DeepSeek-AI"], "2025-01-22T15:19:35+00:00")
        ctx = self._ctx()
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id", self._papers(old)):
            got = fetch.collect_mentions(
                self._items({"Hacker News": ["2501.12948"]}), db, self.SRC, ctx)
        self.assertEqual(got, [])
        self.assertEqual(ctx.stats["fetch"]["arxiv_mentions"]["stale_reports"], 1)
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id", self._papers(old)):
            got = fetch.collect_mentions(
                self._items({"Hacker News": ["2501.12948"], "Interconnects": ["2501.12948"]}),
                db, self.SRC, self._ctx())
        self.assertEqual(len(got), 1, "老论文今天被两个信源讨论，照样该收（D36）")

    def test_lab_report_without_date_is_not_waved_through(self):
        """拿不到发布日期时按"很老"处理——降级方向是收紧"""
        db = Mock(); db.existing_ids.return_value = set()
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00009", "X Technical Report", ["OpenAI"], ""))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00009"]}), db, self.SRC, self._ctx())
        self.assertEqual(got, [])

    def test_already_in_db_is_skipped(self):
        db = Mock()
        db.existing_ids.return_value = {dedupe.item_id("https://arxiv.org/abs/2601.00003")}
        with unittest.mock.patch.object(
                fetch, "fetch_arxiv_by_id",
                self._papers(("2601.00003", "X Technical Report", ["OpenAI"]))):
            got = fetch.collect_mentions(
                self._items({"量子位": ["2601.00003"]}), db, self.SRC, self._ctx())
        self.assertEqual(got, [])

    def test_no_mentions_costs_nothing_but_leaves_a_trace(self):
        """没人提论文时不调接口，但必须留下"跑过、0 篇"的记录——
        否则运行记录里分不清"今天没人提"和"入口根本没跑"（09-18 的运行就是这样）。"""
        db = Mock()
        ctx = Context(cfg=Mock(), llm=Mock(), db=db, run_id="t", stats={})
        with unittest.mock.patch.object(fetch, "fetch_arxiv_by_id",
                                        Mock(side_effect=AssertionError("不该调用"))):
            self.assertEqual(fetch.collect_mentions([], db, self.SRC, ctx), [])
        self.assertEqual(ctx.stats["fetch"]["arxiv_mentions"]["seen"], 0)

    def test_run_keeps_the_gate_stats(self):
        """run() 结尾曾整个赋值 ctx.stats["fetch"]，把闸门写的统计冲掉了——
        闸门上线后第一次云端运行（09-18）实际放进 1 篇，运行记录里却查不到。"""
        yml = ("sources:\n"
               "  - {name: 甲, tier: B, type: fake}\n"
               "  - {name: 论文（被提及）, tier: A, type: arxiv_mentions, max_results: 5}\n")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write(yml)
        src_item = Item(title="t", source="甲", url="https://a.com/1")
        src_item.extra["outbound"] = {"arxiv": ["2601.00001"], "links": []}
        cfg = Mock(since_days=2, per_source_limit=0, max_items_per_run=0, short_ttl_days=14)
        db = Mock(); db.existing_ids.return_value = set()
        ctx = Context(cfg=cfg, llm=Mock(), db=db, run_id="t")
        with unittest.mock.patch.object(fetch, "SOURCES_PATH", fh.name), \
                unittest.mock.patch.dict(fetch._FETCHERS, {"fake": lambda s, since, c: [src_item]}), \
                unittest.mock.patch.object(fetch, "fetch_arxiv_by_id",
                                           self._papers(("2601.00001", "A Study", ["Alice"]))):
            fetch.run([], ctx)
        pathlib.Path(fh.name).unlink()
        self.assertEqual(ctx.stats["fetch"]["arxiv_mentions"],
                         {"seen": 1, "fetched": 1, "kept": 0, "stale_reports": 0, "not_requested": 0})
        self.assertEqual(ctx.stats["fetch"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
