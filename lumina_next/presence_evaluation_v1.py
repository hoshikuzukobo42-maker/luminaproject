"""P5-05 deterministic machine presence proxy for the isolated P5 graph.

This evaluator never dispatches an action.  It scores conversation timing and
continuity only after verifying completed P5-03/P5-04 local-reference results,
their user-scoped boundary provenance, and their complete correlation traces.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.embodied_action_api_v1 import API_VERSION as EMBODIED_API_VERSION
from lumina_next.motion_semantics_v1 import (
    LIBRARY_VERSION as MOTION_LIBRARY_VERSION,
    RESULT_SCHEMA as MOTION_RESULT_SCHEMA,
    TRACE_STAGES as MOTION_TRACE_STAGES,
)
from lumina_next.proxemics_rules_v1 import (
    BEHAVIOR_TO_SEMANTIC,
    RESULT_SCHEMA as PROXEMICS_RESULT_SCHEMA,
    RULESET_VERSION as PROXEMICS_RULESET_VERSION,
    TRACE_STAGES as PROXEMICS_TRACE_STAGES,
)
from lumina_next.spatial_navigation_v1 import SERVICE_VERSION as NAVIGATION_SERVICE_VERSION


REQUEST_SCHEMA = "lumina.presence.evaluation.request.v1"
RESULT_SCHEMA = "lumina.presence.evaluation.result.v1"
TRACE_SCHEMA = "lumina.presence.evaluation.trace.v1"
EVALUATOR_VERSION = "lumina.presence.evaluation.v1"
MACHINE_PROXY_THRESHOLD = 80.0
MAX_SYNC_LATENCY_MS = 750.0
MAX_RESPONSE_LATENCY_MS = 1500.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

TRACE_STAGES = (
    "VALIDATE",
    "CONVERSATION_SYNC",
    "SEMANTIC_MATCH",
    "SAFETY_BOUNDARY",
    "RESPONSE_LATENCY",
    "CONTINUITY",
    "TRACE_PROVENANCE",
    "MACHINE_PROXY",
    "RESULT",
)

DEPENDENCY_VERSIONS = {
    "P5_01_EMBODIED_ACTION": EMBODIED_API_VERSION,
    "P5_02_SPATIAL_NAVIGATION": NAVIGATION_SERVICE_VERSION,
    "P5_03_MOTION_SEMANTICS": MOTION_LIBRARY_VERSION,
    "P5_04_PROXEMICS_RULES": PROXEMICS_RULESET_VERSION,
}

CUE_SPECS: dict[str, dict[str, Any]] = {
    "welcome": {
        "execution_kind": "proxemics",
        "safety_behavior": "approach",
        "semantic": "approach",
        "prior_state": "idle",
        "action_state": "moving",
        "next_state": "attending",
    },
    "give_space": {
        "execution_kind": "proxemics",
        "safety_behavior": "retreat",
        "semantic": "walk",
        "prior_state": "attending",
        "action_state": "moving",
        "next_state": "idle",
    },
    "attend": {
        "execution_kind": "proxemics",
        "safety_behavior": "gaze",
        "semantic": "look",
        "prior_state": "idle",
        "action_state": "listening",
        "next_state": "attending",
    },
    "invite_sit": {
        "execution_kind": "proxemics",
        "safety_behavior": "seat",
        "semantic": "sit_down",
        "prior_state": "standing",
        "action_state": "posture_transition",
        "next_state": "seated",
    },
    "acknowledge": {
        "execution_kind": "motion",
        "safety_behavior": "gaze",
        "semantic": "gesture",
        "prior_state": "attending",
        "action_state": "gesturing",
        "next_state": "attending",
        "animation_channel": "UPPER_BODY",
        "animation_loop": False,
    },
    "seated_pause": {
        "execution_kind": "motion",
        "safety_behavior": "seat",
        "semantic": "sit_idle",
        "prior_state": "seated",
        "action_state": "idle",
        "next_state": "seated",
        "animation_channel": "POSTURE",
        "animation_loop": True,
    },
    "resume": {
        "execution_kind": "motion",
        "safety_behavior": "gaze",
        "semantic": "stand_up",
        "prior_state": "seated",
        "action_state": "posture_transition",
        "next_state": "standing",
        "animation_channel": "POSTURE",
        "animation_loop": False,
    },
    "point_out": {
        "execution_kind": "motion",
        "safety_behavior": "gaze",
        "semantic": "point",
        "prior_state": "attending",
        "action_state": "gesturing",
        "next_state": "attending",
        "animation_channel": "UPPER_BODY",
        "animation_loop": False,
    },
}


class PresenceEvaluationError(ValueError):
    def __init__(self, code: str, detail: str, stage: str = "VALIDATE") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.stage = stage


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PresenceEvaluationError("PRESENCE_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise PresenceEvaluationError("PRESENCE_ID_INVALID", field_name)
    return value


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PresenceEvaluationError("PRESENCE_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PresenceEvaluationError("PRESENCE_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise PresenceEvaluationError("PRESENCE_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise PresenceEvaluationError("PRESENCE_NUMBER_INVALID", field_name)
    return float(value)


def _basic_result_hash_valid(result: Mapping[str, Any], hash_field: str) -> bool:
    expected = result.get(hash_field)
    if not isinstance(expected, str):
        return False
    candidate = _copy(result)
    candidate.pop(hash_field, None)
    return _sha256(candidate) == expected


def _trace_hash_valid(event: Mapping[str, Any]) -> bool:
    expected = event.get("trace_sha256")
    if not isinstance(expected, str):
        return False
    candidate = _copy(event)
    candidate.pop("trace_sha256", None)
    return _sha256(candidate) == expected


def validate_presence_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PresenceEvaluationError("PRESENCE_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "evaluation_id",
        "correlation_id",
        "tenant_id",
        "user_id",
        "target_user_id",
        "session_id",
        "conversation",
        "safety_result",
        "execution",
        "continuity",
        "p4_08_approved",
        "g4_approved",
        "g5_approved",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PresenceEvaluationError("PRESENCE_REQUEST_UNKNOWN_FIELDS", ",".join(unknown))
    if value.get("schema_version") != REQUEST_SCHEMA:
        raise PresenceEvaluationError("PRESENCE_REQUEST_SCHEMA_MISMATCH", "schema_version")
    if any(value.get(field) is not False for field in ("p4_08_approved", "g4_approved", "g5_approved")):
        raise PresenceEvaluationError("PRESENCE_UNAPPROVED_GATE_CLAIM", "P4-08/G4/G5")
    user_id = _identifier(value.get("user_id"), "user_id")
    target_user_id = _identifier(value.get("target_user_id"), "target_user_id")
    if user_id == target_user_id:
        raise PresenceEvaluationError("PRESENCE_SELF_TARGET_FORBIDDEN", user_id)

    conversation = value.get("conversation")
    if not isinstance(conversation, Mapping) or set(conversation) != {
        "turn_id",
        "cue_type",
        "utterance_started_at",
        "utterance_ended_at",
        "response_started_at",
        "response_completed_at",
        "expected_semantic",
    }:
        raise PresenceEvaluationError("PRESENCE_CONVERSATION_FIELDS_INVALID", "conversation")
    cue_type = conversation.get("cue_type")
    if cue_type not in CUE_SPECS:
        raise PresenceEvaluationError("PRESENCE_CUE_UNKNOWN", str(cue_type))
    _identifier(conversation.get("turn_id"), "conversation.turn_id")
    for field_name in ("utterance_started_at", "utterance_ended_at", "response_started_at", "response_completed_at"):
        _parse_time(conversation.get(field_name), f"conversation.{field_name}")
    if not isinstance(conversation.get("expected_semantic"), str):
        raise PresenceEvaluationError("PRESENCE_EXPECTED_SEMANTIC_REQUIRED", "conversation.expected_semantic")

    execution = value.get("execution")
    if not isinstance(execution, Mapping) or set(execution) != {"kind", "result"}:
        raise PresenceEvaluationError("PRESENCE_EXECUTION_FIELDS_INVALID", "execution")
    if execution.get("kind") not in {"proxemics", "motion"} or not isinstance(execution.get("result"), Mapping):
        raise PresenceEvaluationError("PRESENCE_EXECUTION_INVALID", "execution")
    if not isinstance(value.get("safety_result"), Mapping):
        raise PresenceEvaluationError("PRESENCE_SAFETY_RESULT_REQUIRED", "safety_result")

    continuity = value.get("continuity")
    if not isinstance(continuity, Mapping) or set(continuity) != {
        "sequence_id", "step_index", "prior_state", "action_state", "next_state"
    }:
        raise PresenceEvaluationError("PRESENCE_CONTINUITY_FIELDS_INVALID", "continuity")
    _identifier(continuity.get("sequence_id"), "continuity.sequence_id")
    step_index = continuity.get("step_index")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
        raise PresenceEvaluationError("PRESENCE_STEP_INDEX_INVALID", "continuity.step_index")
    if any(not isinstance(continuity.get(field), str) for field in ("prior_state", "action_state", "next_state")):
        raise PresenceEvaluationError("PRESENCE_CONTINUITY_STATE_INVALID", "continuity")

    normalized = {
        "schema_version": REQUEST_SCHEMA,
        "evaluation_id": _identifier(value.get("evaluation_id"), "evaluation_id"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": user_id,
        "target_user_id": target_user_id,
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "conversation": _copy(conversation),
        "safety_result": _copy(value["safety_result"]),
        "execution": _copy(execution),
        "continuity": _copy(continuity),
        "p4_08_approved": False,
        "g4_approved": False,
        "g5_approved": False,
    }
    _canonical_json(normalized)
    return normalized


def _base(value: Any) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}

    def safe(item: Any, fallback: str) -> str:
        return item if isinstance(item, str) and _ID_PATTERN.fullmatch(item) else fallback

    conversation = source.get("conversation") if isinstance(source.get("conversation"), Mapping) else {}
    return {
        "evaluation_id": safe(source.get("evaluation_id"), "invalid-evaluation"),
        "correlation_id": safe(source.get("correlation_id"), "invalid-correlation"),
        "tenant_id": safe(source.get("tenant_id"), "invalid-tenant"),
        "user_id": safe(source.get("user_id"), "invalid-user"),
        "target_user_id": safe(source.get("target_user_id"), "invalid-target"),
        "cue_type": conversation.get("cue_type") if conversation.get("cue_type") in CUE_SPECS else "invalid-cue",
    }


def _outcomes() -> dict[str, dict[str, Any]]:
    return {stage: {"outcome": "SKIPPED", "reason": "not_reached"} for stage in TRACE_STAGES}


def _trace(base: Mapping[str, str], outcomes: Mapping[str, Mapping[str, Any]], occurred_at: str) -> list[dict[str, Any]]:
    trace = []
    for sequence, stage in enumerate(TRACE_STAGES, start=1):
        event = {
            "schema_version": TRACE_SCHEMA,
            "trace_id": f"presence-trace:{base['evaluation_id']}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "outcome": outcomes[stage]["outcome"],
            "reason": outcomes[stage]["reason"],
            "evaluation_id": base["evaluation_id"],
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
    scores: Mapping[str, float] | None = None,
    proxy: float = 0.0,
    safety_result_sha256: str | None = None,
    execution_result_sha256: str | None = None,
) -> dict[str, Any]:
    outcomes["RESULT"] = {"outcome": "TERMINAL", "reason": reason, "detail": {"status": status}}
    occurred_at = "1970-01-01T00:00:00Z"
    if request is not None:
        occurred_at = request["conversation"]["response_completed_at"]
    component_scores = {name: round(float((scores or {}).get(name, 0.0)), 3) for name in (
        "conversation_sync", "semantic", "safety", "response_latency", "continuity"
    )}
    result = {
        "schema_version": RESULT_SCHEMA,
        "evaluator_version": EVALUATOR_VERSION,
        "dependency_versions": _copy(DEPENDENCY_VERSIONS),
        "evaluation_id": base["evaluation_id"],
        "correlation_id": base["correlation_id"],
        "tenant_id": base["tenant_id"],
        "user_id": base["user_id"],
        "target_user_id": base["target_user_id"],
        "cue_type": base["cue_type"],
        "status": status,
        "reason": reason,
        "machine_presence_proxy": round(float(proxy), 3),
        "machine_proxy_threshold": MACHINE_PROXY_THRESHOLD,
        "component_scores": component_scores,
        "semantic_pass": component_scores["semantic"] == 100.0,
        "safety_pass": component_scores["safety"] == 100.0,
        "continuity_pass": component_scores["continuity"] == 100.0,
        "safety_result_sha256": safety_result_sha256,
        "execution_result_sha256": execution_result_sha256,
        "external_execution": False,
        "godot_action_fired": False,
        "human_presence_improvement_claimed": False,
        "blind_review_claimed": False,
        "p4_08_approved": False,
        "g4_approved": False,
        "g5_approved": False,
        "trace": _trace(base, outcomes, occurred_at),
    }
    result["result_sha256"] = _sha256(result)
    return result


def _conversation_sync(request: Mapping[str, Any]) -> tuple[float, float]:
    conversation = request["conversation"]
    utterance_start = _parse_time(conversation["utterance_started_at"], "utterance_started_at")
    utterance_end = _parse_time(conversation["utterance_ended_at"], "utterance_ended_at")
    response_start = _parse_time(conversation["response_started_at"], "response_started_at")
    response_end = _parse_time(conversation["response_completed_at"], "response_completed_at")
    if not utterance_start < utterance_end <= response_start < response_end:
        raise PresenceEvaluationError("PRESENCE_TIMELINE_ORDER_INVALID", "conversation", "CONVERSATION_SYNC")
    sync_ms = (response_start - utterance_end).total_seconds() * 1000.0
    if sync_ms > MAX_SYNC_LATENCY_MS:
        raise PresenceEvaluationError("PRESENCE_SYNC_LATENCY_EXCEEDED", str(sync_ms), "CONVERSATION_SYNC")
    if sync_ms <= 150.0:
        score = 100.0
    elif sync_ms <= 300.0:
        score = 90.0
    else:
        score = 75.0
    return sync_ms, score


def _semantic_match(request: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[str, str]:
    conversation = request["conversation"]
    if conversation["expected_semantic"] != spec["semantic"]:
        raise PresenceEvaluationError("PRESENCE_CUE_SEMANTIC_MISMATCH", str(conversation["expected_semantic"]), "SEMANTIC_MATCH")
    execution = request["execution"]
    if execution["kind"] != spec["execution_kind"]:
        raise PresenceEvaluationError("PRESENCE_EXECUTION_KIND_MISMATCH", str(execution["kind"]), "SEMANTIC_MATCH")
    result = execution["result"]
    if result.get("status") != "COMPLETED":
        raise PresenceEvaluationError("PRESENCE_EXECUTION_NOT_COMPLETED", str(result.get("status")), "SEMANTIC_MATCH")
    if execution["kind"] == "proxemics":
        if result.get("schema_version") != PROXEMICS_RESULT_SCHEMA:
            raise PresenceEvaluationError("PRESENCE_PROXEMICS_SCHEMA_MISMATCH", "execution", "SEMANTIC_MATCH")
        actual = BEHAVIOR_TO_SEMANTIC.get(result.get("behavior"))
    else:
        if result.get("schema_version") != MOTION_RESULT_SCHEMA or result.get("semantic_match") is not True:
            raise PresenceEvaluationError("PRESENCE_MOTION_RESULT_INVALID", "execution", "SEMANTIC_MATCH")
        actual = result.get("semantic_intent")
        contract = result.get("motion_contract")
        if not isinstance(contract, Mapping):
            raise PresenceEvaluationError("PRESENCE_MOTION_CONTRACT_REQUIRED", "execution", "SEMANTIC_MATCH")
        animation = contract.get("animation")
        if not isinstance(animation, Mapping) or animation.get("channel") != spec.get("animation_channel") or animation.get("loop") is not spec.get("animation_loop"):
            raise PresenceEvaluationError("PRESENCE_ANIMATION_CONTINUITY_MISMATCH", "motion_contract", "SEMANTIC_MATCH")
    if actual != spec["semantic"]:
        raise PresenceEvaluationError("PRESENCE_EXECUTED_SEMANTIC_MISMATCH", str(actual), "SEMANTIC_MATCH")
    return str(actual), str(execution["kind"])


def _safety_boundary(request: Mapping[str, Any], spec: Mapping[str, Any]) -> Mapping[str, Any]:
    result = request["safety_result"]
    if result.get("schema_version") != PROXEMICS_RESULT_SCHEMA or result.get("ruleset_version") != PROXEMICS_RULESET_VERSION:
        raise PresenceEvaluationError("PRESENCE_SAFETY_SCHEMA_MISMATCH", "safety_result", "SAFETY_BOUNDARY")
    bindings = {
        "correlation_id": request["correlation_id"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "target_user_id": request["target_user_id"],
        "behavior": spec["safety_behavior"],
    }
    if any(result.get(field) != expected for field, expected in bindings.items()):
        raise PresenceEvaluationError("PRESENCE_SAFETY_BINDING_MISMATCH", "safety_result", "SAFETY_BOUNDARY")
    if result.get("status") != "COMPLETED" or result.get("authorized") is not True:
        raise PresenceEvaluationError("PRESENCE_SAFETY_NOT_AUTHORIZED", str(result.get("status")), "SAFETY_BOUNDARY")
    if any(result.get(field) is not False for field in (
        "external_execution", "godot_action_fired", "p4_08_approved", "g4_approved", "g5_approved"
    )):
        raise PresenceEvaluationError("PRESENCE_UNSAFE_EXECUTION_CLAIM", "safety_result", "SAFETY_BOUNDARY")
    if result.get("distance_zone") == "CONTACT":
        raise PresenceEvaluationError("PRESENCE_CONTACT_ZONE_VIOLATION", "safety_result", "SAFETY_BOUNDARY")
    decision = result.get("comfort_decision")
    if not isinstance(decision, Mapping) or decision.get("comfort_after") is not True:
        raise PresenceEvaluationError("PRESENCE_COMFORT_DECISION_INVALID", "safety_result", "SAFETY_BOUNDARY")
    desired = _number(decision.get("desired_distance_m"), "desired_distance_m", 0.0, 1000.0)
    comfort = _number(result.get("comfort_distance_m"), "comfort_distance_m", 0.45, 5.0)
    if desired < comfort:
        raise PresenceEvaluationError("PRESENCE_COMFORT_DISTANCE_VIOLATION", str(desired), "SAFETY_BOUNDARY")
    if request["execution"]["kind"] == "proxemics" and request["execution"]["result"].get("result_sha256") != result.get("result_sha256"):
        raise PresenceEvaluationError("PRESENCE_PROXEMICS_EXECUTION_SAFETY_MISMATCH", "result_sha256", "SAFETY_BOUNDARY")
    return decision


def _response_latency(request: Mapping[str, Any]) -> tuple[float, float]:
    conversation = request["conversation"]
    utterance_end = _parse_time(conversation["utterance_ended_at"], "utterance_ended_at")
    response_end = _parse_time(conversation["response_completed_at"], "response_completed_at")
    latency_ms = (response_end - utterance_end).total_seconds() * 1000.0
    if latency_ms > MAX_RESPONSE_LATENCY_MS:
        raise PresenceEvaluationError("PRESENCE_RESPONSE_LATENCY_EXCEEDED", str(latency_ms), "RESPONSE_LATENCY")
    if latency_ms <= 750.0:
        score = 100.0
    elif latency_ms <= 1200.0:
        score = 90.0
    else:
        score = 75.0
    return latency_ms, score


def _continuity(request: Mapping[str, Any], spec: Mapping[str, Any]) -> None:
    continuity = request["continuity"]
    expected = {
        "prior_state": spec["prior_state"],
        "action_state": spec["action_state"],
        "next_state": spec["next_state"],
    }
    for field, expected_value in expected.items():
        if continuity.get(field) != expected_value:
            raise PresenceEvaluationError("PRESENCE_CONTINUITY_TRANSITION_INVALID", field, "CONTINUITY")
    if continuity["step_index"] < 0:
        raise PresenceEvaluationError("PRESENCE_CONTINUITY_INDEX_INVALID", "step_index", "CONTINUITY")


def _validate_dependency_trace(
    result: Mapping[str, Any], *, stages: tuple[str, ...], id_field: str, request: Mapping[str, Any]
) -> None:
    if not _basic_result_hash_valid(result, "result_sha256"):
        raise PresenceEvaluationError("PRESENCE_DEPENDENCY_RESULT_HASH_INVALID", id_field, "TRACE_PROVENANCE")
    trace = result.get("trace")
    if not isinstance(trace, list) or len(trace) != len(stages):
        raise PresenceEvaluationError("PRESENCE_DEPENDENCY_TRACE_INCOMPLETE", id_field, "TRACE_PROVENANCE")
    for index, event in enumerate(trace, start=1):
        if (
            not isinstance(event, Mapping)
            or event.get("sequence") != index
            or event.get("stage") != stages[index - 1]
            or event.get("correlation_id") != request["correlation_id"]
            or event.get("user_id") != request["user_id"]
            or event.get(id_field) != result.get(id_field)
            or not _trace_hash_valid(event)
        ):
            raise PresenceEvaluationError("PRESENCE_DEPENDENCY_TRACE_BINDING_INVALID", id_field, "TRACE_PROVENANCE")
    response_started = request["conversation"]["response_started_at"]
    if any(event.get("occurred_at") != response_started for event in trace):
        raise PresenceEvaluationError("PRESENCE_TRACE_RESPONSE_TIME_MISMATCH", id_field, "TRACE_PROVENANCE")


def _trace_and_provenance(request: Mapping[str, Any]) -> Mapping[str, Any]:
    safety = request["safety_result"]
    _validate_dependency_trace(
        safety, stages=PROXEMICS_TRACE_STAGES, id_field="proxemics_id", request=request
    )
    execution = request["execution"]
    if execution["kind"] == "motion":
        _validate_dependency_trace(
            execution["result"], stages=MOTION_TRACE_STAGES, id_field="motion_id", request=request
        )
    provenance = safety.get("boundary_provenance")
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("user_id") != request["target_user_id"]
        or provenance.get("source") != "user_explicit_preference"
        or not isinstance(provenance.get("evidence_id"), str)
        or not provenance["evidence_id"].startswith("boundary:")
        or _number(provenance.get("confidence"), "provenance.confidence", 0.9, 1.0) < 0.9
    ):
        raise PresenceEvaluationError("PRESENCE_BOUNDARY_PROVENANCE_INVALID", "safety_result", "TRACE_PROVENANCE")
    return provenance


def evaluate_presence_episode(value: Mapping[str, Any]) -> dict[str, Any]:
    base = _base(value)
    outcomes = _outcomes()
    scores: dict[str, float] = {}
    try:
        request = validate_presence_request(value)
        base = _base(request)
        spec = CUE_SPECS[request["conversation"]["cue_type"]]
        outcomes["VALIDATE"] = {"outcome": "PASS", "reason": "episode_contract_valid"}

        sync_ms, scores["conversation_sync"] = _conversation_sync(request)
        outcomes["CONVERSATION_SYNC"] = {
            "outcome": "PASS", "reason": "conversation_action_aligned", "detail": {"sync_latency_ms": sync_ms}
        }

        actual_semantic, execution_kind = _semantic_match(request, spec)
        scores["semantic"] = 100.0
        outcomes["SEMANTIC_MATCH"] = {
            "outcome": "PASS", "reason": "cue_semantic_executed", "detail": {
                "semantic": actual_semantic, "execution_kind": execution_kind
            }
        }

        decision = _safety_boundary(request, spec)
        scores["safety"] = 100.0
        outcomes["SAFETY_BOUNDARY"] = {
            "outcome": "PASS", "reason": "contact_and_comfort_safe", "detail": {
                "desired_distance_m": decision["desired_distance_m"]
            }
        }

        response_ms, scores["response_latency"] = _response_latency(request)
        outcomes["RESPONSE_LATENCY"] = {
            "outcome": "PASS", "reason": "machine_response_within_budget", "detail": {"response_latency_ms": response_ms}
        }

        _continuity(request, spec)
        scores["continuity"] = 100.0
        outcomes["CONTINUITY"] = {
            "outcome": "PASS", "reason": "idle_gesture_motion_transition_valid", "detail": _copy(request["continuity"])
        }

        provenance = _trace_and_provenance(request)
        outcomes["TRACE_PROVENANCE"] = {
            "outcome": "PASS", "reason": "dependency_trace_and_boundary_provenance_complete", "detail": {
                "evidence_id": provenance["evidence_id"]
            }
        }
    except PresenceEvaluationError as exc:
        outcomes[exc.stage] = {"outcome": "BLOCK", "reason": exc.code, "detail": {"detail": exc.detail}}
        return _finish(
            locals().get("request"), base, outcomes, status="BLOCKED", reason=exc.code,
            scores=scores,
            safety_result_sha256=(locals().get("request") or {}).get("safety_result", {}).get("result_sha256") if isinstance(locals().get("request"), Mapping) else None,
            execution_result_sha256=(locals().get("request") or {}).get("execution", {}).get("result", {}).get("result_sha256") if isinstance(locals().get("request"), Mapping) else None,
        )

    proxy = (
        scores["conversation_sync"] * 0.20
        + scores["semantic"] * 0.20
        + scores["safety"] * 0.25
        + scores["response_latency"] * 0.15
        + scores["continuity"] * 0.20
    )
    if proxy < MACHINE_PROXY_THRESHOLD:
        outcomes["MACHINE_PROXY"] = {"outcome": "FAIL", "reason": "machine_proxy_below_threshold", "detail": {"score": proxy}}
        return _finish(
            request, base, outcomes, status="FAIL", reason="machine_proxy_below_threshold", scores=scores, proxy=proxy,
            safety_result_sha256=request["safety_result"].get("result_sha256"),
            execution_result_sha256=request["execution"]["result"].get("result_sha256"),
        )
    outcomes["MACHINE_PROXY"] = {"outcome": "PASS", "reason": "machine_proxy_threshold_met", "detail": {"score": proxy}}
    return _finish(
        request, base, outcomes, status="PASS", reason="machine_presence_proxy_pass", scores=scores, proxy=proxy,
        safety_result_sha256=request["safety_result"].get("result_sha256"),
        execution_result_sha256=request["execution"]["result"].get("result_sha256"),
    )


__all__ = [
    "CUE_SPECS",
    "DEPENDENCY_VERSIONS",
    "EVALUATOR_VERSION",
    "MACHINE_PROXY_THRESHOLD",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "TRACE_STAGES",
    "PresenceEvaluationError",
    "evaluate_presence_episode",
    "validate_presence_request",
]
