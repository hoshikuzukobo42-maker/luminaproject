"""Deterministic, scoped memory correction and forgetting tools for P3-05.

This module operates on an in-memory sealed state bundle.  It never opens the
product SQLite database.  Forgetting requires a hash-sealed consent receipt
bound to the exact request, tenant, user, lineage, scope, and validity window.
All six active projections are rewritten atomically in the returned value.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .memory_retrieval_v1 import MemoryRecord, MemoryRetrievalService
from .relationship_model_v1 import (
    CONFIRMATION_PRECEDENCE,
    MEMORY_ARCHITECTURE_CONTRACT_PATH,
    RelationshipEvent,
    RelationshipModelV1,
)


STATE_SCHEMA = "lumina.memory.repair.state.v1"
REQUEST_SCHEMA = "lumina.memory.repair.request.v1"
CONSENT_SCHEMA = "lumina.memory.repair.consent.v1"
RESULT_SCHEMA = "lumina.memory.repair.result.v1"
AUDIT_SCHEMA = "lumina.memory.repair.audit.v1"
TOMBSTONE_SCHEMA = "lumina.memory.repair.tombstone.v1"
POLICY_VERSION = "lumina.memory.repair.policy.v1"

PROJECTIONS = (
    "working",
    "episodic",
    "semantic",
    "profile",
    "relationship",
    "retrieval",
)
OPERATIONS = ("correct", "forget")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")


class MemoryRepairError(ValueError):
    """Fail-closed contract error with a stable machine-readable code."""

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
        raise MemoryRepairError(
            "MEMORY_REPAIR_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryRepairError(code, detail)
    return value


def _exact_fields(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    prefix: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise MemoryRepairError(
            f"MEMORY_REPAIR_{prefix}_UNKNOWN_FIELDS",
            ",".join(unknown),
        )
    missing = sorted(required - set(value))
    if missing:
        raise MemoryRepairError(
            f"MEMORY_REPAIR_{prefix}_FIELDS_REQUIRED",
            ",".join(missing),
        )


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise MemoryRepairError(
            "MEMORY_REPAIR_ID_INVALID",
            f"{field_name} must be a canonical identifier",
        )
    return value


def _timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TIMESTAMP_REQUIRED",
            field_name,
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TIMESTAMP_INVALID",
            field_name,
        ) from exc
    if parsed.tzinfo is None:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryRepairError(
            "MEMORY_REPAIR_REVISION_INVALID",
            f"{field_name} must be a positive integer",
        )
    return value


def _load_memory_architecture(
    path: Path = MEMORY_ARCHITECTURE_CONTRACT_PATH,
) -> dict[str, Any]:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryRepairError(
            "MEMORY_REPAIR_ARCHITECTURE_UNAVAILABLE",
            "memory architecture contract unavailable",
        ) from exc
    required = {
        "tenant_and_user_scope_required",
        "provenance_required",
        "forget_propagates_to_active_indexes_and_caches",
        "audit_records_do_not_retain_forgotten_plaintext",
    }
    if (
        contract.get("contract_id") != "lumina.memory.architecture.v1"
        or contract.get("local_only") is not True
        or not required.issubset(set(contract.get("common_invariants") or []))
    ):
        raise MemoryRepairError(
            "MEMORY_REPAIR_ARCHITECTURE_INCOMPATIBLE",
            "memory architecture invariants are missing",
        )
    return contract


def _normalize_provenance(value: Any, field_name: str) -> dict[str, Any]:
    provenance = _mapping(
        value,
        "MEMORY_REPAIR_PROVENANCE_REQUIRED",
        f"{field_name} must be an object",
    )
    fields = {"source_id", "correlation_id", "observed_at", "confirmation_state"}
    _exact_fields(provenance, allowed=fields, required=fields, prefix="PROVENANCE")
    confirmation = provenance["confirmation_state"]
    if confirmation not in CONFIRMATION_PRECEDENCE:
        raise MemoryRepairError(
            "MEMORY_REPAIR_CONFIRMATION_INVALID",
            f"{field_name}.confirmation_state is unsupported",
        )
    return {
        "source_id": _identifier(provenance["source_id"], f"{field_name}.source_id"),
        "correlation_id": _identifier(
            provenance["correlation_id"],
            f"{field_name}.correlation_id",
        ),
        "observed_at": _timestamp(
            provenance["observed_at"],
            f"{field_name}.observed_at",
        ),
        "confirmation_state": confirmation,
    }


def _normalize_record(value: Any, projection: str) -> dict[str, Any]:
    record = _mapping(
        value,
        "MEMORY_REPAIR_RECORD_OBJECT_REQUIRED",
        "projection record must be an object",
    )
    fields = {
        "record_id",
        "lineage_id",
        "tenant_id",
        "user_id",
        "projection",
        "key",
        "value",
        "status",
        "updated_at",
        "provenance",
    }
    _exact_fields(record, allowed=fields, required=fields, prefix="RECORD")
    if record["projection"] != projection:
        raise MemoryRepairError(
            "MEMORY_REPAIR_PROJECTION_MISMATCH",
            f"record projection must be {projection}",
        )
    key = record["key"]
    if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
        raise MemoryRepairError("MEMORY_REPAIR_KEY_INVALID", "record.key is invalid")
    if record["status"] != "active":
        raise MemoryRepairError(
            "MEMORY_REPAIR_RECORD_STATUS_INVALID",
            "active projections may contain only active records",
        )
    _canonical_json(record["value"])
    return {
        "record_id": _identifier(record["record_id"], "record.record_id"),
        "lineage_id": _identifier(record["lineage_id"], "record.lineage_id"),
        "tenant_id": _identifier(record["tenant_id"], "record.tenant_id"),
        "user_id": _identifier(record["user_id"], "record.user_id"),
        "projection": projection,
        "key": key,
        "value": _copy(record["value"]),
        "status": "active",
        "updated_at": _timestamp(record["updated_at"], "record.updated_at"),
        "provenance": _normalize_provenance(record["provenance"], "record.provenance"),
    }


def _normalize_tombstone(value: Any) -> dict[str, Any]:
    tombstone = _mapping(
        value,
        "MEMORY_REPAIR_TOMBSTONE_OBJECT_REQUIRED",
        "tombstone must be an object",
    )
    fields = {
        "schema_version",
        "tombstone_id",
        "request_id",
        "tenant_id",
        "user_id",
        "lineage_id",
        "created_at",
        "reason",
        "consent_receipt_id",
        "forgotten_record_count",
        "forgotten_record_ids_sha256",
        "provenance",
        "plaintext_retained",
        "tombstone_sha256",
    }
    _exact_fields(tombstone, allowed=fields, required=fields, prefix="TOMBSTONE")
    if tombstone["schema_version"] != TOMBSTONE_SCHEMA:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONE_SCHEMA_MISMATCH",
            "unsupported tombstone schema",
        )
    if tombstone["reason"] != "explicit_user_forget":
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONE_REASON_INVALID",
            "unsupported tombstone reason",
        )
    count = tombstone["forgotten_record_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONE_COUNT_INVALID",
            "forgotten_record_count must be positive",
        )
    if tombstone["plaintext_retained"] is not False:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONE_PLAINTEXT_FORBIDDEN",
            "tombstone may not retain plaintext",
        )
    expected = _sha256({key: _copy(item) for key, item in tombstone.items() if key != "tombstone_sha256"})
    if tombstone["tombstone_sha256"] != expected:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONE_TAMPERED",
            "tombstone hash mismatch",
        )
    return {
        "schema_version": TOMBSTONE_SCHEMA,
        "tombstone_id": _identifier(tombstone["tombstone_id"], "tombstone_id"),
        "request_id": _identifier(tombstone["request_id"], "tombstone.request_id"),
        "tenant_id": _identifier(tombstone["tenant_id"], "tombstone.tenant_id"),
        "user_id": _identifier(tombstone["user_id"], "tombstone.user_id"),
        "lineage_id": _identifier(tombstone["lineage_id"], "tombstone.lineage_id"),
        "created_at": _timestamp(tombstone["created_at"], "tombstone.created_at"),
        "reason": "explicit_user_forget",
        "consent_receipt_id": _identifier(
            tombstone["consent_receipt_id"],
            "tombstone.consent_receipt_id",
        ),
        "forgotten_record_count": count,
        "forgotten_record_ids_sha256": str(tombstone["forgotten_record_ids_sha256"]),
        "provenance": _normalize_provenance(tombstone["provenance"], "tombstone.provenance"),
        "plaintext_retained": False,
        "tombstone_sha256": expected,
    }


def _normalize_audit(value: Any) -> dict[str, Any]:
    audit = _mapping(
        value,
        "MEMORY_REPAIR_AUDIT_OBJECT_REQUIRED",
        "audit event must be an object",
    )
    fields = {
        "schema_version",
        "event_id",
        "request_id",
        "operation",
        "tenant_id",
        "user_id",
        "lineage_id",
        "occurred_at",
        "provenance",
        "before_state_sha256",
        "after_projection_sha256",
        "affected_projections",
        "removed_record_count",
        "replacement_record_count",
        "superseded_record_ids",
        "replacement_record_ids",
        "tombstone_id",
        "consent_receipt_id",
        "plaintext_retained",
        "audit_sha256",
    }
    _exact_fields(audit, allowed=fields, required=fields, prefix="AUDIT")
    if audit["schema_version"] != AUDIT_SCHEMA:
        raise MemoryRepairError(
            "MEMORY_REPAIR_AUDIT_SCHEMA_MISMATCH",
            "unsupported audit schema",
        )
    if audit["operation"] not in OPERATIONS:
        raise MemoryRepairError("MEMORY_REPAIR_OPERATION_INVALID", "audit operation")
    affected = audit["affected_projections"]
    if (
        not isinstance(affected, list)
        or any(item not in PROJECTIONS for item in affected)
        or affected != sorted(set(affected))
    ):
        raise MemoryRepairError(
            "MEMORY_REPAIR_AUDIT_PROJECTIONS_INVALID",
            "affected_projections must be sorted and unique",
        )
    for field_name in ("superseded_record_ids", "replacement_record_ids"):
        values = audit[field_name]
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or _ID_PATTERN.fullmatch(item) is None for item in values)
            or values != sorted(set(values))
        ):
            raise MemoryRepairError(
                "MEMORY_REPAIR_AUDIT_RECORD_IDS_INVALID",
                field_name,
            )
    for field_name in ("removed_record_count", "replacement_record_count"):
        count = audit[field_name]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MemoryRepairError("MEMORY_REPAIR_AUDIT_COUNT_INVALID", field_name)
    if audit["plaintext_retained"] is not False:
        raise MemoryRepairError(
            "MEMORY_REPAIR_AUDIT_PLAINTEXT_FORBIDDEN",
            "audit may not retain plaintext",
        )
    expected = _sha256({key: _copy(item) for key, item in audit.items() if key != "audit_sha256"})
    if audit["audit_sha256"] != expected:
        raise MemoryRepairError(
            "MEMORY_REPAIR_AUDIT_TAMPERED",
            "audit hash mismatch",
        )
    normalized = _copy(dict(audit))
    normalized["event_id"] = _identifier(audit["event_id"], "audit.event_id")
    normalized["request_id"] = _identifier(audit["request_id"], "audit.request_id")
    normalized["tenant_id"] = _identifier(audit["tenant_id"], "audit.tenant_id")
    normalized["user_id"] = _identifier(audit["user_id"], "audit.user_id")
    normalized["lineage_id"] = _identifier(audit["lineage_id"], "audit.lineage_id")
    normalized["occurred_at"] = _timestamp(audit["occurred_at"], "audit.occurred_at")
    normalized["provenance"] = _normalize_provenance(audit["provenance"], "audit.provenance")
    normalized["audit_sha256"] = expected
    return normalized


def create_memory_state(
    *,
    state_id: str,
    tenant_id: str,
    user_id: str,
    revision: int,
    as_of: str,
    projections: Mapping[str, Iterable[Mapping[str, Any]]],
    tombstones: Iterable[Mapping[str, Any]] = (),
    audit_log: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create a canonical sealed active-state bundle without persistence."""

    projection_map = _mapping(
        projections,
        "MEMORY_REPAIR_PROJECTIONS_REQUIRED",
        "projections must be an object",
    )
    if set(projection_map) != set(PROJECTIONS):
        raise MemoryRepairError(
            "MEMORY_REPAIR_PROJECTION_SET_INVALID",
            "all six projections are required exactly once",
        )
    normalized_projections: dict[str, list[dict[str, Any]]] = {}
    for projection in PROJECTIONS:
        raw_records = projection_map[projection]
        if isinstance(raw_records, (str, bytes, Mapping)) or not isinstance(raw_records, Iterable):
            raise MemoryRepairError(
                "MEMORY_REPAIR_PROJECTION_RECORDS_INVALID",
                projection,
            )
        normalized_projections[projection] = sorted(
            (_normalize_record(item, projection) for item in raw_records),
            key=lambda item: item["record_id"],
        )
    normalized_tombstones = sorted(
        (_normalize_tombstone(item) for item in tombstones),
        key=lambda item: item["tombstone_id"],
    )
    normalized_audit = sorted(
        (_normalize_audit(item) for item in audit_log),
        key=lambda item: item["event_id"],
    )
    state = {
        "schema_version": STATE_SCHEMA,
        "policy_version": POLICY_VERSION,
        "state_id": _identifier(state_id, "state_id"),
        "tenant_id": _identifier(tenant_id, "tenant_id"),
        "user_id": _identifier(user_id, "user_id"),
        "revision": _positive_int(revision, "revision"),
        "as_of": _timestamp(as_of, "as_of"),
        "projections": normalized_projections,
        "tombstones": normalized_tombstones,
        "audit_log": normalized_audit,
    }
    state["state_sha256"] = _sha256(state)
    return validate_memory_state(state)


def validate_memory_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate scope, integrity, uniqueness, and tombstone non-reappearance."""

    source = _mapping(
        value,
        "MEMORY_REPAIR_STATE_OBJECT_REQUIRED",
        "state must be an object",
    )
    fields = {
        "schema_version",
        "policy_version",
        "state_id",
        "tenant_id",
        "user_id",
        "revision",
        "as_of",
        "projections",
        "tombstones",
        "audit_log",
        "state_sha256",
    }
    _exact_fields(source, allowed=fields, required=fields, prefix="STATE")
    if source["schema_version"] != STATE_SCHEMA:
        raise MemoryRepairError("MEMORY_REPAIR_STATE_SCHEMA_MISMATCH", "unsupported state schema")
    if source["policy_version"] != POLICY_VERSION:
        raise MemoryRepairError("MEMORY_REPAIR_POLICY_MISMATCH", "unsupported policy version")
    expected_hash = _sha256({key: _copy(item) for key, item in source.items() if key != "state_sha256"})
    if source["state_sha256"] != expected_hash:
        raise MemoryRepairError("MEMORY_REPAIR_STATE_TAMPERED", "state hash mismatch")

    tenant_id = _identifier(source["tenant_id"], "state.tenant_id")
    user_id = _identifier(source["user_id"], "state.user_id")
    projection_map = _mapping(
        source["projections"],
        "MEMORY_REPAIR_PROJECTIONS_REQUIRED",
        "projections must be an object",
    )
    if set(projection_map) != set(PROJECTIONS):
        raise MemoryRepairError(
            "MEMORY_REPAIR_PROJECTION_SET_INVALID",
            "all six projections are required exactly once",
        )
    normalized_projections: dict[str, list[dict[str, Any]]] = {}
    record_ids: list[str] = []
    for projection in PROJECTIONS:
        records = projection_map[projection]
        if not isinstance(records, list):
            raise MemoryRepairError("MEMORY_REPAIR_PROJECTION_RECORDS_INVALID", projection)
        normalized = [_normalize_record(item, projection) for item in records]
        if normalized != sorted(normalized, key=lambda item: item["record_id"]):
            raise MemoryRepairError("MEMORY_REPAIR_STATE_ORDER_INVALID", projection)
        if any(
            (record["tenant_id"], record["user_id"]) != (tenant_id, user_id)
            for record in normalized
        ):
            raise MemoryRepairError(
                "MEMORY_REPAIR_CROSS_USER_CONTEXT_REJECTED",
                "every record must match the state tenant and user",
            )
        lineages = [record["lineage_id"] for record in normalized]
        if len(lineages) != len(set(lineages)):
            raise MemoryRepairError(
                "MEMORY_REPAIR_DUPLICATE_LINEAGE",
                f"duplicate lineage in {projection}",
            )
        normalized_projections[projection] = normalized
        record_ids.extend(record["record_id"] for record in normalized)
    if len(record_ids) != len(set(record_ids)):
        raise MemoryRepairError(
            "MEMORY_REPAIR_DUPLICATE_RECORD_ID",
            "record_id must be globally unique",
        )

    tombstones_raw = source["tombstones"]
    audit_raw = source["audit_log"]
    if not isinstance(tombstones_raw, list) or not isinstance(audit_raw, list):
        raise MemoryRepairError(
            "MEMORY_REPAIR_HISTORY_ARRAY_REQUIRED",
            "tombstones and audit_log must be arrays",
        )
    tombstones = [_normalize_tombstone(item) for item in tombstones_raw]
    audits = [_normalize_audit(item) for item in audit_raw]
    if tombstones != sorted(tombstones, key=lambda item: item["tombstone_id"]):
        raise MemoryRepairError("MEMORY_REPAIR_STATE_ORDER_INVALID", "tombstones")
    if audits != sorted(audits, key=lambda item: item["event_id"]):
        raise MemoryRepairError("MEMORY_REPAIR_STATE_ORDER_INVALID", "audit_log")
    for item in [*tombstones, *audits]:
        if (item["tenant_id"], item["user_id"]) != (tenant_id, user_id):
            raise MemoryRepairError(
                "MEMORY_REPAIR_CROSS_USER_CONTEXT_REJECTED",
                "history scope must match state scope",
            )
    tombstone_ids = [item["tombstone_id"] for item in tombstones]
    tombstone_lineages = [item["lineage_id"] for item in tombstones]
    audit_ids = [item["event_id"] for item in audits]
    request_ids = [item["request_id"] for item in audits]
    if len(tombstone_ids) != len(set(tombstone_ids)) or len(tombstone_lineages) != len(set(tombstone_lineages)):
        raise MemoryRepairError("MEMORY_REPAIR_DUPLICATE_TOMBSTONE", "duplicate tombstone")
    if len(audit_ids) != len(set(audit_ids)) or len(request_ids) != len(set(request_ids)):
        raise MemoryRepairError("MEMORY_REPAIR_DUPLICATE_REQUEST", "duplicate audit request")
    active_lineages = {
        record["lineage_id"]
        for records in normalized_projections.values()
        for record in records
    }
    reappeared = sorted(active_lineages.intersection(tombstone_lineages))
    if reappeared:
        raise MemoryRepairError(
            "MEMORY_REPAIR_TOMBSTONED_LINEAGE_REAPPEARED",
            ",".join(reappeared),
        )
    return {
        "schema_version": STATE_SCHEMA,
        "policy_version": POLICY_VERSION,
        "state_id": _identifier(source["state_id"], "state.state_id"),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "revision": _positive_int(source["revision"], "state.revision"),
        "as_of": _timestamp(source["as_of"], "state.as_of"),
        "projections": _copy(normalized_projections),
        "tombstones": _copy(tombstones),
        "audit_log": _copy(audits),
        "state_sha256": expected_hash,
    }


def create_forget_consent_receipt(
    *,
    receipt_id: str,
    request_id: str,
    tenant_id: str,
    user_id: str,
    lineage_id: str,
    issued_at: str,
    expires_at: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an explicit ALLOW receipt bound to one forget request."""

    normalized_provenance = _normalize_provenance(provenance, "receipt.provenance")
    if normalized_provenance["confirmation_state"] != "explicit_user_confirmation":
        raise MemoryRepairError(
            "MEMORY_REPAIR_CONSENT_NOT_EXPLICIT",
            "forget consent must be explicit_user_confirmation",
        )
    receipt = {
        "schema_version": CONSENT_SCHEMA,
        "receipt_id": _identifier(receipt_id, "receipt_id"),
        "request_id": _identifier(request_id, "receipt.request_id"),
        "tenant_id": _identifier(tenant_id, "receipt.tenant_id"),
        "user_id": _identifier(user_id, "receipt.user_id"),
        "lineage_id": _identifier(lineage_id, "receipt.lineage_id"),
        "decision": "ALLOW",
        "scope": "forget_lineage",
        "issued_at": _timestamp(issued_at, "receipt.issued_at"),
        "expires_at": _timestamp(expires_at, "receipt.expires_at"),
        "provenance": normalized_provenance,
    }
    if _datetime(receipt["expires_at"]) <= _datetime(receipt["issued_at"]):
        raise MemoryRepairError(
            "MEMORY_REPAIR_CONSENT_WINDOW_INVALID",
            "consent expires_at must be after issued_at",
        )
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def _normalize_consent(value: Any) -> dict[str, Any]:
    receipt = _mapping(
        value,
        "MEMORY_REPAIR_CONSENT_REQUIRED",
        "forget requires a bound consent receipt",
    )
    fields = {
        "schema_version",
        "receipt_id",
        "request_id",
        "tenant_id",
        "user_id",
        "lineage_id",
        "decision",
        "scope",
        "issued_at",
        "expires_at",
        "provenance",
        "receipt_sha256",
    }
    _exact_fields(receipt, allowed=fields, required=fields, prefix="CONSENT")
    if receipt["schema_version"] != CONSENT_SCHEMA:
        raise MemoryRepairError("MEMORY_REPAIR_CONSENT_SCHEMA_MISMATCH", "unsupported consent schema")
    expected_hash = _sha256({key: _copy(item) for key, item in receipt.items() if key != "receipt_sha256"})
    if receipt["receipt_sha256"] != expected_hash:
        raise MemoryRepairError("MEMORY_REPAIR_CONSENT_TAMPERED", "consent receipt hash mismatch")
    if receipt["decision"] != "ALLOW" or receipt["scope"] != "forget_lineage":
        raise MemoryRepairError(
            "MEMORY_REPAIR_CONSENT_DENIED",
            "consent must ALLOW exact forget_lineage scope",
        )
    provenance = _normalize_provenance(receipt["provenance"], "receipt.provenance")
    if provenance["confirmation_state"] != "explicit_user_confirmation":
        raise MemoryRepairError(
            "MEMORY_REPAIR_CONSENT_NOT_EXPLICIT",
            "forget consent must be explicit_user_confirmation",
        )
    issued_at = _timestamp(receipt["issued_at"], "receipt.issued_at")
    expires_at = _timestamp(receipt["expires_at"], "receipt.expires_at")
    if _datetime(expires_at) <= _datetime(issued_at):
        raise MemoryRepairError("MEMORY_REPAIR_CONSENT_WINDOW_INVALID", "consent validity window")
    return {
        "schema_version": CONSENT_SCHEMA,
        "receipt_id": _identifier(receipt["receipt_id"], "receipt.receipt_id"),
        "request_id": _identifier(receipt["request_id"], "receipt.request_id"),
        "tenant_id": _identifier(receipt["tenant_id"], "receipt.tenant_id"),
        "user_id": _identifier(receipt["user_id"], "receipt.user_id"),
        "lineage_id": _identifier(receipt["lineage_id"], "receipt.lineage_id"),
        "decision": "ALLOW",
        "scope": "forget_lineage",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "provenance": provenance,
        "receipt_sha256": expected_hash,
    }


def _seal_request(payload: dict[str, Any]) -> dict[str, Any]:
    payload["request_sha256"] = _sha256(payload)
    return payload


def create_correction_request(
    state: Mapping[str, Any],
    *,
    request_id: str,
    lineage_id: str,
    issued_at: str,
    new_value: Any,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a revision-bound explicit correction request."""

    current = validate_memory_state(state)
    normalized_provenance = _normalize_provenance(provenance, "request.provenance")
    if normalized_provenance["confirmation_state"] != "explicit_user_correction":
        raise MemoryRepairError(
            "MEMORY_REPAIR_CORRECTION_NOT_EXPLICIT",
            "correction must be explicit_user_correction",
        )
    _canonical_json(new_value)
    return _seal_request({
        "schema_version": REQUEST_SCHEMA,
        "request_id": _identifier(request_id, "request_id"),
        "operation": "correct",
        "tenant_id": current["tenant_id"],
        "user_id": current["user_id"],
        "lineage_id": _identifier(lineage_id, "request.lineage_id"),
        "issued_at": _timestamp(issued_at, "request.issued_at"),
        "expected_state_revision": current["revision"],
        "based_on_state_sha256": current["state_sha256"],
        "provenance": normalized_provenance,
        "new_value": _copy(new_value),
        "consent_receipt": None,
    })


def create_forget_request(
    state: Mapping[str, Any],
    *,
    request_id: str,
    lineage_id: str,
    issued_at: str,
    provenance: Mapping[str, Any],
    consent_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a revision-bound forget request carrying the bound receipt."""

    current = validate_memory_state(state)
    normalized_provenance = _normalize_provenance(provenance, "request.provenance")
    if normalized_provenance["confirmation_state"] != "explicit_user_confirmation":
        raise MemoryRepairError(
            "MEMORY_REPAIR_FORGET_NOT_EXPLICIT",
            "forget request must be explicit_user_confirmation",
        )
    receipt = _normalize_consent(consent_receipt)
    return _seal_request({
        "schema_version": REQUEST_SCHEMA,
        "request_id": _identifier(request_id, "request_id"),
        "operation": "forget",
        "tenant_id": current["tenant_id"],
        "user_id": current["user_id"],
        "lineage_id": _identifier(lineage_id, "request.lineage_id"),
        "issued_at": _timestamp(issued_at, "request.issued_at"),
        "expected_state_revision": current["revision"],
        "based_on_state_sha256": current["state_sha256"],
        "provenance": normalized_provenance,
        "new_value": None,
        "consent_receipt": receipt,
    })


def _normalize_request(value: Any) -> dict[str, Any]:
    request = _mapping(
        value,
        "MEMORY_REPAIR_REQUEST_OBJECT_REQUIRED",
        "request must be an object",
    )
    fields = {
        "schema_version",
        "request_id",
        "operation",
        "tenant_id",
        "user_id",
        "lineage_id",
        "issued_at",
        "expected_state_revision",
        "based_on_state_sha256",
        "provenance",
        "new_value",
        "consent_receipt",
        "request_sha256",
    }
    _exact_fields(request, allowed=fields, required=fields, prefix="REQUEST")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise MemoryRepairError("MEMORY_REPAIR_REQUEST_SCHEMA_MISMATCH", "unsupported request schema")
    expected_hash = _sha256({key: _copy(item) for key, item in request.items() if key != "request_sha256"})
    if request["request_sha256"] != expected_hash:
        raise MemoryRepairError("MEMORY_REPAIR_REQUEST_TAMPERED", "request hash mismatch")
    operation = request["operation"]
    if operation not in OPERATIONS:
        raise MemoryRepairError("MEMORY_REPAIR_OPERATION_INVALID", "unsupported repair operation")
    provenance = _normalize_provenance(request["provenance"], "request.provenance")
    if operation == "correct":
        if request["consent_receipt"] is not None:
            raise MemoryRepairError(
                "MEMORY_REPAIR_CORRECTION_CONSENT_UNEXPECTED",
                "correction request must not carry a forget receipt",
            )
        if provenance["confirmation_state"] != "explicit_user_correction":
            raise MemoryRepairError(
                "MEMORY_REPAIR_CORRECTION_NOT_EXPLICIT",
                "correction must be explicit_user_correction",
            )
        _canonical_json(request["new_value"])
        consent = None
    else:
        if request["new_value"] is not None:
            raise MemoryRepairError(
                "MEMORY_REPAIR_FORGET_VALUE_FORBIDDEN",
                "forget request may not carry a new value",
            )
        if provenance["confirmation_state"] != "explicit_user_confirmation":
            raise MemoryRepairError(
                "MEMORY_REPAIR_FORGET_NOT_EXPLICIT",
                "forget request must be explicit_user_confirmation",
            )
        consent = _normalize_consent(request["consent_receipt"])
    return {
        "schema_version": REQUEST_SCHEMA,
        "request_id": _identifier(request["request_id"], "request.request_id"),
        "operation": operation,
        "tenant_id": _identifier(request["tenant_id"], "request.tenant_id"),
        "user_id": _identifier(request["user_id"], "request.user_id"),
        "lineage_id": _identifier(request["lineage_id"], "request.lineage_id"),
        "issued_at": _timestamp(request["issued_at"], "request.issued_at"),
        "expected_state_revision": _positive_int(
            request["expected_state_revision"],
            "request.expected_state_revision",
        ),
        "based_on_state_sha256": str(request["based_on_state_sha256"]),
        "provenance": provenance,
        "new_value": _copy(request["new_value"]),
        "consent_receipt": _copy(consent),
        "request_sha256": expected_hash,
    }


def _make_tombstone(
    request: Mapping[str, Any],
    removed_record_ids: list[str],
) -> dict[str, Any]:
    receipt = request["consent_receipt"]
    tombstone = {
        "schema_version": TOMBSTONE_SCHEMA,
        "tombstone_id": f"tombstone:{request['request_id']}",
        "request_id": request["request_id"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "lineage_id": request["lineage_id"],
        "created_at": request["issued_at"],
        "reason": "explicit_user_forget",
        "consent_receipt_id": receipt["receipt_id"],
        "forgotten_record_count": len(removed_record_ids),
        "forgotten_record_ids_sha256": _sha256(sorted(removed_record_ids)),
        "provenance": _copy(request["provenance"]),
        "plaintext_retained": False,
    }
    tombstone["tombstone_sha256"] = _sha256(tombstone)
    return _normalize_tombstone(tombstone)


def _make_audit(
    request: Mapping[str, Any],
    *,
    before_state_sha256: str,
    after_projection_sha256: str,
    affected_projections: list[str],
    removed_record_ids: list[str],
    replacement_record_ids: list[str],
    tombstone: Mapping[str, Any] | None,
) -> dict[str, Any]:
    audit = {
        "schema_version": AUDIT_SCHEMA,
        "event_id": f"audit:{request['request_id']}",
        "request_id": request["request_id"],
        "operation": request["operation"],
        "tenant_id": request["tenant_id"],
        "user_id": request["user_id"],
        "lineage_id": request["lineage_id"],
        "occurred_at": request["issued_at"],
        "provenance": _copy(request["provenance"]),
        "before_state_sha256": before_state_sha256,
        "after_projection_sha256": after_projection_sha256,
        "affected_projections": sorted(affected_projections),
        "removed_record_count": len(removed_record_ids),
        "replacement_record_count": len(replacement_record_ids),
        "superseded_record_ids": sorted(removed_record_ids),
        "replacement_record_ids": sorted(replacement_record_ids),
        "tombstone_id": tombstone["tombstone_id"] if tombstone else None,
        "consent_receipt_id": (
            request["consent_receipt"]["receipt_id"]
            if request["consent_receipt"] is not None
            else None
        ),
        "plaintext_retained": False,
    }
    audit["audit_sha256"] = _sha256(audit)
    return _normalize_audit(audit)


class MemoryRepairEngine:
    """Apply one atomic correction or forget request to a sealed state."""

    def __init__(
        self,
        state: Mapping[str, Any],
        *,
        architecture_contract_path: Path = MEMORY_ARCHITECTURE_CONTRACT_PATH,
    ) -> None:
        self.architecture = _load_memory_architecture(architecture_contract_path)
        self.state = validate_memory_state(state)

    def apply(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = _normalize_request(value)
        state = self.state
        if (request["tenant_id"], request["user_id"]) != (
            state["tenant_id"],
            state["user_id"],
        ):
            raise MemoryRepairError(
                "MEMORY_REPAIR_CROSS_USER_CONTEXT_REJECTED",
                "request scope must match state scope",
            )
        if any(item["request_id"] == request["request_id"] for item in state["audit_log"]):
            raise MemoryRepairError(
                "MEMORY_REPAIR_DUPLICATE_REQUEST",
                "request_id has already been applied",
            )
        if request["expected_state_revision"] != state["revision"]:
            raise MemoryRepairError(
                "MEMORY_REPAIR_STALE_REQUEST",
                "expected_state_revision does not match",
            )
        if request["based_on_state_sha256"] != state["state_sha256"]:
            raise MemoryRepairError(
                "MEMORY_REPAIR_STALE_REQUEST",
                "based_on_state_sha256 does not match",
            )
        if _datetime(request["issued_at"]) < _datetime(state["as_of"]):
            raise MemoryRepairError(
                "MEMORY_REPAIR_STALE_REQUEST",
                "request predates the current state",
            )
        if any(item["lineage_id"] == request["lineage_id"] for item in state["tombstones"]):
            raise MemoryRepairError(
                "MEMORY_REPAIR_LINEAGE_TOMBSTONED",
                "tombstoned lineage cannot reappear or be repaired",
            )

        if request["operation"] == "forget":
            receipt = request["consent_receipt"]
            bindings = (
                "request_id",
                "tenant_id",
                "user_id",
                "lineage_id",
            )
            if any(receipt[field] != request[field] for field in bindings):
                raise MemoryRepairError(
                    "MEMORY_REPAIR_CONSENT_BINDING_MISMATCH",
                    "receipt is not bound to this exact request and scope",
                )
            if receipt["provenance"]["correlation_id"] != request["provenance"]["correlation_id"]:
                raise MemoryRepairError(
                    "MEMORY_REPAIR_CONSENT_BINDING_MISMATCH",
                    "receipt correlation_id must match request",
                )
            request_time = _datetime(request["issued_at"])
            if not _datetime(receipt["issued_at"]) <= request_time <= _datetime(receipt["expires_at"]):
                raise MemoryRepairError(
                    "MEMORY_REPAIR_CONSENT_EXPIRED",
                    "request is outside consent validity window",
                )

        projections = _copy(state["projections"])
        removed: list[dict[str, Any]] = []
        replacements: list[dict[str, Any]] = []
        propagation: list[dict[str, Any]] = []
        affected: list[str] = []

        for projection in PROJECTIONS:
            before_records = projections[projection]
            targets = [
                record for record in before_records
                if record["lineage_id"] == request["lineage_id"]
            ]
            retained = [
                record for record in before_records
                if record["lineage_id"] != request["lineage_id"]
            ]
            removed.extend(targets)
            replacement_count = 0
            if targets and request["operation"] == "correct":
                old = targets[0]
                new_record = {
                    "record_id": (
                        f"repair:{projection}:"
                        f"{_sha256([request['request_id'], projection, request['lineage_id']])[:20]}"
                    ),
                    "lineage_id": request["lineage_id"],
                    "tenant_id": request["tenant_id"],
                    "user_id": request["user_id"],
                    "projection": projection,
                    "key": old["key"],
                    "value": _copy(request["new_value"]),
                    "status": "active",
                    "updated_at": request["issued_at"],
                    "provenance": _copy(request["provenance"]),
                }
                retained.append(_normalize_record(new_record, projection))
                replacements.append(new_record)
                replacement_count = 1
            retained.sort(key=lambda item: item["record_id"])
            projections[projection] = retained
            if targets:
                affected.append(projection)
            old_ids = {item["record_id"] for item in targets}
            after_lineage = [
                item for item in retained
                if item["lineage_id"] == request["lineage_id"]
            ]
            propagation.append({
                "projection": projection,
                "before_lineage_count": len(targets),
                "removed_record_count": len(targets),
                "replacement_record_count": replacement_count,
                "after_lineage_count": len(after_lineage),
                "old_record_ids_absent": not any(
                    item["record_id"] in old_ids for item in retained
                ),
                "lineage_absent": len(after_lineage) == 0,
            })

        if not removed:
            raise MemoryRepairError(
                "MEMORY_REPAIR_LINEAGE_NOT_FOUND",
                "target lineage is not active",
            )

        removed_ids = sorted(item["record_id"] for item in removed)
        replacement_ids = sorted(item["record_id"] for item in replacements)
        tombstone = (
            _make_tombstone(request, removed_ids)
            if request["operation"] == "forget"
            else None
        )
        tombstones = [*state["tombstones"]]
        if tombstone is not None:
            tombstones.append(tombstone)
            tombstones.sort(key=lambda item: item["tombstone_id"])
        projection_sha = _sha256({
            "projections": projections,
            "tombstones": tombstones,
        })
        audit = _make_audit(
            request,
            before_state_sha256=state["state_sha256"],
            after_projection_sha256=projection_sha,
            affected_projections=affected,
            removed_record_ids=removed_ids,
            replacement_record_ids=replacement_ids,
            tombstone=tombstone,
        )
        next_state = create_memory_state(
            state_id=state["state_id"],
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            revision=state["revision"] + 1,
            as_of=request["issued_at"],
            projections=projections,
            tombstones=tombstones,
            audit_log=[*state["audit_log"], audit],
        )
        result = {
            "schema_version": RESULT_SCHEMA,
            "policy_version": POLICY_VERSION,
            "request_id": request["request_id"],
            "operation": request["operation"],
            "tenant_id": request["tenant_id"],
            "user_id": request["user_id"],
            "lineage_id": request["lineage_id"],
            "before_state_sha256": state["state_sha256"],
            "after_state_sha256": next_state["state_sha256"],
            "state": next_state,
            "audit_event": audit,
            "tombstone": _copy(tombstone),
            "propagation": propagation,
        }
        result["result_sha256"] = _sha256(result)
        return validate_repair_result(result)


def validate_repair_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate result, state, audit, tombstone, propagation, and seal."""

    source = _mapping(
        value,
        "MEMORY_REPAIR_RESULT_OBJECT_REQUIRED",
        "result must be an object",
    )
    fields = {
        "schema_version",
        "policy_version",
        "request_id",
        "operation",
        "tenant_id",
        "user_id",
        "lineage_id",
        "before_state_sha256",
        "after_state_sha256",
        "state",
        "audit_event",
        "tombstone",
        "propagation",
        "result_sha256",
    }
    _exact_fields(source, allowed=fields, required=fields, prefix="RESULT")
    if source["schema_version"] != RESULT_SCHEMA or source["policy_version"] != POLICY_VERSION:
        raise MemoryRepairError("MEMORY_REPAIR_RESULT_SCHEMA_MISMATCH", "unsupported result schema")
    expected_hash = _sha256({key: _copy(item) for key, item in source.items() if key != "result_sha256"})
    if source["result_sha256"] != expected_hash:
        raise MemoryRepairError("MEMORY_REPAIR_RESULT_TAMPERED", "result hash mismatch")
    state = validate_memory_state(source["state"])
    audit = _normalize_audit(source["audit_event"])
    tombstone = _normalize_tombstone(source["tombstone"]) if source["tombstone"] is not None else None
    propagation = source["propagation"]
    if not isinstance(propagation, list) or [item.get("projection") for item in propagation] != list(PROJECTIONS):
        raise MemoryRepairError(
            "MEMORY_REPAIR_PROPAGATION_INVALID",
            "propagation must cover all six projections in policy order",
        )
    if source["after_state_sha256"] != state["state_sha256"]:
        raise MemoryRepairError("MEMORY_REPAIR_RESULT_STATE_MISMATCH", "after state hash mismatch")
    if audit not in state["audit_log"]:
        raise MemoryRepairError("MEMORY_REPAIR_RESULT_AUDIT_MISSING", "audit not sealed into state")
    if source["operation"] == "forget":
        if tombstone is None or tombstone not in state["tombstones"]:
            raise MemoryRepairError("MEMORY_REPAIR_RESULT_TOMBSTONE_MISSING", "forget tombstone missing")
        if not all(item.get("lineage_absent") is True for item in propagation):
            raise MemoryRepairError(
                "MEMORY_REPAIR_FORGET_PROPAGATION_INCOMPLETE",
                "forgotten lineage remains active",
            )
    elif tombstone is not None:
        raise MemoryRepairError(
            "MEMORY_REPAIR_CORRECTION_TOMBSTONE_FORBIDDEN",
            "correction must not emit a tombstone",
        )
    if not all(item.get("old_record_ids_absent") is True for item in propagation):
        raise MemoryRepairError(
            "MEMORY_REPAIR_SUPERSESSION_INCOMPLETE",
            "old record remains in a projection",
        )
    return _copy(dict(source))


def validate_downstream_projections(
    value: Mapping[str, Any],
    *,
    query: str,
) -> dict[str, Any]:
    """Read repaired projections through existing P3-03 and P3-04 models."""

    state = validate_memory_state(value)
    retrieval_records: list[MemoryRecord] = []
    for record in state["projections"]["retrieval"]:
        content = record["value"] if isinstance(record["value"], str) else _canonical_json(record["value"])
        retrieval_records.append(MemoryRecord.from_value({
            "memory_id": record["record_id"],
            "tenant_id": record["tenant_id"],
            "user_id": record["user_id"],
            "layer": "semantic",
            "key": record["key"],
            "value": content,
            "content": content,
            "keywords": [record["key"]],
            "confidence": 1.0,
            "provenance": record["provenance"],
            "status": "active",
        }))
    retrieval = MemoryRetrievalService(retrieval_records).retrieve(
        tenant_id=state["tenant_id"],
        user_id=state["user_id"],
        query=query,
        as_of=_datetime(state["as_of"]),
        top_k=20,
    )

    relationship_events: list[RelationshipEvent] = []
    for sequence, record in enumerate(state["projections"]["relationship"], start=1):
        relationship_events.append(RelationshipEvent.from_value({
            "event_id": record["record_id"],
            "tenant_id": record["tenant_id"],
            "user_id": record["user_id"],
            "session_id": "memory-repair-projection",
            "sequence": sequence,
            "occurred_at": record["updated_at"],
            "category": "preference",
            "key": record["key"],
            "value": record["value"],
            "operation": "set",
            "confidence": 1.0,
            "provenance": record["provenance"],
            "status": "active",
        }))
    relationship = RelationshipModelV1(relationship_events).explain(
        tenant_id=state["tenant_id"],
        user_id=state["user_id"],
        as_of=_datetime(state["as_of"]),
        query="",
        current_session_id="memory-repair-validation",
    )
    return {
        "memory_architecture_contract_id": _load_memory_architecture()["contract_id"],
        "retrieval_result": retrieval,
        "relationship_result": relationship,
        "retrieval_active_count": len(state["projections"]["retrieval"]),
        "relationship_active_count": len(state["projections"]["relationship"]),
    }


__all__ = [
    "AUDIT_SCHEMA",
    "CONSENT_SCHEMA",
    "MemoryRepairEngine",
    "MemoryRepairError",
    "POLICY_VERSION",
    "PROJECTIONS",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "STATE_SCHEMA",
    "TOMBSTONE_SCHEMA",
    "create_correction_request",
    "create_forget_consent_receipt",
    "create_forget_request",
    "create_memory_state",
    "validate_downstream_projections",
    "validate_memory_state",
    "validate_repair_result",
]
