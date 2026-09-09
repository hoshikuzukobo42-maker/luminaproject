"""Prepare a deterministic HOLD-only G8 review pack for P8-05.

The pack binds local P8-01..04 evidence and current source hashes. It cannot
sign a release, approve G8, or promote the formal WBS state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.g8-release-review.fixture.v1"
PACK_SCHEMA = "lumina.g8-release-review.pack.v1"
WORK_ITEMS = ("P8-01", "P8-02", "P8-03", "P8-04")
EVIDENCE_SCHEMAS = {
    "P8-01": "lumina.release-engineering.acceptance.v1",
    "P8-02": "lumina.slo-monitoring.acceptance.v1",
    "P8-03": "lumina.incident-playbook.acceptance.v1",
    "P8-04": "lumina.improvement-loop.acceptance.v1",
}
EVIDENCE_STATUSES = {
    "P8-01": "PASS_LOCAL_MECHANICAL",
    "P8-02": "PASS_LOCAL_MONITORING",
    "P8-03": "PASS_LOCAL_PLAYBOOK",
    "P8-04": "PASS_LOCAL_IMPROVEMENT_LOOP",
}
REQUIRED_GAPS = (
    "P8-01:PRODUCTION_SIGNING",
    "P8-01:FRESH_HOST_INSTALL",
    "P8-01:REAL_ROLLBACK_REHEARSAL",
    "P8-02:LIVE_DASHBOARD",
    "P8-02:PAGER_ROUTING",
    "P8-02:PRODUCTION_TELEMETRY_CALIBRATION",
    "P8-03:LIVE_INCIDENT_EXERCISE",
    "P8-03:CONTACT_TREE_VERIFICATION",
    "P8-03:PRODUCTION_ROLLBACK",
    "P8-04:PRODUCTION_USAGE_EVIDENCE",
    "P8-04:QUARTERLY_HUMAN_REVIEW",
    "P8-04:REAL_CANARY",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class G8ReviewPackError(ValueError):
    """Stable fail-closed review-pack error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise G8ReviewPackError("G8_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise G8ReviewPackError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise G8ReviewPackError(code, "fields must match v1 exactly")


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise G8ReviewPackError(code, detail)


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
    fixture = _mapping(value, "G8_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "pack_id", "prepared_at", "repository_root", "evidence_root", "evidence_bindings", "dependency_status", "review_state", "known_gaps", "expectations"}, "G8_FIXTURE_FIELDS_INVALID")
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "G8_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    _require(fixture["pack_id"] == "P8-05:G8-RELEASE-REVIEW", "G8_PACK_ID_INVALID", "pack_id")
    _require(isinstance(fixture["prepared_at"], str) and fixture["prepared_at"].endswith("Z"), "G8_PREPARED_AT_INVALID", "prepared_at")
    for field in ("repository_root", "evidence_root"):
        _require(isinstance(fixture[field], str) and Path(fixture[field]).is_absolute(), "G8_ROOT_PATH_INVALID", field)
    bindings = fixture["evidence_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == 4, "G8_EVIDENCE_CATALOG_INVALID", "four bindings")
    normalized_bindings: list[dict[str, Any]] = []
    for raw in bindings:
        binding = _mapping(raw, "G8_EVIDENCE_BINDING_REQUIRED", "binding")
        _exact(binding, {"work_item", "path", "evidence_sha256", "schema_version"}, "G8_EVIDENCE_BINDING_FIELDS_INVALID")
        work_item = binding["work_item"]
        _require(work_item in WORK_ITEMS, "G8_EVIDENCE_WORK_ITEM_UNKNOWN", str(work_item))
        _require(binding["schema_version"] == EVIDENCE_SCHEMAS[work_item], "G8_EVIDENCE_SCHEMA_BINDING_INVALID", work_item)
        _require(isinstance(binding["path"], str) and Path(binding["path"]).is_absolute(), "G8_EVIDENCE_PATH_INVALID", work_item)
        _require(isinstance(binding["evidence_sha256"], str) and bool(_SHA.fullmatch(binding["evidence_sha256"])), "G8_EVIDENCE_HASH_INVALID", work_item)
        normalized_bindings.append(copy.deepcopy(dict(binding)))
    _require({item["work_item"] for item in normalized_bindings} == set(WORK_ITEMS), "G8_EVIDENCE_CATALOG_INVALID", "unique P8-01..04")
    dependencies = _mapping(fixture["dependency_status"], "G8_DEPENDENCY_STATUS_REQUIRED", "dependency_status")
    _exact(dependencies, {"p8_01", "p8_02", "p8_03", "p8_04", "g8"}, "G8_DEPENDENCY_STATUS_FIELDS_INVALID")
    expected_dependencies = {
        "p8_01": "LOCAL_MECHANICAL_READY",
        "p8_02": "LOCAL_MONITORING_READY",
        "p8_03": "LOCAL_PLAYBOOK_READY",
        "p8_04": "LOCAL_IMPROVEMENT_LOOP_READY",
        "g8": "NOT_APPROVED",
    }
    _require(dict(dependencies) == expected_dependencies, "G8_DEPENDENCY_OVERCLAIM", "dependencies")
    review = _mapping(fixture["review_state"], "G8_REVIEW_STATE_REQUIRED", "review_state")
    _exact(review, {"preparer_id", "independent_reviewer_id", "independent_signatures", "independent_approvals", "g8_pass_claim_count", "readiness_target", "decision", "workflow_status", "completed"}, "G8_REVIEW_STATE_FIELDS_INVALID")
    _require(isinstance(review["preparer_id"], str) and bool(review["preparer_id"]), "G8_PREPARER_REQUIRED", "preparer_id")
    _require(review["independent_reviewer_id"] is None, "G8_INDEPENDENT_REVIEWER_OVERCLAIM", "reviewer")
    _require(review["independent_signatures"] == [], "G8_INDEPENDENT_SIGNATURE_OVERCLAIM", "signatures")
    _require(review["independent_approvals"] == 0, "G8_INDEPENDENT_APPROVAL_OVERCLAIM", "approvals")
    _require(review["g8_pass_claim_count"] == 0, "G8_PASS_OVERCLAIM", "pass claims")
    _require(review["readiness_target"] == "READY_FOR_INDEPENDENT_REVIEW", "G8_READINESS_TARGET_INVALID", "readiness")
    _require(review["decision"] == "HOLD", "G8_DECISION_MUST_HOLD", "decision")
    _require(review["workflow_status"] == "IN_PROGRESS", "G8_WORKFLOW_OVERCLAIM", "workflow")
    _require(review["completed"] is False, "G8_COMPLETION_OVERCLAIM", "completed")
    gaps = fixture["known_gaps"]
    _require(isinstance(gaps, list) and len(gaps) == len(REQUIRED_GAPS), "G8_KNOWN_GAP_CATALOG_INVALID", "known gaps")
    _require(set(gaps) == set(REQUIRED_GAPS) and len(gaps) == len(set(gaps)), "G8_KNOWN_GAP_CATALOG_INVALID", "exact gaps")
    expectations = _mapping(fixture["expectations"], "G8_EXPECTATIONS_REQUIRED", "expectations")
    _exact(expectations, {"evidence_count", "source_binding_count", "false_green_controls_min", "known_gap_count", "independent_approvals", "g8_pass_claim_count"}, "G8_EXPECTATIONS_FIELDS_INVALID")
    _require(expectations == {"evidence_count": 4, "source_binding_count": 24, "false_green_controls_min": 40, "known_gap_count": 12, "independent_approvals": 0, "g8_pass_claim_count": 0}, "G8_EXPECTATION_INVALID", "expectations")
    normalized = copy.deepcopy(dict(fixture))
    normalized["evidence_bindings"] = sorted(normalized_bindings, key=lambda item: item["work_item"])
    normalized["known_gaps"] = sorted(normalized["known_gaps"])
    return normalized


def _check_evidence(work_item: str, doc: Mapping[str, Any], checks: list[str]) -> None:
    def check(name: str, condition: bool) -> None:
        _require(condition, "G8_ACCEPTANCE_CONDITION_FAILED", f"{work_item}:{name}")
        checks.append(f"{work_item}:{name}")

    check("schema", doc.get("schema_version") == EVIDENCE_SCHEMAS[work_item])
    check("work_item", doc.get("work_item") == work_item)
    check("status", doc.get("status") == EVIDENCE_STATUSES[work_item])
    check("standard_python", doc.get("standard_python_only") is True)
    check("network_zero", doc.get("network_used") is False)
    check("dotnet_mono_zero", doc.get("dotnet_or_mono_used") is False)
    check("formal_claim_none", doc.get("formal_wbs_promotion_claim") == "NONE")
    check("g8_claim_none", doc.get("g8_claim") == "NONE")
    promotion = _mapping(doc.get("wbs_promotion"), "G8_PROMOTION_REQUIRED", work_item)
    check("formal_promotion_false", promotion.get("formal_wbs_promotion_allowed") is False)
    check("g8_approved_false", promotion.get("g8_approved") is False)
    metrics = _mapping(doc.get("metrics"), "G8_METRICS_REQUIRED", work_item)
    if work_item == "P8-01":
        check("deploy_trials", metrics.get("deploy_trials", 0) >= 200)
        check("deploy_success", metrics.get("deploy_success_percent") >= 99.0)
        check("rollback_success", metrics.get("rollback_success_percent") == 100.0)
        check("tamper_rejection", metrics.get("tamper_rejection_percent") == 100.0)
        check("external_zero", metrics.get("external_deployments") == 0)
    elif work_item == "P8-02":
        check("anomaly_trials", metrics.get("anomaly_trials", 0) >= 240)
        check("detection", metrics.get("anomaly_detection_percent") == 100.0)
        check("before_impact", metrics.get("before_impact_percent") == 100.0)
        check("mttd", 0 <= metrics.get("mttd_seconds", 999999) < 300)
        check("false_positive_zero", metrics.get("false_positive_count") == 0)
    elif work_item == "P8-03":
        check("incidents", metrics.get("synthetic_incident_trials", 0) >= 200)
        check("closure", metrics.get("closure_percent") == 100.0)
        check("owner", metrics.get("owner_coverage_percent") == 100.0)
        check("mttr", metrics.get("max_mttr_seconds", 999999) < 1800)
        check("external_zero", metrics.get("external_contacts_executed") == 0)
    else:
        check("change_records", metrics.get("change_records", 0) >= 120)
        check("kpi_link", metrics.get("kpi_link_percent") == 100.0)
        check("evidence_link", metrics.get("evidence_link_percent") == 100.0)
        check("rollback_link", metrics.get("rollback_link_percent") == 100.0)
        check("traceability", metrics.get("change_traceability_percent") == 100.0)


def evaluate_g8_review_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    repository_root = Path(fixture["repository_root"])
    evidence_root = Path(fixture["evidence_root"])
    _require(repository_root.is_dir(), "G8_REPOSITORY_ROOT_MISSING", str(repository_root))
    _require(evidence_root.is_dir(), "G8_EVIDENCE_ROOT_MISSING", str(evidence_root))
    checks: list[str] = []
    source_checks: list[str] = []
    binding_summary: list[dict[str, Any]] = []
    aggregate_external = 0
    for binding in fixture["evidence_bindings"]:
        work_item = binding["work_item"]
        path = _inside(evidence_root, Path(binding["path"]), "G8_EVIDENCE_PATH_OUTSIDE_ROOT")
        _require(path.is_file(), "G8_EVIDENCE_MISSING", str(path))
        _require(_file_sha(path) == binding["evidence_sha256"], "G8_EVIDENCE_HASH_MISMATCH", work_item)
        try:
            doc = _mapping(json.loads(path.read_text(encoding="utf-8")), "G8_EVIDENCE_OBJECT_REQUIRED", work_item)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise G8ReviewPackError("G8_EVIDENCE_JSON_INVALID", work_item) from exc
        _check_evidence(work_item, doc, checks)
        source_map = _mapping(doc.get("source_sha256"), "G8_SOURCE_BINDINGS_REQUIRED", work_item)
        _require(len(source_map) == 6, "G8_SOURCE_BINDING_COUNT_INVALID", work_item)
        for relative, expected in sorted(source_map.items()):
            _require(isinstance(relative, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts, "G8_SOURCE_PATH_INVALID", relative)
            _require(isinstance(expected, str) and bool(_SHA.fullmatch(expected)), "G8_SOURCE_HASH_INVALID", relative)
            source = _inside(repository_root, repository_root / relative, "G8_SOURCE_PATH_INVALID")
            _require(source.is_file(), "G8_SOURCE_MISSING", relative)
            _require(_file_sha(source) == expected, "G8_SOURCE_HASH_MISMATCH", relative)
            source_checks.append(f"{work_item}:{relative}")
        metrics = doc["metrics"]
        aggregate_external += int(metrics.get("external_deployments", 0)) + int(metrics.get("external_notifications", 0)) + int(metrics.get("external_contacts_executed", 0))
        binding_summary.append({"work_item": work_item, "path": str(path), "evidence_sha256": binding["evidence_sha256"], "source_bindings": len(source_map)})
    controls = {
        **{f"evidence:{item['work_item']}:bound": True for item in binding_summary},
        **{f"source:{item}": True for item in source_checks},
        **{f"gap:{gap}:missing": True for gap in fixture["known_gaps"]},
        "independent_approvals_are_zero": True,
        "g8_pass_claim_is_zero": True,
        "decision_is_hold": True,
        "workflow_is_in_progress": True,
        "formal_completion_is_false": True,
        "aggregate_external_effects_are_zero": aggregate_external == 0,
    }
    metrics = {
        "evidence_bindings_passed": len(binding_summary),
        "source_bindings_passed": len(source_checks),
        "acceptance_checks_passed": len(checks),
        "acceptance_checks_total": len(checks),
        "acceptance_checks_percent": 100.0,
        "known_missing_validation_count": len(fixture["known_gaps"]),
        "aggregate_external_effect_count": aggregate_external,
        "independent_approvals": 0,
        "g8_pass_claim_count": 0,
        "false_green_controls_passed": sum(controls.values()),
        "false_green_controls_total": len(controls),
        "false_green_rejection_percent": 100.0 * sum(controls.values()) / len(controls),
    }
    pack = {
        "schema_version": PACK_SCHEMA,
        "work_item": "P8-05",
        "readiness_status": "READY_FOR_INDEPENDENT_REVIEW",
        "decision": "HOLD",
        "workflow_status": "IN_PROGRESS",
        "completed": False,
        "independent_approvals": 0,
        "g8_pass_claim_count": 0,
        "metrics": metrics,
        "evidence_bindings": sorted(binding_summary, key=lambda item: item["work_item"]),
        "known_gaps": sorted(fixture["known_gaps"]),
        "false_green_controls": controls,
        "dependency_status": fixture["dependency_status"],
        "claims": {"g8_pass": "NONE", "formal_p8_05_completion": "NONE", "production_release": "NONE"},
        "next_task": "INDEPENDENT_EXEC_PM_SAFETY_REVIEW_REQUIRED",
    }
    pack["pack_sha256"] = _sha(pack)
    return pack


def validate_g8_review_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    pack = _mapping(value, "G8_REVIEW_PACK_OBJECT_REQUIRED", "pack")
    _exact(pack, {"schema_version", "work_item", "readiness_status", "decision", "workflow_status", "completed", "independent_approvals", "g8_pass_claim_count", "metrics", "evidence_bindings", "known_gaps", "false_green_controls", "dependency_status", "claims", "next_task", "pack_sha256"}, "G8_REVIEW_PACK_FIELDS_INVALID")
    supplied = pack["pack_sha256"]
    body = {key: copy.deepcopy(item) for key, item in pack.items() if key != "pack_sha256"}
    if supplied != _sha(body):
        raise G8ReviewPackError("G8_REVIEW_PACK_TAMPERED", "pack_sha256")
    _require(pack["schema_version"] == PACK_SCHEMA and pack["work_item"] == "P8-05", "G8_REVIEW_PACK_SCHEMA_MISMATCH", "schema/work item")
    _require(pack["readiness_status"] == "READY_FOR_INDEPENDENT_REVIEW", "G8_READINESS_OVERCLAIM", "readiness")
    _require(pack["decision"] == "HOLD", "G8_DECISION_MUST_HOLD", "decision")
    _require(pack["workflow_status"] == "IN_PROGRESS", "G8_WORKFLOW_OVERCLAIM", "workflow")
    _require(pack["completed"] is False, "G8_COMPLETION_OVERCLAIM", "completed")
    _require(pack["independent_approvals"] == 0, "G8_INDEPENDENT_APPROVAL_OVERCLAIM", "approvals")
    _require(pack["g8_pass_claim_count"] == 0 and pack["claims"]["g8_pass"] == "NONE", "G8_PASS_OVERCLAIM", "pass")
    _require(pack["claims"]["formal_p8_05_completion"] == "NONE" and pack["claims"]["production_release"] == "NONE", "G8_COMPLETION_OVERCLAIM", "claims")
    _require(pack["dependency_status"]["g8"] == "NOT_APPROVED", "G8_DEPENDENCY_OVERCLAIM", "g8")
    _require(set(pack["known_gaps"]) == set(REQUIRED_GAPS), "G8_KNOWN_GAP_CATALOG_INVALID", "gaps")
    _require(all(pack["false_green_controls"].values()), "G8_FALSE_GREEN_CONTROL_FAILED", "controls")
    return copy.deepcopy(dict(pack))


__all__ = ["G8ReviewPackError", "evaluate_g8_review_fixture", "validate_g8_review_pack"]
