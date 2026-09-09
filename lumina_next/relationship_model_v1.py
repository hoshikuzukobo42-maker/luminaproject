"""Deterministic, user-scoped Relationship Model v1.

This module is deliberately offline. It replays provenance-bearing relationship
events, delegates optional memory grounding to ``memory_retrieval_v1``, and
quarantines ambiguity rather than inventing a relationship fact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from lumina_next.memory_retrieval_v1 import MemoryRetrievalService


SCHEMA_VERSION = "lumina.relationship.model.v1"
EVALUATION_SCHEMA_VERSION = "lumina.relationship.model.evaluation.v1"
ROOT = Path(__file__).resolve().parents[1]
MEMORY_ARCHITECTURE_CONTRACT_PATH = ROOT / "contracts" / "memory_architecture_v1.json"
SUPPORTED_CATEGORIES = (
    "preference",
    "promise",
    "trust",
    "boundary",
    "relationship_change",
)
CONFIRMATION_PRECEDENCE = {
    "inference": 1,
    "prior_confirmed_memory": 2,
    "validated_direct_observation": 3,
    "explicit_user_confirmation": 4,
    "explicit_user_correction": 5,
}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")


class RelationshipModelError(ValueError):
    pass


def _parse_time(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RelationshipModelError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RelationshipModelError(f"{field} timezone required")
    return parsed.astimezone(timezone.utc)


def _bounded(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RelationshipModelError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise RelationshipModelError(f"{field} must be in [{minimum}, {maximum}]")
    return result


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RelationshipModelError("event value must be JSON-compatible") from exc


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_memory_architecture(path: Path = MEMORY_ARCHITECTURE_CONTRACT_PATH) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RelationshipModelError("memory architecture contract unavailable") from exc
    invariants = set(contract.get("common_invariants") or [])
    required = {"tenant_and_user_scope_required", "provenance_required", "stale_or_conflicting_values_are_not_silently_active"}
    if contract.get("contract_id") != "lumina.memory.architecture.v1" or not required.issubset(invariants):
        raise RelationshipModelError("incompatible memory architecture contract")
    if contract.get("local_only") is not True:
        raise RelationshipModelError("relationship model requires local-only memory architecture")
    return contract


@dataclass(frozen=True)
class RelationshipEvent:
    event_id: str
    tenant_id: str
    user_id: str
    session_id: str
    sequence: int
    occurred_at: str
    category: str
    key: str
    value: Any
    operation: str
    confidence: float
    provenance: Mapping[str, Any]
    supersedes_event_ids: tuple[str, ...] = ()
    target_event_id: str = ""
    status: str = "active"

    @classmethod
    def from_value(cls, raw: Mapping[str, Any]) -> "RelationshipEvent":
        if not isinstance(raw, Mapping):
            raise RelationshipModelError("relationship event must be an object")
        required_text = {
            name: str(raw.get(name) or "").strip()
            for name in ("event_id", "tenant_id", "user_id", "session_id", "occurred_at", "category", "key", "operation")
        }
        if not all(required_text.values()):
            raise RelationshipModelError("event identity, scope, session, time, category, key, and operation are required")
        if required_text["category"] not in SUPPORTED_CATEGORIES:
            raise RelationshipModelError("unsupported relationship category")
        key = required_text["key"].casefold()
        if _KEY_RE.fullmatch(key) is None:
            raise RelationshipModelError("relationship key is invalid")
        try:
            sequence = int(raw.get("sequence"))
        except (TypeError, ValueError) as exc:
            raise RelationshipModelError("sequence must be an integer") from exc
        if sequence < 1:
            raise RelationshipModelError("sequence must be positive")
        _parse_time(required_text["occurred_at"], field="occurred_at")
        if "value" not in raw:
            raise RelationshipModelError("relationship event value is required")
        _canonical(raw["value"])
        provenance = raw.get("provenance")
        if not isinstance(provenance, Mapping):
            raise RelationshipModelError("provenance object is required")
        source_id = str(provenance.get("source_id") or "").strip()
        correlation_id = str(provenance.get("correlation_id") or "").strip()
        confirmation = str(provenance.get("confirmation_state") or "").strip()
        if not source_id or not correlation_id or confirmation not in CONFIRMATION_PRECEDENCE:
            raise RelationshipModelError("provenance source_id, correlation_id, and valid confirmation_state are required")
        supersedes = raw.get("supersedes_event_ids", [])
        if not isinstance(supersedes, list) or not all(str(item).strip() for item in supersedes):
            raise RelationshipModelError("supersedes_event_ids must be a string array")
        operation = required_text["operation"].casefold()
        allowed_operations = {
            "preference": {"set", "correct"},
            "promise": {"open", "fulfill", "breach", "cancel"},
            "trust": {"set", "adjust"},
            "boundary": {"enforce", "relax"},
            "relationship_change": {"set", "correct"},
        }
        if operation not in allowed_operations[required_text["category"]]:
            raise RelationshipModelError("operation is invalid for relationship category")
        if operation == "correct" and (
            confirmation != "explicit_user_correction" or not supersedes
        ):
            raise RelationshipModelError("correction requires explicit user correction and superseded event IDs")
        target_event_id = str(raw.get("target_event_id") or "").strip()
        if required_text["category"] == "promise" and operation in {"fulfill", "breach", "cancel"} and not target_event_id:
            raise RelationshipModelError("promise lifecycle event requires target_event_id")
        if required_text["category"] == "boundary":
            if operation == "enforce" and raw["value"] is not True:
                raise RelationshipModelError("boundary enforce value must be true")
            if operation == "relax" and raw["value"] is not False:
                raise RelationshipModelError("boundary relax value must be false")
        if required_text["category"] == "trust":
            if operation == "adjust":
                _bounded(raw["value"], field="trust adjustment", minimum=-1.0, maximum=1.0)
            else:
                _bounded(raw["value"], field="trust score", minimum=0.0, maximum=1.0)
        status = str(raw.get("status") or "active").strip().casefold()
        if status not in {"active", "superseded", "revoked"}:
            raise RelationshipModelError("unsupported relationship event status")
        return cls(
            event_id=required_text["event_id"],
            tenant_id=required_text["tenant_id"],
            user_id=required_text["user_id"],
            session_id=required_text["session_id"],
            sequence=sequence,
            occurred_at=required_text["occurred_at"],
            category=required_text["category"],
            key=key,
            value=copy.deepcopy(raw["value"]),
            operation=operation,
            confidence=_bounded(raw.get("confidence", 0.0), field="confidence", minimum=0.0, maximum=1.0),
            provenance=copy.deepcopy(dict(provenance)),
            supersedes_event_ids=tuple(str(item).strip() for item in supersedes),
            target_event_id=target_event_id,
            status=status,
        )

    @property
    def confirmation_state(self) -> str:
        return str(self.provenance["confirmation_state"])

    @property
    def occurred_datetime(self) -> datetime:
        return _parse_time(self.occurred_at, field="occurred_at")


def _grounding(event: RelationshipEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "source_id": event.provenance["source_id"],
        "correlation_id": event.provenance["correlation_id"],
        "confirmation_state": event.confirmation_state,
        "memory_id": event.provenance.get("memory_id"),
        "occurred_at": event.occurred_at,
    }


def _quarantine(category: str, key: str, events: Iterable[RelationshipEvent], reason: str) -> dict[str, Any]:
    return {
        "category": category,
        "key": key,
        "event_ids": sorted({item.event_id for item in events}),
        "reason": reason,
        "plaintext_values_retained": False,
    }


def _active_after_supersession(events: list[RelationshipEvent]) -> list[RelationshipEvent]:
    event_ids = {item.event_id for item in events}
    superseded: set[str] = set()
    for item in events:
        if item.confirmation_state != "explicit_user_correction":
            continue
        if item.supersedes_event_ids and set(item.supersedes_event_ids).issubset(event_ids):
            superseded.update(item.supersedes_event_ids)
    return [item for item in events if item.event_id not in superseded]


class RelationshipModelV1:
    def __init__(
        self,
        events: Iterable[Mapping[str, Any] | RelationshipEvent],
        *,
        memory_records: Iterable[Mapping[str, Any]] = (),
        architecture_contract_path: Path = MEMORY_ARCHITECTURE_CONTRACT_PATH,
    ) -> None:
        self.architecture_contract_path = Path(architecture_contract_path)
        self.architecture = _load_memory_architecture(self.architecture_contract_path)
        normalized = [item if isinstance(item, RelationshipEvent) else RelationshipEvent.from_value(item) for item in events]
        event_ids = [item.event_id for item in normalized]
        if len(event_ids) != len(set(event_ids)):
            raise RelationshipModelError("duplicate relationship event_id")
        scoped_sequences = [(item.tenant_id, item.user_id, item.sequence) for item in normalized]
        if len(scoped_sequences) != len(set(scoped_sequences)):
            raise RelationshipModelError("duplicate sequence in tenant/user scope")
        by_id = {item.event_id: item for item in normalized}
        for item in normalized:
            references = list(item.supersedes_event_ids)
            if item.target_event_id:
                references.append(item.target_event_id)
            for reference in references:
                target = by_id.get(reference)
                if target is None or (target.tenant_id, target.user_id) != (item.tenant_id, item.user_id):
                    raise RelationshipModelError("relationship event reference must resolve in the same user scope")
                if target.sequence >= item.sequence:
                    raise RelationshipModelError("relationship event may reference only an earlier sequence")
        self.events = tuple(sorted(normalized, key=lambda item: (item.tenant_id, item.user_id, item.sequence, item.event_id)))
        self.memory_records = tuple(copy.deepcopy(list(memory_records)))
        self.retrieval = MemoryRetrievalService(self.memory_records)

    def explain(
        self,
        *,
        tenant_id: str,
        user_id: str,
        as_of: datetime,
        query: str = "relationship preferences promises trust boundaries",
        current_session_id: str = "",
    ) -> dict[str, Any]:
        tenant_id = str(tenant_id).strip()
        user_id = str(user_id).strip()
        if not tenant_id or not user_id:
            raise RelationshipModelError("tenant_id and user_id are required")
        if as_of.tzinfo is None:
            raise RelationshipModelError("as_of must be timezone-aware")
        cutoff = as_of.astimezone(timezone.utc)
        scoped = [
            item
            for item in self.events
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and item.status == "active"
            and item.occurred_datetime <= cutoff
        ]
        state, quarantined = self._replay(scoped)
        memory_grounding: list[dict[str, Any]] = []
        if str(query).strip():
            retrieval = self.retrieval.retrieve(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                as_of=cutoff,
                top_k=5,
            )
            memory_grounding = retrieval["results"]
        prior_event_ids = {
            grounding["event_id"]
            for section in state.values()
            for grounding in self._section_groundings(section)
            if current_session_id and grounding["session_id"] != current_session_id
        }
        architecture_sha = hashlib.sha256(self.architecture_contract_path.read_bytes()).hexdigest()
        canonical = {
            "state": state,
            "quarantined": quarantined,
            "memory_grounding": memory_grounding,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "as_of": cutoff.isoformat().replace("+00:00", "Z"),
            "current_session_id": current_session_id,
            "state": state,
            "quarantined": quarantined,
            "memory_grounding": memory_grounding,
            "continuity": {
                "active_prior_session_event_count": len(prior_event_ids),
                "prior_session_event_ids": sorted(prior_event_ids),
            },
            "policy": {
                "scope": "exact_tenant_and_user",
                "contradiction": "quarantine_without_plaintext",
                "boundary_relaxation": "explicit_user_correction_only",
                "answer": "grounded_state_only",
                "memory_architecture_contract_id": self.architecture["contract_id"],
                "memory_architecture_sha256": architecture_sha,
            },
            "canonical_state_sha256": _stable_hash(canonical),
        }

    @staticmethod
    def _section_groundings(section: Any) -> list[dict[str, Any]]:
        if isinstance(section, list):
            return [grounding for item in section for grounding in item.get("grounding", [])]
        if isinstance(section, Mapping):
            return [grounding for item in section.values() if isinstance(item, Mapping) for grounding in item.get("grounding", [])]
        return []

    def _replay(self, events: list[RelationshipEvent]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        quarantine: list[dict[str, Any]] = []
        state = {
            "preferences": self._resolve_scalar(events, "preference", quarantine),
            "promises": self._resolve_promises(events, quarantine),
            "trust": self._resolve_trust(events, quarantine),
            "boundaries": self._resolve_boundaries(events, quarantine),
            "relationship_changes": self._resolve_scalar(events, "relationship_change", quarantine),
        }
        quarantine.sort(key=lambda item: (item["category"], item["key"], item["reason"], item["event_ids"]))
        return state, quarantine

    def _resolve_scalar(
        self,
        events: list[RelationshipEvent],
        category: str,
        quarantine: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        keys = sorted({item.key for item in events if item.category == category})
        allowed_confirmation = {"explicit_user_confirmation", "explicit_user_correction", "prior_confirmed_memory"}
        for key in keys:
            candidates = [item for item in events if item.category == category and item.key == key]
            eligible = [item for item in candidates if item.confidence >= 0.5 and item.confirmation_state in allowed_confirmation]
            rejected = [item for item in candidates if item not in eligible]
            if rejected:
                quarantine.append(_quarantine(category, key, rejected, "unconfirmed_or_low_confidence"))
            active = _active_after_supersession(eligible)
            values = {_canonical(item.value) for item in active}
            if len(values) != 1:
                if active:
                    quarantine.append(_quarantine(category, key, active, "contradictory_active_values"))
                continue
            chosen = max(active, key=lambda item: (CONFIRMATION_PRECEDENCE[item.confirmation_state], item.sequence, item.event_id))
            output.append({
                "key": key,
                "value": copy.deepcopy(chosen.value),
                "status": "confirmed",
                "explanation": f"{category}:{key} is active because the highest-precedence confirmed event is {chosen.event_id}",
                "grounding": [_grounding(item) for item in sorted(active, key=lambda item: item.sequence)],
            })
        return output

    def _resolve_boundaries(
        self,
        events: list[RelationshipEvent],
        quarantine: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        keys = sorted({item.key for item in events if item.category == "boundary"})
        for key in keys:
            candidates = [item for item in events if item.category == "boundary" and item.key == key]
            enforcements = [
                item for item in candidates
                if item.operation == "enforce"
                and item.confidence >= 0.5
                and item.confirmation_state in {"explicit_user_confirmation", "explicit_user_correction", "prior_confirmed_memory"}
            ]
            rejected_enforcements = [item for item in candidates if item.operation == "enforce" and item not in enforcements]
            if rejected_enforcements:
                quarantine.append(_quarantine("boundary", key, rejected_enforcements, "unconfirmed_boundary"))
            active_enforcements = _active_after_supersession(enforcements)
            relaxations = [item for item in candidates if item.operation == "relax"]
            valid_relaxations = [
                item for item in relaxations
                if item.confidence >= 0.5
                and item.confirmation_state == "explicit_user_correction"
                and active_enforcements
                and {event.event_id for event in active_enforcements}.issubset(set(item.supersedes_event_ids))
            ]
            invalid_relaxations = [item for item in relaxations if item not in valid_relaxations]
            if invalid_relaxations:
                quarantine.append(_quarantine("boundary", key, invalid_relaxations, "boundary_relaxation_not_explicitly_authorized"))
            if valid_relaxations:
                chosen = max(valid_relaxations, key=lambda item: (item.sequence, item.event_id))
                output.append({
                    "key": key,
                    "value": False,
                    "status": "relaxed_by_explicit_correction",
                    "explanation": f"boundary:{key} changed only through explicit correction {chosen.event_id}",
                    "grounding": [_grounding(chosen)],
                })
            elif active_enforcements:
                output.append({
                    "key": key,
                    "value": True,
                    "status": "enforced",
                    "explanation": f"boundary:{key} remains enforced; unconfirmed relaxation cannot override it",
                    "grounding": [_grounding(item) for item in active_enforcements],
                })
        return output

    def _resolve_trust(
        self,
        events: list[RelationshipEvent],
        quarantine: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        keys = sorted({item.key for item in events if item.category == "trust"})
        for key in keys:
            candidates = [item for item in events if item.category == "trust" and item.key == key]
            eligible = [
                item for item in candidates
                if item.confidence >= 0.5 and CONFIRMATION_PRECEDENCE[item.confirmation_state] >= CONFIRMATION_PRECEDENCE["validated_direct_observation"]
            ]
            rejected = [item for item in candidates if item not in eligible]
            if rejected:
                quarantine.append(_quarantine("trust", key, rejected, "unvalidated_trust_signal"))
            sets = _active_after_supersession([item for item in eligible if item.operation == "set"])
            set_values = {_canonical(item.value) for item in sets}
            if len(set_values) > 1:
                quarantine.append(_quarantine("trust", key, sets, "contradictory_trust_baselines"))
                continue
            baseline = float(sets[-1].value) if sets else 0.5
            adjustments = [item for item in eligible if item.operation == "adjust"]
            score = round(min(1.0, max(0.0, baseline + sum(float(item.value) for item in adjustments))), 6)
            grounding_events = sorted(sets + adjustments, key=lambda item: item.sequence)
            if grounding_events:
                output[key] = {
                    "score": score,
                    "baseline": baseline,
                    "status": "derived_from_grounded_events",
                    "explanation": f"trust:{key} = bounded baseline plus {len(adjustments)} grounded adjustment(s)",
                    "grounding": [_grounding(item) for item in grounding_events],
                }
        return output

    def _resolve_promises(
        self,
        events: list[RelationshipEvent],
        quarantine: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        promise_events = [item for item in events if item.category == "promise"]
        keys = sorted({item.key for item in promise_events})
        for key in keys:
            opens = [
                item for item in promise_events
                if item.key == key
                and item.operation == "open"
                and item.confidence >= 0.5
                and CONFIRMATION_PRECEDENCE[item.confirmation_state] >= CONFIRMATION_PRECEDENCE["validated_direct_observation"]
            ]
            rejected_opens = [item for item in promise_events if item.key == key and item.operation == "open" and item not in opens]
            if rejected_opens:
                quarantine.append(_quarantine("promise", key, rejected_opens, "unvalidated_promise"))
            active_opens = _active_after_supersession(opens)
            values = {_canonical(item.value) for item in active_opens}
            if len(values) != 1:
                if active_opens:
                    quarantine.append(_quarantine("promise", key, active_opens, "contradictory_open_promises"))
                continue
            opened = max(active_opens, key=lambda item: (item.sequence, item.event_id))
            lifecycle = [
                item for item in promise_events
                if item.key == key
                and item.operation in {"fulfill", "breach", "cancel"}
                and item.target_event_id == opened.event_id
                and item.confidence >= 0.5
                and CONFIRMATION_PRECEDENCE[item.confirmation_state] >= CONFIRMATION_PRECEDENCE["validated_direct_observation"]
            ]
            active_lifecycle = _active_after_supersession(lifecycle)
            lifecycle_states = {item.operation for item in active_lifecycle}
            if len(lifecycle_states) > 1:
                quarantine.append(_quarantine("promise", key, [opened, *active_lifecycle], "contradictory_promise_outcomes"))
                continue
            outcome = max(active_lifecycle, key=lambda item: (item.sequence, item.event_id)) if active_lifecycle else None
            status = {"fulfill": "fulfilled", "breach": "breached", "cancel": "cancelled"}.get(outcome.operation if outcome else "", "open")
            grounding_events = [opened] + ([outcome] if outcome else [])
            output.append({
                "key": key,
                "value": copy.deepcopy(opened.value),
                "status": status,
                "explanation": f"promise:{key} is {status} from {', '.join(item.event_id for item in grounding_events)}",
                "grounding": [_grounding(item) for item in grounding_events],
            })
        return output


def _facts(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    state = result["state"]
    facts: list[dict[str, Any]] = []
    for category, section_name in (
        ("preference", "preferences"),
        ("promise", "promises"),
        ("boundary", "boundaries"),
        ("relationship_change", "relationship_changes"),
    ):
        for item in state[section_name]:
            facts.append({
                "category": category,
                "key": item["key"],
                "value": item["value"],
                "status": item["status"],
                "grounding": item["grounding"],
            })
    for key, item in state["trust"].items():
        facts.append({
            "category": "trust",
            "key": key,
            "value": item["score"],
            "status": item["status"],
            "grounding": item["grounding"],
        })
    return facts


def _matches_expected(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for field in ("category", "key", "status"):
        if field in expected and actual.get(field) != expected[field]:
            return False
    if "value" in expected:
        expected_value = expected["value"]
        actual_value = actual.get("value")
        if isinstance(expected_value, float):
            return isinstance(actual_value, (int, float)) and abs(float(actual_value) - expected_value) <= 1e-9
        return actual_value == expected_value
    return True


def evaluate_relationship_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    events = fixture.get("events")
    memory_records = fixture.get("memory_records", [])
    continuity_cases = fixture.get("continuity_cases")
    leakage_cases = fixture.get("leakage_cases")
    fail_closed_cases = fixture.get("fail_closed_cases")
    if not isinstance(events, list) or not isinstance(memory_records, list):
        raise RelationshipModelError("fixture events and memory_records must be arrays")
    if not isinstance(continuity_cases, list) or not continuity_cases:
        raise RelationshipModelError("fixture continuity_cases are required")
    if not isinstance(leakage_cases, list) or not isinstance(fail_closed_cases, Mapping):
        raise RelationshipModelError("fixture leakage and fail_closed cases are required")
    as_of = _parse_time(fixture.get("as_of"), field="fixture as_of")
    model = RelationshipModelV1(events, memory_records=memory_records)

    expected_total = 0
    matched_total = 0
    for case in continuity_cases:
        result = model.explain(
            tenant_id=str(case["tenant_id"]),
            user_id=str(case["user_id"]),
            current_session_id=str(case["current_session_id"]),
            query=str(case.get("query") or "relationship preferences"),
            as_of=as_of,
        )
        facts = _facts(result)
        for expected in case.get("expected_facts", []):
            expected_total += 1
            matched_total += int(any(
                _matches_expected(actual, expected)
                and any(
                    grounding["session_id"] != str(case["current_session_id"])
                    for grounding in actual["grounding"]
                )
                for actual in facts
            ))

    leakage_count = 0
    for case in leakage_cases:
        result = model.explain(
            tenant_id=str(case["tenant_id"]),
            user_id=str(case["user_id"]),
            current_session_id=str(case.get("current_session_id") or "leakage-check"),
            query=str(case.get("query") or "relationship preferences"),
            as_of=as_of,
        )
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        leakage_count += sum(serialized.count(str(marker)) for marker in case.get("forbidden_markers", []))

    boundary_cases = fail_closed_cases.get("boundaries", [])
    boundary_pass = 0
    for case in boundary_cases:
        result = model.explain(tenant_id=str(case["tenant_id"]), user_id=str(case["user_id"]), as_of=as_of)
        active = next((item for item in result["state"]["boundaries"] if item["key"] == case["key"]), None)
        quarantined_ids = {event_id for item in result["quarantined"] for event_id in item["event_ids"]}
        boundary_pass += int(
            active is not None
            and active["status"] == case["expected_status"]
            and case["expected_active_event_id"] in {item["event_id"] for item in active["grounding"]}
            and case["quarantined_event_id"] in quarantined_ids
        )

    contradiction_cases = fail_closed_cases.get("contradictions", [])
    contradiction_pass = 0
    for case in contradiction_cases:
        result = model.explain(tenant_id=str(case["tenant_id"]), user_id=str(case["user_id"]), as_of=as_of)
        active_facts = _facts(result)
        matching_active = [item for item in active_facts if item["category"] == case["category"] and item["key"] == case["key"]]
        quarantine_match = any(
            item["category"] == case["category"]
            and item["key"] == case["key"]
            and set(case["expected_event_ids"]).issubset(set(item["event_ids"]))
            for item in result["quarantined"]
        )
        contradiction_pass += int(not matching_active and quarantine_match)

    replay_case = fixture.get("deterministic_replay_case") or continuity_cases[0]
    replay_hashes: list[str] = []
    for ordered in (events, list(reversed(events)), sorted(events, key=lambda item: str(item["event_id"]))):
        replay_model = RelationshipModelV1(ordered, memory_records=list(reversed(memory_records)))
        replay_result = replay_model.explain(
            tenant_id=str(replay_case["tenant_id"]),
            user_id=str(replay_case["user_id"]),
            current_session_id=str(replay_case.get("current_session_id") or "replay"),
            query=str(replay_case.get("query") or "relationship preferences"),
            as_of=as_of,
        )
        replay_hashes.append(replay_result["canonical_state_sha256"])

    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "continuity_expected_fact_count": expected_total,
        "continuity_matched_fact_count": matched_total,
        "cross_session_continuity_rate": matched_total / expected_total if expected_total else 0.0,
        "cross_user_leakage_count": leakage_count,
        "boundary_case_count": len(boundary_cases),
        "boundary_fail_closed_rate": boundary_pass / len(boundary_cases) if boundary_cases else 0.0,
        "contradiction_case_count": len(contradiction_cases),
        "contradiction_fail_closed_rate": contradiction_pass / len(contradiction_cases) if contradiction_cases else 0.0,
        "deterministic_replay": len(set(replay_hashes)) == 1,
        "deterministic_replay_sha256": replay_hashes[0] if replay_hashes else "",
    }


__all__ = [
    "MEMORY_ARCHITECTURE_CONTRACT_PATH",
    "RelationshipEvent",
    "RelationshipModelError",
    "RelationshipModelV1",
    "evaluate_relationship_fixture",
]
