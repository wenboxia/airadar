"""黄金集 v2 数据本身的回归测试（D42）。

为什么值得写：黄金集是评测唯一的标准答案，它的每一条都该说得清"谁判断的、理由从哪来"。
这次的标注是语音聊天里完成的，Word 里 GPT 润色的理由 94/100 条加了主人没说过的内容——
理由的来源一旦说不清，面试时被追问"这条你为什么收"就答不上。

跑：python3 -m unittest evals.test_golden_set -v
"""
import json
import pathlib
import unittest

GS = pathlib.Path(__file__).parent / "golden_set"


def _jsonl(name):
    with open(GS / name, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip() and not l.startswith("//")]


GOLDEN = _jsonl("golden.jsonl")
EVIDENCE = json.loads((GS / "golden_v2_extraction.json").read_text(encoding="utf-8"))["items"]
V2 = [g for g in GOLDEN if g.get("labeled_via") == "voice-gpt-v2"]


class TestGoldenSet(unittest.TestCase):
    def test_basic_shape(self):
        self.assertEqual(len({g["url"] for g in GOLDEN}), len(GOLDEN), "URL 不能重复")
        for g in GOLDEN:
            self.assertIsInstance(g["include"], bool, g["title"])
            self.assertTrue(g["note"].strip(), f"理由不能为空：{g['title']}")

    def test_no_category_labels_by_design(self):
        """主人决定黄金集不标分类（分类准确率改用官网自带标签测）。
        这里一旦出现分类，run_eval 会拿它算分类准确率——那不是主人标的。"""
        for g in GOLDEN:
            self.assertEqual(g.get("categories"), [], g["title"])
            self.assertFalse(g.get("category"), g["title"])

    def test_every_v2_note_traceable(self):
        """每条 v2 理由都能在证据文件里找到来源；补进去的 GPT 要点必须写出处。"""
        by_url = {e["url"]: e for e in EVIDENCE}
        self.assertEqual(len(V2), 100)
        for g in V2:
            e = by_url[g["url"]]
            self.assertEqual(g["include"], e["decision"] == "y", g["title"])
            self.assertEqual(g["note"], e["note"], g["title"])
            for p in g["note_ai_points"]:
                self.assertTrue(p["source"].strip(), f"补点没有出处：{g['title']}")
            if "AI补充" in g["note_source"]:
                self.assertTrue(g["note_ai_points"], f"标了 AI补充 却没列补点：{g['title']}")

    def test_source_labels_do_not_leak_into_notes(self):
        """来源记在 note_source 字段里，不写进理由正文。"""
        for g in GOLDEN:
            self.assertNotIn("采纳 AI", g["note"])
            self.assertNotIn("采纳AI", g["note"])

    def test_gpt_polished_version_kept(self):
        """Word 里 GPT 的版本留痕，不删（黄金集只增不删）。"""
        for g in V2:
            self.assertTrue(g.get("note_docx"), g["title"])

    def test_evidence_has_no_private_paths(self):
        """证据文件是公开的；聊天记录全文只留本地。"""
        text = (GS / "golden_v2_extraction.json").read_text(encoding="utf-8")
        for bad in ("/Users/", ".codex/", "Realtime transcript"):
            self.assertNotIn(bad, text)

    def test_v1_reuse_excludes_retired_and_conflicting(self):
        """v1 复用不能带进已停抓的 arXiv，也不能带进和 v2 判断冲突的两条。"""
        reused = [g for g in GOLDEN if g.get("labeled_via") == "v1-reused"]
        self.assertEqual(len(reused), 11)
        for g in reused:
            self.assertNotEqual(g["source"], "arXiv Agent/LLM")
            self.assertTrue(g.get("note_v1_raw"), "v1 原文要留痕")
        titles = " ".join(g["title"] for g in reused)
        self.assertNotIn("Breaking Claude Code", titles)
        self.assertNotIn("Multi-Vector Embedding", titles)


if __name__ == "__main__":
    unittest.main()
