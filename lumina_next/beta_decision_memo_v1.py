"""P7-05 deterministic Beta Decision Memo reference.

This module can prove that a decision packet is structurally complete.  It
cannot authorize a release, approve G6/G7, or turn synthetic evidence into
human/production evidence.
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

from .quality_triage_v1 import CANONICAL_WBS as P7_04_WBS


FIXTURE_SCHEMA = "lumina.beta-decision-memo.fixture.v1"
MEMO_SCHEMA = "lumina.beta-decision-memo.v1"
POLICY_VERSION = "lumina.beta-decision-memo.policy.v1"
REPOSITORY_ROOT = "/LOCAL_RUNTIME_NOT_INCLUDED"
WBS_WORKBOOK_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/lumina_plan_update_2026-08-30/lumina_virtual_agent_master_plan.xlsx"
WBS_WORKBOOK_SHA256 = "3037f976bb6b1d93a36126d3a96a4edf8a3d23dc06d7082ff4c9569bb935f7c2"
WBS_SNAPSHOT_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_beta_decision_20260831/canonical_wbs_snapshot.xlsx"
CANONICAL_WBS = {
    "work_item": "P7-05",
    "phase": "P7",
    "type": "Beta",
    "task": "release recommendation",
    "objective": "継続・縮小・中止をデータで判断",
    "deliverable": "Beta Decision Memo",
    "owner": "PM/PO",
    "priority": "P0",
    "dependency": "P7-03:P7-04",
    "formal_status": "Not Started",
    "acceptance": "KPI・リスク・学習事項が意思決定可能",
    "kpi": "Decision pack complete",
    "gate": "G7",
}
EVIDENCE_REQUIREMENTS = {
    "P7-03": {
        "path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_longitudinal_beta_20260831/longitudinal_beta_acceptance.json",
        "sha256": "6266162efd2a2ac08594b6198c3fb70303c60bf5a5d72bdda38482f90ac5651b",
        "schema_version": "lumina.longitudinal-beta.acceptance.v1",
        "required_status": "PASS_SYNTHETIC_PIPELINE",
    },
    "P7-04": {
        "path": "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_quality_triage_20260831/quality_triage_acceptance.json",
        "sha256": "d1c5341340db98e1c0c7312c7904476c77e4201e26eae29346766891c0e30cd7",
        "schema_version": "lumina.quality-triage.acceptance.v1",
        "required_status": "SYNTHETIC_TRIAGE_REFERENCE_PASS",
    },
}
DECISION_OPTIONS = ("CONTINUE", "SCALE_DOWN", "STOP")
REQUIRED_SECTIONS = (
    "evidence",
    "kpis",
    "risks",
    "learnings",
    "unknowns",
    "recommendation",
    "reconsideration_conditions",
)
RISK_IDS = (
    "RISK_G6_HOLD",
    "RISK_G7_NOT_APPROVED",
    "RISK_HUMAN_BETA_ABSENT",
    "RISK_PRODUCTION_DATA_ABSENT",
    "RISK_GODOT_LIVE_ABSENT",
    "RISK_SYNTHETIC_KPI_NON_GENERALIZABLE",
)
UNKNOWN_IDS = (
    "ACTUAL_D30_RETENTION",
    "ACTUAL_TRUST_SCORE",
    "ACTUAL_PRODUCT_VALUE",
    "PRODUCTION_ISSUE_PROFILE",
    "GODOT_LIVE_BEHAVIOR",
)
CONDITION_IDS = (
    "G6_INDEPENDENT_PASS",
    "AUTHORIZED_HUMAN_BETA",
    "AUTHORIZED_PRODUCTION_DATA",
    "G7_INDEPENDENT_APPROVAL",
    "GODOT_LIVE_VALIDATION",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class BetaDecisionMemoError(ValueError):
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
        raise BetaDecisionMemoError("DECISION_NONCANONICAL_JSON", "payload") from exc


def _same(actual: Any, expected: Any) -> bool:
    return _canonical(actual) == _canonical(expected)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BetaDecisionMemoError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise BetaDecisionMemoError(code, detail)


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise BetaDecisionMemoError(code, detail)


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise BetaDecisionMemoError("DECISION_TIME_INVALID", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BetaDecisionMemoError("DECISION_TIME_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise BetaDecisionMemoError("DECISION_TIME_INVALID", field)
    normalized = parsed.astimezone(timezone.utc)
    canonical = normalized.isoformat().replace("+00:00", "Z")
    _require(value == canonical, "DECISION_TIME_NONCANONICAL", field)
    return normalized


def _safe_source(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise BetaDecisionMemoError("DECISION_SOURCE_PATH_INVALID", str(relative))
    candidate = (root / relative).resolve()
    try:
        inside = os.path.commonpath((str(root.resolve()), str(candidate))) == str(root.resolve())
    except ValueError:
        inside = False
    _require(inside, "DECISION_SOURCE_PATH_INVALID", relative)
    return candidate


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "DECISION_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(
        fixture,
        {"schema_version", "prepared_at", "repository_root", "canonical_wbs_source", "evidence_bindings", "decision_policy", "gate_status", "expectations"},
        "DECISION_FIXTURE_FIELDS_INVALID",
        "fixture",
    )
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "DECISION_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    _time(fixture["prepared_at"], "prepared_at")
    _require(fixture["repository_root"] == REPOSITORY_ROOT and Path(fixture["repository_root"]).is_dir(), "DECISION_REPOSITORY_ROOT_INVALID", "repository_root")

    source = _mapping(fixture["canonical_wbs_source"], "DECISION_WBS_SOURCE_REQUIRED", "canonical_wbs_source")
    _exact(source, {"workbook_path", "workbook_sha256", "snapshot_path", "snapshot_sha256", "sheet", "row", "range", *CANONICAL_WBS.keys()}, "DECISION_WBS_SOURCE_FIELDS_INVALID", "canonical_wbs_source")
    _require(source["workbook_path"] == WBS_WORKBOOK_PATH and source["workbook_sha256"] == WBS_WORKBOOK_SHA256 and source["snapshot_path"] == WBS_SNAPSHOT_PATH and source["snapshot_sha256"] == WBS_WORKBOOK_SHA256, "DECISION_WBS_IDENTITY_MISMATCH", "workbook")
    workbook = Path(source["workbook_path"])
    snapshot = Path(source["snapshot_path"])
    _require(workbook.is_file(), "DECISION_WBS_WORKBOOK_MISSING", str(workbook))
    _require(snapshot.is_file() and _file_sha(snapshot) == source["snapshot_sha256"], "DECISION_WBS_HASH_MISMATCH", str(snapshot))
    _require(_same({"sheet": source["sheet"], "row": source["row"], "range": source["range"]}, {"sheet": "02_WBS_Detail", "row": 57, "range": "A57:P57"}), "DECISION_WBS_LOCATOR_MISMATCH", "row57")
    for field, expected in CANONICAL_WBS.items():
        _require(source[field] == expected, "DECISION_WBS_TEXT_MISMATCH", field)

    bindings = fixture["evidence_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == 2, "DECISION_EVIDENCE_CATALOG_INVALID", "2 bindings")
    normalized_bindings = []
    for raw in bindings:
        binding = _mapping(raw, "DECISION_EVIDENCE_BINDING_REQUIRED", "binding")
        _exact(binding, {"work_item", "path", "evidence_sha256", "schema_version", "required_status"}, "DECISION_EVIDENCE_BINDING_FIELDS_INVALID", "binding")
        work_item = binding["work_item"]
        _require(work_item in EVIDENCE_REQUIREMENTS, "DECISION_EVIDENCE_WORK_ITEM_UNKNOWN", str(work_item))
        expected = EVIDENCE_REQUIREMENTS[work_item]
        _require(_same(binding, {"work_item": work_item, "path": expected["path"], "evidence_sha256": expected["sha256"], "schema_version": expected["schema_version"], "required_status": expected["required_status"]}), "DECISION_EVIDENCE_IDENTITY_MISMATCH", work_item)
        normalized_bindings.append(_copy(dict(binding)))
    _require({item["work_item"] for item in normalized_bindings} == set(EVIDENCE_REQUIREMENTS), "DECISION_EVIDENCE_CATALOG_INVALID", "unique P7-03/P7-04")

    policy = _mapping(fixture["decision_policy"], "DECISION_POLICY_REQUIRED", "decision_policy")
    expected_policy = {
        "candidate_decisions": list(DECISION_OPTIONS),
        "interim_disposition": "HOLD",
        "evidence_scope": "SYNTHETIC_REFERENCE_ONLY",
        "completion_semantics": "PACK_COMPLETE_DECISION_EXECUTION_BLOCKED",
        "required_sections": list(REQUIRED_SECTIONS),
    }
    _require(_same(policy, expected_policy), "DECISION_POLICY_INVALID", "decision_policy")
    gate = _mapping(fixture["gate_status"], "DECISION_GATE_STATUS_REQUIRED", "gate_status")
    _exact(gate, {"g6_approved", "g7_approved", "actual_human_beta_complete", "production_data_authorized", "godot_live_validated", "formal_wbs_complete"}, "DECISION_GATE_STATUS_FIELDS_INVALID", "gate_status")
    _require(all(item is False for item in gate.values()), "DECISION_GATE_OVERCLAIM", "gate_status")
    expectations = _mapping(fixture["expectations"], "DECISION_EXPECTATIONS_REQUIRED", "expectations")
    expected_expectations = {"evidence_bindings": 2, "source_bindings": 12, "required_sections": 7, "risk_count": 6, "learning_count": 5, "unknown_count": 5, "false_green_controls_min": 40}
    _require(_same(expectations, expected_expectations), "DECISION_EXPECTATIONS_INVALID", "expectations")
    normalized = _copy(dict(fixture))
    normalized["evidence_bindings"] = sorted(normalized_bindings, key=lambda item: item["work_item"])
    return normalized


def _load_evidence(binding: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(binding["path"])
    _require(path.is_file(), "DECISION_EVIDENCE_MISSING", str(path))
    _require(_file_sha(path) == binding["evidence_sha256"], "DECISION_EVIDENCE_HASH_MISMATCH", binding["work_item"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BetaDecisionMemoError("DECISION_EVIDENCE_JSON_INVALID", binding["work_item"]) from exc
    _require(doc.get("schema_version") == binding["schema_version"] and doc.get("work_item") == binding["work_item"], "DECISION_EVIDENCE_SCHEMA_MISMATCH", binding["work_item"])
    _require(doc.get("status") == binding["required_status"], "DECISION_EVIDENCE_STATUS_INVALID", binding["work_item"])
    return doc


def _evidence_summary(fixture: Mapping[str, Any], source_hash_overrides: Mapping[str, str | None] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(fixture["repository_root"])
    summaries = []
    documents: dict[str, Any] = {}
    for binding in fixture["evidence_bindings"]:
        work_item = binding["work_item"]
        doc = _load_evidence(binding)
        metrics = _mapping(doc.get("metrics"), "DECISION_EVIDENCE_METRICS_REQUIRED", work_item)
        promotion = _mapping(doc.get("wbs_promotion"), "DECISION_EVIDENCE_PROMOTION_REQUIRED", work_item)
        _require(doc.get("g6_claim") == "NONE" and doc.get("g7_claim") == "NONE", "DECISION_GATE_OVERCLAIM", work_item)
        if work_item == "P7-03":
            _require(promotion.get("formal_wbs_promotion_allowed") is False and promotion.get("actual_longitudinal_beta_complete") is False, "DECISION_EVIDENCE_PROMOTION_OVERCLAIM", work_item)
            _require(_same(metrics.get("actual_human_participants"), 0) and _same(metrics.get("production_user_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0), "DECISION_REAL_WORLD_SCOPE_OVERCLAIM", work_item)
            _require(_same(metrics.get("critical_issues"), 0), "DECISION_CRITICAL_SIGNAL_INVALID", work_item)
        else:
            _require(doc.get("verification_status") == "PASS" and doc.get("formal_wbs_status") == "Not Started", "DECISION_EVIDENCE_STATUS_INVALID", work_item)
            _require(promotion.get("formal_wbs_completion_allowed") is False, "DECISION_EVIDENCE_PROMOTION_OVERCLAIM", work_item)
            _require(_same(metrics.get("actual_human_beta_participants"), 0) and _same(metrics.get("production_records_read"), 0) and _same(metrics.get("external_contacts_executed"), 0) and _same(metrics.get("godot_live_actions_executed"), 0), "DECISION_REAL_WORLD_SCOPE_OVERCLAIM", work_item)
            _require(_same(metrics.get("critical_open_bugs"), 0), "DECISION_CRITICAL_SIGNAL_INVALID", work_item)
            _require(doc.get("claims", {}).get("p7_03_issue_level_trace") == "NONE", "DECISION_TRACE_OVERCLAIM", work_item)
        sources = _mapping(doc.get("source_sha256"), "DECISION_SOURCE_BINDINGS_REQUIRED", work_item)
        _require(len(sources) == 6, "DECISION_SOURCE_CATALOG_INVALID", work_item)
        verified: dict[str, str] = {}
        for relative, expected_hash in sorted(sources.items()):
            _require(isinstance(expected_hash, str) and bool(_SHA.fullmatch(expected_hash)), "DECISION_SOURCE_HASH_INVALID", relative)
            path = _safe_source(root, relative)
            if source_hash_overrides is not None and relative in source_hash_overrides:
                actual = source_hash_overrides[relative]
                _require(actual is not None, "DECISION_SOURCE_MISSING", relative)
            else:
                _require(path.is_file(), "DECISION_SOURCE_MISSING", relative)
                actual = _file_sha(path)
            _require(actual == expected_hash, "DECISION_SOURCE_HASH_MISMATCH", relative)
            verified[relative] = expected_hash
        summaries.append({
            "work_item": work_item,
            "evidence_path": binding["path"],
            "evidence_sha256": binding["evidence_sha256"],
            "schema_version": binding["schema_version"],
            "status": binding["required_status"],
            "source_sha256": verified,
            "source_binding_count": len(verified),
        })
        documents[work_item] = doc
    return summaries, documents


def _build_core(value: Mapping[str, Any], source_hash_overrides: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    evidence, docs = _evidence_summary(fixture, source_hash_overrides)
    p703 = docs["P7-03"]["metrics"]
    p704 = docs["P7-04"]["metrics"]
    kpis = [
        {"kpi_id": "SYNTHETIC_D30_RETENTION", "value": p703["synthetic_d30_retention_percent"], "unit": "percent", "scope": "synthetic_reference", "evidence_work_item": "P7-03", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-03"]["sha256"]},
        {"kpi_id": "SYNTHETIC_MEAN_TRUST", "value": p703["synthetic_mean_trust_score"], "unit": "score_1_to_5", "scope": "synthetic_reference", "evidence_work_item": "P7-03", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-03"]["sha256"]},
        {"kpi_id": "CRITICAL_OPEN_BUGS", "value": p704["critical_open_bugs"], "unit": "count", "scope": "synthetic_reference", "evidence_work_item": "P7-04", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-04"]["sha256"]},
        {"kpi_id": "NONCRITICAL_DEADLINE_COVERAGE", "value": p704["noncritical_deadline_coverage_percent"], "unit": "percent", "scope": "synthetic_reference", "evidence_work_item": "P7-04", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-04"]["sha256"]},
        {"kpi_id": "ACTUAL_HUMAN_PARTICIPANTS", "value": 0, "unit": "count", "scope": "MISSING", "evidence_work_item": "P7-03", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-03"]["sha256"]},
        {"kpi_id": "PRODUCTION_RECORDS_READ", "value": 0, "unit": "count", "scope": "MISSING", "evidence_work_item": "P7-04", "evidence_sha256": EVIDENCE_REQUIREMENTS["P7-04"]["sha256"]},
    ]
    due = "2026-09-07T00:00:00Z"
    _time(due, "due_at")
    risk_owners = {
        "RISK_G6_HOLD": "PM",
        "RISK_G7_NOT_APPROVED": "PM/PO",
        "RISK_HUMAN_BETA_ABSENT": "UX",
        "RISK_PRODUCTION_DATA_ABSENT": "Privacy/PM",
        "RISK_GODOT_LIVE_ABSENT": "Runtime/QA",
        "RISK_SYNTHETIC_KPI_NON_GENERALIZABLE": "QA/UX",
    }
    risks = [{"risk_id": risk_id, "severity": "HIGH", "status": "OPEN", "owner": risk_owners[risk_id], "due_at": due, "evidence_refs": ["P7-03", "P7-04"]} for risk_id in RISK_IDS]
    learnings = [
        {"learning_id": "SYNTHETIC_RETENTION_SIGNAL", "statement": "Synthetic D30 retention is 75.0%; it is not a human KPI.", "evidence_work_item": "P7-03"},
        {"learning_id": "SYNTHETIC_TRUST_SIGNAL", "statement": "Synthetic mean trust is 3.939/5; it is not a human trust result.", "evidence_work_item": "P7-03"},
        {"learning_id": "DISCOMFORT_TAXONOMY_COVERED", "statement": "Six synthetic discomfort categories are classified.", "evidence_work_item": "P7-03"},
        {"learning_id": "SYNTHETIC_TRIAGE_CRITICAL_ZERO", "statement": "All 20 synthetic critical issues are closed with zero open.", "evidence_work_item": "P7-04"},
        {"learning_id": "DEADLINE_COVERAGE_COMPLETE", "statement": "All 140 synthetic noncritical issues have deadlines.", "evidence_work_item": "P7-04"},
    ]
    unknown_owners = {"ACTUAL_D30_RETENTION": "UX", "ACTUAL_TRUST_SCORE": "UX", "ACTUAL_PRODUCT_VALUE": "PM/PO", "PRODUCTION_ISSUE_PROFILE": "QA/Privacy", "GODOT_LIVE_BEHAVIOR": "Runtime/QA"}
    unknowns = [{"unknown_id": item, "status": "MISSING", "owner": unknown_owners[item], "due_at": due} for item in UNKNOWN_IDS]
    condition_owners = {"G6_INDEPENDENT_PASS": "PM/Safety/QA", "AUTHORIZED_HUMAN_BETA": "PM/UX", "AUTHORIZED_PRODUCTION_DATA": "Privacy/PM", "G7_INDEPENDENT_APPROVAL": "Exec/PM/Safety", "GODOT_LIVE_VALIDATION": "Runtime/QA"}
    conditions = [{"condition_id": item, "status": "MISSING", "owner": condition_owners[item], "due_at": due} for item in CONDITION_IDS]
    metrics = {
        "evidence_bindings_passed": len(evidence),
        "evidence_bindings_total": 2,
        "source_bindings_passed": sum(item["source_binding_count"] for item in evidence),
        "source_bindings_total": 12,
        "required_sections_passed": len(REQUIRED_SECTIONS),
        "required_sections_total": len(REQUIRED_SECTIONS),
        "decision_pack_completeness_percent": 100.0,
        "kpi_cards_passed": len(kpis),
        "kpi_cards_total": len(kpis),
        "risk_entries_with_owner_due": len(risks),
        "risk_entries_total": len(risks),
        "learning_entries_with_provenance": len(learnings),
        "learning_entries_total": len(learnings),
        "unknown_entries_with_owner_due": len(unknowns),
        "unknown_entries_total": len(unknowns),
        "decision_options_assessed": len(DECISION_OPTIONS),
        "decision_options_total": len(DECISION_OPTIONS),
        "blocking_conditions_remaining": len(conditions),
        "critical_open_bugs": 0,
        "actual_human_beta_participants": 0,
        "production_records_read": 0,
        "external_contacts_executed": 0,
        "godot_live_actions_executed": 0,
        "independent_approvals": 0,
        "recommendation_authorized_count": 0,
        "release_authorized_count": 0,
    }
    return {
        "schema_version": MEMO_SCHEMA,
        "policy_version": POLICY_VERSION,
        "work_item": "P7-05",
        "status": "DECISION_PACK_COMPLETE_HOLD",
        "formal_wbs_status": "Not Started",
        "prepared_at": fixture["prepared_at"],
        "fixture_binding": fixture,
        "fixture_sha256": _sha(fixture),
        "canonical_wbs_source": _copy(fixture["canonical_wbs_source"]),
        "evidence_bindings": evidence,
        "kpis": kpis,
        "risks": risks,
        "learnings": learnings,
        "unknowns": unknowns,
        "recommendation": {
            "candidate_decisions_assessed": list(DECISION_OPTIONS),
            "selected_candidate": "NONE",
            "interim_disposition": "HOLD",
            "basis": "INSUFFICIENT_AUTHORIZED_REAL_WORLD_EVIDENCE",
            "decision_pack_complete": True,
            "recommendation_authorized": False,
            "release_authorized": False,
            "executable": False,
        },
        "reconsideration_conditions": conditions,
        "metrics": metrics,
        "claims": {
            "g6_approval": "NONE",
            "g7_approval": "NONE",
            "actual_human_beta": "NONE",
            "production_data": "NONE",
            "external_contact": "NONE",
            "godot_live": "NONE",
            "release_authorization": "NONE",
            "formal_wbs_completion": "NONE",
        },
        "wbs_promotion": {
            "p7_05_local_decision_pack_candidate": True,
            "decision_pack_complete": True,
            "recommendation": "HOLD",
            "recommendation_authorized": False,
            "release_authorized": False,
            "formal_wbs_status": "Not Started",
            "formal_wbs_completion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
        },
    }


def _rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except BetaDecisionMemoError:
        return True
    return False


def _validate_core_candidate(candidate: Mapping[str, Any], fixture: Mapping[str, Any]) -> None:
    expected = _build_core(fixture)
    _require(_same(candidate, expected), "DECISION_CORE_MISMATCH", "candidate")


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
        ("wbs_row_swap_rejected", lambda item: item["canonical_wbs_source"].update({"row": 56})),
        ("wbs_row_float_alias_rejected", lambda item: item["canonical_wbs_source"].update({"row": 57.0})),
        ("wbs_acceptance_swap_rejected", lambda item: item["canonical_wbs_source"].update({"acceptance": "forged"})),
        ("wbs_kpi_swap_rejected", lambda item: item["canonical_wbs_source"].update({"kpi": "Gate pass"})),
        ("wbs_dependency_swap_rejected", lambda item: item["canonical_wbs_source"].update({"dependency": "P7-04"})),
        ("missing_evidence_binding_rejected", lambda item: item["evidence_bindings"].pop()),
        ("duplicate_evidence_binding_rejected", lambda item: item["evidence_bindings"].__setitem__(1, _copy(item["evidence_bindings"][0]))),
        ("evidence_hash_swap_rejected", lambda item: item["evidence_bindings"][0].update({"evidence_sha256": "0" * 64})),
        ("candidate_decision_catalog_swap_rejected", lambda item: item["decision_policy"]["candidate_decisions"].pop()),
        ("fixture_hold_to_continue_rejected", lambda item: item["decision_policy"].update({"interim_disposition": "CONTINUE"})),
        ("fixture_completion_semantics_swap_rejected", lambda item: item["decision_policy"].update({"completion_semantics": "RELEASE_AUTHORIZED"})),
        ("fixture_g6_overclaim_rejected", lambda item: item["gate_status"].update({"g6_approved": True})),
        ("fixture_g7_overclaim_rejected", lambda item: item["gate_status"].update({"g7_approved": True})),
        ("fixture_formal_completion_overclaim_rejected", lambda item: item["gate_status"].update({"formal_wbs_complete": True})),
        ("expectation_source_count_alias_rejected", lambda item: item["expectations"].update({"source_bindings": 12.0})),
    ]
    for name, mutate in fixture_mutations:
        candidate = _copy(fixture)
        mutate(candidate)
        controls[name] = _rejected(lambda candidate=candidate: _build_core(candidate))

    core_mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("status_gate_pass_rejected", lambda item: item.update({"status": "G7_PASS"})),
        ("formal_status_completed_rejected", lambda item: item.update({"formal_wbs_status": "Completed"})),
        ("evidence_binding_removed_rejected", lambda item: item["evidence_bindings"].pop()),
        ("evidence_digest_changed_rejected", lambda item: item["evidence_bindings"][0].update({"evidence_sha256": "f" * 64})),
        ("source_catalog_removed_rejected", lambda item: item["evidence_bindings"][0]["source_sha256"].pop(sorted(item["evidence_bindings"][0]["source_sha256"])[0])),
        ("synthetic_d30_relabelled_real_rejected", lambda item: item["kpis"][0].update({"scope": "actual_human"})),
        ("synthetic_trust_relabelled_real_rejected", lambda item: item["kpis"][1].update({"scope": "actual_human"})),
        ("kpi_value_changed_rejected", lambda item: item["kpis"][0].update({"value": 99.0})),
        ("kpi_removed_rejected", lambda item: item["kpis"].pop()),
        ("risk_removed_rejected", lambda item: item["risks"].pop()),
        ("risk_owner_missing_rejected", lambda item: item["risks"][0].update({"owner": None})),
        ("risk_due_missing_rejected", lambda item: item["risks"][0].update({"due_at": None})),
        ("learning_removed_rejected", lambda item: item["learnings"].pop()),
        ("learning_provenance_missing_rejected", lambda item: item["learnings"][0].update({"evidence_work_item": None})),
        ("unknown_hidden_rejected", lambda item: item["unknowns"].pop()),
        ("unknown_falsely_completed_rejected", lambda item: item["unknowns"][0].update({"status": "COMPLETE"})),
        ("unknown_owner_missing_rejected", lambda item: item["unknowns"][0].update({"owner": None})),
        ("unknown_due_noncanonical_rejected", lambda item: item["unknowns"][0].update({"due_at": "2026-09-07T09:00:00+09:00"})),
        ("hold_to_continue_rejected", lambda item: item["recommendation"].update({"interim_disposition": "CONTINUE", "selected_candidate": "CONTINUE"})),
        ("hold_to_scale_down_rejected", lambda item: item["recommendation"].update({"interim_disposition": "SCALE_DOWN", "selected_candidate": "SCALE_DOWN"})),
        ("hold_to_stop_rejected", lambda item: item["recommendation"].update({"interim_disposition": "STOP", "selected_candidate": "STOP"})),
        ("pack_complete_to_authorized_rejected", lambda item: item["recommendation"].update({"recommendation_authorized": True})),
        ("release_authorized_rejected", lambda item: item["recommendation"].update({"release_authorized": True, "executable": True})),
        ("condition_removed_rejected", lambda item: item["reconsideration_conditions"].pop()),
        ("condition_without_evidence_completed_rejected", lambda item: item["reconsideration_conditions"][0].update({"status": "COMPLETE"})),
        ("completeness_below_100_rejected", lambda item: item["metrics"].update({"decision_pack_completeness_percent": 99.9})),
        ("completeness_bool_alias_rejected", lambda item: item["metrics"].update({"decision_pack_completeness_percent": True})),
        ("source_count_float_alias_rejected", lambda item: item["metrics"].update({"source_bindings_passed": 12.0})),
        ("critical_open_nonzero_rejected", lambda item: item["metrics"].update({"critical_open_bugs": 1})),
        ("human_participant_nonzero_rejected", lambda item: item["metrics"].update({"actual_human_beta_participants": 1})),
        ("production_record_nonzero_rejected", lambda item: item["metrics"].update({"production_records_read": 1})),
        ("external_contact_nonzero_rejected", lambda item: item["metrics"].update({"external_contacts_executed": 1})),
        ("godot_action_nonzero_rejected", lambda item: item["metrics"].update({"godot_live_actions_executed": 1})),
        ("independent_approval_invented_rejected", lambda item: item["metrics"].update({"independent_approvals": 1})),
        ("g6_claim_rejected", lambda item: item["claims"].update({"g6_approval": "PASS"})),
        ("g7_claim_rejected", lambda item: item["claims"].update({"g7_approval": "PASS"})),
        ("human_claim_rejected", lambda item: item["claims"].update({"actual_human_beta": "COMPLETE"})),
        ("production_claim_rejected", lambda item: item["claims"].update({"production_data": "VALIDATED"})),
        ("release_claim_rejected", lambda item: item["claims"].update({"release_authorization": "APPROVED"})),
        ("formal_completion_claim_rejected", lambda item: item["claims"].update({"formal_wbs_completion": "COMPLETE"})),
        ("promotion_recommendation_authorized_rejected", lambda item: item["wbs_promotion"].update({"recommendation_authorized": True})),
        ("promotion_release_authorized_rejected", lambda item: item["wbs_promotion"].update({"release_authorized": True})),
        ("promotion_formal_completion_rejected", lambda item: item["wbs_promotion"].update({"formal_wbs_completion_allowed": True})),
        ("promotion_g7_overclaim_rejected", lambda item: item["wbs_promotion"].update({"g7_approved": True})),
    ]
    for name, mutate in core_mutations:
        controls[name] = _core_attack(base, fixture, mutate)

    first_source = next(iter(base["evidence_bindings"][0]["source_sha256"]))
    controls["current_source_hash_mismatch_rejected"] = _rejected(lambda: _build_core(fixture, {first_source: "0" * 64}))
    controls["current_source_missing_rejected"] = _rejected(lambda: _build_core(fixture, {first_source: None}))
    return controls


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    value["memo_sha256"] = _sha({key: _copy(item) for key, item in value.items() if key != "memo_sha256"})
    return value


def evaluate_beta_decision_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    result = _build_core(fixture)
    controls = run_false_green_controls(fixture)
    _require(len(controls) >= fixture["expectations"]["false_green_controls_min"] and all(controls.values()), "DECISION_FALSE_GREEN_CONTROL_FAILED", f"{sum(controls.values())}/{len(controls)}")
    result["false_green_controls"] = controls
    result["metrics"].update({
        "false_green_controls_passed": len(controls),
        "false_green_controls_total": len(controls),
        "false_green_rejection_percent": 100.0,
        "deterministic_replay_percent": 100.0,
    })
    _seal(result)
    validate_beta_decision_memo(result)
    return result


def validate_beta_decision_memo(value: Mapping[str, Any]) -> dict[str, Any]:
    memo = _mapping(value, "DECISION_MEMO_OBJECT_REQUIRED", "memo")
    expected_fields = set(_build_core(_mapping(memo.get("fixture_binding"), "DECISION_FIXTURE_BINDING_REQUIRED", "fixture_binding"))) | {"false_green_controls", "memo_sha256"}
    _exact(memo, expected_fields, "DECISION_MEMO_FIELDS_INVALID", "memo")
    _require(memo.get("memo_sha256") == _sha({key: _copy(item) for key, item in memo.items() if key != "memo_sha256"}), "DECISION_MEMO_TAMPERED", "memo_sha256")
    fixture = _validate_fixture(memo["fixture_binding"])
    expected = _build_core(fixture)
    controls = memo.get("false_green_controls")
    _require(isinstance(controls, Mapping) and all(item is True for item in controls.values()), "DECISION_FALSE_GREEN_CONTROL_FAILED", "controls")
    _require(len(controls) == 64 and _sha(sorted(controls)) == "820544b557047265f9c68656f9530f3c114336a9fa678240431fd4860a9eabad", "DECISION_FALSE_GREEN_NAMESET_INVALID", "controls")
    expected_metrics = {**expected["metrics"], "false_green_controls_passed": len(controls), "false_green_controls_total": len(controls), "false_green_rejection_percent": 100.0, "deterministic_replay_percent": 100.0}
    for field, expected_value in expected.items():
        if field == "metrics":
            _require(_same(memo.get(field), expected_metrics), "DECISION_METRIC_MISMATCH", field)
        else:
            _require(_same(memo.get(field), expected_value), "DECISION_MEMO_CORE_MISMATCH", field)
    return _copy(dict(memo))


__all__ = [
    "CANONICAL_WBS",
    "CONDITION_IDS",
    "DECISION_OPTIONS",
    "FIXTURE_SCHEMA",
    "MEMO_SCHEMA",
    "POLICY_VERSION",
    "REQUIRED_SECTIONS",
    "RISK_IDS",
    "UNKNOWN_IDS",
    "BetaDecisionMemoError",
    "evaluate_beta_decision_fixture",
    "run_false_green_controls",
    "validate_beta_decision_memo",
]
