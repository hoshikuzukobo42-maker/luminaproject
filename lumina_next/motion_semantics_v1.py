"""P5-03 isolated Motion Semantics Library reference.

Semantic motion intents are mapped to deterministic animation/action contracts.
Locomotion delegates to P5-02, direct embodied actions delegate to P5-01, and
the animation sink is an exact local in-memory fake.  No live/Godot adapter can
be supplied and G5 remains explicitly unapproved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.embodied_action_api_v1 import (
    EmbodiedActionError,
    EmbodiedActionService,
    LocalFakeEmbodiedAdapter,
    validate_embodied_action_request,
)
from lumina_next.spatial_navigation_v1 import (
    SpatialNavigationError,
    SpatialNavigationService,
    validate_navigation_request,
)
from lumina_next.world_state_v1 import WorldStateContractError, validate_world_state_snapshot


REQUEST_SCHEMA = "lumina.motion.semantic.request.v1"
CONTRACT_SCHEMA = "lumina.motion.semantic.contract.v1"
RESULT_SCHEMA = "lumina.motion.semantic.result.v1"
CANCEL_RESULT_SCHEMA = "lumina.motion.semantic.cancel.result.v1"
TRACE_SCHEMA = "lumina.motion.semantic.trace.v1"
LIBRARY_VERSION = "lumina.motion.semantics.v1"
MAX_DEADLINE_SECONDS = 30.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

TRACE_STAGES = (
    "VALIDATE",
    "IDEMPOTENCY",
    "PRECONDITION",
    "SEMANTIC_MAP",
    "RISK_PERMISSION",
    "DOWNSTREAM_DISPATCH",
    "ANIMATION",
    "ROLLBACK",
    "RESULT",
)

SEMANTIC_SPECS: dict[str, dict[str, Any]] = {
    "walk": {
        "allowed_postures": {"standing"},
        "result_posture": "standing",
        "target": True,
        "interface": "P5_02_SPATIAL_NAVIGATION",
        "downstream_intent": "approach",
        "clip": "locomotion.walk.v1",
        "channel": "LOCOMOTION",
        "loop": True,
        "parameters": {"speed_mps"},
    },
    "point": {
        "allowed_postures": {"standing", "seated"},
        "result_posture": "UNCHANGED",
        "target": True,
        "interface": "P5_01_EMBODIED_ACTION",
        "downstream_intent": "point",
        "clip": "upper_body.point.v1",
        "channel": "UPPER_BODY",
        "loop": False,
        "parameters": {"duration_ms"},
    },
    "sit_down": {
        "allowed_postures": {"standing"},
        "result_posture": "seated",
        "target": True,
        "interface": "P5_01_EMBODIED_ACTION",
        "downstream_intent": "sit",
        "clip": "posture.sit_down.v1",
        "channel": "POSTURE",
        "loop": False,
        "parameters": {"duration_ms"},
    },
    "sit_idle": {
        "allowed_postures": {"seated"},
        "result_posture": "seated",
        "target": False,
        "interface": "ANIMATION_ONLY_REFERENCE",
        "downstream_intent": None,
        "clip": "posture.sit_idle.v1",
        "channel": "POSTURE",
        "loop": True,
        "parameters": {"duration_ms"},
    },
    "stand_up": {
        "allowed_postures": {"seated"},
        "result_posture": "standing",
        "target": False,
        "interface": "P5_01_EMBODIED_ACTION",
        "downstream_intent": "stand",
        "clip": "posture.stand_up.v1",
        "channel": "POSTURE",
        "loop": False,
        "parameters": {"duration_ms"},
    },
    "look": {
        "allowed_postures": {"standing", "seated"},
        "result_posture": "UNCHANGED",
        "target": True,
        "interface": "P5_01_EMBODIED_ACTION",
        "downstream_intent": "look",
        "clip": "gaze.look.v1",
        "channel": "GAZE",
        "loop": False,
        "parameters": {"duration_ms"},
    },
    "gesture": {
        "allowed_postures": {"standing", "seated"},
        "result_posture": "UNCHANGED",
        "target": False,
        "interface": "P5_01_EMBODIED_ACTION",
        "downstream_intent": "gesture",
        "clip": "upper_body.gesture.v1",
        "channel": "UPPER_BODY",
        "loop": False,
        "parameters": {"name", "duration_ms"},
    },
    "approach": {
        "allowed_postures": {"standing"},
        "result_posture": "standing",
        "target": True,
        "interface": "P5_02_SPATIAL_NAVIGATION",
        "downstream_intent": "approach",
        "clip": "locomotion.approach.v1",
        "channel": "LOCOMOTION",
        "loop": True,
        "parameters": {"speed_mps"},
    },
}

GESTURE_NAMES = {"wave", "nod", "shake_head", "bow", "open_palm"}


class MotionSemanticsError(ValueError):
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
        raise MotionSemanticsError("MOTION_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise MotionSemanticsError("MOTION_ID_INVALID", field_name)
    return value


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MotionSemanticsError("MOTION_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MotionSemanticsError("MOTION_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise MotionSemanticsError("MOTION_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _current(value: datetime | None, requested_at: datetime) -> datetime:
    if value is None:
        return requested_at
    if value.tzinfo is None:
        raise MotionSemanticsError("MOTION_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _number(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise MotionSemanticsError("MOTION_NUMBER_INVALID", field_name)
    return float(value)


def _semantic(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MotionSemanticsError("MOTION_SEMANTIC_OBJECT_REQUIRED", "semantic_intent")
    if set(value) != {"type", "target_id", "parameters"}:
        raise MotionSemanticsError("MOTION_SEMANTIC_FIELDS_INVALID", "semantic_intent")
    semantic_type = value.get("type")
    if semantic_type not in SEMANTIC_SPECS:
        raise MotionSemanticsError("MOTION_SEMANTIC_UNKNOWN", str(semantic_type))
    spec = SEMANTIC_SPECS[semantic_type]
    target_id = value.get("target_id")
    if spec["target"]:
        target_id = _identifier(target_id, "semantic_intent.target_id")
    elif target_id is not None:
        raise MotionSemanticsError("MOTION_TARGET_FORBIDDEN", semantic_type)
    parameters = value.get("parameters")
    if not isinstance(parameters, Mapping) or set(parameters) != spec["parameters"]:
        raise MotionSemanticsError("MOTION_PARAMETERS_INVALID", semantic_type)
    if semantic_type in {"walk", "approach"}:
        normalized_parameters = {"speed_mps": _number(parameters["speed_mps"], "speed_mps", 0.05, 1.0)}
    elif semantic_type == "gesture":
        if parameters["name"] not in GESTURE_NAMES:
            raise MotionSemanticsError("MOTION_GESTURE_UNKNOWN", str(parameters["name"]))
        normalized_parameters = {
            "name": parameters["name"],
            "duration_ms": int(_number(parameters["duration_ms"], "duration_ms", 100, 10000)),
        }
    else:
        normalized_parameters = {
            "duration_ms": int(_number(parameters["duration_ms"], "duration_ms", 100, 60000)),
        }
    return {"type": semantic_type, "target_id": target_id, "parameters": normalized_parameters}


def validate_motion_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MotionSemanticsError("MOTION_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "motion_id",
        "idempotency_key",
        "correlation_id",
        "tenant_id",
        "user_id",
        "session_id",
        "requested_at",
        "deadline_at",
        "semantic_intent",
        "world_state",
        "action_request",
        "navigation_request",
        "cancel_requested",
        "rollback",
        "g5_approved",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise MotionSemanticsError("MOTION_REQUEST_UNKNOWN_FIELDS", ",".join(unknown))
    if value.get("schema_version") != REQUEST_SCHEMA:
        raise MotionSemanticsError("MOTION_REQUEST_SCHEMA_MISMATCH", "schema_version")
    requested_at = _parse_time(value.get("requested_at"), "requested_at")
    deadline_at = _parse_time(value.get("deadline_at"), "deadline_at")
    lifetime = (deadline_at - requested_at).total_seconds()
    if not 0 < lifetime <= MAX_DEADLINE_SECONDS:
        raise MotionSemanticsError("MOTION_DEADLINE_INVALID", "deadline_at")
    if value.get("g5_approved") is not False:
        raise MotionSemanticsError("MOTION_G5_CLAIM_FORBIDDEN", "g5_approved")
    if not isinstance(value.get("world_state"), Mapping):
        raise MotionSemanticsError("MOTION_WORLD_STATE_REQUIRED", "world_state")
    action_request = value.get("action_request")
    navigation_request = value.get("navigation_request")
    if action_request is not None and not isinstance(action_request, Mapping):
        raise MotionSemanticsError("MOTION_ACTION_REQUEST_INVALID", "action_request")
    if navigation_request is not None and not isinstance(navigation_request, Mapping):
        raise MotionSemanticsError("MOTION_NAVIGATION_REQUEST_INVALID", "navigation_request")
    if not isinstance(value.get("cancel_requested"), bool):
        raise MotionSemanticsError("MOTION_CANCEL_FLAG_INVALID", "cancel_requested")
    rollback = value.get("rollback")
    if not isinstance(rollback, Mapping) or set(rollback) != {"required", "token"} or rollback.get("required") is not True:
        raise MotionSemanticsError("MOTION_ROLLBACK_INVALID", "rollback")
    normalized = {
        "schema_version": REQUEST_SCHEMA,
        "motion_id": _identifier(value.get("motion_id"), "motion_id"),
        "idempotency_key": _identifier(value.get("idempotency_key"), "idempotency_key"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": _identifier(value.get("user_id"), "user_id"),
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "requested_at": _iso(requested_at),
        "deadline_at": _iso(deadline_at),
        "semantic_intent": _semantic(value.get("semantic_intent")),
        "world_state": _copy(value["world_state"]),
        "action_request": _copy(action_request),
        "navigation_request": _copy(navigation_request),
        "cancel_requested": value["cancel_requested"],
        "rollback": {"required": True, "token": _identifier(rollback.get("token"), "rollback.token")},
        "g5_approved": False,
    }
    _canonical_json(normalized)
    return normalized


def _world_precondition(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    try:
        snapshot = validate_world_state_snapshot(request["world_state"], now=now)
    except WorldStateContractError as exc:
        raise MotionSemanticsError("MOTION_WORLD_INVALID", exc.code) from exc
    if snapshot["tenant_id"] != request["tenant_id"]:
        raise MotionSemanticsError("MOTION_WORLD_TENANT_MISMATCH", snapshot["tenant_id"])
    if snapshot["freshness"]["is_stale"]:
        raise MotionSemanticsError("MOTION_WORLD_STALE", snapshot["snapshot_id"])
    environment = snapshot.get("environment")
    if not isinstance(environment, Mapping):
        raise MotionSemanticsError("MOTION_ENVIRONMENT_REQUIRED", "environment")
    if environment.get("kill_switch_engaged") is not False or environment.get("privacy_safe") is not True:
        raise MotionSemanticsError("MOTION_ENVIRONMENT_UNSAFE", "environment")
    actors = [item for item in snapshot["users"] if item["user_id"] == request["user_id"]]
    if len(actors) != 1:
        raise MotionSemanticsError("MOTION_ACTOR_NOT_UNIQUE", request["user_id"])
    actor = actors[0]
    posture = actor.get("posture")
    semantic_type = request["semantic_intent"]["type"]
    spec = SEMANTIC_SPECS[semantic_type]
    if posture not in spec["allowed_postures"]:
        raise MotionSemanticsError("MOTION_POSTURE_INCOMPATIBLE", f"{semantic_type}:{posture}")
    target_id = request["semantic_intent"]["target_id"]
    if target_id is not None:
        subjects = [
            item
            for item in snapshot["users"] + snapshot["objects"]
            if item.get("user_id", item.get("object_id")) == target_id
        ]
        if len(subjects) != 1 or subjects[0].get("stale") is True:
            raise MotionSemanticsError("MOTION_TARGET_UNAVAILABLE", target_id)
    return {"snapshot_id": snapshot["snapshot_id"], "posture_before": posture}


def build_motion_contract(request: Mapping[str, Any], *, posture_before: str) -> dict[str, Any]:
    semantic = request["semantic_intent"]
    spec = SEMANTIC_SPECS[semantic["type"]]
    result_posture = posture_before if spec["result_posture"] == "UNCHANGED" else spec["result_posture"]
    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "library_version": LIBRARY_VERSION,
        "motion_id": request["motion_id"],
        "correlation_id": request["correlation_id"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "semantic_intent": semantic["type"],
        "target_id": semantic["target_id"],
        "semantic_parameters": _copy(semantic["parameters"]),
        "posture_transition": {"from": posture_before, "to": result_posture},
        "animation": {
            "clip_contract": spec["clip"],
            "channel": spec["channel"],
            "loop": spec["loop"],
            "cancelable": True,
            "blend_in_ms": 120,
            "blend_out_ms": 120,
        },
        "action_interface": {
            "kind": spec["interface"],
            "downstream_intent": spec["downstream_intent"],
            "reference_only": True,
        },
        "rollback_token": request["rollback"]["token"],
        "external_execution": False,
        "godot_action_fired": False,
        "g5_approved": False,
    }
    contract["contract_sha256"] = _sha256(contract)
    return contract


def _validate_downstream(request: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = contract["action_interface"]["kind"]
    if kind == "ANIMATION_ONLY_REFERENCE":
        if request["action_request"] is not None or request["navigation_request"] is not None:
            raise MotionSemanticsError("MOTION_DOWNSTREAM_FORBIDDEN", "sit_idle")
        return None
    if kind == "P5_01_EMBODIED_ACTION":
        if request["navigation_request"] is not None or request["action_request"] is None:
            raise MotionSemanticsError("MOTION_ACTION_REQUEST_REQUIRED", contract["semantic_intent"])
        try:
            downstream = validate_embodied_action_request(request["action_request"])
        except EmbodiedActionError as exc:
            raise MotionSemanticsError("MOTION_ACTION_REQUEST_INVALID", exc.code) from exc
        bindings = {
            "dispatch_id": f"action:{request['motion_id']}",
            "idempotency_key": f"action:{request['idempotency_key']}",
            "correlation_id": request["correlation_id"],
            "tenant_id": request["tenant_id"],
            "user_id": request["user_id"],
            "session_id": request["session_id"],
            "requested_at": request["requested_at"],
            "deadline_at": request["deadline_at"],
        }
        for field_name, expected in bindings.items():
            if downstream.get(field_name) != expected:
                raise MotionSemanticsError("MOTION_DOWNSTREAM_BINDING_MISMATCH", field_name)
        if downstream["intent"]["type"] != contract["action_interface"]["downstream_intent"]:
            raise MotionSemanticsError("MOTION_DOWNSTREAM_INTENT_MISMATCH", downstream["intent"]["type"])
        if downstream["intent"]["target_id"] != contract["target_id"]:
            raise MotionSemanticsError("MOTION_DOWNSTREAM_TARGET_MISMATCH", "target_id")
    else:
        if request["action_request"] is not None or request["navigation_request"] is None:
            raise MotionSemanticsError("MOTION_NAVIGATION_REQUEST_REQUIRED", contract["semantic_intent"])
        try:
            downstream = validate_navigation_request(request["navigation_request"])
        except SpatialNavigationError as exc:
            raise MotionSemanticsError("MOTION_NAVIGATION_REQUEST_INVALID", exc.code) from exc
        bindings = {
            "navigation_id": f"nav:{request['motion_id']}",
            "idempotency_key": f"nav:{request['idempotency_key']}",
            "correlation_id": request["correlation_id"],
            "tenant_id": request["tenant_id"],
            "user_id": request["user_id"],
            "session_id": request["session_id"],
            "requested_at": request["requested_at"],
            "deadline_at": request["deadline_at"],
        }
        for field_name, expected in bindings.items():
            if downstream.get(field_name) != expected:
                raise MotionSemanticsError("MOTION_DOWNSTREAM_BINDING_MISMATCH", field_name)
        if downstream["destination"]["target_id"] != contract["target_id"]:
            raise MotionSemanticsError("MOTION_DOWNSTREAM_TARGET_MISMATCH", "target_id")
    if _sha256(downstream["world_state"]) != _sha256(request["world_state"]):
        raise MotionSemanticsError("MOTION_DOWNSTREAM_WORLD_MISMATCH", "world_state")
    return downstream


class LocalFakeAnimationAdapter:
    """In-memory animation contract sink with no engine or external handle."""

    def __init__(
        self,
        *,
        fail_after_apply_motion_ids: set[str] | None = None,
        timeout_after_apply_motion_ids: set[str] | None = None,
        pending_motion_ids: set[str] | None = None,
    ) -> None:
        self.fail_after_apply_motion_ids = set(fail_after_apply_motion_ids or ())
        self.timeout_after_apply_motion_ids = set(timeout_after_apply_motion_ids or ())
        self.pending_motion_ids = set(pending_motion_ids or ())
        self.invocations: list[dict[str, Any]] = []
        self.effects: dict[str, dict[str, Any]] = {}
        self.handles: dict[str, str] = {}
        self.duplicate_side_effects = 0
        self.rollback_calls = 0
        self.cancel_calls = 0

    def dispatch(self, contract: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if contract.get("external_execution") is not False or contract.get("g5_approved") is not False:
            raise MotionSemanticsError("MOTION_ANIMATION_SCOPE_INVALID", "contract")
        key = f"{contract['tenant_id']}|{contract['user_id']}|{idempotency_key}"
        self.invocations.append(
            {
                "motion_id": contract["motion_id"],
                "correlation_id": contract["correlation_id"],
                "tenant_id": contract["tenant_id"],
                "user_id": contract["user_id"],
                "semantic_intent": contract["semantic_intent"],
                "clip_contract": contract["animation"]["clip_contract"],
                "contract_sha256": contract["contract_sha256"],
                "external_execution": False,
                "godot_action_fired": False,
            }
        )
        if key in self.effects:
            self.duplicate_side_effects += 1
            return {"status": "DUPLICATE_REJECTED", "handle_id": self.effects[key]["handle_id"]}
        handle_id = f"animation-fake:{contract['motion_id']}"
        self.effects[key] = {
            "handle_id": handle_id,
            "motion_id": contract["motion_id"],
            "user_id": contract["user_id"],
            "state": "APPLIED_IN_MEMORY_ONLY",
        }
        self.handles[handle_id] = key
        status = "COMPLETED"
        if contract["motion_id"] in self.fail_after_apply_motion_ids:
            status = "FAILED_AFTER_APPLY"
        elif contract["motion_id"] in self.timeout_after_apply_motion_ids:
            status = "TIMED_OUT_AFTER_APPLY"
        elif contract["motion_id"] in self.pending_motion_ids:
            status = "PENDING_AFTER_APPLY"
        return {"status": status, "handle_id": handle_id, "external_execution": False, "godot_action_fired": False}

    def rollback(self, *, handle_id: str) -> dict[str, Any]:
        key = self.handles.get(handle_id)
        if key is None:
            return {"status": "NOT_FOUND", "external_execution": False}
        effect = self.effects[key]
        if effect["state"] == "ROLLED_BACK":
            return {"status": "ALREADY_ROLLED_BACK", "external_execution": False}
        effect["state"] = "ROLLED_BACK"
        self.rollback_calls += 1
        return {"status": "ROLLED_BACK", "external_execution": False}

    def cancel(self, *, handle_id: str) -> dict[str, Any]:
        key = self.handles.get(handle_id)
        if key is None:
            return {"status": "NOT_FOUND", "external_execution": False}
        effect = self.effects[key]
        if effect["state"] in {"CANCELLED", "ROLLED_BACK"}:
            return {"status": "ALREADY_TERMINAL", "external_execution": False}
        effect["state"] = "CANCELLED"
        self.cancel_calls += 1
        return {"status": "CANCELLED", "external_execution": False}


def _base(value: Any) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}

    def safe(item: Any, fallback: str) -> str:
        return item if isinstance(item, str) and _ID_PATTERN.fullmatch(item) else fallback

    semantic = source.get("semantic_intent") if isinstance(source.get("semantic_intent"), Mapping) else {}
    return {
        "motion_id": safe(source.get("motion_id"), "invalid-motion"),
        "correlation_id": safe(source.get("correlation_id"), "invalid-correlation"),
        "tenant_id": safe(source.get("tenant_id"), "invalid-tenant"),
        "user_id": safe(source.get("user_id"), "invalid-user"),
        "semantic_intent": semantic.get("type") if semantic.get("type") in SEMANTIC_SPECS else "invalid-semantic",
    }


def _outcomes() -> dict[str, dict[str, Any]]:
    return {stage: {"outcome": "SKIPPED", "reason": "not_reached"} for stage in TRACE_STAGES}


def _trace(base: Mapping[str, str], outcomes: Mapping[str, Mapping[str, Any]], occurred_at: str) -> list[dict[str, Any]]:
    events = []
    for sequence, stage in enumerate(TRACE_STAGES, start=1):
        event = {
            "schema_version": TRACE_SCHEMA,
            "trace_id": f"motion-trace:{base['motion_id']}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "outcome": outcomes[stage]["outcome"],
            "reason": outcomes[stage]["reason"],
            "motion_id": base["motion_id"],
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
    contract: Mapping[str, Any] | None = None,
    downstream_result: Mapping[str, Any] | None = None,
    animation_handle_id: str | None = None,
    animation_invoked: bool = False,
    rollback_performed: bool = False,
    duplicate_of_result_sha256: str | None = None,
) -> dict[str, Any]:
    outcomes["RESULT"] = {"outcome": "TERMINAL", "reason": reason, "detail": {"status": status}}
    occurred_at = request["requested_at"] if request is not None else "1970-01-01T00:00:00Z"
    result = {
        "schema_version": RESULT_SCHEMA,
        "library_version": LIBRARY_VERSION,
        "motion_id": base["motion_id"],
        "correlation_id": base["correlation_id"],
        "tenant_id": base["tenant_id"],
        "user_id": base["user_id"],
        "semantic_intent": base["semantic_intent"],
        "status": status,
        "reason": reason,
        "semantic_match": status == "COMPLETED",
        "motion_contract": _copy(contract),
        "downstream_status": downstream_result.get("status") if downstream_result else None,
        "downstream_result_sha256": downstream_result.get("result_sha256") if downstream_result else None,
        "downstream_fake_dispatch_invoked": bool(
            downstream_result
            and (downstream_result.get("fake_adapter_invoked") or downstream_result.get("fake_dispatch_invoked"))
        ),
        "animation_fake_invoked": animation_invoked,
        "animation_handle_id": animation_handle_id,
        "rollback_performed": rollback_performed,
        "external_execution": False,
        "godot_action_fired": False,
        "g5_approved": False,
        "duplicate_of_result_sha256": duplicate_of_result_sha256,
        "trace": _trace(base, outcomes, occurred_at),
    }
    result["result_sha256"] = _sha256(result)
    return result


class MotionSemanticsService:
    def __init__(
        self,
        *,
        action_service: EmbodiedActionService,
        navigation_service: SpatialNavigationService,
        animation_adapter: LocalFakeAnimationAdapter,
    ) -> None:
        if (
            type(action_service) is not EmbodiedActionService
            or type(action_service.adapter) is not LocalFakeEmbodiedAdapter
            or type(navigation_service) is not SpatialNavigationService
            or navigation_service.action_service is not action_service
            or type(animation_adapter) is not LocalFakeAnimationAdapter
        ):
            raise MotionSemanticsError("MOTION_LIVE_ADAPTER_FORBIDDEN", "constructor")
        self.action_service = action_service
        self.navigation_service = navigation_service
        self.animation_adapter = animation_adapter
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._owners: dict[str, tuple[str, str, str]] = {}
        self._pending: dict[str, dict[str, str]] = {}

    def execute(self, value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        base = _base(value)
        outcomes = _outcomes()
        try:
            request = validate_motion_request(value)
            base = _base(request)
            outcomes["VALIDATE"] = {"outcome": "PASS", "reason": "request_contract_valid"}
        except MotionSemanticsError as exc:
            outcomes["VALIDATE"] = {"outcome": "BLOCK", "reason": exc.code}
            return _finish(None, base, outcomes, status="BLOCKED", reason=exc.code)
        requested_at = _parse_time(request["requested_at"], "requested_at")
        current = _current(now, requested_at)
        if current >= _parse_time(request["deadline_at"], "deadline_at"):
            outcomes["DOWNSTREAM_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "deadline_expired"}
            return _finish(request, base, outcomes, status="TIMED_OUT", reason="deadline_expired")
        if request["cancel_requested"]:
            outcomes["DOWNSTREAM_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "cancelled_before_mapping"}
            return _finish(request, base, outcomes, status="CANCELLED", reason="cancelled_before_mapping")

        request_hash = _sha256(request)
        scope = (request["tenant_id"], request["user_id"], request["idempotency_key"])
        owner = self._owners.get(request["motion_id"])
        if owner is not None and owner != scope:
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "motion_scope_conflict"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="motion_scope_conflict")
        prior = self._idempotency.get(scope)
        if prior is not None:
            if prior["request_sha256"] == request_hash:
                outcomes["IDEMPOTENCY"] = {"outcome": "DUPLICATE", "reason": "identical_retry_suppressed"}
                outcomes["DOWNSTREAM_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "duplicate_side_effect_suppressed"}
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
            precondition = _world_precondition(request, now=current)
            outcomes["PRECONDITION"] = {
                "outcome": "PASS",
                "reason": "posture_and_world_compatible",
                "detail": precondition,
            }
            contract = build_motion_contract(request, posture_before=precondition["posture_before"])
            downstream = _validate_downstream(request, contract)
            outcomes["SEMANTIC_MAP"] = {
                "outcome": "PASS",
                "reason": "semantic_contract_mapped",
                "detail": {
                    "contract_sha256": contract["contract_sha256"],
                    "interface": contract["action_interface"]["kind"],
                    "clip_contract": contract["animation"]["clip_contract"],
                },
            }
        except MotionSemanticsError as exc:
            stage = "PRECONDITION" if outcomes["PRECONDITION"]["outcome"] == "SKIPPED" else "SEMANTIC_MAP"
            outcomes[stage] = {"outcome": "BLOCK", "reason": exc.code}
            outcomes["DOWNSTREAM_DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "semantic_preflight_failed"}
            return _finish(request, base, outcomes, status="BLOCKED", reason=exc.code)

        self._owners[request["motion_id"]] = scope
        self._idempotency[scope] = {"request_sha256": request_hash, "result_sha256": None}
        interface = contract["action_interface"]["kind"]
        downstream_result: dict[str, Any] | None = None
        if interface == "P5_01_EMBODIED_ACTION":
            assert downstream is not None
            downstream_result = self.action_service.dispatch(downstream, now=current)
            risk_event = next(item for item in downstream_result["trace"] if item["stage"] == "RISK_PERMISSION")
        elif interface == "P5_02_SPATIAL_NAVIGATION":
            assert downstream is not None
            downstream_result = self.navigation_service.navigate(downstream, now=current)
            risk_event = next(item for item in downstream_result["trace"] if item["stage"] == "RISK_PERMISSION")
        else:
            risk_event = {"outcome": "SKIPPED", "reason": "animation_only_reference"}
        outcomes["RISK_PERMISSION"] = {"outcome": risk_event["outcome"], "reason": risk_event["reason"]}

        downstream_success = downstream_result is None or downstream_result["status"] in {"COMPLETED", "NAVIGATED"}
        downstream_pending = bool(downstream_result and downstream_result["status"] == "PENDING")
        outcomes["DOWNSTREAM_DISPATCH"] = {
            "outcome": "SKIPPED" if downstream_result is None else (
                "CALLED_FAKE_ONLY" if downstream_result.get("fake_adapter_invoked") or downstream_result.get("fake_dispatch_invoked") else "NOT_CALLED"
            ),
            "reason": "animation_only_reference" if downstream_result is None else downstream_result["status"].lower(),
        }
        if downstream_pending:
            outcomes["ANIMATION"] = {"outcome": "SKIPPED", "reason": "awaiting_downstream_completion"}
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "pending"}
            result = _finish(request, base, outcomes, status="PENDING", reason="downstream_pending", contract=contract, downstream_result=downstream_result)
            self._pending[request["motion_id"]] = {
                "kind": interface,
                "downstream_id": downstream["dispatch_id"] if interface == "P5_01_EMBODIED_ACTION" else downstream["navigation_id"],
                "tenant_id": request["tenant_id"],
                "user_id": request["user_id"],
                "correlation_id": request["correlation_id"],
            }
            self._idempotency[scope]["result_sha256"] = result["result_sha256"]
            return result
        if not downstream_success:
            rolled_back = bool(downstream_result and downstream_result.get("rollback_performed")) or bool(
                downstream_result and downstream_result.get("rollback", {}).get("performed")
            )
            outcomes["ANIMATION"] = {"outcome": "SKIPPED", "reason": "downstream_not_successful"}
            outcomes["ROLLBACK"] = {"outcome": "PASS" if rolled_back else "SKIPPED", "reason": "downstream_rollback" if rolled_back else "no_effect"}
            downstream_status = downstream_result["status"] if downstream_result else "BLOCKED"
            status = "TIMED_OUT_ROLLED_BACK" if downstream_status == "TIMED_OUT_ROLLED_BACK" else (
                "FAILED_ROLLED_BACK" if "ROLLED_BACK" in downstream_status else "BLOCKED"
            )
            result = _finish(
                request,
                base,
                outcomes,
                status=status,
                reason=downstream_result["reason"] if downstream_result else "downstream_blocked",
                contract=contract,
                downstream_result=downstream_result,
                rollback_performed=rolled_back,
            )
            self._idempotency[scope]["result_sha256"] = result["result_sha256"]
            return result

        animation_result = self.animation_adapter.dispatch(contract, idempotency_key=request["idempotency_key"])
        animation_status = animation_result["status"]
        handle_id = animation_result["handle_id"]
        outcomes["ANIMATION"] = {"outcome": "CALLED_FAKE_ONLY", "reason": animation_status.lower()}
        if animation_status == "PENDING_AFTER_APPLY":
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "pending"}
            result = _finish(
                request,
                base,
                outcomes,
                status="PENDING",
                reason="animation_pending",
                contract=contract,
                downstream_result=downstream_result,
                animation_handle_id=handle_id,
                animation_invoked=True,
            )
            self._pending[request["motion_id"]] = {
                "kind": "ANIMATION_ONLY_REFERENCE",
                "downstream_id": handle_id,
                "tenant_id": request["tenant_id"],
                "user_id": request["user_id"],
                "correlation_id": request["correlation_id"],
            }
        elif animation_status in {"FAILED_AFTER_APPLY", "TIMED_OUT_AFTER_APPLY"}:
            rollback = self.animation_adapter.rollback(handle_id=handle_id)
            rollback_ok = rollback["status"] in {"ROLLED_BACK", "ALREADY_ROLLED_BACK"}
            outcomes["ROLLBACK"] = {"outcome": "PASS" if rollback_ok else "FAIL", "reason": rollback["status"].lower()}
            result = _finish(
                request,
                base,
                outcomes,
                status="TIMED_OUT_ROLLED_BACK" if animation_status.startswith("TIMED") else "FAILED_ROLLED_BACK",
                reason=animation_status.lower(),
                contract=contract,
                downstream_result=downstream_result,
                animation_handle_id=handle_id,
                animation_invoked=True,
                rollback_performed=rollback_ok,
            )
        else:
            outcomes["ROLLBACK"] = {"outcome": "SKIPPED", "reason": "not_needed"}
            result = _finish(
                request,
                base,
                outcomes,
                status="COMPLETED",
                reason="semantic_motion_fake_completed",
                contract=contract,
                downstream_result=downstream_result,
                animation_handle_id=handle_id,
                animation_invoked=True,
            )
        self._idempotency[scope]["result_sha256"] = result["result_sha256"]
        return result

    def cancel(
        self,
        *,
        motion_id: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        motion = _identifier(motion_id, "motion_id")
        tenant = _identifier(tenant_id, "tenant_id")
        user = _identifier(user_id, "user_id")
        correlation = _identifier(correlation_id, "correlation_id")
        pending = self._pending.get(motion)
        status = "BLOCKED_NOT_FOUND"
        rollback_performed = False
        downstream_cancel_sha256: str | None = None
        if pending is not None:
            if (pending["tenant_id"], pending["user_id"], pending["correlation_id"]) != (tenant, user, correlation):
                status = "BLOCKED_SCOPE_MISMATCH"
            elif pending["kind"] == "P5_01_EMBODIED_ACTION":
                cancelled = self.action_service.cancel(
                    dispatch_id=pending["downstream_id"],
                    tenant_id=tenant,
                    user_id=user,
                    correlation_id=correlation,
                )
                status = cancelled["status"]
                rollback_performed = cancelled["rollback_performed"]
                downstream_cancel_sha256 = cancelled["cancel_sha256"]
                self._pending.pop(motion, None)
            elif pending["kind"] == "P5_02_SPATIAL_NAVIGATION":
                cancelled = self.navigation_service.cancel(
                    navigation_id=pending["downstream_id"],
                    tenant_id=tenant,
                    user_id=user,
                    correlation_id=correlation,
                )
                status = cancelled["status"]
                rollback_performed = cancelled["rollback_performed"]
                downstream_cancel_sha256 = cancelled["cancel_sha256"]
                self._pending.pop(motion, None)
            else:
                cancelled = self.animation_adapter.cancel(handle_id=pending["downstream_id"])
                rollback = self.animation_adapter.rollback(handle_id=pending["downstream_id"])
                rollback_performed = rollback["status"] in {"ROLLED_BACK", "ALREADY_ROLLED_BACK"}
                status = "CANCELLED_ROLLED_BACK" if cancelled["status"] in {"CANCELLED", "ALREADY_TERMINAL"} and rollback_performed else "CANCEL_FAILED_CLOSED"
                self._pending.pop(motion, None)
        result = {
            "schema_version": CANCEL_RESULT_SCHEMA,
            "motion_id": motion,
            "tenant_id": tenant,
            "user_id": user,
            "correlation_id": correlation,
            "status": status,
            "rollback_performed": rollback_performed,
            "downstream_cancel_sha256": downstream_cancel_sha256,
            "external_execution": False,
            "godot_action_fired": False,
            "g5_approved": False,
        }
        result["cancel_sha256"] = _sha256(result)
        return result

