"""P5-01 isolated Embodied Action API v1 reference.

This module is deliberately incapable of driving Godot, a robot, a socket, or
any other external system.  It accepts only ``LocalFakeEmbodiedAdapter`` and
only permission receipts whose scope says ``LOCAL_FAKE_ADAPTER_ONLY``.  The
reference models the fail-closed boundary that a future, approved P4-08/G4
integration must replace; it is not that approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.permission_modes_v1 import MODES
from lumina_next.risk_policy_v1 import RiskPolicyError, evaluate_action_request
from lumina_next.world_state_v1 import (
    WorldStateContractError,
    validate_world_state_snapshot,
)


REQUEST_SCHEMA = "lumina.embodied.action.request.v1"
PERMISSION_RECEIPT_SCHEMA = "lumina.embodied.action.permission.reference.v1"
RESULT_SCHEMA = "lumina.embodied.action.result.v1"
CANCEL_RESULT_SCHEMA = "lumina.embodied.action.cancel.result.v1"
TRACE_SCHEMA = "lumina.embodied.action.trace.v1"
API_VERSION = "lumina.embodied.action.api.v1"
LOCAL_ADAPTER_SCOPE = "LOCAL_FAKE_ADAPTER_ONLY"
REFERENCE_AUTHORIZATION_BASIS = "LOCAL_TEST_FIXTURE_NOT_HUMAN_APPROVAL"
MAX_DEADLINE_SECONDS = 30.0
MIN_SUBJECT_CONFIDENCE = 0.85
MIN_ACTOR_CONFIDENCE = 0.90
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

TRACE_STAGES = (
    "VALIDATE",
    "IDEMPOTENCY",
    "WORLD_PRECONDITION",
    "RISK_PERMISSION",
    "DISPATCH",
    "ROLLBACK",
    "RESULT",
)

INTENT_SPECS: dict[str, dict[str, Any]] = {
    "look": {
        "target": "user_or_object",
        "parameters": {"duration_ms", "max_turn_degrees"},
    },
    "gesture": {
        "target": "none",
        "parameters": {"name", "duration_ms"},
    },
    "point": {
        "target": "object",
        "parameters": {"duration_ms"},
    },
    "sit": {
        "target": "object",
        "parameters": {"duration_ms"},
    },
    "stand": {
        "target": "none",
        "parameters": {"duration_ms"},
    },
    "approach": {
        "target": "user_or_object",
        "parameters": {"stop_distance_m", "max_travel_distance_m", "speed_mps"},
    },
}

GESTURE_NAMES = {"wave", "nod", "shake_head", "bow", "open_palm"}
MOVEMENT_INTENTS = {"sit", "stand", "approach"}


class EmbodiedActionError(ValueError):
    """Fail-closed error with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EmbodiedActionError("EMBODIED_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise EmbodiedActionError("EMBODIED_ID_INVALID", field_name)
    return value


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EmbodiedActionError("EMBODIED_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmbodiedActionError("EMBODIED_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise EmbodiedActionError("EMBODIED_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _current(value: datetime | None, requested_at: datetime) -> datetime:
    if value is None:
        return requested_at
    if value.tzinfo is None:
        raise EmbodiedActionError("EMBODIED_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise EmbodiedActionError("EMBODIED_NUMBER_INVALID", field_name)
    return float(value)


def _position(record: Mapping[str, Any], field_name: str) -> tuple[float, float, float]:
    value = record.get("position")
    if not isinstance(value, list) or len(value) != 3:
        raise EmbodiedActionError("EMBODIED_POSITION_INVALID", field_name)
    return tuple(
        _number(item, f"{field_name}[{index}]", minimum=-1000.0, maximum=1000.0)
        for index, item in enumerate(value)
    )  # type: ignore[return-value]


def _normalize_intent(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EmbodiedActionError("EMBODIED_INTENT_OBJECT_REQUIRED", "intent")
    unknown = sorted(set(value) - {"type", "target_id", "parameters"})
    if unknown:
        raise EmbodiedActionError("EMBODIED_INTENT_UNKNOWN_FIELDS", ",".join(unknown))
    intent_type = value.get("type")
    if intent_type not in INTENT_SPECS:
        raise EmbodiedActionError("EMBODIED_INTENT_UNKNOWN", str(intent_type))
    spec = INTENT_SPECS[intent_type]
    target_id = value.get("target_id")
    if spec["target"] == "none":
        if target_id is not None:
            raise EmbodiedActionError("EMBODIED_TARGET_FORBIDDEN", intent_type)
    else:
        target_id = _identifier(target_id, "intent.target_id")

    parameters = value.get("parameters")
    if not isinstance(parameters, Mapping):
        raise EmbodiedActionError("EMBODIED_PARAMETERS_OBJECT_REQUIRED", intent_type)
    unknown_parameters = sorted(set(parameters) - spec["parameters"])
    if unknown_parameters:
        raise EmbodiedActionError(
            "EMBODIED_PARAMETERS_UNKNOWN",
            f"{intent_type}:{','.join(unknown_parameters)}",
        )
    normalized_parameters: dict[str, Any]
    if intent_type == "look":
        normalized_parameters = {
            "duration_ms": int(_number(parameters.get("duration_ms"), "duration_ms", minimum=50, maximum=5000)),
            "max_turn_degrees": _number(parameters.get("max_turn_degrees"), "max_turn_degrees", minimum=1, maximum=120),
        }
    elif intent_type == "gesture":
        name = parameters.get("name")
        if name not in GESTURE_NAMES:
            raise EmbodiedActionError("EMBODIED_GESTURE_UNKNOWN", str(name))
        normalized_parameters = {
            "name": name,
            "duration_ms": int(_number(parameters.get("duration_ms"), "duration_ms", minimum=100, maximum=5000)),
        }
    elif intent_type in {"point", "sit", "stand"}:
        normalized_parameters = {
            "duration_ms": int(_number(parameters.get("duration_ms"), "duration_ms", minimum=100, maximum=10000)),
        }
    else:
        normalized_parameters = {
            "stop_distance_m": _number(parameters.get("stop_distance_m"), "stop_distance_m", minimum=0.8, maximum=3.0),
            "max_travel_distance_m": _number(parameters.get("max_travel_distance_m"), "max_travel_distance_m", minimum=0.1, maximum=5.0),
            "speed_mps": _number(parameters.get("speed_mps"), "speed_mps", minimum=0.05, maximum=1.0),
        }
    return {"type": intent_type, "target_id": target_id, "parameters": normalized_parameters}


def validate_embodied_action_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the request envelope without authorizing or dispatching it."""

    if not isinstance(value, Mapping):
        raise EmbodiedActionError("EMBODIED_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "dispatch_id",
        "idempotency_key",
        "correlation_id",
        "tenant_id",
        "user_id",
        "session_id",
        "requested_at",
        "deadline_at",
        "intent",
        "world_state",
        "risk_request",
        "permission_receipt",
        "cancel_requested",
        "rollback",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EmbodiedActionError("EMBODIED_REQUEST_UNKNOWN_FIELDS", ",".join(unknown))
    if value.get("schema_version") != REQUEST_SCHEMA:
        raise EmbodiedActionError("EMBODIED_REQUEST_SCHEMA_MISMATCH", "schema_version")
    requested_at = _parse_time(value.get("requested_at"), "requested_at")
    deadline_at = _parse_time(value.get("deadline_at"), "deadline_at")
    lifetime = (deadline_at - requested_at).total_seconds()
    if not 0 < lifetime <= MAX_DEADLINE_SECONDS:
        raise EmbodiedActionError("EMBODIED_DEADLINE_INVALID", "deadline_at")

    world_state = value.get("world_state")
    risk_request = value.get("risk_request")
    permission_receipt = value.get("permission_receipt")
    rollback = value.get("rollback")
    if not isinstance(world_state, Mapping):
        raise EmbodiedActionError("EMBODIED_WORLD_STATE_REQUIRED", "world_state")
    if not isinstance(risk_request, Mapping):
        raise EmbodiedActionError("EMBODIED_RISK_REQUEST_REQUIRED", "risk_request")
    if not isinstance(permission_receipt, Mapping):
        raise EmbodiedActionError("EMBODIED_PERMISSION_RECEIPT_REQUIRED", "permission_receipt")
    if not isinstance(rollback, Mapping):
        raise EmbodiedActionError("EMBODIED_ROLLBACK_REQUIRED", "rollback")
    rollback_unknown = sorted(set(rollback) - {"required", "strategy", "token"})
    if rollback_unknown:
        raise EmbodiedActionError("EMBODIED_ROLLBACK_UNKNOWN_FIELDS", ",".join(rollback_unknown))
    if rollback.get("required") is not True or rollback.get("strategy") != "restore_prior_pose":
        raise EmbodiedActionError("EMBODIED_ROLLBACK_POLICY_INVALID", "rollback")

    cancel_requested = value.get("cancel_requested")
    if not isinstance(cancel_requested, bool):
        raise EmbodiedActionError("EMBODIED_CANCEL_FLAG_INVALID", "cancel_requested")
    normalized = {
        "schema_version": REQUEST_SCHEMA,
        "dispatch_id": _identifier(value.get("dispatch_id"), "dispatch_id"),
        "idempotency_key": _identifier(value.get("idempotency_key"), "idempotency_key"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": _identifier(value.get("user_id"), "user_id"),
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "requested_at": _iso(requested_at),
        "deadline_at": _iso(deadline_at),
        "intent": _normalize_intent(value.get("intent")),
        "world_state": _copy(world_state),
        "risk_request": _copy(risk_request),
        "permission_receipt": _copy(permission_receipt),
        "cancel_requested": cancel_requested,
        "rollback": {
            "required": True,
            "strategy": "restore_prior_pose",
            "token": _identifier(rollback.get("token"), "rollback.token"),
        },
    }
    _canonical_json(normalized)
    return normalized


def _subject_maps(snapshot: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    users: dict[str, Mapping[str, Any]] = {}
    objects: dict[str, Mapping[str, Any]] = {}
    for record in snapshot["users"]:
        subject_id = record["user_id"]
        if subject_id in users:
            raise EmbodiedActionError("EMBODIED_DUPLICATE_WORLD_SUBJECT", subject_id)
        users[subject_id] = record
    for record in snapshot["objects"]:
        subject_id = record["object_id"]
        if subject_id in objects or subject_id in users:
            raise EmbodiedActionError("EMBODIED_DUPLICATE_WORLD_SUBJECT", subject_id)
        objects[subject_id] = record
    return users, objects


def _validate_world_preconditions(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    try:
        snapshot = validate_world_state_snapshot(request["world_state"], now=now)
    except WorldStateContractError as exc:
        raise EmbodiedActionError("EMBODIED_WORLD_INVALID", exc.code) from exc
    if snapshot["tenant_id"] != request["tenant_id"]:
        raise EmbodiedActionError("EMBODIED_WORLD_TENANT_MISMATCH", snapshot["tenant_id"])
    if snapshot["freshness"]["is_stale"]:
        raise EmbodiedActionError("EMBODIED_WORLD_STALE", snapshot["snapshot_id"])

    environment = snapshot.get("environment")
    if not isinstance(environment, Mapping):
        raise EmbodiedActionError("EMBODIED_ENVIRONMENT_REQUIRED", "environment")
    required_environment = {"kill_switch_engaged", "privacy_safe", "navigation_safe", "collision_clear"}
    if set(environment) != required_environment:
        raise EmbodiedActionError("EMBODIED_ENVIRONMENT_FIELDS_INVALID", "environment")
    if environment["kill_switch_engaged"] is not False:
        raise EmbodiedActionError("EMBODIED_KILL_SWITCH_ENGAGED", "world_state")
    if environment["privacy_safe"] is not True:
        raise EmbodiedActionError("EMBODIED_PRIVACY_PRECONDITION_FAILED", "world_state")

    users, objects = _subject_maps(snapshot)
    actor = users.get(request["user_id"])
    if actor is None:
        raise EmbodiedActionError("EMBODIED_ACTOR_NOT_FOUND", request["user_id"])
    if actor.get("stale") is True or float(actor.get("confidence", -1)) < MIN_ACTOR_CONFIDENCE:
        raise EmbodiedActionError("EMBODIED_ACTOR_UNCERTAIN", request["user_id"])
    actor_position = _position(actor, "actor.position")
    posture = actor.get("posture")
    if posture not in {"standing", "seated"}:
        raise EmbodiedActionError("EMBODIED_ACTOR_POSTURE_UNKNOWN", str(posture))

    intent = request["intent"]
    intent_type = intent["type"]
    target_id = intent["target_id"]
    target: Mapping[str, Any] | None = None
    target_kind: str | None = None
    if target_id is not None:
        if target_id in users:
            target, target_kind = users[target_id], "user"
        elif target_id in objects:
            target, target_kind = objects[target_id], "object"
        else:
            raise EmbodiedActionError("EMBODIED_TARGET_NOT_FOUND", target_id)
        expected = INTENT_SPECS[intent_type]["target"]
        if expected == "object" and target_kind != "object":
            raise EmbodiedActionError("EMBODIED_TARGET_KIND_INVALID", f"{intent_type}:{target_kind}")
        if target.get("stale") is True or float(target.get("confidence", -1)) < MIN_SUBJECT_CONFIDENCE:
            raise EmbodiedActionError("EMBODIED_TARGET_UNCERTAIN", target_id)

    if intent_type in {"look", "point"} and target is not None and target.get("visible") is not True:
        raise EmbodiedActionError("EMBODIED_TARGET_NOT_VISIBLE", target_id or "target")
    if intent_type in MOVEMENT_INTENTS:
        if environment["navigation_safe"] is not True or environment["collision_clear"] is not True:
            raise EmbodiedActionError("EMBODIED_NAVIGATION_PRECONDITION_FAILED", intent_type)
    if intent_type == "sit":
        assert target is not None
        capabilities = target.get("capabilities")
        if (
            posture != "standing"
            or not isinstance(capabilities, list)
            or "sit" not in capabilities
            or target.get("object_type") not in {"chair", "seat"}
            or target.get("occupied") is not False
            or target.get("reachable") is not True
        ):
            raise EmbodiedActionError("EMBODIED_SIT_PRECONDITION_FAILED", target_id or "target")
    if intent_type == "stand" and posture != "seated":
        raise EmbodiedActionError("EMBODIED_STAND_PRECONDITION_FAILED", posture)
    if intent_type == "approach":
        assert target is not None
        if target.get("reachable") is not True:
            raise EmbodiedActionError("EMBODIED_TARGET_UNREACHABLE", target_id or "target")
        target_position = _position(target, "target.position")
        distance = math.dist(actor_position, target_position)
        params = intent["parameters"]
        if distance > params["max_travel_distance_m"]:
            raise EmbodiedActionError("EMBODIED_APPROACH_DISTANCE_EXCEEDED", target_id or "target")
        boundary = target.get("minimum_approach_distance_m", 0.8)
        boundary_value = _number(boundary, "minimum_approach_distance_m", minimum=0.8, maximum=3.0)
        if params["stop_distance_m"] < boundary_value:
            raise EmbodiedActionError("EMBODIED_APPROACH_BOUNDARY_VIOLATION", target_id or "target")

    return {
        "snapshot_id": snapshot["snapshot_id"],
        "actor_posture": posture,
        "target_id": target_id,
        "target_kind": target_kind,
        "fresh": True,
    }


def _validate_permission_receipt(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    receipt = request["permission_receipt"]
    allowed = {
        "schema_version",
        "receipt_id",
        "decision",
        "scope",
        "issuer",
        "authorization_basis",
        "permission_mode",
        "tenant_id",
        "user_id",
        "session_id",
        "dispatch_id",
        "correlation_id",
        "idempotency_key",
        "intent_type",
        "target_id",
        "issued_at",
        "expires_at",
        "one_time",
        "p4_08_approved",
        "g4_approved",
    }
    unknown = sorted(set(receipt) - allowed)
    if unknown:
        raise EmbodiedActionError("EMBODIED_PERMISSION_UNKNOWN_FIELDS", ",".join(unknown))
    if receipt.get("schema_version") != PERMISSION_RECEIPT_SCHEMA:
        raise EmbodiedActionError("EMBODIED_PERMISSION_SCHEMA_MISMATCH", "schema_version")
    receipt_id = _identifier(receipt.get("receipt_id"), "permission_receipt.receipt_id")
    fixed = {
        "decision": "REFERENCE_ALLOW",
        "scope": LOCAL_ADAPTER_SCOPE,
        "issuer": "local_fixture_authority",
        "authorization_basis": REFERENCE_AUTHORIZATION_BASIS,
        "p4_08_approved": False,
        "g4_approved": False,
        "one_time": True,
    }
    for field_name, expected in fixed.items():
        if receipt.get(field_name) != expected:
            raise EmbodiedActionError("EMBODIED_PERMISSION_REFERENCE_SCOPE_INVALID", field_name)
    if receipt.get("permission_mode") not in MODES or receipt.get("permission_mode") == "PROPOSE_ONLY":
        raise EmbodiedActionError("EMBODIED_PERMISSION_MODE_BLOCKED", str(receipt.get("permission_mode")))
    bindings = {
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "session_id": request["session_id"],
        "dispatch_id": request["dispatch_id"],
        "correlation_id": request["correlation_id"],
        "idempotency_key": request["idempotency_key"],
        "intent_type": request["intent"]["type"],
        "target_id": request["intent"]["target_id"],
    }
    for field_name, expected in bindings.items():
        if receipt.get(field_name) != expected:
            raise EmbodiedActionError("EMBODIED_PERMISSION_BINDING_MISMATCH", field_name)
    issued_at = _parse_time(receipt.get("issued_at"), "permission_receipt.issued_at")
    expires_at = _parse_time(receipt.get("expires_at"), "permission_receipt.expires_at")
    if issued_at > now or expires_at <= now or expires_at <= issued_at:
        raise EmbodiedActionError("EMBODIED_PERMISSION_EXPIRED_OR_FUTURE", receipt_id)
    return {
        "receipt_id": receipt_id,
        "permission_mode": receipt["permission_mode"],
        "scope": LOCAL_ADAPTER_SCOPE,
        "p4_08_approved": False,
        "g4_approved": False,
    }


def _validate_risk_and_permission(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    risk_request = request["risk_request"]
    intent = request["intent"]
    target_scope = intent["target_id"] or "self"
    expected = {
        "request_id": f"risk:{request['dispatch_id']}",
        "correlation_id": request["correlation_id"],
        "user_id": request["user_id"],
        "action_type": f"embodied_{intent['type']}",
        "effect_class": "write_local_reversible",
        "target_scope": target_scope,
        "requested_at": request["requested_at"],
        "rollback_ref": request["rollback"]["token"],
    }
    for field_name, expected_value in expected.items():
        if risk_request.get(field_name) != expected_value:
            raise EmbodiedActionError("EMBODIED_RISK_BINDING_MISMATCH", field_name)
    payload = risk_request.get("payload")
    if not isinstance(payload, Mapping) or payload.get("reference_only") is not True:
        raise EmbodiedActionError("EMBODIED_RISK_REFERENCE_SCOPE_REQUIRED", "payload.reference_only")
    if payload.get("dispatch_id") != request["dispatch_id"] or payload.get("intent_type") != intent["type"]:
        raise EmbodiedActionError("EMBODIED_RISK_PAYLOAD_BINDING_MISMATCH", "payload")
    try:
        risk_decision = evaluate_action_request(risk_request, now=now)
    except RiskPolicyError as exc:
        raise EmbodiedActionError("EMBODIED_RISK_REQUEST_INVALID", exc.code) from exc
    if not risk_decision["execution_permitted"] or risk_decision["risk_level"] != "MEDIUM":
        raise EmbodiedActionError("EMBODIED_RISK_NOT_PERMITTED", risk_decision["reason"])
    permission = _validate_permission_receipt(request, now=now)
    return {
        "risk_level": risk_decision["risk_level"],
        "risk_decision_sha256": risk_decision["decision_sha256"],
        "permission_receipt_id": permission["receipt_id"],
        "permission_mode": permission["permission_mode"],
        "adapter_scope": LOCAL_ADAPTER_SCOPE,
    }


class LocalFakeEmbodiedAdapter:
    """In-memory fake with no imports or handles capable of external effects."""

    def __init__(
        self,
        *,
        fail_after_apply_dispatch_ids: set[str] | None = None,
        timeout_after_apply_dispatch_ids: set[str] | None = None,
        pending_dispatch_ids: set[str] | None = None,
    ) -> None:
        self.fail_after_apply_dispatch_ids = set(fail_after_apply_dispatch_ids or ())
        self.timeout_after_apply_dispatch_ids = set(timeout_after_apply_dispatch_ids or ())
        self.pending_dispatch_ids = set(pending_dispatch_ids or ())
        self.invocations: list[dict[str, Any]] = []
        self.effects: dict[str, dict[str, Any]] = {}
        self.handles: dict[str, str] = {}
        self.duplicate_side_effects = 0
        self.rollback_calls = 0
        self.cancel_calls = 0

    def dispatch(self, command: Mapping[str, Any]) -> dict[str, Any]:
        if command.get("adapter_scope") != LOCAL_ADAPTER_SCOPE:
            raise EmbodiedActionError("EMBODIED_ADAPTER_SCOPE_INVALID", "command")
        record = _copy(command)
        self.invocations.append(record)
        effect_key = f"{command['tenant_id']}|{command['user_id']}|{command['idempotency_key']}"
        if effect_key in self.effects:
            self.duplicate_side_effects += 1
            return {
                "status": "DUPLICATE_REJECTED",
                "handle_id": self.effects[effect_key]["handle_id"],
                "mutation_started": False,
                "external_execution": False,
                "godot_action_fired": False,
            }
        handle_id = f"fake-handle:{command['dispatch_id']}"
        self.effects[effect_key] = {
            "handle_id": handle_id,
            "dispatch_id": command["dispatch_id"],
            "correlation_id": command["correlation_id"],
            "user_id": command["user_id"],
            "state": "APPLIED_IN_MEMORY_ONLY",
        }
        self.handles[handle_id] = effect_key
        dispatch_id = command["dispatch_id"]
        status = "COMPLETED"
        if dispatch_id in self.fail_after_apply_dispatch_ids:
            status = "FAILED_AFTER_APPLY"
        elif dispatch_id in self.timeout_after_apply_dispatch_ids:
            status = "TIMED_OUT_AFTER_APPLY"
        elif dispatch_id in self.pending_dispatch_ids:
            status = "PENDING_AFTER_APPLY"
        return {
            "status": status,
            "handle_id": handle_id,
            "mutation_started": True,
            "external_execution": False,
            "godot_action_fired": False,
        }

    def rollback(self, *, handle_id: str, rollback_token: str) -> dict[str, Any]:
        _identifier(rollback_token, "rollback_token")
        effect_key = self.handles.get(handle_id)
        if effect_key is None:
            return {"status": "NOT_FOUND", "external_execution": False}
        effect = self.effects[effect_key]
        if effect["state"] == "ROLLED_BACK":
            return {"status": "ALREADY_ROLLED_BACK", "external_execution": False}
        effect["state"] = "ROLLED_BACK"
        self.rollback_calls += 1
        return {"status": "ROLLED_BACK", "external_execution": False}

    def cancel(self, *, handle_id: str) -> dict[str, Any]:
        effect_key = self.handles.get(handle_id)
        if effect_key is None:
            return {"status": "NOT_FOUND", "external_execution": False}
        effect = self.effects[effect_key]
        if effect["state"] in {"CANCELLED", "ROLLED_BACK"}:
            return {"status": "ALREADY_TERMINAL", "external_execution": False}
        effect["state"] = "CANCELLED"
        self.cancel_calls += 1
        return {"status": "CANCELLED", "external_execution": False}


def _safe_identifier(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and _ID_PATTERN.fullmatch(value) else fallback


def _base_envelope(value: Any) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    intent = source.get("intent") if isinstance(source.get("intent"), Mapping) else {}
    return {
        "dispatch_id": _safe_identifier(source.get("dispatch_id"), "invalid-dispatch"),
        "correlation_id": _safe_identifier(source.get("correlation_id"), "invalid-correlation"),
        "tenant_id": _safe_identifier(source.get("tenant_id"), "invalid-tenant"),
        "user_id": _safe_identifier(source.get("user_id"), "invalid-user"),
        "intent_type": intent.get("type") if intent.get("type") in INTENT_SPECS else "invalid-intent",
        "target_id": _safe_identifier(intent.get("target_id"), "no-target") if intent.get("target_id") is not None else "no-target",
    }


def _new_outcomes() -> dict[str, dict[str, Any]]:
    return {stage: {"outcome": "SKIPPED", "reason": "not_reached"} for stage in TRACE_STAGES}


def _trace(base: Mapping[str, str], outcomes: Mapping[str, Mapping[str, Any]], occurred_at: str) -> list[dict[str, Any]]:
    events = []
    for sequence, stage in enumerate(TRACE_STAGES, start=1):
        event = {
            "schema_version": TRACE_SCHEMA,
            "trace_id": f"trace:{base['dispatch_id']}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "outcome": outcomes[stage]["outcome"],
            "reason": outcomes[stage]["reason"],
            "dispatch_id": base["dispatch_id"],
            "correlation_id": base["correlation_id"],
            "user_id": base["user_id"],
            "occurred_at": occurred_at,
            "detail": _copy(outcomes[stage].get("detail", {})),
        }
        event["trace_sha256"] = _sha256(event)
        events.append(event)
    return events


def _finish(
    request: Mapping[str, Any] | None,
    base: Mapping[str, str],
    outcomes: dict[str, dict[str, Any]],
    *,
    status: str,
    reason: str,
    fake_adapter_invoked: bool = False,
    handle_id: str | None = None,
    rollback_performed: bool = False,
    rollback_status: str = "NOT_NEEDED",
    permission_receipt_id: str | None = None,
    risk_level: str | None = None,
    duplicate_of_result_sha256: str | None = None,
) -> dict[str, Any]:
    outcomes["RESULT"] = {"outcome": "TERMINAL", "reason": reason, "detail": {"status": status}}
    occurred_at = request["requested_at"] if request is not None else "1970-01-01T00:00:00Z"
    result = {
        "schema_version": RESULT_SCHEMA,
        "api_version": API_VERSION,
        "dispatch_id": base["dispatch_id"],
        "correlation_id": base["correlation_id"],
        "tenant_id": base["tenant_id"],
        "user_id": base["user_id"],
        "intent_type": base["intent_type"],
        "target_id": None if base["target_id"] == "no-target" else base["target_id"],
        "status": status,
        "reason": reason,
        "fake_adapter_invoked": fake_adapter_invoked,
        "adapter_scope": LOCAL_ADAPTER_SCOPE,
        "adapter_handle_id": handle_id,
        "external_execution": False,
        "godot_action_fired": False,
        "rollback": {
            "required": True,
            "performed": rollback_performed,
            "status": rollback_status,
        },
        "permission_receipt_id": permission_receipt_id,
        "risk_level": risk_level,
        "p4_08_approved": False,
        "g4_approved": False,
        "duplicate_of_result_sha256": duplicate_of_result_sha256,
        "trace": _trace(base, outcomes, occurred_at),
    }
    result["result_sha256"] = _sha256(result)
    return result


class EmbodiedActionService:
    """Synchronous fail-closed dispatcher restricted to the local fake adapter."""

    def __init__(self, adapter: LocalFakeEmbodiedAdapter) -> None:
        if type(adapter) is not LocalFakeEmbodiedAdapter:
            raise EmbodiedActionError("EMBODIED_REAL_ADAPTER_FORBIDDEN", type(adapter).__name__)
        self.adapter = adapter
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._dispatch_owners: dict[str, tuple[str, str, str]] = {}
        self._receipt_uses: dict[str, str] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    def dispatch(self, value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        base = _base_envelope(value)
        outcomes = _new_outcomes()
        try:
            request = validate_embodied_action_request(value)
            base = _base_envelope(request)
            outcomes["VALIDATE"] = {"outcome": "PASS", "reason": "request_contract_valid"}
        except EmbodiedActionError as exc:
            outcomes["VALIDATE"] = {"outcome": "BLOCK", "reason": exc.code}
            return _finish(None, base, outcomes, status="BLOCKED", reason=exc.code)

        requested_at = _parse_time(request["requested_at"], "requested_at")
        current = _current(now, requested_at)
        if current >= _parse_time(request["deadline_at"], "deadline_at"):
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "deadline_expired"}
            return _finish(request, base, outcomes, status="TIMED_OUT", reason="deadline_expired")
        if request["cancel_requested"]:
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "cancelled_before_dispatch"}
            return _finish(request, base, outcomes, status="CANCELLED", reason="cancelled_before_dispatch")

        request_hash = _sha256(request)
        scope_key = (request["tenant_id"], request["user_id"], request["idempotency_key"])
        owner = self._dispatch_owners.get(request["dispatch_id"])
        if owner is not None and owner != scope_key:
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "dispatch_scope_conflict"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="dispatch_scope_conflict")
        prior = self._idempotency.get(scope_key)
        if prior is not None:
            if prior["request_sha256"] == request_hash:
                outcomes["IDEMPOTENCY"] = {"outcome": "DUPLICATE", "reason": "identical_retry_suppressed"}
                outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "duplicate_side_effect_suppressed"}
                return _finish(
                    request,
                    base,
                    outcomes,
                    status="DUPLICATE",
                    reason="identical_retry_suppressed",
                    duplicate_of_result_sha256=prior.get("result_sha256"),
                )
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "idempotency_key_conflict"}
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "conflicting_retry_suppressed"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="idempotency_key_conflict")
        outcomes["IDEMPOTENCY"] = {"outcome": "PASS", "reason": "new_scoped_key"}

        try:
            world_detail = _validate_world_preconditions(request, now=current)
            outcomes["WORLD_PRECONDITION"] = {
                "outcome": "PASS",
                "reason": "world_preconditions_satisfied",
                "detail": world_detail,
            }
        except EmbodiedActionError as exc:
            outcomes["WORLD_PRECONDITION"] = {"outcome": "BLOCK", "reason": exc.code}
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "world_precondition_failed"}
            return _finish(request, base, outcomes, status="BLOCKED", reason=exc.code)

        try:
            authorization = _validate_risk_and_permission(request, now=current)
            receipt_id = authorization["permission_receipt_id"]
            if receipt_id in self._receipt_uses:
                raise EmbodiedActionError("EMBODIED_PERMISSION_RECEIPT_REPLAY", receipt_id)
            outcomes["RISK_PERMISSION"] = {
                "outcome": "PASS",
                "reason": "reference_only_authorized",
                "detail": authorization,
            }
        except EmbodiedActionError as exc:
            outcomes["RISK_PERMISSION"] = {"outcome": "BLOCK", "reason": exc.code}
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "risk_or_permission_failed"}
            return _finish(request, base, outcomes, status="BLOCKED", reason=exc.code)

        command = {
            "adapter_scope": LOCAL_ADAPTER_SCOPE,
            "dispatch_id": request["dispatch_id"],
            "idempotency_key": request["idempotency_key"],
            "correlation_id": request["correlation_id"],
            "tenant_id": request["tenant_id"],
            "user_id": request["user_id"],
            "intent_type": request["intent"]["type"],
            "target_id": request["intent"]["target_id"],
            "parameters": _copy(request["intent"]["parameters"]),
            "rollback_token": request["rollback"]["token"],
        }
        self._dispatch_owners[request["dispatch_id"]] = scope_key
        self._receipt_uses[authorization["permission_receipt_id"]] = request["dispatch_id"]
        self._idempotency[scope_key] = {
            "request_sha256": request_hash,
            "dispatch_id": request["dispatch_id"],
            "result_sha256": None,
        }
        adapter_result = self.adapter.dispatch(command)
        adapter_status = adapter_result["status"]
        handle_id = adapter_result["handle_id"]
        outcomes["DISPATCH"] = {
            "outcome": "CALLED_FAKE_ONLY",
            "reason": adapter_status.lower(),
            "detail": {"handle_id": handle_id, "external_execution": False, "godot_action_fired": False},
        }
        permission_receipt_id = authorization["permission_receipt_id"]
        risk_level = authorization["risk_level"]

        if adapter_status == "COMPLETED":
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "not_needed"}
            result = _finish(
                request,
                base,
                outcomes,
                status="COMPLETED",
                reason="local_fake_dispatch_completed",
                fake_adapter_invoked=True,
                handle_id=handle_id,
                permission_receipt_id=permission_receipt_id,
                risk_level=risk_level,
            )
        elif adapter_status == "PENDING_AFTER_APPLY":
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "awaiting_cancel_or_completion"}
            result = _finish(
                request,
                base,
                outcomes,
                status="PENDING",
                reason="local_fake_dispatch_pending",
                fake_adapter_invoked=True,
                handle_id=handle_id,
                rollback_status="PENDING",
                permission_receipt_id=permission_receipt_id,
                risk_level=risk_level,
            )
            self._pending[request["dispatch_id"]] = {
                "tenant_id": request["tenant_id"],
                "user_id": request["user_id"],
                "correlation_id": request["correlation_id"],
                "handle_id": handle_id,
                "rollback_token": request["rollback"]["token"],
            }
        elif adapter_status in {"FAILED_AFTER_APPLY", "TIMED_OUT_AFTER_APPLY"}:
            rollback = self.adapter.rollback(
                handle_id=handle_id,
                rollback_token=request["rollback"]["token"],
            )
            rollback_ok = rollback["status"] in {"ROLLED_BACK", "ALREADY_ROLLED_BACK"}
            outcomes["ROLLBACK"] = {
                "outcome": "PASS" if rollback_ok else "FAIL",
                "reason": rollback["status"].lower(),
            }
            result = _finish(
                request,
                base,
                outcomes,
                status="TIMED_OUT_ROLLED_BACK" if adapter_status.startswith("TIMED") else "FAILED_ROLLED_BACK",
                reason=adapter_status.lower(),
                fake_adapter_invoked=True,
                handle_id=handle_id,
                rollback_performed=rollback_ok,
                rollback_status=rollback["status"],
                permission_receipt_id=permission_receipt_id,
                risk_level=risk_level,
            )
        else:
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "no_mutation_started"}
            result = _finish(
                request,
                base,
                outcomes,
                status="BLOCKED",
                reason="fake_adapter_duplicate_rejected",
                fake_adapter_invoked=True,
                handle_id=handle_id,
                permission_receipt_id=permission_receipt_id,
                risk_level=risk_level,
            )
        self._idempotency[scope_key]["result_sha256"] = result["result_sha256"]
        return result

    def cancel(
        self,
        *,
        dispatch_id: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Cancel only a matching pending fake dispatch, then roll it back."""

        dispatch = _identifier(dispatch_id, "dispatch_id")
        tenant = _identifier(tenant_id, "tenant_id")
        user = _identifier(user_id, "user_id")
        correlation = _identifier(correlation_id, "correlation_id")
        pending = self._pending.get(dispatch)
        status = "BLOCKED_NOT_FOUND"
        fake_cancelled = False
        rollback_performed = False
        if pending is not None:
            if (pending["tenant_id"], pending["user_id"], pending["correlation_id"]) != (tenant, user, correlation):
                status = "BLOCKED_SCOPE_MISMATCH"
            else:
                cancel_result = self.adapter.cancel(handle_id=pending["handle_id"])
                rollback_result = self.adapter.rollback(
                    handle_id=pending["handle_id"],
                    rollback_token=pending["rollback_token"],
                )
                fake_cancelled = cancel_result["status"] in {"CANCELLED", "ALREADY_TERMINAL"}
                rollback_performed = rollback_result["status"] in {"ROLLED_BACK", "ALREADY_ROLLED_BACK"}
                status = "CANCELLED_ROLLED_BACK" if fake_cancelled and rollback_performed else "CANCEL_FAILED_CLOSED"
                self._pending.pop(dispatch, None)
        result = {
            "schema_version": CANCEL_RESULT_SCHEMA,
            "dispatch_id": dispatch,
            "tenant_id": tenant,
            "user_id": user,
            "correlation_id": correlation,
            "status": status,
            "fake_adapter_cancelled": fake_cancelled,
            "rollback_performed": rollback_performed,
            "external_execution": False,
            "godot_action_fired": False,
            "p4_08_approved": False,
            "g4_approved": False,
        }
        result["cancel_sha256"] = _sha256(result)
        return result

