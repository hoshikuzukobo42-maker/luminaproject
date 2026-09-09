"""Deterministic autonomy-loop watchdog for P4-06.

The watchdog emits control decisions only; it does not signal or kill an OS
process. Missing heartbeats, incomplete traces, action repetition, sustained
resource pressure, and explicit stop requests immediately enter safe idle.
The kill switch enters a latched KILLED state until an explicit safe reset.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


WATCHDOG_SAMPLE_SCHEMA = "lumina.autonomy.watchdog.sample.v1"
WATCHDOG_DECISION_SCHEMA = "lumina.autonomy.watchdog.decision.v1"
WATCHDOG_EVENT_SCHEMA = "lumina.autonomy.watchdog.event.v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class AutonomyWatchdogError(ValueError):
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
        raise AutonomyWatchdogError("WATCHDOG_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise AutonomyWatchdogError("WATCHDOG_ID_INVALID", field_name)
    return value


def _time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AutonomyWatchdogError("WATCHDOG_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutonomyWatchdogError("WATCHDOG_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise AutonomyWatchdogError("WATCHDOG_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise AutonomyWatchdogError("WATCHDOG_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WatchdogConfig:
    heartbeat_timeout_seconds: float = 5.0
    max_repeated_action_count: int = 3
    cpu_percent_max: float = 90.0
    memory_percent_max: float = 90.0
    resource_consecutive_limit: int = 3
    recovery_seconds_max_exclusive: float = 30.0


def validate_watchdog_sample(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AutonomyWatchdogError("WATCHDOG_SAMPLE_OBJECT_REQUIRED", "sample")
    allowed = {
        "schema_version", "sample_id", "loop_id", "correlation_id",
        "sampled_at", "heartbeat_at", "action_id", "trace_complete",
        "cpu_percent", "memory_percent", "stop_requested",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise AutonomyWatchdogError("WATCHDOG_UNKNOWN_SAMPLE_FIELDS", ",".join(unknown))
    if value.get("schema_version") != WATCHDOG_SAMPLE_SCHEMA:
        raise AutonomyWatchdogError("WATCHDOG_SAMPLE_SCHEMA_MISMATCH", "schema_version")
    numeric: dict[str, float] = {}
    for field_name in ("cpu_percent", "memory_percent"):
        item = value.get(field_name)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) or not 0 <= float(item) <= 100:
            raise AutonomyWatchdogError("WATCHDOG_RESOURCE_VALUE_INVALID", field_name)
        numeric[field_name] = float(item)
    if not isinstance(value.get("trace_complete"), bool) or not isinstance(value.get("stop_requested"), bool):
        raise AutonomyWatchdogError("WATCHDOG_BOOLEAN_REQUIRED", "trace_complete/stop_requested")
    normalized = {
        "schema_version": WATCHDOG_SAMPLE_SCHEMA,
        "sample_id": _id(value.get("sample_id"), "sample_id"),
        "loop_id": _id(value.get("loop_id"), "loop_id"),
        "correlation_id": _id(value.get("correlation_id"), "correlation_id"),
        "sampled_at": _iso(_time(value.get("sampled_at"), "sampled_at")),
        "heartbeat_at": _iso(_time(value.get("heartbeat_at"), "heartbeat_at")),
        "action_id": _id(value.get("action_id"), "action_id"),
        "trace_complete": value["trace_complete"],
        "cpu_percent": numeric["cpu_percent"],
        "memory_percent": numeric["memory_percent"],
        "stop_requested": value["stop_requested"],
    }
    _canonical_json(normalized)
    return normalized


class AutonomyWatchdog:
    def __init__(self, config: WatchdogConfig | None = None) -> None:
        self.config = config or WatchdogConfig()
        self.state = "RUNNING"
        self._last_action: str | None = None
        self._repeat_count = 0
        self._resource_pressure_count = 0
        self._events: list[dict[str, Any]] = []
        self._sequence = 0

    def _event(self, event_type: str, now: datetime, detail: Mapping[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        event = {
            "schema_version": WATCHDOG_EVENT_SCHEMA,
            "event_id": f"watchdog-event:{self._sequence}",
            "sequence": self._sequence,
            "event_type": event_type,
            "occurred_at": _iso(now),
            "detail": _copy(detail),
        }
        event["event_sha256"] = _sha256(event)
        self._events.append(event)
        return event

    def _decision(
        self,
        sample: Mapping[str, Any],
        *,
        now: datetime,
        reasons: list[str],
        detected_at: datetime | None,
    ) -> dict[str, Any]:
        latency = 0.0 if detected_at is None else max(0.0, (now - detected_at).total_seconds())
        result = {
            "schema_version": WATCHDOG_DECISION_SCHEMA,
            "sample_id": sample["sample_id"],
            "loop_id": sample["loop_id"],
            "correlation_id": sample["correlation_id"],
            "state": self.state,
            "safe_idle_required": self.state in {"SAFE_IDLE", "KILLED"},
            "action_execution_permitted": self.state == "RUNNING",
            "reasons": reasons,
            "recovery_latency_seconds": latency,
            "recovery_deadline_seconds": self.config.recovery_seconds_max_exclusive,
        }
        result["decision_sha256"] = _sha256(result)
        return result

    def ingest(self, value: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
        sample = validate_watchdog_sample(value)
        current = _now(now)
        sampled_at = _time(sample["sampled_at"], "sampled_at")
        heartbeat_at = _time(sample["heartbeat_at"], "heartbeat_at")
        if sampled_at > current + timedelta(seconds=2) or heartbeat_at > current + timedelta(seconds=2):
            raise AutonomyWatchdogError("WATCHDOG_SAMPLE_FROM_FUTURE", sample["sample_id"])
        if self.state == "KILLED":
            return self._decision(sample, now=current, reasons=["kill_switch_latched"], detected_at=current)

        if sample["action_id"] == self._last_action:
            self._repeat_count += 1
        else:
            self._last_action = sample["action_id"]
            self._repeat_count = 1
        pressured = (
            sample["cpu_percent"] > self.config.cpu_percent_max
            or sample["memory_percent"] > self.config.memory_percent_max
        )
        self._resource_pressure_count = self._resource_pressure_count + 1 if pressured else 0

        reasons: list[str] = []
        if sample["stop_requested"]:
            reasons.append("stop_requested")
        if not sample["trace_complete"]:
            reasons.append("trace_incomplete")
        if (current - heartbeat_at).total_seconds() > self.config.heartbeat_timeout_seconds:
            reasons.append("heartbeat_timeout")
        if self._repeat_count > self.config.max_repeated_action_count:
            reasons.append("action_repetition")
        if self._resource_pressure_count >= self.config.resource_consecutive_limit:
            reasons.append("sustained_resource_pressure")

        detected_at = current if reasons else None
        if reasons:
            self.state = "SAFE_IDLE"
            self._event("SAFE_IDLE_ENTERED", current, {"sample_id": sample["sample_id"], "reasons": reasons})
        elif self.state == "SAFE_IDLE":
            reasons = ["safe_idle_latched_until_reset"]
        else:
            self.state = "RUNNING"
        return self._decision(sample, now=current, reasons=reasons, detected_at=detected_at)

    def trigger_kill_switch(
        self,
        *,
        loop_id: str,
        correlation_id: str,
        requested_by: str,
        now: datetime,
    ) -> dict[str, Any]:
        current = _now(now)
        loop = _id(loop_id, "loop_id")
        correlation = _id(correlation_id, "correlation_id")
        requester = _id(requested_by, "requested_by")
        self.state = "KILLED"
        event = self._event("KILL_SWITCH_TRIGGERED", current, {"loop_id": loop, "correlation_id": correlation, "requested_by": requester})
        return {
            "state": self.state,
            "safe_idle_required": True,
            "action_execution_permitted": False,
            "recovery_latency_seconds": 0.0,
            "event": _copy(event),
        }

    def reset_safe_idle(
        self,
        *,
        requested_by: str,
        now: datetime,
        residual_actions: int,
    ) -> dict[str, Any]:
        current = _now(now)
        requester = _id(requested_by, "requested_by")
        if isinstance(residual_actions, bool) or not isinstance(residual_actions, int) or residual_actions < 0:
            raise AutonomyWatchdogError("WATCHDOG_RESIDUAL_COUNT_INVALID", str(residual_actions))
        if residual_actions != 0:
            raise AutonomyWatchdogError("WATCHDOG_RESET_RESIDUAL_ACTIONS", str(residual_actions))
        previous = self.state
        self.state = "RUNNING"
        self._last_action = None
        self._repeat_count = 0
        self._resource_pressure_count = 0
        self._event("WATCHDOG_RESET", current, {"previous": previous, "requested_by": requester, "residual_actions": 0})
        return {"state": self.state, "previous": previous, "residual_actions": 0}

    def audit_events(self) -> list[dict[str, Any]]:
        return _copy(self._events)

