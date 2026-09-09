"""Canonical World State v1 contract and replay-safe in-process event bus.

The module is intentionally local-only and dependency-free.  It gives the
runtime one fail-closed place to validate schema versions, freshness, event
ordering, duplicate delivery, and deterministic replay before later adapters
connect real Godot or sensor producers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


SNAPSHOT_SCHEMA_VERSION = "lumina.world.state.snapshot.v1"
EVENT_SCHEMA_VERSION = "lumina.world.state.event.v1"
DEFAULT_STALE_AFTER_SECONDS = 10.0
MAX_FUTURE_SKEW_SECONDS = 2.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class WorldStateContractError(ValueError):
    """Fail-closed contract error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorldStateContractError(code, detail)
    return value


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise WorldStateContractError("WORLD_ID_INVALID", f"{field_name} is not canonical")
    return value


def _parse_utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WorldStateContractError("WORLD_TIMESTAMP_REQUIRED", f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorldStateContractError("WORLD_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise WorldStateContractError("WORLD_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _now_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise WorldStateContractError("WORLD_NOW_TIMEZONE_REQUIRED", "now must be timezone-aware")
    return now.astimezone(timezone.utc)


def validate_world_state_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Validate and normalize a snapshot while deriving conservative staleness."""

    source = _require_mapping(snapshot, "WORLD_SNAPSHOT_OBJECT_REQUIRED", "snapshot must be an object")
    if source.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise WorldStateContractError("WORLD_SNAPSHOT_SCHEMA_MISMATCH", "unsupported schema_version")
    if not isinstance(stale_after_seconds, (int, float)) or stale_after_seconds <= 0:
        raise WorldStateContractError("WORLD_STALE_THRESHOLD_INVALID", "stale_after_seconds must be positive")

    _require_id(source.get("tenant_id"), "tenant_id")
    _require_id(source.get("world_id"), "world_id")
    _require_id(source.get("snapshot_id"), "snapshot_id")
    _require_id(source.get("source_id"), "source_id")
    observed_at = _parse_utc(source.get("observed_at"), "observed_at")
    current = _now_utc(now)
    age_seconds = (current - observed_at).total_seconds()
    if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise WorldStateContractError("WORLD_SNAPSHOT_FROM_FUTURE", "observed_at exceeds allowed clock skew")

    users = source.get("users")
    objects = source.get("objects")
    if not isinstance(users, list) or not isinstance(objects, list):
        raise WorldStateContractError("WORLD_COLLECTIONS_REQUIRED", "users and objects must be arrays")
    if not isinstance(source.get("event_refs"), list) or not isinstance(source.get("evidence"), list):
        raise WorldStateContractError("WORLD_TRACE_REFS_REQUIRED", "event_refs and evidence must be arrays")

    subject_stale = False
    for collection_name, collection, id_field in (
        ("users", users, "user_id"),
        ("objects", objects, "object_id"),
    ):
        for index, item in enumerate(collection):
            record = _require_mapping(
                item,
                "WORLD_SUBJECT_OBJECT_REQUIRED",
                f"{collection_name}[{index}] must be an object",
            )
            _require_id(record.get(id_field), f"{collection_name}[{index}].{id_field}")
            if not isinstance(record.get("confidence"), (int, float)) or not 0 <= float(record["confidence"]) <= 1:
                raise WorldStateContractError("WORLD_CONFIDENCE_INVALID", f"{collection_name}[{index}]")
            item_observed = _parse_utc(record.get("observed_at"), f"{collection_name}[{index}].observed_at")
            if (current - item_observed).total_seconds() > stale_after_seconds or record.get("stale") is True:
                subject_stale = True

    freshness = _require_mapping(source.get("freshness"), "WORLD_FRESHNESS_REQUIRED", "freshness is required")
    freshness_as_of = _parse_utc(freshness.get("as_of"), "freshness.as_of")
    if abs((freshness_as_of - observed_at).total_seconds()) > MAX_FUTURE_SKEW_SECONDS:
        raise WorldStateContractError("WORLD_FRESHNESS_TIMESTAMP_MISMATCH", "freshness.as_of must match observed_at")

    normalized = _copy(source)
    computed_stale = age_seconds > stale_after_seconds or freshness.get("is_stale") is True or subject_stale
    normalized["freshness"] = {
        **_copy(freshness),
        "as_of": freshness_as_of.isoformat().replace("+00:00", "Z"),
        "age_seconds": max(0.0, age_seconds),
        "is_stale": bool(computed_stale),
        "stale_after_seconds": float(stale_after_seconds),
    }
    return normalized


def validate_world_state_event(
    event: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Validate the canonical event envelope and its embedded snapshot."""

    source = _require_mapping(event, "WORLD_EVENT_OBJECT_REQUIRED", "event must be an object")
    if source.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise WorldStateContractError("WORLD_EVENT_SCHEMA_MISMATCH", "unsupported schema_version")
    _require_id(source.get("event_id"), "event_id")
    _require_id(source.get("correlation_id"), "correlation_id")
    _require_id(source.get("source_id"), "source_id")
    sequence = source.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise WorldStateContractError("WORLD_EVENT_SEQUENCE_INVALID", "sequence must be a positive integer")
    occurred_at = _parse_utc(source.get("occurred_at"), "occurred_at")
    observed_at = _parse_utc(source.get("observed_at"), "observed_at")
    if occurred_at > observed_at:
        raise WorldStateContractError("WORLD_EVENT_TIME_ORDER_INVALID", "occurred_at must not exceed observed_at")

    delivery = _require_mapping(source.get("delivery"), "WORLD_DELIVERY_REQUIRED", "delivery is required")
    if delivery.get("dedupe_key") != source.get("event_id"):
        raise WorldStateContractError("WORLD_DEDUPE_KEY_INVALID", "dedupe_key must equal event_id")
    attempt = delivery.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise WorldStateContractError("WORLD_DELIVERY_ATTEMPT_INVALID", "attempt must be a positive integer")
    replay = delivery.get("replay")
    replay_run_id = delivery.get("replay_run_id")
    if replay is True:
        _require_id(replay_run_id, "delivery.replay_run_id")
    elif replay is not False or replay_run_id is not None:
        raise WorldStateContractError("WORLD_REPLAY_ENVELOPE_INVALID", "non-replay events must use replay=false and null replay_run_id")

    normalized = _copy(source)
    normalized["payload"] = validate_world_state_snapshot(
        _require_mapping(source.get("payload"), "WORLD_PAYLOAD_REQUIRED", "payload is required"),
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    return normalized


def _event_identity(event: Mapping[str, Any]) -> dict[str, Any]:
    """Remove transport retry fields before comparing duplicate semantics."""

    comparable = _copy(event)
    comparable["delivery"] = {
        "dedupe_key": event["delivery"]["dedupe_key"],
    }
    comparable.get("payload", {}).get("freshness", {}).pop("age_seconds", None)
    return comparable


@dataclass
class WorldStateEventBus:
    """Single-partition, replay-safe World State projection bus."""

    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS
    cursor: int = 0
    current_snapshot: dict[str, Any] | None = None
    _event_hashes: dict[str, str] = field(default_factory=dict)
    _event_log: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    received: int = 0
    applied: int = 0
    duplicates: int = 0
    gap_events: int = 0

    def _quarantine(self, event: Mapping[str, Any], reason: str, *, missing: int = 0) -> dict[str, Any]:
        self.quarantined.append({"event_id": event.get("event_id"), "sequence": event.get("sequence"), "reason": reason})
        self.gap_events += max(0, missing)
        return {
            "status": "QUARANTINED",
            "reason": reason,
            "cursor": self.cursor,
            "projection_applied": False,
            "cursor_advanced": False,
            "side_effects_suppressed": True,
        }

    def ingest(self, event: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        normalized = validate_world_state_event(
            event,
            now=now,
            stale_after_seconds=self.stale_after_seconds,
        )
        self.received += 1
        event_id = normalized["event_id"]
        semantic_hash = _canonical_sha256(_event_identity(normalized))
        previous_hash = self._event_hashes.get(event_id)
        if previous_hash is not None:
            if previous_hash != semantic_hash:
                return self._quarantine(normalized, "DUPLICATE_CONTENT_CONFLICT")
            self.duplicates += 1
            return {
                "status": "DUPLICATE",
                "reason": "IDENTICAL_RETRY_OR_REPLAY",
                "cursor": self.cursor,
                "projection_applied": False,
                "cursor_advanced": False,
                "side_effects_suppressed": True,
            }

        expected = self.cursor + 1
        sequence = normalized["sequence"]
        if sequence > expected:
            return self._quarantine(normalized, "SEQUENCE_GAP", missing=sequence - expected)
        if sequence < expected:
            return self._quarantine(normalized, "SEQUENCE_ROLLBACK")
        if normalized["payload"]["freshness"]["is_stale"]:
            return self._quarantine(normalized, "STALE_WORLD_STATE")

        self._event_hashes[event_id] = semantic_hash
        self._event_log.append(_copy(normalized))
        self.current_snapshot = _copy(normalized["payload"])
        self.cursor = sequence
        self.applied += 1
        return {
            "status": "APPLIED",
            "reason": "CONTIGUOUS_FRESH_EVENT",
            "cursor": self.cursor,
            "projection_applied": True,
            "cursor_advanced": True,
            "side_effects_suppressed": False,
            "snapshot_id": self.current_snapshot["snapshot_id"],
        }

    @property
    def event_loss_rate(self) -> float:
        denominator = self.applied + self.gap_events
        return self.gap_events / denominator if denominator else 0.0

    @property
    def projection_sha256(self) -> str | None:
        if self.current_snapshot is None:
            return None
        normalized = _copy(self.current_snapshot)
        normalized.get("freshness", {}).pop("age_seconds", None)
        return _canonical_sha256(normalized)

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "lumina.world.state.bus.report.v1",
            "cursor": self.cursor,
            "received": self.received,
            "applied": self.applied,
            "duplicates": self.duplicates,
            "quarantined": len(self.quarantined),
            "gap_events": self.gap_events,
            "event_loss_rate": self.event_loss_rate,
            "projection_sha256": self.projection_sha256,
            "current_snapshot_id": self.current_snapshot.get("snapshot_id") if self.current_snapshot else None,
        }


def replay_world_state_events(
    events: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Replay events in recorded order and return a deterministic report."""

    bus = WorldStateEventBus(stale_after_seconds=stale_after_seconds)
    results = [bus.ingest(event, now=now) for event in events]
    return {**bus.report(), "results": results, "deterministic_replay": len(bus.quarantined) == 0}


__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "EVENT_SCHEMA_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "WorldStateContractError",
    "WorldStateEventBus",
    "replay_world_state_events",
    "validate_world_state_event",
    "validate_world_state_snapshot",
]
