# N-track Episode Bridge v1

## Purpose

This isolated Challenger connects the generic P0 output to the already-built
`review-product-v1` MVP. It does not change Champion, EP03 artifacts, raw WAV,
or human decisions.

```text
P0 p0_mvp_report.json + track_01.transcript.json ...
  -> canonical_transcripts/ (raw ASR retained, deterministic normalization)
  -> tracks.manifest.json
  -> candidate_source.json
  -> review-product-v1 build_mvp_package.py
  -> N-track review bundle with A/B regenerated from all input tracks
```

## Safety policy

- Track identity is `track_01`, `track_02`, etc. There is no male/female
  assumption.
- Original P0 transcripts are immutable. Canonical transcripts retain every
  raw word id and only merge adjacent English fragments when their concatenated
  form is in the rules glossary. For example, `fe` + `ature` becomes `feature`.
- Candidate boundaries are complete canonical-token boundaries, without hidden
  padding.
- The P0 MVP has no frame-level primary/bleed activity. Therefore no bridge
  candidate is marked `SAFE`; all non-conflicting candidates are
  `NEEDS_HUMAN_REVIEW` and require the existing human `accept/reject` choice.
- A different non-filler word on any other track within the candidate window
  is fail-closed to `blocked_candidates.json`.

## Commands

Run tests first:

```bash
python3 '稳定生产/challengers/ntrack-episode-bridge-v1/scripts/run_tests.py'
```

Build bridge artifacts after P0 has passed for an episode:

```bash
python3 '稳定生产/challengers/ntrack-episode-bridge-v1/scripts/build_ntrack_review_source.py' \
  --p0-report 'main/runs/<EP>-p0/01_transcripts/p0_mvp_report.json' \
  --episode-id '<EP>' \
  --out 'main/runs/<EP>-ntrack-bridge-v1'
```

Then create the existing MVP review bundle. `--ffmpeg` is mandatory so A/B
previews are generated from every EP04 input track, not copied from EP03:

```bash
python3 '稳定生产/challengers/review-product-v1/scripts/build_mvp_package.py' \
  --source-package 'main/runs/<EP>-ntrack-bridge-v1/candidate_source.json' \
  --previews-dir 'main/runs/<EP>-ntrack-bridge-v1/previews' \
  --tracks-manifest 'main/runs/<EP>-ntrack-bridge-v1/tracks.manifest.json' \
  --frontend '审核前端/challenger-review-product-v1/mvp.html' \
  --out 'main/runs/<EP>-review-product-v1/review_bundle' \
  --ffmpeg "${PODCAST_FFMPEG:-ffmpeg}"
```

This produces a hash-bound review package only. It does not create an approved
EDL or render a final episode until a human explicitly saves every decision.
