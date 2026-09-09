"""User-scoped memory consent and control receipts for P3-02.

Consent defaults to denied. The controller is a local reference boundary and
does not write a database. Later runtime integration must call ``can_save``
before every user-memory write and must consume bound delete/export receipts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


MEMORY_CONSENT_SCHEMA = "lumina.memory.consent.v1"
MEMORY_CONTROL_RECEIPT_SCHEMA = "lumina.memory.control.receipt.v1"
MEMORY_CONTROL_EVENT_SCHEMA = "lumina.memory.control.event.v1"
MEMORY_CONTROLLER_SNAPSHOT_SCHEMA = "lumina.memory.consent.controller-snapshot.v1"
MEMORY_LAYERS = ("working", "episodic", "semantic", "profile", "relationship")
CONTROL_OPERATIONS = ("DELETE", "EXPORT")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class MemoryConsentError(ValueError):
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
        raise MemoryConsentError("MEMORY_CONSENT_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise MemoryConsentError("MEMORY_CONSENT_ID_INVALID", field_name)
    return value


def _now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise MemoryConsentError("MEMORY_CONSENT_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MemoryConsentController:
    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._consumed_receipts: set[str] = set()
        self._sequence = 0

    def _event(self, user_id: str, event_type: str, now: datetime, detail: Mapping[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        event = {
            "schema_version": MEMORY_CONTROL_EVENT_SCHEMA,
            "event_id": f"memory-control-event:{self._sequence}",
            "sequence": self._sequence,
            "user_id": user_id,
            "event_type": event_type,
            "occurred_at": _iso(now),
            "detail": _copy(detail),
        }
        event["event_sha256"] = _sha256(event)
        self._events.append(event)
        return event

    def describe(self, user_id: str) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        state = self._states.get(user)
        if state is None:
            return {
                "schema_version": MEMORY_CONSENT_SCHEMA,
                "user_id": user,
                "storage_allowed": False,
                "allowed_layers": [],
                "granted_at": None,
                "revoked_at": None,
                "default_deny": True,
            }
        return _copy(state)

    def set_storage_consent(
        self,
        *,
        user_id: str,
        storage_allowed: bool,
        allowed_layers: Iterable[str],
        confirmed: bool,
        now: datetime,
    ) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        current = _now(now)
        if not isinstance(storage_allowed, bool) or confirmed is not True:
            raise MemoryConsentError("MEMORY_CONSENT_EXPLICIT_CONFIRMATION_REQUIRED", user)
        layers = sorted(set(allowed_layers))
        if any(layer not in MEMORY_LAYERS for layer in layers):
            raise MemoryConsentError("MEMORY_CONSENT_LAYER_UNKNOWN", ",".join(layers))
        if storage_allowed and not layers:
            raise MemoryConsentError("MEMORY_CONSENT_LAYER_REQUIRED", user)
        if not storage_allowed:
            layers = []
        previous = self.describe(user)
        state = {
            "schema_version": MEMORY_CONSENT_SCHEMA,
            "user_id": user,
            "storage_allowed": storage_allowed,
            "allowed_layers": layers,
            "granted_at": _iso(current) if storage_allowed else previous.get("granted_at"),
            "revoked_at": None if storage_allowed else _iso(current),
            "default_deny": True,
        }
        self._states[user] = state
        self._event(user, "CONSENT_GRANTED" if storage_allowed else "CONSENT_REVOKED", current, {"allowed_layers": layers})
        return _copy(state)

    def can_save(self, *, user_id: str, layer: str) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        if layer not in MEMORY_LAYERS:
            return {"allowed": False, "reason": "unknown_layer", "user_id": user, "layer": layer}
        state = self.describe(user)
        allowed = state["storage_allowed"] is True and layer in state["allowed_layers"]
        return {
            "allowed": allowed,
            "reason": "explicit_consent" if allowed else "consent_absent_or_scope_denied",
            "user_id": user,
            "layer": layer,
        }

    def issue_control_receipt(
        self,
        *,
        user_id: str,
        operation: str,
        item_ids: Iterable[str],
        confirmation_text: str,
        now: datetime,
        ttl_seconds: int = 60,
    ) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        current = _now(now)
        if operation not in CONTROL_OPERATIONS:
            raise MemoryConsentError("MEMORY_CONTROL_OPERATION_UNKNOWN", operation)
        items = sorted({_id(item, "item_id") for item in item_ids})
        if not items:
            raise MemoryConsentError("MEMORY_CONTROL_ITEMS_REQUIRED", operation)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 300:
            raise MemoryConsentError("MEMORY_CONTROL_TTL_INVALID", str(ttl_seconds))
        expected = f"{operation} {len(items)} MEMORY ITEMS"
        if confirmation_text != expected:
            raise MemoryConsentError("MEMORY_CONTROL_CONFIRMATION_MISMATCH", expected)
        receipt_id = f"memory-receipt:{self._sequence + 1}"
        receipt = {
            "schema_version": MEMORY_CONTROL_RECEIPT_SCHEMA,
            "receipt_id": receipt_id,
            "user_id": user,
            "operation": operation,
            "item_ids": items,
            "item_set_sha256": _sha256(items),
            "confirmation_sha256": hashlib.sha256(confirmation_text.encode("utf-8")).hexdigest(),
            "issued_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=ttl_seconds)),
            "one_time": True,
        }
        receipt["receipt_sha256"] = _sha256(receipt)
        self._event(user, "CONTROL_RECEIPT_ISSUED", current, {"receipt_id": receipt_id, "operation": operation, "item_count": len(items)})
        return receipt

    def consume_control_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        user_id: str,
        operation: str,
        item_ids: Iterable[str],
        now: datetime,
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping) or receipt.get("schema_version") != MEMORY_CONTROL_RECEIPT_SCHEMA:
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_SCHEMA_MISMATCH", "receipt")
        user = _id(user_id, "user_id")
        current = _now(now)
        items = sorted({_id(item, "item_id") for item in item_ids})
        receipt_id = _id(receipt.get("receipt_id"), "receipt_id")
        if receipt_id in self._consumed_receipts:
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_REPLAYED", receipt_id)
        if receipt.get("receipt_sha256") != _sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"}):
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_TAMPERED", receipt_id)
        if receipt.get("user_id") != user or receipt.get("operation") != operation:
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_BINDING_MISMATCH", receipt_id)
        if receipt.get("item_ids") != items or receipt.get("item_set_sha256") != _sha256(items):
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_ITEM_MISMATCH", receipt_id)
        expires = datetime.fromisoformat(str(receipt.get("expires_at")).replace("Z", "+00:00"))
        if expires <= current:
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_EXPIRED", receipt_id)
        if receipt.get("one_time") is not True:
            raise MemoryConsentError("MEMORY_CONTROL_RECEIPT_NOT_ONE_TIME", receipt_id)
        self._consumed_receipts.add(receipt_id)
        self._event(user, "CONTROL_RECEIPT_CONSUMED", current, {"receipt_id": receipt_id, "operation": operation, "item_count": len(items)})
        return {"authorized": True, "receipt_id": receipt_id, "operation": operation, "item_ids": items}

    def export_user_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        user_id: str,
        receipt: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        copied = [_copy(dict(record)) for record in records]
        user_records = [record for record in copied if record.get("user_id") == user]
        item_ids = [str(record.get("item_id")) for record in user_records]
        self.consume_control_receipt(receipt, user_id=user, operation="EXPORT", item_ids=item_ids, now=now)
        payload = {"schema_version": "lumina.memory.export.v1", "user_id": user, "records": user_records}
        payload["export_sha256"] = _sha256(payload)
        return payload

    def audit_events(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id is None:
            return _copy(self._events)
        user = _id(user_id, "user_id")
        return _copy([event for event in self._events if event["user_id"] == user])

    def snapshot(self) -> dict[str, Any]:
        body = {
            "schema_version": MEMORY_CONTROLLER_SNAPSHOT_SCHEMA,
            "states": _copy(self._states),
            "events": _copy(self._events),
            "consumed_receipts": sorted(self._consumed_receipts),
            "sequence": self._sequence,
        }
        body["snapshot_sha256"] = _sha256(body)
        return body

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "MemoryConsentController":
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != MEMORY_CONTROLLER_SNAPSHOT_SCHEMA:
            raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_SCHEMA_MISMATCH", "snapshot")
        expected = _sha256({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})
        if snapshot.get("snapshot_sha256") != expected:
            raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_TAMPERED", "snapshot")
        states = snapshot.get("states")
        events = snapshot.get("events")
        receipts = snapshot.get("consumed_receipts")
        sequence = snapshot.get("sequence")
        if not isinstance(states, Mapping) or not isinstance(events, list) or not isinstance(receipts, list):
            raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_SHAPE_INVALID", "snapshot")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_SEQUENCE_INVALID", "sequence")
        normalized_states: dict[str, dict[str, Any]] = {}
        for raw_user, raw_state in states.items():
            user = _id(raw_user, "state.user_id")
            if not isinstance(raw_state, Mapping) or raw_state.get("user_id") != user:
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_STATE_INVALID", user)
            allowed = raw_state.get("storage_allowed")
            layers = raw_state.get("allowed_layers")
            if not isinstance(allowed, bool) or not isinstance(layers, list):
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_STATE_INVALID", user)
            if any(layer not in MEMORY_LAYERS for layer in layers) or (not allowed and layers):
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_STATE_INVALID", user)
            normalized_states[user] = _copy(dict(raw_state))
        normalized_events: list[dict[str, Any]] = []
        last_sequence = 0
        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_EVENT_INVALID", "event")
            event = dict(raw_event)
            event_sequence = event.get("sequence")
            if isinstance(event_sequence, bool) or not isinstance(event_sequence, int) or event_sequence <= last_sequence:
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_EVENT_SEQUENCE_INVALID", "event")
            _id(event.get("user_id"), "event.user_id")
            expected_event = _sha256({key: value for key, value in event.items() if key != "event_sha256"})
            if event.get("event_sha256") != expected_event:
                raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_EVENT_TAMPERED", str(event_sequence))
            last_sequence = event_sequence
            normalized_events.append(_copy(event))
        if sequence < last_sequence:
            raise MemoryConsentError("MEMORY_CONTROLLER_SNAPSHOT_SEQUENCE_ROLLBACK", "sequence")
        normalized_receipts = {_id(item, "consumed_receipt") for item in receipts}
        controller = cls()
        controller._states = normalized_states
        controller._events = normalized_events
        controller._consumed_receipts = normalized_receipts
        controller._sequence = sequence
        return controller


__all__ = [
    "CONTROL_OPERATIONS",
    "MEMORY_CONSENT_SCHEMA",
    "MEMORY_CONTROLLER_SNAPSHOT_SCHEMA",
    "MEMORY_CONTROL_EVENT_SCHEMA",
    "MEMORY_CONTROL_RECEIPT_SCHEMA",
    "MEMORY_LAYERS",
    "MemoryConsentController",
    "MemoryConsentError",
]
