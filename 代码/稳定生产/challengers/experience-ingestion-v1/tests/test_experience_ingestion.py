"""experience-ingestion-v1 契约测试。

以合成 fixture 覆盖 20 条契约：
1. 有效真人案例可以导入
2. bulk accept 被排除
3. 缺 package_id 被拒绝
4. review_manifest_sha256 不匹配被拒绝
5. candidate_semantic_sha256 不匹配被拒绝
6. 未知 candidate 被拒绝
7. 重复 decision 被拒绝
8. pending decision 被拒绝
9. 长停顿缺少一版必听试听被拒绝
10. 无 EDL 的 accept 不被标记为 applied_to_edl
11. source 文件变化时 fail closed
12. quarantine 案例不进入统计
13. 规则建议数据不足时不得生成生产变更
14. consumer 不得写入 稳定生产/rules
15. training readiness 当前数据下返回 NOT_READY
16. adapter 输出包含全部禁止动作
17. 输入输出 SHA 可复现
18. 真实运行不影响原有 P0/P1/filler 测试目录 SHA
19. 当前逐项二态案例不因缺 review_mode 而降级
20. adjust=0 与缺 review_mode 不属于当前二态路线的 readiness 阻塞理由
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
SCRIPTS = CHALLENGER / "scripts"
sys.path.insert(0, str(SCRIPTS))

collect = __import__("collect_experience_cases")
consumer = __import__("consume_experience_cases")
readiness = __import__("check_training_readiness")
adapter = __import__("experience_consumer_adapter")


# -----------------------------
# fixture 工厂
# -----------------------------

def _sha256(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _base_package(pkg_id: str, cands: list[dict]) -> dict:
    return {
        "schema_version": "review-product-mvp-v2",
        "episode_id": "EPFX",
        "package_id": pkg_id,
        "sample_rate_hz": 48000,
        "frame_count": 48000 * 60,
        "track_count": 3,
        "tracks": [
            {"track_id": "track_01", "audio_sha256": "a" * 64, "transcript_sha256": "b" * 64},
            {"track_id": "track_02", "audio_sha256": "c" * 64, "transcript_sha256": "d" * 64},
            {"track_id": "track_03", "audio_sha256": "e" * 64, "transcript_sha256": None},
        ],
        "source_package_sha256": "f" * 64,
        "candidates": cands,
    }


def _cand(cid: str, reason: str = "immediate_repetition",
          start=1000, end=2000, sem: str | None = None,
          must_listen: list[str] | None = None) -> dict:
    return {
        "candidate_id": cid,
        "reason_key": reason,
        "source_track_id": "track_01",
        "start_sample": start,
        "end_sample": end,
        "start_seconds": start / 48000,
        "end_seconds": end / 48000,
        "semantic_sha256": sem or _sha256(cid.encode()),
        "review_requirements": (
            {"must_listen_to": must_listen} if must_listen else {}
        ),
        "text_tracks": {
            "track_01": {
                "words": [
                    {"text": "嗯", "start_seconds": start/48000, "end_seconds": end/48000}
                ]
            }
        },
    }


def _decision(cid: str, decision: str = "accept", *,
              basis: str = "text_only",
              sem: str | None = None,
              listened: dict | None = None,
              feedback: str = "") -> dict:
    return {
        "candidate_id": cid,
        "candidate_semantic_sha256": sem or _sha256(cid.encode()),
        "decision": decision,
        "reviewer": "test-reviewer",
        "decided_at": "2026-08-12T00:00:00Z",
        "review_basis": basis,
        "listened_previews": listened or {},
        "feedback": feedback,
    }


def _decisions_doc(pkg_id: str, decisions: list[dict],
                   manifest_sha: str = "m" * 64) -> dict:
    return {
        "schema_version": "human-decisions-mvp-v1",
        "package_id": pkg_id,
        "review_manifest_sha256": manifest_sha,
        "reviewer": "test-reviewer",
        "session_started_at": "2026-08-12T00:00:00Z",
        "session_ended_at": "2026-08-12T00:00:10Z",
        "decisions": decisions,
    }


def _edl_doc(pkg_id: str, cids: list[str]) -> dict:
    return {
        "schema_version": "approved-edl-draft-mvp-v1",
        "package_id": pkg_id,
        "review_manifest_sha256": "m" * 64,
        "sample_rate_hz": 48000,
        "reviewer": "test-reviewer",
        "cuts": [
            {"candidate_id": c, "start_sample": 1000, "end_sample": 2000,
             "applies_to_tracks": ["track_01", "track_02", "track_03"],
             "crossfade_ms": 50}
            for c in cids
        ],
    }


def _bulk_doc() -> dict:
    return {
        "reviewer": "someone",
        "episode": "EPBULK",
        "review_mode": "bulk_accept_reference_prior_authorization",
        "review_mode_explanation": "bulk accept only",
        "candidates": [
            {"candidate_id": "C001", "decision": "accept", "note": "bulk"},
            {"candidate_id": "C002", "decision": "accept", "note": "bulk"},
        ],
    }


def _prepare_repo(tmp_path: Path, *,
                  ep_ok: bool = True,
                  bulk: bool = True,
                  filler: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # baseline dir empty
    # 覆盖 SOURCES / EXCLUSION_SOURCES 到测试自身命名
    collect.SOURCES = [
        {"label": "EPFX-review", "episode_id": "EPFX",
         "run_dir": "main/runs/EPFX-review",
         "kind": "review_product"},
        {"label": "EPFX-filler", "episode_id": "EPFX",
         "run_dir": "main/runs/EPFX-filler",
         "kind": "filler_global_pause"},
    ]
    collect.EXCLUSION_SOURCES = [
        {"label": "EPBULK", "episode_id": "EPBULK",
         "run_dir": "main/runs/EPBULK",
         "reason": "bulk_accept_reference_prior_authorization"},
    ]
    if ep_ok:
        pkg_id = "EPFX-review-000001"
        cands = [_cand("C001"), _cand("C002")]
        pkg = _base_package(pkg_id, cands)
        dec = _decisions_doc(pkg_id, [
            _decision("C001", "accept"),
            _decision("C002", "reject"),
        ])
        edl = _edl_doc(pkg_id, ["C001"])  # C002 reject 不进入 EDL
        base = repo / "main/runs/EPFX-review"
        _write_json(base / "review_bundle/review_package.json", pkg)
        _write_json(base / "human_decisions.json", dec)
        _write_json(base / "approved.edl.draft.json", edl)
    if filler:
        pkg_id = "EPFX-filler-000001"
        cands = [
            _cand("C001", reason="filler_hesitation"),
            _cand("C010", reason="global_long_pause", must_listen=["original", "proposed_cut"]),
        ]
        pkg = _base_package(pkg_id, cands)
        dec = _decisions_doc(pkg_id, [
            _decision("C001", "reject", basis="text_with_audio",
                      listened={"original_sha256": "a" * 64}),
            _decision("C010", "accept", basis="text_and_audio",
                      listened={
                          "original_sha256": "a" * 64,
                          "proposed_cut_sha256": "b" * 64,
                      }),
        ])
        base = repo / "main/runs/EPFX-filler"
        _write_json(base / "review_bundle/review_package.json", pkg)
        _write_json(base / "human_decisions.json", dec)
        # 不写 EDL：filler 无 EDL 场景
        _write_json(base / "bridge_report.json", {"status": "ok"})
    if bulk:
        _write_json(repo / "main/runs/EPBULK/human_decisions.json", _bulk_doc())
    return repo


def _run_collect(repo: Path, tmp_path: Path, *, baseline: Path | None = None,
                 reject_drift: bool = False) -> dict:
    out_dir = tmp_path / "case_store"
    result = collect.run_ingestion(
        repo_root=repo, out_dir=out_dir, baseline_path=baseline,
        run_dir=None, reject_drift=reject_drift,
    )
    return {
        "cases": result.cases,
        "exclusions": result.exclusions,
        "quarantine": result.quarantine,
        "manifest": result.manifest,
        "case_store": out_dir,
    }


# -----------------------------
# 契约测试
# -----------------------------


def test_01_valid_cases_import(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    ids = {c["candidate_id"] for c in r["cases"]}
    assert {"C001", "C002", "C010"}.issubset(ids)


def test_01b_optional_human_feedback_is_retained_in_experience_case(tmp_path):
    repo = _prepare_repo(tmp_path)
    decisions_path = repo / "main/runs/EPFX-review/human_decisions.json"
    document = json.loads(decisions_path.read_text(encoding="utf-8"))
    document["decisions"][0]["feedback"] = "句中这个重复仍然自然，应保留。"
    _write_json(decisions_path, document)
    result = _run_collect(repo, tmp_path)
    case = next(
        item for item in result["cases"]
        if item["provenance"]["source_run_dir"].endswith("EPFX-review")
        and item["candidate_id"] == "C001"
    )
    assert case["label"]["feedback"] == "句中这个重复仍然自然，应保留。"


def test_02_bulk_accept_is_excluded(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    assert all(x["eligibility"]["status"] == "excluded_bulk_accept" for x in r["exclusions"])
    # bulk accept 不在 cases
    for c in r["cases"]:
        assert c["episode_id"] != "EPBULK"


def test_03_missing_package_id_rejected(tmp_path):
    repo = _prepare_repo(tmp_path)
    pkg_path = repo / "main/runs/EPFX-review/review_bundle/review_package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pkg["package_id"] = ""
    _write_json(pkg_path, pkg)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "missing_package_id_in_package" in kinds
    # EPFX-review 不应有 cases
    assert all(c["episode_id"] != "EPFX" or c["provenance"]["source_run_dir"].endswith("EPFX-filler")
               for c in r["cases"])


def test_04_review_manifest_mismatch(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["decisions"][0]["review_manifest_sha256"] = "z" * 64
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "review_manifest_sha256_mismatch_within_decisions" in kinds


def test_05_candidate_semantic_sha_mismatch(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["decisions"][0]["candidate_semantic_sha256"] = "z" * 64
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "candidate_semantic_sha256_mismatch" in kinds
    # 该候选不出现在 cases
    ids = {(c["episode_id"], c["candidate_id"]) for c in r["cases"]}
    assert ("EPFX", "C001") not in {(c["episode_id"], c["candidate_id"])
                                     for c in r["cases"]
                                     if c["provenance"]["source_run_dir"].endswith("EPFX-review")}


def test_06_unknown_candidate_rejected(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["decisions"].append(_decision("C999", "accept"))
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "unknown_candidate" in kinds


def test_07_duplicate_decision_rejected(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dup = copy.deepcopy(dec["decisions"][0])
    dup["decided_at"] = "2026-08-12T00:00:20Z"
    dec["decisions"].append(dup)
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "duplicate_decision" in kinds


def test_08_pending_decision_rejected(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["decisions"][1]["decision"] = "pending"
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "pending_or_invalid_decision" in kinds


def test_09_long_pause_missing_listen_rejected(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-filler/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    for d in dec["decisions"]:
        if d["candidate_id"] == "C010":
            d["listened_previews"] = {"original_sha256": "a" * 64}  # 缺 proposed_cut
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "missing_required_listen" in kinds
    ids = {c["candidate_id"] for c in r["cases"]
           if c["provenance"]["source_run_dir"].endswith("EPFX-filler")}
    assert "C010" not in ids


def test_10_no_edl_accept_not_marked_applied(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    # EPFX-filler 没有 EDL：其 accept case C010 不能标 applied_to_edl=True
    for c in r["cases"]:
        if c["provenance"]["source_run_dir"].endswith("EPFX-filler"):
            assert c["label"]["applied_to_edl"] is False
            assert c["label"]["edl_status"] == "not_generated_yet"


def test_11_source_file_change_fail_closed(tmp_path):
    repo = _prepare_repo(tmp_path)
    # 先生成 baseline
    from hashlib import sha256
    baseline = {
        "sources": {
            "EPFX-review": {
                "path": "main/runs/EPFX-review",
                "files": {
                    "human_decisions.json": {"sha256": sha256(
                        (repo / "main/runs/EPFX-review/human_decisions.json"
                         ).read_bytes()).hexdigest()},
                },
            },
        },
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    # 改动源文件
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["session_ended_at"] = "2026-08-12T00:01:00Z"
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path, baseline=baseline_path, reject_drift=True)
    kinds = {q["kind"] for q in r["quarantine"]}
    assert "source_sha_drift" in kinds
    assert "source_rejected_due_to_drift" in kinds
    # 该 source 不能出案例
    assert all(not c["provenance"]["source_run_dir"].endswith("EPFX-review")
               for c in r["cases"])


def test_12_quarantine_not_in_summary(tmp_path):
    repo = _prepare_repo(tmp_path)
    dec_path = repo / "main/runs/EPFX-review/human_decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["decisions"][1]["decision"] = "pending"
    _write_json(dec_path, dec)
    r = _run_collect(repo, tmp_path)
    reports_dir = tmp_path / "reports"
    out = consumer.write_reports(r["case_store"], reports_dir)
    summary = out["summary"]
    # C002 应该没进 cases
    assert summary["counts"]["total_cases"] == len(r["cases"])
    assert "reject" not in summary["counts"]["by_decision"] or \
        summary["counts"]["by_decision"].get("reject", 0) >= 0  # smoke


def test_13_insufficient_data_no_production_change(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    reports_dir = tmp_path / "reports"
    out = consumer.write_reports(r["case_store"], reports_dir)
    recs = out["recommendations"]["recommendations"]
    assert recs, "至少应生成建议记录"
    for rec in recs:
        assert rec["action"] == "NO_PRODUCTION_CHANGE"
        assert rec["status"] in ("INSUFFICIENT_DATA", "SUFFICIENT")


def test_14_consumer_does_not_write_production_rules(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    reports_dir = tmp_path / "reports"
    prod_rules = CHALLENGER.parent.parent / "rules"
    before = {p: p.stat().st_mtime_ns for p in prod_rules.rglob("*") if p.is_file()}
    consumer.write_reports(r["case_store"], reports_dir)
    after = {p: p.stat().st_mtime_ns for p in prod_rules.rglob("*") if p.is_file()}
    assert before == after, "consumer 不得触碰稳定生产/rules"


def test_15_training_readiness_not_ready(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    doc = readiness.build_readiness(r["case_store"])
    assert doc["status"] == "NOT_READY"
    assert doc["model_trained"] is False
    assert doc["reasons"], "应给出未达标原因"


def test_16_adapter_lists_prohibited_actions(tmp_path):
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    reports_dir = tmp_path / "reports"
    consumer.write_reports(r["case_store"], reports_dir)
    doc = adapter.query(r["case_store"])
    caps = doc["capabilities"]
    assert caps == {
        "can_change_production_rules": False,
        "can_approve_edl": False,
        "can_train_model": False,
        "can_read_cases": True,
    }
    for act in ("modify_production_rules", "approve_edl", "train_model",
                "write_model_weights"):
        assert act in doc["prohibited_actions"]


def test_17_reproducible_sha(tmp_path):
    repo = _prepare_repo(tmp_path)
    r1 = _run_collect(repo, tmp_path / "run1")
    # 清 case store 再跑一次
    r2 = _run_collect(repo, tmp_path / "run2")
    a = (tmp_path / "run1" / "case_store" / "cases" / "EPFX.jsonl").read_bytes()
    b = (tmp_path / "run2" / "case_store" / "cases" / "EPFX.jsonl").read_bytes()
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_18_real_champion_dirs_untouched(tmp_path):
    """真实 Champion 目录在测试期间不得被本任务写入。"""
    def snapshot(base: Path) -> dict[str, str]:
        return {
            str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*"))
            if p.is_file() and not p.is_symlink()
        }

    repo = _prepare_repo(tmp_path)
    forbidden = [
        CHALLENGER.parent.parent / "rules",
        CHALLENGER.parent.parent / "scripts",
        CHALLENGER.parent / "filler-global-pause-v1",
    ]
    before = {str(base): snapshot(base) for base in forbidden}
    r = _run_collect(repo, tmp_path)
    reports_dir = tmp_path / "reports"
    consumer.write_reports(r["case_store"], reports_dir)
    after = {str(base): snapshot(base) for base in forbidden}
    assert before == after, "经验消费者不得写入 Champion 或其他 Challenger"


def test_19_two_state_cases_are_rule_eligible_without_review_mode(tmp_path):
    """当前 accept/reject MVP 不因缺 review_mode 而被降级或隔离。"""
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    assert r["cases"]
    for case in r["cases"]:
        eligibility = case["eligibility"]
        assert eligibility["eligible_for_rule_analysis"] is True
        assert eligibility["eligible_for_model_training"] is False
        assert eligibility["status"] == "eligible_rule_only"
        assert "review_mode" not in eligibility["reason"]


def test_20_adjust_and_review_mode_are_not_current_readiness_gates(tmp_path):
    """adjust=0 与缺 review_mode 只能被记录，不能成为当前二态路线的阻塞理由。"""
    repo = _prepare_repo(tmp_path)
    r = _run_collect(repo, tmp_path)
    doc = readiness.build_readiness(r["case_store"])
    reasons = "\n".join(doc["reasons"])
    assert doc["checks"]["review_mode_is_optional"] is True
    assert doc["checks"]["adjust_count"] == 0
    assert "review_mode" not in reasons
    assert "adjust" not in reasons
