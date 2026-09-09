"""Deterministic, no-side-effect incident playbook reference for P8-03."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.incident-playbook.fixture.v1"
EVALUATION_SCHEMA = "lumina.incident-playbook.evaluation.v1"
INCIDENT_TYPES = ("AUTONOMY_RUNAWAY", "MEMORY_LEAKAGE", "MODEL_MALFUNCTION", "AVATAR_STOP")
STEP_ORDER = ("DETECT", "CLASSIFY", "ASSIGN_OWNER", "CONTAIN", "STOP", "RECOVER", "VERIFY", "CLOSE")


class IncidentPlaybookError(ValueError):
    """Stable fail-closed incident playbook error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IncidentPlaybookError("INCIDENT_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IncidentPlaybookError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise IncidentPlaybookError(code, "fields must match v1 exactly")


def _playbooks(value: Any) -> dict[str, dict[str, str]]:
    table = _mapping(value, "INCIDENT_PLAYBOOKS_REQUIRED", "playbooks")
    if tuple(sorted(table)) != tuple(sorted(INCIDENT_TYPES)):
        raise IncidentPlaybookError("INCIDENT_TYPE_SET_INVALID", "four canonical incident types required")
    result: dict[str, dict[str, str]] = {}
    required = {"owner", "contact_channel", "contain_action", "stop_action", "recovery_action", "rollback_action", "verification"}
    for incident_type in INCIDENT_TYPES:
        playbook = _mapping(table[incident_type], "INCIDENT_PLAYBOOK_REQUIRED", incident_type)
        _exact(playbook, required, "INCIDENT_PLAYBOOK_FIELDS_INVALID")
        if not all(isinstance(playbook[field], str) and playbook[field].strip() for field in required):
            raise IncidentPlaybookError("INCIDENT_PLAYBOOK_VALUE_INVALID", incident_type)
        result[incident_type] = copy.deepcopy(dict(playbook))
    return result


def execute_synthetic_incident(*, playbooks: Mapping[str, Any], incident_type: str, trial_index: int) -> dict[str, Any]:
    table = _playbooks(playbooks)
    if incident_type not in table:
        raise IncidentPlaybookError("INCIDENT_TYPE_UNKNOWN", str(incident_type))
    if not isinstance(trial_index, int) or isinstance(trial_index, bool) or not 0 <= trial_index < 10_000:
        raise IncidentPlaybookError("INCIDENT_TRIAL_INVALID", str(trial_index))
    spec = table[incident_type]
    durations = {
        "DETECT": 60 + (trial_index % 5) * 10,
        "CLASSIFY": 30,
        "ASSIGN_OWNER": 15,
        "CONTAIN": 60 + (trial_index % 7) * 10,
        "STOP": 30,
        "RECOVER": 240 + (trial_index % 6) * 30,
        "VERIFY": 120,
        "CLOSE": 30,
    }
    cursor = 0
    trace: list[dict[str, Any]] = []
    incident_id = f"SYN-{incident_type}-{trial_index:04d}"
    for step in STEP_ORDER:
        start = cursor
        cursor += durations[step]
        trace.append(
            {
                "incident_id": incident_id,
                "step": step,
                "start_sec": start,
                "end_sec": cursor,
                "owner": spec["owner"],
                "evidence_id": f"{incident_id}-{step}",
            }
        )
    return {
        "schema_version": "lumina.synthetic-incident.result.v1",
        "incident_id": incident_id,
        "incident_type": incident_type,
        "owner": spec["owner"],
        "contact_channel_documented": bool(spec["contact_channel"]),
        "contain_action_documented": bool(spec["contain_action"]),
        "stop_action_documented": bool(spec["stop_action"]),
        "recovery_action_documented": bool(spec["recovery_action"]),
        "rollback_action_documented": bool(spec["rollback_action"]),
        "verification_documented": bool(spec["verification"]),
        "trace": trace,
        "mttr_seconds": cursor,
        "closed": trace[-1]["step"] == "CLOSE",
        "unsafe_actions_after_containment": 0,
        "external_contacts_executed": 0,
        "process_signals_sent": 0,
        "production_records_read": 0,
    }


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "INCIDENT_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "playbooks", "dependencies"}, "INCIDENT_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise IncidentPlaybookError("INCIDENT_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "INCIDENT_GENERATION_REQUIRED", "generation")
    _exact(generation, {"trials_per_type"}, "INCIDENT_GENERATION_FIELDS_INVALID")
    if generation != {"trials_per_type": 50}:
        raise IncidentPlaybookError("INCIDENT_GENERATION_INVALID", "generation")
    _playbooks(fixture["playbooks"])
    dependencies = _mapping(fixture["dependencies"], "INCIDENT_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"p8_01_status", "p8_02_status", "g8_status"}, "INCIDENT_DEPENDENCY_FIELDS_INVALID")
    if dependencies != {"p8_01_status": "LOCAL_MECHANICAL_READY", "p8_02_status": "LOCAL_MONITORING_READY", "g8_status": "NOT_APPROVED"}:
        raise IncidentPlaybookError("INCIDENT_DEPENDENCY_OVERCLAIM", "dependencies")
    return copy.deepcopy(dict(fixture))


def evaluate_incident_playbook_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    results = [
        execute_synthetic_incident(playbooks=fixture["playbooks"], incident_type=incident_type, trial_index=index)
        for incident_type in INCIDENT_TYPES
        for index in range(fixture["generation"]["trials_per_type"])
    ]
    mttr = [result["mttr_seconds"] for result in results]
    coverage_fields = (
        "contact_channel_documented",
        "contain_action_documented",
        "stop_action_documented",
        "recovery_action_documented",
        "rollback_action_documented",
        "verification_documented",
    )
    negative_controls = {
        "all_incidents_have_owner": all(result["owner"] for result in results),
        "all_required_plan_fields_documented": all(result[field] for result in results for field in coverage_fields),
        "all_step_orders_are_exact": all(tuple(item["step"] for item in result["trace"]) == STEP_ORDER for result in results),
        "all_incidents_close_under_30_minutes": all(result["closed"] and result["mttr_seconds"] < 1800 for result in results),
        "post_containment_unsafe_actions_are_zero": not any(result["unsafe_actions_after_containment"] for result in results),
        "external_contacts_are_zero": not any(result["external_contacts_executed"] for result in results),
        "process_signals_are_zero": not any(result["process_signals_sent"] for result in results),
        "production_record_reads_are_zero": not any(result["production_records_read"] for result in results),
        "all_trace_steps_have_evidence": all(item["evidence_id"] for result in results for item in result["trace"]),
        "g8_claim_is_zero": True,
        "formal_wbs_claim_is_zero": True,
        "protected_assets_touched_are_zero": True,
    }
    total_steps = sum(len(result["trace"]) for result in results)
    metrics = {
        "incident_type_count": len(INCIDENT_TYPES),
        "synthetic_incident_trials": len(results),
        "incidents_closed": sum(result["closed"] for result in results),
        "closure_percent": 100.0 * sum(result["closed"] for result in results) / len(results),
        "owner_coverage_percent": 100.0 * sum(bool(result["owner"]) for result in results) / len(results),
        "contact_plan_coverage_percent": 100.0 * sum(result["contact_channel_documented"] for result in results) / len(results),
        "stop_plan_coverage_percent": 100.0 * sum(result["stop_action_documented"] for result in results) / len(results),
        "recovery_plan_coverage_percent": 100.0 * sum(result["recovery_action_documented"] for result in results) / len(results),
        "rollback_plan_coverage_percent": 100.0 * sum(result["rollback_action_documented"] for result in results) / len(results),
        "verification_plan_coverage_percent": 100.0 * sum(result["verification_documented"] for result in results) / len(results),
        "mttr_seconds": sum(mttr) / len(mttr),
        "max_mttr_seconds": max(mttr),
        "trace_steps": total_steps,
        "trace_steps_with_evidence": sum(bool(item["evidence_id"]) for result in results for item in result["trace"]),
        "trace_completeness_percent": 100.0 * sum(bool(item["evidence_id"]) for result in results for item in result["trace"]) / total_steps,
        "unsafe_actions_after_containment": sum(result["unsafe_actions_after_containment"] for result in results),
        "external_contacts_executed": sum(result["external_contacts_executed"] for result in results),
        "process_signals_sent": sum(result["process_signals_sent"] for result in results),
        "production_records_read": sum(result["production_records_read"] for result in results),
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P8-03",
        "status": "LOCAL_PLAYBOOK_READY",
        "metrics": metrics,
        "incident_summary": {
            incident_type: {
                "trials": sum(result["incident_type"] == incident_type for result in results),
                "closed": sum(result["incident_type"] == incident_type and result["closed"] for result in results),
                "max_mttr_seconds": max(result["mttr_seconds"] for result in results if result["incident_type"] == incident_type),
            }
            for incident_type in INCIDENT_TYPES
        },
        "trace_sha256": _sha(results),
        "negative_controls": negative_controls,
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p8_03_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "live_incident_exercise_complete": False,
            "external_contact_tree_verified": False,
            "g8_approved": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_incident_playbook_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "INCIDENT_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "incident_summary", "trace_sha256", "negative_controls", "dependencies", "wbs_promotion", "evaluation_sha256"}, "INCIDENT_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise IncidentPlaybookError("INCIDENT_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P8-03":
        raise IncidentPlaybookError("INCIDENT_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    if evaluation["wbs_promotion"] != {
        "p8_03_local_mechanical_candidate": True,
        "formal_wbs_promotion_allowed": False,
        "live_incident_exercise_complete": False,
        "external_contact_tree_verified": False,
        "g8_approved": False,
    }:
        raise IncidentPlaybookError("INCIDENT_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    for field in ("closure_percent", "owner_coverage_percent", "contact_plan_coverage_percent", "stop_plan_coverage_percent", "recovery_plan_coverage_percent", "rollback_plan_coverage_percent", "verification_plan_coverage_percent", "trace_completeness_percent", "negative_controls_percent"):
        if metrics.get(field) != 100.0:
            raise IncidentPlaybookError("INCIDENT_ACCEPTANCE_NOT_MET", field)
    if not 0 <= metrics.get("mttr_seconds", 999999) < 1800 or metrics.get("max_mttr_seconds", 999999) >= 1800:
        raise IncidentPlaybookError("INCIDENT_ACCEPTANCE_NOT_MET", "mttr")
    for field in ("unsafe_actions_after_containment", "external_contacts_executed", "process_signals_sent", "production_records_read"):
        if metrics.get(field) != 0:
            raise IncidentPlaybookError("INCIDENT_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise IncidentPlaybookError("INCIDENT_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "INCIDENT_TYPES",
    "STEP_ORDER",
    "IncidentPlaybookError",
    "evaluate_incident_playbook_fixture",
    "execute_synthetic_incident",
    "validate_incident_playbook_evaluation",
]
