"""Deterministic, fail-closed state reconciliation for current-runtime claims.

The policy reconciles versioned claims from the existing Godot, user-action,
STT, and perception source families.  It is deliberately local-only and has
no network, Godot-process, or .NET dependency.

Decision order is fixed:

1. validate exact claim contracts;
2. exclude policy-stale or upstream-stale claims;
3. refuse an older version when a newer known version is stale;
4. keep only the greatest current state version;
5. resolve conflicting values with the authority profile matrix;
6. fail closed when equal-authority claims remain ambiguous.

This module does not attach itself to live producers.  It is the independent
P2-05 reconciliation boundary consumed by later integration work.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Iterable, Mapping


CLAIM_SCHEMA_VERSION = "lumina.state.claim.v1"
RECONCILIATION_SCHEMA_VERSION = "lumina.state.reconciliation.v1"
POLICY_VERSION = "lumina.state.reconciliation.policy.v1"
MAX_FUTURE_SKEW_SECONDS = 2.0

SOURCE_KINDS = (
    "godot_engine",
    "user_action",
    "stt_result",
    "perception_inference",
)

# Strongest source first.  Authority is domain-specific: engine telemetry is
# authoritative for spatial facts, while direct user operations are strongest
# for intent and user-visible behaviour.
AUTHORITY_PROFILES: dict[str, tuple[str, ...]] = {
    "spatial_fact": (
        "godot_engine",
        "user_action",
        "stt_result",
        "perception_inference",
    ),
    "user_intent": (
        "user_action",
        "stt_result",
        "perception_inference",
        "godot_engine",
    ),
    "speech_fact": (
        "stt_result",
        "user_action",
        "perception_inference",
        "godot_engine",
    ),
    "behavior_inference": (
        "user_action",
        "perception_inference",
        "stt_result",
        "godot_engine",
    ),
}

# A source cannot lengthen its own lifetime.  Upstream source_stale=true always
# wins over these limits.
FRESHNESS_SLO_SECONDS: dict[str, float] = {
    "godot_engine": 2.0,
    "user_action": 10.0,
    "stt_result": 5.0,
    "perception_inference": 2.0,
}

FAIL_CLOSED_AMBIGUOUS = "FAIL_CLOSED_AMBIGUOUS"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STATE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class StateReconciliationError(ValueError):
    """Contract violation with a stable machine-readable error code."""

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
        raise StateReconciliationError(
            "RECONCILE_VALUE_NOT_CANONICAL_JSON",
            "value must be finite canonical JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateReconciliationError(code, detail)
    return value


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise StateReconciliationError(
            "RECONCILE_ID_INVALID",
            f"{field_name} must be a canonical identifier",
        )
    return value


def _state_key(value: Any) -> str:
    if not isinstance(value, str) or not _STATE_KEY_PATTERN.fullmatch(value):
        raise StateReconciliationError(
            "RECONCILE_STATE_KEY_INVALID",
            "state_key must be canonical lowercase text",
        )
    return value


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise StateReconciliationError(
            "RECONCILE_TIMESTAMP_REQUIRED",
            f"{field_name} is required",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateReconciliationError(
            "RECONCILE_TIMESTAMP_INVALID",
            field_name,
        ) from exc
    if parsed.tzinfo is None:
        raise StateReconciliationError(
            "RECONCILE_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc)


def _now_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise StateReconciliationError(
            "RECONCILE_NOW_TIMEZONE_REQUIRED",
            "now must be timezone-aware",
        )
    return now.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _priority(profile: str, source_kind: str) -> int:
    return AUTHORITY_PROFILES[profile].index(source_kind)


def build_conflict_matrix() -> dict[str, dict[str, dict[str, str]]]:
    """Return the complete directed source-by-source policy matrix."""

    matrix: dict[str, dict[str, dict[str, str]]] = {}
    for profile, ordered_sources in AUTHORITY_PROFILES.items():
        rows: dict[str, dict[str, str]] = {}
        for left in SOURCE_KINDS:
            cells: dict[str, str] = {}
            for right in SOURCE_KINDS:
                if left == right:
                    cells[right] = FAIL_CLOSED_AMBIGUOUS
                else:
                    cells[right] = min(
                        (left, right),
                        key=lambda source: ordered_sources.index(source),
                    )
            rows[left] = cells
        matrix[profile] = rows
    return matrix


CONFLICT_MATRIX = build_conflict_matrix()


def validate_state_claim(
    claim: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and normalize one claim, deriving freshness from policy."""

    source = _mapping(
        claim,
        "RECONCILE_CLAIM_OBJECT_REQUIRED",
        "claim must be an object",
    )
    if source.get("schema_version") != CLAIM_SCHEMA_VERSION:
        raise StateReconciliationError(
            "RECONCILE_CLAIM_SCHEMA_MISMATCH",
            "unsupported schema_version",
        )
    _identifier(source.get("claim_id"), "claim_id")
    _identifier(source.get("source_id"), "source_id")
    key = _state_key(source.get("state_key"))

    version = source.get("state_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise StateReconciliationError(
            "RECONCILE_STATE_VERSION_INVALID",
            "state_version must be a positive integer",
        )

    source_kind = source.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        raise StateReconciliationError(
            "RECONCILE_SOURCE_UNKNOWN",
            f"unsupported source_kind for {key}",
        )
    profile = source.get("authority_profile")
    if profile not in AUTHORITY_PROFILES:
        raise StateReconciliationError(
            "RECONCILE_AUTHORITY_PROFILE_UNKNOWN",
            f"unsupported authority_profile for {key}",
        )

    observed_at = _utc(source.get("observed_at"), "observed_at")
    current = _now_utc(now)
    age_seconds = (current - observed_at).total_seconds()
    if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise StateReconciliationError(
            "RECONCILE_CLAIM_FROM_FUTURE",
            "observed_at exceeds allowed clock skew",
        )
    age_seconds = max(0.0, age_seconds)

    source_stale = source.get("source_stale")
    if not isinstance(source_stale, bool):
        raise StateReconciliationError(
            "RECONCILE_SOURCE_STALE_FLAG_REQUIRED",
            "source_stale must be boolean",
        )

    confidence = source.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise StateReconciliationError(
            "RECONCILE_CONFIDENCE_INVALID",
            "confidence must be finite and in [0, 1]",
        )

    evidence_refs = source.get("evidence_refs")
    if (
        not isinstance(evidence_refs, list)
        or not evidence_refs
        or any(not isinstance(item, str) or not item.strip() for item in evidence_refs)
    ):
        raise StateReconciliationError(
            "RECONCILE_EVIDENCE_REQUIRED",
            "evidence_refs must contain non-empty strings",
        )
    if "value" not in source:
        raise StateReconciliationError(
            "RECONCILE_VALUE_REQUIRED",
            "value is required",
        )
    value_fingerprint = _sha256(source["value"])

    slo_seconds = FRESHNESS_SLO_SECONDS[str(source_kind)]
    normalized = _copy(source)
    normalized["observed_at"] = _iso(observed_at)
    normalized["confidence"] = float(confidence)
    normalized["value_fingerprint"] = value_fingerprint
    normalized["freshness"] = {
        "age_seconds": age_seconds,
        "slo_seconds": slo_seconds,
        "source_stale": source_stale,
        "is_stale": bool(source_stale or age_seconds > slo_seconds),
    }
    return normalized


def _decision(
    *,
    key: str,
    profile: str | None,
    version: int | None,
    status: str,
    reason: str,
    claims: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    stale_claim_ids: list[str],
    superseded_claim_ids: list[str],
) -> dict[str, Any]:
    fingerprints = sorted({claim["value_fingerprint"] for claim in claims})
    result: dict[str, Any] = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "state_key": key,
        "authority_profile": profile,
        "state_version": version,
        "status": status,
        "reason": reason,
        "safe_to_use": status == "RESOLVED" and selected is not None,
        "conflict_detected": len(fingerprints) > 1,
        "selected_claim": _copy(selected),
        "candidate_claim_ids": sorted(claim["claim_id"] for claim in claims),
        "candidate_value_fingerprints": fingerprints,
        "stale_claim_ids": sorted(stale_claim_ids),
        "superseded_claim_ids": sorted(superseded_claim_ids),
        "side_effects_suppressed": status != "RESOLVED",
    }
    result["decision_sha256"] = _sha256(result)
    return result


def reconcile_state_claims(
    claims: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile claims for exactly one state key.

    The function never returns a selected stale claim.  Contract violations
    raise ``StateReconciliationError``; policy ambiguity returns a structured
    ``FAIL_CLOSED`` decision with ``selected_claim=None``.
    """

    raw_claims = list(claims)
    if not raw_claims:
        raise StateReconciliationError(
            "RECONCILE_CLAIMS_REQUIRED",
            "at least one claim is required",
        )
    normalized = [validate_state_claim(item, now=now) for item in raw_claims]
    keys = {item["state_key"] for item in normalized}
    if len(keys) != 1:
        raise StateReconciliationError(
            "RECONCILE_STATE_KEY_CONFLICT",
            "reconcile_state_claims accepts one state_key",
        )
    key = next(iter(keys))
    stale = [item for item in normalized if item["freshness"]["is_stale"]]
    fresh = [item for item in normalized if not item["freshness"]["is_stale"]]
    stale_ids = [item["claim_id"] for item in stale]

    if not fresh:
        return _decision(
            key=key,
            profile=None,
            version=max(item["state_version"] for item in normalized),
            status="FAIL_CLOSED",
            reason="ALL_CLAIMS_STALE",
            claims=normalized,
            selected=None,
            stale_claim_ids=stale_ids,
            superseded_claim_ids=[],
        )

    newest_fresh_version = max(item["state_version"] for item in fresh)
    newest_stale_version = max(
        (item["state_version"] for item in stale),
        default=0,
    )
    if newest_stale_version > newest_fresh_version:
        return _decision(
            key=key,
            profile=None,
            version=newest_stale_version,
            status="FAIL_CLOSED",
            reason="NEWER_VERSION_STALE",
            claims=normalized,
            selected=None,
            stale_claim_ids=stale_ids,
            superseded_claim_ids=[
                item["claim_id"]
                for item in fresh
                if item["state_version"] < newest_stale_version
            ],
        )

    candidates = [
        item for item in fresh if item["state_version"] == newest_fresh_version
    ]
    superseded_ids = [
        item["claim_id"]
        for item in fresh
        if item["state_version"] < newest_fresh_version
    ]
    profiles = {item["authority_profile"] for item in candidates}
    if len(profiles) != 1:
        return _decision(
            key=key,
            profile=None,
            version=newest_fresh_version,
            status="FAIL_CLOSED",
            reason="AUTHORITY_PROFILE_CONFLICT",
            claims=candidates,
            selected=None,
            stale_claim_ids=stale_ids,
            superseded_claim_ids=superseded_ids,
        )
    profile = next(iter(profiles))

    fingerprints = {item["value_fingerprint"] for item in candidates}
    top_priority = min(_priority(profile, item["source_kind"]) for item in candidates)
    top_claims = [
        item
        for item in candidates
        if _priority(profile, item["source_kind"]) == top_priority
    ]
    newest_top_time = max(_utc(item["observed_at"], "observed_at") for item in top_claims)
    top_current = [
        item
        for item in top_claims
        if _utc(item["observed_at"], "observed_at") == newest_top_time
    ]
    top_values = {item["value_fingerprint"] for item in top_current}
    if len(top_values) > 1:
        return _decision(
            key=key,
            profile=profile,
            version=newest_fresh_version,
            status="FAIL_CLOSED",
            reason="AMBIGUOUS_EQUAL_PRIORITY",
            claims=candidates,
            selected=None,
            stale_claim_ids=stale_ids,
            superseded_claim_ids=superseded_ids,
        )

    selected = min(top_current, key=lambda item: item["claim_id"])
    if len(fingerprints) == 1:
        reason = "CONSENSUS"
    elif len(top_claims) > len(top_current):
        reason = "LATEST_EQUAL_PRIORITY"
    else:
        reason = "SOURCE_PRIORITY"
    return _decision(
        key=key,
        profile=profile,
        version=newest_fresh_version,
        status="RESOLVED",
        reason=reason,
        claims=candidates,
        selected=selected,
        stale_claim_ids=stale_ids,
        superseded_claim_ids=superseded_ids,
    )


def reconcile_state_batch(
    claims: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Group claims by state key and reconcile each key independently."""

    raw_claims = list(claims)
    if not raw_claims:
        raise StateReconciliationError(
            "RECONCILE_CLAIMS_REQUIRED",
            "at least one claim is required",
        )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for claim in raw_claims:
        validated = validate_state_claim(claim, now=now)
        grouped.setdefault(validated["state_key"], []).append(claim)
    decisions = {
        key: reconcile_state_claims(group, now=now)
        for key, group in sorted(grouped.items())
    }
    resolved = sum(item["status"] == "RESOLVED" for item in decisions.values())
    result = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "RESOLVED" if resolved == len(decisions) else "FAIL_CLOSED",
        "state_key_count": len(decisions),
        "resolved_count": resolved,
        "fail_closed_count": len(decisions) - resolved,
        "decisions": decisions,
    }
    result["batch_sha256"] = _sha256(result)
    return result


def _claim_is_current(claim: Mapping[str, Any], now: datetime | None) -> bool:
    return not validate_state_claim(claim, now=now)["freshness"]["is_stale"]


@dataclass
class StateReconciler:
    """Small in-process projection guard with rollback and stale eviction."""

    projections: dict[str, dict[str, Any]] = field(default_factory=dict)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    received: int = 0
    applied: int = 0
    fail_closed: int = 0
    stale_evictions: int = 0
    conflict_invalidations: int = 0

    def get(
        self,
        state_key: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return only a still-current projection; evict stale projections."""

        key = _state_key(state_key)
        current = self.projections.get(key)
        if current is None:
            return None
        if not _claim_is_current(current, now):
            self.projections.pop(key, None)
            self.stale_evictions += 1
            return None
        return _copy(current)

    def ingest(
        self,
        claims: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        raw_claims = list(claims)
        self.received += len(raw_claims)
        batch = reconcile_state_batch(raw_claims, now=now)
        applied_keys: list[str] = []
        results: dict[str, dict[str, Any]] = {}

        for key, incoming in batch["decisions"].items():
            current = self.get(key, now=now)
            if incoming["status"] != "RESOLVED":
                # A contradiction at the current or a newer version makes an
                # older projection unsafe to read.  Retention is allowed only
                # when the rejected input is itself a temporal rollback.
                if (
                    current is not None
                    and isinstance(incoming["state_version"], int)
                    and incoming["state_version"] >= current["state_version"]
                ):
                    self.projections.pop(key, None)
                    self.conflict_invalidations += 1
                self.fail_closed += 1
                self.quarantined.append({
                    "state_key": key,
                    "reason": incoming["reason"],
                    "decision_sha256": incoming["decision_sha256"],
                })
                results[key] = incoming
                continue

            selected = incoming["selected_claim"]
            if current is not None and selected["state_version"] < current["state_version"]:
                rollback = _decision(
                    key=key,
                    profile=selected["authority_profile"],
                    version=selected["state_version"],
                    status="FAIL_CLOSED",
                    reason="TEMPORAL_ROLLBACK",
                    claims=[selected],
                    selected=None,
                    stale_claim_ids=[],
                    superseded_claim_ids=[selected["claim_id"]],
                )
                self.fail_closed += 1
                self.quarantined.append({
                    "state_key": key,
                    "reason": rollback["reason"],
                    "decision_sha256": rollback["decision_sha256"],
                })
                results[key] = rollback
                continue

            if current is not None and selected["state_version"] == current["state_version"]:
                combined = reconcile_state_claims([current, selected], now=now)
                if combined["status"] != "RESOLVED":
                    self.projections.pop(key, None)
                    self.conflict_invalidations += 1
                    self.fail_closed += 1
                    self.quarantined.append({
                        "state_key": key,
                        "reason": combined["reason"],
                        "decision_sha256": combined["decision_sha256"],
                    })
                    results[key] = combined
                    continue
                selected = combined["selected_claim"]
                incoming = combined

            self.projections[key] = _copy(selected)
            self.applied += 1
            applied_keys.append(key)
            results[key] = incoming

        return {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "status": "APPLIED" if len(applied_keys) == len(results) else "FAIL_CLOSED",
            "applied_state_keys": sorted(applied_keys),
            "decisions": results,
            "projection_keys": sorted(self.projections),
        }

    def report(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "applied": self.applied,
            "fail_closed": self.fail_closed,
            "stale_evictions": self.stale_evictions,
            "conflict_invalidations": self.conflict_invalidations,
            "projection_keys": sorted(self.projections),
            "quarantined": _copy(self.quarantined),
        }


def expected_unique_pair_count() -> int:
    """Number of unordered cross-source pairs across all profiles."""

    return len(AUTHORITY_PROFILES) * len(list(combinations(SOURCE_KINDS, 2)))


__all__ = [
    "AUTHORITY_PROFILES",
    "CLAIM_SCHEMA_VERSION",
    "CONFLICT_MATRIX",
    "FAIL_CLOSED_AMBIGUOUS",
    "FRESHNESS_SLO_SECONDS",
    "MAX_FUTURE_SKEW_SECONDS",
    "POLICY_VERSION",
    "RECONCILIATION_SCHEMA_VERSION",
    "SOURCE_KINDS",
    "StateReconciler",
    "StateReconciliationError",
    "build_conflict_matrix",
    "expected_unique_pair_count",
    "reconcile_state_batch",
    "reconcile_state_claims",
    "validate_state_claim",
]
