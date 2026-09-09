"""Deterministic, fail-closed proactive trigger decisions for P4-05.

The service decides whether an already-proposed local interruption may be shown.
It never delivers a message or executes an action.  Decisions combine observed
facts, exact-user relationship settings, cooldown history, quiet hours, and the
P4 risk policy while retaining machine-readable reasons and grounding.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from lumina_next.perception_v1 import PerceptionAdapter, PerceptionContractError
from lumina_next.relationship_model_v1 import RelationshipModelError, RelationshipModelV1
from lumina_next.risk_policy_v1 import RiskPolicyError, evaluate_action_request


CANDIDATE_SCHEMA = "lumina.proactive.trigger.candidate.v1"
DECISION_SCHEMA = "lumina.proactive.trigger.decision.v1"
BATCH_SCHEMA = "lumina.proactive.trigger.batch.v1"
EVALUATION_SCHEMA = "lumina.proactive.trigger.evaluation.v1"
POLICY_VERSION = "lumina.proactive.trigger.policy.v1"
TRIGGER_TYPES = ("assistance", "interest_tip", "reminder", "safety_alert")
URGENCIES = ("routine", "important", "critical")
CHANNELS = ("visual", "voice")
DEFAULT_MIN_CONFIDENCE = 0.8
DEFAULT_COOLDOWN_SECONDS = 900
MAX_COOLDOWN_SECONDS = 86400
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ProactiveTriggerError(ValueError):
    pass


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProactiveTriggerError("value must be finite canonical JSON") from exc


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ProactiveTriggerError(f"{field} must be a canonical identifier")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ProactiveTriggerError(f"{field} is required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProactiveTriggerError(f"{field} must be ISO-8601") from exc
    if result.tzinfo is None:
        raise ProactiveTriggerError(f"{field} timezone is required")
    return result.astimezone(timezone.utc)


def _bounded(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ProactiveTriggerError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProactiveTriggerError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ProactiveTriggerError(f"{field} must be in [0, 1]")
    return result


def _normalize_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProactiveTriggerError("candidate must be an object")
    allowed = {
        "schema_version",
        "candidate_id",
        "correlation_id",
        "tenant_id",
        "user_id",
        "session_id",
        "occurred_at",
        "trigger_type",
        "urgency",
        "channel",
        "confidence",
        "observation",
        "context",
        "action_request",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProactiveTriggerError(f"unknown candidate fields: {','.join(unknown)}")
    if value.get("schema_version") != CANDIDATE_SCHEMA:
        raise ProactiveTriggerError("candidate schema mismatch")
    observation = value.get("observation")
    context = value.get("context", {})
    action_request = value.get("action_request")
    if not isinstance(observation, Mapping):
        raise ProactiveTriggerError("observation must be an object")
    if not isinstance(context, Mapping):
        raise ProactiveTriggerError("context must be an object")
    if not isinstance(action_request, Mapping):
        raise ProactiveTriggerError("action_request must be an object")
    trigger_type = str(value.get("trigger_type") or "")
    urgency = str(value.get("urgency") or "")
    channel = str(value.get("channel") or "")
    if trigger_type not in TRIGGER_TYPES:
        raise ProactiveTriggerError("unsupported trigger_type")
    if urgency not in URGENCIES:
        raise ProactiveTriggerError("unsupported urgency")
    if channel not in CHANNELS:
        raise ProactiveTriggerError("unsupported channel")
    occurred_at = _timestamp(value.get("occurred_at"), "occurred_at")
    normalized = {
        "schema_version": CANDIDATE_SCHEMA,
        "candidate_id": _identifier(value.get("candidate_id"), "candidate_id"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "tenant_id"),
        "user_id": _identifier(value.get("user_id"), "user_id"),
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "trigger_type": trigger_type,
        "urgency": urgency,
        "channel": channel,
        "confidence": _bounded(value.get("confidence"), "confidence"),
        "observation": copy.deepcopy(dict(observation)),
        "context": copy.deepcopy(dict(context)),
        "action_request": copy.deepcopy(dict(action_request)),
    }
    _canonical(normalized)
    return normalized


def _history_record(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProactiveTriggerError("history record must be an object")
    allowed = {
        "candidate_id",
        "tenant_id",
        "user_id",
        "trigger_type",
        "occurred_at",
        "decision",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProactiveTriggerError(f"unknown history fields: {','.join(unknown)}")
    decision = str(value.get("decision") or "")
    if decision not in {"TRIGGER", "NO_TRIGGER"}:
        raise ProactiveTriggerError("history decision is invalid")
    trigger_type = str(value.get("trigger_type") or "")
    if trigger_type not in TRIGGER_TYPES:
        raise ProactiveTriggerError("history trigger_type is invalid")
    occurred = _timestamp(value.get("occurred_at"), "history.occurred_at")
    return {
        "candidate_id": _identifier(value.get("candidate_id"), "history.candidate_id"),
        "tenant_id": _identifier(value.get("tenant_id"), "history.tenant_id"),
        "user_id": _identifier(value.get("user_id"), "history.user_id"),
        "trigger_type": trigger_type,
        "occurred_at": occurred.isoformat().replace("+00:00", "Z"),
        "decision": decision,
    }


def _preference_map(relationship: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["key"]): item
        for item in relationship["state"]["preferences"]
        if isinstance(item, Mapping)
    }


def _boundary_map(relationship: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["key"]): item
        for item in relationship["state"]["boundaries"]
        if isinstance(item, Mapping) and item.get("value") is True
    }


def _setting(
    preferences: Mapping[str, Mapping[str, Any]],
    key: str,
    default: Any,
) -> tuple[Any, dict[str, Any]]:
    item = preferences.get(key)
    if item is None:
        return copy.deepcopy(default), {
            "key": key,
            "value": copy.deepcopy(default),
            "source": "fail_closed_default",
            "grounding": [],
        }
    return copy.deepcopy(item.get("value")), {
        "key": key,
        "value": copy.deepcopy(item.get("value")),
        "source": "relationship_model_v1",
        "grounding": copy.deepcopy(item.get("grounding", [])),
    }


def _parse_clock(value: str) -> time:
    if _TIME_RE.fullmatch(value) is None:
        raise ProactiveTriggerError("quiet-hours clock must be HH:MM")
    hour, minute = (int(item) for item in value.split(":"))
    return time(hour=hour, minute=minute)


def _quiet_hours(value: Any, occurred_at: datetime) -> dict[str, Any]:
    if value is None:
        return {
            "enabled": False,
            "active": False,
            "start": None,
            "end": None,
            "utc_offset_minutes": 0,
            "local_time": None,
        }
    if not isinstance(value, Mapping):
        raise ProactiveTriggerError("quiet_hours_local must be an object or null")
    allowed = {"enabled", "start", "end", "utc_offset_minutes"}
    if set(value) - allowed:
        raise ProactiveTriggerError("quiet_hours_local contains unknown fields")
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise ProactiveTriggerError("quiet_hours_local.enabled must be boolean")
    if not enabled:
        return {
            "enabled": False,
            "active": False,
            "start": None,
            "end": None,
            "utc_offset_minutes": 0,
            "local_time": None,
        }
    start_text = str(value.get("start") or "")
    end_text = str(value.get("end") or "")
    start = _parse_clock(start_text)
    end = _parse_clock(end_text)
    offset_raw = value.get("utc_offset_minutes")
    if isinstance(offset_raw, bool) or not isinstance(offset_raw, int) or not -840 <= offset_raw <= 840:
        raise ProactiveTriggerError("quiet-hours UTC offset must be an integer in [-840, 840]")
    local_datetime = occurred_at + timedelta(minutes=offset_raw)
    local_clock = local_datetime.time().replace(tzinfo=None)
    if start == end:
        active = True
    elif start < end:
        active = start <= local_clock < end
    else:
        active = local_clock >= start or local_clock < end
    return {
        "enabled": True,
        "active": active,
        "start": start_text,
        "end": end_text,
        "utc_offset_minutes": offset_raw,
        "local_time": local_clock.isoformat(timespec="minutes"),
    }


def _relationship_settings(
    relationship: Mapping[str, Any],
    occurred_at: datetime,
) -> tuple[dict[str, Any], list[str]]:
    preferences = _preference_map(relationship)
    errors: list[str] = []
    policy_value, policy_explain = _setting(preferences, "proactive_policy", "off")
    threshold_value, threshold_explain = _setting(
        preferences, "proactive_min_confidence", DEFAULT_MIN_CONFIDENCE
    )
    cooldown_value, cooldown_explain = _setting(
        preferences, "proactive_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS
    )
    quiet_value, quiet_explain = _setting(preferences, "quiet_hours_local", None)
    channel_value, channel_explain = _setting(preferences, "proactive_channel", "any")

    if policy_value not in {"allow", "important_only", "off"}:
        errors.append("invalid_relationship_policy")
        policy_value = "off"
    try:
        threshold = _bounded(threshold_value, "proactive_min_confidence")
    except ProactiveTriggerError:
        errors.append("invalid_relationship_confidence")
        threshold = 1.0
    if (
        isinstance(cooldown_value, bool)
        or not isinstance(cooldown_value, (int, float))
        or not math.isfinite(float(cooldown_value))
        or not 0 <= float(cooldown_value) <= MAX_COOLDOWN_SECONDS
    ):
        errors.append("invalid_relationship_cooldown")
        cooldown = MAX_COOLDOWN_SECONDS
    else:
        cooldown = int(float(cooldown_value))
    if channel_value not in {"any", *CHANNELS}:
        errors.append("invalid_relationship_channel")
        channel_value = "none"
    try:
        quiet = _quiet_hours(quiet_value, occurred_at)
    except ProactiveTriggerError:
        errors.append("invalid_relationship_quiet_hours")
        quiet = {
            "enabled": True,
            "active": True,
            "start": None,
            "end": None,
            "utc_offset_minutes": 0,
            "local_time": None,
        }
    for explanation, value in (
        (policy_explain, policy_value),
        (threshold_explain, threshold),
        (cooldown_explain, cooldown),
        (channel_explain, channel_value),
    ):
        explanation["value"] = value
    quiet_explain["value"] = quiet
    return {
        "policy": policy_explain,
        "minimum_confidence": threshold_explain,
        "cooldown_seconds": cooldown_explain,
        "quiet_hours": quiet_explain,
        "channel": channel_explain,
    }, errors


def _cooldown(
    candidate: Mapping[str, Any],
    history: Iterable[Mapping[str, Any]],
    cooldown_seconds: int,
) -> dict[str, Any]:
    occurred = _timestamp(candidate["occurred_at"], "occurred_at")
    scoped = [
        item
        for item in history
        if item["tenant_id"] == candidate["tenant_id"]
        and item["user_id"] == candidate["user_id"]
        and item["decision"] == "TRIGGER"
        and _timestamp(item["occurred_at"], "history.occurred_at") < occurred
    ]
    latest = max(
        scoped,
        key=lambda item: (_timestamp(item["occurred_at"], "history.occurred_at"), item["candidate_id"]),
        default=None,
    )
    if latest is None or cooldown_seconds == 0:
        return {
            "configured_seconds": cooldown_seconds,
            "active": False,
            "remaining_seconds": 0,
            "last_trigger_candidate_id": None,
            "last_trigger_at": None,
        }
    elapsed = (occurred - _timestamp(latest["occurred_at"], "history.occurred_at")).total_seconds()
    remaining = max(0, int(math.ceil(cooldown_seconds - elapsed)))
    return {
        "configured_seconds": cooldown_seconds,
        "active": remaining > 0,
        "remaining_seconds": remaining,
        "last_trigger_candidate_id": latest["candidate_id"],
        "last_trigger_at": latest["occurred_at"],
    }


def _inference(perception: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    return next(item for item in perception["inferences"] if item["label"] == label)


class ProactiveTriggerService:
    """Evaluate proactive candidates without delivering or executing them."""

    def __init__(
        self,
        relationship_events: Iterable[Mapping[str, Any]],
        *,
        memory_records: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.perception = PerceptionAdapter()
        self.relationship = RelationshipModelV1(
            relationship_events,
            memory_records=memory_records,
        )

    def decide(
        self,
        candidate: Mapping[str, Any],
        *,
        history: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        normalized = _normalize_candidate(candidate)
        normalized_history = tuple(_history_record(item) for item in history)
        if len({item["candidate_id"] for item in normalized_history}) != len(normalized_history):
            raise ProactiveTriggerError("duplicate history candidate_id")
        occurred = _timestamp(normalized["occurred_at"], "occurred_at")

        try:
            perception = self.perception.infer(normalized["observation"])
        except PerceptionContractError as exc:
            raise ProactiveTriggerError(f"perception contract rejected observation: {exc}") from exc
        try:
            relationship = self.relationship.explain(
                tenant_id=normalized["tenant_id"],
                user_id=normalized["user_id"],
                current_session_id=normalized["session_id"],
                query="proactive interruption preference boundary quiet hours",
                as_of=occurred,
            )
        except RelationshipModelError as exc:
            raise ProactiveTriggerError(f"relationship model rejected scope: {exc}") from exc

        settings, settings_errors = _relationship_settings(relationship, occurred)
        boundaries = _boundary_map(relationship)
        applied_boundaries = {
            key: {
                "key": key,
                "enforced": True,
                "grounding": copy.deepcopy(item.get("grounding", [])),
            }
            for key, item in boundaries.items()
            if key in {"no_proactive_interruptions", "no_voice_interruptions", "no_safety_alerts"}
        }

        request = normalized["action_request"]
        risk_binding_errors: list[str] = []
        if request.get("user_id") != normalized["user_id"]:
            risk_binding_errors.append("action_user_scope_mismatch")
        if request.get("correlation_id") != normalized["correlation_id"]:
            risk_binding_errors.append("action_correlation_mismatch")
        if request.get("requested_at") != normalized["occurred_at"]:
            risk_binding_errors.append("action_time_mismatch")
        if risk_binding_errors:
            risk = {
                "decision": "BLOCK",
                "execution_permitted": False,
                "risk_level": "UNKNOWN",
                "reason": risk_binding_errors[0],
                "policy_version": "lumina.risk.policy.v1",
            }
        else:
            try:
                raw_risk = evaluate_action_request(request, now=occurred)
                risk = {
                    key: copy.deepcopy(raw_risk[key])
                    for key in (
                        "policy_version",
                        "risk_level",
                        "decision",
                        "execution_permitted",
                        "reason",
                        "request_sha256",
                        "decision_sha256",
                    )
                }
            except RiskPolicyError as exc:
                risk = {
                    "decision": "BLOCK",
                    "execution_permitted": False,
                    "risk_level": "UNKNOWN",
                    "reason": f"risk_policy_error:{exc.code}",
                    "policy_version": "lumina.risk.policy.v1",
                }

        policy = settings["policy"]["value"]
        threshold = float(settings["minimum_confidence"]["value"])
        cooldown = _cooldown(
            normalized,
            normalized_history,
            int(settings["cooldown_seconds"]["value"]),
        )
        quiet = settings["quiet_hours"]["value"]
        critical_safety_override = (
            normalized["trigger_type"] == "safety_alert"
            and normalized["urgency"] == "critical"
            and normalized["context"].get("safety_validated") is True
            and risk["execution_permitted"] is True
        )

        required_label = {
            "assistance": "trouble",
            "interest_tip": "interest",
            "safety_alert": "trouble",
        }.get(normalized["trigger_type"])
        selected_inference = _inference(perception, required_label) if required_label else None
        signal_present = selected_inference is None or selected_inference["state"] == "present"
        inferred_confidence = float(selected_inference["confidence"]) if selected_inference else 1.0
        effective_confidence = min(normalized["confidence"], inferred_confidence)

        blockers: list[str] = []
        if risk_binding_errors:
            blockers.extend(risk_binding_errors)
        if risk["execution_permitted"] is not True:
            blockers.append(f"risk_policy:{risk['reason']}")
        blockers.extend(settings_errors)
        if "no_proactive_interruptions" in applied_boundaries:
            blockers.append("boundary:no_proactive_interruptions")
        if normalized["channel"] == "voice" and "no_voice_interruptions" in applied_boundaries:
            blockers.append("boundary:no_voice_interruptions")
        if normalized["trigger_type"] == "safety_alert" and "no_safety_alerts" in applied_boundaries:
            blockers.append("boundary:no_safety_alerts")
        if policy == "off":
            blockers.append("relationship_preference:proactive_off")
        elif policy == "important_only" and normalized["urgency"] == "routine":
            blockers.append("relationship_preference:important_only")
        preferred_channel = settings["channel"]["value"]
        if preferred_channel not in {"any", normalized["channel"]}:
            blockers.append("relationship_preference:channel_mismatch")
        if quiet["active"] and not critical_safety_override:
            blockers.append("quiet_hours_active")
        if cooldown["active"] and not critical_safety_override:
            blockers.append("cooldown_active")
        if perception["primary_state"] == "disengagement" and not critical_safety_override:
            blockers.append("perception_disengagement")
        if normalized["trigger_type"] == "reminder" and not (
            normalized["context"].get("user_requested") is True
            and normalized["context"].get("due_now") is True
        ):
            blockers.append("reminder_not_explicitly_due")
        if normalized["trigger_type"] == "safety_alert" and normalized["context"].get("safety_validated") is not True:
            blockers.append("safety_signal_not_validated")
        if not signal_present:
            blockers.append(f"perception_signal_missing:{required_label}")
        if effective_confidence < threshold:
            blockers.append("confidence_below_threshold")

        should_trigger = not blockers
        if normalized["trigger_type"] == "reminder":
            trigger_reason = "explicit_due_reminder_above_threshold"
        elif critical_safety_override:
            trigger_reason = "validated_critical_safety_override"
        else:
            trigger_reason = f"grounded_{required_label}_above_threshold"
        primary_reason = trigger_reason if should_trigger else blockers[0]
        reasons = [trigger_reason] if should_trigger else blockers
        explanation = (
            f"TRIGGER: {trigger_reason}; all suppression gates passed"
            if should_trigger
            else f"NO_TRIGGER: {primary_reason}; {len(blockers)} suppression gate(s) active"
        )
        decision = {
            "schema_version": DECISION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "candidate_id": normalized["candidate_id"],
            "correlation_id": normalized["correlation_id"],
            "tenant_id": normalized["tenant_id"],
            "user_id": normalized["user_id"],
            "session_id": normalized["session_id"],
            "occurred_at": normalized["occurred_at"],
            "trigger_type": normalized["trigger_type"],
            "urgency": normalized["urgency"],
            "channel": normalized["channel"],
            "decision": "TRIGGER" if should_trigger else "NO_TRIGGER",
            "should_trigger": should_trigger,
            "primary_reason": primary_reason,
            "reasons": reasons,
            "explanation": explanation,
            "confidence": {
                "candidate": normalized["confidence"],
                "required_perception_label": required_label,
                "perception_state": selected_inference["state"] if selected_inference else "not_required",
                "perception": inferred_confidence,
                "effective": effective_confidence,
                "threshold": threshold,
                "passed": effective_confidence >= threshold,
            },
            "perception": {
                "schema_version": perception["schema_version"],
                "primary_state": perception["primary_state"],
                "primary_confidence": perception["primary_confidence"],
                "selected_inference": copy.deepcopy(selected_inference),
                "separation_enforced": perception["separation_enforced"],
            },
            "relationship": {
                "scope": relationship["policy"]["scope"],
                "state_sha256": relationship["canonical_state_sha256"],
                "settings": settings,
                "applied_boundaries": applied_boundaries,
                "settings_errors": settings_errors,
            },
            "quiet_hours": {
                **copy.deepcopy(quiet),
                "critical_safety_override": critical_safety_override and quiet["active"],
            },
            "cooldown": {
                **cooldown,
                "critical_safety_override": critical_safety_override and cooldown["active"],
            },
            "risk": risk,
            "execution_performed": False,
        }
        decision["decision_sha256"] = _stable_hash(decision)
        return decision

    def decide_batch(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        history: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        normalized = [_normalize_candidate(item) for item in candidates]
        ids = [item["candidate_id"] for item in normalized]
        if len(ids) != len(set(ids)):
            raise ProactiveTriggerError("duplicate candidate_id")
        ordered = sorted(
            normalized,
            key=lambda item: (item["occurred_at"], item["tenant_id"], item["user_id"], item["candidate_id"]),
        )
        replay_history = [_history_record(item) for item in history]
        decisions: list[dict[str, Any]] = []
        for candidate in ordered:
            decision = self.decide(candidate, history=replay_history)
            decisions.append(decision)
            replay_history.append({
                "candidate_id": decision["candidate_id"],
                "tenant_id": decision["tenant_id"],
                "user_id": decision["user_id"],
                "trigger_type": decision["trigger_type"],
                "occurred_at": decision["occurred_at"],
                "decision": decision["decision"],
            })
        result = {
            "schema_version": BATCH_SCHEMA,
            "policy_version": POLICY_VERSION,
            "decisions": decisions,
            "trigger_count": sum(int(item["should_trigger"]) for item in decisions),
            "no_trigger_count": sum(int(not item["should_trigger"]) for item in decisions),
        }
        result["batch_sha256"] = _stable_hash(result)
        return result


def evaluate_proactive_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(fixture, Mapping):
        raise ProactiveTriggerError("fixture must be an object")
    events = fixture.get("relationship_events")
    memory = fixture.get("memory_records", [])
    cases = fixture.get("cases")
    leakage_checks = fixture.get("leakage_checks", [])
    if not isinstance(events, list) or not isinstance(memory, list):
        raise ProactiveTriggerError("fixture relationship data must be arrays")
    if not isinstance(cases, list) or not cases:
        raise ProactiveTriggerError("fixture cases are required")
    if not isinstance(leakage_checks, list):
        raise ProactiveTriggerError("fixture leakage_checks must be an array")
    service = ProactiveTriggerService(events, memory_records=memory)

    def replay(ordered_cases: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        decisions = [
            service.decide(case["candidate"], history=case.get("history", []))
            for case in ordered_cases
        ]
        decisions.sort(key=lambda item: item["candidate_id"])
        return decisions, _stable_hash(decisions)

    decisions, replay_sha = replay(cases)
    decision_by_id = {item["candidate_id"]: item for item in decisions}
    tp = fp = fn = tn = 0
    negative_total = negative_pass = 0
    critical_unsafe_total = critical_unsafe_interrupts = 0
    reason_match_count = 0
    for case in cases:
        candidate_id = str(case["candidate"]["candidate_id"])
        actual = decision_by_id[candidate_id]["should_trigger"]
        expected = case.get("expected_trigger")
        if not isinstance(expected, bool):
            raise ProactiveTriggerError("expected_trigger must be boolean")
        tp += int(actual and expected)
        fp += int(actual and not expected)
        fn += int(not actual and expected)
        tn += int(not actual and not expected)
        if case.get("negative_control") is True:
            negative_total += 1
            negative_pass += int(not actual)
        if case.get("critical_unsafe") is True:
            critical_unsafe_total += 1
            critical_unsafe_interrupts += int(actual)
        expected_reason = case.get("expected_primary_reason")
        if expected_reason is not None:
            reason_match_count += int(decision_by_id[candidate_id]["primary_reason"] == expected_reason)

    leakage_count = 0
    for check in leakage_checks:
        decision = decision_by_id[str(check["candidate_id"])]
        serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True)
        leakage_count += sum(serialized.count(str(marker)) for marker in check.get("forbidden_markers", []))

    replay_hashes = [replay_sha]
    for ordered_cases in (reversed(cases), sorted(cases, key=lambda item: str(item["candidate"]["candidate_id"]))):
        _, digest = replay(ordered_cases)
        replay_hashes.append(digest)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "schema_version": EVALUATION_SCHEMA,
        "case_count": len(cases),
        "labeled_positive_count": tp + fn,
        "labeled_negative_count": tn + fp,
        "true_positive_count": tp,
        "false_positive_count": fp,
        "false_negative_count": fn,
        "true_negative_count": tn,
        "proactive_precision": precision,
        "proactive_recall": recall,
        "critical_unsafe_case_count": critical_unsafe_total,
        "critical_unsafe_interrupt_count": critical_unsafe_interrupts,
        "negative_control_count": negative_total,
        "negative_control_pass_rate": negative_pass / negative_total if negative_total else 0.0,
        "expected_primary_reason_match_rate": reason_match_count / len(cases),
        "cross_user_leakage_count": leakage_count,
        "deterministic_replay": len(set(replay_hashes)) == 1,
        "deterministic_replay_sha256": replay_hashes[0],
    }


__all__ = [
    "CANDIDATE_SCHEMA",
    "DECISION_SCHEMA",
    "POLICY_VERSION",
    "ProactiveTriggerError",
    "ProactiveTriggerService",
    "evaluate_proactive_fixture",
]
