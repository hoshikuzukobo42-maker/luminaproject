"""Fail-closed action risk policy for P4-03.

The module only classifies and authorizes action requests; it never executes an
action. Elevated effects require a short-lived receipt bound to the user,
correlation ID, action ID, effect class, and target scope. Unknown or dangerous
requests are blocked rather than guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


ACTION_REQUEST_SCHEMA = "lumina.action.request.v1"
APPROVAL_RECEIPT_SCHEMA = "lumina.action.approval.v1"
POLICY_DECISION_SCHEMA = "lumina.action.policy.decision.v1"
POLICY_VERSION = "lumina.risk.policy.v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

EFFECT_POLICIES: dict[str, dict[str, Any]] = {
    "read_public": {"risk": "LOW", "approval": False, "rollback": False},
    "write_local_reversible": {"risk": "MEDIUM", "approval": False, "rollback": True},
    "external_communication": {"risk": "HIGH", "approval": True, "rollback": False},
    "delete_data": {"risk": "CRITICAL", "approval": True, "rollback": True},
    "financial_transaction": {"risk": "CRITICAL", "approval": True, "rollback": False},
    "privilege_change": {"risk": "CRITICAL", "approval": True, "rollback": True},
    "sensor_capture": {"risk": "HIGH", "approval": True, "rollback": False},
}

HARD_PROHIBITED_ACTION_TYPES = {
    "bypass_safety",
    "credential_exfiltration",
    "hidden_recording",
    "self_modify_policy",
    "disable_kill_switch",
}


class RiskPolicyError(ValueError):
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
        raise RiskPolicyError(
            "RISK_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise RiskPolicyError(
            "RISK_ID_INVALID",
            f"{field_name} must be a canonical identifier",
        )
    return value


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RiskPolicyError("RISK_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RiskPolicyError("RISK_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise RiskPolicyError("RISK_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | None, requested_at: datetime) -> datetime:
    if value is None:
        return requested_at
    if value.tzinfo is None:
        raise RiskPolicyError("RISK_NOW_TIMEZONE_REQUIRED", "now")
    return value.astimezone(timezone.utc)


def validate_action_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RiskPolicyError("RISK_REQUEST_OBJECT_REQUIRED", "request")
    allowed = {
        "schema_version",
        "request_id",
        "correlation_id",
        "user_id",
        "action_type",
        "effect_class",
        "target_scope",
        "requested_at",
        "payload",
        "rollback_ref",
        "approval_receipt",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RiskPolicyError("RISK_UNKNOWN_REQUEST_FIELDS", ",".join(unknown))
    if value.get("schema_version") != ACTION_REQUEST_SCHEMA:
        raise RiskPolicyError("RISK_REQUEST_SCHEMA_MISMATCH", "schema_version")
    normalized = {
        "schema_version": ACTION_REQUEST_SCHEMA,
        "request_id": _identifier(value.get("request_id"), "request_id"),
        "correlation_id": _identifier(value.get("correlation_id"), "correlation_id"),
        "user_id": _identifier(value.get("user_id"), "user_id"),
        "action_type": _identifier(value.get("action_type"), "action_type"),
        "effect_class": _identifier(value.get("effect_class"), "effect_class"),
        "target_scope": _identifier(value.get("target_scope"), "target_scope"),
        "requested_at": _timestamp(value.get("requested_at"), "requested_at")
        .isoformat()
        .replace("+00:00", "Z"),
        "payload": _copy(value.get("payload", {})),
        "rollback_ref": value.get("rollback_ref"),
        "approval_receipt": _copy(value.get("approval_receipt")),
    }
    if not isinstance(normalized["payload"], Mapping):
        raise RiskPolicyError("RISK_PAYLOAD_OBJECT_REQUIRED", "payload")
    if normalized["rollback_ref"] is not None:
        _identifier(normalized["rollback_ref"], "rollback_ref")
    if normalized["approval_receipt"] is not None and not isinstance(
        normalized["approval_receipt"], Mapping
    ):
        raise RiskPolicyError("RISK_RECEIPT_OBJECT_REQUIRED", "approval_receipt")
    _canonical_json(normalized)
    return normalized


def _decision(
    request: Mapping[str, Any],
    *,
    decision: str,
    risk: str,
    reason: str,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": POLICY_DECISION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "request_id": request["request_id"],
        "correlation_id": request["correlation_id"],
        "effect_class": request["effect_class"],
        "risk_level": risk,
        "decision": decision,
        "execution_permitted": decision == "ALLOW",
        "reason": reason,
        "approval_receipt_id": receipt_id,
        "request_sha256": _sha256(request),
    }
    result["decision_sha256"] = _sha256(result)
    return result


def _validate_receipt(
    request: Mapping[str, Any],
    current: datetime,
) -> tuple[bool, str, str | None]:
    receipt = request.get("approval_receipt")
    if receipt is None:
        return False, "approval_required", None
    if not isinstance(receipt, Mapping):
        return False, "approval_receipt_invalid", None
    receipt_id = receipt.get("receipt_id")
    try:
        _identifier(receipt_id, "receipt_id")
    except RiskPolicyError:
        return False, "approval_receipt_id_invalid", None
    if receipt.get("schema_version") != APPROVAL_RECEIPT_SCHEMA:
        return False, "approval_receipt_schema_mismatch", receipt_id
    if receipt.get("decision") != "APPROVE":
        return False, "approval_not_granted", receipt_id
    bindings = {
        "user_id": request["user_id"],
        "correlation_id": request["correlation_id"],
        "request_id": request["request_id"],
        "effect_class": request["effect_class"],
        "target_scope": request["target_scope"],
    }
    for field_name, expected in bindings.items():
        if receipt.get(field_name) != expected:
            return False, f"approval_{field_name}_mismatch", receipt_id
    try:
        issued = _timestamp(receipt.get("issued_at"), "issued_at")
        expires = _timestamp(receipt.get("expires_at"), "expires_at")
    except RiskPolicyError as exc:
        return False, exc.code.lower(), receipt_id
    if issued > current:
        return False, "approval_from_future", receipt_id
    if expires <= current or expires <= issued:
        return False, "approval_expired", receipt_id
    if receipt.get("one_time") is not True:
        return False, "approval_not_one_time", receipt_id

    payload = request["payload"]
    if request["effect_class"] == "financial_transaction":
        cost = payload.get("estimated_cost")
        max_cost = receipt.get("max_cost")
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(float(cost))
            or float(cost) < 0
            or isinstance(max_cost, bool)
            or not isinstance(max_cost, (int, float))
            or not math.isfinite(float(max_cost))
            or float(cost) > float(max_cost)
        ):
            return False, "approval_cost_limit_invalid", receipt_id
    if request["effect_class"] == "delete_data":
        confirmation = payload.get("confirmation_sha256")
        if not isinstance(confirmation, str) or receipt.get("confirmation_sha256") != confirmation:
            return False, "approval_delete_confirmation_mismatch", receipt_id
    return True, "approval_bound_and_current", receipt_id


def evaluate_action_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an authorization decision without executing the action."""

    request = validate_action_request(value)
    requested_at = _timestamp(request["requested_at"], "requested_at")
    current = _now(now, requested_at)
    action_type = request["action_type"]
    effect_class = request["effect_class"]

    if action_type in HARD_PROHIBITED_ACTION_TYPES:
        return _decision(
            request,
            decision="BLOCK",
            risk="PROHIBITED",
            reason="hard_prohibited_action_type",
        )
    policy = EFFECT_POLICIES.get(effect_class)
    if policy is None:
        return _decision(
            request,
            decision="BLOCK",
            risk="UNKNOWN",
            reason="unknown_effect_class",
        )
    if policy["rollback"] and not request.get("rollback_ref"):
        return _decision(
            request,
            decision="BLOCK",
            risk=policy["risk"],
            reason="rollback_required",
        )
    if not policy["approval"]:
        return _decision(
            request,
            decision="ALLOW",
            risk=policy["risk"],
            reason="policy_allows_local_effect",
        )

    approved, reason, receipt_id = _validate_receipt(request, current)
    return _decision(
        request,
        decision="ALLOW" if approved else ("REQUIRE_CONFIRMATION" if reason == "approval_required" else "BLOCK"),
        risk=policy["risk"],
        reason=reason,
        receipt_id=receipt_id,
    )


def evaluate_action_batch(
    requests: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for request in requests:
        normalized = validate_action_request(request)
        request_id = normalized["request_id"]
        if request_id in seen:
            raise RiskPolicyError("RISK_DUPLICATE_REQUEST_ID", request_id)
        seen.add(request_id)
        decisions.append(evaluate_action_request(normalized, now=now))
    return decisions

