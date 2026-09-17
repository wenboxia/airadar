"""分类体系的回归测试。

为什么值得写：类目表在代码里出现过不止一处。D25 给 classify 加「模型训练」时，
标注工具 review_golden.py 里的手抄列表没跟着改——人工标注选不到这个类，
而人工标注恰恰是用来评测 classify 的。这种漂移不会报错，只会让评测悄悄失真。

2026-09-17 改为平铺 8 类（D43）：6 个内容类由模型判断，「开源项目」「研究论文」按链接判定。

另一半是改版前分类快照（categories_v1 / categories_v2）的保护：它们只在 DB 里、不在 Item 里，
而 upsert_items 是 INSERT OR REPLACE——哪天历史条目被重新写入，快照就无声消失。

跑：python3 -m unittest evals.test_classify_taxonomy -v
"""
import importlib.util
import os
import pathlib
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from pipeline.config import Config
from pipeline.models import Context, Item
from pipeline.stages import classify, dedupe

ROOT = pathlib.Path(__file__).parent.parent


def _run(llm_out, url="https://example.com/a", llm_ok=True):
    cfg = Config()
    cfg.llm_workers = 1
    llm = Mock()
    llm.available.return_value = llm_ok
    llm.json_chat.return_value = llm_out
    it = Item(title="t", source="s", content="c", status="published", url=url)
    classify.run([it], Context(cfg=cfg, llm=llm, db=Mock(), run_id="test"))
    return it


class TestTaxonomy(unittest.TestCase):
    def test_flat_eight_categories(self):
        """主人定的平铺 8 类：模型与安全各合成一个，开源 / 论文保留。"""
        self.assertEqual(classify.CATEGORIES, [
            "模型", "Agent 与开发", "评测", "安全", "产品与应用", "行业动态", "开源项目", "研究论文"])
        self.assertEqual(len(set(classify.CATEGORIES)), 8)
        for gone in ("安全与对齐", "安全与防护", "模型发布", "模型训练", "论文"):
            self.assertNotIn(gone, classify.CATEGORIES)

    def test_priority_covers_exactly_content_categories(self):
        self.assertEqual(sorted(classify.PRIORITY), sorted(classify.CONTENT_CATEGORIES))
        self.assertEqual(classify.PRIORITY[-1], "行业动态", "行业动态只能兜底")

    def test_prompt_explains_every_content_category(self):
        """每个内容类都要有边界说明，三组易混的判定句都要在。"""
        for c in classify.CONTENT_CATEGORIES:
            self.assertIn(f"- {c}：", classify._SYS, f"prompt 没解释 {c}")
        for phrase in ("测模型做安全任务的能力算评测", "怎么搭、怎么用", "发布的东西本身是模型就归模型"):
            self.assertIn(phrase, classify._SYS)
        self.assertIn(" > ".join(classify.PRIORITY), classify._SYS)

    def test_rule_check_flags_stale_category(self):
        """漏回填的旧类名要在规则校验里报出来，而不是悄悄变成前端的一个筛选按钮。"""
        spec = importlib.util.spec_from_file_location("ev", ROOT / "evals" / "run_eval.py")
        ev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ev)
        base = {"url": "https://x", "title": "t", "source": "s", "tier": "A", "status": "review",
                "summary_short": "", "notes": ""}
        rows = [{**base, "id": "old", "categories": '["安全与对齐"]'},
                {**base, "id": "new", "categories": '["安全", "研究论文"]'},
                {**base, "id": "none", "categories": None}]
        problems = [p for p in ev.rule_checks(rows)["problems"] if "类目表" in p]
        self.assertEqual(len(problems), 1)
        self.assertIn("old", problems[0])

    def test_labeling_tool_shares_the_same_list(self):
        """标注工具必须和 pipeline 用同一份类目表，不许各抄一份。"""
        spec = importlib.util.spec_from_file_location(
            "review_golden", ROOT / "evals" / "review_golden.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIs(mod.CATEGORIES, classify.CATEGORIES)


class TestGenreByUrl(unittest.TestCase):
    """「开源项目」「研究论文」由链接判定，不让模型猜——旧体系里「论文」重分一次有 16% 会变。"""

    def test_github_repo_is_open_source(self):
        self.assertEqual(classify.genre_tags("https://github.com/SaladDay/pi-from-scratch"), ["开源项目"])

    def test_github_non_repo_pages_are_not(self):
        for u in ("https://github.blog/news/x", "https://github.com/features/copilot",
                  "https://github.com/trending", "https://github.com/SaladDay"):
            self.assertEqual(classify.genre_tags(u), [], u)

    def test_paper_hosts(self):
        for u in ("https://arxiv.org/abs/2609.03966v1", "https://openreview.net/forum?id=x",
                  "https://huggingface.co/papers/2609.1"):
            self.assertEqual(classify.genre_tags(u), ["研究论文"], u)
        self.assertEqual(classify.genre_tags("https://huggingface.co/blog/x"), [])

    def test_model_cannot_claim_genre(self):
        """模型输出「开源项目」当主类不算数。"""
        it = _run({"primary": "开源项目", "topics": [], "horizon": "short"})
        self.assertNotIn("开源项目", it.categories)
        self.assertIn("classify_degraded", " ".join(it.notes))


class TestClassifyOutput(unittest.TestCase):
    def test_primary_secondary_then_genre(self):
        it = _run({"primary": "Agent 与开发", "secondary": "评测", "topics": ["eval"], "horizon": "long"},
                  url="https://github.com/a/b")
        self.assertEqual(it.categories, ["Agent 与开发", "评测", "开源项目"])
        self.assertEqual(it.category, "Agent 与开发")

    def test_duplicate_or_invalid_secondary_dropped(self):
        self.assertEqual(_run({"primary": "安全", "secondary": "安全"}).categories, ["安全"])
        self.assertEqual(_run({"primary": "安全", "secondary": "安全与防护"}).categories, ["安全"])

    def test_legacy_list_format_still_parsed(self):
        it = _run({"categories": ["瞎编的类", "模型", "评测"]})
        self.assertEqual(it.categories, ["模型", "评测"])

    def test_degraded_leaves_content_empty(self):
        """没有模型时不猜内容类，只按链接打开源 / 论文，并留痕。"""
        it = _run({}, url="https://arxiv.org/abs/1", llm_ok=False)
        self.assertEqual(it.categories, ["研究论文"])
        self.assertEqual(it.horizon, "long")
        self.assertIn("classify_degraded", " ".join(it.notes))
        it = _run({}, llm_ok=False)
        self.assertEqual(it.categories, [])
        self.assertEqual(it.category, "")


class TestCategorySnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "k.db")

    def _db(self):
        from pipeline.db import DB
        return DB(self.path)

    def test_migration_adds_column_without_touching_data(self):
        """模拟一个改版前的老库：迁移只加列，不动任何已有值。"""
        from pipeline.db import SCHEMA
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)          # 改版前的 schema 里本来就没有 categories_v1
        conn.execute("INSERT INTO items (id, category, categories, status, auto_status) "
                     "VALUES ('a','论文','[\"论文\"]','review','review')")
        conn.commit()
        conn.close()

        db = self._db()
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(items)")}
        self.assertIn("categories_v1", cols)
        self.assertIn("categories_v2", cols)
        row = db.conn.execute("SELECT * FROM items WHERE id='a'").fetchone()
        self.assertEqual(row["categories"], '["论文"]')
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["auto_status"], "review")
        self.assertIsNone(row["categories_v1"], "迁移不该自己填值，由回填脚本负责")
        self.assertIsNone(row["categories_v2"])

    def test_backfill_never_writes_judgments(self):
        """回填只改分类。碰 status 是改人的判断，碰 auto_status 是改系统原判（D28）。"""
        src = (ROOT / "tools" / "backfill_categories.py").read_text(encoding="utf-8")
        stmts = re.findall(r"UPDATE items SET [^\"]+", src)
        self.assertTrue(stmts, "没找到 UPDATE 语句，测试失去意义")
        for stmt in stmts:
            self.assertNotRegex(stmt, r"\b(auto_)?status\s*=",
                                f"回填脚本不允许写判断字段：{stmt}")
            self.assertNotRegex(stmt, r"\b(horizon|topics)\s*=",
                                f"回填只改分类，horizon/topics 另有下游：{stmt}")

    def test_existing_items_never_reach_upsert(self):
        """categories_v1 不在 Item 里，upsert 会把它清空。
        它能活下来，全靠 dedupe 把库里已有的条目挡在 pipeline 外面。"""
        url = "https://example.com/a"
        existing_id = dedupe.item_id(url)
        db = Mock()
        db.existing_ids.return_value = {existing_id}
        cfg = Config()
        items = [Item(url=url, title="已入库的旧条目", tier="S"),
                 Item(url="https://example.com/b", title="新条目", tier="S")]
        kept = dedupe.run(items, Context(cfg=cfg, llm=Mock(), db=db, run_id="test"))
        self.assertNotIn(existing_id, {it.id for it in kept})
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
