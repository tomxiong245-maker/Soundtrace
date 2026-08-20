# Mentor feedback regression v1

## Purpose and boundary

This is a small **development-only** regression catalog of two completed Mentor review packets. It preserves the candidate metadata, final human decision, verbatim feedback, source paths/SHA, and both package and human-recorded preview hashes. It does not alter production rules, EP04 v20, the canonical experience snapshot, or real media.

The builder reads only the four pinned JSON files below. It does not glob for decision files and does not open, decode, copy, or hash WAV/MP3 previews.

## Pinned sources

| Source | Final decision JSON | Decision SHA-256 | Review package SHA-256 | Decisions | Accept | Reject | Non-empty feedback |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| mixed-14-final | `main/runs/EP04/EP04-review-mixed-14-20260814-043428/human_decisions_and_feedback__20260814-052319.json` | `df94c9a6ff0f7d3e4b85ed4e7af8095a964c840c1a85c8d7abb8a656bf6c9d9f` | `21e1a14df6397bb8e4ec636bc31bf9bebbe0bf942c113c7526908530c13032a2` | 14 | 4 | 10 | 11 |
| round2-final | `main/runs/EP04/EP04-review-round2-20260814-1355/human_decisions_and_feedback__20260814-final.json` | `dfa375520ef2f85d81f11471195579c0b3b7ca52e7658d866f08bebc7d8af4f7` | `3d29214ecf3d6862679b600cc3be8cf02aed245834e20249f3a162e76cc604bf` | 18 | 4 | 14 | 12 |

Round 2 explicitly excludes `auto_saved_reviews.json`, the non-final timestamped snapshot, and the `-partial.json` snapshot. The mixed-14 source is also selected by exact path, never by a directory scan.

## Actual result

- Final human decisions: **32** total — **8 accept**, **24 reject**.
- Verbatim feedback: **23** non-empty fields; **9** intentionally empty fields are retained as empty strings.
- Strict semantic binding: **32/32** decision `candidate_semantic_sha256` values exactly match their review-package candidate.

## Preview-hash observation

The catalog retains **39** human-recorded preview-hash fields: **23** match the current package hash and **16** differ; **25** were not recorded. A mismatch is not erased or treated as a pass. It is a provenance question to resolve before using these records for any claim that a specific current A/B preview was heard.

No feedback keyword classification is generated. Any future keyword grouping must be explicitly marked derived and can never replace the human accept/reject or verbatim feedback.

## Rebuild and verify

```bash
python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --build
python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --check
```

`--check` reconstructs the catalog in memory, validates the exact candidate-set and semantic-SHA bindings, requires every feedback field to survive verbatim, and rejects stale generated artifacts. It reads only the pinned JSON source files and the two generated text/JSON artifacts.
