"""Fail-closed Spatial Context v1 projection for engine-authoritative telemetry.

The service converts read-only Godot telemetry into conservative proximity,
destination, visibility/occlusion, and seat context.  Exact engine facts stay
separate from deterministic derivations, stale snapshots never replace the
latest usable projection, and replay is deterministic.  It has no network,
model, Godot process, or .NET dependency.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


SPATIAL_CONTEXT_SCHEMA_VERSION = "lumina.spatial.context.v1"
GODOT_TELEMETRY_SCHEMA_VERSION = "1.0"
DEFAULT_FRESHNESS_SLO_SECONDS = 2.0
MAX_FUTURE_SKEW_SECONDS = 2.0
NEAR_DISTANCE_METERS = 1.5
AWARE_DISTANCE_METERS = 4.0
SEAT_TOKENS = ("seat", "chair", "sofa", "bench", "stool")


class SpatialContextError(ValueError):
    """Contract violation with a stable machine-readable error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpatialContextError(code, detail)
    return value


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SpatialContextError("SPATIAL_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpatialContextError("SPATIAL_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise SpatialContextError("SPATIAL_TIMESTAMP_TIMEZONE_REQUIRED", field_name)
    return parsed.astimezone(timezone.utc)


def _now_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise SpatialContextError("SPATIAL_NOW_TIMEZONE_REQUIRED", "now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _vector(value: Any, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SpatialContextError("SPATIAL_VECTOR_INVALID", field_name)
    output: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise SpatialContextError("SPATIAL_VECTOR_INVALID", f"{field_name}[{index}]")
        output.append(float(item))
    return output[0], output[1], output[2]


def _origin(record: Mapping[str, Any], field_name: str) -> tuple[float, float, float]:
    if record.get("available") is not True:
        raise SpatialContextError("SPATIAL_ACTOR_UNAVAILABLE", field_name)
    transform = _mapping(
        record.get("transform"),
        "SPATIAL_TRANSFORM_REQUIRED",
        f"{field_name}.transform",
    )
    return _vector(transform.get("origin"), f"{field_name}.transform.origin")


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _distance_band(distance_m: float) -> str:
    if distance_m <= NEAR_DISTANCE_METERS:
        return "NEAR"
    if distance_m <= AWARE_DISTANCE_METERS:
        return "AWARE"
    return "FAR"


def _location_id(snapshot: Mapping[str, Any], key: str) -> str:
    location = _mapping(
        snapshot.get(key),
        "SPATIAL_LOCATION_REQUIRED",
        key,
    )
    value = location.get("id")
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return value.strip()


def _authority(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    authority = _mapping(
        snapshot.get("authority"),
        "SPATIAL_AUTHORITY_REQUIRED",
        "authority",
    )
    for field_name in ("geometry", "visibility", "navigation"):
        if authority.get(field_name) != "godot_engine":
            raise SpatialContextError(
                "SPATIAL_ENGINE_AUTHORITY_REQUIRED",
                f"authority.{field_name}",
            )
    if authority.get("vlm_may_override_geometry") is not False:
        raise SpatialContextError(
            "SPATIAL_VLM_GEOMETRY_OVERRIDE_FORBIDDEN",
            "authority.vlm_may_override_geometry",
        )
    return {
        "geometry": "godot_engine",
        "visibility": "godot_engine",
        "navigation": "godot_engine",
        "vlm_may_override_geometry": False,
    }


def _destination(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    navigation = _mapping(
        snapshot.get("navigation"),
        "SPATIAL_NAVIGATION_REQUIRED",
        "navigation",
    )
    agent = navigation.get("agent")
    if not isinstance(agent, Mapping) or agent.get("available") is not True:
        return {
            "status": "UNKNOWN",
            "configured": bool(navigation.get("configured")),
            "source": "godot_engine",
        }
    navigation_finished = agent.get("navigation_finished")
    target_reachable = agent.get("target_reachable")
    if not isinstance(navigation_finished, bool) or not isinstance(target_reachable, bool):
        raise SpatialContextError(
            "SPATIAL_NAVIGATION_STATE_INVALID",
            "navigation.agent completion/reachability",
        )
    target = _vector(agent.get("target_position"), "navigation.agent.target_position")
    path_length = agent.get("path_length")
    if isinstance(path_length, bool) or not isinstance(path_length, (int, float)) or not math.isfinite(float(path_length)):
        raise SpatialContextError("SPATIAL_PATH_LENGTH_INVALID", "navigation.agent.path_length")
    if navigation_finished:
        status = "ARRIVED"
    elif not target_reachable:
        status = "UNREACHABLE"
    else:
        status = "EN_ROUTE"
    return {
        "status": status,
        "configured": True,
        "target_reachable": target_reachable,
        "navigation_finished": navigation_finished,
        "target_position": list(target),
        "path_length_m": max(0.0, float(path_length)),
        "source": "godot_engine",
    }


def _object_role(item: Mapping[str, Any]) -> dict[str, Any]:
    explicit = item.get("spatial_role")
    if isinstance(explicit, str) and explicit.strip():
        return {
            "value": explicit.strip().lower(),
            "assertion_type": "FACT",
            "confidence": 1.0,
            "source": "godot_engine_metadata",
        }
    name = str(item.get("name") or item.get("id") or "").strip().lower()
    if any(token in name for token in SEAT_TOKENS):
        return {
            "value": "seat",
            "assertion_type": "DERIVED",
            "confidence": 0.70,
            "source": "stable_id_keyword",
        }
    return {
        "value": "object",
        "assertion_type": "UNKNOWN",
        "confidence": 0.0,
        "source": "none",
    }


def _visibility(item: Mapping[str, Any]) -> dict[str, Any]:
    visibility = item.get("visibility")
    if not isinstance(visibility, Mapping):
        return {
            "in_camera_frustum": None,
            "occlusion_status": "UNKNOWN",
            "occlusion_checked": False,
            "source": "godot_engine",
        }
    in_frustum = visibility.get("in_camera_frustum")
    if in_frustum is not None and not isinstance(in_frustum, bool):
        raise SpatialContextError("SPATIAL_VISIBILITY_INVALID", "in_camera_frustum")
    occlusion_checked = visibility.get("occlusion_checked") is True
    if occlusion_checked:
        occluded = visibility.get("occluded")
        if not isinstance(occluded, bool):
            raise SpatialContextError(
                "SPATIAL_OCCLUSION_RESULT_REQUIRED",
                "occlusion_checked=true requires boolean occluded",
            )
        occlusion_status = "OCCLUDED" if occluded else "CLEAR"
    else:
        # Never turn an unchecked ray-cast into a false clear-path claim.
        occlusion_status = "UNKNOWN"
    return {
        "visible_in_tree": visibility.get("visible_in_tree") is True,
        "in_camera_frustum": in_frustum,
        "occlusion_status": occlusion_status,
        "occlusion_checked": occlusion_checked,
        "source": "godot_engine",
    }


def build_spatial_context(
    snapshot: Mapping[str, Any],
    *,
    now: datetime | None = None,
    freshness_slo_seconds: float = DEFAULT_FRESHNESS_SLO_SECONDS,
) -> dict[str, Any]:
    """Validate one Godot snapshot and create a conservative context projection."""

    source = _mapping(
        snapshot,
        "SPATIAL_SNAPSHOT_OBJECT_REQUIRED",
        "snapshot must be an object",
    )
    if source.get("schema_version") != GODOT_TELEMETRY_SCHEMA_VERSION:
        raise SpatialContextError("SPATIAL_UPSTREAM_SCHEMA_MISMATCH", "schema_version")
    if source.get("source") != "godot_engine":
        raise SpatialContextError("SPATIAL_ENGINE_SOURCE_REQUIRED", "source")
    if source.get("valid") is not True or source.get("errors") not in ([], ()):
        raise SpatialContextError("SPATIAL_UPSTREAM_INVALID", "valid/errors")
    if (
        isinstance(freshness_slo_seconds, bool)
        or not isinstance(freshness_slo_seconds, (int, float))
        or freshness_slo_seconds <= 0
    ):
        raise SpatialContextError("SPATIAL_FRESHNESS_SLO_INVALID", "must be positive")

    authority = _authority(source)
    captured = _utc(source.get("captured_at_utc"), "captured_at_utc")
    current = _now_utc(now)
    age_seconds = (current - captured).total_seconds()
    if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise SpatialContextError("SPATIAL_SNAPSHOT_FROM_FUTURE", "captured_at_utc")
    age_seconds = max(0.0, age_seconds)
    is_stale = age_seconds > float(freshness_slo_seconds)

    player = _origin(
        _mapping(source.get("player"), "SPATIAL_PLAYER_REQUIRED", "player"),
        "player",
    )
    lumina = _origin(
        _mapping(source.get("lumina"), "SPATIAL_LUMINA_REQUIRED", "lumina"),
        "lumina",
    )
    visitor_distance = _distance(player, lumina)
    destination = _destination(source)

    object_facts: list[dict[str, Any]] = []
    seat_candidates: list[dict[str, Any]] = []
    interactables = source.get("interactables")
    if not isinstance(interactables, list):
        raise SpatialContextError("SPATIAL_INTERACTABLES_REQUIRED", "interactables")
    for index, raw_item in enumerate(interactables):
        item = _mapping(
            raw_item,
            "SPATIAL_INTERACTABLE_INVALID",
            f"interactables[{index}]",
        )
        if item.get("available") is not True:
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise SpatialContextError("SPATIAL_OBJECT_ID_REQUIRED", f"interactables[{index}].id")
        item_origin = _origin(item, f"interactables[{index}]")
        role = _object_role(item)
        visibility = _visibility(item)
        lumina_distance = _distance(lumina, item_origin)
        fact = {
            "object_id": item_id.strip(),
            "name": str(item.get("name") or item_id).strip(),
            "position": list(item_origin),
            "distance_from_lumina_m": lumina_distance,
            "proximity_band": _distance_band(lumina_distance),
            "role": role,
            "visibility": visibility,
            "source": "godot_engine",
        }
        object_facts.append(fact)
        if role["value"] == "seat":
            seat_candidates.append({
                "object_id": fact["object_id"],
                "distance_from_lumina_m": lumina_distance,
                "proximity_band": fact["proximity_band"],
                "role_assertion_type": role["assertion_type"],
                "role_confidence": role["confidence"],
                "actionable": role["assertion_type"] == "FACT" and not is_stale,
            })
    object_facts.sort(key=lambda item: item["object_id"])
    seat_candidates.sort(key=lambda item: (item["distance_from_lumina_m"], item["object_id"]))

    snapshot_hash = _canonical_sha256(source)
    context = {
        "schema_version": SPATIAL_CONTEXT_SCHEMA_VERSION,
        "context_id": f"spatial-{snapshot_hash[:20]}",
        "source_snapshot_sha256": snapshot_hash,
        "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
        "authority": authority,
        "freshness": {
            "age_seconds": age_seconds,
            "slo_seconds": float(freshness_slo_seconds),
            "status": "STALE" if is_stale else "FRESH",
            "is_stale": is_stale,
        },
        "decision_eligible": not is_stale,
        "facts": {
            "room_id": _location_id(source, "room"),
            "zone_id": _location_id(source, "zone"),
            "player_position": list(player),
            "lumina_position": list(lumina),
            "destination": destination,
            "objects": object_facts,
        },
        "derived": {
            "visitor_proximity": {
                "distance_m": visitor_distance,
                "band": _distance_band(visitor_distance),
                "assertion_type": "DERIVED_FROM_ENGINE_FACTS",
                "source_fields": ["player.transform.origin", "lumina.transform.origin"],
            },
            "seat_candidates": seat_candidates,
        },
        "claims": {
            "occlusion_unknown_count": sum(
                item["visibility"]["occlusion_status"] == "UNKNOWN" for item in object_facts
            ),
            "unchecked_occlusion_claimed_clear_count": sum(
                item["visibility"]["occlusion_status"] == "CLEAR"
                and item["visibility"]["occlusion_checked"] is False
                for item in object_facts
            ),
        },
    }
    return context


def _percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


@dataclass
class SpatialContextService:
    """In-process projection boundary; P2-02 remains the upstream event owner."""

    freshness_slo_seconds: float = DEFAULT_FRESHNESS_SLO_SECONDS
    latest_context: dict[str, Any] | None = None
    accepted_contexts: list[dict[str, Any]] = field(default_factory=list)
    freshness_samples_seconds: list[float] = field(default_factory=list)
    quarantine: list[dict[str, Any]] = field(default_factory=list)
    received: int = 0

    def ingest(self, snapshot: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        context = build_spatial_context(
            snapshot,
            now=now,
            freshness_slo_seconds=self.freshness_slo_seconds,
        )
        self.received += 1
        age_seconds = float(context["freshness"]["age_seconds"])
        self.freshness_samples_seconds.append(age_seconds)
        if context["freshness"]["is_stale"]:
            result = {
                "status": "QUARANTINED",
                "reason": "STALE_SPATIAL_CONTEXT",
                "context_id": context["context_id"],
                "projection_applied": False,
                "decision_eligible": False,
                "age_seconds": age_seconds,
            }
            self.quarantine.append(_copy(result))
            return result
        self.latest_context = _copy(context)
        self.accepted_contexts.append(_copy(context))
        return {
            "status": "APPLIED",
            "reason": "FRESH_ENGINE_AUTHORITY",
            "context_id": context["context_id"],
            "projection_applied": True,
            "decision_eligible": True,
            "age_seconds": age_seconds,
        }

    @property
    def projection_sha256(self) -> str | None:
        if self.latest_context is None:
            return None
        return _canonical_sha256(self.latest_context)

    def report(self) -> dict[str, Any]:
        accepted = len(self.accepted_contexts)
        p95 = _percentile_nearest_rank(self.freshness_samples_seconds, 0.95)
        object_count = sum(len(item["facts"]["objects"]) for item in self.accepted_contexts)
        event_categories = Counter()
        for item in self.accepted_contexts:
            event_categories["proximity"] += 1
            event_categories["destination"] += 1
            event_categories["visibility_occlusion"] += int(bool(item["facts"]["objects"]))
            event_categories["seat"] += int(bool(item["derived"]["seat_candidates"]))
        return {
            "received": self.received,
            "accepted": accepted,
            "quarantined": len(self.quarantine),
            "acceptance_rate": accepted / self.received if self.received else 0.0,
            "freshness_p95_seconds": p95,
            "freshness_slo_seconds": float(self.freshness_slo_seconds),
            "freshness_slo_met": p95 is not None and p95 <= self.freshness_slo_seconds,
            "object_facts_projected": object_count,
            "event_category_coverage": dict(sorted(event_categories.items())),
            "projection_sha256": self.projection_sha256,
        }


def replay_spatial_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    freshness_slo_seconds: float = DEFAULT_FRESHNESS_SLO_SECONDS,
) -> dict[str, Any]:
    """Replay recorded snapshots in input order and return deterministic evidence."""

    service = SpatialContextService(freshness_slo_seconds=freshness_slo_seconds)
    statuses: list[str] = []
    context_ids: list[str] = []
    for snapshot in snapshots:
        result = service.ingest(snapshot, now=now)
        statuses.append(result["status"])
        context_ids.append(result["context_id"])
    report = service.report()
    report.update({
        "statuses": statuses,
        "context_ids_sha256": _canonical_sha256(context_ids),
        "deterministic_replay_sha256": _canonical_sha256({
            "statuses": statuses,
            "accepted_contexts": service.accepted_contexts,
            "quarantine": service.quarantine,
        }),
    })
    return report
