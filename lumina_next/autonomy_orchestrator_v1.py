"""Deterministic, fail-closed autonomy loop for P4-01.

The loop is deliberately side-effect free by default.  It emits the complete
``observe -> interpret -> goal -> plan -> act -> reflect`` trace under one
correlation ID.  A caller may provide a local action adapter, but that adapter
is invoked only for an explicitly allowed, local-no-side-effect permission
receipt.  External action policy remains the responsibility of P4-03/P4-04.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping


AUTONOMY_INPUT_SCHEMA = "lumina.autonomy.input.v1"
AUTONOMY_TRACE_SCHEMA = "lumina.autonomy.trace.v1"
PERMISSION_SCHEMA = "lumina.permission.decision.v1"
STAGES = ("observe", "interpret", "goal", "plan", "act", "reflect")
MODES = ("observe_only", "dry_run", "execute_local")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class AutonomyLoopError(ValueError):
    """Contract or safety failure with a stable machine-readable code."""

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
        raise AutonomyLoopError(
            "AUTONOMY_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AutonomyLoopError(code, detail)
    return value


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise AutonomyLoopError(
            "AUTONOMY_ID_INVALID",
            f"{field_name} must be a canonical identifier",
        )
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise AutonomyLoopError(
            "AUTONOMY_TIMESTAMP_REQUIRED",
            "observed_at is required",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutonomyLoopError(
            "AUTONOMY_TIMESTAMP_INVALID",
            "observed_at must be ISO-8601",
        ) from exc
    if parsed.tzinfo is None:
        raise AutonomyLoopError(
            "AUTONOMY_TIMESTAMP_TIMEZONE_REQUIRED",
            "observed_at must include a timezone",
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_autonomy_input(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one strict autonomy-loop input without mutating the caller."""

    source = _mapping(
        value,
        "AUTONOMY_INPUT_OBJECT_REQUIRED",
        "loop input must be an object",
    )
    allowed = {
        "schema_version",
        "loop_id",
        "correlation_id",
        "observed_at",
        "world_state",
        "relationship_state",
        "requested_mode",
        "requested_goal",
        "permission_decision",
    }
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise AutonomyLoopError(
            "AUTONOMY_UNKNOWN_INPUT_FIELDS",
            ",".join(unknown),
        )
    if source.get("schema_version") != AUTONOMY_INPUT_SCHEMA:
        raise AutonomyLoopError(
            "AUTONOMY_INPUT_SCHEMA_MISMATCH",
            "unsupported schema_version",
        )
    loop_id = _identifier(source.get("loop_id"), "loop_id")
    correlation_id = _identifier(source.get("correlation_id"), "correlation_id")
    observed_at = _timestamp(source.get("observed_at"))
    world_state = _mapping(
        source.get("world_state"),
        "AUTONOMY_WORLD_STATE_REQUIRED",
        "world_state must be an object",
    )
    relationship_state = source.get("relationship_state", {})
    _mapping(
        relationship_state,
        "AUTONOMY_RELATIONSHIP_STATE_INVALID",
        "relationship_state must be an object",
    )
    mode = source.get("requested_mode")
    if mode not in MODES:
        raise AutonomyLoopError(
            "AUTONOMY_MODE_UNKNOWN",
            "requested_mode is unsupported",
        )
    requested_goal = source.get("requested_goal")
    if requested_goal is not None:
        _identifier(requested_goal, "requested_goal")
    permission = source.get("permission_decision")
    if permission is not None:
        _mapping(
            permission,
            "AUTONOMY_PERMISSION_INVALID",
            "permission_decision must be an object",
        )
    _canonical_json(source)
    return {
        "schema_version": AUTONOMY_INPUT_SCHEMA,
        "loop_id": loop_id,
        "correlation_id": correlation_id,
        "observed_at": observed_at,
        "world_state": _copy(world_state),
        "relationship_state": _copy(relationship_state),
        "requested_mode": mode,
        "requested_goal": requested_goal,
        "permission_decision": _copy(permission),
    }


def _default_interpreter(context: Mapping[str, Any]) -> dict[str, Any]:
    observed = context["observe"]
    world = observed["world_state"]
    safe_to_use = world.get("safe_to_use") is True
    facts = world.get("facts", [])
    fact_count = len(facts) if isinstance(facts, list) else 0
    return {
        "world_state_safe": safe_to_use,
        "fact_count": fact_count,
        "relationship_state_present": bool(observed["relationship_state"]),
        "explanation": (
            "current world state accepted"
            if safe_to_use
            else "world state absent, stale, conflicted, or not explicitly safe"
        ),
    }


def _default_goal_selector(context: Mapping[str, Any]) -> dict[str, Any]:
    interpreted = context["interpret"]
    requested = context["input"].get("requested_goal")
    if not interpreted["world_state_safe"]:
        return {"goal_id": "safe_idle", "reason": "world_state_fail_closed"}
    if requested:
        return {"goal_id": requested, "reason": "validated_user_request"}
    return {"goal_id": "assist_user", "reason": "safe_default_assistance"}


def _default_planner(context: Mapping[str, Any]) -> dict[str, Any]:
    goal_id = context["goal"]["goal_id"]
    if goal_id == "safe_idle":
        actions = [{
            "action_id": "safe_idle",
            "effect_class": "local_no_side_effect",
            "reason": "fail_closed",
        }]
    else:
        actions = [{
            "action_id": "present_assistance",
            "effect_class": "local_no_side_effect",
            "reason": goal_id,
        }]
    return {"plan_id": f"plan:{goal_id}", "actions": actions}


def _validate_step_output(stage: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AutonomyLoopError(
            "AUTONOMY_STEP_OUTPUT_INVALID",
            f"{stage} output must be an object",
        )
    _canonical_json(value)
    return _copy(dict(value))


def _permission_allows_local(value: Any) -> tuple[bool, str]:
    if not isinstance(value, Mapping):
        return False, "permission_missing"
    if value.get("schema_version") != PERMISSION_SCHEMA:
        return False, "permission_schema_mismatch"
    if value.get("decision") != "ALLOW":
        return False, "permission_not_allowed"
    if value.get("scope") != "local_no_side_effect":
        return False, "permission_scope_mismatch"
    try:
        _identifier(value.get("receipt_id"), "receipt_id")
    except AutonomyLoopError:
        return False, "permission_receipt_invalid"
    return True, "permission_verified"


@dataclass(frozen=True)
class AutonomyHandlers:
    interpreter: Callable[[Mapping[str, Any]], Mapping[str, Any]] = _default_interpreter
    goal_selector: Callable[[Mapping[str, Any]], Mapping[str, Any]] = _default_goal_selector
    planner: Callable[[Mapping[str, Any]], Mapping[str, Any]] = _default_planner
    action_executor: Callable[[list[dict[str, Any]], Mapping[str, Any]], Mapping[str, Any]] | None = None


class AutonomyOrchestrator:
    """Run one local deterministic autonomy loop and emit a sealed trace."""

    def __init__(self, handlers: AutonomyHandlers | None = None) -> None:
        self.handlers = handlers or AutonomyHandlers()

    def _act(self, context: Mapping[str, Any]) -> dict[str, Any]:
        mode = context["input"]["requested_mode"]
        actions = context["plan"].get("actions")
        if not isinstance(actions, list) or not actions:
            return {
                "status": "BLOCKED",
                "reason": "plan_has_no_actions",
                "executed_action_count": 0,
            }
        if any(
            not isinstance(action, Mapping)
            or action.get("effect_class") != "local_no_side_effect"
            for action in actions
        ):
            return {
                "status": "BLOCKED",
                "reason": "nonlocal_effect_requires_p4_03_policy",
                "executed_action_count": 0,
            }
        if mode == "observe_only":
            return {
                "status": "SKIPPED",
                "reason": "observe_only",
                "executed_action_count": 0,
            }
        if mode == "dry_run":
            return {
                "status": "DRY_RUN",
                "reason": "side_effect_free_preview",
                "executed_action_count": 0,
                "proposed_actions": _copy(actions),
            }
        allowed, reason = _permission_allows_local(
            context["input"].get("permission_decision")
        )
        if not allowed:
            return {
                "status": "BLOCKED",
                "reason": reason,
                "executed_action_count": 0,
            }
        if self.handlers.action_executor is None:
            return {
                "status": "BLOCKED",
                "reason": "local_action_adapter_missing",
                "executed_action_count": 0,
            }
        adapter_result = self.handlers.action_executor(
            _copy(actions),
            _copy(context["input"]["permission_decision"]),
        )
        normalized = _validate_step_output("action_executor", adapter_result)
        return {
            "status": "EXECUTED",
            "reason": reason,
            "executed_action_count": len(actions),
            "adapter_result": normalized,
        }

    def run(self, value: Mapping[str, Any]) -> dict[str, Any]:
        source = validate_autonomy_input(value)
        correlation_id = source["correlation_id"]
        context: dict[str, Any] = {"input": source}
        trace: list[dict[str, Any]] = []

        def record(stage: str, output: Mapping[str, Any]) -> None:
            normalized = _validate_step_output(stage, output)
            sequence = len(trace) + 1
            event = {
                "schema_version": AUTONOMY_TRACE_SCHEMA,
                "event_id": f"{correlation_id}:{sequence}:{stage}",
                "loop_id": source["loop_id"],
                "correlation_id": correlation_id,
                "sequence": sequence,
                "stage": stage,
                "observed_at": source["observed_at"],
                "input_context_sha256": _sha256(context),
                "output": normalized,
                "output_sha256": _sha256(normalized),
            }
            trace.append(event)
            context[stage] = normalized

        record("observe", {
            "world_state": source["world_state"],
            "relationship_state": source["relationship_state"],
        })
        record("interpret", self.handlers.interpreter(_copy(context)))
        record("goal", self.handlers.goal_selector(_copy(context)))
        record("plan", self.handlers.planner(_copy(context)))
        record("act", self._act(_copy(context)))
        record("reflect", {
            "goal_id": context["goal"].get("goal_id"),
            "action_status": context["act"].get("status"),
            "safe_to_continue": context["act"].get("status") in {
                "SKIPPED", "DRY_RUN", "EXECUTED", "BLOCKED",
            },
            "next_state": (
                "await_observation"
                if context["act"].get("status") != "BLOCKED"
                else "safe_idle"
            ),
        })

        result = {
            "schema_version": AUTONOMY_TRACE_SCHEMA,
            "loop_id": source["loop_id"],
            "correlation_id": correlation_id,
            "trace": trace,
            "stage_count": len(trace),
            "trace_complete": True,
            "act_status": context["act"]["status"],
            "goal_id": context["goal"]["goal_id"],
        }
        result["trace_sha256"] = _sha256(trace)
        validate_autonomy_trace(result)
        return result


def validate_autonomy_trace(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(
        value,
        "AUTONOMY_TRACE_OBJECT_REQUIRED",
        "trace result must be an object",
    )
    if source.get("schema_version") != AUTONOMY_TRACE_SCHEMA:
        raise AutonomyLoopError(
            "AUTONOMY_TRACE_SCHEMA_MISMATCH",
            "unsupported trace schema",
        )
    correlation_id = _identifier(source.get("correlation_id"), "correlation_id")
    trace = source.get("trace")
    if not isinstance(trace, list) or len(trace) != len(STAGES):
        raise AutonomyLoopError(
            "AUTONOMY_TRACE_INCOMPLETE",
            "exactly six stage events are required",
        )
    seen_ids: set[str] = set()
    for sequence, (event, expected_stage) in enumerate(zip(trace, STAGES), start=1):
        if not isinstance(event, Mapping):
            raise AutonomyLoopError("AUTONOMY_TRACE_EVENT_INVALID", expected_stage)
        if event.get("schema_version") != AUTONOMY_TRACE_SCHEMA:
            raise AutonomyLoopError("AUTONOMY_TRACE_EVENT_SCHEMA_MISMATCH", expected_stage)
        if event.get("sequence") != sequence or event.get("stage") != expected_stage:
            raise AutonomyLoopError("AUTONOMY_TRACE_STAGE_ORDER_INVALID", expected_stage)
        if event.get("correlation_id") != correlation_id:
            raise AutonomyLoopError("AUTONOMY_TRACE_CORRELATION_MISMATCH", expected_stage)
        event_id = _identifier(event.get("event_id"), "event_id")
        if event_id in seen_ids:
            raise AutonomyLoopError("AUTONOMY_TRACE_EVENT_ID_DUPLICATE", event_id)
        seen_ids.add(event_id)
        if event.get("output_sha256") != _sha256(event.get("output")):
            raise AutonomyLoopError("AUTONOMY_TRACE_OUTPUT_TAMPERED", expected_stage)
    if source.get("trace_sha256") != _sha256(trace):
        raise AutonomyLoopError("AUTONOMY_TRACE_TAMPERED", "trace hash mismatch")
    return _copy(dict(source))


def run_autonomy_batch(
    values: Iterable[Mapping[str, Any]],
    *,
    orchestrator: AutonomyOrchestrator | None = None,
) -> list[dict[str, Any]]:
    runner = orchestrator or AutonomyOrchestrator()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        normalized = validate_autonomy_input(value)
        correlation_id = normalized["correlation_id"]
        if correlation_id in seen:
            raise AutonomyLoopError(
                "AUTONOMY_BATCH_CORRELATION_DUPLICATE",
                correlation_id,
            )
        seen.add(correlation_id)
        results.append(runner.run(normalized))
    return results

