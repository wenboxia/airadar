"""分类体系的回归测试。

为什么值得写：类目表在代码里出现过不止一处。D25 给 classify 加「模型训练」时，
标注工具 review_golden.py 里的手抄列表没跟着改——人工标注选不到这个类，
而人工标注恰恰是用来评测 classify 的。这种漂移不会报错，只会让评测悄悄失真。

另一半是 categories_v1（改版前的分类快照）的保护：它只在 DB 里、不在 Item 里，
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


class TestTaxonomy(unittest.TestCase):
    def test_split_and_new_categories_present(self):
        """2026-09-16 改版：安全拆成对齐/防护两类，新增落地案例。"""
        for c in ("安全与对齐", "安全与防护", "落地案例"):
            self.assertIn(c, classify.CATEGORIES)

    def test_fallback_uses_only_valid_categories(self):
        """改类目表时最容易漏改兜底表——无 LLM 时会写出取值域外的分类。"""
        for source, (cat, horizon) in classify._FALLBACK.items():
            self.assertIn(cat, classify.CATEGORIES, f"{source} 的兜底类目已不存在")
            self.assertIn(horizon, ("short", "long"))
        self.assertIn("行业动态", classify.CATEGORIES, "_fallback 的默认值")

    def test_prompt_explains_every_new_category(self):
        """新类目光在列表里不够——模型分不清「落地案例」和「产品与商业」。"""
        for c in ("安全与对齐", "安全与防护", "落地案例", "产品与商业"):
            self.assertIn(f"「{c}」指", classify._SYS, f"prompt 没解释 {c} 的边界")

    def test_labeling_tool_shares_the_same_list(self):
        """标注工具必须和 pipeline 用同一份类目表，不许各抄一份。"""
        spec = importlib.util.spec_from_file_location(
            "review_golden", ROOT / "evals" / "review_golden.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIs(mod.CATEGORIES, classify.CATEGORIES)

    def test_llm_output_outside_taxonomy_is_dropped(self):
        """模型编出不存在的类目时要过滤掉，全被过滤则走兜底。"""
        cfg = Config()
        cfg.llm_workers = 1
        llm = Mock()
        llm.available.return_value = True
        llm.json_chat.return_value = {"categories": ["瞎编的类", "落地案例"],
                                      "topics": ["x"], "horizon": "long"}
        it = Item(title="t", source="s", content="c", status="published")
        classify.run([it], Context(cfg=cfg, llm=llm, db=Mock(), run_id="test"))
        self.assertEqual(it.categories, ["落地案例"])


class TestCategoriesV1Snapshot(unittest.TestCase):
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
        row = db.conn.execute("SELECT * FROM items WHERE id='a'").fetchone()
        self.assertEqual(row["categories"], '["论文"]')
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["auto_status"], "review")
        self.assertIsNone(row["categories_v1"], "迁移不该自己填值，由回填脚本负责")

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
