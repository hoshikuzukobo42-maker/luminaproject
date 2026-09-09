"""User-visible permission modes and immediate revocation for P4-04.

This local in-memory reference service issues receipts compatible with
``risk_policy_v1``. It never executes actions and defaults to PROPOSE_ONLY.
LIMITED_AUTO is restricted to explicitly granted external communication or
sensor-capture scopes; destructive, financial, and privilege effects still
require per-action confirmation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from lumina_next.risk_policy_v1 import (
    ACTION_REQUEST_SCHEMA,
    APPROVAL_RECEIPT_SCHEMA,
    EFFECT_POLICIES,
    validate_action_request,
)


PERMISSION_MODE_SCHEMA = "lumina.permission.mode.v1"
PERMISSION_GRANT_SCHEMA = "lumina.permission.grant.v1"
PERMISSION_EVENT_SCHEMA = "lumina.permission.event.v1"
MODES = ("PROPOSE_ONLY", "CONFIRM_EACH", "LIMITED_AUTO")
LIMITED_AUTO_EFFECTS = {"external_communication", "sensor_capture"}
ALWAYS_CONFIRM_EFFECTS = {"delete_data", "financial_transaction", "privilege_change"}
MAX_RECEIPT_TTL_SECONDS = 60
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class PermissionModeError(ValueError):
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
        raise PermissionModeError("PERMISSION_NONCANONICAL_JSON", "payload") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise PermissionModeError("PERMISSION_ID_INVALID", field_name)
    return value


def _now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PermissionModeError("PERMISSION_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PermissionModeRegistry:
    """Deterministic user-scoped permission registry with append-only audit."""

    def __init__(self) -> None:
        self._modes: dict[str, str] = {}
        self._grants: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._sequence = 0

    def _event(
        self,
        *,
        user_id: str,
        event_type: str,
        now: datetime,
        detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._sequence += 1
        event = {
            "schema_version": PERMISSION_EVENT_SCHEMA,
            "event_id": f"permission-event:{self._sequence}",
            "sequence": self._sequence,
            "user_id": user_id,
            "event_type": event_type,
            "occurred_at": _iso(now),
            "detail": _copy(detail),
        }
        event["event_sha256"] = _sha256(event)
        self._events.append(event)
        return _copy(event)

    def get_mode(self, user_id: str) -> str:
        return self._modes.get(_id(user_id, "user_id"), "PROPOSE_ONLY")

    def set_mode(
        self,
        *,
        user_id: str,
        mode: str,
        now: datetime,
        reason: str,
    ) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        current = _now(now)
        if mode not in MODES:
            raise PermissionModeError("PERMISSION_MODE_UNKNOWN", str(mode))
        if not isinstance(reason, str) or not reason.strip():
            raise PermissionModeError("PERMISSION_REASON_REQUIRED", "reason")
        previous = self.get_mode(user)
        self._modes[user] = mode
        self._event(
            user_id=user,
            event_type="MODE_CHANGED",
            now=current,
            detail={"previous": previous, "current": mode, "reason": reason.strip()},
        )
        return self.describe(user, now=current)

    def grant(
        self,
        *,
        grant_id: str,
        user_id: str,
        effect_class: str,
        target_scope: str,
        now: datetime,
        ttl_seconds: int,
        max_cost: float | None = None,
    ) -> dict[str, Any]:
        grant = _id(grant_id, "grant_id")
        user = _id(user_id, "user_id")
        target = _id(target_scope, "target_scope")
        current = _now(now)
        if grant in self._grants:
            raise PermissionModeError("PERMISSION_GRANT_DUPLICATE", grant)
        if effect_class not in LIMITED_AUTO_EFFECTS:
            raise PermissionModeError("PERMISSION_EFFECT_NOT_LIMITED_AUTO", effect_class)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 86400:
            raise PermissionModeError("PERMISSION_GRANT_TTL_INVALID", str(ttl_seconds))
        record = {
            "schema_version": PERMISSION_GRANT_SCHEMA,
            "grant_id": grant,
            "user_id": user,
            "effect_class": effect_class,
            "target_scope": target,
            "created_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=ttl_seconds)),
            "max_cost": max_cost,
            "revoked_at": None,
            "revocation_requested_at": None,
        }
        _canonical_json(record)
        self._grants[grant] = record
        self._event(user_id=user, event_type="GRANT_CREATED", now=current, detail={"grant_id": grant, "effect_class": effect_class, "target_scope": target})
        return _copy(record)

    def revoke(
        self,
        *,
        grant_id: str,
        user_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        grant = _id(grant_id, "grant_id")
        user = _id(user_id, "user_id")
        current = _now(now)
        record = self._grants.get(grant)
        if record is None or record["user_id"] != user:
            raise PermissionModeError("PERMISSION_GRANT_NOT_FOUND", grant)
        if record["revoked_at"] is not None:
            raise PermissionModeError("PERMISSION_GRANT_ALREADY_REVOKED", grant)
        requested = current
        record["revocation_requested_at"] = _iso(requested)
        record["revoked_at"] = _iso(current)
        latency = (current - requested).total_seconds()
        self._event(user_id=user, event_type="GRANT_REVOKED", now=current, detail={"grant_id": grant, "revocation_latency_seconds": latency})
        return {"grant_id": grant, "revoked_at": record["revoked_at"], "revocation_latency_seconds": latency, "effective": True}

    def _active_grant(
        self,
        *,
        user_id: str,
        effect_class: str,
        target_scope: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        matches = []
        for grant in self._grants.values():
            if (
                grant["user_id"] == user_id
                and grant["effect_class"] == effect_class
                and grant["target_scope"] == target_scope
                and grant["revoked_at"] is None
                and datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00")) > now
            ):
                matches.append(grant)
        if not matches:
            return None
        return _copy(sorted(matches, key=lambda item: item["grant_id"])[0])

    def issue_receipt(
        self,
        request: Mapping[str, Any],
        *,
        now: datetime,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        action = validate_action_request(request)
        current = _now(now)
        user = action["user_id"]
        mode = self.get_mode(user)
        effect = action["effect_class"]
        if effect not in EFFECT_POLICIES or not EFFECT_POLICIES[effect]["approval"]:
            return {"status": "NOT_REQUIRED", "reason": "effect_does_not_require_receipt", "receipt": None}
        if mode == "PROPOSE_ONLY":
            return {"status": "BLOCKED", "reason": "mode_propose_only", "receipt": None}

        grant = self._active_grant(user_id=user, effect_class=effect, target_scope=action["target_scope"], now=current)
        allowed = False
        reason = "confirmation_required"
        if confirmed:
            allowed = True
            reason = "per_action_confirmation"
        elif mode == "LIMITED_AUTO" and effect in LIMITED_AUTO_EFFECTS and grant is not None:
            allowed = True
            reason = "active_limited_auto_grant"
        if effect in ALWAYS_CONFIRM_EFFECTS and not confirmed:
            allowed = False
            reason = "destructive_financial_or_privilege_requires_confirmation"
        if not allowed:
            return {"status": "BLOCKED", "reason": reason, "receipt": None}

        receipt_id = f"receipt:{action['request_id']}:{self._sequence + 1}"
        payload = action["payload"]
        receipt = {
            "schema_version": APPROVAL_RECEIPT_SCHEMA,
            "receipt_id": receipt_id,
            "decision": "APPROVE",
            "user_id": user,
            "correlation_id": action["correlation_id"],
            "request_id": action["request_id"],
            "effect_class": effect,
            "target_scope": action["target_scope"],
            "issued_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=MAX_RECEIPT_TTL_SECONDS)),
            "one_time": True,
            "max_cost": payload.get("estimated_cost"),
            "confirmation_sha256": payload.get("confirmation_sha256"),
            "grant_id": grant["grant_id"] if grant else None,
        }
        self._event(user_id=user, event_type="RECEIPT_ISSUED", now=current, detail={"receipt_id": receipt_id, "request_id": action["request_id"], "reason": reason, "grant_id": receipt["grant_id"]})
        return {"status": "ISSUED", "reason": reason, "receipt": receipt}

    def describe(self, user_id: str, *, now: datetime) -> dict[str, Any]:
        user = _id(user_id, "user_id")
        current = _now(now)
        active = [
            _copy(grant)
            for grant in self._grants.values()
            if grant["user_id"] == user
            and grant["revoked_at"] is None
            and datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00")) > current
        ]
        active.sort(key=lambda item: item["grant_id"])
        return {
            "schema_version": PERMISSION_MODE_SCHEMA,
            "user_id": user,
            "mode": self.get_mode(user),
            "active_grants": active,
            "can_revoke": [item["grant_id"] for item in active],
            "default_behavior": "no receipt; no elevated execution",
        }

    def audit_events(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id is None:
            return _copy(self._events)
        user = _id(user_id, "user_id")
        return _copy([event for event in self._events if event["user_id"] == user])

