from __future__ import annotations

import sys
import unittest
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import transcript_text_layers as layers  # noqa: E402


class TranscriptTextLayersTests(unittest.TestCase):
    def test_raw_match_display_are_separate_and_keep_word_times(self) -> None:
        source = {
            "track_id": "track_01",
            "words": [
                {"word_id": "track_01:w000001", "text": "這個", "start_seconds": 1.0, "end_seconds": 1.2},
                {"word_id": "track_01:w000002", "text": " MCP", "start_seconds": 1.2, "end_seconds": 1.5},
            ],
        }
        result = layers.build_text_layers(source)
        self.assertEqual(result["document"]["raw_text"], "這個 MCP")
        self.assertEqual(result["document"]["display_text"], "这个 MCP")
        self.assertEqual(result["document"]["match_text"], "这个mcp")
        self.assertEqual(result["words"][0]["source_word_id"], "track_01:w000001")
        self.assertEqual(result["words"][0]["start_seconds"], 1.0)
        self.assertTrue(result["policy"]["raw_is_immutable"])

    def test_legacy_challenger_view_is_backed_by_the_same_canonical_output(self) -> None:
        source = {
            "track_id": "track_01",
            "words": [
                {"word_id": "track_01:w000001", "text": "S", "start_seconds": 0.00, "end_seconds": 0.02},
                {"word_id": "track_01:w000002", "text": "oph", "start_seconds": 0.02, "end_seconds": 0.04},
                {"word_id": "track_01:w000003", "text": "ie", "start_seconds": 0.04, "end_seconds": 0.06},
            ],
        }
        result = layers.build_text_layers(source)
        self.assertEqual(result["legacy_schema_version"], "asr-text-layers-v1")
        self.assertEqual(result["display_text"], "Sophie")
        self.assertEqual(result["document"]["display_text"], result["display_text"])
        self.assertEqual(len(result["word_layers"]), len(result["words"]))
        self.assertEqual(result["display_spans"][0]["source_word_ids"], [
            "track_01:w000001", "track_01:w000002", "track_01:w000003",
        ])
        self.assertNotIn("start_seconds", result["display_spans"][0])

    def test_duplicate_word_id_fails_closed(self) -> None:
        source = {
            "track_id": "track_01",
            "words": [
                {"word_id": "track_01:w000001", "text": "一", "start_seconds": 0.0, "end_seconds": 0.1},
                {"word_id": "track_01:w000001", "text": "二", "start_seconds": 0.1, "end_seconds": 0.2},
            ],
        }
        with self.assertRaises(layers.ContractError):
            layers.build_text_layers(source)


if __name__ == "__main__":
    unittest.main()
