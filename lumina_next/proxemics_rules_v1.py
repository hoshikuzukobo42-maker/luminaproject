"""P5-04 isolated Proxemics Rules v1 reference.

User-scoped, provenance-bound distance and gaze preferences are evaluated
before any P5-03 motion request.  The only downstream effects remain the exact
local fake graph from P5-01/02/03; P4-08, G4, and G5 stay unapproved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.motion_semantics_v1 import (
    MotionSemanticsError,
    MotionSemanticsService,
    validate_motion_request,
)
from lumina_next.world_state_v1 import WorldStateContractError, validate_world_state_snapshot


REQUEST_SCHEMA = "lumina.proxemics.request.v1"
RESULT_SCHEMA = "lumina.proxemics.result.v1"
CANCEL_RESULT_SCHEMA = "lumina.proxemics.cancel.result.v1"
TRACE_SCHEMA = "lumina.proxemics.trace.v1"
RULESET_VERSION = "lumina.proxemics.rules.v1"
CONTACT_ZONE_MAX_M = 0.45
PERSONAL_ZONE_MAX_M = 1.20
SOCIAL_ZONE_MAX_M = 3.60
MAX_DEADLINE_SECONDS = 30.0
MAX_BOUNDARY_AGE_SECONDS = 86400.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

TRACE_STAGES = (
    "VALIDATE",
    "IDEMPOTENCY",
    "WORLD_BOUNDARY",
    "DISTANCE_ZONE",
    "COMFORT_DECISION",
    "RISK_PERMISSION",
    "MOTION_DISPATCH",
    "ROLLBACK",
    "RESULT",
)

BEHAVIOR_TO_SEMANTIC = {
    "approach": "approach",
    "retreat": "walk",
    "gaze": "look",
    "seat": "sit_down",
}


class ProxemicsRuleError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProxemicsRuleError("PROX_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ProxemicsRuleError("PROX_ID_INVALID", field_name)
    return value


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ProxemicsRuleError("PROX_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProxemicsRuleError("PROX_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise ProxemicsRuleError("PROX_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _current(value: datetime | None, requested_at: datetime) -> datetime:
    if value is None:
        return requested_at
    if value.tzinfo is None:
        raise ProxemicsRuleError("PROX_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _number(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise ProxemicsRuleError("PROX_NUMBER_INVALID", field_name)
    return float(value)


def _position(record: Mapping[str, Any], field_name: str) -> tuple[float, float, float]:
    value = record.get("position")
    if not isinstance(value, list) or len(value) != 3:
        raise ProxemicsRuleError("PROX_POSITION_INVALID", field_name)
    return tuple(_number(item, field_name, -1000.0, 1000.0) for item in value)  # type: ignore[return-value]


def _behavior(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "type",
        "desired_distance_m",
        "gaze_duration_ms",
        "seat_id",
        "retreat_destination_id",
    }:
        raise ProxemicsRuleError("PROX_BEHAVIOR_FIELDS_INVALID", "behavior")
    behavior_type = value.get("type")
    if behavior_type not in BEHAVIOR_TO_SEMANTIC:
        raise ProxemicsRuleError("PROX_BEHAVIOR_UNKNOWN", str(behavior_type))
    desired = value.get("desired_distance_m")
    gaze_duration = value.get("gaze_duration_ms")
    seat_id = value.get("seat_id")
    retreat_id = value.get("retreat_destination_id")
    if behavior_type in {"approach", "retreat"}:
        desired = _number(desired, "behavior.desired_distance_m", 0.0, 10.0)
    elif desired is not None:
        raise ProxemicsRuleError("PROX_DISTANCE_FORBIDDEN", behavior_type)
    if behavior_type == "gaze":
        gaze_duration = int(_number(gaze_duration, "behavior.gaze_duration_ms", 100, 30000))
    elif gaze_duration is not None:
        raise ProxemicsRuleError("PROX_GAZE_DURATION_FORBIDDEN", behavior_type)
    if behavior_type == "seat":
        seat_id = _identifier(seat_id, "behavior.seat_id")
    elif seat_id is not None:
        raise ProxemicsRuleError("PROX_SEAT_FORBIDDEN", behavior_type)
    if behavior_type == "retreat":
        retreat_id = _identifier(retreat_id, "behavior.retreat_destination_id")
    elif retreat_id is not None:
        raise ProxemicsRuleError("PROX_RETREAT_DESTINATION_FORBIDDEN", behavior_type)
    return {
        "type": behavior_type,
        "desired_distance_m": desired,
        "gaze_duration_ms": gaze_duration,
        "seat_id": seat_id,
        "retreat_destination_id": retreat_id,
    }


def validate_proxemics_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProxemicsRuleError("PROX_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "proxemics_id",
        "idempotency_key",
        "correlation_id",
        "tenant_id",
        "user_id",
        "target_user_id",
        "session_id",
        "requested_at",
        "deadline_at",
        "behavior",
        "world_state",
        "motion_request",
        "cancel_requested",
        "rollback",
        "p4_08_approved",
        "g4_approved",
        "g5_approved",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProxemicsRuleError("PROX_REQUEST_UNKNOWN_FIELDS", ",".join(unknown))
    if value.get("schema_version") != REQUEST_SCHEMA:
        raise ProxemicsRuleError("PROX_REQUEST_SCHEMA_MISMATCH", "schema_version")
    requested_at = _parse_time(value.get("requested_at"), "requested_at")
    deadline_at = _parse_time(value.get("deadline_at"), "deadline_at")
    if not 0 < (deadline_at - requested_at).total_seconds() <= MAX_DEADLINE_SECONDS:
        raise ProxemicsRuleError("PROX_DEADLINE_INVALID", "deadline_at")
    if any(value.get(field) is not False for field in ("p4_08_approved", "g4_approved", "g5_approved")):
        raise ProxemicsRuleError("PROX_UNAPPROVED_GATE_CLAIM", "P4-08/G4/G5")
    if not isinstance(value.get("world_state"), Mapping):
        raise ProxemicsRuleError("PROX_WORLD_STATE_REQUIRED", "world_state")
    if not isinstance(value.get("motion_request"), Mapping):
        raise ProxemicsRuleError("PROX_MOTION_REQUEST_REQUIRED", "motion_request")
    if not isinstance(value.get("cancel_requested"), bool):
        raise ProxemicsRuleError("PROX_CANCEL_FLAG_INVALID", "cancel_requested")
    rollback = value.get("rollback")
    if not isinstance(rollback, Mapping) or set(rollback) != {"required", "token"} or rollback.get("required") is not True:
        raise ProxemicsRuleError("PROX_ROLLBACK_INVALID", "rollback")
    user_id = _identifier(value.get("user_id"), "user_id")
    target_user_id = _identifier(value.get("target_user_id"), "target_user_id")
    if user_id == target_user_id:
        raise ProxemicsRuleError("PROX_SELF_TARGET_FORBIDDEN", user_id)
    normalized = {
        "schema_version": REQUEST_SCHEMA,
        "proxemics_id": _identifier(value.get("proxemics_id"), "proxemics_id"),
        "idempotency_key": _identifier(value.get("idempotency_key"), "idempotency_key"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": user_id,
        "target_user_id": target_user_id,
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "requested_at": _iso(requested_at),
        "deadline_at": _iso(deadline_at),
        "behavior": _behavior(value.get("behavior")),
        "world_state": _copy(value["world_state"]),
        "motion_request": _copy(value["motion_request"]),
        "cancel_requested": value["cancel_requested"],
        "rollback": {"required": True, "token": _identifier(rollback.get("token"), "rollback.token")},
        "p4_08_approved": False,
        "g4_approved": False,
        "g5_approved": False,
    }
    _canonical_json(normalized)
    return normalized


def classify_distance_zone(distance_m: float) -> str:
    if distance_m < CONTACT_ZONE_MAX_M:
        return "CONTACT"
    if distance_m < PERSONAL_ZONE_MAX_M:
        return "PERSONAL"
    if distance_m < SOCIAL_ZONE_MAX_M:
        return "SOCIAL"
    return "PUBLIC"


def _boundary_and_world(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    try:
        snapshot = validate_world_state_snapshot(request["world_state"], now=now)
    except WorldStateContractError as exc:
        raise ProxemicsRuleError("PROX_WORLD_INVALID", exc.code) from exc
    if snapshot["tenant_id"] != request["tenant_id"]:
        raise ProxemicsRuleError("PROX_WORLD_TENANT_MISMATCH", snapshot["tenant_id"])
    if snapshot["freshness"]["is_stale"]:
        raise ProxemicsRuleError("PROX_WORLD_STALE", snapshot["snapshot_id"])
    environment = snapshot.get("environment")
    if not isinstance(environment, Mapping) or environment.get("kill_switch_engaged") is not False or environment.get("privacy_safe") is not True:
        raise ProxemicsRuleError("PROX_ENVIRONMENT_UNSAFE", "environment")
    users = {item["user_id"]: item for item in snapshot["users"]}
    actor = users.get(request["user_id"])
    target = users.get(request["target_user_id"])
    if actor is None or target is None:
        raise ProxemicsRuleError("PROX_USER_NOT_FOUND", request["target_user_id"])
    current_distance = math.dist(_position(actor, "actor.position"), _position(target, "target.position"))
    boundary = target.get("proxemics_boundary")
    if not isinstance(boundary, Mapping) or set(boundary) != {
        "minimum_distance_m",
        "comfort_distance_m",
        "contact_allowed",
        "direct_gaze_allowed",
        "max_direct_gaze_seconds",
        "seat_min_spacing_m",
        "requires_visual_privacy",
        "observed_at",
        "provenance",
    }:
        raise ProxemicsRuleError("PROX_BOUNDARY_REQUIRED", request["target_user_id"])
    minimum = _number(boundary["minimum_distance_m"], "minimum_distance_m", CONTACT_ZONE_MAX_M, 3.0)
    comfort = _number(boundary["comfort_distance_m"], "comfort_distance_m", CONTACT_ZONE_MAX_M, 5.0)
    if comfort < minimum:
        raise ProxemicsRuleError("PROX_BOUNDARY_CONTRADICTORY", "comfort<minimum")
    if not isinstance(boundary["contact_allowed"], bool) or not isinstance(boundary["direct_gaze_allowed"], bool) or not isinstance(boundary["requires_visual_privacy"], bool):
        raise ProxemicsRuleError("PROX_BOUNDARY_BOOLEAN_INVALID", "boundary")
    max_gaze = _number(boundary["max_direct_gaze_seconds"], "max_direct_gaze_seconds", 0.1, 30.0)
    seat_spacing = _number(boundary["seat_min_spacing_m"], "seat_min_spacing_m", CONTACT_ZONE_MAX_M, 5.0)
    observed_at = _parse_time(boundary["observed_at"], "boundary.observed_at")
    if observed_at > now or (now - observed_at).total_seconds() > MAX_BOUNDARY_AGE_SECONDS:
        raise ProxemicsRuleError("PROX_BOUNDARY_STALE_OR_FUTURE", request["target_user_id"])
    provenance = boundary["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {"user_id", "source", "evidence_id", "confidence"}:
        raise ProxemicsRuleError("PROX_PROVENANCE_REQUIRED", "boundary.provenance")
    if provenance.get("user_id") != request["target_user_id"] or provenance.get("source") != "user_explicit_preference":
        raise ProxemicsRuleError("PROX_PROVENANCE_BINDING_MISMATCH", "provenance")
    evidence_id = _identifier(provenance.get("evidence_id"), "provenance.evidence_id")
    confidence = _number(provenance.get("confidence"), "provenance.confidence", 0.9, 1.0)
    objects = {item["object_id"]: item for item in snapshot["objects"]}
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "actor": actor,
        "target": target,
        "objects": objects,
        "current_distance_m": current_distance,
        "zone": classify_distance_zone(current_distance),
        "minimum_distance_m": minimum,
        "comfort_distance_m": comfort,
        "contact_allowed": boundary["contact_allowed"],
        "direct_gaze_allowed": boundary["direct_gaze_allowed"],
        "max_direct_gaze_seconds": max_gaze,
        "seat_min_spacing_m": seat_spacing,
        "requires_visual_privacy": boundary["requires_visual_privacy"],
        "provenance": {
            "user_id": request["target_user_id"],
            "source": "user_explicit_preference",
            "evidence_id": evidence_id,
            "confidence": confidence,
        },
    }


def _comfort_decision(request: Mapping[str, Any], world: Mapping[str, Any]) -> dict[str, Any]:
    behavior = request["behavior"]
    behavior_type = behavior["type"]
    current = world["current_distance_m"]
    minimum = world["minimum_distance_m"]
    comfort = world["comfort_distance_m"]
    motion = request["motion_request"]
    semantic = motion["semantic_intent"]
    expected_semantic = BEHAVIOR_TO_SEMANTIC[behavior_type]
    if semantic.get("type") != expected_semantic:
        raise ProxemicsRuleError("PROX_MOTION_SEMANTIC_MISMATCH", expected_semantic)
    if behavior_type == "approach":
        desired = behavior["desired_distance_m"]
        if desired < max(minimum, comfort) or (desired < CONTACT_ZONE_MAX_M and not world["contact_allowed"]):
            raise ProxemicsRuleError("PROX_APPROACH_BOUNDARY_VIOLATION", str(desired))
        if current <= desired:
            raise ProxemicsRuleError("PROX_APPROACH_DIRECTION_INVALID", str(current))
        if semantic.get("target_id") != request["target_user_id"]:
            raise ProxemicsRuleError("PROX_MOTION_TARGET_MISMATCH", "approach")
        navigation = motion.get("navigation_request")
        stop_distance = navigation.get("action_request", {}).get("intent", {}).get("parameters", {}).get("stop_distance_m") if isinstance(navigation, Mapping) else None
        if not isinstance(stop_distance, (int, float)) or float(stop_distance) < desired:
            raise ProxemicsRuleError("PROX_APPROACH_STOP_DISTANCE_UNSAFE", str(stop_distance))
        return {"desired_distance_m": desired, "corrective": False, "comfort_after": desired >= comfort}
    if behavior_type == "retreat":
        desired = behavior["desired_distance_m"]
        if desired < comfort or current >= desired:
            raise ProxemicsRuleError("PROX_RETREAT_DISTANCE_INVALID", str(desired))
        destination_id = behavior["retreat_destination_id"]
        if semantic.get("target_id") != destination_id:
            raise ProxemicsRuleError("PROX_MOTION_TARGET_MISMATCH", "retreat")
        destination = world["objects"].get(destination_id)
        if destination is None or destination.get("reachable") is not True or destination.get("occluded") is True:
            raise ProxemicsRuleError("PROX_RETREAT_DESTINATION_UNSAFE", destination_id)
        target_distance = math.dist(_position(destination, "retreat.position"), _position(world["target"], "target.position"))
        if target_distance < desired or target_distance <= current:
            raise ProxemicsRuleError("PROX_RETREAT_DESTINATION_TOO_CLOSE", destination_id)
        return {"desired_distance_m": desired, "corrective": current < comfort, "comfort_after": True}
    if behavior_type == "gaze":
        duration_seconds = behavior["gaze_duration_ms"] / 1000.0
        if (
            not world["direct_gaze_allowed"]
            or duration_seconds > world["max_direct_gaze_seconds"]
            or current < comfort
        ):
            raise ProxemicsRuleError("PROX_GAZE_BOUNDARY_VIOLATION", str(duration_seconds))
        if world["target"].get("visible") is not True or world["target"].get("occluded") is True:
            raise ProxemicsRuleError("PROX_GAZE_TARGET_OCCLUDED", request["target_user_id"])
        if semantic.get("target_id") != request["target_user_id"] or semantic.get("parameters", {}).get("duration_ms") != behavior["gaze_duration_ms"]:
            raise ProxemicsRuleError("PROX_GAZE_MOTION_MISMATCH", "motion_request")
        return {"desired_distance_m": current, "corrective": False, "comfort_after": current >= minimum}
    seat_id = behavior["seat_id"]
    seat = world["objects"].get(seat_id)
    if seat is None or semantic.get("target_id") != seat_id:
        raise ProxemicsRuleError("PROX_SEAT_MOTION_MISMATCH", seat_id)
    capabilities = seat.get("capabilities")
    if (
        seat.get("object_type") not in {"seat", "chair"}
        or seat.get("occupied") is not False
        or seat.get("reachable") is not True
        or seat.get("occluded") is True
        or not isinstance(capabilities, list)
        or "sit" not in capabilities
    ):
        raise ProxemicsRuleError("PROX_SEAT_UNSAFE", seat_id)
    seat_distance = math.dist(_position(seat, "seat.position"), _position(world["target"], "target.position"))
    if seat_distance < max(world["seat_min_spacing_m"], comfort):
        raise ProxemicsRuleError("PROX_SEAT_SPACING_VIOLATION", seat_id)
    if world["requires_visual_privacy"] and seat.get("privacy_screened") is not True:
        raise ProxemicsRuleError("PROX_SEAT_PRIVACY_REQUIRED", seat_id)
    return {"desired_distance_m": seat_distance, "corrective": False, "comfort_after": seat_distance >= comfort}


def _validate_motion_binding(request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        motion = validate_motion_request(request["motion_request"])
    except MotionSemanticsError as exc:
        raise ProxemicsRuleError("PROX_MOTION_REQUEST_INVALID", exc.code) from exc
    bindings = {
        "motion_id": f"motion:{request['proxemics_id']}",
        "idempotency_key": f"motion:{request['idempotency_key']}",
        "correlation_id": request["correlation_id"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "session_id": request["session_id"],
        "requested_at": request["requested_at"],
        "deadline_at": request["deadline_at"],
    }
    for field_name, expected in bindings.items():
        if motion.get(field_name) != expected:
            raise ProxemicsRuleError("PROX_MOTION_BINDING_MISMATCH", field_name)
    if _sha256(motion["world_state"]) != _sha256(request["world_state"]):
        raise ProxemicsRuleError("PROX_MOTION_WORLD_MISMATCH", "world_state")
    return motion


def _base(value: Any) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}

    def safe(item: Any, fallback: str) -> str:
        return item if isinstance(item, str) and _ID_PATTERN.fullmatch(item) else fallback

    behavior = source.get("behavior") if isinstance(source.get("behavior"), Mapping) else {}
    return {
        "proxemics_id": safe(source.get("proxemics_id"), "invalid-proxemics"),
        "correlation_id": safe(source.get("correlation_id"), "invalid-correlation"),
        "tenant_id": safe(source.get("tenant_id"), "invalid-tenant"),
        "user_id": safe(source.get("user_id"), "invalid-user"),
        "target_user_id": safe(source.get("target_user_id"), "invalid-target"),
        "behavior": behavior.get("type") if behavior.get("type") in BEHAVIOR_TO_SEMANTIC else "invalid-behavior",
    }


def _outcomes() -> dict[str, dict[str, Any]]:
    return {stage: {"outcome": "SKIPPED", "reason": "not_reached"} for stage in TRACE_STAGES}


def _trace(base: Mapping[str, str], outcomes: Mapping[str, Mapping[str, Any]], occurred_at: str) -> list[dict[str, Any]]:
    trace = []
    for sequence, stage in enumerate(TRACE_STAGES, start=1):
        event = {
            "schema_version": TRACE_SCHEMA,
            "trace_id": f"prox-trace:{base['proxemics_id']}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "outcome": outcomes[stage]["outcome"],
            "reason": outcomes[stage]["reason"],
            "proxemics_id": base["proxemics_id"],
            "correlation_id": base["correlation_id"],
            "user_id": base["user_id"],
            "occurred_at": occurred_at,
            "detail": _copy(outcomes[stage].get("detail", {})),
        }
        event["trace_sha256"] = _sha256(event)
        trace.append(event)
    return trace


def _finish(
    request: Mapping[str, Any] | None,
    base: Mapping[str, str],
    outcomes: dict[str, dict[str, Any]],
    *,
    status: str,
    reason: str,
    world: Mapping[str, Any] | None = None,
    decision: Mapping[str, Any] | None = None,
    motion_result: Mapping[str, Any] | None = None,
    duplicate_of_result_sha256: str | None = None,
) -> dict[str, Any]:
    outcomes["RESULT"] = {"outcome": "TERMINAL", "reason": reason, "detail": {"status": status}}
    occurred_at = request["requested_at"] if request is not None else "1970-01-01T00:00:00Z"
    result = {
        "schema_version": RESULT_SCHEMA,
        "ruleset_version": RULESET_VERSION,
        "proxemics_id": base["proxemics_id"],
        "correlation_id": base["correlation_id"],
        "tenant_id": base["tenant_id"],
        "user_id": base["user_id"],
        "target_user_id": base["target_user_id"],
        "behavior": base["behavior"],
        "status": status,
        "reason": reason,
        "authorized": status == "COMPLETED",
        "distance_zone": world.get("zone") if world else None,
        "current_distance_m": world.get("current_distance_m") if world else None,
        "minimum_distance_m": world.get("minimum_distance_m") if world else None,
        "comfort_distance_m": world.get("comfort_distance_m") if world else None,
        "comfort_decision": _copy(decision),
        "boundary_provenance": _copy(world.get("provenance")) if world else None,
        "motion_status": motion_result.get("status") if motion_result else None,
        "motion_result_sha256": motion_result.get("result_sha256") if motion_result else None,
        "fake_dispatch_invoked": bool(
            motion_result
            and (motion_result.get("downstream_fake_dispatch_invoked") or motion_result.get("animation_fake_invoked"))
        ),
        "rollback_performed": bool(motion_result and motion_result.get("rollback_performed")),
        "external_execution": False,
        "godot_action_fired": False,
        "p4_08_approved": False,
        "g4_approved": False,
        "g5_approved": False,
        "duplicate_of_result_sha256": duplicate_of_result_sha256,
        "trace": _trace(base, outcomes, occurred_at),
    }
    result["result_sha256"] = _sha256(result)
    return result


class ProxemicsRulesService:
    def __init__(self, motion_service: MotionSemanticsService) -> None:
        if type(motion_service) is not MotionSemanticsService:
            raise ProxemicsRuleError("PROX_LIVE_SERVICE_FORBIDDEN", type(motion_service).__name__)
        self.motion_service = motion_service
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._owners: dict[str, tuple[str, str, str]] = {}
        self._pending: dict[str, dict[str, str]] = {}

    def evaluate(self, value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        base = _base(value)
        outcomes = _outcomes()
        try:
            request = validate_proxemics_request(value)
            base = _base(request)
            outcomes["VALIDATE"] = {"outcome": "PASS", "reason": "request_contract_valid"}
        except ProxemicsRuleError as exc:
            outcomes["VALIDATE"] = {"outcome": "BLOCK", "reason": exc.code}
            return _finish(None, base, outcomes, status="BLOCKED", reason=exc.code)
        requested_at = _parse_time(request["requested_at"], "requested_at")
        current = _current(now, requested_at)
        if current >= _parse_time(request["deadline_at"], "deadline_at"):
            outcomes["MOTION_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "deadline_expired"}
            return _finish(request, base, outcomes, status="TIMED_OUT", reason="deadline_expired")
        if request["cancel_requested"]:
            outcomes["MOTION_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "cancelled_before_policy"}
            return _finish(request, base, outcomes, status="CANCELLED", reason="cancelled_before_policy")

        request_hash = _sha256(request)
        scope = (request["tenant_id"], request["user_id"], request["idempotency_key"])
        owner = self._owners.get(request["proxemics_id"])
        if owner is not None and owner != scope:
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "proxemics_scope_conflict"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="proxemics_scope_conflict")
        prior = self._idempotency.get(scope)
        if prior is not None:
            if prior["request_sha256"] == request_hash:
                outcomes["IDEMPOTENCY"] = {"outcome": "DUPLICATE", "reason": "identical_retry_suppressed"}
                outcomes["MOTION_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "duplicate_side_effect_suppressed"}
                return _finish(
                    request,
                    base,
                    outcomes,
                    status="DUPLICATE",
                    reason="identical_retry_suppressed",
                    duplicate_of_result_sha256=prior.get("result_sha256"),
                )
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "idempotency_key_conflict"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="idempotency_key_conflict")
        outcomes["IDEMPOTENCY"] = {"outcome": "PASS", "reason": "new_user_scoped_key"}

        try:
            world = _boundary_and_world(request, now=current)
            outcomes["WORLD_BOUNDARY"] = {
                "outcome": "PASS",
                "reason": "user_scoped_boundary_with_provenance",
                "detail": {"snapshot_id": world["snapshot_id"], "evidence_id": world["provenance"]["evidence_id"]},
            }
            outcomes["DISTANCE_ZONE"] = {
                "outcome": "PASS",
                "reason": "distance_zone_classified",
                "detail": {"zone": world["zone"], "distance_m": world["current_distance_m"]},
            }
            motion = _validate_motion_binding(request)
            decision = _comfort_decision(request, world)
            outcomes["COMFORT_DECISION"] = {
                "outcome": "PASS",
                "reason": "personal_boundary_satisfied",
                "detail": _copy(decision),
            }
        except ProxemicsRuleError as exc:
            if outcomes["WORLD_BOUNDARY"]["outcome"] == "SKIPPED":
                stage = "WORLD_BOUNDARY"
            elif outcomes["DISTANCE_ZONE"]["outcome"] == "SKIPPED":
                stage = "DISTANCE_ZONE"
            else:
                stage = "COMFORT_DECISION"
            outcomes[stage] = {"outcome": "BLOCK", "reason": exc.code}
            outcomes["MOTION_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "proxemics_failed_closed"}
            return _finish(request, base, outcomes, status="BLOCKED", reason=exc.code)

        self._owners[request["proxemics_id"]] = scope
        self._idempotency[scope] = {"request_sha256": request_hash, "result_sha256": None}
        motion_result = self.motion_service.execute(motion, now=current)
        risk_event = next(item for item in motion_result["trace"] if item["stage"] == "RISK_PERMISSION")
        outcomes["RISK_PERMISSION"] = {"outcome": risk_event["outcome"], "reason": risk_event["reason"]}
        outcomes["MOTION_DISPATCH"] = {
            "outcome": "CALLED_FAKE_ONLY" if motion_result["downstream_fake_dispatch_invoked"] or motion_result["animation_fake_invoked"] else "NOT_CALLED",
            "reason": motion_result["status"].lower(),
        }
        outcomes["ROLLBACK"] = {
            "outcome": "PASS" if motion_result["rollback_performed"] else "SKIPPED",
            "reason": "motion_rollback" if motion_result["rollback_performed"] else "not_needed",
        }
        status = motion_result["status"] if motion_result["status"] in {
            "PENDING",
            "FAILED_ROLLED_BACK",
            "TIMED_OUT_ROLLED_BACK",
            "TIMED_OUT",
            "CANCELLED",
        } else ("COMPLETED" if motion_result["status"] == "COMPLETED" else "BLOCKED")
        result = _finish(
            request,
            base,
            outcomes,
            status=status,
            reason="proxemics_safe_motion_completed" if status == "COMPLETED" else motion_result["reason"],
            world=world,
            decision=decision,
            motion_result=motion_result,
        )
        self._idempotency[scope]["result_sha256"] = result["result_sha256"]
        if status == "PENDING":
            self._pending[request["proxemics_id"]] = {
                "motion_id": motion["motion_id"],
                "tenant_id": request["tenant_id"],
                "user_id": request["user_id"],
                "correlation_id": request["correlation_id"],
            }
        return result

    def cancel(self, *, proxemics_id: str, tenant_id: str, user_id: str, correlation_id: str) -> dict[str, Any]:
        proxemics = _identifier(proxemics_id, "proxemics_id")
        tenant = _identifier(tenant_id, "tenant_id")
        user = _identifier(user_id, "user_id")
        correlation = _identifier(correlation_id, "correlation_id")
        pending = self._pending.get(proxemics)
        status = "BLOCKED_NOT_FOUND"
        rollback_performed = False
        motion_cancel_sha256: str | None = None
        if pending is not None:
            if (pending["tenant_id"], pending["user_id"], pending["correlation_id"]) != (tenant, user, correlation):
                status = "BLOCKED_SCOPE_MISMATCH"
            else:
                cancelled = self.motion_service.cancel(
                    motion_id=pending["motion_id"],
                    tenant_id=tenant,
                    user_id=user,
                    correlation_id=correlation,
                )
                status = cancelled["status"]
                rollback_performed = cancelled["rollback_performed"]
                motion_cancel_sha256 = cancelled["cancel_sha256"]
                self._pending.pop(proxemics, None)
        result = {
            "schema_version": CANCEL_RESULT_SCHEMA,
            "proxemics_id": proxemics,
            "tenant_id": tenant,
            "user_id": user,
            "correlation_id": correlation,
            "status": status,
            "rollback_performed": rollback_performed,
            "motion_cancel_sha256": motion_cancel_sha256,
            "external_execution": False,
            "godot_action_fired": False,
            "p4_08_approved": False,
            "g4_approved": False,
            "g5_approved": False,
        }
        result["cancel_sha256"] = _sha256(result)
        return result
