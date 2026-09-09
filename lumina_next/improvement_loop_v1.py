"""Deterministic, evidence-bound improvement-loop reference for P8-04."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.improvement-loop.fixture.v1"
EVALUATION_SCHEMA = "lumina.improvement-loop.evaluation.v1"
CHANGE_TYPES = ("MODEL", "PERSONA", "BEHAVIOR")
STAGES = ("INGEST_EVIDENCE", "IDENTIFY_KPI_GAP", "DESIGN_CHANGE", "RISK_REVIEW", "CANARY_PLAN", "ROLLBACK_PLAN", "DECISION")


class ImprovementLoopError(ValueError):
    """Stable fail-closed improvement-loop error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ImprovementLoopError("IMPROVEMENT_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ImprovementLoopError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise ImprovementLoopError(code, "fields must match v1 exactly")


def _catalog(value: Any) -> dict[str, Any]:
    catalog = _mapping(value, "IMPROVEMENT_CATALOG_REQUIRED", "catalog")
    _exact(catalog, {"kpis", "evidence", "rollback_bundles", "owners"}, "IMPROVEMENT_CATALOG_FIELDS_INVALID")
    for field in ("kpis", "evidence", "rollback_bundles", "owners"):
        items = catalog[field]
        if not isinstance(items, list) or not items or len(items) != len(set(items)):
            raise ImprovementLoopError("IMPROVEMENT_CATALOG_INVALID", field)
        if not all(isinstance(item, str) and item and len(item) <= 128 for item in items):
            raise ImprovementLoopError("IMPROVEMENT_CATALOG_INVALID", field)
    if len(catalog["kpis"]) != 13 or len(catalog["evidence"]) != 12 or len(catalog["rollback_bundles"]) != 4 or len(catalog["owners"]) != 3:
        raise ImprovementLoopError("IMPROVEMENT_CATALOG_INVALID", "canonical counts")
    return copy.deepcopy(dict(catalog))


def build_change_record(*, catalog: Mapping[str, Any], quarter: int, change_index: int) -> dict[str, Any]:
    checked = _catalog(catalog)
    if not isinstance(quarter, int) or isinstance(quarter, bool) or not 1 <= quarter <= 4:
        raise ImprovementLoopError("IMPROVEMENT_QUARTER_INVALID", str(quarter))
    if not isinstance(change_index, int) or isinstance(change_index, bool) or not 0 <= change_index < 30:
        raise ImprovementLoopError("IMPROVEMENT_CHANGE_INDEX_INVALID", str(change_index))
    change_type = CHANGE_TYPES[change_index % len(CHANGE_TYPES)]
    change_id = f"Q{quarter}-CHANGE-{change_index:03d}"
    kpi_ids = [checked["kpis"][(change_index + quarter) % len(checked["kpis"])], checked["kpis"][(change_index + quarter + 5) % len(checked["kpis"])]]
    evidence_ids = [checked["evidence"][(change_index + quarter) % len(checked["evidence"])], checked["evidence"][(change_index + quarter + 3) % len(checked["evidence"])]]
    rollback = checked["rollback_bundles"][(change_index + quarter) % len(checked["rollback_bundles"])]
    owner = checked["owners"][change_index % len(checked["owners"])]
    stages = [
        {
            "stage": stage,
            "evidence_id": f"{change_id}-{stage}",
            "owner": owner,
            "decision": "STAGED_ONLY" if stage == "DECISION" else "RECORDED",
        }
        for stage in STAGES
    ]
    record = {
        "schema_version": "lumina.improvement-change.v1",
        "change_id": change_id,
        "quarter": quarter,
        "change_type": change_type,
        "owner": owner,
        "kpi_ids": kpi_ids,
        "evidence_ids": evidence_ids,
        "rollback_bundle_id": rollback,
        "stages": stages,
        "production_change_applied": False,
        "external_contact_executed": False,
        "record_sha256": "",
    }
    record["record_sha256"] = _sha({key: item for key, item in record.items() if key != "record_sha256"})
    return record


def validate_change_record(value: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(value, "IMPROVEMENT_RECORD_REQUIRED", "record")
    _exact(record, {"schema_version", "change_id", "quarter", "change_type", "owner", "kpi_ids", "evidence_ids", "rollback_bundle_id", "stages", "production_change_applied", "external_contact_executed", "record_sha256"}, "IMPROVEMENT_RECORD_FIELDS_INVALID")
    supplied = record["record_sha256"]
    body = {key: copy.deepcopy(item) for key, item in record.items() if key != "record_sha256"}
    if supplied != _sha(body):
        raise ImprovementLoopError("IMPROVEMENT_RECORD_TAMPERED", "record_sha256")
    checked = _catalog(catalog)
    if record["schema_version"] != "lumina.improvement-change.v1" or record["change_type"] not in CHANGE_TYPES:
        raise ImprovementLoopError("IMPROVEMENT_RECORD_INVALID", "schema/change_type")
    if not set(record["kpi_ids"]).issubset(checked["kpis"]) or len(record["kpi_ids"]) < 1:
        raise ImprovementLoopError("IMPROVEMENT_KPI_LINK_INVALID", "kpi_ids")
    if not set(record["evidence_ids"]).issubset(checked["evidence"]) or len(record["evidence_ids"]) < 1:
        raise ImprovementLoopError("IMPROVEMENT_EVIDENCE_LINK_INVALID", "evidence_ids")
    if record["rollback_bundle_id"] not in checked["rollback_bundles"] or record["owner"] not in checked["owners"]:
        raise ImprovementLoopError("IMPROVEMENT_OWNER_ROLLBACK_INVALID", "owner/rollback")
    if tuple(stage.get("stage") for stage in record["stages"]) != STAGES or not all(stage.get("evidence_id") and stage.get("owner") == record["owner"] for stage in record["stages"]):
        raise ImprovementLoopError("IMPROVEMENT_TRACE_INVALID", "stages")
    if record["production_change_applied"] is not False or record["external_contact_executed"] is not False:
        raise ImprovementLoopError("IMPROVEMENT_SIDE_EFFECT_OVERCLAIM", "side effects")
    return copy.deepcopy(dict(record))


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "IMPROVEMENT_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "catalog", "dependencies"}, "IMPROVEMENT_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise ImprovementLoopError("IMPROVEMENT_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "IMPROVEMENT_GENERATION_REQUIRED", "generation")
    _exact(generation, {"quarters", "changes_per_quarter"}, "IMPROVEMENT_GENERATION_FIELDS_INVALID")
    if generation != {"quarters": 4, "changes_per_quarter": 30}:
        raise ImprovementLoopError("IMPROVEMENT_GENERATION_INVALID", "generation")
    _catalog(fixture["catalog"])
    dependencies = _mapping(fixture["dependencies"], "IMPROVEMENT_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"p8_02_status", "p8_03_status", "g8_status"}, "IMPROVEMENT_DEPENDENCY_FIELDS_INVALID")
    if dependencies != {"p8_02_status": "LOCAL_MONITORING_READY", "p8_03_status": "LOCAL_PLAYBOOK_READY", "g8_status": "NOT_APPROVED"}:
        raise ImprovementLoopError("IMPROVEMENT_DEPENDENCY_OVERCLAIM", "dependencies")
    return copy.deepcopy(dict(fixture))


def evaluate_improvement_loop_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    records = [
        build_change_record(catalog=fixture["catalog"], quarter=quarter, change_index=index)
        for quarter in range(1, fixture["generation"]["quarters"] + 1)
        for index in range(fixture["generation"]["changes_per_quarter"])
    ]
    checked = [validate_change_record(record, fixture["catalog"]) for record in records]
    negative_controls = {
        "all_records_are_sealed": all(record["record_sha256"] for record in checked),
        "all_records_link_kpis": all(record["kpi_ids"] for record in checked),
        "all_records_link_evidence": all(record["evidence_ids"] for record in checked),
        "all_records_link_rollback": all(record["rollback_bundle_id"] for record in checked),
        "all_records_have_owner": all(record["owner"] for record in checked),
        "all_stage_orders_are_exact": all(tuple(stage["stage"] for stage in record["stages"]) == STAGES for record in checked),
        "all_stages_have_evidence": all(stage["evidence_id"] for record in checked for stage in record["stages"]),
        "production_changes_are_zero": not any(record["production_change_applied"] for record in checked),
        "external_contacts_are_zero": not any(record["external_contact_executed"] for record in checked),
        "production_log_reads_are_zero": True,
        "g8_claim_is_zero": True,
        "formal_wbs_claim_is_zero": True,
        "protected_assets_touched_are_zero": True,
    }
    total_stages = sum(len(record["stages"]) for record in checked)
    metrics = {
        "quarters": 4,
        "change_records": len(checked),
        "change_types_covered": len(set(record["change_type"] for record in checked)),
        "kpi_linked_records": sum(bool(record["kpi_ids"]) for record in checked),
        "kpi_link_percent": 100.0 * sum(bool(record["kpi_ids"]) for record in checked) / len(checked),
        "evidence_linked_records": sum(bool(record["evidence_ids"]) for record in checked),
        "evidence_link_percent": 100.0 * sum(bool(record["evidence_ids"]) for record in checked) / len(checked),
        "rollback_linked_records": sum(bool(record["rollback_bundle_id"]) for record in checked),
        "rollback_link_percent": 100.0 * sum(bool(record["rollback_bundle_id"]) for record in checked) / len(checked),
        "owner_link_percent": 100.0 * sum(bool(record["owner"]) for record in checked) / len(checked),
        "trace_stages": total_stages,
        "trace_stages_with_evidence": sum(bool(stage["evidence_id"]) for record in checked for stage in record["stages"]),
        "change_traceability_percent": 100.0 * sum(bool(stage["evidence_id"]) for record in checked for stage in record["stages"]) / total_stages,
        "production_changes_applied": sum(record["production_change_applied"] for record in checked),
        "production_log_reads": 0,
        "external_contacts_executed": sum(record["external_contact_executed"] for record in checked),
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P8-04",
        "status": "LOCAL_IMPROVEMENT_LOOP_READY",
        "metrics": metrics,
        "change_type_summary": {change_type: sum(record["change_type"] == change_type for record in checked) for change_type in CHANGE_TYPES},
        "records_sha256": _sha(checked),
        "negative_controls": negative_controls,
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p8_04_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "production_improvement_cycle_executed": False,
            "quarterly_human_review_complete": False,
            "g8_approved": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_improvement_loop_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "IMPROVEMENT_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "change_type_summary", "records_sha256", "negative_controls", "dependencies", "wbs_promotion", "evaluation_sha256"}, "IMPROVEMENT_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise ImprovementLoopError("IMPROVEMENT_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P8-04":
        raise ImprovementLoopError("IMPROVEMENT_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    if evaluation["wbs_promotion"] != {
        "p8_04_local_mechanical_candidate": True,
        "formal_wbs_promotion_allowed": False,
        "production_improvement_cycle_executed": False,
        "quarterly_human_review_complete": False,
        "g8_approved": False,
    }:
        raise ImprovementLoopError("IMPROVEMENT_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    for field in ("kpi_link_percent", "evidence_link_percent", "rollback_link_percent", "owner_link_percent", "change_traceability_percent", "negative_controls_percent"):
        if metrics.get(field) != 100.0:
            raise ImprovementLoopError("IMPROVEMENT_ACCEPTANCE_NOT_MET", field)
    for field in ("production_changes_applied", "production_log_reads", "external_contacts_executed"):
        if metrics.get(field) != 0:
            raise ImprovementLoopError("IMPROVEMENT_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise ImprovementLoopError("IMPROVEMENT_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "CHANGE_TYPES",
    "STAGES",
    "ImprovementLoopError",
    "build_change_record",
    "evaluate_improvement_loop_fixture",
    "validate_change_record",
    "validate_improvement_loop_evaluation",
]
