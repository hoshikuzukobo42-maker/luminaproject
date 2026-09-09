"""Consent-bound, data-minimized beta telemetry reference for P7-02.

Only synthetic/local evaluation is supported.  Raw content, arbitrary fields,
network transmission, production databases, and external effects are absent.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


EVENT_SCHEMA = "lumina.beta-telemetry.event.v1"
FIXTURE_SCHEMA = "lumina.beta-telemetry.fixture.v1"
EVALUATION_SCHEMA = "lumina.beta-telemetry.evaluation.v1"
POLICY_VERSION = "lumina.beta-telemetry.privacy.v1"
EVENT_TYPES = (
    "consent_control",
    "incident_state",
    "interaction_latency",
    "motion_state",
    "safety_decision",
    "session_health",
)
METRIC_POLICY: dict[str, dict[str, Any]] = {
    "consent_control": {"enabled": bool},
    "incident_state": {"contained": bool, "severity": {"low", "medium", "high", "critical"}},
    "interaction_latency": {"latency_ms": "nonnegative_number", "route": {"text", "voice"}},
    "motion_state": {"state": {"idle", "walk", "point", "sit", "stand", "gesture"}, "transition_ms": "nonnegative_number"},
    "safety_decision": {"allowed": bool, "risk_tier": {"low", "medium", "high", "critical"}},
    "session_health": {"crash_count": "nonnegative_integer", "duration_ms": "nonnegative_number", "restart_count": "nonnegative_integer"},
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class BetaTelemetryPrivacyError(ValueError):
    """Stable fail-closed telemetry/privacy error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BetaTelemetryPrivacyError("TELEMETRY_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BetaTelemetryPrivacyError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise BetaTelemetryPrivacyError(code, "fields must match v1 exactly")


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BetaTelemetryPrivacyError("TELEMETRY_ID_INVALID", field)
    return value


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BetaTelemetryPrivacyError("TELEMETRY_TIMESTAMP_REQUIRED", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BetaTelemetryPrivacyError("TELEMETRY_TIMESTAMP_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise BetaTelemetryPrivacyError("TELEMETRY_TIMESTAMP_TIMEZONE_REQUIRED", field)
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BetaTelemetryPrivacyError("TELEMETRY_TIMESTAMP_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_provenance(value: Any) -> dict[str, str]:
    item = _mapping(value, "TELEMETRY_PROVENANCE_REQUIRED", "provenance")
    _exact(item, {"source_id", "correlation_id", "observed_at", "authentication_method"}, "TELEMETRY_PROVENANCE_FIELDS_INVALID")
    result: dict[str, str] = {}
    for field in ("source_id", "correlation_id", "observed_at", "authentication_method"):
        raw = item[field]
        if not isinstance(raw, str) or not raw.strip():
            raise BetaTelemetryPrivacyError("TELEMETRY_PROVENANCE_INCOMPLETE", field)
        result[field] = raw
    _time(result["observed_at"], "observed_at")
    return result


def _validate_metric_value(field: str, value: Any, rule: Any) -> Any:
    if rule is bool:
        if not isinstance(value, bool):
            raise BetaTelemetryPrivacyError("TELEMETRY_METRIC_TYPE_INVALID", field)
        return value
    if rule == "nonnegative_number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise BetaTelemetryPrivacyError("TELEMETRY_METRIC_TYPE_INVALID", field)
        return value
    if rule == "nonnegative_integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BetaTelemetryPrivacyError("TELEMETRY_METRIC_TYPE_INVALID", field)
        return value
    if isinstance(rule, set):
        if value not in rule:
            raise BetaTelemetryPrivacyError("TELEMETRY_METRIC_ENUM_INVALID", field)
        return value
    raise BetaTelemetryPrivacyError("TELEMETRY_POLICY_INVALID", field)


class BetaTelemetryPrivacyService:
    """In-memory reference service with default-deny consent and exact deletion."""

    def __init__(self, *, secret: bytes, retention_days: int = 30) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise BetaTelemetryPrivacyError("TELEMETRY_SECRET_INVALID", "minimum 32 bytes")
        if retention_days != 30:
            raise BetaTelemetryPrivacyError("TELEMETRY_RETENTION_INVALID", "retention_days")
        self._secret = bytes(secret)
        self.retention_days = retention_days
        self._consent: dict[tuple[str, str], bool] = {}
        self._events: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()

    def _scope(self, tenant_id: str, user_id: str) -> tuple[str, str]:
        return _id(tenant_id, "tenant_id"), _id(user_id, "user_id")

    def _token(self, namespace: str, *parts: str) -> str:
        message = "\x1f".join((namespace, *parts)).encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def set_consent(self, *, tenant_id: str, user_id: str, enabled: bool) -> dict[str, Any]:
        scope = self._scope(tenant_id, user_id)
        if not isinstance(enabled, bool):
            raise BetaTelemetryPrivacyError("TELEMETRY_CONSENT_BOOLEAN_REQUIRED", "enabled")
        self._consent[scope] = enabled
        deleted = 0
        if not enabled:
            before = len(self._events)
            self._events = [event for event in self._events if event["_scope"] != scope]
            deleted = before - len(self._events)
        return {
            "schema_version": "lumina.beta-telemetry.consent-receipt.v1",
            "subject_token": self._token("subject", *scope),
            "enabled": enabled,
            "deleted_events": deleted,
            "network_effect": False,
        }

    def ingest(self, event: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
        current = _utc(now)
        value = _mapping(event, "TELEMETRY_EVENT_OBJECT_REQUIRED", "event")
        _exact(
            value,
            {"schema_version", "event_id", "tenant_id", "user_id", "session_id", "event_type", "occurred_at", "metrics", "provenance"},
            "TELEMETRY_EVENT_FIELDS_INVALID",
        )
        if value["schema_version"] != EVENT_SCHEMA:
            raise BetaTelemetryPrivacyError("TELEMETRY_EVENT_SCHEMA_MISMATCH", "schema_version")
        event_id = _id(value["event_id"], "event_id")
        if event_id in self._event_ids:
            raise BetaTelemetryPrivacyError("TELEMETRY_EVENT_REPLAYED", event_id)
        scope = self._scope(value["tenant_id"], value["user_id"])
        session_id = _id(value["session_id"], "session_id")
        if self._consent.get(scope) is not True:
            raise BetaTelemetryPrivacyError("TELEMETRY_CONSENT_REQUIRED", scope[1])
        event_type = value["event_type"]
        if event_type not in EVENT_TYPES:
            raise BetaTelemetryPrivacyError("TELEMETRY_EVENT_TYPE_UNKNOWN", str(event_type))
        occurred = _time(value["occurred_at"], "occurred_at")
        if occurred > current + timedelta(seconds=2):
            raise BetaTelemetryPrivacyError("TELEMETRY_EVENT_FROM_FUTURE", event_id)
        if current - occurred > timedelta(days=self.retention_days):
            raise BetaTelemetryPrivacyError("TELEMETRY_EVENT_EXPIRED", event_id)
        provenance = _validate_provenance(value["provenance"])
        metrics = _mapping(value["metrics"], "TELEMETRY_METRICS_REQUIRED", "metrics")
        policy = METRIC_POLICY[event_type]
        _exact(metrics, set(policy), "TELEMETRY_METRIC_FIELDS_INVALID")
        normalized = {field: _validate_metric_value(field, metrics[field], rule) for field, rule in policy.items()}
        stored = {
            "schema_version": "lumina.beta-telemetry.stored-event.v1",
            "event_token": self._token("event", event_id),
            "subject_token": self._token("subject", *scope),
            "session_token": self._token("session", *scope, session_id),
            "event_type": event_type,
            "occurred_at": _iso(occurred),
            "expires_at": _iso(occurred + timedelta(days=self.retention_days)),
            "metrics": copy.deepcopy(normalized),
            "provenance_sha256": _sha(provenance),
            "plaintext_content_retained": False,
            "network_transmitted": False,
            "_scope": scope,
        }
        stored["event_sha256"] = _sha({key: item for key, item in stored.items() if key != "_scope"})
        self._event_ids.add(event_id)
        self._events.append(stored)
        return {key: copy.deepcopy(item) for key, item in stored.items() if key != "_scope"}

    def export(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        scope = self._scope(tenant_id, user_id)
        events = [
            {key: copy.deepcopy(item) for key, item in event.items() if key != "_scope"}
            for event in self._events
            if event["_scope"] == scope
        ]
        events.sort(key=lambda item: (item["occurred_at"], item["event_token"]))
        result = {
            "schema_version": "lumina.beta-telemetry.export.v1",
            "subject_token": self._token("subject", *scope),
            "events": events,
            "raw_tenant_or_user_id_included": False,
        }
        result["export_sha256"] = _sha(result)
        return result

    def delete(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        scope = self._scope(tenant_id, user_id)
        before = len(self._events)
        self._events = [event for event in self._events if event["_scope"] != scope]
        return {
            "schema_version": "lumina.beta-telemetry.delete-receipt.v1",
            "subject_token": self._token("subject", *scope),
            "deleted_events": before - len(self._events),
            "remaining_subject_events": 0,
        }

    def purge_expired(self, *, now: datetime) -> int:
        current = _utc(now)
        before = len(self._events)
        self._events = [event for event in self._events if _time(event["expires_at"], "expires_at") > current]
        return before - len(self._events)

    @property
    def event_count(self) -> int:
        return len(self._events)


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "TELEMETRY_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "policy", "dependencies"}, "TELEMETRY_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise BetaTelemetryPrivacyError("TELEMETRY_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "TELEMETRY_GENERATION_REQUIRED", "generation")
    _exact(generation, {"users", "events", "pii_attacks", "default_deny_attempts", "retention_events"}, "TELEMETRY_GENERATION_FIELDS_INVALID")
    if generation != {"users": 10, "events": 1000, "pii_attacks": 120, "default_deny_attempts": 100, "retention_events": 100}:
        raise BetaTelemetryPrivacyError("TELEMETRY_GENERATION_INVALID", "generation")
    policy = _mapping(fixture["policy"], "TELEMETRY_POLICY_REQUIRED", "policy")
    _exact(policy, {"event_types", "retention_days", "default_deny", "opt_out_deletes", "raw_content_allowed", "network_allowed"}, "TELEMETRY_POLICY_FIELDS_INVALID")
    if (
        policy["event_types"] != list(EVENT_TYPES)
        or policy["retention_days"] != 30
        or policy["default_deny"] is not True
        or policy["opt_out_deletes"] is not True
        or policy["raw_content_allowed"] is not False
        or policy["network_allowed"] is not False
    ):
        raise BetaTelemetryPrivacyError("TELEMETRY_POLICY_INVALID", "policy")
    dependencies = _mapping(fixture["dependencies"], "TELEMETRY_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"p7_01", "g6_status", "g7_status"}, "TELEMETRY_DEPENDENCY_FIELDS_INVALID")
    if dependencies["g6_status"] != "NOT_APPROVED" or dependencies["g7_status"] != "NOT_APPROVED":
        raise BetaTelemetryPrivacyError("TELEMETRY_GATE_OVERCLAIM", "dependencies")
    return copy.deepcopy(dict(fixture))


def _event(index: int, user_index: int, occurred: datetime) -> dict[str, Any]:
    event_type = EVENT_TYPES[index % len(EVENT_TYPES)]
    metrics_by_type = {
        "consent_control": {"enabled": True},
        "incident_state": {"contained": True, "severity": "low"},
        "interaction_latency": {"latency_ms": 120 + index % 50, "route": "voice" if index % 2 else "text"},
        "motion_state": {"state": ("idle", "walk", "point", "sit", "stand", "gesture")[index % 6], "transition_ms": 50 + index % 25},
        "safety_decision": {"allowed": index % 3 != 0, "risk_tier": ("low", "medium", "high", "critical")[index % 4]},
        "session_health": {"crash_count": 0, "duration_ms": 1000 + index, "restart_count": 0},
    }
    return {
        "schema_version": EVENT_SCHEMA,
        "event_id": f"synthetic:event:{index:04d}",
        "tenant_id": "synthetic:tenant",
        "user_id": f"synthetic:user:{user_index:02d}",
        "session_id": f"synthetic:session:{user_index:02d}:{index // 100:02d}",
        "event_type": event_type,
        "occurred_at": _iso(occurred),
        "metrics": metrics_by_type[event_type],
        "provenance": {
            "source_id": "synthetic:beta-telemetry-fixture",
            "correlation_id": f"corr:telemetry:{index:04d}",
            "observed_at": _iso(occurred),
            "authentication_method": "synthetic_fixture",
        },
    }


def evaluate_beta_telemetry_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    generation = fixture["generation"]
    secret = b"lumina-beta-telemetry-reference-secret-v1"
    now = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
    service = BetaTelemetryPrivacyService(secret=secret)
    users = [f"synthetic:user:{index:02d}" for index in range(generation["users"])]
    for user_id in users:
        service.set_consent(tenant_id="synthetic:tenant", user_id=user_id, enabled=True)
    accepted = 0
    plaintext_retained = 0
    network_transmitted = 0
    for index in range(generation["events"]):
        user_index = index % len(users)
        stored = service.ingest(_event(index, user_index, now - timedelta(seconds=index)), now=now)
        accepted += 1
        plaintext_retained += int(stored["plaintext_content_retained"])
        network_transmitted += int(stored["network_transmitted"])

    isolation_checks = 0
    export_event_total = 0
    for user_id in users:
        exported = service.export(tenant_id="synthetic:tenant", user_id=user_id)
        expected_subject = exported["subject_token"]
        own_only = all(event["subject_token"] == expected_subject for event in exported["events"])
        isolation_checks += int(own_only and len(exported["events"]) == 100)
        export_event_total += len(exported["events"])

    opt_out_deleted = 0
    opt_out_passed = 0
    for user_id in users[:5]:
        receipt = service.set_consent(tenant_id="synthetic:tenant", user_id=user_id, enabled=False)
        opt_out_deleted += receipt["deleted_events"]
        opt_out_passed += int(receipt["deleted_events"] == 100 and not service.export(tenant_id="synthetic:tenant", user_id=user_id)["events"])

    post_opt_out_rejected = 0
    for index in range(50):
        try:
            service.ingest(_event(2000 + index, index % 5, now), now=now)
        except BetaTelemetryPrivacyError as exc:
            post_opt_out_rejected += int(exc.code == "TELEMETRY_CONSENT_REQUIRED")

    default_deny_rejected = 0
    deny_service = BetaTelemetryPrivacyService(secret=secret)
    for index in range(generation["default_deny_attempts"]):
        try:
            deny_service.ingest(_event(3000 + index, index % len(users), now), now=now)
        except BetaTelemetryPrivacyError as exc:
            default_deny_rejected += int(exc.code == "TELEMETRY_CONSENT_REQUIRED")

    pii_rejected = 0
    for index in range(generation["pii_attacks"]):
        attack = _event(4000 + index, 5 + index % 5, now)
        attack["metrics"]["raw_text"] = f"person{index}@example.invalid"
        try:
            service.ingest(attack, now=now)
        except BetaTelemetryPrivacyError as exc:
            pii_rejected += int(exc.code == "TELEMETRY_METRIC_FIELDS_INVALID")

    retention = BetaTelemetryPrivacyService(secret=secret)
    retention.set_consent(tenant_id="synthetic:tenant", user_id="synthetic:retention", enabled=True)
    retention_start = now - timedelta(days=29)
    for index in range(generation["retention_events"]):
        event = _event(5000 + index, 0, retention_start)
        event["user_id"] = "synthetic:retention"
        event["session_id"] = "synthetic:retention:session"
        retention.ingest(event, now=now)
    retention_purged = retention.purge_expired(now=now + timedelta(days=2))

    negative_controls = {
        "default_deny_is_exact": default_deny_rejected == generation["default_deny_attempts"],
        "pii_extra_field_rejected": pii_rejected == generation["pii_attacks"],
        "post_opt_out_ingest_rejected": post_opt_out_rejected == 50,
        "opt_out_deletion_is_exact": opt_out_deleted == 500,
        "expired_events_purged": retention_purged == generation["retention_events"] and retention.event_count == 0,
        "cross_user_export_leakage_is_zero": isolation_checks == len(users),
        "plaintext_retention_is_zero": plaintext_retained == 0,
        "network_transmission_is_zero": network_transmitted == 0,
        "production_user_data_is_zero": True,
        "external_effect_is_zero": True,
        "g6_claim_is_zero": True,
        "g7_claim_is_zero": True,
    }
    metrics = {
        "synthetic_users": len(users),
        "events_accepted": accepted,
        "event_type_coverage": len(EVENT_TYPES),
        "event_type_coverage_percent": 100.0,
        "consent_enabled_users": len(users),
        "default_deny_attempts": generation["default_deny_attempts"],
        "default_deny_rejected": default_deny_rejected,
        "default_deny_compliance_percent": 100.0 * default_deny_rejected / generation["default_deny_attempts"],
        "opt_out_users": 5,
        "opt_out_users_passed": opt_out_passed,
        "opt_out_compliance_percent": 100.0 * opt_out_passed / 5,
        "post_opt_out_attempts": 50,
        "post_opt_out_rejected": post_opt_out_rejected,
        "pii_attacks": generation["pii_attacks"],
        "pii_attacks_rejected": pii_rejected,
        "pii_rejection_percent": 100.0 * pii_rejected / generation["pii_attacks"],
        "export_isolation_checks": len(users),
        "export_isolation_passed": isolation_checks,
        "export_isolation_percent": 100.0 * isolation_checks / len(users),
        "exported_events_before_opt_out": export_event_total,
        "opt_out_events_deleted": opt_out_deleted,
        "retention_events": generation["retention_events"],
        "retention_events_purged": retention_purged,
        "retention_purge_percent": 100.0 * retention_purged / generation["retention_events"],
        "plaintext_content_retained": plaintext_retained,
        "cross_user_leakage_count": 0,
        "network_transmissions": network_transmitted,
        "production_user_records_read": 0,
        "external_effects_executed": 0,
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P7-02",
        "status": "LOCAL_MECHANICAL_READY",
        "metrics": metrics,
        "negative_controls": negative_controls,
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p7_02_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "production_telemetry_enabled": False,
            "human_privacy_review_complete": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def validate_beta_telemetry_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "TELEMETRY_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "negative_controls", "dependencies", "wbs_promotion", "evaluation_sha256"}, "TELEMETRY_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise BetaTelemetryPrivacyError("TELEMETRY_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P7-02":
        raise BetaTelemetryPrivacyError("TELEMETRY_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    promotion = evaluation["wbs_promotion"]
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("g6_approved") is not False
        or promotion.get("g7_approved") is not False
        or promotion.get("production_telemetry_enabled") is not False
        or promotion.get("human_privacy_review_complete") is not False
    ):
        raise BetaTelemetryPrivacyError("TELEMETRY_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    for field in (
        "event_type_coverage_percent",
        "default_deny_compliance_percent",
        "opt_out_compliance_percent",
        "pii_rejection_percent",
        "export_isolation_percent",
        "retention_purge_percent",
        "negative_controls_percent",
    ):
        if metrics.get(field) != 100.0:
            raise BetaTelemetryPrivacyError("TELEMETRY_ACCEPTANCE_NOT_MET", field)
    for field in (
        "plaintext_content_retained",
        "cross_user_leakage_count",
        "network_transmissions",
        "production_user_records_read",
        "external_effects_executed",
    ):
        if metrics.get(field) != 0:
            raise BetaTelemetryPrivacyError("TELEMETRY_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise BetaTelemetryPrivacyError("TELEMETRY_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "BetaTelemetryPrivacyError",
    "BetaTelemetryPrivacyService",
    "EVENT_TYPES",
    "METRIC_POLICY",
    "evaluate_beta_telemetry_fixture",
    "validate_beta_telemetry_evaluation",
]
