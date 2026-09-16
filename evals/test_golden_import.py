"""Word 标注表回填的回归测试。

为什么值得写：这是人工判断进入评测体系的唯一入口。填错的格式被静默吞掉，
等于把主人的判断弄丢；错误的分类被静默接受，评测就拿着不存在的类目在比。

跑：python3 -m unittest evals.test_golden_import -v
"""
import importlib.util
import pathlib
import unittest

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
        rec, err = imp.to_record(DRAFT, "Y", "落地案例、产品与商业", "理由")
        self.assertIsNone(err)
        self.assertTrue(rec["include"])
        self.assertEqual(rec["categories"], ["落地案例", "产品与商业"])
        self.assertEqual(rec["category"], "落地案例")

    def test_exclude_needs_no_category(self):
        rec, err = imp.to_record(DRAFT, "n", "", "营销稿")
        self.assertIsNone(err)
        self.assertFalse(rec["include"])

    def test_chinese_answers_accepted(self):
        self.assertTrue(imp.to_record(DRAFT, "是", "论文", "")[0]["include"])
        self.assertFalse(imp.to_record(DRAFT, "否", "", "")[0]["include"])

    def test_unknown_flag_is_reported_not_guessed(self):
        rec, err = imp.to_record(DRAFT, "maybe", "论文", "")
        self.assertIsNone(rec)
        self.assertIn("只认 y / n", err)

    def test_unknown_category_rejected(self):
        """旧类目（比如拆分前的叫法）或错别字都不能进黄金集。"""
        rec, err = imp.to_record(DRAFT, "y", "安全, 论文", "")
        self.assertIsNone(rec)
        self.assertIn("安全", err)

    def test_include_without_category_rejected(self):
        rec, err = imp.to_record(DRAFT, "y", "", "")
        self.assertIsNone(rec)
        self.assertIn("没填分类", err)

    def test_at_most_three_categories(self):
        rec, _ = imp.to_record(DRAFT, "y", "论文，模型训练，评测与基准，开源项目", "")
        self.assertEqual(len(rec["categories"]), 3)


if __name__ == "__main__":
    unittest.main()
