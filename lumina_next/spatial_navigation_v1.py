"""P5-02 isolated spatial-navigation reference.

The planner consumes a validated World State snapshot, creates a deterministic
grid route, and delegates risk/permission/dispatch to P5-01's exact local fake
boundary.  It cannot accept a live adapter and does not claim P4-08 or G5.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.embodied_action_api_v1 import (
    EmbodiedActionError,
    EmbodiedActionService,
    LocalFakeEmbodiedAdapter,
    validate_embodied_action_request,
)
from lumina_next.world_state_v1 import WorldStateContractError, validate_world_state_snapshot


REQUEST_SCHEMA = "lumina.spatial.navigation.request.v1"
PLAN_SCHEMA = "lumina.spatial.navigation.plan.v1"
RESULT_SCHEMA = "lumina.spatial.navigation.result.v1"
CANCEL_RESULT_SCHEMA = "lumina.spatial.navigation.cancel.result.v1"
TRACE_SCHEMA = "lumina.spatial.navigation.trace.v1"
SERVICE_VERSION = "lumina.spatial.navigation.v1"
MAX_DEADLINE_SECONDS = 30.0
MAX_GRID_DIMENSION = 32
MAX_WAYPOINTS = 128
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

TRACE_STAGES = (
    "VALIDATE",
    "IDEMPOTENCY",
    "WORLD_STATE",
    "ROUTE_PLAN",
    "RISK_PERMISSION",
    "DISPATCH",
    "ROLLBACK",
    "RESULT",
)

DESTINATION_KINDS = {"user", "object", "seat"}
_NEIGHBOR_DELTAS = ((0, -1), (1, 0), (0, 1), (-1, 0))


class SpatialNavigationError(ValueError):
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
        raise SpatialNavigationError("NAV_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise SpatialNavigationError("NAV_ID_INVALID", field_name)
    return value


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SpatialNavigationError("NAV_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpatialNavigationError("NAV_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise SpatialNavigationError("NAV_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _current(value: datetime | None, requested_at: datetime) -> datetime:
    if value is None:
        return requested_at
    if value.tzinfo is None:
        raise SpatialNavigationError("NAV_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _integer(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SpatialNavigationError("NAV_INTEGER_INVALID", field_name)
    return value


def _number(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise SpatialNavigationError("NAV_NUMBER_INVALID", field_name)
    return float(value)


def _cell(value: Any, field_name: str, *, width: int | None = None, height: int | None = None) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise SpatialNavigationError("NAV_CELL_INVALID", field_name)
    x = _integer(value[0], f"{field_name}[0]", 0, MAX_GRID_DIMENSION - 1)
    y = _integer(value[1], f"{field_name}[1]", 0, MAX_GRID_DIMENSION - 1)
    if width is not None and height is not None and (x >= width or y >= height):
        raise SpatialNavigationError("NAV_CELL_OUT_OF_BOUNDS", field_name)
    return x, y


def validate_navigation_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SpatialNavigationError("NAV_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "navigation_id",
        "idempotency_key",
        "correlation_id",
        "tenant_id",
        "user_id",
        "session_id",
        "requested_at",
        "deadline_at",
        "destination",
        "world_state",
        "action_request",
        "cancel_requested",
        "max_waypoints",
        "rollback",
        "p4_08_approved",
        "g5_approved",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SpatialNavigationError("NAV_REQUEST_UNKNOWN_FIELDS", ",".join(unknown))
    if value.get("schema_version") != REQUEST_SCHEMA:
        raise SpatialNavigationError("NAV_REQUEST_SCHEMA_MISMATCH", "schema_version")
    requested_at = _parse_time(value.get("requested_at"), "requested_at")
    deadline_at = _parse_time(value.get("deadline_at"), "deadline_at")
    lifetime = (deadline_at - requested_at).total_seconds()
    if not 0 < lifetime <= MAX_DEADLINE_SECONDS:
        raise SpatialNavigationError("NAV_DEADLINE_INVALID", "deadline_at")
    if value.get("p4_08_approved") is not False or value.get("g5_approved") is not False:
        raise SpatialNavigationError("NAV_UNAPPROVED_GATE_CLAIM", "P4-08/G5 must remain false")
    if not isinstance(value.get("world_state"), Mapping):
        raise SpatialNavigationError("NAV_WORLD_STATE_REQUIRED", "world_state")
    if not isinstance(value.get("action_request"), Mapping):
        raise SpatialNavigationError("NAV_ACTION_REQUEST_REQUIRED", "action_request")
    if not isinstance(value.get("cancel_requested"), bool):
        raise SpatialNavigationError("NAV_CANCEL_FLAG_INVALID", "cancel_requested")

    destination = value.get("destination")
    if not isinstance(destination, Mapping):
        raise SpatialNavigationError("NAV_DESTINATION_OBJECT_REQUIRED", "destination")
    destination_unknown = sorted(set(destination) - {"target_id", "kind", "arrival_radius_cells", "require_visibility"})
    if destination_unknown:
        raise SpatialNavigationError("NAV_DESTINATION_UNKNOWN_FIELDS", ",".join(destination_unknown))
    kind = destination.get("kind")
    if kind not in DESTINATION_KINDS:
        raise SpatialNavigationError("NAV_DESTINATION_KIND_INVALID", str(kind))
    arrival_radius = _integer(destination.get("arrival_radius_cells"), "arrival_radius_cells", 0, 2)
    if kind == "seat" and arrival_radius != 0:
        raise SpatialNavigationError("NAV_SEAT_REQUIRES_EXACT_ARRIVAL", str(arrival_radius))
    if kind != "seat" and arrival_radius < 1:
        raise SpatialNavigationError("NAV_APPROACH_REQUIRES_STANDOFF", str(arrival_radius))
    if not isinstance(destination.get("require_visibility"), bool):
        raise SpatialNavigationError("NAV_VISIBILITY_FLAG_INVALID", "require_visibility")

    rollback = value.get("rollback")
    if not isinstance(rollback, Mapping) or set(rollback) != {"required", "plan_token"}:
        raise SpatialNavigationError("NAV_ROLLBACK_INVALID", "rollback")
    if rollback.get("required") is not True:
        raise SpatialNavigationError("NAV_ROLLBACK_REQUIRED", "rollback.required")

    normalized = {
        "schema_version": REQUEST_SCHEMA,
        "navigation_id": _identifier(value.get("navigation_id"), "navigation_id"),
        "idempotency_key": _identifier(value.get("idempotency_key"), "idempotency_key"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": _identifier(value.get("user_id"), "user_id"),
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "requested_at": _iso(requested_at),
        "deadline_at": _iso(deadline_at),
        "destination": {
            "target_id": _identifier(destination.get("target_id"), "destination.target_id"),
            "kind": kind,
            "arrival_radius_cells": arrival_radius,
            "require_visibility": destination["require_visibility"],
        },
        "world_state": _copy(value["world_state"]),
        "action_request": _copy(value["action_request"]),
        "cancel_requested": value["cancel_requested"],
        "max_waypoints": _integer(value.get("max_waypoints"), "max_waypoints", 2, MAX_WAYPOINTS),
        "rollback": {"required": True, "plan_token": _identifier(rollback.get("plan_token"), "rollback.plan_token")},
        "p4_08_approved": False,
        "g5_approved": False,
    }
    _canonical_json(normalized)
    return normalized


def _validate_grid(snapshot: Mapping[str, Any]) -> tuple[int, int, float, set[tuple[int, int]], set[tuple[int, int]]]:
    grid = snapshot.get("navigation_grid")
    if not isinstance(grid, Mapping):
        raise SpatialNavigationError("NAV_GRID_REQUIRED", "world_state.navigation_grid")
    if set(grid) != {"width", "height", "cell_size_m", "blocked_cells", "occluded_cells"}:
        raise SpatialNavigationError("NAV_GRID_FIELDS_INVALID", "navigation_grid")
    width = _integer(grid.get("width"), "grid.width", 2, MAX_GRID_DIMENSION)
    height = _integer(grid.get("height"), "grid.height", 2, MAX_GRID_DIMENSION)
    cell_size = _number(grid.get("cell_size_m"), "grid.cell_size_m", 0.25, 2.0)

    def cells(field_name: str) -> set[tuple[int, int]]:
        values = grid.get(field_name)
        if not isinstance(values, list):
            raise SpatialNavigationError("NAV_GRID_CELLS_REQUIRED", field_name)
        normalized = [_cell(value, field_name, width=width, height=height) for value in values]
        if len(normalized) != len(set(normalized)):
            raise SpatialNavigationError("NAV_GRID_DUPLICATE_CELL", field_name)
        return set(normalized)

    blocked = cells("blocked_cells")
    occluded = cells("occluded_cells")
    if blocked & occluded:
        raise SpatialNavigationError("NAV_GRID_AMBIGUOUS_CELL", "blocked_and_occluded")
    return width, height, cell_size, blocked, occluded


def _subject_maps(snapshot: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    users: dict[str, Mapping[str, Any]] = {}
    objects: dict[str, Mapping[str, Any]] = {}
    for item in snapshot["users"]:
        subject_id = item["user_id"]
        if subject_id in users:
            raise SpatialNavigationError("NAV_DUPLICATE_SUBJECT", subject_id)
        users[subject_id] = item
    for item in snapshot["objects"]:
        subject_id = item["object_id"]
        if subject_id in objects or subject_id in users:
            raise SpatialNavigationError("NAV_DUPLICATE_SUBJECT", subject_id)
        objects[subject_id] = item
    return users, objects


def _world_and_cells(request: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    try:
        snapshot = validate_world_state_snapshot(request["world_state"], now=now)
    except WorldStateContractError as exc:
        raise SpatialNavigationError("NAV_WORLD_INVALID", exc.code) from exc
    if snapshot["tenant_id"] != request["tenant_id"]:
        raise SpatialNavigationError("NAV_WORLD_TENANT_MISMATCH", snapshot["tenant_id"])
    if snapshot["freshness"]["is_stale"]:
        raise SpatialNavigationError("NAV_WORLD_STALE", snapshot["snapshot_id"])
    environment = snapshot.get("environment")
    if not isinstance(environment, Mapping):
        raise SpatialNavigationError("NAV_ENVIRONMENT_REQUIRED", "environment")
    expected_environment = {"kill_switch_engaged", "privacy_safe", "navigation_safe", "collision_clear"}
    if set(environment) != expected_environment:
        raise SpatialNavigationError("NAV_ENVIRONMENT_FIELDS_INVALID", "environment")
    if environment["kill_switch_engaged"] is not False:
        raise SpatialNavigationError("NAV_KILL_SWITCH_ENGAGED", "environment")
    if environment["privacy_safe"] is not True:
        raise SpatialNavigationError("NAV_PRIVACY_UNSAFE", "environment")
    if environment["navigation_safe"] is not True or environment["collision_clear"] is not True:
        raise SpatialNavigationError("NAV_MOVEMENT_UNSAFE", "environment")

    width, height, cell_size, blocked, occluded = _validate_grid(snapshot)
    users, objects = _subject_maps(snapshot)
    actor = users.get(request["user_id"])
    if actor is None:
        raise SpatialNavigationError("NAV_ACTOR_NOT_FOUND", request["user_id"])
    start = _cell(actor.get("nav_cell"), "actor.nav_cell", width=width, height=height)
    destination = request["destination"]
    target_id = destination["target_id"]
    target: Mapping[str, Any] | None = None
    if destination["kind"] == "user":
        target = users.get(target_id)
        if target_id == request["user_id"]:
            raise SpatialNavigationError("NAV_SELF_DESTINATION_FORBIDDEN", target_id)
    else:
        target = objects.get(target_id)
    if target is None:
        raise SpatialNavigationError("NAV_DESTINATION_NOT_FOUND", target_id)
    if destination["kind"] == "seat":
        capabilities = target.get("capabilities")
        if (
            target.get("object_type") not in {"seat", "chair"}
            or target.get("occupied") is not False
            or not isinstance(capabilities, list)
            or "sit" not in capabilities
            or target.get("reachable") is not True
        ):
            raise SpatialNavigationError("NAV_SEAT_UNAVAILABLE", target_id)
    if destination["require_visibility"] and target.get("visible") is not True:
        raise SpatialNavigationError("NAV_DESTINATION_NOT_VISIBLE", target_id)
    goal = _cell(target.get("nav_cell"), "destination.nav_cell", width=width, height=height)
    if start in blocked or start in occluded:
        raise SpatialNavigationError("NAV_ACTOR_CELL_UNSAFE", str(start))
    if goal in blocked:
        raise SpatialNavigationError("NAV_DESTINATION_BLOCKED", str(goal))
    if goal in occluded:
        raise SpatialNavigationError("NAV_DESTINATION_OCCLUDED", str(goal))
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "width": width,
        "height": height,
        "cell_size_m": cell_size,
        "blocked": blocked,
        "occluded": occluded,
        "start": start,
        "goal": goal,
    }


def _goal_cells(
    goal: tuple[int, int],
    radius: int,
    *,
    width: int,
    height: int,
    forbidden: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    goals = {
        (x, y)
        for x in range(width)
        for y in range(height)
        if abs(x - goal[0]) + abs(y - goal[1]) == radius and (x, y) not in forbidden
    }
    if not goals:
        raise SpatialNavigationError("NAV_NO_SAFE_ARRIVAL_CELL", str(goal))
    return goals


def build_route_plan(request: Mapping[str, Any], world: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic shortest 4-neighbour path around obstacles/occlusion."""

    forbidden = set(world["blocked"]) | set(world["occluded"])
    goals = _goal_cells(
        world["goal"],
        request["destination"]["arrival_radius_cells"],
        width=world["width"],
        height=world["height"],
        forbidden=forbidden,
    )
    start = world["start"]
    queue: deque[tuple[int, int]] = deque([start])
    parents: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    arrival: tuple[int, int] | None = None
    while queue:
        current = queue.popleft()
        if current in goals:
            arrival = current
            break
        for dx, dy in _NEIGHBOR_DELTAS:
            candidate = current[0] + dx, current[1] + dy
            if (
                0 <= candidate[0] < world["width"]
                and 0 <= candidate[1] < world["height"]
                and candidate not in forbidden
                and candidate not in parents
            ):
                parents[candidate] = current
                queue.append(candidate)
    if arrival is None:
        raise SpatialNavigationError("NAV_ROUTE_NOT_FOUND", request["destination"]["target_id"])
    path: list[tuple[int, int]] = []
    cursor: tuple[int, int] | None = arrival
    while cursor is not None:
        path.append(cursor)
        cursor = parents[cursor]
    path.reverse()
    if len(path) > request["max_waypoints"]:
        raise SpatialNavigationError("NAV_ROUTE_TOO_LONG", str(len(path)))
    distance_m = (len(path) - 1) * world["cell_size_m"]
    action = request["action_request"]["intent"]
    if action["type"] == "approach" and distance_m > float(action["parameters"]["max_travel_distance_m"]):
        raise SpatialNavigationError("NAV_ROUTE_DISTANCE_EXCEEDS_ACTION_LIMIT", str(distance_m))
    plan = {
        "schema_version": PLAN_SCHEMA,
        "navigation_id": request["navigation_id"],
        "correlation_id": request["correlation_id"],
        "user_id": request["user_id"],
        "snapshot_id": world["snapshot_id"],
        "destination_id": request["destination"]["target_id"],
        "destination_kind": request["destination"]["kind"],
        "destination_cell": list(world["goal"]),
        "arrival_cell": list(arrival),
        "waypoints": [list(cell) for cell in path],
        "waypoint_count": len(path),
        "route_distance_m": distance_m,
        "obstacle_cells_avoided": len(world["blocked"]),
        "occluded_cells_avoided": len(world["occluded"]),
        "deterministic_neighbor_order": [list(item) for item in _NEIGHBOR_DELTAS],
    }
    plan["plan_sha256"] = _sha256(plan)
    return plan


def _validate_action_binding(request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        action = validate_embodied_action_request(request["action_request"])
    except EmbodiedActionError as exc:
        raise SpatialNavigationError("NAV_ACTION_REQUEST_INVALID", exc.code) from exc
    bindings = {
        "dispatch_id": f"action:{request['navigation_id']}",
        "idempotency_key": f"action:{request['idempotency_key']}",
        "correlation_id": request["correlation_id"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "session_id": request["session_id"],
        "requested_at": request["requested_at"],
        "deadline_at": request["deadline_at"],
    }
    for field_name, expected in bindings.items():
        if action.get(field_name) != expected:
            raise SpatialNavigationError("NAV_ACTION_BINDING_MISMATCH", field_name)
    expected_intent = "sit" if request["destination"]["kind"] == "seat" else "approach"
    if action["intent"]["type"] != expected_intent:
        raise SpatialNavigationError("NAV_ACTION_INTENT_MISMATCH", expected_intent)
    if action["intent"]["target_id"] != request["destination"]["target_id"]:
        raise SpatialNavigationError("NAV_ACTION_TARGET_MISMATCH", "target_id")
    if action["cancel_requested"] is not False:
        raise SpatialNavigationError("NAV_ACTION_CANCEL_FLAG_INVALID", "action_request")
    if _sha256(action["world_state"]) != _sha256(request["world_state"]):
        raise SpatialNavigationError("NAV_ACTION_WORLD_MISMATCH", "world_state")
    return action


def _base(value: Any) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    destination = source.get("destination") if isinstance(source.get("destination"), Mapping) else {}

    def safe(item: Any, fallback: str) -> str:
        return item if isinstance(item, str) and _ID_PATTERN.fullmatch(item) else fallback

    return {
        "navigation_id": safe(source.get("navigation_id"), "invalid-navigation"),
        "correlation_id": safe(source.get("correlation_id"), "invalid-correlation"),
        "tenant_id": safe(source.get("tenant_id"), "invalid-tenant"),
        "user_id": safe(source.get("user_id"), "invalid-user"),
        "destination_id": safe(destination.get("target_id"), "invalid-destination"),
    }


def _outcomes() -> dict[str, dict[str, Any]]:
    return {stage: {"outcome": "SKIPPED", "reason": "not_reached"} for stage in TRACE_STAGES}


def _trace(base: Mapping[str, str], outcomes: Mapping[str, Mapping[str, Any]], occurred_at: str) -> list[dict[str, Any]]:
    events = []
    for sequence, stage in enumerate(TRACE_STAGES, start=1):
        event = {
            "schema_version": TRACE_SCHEMA,
            "trace_id": f"nav-trace:{base['navigation_id']}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "outcome": outcomes[stage]["outcome"],
            "reason": outcomes[stage]["reason"],
            "navigation_id": base["navigation_id"],
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
    plan: Mapping[str, Any] | None = None,
    action_result: Mapping[str, Any] | None = None,
    duplicate_of_result_sha256: str | None = None,
) -> dict[str, Any]:
    outcomes["RESULT"] = {"outcome": "TERMINAL", "reason": reason, "detail": {"status": status}}
    occurred_at = request["requested_at"] if request is not None else "1970-01-01T00:00:00Z"
    result = {
        "schema_version": RESULT_SCHEMA,
        "service_version": SERVICE_VERSION,
        "navigation_id": base["navigation_id"],
        "correlation_id": base["correlation_id"],
        "tenant_id": base["tenant_id"],
        "user_id": base["user_id"],
        "destination_id": base["destination_id"],
        "status": status,
        "reason": reason,
        "navigation_success": status == "NAVIGATED",
        "route_plan": _copy(plan),
        "action_status": action_result.get("status") if action_result else None,
        "action_result_sha256": action_result.get("result_sha256") if action_result else None,
        "fake_dispatch_invoked": bool(action_result and action_result.get("fake_adapter_invoked")),
        "rollback_performed": bool(action_result and action_result.get("rollback", {}).get("performed")),
        "external_execution": False,
        "godot_action_fired": False,
        "p4_08_approved": False,
        "g5_approved": False,
        "duplicate_of_result_sha256": duplicate_of_result_sha256,
        "trace": _trace(base, outcomes, occurred_at),
    }
    result["result_sha256"] = _sha256(result)
    return result


class SpatialNavigationService:
    """Deterministic planner with an exact P5-01 local-fake dispatch boundary."""

    def __init__(self, action_service: EmbodiedActionService) -> None:
        if type(action_service) is not EmbodiedActionService or type(action_service.adapter) is not LocalFakeEmbodiedAdapter:
            raise SpatialNavigationError("NAV_LIVE_DISPATCH_FORBIDDEN", type(action_service).__name__)
        self.action_service = action_service
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._navigation_owners: dict[str, tuple[str, str, str]] = {}
        self._pending: dict[str, dict[str, str]] = {}

    def navigate(self, value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        base = _base(value)
        outcomes = _outcomes()
        try:
            request = validate_navigation_request(value)
            base = _base(request)
            outcomes["VALIDATE"] = {"outcome": "PASS", "reason": "request_contract_valid"}
        except SpatialNavigationError as exc:
            outcomes["VALIDATE"] = {"outcome": "BLOCK", "reason": exc.code}
            return _finish(None, base, outcomes, status="BLOCKED", reason=exc.code)
        requested_at = _parse_time(request["requested_at"], "requested_at")
        current = _current(now, requested_at)
        if current >= _parse_time(request["deadline_at"], "deadline_at"):
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "deadline_expired"}
            return _finish(request, base, outcomes, status="TIMED_OUT", reason="deadline_expired")
        if request["cancel_requested"]:
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "cancelled_before_plan"}
            return _finish(request, base, outcomes, status="CANCELLED", reason="cancelled_before_plan")

        request_hash = _sha256(request)
        scope = (request["tenant_id"], request["user_id"], request["idempotency_key"])
        owner = self._navigation_owners.get(request["navigation_id"])
        if owner is not None and owner != scope:
            outcomes["IDEMPOTENCY"] = {"outcome": "BLOCK", "reason": "navigation_scope_conflict"}
            return _finish(request, base, outcomes, status="IDEMPOTENCY_CONFLICT", reason="navigation_scope_conflict")
        prior = self._idempotency.get(scope)
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
        outcomes["IDEMPOTENCY"] = {"outcome": "PASS", "reason": "new_user_scoped_key"}

        try:
            action = _validate_action_binding(request)
            world = _world_and_cells(request, now=current)
            outcomes["WORLD_STATE"] = {
                "outcome": "PASS",
                "reason": "fresh_safe_world_state",
                "detail": {"snapshot_id": world["snapshot_id"], "start": list(world["start"]), "goal": list(world["goal"])},
            }
            plan = build_route_plan(request, world)
            outcomes["ROUTE_PLAN"] = {
                "outcome": "PASS",
                "reason": "deterministic_safe_route_found",
                "detail": {"plan_sha256": plan["plan_sha256"], "waypoint_count": plan["waypoint_count"]},
            }
        except SpatialNavigationError as exc:
            stage = "WORLD_STATE" if outcomes["WORLD_STATE"]["outcome"] == "SKIPPED" else "ROUTE_PLAN"
            outcomes[stage] = {"outcome": "BLOCK", "reason": exc.code}
            outcomes["DISPATCH"] = {"outcome": "NOT_CALLED", "reason": "planning_failed_closed"}
            return _finish(request, base, outcomes, status="BLOCKED", reason=exc.code)

        self._navigation_owners[request["navigation_id"]] = scope
        self._idempotency[scope] = {"request_sha256": request_hash, "result_sha256": None}
        action_result = self.action_service.dispatch(action, now=current)
        nested_risk = next(event for event in action_result["trace"] if event["stage"] == "RISK_PERMISSION")
        outcomes["RISK_PERMISSION"] = {
            "outcome": nested_risk["outcome"],
            "reason": nested_risk["reason"],
            "detail": {"action_result_sha256": action_result["result_sha256"]},
        }
        outcomes["DISPATCH"] = {
            "outcome": "CALLED_FAKE_ONLY" if action_result["fake_adapter_invoked"] else "NOT_CALLED",
            "reason": action_result["status"].lower(),
            "detail": {"external_execution": False, "godot_action_fired": False},
        }
        outcomes["ROLLBACK"] = {
            "outcome": "PASS" if action_result["rollback"]["performed"] else "SKIPPED",
            "reason": action_result["rollback"]["status"].lower(),
        }
        status_map = {
            "COMPLETED": "NAVIGATED",
            "PENDING": "PENDING",
            "FAILED_ROLLED_BACK": "NAVIGATION_FAILED_ROLLED_BACK",
            "TIMED_OUT_ROLLED_BACK": "TIMED_OUT_ROLLED_BACK",
            "CANCELLED": "CANCELLED",
            "TIMED_OUT": "TIMED_OUT",
        }
        result_status = status_map.get(action_result["status"], "BLOCKED")
        reason = "safe_route_fake_dispatched" if result_status == "NAVIGATED" else action_result["reason"]
        result = _finish(
            request,
            base,
            outcomes,
            status=result_status,
            reason=reason,
            plan=plan,
            action_result=action_result,
        )
        self._idempotency[scope]["result_sha256"] = result["result_sha256"]
        if result_status == "PENDING":
            self._pending[request["navigation_id"]] = {
                "action_dispatch_id": action["dispatch_id"],
                "tenant_id": request["tenant_id"],
                "user_id": request["user_id"],
                "correlation_id": request["correlation_id"],
            }
        return result

    def cancel(
        self,
        *,
        navigation_id: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        navigation = _identifier(navigation_id, "navigation_id")
        tenant = _identifier(tenant_id, "tenant_id")
        user = _identifier(user_id, "user_id")
        correlation = _identifier(correlation_id, "correlation_id")
        pending = self._pending.get(navigation)
        action_cancel: dict[str, Any] | None = None
        status = "BLOCKED_NOT_FOUND"
        if pending is not None:
            if (pending["tenant_id"], pending["user_id"], pending["correlation_id"]) != (tenant, user, correlation):
                status = "BLOCKED_SCOPE_MISMATCH"
            else:
                action_cancel = self.action_service.cancel(
                    dispatch_id=pending["action_dispatch_id"],
                    tenant_id=tenant,
                    user_id=user,
                    correlation_id=correlation,
                )
                status = action_cancel["status"]
                self._pending.pop(navigation, None)
        result = {
            "schema_version": CANCEL_RESULT_SCHEMA,
            "navigation_id": navigation,
            "tenant_id": tenant,
            "user_id": user,
            "correlation_id": correlation,
            "status": status,
            "action_cancel_sha256": action_cancel.get("cancel_sha256") if action_cancel else None,
            "rollback_performed": bool(action_cancel and action_cancel.get("rollback_performed")),
            "external_execution": False,
            "godot_action_fired": False,
            "p4_08_approved": False,
            "g5_approved": False,
        }
        result["cancel_sha256"] = _sha256(result)
        return result

