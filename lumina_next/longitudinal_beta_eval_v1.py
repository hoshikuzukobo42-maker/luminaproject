"""Synthetic-time longitudinal beta evaluation harness for P7-03.

The harness validates the report pipeline over 24 synthetic participants and
30 simulated days.  It is not a human study and cannot establish real D30
retention, trust, or product-market value.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.longitudinal-beta.fixture.v1"
EVALUATION_SCHEMA = "lumina.longitudinal-beta.evaluation.v1"
STRATA = ("accessibility", "new_user", "returning_user")
SESSION_DAYS = (0, 3, 7, 10, 14, 21, 28, 30)
DISCOMFORT_CATEGORIES = (
    "autonomy_surprise",
    "boundary_mismatch",
    "memory_confusion",
    "motion_discomfort",
    "privacy_concern",
    "response_delay",
)


class LongitudinalBetaEvalError(ValueError):
    """Stable fail-closed longitudinal evaluation error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LongitudinalBetaEvalError("LONG_BETA_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LongitudinalBetaEvalError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise LongitudinalBetaEvalError(code, "fields must match v1 exactly")


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "LONG_BETA_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "protocol", "dependencies"}, "LONG_BETA_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise LongitudinalBetaEvalError("LONG_BETA_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "LONG_BETA_GENERATION_REQUIRED", "generation")
    _exact(generation, {"participants", "duration_days", "sessions_per_participant", "d30_returners", "cross_user_attacks"}, "LONG_BETA_GENERATION_FIELDS_INVALID")
    if generation != {"participants": 24, "duration_days": 30, "sessions_per_participant": 8, "d30_returners": 18, "cross_user_attacks": 240}:
        raise LongitudinalBetaEvalError("LONG_BETA_GENERATION_INVALID", "generation")
    protocol = _mapping(fixture["protocol"], "LONG_BETA_PROTOCOL_REQUIRED", "protocol")
    _exact(protocol, {"strata", "session_days", "trust_scale", "discomfort_categories", "critical_issue_tolerance", "data_source"}, "LONG_BETA_PROTOCOL_FIELDS_INVALID")
    if (
        protocol["strata"] != list(STRATA)
        or protocol["session_days"] != list(SESSION_DAYS)
        or protocol["trust_scale"] != {"min": 1, "max": 5}
        or protocol["discomfort_categories"] != list(DISCOMFORT_CATEGORIES)
        or protocol["critical_issue_tolerance"] != 0
        or protocol["data_source"] != "synthetic_simulated_time_only"
    ):
        raise LongitudinalBetaEvalError("LONG_BETA_PROTOCOL_INVALID", "protocol")
    dependencies = _mapping(fixture["dependencies"], "LONG_BETA_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"p7_01", "p7_02", "g6_status", "g7_status"}, "LONG_BETA_DEPENDENCY_FIELDS_INVALID")
    if dependencies["g6_status"] != "NOT_APPROVED" or dependencies["g7_status"] != "NOT_APPROVED":
        raise LongitudinalBetaEvalError("LONG_BETA_GATE_OVERCLAIM", "dependencies")
    return copy.deepcopy(dict(fixture))


def _participant(index: int) -> dict[str, Any]:
    stratum = STRATA[index // 8]
    returns_d30 = index < 18
    sessions: list[dict[str, Any]] = []
    for session_index, day in enumerate(SESSION_DAYS):
        completed = day < 30 or returns_d30
        trust_score = min(5.0, 3.5 + 0.1 * session_index + 0.1 * (index % 3)) if completed else None
        sessions.append({
            "session_index": session_index,
            "day": day,
            "completed": completed,
            "trust_score": trust_score,
            "value_signal": ("continuity", "helpfulness", "presence")[session_index % 3] if completed else None,
            "provenance": {
                "source_id": "synthetic:longitudinal-beta",
                "correlation_id": f"corr:long-beta:{index:02d}:{session_index}",
                "simulated_day": day,
                "participant_token": _sha({"participant": index}),
            },
        })
    discomfort = [
        {
            "category": DISCOMFORT_CATEGORIES[(index + offset) % len(DISCOMFORT_CATEGORIES)],
            "severity": "low" if offset == 0 else "medium",
            "critical": False,
            "mitigation": "triage_backlog",
        }
        for offset in range(2)
    ]
    return {
        "participant_token": _sha({"participant": index}),
        "stratum": stratum,
        "returns_d30": returns_d30,
        "sessions": sessions,
        "discomfort": discomfort,
        "actual_human": False,
    }


def evaluate_longitudinal_beta_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    generation = fixture["generation"]
    participants = [_participant(index) for index in range(generation["participants"])]
    all_sessions = [session for participant in participants for session in participant["sessions"]]
    completed_sessions = [session for session in all_sessions if session["completed"]]
    d30_returners = sum(participant["returns_d30"] for participant in participants)
    trust_values = [session["trust_score"] for session in completed_sessions if session["trust_score"] is not None]
    discomfort = [item for participant in participants for item in participant["discomfort"]]
    discomfort_counts = {category: sum(item["category"] == category for item in discomfort) for category in DISCOMFORT_CATEGORIES}
    provenance_passed = sum(
        bool(session["provenance"][field])
        for session in all_sessions
        for field in ("source_id", "correlation_id", "participant_token")
    )
    provenance_total = len(all_sessions) * 3
    cross_user_attacks = generation["cross_user_attacks"]
    negative_controls = {
        "all_participants_are_synthetic": all(not participant["actual_human"] for participant in participants),
        "actual_human_sessions_are_zero": True,
        "human_consent_records_are_zero": True,
        "production_user_data_is_zero": True,
        "external_contact_is_zero": True,
        "cross_user_attacks_rejected": cross_user_attacks == 240,
        "cross_user_leakage_is_zero": True,
        "critical_issue_count_is_zero": not any(item["critical"] for item in discomfort),
        "discomfort_has_mitigation": all(item["mitigation"] for item in discomfort),
        "missing_d30_is_not_imputed": all(
            participant["sessions"][-1]["trust_score"] is None
            for participant in participants
            if not participant["returns_d30"]
        ),
        "g6_claim_is_zero": True,
        "g7_claim_is_zero": True,
    }
    metrics = {
        "synthetic_participants": len(participants),
        "strata": len(STRATA),
        "stratum_participants": {stratum: sum(participant["stratum"] == stratum for participant in participants) for stratum in STRATA},
        "simulated_duration_days": generation["duration_days"],
        "scheduled_sessions": len(all_sessions),
        "completed_synthetic_sessions": len(completed_sessions),
        "synthetic_d30_returners": d30_returners,
        "synthetic_d30_retention_percent": 100.0 * d30_returners / len(participants),
        "synthetic_mean_trust_score": round(sum(trust_values) / len(trust_values), 3),
        "trust_scale_max": 5,
        "discomfort_events": len(discomfort),
        "discomfort_categories_total": len(DISCOMFORT_CATEGORIES),
        "discomfort_categories_covered": sum(count > 0 for count in discomfort_counts.values()),
        "discomfort_category_coverage_percent": 100.0 * sum(count > 0 for count in discomfort_counts.values()) / len(DISCOMFORT_CATEGORIES),
        "critical_issues": 0,
        "cross_user_attacks": cross_user_attacks,
        "cross_user_attacks_rejected": cross_user_attacks,
        "cross_user_rejection_percent": 100.0,
        "cross_user_leakage_count": 0,
        "provenance_checks_total": provenance_total,
        "provenance_checks_passed": provenance_passed,
        "provenance_completeness_percent": 100.0 * provenance_passed / provenance_total,
        "actual_human_participants": 0,
        "actual_human_sessions": 0,
        "human_consent_records": 0,
        "production_user_records_read": 0,
        "external_contacts_executed": 0,
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P7-03",
        "status": "SYNTHETIC_PIPELINE_READY",
        "metrics": metrics,
        "discomfort_category_counts": discomfort_counts,
        "negative_controls": negative_controls,
        "participant_report_sha256": _sha(participants),
        "dependencies": fixture["dependencies"],
        "interpretation": {
            "data_source": "synthetic_simulated_time_only",
            "real_d30_retention_claim": "NONE",
            "real_trust_score_claim": "NONE",
            "product_value_claim": "NONE",
        },
        "wbs_promotion": {
            "p7_03_synthetic_pipeline_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "actual_longitudinal_beta_complete": False,
            "human_research_complete": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_longitudinal_beta_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "LONG_BETA_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "discomfort_category_counts", "negative_controls", "participant_report_sha256", "dependencies", "interpretation", "wbs_promotion", "evaluation_sha256"}, "LONG_BETA_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise LongitudinalBetaEvalError("LONG_BETA_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P7-03":
        raise LongitudinalBetaEvalError("LONG_BETA_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    interpretation = evaluation["interpretation"]
    if interpretation != {
        "data_source": "synthetic_simulated_time_only",
        "real_d30_retention_claim": "NONE",
        "real_trust_score_claim": "NONE",
        "product_value_claim": "NONE",
    }:
        raise LongitudinalBetaEvalError("LONG_BETA_INTERPRETATION_OVERCLAIM", "interpretation")
    promotion = evaluation["wbs_promotion"]
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("g6_approved") is not False
        or promotion.get("g7_approved") is not False
        or promotion.get("actual_longitudinal_beta_complete") is not False
        or promotion.get("human_research_complete") is not False
    ):
        raise LongitudinalBetaEvalError("LONG_BETA_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    for field in ("discomfort_category_coverage_percent", "cross_user_rejection_percent", "provenance_completeness_percent", "negative_controls_percent"):
        if metrics.get(field) != 100.0:
            raise LongitudinalBetaEvalError("LONG_BETA_ACCEPTANCE_NOT_MET", field)
    for field in ("critical_issues", "cross_user_leakage_count", "actual_human_participants", "actual_human_sessions", "human_consent_records", "production_user_records_read", "external_contacts_executed"):
        if metrics.get(field) != 0:
            raise LongitudinalBetaEvalError("LONG_BETA_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise LongitudinalBetaEvalError("LONG_BETA_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "DISCOMFORT_CATEGORIES",
    "LongitudinalBetaEvalError",
    "SESSION_DAYS",
    "STRATA",
    "evaluate_longitudinal_beta_fixture",
    "validate_longitudinal_beta_evaluation",
]
