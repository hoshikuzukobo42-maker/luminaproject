"""Deterministic, engine-neutral geometry and grid navigation primitives.

The module consumes the validated envelopes in
``portable_navigation_contract_v1``.  It deliberately has no scene graph,
native object identifier, transport, or model dependency.  Semantic vision is
responsible only for a normalized image bounding box; this module grounds that
box with metric depth, builds a conservative occupancy grid, and plans a path.

Safety rules are intentionally fail closed:

* absent depth never becomes an estimated target;
* similarly supported depth surfaces are reported as ambiguous;
* unknown and out-of-bounds grid cells are not traversable;
* footprint and requested clearance are represented by obstacle inflation;
* simplified paths are returned only after segment-level collision checking.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import heapq
import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .portable_navigation_contract_v1 import (
    COORDINATE_CONVENTION,
    GROUND_RESULT_SCHEMA,
    MAX_GROUND_SURFACE_POINTS,
    MAP_RESULT_SCHEMA,
    PortableNavigationContractError,
    validate_capture_result,
    validate_ground_request,
    validate_ground_result,
    validate_map_request,
    validate_map_result,
)


FREE_VALUE = 0
UNKNOWN_VALUE = 127
OCCUPIED_VALUE = 255
OCCUPIED_THRESHOLD = 200
GROUNDING_POLICY_REVISION = "rgbd_bbox_surface_partition_occluder_bilateral_role_continuity_v7"
_EPSILON = 1e-9
_MAX_CORE_GRID_CELLS = 16 * 1024 * 1024
_MIN_INDEPENDENT_DEPTH_SAMPLES = 3
_ARRIVAL_CONTINUITY_UNIQUE_MARGIN_M = 0.08
_WIDE_LAYER_CLUSTER_GAP_M = 0.20
_WIDE_LAYER_CLUSTER_GAP_RATIO = 0.06
_WIDE_WALKABLE_RUNNER_MAX_RATIO = 0.75


class PortableNavigationCoreError(ValueError):
    """A deterministic failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _error(code: str, detail: str) -> PortableNavigationCoreError:
    return PortableNavigationCoreError(code, detail)


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error("PORTABLE_CORE_NUMBER_INVALID", field)
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise _error("PORTABLE_CORE_NUMBER_INVALID", field)
    return result


def _vector(value: Any, length: int, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise _error("PORTABLE_CORE_VECTOR_INVALID", field)
    return [_number(component, f"{field}[{index}]") for index, component in enumerate(value)]


def _canonical_digest(parts: Any) -> str:
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _opaque_id(prefix: str, parts: Any) -> str:
    return f"{prefix}:{_canonical_digest(parts)}"


def _rotate_xyzw(quaternion: Sequence[float], vector: Sequence[float]) -> list[float]:
    """Rotate ``vector`` by a normalized xyzw quaternion."""

    qx, qy, qz, qw = quaternion
    vx, vy, vz = vector
    # q * v * conjugate(q), expanded to avoid matrix dependencies.
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return [
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    ]


def _inverse_rotate_xyzw(quaternion: Sequence[float], vector: Sequence[float]) -> list[float]:
    return _rotate_xyzw([-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]], vector)


def _inside_bbox(uv: Sequence[float], bbox: Sequence[float]) -> bool:
    return bbox[0] <= uv[0] <= bbox[2] and bbox[1] <= uv[1] <= bbox[3]


def _inset_bbox(bbox: Sequence[float], ratio: float) -> list[float]:
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return [
        bbox[0] + width * ratio,
        bbox[1] + height * ratio,
        bbox[2] - width * ratio,
        bbox[3] - height * ratio,
    ]


def _depth_to_point(
    *,
    uv_norm: Sequence[float],
    measurement_m: float,
    measurement_model: str,
    rgb_width: int,
    rgb_height: int,
    camera: Mapping[str, Any],
    confidence: float,
    source_index: int,
    surface_id: str | None = None,
    surface_role: str | None = None,
) -> dict[str, Any]:
    intrinsics = camera["intrinsics"]
    # Normalized coordinates use image edges as 0 and 1.  Consequently the
    # centre of a dense pixel is (column + .5) / width and maps back to the
    # camera convention with u_px = uv_norm * rgb_width.
    u_px = float(uv_norm[0]) * rgb_width
    v_px = float(uv_norm[1]) * rgb_height
    ray = [
        (u_px - intrinsics["cx_px"]) / intrinsics["fx_px"],
        (v_px - intrinsics["cy_px"]) / intrinsics["fy_px"],
        1.0,
    ]
    if measurement_model == "optical_axis_z_m":
        optical = [ray[0] * measurement_m, ray[1] * measurement_m, measurement_m]
    elif measurement_model == "ray_range_m":
        ray_norm = math.sqrt(sum(component * component for component in ray))
        optical = [component * measurement_m / ray_norm for component in ray]
    else:  # The contract normally catches this; retain a local fail-closed guard.
        raise _error("PORTABLE_CORE_DEPTH_MODEL_UNSUPPORTED", measurement_model)
    rotated = _rotate_xyzw(camera["orientation_xyzw"], optical)
    world = [camera["position_m"][index] + rotated[index] for index in range(3)]
    result = {
        "uv_norm": [float(uv_norm[0]), float(uv_norm[1])],
        "position_m": world,
        "optical_position_m": optical,
        "range_m": math.sqrt(sum(component * component for component in optical)),
        "forward_depth_m": optical[2],
        "measurement_m": measurement_m,
        "measurement_model": measurement_model,
        "confidence": confidence,
        "source_index": source_index,
    }
    if surface_id is not None:
        result["surface_id"] = surface_id
    if surface_role is not None:
        result["surface_role"] = surface_role
    return result


def project_depth_samples(
    capture_result: Mapping[str, Any],
    *,
    bbox_norm: Sequence[float] | None = None,
    min_confidence: float = 0.0,
    min_range_m: float = 0.001,
    max_range_m: float = 10_000.0,
) -> list[dict[str, Any]]:
    """Project dense or sparse registered depth into canonical 3-D points.

    Dense ``optical_axis_z_m`` and ``ray_range_m`` planes are both supported.
    Sparse input is required by the contract to use ray range.  Invalid,
    non-positive, out-of-range, or low-confidence samples are omitted.
    """

    capture = validate_capture_result(capture_result)
    depth = capture["depth"]
    if depth is None:
        return []
    confidence_floor = _number(min_confidence, "min_confidence", minimum=0.0)
    if confidence_floor > 1.0:
        raise _error("PORTABLE_CORE_NUMBER_INVALID", "min_confidence")
    minimum = _number(min_range_m, "min_range_m", minimum=0.0)
    maximum = _number(max_range_m, "max_range_m", minimum=minimum)
    if maximum < minimum:
        raise _error("PORTABLE_CORE_RANGE_INVALID", "max_range_m")
    if bbox_norm is None:
        bbox = [0.0, 0.0, 1.0, 1.0]
    else:
        bbox = _vector(bbox_norm, 4, "bbox_norm")
        if (
            min(bbox) < 0.0
            or max(bbox) > 1.0
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            raise _error("PORTABLE_CORE_BBOX_INVALID", "bbox_norm")

    rgb_width = capture["rgb"]["width_px"]
    rgb_height = capture["rgb"]["height_px"]
    camera = capture["camera"]
    projected: list[dict[str, Any]] = []

    if depth["representation"] == "sparse_rays":
        for index, sample in enumerate(depth["samples"]):
            uv = sample["uv_norm"]
            distance = sample["distance_m"]
            confidence = sample["confidence"]
            if confidence < confidence_floor or not minimum <= distance <= maximum or not _inside_bbox(uv, bbox):
                continue
            projected.append(
                _depth_to_point(
                    uv_norm=uv,
                    measurement_m=distance,
                    measurement_model="ray_range_m",
                    rgb_width=rgb_width,
                    rgb_height=rgb_height,
                    camera=camera,
                    confidence=confidence,
                    source_index=index,
                    surface_id=sample.get("surface_id"),
                    surface_role=sample.get("surface_role"),
                )
            )
        return projected

    try:
        raw = base64.b64decode(depth["data_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:  # pragma: no cover - contract normally catches it
        raise _error("PORTABLE_CORE_DEPTH_DATA_INVALID", "depth.data_base64") from exc
    encoding = depth["encoding"]
    sample_format = "<f" if encoding == "f32_le_m" else "<H"
    width = depth["width_px"]
    height = depth["height_px"]
    invalid = depth["invalid_value"]
    for index, unpacked in enumerate(struct.iter_unpack(sample_format, raw)):
        measurement = float(unpacked[0])
        if encoding == "f32_le_m":
            if not math.isfinite(measurement) or (invalid == "zero" and measurement == 0.0):
                continue
        elif int(unpacked[0]) == invalid:
            continue
        if encoding == "u16_le_mm":
            measurement /= 1000.0
        if measurement <= 0.0:
            continue
        column = index % width
        row = index // width
        uv = [(column + 0.5) / width, (row + 0.5) / height]
        if not _inside_bbox(uv, bbox):
            continue
        point = _depth_to_point(
            uv_norm=uv,
            measurement_m=measurement,
            measurement_model=depth["measurement_model"],
            rgb_width=rgb_width,
            rgb_height=rgb_height,
            camera=camera,
            confidence=1.0,
            source_index=index,
        )
        if minimum <= point["range_m"] <= maximum:
            projected.append(point)
    return projected


# Explicit name retained for callers that describe the operation rather than
# the contract representation.
project_depth_to_3d = project_depth_samples


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _depth_clusters(
    points: Sequence[dict[str, Any]],
    absolute_gap: float,
    relative_gap: float,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(points, key=lambda point: (point["forward_depth_m"], point["source_index"]))
    clusters: list[list[dict[str, Any]]] = []
    for point in ordered:
        if not clusters:
            clusters.append([point])
            continue
        previous_depth = clusters[-1][-1]["forward_depth_m"]
        allowed_gap = max(
            absolute_gap,
            min(previous_depth, point["forward_depth_m"]) * relative_gap,
        )
        if point["forward_depth_m"] - previous_depth <= allowed_gap + _EPSILON:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    return clusters


def _arrival_continuity_surface_match(
    clusters: Sequence[Sequence[dict[str, Any]]],
    reference_points: Sequence[Sequence[float]],
    maximum_distance_m: float,
    reference_surface_id: str | None = None,
    reference_surface_role: str | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]] | None,
    float,
] | None:
    """Select one fresh depth surface only when a metric prior is unique.

    Candidate distance is the third-nearest independently registered ray to
    the certified reference surface.  A single coincident sample can therefore
    never establish continuity.  Competing eligible depth surfaces must be
    separated by a fixed margin that callers cannot relax.
    """

    candidates: list[
        tuple[
            float,
            int,
            float,
            int,
            list[dict[str, Any]],
            list[float],
        ]
    ] = []
    for cluster in clusters:
        matched: list[tuple[float, dict[str, Any]]] = []
        for point in cluster:
            if (
                reference_surface_id is not None
                and point.get("surface_id") != reference_surface_id
            ):
                continue
            if (
                reference_surface_role is not None
                and point.get("surface_role") != reference_surface_role
            ):
                continue
            position = point["position_m"]
            nearest = min(math.dist(position, reference) for reference in reference_points)
            if nearest <= maximum_distance_m + _EPSILON:
                matched.append((nearest, point))
        independent = {
            (float(point["uv_norm"][0]), float(point["uv_norm"][1]))
            for _, point in matched
        }
        if len(independent) < _MIN_INDEPENDENT_DEPTH_SAMPLES:
            continue
        matched.sort(key=lambda item: (item[0], item[1]["source_index"]))
        distances = [item[0] for item in matched]
        matched_points = [item[1] for item in matched]
        evidence_distance = distances[_MIN_INDEPENDENT_DEPTH_SAMPLES - 1]
        candidates.append(
            (
                evidence_distance,
                -len(independent),
                _median([point["forward_depth_m"] for point in matched_points]),
                matched_points[0]["source_index"],
                matched_points,
                distances,
            )
        )
    candidates.sort(key=lambda item: item[:4])
    if not candidates:
        return None
    if (
        len(candidates) > 1
        and candidates[1][0]
        < candidates[0][0] + _ARRIVAL_CONTINUITY_UNIQUE_MARGIN_M - _EPSILON
    ):
        return None
    winning = candidates[0]
    runner = candidates[1][4] if len(candidates) > 1 else None
    match_uncertainty = max(0.01, _median(winning[5]))
    return winning[4], runner, match_uncertainty


def _bbox_intersection(first: Sequence[float], second: Sequence[float]) -> list[float] | None:
    result = [
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    ]
    return result if result[2] > result[0] and result[3] > result[1] else None


def _uniform_surface_id(points: Sequence[Mapping[str, Any]]) -> str | None:
    identifiers = {point.get("surface_id") for point in points}
    if len(identifiers) != 1:
        return None
    identifier = next(iter(identifiers))
    return identifier if isinstance(identifier, str) else None


def _uniform_surface_role(points: Sequence[Mapping[str, Any]]) -> str | None:
    roles = {point.get("surface_role") for point in points}
    if len(roles) != 1:
        return None
    role = next(iter(roles))
    return role if isinstance(role, str) else None


def _surface_identity_partitions(
    points: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]] | None:
    """Partition rays before depth clustering without inventing identity.

    A depth band can contain unrelated edge rays at nearly the same range.  If
    those rays are clustered first, their different physical ids contaminate
    an otherwise coherent surface and can also manufacture a false bilateral
    layer from two one-sided objects.  Identified rays therefore cluster only
    with the exact same opaque adapter id.  A fully unidentified capture stays
    in one legacy partition.  Partial identity coverage is not evidence: it
    returns ``None`` so the caller can fail closed rather than ignoring an
    unidentified competitor.
    """

    identified = [isinstance(point.get("surface_id"), str) for point in points]
    if any(identified) and not all(identified):
        return None
    if not any(identified):
        return [list(points)]
    partitions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for point in points:
        surface_id = point.get("surface_id")
        key = ("identified", surface_id) if isinstance(surface_id, str) else ("unidentified", "")
        partitions.setdefault(key, []).append(point)
    return list(partitions.values())


def _wide_occlusion_surface_rescue(
    capture: Mapping[str, Any],
    bbox: Sequence[float],
    occluder_bboxes: Sequence[Sequence[float]],
    *,
    absolute_gap: float,
    relative_gap: float,
    ambiguity: float,
    confidence_floor: float,
    expected_surface_role: str | None,
) -> tuple[bool, tuple[list[dict[str, Any]], float, float, float] | None]:
    """Resolve a wide semantic box only with explicit target-free occluders.

    A foreground object's box can split a large visible target into two lobes.
    This rescue is deliberately unavailable without that independent image
    evidence.  It accepts only a coherent depth surface spanning both halves
    of the target's major axis, with at least three registered rays per half.
    Multiple depth layers remain ambiguous unless every contributing ray has
    the same opaque physical surface id; in that case the nearest exposed
    layer is the navigation and continuity anchor.  The only exception is a
    caller-declared navigation obstacle competing exclusively with explicitly
    tagged walkable ground.  This is adapter geometry metadata, never a model
    label or engine object name, and is intentionally unavailable for unknown
    or structural surfaces.
    """

    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    minor = min(width, height)
    if minor <= _EPSILON or max(width, height) / minor < 3.0 or not occluder_bboxes:
        return False, None
    major_axis = 0 if width >= height else 1
    major_extent = width if major_axis == 0 else height
    major_start = bbox[major_axis]
    major_end = bbox[major_axis + 2]
    major_centre = (major_start + major_end) / 2.0

    exact_points = project_depth_samples(
        capture,
        bbox_norm=bbox,
        min_confidence=confidence_floor,
    )
    # Identity attestation is capture-wide within the semantic region.  Never
    # let a partially annotated frame use identified rays while silently
    # discarding a possible unidentified competing surface.
    exact_identity_partitions = _surface_identity_partitions(exact_points)
    if exact_identity_partitions is None:
        return True, None
    overlapping: list[list[float]] = []
    foreground_depths: list[float] = []
    for raw_box in occluder_bboxes:
        overlap = _bbox_intersection(bbox, raw_box)
        if overlap is None:
            continue
        # A bounded central occluder, not another scene-wide box, is required.
        overlap_span = overlap[major_axis + 2] - overlap[major_axis]
        if (
            overlap_span / major_extent > 0.40 + _EPSILON
            or not overlap[major_axis] <= major_centre <= overlap[major_axis + 2]
        ):
            continue
        inside = [point for point in exact_points if _inside_bbox(point["uv_norm"], overlap)]
        box_foreground_depths: list[float] = []
        inside_partitions = _surface_identity_partitions(inside)
        if inside_partitions is None:
            return True, None
        for identity_partition in inside_partitions:
            for cluster in _depth_clusters(identity_partition, absolute_gap, relative_gap):
                if len({tuple(point["uv_norm"]) for point in cluster}) < _MIN_INDEPENDENT_DEPTH_SAMPLES:
                    continue
                cluster_span = (
                    max(point["uv_norm"][major_axis] for point in cluster)
                    - min(point["uv_norm"][major_axis] for point in cluster)
                ) / major_extent
                if cluster_span <= 0.30 + _EPSILON:
                    box_foreground_depths.append(
                        _median([point["forward_depth_m"] for point in cluster])
                    )
        # An unrelated detector box cannot borrow foreground evidence from a
        # different box and erase depth support from this target region.
        if box_foreground_depths:
            overlapping.append(overlap)
            foreground_depths.extend(box_foreground_depths)
    if not foreground_depths or not overlapping:
        return False, None

    outside_points = [
        point
        for point in exact_points
        if not any(_inside_bbox(point["uv_norm"], box) for box in overlapping)
    ]
    centre = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
    half_diagonal = max(
        _EPSILON,
        math.hypot(width / 2.0, height / 2.0),
    )

    def visual_support(cluster: Sequence[Mapping[str, Any]]) -> float:
        total = 0.0
        for point in cluster:
            centrality = 1.0 - math.hypot(
                point["uv_norm"][0] - centre[0],
                point["uv_norm"][1] - centre[1],
            ) / half_diagonal
            total += point["confidence"] * max(0.25, centrality)
        return total

    eligible: list[tuple[float, float, list[dict[str, Any]], float]] = []
    # Wide furniture commonly exposes a front and top/back face separated by a
    # smaller step than the generic scene-depth clustering threshold.  Keep
    # this fixed and conservative so callers cannot relax identity grouping.
    # Identity is the stronger partition boundary.  Depth-first clustering can
    # mix a valid full-width surface with unrelated left/right edge geometry at
    # the same range; it can also make two different one-sided bodies look like
    # one bilateral candidate.  Unknown-id rays retain their own shared bucket
    # so adapters without this attestation keep the legacy fail-closed path.
    depth_layers: list[list[dict[str, Any]]] = []
    outside_partitions = _surface_identity_partitions(outside_points)
    if outside_partitions is None:
        return True, None
    for identity_partition in outside_partitions:
        depth_layers.extend(_depth_clusters(
            identity_partition,
            _WIDE_LAYER_CLUSTER_GAP_M,
            _WIDE_LAYER_CLUSTER_GAP_RATIO,
        ))
    for cluster in depth_layers:
        unique = len({tuple(point["uv_norm"]) for point in cluster})
        if unique < 2 * _MIN_INDEPENDENT_DEPTH_SAMPLES:
            continue
        major_values = [point["uv_norm"][major_axis] for point in cluster]
        span_ratio = (max(major_values) - min(major_values)) / major_extent
        left_count = sum(value < major_centre - _EPSILON for value in major_values)
        right_count = sum(value > major_centre + _EPSILON for value in major_values)
        if (
            span_ratio < 0.70 - _EPSILON
            or left_count < _MIN_INDEPENDENT_DEPTH_SAMPLES
            or right_count < _MIN_INDEPENDENT_DEPTH_SAMPLES
        ):
            continue
        depths = [point["forward_depth_m"] for point in cluster]
        depth_median = _median(depths)
        depth_mad = _median([abs(depth - depth_median) for depth in depths])
        if depth_mad > max(0.10, depth_median * 0.03) + _EPSILON:
            continue
        if max(depths) - min(depths) > max(0.20, depth_median * 0.08) + _EPSILON:
            continue
        if not any(depth_median > foreground + max(0.15, foreground * 0.05) for foreground in foreground_depths):
            continue
        score = visual_support(cluster) * span_ratio
        eligible.append((score, depth_median, cluster, depth_mad))
    eligible.sort(key=lambda item: (-item[0], item[1], item[2][0]["source_index"]))
    if not eligible:
        return True, None
    winning_score, _, winning, depth_mad = eligible[0]
    same_physical_surface_layers = False
    competing_scores: list[float] = []
    if len(eligible) > 1:
        surface_ids = [_uniform_surface_id(entry[2]) for entry in eligible]
        shared_surface_id = surface_ids[0]
        if (
            shared_surface_id is not None
            and all(identifier == shared_surface_id for identifier in surface_ids)
        ):
            same_physical_surface_layers = True
            winning_score, _, winning, depth_mad = min(
                eligible,
                key=lambda item: (item[1], item[2][0]["source_index"]),
            )
        elif expected_surface_role == "navigation_obstacle":
            # All geometric eligibility checks above run before role
            # classification.  Therefore role metadata cannot manufacture a
            # bilateral candidate or bypass occluder/depth-quality evidence.
            surface_roles = [_uniform_surface_role(entry[2]) for entry in eligible]
            if any(role is None for role in surface_roles):
                return True, None
            obstacle_entries = [
                entry
                for entry, role in zip(eligible, surface_roles)
                if role == "navigation_obstacle"
            ]
            other_roles = [
                role for role in surface_roles if role != "navigation_obstacle"
            ]
            obstacle_ids = [_uniform_surface_id(entry[2]) for entry in obstacle_entries]
            shared_obstacle_id = obstacle_ids[0] if obstacle_ids else None
            if (
                shared_obstacle_id is None
                or not all(identifier == shared_obstacle_id for identifier in obstacle_ids)
                or any(role != "walkable_ground" for role in other_roles)
            ):
                return True, None
            winning_score, _, winning, depth_mad = min(
                obstacle_entries,
                key=lambda item: (item[1], item[2][0]["source_index"]),
            )
            walkable_scores = [
                entry[0]
                for entry, role in zip(eligible, surface_roles)
                if role == "walkable_ground"
            ]
            strongest_walkable = max(walkable_scores) if walkable_scores else 0.0
            # Equality at the threshold is rejected.  A merely comparable
            # ground layer cannot be erased by its role declaration.
            if strongest_walkable >= winning_score * _WIDE_WALKABLE_RUNNER_MAX_RATIO - _EPSILON:
                return True, None
            competing_scores = walkable_scores
        else:
            # Even a weak second layer is a distinct physical candidate.
            # Support dominance cannot bind it to this wide semantic target;
            # only one shared, non-null opaque surface identity can do that.
            return True, None

    position = [_median([point["position_m"][axis] for point in winning]) for axis in range(3)]
    residuals = [
        math.sqrt(sum((point["position_m"][axis] - position[axis]) ** 2 for axis in range(3)))
        for point in winning
    ]
    left_depth = _median([
        point["forward_depth_m"]
        for point in winning
        if point["uv_norm"][major_axis] < major_centre
    ])
    right_depth = _median([
        point["forward_depth_m"]
        for point in winning
        if point["uv_norm"][major_axis] > major_centre
    ])
    uncertainty = max(
        0.01,
        depth_mad,
        abs(left_depth - right_depth),
        _median(residuals) / math.sqrt(len(winning)),
    )
    # The ordinary ambiguity rule is pairwise against the strongest competing
    # surface.  Preserve that meaning in the confidence denominator instead of
    # allowing many individually weak background layers to swamp a valid one.
    runner_scores = competing_scores or [
        score for score, _, cluster, _ in eligible if cluster is not winning
    ]
    total_score = (
        winning_score
        if same_physical_surface_layers
        else winning_score + (max(runner_scores) if runner_scores else 0.0)
    )
    return True, (winning, winning_score, total_score, uncertainty)


def _ground_result_base(request: Mapping[str, Any], grounded_unix: float) -> dict[str, Any]:
    detection = request["detection"]
    return {
        "schema_version": GROUND_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "environment_id": request["environment_id"],
        "session_id": request["session_id"],
        "source_frame_id": request["source_frame_id"],
        "map_id": request["map_id"],
        "detection_id": detection["detection_id"],
        "grounded_unix": grounded_unix,
        "label": detection["label"],
        "coordinate_convention": COORDINATE_CONVENTION,
    }


def _bounded_surface_positions(
    points: Sequence[Mapping[str, Any]],
) -> list[list[float]]:
    """Keep a deterministic, geometry-preserving sample of one depth surface."""

    ordered = sorted(points, key=lambda point: int(point["source_index"]))
    if len(ordered) > MAX_GROUND_SURFACE_POINTS:
        last = len(ordered) - 1
        indices = [
            int(round(index * last / (MAX_GROUND_SURFACE_POINTS - 1)))
            for index in range(MAX_GROUND_SURFACE_POINTS)
        ]
        ordered = [ordered[index] for index in indices]
    return [list(_vector(point["position_m"], 3, "surface.position_m")) for point in ordered]


def _unresolved_ground_result(
    request: Mapping[str, Any], grounded_unix: float, reason: str
) -> dict[str, Any]:
    result = _ground_result_base(request, grounded_unix)
    result.update(
        {
            "status": "unresolved",
            "target_id": None,
            "confidence": request["detection"]["confidence"],
            "coordinate_frame_id": None,
            "position_m": None,
            "uncertainty_radius_m": None,
            "reason": reason,
        }
    )
    return validate_ground_result(result, request=request)


def ground_bbox_to_depth(
    request: Mapping[str, Any],
    capture_result: Mapping[str, Any],
    *,
    grounded_unix: float | None = None,
    bbox_inset_ratio: float = 0.12,
    cluster_gap_m: float = 0.35,
    cluster_gap_ratio: float = 0.12,
    ambiguity_ratio: float = 0.25,
    min_depth_confidence: float = 0.25,
) -> dict[str, Any]:
    """Ground a semantic bbox using depth without native object identity.

    The winning one-dimensional depth surface is selected by confidence and
    proximity to the box centre.  If the runner-up retains at least
    ``1 - ambiguity_ratio`` of the winning support, no target is emitted.  A
    surface must also contain at least three distinct registered rays/pixels;
    one high-confidence measurement is never sufficient to create a target.
    """

    ground_request = validate_ground_request(request)
    capture = validate_capture_result(capture_result)
    for field in ("environment_id", "session_id"):
        if capture[field] != ground_request[field]:
            raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", field)
    if capture["frame_id"] != ground_request["source_frame_id"]:
        raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", "source_frame_id")
    timestamp = (
        max(ground_request["requested_unix"], capture["captured_unix"])
        if grounded_unix is None
        else _number(grounded_unix, "grounded_unix", minimum=0.0)
    )
    inset = _number(bbox_inset_ratio, "bbox_inset_ratio", minimum=0.0)
    if inset >= 0.5:
        raise _error("PORTABLE_CORE_NUMBER_INVALID", "bbox_inset_ratio")
    absolute_gap = _number(cluster_gap_m, "cluster_gap_m", minimum=0.0)
    relative_gap = _number(cluster_gap_ratio, "cluster_gap_ratio", minimum=0.0)
    ambiguity = _number(ambiguity_ratio, "ambiguity_ratio", minimum=0.0)
    if ambiguity >= 1.0:
        raise _error("PORTABLE_CORE_NUMBER_INVALID", "ambiguity_ratio")
    confidence_floor = _number(min_depth_confidence, "min_depth_confidence", minimum=0.0)
    if confidence_floor > 1.0:
        raise _error("PORTABLE_CORE_NUMBER_INVALID", "min_depth_confidence")

    def independent_support(points: Sequence[Mapping[str, Any]]) -> int:
        return len(
            {
                (float(point["uv_norm"][0]), float(point["uv_norm"][1]))
                for point in points
            }
        )

    bbox = ground_request["detection"]["bbox_norm"]
    expected_surface_role = ground_request["detection"].get("expected_surface_role")
    continuity_surface = ground_request.get("arrival_continuity_surface")
    if continuity_surface is not None:
        # The independent metric prior makes the whole semantic box safe to
        # inspect.  This also lets a new viewpoint match a visible edge of a
        # wide target instead of requiring that edge to survive a fixed inset.
        points = project_depth_samples(
            capture,
            bbox_norm=bbox,
            min_confidence=confidence_floor,
        )
    else:
        points = project_depth_samples(
            capture,
            bbox_norm=_inset_bbox(bbox, inset),
            min_confidence=confidence_floor,
        )
        # A tiny or sparse detector box may not retain enough independent inset
        # rays.  Falling back to the exact semantic box remains safe; falling
        # outside it does not.
        if independent_support(points) < _MIN_INDEPENDENT_DEPTH_SAMPLES and inset > 0.0:
            points = project_depth_samples(capture, bbox_norm=bbox, min_confidence=confidence_floor)
    if not points:
        return _unresolved_ground_result(ground_request, timestamp, "no_depth")
    if independent_support(points) < _MIN_INDEPENDENT_DEPTH_SAMPLES:
        return _unresolved_ground_result(
            ground_request, timestamp, "insufficient_depth_support"
        )

    clusters = _depth_clusters(points, absolute_gap, relative_gap)

    centre = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
    half_diagonal = max(
        _EPSILON,
        math.hypot((bbox[2] - bbox[0]) / 2.0, (bbox[3] - bbox[1]) / 2.0),
    )

    def support(cluster: Sequence[Mapping[str, Any]]) -> float:
        total = 0.0
        for point in cluster:
            centrality = 1.0 - math.hypot(
                point["uv_norm"][0] - centre[0], point["uv_norm"][1] - centre[1]
            ) / half_diagonal
            total += point["confidence"] * max(0.25, centrality)
        return total

    ranked = sorted(
        ((support(cluster), _median([point["forward_depth_m"] for point in cluster]), cluster)
         for cluster in clusters),
        key=lambda item: (-item[0], item[1], item[2][0]["source_index"]),
    )
    winning_support, _, winning = ranked[0]
    rescue_uncertainty = None
    # The ambiguity rule is pairwise against the strongest runner, so the
    # confidence denominator must use that same comparison.  Summing every
    # individually weak background layer can otherwise make an unambiguous
    # winner report less than 0.5 confidence.
    confidence_support_total = winning_support + (
        ranked[1][0] if len(ranked) > 1 else 0.0
    )
    default_ambiguous = (
        len(ranked) > 1
        and ranked[1][0] >= winning_support * (1.0 - ambiguity) - _EPSILON
    )
    if continuity_surface is not None:
        continuity_match = _arrival_continuity_surface_match(
            clusters,
            continuity_surface["points_m"],
            continuity_surface["maximum_distance_m"],
            continuity_surface.get("surface_id"),
            continuity_surface.get("surface_role"),
        )
        if continuity_match is None:
            return _unresolved_ground_result(ground_request, timestamp, "ambiguous")
        winning, continuity_runner, rescue_uncertainty = continuity_match
        winning_support = support(winning)
        confidence_support_total = winning_support + (
            support(continuity_runner) if continuity_runner is not None else 0.0
        )
    else:
        rescue_attempted, rescue = _wide_occlusion_surface_rescue(
            capture,
            bbox,
            ground_request["detection"].get("occluder_bboxes_norm", []),
            absolute_gap=absolute_gap,
            relative_gap=relative_gap,
            ambiguity=ambiguity,
            confidence_floor=confidence_floor,
            expected_surface_role=expected_surface_role,
        )
        if rescue_attempted:
            if rescue is None:
                return _unresolved_ground_result(ground_request, timestamp, "ambiguous")
            winning, winning_support, confidence_support_total, rescue_uncertainty = rescue
        elif default_ambiguous:
            return _unresolved_ground_result(ground_request, timestamp, "ambiguous")
        else:
            bbox_width, bbox_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            bbox_minor = min(bbox_width, bbox_height)
            if bbox_minor > _EPSILON and max(bbox_width, bbox_height) / bbox_minor >= 3.0:
                major_axis = 0 if bbox_width >= bbox_height else 1
                major_extent = bbox_width if major_axis == 0 else bbox_height
                major_values = [point["uv_norm"][major_axis] for point in winning]
                winning_span = (max(major_values) - min(major_values)) / major_extent
                # A compact depth blob cannot silently stand in for a scene-wide
                # semantic box. Without validated occluder evidence this geometry
                # is underdetermined, so retain fail-closed behavior.
                if winning_span < 0.50 - _EPSILON:
                    return _unresolved_ground_result(ground_request, timestamp, "ambiguous")
    winning_surface_role = _uniform_surface_role(winning)
    if (
        expected_surface_role is not None
        and winning_surface_role != expected_surface_role
    ):
        return _unresolved_ground_result(ground_request, timestamp, "ambiguous")
    if independent_support(winning) < _MIN_INDEPENDENT_DEPTH_SAMPLES:
        return _unresolved_ground_result(
            ground_request, timestamp, "insufficient_depth_support"
        )

    position = [_median([point["position_m"][axis] for point in winning]) for axis in range(3)]
    residuals = [
        math.sqrt(sum((point["position_m"][axis] - position[axis]) ** 2 for axis in range(3)))
        for point in winning
    ]
    uncertainty = (
        max(0.01, _median(residuals) if residuals else 0.0)
        if rescue_uncertainty is None
        else rescue_uncertainty
    )
    dominance = (
        winning_support / confidence_support_total
        if confidence_support_total > 0.0
        else 0.0
    )
    depth_confidence = sum(point["confidence"] for point in winning) / len(winning)
    confidence = max(
        0.0,
        min(1.0, ground_request["detection"]["confidence"] * depth_confidence * dominance),
    )
    surface_id = _uniform_surface_id(winning)
    target_id = _opaque_id(
        "target",
        {
            "environment_id": ground_request["environment_id"],
            "session_id": ground_request["session_id"],
            "source_frame_id": ground_request["source_frame_id"],
            "map_id": ground_request["map_id"],
            "detection_id": ground_request["detection"]["detection_id"],
            "position_mm": [int(round(component * 1000.0)) for component in position],
            "surface_points_mm": [
                [int(round(component * 1000.0)) for component in point]
                for point in _bounded_surface_positions(winning)
            ],
            "surface_id": surface_id,
            "surface_role": winning_surface_role,
        },
    )
    surface_points = _bounded_surface_positions(winning)
    result = _ground_result_base(ground_request, timestamp)
    result.update(
        {
            "status": "grounded",
            "target_id": target_id,
            "confidence": confidence,
            "coordinate_frame_id": capture["camera"]["coordinate_frame_id"],
            "position_m": position,
            "surface_points_m": surface_points,
            "uncertainty_radius_m": min(100.0, uncertainty),
            "reason": None,
        }
    )
    if surface_id is not None:
        result["surface_id"] = surface_id
    if winning_surface_role is not None:
        result["surface_role"] = winning_surface_role
    return validate_ground_result(result, request=ground_request)


ground_detection_with_depth = ground_bbox_to_depth


def _iou(first: Sequence[float], second: Sequence[float]) -> float:
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    union = (
        (first[2] - first[0]) * (first[3] - first[1])
        + (second[2] - second[0]) * (second[3] - second[1])
        - intersection
    )
    return intersection / union if union > 0.0 else 0.0


def ground_bbox_by_iou(
    bbox_norm: Sequence[float],
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_iou: float = 0.12,
    ambiguity_gap: float = 0.08,
) -> dict[str, Any] | None:
    """Match portable projected geometry without consulting semantic names.

    This is a compatibility helper for metric simulators that already expose
    projected collision geometry.  Candidate data is strictly limited to
    bbox, canonical position, opaque target id, and optional confidence.
    """

    bbox = _vector(bbox_norm, 4, "bbox_norm")
    threshold = _number(min_iou, "min_iou", minimum=0.0)
    gap = _number(ambiguity_gap, "ambiguity_gap", minimum=0.0)
    allowed = {"bbox_norm", "bbox", "position_m", "target_id", "confidence", "uncertainty_radius_m"}
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for index, raw in enumerate(candidates):
        if not isinstance(raw, Mapping):
            raise _error("PORTABLE_CORE_CANDIDATE_INVALID", f"candidates[{index}]")
        unknown = set(raw) - allowed
        if unknown:
            raise _error("PORTABLE_CORE_CANDIDATE_FIELDS_UNKNOWN", ",".join(sorted(unknown)))
        candidate_bbox = _vector(raw.get("bbox_norm", raw.get("bbox")), 4, f"candidates[{index}].bbox")
        position = _vector(raw.get("position_m"), 3, f"candidates[{index}].position_m")
        score = _iou(bbox, candidate_bbox)
        target_id = raw.get("target_id")
        if target_id is None:
            target_id = _opaque_id("target", {"bbox": candidate_bbox, "position_mm": [round(v, 3) for v in position]})
        if not isinstance(target_id, str) or not target_id.startswith("target:"):
            raise _error("PORTABLE_CORE_TARGET_ID_INVALID", f"candidates[{index}].target_id")
        ranked.append(
            (
                score,
                index,
                {
                    "target_id": target_id,
                    "position_m": position,
                    "bbox_norm": candidate_bbox,
                    "iou": score,
                    "confidence": _number(raw.get("confidence", 1.0), f"candidates[{index}].confidence", minimum=0.0),
                    "uncertainty_radius_m": _number(
                        raw.get("uncertainty_radius_m", 0.0),
                        f"candidates[{index}].uncertainty_radius_m",
                        minimum=0.0,
                    ),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked or ranked[0][0] < threshold:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < gap:
        raise _error("PORTABLE_CORE_GROUND_AMBIGUOUS", "bbox candidates")
    return ranked[0][2]


def _bounds(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, Mapping):
        raw = [value.get("min_x"), value.get("max_x"), value.get("min_y"), value.get("max_y")]
    else:
        raw = value
    result = _vector(raw, 4, "bounds_xy")
    if not result[0] < result[1] or not result[2] < result[3]:
        raise _error("PORTABLE_CORE_BOUNDS_INVALID", "bounds_xy")
    return result[0], result[1], result[2], result[3]


def _grid_dimensions(bounds: Sequence[float], resolution: float) -> tuple[int, int]:
    width = int(math.ceil((bounds[1] - bounds[0]) / resolution))
    height = int(math.ceil((bounds[3] - bounds[2]) / resolution))
    if width < 1 or height < 1 or width * height > _MAX_CORE_GRID_CELLS:
        raise _error("PORTABLE_CORE_GRID_SIZE_INVALID", f"{width}x{height}")
    return width, height


def _cell_half_diagonal(resolution: float) -> float:
    """Worst-case distance from a grid sample centre to any point in its cell."""

    return math.sqrt(2.0) * resolution / 2.0


def _erode_boundary_cells(
    occupancy: bytearray,
    width: int,
    height: int,
    bounds: Sequence[float],
    resolution: float,
    centre_clearance: float,
) -> None:
    """Block cells whose area can cross the clearance-eroded map boundary."""

    conservative_clearance = centre_clearance + _cell_half_diagonal(resolution)
    for iy in range(height):
        sample_y = bounds[2] + (iy + 0.5) * resolution
        row = iy * width
        for ix in range(width):
            sample_x = bounds[0] + (ix + 0.5) * resolution
            if min(
                sample_x - bounds[0],
                bounds[1] - sample_x,
                sample_y - bounds[2],
                bounds[3] - sample_y,
            ) <= conservative_clearance + _EPSILON:
                if occupancy[row + ix] != UNKNOWN_VALUE:
                    occupancy[row + ix] = OCCUPIED_VALUE


def rasterize_occupancy(
    bounds_xy: Sequence[float] | Mapping[str, Any],
    obstacles: Sequence[Mapping[str, Any]],
    resolution_m: float,
    footprint_radius_m: float,
    minimum_clearance_m: float = 0.0,
    *,
    unknown_cells: Iterable[Sequence[int]] | None = None,
) -> tuple[int, int, bytes]:
    """Rasterize canonical metric circles/AABBs into a u8 probability grid."""

    bounds = _bounds(bounds_xy)
    resolution = _number(resolution_m, "resolution_m", minimum=0.01)
    inflation = _number(footprint_radius_m, "footprint_radius_m", minimum=0.0) + _number(
        minimum_clearance_m, "minimum_clearance_m", minimum=0.0
    )
    conservative_inflation = inflation + _cell_half_diagonal(resolution)
    width, height = _grid_dimensions(bounds, resolution)
    occupancy = bytearray(width * height)
    if unknown_cells is not None:
        for index, raw_cell in enumerate(unknown_cells):
            cell = _vector(raw_cell, 2, f"unknown_cells[{index}]")
            ix, iy = int(cell[0]), int(cell[1])
            if cell[0] != ix or cell[1] != iy or not (0 <= ix < width and 0 <= iy < height):
                raise _error("PORTABLE_CORE_CELL_INVALID", f"unknown_cells[{index}]")
            occupancy[iy * width + ix] = UNKNOWN_VALUE

    for obstacle_index, raw in enumerate(obstacles):
        if not isinstance(raw, Mapping):
            raise _error("PORTABLE_CORE_OBSTACLE_INVALID", f"obstacles[{obstacle_index}]")
        kind = raw.get("kind")
        centre = _vector(raw.get("center_m", raw.get("center")), 2, f"obstacles[{obstacle_index}].center")
        if kind in {"rect", "aabb"}:
            half = _vector(
                raw.get("half_extents_m", raw.get("half")), 2, f"obstacles[{obstacle_index}].half"
            )
            if min(half) <= 0.0:
                raise _error("PORTABLE_CORE_OBSTACLE_INVALID", f"obstacles[{obstacle_index}].half")
            x0, x1 = (
                centre[0] - half[0] - conservative_inflation,
                centre[0] + half[0] + conservative_inflation,
            )
            y0, y1 = (
                centre[1] - half[1] - conservative_inflation,
                centre[1] + half[1] + conservative_inflation,
            )

            def occupied(sample_x: float, sample_y: float) -> bool:
                return x0 - _EPSILON <= sample_x <= x1 + _EPSILON and y0 - _EPSILON <= sample_y <= y1 + _EPSILON

            span = (x0, x1, y0, y1)
        elif kind == "circle":
            radius = _number(raw.get("radius_m", raw.get("radius")), f"obstacles[{obstacle_index}].radius", minimum=0.0)
            if radius <= 0.0:
                raise _error("PORTABLE_CORE_OBSTACLE_INVALID", f"obstacles[{obstacle_index}].radius")
            inflated_radius = radius + conservative_inflation
            radius_squared = inflated_radius * inflated_radius

            def occupied(sample_x: float, sample_y: float) -> bool:
                return (sample_x - centre[0]) ** 2 + (sample_y - centre[1]) ** 2 <= radius_squared + _EPSILON

            span = (
                centre[0] - inflated_radius,
                centre[0] + inflated_radius,
                centre[1] - inflated_radius,
                centre[1] + inflated_radius,
            )
        else:
            raise _error("PORTABLE_CORE_OBSTACLE_KIND_UNSUPPORTED", f"obstacles[{obstacle_index}].kind")

        ix0 = max(0, int(math.floor((span[0] - bounds[0]) / resolution)))
        ix1 = min(width - 1, int(math.floor((span[1] - bounds[0]) / resolution)))
        iy0 = max(0, int(math.floor((span[2] - bounds[2]) / resolution)))
        iy1 = min(height - 1, int(math.floor((span[3] - bounds[2]) / resolution)))
        if ix0 > ix1 or iy0 > iy1:
            continue
        for iy in range(iy0, iy1 + 1):
            sample_y = bounds[2] + (iy + 0.5) * resolution
            for ix in range(ix0, ix1 + 1):
                sample_x = bounds[0] + (ix + 0.5) * resolution
                if occupied(sample_x, sample_y):
                    cell_index = iy * width + ix
                    if occupancy[cell_index] != UNKNOWN_VALUE:
                        occupancy[cell_index] = OCCUPIED_VALUE
    _erode_boundary_cells(occupancy, width, height, bounds, resolution, inflation)
    return width, height, bytes(occupancy)


def inflate_occupancy(
    occupancy: bytes | bytearray,
    width: int,
    height: int,
    resolution_m: float,
    radius_m: float,
    *,
    unknown_value: int = UNKNOWN_VALUE,
    occupied_threshold: int = OCCUPIED_THRESHOLD,
) -> bytes:
    """Inflate blocked cells by a metric disk while preserving unknown labels."""

    if len(occupancy) != width * height:
        raise _error("PORTABLE_CORE_OCCUPANCY_SIZE_MISMATCH", "occupancy")
    radius = _number(radius_m, "radius_m", minimum=0.0)
    if radius <= _EPSILON:
        return bytes(occupancy)
    resolution = _number(resolution_m, "resolution_m", minimum=0.01)
    # A centre-only dilation can leave up to half a cell diagonal of the
    # traversed cell inside the requested clearance.  Include that geometric
    # uncertainty so every returned free cell is conservative as an area.
    conservative_radius = radius + _cell_half_diagonal(resolution)
    cells = int(math.ceil(conservative_radius / resolution))
    offsets = [
        (dx, dy)
        for dy in range(-cells, cells + 1)
        for dx in range(-cells, cells + 1)
        if math.hypot(dx, dy) * resolution <= conservative_radius + _EPSILON
    ]
    result = bytearray(occupancy)
    blocked_cells = [
        (index % width, index // width)
        for index, value in enumerate(occupancy)
        if value == unknown_value or value >= occupied_threshold
    ]
    for source_x, source_y in blocked_cells:
        for dx, dy in offsets:
            x, y = source_x + dx, source_y + dy
            if (
                0 <= x < width
                and 0 <= y < height
                and result[y * width + x] != unknown_value
            ):
                result[y * width + x] = OCCUPIED_VALUE
    return bytes(result)


@dataclass(frozen=True)
class _GridView:
    width: int
    height: int
    resolution: float
    origin: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    occupancy: bytes
    unknown: int
    threshold: int

    def blocked(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        if not (0 <= x < self.width and 0 <= y < self.height):
            return True
        value = self.occupancy[y * self.width + x]
        return value == self.unknown or value >= self.threshold

    def world_to_cell(self, point: Sequence[float]) -> tuple[int, int]:
        local = self.world_to_grid_coordinates(point)
        return int(math.floor(local[0])), int(math.floor(local[1]))

    def world_to_grid_coordinates(self, point: Sequence[float]) -> tuple[float, float]:
        relative = [point[index] - self.origin[index] for index in range(3)]
        local = _inverse_rotate_xyzw(self.orientation, relative)
        return local[0] / self.resolution, local[1] / self.resolution

    def cell_to_world(self, cell: tuple[int, int]) -> list[float]:
        local = [(cell[0] + 0.5) * self.resolution, (cell[1] + 0.5) * self.resolution, 0.0]
        rotated = _rotate_xyzw(self.orientation, local)
        return [self.origin[index] + rotated[index] for index in range(3)]


def _decode_grid(value: Mapping[str, Any]) -> _GridView:
    if "grid" in value:
        normalized = validate_map_result(value)
        grid = normalized["grid"]
    else:
        grid = value
    try:
        occupancy = base64.b64decode(grid["occupancy_base64"], validate=True)
        width = int(grid["width_cells"])
        height = int(grid["height_cells"])
        resolution = _number(grid["resolution_m"], "grid.resolution_m", minimum=0.01)
        origin = tuple(_vector(grid["origin_position_m"], 3, "grid.origin_position_m"))
        orientation_list = _vector(grid["origin_orientation_xyzw"], 4, "grid.origin_orientation_xyzw")
        norm = math.sqrt(sum(component * component for component in orientation_list))
        if not 0.999 <= norm <= 1.001:
            raise _error("PORTABLE_CORE_QUATERNION_INVALID", "grid.origin_orientation_xyzw")
        orientation = tuple(component / norm for component in orientation_list)
        unknown = int(grid["unknown_value"])
        threshold = int(grid["occupied_threshold"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise _error("PORTABLE_CORE_GRID_INVALID", "grid") from exc
    if width < 1 or height < 1 or len(occupancy) != width * height:
        raise _error("PORTABLE_CORE_OCCUPANCY_SIZE_MISMATCH", "grid.occupancy_base64")
    if hashlib.sha256(occupancy).hexdigest() != grid.get("occupancy_sha256"):
        raise _error("PORTABLE_CORE_OCCUPANCY_HASH_MISMATCH", "grid.occupancy_sha256")
    return _GridView(width, height, resolution, origin, orientation, occupancy, unknown, threshold)


def _segment_cells(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Integer supercover line, including both side cells at corner crossings."""

    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    nx, ny = abs(dx), abs(dy)
    sign_x = 1 if dx > 0 else -1 if dx < 0 else 0
    sign_y = 1 if dy > 0 else -1 if dy < 0 else 0
    x, y = x0, y0
    ix = iy = 0
    cells = [(x, y)]
    while ix < nx or iy < ny:
        left = (1 + 2 * ix) * ny
        right = (1 + 2 * iy) * nx
        if left == right:
            if sign_x and sign_y:
                cells.append((x + sign_x, y))
                cells.append((x, y + sign_y))
            x += sign_x
            y += sign_y
            ix += 1
            iy += 1
        elif left < right:
            x += sign_x
            ix += 1
        else:
            y += sign_y
            iy += 1
        cells.append((x, y))
    # Preserve traversal order but remove duplicates from axis-aligned ties.
    return list(dict.fromkeys(cells))


def _continuous_segment_cells(
    start: Sequence[float], end: Sequence[float], *, include_corner_sides: bool
) -> list[tuple[int, int]]:
    """Traverse cells touched by a continuous segment in grid coordinates."""

    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    cell_x, cell_y = int(math.floor(x0)), int(math.floor(y0))
    end_x, end_y = int(math.floor(x1)), int(math.floor(y1))
    cells = [(cell_x, cell_y)]
    delta_x, delta_y = x1 - x0, y1 - y0
    step_x = 1 if delta_x > 0.0 else -1 if delta_x < 0.0 else 0
    step_y = 1 if delta_y > 0.0 else -1 if delta_y < 0.0 else 0
    if step_x:
        boundary_x = cell_x + 1.0 if step_x > 0 else float(cell_x)
        max_x = (boundary_x - x0) / delta_x
        step_t_x = 1.0 / abs(delta_x)
    else:
        max_x = step_t_x = math.inf
    if step_y:
        boundary_y = cell_y + 1.0 if step_y > 0 else float(cell_y)
        max_y = (boundary_y - y0) / delta_y
        step_t_y = 1.0 / abs(delta_y)
    else:
        max_y = step_t_y = math.inf
    while (cell_x, cell_y) != (end_x, end_y):
        if abs(max_x - max_y) <= 1e-12:
            if include_corner_sides and step_x and step_y:
                cells.append((cell_x + step_x, cell_y))
                cells.append((cell_x, cell_y + step_y))
            cell_x += step_x
            cell_y += step_y
            max_x += step_t_x
            max_y += step_t_y
        elif max_x < max_y:
            cell_x += step_x
            max_x += step_t_x
        else:
            cell_y += step_y
            max_y += step_t_y
        cells.append((cell_x, cell_y))
    return list(dict.fromkeys(cells))


def occupancy_from_depth(
    capture_result: Mapping[str, Any],
    bounds_xy: Sequence[float] | Mapping[str, Any],
    resolution_m: float,
    footprint_radius_m: float,
    minimum_clearance_m: float = 0.0,
    *,
    ground_z_m: float = 0.0,
    obstacle_height_min_m: float = 0.05,
    obstacle_height_max_m: float = 2.2,
    min_depth_confidence: float = 0.25,
) -> tuple[int, int, bytes]:
    """Build a conservative unknown/free/occupied grid from registered depth."""

    capture = validate_capture_result(capture_result)
    bounds = _bounds(bounds_xy)
    resolution = _number(resolution_m, "resolution_m", minimum=0.01)
    width, height = _grid_dimensions(bounds, resolution)
    occupancy = bytearray([UNKNOWN_VALUE]) * (width * height)
    points = project_depth_samples(capture, min_confidence=min_depth_confidence)
    camera_xy = capture["camera"]["position_m"][:2]

    def raw_cell(point_xy: Sequence[float]) -> tuple[int, int]:
        return (
            int(math.floor((point_xy[0] - bounds[0]) / resolution)),
            int(math.floor((point_xy[1] - bounds[2]) / resolution)),
        )

    origin_cell = raw_cell(camera_xy)
    origin_grid = ((camera_xy[0] - bounds[0]) / resolution, (camera_xy[1] - bounds[2]) / resolution)
    obstacle_cells: set[tuple[int, int]] = set()
    min_height = _number(obstacle_height_min_m, "obstacle_height_min_m")
    max_height = _number(obstacle_height_max_m, "obstacle_height_max_m")
    if max_height < min_height:
        raise _error("PORTABLE_CORE_HEIGHT_RANGE_INVALID", "obstacle_height_max_m")
    ground_z = _number(ground_z_m, "ground_z_m")
    for point in points:
        endpoint = raw_cell(point["position_m"][:2])
        if not (0 <= endpoint[0] < width and 0 <= endpoint[1] < height):
            continue
        endpoint_grid = (
            (point["position_m"][0] - bounds[0]) / resolution,
            (point["position_m"][1] - bounds[2]) / resolution,
        )
        # A sensor outside this local costmap does not certify an arbitrarily
        # long strip as free.  Its in-bounds endpoint may still be an obstacle.
        ray_cells = (
            _continuous_segment_cells(origin_grid, endpoint_grid, include_corner_sides=False)
            if 0 <= origin_cell[0] < width and 0 <= origin_cell[1] < height
            else [endpoint]
        )
        for cell in ray_cells[:-1]:
            if 0 <= cell[0] < width and 0 <= cell[1] < height:
                occupancy[cell[1] * width + cell[0]] = FREE_VALUE
        relative_height = point["position_m"][2] - ground_z
        if min_height <= relative_height <= max_height:
            obstacle_cells.add(endpoint)
        else:
            occupancy[endpoint[1] * width + endpoint[0]] = FREE_VALUE
    for x, y in obstacle_cells:
        occupancy[y * width + x] = OCCUPIED_VALUE
    footprint_and_clearance = (
        _number(footprint_radius_m, "footprint_radius_m", minimum=0.0)
        + _number(minimum_clearance_m, "minimum_clearance_m", minimum=0.0)
    )
    inflated = bytearray(
        inflate_occupancy(
            occupancy,
            width,
            height,
            resolution,
            footprint_and_clearance,
        )
    )
    _erode_boundary_cells(
        inflated, width, height, bounds, resolution, footprint_and_clearance
    )
    return width, height, bytes(inflated)


def build_occupancy_map(
    request: Mapping[str, Any],
    *,
    bounds_xy: Sequence[float] | Mapping[str, Any],
    obstacles: Sequence[Mapping[str, Any]] | None = None,
    capture_result: Mapping[str, Any] | None = None,
    agent_position_m: Sequence[float],
    agent_orientation_xyzw: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
    minimum_clearance_m: float = 0.0,
    created_unix: float | None = None,
    ground_z_m: float = 0.0,
) -> dict[str, Any]:
    """Build and validate a complete portable map-result envelope.

    Supplying obstacle primitives describes known metric free space inside the
    bounds.  Depth-only construction leaves unobserved cells unknown.  If both
    are supplied, primitives form the authoritative metric map and valid depth
    obstacle endpoints are unioned into it.
    """

    normalized = validate_map_request(request)
    bounds = _bounds(bounds_xy)
    if obstacles is None and capture_result is None:
        raise _error("PORTABLE_CORE_MAP_SOURCE_REQUIRED", "obstacles or capture_result")
    if capture_result is not None:
        capture = validate_capture_result(capture_result)
        for field in ("environment_id", "session_id"):
            if capture[field] != normalized[field]:
                raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", field)
        if capture["frame_id"] != normalized["source_frame_id"]:
            raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", "source_frame_id")
    else:
        capture = None

    if obstacles is not None:
        width, height, occupancy = rasterize_occupancy(
            bounds,
            obstacles,
            normalized["resolution_m"],
            normalized["footprint_radius_m"],
            minimum_clearance_m,
        )
        if capture is not None and capture["depth"] is not None:
            _, _, sensed = occupancy_from_depth(
                capture,
                bounds,
                normalized["resolution_m"],
                normalized["footprint_radius_m"],
                minimum_clearance_m,
                ground_z_m=ground_z_m,
            )
            merged = bytearray(occupancy)
            for index, value in enumerate(sensed):
                if value >= OCCUPIED_THRESHOLD:
                    merged[index] = OCCUPIED_VALUE
            occupancy = bytes(merged)
    else:
        assert capture is not None
        if capture["depth"] is None:
            raise _error("PORTABLE_CORE_DEPTH_REQUIRED", "capture_result.depth")
        width, height, occupancy = occupancy_from_depth(
            capture,
            bounds,
            normalized["resolution_m"],
            normalized["footprint_radius_m"],
            minimum_clearance_m,
            ground_z_m=ground_z_m,
        )

    position = _vector(agent_position_m, 3, "agent_position_m")
    orientation = _vector(agent_orientation_xyzw, 4, "agent_orientation_xyzw")
    orientation_norm = math.sqrt(sum(component * component for component in orientation))
    if not 0.999 <= orientation_norm <= 1.001:
        raise _error("PORTABLE_CORE_QUATERNION_INVALID", "agent_orientation_xyzw")
    timestamp = (
        max(normalized["requested_unix"], capture["captured_unix"] if capture is not None else 0.0)
        if created_unix is None
        else _number(created_unix, "created_unix", minimum=0.0)
    )
    digest = hashlib.sha256(occupancy).hexdigest()
    map_id = _opaque_id(
        "map",
        {
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "source_frame_id": normalized["source_frame_id"],
            "bounds_xy": bounds,
            "resolution_m": normalized["resolution_m"],
            "occupancy_sha256": digest,
        },
    )
    result = {
        "schema_version": MAP_RESULT_SCHEMA,
        "request_id": normalized["request_id"],
        "environment_id": normalized["environment_id"],
        "session_id": normalized["session_id"],
        "map_id": map_id,
        "source_frame_id": normalized["source_frame_id"],
        "created_unix": timestamp,
        "coordinate_frame_id": (
            capture["camera"]["coordinate_frame_id"]
            if capture is not None
            else _opaque_id("world", [normalized["environment_id"], normalized["session_id"]])
        ),
        "coordinate_convention": COORDINATE_CONVENTION,
        "grid": {
            "width_cells": width,
            "height_cells": height,
            "resolution_m": normalized["resolution_m"],
            "origin_position_m": [bounds[0], bounds[2], ground_z_m],
            "origin_orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "occupancy_encoding": "u8_probability",
            "occupancy_base64": base64.b64encode(occupancy).decode("ascii"),
            "occupancy_sha256": digest,
            "unknown_value": UNKNOWN_VALUE,
            "occupied_threshold": OCCUPIED_THRESHOLD,
        },
        "agent": {
            "position_m": position,
            "orientation_xyzw": [component / orientation_norm for component in orientation],
            "footprint_radius_m": normalized["footprint_radius_m"],
        },
    }
    return validate_map_result(result, request=normalized)


def _grid_with_inflation(value: Mapping[str, Any], additional_radius_m: float) -> _GridView:
    view = _decode_grid(value)
    radius = _number(additional_radius_m, "additional_radius_m", minimum=0.0)
    if radius <= _EPSILON:
        return view
    inflated = inflate_occupancy(
        view.occupancy,
        view.width,
        view.height,
        view.resolution,
        radius,
        unknown_value=view.unknown,
        occupied_threshold=view.threshold,
    )
    return _GridView(
        view.width,
        view.height,
        view.resolution,
        view.origin,
        view.orientation,
        inflated,
        view.unknown,
        view.threshold,
    )


def path_collision_report(
    grid_or_map: Mapping[str, Any],
    points_m: Sequence[Sequence[float]],
    *,
    additional_inflation_m: float = 0.0,
) -> dict[str, Any]:
    """Check every grid cell crossed by every path segment."""

    if not isinstance(points_m, (list, tuple)) or not points_m:
        raise _error("PORTABLE_CORE_PATH_INVALID", "points_m")
    points = [_vector(point, 3, f"points_m[{index}]") for index, point in enumerate(points_m)]
    view = _grid_with_inflation(grid_or_map, additional_inflation_m)
    if len(points) == 1:
        cell = view.world_to_cell(points[0])
        valid = not view.blocked(cell)
        return {"valid": valid, "reason": None if valid else "blocked", "segment_index": 0, "cell": list(cell)}
    for segment_index in range(len(points) - 1):
        start_coordinates = view.world_to_grid_coordinates(points[segment_index])
        end_coordinates = view.world_to_grid_coordinates(points[segment_index + 1])
        start = (int(math.floor(start_coordinates[0])), int(math.floor(start_coordinates[1])))
        end = (int(math.floor(end_coordinates[0])), int(math.floor(end_coordinates[1])))
        for endpoint in (start, end):
            if not (0 <= endpoint[0] < view.width and 0 <= endpoint[1] < view.height):
                return {
                    "valid": False,
                    "reason": "out_of_bounds",
                    "segment_index": segment_index,
                    "cell": list(endpoint),
                }
        for cell in _continuous_segment_cells(
            start_coordinates, end_coordinates, include_corner_sides=True
        ):
            if view.blocked(cell):
                in_bounds = 0 <= cell[0] < view.width and 0 <= cell[1] < view.height
                return {
                    "valid": False,
                    "reason": "blocked" if in_bounds else "out_of_bounds",
                    "segment_index": segment_index,
                    "cell": list(cell),
                }
    return {"valid": True, "reason": None, "segment_index": None, "cell": None}


def validate_path_collision(
    grid_or_map: Mapping[str, Any],
    points_m: Sequence[Sequence[float]],
    *,
    additional_inflation_m: float = 0.0,
) -> bool:
    """Return ``True`` only when the complete path is collision-free."""

    return path_collision_report(
        grid_or_map, points_m, additional_inflation_m=additional_inflation_m
    )["valid"]


is_path_collision_free = validate_path_collision


def simplify_path(
    grid_or_map: Mapping[str, Any],
    points_m: Sequence[Sequence[float]],
    *,
    additional_inflation_m: float = 0.0,
) -> list[list[float]]:
    """Deterministically string-pull a path, then revalidate every segment."""

    points = [_vector(point, 3, f"points_m[{index}]") for index, point in enumerate(points_m)]
    if not points:
        raise _error("PORTABLE_CORE_PATH_INVALID", "points_m")
    if not validate_path_collision(grid_or_map, points, additional_inflation_m=additional_inflation_m):
        raise _error("PORTABLE_CORE_PATH_COLLISION", "input path")
    if len(points) <= 2:
        return points
    simplified = [points[0]]
    anchor = 0
    while anchor < len(points) - 1:
        candidate = len(points) - 1
        while candidate > anchor + 1:
            if validate_path_collision(
                grid_or_map,
                [points[anchor], points[candidate]],
                additional_inflation_m=additional_inflation_m,
            ):
                break
            candidate -= 1
        simplified.append(points[candidate])
        anchor = candidate
    if not validate_path_collision(
        grid_or_map, simplified, additional_inflation_m=additional_inflation_m
    ):
        raise _error("PORTABLE_CORE_PATH_COLLISION", "simplified path")
    return simplified


def _neighbours(view: _GridView, cell: tuple[int, int]) -> Iterable[tuple[tuple[int, int], float]]:
    # Fixed order plus heap tie fields make results reproducible across runs.
    for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (-1, -1), (1, -1)):
        neighbour = cell[0] + dx, cell[1] + dy
        if view.blocked(neighbour):
            continue
        if dx and dy:
            # Never squeeze diagonally between two blocked/unknown cells.
            if view.blocked((cell[0] + dx, cell[1])) or view.blocked((cell[0], cell[1] + dy)):
                continue
            cost = math.sqrt(2.0)
        else:
            cost = 1.0
        yield neighbour, cost


def _arrival_cells(
    view: _GridView, target: Sequence[float], arrival_radius: float
) -> set[tuple[int, int]]:
    local_target = _inverse_rotate_xyzw(
        view.orientation, [target[index] - view.origin[index] for index in range(3)]
    )
    centre_x = local_target[0] / view.resolution - 0.5
    centre_y = local_target[1] / view.resolution - 0.5
    radius_cells = arrival_radius / view.resolution
    extent = int(math.ceil(radius_cells)) + 1
    goals: set[tuple[int, int]] = set()
    first_y = max(0, math.floor(centre_y) - extent)
    last_y = min(view.height - 1, math.floor(centre_y) + extent)
    first_x = max(0, math.floor(centre_x) - extent)
    last_x = min(view.width - 1, math.floor(centre_x) + extent)
    for y in range(first_y, last_y + 1):
        for x in range(first_x, last_x + 1):
            if view.blocked((x, y)):
                continue
            world = view.cell_to_world((x, y))
            if math.dist(world[:2], target[:2]) <= arrival_radius + _EPSILON:
                goals.add((x, y))
    return goals


def plan_path(
    map_result: Mapping[str, Any],
    target: Sequence[float] | Mapping[str, Any],
    *,
    arrival_radius_m: float,
    minimum_clearance_m: float = 0.0,
    simplify: bool = True,
    max_expansions: int | None = None,
) -> dict[str, Any]:
    """Plan a deterministic fail-closed A* path on a portable map."""

    normalized_map = validate_map_result(map_result)
    target_id: str | None = None
    target_uncertainty = 0.0
    target_references: list[list[float]]
    if isinstance(target, Mapping):
        if target.get("status") not in {None, "grounded"} or target.get("position_m") is None:
            raise _error("PORTABLE_CORE_TARGET_UNRESOLVED", "target")
        if target.get("map_id") not in {None, normalized_map["map_id"]}:
            raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", "map_id")
        if target.get("coordinate_frame_id") not in {None, normalized_map["coordinate_frame_id"]}:
            raise _error("PORTABLE_CORE_LINEAGE_MISMATCH", "coordinate_frame_id")
        target_position = _vector(target["position_m"], 3, "target.position_m")
        target_id = target.get("target_id")
        target_uncertainty = _number(
            target.get("uncertainty_radius_m", 0.0),
            "target.uncertainty_radius_m",
            minimum=0.0,
        )
        raw_surface_points = target.get("surface_points_m")
        if raw_surface_points is None:
            target_references = [target_position]
        else:
            if (
                not isinstance(raw_surface_points, (list, tuple))
                or not 1 <= len(raw_surface_points) <= MAX_GROUND_SURFACE_POINTS
            ):
                raise _error("PORTABLE_CORE_TARGET_SURFACE_INVALID", "target.surface_points_m")
            target_references = [
                _vector(point, 3, f"target.surface_points_m[{index}]")
                for index, point in enumerate(raw_surface_points)
            ]
    else:
        target_position = _vector(target, 3, "target")
        target_references = [target_position]
    arrival = _number(arrival_radius_m, "arrival_radius_m", minimum=0.0)
    if arrival <= 0.0:
        raise _error("PORTABLE_CORE_ARRIVAL_RADIUS_INVALID", "arrival_radius_m")
    effective_arrival = arrival + target_uncertainty
    clearance = _number(minimum_clearance_m, "minimum_clearance_m", minimum=0.0)
    view = _grid_with_inflation(normalized_map, clearance)
    start_position = normalized_map["agent"]["position_m"]
    start = view.world_to_cell(start_position)
    if view.blocked(start):
        raise _error("PORTABLE_CORE_START_BLOCKED", str(start))
    goals: set[tuple[int, int]] = set()
    for reference in target_references:
        goals.update(_arrival_cells(view, reference, effective_arrival))
    if not goals:
        raise _error("PORTABLE_CORE_NO_SAFE_ARRIVAL", "target")
    expansion_limit = view.width * view.height if max_expansions is None else int(max_expansions)
    if expansion_limit < 1:
        raise _error("PORTABLE_CORE_EXPANSION_LIMIT_INVALID", "max_expansions")

    def heuristic(cell: tuple[int, int]) -> float:
        world = view.cell_to_world(cell)
        remaining = min(
            math.dist(world[:2], reference[:2]) for reference in target_references
        )
        return max(0.0, remaining - effective_arrival) / view.resolution

    frontier: list[tuple[float, float, float, int, int, int, tuple[int, int]]] = []
    serial = 0
    initial_h = heuristic(start)
    heapq.heappush(frontier, (initial_h, initial_h, 0.0, start[1], start[0], serial, start))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    best_cost: dict[tuple[int, int], float] = {start: 0.0}
    reached: tuple[int, int] | None = None
    expansions = 0
    while frontier and expansions < expansion_limit:
        _, _, cost, _, _, _, cell = heapq.heappop(frontier)
        if cost > best_cost.get(cell, math.inf) + _EPSILON:
            continue
        expansions += 1
        if cell in goals:
            reached = cell
            break
        for neighbour, step in _neighbours(view, cell):
            candidate_cost = cost + step
            if candidate_cost + _EPSILON >= best_cost.get(neighbour, math.inf):
                continue
            came_from[neighbour] = cell
            best_cost[neighbour] = candidate_cost
            serial += 1
            estimate = heuristic(neighbour)
            heapq.heappush(
                frontier,
                (
                    candidate_cost + estimate,
                    estimate,
                    candidate_cost,
                    neighbour[1],
                    neighbour[0],
                    serial,
                    neighbour,
                ),
            )
    if reached is None:
        reason = "expansion_limit" if frontier else "disconnected"
        raise _error("PORTABLE_CORE_NO_PATH", reason)

    cells = [reached]
    while cells[-1] != start:
        cells.append(came_from[cells[-1]])
    cells.reverse()
    full_points = [start_position] + [view.cell_to_world(cell) for cell in cells[1:]]
    if len(full_points) == 1:
        full_points.append(list(start_position))
    if not validate_path_collision(normalized_map, full_points, additional_inflation_m=clearance):
        raise _error("PORTABLE_CORE_PATH_COLLISION", "planned path")
    simplified = (
        simplify_path(normalized_map, full_points, additional_inflation_m=clearance)
        if simplify
        else full_points
    )
    if not validate_path_collision(normalized_map, simplified, additional_inflation_m=clearance):
        raise _error("PORTABLE_CORE_PATH_COLLISION", "final path")
    waypoints = simplified[1:] if len(simplified) > 1 else [simplified[0]]
    arrival_world = view.cell_to_world(reached)
    arrival_reference = min(
        target_references,
        key=lambda reference: (
            math.dist(arrival_world[:2], reference[:2]),
            reference[0],
            reference[1],
            reference[2],
        ),
    )
    return {
        "status": "planned",
        "map_id": normalized_map["map_id"],
        "target_id": target_id,
        "start_cell": list(start),
        "arrival_cell": list(reached),
        "path_cells": [list(cell) for cell in cells],
        "path_m": simplified,
        "waypoints_m": waypoints,
        "cost_cells": best_cost[reached],
        "expanded_cells": expansions,
        "minimum_clearance_m": clearance,
        "effective_arrival_radius_m": effective_arrival,
        "arrival_reference_position_m": list(arrival_reference),
        "target_surface_point_count": len(target_references),
    }


astar_path = plan_path


def validate_or_simplify_path(
    grid_or_map: Mapping[str, Any],
    start_m: Sequence[float],
    waypoints_m: Sequence[Sequence[float]],
    target_m: Sequence[float],
    arrival_radius_m: float,
    *,
    additional_inflation_m: float = 0.0,
) -> dict[str, Any]:
    """Validate arrival and return a safely simplified waypoint sequence."""

    start = _vector(start_m, 3, "start_m")
    target = _vector(target_m, 3, "target_m")
    waypoints = [_vector(point, 3, f"waypoints_m[{index}]") for index, point in enumerate(waypoints_m)]
    if not waypoints:
        raise _error("PORTABLE_CORE_PATH_INVALID", "waypoints_m")
    arrival = _number(arrival_radius_m, "arrival_radius_m", minimum=0.0)
    if math.dist(waypoints[-1][:2], target[:2]) > arrival + _EPSILON:
        raise _error("PORTABLE_CORE_TARGET_NOT_REACHED", "waypoints_m[-1]")
    simplified = simplify_path(
        grid_or_map,
        [start] + waypoints,
        additional_inflation_m=additional_inflation_m,
    )
    return {"valid": True, "path_m": simplified, "waypoints_m": simplified[1:]}


__all__ = [
    "FREE_VALUE",
    "UNKNOWN_VALUE",
    "OCCUPIED_VALUE",
    "OCCUPIED_THRESHOLD",
    "GROUNDING_POLICY_REVISION",
    "PortableNavigationCoreError",
    "project_depth_samples",
    "project_depth_to_3d",
    "ground_bbox_to_depth",
    "ground_detection_with_depth",
    "ground_bbox_by_iou",
    "rasterize_occupancy",
    "inflate_occupancy",
    "occupancy_from_depth",
    "build_occupancy_map",
    "path_collision_report",
    "validate_path_collision",
    "is_path_collision_free",
    "simplify_path",
    "plan_path",
    "astar_path",
    "validate_or_simplify_path",
]
