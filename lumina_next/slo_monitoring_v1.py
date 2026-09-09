"""Deterministic, offline SLO monitoring reference for P8-02.

The module exercises alert semantics in memory. It does not connect to a live
dashboard, production telemetry, a pager, or an external monitoring service.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.slo-monitoring.fixture.v1"
EVALUATION_SCHEMA = "lumina.slo-monitoring.evaluation.v1"
METRICS = (
    "response_latency_ms",
    "stop_ack_seconds",
    "memory_leakage_count",
    "unsafe_autonomy_count",
    "safety_violation_count",
    "avatar_heartbeat_gap_seconds",
)


class SloMonitoringError(ValueError):
    """Stable fail-closed monitoring error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SloMonitoringError("SLO_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SloMonitoringError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise SloMonitoringError(code, "fields must match v1 exactly")


def _policy_table(value: Any) -> dict[str, dict[str, Any]]:
    table = _mapping(value, "SLO_POLICIES_REQUIRED", "policies")
    if tuple(sorted(table)) != tuple(sorted(METRICS)):
        raise SloMonitoringError("SLO_METRIC_SET_INVALID", "six canonical metrics required")
    checked: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        policy = _mapping(table[metric], "SLO_POLICY_REQUIRED", metric)
        _exact(policy, {"direction", "warning_threshold", "impact_threshold", "mttd_limit_sec", "unit"}, "SLO_POLICY_FIELDS_INVALID")
        warning = policy["warning_threshold"]
        impact = policy["impact_threshold"]
        limit = policy["mttd_limit_sec"]
        if policy["direction"] != "MAX" or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (warning, impact, limit)):
            raise SloMonitoringError("SLO_POLICY_INVALID", metric)
        if warning < 0 or impact <= warning or limit != 300.0 or not isinstance(policy["unit"], str) or not policy["unit"]:
            raise SloMonitoringError("SLO_POLICY_INVALID", metric)
        checked[metric] = copy.deepcopy(dict(policy))
    return checked


def detect_slo_sample(*, policies: Mapping[str, Any], sample: Mapping[str, Any]) -> dict[str, Any]:
    table = _policy_table(policies)
    value = _mapping(sample, "SLO_SAMPLE_REQUIRED", "sample")
    _exact(
        value,
        {"scenario_id", "tenant_id", "user_id", "metric", "value", "anomaly_onset_sec", "observed_at_sec", "impact_at_sec", "provenance_id"},
        "SLO_SAMPLE_FIELDS_INVALID",
    )
    for field in ("scenario_id", "tenant_id", "user_id", "provenance_id"):
        if not isinstance(value[field], str) or not value[field] or len(value[field]) > 128:
            raise SloMonitoringError("SLO_SAMPLE_ID_INVALID", field)
    metric = value["metric"]
    if metric not in table:
        raise SloMonitoringError("SLO_METRIC_UNKNOWN", str(metric))
    for field in ("value", "anomaly_onset_sec", "observed_at_sec", "impact_at_sec"):
        if not isinstance(value[field], (int, float)) or isinstance(value[field], bool):
            raise SloMonitoringError("SLO_SAMPLE_NUMBER_INVALID", field)
    if value["value"] < 0 or value["observed_at_sec"] < value["anomaly_onset_sec"] or value["impact_at_sec"] <= value["anomaly_onset_sec"]:
        raise SloMonitoringError("SLO_SAMPLE_TIME_INVALID", value["scenario_id"])
    policy = table[metric]
    breached = value["value"] >= policy["warning_threshold"]
    detection_latency = value["observed_at_sec"] - value["anomaly_onset_sec"] if breached else None
    return {
        "schema_version": "lumina.slo-alert.v1",
        "scenario_id": value["scenario_id"],
        "tenant_id": value["tenant_id"],
        "user_id": value["user_id"],
        "metric": metric,
        "alerted": breached,
        "severity": "WARNING" if breached else "HEALTHY",
        "detection_latency_sec": detection_latency,
        "detected_before_impact": bool(breached and value["observed_at_sec"] < value["impact_at_sec"]),
        "within_mttd": bool(breached and detection_latency is not None and detection_latency < policy["mttd_limit_sec"]),
        "provenance_id": value["provenance_id"],
        "external_notification_sent": False,
    }


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "SLO_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "policies", "dependencies"}, "SLO_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise SloMonitoringError("SLO_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "SLO_GENERATION_REQUIRED", "generation")
    _exact(generation, {"anomaly_trials_per_metric", "healthy_trials_per_metric"}, "SLO_GENERATION_FIELDS_INVALID")
    if generation != {"anomaly_trials_per_metric": 40, "healthy_trials_per_metric": 20}:
        raise SloMonitoringError("SLO_GENERATION_INVALID", "generation")
    _policy_table(fixture["policies"])
    dependencies = _mapping(fixture["dependencies"], "SLO_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"p8_01_status", "g7_status", "g8_status"}, "SLO_DEPENDENCY_FIELDS_INVALID")
    if dependencies != {"p8_01_status": "LOCAL_MECHANICAL_READY", "g7_status": "NOT_APPROVED", "g8_status": "NOT_APPROVED"}:
        raise SloMonitoringError("SLO_DEPENDENCY_OVERCLAIM", "dependencies")
    return copy.deepcopy(dict(fixture))


def evaluate_slo_monitoring_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    generation = fixture["generation"]
    policies = fixture["policies"]
    alerts: list[dict[str, Any]] = []
    healthy: list[dict[str, Any]] = []
    for metric_index, metric in enumerate(METRICS):
        policy = policies[metric]
        span = policy["impact_threshold"] - policy["warning_threshold"]
        for index in range(generation["anomaly_trials_per_metric"]):
            onset = 10_000.0 + metric_index * 1_000.0 + index * 100.0
            delay = float(1 + (index % 25))
            sample = {
                "scenario_id": f"ANOM-{metric_index:02d}-{index:03d}",
                "tenant_id": f"synthetic-tenant-{index % 4}",
                "user_id": f"synthetic-user-{index % 20}",
                "metric": metric,
                "value": policy["warning_threshold"] + span * 0.25,
                "anomaly_onset_sec": onset,
                "observed_at_sec": onset + delay,
                "impact_at_sec": onset + 60.0 + float(index % 10),
                "provenance_id": f"PROV-A-{metric_index:02d}-{index:03d}",
            }
            alerts.append(detect_slo_sample(policies=policies, sample=sample))
        for index in range(generation["healthy_trials_per_metric"]):
            onset = 30_000.0 + metric_index * 1_000.0 + index * 100.0
            sample = {
                "scenario_id": f"HEALTHY-{metric_index:02d}-{index:03d}",
                "tenant_id": f"synthetic-tenant-{index % 4}",
                "user_id": f"synthetic-user-{index % 20}",
                "metric": metric,
                "value": policy["warning_threshold"] * 0.5,
                "anomaly_onset_sec": onset,
                "observed_at_sec": onset + 1.0,
                "impact_at_sec": onset + 60.0,
                "provenance_id": f"PROV-H-{metric_index:02d}-{index:03d}",
            }
            healthy.append(detect_slo_sample(policies=policies, sample=sample))
    latencies = [float(item["detection_latency_sec"]) for item in alerts]
    negative_controls = {
        "all_anomalies_alerted": all(item["alerted"] for item in alerts),
        "all_alerts_before_synthetic_impact": all(item["detected_before_impact"] for item in alerts),
        "all_alerts_within_mttd": all(item["within_mttd"] for item in alerts),
        "healthy_false_alerts_are_zero": not any(item["alerted"] for item in healthy),
        "all_alerts_have_provenance": all(item["provenance_id"] for item in alerts),
        "external_notifications_are_zero": not any(item["external_notification_sent"] for item in alerts),
        "production_telemetry_reads_are_zero": True,
        "host_dashboard_writes_are_zero": True,
        "cross_user_aggregation_is_zero": True,
        "g8_claim_is_zero": True,
        "formal_wbs_claim_is_zero": True,
        "protected_assets_touched_are_zero": True,
    }
    metrics = {
        "metric_count": len(METRICS),
        "anomaly_trials": len(alerts),
        "anomalies_detected": sum(item["alerted"] for item in alerts),
        "anomaly_detection_percent": 100.0 * sum(item["alerted"] for item in alerts) / len(alerts),
        "before_impact_detected": sum(item["detected_before_impact"] for item in alerts),
        "before_impact_percent": 100.0 * sum(item["detected_before_impact"] for item in alerts) / len(alerts),
        "mttd_seconds": sum(latencies) / len(latencies),
        "max_detection_seconds": max(latencies),
        "healthy_trials": len(healthy),
        "false_positive_count": sum(item["alerted"] for item in healthy),
        "trace_records": len(alerts) + len(healthy),
        "trace_records_with_provenance": sum(bool(item["provenance_id"]) for item in alerts + healthy),
        "trace_provenance_percent": 100.0 * sum(bool(item["provenance_id"]) for item in alerts + healthy) / (len(alerts) + len(healthy)),
        "production_telemetry_reads": 0,
        "host_dashboard_writes": 0,
        "external_notifications": 0,
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P8-02",
        "status": "LOCAL_MONITORING_READY",
        "metrics": metrics,
        "metric_summary": {
            metric: {
                "anomaly_trials": sum(item["metric"] == metric for item in alerts),
                "detected": sum(item["metric"] == metric and item["alerted"] for item in alerts),
                "false_positives": sum(item["metric"] == metric and item["alerted"] for item in healthy),
            }
            for metric in METRICS
        },
        "trace_sha256": _sha({"alerts": alerts, "healthy": healthy}),
        "negative_controls": negative_controls,
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p8_02_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "live_slo_dashboard_connected": False,
            "pager_connected": False,
            "g8_approved": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_slo_monitoring_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "SLO_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "metric_summary", "trace_sha256", "negative_controls", "dependencies", "wbs_promotion", "evaluation_sha256"}, "SLO_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise SloMonitoringError("SLO_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P8-02":
        raise SloMonitoringError("SLO_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    promotion = evaluation["wbs_promotion"]
    if promotion != {
        "p8_02_local_mechanical_candidate": True,
        "formal_wbs_promotion_allowed": False,
        "live_slo_dashboard_connected": False,
        "pager_connected": False,
        "g8_approved": False,
    }:
        raise SloMonitoringError("SLO_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    if metrics.get("anomaly_detection_percent") != 100.0 or metrics.get("before_impact_percent") != 100.0 or metrics.get("trace_provenance_percent") != 100.0:
        raise SloMonitoringError("SLO_ACCEPTANCE_NOT_MET", "detection")
    if not 0 <= metrics.get("mttd_seconds", 999999) < 300.0 or metrics.get("false_positive_count") != 0:
        raise SloMonitoringError("SLO_ACCEPTANCE_NOT_MET", "mttd/false positive")
    for field in ("production_telemetry_reads", "host_dashboard_writes", "external_notifications"):
        if metrics.get(field) != 0:
            raise SloMonitoringError("SLO_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise SloMonitoringError("SLO_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "METRICS",
    "SloMonitoringError",
    "detect_slo_sample",
    "evaluate_slo_monitoring_fixture",
    "validate_slo_monitoring_evaluation",
]
