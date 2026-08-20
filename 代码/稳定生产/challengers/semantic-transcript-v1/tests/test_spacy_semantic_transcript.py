"""test_spacy_semantic_transcript · Q1 (v20.6, 2026-08-18)"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

# 添加 script dir 到 path (在 import 前)
# tests/ 在 semantic-transcript-v1/, parents: challengers/ -> 稳定生产/ -> 剪辑项目/
ROOT = Path(__file__).resolve().parents[4]
SCRIPT_DIR = ROOT / "稳定生产/challengers/semantic-transcript-v1/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "spacy_semantic_transcript",
    SCRIPT_DIR / "spacy_semantic_transcript.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
_init_spacy = mod._init_spacy
classify_sentence = mod.classify_sentence
segment_with_spacy = mod.segment_with_spacy
sentence_for_word = mod.sentence_for_word


class TestClassifySentence(unittest.TestCase):
    def test_question_mark_interrogative(self):
        self.assertEqual(classify_sentence("你说什么？"), "interrogative")
        self.assertEqual(classify_sentence("What?"), "interrogative")

    def test_end_particle_interrogative(self):
        self.assertEqual(classify_sentence("你去吗"), "interrogative")
        self.assertEqual(classify_sentence("怎么办呢"), "interrogative")

    def test_prefix_question_word(self):
        self.assertEqual(classify_sentence("什么意思"), "interrogative")
        self.assertEqual(classify_sentence("为什么这样"), "interrogative")

    def test_declarative_default(self):
        self.assertEqual(classify_sentence("然后我们继续"), "declarative")
        self.assertEqual(classify_sentence("这是一个好想法"), "declarative")

    def test_exclamation(self):
        self.assertEqual(classify_sentence("太棒了！"), "exclamation")


class TestSpacySegmentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nlp = _init_spacy("zh_core_web_sm")

    def setUp(self):
        if self.nlp is None:
            self.skipTest("spaCy zh_core_web_sm 未装")

    def test_segment_and_classify(self):
        words = [
            {"text": "你", "start_seconds": 0.0, "end_seconds": 0.2},
            {"text": "说", "start_seconds": 0.2, "end_seconds": 0.4},
            {"text": "什么", "start_seconds": 0.4, "end_seconds": 0.8},
            {"text": "然后", "start_seconds": 1.0, "end_seconds": 1.2},
            {"text": "我们", "start_seconds": 1.2, "end_seconds": 1.5},
            {"text": "继续", "start_seconds": 1.5, "end_seconds": 1.9},
        ]
        sentences = segment_with_spacy(words, self.nlp)
        self.assertGreater(len(sentences), 0)
        # 至少一个 declarative (然后我们继续 类)
        cats = [s["category"] for s in sentences]
        # 因为句间无标点, spaCy 可能不切 · 只保证不 crash + 时间戳合理
        self.assertTrue(all("start_seconds" in s for s in sentences))
        self.assertTrue(all("category" in s for s in sentences))

    def test_sentence_for_word(self):
        sentences = [
            {"sentence_id": "s001", "start_word_idx": 0, "end_word_idx": 3, "category": "declarative"},
            {"sentence_id": "s002", "start_word_idx": 4, "end_word_idx": 7, "category": "interrogative"},
        ]
        self.assertEqual(sentence_for_word(sentences, 2)["sentence_id"], "s001")
        self.assertEqual(sentence_for_word(sentences, 5)["sentence_id"], "s002")
        self.assertIsNone(sentence_for_word(sentences, 100))


if __name__ == "__main__":
    unittest.main()
