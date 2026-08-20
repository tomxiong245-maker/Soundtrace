# Test Results

Date: 2026-08-11

## Commands

```bash
PYTHONPYCACHEPREFIX=/private/tmp/ntrack-bridge-pycache python3 -m py_compile \
  scripts/build_ntrack_review_source.py scripts/run_tests.py tests/test_bridge.py

PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_tests.py
```

## Result

- Python syntax compilation: PASS
- Focused tests: 5/5 PASS
- Deterministic glossary merge (`fe` + `ature` -> `feature`) with raw ids: PASS
- Generic three-track manifest and review-only candidate: PASS
- Conflicting transcript on another track fails closed: PASS
- Same filler heard by several microphones is deduplicated: PASS
- Existing `review-product-v1/build_mvp_package.py` consumed the generated source
  and produced a three-track package whose global cut applies to
  `track_01/track_02/track_03`: PASS

## Evidence limit

These tests use generated local fixtures. At the time of testing,
`main/runs/EP04-p0-20260811/01_transcripts/p0_mvp_report.json` did not yet
exist. Therefore this is code/schema compatibility evidence, not an EP04 real
audio result.
