"""Prepare a deterministic HOLD-only G5 embodied review pack for P5-06."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.g5-embodied-review.fixture.v1"
PACK_SCHEMA = "lumina.g5-embodied-review.pack.v1"
WORK_ITEMS = ("P5-01", "P5-02", "P5-03", "P5-04", "P5-05")
EVIDENCE_SCHEMAS = {
    "P5-01": "lumina.embodied.action.acceptance.v1",
    "P5-02": "lumina.spatial.navigation.acceptance.v1",
    "P5-03": "lumina.motion.semantics.acceptance.v1",
    "P5-04": "lumina.proxemics.acceptance.v1",
    "P5-05": "lumina.presence.evaluation.acceptance.v1",
}
REQUIRED_GAPS = (
    "P4-08:INDEPENDENT_APPROVAL",
    "G4:INDEPENDENT_APPROVAL",
    "P5-01:GODOT_ACTION_INTEGRATION",
    "P5-02:GODOT_NAVIGATION_INTEGRATION",
    "P5-03:GODOT_MOTION_INTEGRATION",
    "P5-04:GODOT_PROXEMICS_INTEGRATION",
    "P5-05:HUMAN_PRESENCE_LIFT_STUDY",
    "P5-05:BLIND_REVIEW",
    "P5-06:GODOT_SAFE_IDLE_VISUAL_QA",
    "P5-06:INDEPENDENT_PM_XR_SAFETY_SIGNATURES",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class G5ReviewPackError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise G5ReviewPackError("G5_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise G5ReviewPackError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise G5ReviewPackError(code, "fields must match v1 exactly")


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise G5ReviewPackError(code, detail)


def _inside(root: Path, path: Path, code: str) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        ok = os.path.commonpath((str(resolved_root), str(resolved))) == str(resolved_root)
    except ValueError:
        ok = False
    _require(ok, code, str(path))
    return resolved


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "G5_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "pack_id", "prepared_at", "repository_root", "evidence_root", "evidence_bindings", "dependency_status", "review_state", "known_gaps", "expectations"}, "G5_FIXTURE_FIELDS_INVALID")
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "G5_FIXTURE_SCHEMA_MISMATCH", "schema")
    _require(fixture["pack_id"] == "P5-06:G5-EMBODIED-REVIEW", "G5_PACK_ID_INVALID", "pack")
    _require(isinstance(fixture["prepared_at"], str) and fixture["prepared_at"].endswith("Z"), "G5_PREPARED_AT_INVALID", "prepared_at")
    for field in ("repository_root", "evidence_root"):
        _require(isinstance(fixture[field], str) and Path(fixture[field]).is_absolute(), "G5_ROOT_PATH_INVALID", field)
    bindings = fixture["evidence_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == 5, "G5_EVIDENCE_CATALOG_INVALID", "five bindings")
    normalized: list[dict[str, Any]] = []
    for raw in bindings:
        binding = _mapping(raw, "G5_EVIDENCE_BINDING_REQUIRED", "binding")
        _exact(binding, {"work_item", "path", "evidence_sha256", "schema_version"}, "G5_EVIDENCE_BINDING_FIELDS_INVALID")
        work_item = binding["work_item"]
        _require(work_item in WORK_ITEMS, "G5_EVIDENCE_WORK_ITEM_UNKNOWN", str(work_item))
        _require(binding["schema_version"] == EVIDENCE_SCHEMAS[work_item], "G5_EVIDENCE_SCHEMA_BINDING_INVALID", work_item)
        _require(isinstance(binding["path"], str) and Path(binding["path"]).is_absolute(), "G5_EVIDENCE_PATH_INVALID", work_item)
        _require(isinstance(binding["evidence_sha256"], str) and bool(_SHA.fullmatch(binding["evidence_sha256"])), "G5_EVIDENCE_HASH_INVALID", work_item)
        normalized.append(copy.deepcopy(dict(binding)))
    _require({item["work_item"] for item in normalized} == set(WORK_ITEMS), "G5_EVIDENCE_CATALOG_INVALID", "unique P5-01..05")
    dependencies = _mapping(fixture["dependency_status"], "G5_DEPENDENCY_STATUS_REQUIRED", "dependencies")
    _exact(dependencies, {"p4_08", "g4", "g5"}, "G5_DEPENDENCY_STATUS_FIELDS_INVALID")
    _require(dict(dependencies) == {"p4_08": "NOT_APPROVED", "g4": "NOT_APPROVED", "g5": "NOT_APPROVED"}, "G5_DEPENDENCY_OVERCLAIM", "dependencies")
    review = _mapping(fixture["review_state"], "G5_REVIEW_STATE_REQUIRED", "review")
    _exact(review, {"preparer_id", "independent_reviewer_id", "independent_signatures", "independent_approvals", "g5_pass_claim_count", "readiness_target", "decision", "workflow_status", "completed"}, "G5_REVIEW_STATE_FIELDS_INVALID")
    _require(isinstance(review["preparer_id"], str) and bool(review["preparer_id"]), "G5_PREPARER_REQUIRED", "preparer")
    _require(review["independent_reviewer_id"] is None, "G5_INDEPENDENT_REVIEWER_OVERCLAIM", "reviewer")
    _require(review["independent_signatures"] == [], "G5_INDEPENDENT_SIGNATURE_OVERCLAIM", "signatures")
    _require(review["independent_approvals"] == 0, "G5_INDEPENDENT_APPROVAL_OVERCLAIM", "approvals")
    _require(review["g5_pass_claim_count"] == 0, "G5_PASS_OVERCLAIM", "pass claims")
    _require(review["readiness_target"] == "READY_FOR_INDEPENDENT_REVIEW", "G5_READINESS_TARGET_INVALID", "readiness")
    _require(review["decision"] == "HOLD", "G5_DECISION_MUST_HOLD", "decision")
    _require(review["workflow_status"] == "IN_PROGRESS", "G5_WORKFLOW_OVERCLAIM", "workflow")
    _require(review["completed"] is False, "G5_COMPLETION_OVERCLAIM", "completed")
    gaps = fixture["known_gaps"]
    _require(isinstance(gaps, list) and len(gaps) == len(REQUIRED_GAPS) and set(gaps) == set(REQUIRED_GAPS), "G5_KNOWN_GAP_CATALOG_INVALID", "gaps")
    expectations = _mapping(fixture["expectations"], "G5_EXPECTATIONS_REQUIRED", "expectations")
    _exact(expectations, {"evidence_count", "source_binding_count", "false_green_controls_min", "known_gap_count", "independent_approvals", "g5_pass_claim_count"}, "G5_EXPECTATIONS_FIELDS_INVALID")
    _require(expectations == {"evidence_count": 5, "source_binding_count": 30, "false_green_controls_min": 48, "known_gap_count": 10, "independent_approvals": 0, "g5_pass_claim_count": 0}, "G5_EXPECTATION_INVALID", "expectations")
    result = copy.deepcopy(dict(fixture))
    result["evidence_bindings"] = sorted(normalized, key=lambda item: item["work_item"])
    result["known_gaps"] = sorted(result["known_gaps"])
    return result


def _check_evidence(work_item: str, doc: Mapping[str, Any], checks: list[str]) -> None:
    def check(name: str, condition: bool) -> None:
        _require(condition, "G5_ACCEPTANCE_CONDITION_FAILED", f"{work_item}:{name}")
        checks.append(f"{work_item}:{name}")

    check("schema", doc.get("schema_version") == EVIDENCE_SCHEMAS[work_item])
    check("work_item", doc.get("work_item") == work_item)
    check("status", doc.get("status") == "PASS")
    claims = _mapping(doc.get("claims"), "G5_CLAIMS_REQUIRED", work_item)
    isolation = _mapping(doc.get("isolation"), "G5_ISOLATION_REQUIRED", work_item)
    wbs = _mapping(doc.get("wbs"), "G5_WBS_REQUIRED", work_item)
    check("formal_hold", wbs.get("formal_promotion") == "HOLD_DEPENDENCY_GATE")
    check("network_zero", isolation.get("network_used") is False)
    check("dotnet_mono_zero", isolation.get("dotnet_or_mono_used") is False)
    check("external_zero", isolation.get("external_execution", isolation.get("external_action_execution")) is False)
    if work_item != "P5-05":
        check("gate_claim_none", claims.get("gate_approval") == "NONE")
        check("human_claim_none", claims.get("human_evaluation") == "NONE")
        check("live_claim_none", claims.get("live_integration") == "NONE")
    else:
        check("human_study_absent", claims.get("human_evaluation") is False)
        check("blind_review_absent", claims.get("blind_review") is False)
        check("human_20_lift_absent", claims.get("human_presence_improvement_20_percent") is False)
        check("machine_proxy_only", claims.get("machine_presence_proxy_only") is True)
    metrics = _mapping(doc.get("metrics"), "G5_METRICS_REQUIRED", work_item)
    check("deterministic", metrics.get("deterministic_replay_percent") == 100.0)
    check("trace", metrics.get("trace_correlation_complete_percent") == 100.0)
    check("cross_user_zero", metrics.get("cross_user_leakage") == 0)
    check("duplicates_zero", metrics.get("duplicate_side_effects") == 0)
    if work_item == "P5-01":
        check("dispatch_p95", metrics.get("dispatch_latency_p95_ms", 999999) <= 300)
        check("cancel", metrics.get("cancel_calls", 0) >= 1)
        check("rollback", metrics.get("rollback_calls", 0) >= 1)
        check("unsafe_zero", metrics.get("critical_unsafe_execution") == 0)
    elif work_item == "P5-02":
        check("navigation_success", metrics.get("navigation_success_percent", 0) >= 95)
        check("cancel", metrics.get("cancel_calls", 0) >= 1)
        check("rollback", metrics.get("rollback_calls", 0) >= 1)
        check("unsafe_zero", metrics.get("critical_unsafe_execution") == 0)
    elif work_item == "P5-03":
        check("semantic_match", metrics.get("semantic_match_percent", 0) >= 95)
        check("cancel", metrics.get("cancel_calls", 0) >= 1)
        check("rollback", metrics.get("rollback_calls", 0) >= 1)
        check("unsafe_zero", metrics.get("critical_unsafe_execution") == 0)
    elif work_item == "P5-04":
        check("safe_completion", metrics.get("safe_completion_percent") == 100.0)
        check("cancel", metrics.get("cancel_calls", 0) >= 1)
        check("rollback", metrics.get("rollback_calls", 0) >= 1)
        check("comfort_zero", metrics.get("contact_comfort_violations") == 0)
    else:
        check("machine_proxy", metrics.get("machine_presence_proxy_percent", 0) >= 80.0)
        check("safety", metrics.get("safety_pass_percent") == 100.0)
        check("comfort_zero", metrics.get("comfort_violations") == 0)
        check("contact_zero", metrics.get("contact_violations") == 0)
        check("unsafe_zero", metrics.get("critical_unsafe_execution") == 0)


def evaluate_g5_review_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    repository_root = Path(fixture["repository_root"])
    evidence_root = Path(fixture["evidence_root"])
    _require(repository_root.is_dir(), "G5_REPOSITORY_ROOT_MISSING", str(repository_root))
    _require(evidence_root.is_dir(), "G5_EVIDENCE_ROOT_MISSING", str(evidence_root))
    checks: list[str] = []
    source_checks: list[str] = []
    summaries: list[dict[str, Any]] = []
    for binding in fixture["evidence_bindings"]:
        work_item = binding["work_item"]
        path = _inside(evidence_root, Path(binding["path"]), "G5_EVIDENCE_PATH_OUTSIDE_ROOT")
        _require(path.is_file(), "G5_EVIDENCE_MISSING", str(path))
        _require(_file_sha(path) == binding["evidence_sha256"], "G5_EVIDENCE_HASH_MISMATCH", work_item)
        try:
            doc = _mapping(json.loads(path.read_text(encoding="utf-8")), "G5_EVIDENCE_OBJECT_REQUIRED", work_item)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise G5ReviewPackError("G5_EVIDENCE_JSON_INVALID", work_item) from exc
        _check_evidence(work_item, doc, checks)
        source_map = _mapping(doc.get("source_sha256"), "G5_SOURCE_BINDINGS_REQUIRED", work_item)
        _require(len(source_map) == 6, "G5_SOURCE_BINDING_COUNT_INVALID", work_item)
        for relative, expected in sorted(source_map.items()):
            _require(isinstance(relative, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts, "G5_SOURCE_PATH_INVALID", relative)
            _require(isinstance(expected, str) and bool(_SHA.fullmatch(expected)), "G5_SOURCE_HASH_INVALID", relative)
            source = _inside(repository_root, repository_root / relative, "G5_SOURCE_PATH_INVALID")
            _require(source.is_file(), "G5_SOURCE_MISSING", relative)
            _require(_file_sha(source) == expected, "G5_SOURCE_HASH_MISMATCH", relative)
            source_checks.append(f"{work_item}:{relative}")
        summaries.append({"work_item": work_item, "path": str(path), "evidence_sha256": binding["evidence_sha256"], "source_bindings": len(source_map)})
    controls = {
        **{f"evidence:{item['work_item']}:bound": True for item in summaries},
        **{f"source:{item}": True for item in source_checks},
        **{f"gap:{gap}:missing": True for gap in fixture["known_gaps"]},
        "independent_approvals_are_zero": True,
        "g5_pass_claim_is_zero": True,
        "decision_is_hold": True,
        "workflow_is_in_progress": True,
        "formal_completion_is_false": True,
    }
    metrics = {
        "evidence_bindings_passed": len(summaries),
        "source_bindings_passed": len(source_checks),
        "acceptance_checks_passed": len(checks),
        "acceptance_checks_total": len(checks),
        "acceptance_checks_percent": 100.0,
        "known_missing_validation_count": len(fixture["known_gaps"]),
        "local_cancel_paths_passed": 4,
        "local_safe_state_paths_passed": 5,
        "human_presence_lift_claim_count": 0,
        "independent_approvals": 0,
        "g5_pass_claim_count": 0,
        "false_green_controls_passed": sum(controls.values()),
        "false_green_controls_total": len(controls),
        "false_green_rejection_percent": 100.0 * sum(controls.values()) / len(controls),
    }
    pack = {
        "schema_version": PACK_SCHEMA,
        "work_item": "P5-06",
        "readiness_status": "READY_FOR_INDEPENDENT_REVIEW",
        "decision": "HOLD",
        "workflow_status": "IN_PROGRESS",
        "completed": False,
        "independent_approvals": 0,
        "g5_pass_claim_count": 0,
        "metrics": metrics,
        "evidence_bindings": sorted(summaries, key=lambda item: item["work_item"]),
        "known_gaps": sorted(fixture["known_gaps"]),
        "false_green_controls": controls,
        "dependency_status": fixture["dependency_status"],
        "claims": {"g5_pass": "NONE", "formal_p5_06_completion": "NONE", "human_presence_lift_20_percent": "NONE", "godot_safe_idle_visual_qa": "NONE"},
        "next_task": "INDEPENDENT_PM_XR_SAFETY_REVIEW_REQUIRED",
    }
    pack["pack_sha256"] = _sha(pack)
    return pack


def validate_g5_review_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    pack = _mapping(value, "G5_REVIEW_PACK_OBJECT_REQUIRED", "pack")
    _exact(pack, {"schema_version", "work_item", "readiness_status", "decision", "workflow_status", "completed", "independent_approvals", "g5_pass_claim_count", "metrics", "evidence_bindings", "known_gaps", "false_green_controls", "dependency_status", "claims", "next_task", "pack_sha256"}, "G5_REVIEW_PACK_FIELDS_INVALID")
    supplied = pack["pack_sha256"]
    body = {key: copy.deepcopy(item) for key, item in pack.items() if key != "pack_sha256"}
    if supplied != _sha(body):
        raise G5ReviewPackError("G5_REVIEW_PACK_TAMPERED", "pack_sha256")
    _require(pack["schema_version"] == PACK_SCHEMA and pack["work_item"] == "P5-06", "G5_REVIEW_PACK_SCHEMA_MISMATCH", "schema/work item")
    _require(pack["readiness_status"] == "READY_FOR_INDEPENDENT_REVIEW", "G5_READINESS_OVERCLAIM", "readiness")
    _require(pack["decision"] == "HOLD", "G5_DECISION_MUST_HOLD", "decision")
    _require(pack["workflow_status"] == "IN_PROGRESS", "G5_WORKFLOW_OVERCLAIM", "workflow")
    _require(pack["completed"] is False, "G5_COMPLETION_OVERCLAIM", "completed")
    _require(pack["independent_approvals"] == 0, "G5_INDEPENDENT_APPROVAL_OVERCLAIM", "approvals")
    _require(pack["g5_pass_claim_count"] == 0 and pack["claims"]["g5_pass"] == "NONE", "G5_PASS_OVERCLAIM", "pass")
    _require(pack["claims"]["formal_p5_06_completion"] == "NONE", "G5_COMPLETION_OVERCLAIM", "completion")
    _require(pack["claims"]["human_presence_lift_20_percent"] == "NONE" and pack["claims"]["godot_safe_idle_visual_qa"] == "NONE", "G5_HUMAN_LIVE_OVERCLAIM", "human/live")
    _require(pack["dependency_status"] == {"p4_08": "NOT_APPROVED", "g4": "NOT_APPROVED", "g5": "NOT_APPROVED"}, "G5_DEPENDENCY_OVERCLAIM", "dependencies")
    _require(set(pack["known_gaps"]) == set(REQUIRED_GAPS), "G5_KNOWN_GAP_CATALOG_INVALID", "gaps")
    _require(all(pack["false_green_controls"].values()), "G5_FALSE_GREEN_CONTROL_FAILED", "controls")
    return copy.deepcopy(dict(pack))


__all__ = ["G5ReviewPackError", "evaluate_g5_review_fixture", "validate_g5_review_pack"]
