"""Unit tests for autocut-gate-v1."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "scripts"))

import apply_autocut_gate as gate  # noqa: E402


POLICY = {
    "autocut_policy": {
        "whitelist_kinds": [
            "filler_hesitation",
            "immediate_repetition",
            "global_long_pause",
            "self_correction",
        ],
        "denylist_kinds": [
            "semantic_duplicate",
            "off_topic",
            "crosstalk_attribution",
        ],
    }
}


def _cand(cid: str, kind: str, *, tier="high", ha=1, hr=0,
           start=100.0, end=100.5, sig=True, stratum=None) -> dict:
    return {
        "candidate_id": cid,
        "candidate_kind": kind,
        "confidence_tier": tier,
        "start_seconds": start,
        "end_seconds": end,
        "start_sample": int(start * 48000),
        "end_sample": int(end * 48000),
        "experience_signal": {
            "historical_accept_count": ha,
            "historical_reject_count": hr,
        },
        "repetition_signature": {"has_signature": sig} if sig is not None else None,
        "stratum": stratum or f"{kind}:strong_hesitation_sound:100_400ms:track_01",
    }


class AutocutGateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.policy_path = self.root / "policy.json"
        self.policy_path.write_text(json.dumps(POLICY), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, candidates: list[dict], **overrides) -> dict:
        cand_path = self.root / "candidates.json"
        cand_path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
        return gate.run(
            cand_path,
            self.policy_path,
            self.root / "out",
            episode_duration_s=overrides.get("episode_duration_s", 3272.7),
            max_duration_s=overrides.get("max_duration_s", 0.8),
            opening_s=overrides.get("opening_s", 6.0),
            closing_s=overrides.get("closing_s", 6.0),
            min_hist_accept=overrides.get("min_hist_accept", 1),
        )

    # G1 · whitelist [DISABLED 2026-08-20 · Optuna 参数层] --------------------

    def test_g1_disabled_denylist_passes_through(self) -> None:
        """2026-08-20 · G1 参数层已彻底关闭 · denylist kind 不再拒判 · 直接放行。"""
        r = self._run([_cand("D1", "semantic_duplicate")])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    def test_g1_disabled_unknown_kind_passes_through(self) -> None:
        """2026-08-20 · G1 参数层已彻底关闭 · unknown kind 不再拒判 · 直接放行。"""
        r = self._run([_cand("U1", "some_unknown_kind")])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    def test_g1_whitelist_kind_passes(self) -> None:
        r = self._run([_cand("W1", "filler_hesitation")])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    # G2 · high confidence [DISABLED 2026-08-20 · Optuna 参数层] ---------------

    def test_g2_disabled_mid_tier_passes_through(self) -> None:
        """2026-08-20 · G2 参数层已彻底关闭 · tier=mid 不再拒判。"""
        r = self._run([_cand("M1", "filler_hesitation", tier="mid")])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    def test_g2_disabled_low_tier_passes_through(self) -> None:
        """2026-08-20 · G2 参数层已彻底关闭 · tier=low 不再拒判。"""
        r = self._run([_cand("L1", "filler_hesitation", tier="low")])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    # G5 · history ---------------------------------------------------------

    def test_g5_zero_accept_passes_when_no_reject(self) -> None:
        """New v3 rule: hr == 0 (no negative evidence) is enough to pass G5
        after G2/G3/G6/G7 have all passed. Requires positive signals ONLY as
        a bonus; the strict precision-first behavior is now driven by hr==0
        + wordlevel/cross-track/tier signals in G2."""
        r = self._run([_cand("H0", "immediate_repetition", ha=0, hr=0)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    def test_g5_any_reject_fails(self) -> None:
        r = self._run([_cand("HR", "immediate_repetition", ha=5, hr=1)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 0)

    def test_g5_accept_only_passes(self) -> None:
        r = self._run([_cand("HA", "immediate_repetition", ha=1, hr=0)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    # G6 · duration -------------------------------------------------------

    def test_g6_long_duration_fails(self) -> None:
        r = self._run([_cand("LD", "filler_hesitation", start=100.0, end=101.5)])
        # 1500ms > 800ms
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 0)

    def test_g6_short_duration_passes(self) -> None:
        r = self._run([_cand("SD", "filler_hesitation", start=100.0, end=100.3)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 1)

    # G7 · protection zone -------------------------------------------------

    def test_g7_opening_protection_fails(self) -> None:
        r = self._run([_cand("O1", "filler_hesitation", start=3.0, end=3.4)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 0)

    def test_g7_closing_protection_fails(self) -> None:
        # episode 3272.7s, closing 6s → end must ≤ 3266.7
        r = self._run([_cand("C1", "filler_hesitation", start=3270.0, end=3270.4)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 0)

    # Composite -----------------------------------------------------------

    def test_multiple_candidates_partition_correctly(self) -> None:
        """2026-08-20 · G1/G2 参数层禁用 · 之前的 A3 (mid tier) / A4 (denylist) 现在都通过。
        只有 A2 (G5 hr=1 硬拒) 走人审."""
        r = self._run([
            _cand("A1", "filler_hesitation", ha=1, hr=0),  # pass
            _cand("A2", "filler_hesitation", ha=0, hr=1),  # G5 fail
            _cand("A3", "filler_hesitation", tier="mid"),  # G2 disabled · now pass
            _cand("A4", "semantic_duplicate", ha=1, hr=0),  # G1 disabled · now pass
        ])
        self.assertEqual(r["summary"]["total_candidates"], 4)
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 3)
        self.assertEqual(r["summary"]["human_review_required_count"], 1)
        self.assertEqual(r["auto_cut_candidate_ids"], ["A1", "A3", "A4"])

    def test_precision_first_denies_reject_history(self) -> None:
        """A candidate whose feature-family has any historical reject is
        denied auto-cut even if the current sample looks clean. This is
        the primary precision-safety mechanism."""
        r = self._run([_cand("PF", "immediate_repetition", ha=10, hr=1)])
        self.assertEqual(r["summary"]["auto_cut_eligible_count"], 0)


if __name__ == "__main__":
    unittest.main()
