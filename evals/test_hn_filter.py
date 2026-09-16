"""HN 关键词过滤的回归测试。

为什么值得写：HN 是「社区在讨论什么」的唯一入口，过滤器漏掉的东西不会报错，
只会在知识库里安静地缺席。2026-09-16 用近 30 天真实数据复盘，旧过滤器把
《Nvidia agrees to acquire Hugging Face》1988 分、本项目自己在用的《GLM-5.3-Flash》
1132 分都拦掉了——原因是复数匹配不上、关键词表只认美国三家。

夹具标题全部取自那次复盘的真实 HN 帖子。

跑：python3 -m unittest evals.test_hn_filter -v
"""
import pathlib
import unittest

import yaml

from pipeline.stages.fetch import title_words

ROOT = pathlib.Path(__file__).parent.parent
_SRC = [s for s in yaml.safe_load((ROOT / "pipeline" / "sources.yaml").read_text(
    encoding="utf-8"))["sources"] if s["type"] == "hn"][0]
KWS = {k.lower() for k in _SRC["keywords"]}


def passes(title: str) -> bool:
    return bool(title_words(title) & KWS)


class TestPlurals(unittest.TestCase):
    def test_plural_forms_match(self):
        for t in ("Introducing System One Models and Jev",       # 1301 分
                  "LLMs as a Cognitive Virus",                   # 394 分
                  "How well do agents use test/verification techniques?",
                  "Harnessing the Universal Geometry of Embeddings"):
            self.assertTrue(passes(t), t)

    def test_original_word_kept(self):
        """只补不替：去 s 的同时原词还在。"""
        self.assertIn("gpus", title_words("Cheap GPUs"))
        self.assertIn("gpu", title_words("Cheap GPUs"))

    def test_short_words_untouched(self):
        """长度 ≤3 不去 s，否则 'is'→'i'、'gas'→'ga' 之类会乱匹配。"""
        self.assertNotIn("i", title_words("This is it"))


class TestVendorAndPeople(unittest.TestCase):
    def test_vendor_news_passes(self):
        for t in ("Nvidia agrees to acquire Hugging Face for $13B",  # 1988 分
                  "GLM-5.3-Flash",                                  # 1132 分
                  "Mistral raises €3B",
                  "Qwen 3.8 27B available on Cerebras at 1500 tokens/s",
                  "Kimi K3 (2.8T) at 1 token/s on a MacBook Pro"):
            self.assertTrue(passes(t), t)

    def test_opinion_headlines_with_only_a_name(self):
        """观点型爆款的标题里常常只有人名。"""
        for t in ("Karpathy’s Pelican",                             # 618 分，弯引号
                  "Changes at Google DeepMind: Demis Hassabis"):
            self.assertTrue(passes(t), t)


class TestNoiseStaysOut(unittest.TestCase):
    def test_high_score_non_ai_blocked(self):
        """同一批数据里 ≥500 分却与 AI 无关的帖子——这正是没做「高分豁免」的原因。"""
        for t in ("Dolly Parton has died",                          # 1602 分
                  "On the Navier–Stokes Millennium Prize Problem",  # 1341 分
                  "Apple introduces M6 and M5 Ultra",               # 1312 分
                  "U.S. State Department pauses immigrant visa applications"):
            self.assertFalse(passes(t), t)


if __name__ == "__main__":
    unittest.main()
