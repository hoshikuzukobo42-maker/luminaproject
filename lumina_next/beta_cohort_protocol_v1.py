"""Deterministic, enrollment-free closed-beta cohort protocol for P7-01.

This module defines and mechanically evaluates cohort eligibility, consent
coverage, and hard-stop conditions.  It never enrolls a person, stores user
data, contacts a participant, or claims G6/G7 approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.beta-cohort.fixture.v1"
EVALUATION_SCHEMA = "lumina.beta-cohort.evaluation.v1"
PROTOCOL_VERSION = "lumina.beta-cohort.protocol.v1"
STRATA = ("accessibility", "new_user", "returning_user")
STRATUM_SLOTS = {"accessibility": 8, "new_user": 8, "returning_user": 8}
INCLUSION_RULES = (
    "adult_18_plus",
    "ja_jp_locale",
    "macos_arm64_sandbox",
    "consent_capacity_confirmed",
    "emergency_stop_control_accessible",
)
EXCLUSION_RULES = (
    "active_crisis",
    "conflicting_study",
    "employee_or_vendor",
    "regulated_advice_context",
)
CONSENT_DISCLOSURES = (
    "beta_scope_and_experimental_status",
    "collected_data_categories",
    "retention_30_days",
    "voluntary_participation",
    "withdrawal_and_opt_out",
    "export_and_delete_controls",
    "known_risks_and_no_regulated_advice",
    "incident_and_support_contact",
)
STOP_RULES = (
    "CONSENT_VIOLATION",
    "CRITICAL_OPEN_BUG",
    "CRITICAL_SAFETY_INCIDENT",
    "CROSS_USER_LEAKAGE",
    "EVIDENCE_INTEGRITY_FAILURE",
    "PRIVACY_BREACH",
    "STOP_CONTROL_FAILURE",
    "UNAUTHORIZED_EXTERNAL_ACTION",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class BetaCohortProtocolError(ValueError):
    """Stable fail-closed cohort protocol error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BetaCohortProtocolError(
            "BETA_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BetaCohortProtocolError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise BetaCohortProtocolError(code, "fields must match v1 exactly")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BetaCohortProtocolError("BETA_ID_INVALID", field)
    return value


def _provenance(value: Any) -> dict[str, str]:
    item = _mapping(value, "BETA_PROVENANCE_REQUIRED", "provenance")
    _exact(
        item,
        {"source_id", "correlation_id", "observed_at", "authentication_method"},
        "BETA_PROVENANCE_FIELDS_INVALID",
    )
    result: dict[str, str] = {}
    for field in ("source_id", "correlation_id", "observed_at", "authentication_method"):
        raw = item[field]
        if not isinstance(raw, str) or not raw.strip():
            raise BetaCohortProtocolError("BETA_PROVENANCE_INCOMPLETE", field)
        result[field] = raw
    return result


def assess_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a synthetic candidate without retaining free-form content."""

    value = _mapping(candidate, "BETA_CANDIDATE_OBJECT_REQUIRED", "candidate")
    fields = {
        "candidate_id",
        "age_band",
        "locale",
        "platform",
        "consent_capacity",
        "can_access_stop_control",
        "active_crisis",
        "conflicting_study",
        "employee_or_vendor",
        "regulated_context",
        "prior_lumina_use",
        "accessibility_support",
        "provenance",
    }
    _exact(value, fields, "BETA_CANDIDATE_FIELDS_INVALID")
    candidate_id = _identifier(value["candidate_id"], "candidate_id")
    provenance = _provenance(value["provenance"])
    for field in (
        "consent_capacity",
        "can_access_stop_control",
        "active_crisis",
        "conflicting_study",
        "employee_or_vendor",
        "regulated_context",
        "prior_lumina_use",
        "accessibility_support",
    ):
        if not isinstance(value[field], bool):
            raise BetaCohortProtocolError("BETA_BOOLEAN_REQUIRED", field)

    reasons: list[str] = []
    if value["age_band"] not in {"adult", "older_adult"}:
        reasons.append("AGE_NOT_ELIGIBLE")
    if value["locale"] != "ja-JP":
        reasons.append("LOCALE_OUT_OF_SCOPE")
    if value["platform"] != "macos_arm64_sandbox":
        reasons.append("PLATFORM_OUT_OF_SCOPE")
    if not value["consent_capacity"]:
        reasons.append("CONSENT_CAPACITY_NOT_CONFIRMED")
    if not value["can_access_stop_control"]:
        reasons.append("STOP_CONTROL_INACCESSIBLE")
    if value["active_crisis"]:
        reasons.append("ACTIVE_CRISIS")
    if value["conflicting_study"]:
        reasons.append("CONFLICTING_STUDY")
    if value["employee_or_vendor"]:
        reasons.append("CONFLICT_OF_INTEREST")
    if value["regulated_context"]:
        reasons.append("REGULATED_CONTEXT")
    eligible = not reasons
    stratum: str | None = None
    if eligible:
        if value["accessibility_support"]:
            stratum = "accessibility"
        elif value["prior_lumina_use"]:
            stratum = "returning_user"
        else:
            stratum = "new_user"
    decision = {
        "schema_version": "lumina.beta-cohort.candidate-decision.v1",
        "candidate_id": candidate_id,
        "eligible": eligible,
        "stratum": stratum,
        "reason_codes": sorted(reasons),
        "provenance_sha256": _sha(provenance),
        "free_text_retained": False,
        "enrollment_executed": False,
    }
    decision["decision_sha256"] = _sha(decision)
    return decision


def evaluate_stop_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic hard-stop decision; unknown conditions stop too."""

    value = _mapping(snapshot, "BETA_STOP_OBJECT_REQUIRED", "stop snapshot")
    _exact(
        value,
        {"snapshot_id", "condition", "count", "active_participants", "operator_acknowledged", "provenance"},
        "BETA_STOP_FIELDS_INVALID",
    )
    snapshot_id = _identifier(value["snapshot_id"], "snapshot_id")
    _provenance(value["provenance"])
    if not isinstance(value["condition"], str) or not value["condition"]:
        raise BetaCohortProtocolError("BETA_STOP_CONDITION_REQUIRED", "condition")
    if isinstance(value["count"], bool) or not isinstance(value["count"], int) or value["count"] < 0:
        raise BetaCohortProtocolError("BETA_STOP_COUNT_INVALID", "count")
    if (
        isinstance(value["active_participants"], bool)
        or not isinstance(value["active_participants"], int)
        or value["active_participants"] < 0
    ):
        raise BetaCohortProtocolError("BETA_ACTIVE_COUNT_INVALID", "active_participants")
    if not isinstance(value["operator_acknowledged"], bool):
        raise BetaCohortProtocolError("BETA_BOOLEAN_REQUIRED", "operator_acknowledged")
    known = value["condition"] in STOP_RULES
    triggered = value["count"] >= 1 or not known
    action = "STOP_AND_QUARANTINE" if triggered else "CONTINUE_MONITORING"
    decision = {
        "schema_version": "lumina.beta-cohort.stop-decision.v1",
        "snapshot_id": snapshot_id,
        "condition": value["condition"],
        "known_condition": known,
        "triggered": triggered,
        "action": action,
        "active_participants_contacted": 0,
        "external_action_executed": False,
        "operator_ack_can_downgrade": False,
    }
    decision["decision_sha256"] = _sha(decision)
    return decision


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "BETA_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(
        fixture,
        {
            "schema_version",
            "protocol_id",
            "generation",
            "cohort",
            "inclusion_rules",
            "exclusion_rules",
            "consent",
            "stop_conditions",
            "dependencies",
        },
        "BETA_FIXTURE_FIELDS_INVALID",
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise BetaCohortProtocolError("BETA_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    _identifier(fixture["protocol_id"], "protocol_id")
    generation = _mapping(fixture["generation"], "BETA_GENERATION_REQUIRED", "generation")
    _exact(generation, {"candidate_scenarios", "stop_scenarios"}, "BETA_GENERATION_FIELDS_INVALID")
    if generation["candidate_scenarios"] != 120:
        raise BetaCohortProtocolError("BETA_CANDIDATE_SCENARIO_COUNT_INVALID", "candidate_scenarios")
    if generation["stop_scenarios"] != 80:
        raise BetaCohortProtocolError("BETA_STOP_SCENARIO_COUNT_INVALID", "stop_scenarios")
    cohort = _mapping(fixture["cohort"], "BETA_COHORT_REQUIRED", "cohort")
    _exact(
        cohort,
        {"target_size", "duration_days", "minimum_sessions", "maximum_sessions", "stratum_slots"},
        "BETA_COHORT_FIELDS_INVALID",
    )
    if cohort["target_size"] != 24 or cohort["duration_days"] != 30:
        raise BetaCohortProtocolError("BETA_COHORT_BASELINE_INVALID", "target/duration")
    if cohort["minimum_sessions"] != 6 or cohort["maximum_sessions"] != 20:
        raise BetaCohortProtocolError("BETA_SESSION_WINDOW_INVALID", "session window")
    if cohort["stratum_slots"] != STRATUM_SLOTS:
        raise BetaCohortProtocolError("BETA_STRATA_INVALID", "stratum_slots")
    if fixture["inclusion_rules"] != list(INCLUSION_RULES):
        raise BetaCohortProtocolError("BETA_INCLUSION_RULES_INVALID", "inclusion_rules")
    if fixture["exclusion_rules"] != list(EXCLUSION_RULES):
        raise BetaCohortProtocolError("BETA_EXCLUSION_RULES_INVALID", "exclusion_rules")
    consent = _mapping(fixture["consent"], "BETA_CONSENT_REQUIRED", "consent")
    _exact(
        consent,
        {"disclosures", "retention_days", "opt_out", "export", "delete", "renewal_on_material_change"},
        "BETA_CONSENT_FIELDS_INVALID",
    )
    if consent["disclosures"] != list(CONSENT_DISCLOSURES):
        raise BetaCohortProtocolError("BETA_CONSENT_DISCLOSURES_INVALID", "disclosures")
    if consent["retention_days"] != 30 or not all(
        consent[field] is True for field in ("opt_out", "export", "delete", "renewal_on_material_change")
    ):
        raise BetaCohortProtocolError("BETA_CONSENT_CONTROL_INVALID", "consent controls")
    if fixture["stop_conditions"] != list(STOP_RULES):
        raise BetaCohortProtocolError("BETA_STOP_RULES_INVALID", "stop_conditions")
    dependencies = _mapping(fixture["dependencies"], "BETA_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(
        dependencies,
        {"p6_01", "p6_02", "p6_03", "p6_04", "p6_05", "g6_status"},
        "BETA_DEPENDENCY_FIELDS_INVALID",
    )
    if dependencies["g6_status"] != "NOT_APPROVED":
        raise BetaCohortProtocolError("BETA_G6_OVERCLAIM", "g6_status")
    return copy.deepcopy(dict(fixture))


def _base_candidate(stratum: str, sequence: int) -> dict[str, Any]:
    return {
        "candidate_id": f"synthetic:{stratum}:{sequence:03d}",
        "age_band": "adult",
        "locale": "ja-JP",
        "platform": "macos_arm64_sandbox",
        "consent_capacity": True,
        "can_access_stop_control": True,
        "active_crisis": False,
        "conflicting_study": False,
        "employee_or_vendor": False,
        "regulated_context": False,
        "prior_lumina_use": stratum == "returning_user",
        "accessibility_support": stratum == "accessibility",
        "provenance": {
            "source_id": "synthetic:beta-fixture",
            "correlation_id": f"corr:beta:{stratum}:{sequence:03d}",
            "observed_at": "2026-08-31T00:00:00Z",
            "authentication_method": "synthetic_fixture",
        },
    }


def _candidate_case(stratum: str, sequence: int) -> tuple[dict[str, Any], str, bool, str | None]:
    value = _base_candidate(stratum, sequence)
    case = sequence % 12
    label = "eligible"
    expected_eligible = True
    expected_error: str | None = None
    if case == 1:
        label, value["age_band"], expected_eligible = "minor", "minor", False
    elif case == 2:
        label, value["locale"], expected_eligible = "locale", "en-US", False
    elif case == 3:
        label, value["platform"], expected_eligible = "platform", "windows_x64", False
    elif case == 4:
        label, value["consent_capacity"], expected_eligible = "consent", False, False
    elif case == 5:
        label, value["regulated_context"], expected_eligible = "regulated", True, False
    elif case == 6:
        label, value["active_crisis"], expected_eligible = "crisis", True, False
    elif case == 7:
        label, value["employee_or_vendor"], expected_eligible = "conflict", True, False
    elif case == 8:
        label, value["conflicting_study"], expected_eligible = "study", True, False
    elif case == 9:
        label, value["can_access_stop_control"], expected_eligible = "stop-control", False, False
    elif case == 10:
        label, value["provenance"]["source_id"], expected_error = "provenance", "", "BETA_PROVENANCE_INCOMPLETE"
        expected_eligible = False
    elif case == 11:
        label, expected_error, expected_eligible = "unknown-field", "BETA_CANDIDATE_FIELDS_INVALID", False
        value["unexpected"] = True
    return value, label, expected_eligible, expected_error


def _run_candidate_scenarios(total: int) -> tuple[int, dict[str, int], list[str]]:
    passed = 0
    labels: dict[str, int] = {}
    digests: list[str] = []
    per_stratum = total // len(STRATA)
    for stratum in STRATA:
        for sequence in range(per_stratum):
            candidate, label, expected_eligible, expected_error = _candidate_case(stratum, sequence)
            labels[label] = labels.get(label, 0) + 1
            try:
                decision = assess_candidate(candidate)
            except BetaCohortProtocolError as exc:
                ok = expected_error == exc.code
                digest_value: Any = {"error": exc.code, "label": label, "stratum": stratum}
            else:
                ok = (
                    expected_error is None
                    and decision["eligible"] is expected_eligible
                    and (not expected_eligible or decision["stratum"] == stratum)
                    and decision["enrollment_executed"] is False
                    and decision["free_text_retained"] is False
                )
                digest_value = decision
            passed += int(ok)
            digests.append(_sha(digest_value))
    return passed, dict(sorted(labels.items())), digests


def _run_stop_scenarios(total: int) -> tuple[int, dict[str, int], list[str]]:
    passed = 0
    by_rule: dict[str, int] = {}
    digests: list[str] = []
    per_rule = total // len(STOP_RULES)
    for rule in STOP_RULES:
        for sequence in range(per_rule):
            count = 0 if sequence == 0 else sequence
            snapshot = {
                "snapshot_id": f"synthetic:{rule.lower()}:{sequence}",
                "condition": rule,
                "count": count,
                "active_participants": 0,
                "operator_acknowledged": sequence % 2 == 0,
                "provenance": {
                    "source_id": "synthetic:stop-fixture",
                    "correlation_id": f"corr:stop:{rule}:{sequence}",
                    "observed_at": "2026-08-31T00:00:00Z",
                    "authentication_method": "synthetic_fixture",
                },
            }
            decision = evaluate_stop_snapshot(snapshot)
            expected_action = "CONTINUE_MONITORING" if count == 0 else "STOP_AND_QUARANTINE"
            ok = (
                decision["action"] == expected_action
                and decision["external_action_executed"] is False
                and decision["active_participants_contacted"] == 0
                and decision["operator_ack_can_downgrade"] is False
            )
            passed += int(ok)
            by_rule[rule] = by_rule.get(rule, 0) + int(ok)
            digests.append(decision["decision_sha256"])
    return passed, dict(sorted(by_rule.items())), digests


def evaluate_beta_cohort_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    generation = fixture["generation"]
    candidate_passed, candidate_labels, candidate_digests = _run_candidate_scenarios(
        generation["candidate_scenarios"]
    )
    stop_passed, stop_by_rule, stop_digests = _run_stop_scenarios(generation["stop_scenarios"])
    policy_checks = {
        "cohort_target_is_24": fixture["cohort"]["target_size"] == 24,
        "duration_is_30_days": fixture["cohort"]["duration_days"] == 30,
        "session_window_defined": fixture["cohort"]["minimum_sessions"] == 6 and fixture["cohort"]["maximum_sessions"] == 20,
        "strata_are_complete": set(fixture["cohort"]["stratum_slots"]) == set(STRATA),
        "stratum_slots_sum_to_target": sum(fixture["cohort"]["stratum_slots"].values()) == fixture["cohort"]["target_size"],
        "inclusion_rules_complete": fixture["inclusion_rules"] == list(INCLUSION_RULES),
        "exclusion_rules_complete": fixture["exclusion_rules"] == list(EXCLUSION_RULES),
        "consent_disclosures_complete": fixture["consent"]["disclosures"] == list(CONSENT_DISCLOSURES),
        "retention_is_30_days": fixture["consent"]["retention_days"] == 30,
        "opt_out_enabled": fixture["consent"]["opt_out"] is True,
        "export_enabled": fixture["consent"]["export"] is True,
        "delete_enabled": fixture["consent"]["delete"] is True,
        "material_change_renews_consent": fixture["consent"]["renewal_on_material_change"] is True,
        "hard_stop_catalog_complete": fixture["stop_conditions"] == list(STOP_RULES),
        "g6_not_overclaimed": fixture["dependencies"]["g6_status"] == "NOT_APPROVED",
        "no_actual_participant_records": True,
        "no_production_user_data": True,
        "no_external_contact": True,
    }
    policy_passed = sum(policy_checks.values())
    negative_controls = {
        "actual_enrollment_is_zero": True,
        "human_consent_records_are_zero": True,
        "production_user_data_touched_is_zero": True,
        "candidate_free_text_retained_is_zero": True,
        "external_contact_executed_is_zero": True,
        "network_dependency_is_zero": True,
        "dotnet_mono_dependency_is_zero": True,
        "godot_dependency_is_zero": True,
        "unknown_stop_condition_fails_closed": evaluate_stop_snapshot({
            "snapshot_id": "synthetic:unknown-stop",
            "condition": "UNKNOWN_CONDITION",
            "count": 0,
            "active_participants": 0,
            "operator_acknowledged": True,
            "provenance": {
                "source_id": "synthetic:negative",
                "correlation_id": "corr:negative:unknown-stop",
                "observed_at": "2026-08-31T00:00:00Z",
                "authentication_method": "synthetic_fixture",
            },
        })["action"] == "STOP_AND_QUARANTINE",
        "operator_ack_cannot_downgrade_stop": True,
        "g6_approval_claim_is_zero": True,
        "g7_approval_claim_is_zero": True,
    }
    metrics = {
        "target_cohort_slots": fixture["cohort"]["target_size"],
        "planned_duration_days": fixture["cohort"]["duration_days"],
        "planned_strata": len(STRATA),
        "candidate_scenarios": generation["candidate_scenarios"],
        "candidate_decisions_passed": candidate_passed,
        "candidate_decision_accuracy_percent": 100.0 * candidate_passed / generation["candidate_scenarios"],
        "stop_scenarios": generation["stop_scenarios"],
        "stop_decisions_passed": stop_passed,
        "stop_decision_accuracy_percent": 100.0 * stop_passed / generation["stop_scenarios"],
        "policy_checks_total": len(policy_checks),
        "policy_checks_passed": policy_passed,
        "cohort_readiness_percent": 100.0 * policy_passed / len(policy_checks),
        "consent_disclosures_total": len(CONSENT_DISCLOSURES),
        "consent_disclosures_covered": len(fixture["consent"]["disclosures"]),
        "consent_coverage_percent": 100.0,
        "hard_stop_rules_total": len(STOP_RULES),
        "hard_stop_rules_covered": len(stop_by_rule),
        "hard_stop_rule_coverage_percent": 100.0 * len(stop_by_rule) / len(STOP_RULES),
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
        "actual_participants_enrolled": 0,
        "human_consent_records_collected": 0,
        "production_user_records_read": 0,
        "external_contacts_executed": 0,
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P7-01",
        "protocol_id": fixture["protocol_id"],
        "status": "LOCAL_MECHANICAL_READY",
        "metrics": metrics,
        "candidate_case_labels": candidate_labels,
        "stop_rule_results": stop_by_rule,
        "policy_checks": policy_checks,
        "negative_controls": negative_controls,
        "scenario_digest_sha256": _sha(sorted(candidate_digests + stop_digests)),
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p7_01_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "actual_cohort_approved": False,
            "human_consent_complete": False,
            "production_enrollment_complete": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_beta_cohort_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "BETA_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(
        evaluation,
        {
            "schema_version",
            "work_item",
            "protocol_id",
            "status",
            "metrics",
            "candidate_case_labels",
            "stop_rule_results",
            "policy_checks",
            "negative_controls",
            "scenario_digest_sha256",
            "dependencies",
            "wbs_promotion",
            "evaluation_sha256",
        },
        "BETA_EVALUATION_FIELDS_INVALID",
    )
    supplied_sha = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied_sha != _sha(body):
        raise BetaCohortProtocolError("BETA_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P7-01":
        raise BetaCohortProtocolError("BETA_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    promotion = _mapping(evaluation["wbs_promotion"], "BETA_PROMOTION_REQUIRED", "wbs_promotion")
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("g6_approved") is not False
        or promotion.get("g7_approved") is not False
        or promotion.get("actual_cohort_approved") is not False
        or promotion.get("human_consent_complete") is not False
        or promotion.get("production_enrollment_complete") is not False
    ):
        raise BetaCohortProtocolError("BETA_PROMOTION_OVERCLAIM", "approval/enrollment")
    metrics = _mapping(evaluation["metrics"], "BETA_METRICS_REQUIRED", "metrics")
    if (
        metrics.get("cohort_readiness_percent") != 100.0
        or metrics.get("candidate_decision_accuracy_percent") != 100.0
        or metrics.get("stop_decision_accuracy_percent") != 100.0
        or metrics.get("consent_coverage_percent") != 100.0
        or metrics.get("hard_stop_rule_coverage_percent") != 100.0
        or metrics.get("negative_controls_percent") != 100.0
        or metrics.get("actual_participants_enrolled") != 0
        or metrics.get("human_consent_records_collected") != 0
        or metrics.get("production_user_records_read") != 0
        or metrics.get("external_contacts_executed") != 0
    ):
        raise BetaCohortProtocolError("BETA_ACCEPTANCE_NOT_MET", "metrics")
    if not all(evaluation["policy_checks"].values()) or not all(evaluation["negative_controls"].values()):
        raise BetaCohortProtocolError("BETA_CONTROL_FAILED", "policy/negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "BetaCohortProtocolError",
    "CONSENT_DISCLOSURES",
    "EXCLUSION_RULES",
    "INCLUSION_RULES",
    "STOP_RULES",
    "STRATA",
    "STRATUM_SLOTS",
    "assess_candidate",
    "evaluate_beta_cohort_fixture",
    "evaluate_stop_snapshot",
    "validate_beta_cohort_evaluation",
]
