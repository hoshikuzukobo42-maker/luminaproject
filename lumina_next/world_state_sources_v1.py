"""Adapters that normalize Godot, STT, and user-operation inputs to World State v1."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from lumina_next.world_state_v1 import EVENT_SCHEMA_VERSION, SNAPSHOT_SCHEMA_VERSION, WorldStateContractError


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise WorldStateContractError("WORLD_SOURCE_TIMEZONE_REQUIRED", "observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorldStateContractError("WORLD_SOURCE_OBJECT_REQUIRED", f"{field_name} must be an object")
    return value


def _confidence(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not 0.0 <= result <= 1.0:
        raise WorldStateContractError("WORLD_SOURCE_CONFIDENCE_INVALID", "confidence must be in [0, 1]")
    return result


@dataclass
class WorldStateSourceAdapter:
    """Monotonic envelope factory for the three product input families."""

    tenant_id: str = "lumina-local"
    world_id: str = "exhibit-main"
    sequence: int = 0

    def _event(
        self,
        *,
        source_kind: str,
        correlation_id: str,
        observed_at: datetime,
        users: list[dict[str, Any]],
        objects: list[dict[str, Any]],
        source_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.sequence += 1
        sequence = self.sequence
        source_id = f"source-{source_kind}"
        timestamp = _iso(observed_at)
        event_id = f"world-event-{sequence}"
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "tenant_id": self.tenant_id,
            "world_id": self.world_id,
            "snapshot_id": f"snapshot-{sequence}",
            "source_id": source_id,
            "observed_at": timestamp,
            "freshness": {"as_of": timestamp, "is_stale": False},
            "users": copy.deepcopy(users),
            "objects": copy.deepcopy(objects),
            "event_refs": [event_id],
            "evidence": [f"source://{source_kind}/{correlation_id}"],
            "source_event": {
                "kind": source_kind,
                "payload": copy.deepcopy(dict(source_payload)),
            },
        }
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "source_id": source_id,
            "sequence": sequence,
            "occurred_at": timestamp,
            "observed_at": timestamp,
            "delivery": {
                "dedupe_key": event_id,
                "attempt": 1,
                "replay": False,
                "replay_run_id": None,
            },
            "payload": snapshot,
        }

    def from_godot_world_state(
        self,
        state: Mapping[str, Any],
        *,
        correlation_id: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        source = _mapping(state, "state")
        timestamp = _iso(observed_at)
        users: list[dict[str, Any]] = []
        player = source.get("player_position")
        if isinstance(player, Mapping):
            users.append({
                "user_id": str(source.get("user_id") or "visitor-1"),
                "observed_at": timestamp,
                "confidence": _confidence(source.get("player_confidence"), 1.0),
                "stale": False,
                "position": copy.deepcopy(dict(player)),
                "fact_source": "godot_world_state",
            })
        objects: list[dict[str, Any]] = []
        nearby = source.get("nearby_objects", [])
        if not isinstance(nearby, list):
            raise WorldStateContractError("WORLD_SOURCE_COLLECTION_INVALID", "nearby_objects must be an array")
        for index, item in enumerate(nearby):
            obj = _mapping(item, f"nearby_objects[{index}]")
            name = str(obj.get("name") or f"object-{index + 1}")
            objects.append({
                "object_id": name,
                "observed_at": timestamp,
                "confidence": _confidence(obj.get("confidence"), 1.0),
                "stale": False,
                "position": copy.deepcopy(obj.get("position")),
                "distance": obj.get("distance"),
                "object_type": str(obj.get("type") or "object"),
                "fact_source": "godot_world_state",
            })
        return self._event(
            source_kind="godot",
            correlation_id=correlation_id,
            observed_at=observed_at,
            users=users,
            objects=objects,
            source_payload=source,
        )

    def from_stt_result(
        self,
        result: Mapping[str, Any],
        *,
        correlation_id: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        source = _mapping(result, "result")
        text = str(source.get("text") or "").strip()
        if not text:
            raise WorldStateContractError("WORLD_STT_TEXT_REQUIRED", "STT text is required")
        timestamp = _iso(observed_at)
        user_id = str(source.get("user_id") or "visitor-1")
        users = [{
            "user_id": user_id,
            "observed_at": timestamp,
            "confidence": _confidence(source.get("confidence"), 0.5),
            "stale": False,
            "speech": {"text": text, "final": bool(source.get("final", True))},
            "fact_source": "stt_result",
        }]
        return self._event(
            source_kind="stt",
            correlation_id=correlation_id,
            observed_at=observed_at,
            users=users,
            objects=[],
            source_payload=source,
        )

    def from_user_action(
        self,
        action: Mapping[str, Any],
        *,
        correlation_id: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        source = _mapping(action, "action")
        action_name = str(source.get("action") or source.get("type") or "").strip()
        if not action_name:
            raise WorldStateContractError("WORLD_USER_ACTION_REQUIRED", "action/type is required")
        timestamp = _iso(observed_at)
        user_id = str(source.get("user_id") or "visitor-1")
        users = [{
            "user_id": user_id,
            "observed_at": timestamp,
            "confidence": 1.0,
            "stale": False,
            "user_action": {"name": action_name, "params": copy.deepcopy(source.get("params") or {})},
            "fact_source": "user_action",
        }]
        return self._event(
            source_kind="user-action",
            correlation_id=correlation_id,
            observed_at=observed_at,
            users=users,
            objects=[],
            source_payload=source,
        )


__all__ = ["WorldStateSourceAdapter"]
