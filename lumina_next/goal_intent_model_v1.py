"""Deterministic, user-scoped goal/intent model for P4-02.

The model turns current role, user-objective, and venue-objective context into
ranked short/medium goal candidates.  It is side-effect free and is designed
to be installed as the goal selector of :mod:`autonomy_orchestrator_v1`.
Unsafe or stale context produces only ``safe_idle``; cross-user context is
rejected before any decision can be emitted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .autonomy_orchestrator_v1 import AUTONOMY_INPUT_SCHEMA, AutonomyHandlers


GOAL_CONTEXT_SCHEMA = "lumina.goal.intent.context.v1"
GOAL_DECISION_SCHEMA = "lumina.goal.intent.decision.v1"
GOAL_POLICY_VERSION = "lumina.goal.intent.policy.v1"

HORIZONS = ("short", "medium")
LIFECYCLE_ACTIONS = (
    "START",
    "CONTINUE",
    "INTERRUPT",
    "PREEMPT",
    "RESUME",
    "CANCEL",
    "SAFE_IDLE",
)
SAFE_GOAL_TYPES = frozenset({
    "answer_user",
    "explain_context",
    "guide_user",
    "maintain_role",
    "manage_venue_flow",
    "preserve_comfort",
    "support_visit",
})
UNSAFE_GOAL_TYPES = frozenset({
    "bypass_safety",
    "disclose_private_data",
    "external_purchase",
    "unrestricted_external_action",
})
SIGNALS = frozenset({
    "emergency_stop",
    "higher_priority_goal",
    "interrupt_request",
    "objective_completed",
    "resume_ready",
    "role_revoked",
    "user_cancel",
})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SOURCE_BASE = {"user": 600, "venue": 400, "role": 200}
_CONDITIONS = {
    "interrupt_when": ["emergency_stop", "interrupt_request"],
    "preempt_when": ["higher_priority_goal", "higher_scored_current_goal"],
    "resume_when": [
        "resume_ready",
        "context_safe_and_current",
        "same_user_and_objective_active",
    ],
    "cancel_when": [
        "user_cancel",
        "objective_completed",
        "role_revoked",
        "unsafe_or_stale_context",
        "context_owner_changed",
    ],
}


class GoalIntentError(ValueError):
    """Contract or isolation failure with a stable machine-readable code."""

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
        raise GoalIntentError(
            "GOAL_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoalIntentError(code, detail)
    return value


def _exact_fields(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    prefix: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GoalIntentError(f"GOAL_{prefix}_UNKNOWN_FIELDS", ",".join(unknown))
    missing = sorted(required - set(value))
    if missing:
        raise GoalIntentError(f"GOAL_{prefix}_FIELDS_REQUIRED", ",".join(missing))


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise GoalIntentError(
            "GOAL_ID_INVALID",
            f"{field_name} must be a canonical identifier",
        )
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise GoalIntentError("GOAL_TIMESTAMP_REQUIRED", "observed_at is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoalIntentError(
            "GOAL_TIMESTAMP_INVALID",
            "observed_at must be ISO-8601",
        ) from exc
    if parsed.tzinfo is None:
        raise GoalIntentError(
            "GOAL_TIMESTAMP_TIMEZONE_REQUIRED",
            "observed_at must include a timezone",
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise GoalIntentError("GOAL_BOOLEAN_REQUIRED", field_name)
    return value


def _priority(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise GoalIntentError(
            "GOAL_PRIORITY_INVALID",
            f"{field_name} must be an integer from 0 through 100",
        )
    return value


def _revision(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GoalIntentError(
            "GOAL_REVISION_INVALID",
            f"{field_name} must be a positive integer",
        )
    return value


def _description(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise GoalIntentError(
            "GOAL_DESCRIPTION_INVALID",
            f"{field_name} must be 1..256 characters",
        )
    return value


def _horizon(value: Any, field_name: str) -> str:
    if value not in HORIZONS:
        raise GoalIntentError("GOAL_HORIZON_INVALID", field_name)
    return str(value)


def _normalize_role(value: Any) -> dict[str, Any]:
    role = _mapping(value, "GOAL_ROLE_REQUIRED", "role must be an object")
    fields = {
        "role_id",
        "objective_id",
        "goal_type",
        "description",
        "priority",
        "horizon",
        "allowed_goal_types",
        "is_stale",
    }
    _exact_fields(role, allowed=fields, required=fields, prefix="ROLE")
    allowed_types = role["allowed_goal_types"]
    if (
        not isinstance(allowed_types, list)
        or not allowed_types
        or any(not isinstance(item, str) or not _ID_PATTERN.fullmatch(item) for item in allowed_types)
        or len(set(allowed_types)) != len(allowed_types)
    ):
        raise GoalIntentError(
            "GOAL_ROLE_ALLOWED_TYPES_INVALID",
            "allowed_goal_types must be a non-empty unique identifier list",
        )
    return {
        "role_id": _identifier(role["role_id"], "role.role_id"),
        "objective_id": _identifier(role["objective_id"], "role.objective_id"),
        "goal_type": _identifier(role["goal_type"], "role.goal_type"),
        "description": _description(role["description"], "role.description"),
        "priority": _priority(role["priority"], "role.priority"),
        "horizon": _horizon(role["horizon"], "role.horizon"),
        "allowed_goal_types": sorted(allowed_types),
        "is_stale": _boolean(role["is_stale"], "role.is_stale"),
    }


def _normalize_objective(value: Any, source: str) -> dict[str, Any] | None:
    if value is None:
        return None
    objective = _mapping(
        value,
        f"GOAL_{source.upper()}_OBJECTIVE_INVALID",
        f"{source}_objective must be an object or null",
    )
    owner_field = "user_id" if source == "user" else "venue_id"
    fields = {
        owner_field,
        "objective_id",
        "goal_type",
        "description",
        "priority",
        "horizon",
        "is_stale",
    }
    if source == "user":
        fields.add("explicit")
    _exact_fields(objective, allowed=fields, required=fields, prefix=f"{source.upper()}_OBJECTIVE")
    result = {
        owner_field: _identifier(objective[owner_field], f"{source}_objective.{owner_field}"),
        "objective_id": _identifier(objective["objective_id"], f"{source}_objective.objective_id"),
        "goal_type": _identifier(objective["goal_type"], f"{source}_objective.goal_type"),
        "description": _description(objective["description"], f"{source}_objective.description"),
        "priority": _priority(objective["priority"], f"{source}_objective.priority"),
        "horizon": _horizon(objective["horizon"], f"{source}_objective.horizon"),
        "is_stale": _boolean(objective["is_stale"], f"{source}_objective.is_stale"),
    }
    if source == "user":
        result["explicit"] = _boolean(objective["explicit"], "user_objective.explicit")
    return result


def _normalize_world(value: Any) -> dict[str, Any]:
    world = _mapping(value, "GOAL_WORLD_REQUIRED", "world_state must be an object")
    fields = {"safe_to_use", "is_stale", "risk_level", "revision"}
    _exact_fields(world, allowed=fields, required=fields, prefix="WORLD")
    risk = world["risk_level"]
    if risk not in {"low", "normal", "elevated", "high"}:
        raise GoalIntentError("GOAL_RISK_LEVEL_INVALID", "world_state.risk_level")
    return {
        "safe_to_use": _boolean(world["safe_to_use"], "world_state.safe_to_use"),
        "is_stale": _boolean(world["is_stale"], "world_state.is_stale"),
        "risk_level": risk,
        "revision": _revision(world["revision"], "world_state.revision"),
    }


def _normalize_relationship(value: Any) -> dict[str, Any]:
    relationship = _mapping(
        value,
        "GOAL_RELATIONSHIP_REQUIRED",
        "relationship_state must be an object",
    )
    fields = {"user_id", "continuity", "is_stale", "revision"}
    _exact_fields(relationship, allowed=fields, required=fields, prefix="RELATIONSHIP")
    continuity = relationship["continuity"]
    if continuity not in {"new", "known", "returning"}:
        raise GoalIntentError(
            "GOAL_RELATIONSHIP_CONTINUITY_INVALID",
            "relationship_state.continuity",
        )
    return {
        "user_id": _identifier(relationship["user_id"], "relationship_state.user_id"),
        "continuity": continuity,
        "is_stale": _boolean(relationship["is_stale"], "relationship_state.is_stale"),
        "revision": _revision(relationship["revision"], "relationship_state.revision"),
    }


def _normalize_active(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    active = _mapping(value, "GOAL_ACTIVE_INVALID", "active_goal must be an object or null")
    fields = {
        "goal_id",
        "user_id",
        "goal_type",
        "horizon",
        "priority_score",
        "status",
        "source",
        "source_id",
        "is_stale",
    }
    _exact_fields(active, allowed=fields, required=fields, prefix="ACTIVE")
    score = active["priority_score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 1000:
        raise GoalIntentError("GOAL_ACTIVE_PRIORITY_INVALID", "active_goal.priority_score")
    status = active["status"]
    if status not in {"ACTIVE", "PAUSED"}:
        raise GoalIntentError("GOAL_ACTIVE_STATUS_INVALID", "active_goal.status")
    source = active["source"]
    if source not in {"user", "venue", "role"}:
        raise GoalIntentError("GOAL_ACTIVE_SOURCE_INVALID", "active_goal.source")
    return {
        "goal_id": _identifier(active["goal_id"], "active_goal.goal_id"),
        "user_id": _identifier(active["user_id"], "active_goal.user_id"),
        "goal_type": _identifier(active["goal_type"], "active_goal.goal_type"),
        "horizon": _horizon(active["horizon"], "active_goal.horizon"),
        "priority_score": score,
        "status": status,
        "source": source,
        "source_id": _identifier(active["source_id"], "active_goal.source_id"),
        "is_stale": _boolean(active["is_stale"], "active_goal.is_stale"),
    }


def validate_goal_context(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete, explicitly current, single-user goal context."""

    source = _mapping(value, "GOAL_CONTEXT_OBJECT_REQUIRED", "context must be an object")
    fields = {
        "schema_version",
        "decision_id",
        "correlation_id",
        "observed_at",
        "actor_user_id",
        "role",
        "user_objective",
        "venue_objective",
        "world_state",
        "relationship_state",
        "active_goal",
        "signals",
    }
    _exact_fields(source, allowed=fields, required=fields, prefix="CONTEXT")
    if source["schema_version"] != GOAL_CONTEXT_SCHEMA:
        raise GoalIntentError("GOAL_CONTEXT_SCHEMA_MISMATCH", "unsupported schema_version")

    actor_user_id = _identifier(source["actor_user_id"], "actor_user_id")
    role = _normalize_role(source["role"])
    user_objective = _normalize_objective(source["user_objective"], "user")
    venue_objective = _normalize_objective(source["venue_objective"], "venue")
    world_state = _normalize_world(source["world_state"])
    relationship_state = _normalize_relationship(source["relationship_state"])
    active_goal = _normalize_active(source["active_goal"])

    signals = source["signals"]
    if (
        not isinstance(signals, list)
        or any(item not in SIGNALS for item in signals)
        or len(set(signals)) != len(signals)
    ):
        raise GoalIntentError(
            "GOAL_SIGNALS_INVALID",
            "signals must be a unique supported-signal list",
        )

    owner_ids = [relationship_state["user_id"]]
    if user_objective is not None:
        owner_ids.append(user_objective["user_id"])
    if active_goal is not None:
        owner_ids.append(active_goal["user_id"])
    if any(owner_id != actor_user_id for owner_id in owner_ids):
        raise GoalIntentError(
            "GOAL_CROSS_USER_CONTEXT_REJECTED",
            "all user-scoped context must match actor_user_id",
        )

    _canonical_json(source)
    return {
        "schema_version": GOAL_CONTEXT_SCHEMA,
        "decision_id": _identifier(source["decision_id"], "decision_id"),
        "correlation_id": _identifier(source["correlation_id"], "correlation_id"),
        "observed_at": _timestamp(source["observed_at"]),
        "actor_user_id": actor_user_id,
        "role": role,
        "user_objective": user_objective,
        "venue_objective": venue_objective,
        "world_state": world_state,
        "relationship_state": relationship_state,
        "active_goal": active_goal,
        "signals": sorted(signals),
    }


def _candidate(source: str, objective: Mapping[str, Any]) -> dict[str, Any]:
    explicit_bonus = 100 if source == "user" and objective.get("explicit") is True else 0
    horizon_bonus = 20 if objective["horizon"] == "short" else 0
    score = _SOURCE_BASE[source] + objective["priority"] + explicit_bonus + horizon_bonus
    source_id = objective["objective_id"]
    return {
        "goal_id": f"goal:{source}:{source_id}:{objective['horizon']}",
        "goal_type": objective["goal_type"],
        "horizon": objective["horizon"],
        "source": source,
        "source_id": source_id,
        "priority_score": score,
        "explanation": (
            f"{source} objective {source_id} supports {objective['goal_type']} "
            f"under current role policy"
        ),
        "evidence_refs": [f"{source}:{source_id}"],
        "lifecycle_conditions": _copy(_CONDITIONS),
    }


def _active_candidate(active: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "goal_id": active["goal_id"],
        "goal_type": active["goal_type"],
        "horizon": active["horizon"],
        "source": active["source"],
        "source_id": active["source_id"],
        "priority_score": active["priority_score"],
        "explanation": "active goal remains eligible for same-user continuation",
        "evidence_refs": [f"active:{active['source_id']}"],
        "lifecycle_conditions": _copy(_CONDITIONS),
    }


def _safe_idle(reason: str) -> dict[str, Any]:
    return {
        "goal_id": "safe_idle",
        "goal_type": "safe_idle",
        "horizon": "short",
        "source": "safety",
        "source_id": reason,
        "priority_score": 1000,
        "explanation": f"fail closed: {reason}",
        "evidence_refs": [f"safety:{reason}"],
        "lifecycle_conditions": _copy(_CONDITIONS),
    }


def _context_fail_reason(context: Mapping[str, Any]) -> str | None:
    world = context["world_state"]
    if world["is_stale"]:
        return "stale_world_state"
    if not world["safe_to_use"] or world["risk_level"] in {"elevated", "high"}:
        return "unsafe_world_state"
    if context["relationship_state"]["is_stale"]:
        return "stale_relationship_context"
    if context["role"]["is_stale"]:
        return "stale_role_context"
    for key in ("user_objective", "venue_objective", "active_goal"):
        value = context[key]
        if value is not None and value["is_stale"]:
            return f"stale_{key}"
    goal_types = [context["role"]["goal_type"]]
    goal_types.extend(
        context[key]["goal_type"]
        for key in ("user_objective", "venue_objective", "active_goal")
        if context[key] is not None
    )
    if any(goal_type in UNSAFE_GOAL_TYPES for goal_type in goal_types):
        return "unsafe_goal_type"
    if any(goal_type not in SAFE_GOAL_TYPES for goal_type in goal_types):
        return "unsupported_goal_type"
    return None


def _sealed_decision(
    context: Mapping[str, Any],
    *,
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
    lifecycle_action: str,
    fail_closed: bool,
    fail_closed_reason: str | None,
) -> dict[str, Any]:
    decision = {
        "schema_version": GOAL_DECISION_SCHEMA,
        "policy_version": GOAL_POLICY_VERSION,
        "decision_id": context["decision_id"],
        "correlation_id": context["correlation_id"],
        "observed_at": context["observed_at"],
        "actor_user_id": context["actor_user_id"],
        "context_sha256": _sha256(context),
        "selected_goal": _copy(selected),
        "candidates": _copy(candidates),
        "lifecycle_action": lifecycle_action,
        "fail_closed": fail_closed,
        "fail_closed_reason": fail_closed_reason,
    }
    decision["decision_sha256"] = _sha256(decision)
    return decision


class GoalIntentModel:
    """Rank current goal candidates and determine one lifecycle transition."""

    def evaluate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        context = validate_goal_context(value)
        active = context["active_goal"]
        signals = set(context["signals"])
        fail_reason = _context_fail_reason(context)

        if fail_reason is not None:
            idle = _safe_idle(fail_reason)
            action = "CANCEL" if active is not None else "SAFE_IDLE"
            return _sealed_decision(
                context,
                candidates=[idle],
                selected=idle,
                lifecycle_action=action,
                fail_closed=True,
                fail_closed_reason=fail_reason,
            )

        if "emergency_stop" in signals:
            idle = _safe_idle("emergency_stop")
            return _sealed_decision(
                context,
                candidates=[idle],
                selected=idle,
                lifecycle_action="INTERRUPT",
                fail_closed=True,
                fail_closed_reason="emergency_stop",
            )

        for signal in ("user_cancel", "objective_completed", "role_revoked"):
            if signal in signals:
                idle = _safe_idle(signal)
                return _sealed_decision(
                    context,
                    candidates=[idle],
                    selected=idle,
                    lifecycle_action="CANCEL",
                    fail_closed=True,
                    fail_closed_reason=signal,
                )

        allowed = set(context["role"]["allowed_goal_types"])
        if active is not None and active["goal_type"] not in allowed:
            idle = _safe_idle("active_goal_not_allowed")
            return _sealed_decision(
                context,
                candidates=[idle],
                selected=idle,
                lifecycle_action="CANCEL",
                fail_closed=True,
                fail_closed_reason="active_goal_not_allowed",
            )
        source_values = [
            ("role", context["role"]),
            ("user", context["user_objective"]),
            ("venue", context["venue_objective"]),
        ]
        candidates = [
            _candidate(source, objective)
            for source, objective in source_values
            if objective is not None and objective["goal_type"] in allowed
        ]
        candidates.sort(key=lambda item: (-item["priority_score"], item["goal_id"]))

        if not candidates:
            idle = _safe_idle("no_allowed_goal_candidates")
            return _sealed_decision(
                context,
                candidates=[idle],
                selected=idle,
                lifecycle_action="CANCEL" if active is not None else "SAFE_IDLE",
                fail_closed=True,
                fail_closed_reason="no_allowed_goal_candidates",
            )

        top = candidates[0]
        selected = top
        action = "START"
        if active is not None:
            active_candidate = next(
                (item for item in candidates if item["goal_id"] == active["goal_id"]),
                _active_candidate(active),
            )
            if all(item["goal_id"] != active["goal_id"] for item in candidates):
                candidates.append(active_candidate)
                candidates.sort(key=lambda item: (-item["priority_score"], item["goal_id"]))
            if (
                active["status"] == "PAUSED"
                and "resume_ready" in signals
                and active["priority_score"] >= top["priority_score"]
            ):
                selected = active_candidate
                action = "RESUME"
            elif active["status"] == "PAUSED" and top["priority_score"] > active["priority_score"]:
                selected = top
                action = "PREEMPT"
            elif active["status"] == "PAUSED":
                idle = _safe_idle("resume_conditions_not_met")
                return _sealed_decision(
                    context,
                    candidates=[idle],
                    selected=idle,
                    lifecycle_action="SAFE_IDLE",
                    fail_closed=True,
                    fail_closed_reason="resume_conditions_not_met",
                )
            elif "interrupt_request" in signals and top["goal_id"] != active["goal_id"]:
                selected = top
                action = "INTERRUPT"
            elif top["goal_id"] == active["goal_id"]:
                selected = top
                action = "CONTINUE"
            elif top["priority_score"] > active["priority_score"]:
                selected = top
                action = "PREEMPT"
            else:
                selected = active_candidate
                action = "CONTINUE"

        return _sealed_decision(
            context,
            candidates=candidates,
            selected=selected,
            lifecycle_action=action,
            fail_closed=False,
            fail_closed_reason=None,
        )

    def autonomy_goal_selector(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """P4-01 ``AutonomyHandlers.goal_selector`` adapter."""

        outer = _mapping(
            context,
            "GOAL_AUTONOMY_CONTEXT_REQUIRED",
            "autonomy goal-selector context must be an object",
        )
        autonomy_input = _mapping(
            outer.get("input"),
            "GOAL_AUTONOMY_INPUT_REQUIRED",
            "autonomy input is required",
        )
        if autonomy_input.get("schema_version") != AUTONOMY_INPUT_SCHEMA:
            raise GoalIntentError(
                "GOAL_AUTONOMY_SCHEMA_MISMATCH",
                "unsupported autonomy input schema",
            )
        outer_world = _mapping(
            autonomy_input.get("world_state"),
            "GOAL_AUTONOMY_WORLD_REQUIRED",
            "autonomy world_state is required",
        )
        embedded = outer_world.get("goal_intent_context")
        if not isinstance(embedded, Mapping):
            raise GoalIntentError(
                "GOAL_AUTONOMY_EMBEDDED_CONTEXT_REQUIRED",
                "world_state.goal_intent_context is required",
            )
        goal_context = _copy(dict(embedded))
        if goal_context.get("correlation_id") != autonomy_input.get("correlation_id"):
            raise GoalIntentError(
                "GOAL_AUTONOMY_CORRELATION_MISMATCH",
                "embedded and autonomy correlation_id must match",
            )
        outer_relationship = autonomy_input.get("relationship_state")
        if isinstance(outer_relationship, Mapping) and outer_relationship.get("user_id") not in {
            None,
            goal_context.get("actor_user_id"),
        }:
            raise GoalIntentError(
                "GOAL_CROSS_USER_CONTEXT_REJECTED",
                "autonomy relationship owner must match actor_user_id",
            )
        if outer_world.get("safe_to_use") is not True:
            goal_context["world_state"]["safe_to_use"] = False
        if outer_world.get("is_stale") is True:
            goal_context["world_state"]["is_stale"] = True
        decision = self.evaluate(goal_context)
        return {
            "goal_id": decision["selected_goal"]["goal_id"],
            "reason": decision["selected_goal"]["explanation"],
            "lifecycle_action": decision["lifecycle_action"],
            "goal_intent_decision": decision,
        }


def build_autonomy_handlers(model: GoalIntentModel | None = None) -> AutonomyHandlers:
    """Return P4-01 handlers with this model installed as goal selector."""

    instance = model or GoalIntentModel()
    return AutonomyHandlers(goal_selector=instance.autonomy_goal_selector)


def validate_goal_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the sealed deterministic decision envelope."""

    source = _mapping(value, "GOAL_DECISION_OBJECT_REQUIRED", "decision must be an object")
    required = {
        "schema_version",
        "policy_version",
        "decision_id",
        "correlation_id",
        "observed_at",
        "actor_user_id",
        "context_sha256",
        "selected_goal",
        "candidates",
        "lifecycle_action",
        "fail_closed",
        "fail_closed_reason",
        "decision_sha256",
    }
    _exact_fields(source, allowed=required, required=required, prefix="DECISION")
    if source["schema_version"] != GOAL_DECISION_SCHEMA:
        raise GoalIntentError("GOAL_DECISION_SCHEMA_MISMATCH", "unsupported decision schema")
    if source["policy_version"] != GOAL_POLICY_VERSION:
        raise GoalIntentError("GOAL_POLICY_VERSION_MISMATCH", "unsupported policy version")
    if source["lifecycle_action"] not in LIFECYCLE_ACTIONS:
        raise GoalIntentError("GOAL_LIFECYCLE_ACTION_INVALID", "unsupported lifecycle action")
    candidates = source["candidates"]
    selected = source["selected_goal"]
    if not isinstance(candidates, list) or not candidates or not isinstance(selected, Mapping):
        raise GoalIntentError("GOAL_DECISION_CANDIDATES_INVALID", "candidates/selected_goal invalid")
    if not any(item == selected for item in candidates):
        raise GoalIntentError("GOAL_SELECTED_NOT_CANDIDATE", "selected_goal must occur in candidates")
    expected_hash = _sha256({key: _copy(item) for key, item in source.items() if key != "decision_sha256"})
    if source["decision_sha256"] != expected_hash:
        raise GoalIntentError("GOAL_DECISION_TAMPERED", "decision hash mismatch")
    _canonical_json(source)
    return _copy(dict(source))
