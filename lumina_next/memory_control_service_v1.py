"""Persistent, user-visible memory control boundary for P3-02.

The service is backend-agnostic. Production integration supplies the existing
memory adapter; tests use a temporary fake and never touch the real database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .memory_consent_v1 import MemoryConsentController, MemoryConsentError


SERVICE_SCHEMA = "lumina.memory.control-service.v1"
LEDGER_SCHEMA = "lumina.memory.control-ledger.v1"
_PUBLIC_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def normalize_memory_user_id(value: str) -> str:
    """Map arbitrary product user identifiers into the sealed control-ID space."""
    raw = str(value or "").strip()
    if not raw:
        raise MemoryConsentError("MEMORY_CONSENT_ID_INVALID", "user_id")
    if _PUBLIC_USER_ID.fullmatch(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"user:{digest}"


class MemoryManagementBackend(Protocol):
    def management_snapshot(self, limit: int = 100) -> dict[str, Any]: ...
    def forget(self, kind: str, item_id: str) -> bool: ...


class MemoryControlService:
    def __init__(
        self,
        backend: MemoryManagementBackend,
        *,
        ledger_path: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.backend = backend
        self.ledger_path = Path(ledger_path) if ledger_path is not None else None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.controller = self._load_controller()

    def _load_controller(self) -> MemoryConsentController:
        if self.ledger_path is None or not self.ledger_path.exists():
            return MemoryConsentController()
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MemoryConsentError("MEMORY_CONTROL_LEDGER_READ_FAILED", str(self.ledger_path)) from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != LEDGER_SCHEMA:
            raise MemoryConsentError("MEMORY_CONTROL_LEDGER_SCHEMA_MISMATCH", str(self.ledger_path))
        return MemoryConsentController.from_snapshot(payload.get("controller", {}))

    def _persist(self) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": LEDGER_SCHEMA, "controller": self.controller.snapshot()}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.ledger_path.name}.", suffix=".tmp", dir=self.ledger_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.ledger_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def consent_state(self, user_id: str) -> dict[str, Any]:
        return self.controller.describe(normalize_memory_user_id(user_id))

    def set_consent(
        self,
        *,
        user_id: str,
        storage_allowed: bool,
        allowed_layers: list[str],
        confirmed: bool,
    ) -> dict[str, Any]:
        normalized_user = normalize_memory_user_id(user_id)
        state = self.controller.set_storage_consent(
            user_id=normalized_user,
            storage_allowed=storage_allowed,
            allowed_layers=allowed_layers,
            confirmed=confirmed,
            now=self._now(),
        )
        self._persist()
        events = self.controller.audit_events(normalized_user)
        return {**state, "audit_event_id": events[-1]["event_id"] if events else None}

    def can_save(self, *, user_id: str, layer: str) -> dict[str, Any]:
        return self.controller.can_save(user_id=normalize_memory_user_id(user_id), layer=layer)

    def management_snapshot(self, *, user_id: str, limit: int = 100) -> dict[str, Any]:
        snapshot = self.backend.management_snapshot(limit=limit)
        return {**snapshot, "consent": self.consent_state(user_id)}

    @staticmethod
    def _control_records(snapshot: Mapping[str, Any], user_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for kind, key in (("profile_fact", "profile_facts"), ("episodic_memory", "episodic_memories")):
            values = snapshot.get(key, [])
            if not isinstance(values, list):
                continue
            for raw in values:
                if not isinstance(raw, Mapping) or not raw.get("id"):
                    continue
                records.append({
                    "item_id": str(raw["id"]),
                    "kind": kind,
                    "user_id": user_id,
                    "data": dict(raw),
                })
        return records

    def export_memory(
        self,
        *,
        user_id: str,
        item_ids: list[str],
        confirmation_text: str,
    ) -> dict[str, Any]:
        normalized_user = normalize_memory_user_id(user_id)
        records = self._control_records(self.backend.management_snapshot(limit=500), normalized_user)
        requested = sorted(set(item_ids))
        selected = [record for record in records if record["item_id"] in requested]
        if sorted(record["item_id"] for record in selected) != requested:
            raise MemoryConsentError("MEMORY_CONTROL_ITEM_NOT_FOUND", "export")
        receipt = self.controller.issue_control_receipt(
            user_id=normalized_user,
            operation="EXPORT",
            item_ids=requested,
            confirmation_text=confirmation_text,
            now=self._now(),
        )
        payload = self.controller.export_user_records(
            selected, user_id=normalized_user, receipt=receipt, now=self._now()
        )
        self._persist()
        return {"ok": True, "receipt_id": receipt["receipt_id"], "export": payload}

    def forget_all(
        self,
        *,
        user_id: str,
        item_ids: list[str],
        confirmation_text: str,
    ) -> dict[str, Any]:
        normalized_user = normalize_memory_user_id(user_id)
        records = self._control_records(self.backend.management_snapshot(limit=500), normalized_user)
        requested = sorted(set(item_ids))
        selected = [record for record in records if record["item_id"] in requested]
        if sorted(record["item_id"] for record in selected) != requested:
            raise MemoryConsentError("MEMORY_CONTROL_ITEM_NOT_FOUND", "delete")
        receipt = self.controller.issue_control_receipt(
            user_id=normalized_user,
            operation="DELETE",
            item_ids=requested,
            confirmation_text=confirmation_text,
            now=self._now(),
        )
        self.controller.consume_control_receipt(
            receipt,
            user_id=normalized_user,
            operation="DELETE",
            item_ids=requested,
            now=self._now(),
        )
        self._persist()
        deleted = 0
        for record in selected:
            if self.backend.forget(record["kind"], record["item_id"]):
                deleted += 1
        remaining = {
            record["item_id"]
            for record in self._control_records(
                self.backend.management_snapshot(limit=500), normalized_user
            )
        }
        reappeared = sorted(set(requested) & remaining)
        if deleted != len(selected) or reappeared:
            raise MemoryConsentError("MEMORY_CONTROL_DELETE_PROPAGATION_FAILED", ",".join(reappeared))
        return {
            "ok": True,
            "forgotten": True,
            "deleted_count": deleted,
            "receipt_id": receipt["receipt_id"],
            "receipt_sha256": receipt["receipt_sha256"],
            "reappeared_item_ids": [],
        }

    def status(self, user_id: str) -> dict[str, Any]:
        normalized_user = normalize_memory_user_id(user_id)
        return {
            "schema_version": SERVICE_SCHEMA,
            "user_id": normalized_user,
            "consent": self.consent_state(normalized_user),
            "persistent_ledger": self.ledger_path is not None,
        }


__all__ = [
    "LEDGER_SCHEMA",
    "MemoryControlService",
    "MemoryManagementBackend",
    "SERVICE_SCHEMA",
    "normalize_memory_user_id",
]
