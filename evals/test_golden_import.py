"""Word 标注表回填的回归测试。

为什么值得写：这是人工判断进入评测体系的唯一入口。填错的格式被静默吞掉，
等于把主人的判断弄丢；错误的分类被静默接受，评测就拿着不存在的类目在比。

跑：python3 -m unittest evals.test_golden_import -v
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("imp", ROOT / "evals" / "import_golden_docx.py")
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)

DRAFT = {"url": "https://x", "title": "t", "source": "s", "tier": "A"}


class TestToRecord(unittest.TestCase):
    def test_blank_row_is_not_an_error(self):
        """分几次填是正常的，没填的行既不导入也不报错。"""
        self.assertEqual(imp.to_record(DRAFT, "", "", ""), (None, None))

    def test_include_with_categories(self):
        rec, err = imp.to_record(DRAFT, "Y", "产品与应用、行业动态", "理由")
        self.assertIsNone(err)
        self.assertTrue(rec["include"])
        self.assertEqual(rec["categories"], ["产品与应用", "行业动态"])
        self.assertEqual(rec["category"], "产品与应用")

    def test_exclude_needs_no_category(self):
        rec, err = imp.to_record(DRAFT, "n", "", "营销稿")
        self.assertIsNone(err)
        self.assertFalse(rec["include"])

    def test_chinese_answers_accepted(self):
        self.assertTrue(imp.to_record(DRAFT, "是", "研究论文", "")[0]["include"])
        self.assertFalse(imp.to_record(DRAFT, "否", "", "")[0]["include"])

    def test_unknown_flag_is_reported_not_guessed(self):
        rec, err = imp.to_record(DRAFT, "maybe", "研究论文", "")
        self.assertIsNone(rec)
        self.assertIn("只认 y / n", err)

    def test_unknown_category_rejected(self):
        """旧类目（比如合并前的叫法）或错别字都不能进黄金集。"""
        rec, err = imp.to_record(DRAFT, "y", "安全与防护, 研究论文", "")
        self.assertIsNone(rec)
        self.assertIn("安全与防护", err)
        self.assertNotIn("研究论文", err)

    def test_include_without_category_accepted(self):
        """黄金集不标分类（D43），收录但分类留空是正常的。"""
        rec, err = imp.to_record(DRAFT, "y", "", "")
        self.assertIsNone(err)
        self.assertEqual(rec["categories"], [])

    def test_at_most_three_categories(self):
        rec, _ = imp.to_record(DRAFT, "y", "研究论文，模型，评测，开源项目", "")
        self.assertEqual(len(rec["categories"]), 3)



class TestReimport(unittest.TestCase):
    """分几次填、分几次导：改过的行要生效，没变的不重复写，旧记录作废留痕。"""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.out = os.path.join(d, "golden.jsonl")
        draft = os.path.join(d, "draft.jsonl")
        with open(draft, "w", encoding="utf-8") as f:
            for i in (1, 2):
                f.write(json.dumps({"url": f"https://u{i}", "title": f"标题{i}",
                                    "source": "s", "tier": "A"}, ensure_ascii=False) + "\n")
        self.patches = [mock.patch.object(imp, "OUT", self.out),
                        mock.patch.object(imp, "DRAFT", draft),
                        mock.patch.object(imp.sys, "argv", ["x", "unused.docx"])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _import(self, rows):
        with mock.patch.object(imp, "parse_rows", return_value=rows), \
                contextlib.redirect_stdout(io.StringIO()):
            imp.main()
        return imp._read_jsonl(self.out)

    def test_changed_row_replaces_old_one(self):
        self._import([(1, "标题1", "y", "研究论文", "先收"), (2, "标题2", "", "", "")])
        recs = self._import([(1, "标题1", "n", "", "改主意了"), (2, "标题2", "y", "模型", "新填")])
        live = [r for r in recs if not r.get("deprecated")]
        self.assertEqual(len(live), 2)
        self.assertFalse(next(r for r in live if r["url"] == "https://u1")["include"])
        old = [r for r in recs if r.get("deprecated")]
        self.assertEqual(len(old), 1, "旧判断要留痕，不能删")
        self.assertTrue(old[0]["include"])

    def test_unchanged_row_not_duplicated(self):
        rows = [(1, "标题1", "y", "研究论文", "理由")]
        self._import(rows)
        self.assertEqual(len(self._import(rows)), 1)

    def test_eval_ignores_deprecated(self):
        """评测只能看最新那条，否则改过的条目会被算两次。"""
        _spec2 = importlib.util.spec_from_file_location("ev", ROOT / "evals" / "run_eval.py")
        ev = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(ev)
        with open(self.out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"url": "https://u1", "include": True, "deprecated": True}) + "\n")
            f.write(json.dumps({"url": "https://u1", "include": False}) + "\n")
        rows = [{"id": "x", "url": "https://u1", "status": "published", "auto_status": "published",
                 "categories": "[]", "category": ""}]
        with mock.patch.object(ev, "GOLDEN_PATH", self.out):
            g = ev.golden_compare(rows)
        self.assertEqual(g["labeled"], 1)


if __name__ == "__main__":
    unittest.main()
