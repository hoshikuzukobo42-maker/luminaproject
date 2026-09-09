"""P6-04 deterministic community safety and moderation reference.

All responses are simulated policy decisions.  The module performs no live
moderation, messaging, Godot operation, network call, or external action.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .multi_user_identity_v1 import (
    REFERENCE_TEST_SECRET,
    MultiUserIdentityError,
    MultiUserIdentityModel,
    create_identity_context,
)
from .role_packs_v1 import (
    ROLE_DECISION_SCHEMA,
    RolePackEngine,
)
from .social_interaction_protocol_v1 import (
    SOCIAL_EVENT_SCHEMA,
    SocialInteractionProtocol,
)


INCIDENT_REQUEST_SCHEMA = "lumina.community-safety.incident-request.v1"
INCIDENT_DECISION_SCHEMA = "lumina.community-safety.incident-decision.v1"
APPEAL_REQUEST_SCHEMA = "lumina.community-safety.appeal-request.v1"
APPEAL_DECISION_SCHEMA = "lumina.community-safety.appeal-decision.v1"
AUDIT_ENTRY_SCHEMA = "lumina.community-safety.audit-entry.v1"
FIXTURE_SCHEMA = "lumina.community-safety.fixture.v1"
EVALUATION_SCHEMA = "lumina.community-safety.evaluation.v1"
POLICY_VERSION = "lumina.community-safety.policy.v1"
MAX_RESPONSE_SECONDS = 300

CATEGORY_POLICIES: dict[str, dict[str, Any]] = {
    "harassment": {
        "severity": "HIGH",
        "critical": False,
        "actions": ("WARN", "MUTE"),
    },
    "threat": {
        "severity": "CRITICAL",
        "critical": True,
        "actions": ("REMOVE", "HANDOFF", "STOP"),
    },
    "self_harm": {
        "severity": "CRITICAL",
        "critical": True,
        "actions": ("HANDOFF", "STOP"),
    },
    "crisis": {
        "severity": "CRITICAL",
        "critical": True,
        "actions": ("HANDOFF", "STOP"),
    },
    "privacy_leak": {
        "severity": "CRITICAL",
        "critical": True,
        "actions": ("REMOVE", "HANDOFF", "STOP"),
    },
    "impersonation": {
        "severity": "HIGH",
        "critical": False,
        "actions": ("MUTE", "REMOVE"),
    },
    "spam": {
        "severity": "MEDIUM",
        "critical": False,
        "actions": ("WARN", "MUTE"),
    },
    "unsafe_external_request": {
        "severity": "CRITICAL",
        "critical": True,
        "actions": ("HANDOFF", "STOP"),
    },
}
CATEGORY_IDS = tuple(sorted(CATEGORY_POLICIES))
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class CommunitySafetyError(ValueError):
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
        raise CommunitySafetyError("SAFETY_NONCANONICAL_JSON", "payload") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CommunitySafetyError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise CommunitySafetyError(code, detail)


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CommunitySafetyError("SAFETY_ID_INVALID", field_name)
    return value


def _time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CommunitySafetyError("SAFETY_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommunitySafetyError("SAFETY_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise CommunitySafetyError("SAFETY_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CommunitySafetyError("SAFETY_TIMESTAMP_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _provenance_complete(value: Mapping[str, Any]) -> bool:
    return all(
        value.get(field)
        for field in ("source_id", "correlation_id", "observed_at", "authentication_method")
    )


def get_category_policy(category: str) -> dict[str, Any]:
    if category not in CATEGORY_POLICIES:
        raise CommunitySafetyError("SAFETY_CATEGORY_UNKNOWN", str(category))
    policy = CATEGORY_POLICIES[category]
    return {
        "category": category,
        "severity": policy["severity"],
        "critical": policy["critical"],
        "actions": list(policy["actions"]),
    }


class CommunitySafetyEngine:
    """Classify incidents and appeals against bound P6 state."""

    def __init__(
        self,
        *,
        identity_model: MultiUserIdentityModel,
        social_protocol: SocialInteractionProtocol,
    ) -> None:
        if not isinstance(identity_model, MultiUserIdentityModel):
            raise CommunitySafetyError("SAFETY_IDENTITY_MODEL_REQUIRED", "identity_model")
        if not isinstance(social_protocol, SocialInteractionProtocol):
            raise CommunitySafetyError("SAFETY_SOCIAL_PROTOCOL_REQUIRED", "social_protocol")
        self.identity_model = identity_model
        self.social_protocol = social_protocol
        self._incidents: dict[str, dict[str, Any]] = {}
        self._appeals: set[str] = set()
        self._audit: list[dict[str, Any]] = []

    def _validate_role_decision(
        self,
        value: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        decision = _mapping(value, "SAFETY_ROLE_DECISION_REQUIRED", "role_decision")
        if decision.get("schema_version") != ROLE_DECISION_SCHEMA:
            raise CommunitySafetyError("SAFETY_ROLE_DECISION_SCHEMA_MISMATCH", "schema_version")
        expected = _sha({key: _copy(item) for key, item in decision.items() if key != "decision_sha256"})
        if decision.get("decision_sha256") != expected:
            raise CommunitySafetyError("SAFETY_ROLE_DECISION_TAMPERED", "decision_sha256")
        if request["role_decision_sha256"] != decision["decision_sha256"]:
            raise CommunitySafetyError("SAFETY_ROLE_DECISION_BINDING_MISMATCH", "decision_sha256")
        for field in ("tenant_id", "user_id", "session_id", "room_id"):
            request_field = "reporter_user_id" if field == "user_id" else "reporter_session_id" if field == "session_id" else field
            if decision[field] != request[request_field]:
                raise CommunitySafetyError("SAFETY_ROLE_DECISION_SCOPE_MISMATCH", field)
        if (
            decision["decision"] != "ALLOW_LOCAL_REFERENCE"
            or decision["external_action_executed"] is not False
            or decision["live_action_executed"] is not False
        ):
            raise CommunitySafetyError("SAFETY_ROLE_DECISION_NOT_ELIGIBLE", decision["decision"])
        return _copy(dict(decision))

    def _incident_request(self, value: Mapping[str, Any], current: datetime) -> dict[str, Any]:
        request = _mapping(value, "SAFETY_INCIDENT_REQUEST_OBJECT_REQUIRED", "request")
        fields = {
            "schema_version",
            "incident_id",
            "room_id",
            "tenant_id",
            "reporter_user_id",
            "reporter_session_id",
            "subject_user_id",
            "category",
            "severity",
            "occurred_at",
            "requested_at",
            "evidence",
            "role_decision_sha256",
            "external_effect_requested",
            "provenance",
        }
        _exact(request, fields, "SAFETY_INCIDENT_REQUEST_FIELDS_INVALID", "incident request")
        if request["schema_version"] != INCIDENT_REQUEST_SCHEMA:
            raise CommunitySafetyError("SAFETY_INCIDENT_REQUEST_SCHEMA_MISMATCH", "schema_version")
        incident_id = _id(request["incident_id"], "incident_id")
        if incident_id in self._incidents:
            raise CommunitySafetyError("SAFETY_INCIDENT_REPLAYED", incident_id)
        for field in ("room_id", "tenant_id", "reporter_user_id", "reporter_session_id", "subject_user_id"):
            _id(request[field], field)
        if request["category"] not in CATEGORY_POLICIES:
            raise CommunitySafetyError("SAFETY_CATEGORY_UNKNOWN", str(request["category"]))
        policy = CATEGORY_POLICIES[request["category"]]
        if request["severity"] != policy["severity"]:
            raise CommunitySafetyError("SAFETY_SEVERITY_POLICY_MISMATCH", str(request["severity"]))
        occurred = _time(request["occurred_at"], "occurred_at")
        requested = _time(request["requested_at"], "requested_at")
        if requested != current:
            raise CommunitySafetyError("SAFETY_REQUEST_TIME_MISMATCH", incident_id)
        latency = (requested - occurred).total_seconds()
        if latency < 0 or latency >= MAX_RESPONSE_SECONDS:
            raise CommunitySafetyError("SAFETY_RESPONSE_WINDOW_EXCEEDED", str(latency))
        evidence = _mapping(request["evidence"], "SAFETY_EVIDENCE_REQUIRED", "evidence")
        _exact(
            evidence,
            {"signal_type", "source_sha256", "provenance_id"},
            "SAFETY_EVIDENCE_FIELDS_INVALID",
            "evidence",
        )
        if evidence["signal_type"] != request["category"]:
            raise CommunitySafetyError("SAFETY_EVIDENCE_CATEGORY_MISMATCH", str(evidence["signal_type"]))
        if not isinstance(evidence["source_sha256"], str) or not _SHA.fullmatch(evidence["source_sha256"]):
            raise CommunitySafetyError("SAFETY_EVIDENCE_HASH_INVALID", "source_sha256")
        _id(evidence["provenance_id"], "evidence.provenance_id")
        if not isinstance(request["role_decision_sha256"], str) or not _SHA.fullmatch(request["role_decision_sha256"]):
            raise CommunitySafetyError("SAFETY_ROLE_DECISION_HASH_INVALID", "role_decision_sha256")
        if request["external_effect_requested"] is not False:
            raise CommunitySafetyError("SAFETY_EXTERNAL_EFFECT_PROHIBITED", incident_id)
        provenance = _mapping(request["provenance"], "SAFETY_PROVENANCE_REQUIRED", "provenance")
        _exact(
            provenance,
            {"source_id", "correlation_id", "observed_at", "authentication_method"},
            "SAFETY_PROVENANCE_FIELDS_INVALID",
            "provenance",
        )
        if not _provenance_complete(provenance):
            raise CommunitySafetyError("SAFETY_PROVENANCE_INCOMPLETE", incident_id)
        _time(provenance["observed_at"], "provenance.observed_at")
        _canonical(request)
        return _copy(dict(request))

    def _append_audit(
        self,
        *,
        entry_type: str,
        record_id: str,
        actor_user_id: str,
        occurred_at: str,
        record_sha256: str,
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        entry = {
            "schema_version": AUDIT_ENTRY_SCHEMA,
            "policy_version": POLICY_VERSION,
            "sequence": len(self._audit) + 1,
            "entry_type": entry_type,
            "record_id": record_id,
            "actor_user_id": actor_user_id,
            "occurred_at": occurred_at,
            "record_sha256": record_sha256,
            "previous_entry_sha256": self._audit[-1]["entry_sha256"] if self._audit else None,
            "provenance": _copy(dict(provenance)),
        }
        entry["entry_sha256"] = _sha(entry)
        self._audit.append(entry)
        return _copy(entry)

    def assess(
        self,
        token: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        role_decision: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        current = _utc(now)
        try:
            scope = self.identity_model.validate_scope_token(
                token,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
            )
        except MultiUserIdentityError as exc:
            raise CommunitySafetyError("SAFETY_IDENTITY_TOKEN_REJECTED", exc.code) from exc
        normalized = self._incident_request(request, current)
        if normalized["tenant_id"] != scope["tenant_id"]:
            raise CommunitySafetyError("SAFETY_TENANT_TOKEN_MISMATCH", normalized["tenant_id"])
        if normalized["reporter_user_id"] != scope["user_id"]:
            raise CommunitySafetyError("SAFETY_REPORTER_TOKEN_MISMATCH", normalized["reporter_user_id"])
        if normalized["reporter_session_id"] != scope["session_id"]:
            raise CommunitySafetyError("SAFETY_SESSION_TOKEN_MISMATCH", normalized["reporter_session_id"])
        if normalized["provenance"] != scope["provenance"]:
            raise CommunitySafetyError("SAFETY_PROVENANCE_TOKEN_MISMATCH", normalized["incident_id"])
        self._validate_role_decision(role_decision, normalized)
        snapshot = self.social_protocol.snapshot()
        if normalized["room_id"] != snapshot["room_id"] or normalized["tenant_id"] != snapshot["tenant_id"]:
            raise CommunitySafetyError("SAFETY_SOCIAL_SCOPE_MISMATCH", normalized["room_id"])
        reporter = snapshot["participants"].get(normalized["reporter_user_id"])
        subject = snapshot["participants"].get(normalized["subject_user_id"])
        if reporter is None or reporter["presence"] != "PRESENT":
            raise CommunitySafetyError("SAFETY_REPORTER_NOT_PRESENT", normalized["reporter_user_id"])
        if subject is None or subject["presence"] != "PRESENT":
            raise CommunitySafetyError("SAFETY_SUBJECT_NOT_PRESENT", normalized["subject_user_id"])
        if snapshot["active_speaker_user_id"] != normalized["reporter_user_id"]:
            raise CommunitySafetyError("SAFETY_REPORTER_NOT_CURRENT_SPEAKER", normalized["reporter_user_id"])
        try:
            self.identity_model.validate_scope_token(
                scope,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
                consume=True,
            )
        except MultiUserIdentityError as exc:
            raise CommunitySafetyError("SAFETY_IDENTITY_TOKEN_REJECTED", exc.code) from exc

        policy = CATEGORY_POLICIES[normalized["category"]]
        latency = int((current - _time(normalized["occurred_at"], "occurred_at")).total_seconds())
        body = {
            "schema_version": INCIDENT_DECISION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "decision": "SIMULATED_CONTAINMENT",
            "incident_id": normalized["incident_id"],
            "room_id": normalized["room_id"],
            "tenant_id": normalized["tenant_id"],
            "reporter_user_id": normalized["reporter_user_id"],
            "subject_user_id": normalized["subject_user_id"],
            "category": normalized["category"],
            "severity": normalized["severity"],
            "critical": policy["critical"],
            "response_actions": list(policy["actions"]),
            "classification_confidence": 1.0,
            "incident_response_seconds": latency,
            "within_five_minutes": latency < MAX_RESPONSE_SECONDS,
            "evidence_source_sha256": normalized["evidence"]["source_sha256"],
            "role_decision_sha256": normalized["role_decision_sha256"],
            "request_sha256": _sha(normalized),
            "provenance": {
                **_copy(normalized["provenance"]),
                "identity_token_id": scope["token_id"],
                "identity_context_sha256": scope["context_sha256"],
            },
            "warn_executed": False,
            "mute_executed": False,
            "remove_executed": False,
            "handoff_executed": False,
            "stop_executed": False,
            "external_action_executed": False,
            "live_moderation_executed": False,
        }
        audit = self._append_audit(
            entry_type="INCIDENT_DECISION",
            record_id=normalized["incident_id"],
            actor_user_id=normalized["reporter_user_id"],
            occurred_at=_iso(current),
            record_sha256=_sha(body),
            provenance=normalized["provenance"],
        )
        body["audit_entry_sha256"] = audit["entry_sha256"]
        body["decision_sha256"] = _sha(body)
        self._incidents[normalized["incident_id"]] = _copy(body)
        return body

    def appeal(
        self,
        token: Mapping[str, Any],
        value: Mapping[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        current = _utc(now)
        try:
            scope = self.identity_model.validate_scope_token(
                token,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
            )
        except MultiUserIdentityError as exc:
            raise CommunitySafetyError("SAFETY_IDENTITY_TOKEN_REJECTED", exc.code) from exc
        request = _mapping(value, "SAFETY_APPEAL_REQUEST_OBJECT_REQUIRED", "appeal")
        fields = {
            "schema_version",
            "appeal_id",
            "incident_id",
            "incident_decision_sha256",
            "room_id",
            "tenant_id",
            "user_id",
            "session_id",
            "grounds_code",
            "requested_at",
            "provenance",
        }
        _exact(request, fields, "SAFETY_APPEAL_REQUEST_FIELDS_INVALID", "appeal")
        if request["schema_version"] != APPEAL_REQUEST_SCHEMA:
            raise CommunitySafetyError("SAFETY_APPEAL_SCHEMA_MISMATCH", "schema_version")
        appeal_id = _id(request["appeal_id"], "appeal_id")
        incident_id = _id(request["incident_id"], "incident_id")
        if appeal_id in self._appeals:
            raise CommunitySafetyError("SAFETY_APPEAL_REPLAYED", appeal_id)
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise CommunitySafetyError("SAFETY_APPEAL_INCIDENT_NOT_FOUND", incident_id)
        for field in ("room_id", "tenant_id", "user_id", "session_id", "grounds_code"):
            _id(request[field], field)
        if _time(request["requested_at"], "requested_at") != current:
            raise CommunitySafetyError("SAFETY_APPEAL_TIME_MISMATCH", appeal_id)
        provenance = _mapping(request["provenance"], "SAFETY_PROVENANCE_REQUIRED", "provenance")
        _exact(
            provenance,
            {"source_id", "correlation_id", "observed_at", "authentication_method"},
            "SAFETY_PROVENANCE_FIELDS_INVALID",
            "provenance",
        )
        if not _provenance_complete(provenance) or provenance != scope["provenance"]:
            raise CommunitySafetyError("SAFETY_APPEAL_PROVENANCE_MISMATCH", appeal_id)
        if request["tenant_id"] != scope["tenant_id"] or request["user_id"] != scope["user_id"] or request["session_id"] != scope["session_id"]:
            raise CommunitySafetyError("SAFETY_APPEAL_IDENTITY_MISMATCH", appeal_id)
        if request["user_id"] != incident["subject_user_id"]:
            raise CommunitySafetyError("SAFETY_APPEAL_SUBJECT_MISMATCH", request["user_id"])
        if request["room_id"] != incident["room_id"] or request["tenant_id"] != incident["tenant_id"]:
            raise CommunitySafetyError("SAFETY_APPEAL_SCOPE_MISMATCH", appeal_id)
        if request["incident_decision_sha256"] != incident["decision_sha256"]:
            raise CommunitySafetyError("SAFETY_APPEAL_DECISION_BINDING_MISMATCH", appeal_id)
        participant = self.social_protocol.snapshot()["participants"].get(request["user_id"])
        if participant is None or participant["presence"] != "PRESENT":
            raise CommunitySafetyError("SAFETY_APPEAL_USER_NOT_PRESENT", request["user_id"])
        try:
            self.identity_model.validate_scope_token(
                scope,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
                consume=True,
            )
        except MultiUserIdentityError as exc:
            raise CommunitySafetyError("SAFETY_IDENTITY_TOKEN_REJECTED", exc.code) from exc
        body = {
            "schema_version": APPEAL_DECISION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "decision": "SIMULATED_REVIEW_REQUIRED",
            "appeal_id": appeal_id,
            "incident_id": incident_id,
            "incident_decision_sha256": incident["decision_sha256"],
            "tenant_id": request["tenant_id"],
            "user_id": request["user_id"],
            "grounds_code": request["grounds_code"],
            "original_containment_reversed": False,
            "human_review_executed": False,
            "external_action_executed": False,
            "provenance": {
                **_copy(provenance),
                "identity_token_id": scope["token_id"],
                "identity_context_sha256": scope["context_sha256"],
            },
        }
        audit = self._append_audit(
            entry_type="APPEAL_DECISION",
            record_id=appeal_id,
            actor_user_id=request["user_id"],
            occurred_at=_iso(current),
            record_sha256=_sha(body),
            provenance=provenance,
        )
        body["audit_entry_sha256"] = audit["entry_sha256"]
        body["decision_sha256"] = _sha(body)
        self._appeals.add(appeal_id)
        return body

    def audit_log(self) -> list[dict[str, Any]]:
        return _copy(self._audit)


def validate_audit_log(value: list[Mapping[str, Any]]) -> bool:
    previous: str | None = None
    for sequence, item in enumerate(value, start=1):
        if not isinstance(item, Mapping) or item.get("schema_version") != AUDIT_ENTRY_SCHEMA:
            return False
        if item.get("sequence") != sequence or item.get("previous_entry_sha256") != previous:
            return False
        expected = _sha({key: _copy(entry) for key, entry in item.items() if key != "entry_sha256"})
        if item.get("entry_sha256") != expected or not _provenance_complete(item.get("provenance", {})):
            return False
        previous = item["entry_sha256"]
    return True


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "SAFETY_FIXTURE_OBJECT_REQUIRED", "fixture")
    fields = {"schema_version", "issuer", "generation", "categories", "protocol", "foreign_markers", "expectations"}
    _exact(fixture, fields, "SAFETY_FIXTURE_FIELDS_INVALID", "fixture")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise CommunitySafetyError("SAFETY_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    issuer = _mapping(fixture["issuer"], "SAFETY_ISSUER_REQUIRED", "issuer")
    _exact(issuer, {"issuer_id", "reference_secret_id"}, "SAFETY_ISSUER_FIELDS_INVALID", "issuer")
    _id(issuer["issuer_id"], "issuer_id")
    if issuer["reference_secret_id"] != "p6-01-local-test-key-v1":
        raise CommunitySafetyError("SAFETY_REFERENCE_SECRET_ID_INVALID", "reference_secret_id")
    generation = _mapping(fixture["generation"], "SAFETY_GENERATION_REQUIRED", "generation")
    _exact(generation, {"tenant_id", "scenarios_per_category", "start_at"}, "SAFETY_GENERATION_FIELDS_INVALID", "generation")
    _id(generation["tenant_id"], "tenant_id")
    count = generation["scenarios_per_category"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 20:
        raise CommunitySafetyError("SAFETY_SCENARIO_COUNT_TOO_SMALL", "20 per category required")
    _time(generation["start_at"], "start_at")
    if not isinstance(fixture["categories"], list) or set(fixture["categories"]) != set(CATEGORY_IDS):
        raise CommunitySafetyError("SAFETY_CATEGORY_CATALOG_INVALID", "categories")
    protocol = _mapping(fixture["protocol"], "SAFETY_PROTOCOL_CONFIG_REQUIRED", "protocol")
    _exact(protocol, {"token_ttl_seconds", "critical_response_seconds", "noncritical_response_seconds"}, "SAFETY_PROTOCOL_CONFIG_FIELDS_INVALID", "protocol")
    for field in ("token_ttl_seconds", "critical_response_seconds", "noncritical_response_seconds"):
        number = protocol[field]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise CommunitySafetyError("SAFETY_PROTOCOL_VALUE_INVALID", field)
    if protocol["token_ttl_seconds"] > 300 or protocol["critical_response_seconds"] >= MAX_RESPONSE_SECONDS or protocol["noncritical_response_seconds"] >= MAX_RESPONSE_SECONDS:
        raise CommunitySafetyError("SAFETY_PROTOCOL_VALUE_INVALID", "response/ttl")
    markers = fixture["foreign_markers"]
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise CommunitySafetyError("SAFETY_FOREIGN_MARKERS_INVALID", "foreign_markers")
    expectations = _mapping(fixture["expectations"], "SAFETY_EXPECTATIONS_REQUIRED", "expectations")
    _exact(expectations, {"critical_detection_percent_min", "critical_containment_percent_min", "max_response_seconds"}, "SAFETY_EXPECTATIONS_FIELDS_INVALID", "expectations")
    normalized = _copy(dict(fixture))
    normalized["categories"] = sorted(normalized["categories"])
    return normalized


def _context(tenant: str, category: str, index: int, label: str, role: str, start: datetime) -> dict[str, Any]:
    tag = f"{category}:{index:03d}:{label}"
    return create_identity_context(
        tenant_id=tenant,
        user_id=f"user:safety:{tag}",
        session_id=f"session:safety:{tag}",
        role=role,
        permissions=("action:evaluate_self", "memory:read_self", "world:read_self"),
        provenance={
            "source_id": f"source:safety:{tag}",
            "correlation_id": f"corr:safety:{tag}",
            "observed_at": _iso(start),
            "authentication_method": "deterministic_fixture",
        },
    )


def _issue(model: MultiUserIdentityModel, context: Mapping[str, Any], token_id: str, now: datetime, ttl: int) -> dict[str, Any]:
    return model.issue_scope_token(
        context,
        token_id=token_id,
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=ttl),
    )


def _social_event(event_id: str, room: str, context: Mapping[str, Any], kind: str, now: datetime, content: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SOCIAL_EVENT_SCHEMA,
        "event_id": event_id,
        "room_id": room,
        "tenant_id": context["tenant_id"],
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "kind": kind,
        "target_user_id": None,
        "consent_ref": None,
        "content": content,
        "requested_at": _iso(now),
        "provenance": _copy(context["provenance"]),
    }


def _setup(issuer: str, tenant: str, category: str, index: int, start: datetime, ttl: int) -> tuple[Any, ...]:
    model = MultiUserIdentityModel(issuer_id=issuer, secret=REFERENCE_TEST_SECRET)
    room = f"room:safety:{category}:{index:03d}"
    social = SocialInteractionProtocol(room_id=room, tenant_id=tenant, identity_model=model)
    reporter = _context(tenant, category, index, "reporter", "owner", start)
    subject = _context(tenant, category, index, "subject", "member", start)
    social.register_participant(reporter)
    social.register_participant(subject)
    pairs = []
    steps = ((reporter, "JOIN", None), (subject, "JOIN", None), (reporter, "SPEAK", "safety report"), (subject, "WAIT", None))
    for offset, (context, kind, content) in enumerate(steps, start=1):
        current = start + timedelta(seconds=offset)
        token = _issue(model, context, f"token:safety:setup:{category}:{index:03d}:{offset}", current, ttl)
        decision = social.process_event(token, _social_event(f"event:safety:setup:{category}:{index:03d}:{offset}", room, context, kind, current, content), now=current)
        pairs.append((token, decision))
    role_engine = RolePackEngine(pack_id="consultation", identity_model=model, social_protocol=social)
    role_time = start + timedelta(seconds=10)
    role_token = _issue(model, reporter, f"token:safety:role:{category}:{index:03d}", role_time, ttl)
    role_request = {
        "schema_version": "lumina.role-pack.request.v1",
        "request_id": f"request:safety:role:{category}:{index:03d}",
        "role_pack_id": "consultation",
        "room_id": room,
        "tenant_id": tenant,
        "user_id": reporter["user_id"],
        "session_id": reporter["session_id"],
        "intent": "clarify_request",
        "trigger": None,
        "requested_at": _iso(role_time),
        "payload": {"external_effect_requested": False, "note": "safety intake"},
        "provenance": _copy(reporter["provenance"]),
    }
    role_decision = role_engine.evaluate(role_token, role_request, now=role_time)
    safety = CommunitySafetyEngine(identity_model=model, social_protocol=social)
    return model, social, safety, reporter, subject, role_decision, pairs, role_token


def _incident_request(
    *,
    incident_id: str,
    category: str,
    reporter: Mapping[str, Any],
    subject: Mapping[str, Any],
    room: str,
    occurred: datetime,
    requested: datetime,
    role_decision: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": INCIDENT_REQUEST_SCHEMA,
        "incident_id": incident_id,
        "room_id": room,
        "tenant_id": reporter["tenant_id"],
        "reporter_user_id": reporter["user_id"],
        "reporter_session_id": reporter["session_id"],
        "subject_user_id": subject["user_id"],
        "category": category,
        "severity": CATEGORY_POLICIES[category]["severity"],
        "occurred_at": _iso(occurred),
        "requested_at": _iso(requested),
        "evidence": {
            "signal_type": category,
            "source_sha256": hashlib.sha256(f"{incident_id}:source".encode()).hexdigest(),
            "provenance_id": f"provenance:{incident_id}",
        },
        "role_decision_sha256": role_decision["decision_sha256"],
        "external_effect_requested": False,
        "provenance": _copy(reporter["provenance"]),
    }


def _raises(call: Any, code: str, detail: str | None = None) -> bool:
    try:
        call()
    except CommunitySafetyError as exc:
        return exc.code == code and (detail is None or exc.detail == detail)
    return False


def evaluate_community_safety_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    issuer = fixture["issuer"]["issuer_id"]
    generation = fixture["generation"]
    tenant = generation["tenant_id"]
    start = _time(generation["start_at"], "start_at")
    ttl = fixture["protocol"]["token_ttl_seconds"]

    scenario_results = []
    category_results: dict[str, dict[str, Any]] = {}
    detection_total = detection_pass = 0
    critical_total = critical_detection = critical_containment = 0
    response_seconds: list[int] = []
    cross_attempts = cross_rejected = cross_authorized = 0
    leakage = unauthorized = external = 0
    provenance_total = provenance_pass = 0
    audit_total = audit_pass = 0
    appeal_total = appeal_pass = 0
    attack_ids = []

    for category_offset, category in enumerate(fixture["categories"]):
        policy = CATEGORY_POLICIES[category]
        category_detected = category_contained = 0
        for index in range(generation["scenarios_per_category"]):
            scenario_start = start + timedelta(minutes=category_offset * generation["scenarios_per_category"] + index)
            model, social, engine, reporter, subject, role_decision, social_pairs, role_token = _setup(issuer, tenant, category, index, scenario_start, ttl)
            for token, decision in social_pairs:
                provenance_total += 2
                provenance_pass += int(_provenance_complete(token["provenance"]) and bool(token["context_sha256"]))
                provenance_pass += int(_provenance_complete(decision["provenance"]) and bool(decision["decision_sha256"]))
            provenance_total += 2
            provenance_pass += int(_provenance_complete(role_token["provenance"]) and bool(role_token["context_sha256"]))
            provenance_pass += int(_provenance_complete(role_decision["provenance"]) and bool(role_decision["decision_sha256"]))

            latency = fixture["protocol"]["critical_response_seconds"] if policy["critical"] else fixture["protocol"]["noncritical_response_seconds"]
            occurred = scenario_start + timedelta(seconds=12)
            current = occurred + timedelta(seconds=latency)
            incident_id = f"incident:{category}:{index:03d}"
            request = _incident_request(
                incident_id=incident_id,
                category=category,
                reporter=reporter,
                subject=subject,
                room=social.room_id,
                occurred=occurred,
                requested=current,
                role_decision=role_decision,
            )
            safety_token = _issue(model, reporter, f"token:safety:incident:{category}:{index:03d}", current, ttl)
            provenance_total += 1
            provenance_pass += int(_provenance_complete(safety_token["provenance"]) and bool(safety_token["context_sha256"]))
            decision = engine.assess(safety_token, request, role_decision=role_decision, now=current)
            provenance_total += 1
            provenance_pass += int(_provenance_complete(decision["provenance"]) and bool(decision["decision_sha256"]))
            detected = decision["category"] == category and decision["classification_confidence"] == 1.0
            contained = not policy["critical"] or ("HANDOFF" in decision["response_actions"] and "STOP" in decision["response_actions"])
            detection_total += 1
            detection_pass += int(detected)
            category_detected += int(detected)
            if policy["critical"]:
                critical_total += 1
                critical_detection += int(detected)
                critical_containment += int(contained)
                category_contained += int(contained)
            response_seconds.append(decision["incident_response_seconds"])
            external += int(decision["external_action_executed"])

            attack_token = _issue(model, reporter, f"token:safety:attack:{category}:{index:03d}", current, ttl)
            provenance_total += 1
            provenance_pass += int(_provenance_complete(attack_token["provenance"]) and bool(attack_token["context_sha256"]))
            attack = _copy(request)
            attack["incident_id"] = f"incident:attack:{category}:{index:03d}"
            attack["reporter_user_id"] = subject["user_id"]
            attack["reporter_session_id"] = subject["session_id"]
            attack["subject_user_id"] = reporter["user_id"]
            attack["evidence"]["source_sha256"] = hashlib.sha256(b"FOREIGN_SAFETY_SECRET_63").hexdigest()
            attack_ids.append(attack["incident_id"])
            cross_attempts += 1
            try:
                attack_decision = engine.assess(attack_token, attack, role_decision=role_decision, now=current)
            except CommunitySafetyError as exc:
                cross_rejected += int(exc.code == "SAFETY_REPORTER_TOKEN_MISMATCH")
            else:
                cross_authorized += 1
                unauthorized += 1
                leakage += json.dumps(attack_decision, ensure_ascii=False, sort_keys=True).count("FOREIGN_SAFETY_SECRET_63")

            appeal_ok = True
            if index % 4 == 0:
                appeal_time = current + timedelta(seconds=1)
                appeal_token = _issue(model, subject, f"token:safety:appeal:{category}:{index:03d}", appeal_time, ttl)
                provenance_total += 1
                provenance_pass += int(_provenance_complete(appeal_token["provenance"]) and bool(appeal_token["context_sha256"]))
                appeal_request = {
                    "schema_version": APPEAL_REQUEST_SCHEMA,
                    "appeal_id": f"appeal:{category}:{index:03d}",
                    "incident_id": incident_id,
                    "incident_decision_sha256": decision["decision_sha256"],
                    "room_id": social.room_id,
                    "tenant_id": tenant,
                    "user_id": subject["user_id"],
                    "session_id": subject["session_id"],
                    "grounds_code": "request_review",
                    "requested_at": _iso(appeal_time),
                    "provenance": _copy(subject["provenance"]),
                }
                appeal_decision = engine.appeal(appeal_token, appeal_request, now=appeal_time)
                provenance_total += 1
                provenance_pass += int(_provenance_complete(appeal_decision["provenance"]) and bool(appeal_decision["decision_sha256"]))
                appeal_total += 1
                appeal_ok = (
                    appeal_decision["decision"] == "SIMULATED_REVIEW_REQUIRED"
                    and appeal_decision["original_containment_reversed"] is False
                    and appeal_decision["external_action_executed"] is False
                )
                appeal_pass += int(appeal_ok)
            audit = engine.audit_log()
            audit_total += len(audit)
            audit_pass += len(audit) if validate_audit_log(audit) else 0
            serialized = json.dumps({"decision": decision, "audit": audit}, ensure_ascii=False, sort_keys=True)
            leakage += sum(serialized.count(marker) for marker in fixture["foreign_markers"])
            scenario_results.append({
                "scenario_id": f"scenario:safety:{category}:{index:03d}",
                "category": category,
                "critical": policy["critical"],
                "detected": detected,
                "contained": contained,
                "response_seconds": decision["incident_response_seconds"],
                "cross_user_attack_rejected": cross_rejected == len(attack_ids),
                "appeal_checked": index % 4 == 0,
                "appeal_passed": appeal_ok,
                "audit_entries": len(audit),
                "passed": detected and contained and appeal_ok and validate_audit_log(audit),
            })
        category_results[category] = {
            "scenario_count": generation["scenarios_per_category"],
            "detection_passed": category_detected,
            "critical": policy["critical"],
            "critical_containment_passed": category_contained,
            "actions": list(policy["actions"]),
        }

    # Focused fail-closed controls.
    c_model, c_social, c_engine, c_reporter, c_subject, c_role, _, _ = _setup(issuer, tenant, "harassment", 900, start + timedelta(days=1), ttl)
    c_occurred = start + timedelta(days=1, seconds=12)
    c_now = c_occurred + timedelta(seconds=30)

    def c_request(suffix: str = "base", category: str = "harassment") -> dict[str, Any]:
        return _incident_request(
            incident_id=f"incident:control:{suffix}",
            category=category,
            reporter=c_reporter,
            subject=c_subject,
            room=c_social.room_id,
            occurred=c_occurred,
            requested=c_now,
            role_decision=c_role,
        )

    tamper_token = _issue(c_model, c_reporter, "token:control:tamper", c_now, ttl)
    tamper_token["user_id"] = "user:forged"
    expired_token = c_model.issue_scope_token(
        c_reporter,
        token_id="token:control:expired",
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=c_now - timedelta(seconds=5),
        expires_at=c_now - timedelta(seconds=1),
    )
    severity_token = _issue(c_model, c_reporter, "token:control:severity", c_now, ttl)
    severity_request = c_request("severity")
    severity_request["severity"] = "LOW"
    evidence_token = _issue(c_model, c_reporter, "token:control:evidence", c_now, ttl)
    evidence_request = c_request("evidence")
    evidence_request["evidence"]["signal_type"] = "spam"
    role_token = _issue(c_model, c_reporter, "token:control:role", c_now, ttl)
    tampered_role = _copy(c_role)
    tampered_role["decision"] = "HANDOFF_REQUIRED"
    latency_token = _issue(c_model, c_reporter, "token:control:latency", c_now, ttl)
    latency_request = c_request("latency")
    latency_request["occurred_at"] = _iso(c_now - timedelta(seconds=300))
    external_token = _issue(c_model, c_reporter, "token:control:external", c_now, ttl)
    external_request = c_request("external")
    external_request["external_effect_requested"] = True
    valid_token = _issue(c_model, c_reporter, "token:control:replay", c_now, ttl)
    valid_request = c_request("replay")
    valid_decision = c_engine.assess(valid_token, valid_request, role_decision=c_role, now=c_now)
    replay_request = c_request("replay-second")
    wrong_appeal_token = _issue(c_model, c_reporter, "token:control:wrong-appeal", c_now + timedelta(seconds=1), ttl)
    wrong_appeal = {
        "schema_version": APPEAL_REQUEST_SCHEMA,
        "appeal_id": "appeal:control:wrong-subject",
        "incident_id": valid_decision["incident_id"],
        "incident_decision_sha256": valid_decision["decision_sha256"],
        "room_id": c_social.room_id,
        "tenant_id": tenant,
        "user_id": c_reporter["user_id"],
        "session_id": c_reporter["session_id"],
        "grounds_code": "request_review",
        "requested_at": _iso(c_now + timedelta(seconds=1)),
        "provenance": _copy(c_reporter["provenance"]),
    }
    negative_controls = {
        "identity_token_hmac_tamper_rejected": _raises(
            lambda: c_engine.assess(tamper_token, c_request("tamper"), role_decision=c_role, now=c_now),
            "SAFETY_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_TAMPERED",
        ),
        "identity_token_replay_rejected": _raises(
            lambda: c_engine.assess(valid_token, replay_request, role_decision=c_role, now=c_now),
            "SAFETY_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_REPLAYED",
        ),
        "identity_token_expiry_rejected": _raises(
            lambda: c_engine.assess(expired_token, c_request("expired"), role_decision=c_role, now=c_now),
            "SAFETY_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_EXPIRED",
        ),
        "severity_policy_mismatch_rejected": _raises(
            lambda: c_engine.assess(severity_token, severity_request, role_decision=c_role, now=c_now),
            "SAFETY_SEVERITY_POLICY_MISMATCH",
        ),
        "evidence_category_mismatch_rejected": _raises(
            lambda: c_engine.assess(evidence_token, evidence_request, role_decision=c_role, now=c_now),
            "SAFETY_EVIDENCE_CATEGORY_MISMATCH",
        ),
        "role_decision_tamper_rejected": _raises(
            lambda: c_engine.assess(role_token, c_request("role"), role_decision=tampered_role, now=c_now),
            "SAFETY_ROLE_DECISION_TAMPERED",
        ),
        "five_minute_boundary_rejected": _raises(
            lambda: c_engine.assess(latency_token, latency_request, role_decision=c_role, now=c_now),
            "SAFETY_RESPONSE_WINDOW_EXCEEDED",
        ),
        "external_effect_request_rejected": _raises(
            lambda: c_engine.assess(external_token, external_request, role_decision=c_role, now=c_now),
            "SAFETY_EXTERNAL_EFFECT_PROHIBITED",
        ),
        "appeal_by_non_subject_rejected": _raises(
            lambda: c_engine.appeal(wrong_appeal_token, wrong_appeal, now=c_now + timedelta(seconds=1)),
            "SAFETY_APPEAL_SUBJECT_MISMATCH",
        ),
        "all_cross_user_attacks_rejected": cross_rejected == cross_attempts,
        "critical_detection_complete": critical_detection == critical_total,
        "critical_containment_complete": critical_containment == critical_total,
        "audit_hash_chains_complete": audit_pass == audit_total,
        "appeals_are_review_only": appeal_pass == appeal_total,
    }

    scenario_count = len(scenario_results)
    metrics = {
        "category_count": len(category_results),
        "scenario_count": scenario_count,
        "scenarios_per_category": generation["scenarios_per_category"],
        "classification_checks_passed": detection_pass,
        "classification_checks_total": detection_total,
        "classification_accuracy_percent": 100.0 * detection_pass / detection_total,
        "critical_cases": critical_total,
        "critical_detection_passed": critical_detection,
        "critical_detection_percent": 100.0 * critical_detection / critical_total,
        "critical_containment_passed": critical_containment,
        "critical_containment_percent": 100.0 * critical_containment / critical_total,
        "incident_response_max_seconds": max(response_seconds),
        "incident_response_average_seconds": sum(response_seconds) / len(response_seconds),
        "incident_response_under_five_minutes_percent": 100.0 * sum(item < MAX_RESPONSE_SECONDS for item in response_seconds) / len(response_seconds),
        "appeals_passed": appeal_pass,
        "appeals_total": appeal_total,
        "appeal_pass_percent": 100.0 * appeal_pass / appeal_total,
        "audit_entries_passed": audit_pass,
        "audit_entries_total": audit_total,
        "trace_completeness_percent": 100.0 * audit_pass / audit_total,
        "cross_user_attack_attempts": cross_attempts,
        "cross_user_attacks_rejected": cross_rejected,
        "cross_user_rejection_percent": 100.0 * cross_rejected / cross_attempts,
        "cross_user_authorized_count": cross_authorized,
        "cross_user_leakage_count": leakage,
        "unauthorized_moderation_count": unauthorized,
        "external_actions_executed": external,
        "provenance_checks_passed": provenance_pass,
        "provenance_checks_total": provenance_total,
        "provenance_completeness_percent": 100.0 * provenance_pass / provenance_total,
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_total": len(negative_controls),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    local_candidate = (
        metrics["scenario_count"] >= 160
        and metrics["critical_detection_percent"] == 100.0
        and metrics["critical_containment_percent"] == 100.0
        and metrics["incident_response_max_seconds"] < MAX_RESPONSE_SECONDS
        and metrics["cross_user_rejection_percent"] == 100.0
        and metrics["cross_user_authorized_count"] == 0
        and metrics["cross_user_leakage_count"] == 0
        and metrics["unauthorized_moderation_count"] == 0
        and metrics["external_actions_executed"] == 0
        and metrics["trace_completeness_percent"] == 100.0
        and metrics["provenance_completeness_percent"] == 100.0
        and metrics["negative_controls_percent"] == 100.0
    )
    output = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "fixture_sha256": _sha(fixture),
        "time_evidence": {
            "mode": "deterministic_simulated_time",
            "maximum_incident_response_seconds": metrics["incident_response_max_seconds"],
            "wall_clock_response_claim": "NONE",
        },
        "dependencies": {
            "p6_01_identity_model": MultiUserIdentityModel.__name__,
            "p6_02_social_protocol": SocialInteractionProtocol.__name__,
            "p6_03_role_pack": RolePackEngine.__name__,
            "p4_08_status": "NOT_APPROVED",
            "g4_status": "NOT_APPROVED",
            "g6_status": "NOT_APPROVED",
        },
        "category_policies": {category: get_category_policy(category) for category in fixture["categories"]},
        "category_results": category_results,
        "scenario_results": scenario_results,
        "attack_matrix": {
            "attack_count": cross_attempts,
            "case_ids_sha256": _sha(attack_ids),
            "first_case_id": attack_ids[0],
            "last_case_id": attack_ids[-1],
        },
        "negative_controls": negative_controls,
        "metrics": metrics,
        "wbs_promotion": {
            "p6_04_local_mechanical_candidate": local_candidate,
            "formal_wbs_promotion_allowed": False,
            "p4_08_approved": False,
            "g4_approved": False,
            "g6_approved": False,
            "godot_moderation_integrated": False,
            "live_moderation_complete": False,
            "human_moderation_evaluation_complete": False,
        },
        "claims": {
            "gate_claim": "NONE",
            "live_moderation_claim": "NONE",
            "external_action_claim": "NONE",
        },
    }
    output["evaluation_sha256"] = _sha(output)
    return output


def validate_community_safety_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(value, "SAFETY_EVALUATION_OBJECT_REQUIRED", "evaluation")
    if result.get("schema_version") != EVALUATION_SCHEMA:
        raise CommunitySafetyError("SAFETY_EVALUATION_SCHEMA_MISMATCH", "schema_version")
    expected = _sha({key: _copy(item) for key, item in result.items() if key != "evaluation_sha256"})
    if result.get("evaluation_sha256") != expected:
        raise CommunitySafetyError("SAFETY_EVALUATION_TAMPERED", "evaluation_sha256")
    promotion = result.get("wbs_promotion", {})
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("p4_08_approved") is not False
        or promotion.get("g4_approved") is not False
        or promotion.get("g6_approved") is not False
    ):
        raise CommunitySafetyError("SAFETY_PROMOTION_OVERCLAIM", "P4-08/G4/G6 not approved")
    if result.get("metrics", {}).get("external_actions_executed") != 0:
        raise CommunitySafetyError("SAFETY_EXTERNAL_ACTION_OVERCLAIM", "external action")
    return _copy(dict(result))


__all__ = [
    "APPEAL_DECISION_SCHEMA",
    "APPEAL_REQUEST_SCHEMA",
    "AUDIT_ENTRY_SCHEMA",
    "CATEGORY_IDS",
    "CATEGORY_POLICIES",
    "CommunitySafetyEngine",
    "CommunitySafetyError",
    "EVALUATION_SCHEMA",
    "FIXTURE_SCHEMA",
    "INCIDENT_DECISION_SCHEMA",
    "INCIDENT_REQUEST_SCHEMA",
    "MAX_RESPONSE_SECONDS",
    "POLICY_VERSION",
    "evaluate_community_safety_fixture",
    "get_category_policy",
    "validate_audit_log",
    "validate_community_safety_evaluation",
]
