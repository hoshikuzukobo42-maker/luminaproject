"""P7-06 deterministic G7 independent-review preparation pack.

Mechanical verification success and review readiness are deliberately separate
from a G7 gate decision.  This module never signs, approves, or releases.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


FIXTURE_SCHEMA = "lumina.g7-independent-review.fixture.v1"
PACK_SCHEMA = "lumina.g7-independent-review.pack.v1"
POLICY_VERSION = "lumina.g7-independent-review.policy.v1"
REPOSITORY_ROOT = "/LOCAL_RUNTIME_NOT_INCLUDED"
WBS_WORKBOOK_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/lumina_plan_update_2026-08-30/lumina_virtual_agent_master_plan.xlsx"
WBS_SNAPSHOT_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_g7_review_pack_20260831/canonical_wbs_snapshot.xlsx"
WBS_WORKBOOK_SHA256 = "3037f976bb6b1d93a36126d3a96a4edf8a3d23dc06d7082ff4c9569bb935f7c2"
CANONICAL_WBS = {
    "work_item": "P7-06",
    "phase": "P7",
    "type": "Gate",
    "task": "G7 review",
    "objective": "クローズドβから製品化へ進むか承認",
    "deliverable": "G7 Evidence Pack",
    "owner": "Exec/PM/Safety",
    "priority": "P0",
    "dependency": "P7-05",
    "formal_status": "Not Started",
    "acceptance": "継続利用価値と安全性が合格",
    "kpi": "Gate pass",
    "gate": "G7",
}
EVIDENCE_REQUIREMENTS = {
    "P6-05": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_g6_review_pack_20260831/g6_independent_review_acceptance.json", "sha256": "e5c3d4ed98bc4518c59a03486c885db7a263c814ae5f6010acbd19c7c7fb6eea", "schema_version": "lumina.g6-independent-review.acceptance.v1", "required_status": "READY_FOR_INDEPENDENT_REVIEW"},
    "P7-01": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_beta_cohort_20260831/beta_cohort_protocol_acceptance.json", "sha256": "673539f6e1d42cc8df545bac544400edac125a50a349f703eba9155aed8064e0", "schema_version": "lumina.beta-cohort.acceptance.v1", "required_status": "PASS"},
    "P7-02": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_beta_telemetry_20260831/beta_telemetry_privacy_acceptance.json", "sha256": "c70111957e54c2fca49b54a0b46de413913fc655a53672e1ffef628f83828ea3", "schema_version": "lumina.beta-telemetry.acceptance.v1", "required_status": "PASS"},
    "P7-03": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_longitudinal_beta_20260831/longitudinal_beta_acceptance.json", "sha256": "6266162efd2a2ac08594b6198c3fb70303c60bf5a5d72bdda38482f90ac5651b", "schema_version": "lumina.longitudinal-beta.acceptance.v1", "required_status": "PASS_SYNTHETIC_PIPELINE"},
    "P7-04": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_quality_triage_20260831/quality_triage_acceptance.json", "sha256": "d1c5341340db98e1c0c7312c7904476c77e4201e26eae29346766891c0e30cd7", "schema_version": "lumina.quality-triage.acceptance.v1", "required_status": "SYNTHETIC_TRIAGE_REFERENCE_PASS"},
    "P7-05": {"path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_beta_decision_20260831/beta_decision_memo_acceptance.json", "sha256": "785c4308a266defd7a3cac8fe6b8a1d8cdd449bb791604b7f31b7701cbbf9398", "schema_version": "lumina.beta-decision-memo.acceptance.v1", "required_status": "DECISION_PACK_COMPLETE_HOLD"},
}
REQUIRED_REVIEWER_ROLES = ("EXEC", "PM", "SAFETY")
REQUIRED_GAPS = (
    "G6_INDEPENDENT_APPROVAL",
    "ACTUAL_HUMAN_BETA",
    "REAL_D30_RETENTION",
    "REAL_TRUST_SCORE",
    "ACTUAL_PRODUCT_VALUE",
    "HUMAN_RESEARCH_REVIEW",
    "PRODUCTION_ISSUE_AUTHORIZATION",
    "GODOT_LIVE_VALIDATION",
    "EXEC_INDEPENDENT_SIGNATURE",
    "PM_INDEPENDENT_SIGNATURE",
    "SAFETY_INDEPENDENT_SIGNATURE",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class G7ReviewPackError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise G7ReviewPackError("G7_NONCANONICAL_JSON", "payload") from exc


def _same(actual: Any, expected: Any) -> bool:
    return _canonical(actual) == _canonical(expected)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise G7ReviewPackError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise G7ReviewPackError(code, detail)


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise G7ReviewPackError(code, detail)


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise G7ReviewPackError("G7_TIME_INVALID", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise G7ReviewPackError("G7_TIME_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise G7ReviewPackError("G7_TIME_INVALID", field)
    normalized = parsed.astimezone(timezone.utc)
    _require(value == normalized.isoformat().replace("+00:00", "Z"), "G7_TIME_NONCANONICAL", field)
    return normalized


def _safe_source(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise G7ReviewPackError("G7_SOURCE_PATH_INVALID", str(relative))
    candidate = (root / relative).resolve()
    try:
        inside = os.path.commonpath((str(root.resolve()), str(candidate))) == str(root.resolve())
    except ValueError:
        inside = False
    _require(inside, "G7_SOURCE_PATH_INVALID", relative)
    return candidate


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "G7_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "prepared_at", "repository_root", "canonical_wbs_source", "evidence_bindings", "review_state", "known_gaps", "expectations"}, "G7_FIXTURE_FIELDS_INVALID", "fixture")
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "G7_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    _time(fixture["prepared_at"], "prepared_at")
    _require(fixture["repository_root"] == REPOSITORY_ROOT and Path(fixture["repository_root"]).is_dir(), "G7_REPOSITORY_ROOT_INVALID", "repository_root")
    source = _mapping(fixture["canonical_wbs_source"], "G7_WBS_SOURCE_REQUIRED", "canonical_wbs_source")
    _exact(source, {"workbook_path", "workbook_sha256", "snapshot_path", "snapshot_sha256", "sheet", "row", "range", *CANONICAL_WBS.keys()}, "G7_WBS_SOURCE_FIELDS_INVALID", "canonical_wbs_source")
    _require(source["workbook_path"] == WBS_WORKBOOK_PATH and source["workbook_sha256"] == WBS_WORKBOOK_SHA256 and source["snapshot_path"] == WBS_SNAPSHOT_PATH and source["snapshot_sha256"] == WBS_WORKBOOK_SHA256, "G7_WBS_IDENTITY_MISMATCH", "workbook")
    _require(Path(source["workbook_path"]).is_file(), "G7_WBS_WORKBOOK_MISSING", source["workbook_path"])
    snapshot = Path(source["snapshot_path"])
    _require(snapshot.is_file() and _file_sha(snapshot) == source["snapshot_sha256"], "G7_WBS_HASH_MISMATCH", str(snapshot))
    _require(_same({"sheet": source["sheet"], "row": source["row"], "range": source["range"]}, {"sheet": "02_WBS_Detail", "row": 58, "range": "A58:P58"}), "G7_WBS_LOCATOR_MISMATCH", "row58")
    for field, expected in CANONICAL_WBS.items():
        _require(source[field] == expected, "G7_WBS_TEXT_MISMATCH", field)

    bindings = fixture["evidence_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == len(EVIDENCE_REQUIREMENTS), "G7_EVIDENCE_CATALOG_INVALID", "6 bindings")
    normalized_bindings = []
    for raw in bindings:
        binding = _mapping(raw, "G7_EVIDENCE_BINDING_REQUIRED", "binding")
        _exact(binding, {"work_item", "path", "evidence_sha256", "schema_version", "required_status"}, "G7_EVIDENCE_BINDING_FIELDS_INVALID", "binding")
        work_item = binding["work_item"]
        _require(work_item in EVIDENCE_REQUIREMENTS, "G7_EVIDENCE_WORK_ITEM_UNKNOWN", str(work_item))
        expected = EVIDENCE_REQUIREMENTS[work_item]
        _require(_same(binding, {"work_item": work_item, "path": expected["path"], "evidence_sha256": expected["sha256"], "schema_version": expected["schema_version"], "required_status": expected["required_status"]}), "G7_EVIDENCE_IDENTITY_MISMATCH", work_item)
        normalized_bindings.append(_copy(dict(binding)))
    _require({item["work_item"] for item in normalized_bindings} == set(EVIDENCE_REQUIREMENTS), "G7_EVIDENCE_CATALOG_INVALID", "unique requirements")

    review = _mapping(fixture["review_state"], "G7_REVIEW_STATE_REQUIRED", "review_state")
    expected_review = {
        "preparer_id": "agent:p7-06-reference",
        "required_reviewer_roles": list(REQUIRED_REVIEWER_ROLES),
        "independent_reviewer_ids": [],
        "independent_signatures": [],
        "independent_approvals": 0,
        "g7_pass_claim_count": 0,
        "readiness_target": "READY_FOR_INDEPENDENT_REVIEW",
        "decision": "HOLD",
        "workflow_status": "IN_PROGRESS",
        "completed": False,
    }
    _require(_same(review, expected_review), "G7_REVIEW_STATE_OVERCLAIM", "review_state")
    gaps = fixture["known_gaps"]
    _require(isinstance(gaps, list) and len(gaps) == len(REQUIRED_GAPS), "G7_GAP_CATALOG_INVALID", "known_gaps")
    normalized_gaps = []
    for raw in gaps:
        gap = _mapping(raw, "G7_GAP_REQUIRED", "gap")
        _exact(gap, {"gap_id", "status", "owner", "due_at"}, "G7_GAP_FIELDS_INVALID", "gap")
        _require(gap["gap_id"] in REQUIRED_GAPS and gap["status"] == "MISSING" and isinstance(gap["owner"], str) and bool(gap["owner"]), "G7_GAP_OVERCLAIM", str(gap.get("gap_id")))
        _time(gap["due_at"], "gap due_at")
        normalized_gaps.append(_copy(dict(gap)))
    _require({item["gap_id"] for item in normalized_gaps} == set(REQUIRED_GAPS), "G7_GAP_CATALOG_INVALID", "exact gaps")
    expected_expectations = {"evidence_bindings": 6, "source_bindings": 36, "transitive_bindings": 2, "known_gaps": 11, "false_green_controls_min": 40}
    _require(_same(fixture["expectations"], expected_expectations), "G7_EXPECTATIONS_INVALID", "expectations")
    normalized = _copy(dict(fixture))
    normalized["evidence_bindings"] = sorted(normalized_bindings, key=lambda item: item["work_item"])
    normalized["known_gaps"] = sorted(normalized_gaps, key=lambda item: item["gap_id"])
    return normalized


def _load(binding: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(binding["path"])
    _require(path.is_file(), "G7_EVIDENCE_MISSING", str(path))
    _require(_file_sha(path) == binding["evidence_sha256"], "G7_EVIDENCE_HASH_MISMATCH", binding["work_item"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise G7ReviewPackError("G7_EVIDENCE_JSON_INVALID", binding["work_item"]) from exc
    _require(doc.get("schema_version") == binding["schema_version"] and doc.get("work_item") == binding["work_item"] and doc.get("status") == binding["required_status"], "G7_EVIDENCE_STATUS_INVALID", binding["work_item"])
    return doc


def _validate_evidence_semantics(work_item: str, doc: Mapping[str, Any]) -> None:
    metrics = _mapping(doc.get("metrics"), "G7_EVIDENCE_METRICS_REQUIRED", work_item)
    if work_item == "P6-05":
        _require(doc.get("verification_status") == "PASS" and doc.get("decision") == "HOLD" and doc.get("workflow_status") == "IN_PROGRESS", "G7_G6_STATE_INVALID", work_item)
        _require(_same(metrics.get("independent_approvals"), 0) and _same(metrics.get("g6_pass_claim_count"), 0), "G7_G6_OVERCLAIM", work_item)
        _require(doc.get("claims", {}).get("g6_pass") == "NONE", "G7_G6_OVERCLAIM", work_item)
    elif work_item == "P7-01":
        _require(doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE" and doc.get("wbs_promotion", {}).get("formal_wbs_promotion_allowed") is False, "G7_GATE_OVERCLAIM", work_item)
        _require(_same(metrics.get("actual_participants_enrolled"), 0) and _same(metrics.get("production_user_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0), "G7_REAL_WORLD_OVERCLAIM", work_item)
    elif work_item == "P7-02":
        _require(doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE" and doc.get("wbs_promotion", {}).get("formal_wbs_promotion_allowed") is False, "G7_GATE_OVERCLAIM", work_item)
        _require(_same(metrics.get("production_user_records_read"), 0) and _same(metrics.get("external_effects_executed"), 0) and _same(metrics.get("network_transmissions"), 0), "G7_REAL_WORLD_OVERCLAIM", work_item)
    elif work_item == "P7-03":
        _require(doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE" and doc.get("wbs_promotion", {}).get("formal_wbs_promotion_allowed") is False, "G7_GATE_OVERCLAIM", work_item)
        _require(_same(metrics.get("actual_human_participants"), 0) and _same(metrics.get("production_user_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0) and _same(metrics.get("critical_issues"), 0), "G7_REAL_WORLD_OVERCLAIM", work_item)
    elif work_item == "P7-04":
        _require(doc.get("verification_status") == "PASS" and doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE" and doc.get("wbs_promotion", {}).get("formal_wbs_completion_allowed") is False, "G7_GATE_OVERCLAIM", work_item)
        _require(_same(metrics.get("actual_human_beta_participants"), 0) and _same(metrics.get("production_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0) and _same(metrics.get("godot_live_actions_executed"), 0) and _same(metrics.get("critical_open_bugs"), 0), "G7_REAL_WORLD_OVERCLAIM", work_item)
    else:
        _require(doc.get("verification_status") == "PASS" and doc.get("decision") == "HOLD" and doc.get("decision_pack_complete") is True, "G7_P7_05_STATE_INVALID", work_item)
        _require(doc.get("recommendation_authorized") is False and doc.get("release_authorized") is False and doc.get("formal_wbs_status") == "Not Started", "G7_P7_05_AUTHORIZATION_OVERCLAIM", work_item)
        _require(doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE" and doc.get("formal_wbs_completion_claim") == "NONE", "G7_GATE_OVERCLAIM", work_item)
        _require(_same(metrics.get("actual_human_beta_participants"), 0) and _same(metrics.get("production_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0) and _same(metrics.get("godot_live_actions_executed"), 0) and _same(metrics.get("decision_pack_completeness_percent"), 100.0), "G7_P7_05_STATE_INVALID", work_item)


def _collect_evidence(fixture: Mapping[str, Any], source_hash_overrides: Mapping[str, str | None] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(fixture["repository_root"])
    summaries = []
    docs = {}
    for binding in fixture["evidence_bindings"]:
        work_item = binding["work_item"]
        doc = _load(binding)
        _validate_evidence_semantics(work_item, doc)
        sources = _mapping(doc.get("source_sha256"), "G7_SOURCE_BINDINGS_REQUIRED", work_item)
        _require(len(sources) == 6, "G7_SOURCE_CATALOG_INVALID", work_item)
        verified = {}
        for relative, expected_hash in sorted(sources.items()):
            _require(isinstance(expected_hash, str) and bool(_SHA.fullmatch(expected_hash)), "G7_SOURCE_HASH_INVALID", relative)
            path = _safe_source(root, relative)
            if source_hash_overrides is not None and relative in source_hash_overrides:
                actual = source_hash_overrides[relative]
                _require(actual is not None, "G7_SOURCE_MISSING", relative)
            else:
                _require(path.is_file(), "G7_SOURCE_MISSING", relative)
                actual = _file_sha(path)
            _require(actual == expected_hash, "G7_SOURCE_HASH_MISMATCH", relative)
            verified[relative] = expected_hash
        summaries.append({"work_item": work_item, "evidence_path": binding["path"], "evidence_sha256": binding["evidence_sha256"], "schema_version": binding["schema_version"], "status": binding["required_status"], "source_sha256": verified, "source_binding_count": len(verified)})
        docs[work_item] = doc
    return summaries, docs


def _build_core(value: Mapping[str, Any], source_hash_overrides: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    evidence, docs = _collect_evidence(fixture, source_hash_overrides)
    p705_bindings = {item["work_item"]: item["evidence_sha256"] for item in docs["P7-05"]["evidence_bindings"]}
    expected_transitive = {item: EVIDENCE_REQUIREMENTS[item]["sha256"] for item in ("P7-03", "P7-04")}
    _require(_same(p705_bindings, expected_transitive), "G7_TRANSITIVE_CLOSURE_INVALID", "P7-05 -> P7-03/P7-04")
    transitive = [{"via": "P7-05", "work_item": item, "evidence_sha256": expected_transitive[item], "status": "VERIFIED"} for item in ("P7-03", "P7-04")]
    metrics = {
        "evidence_bindings_passed": len(evidence),
        "evidence_bindings_total": 6,
        "source_bindings_passed": sum(item["source_binding_count"] for item in evidence),
        "source_bindings_total": 36,
        "transitive_bindings_passed": len(transitive),
        "transitive_bindings_total": 2,
        "known_gaps_recorded": len(fixture["known_gaps"]),
        "known_gaps_total": len(REQUIRED_GAPS),
        "required_reviewer_roles_recorded": len(REQUIRED_REVIEWER_ROLES),
        "required_reviewer_roles_total": len(REQUIRED_REVIEWER_ROLES),
        "independent_reviewers": 0,
        "independent_signatures": 0,
        "independent_approvals": 0,
        "g7_pass_claim_count": 0,
        "actual_human_beta_participants": 0,
        "production_records_read": 0,
        "external_contacts_executed": 0,
        "godot_live_actions_executed": 0,
        "release_authorized_count": 0,
        "unsafe_aggregate_count": 0,
    }
    return {
        "schema_version": PACK_SCHEMA,
        "policy_version": POLICY_VERSION,
        "work_item": "P7-06",
        "mechanical_verification_status": "PASS",
        "readiness_status": "READY_FOR_INDEPENDENT_REVIEW",
        "decision": "HOLD",
        "workflow_status": "IN_PROGRESS",
        "completed": False,
        "formal_wbs_status": "Not Started",
        "prepared_at": fixture["prepared_at"],
        "fixture_binding": fixture,
        "fixture_sha256": _sha(fixture),
        "canonical_wbs_source": _copy(fixture["canonical_wbs_source"]),
        "evidence_bindings": evidence,
        "transitive_evidence_closure": transitive,
        "known_gaps": _copy(fixture["known_gaps"]),
        "review_ledger": {
            "preparer_id": fixture["review_state"]["preparer_id"],
            "required_reviewer_roles": list(REQUIRED_REVIEWER_ROLES),
            "independent_reviewer_ids": [],
            "independent_signatures": [],
            "independent_approvals": 0,
            "g7_pass_claim_count": 0,
        },
        "metrics": metrics,
        "claims": {
            "g6_approval": "NONE",
            "g7_pass": "NONE",
            "formal_p7_06_completion": "NONE",
            "actual_human_beta": "NONE",
            "production_data": "NONE",
            "external_contact": "NONE",
            "godot_live": "NONE",
            "p8_release_authorization": "NONE",
        },
        "wbs_promotion": {
            "p7_06_review_pack_candidate": True,
            "formal_wbs_status": "Not Started",
            "formal_wbs_completion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "p8_release_authorized": False,
        },
    }


def _rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except G7ReviewPackError:
        return True
    return False


def _validate_core_candidate(candidate: Mapping[str, Any], fixture: Mapping[str, Any]) -> None:
    _require(_same(candidate, _build_core(fixture)), "G7_CORE_MISMATCH", "candidate")


def _core_attack(base: Mapping[str, Any], fixture: Mapping[str, Any], mutate: Callable[[dict[str, Any]], None]) -> bool:
    candidate = _copy(dict(base))
    mutate(candidate)
    return _rejected(lambda: _validate_core_candidate(candidate, fixture))


def run_false_green_controls(value: Mapping[str, Any]) -> dict[str, bool]:
    fixture = _validate_fixture(value)
    base = _build_core(fixture)
    _validate_core_candidate(base, fixture)
    controls: dict[str, bool] = {}
    fixture_mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("wbs_hash_swap_rejected", lambda item: item["canonical_wbs_source"].update({"workbook_sha256": "0" * 64})),
        ("wbs_snapshot_hash_swap_rejected", lambda item: item["canonical_wbs_source"].update({"snapshot_sha256": "0" * 64})),
        ("wbs_snapshot_path_swap_rejected", lambda item: item["canonical_wbs_source"].update({"snapshot_path": item["canonical_wbs_source"]["workbook_path"]})),
        ("wbs_row_swap_rejected", lambda item: item["canonical_wbs_source"].update({"row": 57})),
        ("wbs_row_float_alias_rejected", lambda item: item["canonical_wbs_source"].update({"row": 58.0})),
        ("wbs_acceptance_swap_rejected", lambda item: item["canonical_wbs_source"].update({"acceptance": "forged"})),
        ("wbs_kpi_gate_pass_removed_rejected", lambda item: item["canonical_wbs_source"].update({"kpi": "Review ready"})),
        ("wbs_dependency_swap_rejected", lambda item: item["canonical_wbs_source"].update({"dependency": "P7-04"})),
        ("missing_evidence_binding_rejected", lambda item: item["evidence_bindings"].pop()),
        ("duplicate_evidence_binding_rejected", lambda item: item["evidence_bindings"].__setitem__(5, _copy(item["evidence_bindings"][0]))),
        ("p7_05_evidence_hash_swap_rejected", lambda item: next(entry for entry in item["evidence_bindings"] if entry["work_item"] == "P7-05").update({"evidence_sha256": "0" * 64})),
        ("g6_evidence_hash_swap_rejected", lambda item: next(entry for entry in item["evidence_bindings"] if entry["work_item"] == "P6-05").update({"evidence_sha256": "0" * 64})),
        ("self_reviewer_invented_rejected", lambda item: item["review_state"]["independent_reviewer_ids"].append(item["review_state"]["preparer_id"])),
        ("independent_signature_invented_rejected", lambda item: item["review_state"]["independent_signatures"].append("forged")),
        ("independent_approval_invented_rejected", lambda item: item["review_state"].update({"independent_approvals": 1})),
        ("g7_pass_claim_invented_rejected", lambda item: item["review_state"].update({"g7_pass_claim_count": 1})),
        ("fixture_decision_pass_rejected", lambda item: item["review_state"].update({"decision": "PASS"})),
        ("fixture_workflow_completed_rejected", lambda item: item["review_state"].update({"workflow_status": "COMPLETED"})),
        ("fixture_completed_true_rejected", lambda item: item["review_state"].update({"completed": True})),
        ("known_gap_removed_rejected", lambda item: item["known_gaps"].pop()),
        ("known_gap_falsely_completed_rejected", lambda item: item["known_gaps"][0].update({"status": "COMPLETE"})),
        ("known_gap_owner_missing_rejected", lambda item: item["known_gaps"][0].update({"owner": None})),
        ("known_gap_due_noncanonical_rejected", lambda item: item["known_gaps"][0].update({"due_at": "2026-09-14T09:00:00+09:00"})),
        ("expectation_source_float_alias_rejected", lambda item: item["expectations"].update({"source_bindings": 36.0})),
    ]
    for name, mutate in fixture_mutations:
        candidate = _copy(fixture)
        mutate(candidate)
        controls[name] = _rejected(lambda candidate=candidate: _build_core(candidate))
    core_mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("mechanical_pass_to_gate_pass_rejected", lambda item: item.update({"mechanical_verification_status": "G7_PASS"})),
        ("readiness_to_gate_pass_rejected", lambda item: item.update({"readiness_status": "G7_PASS"})),
        ("decision_hold_to_pass_rejected", lambda item: item.update({"decision": "PASS"})),
        ("workflow_completed_rejected", lambda item: item.update({"workflow_status": "COMPLETED"})),
        ("completed_true_rejected", lambda item: item.update({"completed": True})),
        ("formal_status_completed_rejected", lambda item: item.update({"formal_wbs_status": "Completed"})),
        ("evidence_binding_removed_rejected", lambda item: item["evidence_bindings"].pop()),
        ("evidence_digest_changed_rejected", lambda item: item["evidence_bindings"][0].update({"evidence_sha256": "f" * 64})),
        ("source_binding_removed_rejected", lambda item: item["evidence_bindings"][0]["source_sha256"].pop(sorted(item["evidence_bindings"][0]["source_sha256"])[0])),
        ("transitive_binding_removed_rejected", lambda item: item["transitive_evidence_closure"].pop()),
        ("transitive_digest_changed_rejected", lambda item: item["transitive_evidence_closure"][0].update({"evidence_sha256": "0" * 64})),
        ("known_gap_hidden_rejected", lambda item: item["known_gaps"].pop()),
        ("known_gap_completed_without_evidence_rejected", lambda item: item["known_gaps"][0].update({"status": "COMPLETE"})),
        ("reviewer_invented_rejected", lambda item: item["review_ledger"]["independent_reviewer_ids"].append("invented")),
        ("signature_invented_rejected", lambda item: item["review_ledger"]["independent_signatures"].append("forged")),
        ("approval_invented_rejected", lambda item: item["review_ledger"].update({"independent_approvals": 1})),
        ("g7_claim_count_invented_rejected", lambda item: item["review_ledger"].update({"g7_pass_claim_count": 1})),
        ("review_role_removed_rejected", lambda item: item["review_ledger"]["required_reviewer_roles"].pop()),
        ("evidence_count_float_alias_rejected", lambda item: item["metrics"].update({"evidence_bindings_passed": 6.0})),
        ("source_count_below_expected_rejected", lambda item: item["metrics"].update({"source_bindings_passed": 35})),
        ("transitive_count_below_expected_rejected", lambda item: item["metrics"].update({"transitive_bindings_passed": 1})),
        ("gap_count_hidden_rejected", lambda item: item["metrics"].update({"known_gaps_recorded": 10})),
        ("independent_reviewer_metric_invented_rejected", lambda item: item["metrics"].update({"independent_reviewers": 1})),
        ("signature_metric_invented_rejected", lambda item: item["metrics"].update({"independent_signatures": 1})),
        ("approval_metric_invented_rejected", lambda item: item["metrics"].update({"independent_approvals": 1})),
        ("g7_metric_claim_invented_rejected", lambda item: item["metrics"].update({"g7_pass_claim_count": 1})),
        ("human_metric_overclaim_rejected", lambda item: item["metrics"].update({"actual_human_beta_participants": 1})),
        ("production_metric_overclaim_rejected", lambda item: item["metrics"].update({"production_records_read": 1})),
        ("external_metric_overclaim_rejected", lambda item: item["metrics"].update({"external_contacts_executed": 1})),
        ("godot_metric_overclaim_rejected", lambda item: item["metrics"].update({"godot_live_actions_executed": 1})),
        ("release_metric_overclaim_rejected", lambda item: item["metrics"].update({"release_authorized_count": 1})),
        ("unsafe_aggregate_nonzero_rejected", lambda item: item["metrics"].update({"unsafe_aggregate_count": 1})),
        ("g6_claim_rejected", lambda item: item["claims"].update({"g6_approval": "PASS"})),
        ("g7_claim_rejected", lambda item: item["claims"].update({"g7_pass": "PASS"})),
        ("formal_completion_claim_rejected", lambda item: item["claims"].update({"formal_p7_06_completion": "COMPLETE"})),
        ("human_claim_rejected", lambda item: item["claims"].update({"actual_human_beta": "COMPLETE"})),
        ("production_claim_rejected", lambda item: item["claims"].update({"production_data": "VALIDATED"})),
        ("godot_claim_rejected", lambda item: item["claims"].update({"godot_live": "PASS"})),
        ("p8_release_claim_rejected", lambda item: item["claims"].update({"p8_release_authorization": "APPROVED"})),
        ("promotion_formal_completion_rejected", lambda item: item["wbs_promotion"].update({"formal_wbs_completion_allowed": True})),
        ("promotion_g6_overclaim_rejected", lambda item: item["wbs_promotion"].update({"g6_approved": True})),
        ("promotion_g7_overclaim_rejected", lambda item: item["wbs_promotion"].update({"g7_approved": True})),
        ("promotion_p8_release_rejected", lambda item: item["wbs_promotion"].update({"p8_release_authorized": True})),
    ]
    for name, mutate in core_mutations:
        controls[name] = _core_attack(base, fixture, mutate)
    first_source = next(iter(base["evidence_bindings"][0]["source_sha256"]))
    controls["current_source_hash_mismatch_rejected"] = _rejected(lambda: _build_core(fixture, {first_source: "0" * 64}))
    controls["current_source_missing_rejected"] = _rejected(lambda: _build_core(fixture, {first_source: None}))
    return controls


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    value["pack_sha256"] = _sha({key: _copy(item) for key, item in value.items() if key != "pack_sha256"})
    return value


def evaluate_g7_review_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    result = _build_core(fixture)
    controls = run_false_green_controls(fixture)
    _require(len(controls) >= fixture["expectations"]["false_green_controls_min"] and all(controls.values()), "G7_FALSE_GREEN_CONTROL_FAILED", f"{sum(controls.values())}/{len(controls)}")
    result["false_green_controls"] = controls
    result["metrics"].update({"false_green_controls_passed": len(controls), "false_green_controls_total": len(controls), "false_green_rejection_percent": 100.0, "deterministic_replay_percent": 100.0})
    _seal(result)
    validate_g7_review_pack(result)
    return result


def validate_g7_review_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    pack = _mapping(value, "G7_PACK_OBJECT_REQUIRED", "pack")
    fixture = _mapping(pack.get("fixture_binding"), "G7_FIXTURE_BINDING_REQUIRED", "fixture_binding")
    expected = _build_core(fixture)
    _exact(pack, set(expected) | {"false_green_controls", "pack_sha256"}, "G7_PACK_FIELDS_INVALID", "pack")
    _require(pack.get("pack_sha256") == _sha({key: _copy(item) for key, item in pack.items() if key != "pack_sha256"}), "G7_PACK_TAMPERED", "pack_sha256")
    controls = pack.get("false_green_controls")
    _require(isinstance(controls, Mapping) and all(item is True for item in controls.values()), "G7_FALSE_GREEN_CONTROL_FAILED", "controls")
    _require(len(controls) == 69 and _sha(sorted(controls)) == "edd04fef2f39958289f4cc80e4d5d31937b2ac751fa9d89b9a7a1d02861832a8", "G7_FALSE_GREEN_NAMESET_INVALID", "controls")
    expected_metrics = {**expected["metrics"], "false_green_controls_passed": len(controls), "false_green_controls_total": len(controls), "false_green_rejection_percent": 100.0, "deterministic_replay_percent": 100.0}
    for field, expected_value in expected.items():
        if field == "metrics":
            _require(_same(pack.get(field), expected_metrics), "G7_METRIC_MISMATCH", field)
        else:
            _require(_same(pack.get(field), expected_value), "G7_PACK_CORE_MISMATCH", field)
    return _copy(dict(pack))


__all__ = [
    "CANONICAL_WBS",
    "EVIDENCE_REQUIREMENTS",
    "FIXTURE_SCHEMA",
    "PACK_SCHEMA",
    "POLICY_VERSION",
    "REQUIRED_GAPS",
    "REQUIRED_REVIEWER_ROLES",
    "G7ReviewPackError",
    "evaluate_g7_review_fixture",
    "run_false_green_controls",
    "validate_g7_review_pack",
]
