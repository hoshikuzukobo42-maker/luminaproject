"""Deterministic, in-process memory consolidation for Lumina.

This module deliberately has no persistence, clock, randomness, model, or
application dependencies.  Callers provide the complete input snapshot and
the observation timestamp; :func:`sleep_once` returns a new snapshot.  The
``MemorySleep`` facade is a small convenience wrapper around that pure core.

The implementation is intentionally conservative: semantic and relationship
conflicts are quarantined, and sleep-generated writes can never change the
identity layer.  See ``docs/MEMORY_SLEEP_CONTRACT.md`` for the data contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence


MODULE_VERSION = "memory-sleep-v1"
MEMORY_KINDS = ("working", "episodic", "semantic", "relationship", "identity")
_VALID_KINDS = frozenset(MEMORY_KINDS)
_MAX_TEXT = 4000
_MAX_KEY = 256
_MAX_SOURCE_ID = 256
_WHITESPACE_RE = re.compile(r"\s+")


def _bounded_float(value: Any, default: float = 0.0) -> float:
    """Return a finite float in ``[0, 1]`` without allowing bad input through."""

    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):  # Keep malformed event metadata non-fatal.
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return round(max(0.0, min(1.0, number)), 6)


def _text(value: Any, limit: int) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _canonical(value: Any) -> Any:
    """Convert JSON-like input into a stable, JSON-serializable structure."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 8) if value == value and abs(value) != float("inf") else None
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(values, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return values
    return _text(value, _MAX_TEXT)


def canonical_json(value: Any) -> str:
    """Serialize JSON-like data canonically for hashing and comparisons."""

    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, *parts: Any) -> str:
    """Return a stable ID derived solely from the supplied values."""

    digest = hashlib.sha256(canonical_json([MODULE_VERSION, *parts]).encode("utf-8")).hexdigest()
    return f"{_text(prefix, 32) or 'memory'}-{digest[:24]}"


def _normalize_kind(value: Any) -> str:
    kind = _text(value, 32).lower()
    if kind not in _VALID_KINDS:
        raise ValueError(f"unsupported memory kind: {kind or '<empty>'}")
    return kind


def _normalize_sources(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        values = []
    return tuple(sorted({_text(item, _MAX_SOURCE_ID) for item in values if _text(item, _MAX_SOURCE_ID)}))


def _as_sequence(value: Any) -> tuple[Any, ...]:
    """Coerce optional collection fields without iterating scalar junk."""

    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def compute_salience(
    *,
    importance: Any = 0.5,
    confidence: Any = 0.5,
    emotional_salience: Any = 0.0,
    explicit_salience: Any = 0.0,
    repetition: Any = 0.0,
) -> float:
    """Compute the documented deterministic salience score.

    Inputs are clamped before applying weights.  Repetition is a normalized
    bonus, not an unbounded multiplier, so repeated low-quality input cannot
    overwhelm a protected or contradictory memory.
    """

    score = (
        0.35 * _bounded_float(explicit_salience)
        + 0.25 * _bounded_float(importance, 0.5)
        + 0.20 * _bounded_float(confidence, 0.5)
        + 0.15 * _bounded_float(emotional_salience)
        + 0.05 * _bounded_float(repetition)
    )
    return round(max(0.0, min(1.0, score)), 6)


@dataclass(frozen=True)
class MemoryEvent:
    """A caller-supplied observation to be considered during sleep."""

    kind: str
    content: str
    key: str = ""
    value: str = ""
    source_id: str = ""
    occurred_at: str = ""
    importance: float = 0.5
    confidence: float = 0.5
    emotional_salience: float = 0.0
    explicit_salience: float = 0.0
    repetition: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> "MemoryEvent":
        kind = _normalize_kind(self.kind)
        content = _text(self.content, _MAX_TEXT)
        value = _text(self.value, _MAX_TEXT)
        key = _text(self.key, _MAX_KEY).casefold()
        occurred_at = _text(self.occurred_at, 64)
        metadata = _canonical(self.metadata)
        if not isinstance(metadata, dict):
            metadata = {}
        source_id = _text(self.source_id, _MAX_SOURCE_ID)
        if not source_id:
            source_id = stable_id("source", kind, key, value or content, occurred_at)
        return replace(
            self,
            kind=kind,
            content=content,
            key=key,
            value=value,
            source_id=source_id,
            occurred_at=occurred_at,
            importance=_bounded_float(self.importance, 0.5),
            confidence=_bounded_float(self.confidence, 0.5),
            emotional_salience=_bounded_float(self.emotional_salience),
            explicit_salience=_bounded_float(self.explicit_salience),
            repetition=_bounded_float(self.repetition),
            metadata=metadata,
        )

    @property
    def salience(self) -> float:
        return compute_salience(
            importance=self.importance,
            confidence=self.confidence,
            emotional_salience=self.emotional_salience,
            explicit_salience=self.explicit_salience,
            repetition=self.repetition,
        )

    @classmethod
    def from_value(cls, value: "MemoryEvent | Mapping[str, Any]") -> "MemoryEvent":
        if isinstance(value, cls):
            return value.normalized()
        if not isinstance(value, Mapping):
            raise TypeError("memory events must be MemoryEvent or mapping values")
        return cls(
            kind=str(value.get("kind", "")),
            content=str(value.get("content", value.get("text", ""))),
            key=str(value.get("key", "")),
            value=str(value.get("value", "")),
            source_id=str(value.get("source_id", value.get("id", ""))),
            occurred_at=str(value.get("occurred_at", value.get("created_at", ""))),
            importance=value.get("importance", 0.5),
            confidence=value.get("confidence", 0.5),
            emotional_salience=value.get("emotional_salience", value.get("emotion", 0.0)),
            explicit_salience=value.get("salience", value.get("explicit_salience", 0.0)),
            repetition=value.get("repetition", 0.0),
            metadata=value.get("metadata", {}),
        ).normalized()


@dataclass(frozen=True)
class MemoryItem:
    """A normalized item in one of the five memory layers."""

    id: str
    kind: str
    content: str
    key: str = ""
    value: str = ""
    salience: float = 0.0
    confidence: float = 0.5
    source_ids: tuple[str, ...] = ()
    occurred_at: str = ""
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: MemoryEvent, *, item_id: str | None = None) -> "MemoryItem":
        event = event.normalized()
        content = event.content or event.value
        item_id = item_id or stable_id(
            event.kind, event.key, event.value or content, event.source_id, event.occurred_at
        )
        return cls(
            id=item_id,
            kind=event.kind,
            content=content,
            key=event.key,
            value=event.value or content,
            salience=event.salience,
            confidence=event.confidence,
            source_ids=(event.source_id,),
            occurred_at=event.occurred_at,
            metadata=dict(event.metadata),
        )

    @classmethod
    def from_value(cls, value: "MemoryItem | Mapping[str, Any]") -> "MemoryItem":
        if isinstance(value, cls):
            return value.normalized()
        if not isinstance(value, Mapping):
            raise TypeError("memory items must be MemoryItem or mapping values")
        item = cls(
            id=str(value.get("id", "")),
            kind=str(value.get("kind", "")),
            content=str(value.get("content", value.get("text", ""))),
            key=str(value.get("key", "")),
            value=str(value.get("value", "")),
            salience=value.get("salience", 0.0),
            confidence=value.get("confidence", 0.5),
            source_ids=_normalize_sources(value.get("source_ids", value.get("sources", []))),
            occurred_at=str(value.get("occurred_at", value.get("created_at", ""))),
            status=str(value.get("status", "active")),
            metadata=value.get("metadata", {}),
        )
        return item.normalized()

    def normalized(self) -> "MemoryItem":
        kind = _normalize_kind(self.kind)
        content = _text(self.content, _MAX_TEXT)
        value = _text(self.value, _MAX_TEXT) or content
        key = _text(self.key, _MAX_KEY).casefold()
        metadata = _canonical(self.metadata)
        if not isinstance(metadata, dict):
            metadata = {}
        item_id = _text(self.id, 80) or stable_id(kind, key, value or content, self.occurred_at)
        return replace(
            self,
            id=item_id,
            kind=kind,
            content=content,
            key=key,
            value=value,
            salience=_bounded_float(self.salience),
            confidence=_bounded_float(self.confidence, 0.5),
            source_ids=_normalize_sources(self.source_ids),
            occurred_at=_text(self.occurred_at, 64),
            status=_text(self.status, 32).lower() or "active",
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "key": self.key,
            "value": self.value,
            "salience": self.salience,
            "confidence": self.confidence,
            "source_ids": list(self.source_ids),
            "occurred_at": self.occurred_at,
            "status": self.status,
            "metadata": _canonical(self.metadata),
        }


@dataclass(frozen=True)
class QuarantineRecord:
    """A non-active candidate retained for human review, never applied."""

    id: str
    reason: str
    candidate: MemoryItem
    conflicting_ids: tuple[str, ...] = ()
    sleep_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reason": self.reason,
            "candidate": self.candidate.to_dict(),
            "conflicting_ids": list(self.conflicting_ids),
            "sleep_id": self.sleep_id,
        }


@dataclass(frozen=True)
class MemoryState:
    """Immutable memory snapshot used as the input and output of sleep."""

    working: tuple[MemoryItem, ...] = ()
    episodic: tuple[MemoryItem, ...] = ()
    semantic: tuple[MemoryItem, ...] = ()
    relationship: tuple[MemoryItem, ...] = ()
    identity: tuple[MemoryItem, ...] = ()
    quarantine: tuple[QuarantineRecord, ...] = ()
    applied_sleep_ids: tuple[str, ...] = ()

    def normalized(self) -> "MemoryState":
        buckets: dict[str, list[MemoryItem]] = {kind: [] for kind in MEMORY_KINDS}
        for kind in MEMORY_KINDS:
            for raw in getattr(self, kind):
                item = MemoryItem.from_value(raw)
                if item.kind != kind:
                    item = replace(item, kind=kind)
                buckets[kind].append(item)
        quarantine: list[QuarantineRecord] = []
        for raw in self.quarantine:
            if isinstance(raw, QuarantineRecord):
                quarantine.append(raw)
            elif isinstance(raw, Mapping):
                quarantine.append(
                    QuarantineRecord(
                        id=_text(raw.get("id"), 80),
                        reason=_text(raw.get("reason"), 80),
                        candidate=MemoryItem.from_value(raw.get("candidate", {})),
                        conflicting_ids=_normalize_sources(raw.get("conflicting_ids", [])),
                        sleep_id=_text(raw.get("sleep_id"), 80),
                    )
                )
        return replace(
            self,
            working=tuple(buckets["working"]),
            episodic=tuple(buckets["episodic"]),
            semantic=tuple(buckets["semantic"]),
            relationship=tuple(buckets["relationship"]),
            identity=tuple(buckets["identity"]),
            quarantine=tuple(quarantine),
            applied_sleep_ids=tuple(dict.fromkeys(_text(item, 80) for item in self.applied_sleep_ids if _text(item, 80))),
        )

    @classmethod
    def from_value(cls, value: "MemoryState | Mapping[str, Any] | None") -> "MemoryState":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value.normalized()
        if not isinstance(value, Mapping):
            raise TypeError("memory state must be MemoryState, mapping, or None")
        return cls(
            working=_as_sequence(value.get("working", ())),
            episodic=_as_sequence(value.get("episodic", ())),
            semantic=_as_sequence(value.get("semantic", value.get("facts", ()))),
            relationship=_as_sequence(value.get("relationship", ())),
            identity=_as_sequence(value.get("identity", ())),
            quarantine=_as_sequence(value.get("quarantine", ())),
            applied_sleep_ids=_as_sequence(value.get("applied_sleep_ids", ())),
        ).normalized()

    def to_dict(self) -> dict[str, Any]:
        return {
            "working": [item.to_dict() for item in self.working],
            "episodic": [item.to_dict() for item in self.episodic],
            "semantic": [item.to_dict() for item in self.semantic],
            "relationship": [item.to_dict() for item in self.relationship],
            "identity": [item.to_dict() for item in self.identity],
            "quarantine": [item.to_dict() for item in self.quarantine],
            "applied_sleep_ids": list(self.applied_sleep_ids),
        }


@dataclass(frozen=True)
class RollbackPlan:
    """Pure rollback record: restore ``before`` only if the current snapshot matches."""

    sleep_id: str
    before_digest: str
    after_digest: str
    before: MemoryState

    def apply(self, current: MemoryState | Mapping[str, Any]) -> MemoryState:
        current_state = MemoryState.from_value(current)
        if state_digest(current_state) != self.after_digest:
            raise ValueError("rollback refused: current state does not match sleep output")
        return self.before


@dataclass(frozen=True)
class SleepResult:
    sleep_id: str
    state: MemoryState
    report: Mapping[str, Any]
    rollback: RollbackPlan

    @property
    def changed(self) -> bool:
        return bool(self.report.get("status") == "completed" and self.report.get("accepted", 0))


def state_digest(state: MemoryState | Mapping[str, Any]) -> str:
    """Hash a normalized state snapshot for optimistic rollback checks."""

    normalized = MemoryState.from_value(state)
    return hashlib.sha256(canonical_json(normalized.to_dict()).encode("utf-8")).hexdigest()


def stable_sleep_id(
    trigger: str,
    events: Iterable[MemoryEvent | Mapping[str, Any]],
    *,
    observed_at: str = "",
) -> str:
    """Derive an idempotency key from the complete sleep input."""

    normalized = [MemoryEvent.from_value(event) for event in events]
    event_payload = [
        {
            "kind": event.kind,
            "content": event.content,
            "key": event.key,
            "value": event.value,
            "source_id": event.source_id,
            "occurred_at": event.occurred_at,
            "importance": event.importance,
            "confidence": event.confidence,
            "emotional_salience": event.emotional_salience,
            "explicit_salience": event.explicit_salience,
            "repetition": event.repetition,
            "metadata": event.metadata,
        }
        for event in normalized
    ]
    return stable_id("sleep", _text(trigger, 120), _text(observed_at, 64), event_payload)


def _event_key(event: MemoryEvent) -> str:
    return event.key or _text(event.value or event.content, _MAX_TEXT).casefold()


def _item_matches_event(item: MemoryItem, event: MemoryEvent) -> bool:
    if item.id == stable_id(
        event.kind, event.key, event.value or event.content, event.source_id, event.occurred_at
    ):
        return True
    return (
        item.kind == event.kind
        and _event_key(event) == (item.key or item.value or item.content).casefold()
        and (item.value or item.content).casefold() == (event.value or event.content).casefold()
    )


def _conflicting_items(items: Sequence[MemoryItem], event: MemoryEvent) -> list[MemoryItem]:
    key = _event_key(event)
    if not event.key:
        return []
    return [
        item
        for item in items
        if item.status == "active"
        and item.key == key
        and (item.value or item.content).casefold() != (event.value or event.content).casefold()
    ]


def apply_capacity_decay(
    state: MemoryState | Mapping[str, Any] | None,
    *,
    max_working: int = 32,
    max_episodic: int = 256,
) -> MemoryState:
    """Apply structural decay-by-capacity to working and episodic buckets.

    Working items are retained by recency ``(occurred_at, id)``; episodic items
    by salience ``(salience, occurred_at, id)``.  Other layers are unchanged.
    """

    normalized = MemoryState.from_value(state)
    working_cap = max(0, min(int(max_working), 10000))
    episodic_cap = max(0, min(int(max_episodic), 10000))
    working = sorted(
        normalized.working,
        key=lambda item: (item.occurred_at, item.id),
        reverse=True,
    )[:working_cap]
    episodic = sorted(
        normalized.episodic,
        key=lambda item: (item.salience, item.occurred_at, item.id),
        reverse=True,
    )[:episodic_cap]
    return replace(
        normalized,
        working=tuple(working),
        episodic=tuple(episodic),
    ).normalized()


def _quarantine(
    event: MemoryEvent,
    *,
    reason: str,
    sleep_id: str,
    conflicts: Sequence[MemoryItem] = (),
) -> QuarantineRecord:
    candidate = MemoryItem.from_event(event)
    return QuarantineRecord(
        id=stable_id("quarantine", sleep_id, reason, candidate.id),
        reason=reason,
        candidate=candidate,
        conflicting_ids=tuple(item.id for item in conflicts),
        sleep_id=sleep_id,
    )


def sleep_once(
    state: MemoryState | Mapping[str, Any] | None,
    events: Iterable[MemoryEvent | Mapping[str, Any]],
    *,
    trigger: str = "manual",
    observed_at: str = "",
    sleep_id: str | None = None,
    max_working: int = 32,
    max_episodic: int = 256,
    episode_salience_threshold: float = 0.65,
) -> SleepResult:
    """Consolidate events into a new state without side effects.

    Repeating the same input and sleep ID returns the original state with an
    ``idempotent`` report.  A caller-provided ID must equal the canonical ID;
    this prevents accidental reuse of a key for different event payloads.
    """

    before = MemoryState.from_value(state)
    normalized_events = tuple(MemoryEvent.from_value(event) for event in events)
    canonical_id = stable_sleep_id(trigger, normalized_events, observed_at=observed_at)
    final_sleep_id = _text(sleep_id, 80) if sleep_id else canonical_id
    if final_sleep_id != canonical_id:
        raise ValueError("sleep_id does not match deterministic sleep input")

    before_digest = state_digest(before)
    if final_sleep_id in before.applied_sleep_ids:
        report = {
            "status": "idempotent",
            "sleep_id": final_sleep_id,
            "input_events": len(normalized_events),
            "accepted": 0,
            "deduped": 0,
            "quarantined": 0,
        }
        rollback = RollbackPlan(final_sleep_id, before_digest, before_digest, before)
        return SleepResult(final_sleep_id, before, report, rollback)

    buckets: dict[str, list[MemoryItem]] = {
        kind: list(getattr(before, kind)) for kind in MEMORY_KINDS
    }
    quarantine = list(before.quarantine)
    accepted = 0
    deduped = 0
    quarantined = 0
    promoted = 0
    by_kind = {kind: 0 for kind in MEMORY_KINDS}
    reasons: dict[str, int] = {}
    threshold = _bounded_float(episode_salience_threshold, 0.65)

    for event in normalized_events:
        item = MemoryItem.from_event(event)
        target_kind = event.kind
        if target_kind == "identity":
            existing = [candidate for candidate in buckets["identity"] if _item_matches_event(candidate, event)]
            if existing:
                deduped += 1
            else:
                record = _quarantine(event, reason="identity_protected", sleep_id=final_sleep_id,
                                     conflicts=buckets["identity"])
                quarantine.append(record)
                quarantined += 1
                reasons[record.reason] = reasons.get(record.reason, 0) + 1
            continue

        existing = [candidate for candidate in buckets[target_kind] if _item_matches_event(candidate, event)]
        if existing:
            deduped += 1
            continue

        if target_kind in {"semantic", "relationship"}:
            conflicts = _conflicting_items(buckets[target_kind], event)
            if conflicts:
                record = _quarantine(
                    event,
                    reason=f"{target_kind}_contradiction",
                    sleep_id=final_sleep_id,
                    conflicts=conflicts,
                )
                quarantine.append(record)
                quarantined += 1
                reasons[record.reason] = reasons.get(record.reason, 0) + 1
                continue

        buckets[target_kind].append(item)
        accepted += 1
        by_kind[target_kind] += 1

        if target_kind == "working" and item.salience >= threshold:
            episode = replace(
                item,
                id=stable_id("episode", item.id),
                kind="episodic",
                content=f"Working memory episode: {item.content}"[:_MAX_TEXT],
                metadata={**dict(item.metadata), "promoted_from": item.id},
            )
            if not any(candidate.id == episode.id for candidate in buckets["episodic"]):
                buckets["episodic"].append(episode)
                promoted += 1

    decayed = apply_capacity_decay(
        MemoryState(
            working=tuple(buckets["working"]),
            episodic=tuple(buckets["episodic"]),
            semantic=tuple(buckets["semantic"]),
            relationship=tuple(buckets["relationship"]),
            identity=tuple(buckets["identity"]),
            quarantine=tuple(quarantine),
            applied_sleep_ids=before.applied_sleep_ids,
        ),
        max_working=max_working,
        max_episodic=max_episodic,
    )
    buckets["working"] = list(decayed.working)
    buckets["episodic"] = list(decayed.episodic)
    # Stable ordering makes serialized snapshots and downstream diffs repeatable.
    for kind in ("semantic", "relationship", "identity"):
        buckets[kind] = sorted(buckets[kind], key=lambda item: (item.key, item.id))

    after = MemoryState(
        working=tuple(buckets["working"]),
        episodic=tuple(buckets["episodic"]),
        semantic=tuple(buckets["semantic"]),
        relationship=tuple(buckets["relationship"]),
        identity=tuple(buckets["identity"]),
        quarantine=tuple(quarantine),
        applied_sleep_ids=(*before.applied_sleep_ids, final_sleep_id),
    ).normalized()
    after_digest = state_digest(after)
    report = {
        "status": "completed",
        "sleep_id": final_sleep_id,
        "trigger": _text(trigger, 120),
        "observed_at": _text(observed_at, 64),
        "input_events": len(normalized_events),
        "accepted": accepted,
        "deduped": deduped,
        "quarantined": quarantined,
        "promoted_to_episodic": promoted,
        "by_kind": by_kind,
        "quarantine_reasons": reasons,
        "salience_threshold": threshold,
        "identity_writes": 0,
    }
    rollback = RollbackPlan(final_sleep_id, before_digest, after_digest, before)
    return SleepResult(final_sleep_id, after, report, rollback)


def rollback_sleep(
    result: SleepResult, current: MemoryState | Mapping[str, Any]
) -> MemoryState:
    """Apply a result's optimistic rollback plan as a pure state transform."""

    return result.rollback.apply(current)


class MemorySleep:
    """Small in-memory facade; ``sleep_once`` remains the pure source of truth."""

    def __init__(self, state: MemoryState | Mapping[str, Any] | None = None) -> None:
        self.state = MemoryState.from_value(state)

    def sleep(
        self,
        events: Iterable[MemoryEvent | Mapping[str, Any]],
        *,
        trigger: str = "manual",
        observed_at: str = "",
        sleep_id: str | None = None,
        max_working: int = 32,
        max_episodic: int = 256,
        episode_salience_threshold: float = 0.65,
    ) -> SleepResult:
        result = sleep_once(
            self.state,
            events,
            trigger=trigger,
            observed_at=observed_at,
            sleep_id=sleep_id,
            max_working=max_working,
            max_episodic=max_episodic,
            episode_salience_threshold=episode_salience_threshold,
        )
        self.state = result.state
        return result


# Explicit alias for callers that prefer the engine terminology.
MemorySleepEngine = MemorySleep


__all__ = [
    "MEMORY_KINDS",
    "MODULE_VERSION",
    "MemoryEvent",
    "MemoryItem",
    "MemorySleep",
    "MemorySleepEngine",
    "MemoryState",
    "QuarantineRecord",
    "RollbackPlan",
    "SleepResult",
    "apply_capacity_decay",
    "canonical_json",
    "compute_salience",
    "rollback_sleep",
    "sleep_once",
    "stable_id",
    "stable_sleep_id",
    "state_digest",
]
