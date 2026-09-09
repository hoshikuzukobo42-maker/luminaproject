"""Seven-day deterministic simulated-time longitudinal memory evaluation.

P3-06 is an offline evaluation harness.  It composes the P3-01 architecture,
P3-02 consent reference, P3-03 retrieval, P3-04 relationship projection, and
P3-05 repair tools without touching production persistence or live runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .memory_consent_v1 import MEMORY_LAYERS, MemoryConsentController
from .memory_repair_v1 import (
    PROJECTIONS,
    MemoryRepairEngine,
    create_correction_request,
    create_forget_consent_receipt,
    create_forget_request,
    create_memory_state,
    validate_downstream_projections,
)
from .memory_retrieval_v1 import MemoryRetrievalService
from .relationship_model_v1 import (
    MEMORY_ARCHITECTURE_CONTRACT_PATH,
    RelationshipModelV1,
)


FIXTURE_SCHEMA = "lumina.memory.longitudinal.fixture.v1"
EVALUATION_SCHEMA = "lumina.memory.longitudinal.evaluation.v1"
POLICY_VERSION = "lumina.memory.longitudinal.policy.v1"
TIME_MODE = "deterministic_simulated_time"
RETENTION_CATEGORIES = (
    "preference",
    "promise",
    "boundary",
    "correction",
    "deletion",
    "relationship_change",
)


class LongitudinalMemoryEvalError(ValueError):
    """Stable fail-closed evaluation contract error."""

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
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_NONCANONICAL_JSON",
            "fixture/result must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_TIMESTAMP_REQUIRED",
            field_name,
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_TIMESTAMP_INVALID",
            field_name,
        ) from exc
    if parsed.tzinfo is None:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LongitudinalMemoryEvalError(code, detail)
    return value


def _load_architecture() -> dict[str, Any]:
    try:
        contract = json.loads(MEMORY_ARCHITECTURE_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_ARCHITECTURE_UNAVAILABLE",
            "P3-01 architecture contract unavailable",
        ) from exc
    required = {
        "tenant_and_user_scope_required",
        "provenance_required",
        "explicit_user_correction_outranks_inference",
        "forget_propagates_to_active_indexes_and_caches",
    }
    if (
        contract.get("contract_id") != "lumina.memory.architecture.v1"
        or contract.get("local_only") is not True
        or not required.issubset(set(contract.get("common_invariants") or []))
    ):
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_ARCHITECTURE_INCOMPATIBLE",
            "P3-01 invariants missing",
        )
    return contract


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(
        value,
        "LONGITUDINAL_FIXTURE_OBJECT_REQUIRED",
        "fixture must be an object",
    )
    fields = {
        "schema_version",
        "simulation",
        "base_lineages",
        "relationship_events",
        "operations",
        "expectations",
    }
    if set(fixture) != fields:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_FIXTURE_FIELDS_INVALID",
            "fixture fields must match the v1 contract exactly",
        )
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_FIXTURE_SCHEMA_MISMATCH",
            "unsupported fixture schema",
        )
    simulation = _mapping(
        fixture["simulation"],
        "LONGITUDINAL_SIMULATION_REQUIRED",
        "simulation must be an object",
    )
    required_simulation = {
        "simulation_id",
        "tenant_id",
        "user_id",
        "start_at",
        "end_at",
        "time_mode",
        "sessions",
    }
    if set(simulation) != required_simulation:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_SIMULATION_FIELDS_INVALID",
            "simulation fields invalid",
        )
    if simulation["time_mode"] != TIME_MODE:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_TIME_MODE_INVALID",
            "only deterministic simulated time is accepted",
        )
    start = _parse_time(simulation["start_at"], "simulation.start_at")
    end = _parse_time(simulation["end_at"], "simulation.end_at")
    if (end - start).total_seconds() != 7 * 24 * 60 * 60:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_DURATION_INVALID",
            "simulation window must be exactly seven days",
        )
    sessions = simulation["sessions"]
    if not isinstance(sessions, list) or not sessions:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_SESSIONS_REQUIRED",
            "sessions must be a non-empty array",
        )
    session_ids: set[str] = set()
    day_counts: Counter[int] = Counter()
    for session in sessions:
        if not isinstance(session, Mapping) or set(session) != {"session_id", "day", "as_of"}:
            raise LongitudinalMemoryEvalError(
                "LONGITUDINAL_SESSION_INVALID",
                "session fields invalid",
            )
        session_id = str(session["session_id"])
        if not session_id or session_id in session_ids:
            raise LongitudinalMemoryEvalError(
                "LONGITUDINAL_SESSION_DUPLICATE",
                session_id,
            )
        session_ids.add(session_id)
        day = session["day"]
        if isinstance(day, bool) or not isinstance(day, int) or day not in range(1, 8):
            raise LongitudinalMemoryEvalError(
                "LONGITUDINAL_SESSION_DAY_INVALID",
                session_id,
            )
        at = _parse_time(session["as_of"], f"session:{session_id}.as_of")
        if not start <= at < end:
            raise LongitudinalMemoryEvalError(
                "LONGITUDINAL_SESSION_OUTSIDE_WINDOW",
                session_id,
            )
        expected_day = int((at - start).total_seconds() // 86400) + 1
        if day != expected_day:
            raise LongitudinalMemoryEvalError(
                "LONGITUDINAL_SESSION_DAY_MISMATCH",
                session_id,
            )
        day_counts[day] += 1
    if set(day_counts) != set(range(1, 8)) or any(day_counts[day] < 2 for day in range(1, 8)):
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_MULTI_SESSION_COVERAGE_INVALID",
            "each of seven days requires at least two sessions",
        )
    if not isinstance(fixture["base_lineages"], list) or not fixture["base_lineages"]:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_BASE_LINEAGES_REQUIRED",
            "base_lineages required",
        )
    if not isinstance(fixture["relationship_events"], list):
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_RELATIONSHIP_EVENTS_INVALID",
            "relationship_events must be an array",
        )
    if not isinstance(fixture["operations"], list) or {
        item.get("operation") for item in fixture["operations"] if isinstance(item, Mapping)
    } != {"correct", "forget"}:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_OPERATIONS_INVALID",
            "one correction and one forget operation are required",
        )
    expectations = _mapping(
        fixture["expectations"],
        "LONGITUDINAL_EXPECTATIONS_REQUIRED",
        "expectations must be an object",
    )
    if set(expectations) != set(RETENTION_CATEGORIES) | {"foreign_markers"}:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_EXPECTATIONS_INVALID",
            "all retention categories and foreign markers are required",
        )
    _canonical_json(fixture)
    return _copy(dict(fixture))


def _base_record(
    fixture: Mapping[str, Any],
    projection: str,
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    simulation = fixture["simulation"]
    suffix = str(lineage["lineage_id"]).split(":", 1)[-1]
    return {
        "record_id": f"long:{projection}:{suffix}",
        "lineage_id": lineage["lineage_id"],
        "tenant_id": simulation["tenant_id"],
        "user_id": simulation["user_id"],
        "projection": projection,
        "key": lineage["key"],
        "value": _copy(lineage["value"]),
        "status": "active",
        "updated_at": lineage["observed_at"],
        "provenance": _copy(lineage["provenance"]),
    }


def _build_state(fixture: Mapping[str, Any]) -> dict[str, Any]:
    simulation = fixture["simulation"]
    projections = {
        projection: [
            _base_record(fixture, projection, lineage)
            for lineage in fixture["base_lineages"]
        ]
        for projection in PROJECTIONS
    }
    return create_memory_state(
        state_id=f"state:{simulation['simulation_id']}",
        tenant_id=simulation["tenant_id"],
        user_id=simulation["user_id"],
        revision=1,
        as_of=fixture["simulation"]["start_at"],
        projections=projections,
    )


def _relationship_facts(result: Mapping[str, Any]) -> dict[str, Any]:
    state = result["state"]
    return {
        "preferences": {item["key"]: item for item in state["preferences"]},
        "promises": {item["key"]: item for item in state["promises"]},
        "boundaries": {item["key"]: item for item in state["boundaries"]},
        "relationship_changes": {
            item["key"]: item for item in state["relationship_changes"]
        },
    }


def _grounding_ok(item: Mapping[str, Any] | None) -> bool:
    if not isinstance(item, Mapping) or not item.get("grounding"):
        return False
    return all(
        grounding.get("event_id")
        and grounding.get("session_id")
        and grounding.get("source_id")
        and grounding.get("correlation_id")
        and grounding.get("confirmation_state")
        and grounding.get("occurred_at")
        for grounding in item["grounding"]
    )


def _retrieval_value(result: Mapping[str, Any], key: str) -> Any:
    matches = [item["content"] for item in result["results"] if item["key"] == key]
    return matches[0] if len(matches) == 1 else None


def _operation_by_type(fixture: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    matches = [item for item in fixture["operations"] if item["operation"] == operation]
    if len(matches) != 1:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_OPERATION_COUNT_INVALID",
            operation,
        )
    return matches[0]


def evaluate_longitudinal_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a canonical seven-day, multi-session simulated timeline."""

    fixture = _validate_fixture(value)
    architecture = _load_architecture()
    simulation = fixture["simulation"]
    user_id = simulation["user_id"]
    tenant_id = simulation["tenant_id"]
    start = _parse_time(simulation["start_at"], "simulation.start_at")
    end = _parse_time(simulation["end_at"], "simulation.end_at")

    consent = MemoryConsentController()
    default_deny_verified = all(
        consent.can_save(user_id=user_id, layer=layer)["allowed"] is False
        for layer in MEMORY_LAYERS
    )
    consent.set_storage_consent(
        user_id=user_id,
        storage_allowed=True,
        allowed_layers=MEMORY_LAYERS,
        confirmed=True,
        now=start,
    )
    consent_save_checks = {
        layer: consent.can_save(user_id=user_id, layer=layer)["allowed"]
        for layer in MEMORY_LAYERS
    }
    if not default_deny_verified or not all(consent_save_checks.values()):
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_CONSENT_REFERENCE_FAILED",
            "P3-02 default-deny/grant reference failed",
        )

    state = _build_state(fixture)
    sessions = sorted(
        fixture["simulation"]["sessions"],
        key=lambda item: (_parse_time(item["as_of"], "session.as_of"), item["session_id"]),
    )
    operations = sorted(
        fixture["operations"],
        key=lambda item: (_parse_time(item["issued_at"], "operation.issued_at"), item["request_id"]),
    )
    operation_index = 0
    repair_evidence: list[dict[str, Any]] = []
    p3_02_delete_receipts: list[str] = []
    session_results: list[dict[str, Any]] = []
    category_pass: Counter[str] = Counter()
    category_total: Counter[str] = Counter()
    provenance_pass = 0
    provenance_total = 0
    cross_user_leakage_count = 0
    superseded_reappearance_count = 0
    forgotten_reappearance_count = 0

    correction_operation = _operation_by_type(fixture, "correct")
    forget_operation = _operation_by_type(fixture, "forget")
    correction_at = _parse_time(correction_operation["issued_at"], "correction.issued_at")
    forget_at = _parse_time(forget_operation["issued_at"], "forget.issued_at")
    promise_change_at = _parse_time(
        fixture["expectations"]["promise"]["status_change_at"],
        "expectations.promise.status_change_at",
    )

    for session in sessions:
        session_at = _parse_time(session["as_of"], f"session:{session['session_id']}.as_of")
        while operation_index < len(operations):
            operation = operations[operation_index]
            operation_at = _parse_time(operation["issued_at"], "operation.issued_at")
            if operation_at > session_at:
                break
            provenance = _copy(operation["provenance"])
            if operation["operation"] == "correct":
                request = create_correction_request(
                    state,
                    request_id=operation["request_id"],
                    lineage_id=operation["lineage_id"],
                    issued_at=operation["issued_at"],
                    new_value=operation["new_value"],
                    provenance=provenance,
                )
            else:
                target_ids = sorted(
                    record["record_id"]
                    for records in state["projections"].values()
                    for record in records
                    if record["lineage_id"] == operation["lineage_id"]
                )
                p3_02_at = _parse_time(
                    operation["p3_02_receipt_issued_at"],
                    "operation.p3_02_receipt_issued_at",
                )
                reference_receipt = consent.issue_control_receipt(
                    user_id=user_id,
                    operation="DELETE",
                    item_ids=target_ids,
                    confirmation_text=f"DELETE {len(target_ids)} MEMORY ITEMS",
                    now=p3_02_at,
                    ttl_seconds=300,
                )
                consent.consume_control_receipt(
                    reference_receipt,
                    user_id=user_id,
                    operation="DELETE",
                    item_ids=target_ids,
                    now=operation_at,
                )
                p3_02_delete_receipts.append(reference_receipt["receipt_id"])
                bound_receipt = create_forget_consent_receipt(
                    receipt_id=operation["consent_receipt_id"],
                    request_id=operation["request_id"],
                    tenant_id=tenant_id,
                    user_id=user_id,
                    lineage_id=operation["lineage_id"],
                    issued_at=operation["consent_issued_at"],
                    expires_at=operation["consent_expires_at"],
                    provenance=provenance,
                )
                request = create_forget_request(
                    state,
                    request_id=operation["request_id"],
                    lineage_id=operation["lineage_id"],
                    issued_at=operation["issued_at"],
                    provenance=provenance,
                    consent_receipt=bound_receipt,
                )
            repair = MemoryRepairEngine(state).apply(request)
            state = repair["state"]
            repair_evidence.append({
                "request_id": repair["request_id"],
                "operation": repair["operation"],
                "lineage_id": repair["lineage_id"],
                "after_state_sha256": repair["after_state_sha256"],
                "audit_sha256": repair["audit_event"]["audit_sha256"],
                "tombstone_sha256": (
                    repair["tombstone"]["tombstone_sha256"]
                    if repair["tombstone"] is not None
                    else None
                ),
                "propagation_complete": all(
                    item["old_record_ids_absent"]
                    and (
                        item["lineage_absent"]
                        if repair["operation"] == "forget"
                        else item["replacement_record_count"] == 1
                    )
                    for item in repair["propagation"]
                ),
            })
            operation_index += 1

        tombstoned_lineages = {
            item["lineage_id"] for item in state["tombstones"]
        }
        active_relationship_events = [
            event
            for event in fixture["relationship_events"]
            if not (
                event.get("tenant_id") == tenant_id
                and event.get("user_id") == user_id
                and event.get("lineage_id") in tombstoned_lineages
            )
        ]
        relationship = RelationshipModelV1(active_relationship_events).explain(
            tenant_id=tenant_id,
            user_id=user_id,
            current_session_id=session["session_id"],
            query="",
            as_of=session_at,
        )
        downstream = validate_downstream_projections(
            state,
            query="beverage music",
        )
        retrieval = downstream["retrieval_result"]
        facts = _relationship_facts(relationship)
        expectation = fixture["expectations"]

        after_correction = session_at >= correction_at
        after_forget = session_at >= forget_at
        after_promise_change = session_at >= promise_change_at
        beverage_expected = (
            expectation["correction"]["new_value"]
            if after_correction
            else expectation["correction"]["old_value"]
        )
        beverage_fact = facts["preferences"].get(expectation["preference"]["key"])
        beverage_retrieval = _retrieval_value(retrieval, expectation["preference"]["key"])
        preference_ok = (
            beverage_fact is not None
            and beverage_fact.get("value") == beverage_expected
            and beverage_retrieval == beverage_expected
        )

        promise_fact = facts["promises"].get(expectation["promise"]["key"])
        promise_expected = (
            expectation["promise"]["status_after"]
            if after_promise_change
            else expectation["promise"]["status_before"]
        )
        promise_ok = promise_fact is not None and promise_fact.get("status") == promise_expected

        boundary_fact = facts["boundaries"].get(expectation["boundary"]["key"])
        boundary_ok = (
            boundary_fact is not None
            and boundary_fact.get("value") == expectation["boundary"]["value"]
            and boundary_fact.get("status") == expectation["boundary"]["status"]
        )

        active_serialized = json.dumps(
            {
                "state": state["projections"],
                "relationship": relationship,
                "retrieval": retrieval,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        correction_ok = (
            beverage_expected in active_serialized
            and (
                expectation["correction"]["old_value"] not in active_serialized
                if after_correction
                else expectation["correction"]["new_value"] not in active_serialized
            )
        )

        music_fact = facts["preferences"].get(expectation["deletion"]["key"])
        music_retrieval = _retrieval_value(retrieval, expectation["deletion"]["key"])
        if after_forget:
            deletion_ok = (
                music_fact is None
                and music_retrieval is None
                and expectation["deletion"]["value"] not in active_serialized
                and expectation["deletion"]["lineage_id"] in tombstoned_lineages
            )
            deletion_provenance_ok = any(
                item["lineage_id"] == expectation["deletion"]["lineage_id"]
                and item["provenance"].get("source_id")
                and item["provenance"].get("correlation_id")
                for item in state["tombstones"]
            )
        else:
            deletion_ok = (
                music_fact is not None
                and music_fact.get("value") == expectation["deletion"]["value"]
                and music_retrieval == expectation["deletion"]["value"]
            )
            deletion_provenance_ok = _grounding_ok(music_fact)

        relationship_change_fact = facts["relationship_changes"].get(
            expectation["relationship_change"]["key"]
        )
        relationship_change_ok = (
            relationship_change_fact is not None
            and relationship_change_fact.get("value")
            == expectation["relationship_change"]["value"]
        )

        checks = {
            "preference": {
                "passed": preference_ok,
                "provenance_ok": _grounding_ok(beverage_fact),
            },
            "promise": {
                "passed": promise_ok,
                "provenance_ok": _grounding_ok(promise_fact),
            },
            "boundary": {
                "passed": boundary_ok,
                "provenance_ok": _grounding_ok(boundary_fact),
            },
            "correction": {
                "passed": correction_ok,
                "provenance_ok": _grounding_ok(beverage_fact),
            },
            "deletion": {
                "passed": deletion_ok,
                "provenance_ok": bool(deletion_provenance_ok),
            },
            "relationship_change": {
                "passed": relationship_change_ok,
                "provenance_ok": _grounding_ok(relationship_change_fact),
            },
        }
        for category, check in checks.items():
            category_total[category] += 1
            category_pass[category] += int(check["passed"])
            provenance_total += 1
            provenance_pass += int(check["provenance_ok"])

        foreign_markers = expectation["foreign_markers"]
        session_leakage = sum(active_serialized.count(str(marker)) for marker in foreign_markers)
        cross_user_leakage_count += session_leakage
        if after_correction:
            superseded_reappearance_count += active_serialized.count(
                expectation["correction"]["old_value"]
            )
        if after_forget:
            forgotten_reappearance_count += active_serialized.count(
                expectation["deletion"]["value"]
            )
        session_results.append({
            "session_id": session["session_id"],
            "day": session["day"],
            "as_of": _iso(session_at),
            "state_revision": state["revision"],
            "state_sha256": state["state_sha256"],
            "checks": checks,
            "cross_user_leakage_count": session_leakage,
            "active_relationship_fact_count": sum(
                len(section) if isinstance(section, list) else len(section)
                for section in relationship["state"].values()
            ),
            "retrieval_result_count": len(retrieval["results"]),
        })

    total_checks = sum(category_total.values())
    passed_checks = sum(category_pass.values())
    metrics = {
        "simulated_duration_days": (end - start).total_seconds() / 86400,
        "simulated_sessions": len(sessions),
        "simulated_days_with_multiple_sessions": sum(
            count >= 2 for count in Counter(item["day"] for item in sessions).values()
        ),
        "retention_checks_passed": passed_checks,
        "retention_checks_total": total_checks,
        "retention_percent": 100.0 * passed_checks / total_checks,
        "retention_by_category_percent": {
            category: 100.0 * category_pass[category] / category_total[category]
            for category in RETENTION_CATEGORIES
        },
        "provenance_checks_passed": provenance_pass,
        "provenance_checks_total": provenance_total,
        "provenance_coverage_percent": 100.0 * provenance_pass / provenance_total,
        "cross_user_leakage_count": cross_user_leakage_count,
        "superseded_reappearance_count": superseded_reappearance_count,
        "forgotten_reappearance_count": forgotten_reappearance_count,
        "repair_operations_applied": len(repair_evidence),
        "repair_operations_propagation_complete": sum(
            item["propagation_complete"] for item in repair_evidence
        ),
        "p3_02_delete_receipts_consumed": len(p3_02_delete_receipts),
    }
    local_candidate = (
        metrics["simulated_duration_days"] == 7.0
        and metrics["simulated_days_with_multiple_sessions"] == 7
        and metrics["retention_percent"] == 100.0
        and metrics["provenance_coverage_percent"] == 100.0
        and metrics["cross_user_leakage_count"] == 0
        and metrics["superseded_reappearance_count"] == 0
        and metrics["forgotten_reappearance_count"] == 0
        and metrics["repair_operations_applied"] == 2
        and metrics["repair_operations_propagation_complete"] == 2
        and metrics["p3_02_delete_receipts_consumed"] == 1
    )
    output = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "simulation_id": simulation["simulation_id"],
        "tenant_id": tenant_id,
        "user_id": user_id,
        "time_evidence": {
            "mode": TIME_MODE,
            "start_at": _iso(start),
            "end_at": _iso(end),
            "elapsed_seconds": int((end - start).total_seconds()),
            "simulated_days": 7,
            "wall_clock_wait_used": False,
            "human_elapsed_time_claim": "NONE",
        },
        "dependencies": {
            "p3_01_memory_architecture_contract_id": architecture["contract_id"],
            "p3_02_default_deny_verified": default_deny_verified,
            "p3_02_save_checks": consent_save_checks,
            "p3_02_delete_receipt_ids": p3_02_delete_receipts,
            "p3_03_retrieval_service": MemoryRetrievalService.__name__,
            "p3_04_relationship_model": RelationshipModelV1.__name__,
            "p3_05_repair_evidence": repair_evidence,
        },
        "metrics": metrics,
        "session_results": session_results,
        "wbs_promotion": {
            "p3_06_local_mechanical_candidate": local_candidate,
            "eligible_for_next_wbs_local_stage": local_candidate,
            "g3_approved": False,
            "human_seven_day_evaluation_complete": False,
            "production_sqlite_integration_complete": False,
            "live_runtime_integration_complete": False,
        },
        "claims": {
            "gate_claim": "NONE",
            "human_review_claim": "NONE",
            "live_integration_claim": "NONE",
            "seven_day_claim": "DETERMINISTIC_SIMULATION_ONLY",
        },
    }
    output["evaluation_sha256"] = _sha256(output)
    return output


def validate_longitudinal_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the sealed evaluation and its explicit non-claims."""

    result = _mapping(
        value,
        "LONGITUDINAL_EVALUATION_OBJECT_REQUIRED",
        "evaluation must be an object",
    )
    if result.get("schema_version") != EVALUATION_SCHEMA:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_EVALUATION_SCHEMA_MISMATCH",
            "unsupported evaluation schema",
        )
    expected = _sha256({key: _copy(item) for key, item in result.items() if key != "evaluation_sha256"})
    if result.get("evaluation_sha256") != expected:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_EVALUATION_TAMPERED",
            "evaluation hash mismatch",
        )
    if result.get("time_evidence", {}).get("mode") != TIME_MODE:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_TIME_EVIDENCE_INVALID",
            "simulation evidence missing",
        )
    promotion = result.get("wbs_promotion", {})
    if promotion.get("g3_approved") is not False or promotion.get("human_seven_day_evaluation_complete") is not False:
        raise LongitudinalMemoryEvalError(
            "LONGITUDINAL_OVERCLAIM_REJECTED",
            "G3/human seven-day evaluation may not be claimed",
        )
    return _copy(dict(result))


__all__ = [
    "EVALUATION_SCHEMA",
    "FIXTURE_SCHEMA",
    "LongitudinalMemoryEvalError",
    "POLICY_VERSION",
    "RETENTION_CATEGORIES",
    "TIME_MODE",
    "evaluate_longitudinal_fixture",
    "validate_longitudinal_evaluation",
]
