"""Opt-in eye-image -> local Qwen -> metric grounding -> safe movement.

Image recognition receives only pixels, then text selects from that inventory.
Neither pass receives scene labels, coordinates, depth, a map, or the evaluator
sidecar.  Portable mode grounds the selected visual box with metric depth and
plans on the environment adapter's occupancy map.  The legacy simulator-only
grounder remains available behind the older launch mode.  No physical robots or
external services.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from types import MappingProxyType
from .arrival_pose_epoch_fast_path_v1 import (
    AGENT_ADDITIONAL_INFLATION_M,
    ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
    INITIAL_CERTIFICATION_REVISION,
    OUTCOME_HARD_FAIL,
    OUTCOME_PASS,
    compute_initial_certification_digest,
    evaluate_arrival_pose_epoch_fast_path,
)
from .godot_portable_navigation_adapter_v1 import GodotPortableNavigationAdapter
from .portable_navigation_contract_v1 import (
    CAPTURE_REQUEST_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    GROUND_REQUEST_SCHEMA,
    MAP_REQUEST_SCHEMA,
    NAVIGATE_REQUEST_SCHEMA,
    STOP_REQUEST_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    PortableNavigationAdapter,
    PortableNavigationContractError,
    validate_capture_result,
    validate_feedback_result,
    validate_ground_result,
    validate_map_result,
    validate_navigate_result,
    validate_stop_result,
)
from .portable_navigation_core_v1 import (
    GROUNDING_POLICY_REVISION,
    PortableNavigationCoreError,
    ground_bbox_to_depth,
    path_collision_report,
    plan_path,
    project_depth_samples,
)
from .visual_perception import (
    INVENTORY_WIRE_FORMAT,
    OPEN_VOCAB_CONFIDENCE_THRESHOLD,
    OPEN_VOCAB_SELECTION_POLICY_REVISION,
    SELECTION_POLICY_REVISION,
    PerceptionError,
    infer as perceive,
    open_visual_descriptor,
    visual_color_base,
    visual_colors_compatible,
)
from .visual_resource_budget import VisualResourceBudget, VisualResourceError
from .visual_phase import PHASE_STATUS_REVISION, PhaseLeaseError, enabled as phase_enabled, inference_lease, status as phase_status

MODEL = "qwen3.5-4b-q4_K_M"
MOVE = re.compile(r"(?:行って|行こう|いって|向かって|向かおう|近づいて|近付いて|移動して|歩いて|進んで|そばまで|ところまで|の前まで|go to|walk to|approach|move to)", re.I)
OBSERVE = re.compile(r"(?:見えて|見える|を見て|を観察して|を確認して|があるか確認して|何がある|どこにある|どこにいる|探して|見つけて|what.*see|can you see|where is|look at|observe)", re.I)
STOP = re.compile(
    r"^(?:(?:ちょっと|一旦|いったん|今すぐ|お願い|ルミナ|please)[、,\s]*)*"
    r"(?:止まって|とまって|止まれ|とまれ|停止|中止|やめて|ストップ|キャンセル|stop|cancel)"
    r"(?:して(?:ください|下さい|くれ)?|ください|下さい|くれ(?:る)?|お願い|\s+please)?[！!。？?\s]*$", re.I)
TERMINAL = {"arrived", "observed", "not_visible", "ambiguous", "failed", "cancelled"}
ARRIVAL_REVALIDATION_REVISION = (
    "portable_fresh_semantic_rgbd_continuity_component_part_union_distance_fusion_arrival_v6"
)
ARRIVAL_REVALIDATION_TOLERANCE_M = .08
ARRIVAL_TARGET_CONTINUITY_BASE_TOLERANCE_M = .05
ARRIVAL_TARGET_CONTINUITY_MIN_TOLERANCE_M = .12
ARRIVAL_TARGET_CONTINUITY_MAX_TOLERANCE_M = .30
ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M = .20
RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M = .20
ARRIVAL_PROVISIONAL_COLOR_PAIR = frozenset({"purple", "pink"})
PORTABLE_APPROACH_RADIUS_CANDIDATES_M = (.80, .85, .90, .95, 1.00, 1.10, 1.20)
PORTABLE_WIDE_LOW_TARGET_MIN_WIDTH_NORM = .75
PORTABLE_WIDE_LOW_TARGET_MAX_HEIGHT_NORM = .25
PORTABLE_WIDE_LOW_TARGET_MIN_ASPECT_RATIO = 3.0
PORTABLE_SURFACE_ROLE_POLICY_REVISION = "portable_validated_category_surface_role_allowlist_v1"
# These are category semantics only. They contain no scene node name, native
# object identifier, coordinate, or evaluator metadata. Keep the allowlists
# intentionally small: an unknown open-vocabulary category must retain the
# older geometry-only, fail-closed behavior.
PORTABLE_NAVIGATION_OBSTACLE_CATEGORIES = frozenset({
    "chair",
    "plant",
    "sofa",
    "table",
})
PORTABLE_WALKABLE_GROUND_CATEGORIES = frozenset({
    "carpet",
    "floor",
    "ground",
    "path",
    "pavement",
    "road",
    "rug",
    "sidewalk",
    "terrain",
    "trail",
    "walkway",
})
VISUAL_INFERENCE_TIMEOUT_SECONDS = 60
VISUAL_MODEL_HTTP_TIMEOUT_SECONDS = 65


class NavigationError(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get("LUMINA_VISUAL_NAVIGATION", "0") == "1"


def portable_enabled() -> bool:
    """Use the depth/map adapter path only when the exhibit opts into it."""

    return enabled() and os.environ.get("LUMINA_PORTABLE_NAVIGATION", "0") == "1"


def arrival_fast_path_enabled() -> bool:
    """Opt-in pose-epoch arrival fast path; default off, portable mode only.

    When enabled, an ``arrived`` feedback is first checked by the pure
    ``evaluate_arrival_pose_epoch_fast_path`` over two fresh RGB-D frames and a
    fresh map.  Only ``pass`` skips the second Qwen revalidation; only
    ``fallback_eligible`` reaches it; ``hard_fail`` reaches neither.
    """

    return portable_enabled() and os.environ.get("LUMINA_ARRIVAL_POSE_EPOCH_FAST_PATH", "0") == "1"


def _idle_model_slots(value: Any) -> list[dict]:
    # The owned visual server has exactly one slot. Do not treat a malformed
    # response, another parallel profile, or an occupied slot as wake readiness.
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict)
        or type(value[0].get("id")) is not int or value[0]["id"] != 0
        or type(value[0].get("is_processing")) is not bool):
        raise NavigationError("visual_heavy_phase_model_slots_invalid")
    if value[0]["is_processing"]:
        raise NavigationError("visual_heavy_phase_model_slot_busy")
    return value


def relevant(text: str) -> bool:
    return bool(MOVE.search(text) or OBSERVE.search(text) or STOP.fullmatch(text.strip()))


def _vector(value: Any) -> tuple[float, float, float]:
    if isinstance(value, dict):
        value = [value.get(k) for k in ("x", "y", "z")]
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise NavigationError("invalid_pose")
    if any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in value):
        raise NavigationError("invalid_pose")
    return tuple(float(n) for n in value)


def distance(a: Any, b: Any, *, flat: bool = False) -> float:
    va, vb = _vector(a), _vector(b)
    return math.sqrt(sum((va[i] - vb[i]) ** 2 for i in ((0, 2) if flat else (0, 1, 2))))


def valid_bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise NavigationError("invalid_visual_box")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 1 for n in value):
        raise NavigationError("invalid_visual_box")
    x0, y0, x1, y1 = map(float, value)
    if x1 <= x0 or y1 <= y0:
        raise NavigationError("invalid_visual_box")
    return [x0, y0, x1, y1]


def portable_occluder_bboxes(detection: dict, target_bbox: list[float]) -> list[list[float]]:
    """Return bounded target-free inventory boxes overlapping the target.

    The hints contain geometry only: no labels, engine identities, or world
    metadata cross the portable grounding boundary. Missing or malformed audit
    evidence disables the optional occlusion rescue and therefore fails closed.
    """

    audit = detection.get("perception_audit")
    if not isinstance(audit, dict):
        return []
    inventory = audit.get("inventory")
    target_id = detection.get("target_id")
    if not isinstance(inventory, list) or len(inventory) > 8 or type(target_id) is not int:
        return []
    excluded_ids = {target_id}
    fragment_ids = detection.get("_arrival_fragment_inventory_ids")
    if (
        isinstance(fragment_ids, list)
        and len(fragment_ids) >= 2
        and all(type(item_id) is int for item_id in fragment_ids)
        and target_id in fragment_ids
    ):
        excluded_ids.update(fragment_ids)
    part_ids = detection.get("_arrival_sofa_part_inventory_ids")
    if (
        isinstance(part_ids, list)
        and len(part_ids) >= 2
        and all(type(item_id) is int for item_id in part_ids)
        and target_id in part_ids
    ):
        excluded_ids.update(part_ids)
    result: list[list[float]] = []
    for item in inventory:
        if not isinstance(item, dict) or item.get("id") in excluded_ids:
            continue
        raw = item.get("bbox")
        if (
            not isinstance(raw, list)
            or len(raw) != 4
            or any(type(component) is not int or not 0 <= component <= 1000 for component in raw)
        ):
            continue
        box = [component / 1000.0 for component in raw]
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        if (
            min(target_bbox[2], box[2]) > max(target_bbox[0], box[0])
            and min(target_bbox[3], box[3]) > max(target_bbox[1], box[1])
        ):
            result.append(box)
    return result


def _positive_area_overlap(first: list[int], second: list[int]) -> bool:
    return (
        min(first[2], second[2]) > max(first[0], second[0])
        and min(first[3], second[3]) > max(first[1], second[1])
    )


def portable_arrival_fragment_union(
    detection: Any,
    *,
    requested_category: Any,
    original_color: Any,
    reference_surface_id: Any,
    reference_surface_role: Any,
    expected_surface_role: Any,
) -> dict[str, Any] | None:
    """Recover one arrival target when Qwen split it into overlapping boxes.

    This is intentionally unavailable to initial target selection.  It accepts
    only a model-declared ambiguous/absent selection backed by at least two
    strictly valid, same-category inventory rows whose boxes form one
    positive-area overlap component.  Exact adapter-owned surface identity and
    role must already exist; the caller subsequently requires both again from
    each of two fresh RGB-D groundings.
    """

    if (
        not isinstance(detection, dict)
        or detection.get("visible") is not False
        or detection.get("intent") != "none"
        or detection.get("target_id") != -1
    ):
        return None
    confidence = detection.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or float(confidence) != 0.0
    ):
        return None
    if not isinstance(requested_category, str) or not requested_category.strip():
        return None
    requested_category = requested_category.strip().lower()
    if (
        not isinstance(original_color, str)
        or not re.fullmatch(r"surface:[0-9a-f]{64}", reference_surface_id or "")
        or reference_surface_role != "navigation_obstacle"
        or expected_surface_role != reference_surface_role
    ):
        return None

    perception_audit = detection.get("perception_audit")
    inventory = (
        perception_audit.get("inventory")
        if isinstance(perception_audit, dict)
        else None
    )
    if not isinstance(inventory, list) or not 2 <= len(inventory) <= 8:
        return None

    candidates: list[tuple[dict[str, Any], str]] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(inventory):
        if not isinstance(item, dict) or set(item) != {"id", "label", "color", "bbox"}:
            return None
        item_id = item.get("id")
        bbox = item.get("bbox")
        if (
            type(item_id) is not int
            or item_id != index
            or item_id in seen_ids
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(component) is not int or not 0 <= component <= 1000 for component in bbox)
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            return None
        seen_ids.add(item_id)
        try:
            category, color = open_visual_descriptor(
                item.get("label"), item.get("color")
            )
        except (AttributeError, PerceptionError):
            return None
        if category == requested_category:
            candidates.append((item, color))

    if len(candidates) < 2:
        return None

    try:
        original_color_base = visual_color_base(original_color)
        candidate_color_bases = [
            visual_color_base(color) for _item, color in candidates
        ]
        for _item, color in candidates:
            if not (
                visual_colors_compatible(color, original_color)
                or frozenset(
                    {visual_color_base(color), original_color_base}
                ) == ARRIVAL_PROVISIONAL_COLOR_PAIR
            ):
                return None
        for left_index, (_left_item, left_color) in enumerate(candidates):
            for _right_item, right_color in candidates[left_index + 1:]:
                if not (
                    visual_colors_compatible(left_color, right_color)
                    or frozenset(
                        {
                            visual_color_base(left_color),
                            visual_color_base(right_color),
                        }
                    ) == ARRIVAL_PROVISIONAL_COLOR_PAIR
                ):
                    return None
    except PerceptionError:
        return None

    # A connected graph is stronger than merely overlapping the final union:
    # each accepted fragment must be joined to another observed fragment by a
    # positive-area intersection. Edge/corner contact is intentionally absent.
    pending = {0}
    connected = set()
    while pending:
        current = pending.pop()
        if current in connected:
            continue
        connected.add(current)
        current_box = candidates[current][0]["bbox"]
        for other_index, (other, _color) in enumerate(candidates):
            if (
                other_index not in connected
                and _positive_area_overlap(current_box, other["bbox"])
            ):
                pending.add(other_index)
    if len(connected) != len(candidates):
        return None

    boxes = [item["bbox"] for item, _color in candidates]
    union_box = [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]
    representative, representative_color = candidates[0]
    fragment_ids = [item["id"] for item, _color in candidates]
    recovered = dict(detection)
    recovered.update(
        intent="approach",
        visible=True,
        label=representative["label"],
        category=requested_category,
        color=representative_color,
        confidence=1.0,
        bbox=[component / 1000.0 for component in union_box],
        target_id=representative["id"],
        _arrival_fragment_inventory_ids=fragment_ids,
    )
    recovered_audit = dict(perception_audit)
    recovered_audit["arrival_fragment_union"] = {
        # This helper produces only an image-space candidate.  Acceptance is
        # deliberately deferred until both fresh RGB-D groundings, opaque
        # surface identity/role, pose epoch, metric continuity, and the
        # unchanged arrival-distance guard have all passed below.
        "accepted": False,
        "image_candidate_generated": True,
        "scope": "arrival_only",
        "selection_basis": "single_positive_overlap_component",
        "requested_category": requested_category,
        "fragment_inventory_ids": fragment_ids,
        "fragment_count": len(fragment_ids),
        "fragment_colors": [color for _item, color in candidates],
        "fragment_color_bases": candidate_color_bases,
        "union_bbox": union_box,
        "reference_surface_id_required": True,
        "reference_surface_role": reference_surface_role,
        "dual_rgbd_exact_surface_continuity_required": True,
    }
    recovered["perception_audit"] = recovered_audit
    return recovered


def portable_arrival_fragment_occluder_policy(
    captures: tuple[dict, dict],
    *,
    target_bbox: list[float],
    occluder_bboxes: list[list[float]],
    reference_surface_points: list,
    reference_surface_id: Any,
    reference_surface_role: Any,
    expected_surface_role: Any,
    surface_id_semantics_revisions: tuple[Any, Any, Any],
) -> tuple[list[list[float]], dict[str, Any]]:
    """Preserve exact-reference rays hidden by an inventory occluder box.

    A close-view Qwen inventory can describe a small section of the same sofa
    as a ``cushion``.  Treating that detector box as an occluder would then hide
    valid target rays.  A box is omitted only when *each* fresh RGB-D frame has
    at least three independent rays inside its overlap with the fragment union
    that match the initial opaque surface id, physical role, pose-epoch
    semantics, and the existing 0.20 m continuity limit.  Rays from every
    other or missing surface id are measured for audit but never contribute.

    The returned boxes remain ordinary target-free visual hints.  The caller
    still supplies ``arrival_continuity_surface`` to both grounding passes, so
    removing a false visual occluder cannot admit another physical surface.
    """

    bbox = valid_bbox(target_bbox)
    bounded_occluders = [valid_bbox(box) for box in occluder_bboxes]
    audit: dict[str, Any] = {
        "revision": "arrival_fragment_exact_reference_occluder_policy_v1",
        "raw_visual_inventory_occluder_count": len(bounded_occluders),
        "effective_visual_inventory_occluder_count": len(bounded_occluders),
        "suppressed_visual_inventory_occluder_indices": [],
        "minimum_exact_reference_rays_per_fresh_frame": 3,
        "maximum_reference_distance_m": ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M,
        "exact_reference_surface_id_and_role_only": True,
        "other_surface_ids_count_as_support": False,
        "arrival_continuity_surface_identity_filter_required": True,
        "pose_epoch_semantics_match": False,
        "per_occluder": [],
        "applied": False,
    }
    if not bounded_occluders:
        audit["reason"] = "no_overlapping_visual_inventory_occluder"
        return bounded_occluders, audit

    frame_ids = [
        capture.get("frame_id") if isinstance(capture, dict) else None
        for capture in captures
    ]
    pose_epoch_match = (
        len(captures) == 2
        and all(isinstance(frame_id, str) for frame_id in frame_ids)
        and len(set(frame_ids)) == 2
        and len(surface_id_semantics_revisions) == 3
        and all(
            revision == SURFACE_ID_SEMANTICS_REVISION
            for revision in surface_id_semantics_revisions
        )
        and all(
            isinstance(capture, dict)
            and isinstance(capture.get("depth"), dict)
            and capture["depth"].get("surface_id_semantics_revision")
            == SURFACE_ID_SEMANTICS_REVISION
            for capture in captures
        )
    )
    audit["pose_epoch_semantics_match"] = pose_epoch_match
    if (
        not pose_epoch_match
        or not re.fullmatch(r"surface:[0-9a-f]{64}", reference_surface_id or "")
        or reference_surface_role != "navigation_obstacle"
        or expected_surface_role != reference_surface_role
        or not isinstance(reference_surface_points, list)
        or not reference_surface_points
    ):
        audit["reason"] = "exact_reference_identity_role_pose_epoch_unavailable"
        return bounded_occluders, audit
    try:
        references = [_vector(point) for point in reference_surface_points]
    except NavigationError:
        audit["reason"] = "reference_surface_invalid"
        return bounded_occluders, audit

    retained: list[list[float]] = []
    suppressed_indices: list[int] = []
    for index, occluder in enumerate(bounded_occluders):
        overlap = [
            max(bbox[0], occluder[0]),
            max(bbox[1], occluder[1]),
            min(bbox[2], occluder[2]),
            min(bbox[3], occluder[3]),
        ]
        frame_audits: list[dict[str, Any]] = []
        if overlap[2] <= overlap[0] or overlap[3] <= overlap[1]:
            retained.append(occluder)
            audit["per_occluder"].append({
                "index": index,
                "suppressed": False,
                "reason": "no_positive_target_overlap",
                "fresh_frames": frame_audits,
            })
            continue
        try:
            for capture in captures:
                projected = project_depth_samples(
                    capture,
                    bbox_norm=overlap,
                    min_confidence=.25,
                )
                exact_uv: set[tuple[float, float]] = set()
                nonreference_uv: set[tuple[float, float]] = set()
                for point in projected:
                    uv = tuple(float(component) for component in point["uv_norm"])
                    if (
                        point.get("surface_id") == reference_surface_id
                        and point.get("surface_role") == reference_surface_role
                        and min(
                            math.dist(_vector(point["position_m"]), reference)
                            for reference in references
                        ) <= ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                    ):
                        exact_uv.add(uv)
                    else:
                        nonreference_uv.add(uv)
                frame_audits.append({
                    "frame_id": capture.get("frame_id"),
                    "exact_reference_ray_count": len(exact_uv),
                    "nonreference_ray_count_excluded": len(nonreference_uv),
                    "minimum_required": 3,
                    "passed": len(exact_uv) >= 3,
                })
        except (NavigationError, PortableNavigationCoreError, PortableNavigationContractError):
            retained.append(occluder)
            audit["per_occluder"].append({
                "index": index,
                "suppressed": False,
                "reason": "fresh_depth_evidence_invalid",
                "fresh_frames": frame_audits,
            })
            continue
        suppress = len(frame_audits) == 2 and all(
            frame["passed"] is True for frame in frame_audits
        )
        if suppress:
            suppressed_indices.append(index)
        else:
            retained.append(occluder)
        audit["per_occluder"].append({
            "index": index,
            "suppressed": suppress,
            "reason": (
                "dual_fresh_exact_reference_rays"
                if suppress
                else "insufficient_dual_fresh_exact_reference_rays"
            ),
            "fresh_frames": frame_audits,
        })

    audit.update(
        effective_visual_inventory_occluder_count=len(retained),
        suppressed_visual_inventory_occluder_indices=suppressed_indices,
        applied=bool(suppressed_indices),
        reason=(
            "dual_fresh_exact_reference_rays_preserved"
            if suppressed_indices
            else "no_occluder_proved_to_cover_exact_reference_surface"
        ),
    )
    return retained, audit


def _rectangle_union_area(boxes: list[list[int]]) -> int:
    """Return exact covered area for a small set of validated integer boxes."""

    x_edges = sorted({box[0] for box in boxes} | {box[2] for box in boxes})
    area = 0
    for left, right in zip(x_edges, x_edges[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (box[1], box[3])
            for box in boxes
            if box[0] < right and box[2] > left
        )
        if not intervals:
            continue
        covered = 0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered += end - start
        area += (right - left) * covered
    return area


def _sofa_part_band_adjacent(first: list[int], second: list[int]) -> bool:
    """Accept overlap/exact horizontal joins, never corner or vertical chains."""

    vertical_overlap = min(first[3], second[3]) - max(first[1], second[1])
    minimum_height = min(first[3] - first[1], second[3] - second[1])
    if vertical_overlap <= 0 or vertical_overlap < minimum_height * .80:
        return False
    horizontal_gap = max(
        0,
        max(first[0], second[0]) - min(first[2], second[2]),
    )
    return horizontal_gap <= 1


def portable_arrival_sofa_part_union(
    detection: Any,
    *,
    requested_category: Any,
    original_color: Any,
    reference_surface_id: Any,
    reference_surface_role: Any,
    expected_surface_role: Any,
    normal_groundings: tuple[dict, dict],
    surface_id_semantics_revisions: tuple[Any, Any, Any],
    initial_surface_sha256: Any,
    expected_initial_surface_sha256: Any,
) -> dict[str, Any] | None:
    """Build one tightly bounded sofa/cushion arrival-only recovery box.

    Qwen can label visually continuous sections of a close sofa as cushions.
    This helper is deliberately unavailable until the unique sofa anchor has
    failed *both* ordinary fresh RGB-D groundings as ambiguous.  It changes
    only the image-space measurement window; the caller must subsequently
    prove the original opaque surface identity in both fresh frames.
    """

    if (
        not isinstance(detection, dict)
        or detection.get("visible") is not True
        or detection.get("intent") != "approach"
        or requested_category != "sofa"
        or detection.get("category") != "sofa"
        or not isinstance(original_color, str)
        or not re.fullmatch(r"surface:[0-9a-f]{64}", reference_surface_id or "")
        or reference_surface_role != "navigation_obstacle"
        or expected_surface_role != "navigation_obstacle"
        or not isinstance(initial_surface_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", initial_surface_sha256) is None
        or initial_surface_sha256 != expected_initial_surface_sha256
        or len(normal_groundings) != 2
        or not all(
            isinstance(result, dict)
            and result.get("status") == "unresolved"
            and result.get("reason") == "ambiguous"
            for result in normal_groundings
        )
        or len(surface_id_semantics_revisions) != 3
        or not all(
            revision == SURFACE_ID_SEMANTICS_REVISION
            for revision in surface_id_semantics_revisions
        )
    ):
        return None

    confidence = detection.get("confidence")
    target_id = detection.get("target_id")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not OPEN_VOCAB_CONFIDENCE_THRESHOLD <= float(confidence) <= 1.0
        or type(target_id) is not int
    ):
        return None
    try:
        selected_bbox = valid_bbox(detection.get("bbox"))
        selected_category, selected_color = open_visual_descriptor(
            detection.get("label"), detection.get("color")
        )
    except (NavigationError, PerceptionError):
        return None
    if selected_category != "sofa":
        return None

    perception_audit = detection.get("perception_audit")
    inventory = (
        perception_audit.get("inventory")
        if isinstance(perception_audit, dict)
        else None
    )
    if not isinstance(inventory, list) or not 2 <= len(inventory) <= 8:
        return None
    if (
        perception_audit.get("native_inventory_row_count") != len(inventory)
        or perception_audit.get("retained_native_row_indices")
        != list(range(len(inventory)))
        or perception_audit.get("dropped_degenerate_native_box_rows") != []
    ):
        return None

    parsed: list[tuple[dict[str, Any], str, str]] = []
    seen_ids: set[int] = set()
    duplicate_groups: dict[tuple[int, int, int, int], list[int]] = {}
    descriptors_by_box: dict[tuple[int, int, int, int], tuple[str, str]] = {}
    for index, item in enumerate(inventory):
        if not isinstance(item, dict) or set(item) != {"id", "label", "color", "bbox"}:
            return None
        item_id = item.get("id")
        bbox = item.get("bbox")
        if (
            type(item_id) is not int
            or item_id != index
            or item_id in seen_ids
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(component) is not int or not 0 <= component <= 1000 for component in bbox)
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            return None
        seen_ids.add(item_id)
        try:
            category, color = open_visual_descriptor(item.get("label"), item.get("color"))
        except (AttributeError, PerceptionError):
            return None
        box_key = tuple(bbox)
        prior_descriptor = descriptors_by_box.setdefault(box_key, (category, color))
        if prior_descriptor != (category, color):
            return None
        duplicate_groups.setdefault(box_key, []).append(item_id)
        if len(duplicate_groups[box_key]) > 2:
            return None
        parsed.append((item, category, color))

    sofa_rows = [entry for entry in parsed if entry[1] == "sofa"]
    if len(sofa_rows) != 1:
        return None
    anchor, _anchor_category, anchor_color = sofa_rows[0]
    if (
        anchor["id"] != target_id
        or anchor.get("label") != detection.get("label")
        or anchor_color != selected_color
        or any(
            abs(round(selected_bbox[index] * 1000) - anchor["bbox"][index]) > 1
            for index in range(4)
        )
    ):
        return None

    cushion_rows = [entry for entry in parsed if entry[1] == "cushion"]
    if not cushion_rows:
        return None
    try:
        if not visual_colors_compatible(anchor_color, original_color):
            return None
        if any(
            not visual_colors_compatible(color, anchor_color)
            or not visual_colors_compatible(color, original_color)
            for _item, _category, color in cushion_rows
        ):
            return None
    except PerceptionError:
        return None

    # Exact duplicate cushion boxes are a common model repetition.  They are
    # retained only for exclusion/audit and never increase coverage or support.
    unique_parts: list[tuple[dict[str, Any], str]] = []
    seen_part_boxes: set[tuple[int, int, int, int]] = set()
    for item, _category, color in cushion_rows:
        box_key = tuple(item["bbox"])
        if box_key in seen_part_boxes:
            continue
        seen_part_boxes.add(box_key)
        unique_parts.append((item, color))
    if not 1 <= len(unique_parts) <= 5:
        return None

    anchor_box = anchor["bbox"]
    if any(
        part["bbox"][1] < anchor_box[1]
        or part["bbox"][3] > anchor_box[3]
        for part, _color in unique_parts
    ):
        return None

    component = [(anchor, anchor_color), *unique_parts]
    pending = {0}
    connected: set[int] = set()
    while pending:
        current = pending.pop()
        if current in connected:
            continue
        connected.add(current)
        current_box = component[current][0]["bbox"]
        for other_index, (other, _color) in enumerate(component):
            if (
                other_index not in connected
                and _sofa_part_band_adjacent(current_box, other["bbox"])
            ):
                pending.add(other_index)
    if len(connected) != len(component):
        return None

    boxes = [item["bbox"] for item, _color in component]
    union_box = [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]
    union_width = union_box[2] - union_box[0]
    union_height = union_box[3] - union_box[1]
    union_area = union_width * union_height
    anchor_area = (anchor_box[2] - anchor_box[0]) * (anchor_box[3] - anchor_box[1])
    covered_area = _rectangle_union_area(boxes)
    if (
        union_width > (anchor_box[2] - anchor_box[0]) * 2.5
        or union_height != anchor_box[3] - anchor_box[1]
        or union_area > 450_000
        or anchor_area < union_area * .35
        or covered_area < union_area * .85
    ):
        return None

    member_ids = [
        anchor["id"],
        *[item["id"] for item, _category, _color in cushion_rows],
    ]
    duplicate_member_groups = [
        ids
        for box_key, ids in duplicate_groups.items()
        if len(ids) > 1
        and descriptors_by_box[box_key][0] == "cushion"
    ]
    recovered = dict(detection)
    recovered.update(
        bbox=[component / 1000.0 for component in union_box],
        _arrival_sofa_part_inventory_ids=member_ids,
    )
    recovered_audit = dict(perception_audit)
    recovered_audit["arrival_sofa_part_union"] = {
        "accepted": False,
        "image_candidate_generated": True,
        "scope": "arrival_second_stage_only",
        "selection_basis": "unique_sofa_anchor_strict_cushion_horizontal_band",
        "normal_dual_grounding_requirement": "both_unresolved_ambiguous",
        "anchor_inventory_id": anchor["id"],
        "anchor_bbox": list(anchor_box),
        "part_inventory_ids": [
            item["id"] for item, _category, _color in cushion_rows
        ],
        "unique_part_count": len(unique_parts),
        "deduplicated_part_inventory_id_groups": duplicate_member_groups,
        "union_bbox": union_box,
        "union_coverage_ratio": covered_area / union_area,
        "anchor_area_ratio": anchor_area / union_area,
        "union_area_ratio": union_area / 1_000_000.0,
        "horizontal_expansion_ratio": union_width / (anchor_box[2] - anchor_box[0]),
        "part_category_allowlist": ["cushion"],
        "part_color_requires_ordinary_base_compatibility": True,
        "reference_surface_id_required": True,
        "reference_surface_role": reference_surface_role,
        "surface_id_world_pose_epoch_required": True,
        "dual_rgbd_exact_surface_continuity_required": True,
        "initial_certified_surface_integrity_match": True,
        "anchor_and_additional_exact_surface_support_required_per_frame": 3,
    }
    recovered["perception_audit"] = recovered_audit
    return recovered


def portable_arrival_sofa_part_surface_support(
    capture: dict,
    *,
    anchor_bbox: list[float],
    union_bbox: list[float],
    reference_surface_points: list,
    reference_surface_id: Any,
    reference_surface_role: Any,
) -> dict[str, Any]:
    """Count independent exact-target continuity rays in anchor/new regions."""

    anchor = valid_bbox(anchor_bbox)
    union = valid_bbox(union_bbox)
    depth = capture.get("depth") if isinstance(capture, dict) else None
    semantics_revision_match = (
        isinstance(depth, dict)
        and depth.get("surface_id_semantics_revision")
        == SURFACE_ID_SEMANTICS_REVISION
    )
    references = [_vector(point) for point in reference_surface_points]
    if (
        not references
        or not re.fullmatch(r"surface:[0-9a-f]{64}", reference_surface_id or "")
        or reference_surface_role != "navigation_obstacle"
        or not semantics_revision_match
    ):
        return {
            "anchor_exact_continuity_ray_count": 0,
            "additional_exact_continuity_ray_count": 0,
            "minimum_required_per_region": 3,
            "surface_id_world_pose_epoch_match": semantics_revision_match,
            "passed": False,
        }

    exact_points = []
    for point in project_depth_samples(capture, bbox_norm=union, min_confidence=.25):
        if (
            point.get("surface_id") == reference_surface_id
            and point.get("surface_role") == reference_surface_role
            and min(
                math.dist(_vector(point["position_m"]), reference)
                for reference in references
            ) <= ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
        ):
            exact_points.append(point)

    def inside(point: dict, box: list[float]) -> bool:
        uv = point["uv_norm"]
        return box[0] <= uv[0] <= box[2] and box[1] <= uv[1] <= box[3]

    anchor_uv = {
        tuple(point["uv_norm"])
        for point in exact_points
        if inside(point, anchor)
    }
    additional_uv = {
        tuple(point["uv_norm"])
        for point in exact_points
        if not inside(point, anchor)
    }
    passed = len(anchor_uv) >= 3 and len(additional_uv) >= 3
    return {
        "anchor_exact_continuity_ray_count": len(anchor_uv),
        "additional_exact_continuity_ray_count": len(additional_uv),
        "minimum_required_per_region": 3,
        "surface_id_world_pose_epoch_match": semantics_revision_match,
        "passed": passed,
    }


def portable_expected_surface_role(category: Any) -> str | None:
    """Map only an already-validated target category to a safe role hint.

    This helper is deliberately not fuzzy. In particular, arbitrary open-vocab
    labels containing words such as ``chair`` or ``floor`` do not inherit a
    role. The caller omits the field for every unknown category so adapters
    without role metadata continue through the established geometry checks.
    """

    if not isinstance(category, str):
        return None
    normalized = category.strip().lower()
    if normalized in PORTABLE_NAVIGATION_OBSTACLE_CATEGORIES:
        return "navigation_obstacle"
    if normalized in PORTABLE_WALKABLE_GROUND_CATEGORIES:
        return "walkable_ground"
    return None


def portable_target_planar_distance(grounded: dict, position: Any) -> float:
    """Measure agent-centre distance to a certified RGB-D target surface."""

    agent = _vector(position)
    references = grounded.get("surface_points_m")
    if not isinstance(references, list) or not references:
        references = [grounded.get("position_m")]
    return min(
        math.hypot(agent[0] - reference[0], agent[1] - reference[1])
        for reference in (_vector(item) for item in references)
    )


def select_portable_arrival_planar_distance(
    fresh_grounded: dict,
    initial_surface_points: list,
    position: Any,
    *,
    fresh_maximum_allowed_distance_m: float,
    initial_maximum_allowed_distance_m: float,
    reference_surface_id: Any,
    reference_surface_role: Any,
    expected_surface_role: Any,
    surface_id_semantics_revisions: tuple[Any, Any, Any],
    dual_fresh_groundings: tuple[dict, dict],
    continuity_checks: list[dict],
    initial_surface_sha256: Any,
    expected_initial_surface_sha256: Any,
) -> tuple[float, dict]:
    """Select arrival distance without treating a partial view as movement.

    The fresh RGB-D subset remains authoritative whenever it is already inside
    the unchanged arrival threshold.  The initial full surface is usable only
    as a false-negative fallback when two fresh groundings attest the exact
    same opaque surface id and role, both metric continuity checks pass, and
    the stored initial surface has not changed.  Thus this does not let a
    visually similar object, an unidentified surface, or a moved surface borrow
    the movement-start geometry.
    """

    for candidate_maximum in (
        fresh_maximum_allowed_distance_m,
        initial_maximum_allowed_distance_m,
    ):
        if (
            isinstance(candidate_maximum, bool)
            or not isinstance(candidate_maximum, (int, float))
            or not math.isfinite(candidate_maximum)
            or candidate_maximum < 0.0
        ):
            raise NavigationError("arrival_distance_threshold_invalid")
    fresh_maximum = float(fresh_maximum_allowed_distance_m)
    initial_maximum = float(initial_maximum_allowed_distance_m)
    fresh_distance = portable_target_planar_distance(fresh_grounded, position)
    initial_distance = portable_target_planar_distance(
        {"surface_points_m": initial_surface_points},
        position,
    )

    pose_epoch_attested = (
        len(surface_id_semantics_revisions) == 3
        and all(
            revision == SURFACE_ID_SEMANTICS_REVISION
            for revision in surface_id_semantics_revisions
        )
    )
    exact_identity_role_match = (
        isinstance(reference_surface_id, str)
        and re.fullmatch(r"surface:[0-9a-f]{64}", reference_surface_id) is not None
        and reference_surface_role == "navigation_obstacle"
        and expected_surface_role == "navigation_obstacle"
        and pose_epoch_attested
        and len(dual_fresh_groundings) == 2
        and all(
            result.get("status") == "grounded"
            and result.get("surface_id") == reference_surface_id
            and result.get("surface_role") == reference_surface_role
            and isinstance(result.get("surface_points_m"), list)
            and len(result["surface_points_m"]) >= 3
            for result in dual_fresh_groundings
        )
    )
    checks_by_basis = {
        check.get("basis"): check
        for check in continuity_checks
        if isinstance(check, dict)
    }
    dual_metric_continuity = (
        len(continuity_checks) == 2
        and set(checks_by_basis) == {"semantic", "post_inference"}
        and all(
            check.get("continuous") is True
            and not isinstance(check.get("distance_m"), bool)
            and isinstance(check.get("distance_m"), (int, float))
            and math.isfinite(float(check["distance_m"]))
            and float(check["distance_m"])
            <= ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
            for check in checks_by_basis.values()
        )
    )
    initial_surface_integrity_match = (
        isinstance(initial_surface_sha256, str)
        and isinstance(expected_initial_surface_sha256, str)
        and initial_surface_sha256 == expected_initial_surface_sha256
    )
    initial_surface_eligible = (
        exact_identity_role_match
        and dual_metric_continuity
        and initial_surface_integrity_match
    )

    if fresh_distance <= fresh_maximum:
        selected_distance = fresh_distance
        selected_maximum = fresh_maximum
        selected_basis = "fresh_rgbd_exact_surface_subset"
        selection_reason = "fresh_surface_subset_within_unchanged_threshold"
    elif initial_surface_eligible and initial_distance <= initial_maximum:
        selected_distance = initial_distance
        selected_maximum = initial_maximum
        selected_basis = (
            "initial_certified_full_surface_after_dual_exact_id_role_continuity"
        )
        selection_reason = (
            "viewpoint_sampling_bias_on_same_attested_surface_component"
        )
    else:
        selected_distance = fresh_distance
        selected_maximum = fresh_maximum
        selected_basis = "fresh_rgbd_surface_subset"
        if not initial_surface_eligible:
            selection_reason = (
                "initial_surface_ineligible_without_dual_exact_id_role_metric_continuity"
            )
        else:
            selection_reason = "both_surface_distances_exceed_unchanged_threshold"

    return selected_distance, {
        "fresh_surface_subset_planar_distance_m": fresh_distance,
        "initial_certified_full_surface_planar_distance_m": initial_distance,
        "fresh_surface_subset_maximum_allowed_distance_m": fresh_maximum,
        "initial_certified_full_surface_maximum_allowed_distance_m": (
            initial_maximum
        ),
        "selected_maximum_allowed_distance_m": selected_maximum,
        "fresh_surface_point_count": len(
            fresh_grounded.get("surface_points_m") or []
        ),
        "initial_certified_surface_point_count": len(initial_surface_points),
        "dual_fresh_exact_surface_id_role_match": exact_identity_role_match,
        "surface_id_semantics_revision_required": (
            SURFACE_ID_SEMANTICS_REVISION
        ),
        "surface_id_semantics_revision": (
            SURFACE_ID_SEMANTICS_REVISION if pose_epoch_attested else None
        ),
        "surface_id_semantics_revision_observations": {
            "initial_grounding": surface_id_semantics_revisions[0],
            "arrival_semantic": surface_id_semantics_revisions[1],
            "arrival_grounding": surface_id_semantics_revisions[2],
        },
        "surface_id_world_pose_epoch_attested": pose_epoch_attested,
        "dual_metric_surface_continuity_passed_for_distance": (
            dual_metric_continuity
        ),
        "initial_certified_surface_integrity_match": (
            initial_surface_integrity_match
        ),
        "initial_certified_surface_distance_eligible": initial_surface_eligible,
        "distance_basis": selected_basis,
        "distance_selection_reason": selection_reason,
        "maximum_allowed_distance_m": selected_maximum,
    }


def portable_wide_low_target_needs_visibility_standoff(
    target_bbox_norm: Any,
) -> bool:
    """Detect a close-looking horizontal target using pixels only.

    A box occupying most of the image width but little of its height is at
    high risk of leaving the eye camera's view during the final approach.
    This remains engine-neutral: it uses only the already validated visual
    box and does not inspect scene names, maps, depth, or evaluator state.
    """

    try:
        left, top, right, bottom = valid_bbox(target_bbox_norm)
    except NavigationError:
        return False
    width = right - left
    height = bottom - top
    return (
        width >= PORTABLE_WIDE_LOW_TARGET_MIN_WIDTH_NORM
        and height <= PORTABLE_WIDE_LOW_TARGET_MAX_HEIGHT_NORM
        and width / height >= PORTABLE_WIDE_LOW_TARGET_MIN_ASPECT_RATIO
    )


def plan_portable_approach(
    portable_map: dict,
    grounded: dict,
    *,
    minimum_clearance_m: float,
    target_bbox_norm: Any = None,
) -> tuple[dict, float, list[float]]:
    """Find a bounded stand-off with at least one safe map cell.

    A target surface can be physically occupied while a person or another
    obstacle blocks its normal approach side. Expanding only
    ``NO_SAFE_ARRIVAL`` keeps the result collision-free without treating a
    wall-disconnected target as reached from the other side. Very wide, low
    image targets try the existing bounded radii farthest-first so more of the
    target remains visible; ordinary targets retain nearest-first behavior.
    """

    candidates = PORTABLE_APPROACH_RADIUS_CANDIDATES_M
    rejected: list[float] = []
    last_error: PortableNavigationCoreError | None = None
    if portable_wide_low_target_needs_visibility_standoff(target_bbox_norm):
        # Preserve the ordinary policy's fail-closed connectivity invariant
        # before considering a looser visibility radius.  Without this guard,
        # 0.8m could have real goals that are wall-disconnected while a 1.2m
        # goal on the camera side of that wall is accepted as "arrived".
        baseline_route: dict | None = None
        baseline_radius: float | None = None
        for arrival_radius in candidates:
            try:
                baseline_route = plan_path(
                    portable_map,
                    grounded,
                    arrival_radius_m=arrival_radius,
                    minimum_clearance_m=minimum_clearance_m,
                )
            except PortableNavigationCoreError as exc:
                if exc.code != "PORTABLE_CORE_NO_SAFE_ARRIVAL":
                    raise
                rejected.append(arrival_radius)
                last_error = exc
                continue
            baseline_radius = arrival_radius
            break
        if baseline_route is None or baseline_radius is None:
            assert last_error is not None
            raise last_error

        # A connected ordinary-radius route now exists.  Select the largest
        # bounded radius that also plans safely; all collision/map validation
        # remains inside the unchanged plan_path implementation.
        for arrival_radius in reversed(candidates):
            if arrival_radius < baseline_radius:
                continue
            if arrival_radius == baseline_radius:
                route = baseline_route
            else:
                try:
                    route = plan_path(
                        portable_map,
                        grounded,
                        arrival_radius_m=arrival_radius,
                        minimum_clearance_m=minimum_clearance_m,
                    )
                except PortableNavigationCoreError as exc:
                    if exc.code != "PORTABLE_CORE_NO_SAFE_ARRIVAL":
                        raise
                    rejected.append(arrival_radius)
                    last_error = exc
                    continue
            route["visibility_standoff_connectivity_guard_radius_m"] = (
                baseline_radius
            )
            return route, arrival_radius, rejected
        raise AssertionError("connected visibility stand-off candidate missing")

    for arrival_radius in candidates:
        try:
            route = plan_path(
                portable_map,
                grounded,
                arrival_radius_m=arrival_radius,
                minimum_clearance_m=minimum_clearance_m,
            )
        except PortableNavigationCoreError as exc:
            if exc.code != "PORTABLE_CORE_NO_SAFE_ARRIVAL":
                raise
            rejected.append(arrival_radius)
            last_error = exc
            continue
        return route, arrival_radius, rejected
    assert last_error is not None
    raise last_error


def portable_surface_evidence_distance(
    grounded: dict,
    reference_points: list,
) -> float:
    """Return the third-nearest fresh ray distance to a certified surface."""

    references = [_vector(point) for point in reference_points]
    current = grounded.get("surface_points_m")
    if not references or not isinstance(current, list) or len(current) < 3:
        raise NavigationError("arrival_surface_continuity_evidence_invalid")
    distances = sorted(
        min(math.dist(_vector(point), reference) for reference in references)
        for point in current
    )
    return distances[2]


def portable_surface_sha256(points: list) -> str:
    normalized = [list(_vector(point)) for point in points]
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iou(a: list, b: list) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def ground_detection(detection: dict, metadata: dict) -> dict:
    confidence = detection.get("confidence")
    if detection.get("visible") is not True or isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not .70 <= confidence <= 1:
        raise NavigationError("not_visible")
    box = valid_bbox(detection.get("bbox"))
    candidates = []
    for item in metadata.get("evaluator_geometry", {}).get("objects", []):
        if item.get("occluded") is not False or item.get("in_frustum") is not True or item.get("can_approach", True) is not True:
            continue
        try:
            score = _iou(box, valid_bbox(item.get("projected_bbox")))
            _vector(item.get("world_position"))
        except NavigationError:
            continue
        name = item.get("name")
        if score >= .12 and isinstance(name, str) and 0 < len(name) <= 120:
            candidates.append((score, item))
    candidates.sort(key=lambda row: row[0], reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0] - candidates[1][0] < .08):
        raise NavigationError("ambiguous")
    result = dict(candidates[0][1])
    result["visual_box_iou"] = candidates[0][0]
    return result


def read_frame(directory: Path, *, newer_than: float = 0, max_age: float = 3.0) -> tuple[dict, bytes]:
    directory = directory.resolve()
    try:
        raw = (directory / "latest.json").read_bytes()
        if len(raw) > 2_000_000:
            raise NavigationError("frame_metadata_too_large")
        metadata = json.loads(raw)
        stamp = float(metadata["captured_unix"])
        if stamp <= newer_than or not 0 <= time.time() - stamp <= max_age:
            raise NavigationError("stale_eye_frame")
        image_path = Path(metadata["image_path"]).resolve()
        if directory not in image_path.parents or image_path.suffix.lower() != ".png":
            raise NavigationError("invalid_frame_path")
        if image_path.stat().st_size > 4_000_000:
            raise NavigationError("frame_too_large")
        image = image_path.read_bytes()
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(image).hexdigest() != metadata["image_sha256"]:
            raise NavigationError("frame_hash_mismatch")
        _vector(metadata["camera_position"])
        _vector(metadata["camera_forward"])
        return metadata, image
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NavigationError("eye_frame_unavailable") from exc


def pose_matches(before: dict, after: dict) -> bool:
    try:
        if str(before.get("frame_id", "")).split("-", 1)[0] != str(after.get("frame_id", "")).split("-", 1)[0]:
            return False
        a, b = _vector(before["camera_forward"]), _vector(after["camera_forward"])
        norm = math.sqrt(sum(n * n for n in a) * sum(n * n for n in b))
        return norm > .5 and sum(x * y for x, y in zip(a, b)) / norm >= math.cos(math.radians(12)) and distance(before["camera_position"], after["camera_position"]) <= .20
    except (KeyError, NavigationError):
        return False


def portable_pose_matches(before: dict, after: dict) -> bool:
    """Compare synchronized camera calibration without engine-specific fields."""

    try:
        first_camera, second_camera = before["camera"], after["camera"]
        if first_camera["coordinate_frame_id"] != second_camera["coordinate_frame_id"]:
            return False
        if any(before["rgb"][field] != after["rgb"][field] for field in ("width_px", "height_px")):
            return False
        for field in ("fx_px", "fy_px", "cx_px", "cy_px"):
            if not math.isclose(
                float(first_camera["intrinsics"][field]),
                float(second_camera["intrinsics"][field]),
                rel_tol=0.0,
                abs_tol=1e-6,
            ):
                return False
        first_position = _vector(first_camera["position_m"])
        second_position = _vector(second_camera["position_m"])
        first_q = _vector4(first_camera["orientation_xyzw"])
        second_q = _vector4(second_camera["orientation_xyzw"])
        # q and -q represent the same rotation.  For unit quaternions their
        # absolute dot product is cos(half the relative rotation angle).
        dot = abs(sum(a * b for a, b in zip(first_q, second_q)))
        return distance(first_position, second_position) <= .08 and dot >= math.cos(math.radians(2.5))
    except (KeyError, NavigationError, TypeError, ValueError):
        return False


def _vector4(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise NavigationError("invalid_pose")
    if any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in value):
        raise NavigationError("invalid_pose")
    norm = math.sqrt(sum(float(n) * float(n) for n in value))
    if norm < .999 or norm > 1.001:
        raise NavigationError("invalid_pose")
    return tuple(float(n) for n in value)


def portable_rgb_bytes(capture: dict) -> bytes:
    """Return only the validated RGB plane for the VLM boundary."""

    try:
        rgb = capture["rgb"]
        image = base64.b64decode(rgb["data_base64"], validate=True)
        if hashlib.sha256(image).hexdigest() != rgb["sha256"]:
            raise NavigationError("portable_rgb_hash_mismatch")
        if rgb["encoding"] == "png" and not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise NavigationError("portable_rgb_invalid")
        if rgb["encoding"] == "jpeg" and not image.startswith(b"\xff\xd8"):
            raise NavigationError("portable_rgb_invalid")
        return image
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise NavigationError("portable_rgb_invalid") from exc


def verified_arrival(job: dict, world: dict) -> float:
    result = world.get("raw", {}).get("avatar_state", {}).get("last_navigation_result", {})
    radius = result.get("effective_arrival_radius")
    if (result.get("command_id") != job.get("command_id") or result.get("target_node") != job.get("target_node")
        or result.get("ok") is not True or result.get("status") != "completed" or result.get("target_valid") is not True
        or isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0 < radius <= 5):
        raise NavigationError("arrival_not_verified")
    matches = [r for r in world.get("nearby_objects", []) if r.get("name") == job["target_node"]]
    if (len(matches) != 1 or not result.get("target_instance_id")
        or result["target_instance_id"] != job.get("target_instance_id")
        or matches[0].get("instance_id") != result["target_instance_id"]
        or distance(matches[0].get("position"), result.get("target_position")) > .15):
        raise NavigationError("arrival_target_changed")
    remaining = distance(world.get("avatar_position"), matches[0].get("position"), flat=True)
    if remaining > radius + .12 or distance(world.get("avatar_position"), result.get("approach_position"), flat=True) > .35:
        raise NavigationError("arrival_not_verified")
    return remaining


class VisualNavigation:
    def __init__(
        self,
        runtime_root: Path,
        bridge_url: str,
        llm_url: str,
        *,
        frame_dir: Path | None = None,
        portable_adapter: PortableNavigationAdapter | None = None,
    ) -> None:
        self.bridge_url = bridge_url.rstrip("/")
        self.llm_url = llm_url.rstrip("/").removesuffix("/v1")
        configured_frame_dir = frame_dir or Path(
            os.environ.get("LUMINA_EYE_CAPTURE_DIR", "").strip()
            or runtime_root / "artifacts/lumina_eye_capture"
        )
        self.frame_dir = Path(configured_frame_dir).expanduser().resolve()
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        self._surface_ids: dict[str, str] = {}
        self._surface_roles: dict[str, str] = {}
        # Initial arrival certifications hold the opaque surface id.  They are
        # written once per request, kept read-only, and never copied onto the
        # status-visible job dictionaries.
        self._arrival_certifications: dict[str, Mapping[str, Any]] = {}
        self._arrival_fast_path_last: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        self.active_id: str | None = None
        self.unconfirmed_stop: dict | None = None
        self.lock = asyncio.Lock()
        self.resource_budget = VisualResourceBudget()
        self.portable_environment_id = os.environ.get("LUMINA_PORTABLE_ENVIRONMENT_ID", "environment:godot").strip() or "environment:godot"
        self.portable_session_id = "session:" + uuid.uuid4().hex
        self.portable_adapter: PortableNavigationAdapter = portable_adapter or GodotPortableNavigationAdapter(
            self.frame_dir, self.bridge_url
        )

    def status(self, *, phase_snapshot: dict | None = None) -> dict:
        grounding_basis = "rgb_bbox_then_metric_depth" if portable_enabled() else "image_recognition_then_simulation_geometry"
        selection_revision = (
            OPEN_VOCAB_SELECTION_POLICY_REVISION if portable_enabled() else SELECTION_POLICY_REVISION
        )
        return {"enabled": enabled(), "portable_navigation_enabled": portable_enabled(), "active_request_id": self.active_id,
                "capture_directory": str(self.frame_dir),
                "inventory_wire_format": INVENTORY_WIRE_FORMAT,
                "selection_policy_revision": selection_revision,
                "grounding_policy_revision": GROUNDING_POLICY_REVISION if portable_enabled() else None,
                "arrival_fast_path_enabled": arrival_fast_path_enabled(),
                "arrival_fast_path_revision": (
                    ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION if arrival_fast_path_enabled() else None
                ),
                "arrival_fast_path_last_outcome": (
                    self._arrival_fast_path_last.get("outcome") if self._arrival_fast_path_last else None
                ),
                "arrival_fast_path_last_reason": (
                    self._arrival_fast_path_last.get("reason") if self._arrival_fast_path_last else None
                ),
                "arrival_fast_path_last_semantic_revalidation_performed": (
                    self._arrival_fast_path_last.get("semantic_revalidation_performed")
                    if self._arrival_fast_path_last else None
                ),
                "status_probe_revision": PHASE_STATUS_REVISION,
                "resource_monitor_scope": "visual_job_including_speech" if enabled() and phase_enabled() else "perception_only",
                "unconfirmed_stop_command_id": self.unconfirmed_stop.get("stop_expected_command_id") if self.unconfirmed_stop else None,
                "jobs": list(self.jobs.values())[-20:], "grounding_basis": grounding_basis,
                "resource_budget": self.resource_budget.status(),
                "heavy_phase": phase_status() if phase_snapshot is None else phase_snapshot}

    async def _http(self, method: str, path: str, body: dict | None = None, *, model: bool = False) -> dict:
        # Validate at use, not module construction: the opt-in feature must not
        # break an unrelated existing provider while it is disabled.
        parsed = urlparse(self.llm_url if model else self.bridge_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
            raise NavigationError("visual_navigation_requires_local_services")
        async with httpx.AsyncClient(
            trust_env=False,
            timeout=VISUAL_MODEL_HTTP_TIMEOUT_SECONDS if model else 5,
        ) as client:
            response = await client.request(method, (self.llm_url if model else self.bridge_url) + path, json=body)
            response.raise_for_status()
            result = response.json()
            if model is True and method == "GET" and path == "/slots":
                return {"slots": _idle_model_slots(result)}
            if not isinstance(result, dict):
                raise NavigationError("invalid_service_response")
            return result

    async def _world(self) -> dict:
        world = await self._http("GET", "/world-state")
        if not 0 <= time.time() - float(world.get("updated_at", 0)) <= 3 or not world.get("navigation_ready"):
            raise NavigationError("world_not_ready")
        return world

    async def _command(self, action: str, request_id: str, *, command_id: str | None = None, **kwargs: Any) -> tuple[str, dict]:
        command_id = command_id or uuid.uuid4().hex
        result = await self._http("POST", "/command", {
            "command_id": command_id, "correlation_id": request_id, "actor_id": "toha",
            "action": action, "priority": 10 if action == "stop" else 5,
            "reason": "lumina-visual-navigation", "expires_in_ms": 3000, **kwargs})
        if result.get("ok") is not True or result.get("status") != "sent" or result.get("godot_connected") is not True:
            raise NavigationError("command_not_sent")
        return command_id, result

    async def start(self, text: str, request_id: str | None = None, *, speak: bool = True, allow_move: bool = True, only_if_idle: bool = False) -> dict:
        if not enabled():
            raise NavigationError("visual_navigation_disabled")
        if type(only_if_idle) is not bool:
            raise NavigationError("invalid_idle_admission_policy")
        if not text.strip() or len(text) > 400:
            raise NavigationError("invalid_request_text")
        request_id = request_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", request_id):
            raise NavigationError("invalid_request_id")
        async with self.lock:
            if request_id in self.jobs:
                return dict(self.jobs[request_id])
            # Background decisions may not cancel a newer human request after
            # their earlier activity probe. Check atomically under the same
            # lock used by every start/cancel, before any cancellation or I/O.
            if only_if_idle and (
                self.active_id
                or (self.task is not None and not self.task.done())
                or self.unconfirmed_stop is not None
            ):
                raise NavigationError("visual_navigation_busy")
            if self.active_id and not allow_move and not speak:
                raise NavigationError("visual_navigation_busy")
            await self._cancel_active()
            if self.unconfirmed_stop:
                await self._safe_stop(self.unconfirmed_stop)
                if self.unconfirmed_stop:
                    raise NavigationError("previous_stop_unconfirmed")
            job = {"request_id": request_id, "status": "observing", "started_unix": time.time(),
                   "admission_policy": "idle_only" if only_if_idle else "replace_owned_job",
                   "arrived": False, "answer": "今の視界を確認するね。", "speak": speak,
                   "allow_move": allow_move and bool(MOVE.search(text)),
                   "recognition_basis": "raw_eye_image_only",
                   "grounding_basis": "metric_depth_after_recognition" if portable_enabled() else "simulation_geometry_after_recognition",
                   "portable_navigation": portable_enabled(),
                   "grounding_policy_revision": GROUNDING_POLICY_REVISION if portable_enabled() else None,
                   "selection_policy_revision": (
                       OPEN_VOCAB_SELECTION_POLICY_REVISION if portable_enabled() else SELECTION_POLICY_REVISION
                   )}
            if job["portable_navigation"]:
                job["arrival_revalidation_revision"] = ARRIVAL_REVALIDATION_REVISION
                job["arrival_fast_path_enabled"] = arrival_fast_path_enabled()
            self.jobs[request_id] = job
            while len(self.jobs) > 64:
                expired_request_id, _ = self.jobs.popitem(last=False)
                self._surface_ids.pop(expired_request_id, None)
                self._surface_roles.pop(expired_request_id, None)
                self._arrival_certifications.pop(expired_request_id, None)
            self.active_id = request_id
            self.task = asyncio.create_task(self._run(job, text), name=f"visual-nav-{request_id}")
            return dict(job)

    async def cancel(self, request_id: str | None = None) -> bool:
        async with self.lock:
            if not self.active_id and self.unconfirmed_stop and (not request_id or request_id == self.unconfirmed_stop["request_id"]):
                await self._safe_stop(self.unconfirmed_stop)
                return True
            if not self.active_id or (request_id and request_id != self.active_id):
                return False
            await self._cancel_active()
            return True

    async def _cancel_active(self) -> None:
        cancelled_id = self.active_id
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if cancelled_id and self.jobs[cancelled_id]["status"] not in TERMINAL:
            self.jobs[cancelled_id].update(status="cancelled", arrived=False,
                answer="移動を中止したよ。", finished_unix=time.time())
        self.task = None
        self.active_id = None

    async def _fresh_frame(self, newer_than: float) -> tuple[dict, bytes]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return read_frame(self.frame_dir, newer_than=newer_than)
            except NavigationError:
                await asyncio.sleep(.15)
        raise NavigationError("eye_frame_unavailable")

    def _portable_common_request(self, schema: str, request_id: str) -> dict[str, Any]:
        return {
            "schema_version": schema,
            "request_id": request_id,
            "environment_id": self.portable_environment_id,
            "session_id": self.portable_session_id,
            "requested_unix": time.time(),
        }

    async def _portable_capture_after(
        self,
        newer_than: float,
        *,
        require_depth: bool,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 5.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            request = {
                **self._portable_common_request(CAPTURE_REQUEST_SCHEMA, "capture:" + uuid.uuid4().hex),
                "require_depth": require_depth,
                "max_age_ms": 1500,
            }
            try:
                capture = validate_capture_result(
                    await self.portable_adapter.capture(request),
                    request=request,
                    now_unix=time.time(),
                )
                if float(capture["captured_unix"]) > newer_than and capture.get("frame_id"):
                    return capture
            except (PortableNavigationContractError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
            await asyncio.sleep(.15)
        if isinstance(last_error, PortableNavigationContractError):
            raise NavigationError(last_error.code.lower()) from last_error
        raise NavigationError("portable_capture_unavailable") from last_error

    @staticmethod
    def _portable_detection_descriptor(detection: dict) -> tuple[str, str, str]:
        label = str(detection.get("label") or "").strip()
        if not label or len(label) > 40:
            raise NavigationError("portable_detection_label_invalid")
        reported_color = detection.get("color")
        try:
            category, color = open_visual_descriptor(
                label,
                reported_color if isinstance(reported_color, str) else None,
            )
        except PerceptionError as exc:
            raise NavigationError(str(exc)) from exc
        reported_category = detection.get("category")
        if reported_category not in (None, "", category):
            raise NavigationError("portable_detection_category_mismatch")
        if reported_color not in (None, "", color):
            raise NavigationError("portable_detection_color_mismatch")
        return label, category, color

    def _record_arrival_certification(
        self,
        job: dict,
        *,
        semantic_capture: dict,
        semantic_map: dict,
        post_inference_capture: dict,
        navigation_map: dict,
        semantic_grounded: dict,
        post_inference_grounded: dict,
        semantic_ground_request_id: str,
        post_inference_ground_request_id: str,
        expected_surface_role: Any,
        surface_points: list,
    ) -> None:
        """Freeze the grounding-time proof consumed by the arrival fast path.

        Called exactly once per request after both groundings succeeded.  The
        certification carries the opaque surface id, so it is stored in a
        private read-only mapping and never on the status-visible job.  Its
        SHA-256 detects corruption/unsigned modification only; it does not
        authenticate the certification.
        """

        request_id = job["request_id"]
        if request_id in self._arrival_certifications:
            return
        surface_id = post_inference_grounded.get("surface_id")
        if not isinstance(surface_id, str):
            surface_id = None
        surface_role = post_inference_grounded.get("surface_role")
        if surface_id is None or not isinstance(surface_role, str):
            surface_role = None
        semantic_revision = (semantic_capture.get("depth") or {}).get(
            "surface_id_semantics_revision"
        )
        post_inference_revision = (post_inference_capture.get("depth") or {}).get(
            "surface_id_semantics_revision"
        )
        points = tuple(tuple(_vector(point)) for point in surface_points[:256])
        certification: dict[str, Any] = {
            "certification_revision": INITIAL_CERTIFICATION_REVISION,
            "certified_unix": float(post_inference_capture["captured_unix"]),
            "environment_id": self.portable_environment_id,
            "session_id": self.portable_session_id,
            "coordinate_frame_id": navigation_map["coordinate_frame_id"],
            "target_id": post_inference_grounded["target_id"],
            "expected_surface_role": (
                expected_surface_role if isinstance(expected_surface_role, str) else None
            ),
            "surface_id": surface_id,
            "surface_role": surface_role,
            "surface_id_semantics_revision": (
                post_inference_revision if isinstance(post_inference_revision, str) else None
            ),
            "surface_points_m": points,
            "surface_sha256": portable_surface_sha256(list(points)),
            "semantic_frame_id": semantic_capture["frame_id"],
            "semantic_capture_request_id": semantic_capture["request_id"],
            "post_inference_frame_id": post_inference_capture["frame_id"],
            "post_inference_capture_request_id": post_inference_capture["request_id"],
            "semantic_map_id": semantic_map["map_id"],
            "semantic_map_request_id": semantic_map["request_id"],
            "navigation_map_id": navigation_map["map_id"],
            "navigation_map_request_id": navigation_map["request_id"],
            "semantic_ground_request_id": semantic_ground_request_id,
            "post_inference_ground_request_id": post_inference_ground_request_id,
            "dual_grounding_surface_id_match": (
                semantic_grounded.get("surface_id") == post_inference_grounded.get("surface_id")
            ),
            "dual_grounding_surface_role_match": (
                semantic_grounded.get("surface_role") == post_inference_grounded.get("surface_role")
            ),
            "dual_grounding_semantics_revision_match": (
                semantic_revision == post_inference_revision
            ),
        }
        certification["certification_sha256"] = compute_initial_certification_digest(
            certification
        )
        self._arrival_certifications[request_id] = MappingProxyType(certification)
        job.update(
            arrival_fast_path_certification_recorded=True,
            arrival_fast_path_certification_identity_basis=(
                "opaque_physics_surface_id" if surface_id is not None else "metric_geometry_only"
            ),
            arrival_fast_path_certification_role_present=surface_role is not None,
            arrival_fast_path_certification_point_count=len(points),
        )

    async def _portable_arrival_fast_path(
        self,
        job: dict,
        feedback: dict,
        feedback_request: dict,
        previous_capture: dict,
        *,
        arrival_radius: float,
    ) -> tuple[bool, dict | None]:
        """Try to confirm arrival without Qwen.

        Returns ``(confirmed, newest_fresh_frame)``.  Performs exactly two fresh
        RGB-D captures after ``max(previous_capture.captured_unix,
        feedback.observed_unix)`` and one fresh map build from the newest frame,
        then delegates the decision to the pure evaluator.  No inference lease is
        taken and no model wake happens here because no model runs.

        ``pass`` returns ``(True, frame2)`` and the caller skips the semantic
        revalidation.  ``fallback_eligible`` returns ``(False, frame2)`` so the
        unchanged slow path captures strictly newer frames.  ``hard_fail``
        raises ``NavigationError`` and is never rescued.  A missing private
        certification is an internal contract failure.  An existing
        certification with missing/unsupported identity or role still runs the
        fresh evidence and safety checks before it may become fallback-eligible;
        this keeps a simultaneous integrity, motion, map, or feedback violation
        from being hidden by an observation gap.  Every other exception,
        including ``asyncio.CancelledError``, propagates unchanged.
        """

        audit: dict[str, Any] = {
            "revision": ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
            "status": "checking_certification",
            "started_unix": time.time(),
            "semantic_revalidation_performed": False,
            "qwen_invoked": False,
            "inference_lease_held": False,
            "model_wake_performed": False,
            "arrival_radius_m": arrival_radius,
            "fresh_capture_count": 0,
            "fresh_map_count": 0,
        }
        job["arrival_fast_path"] = audit
        certification = self._arrival_certifications.get(job["request_id"])
        if certification is None:
            # With the flag sampled on, recognition must have recorded exactly
            # one private certification before navigation was dispatched.  Do
            # not let missing internal evidence fall through to a semantic
            # success: it is a fail-closed contract error.
            audit.update(
                status=OUTCOME_HARD_FAIL,
                outcome=OUTCOME_HARD_FAIL,
                reason="contract_invalid",
                contract_error="CERTIFICATION_UNAVAILABLE",
                qwen_fallback_allowed=False,
                finished_unix=time.time(),
            )
            self._arrival_fast_path_last = {
                "request_id": job["request_id"],
                "outcome": OUTCOME_HARD_FAIL,
                "reason": "contract_invalid",
                "semantic_revalidation_performed": False,
                "finished_unix": audit["finished_unix"],
            }
            raise NavigationError("arrival_fast_path_hard_fail:contract_invalid")
        cutoff = max(float(previous_capture["captured_unix"]), float(feedback["observed_unix"]))
        audit.update(status="capturing", cutoff_unix=cutoff)
        try:
            first = await self._portable_capture_after(cutoff, require_depth=True)
            audit["fresh_capture_count"] = 1
            second = await self._portable_capture_after(
                float(first["captured_unix"]),
                require_depth=True,
            )
            audit["fresh_capture_count"] = 2
            map_request = {
                **self._portable_common_request(
                    MAP_REQUEST_SCHEMA,
                    "arrival-fast-path-map:" + uuid.uuid4().hex,
                ),
                "source_frame_id": second["frame_id"],
                "resolution_m": .20,
                "footprint_radius_m": .35,
            }
            fresh_map = validate_map_result(
                await self.portable_adapter.build_map(map_request),
                request=map_request,
                now_unix=time.time(),
            )
            audit["fresh_map_count"] = 1
            audit.update(
                status="evaluating",
                fresh_frame_ids=[first["frame_id"], second["frame_id"]],
                fresh_frame_captured_unix=[first["captured_unix"], second["captured_unix"]],
                fresh_map_id=fresh_map["map_id"],
                fresh_map_source_frame_id=fresh_map["source_frame_id"],
            )
            result = evaluate_arrival_pose_epoch_fast_path(
                certification,
                (first, second),
                fresh_map,
                fresh_map_request=map_request,
                arrived_feedback=feedback,
                arrived_feedback_request=feedback_request,
                arrival_radius_m=arrival_radius,
                not_before_unix=cutoff,
                now_unix=time.time(),
            )
        except BaseException:
            # Nothing is swallowed here: cancellation, adapter failures, contract
            # errors and resource trips all propagate.  Only the audit status
            # is finalized so the job records where the fast path stopped.
            audit.update(status="failed", finished_unix=time.time())
            raise
        # The evaluator never copies surface ids into its result; only match
        # booleans and counts are recorded on the job.
        audit.update(result)
        audit.update(status=result["outcome"], finished_unix=time.time())
        self._arrival_fast_path_last = {
            "request_id": job["request_id"],
            "outcome": result["outcome"],
            "reason": result["reason"],
            "semantic_revalidation_performed": False,
            "finished_unix": audit["finished_unix"],
        }
        if result["outcome"] == OUTCOME_HARD_FAIL:
            raise NavigationError("arrival_fast_path_hard_fail:" + str(result["reason"]))
        if result["outcome"] != OUTCOME_PASS:
            return False, second
        job.update(
            arrival_distance_m=result["arrival_distance_m"],
            arrival_confirmation_basis="pose_epoch_fast_path_dual_fresh_rgbd_exact_surface",
            arrival_revalidation={
                "revision": ARRIVAL_REVALIDATION_REVISION,
                "status": "skipped",
                "skipped_by": ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
                "semantic_revalidation_performed": False,
                "started_unix": audit["started_unix"],
                "finished_unix": audit["finished_unix"],
            },
        )
        return True, second

    async def _portable_revalidate_arrival(
        self,
        job: dict,
        feedback: dict,
        previous_capture: dict,
        *,
        arrival_radius: float,
    ) -> float:
        """Require a new semantic RGB-D observation before claiming arrival."""

        audit = {
            "revision": ARRIVAL_REVALIDATION_REVISION,
            "status": "capturing",
            "started_unix": time.time(),
            "original_inventory_label": job["target_inventory_label"],
            "original_category": job["target_category"],
            "original_color": job["target_color"],
            "relative_side_reapplied": False,
            "target_sent_to_visual_pass": False,
            "world_metadata_sent": False,
            "fresh_agent_position_basis": "post_inference_capture_synchronized_map",
            "expected_surface_role": job.get("expected_surface_role"),
            "surface_role_policy_revision": PORTABLE_SURFACE_ROLE_POLICY_REVISION,
        }
        job["arrival_revalidation"] = audit
        try:
            async with inference_lease():
                try:
                    capture_after = max(
                        float(previous_capture["captured_unix"]),
                        float(feedback["observed_unix"]),
                    )
                    if phase_enabled():
                        job["arrival_phase_lease_held"] = True
                        await self._wake_model(
                            job,
                            audit_field="arrival_model_wake",
                            latency_field="arrival_model_wake_latency_ms",
                        )
                        capture_after = max(capture_after, time.time())
                    semantic_capture = await self._portable_capture_after(
                        capture_after,
                        require_depth=True,
                    )
                    semantic_image = portable_rgb_bytes(semantic_capture)
                    audit.update(
                        status="inferring",
                        semantic_frame_id=semantic_capture["frame_id"],
                        semantic_image_sha256=semantic_capture["rgb"]["sha256"],
                        semantic_captured_unix=semantic_capture["captured_unix"],
                        semantic_rgbd=True,
                    )
                    semantic_map_request = {
                        **self._portable_common_request(
                            MAP_REQUEST_SCHEMA,
                            "arrival-semantic-map:" + uuid.uuid4().hex,
                        ),
                        "source_frame_id": semantic_capture["frame_id"],
                        "resolution_m": .20,
                        "footprint_radius_m": .35,
                    }
                    semantic_map = validate_map_result(
                        await self.portable_adapter.build_map(semantic_map_request),
                        request=semantic_map_request,
                        now_unix=time.time(),
                    )
                    if semantic_map["coordinate_frame_id"] != job["coordinate_frame_id"]:
                        raise NavigationError("arrival_coordinate_frame_changed")
                    audit.update(
                        semantic_map_id=semantic_map["map_id"],
                        semantic_map_source_frame_id=semantic_map["source_frame_id"],
                    )
                    # This synthetic selector request contains only the original
                    # validated class/color. Initial left/right/center wording is
                    # intentionally discarded because the viewpoint has changed.
                    selector_text = (
                        "revalidate unique visible category: "
                        + job["target_category"]
                    )
                    inference_started = time.monotonic()
                    detection = await self._infer(semantic_image, selector_text)
                    fragment_union_used = False
                    if detection.get("visible") is not True:
                        fragment_union = portable_arrival_fragment_union(
                            detection,
                            requested_category=job["target_category"],
                            original_color=job["target_color"],
                            reference_surface_id=self._surface_ids.get(
                                job["request_id"]
                            ),
                            reference_surface_role=self._surface_roles.get(
                                job["request_id"]
                            ),
                            expected_surface_role=job.get("expected_surface_role"),
                        )
                        if fragment_union is not None:
                            detection = fragment_union
                            fragment_union_used = True
                    audit.update(
                        selection=detection,
                        inference_latency_ms=round((time.monotonic() - inference_started) * 1000),
                    )
                    if fragment_union_used:
                        audit["arrival_fragment_union"] = detection[
                            "perception_audit"
                        ]["arrival_fragment_union"]
                    if detection.get("visible") is not True:
                        inventory = (
                            detection.get("perception_audit", {}).get("inventory", [])
                            if isinstance(detection.get("perception_audit"), dict)
                            else []
                        )
                        same_category = 0
                        for item in inventory if isinstance(inventory, list) else []:
                            try:
                                item_category, _item_color = open_visual_descriptor(
                                    item.get("label"), item.get("color")
                                )
                            except (AttributeError, PerceptionError):
                                continue
                            same_category += item_category == job["target_category"]
                        if same_category > 1:
                            raise NavigationError("ambiguous_arrival_visual_target")
                        raise NavigationError("not_visible")
                    confidence = detection.get("confidence")
                    if (
                        isinstance(confidence, bool)
                        or not isinstance(confidence, (int, float))
                        or not OPEN_VOCAB_CONFIDENCE_THRESHOLD <= confidence <= 1.0
                    ):
                        raise NavigationError("open_vocab_selection_low_confidence")
                    label, category, color = self._portable_detection_descriptor(detection)
                    original_color_base = visual_color_base(job["target_color"])
                    current_color_base = visual_color_base(color)
                    ordinary_color_match = visual_colors_compatible(
                        color, job["target_color"]
                    )
                    provisional_color_match = (
                        not ordinary_color_match
                        and frozenset({original_color_base, current_color_base})
                        == ARRIVAL_PROVISIONAL_COLOR_PAIR
                    )
                    audit.update(
                        current_category=category,
                        current_color=color,
                        original_color_base=original_color_base,
                        current_color_base=current_color_base,
                        shade_variation_accepted=(
                            color != job["target_color"]
                            and ordinary_color_match
                        ),
                        provisional_photometric_color_continuity={
                            "pair": sorted({original_color_base, current_color_base}),
                            "status": "pending_metric_continuity",
                            "accepted": False,
                            "maximum_reference_distance_m": (
                                ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                            ),
                        } if provisional_color_match else None,
                    )
                    if (
                        category != job["target_category"]
                        or not (ordinary_color_match or provisional_color_match)
                    ):
                        raise NavigationError("arrival_target_semantics_changed")
                    bbox = valid_bbox(detection.get("bbox"))
                    arrival_occluder_bboxes = portable_occluder_bboxes(detection, bbox)
                    audit["grounding_occluder_bbox_count"] = len(arrival_occluder_bboxes)
                    grounding_capture = await self._portable_capture_after(
                        float(semantic_capture["captured_unix"]),
                        require_depth=True,
                    )
                    pose_ok = portable_pose_matches(semantic_capture, grounding_capture)
                    audit.update(
                        grounding_frame_id=grounding_capture["frame_id"],
                        grounding_image_sha256=grounding_capture["rgb"]["sha256"],
                        grounding_captured_unix=grounding_capture["captured_unix"],
                        synchronized_camera_match=pose_ok,
                    )
                    if not pose_ok:
                        raise NavigationError("view_changed_during_arrival_revalidation")
                finally:
                    if "arrival_phase_lease_held" in job:
                        job["arrival_phase_lease_held"] = False
                    if "arrival_model_wake" in job:
                        job["arrival_model_wake"]["phase_lease_held"] = False

            final_map_request = {
                **self._portable_common_request(MAP_REQUEST_SCHEMA, "arrival-map:" + uuid.uuid4().hex),
                "source_frame_id": grounding_capture["frame_id"],
                "resolution_m": .20,
                "footprint_radius_m": .35,
            }
            final_map = validate_map_result(
                await self.portable_adapter.build_map(final_map_request),
                request=final_map_request,
                now_unix=time.time(),
            )
            if final_map["coordinate_frame_id"] != job["coordinate_frame_id"]:
                raise NavigationError("arrival_coordinate_frame_changed")
            # Final safety gate on the post-Qwen map: the agent's own cell must
            # stay free after the same binary occupancy inflation the fast path
            # applies.  This is not a numeric clearance measurement.
            final_agent_position = list(final_map["agent"]["position_m"])
            final_map_collision = path_collision_report(
                final_map,
                [final_agent_position, final_agent_position],
                additional_inflation_m=AGENT_ADDITIONAL_INFLATION_M,
            )
            audit["final_map_inflated_occupancy_proof"] = {
                "kind": "binary_inflated_occupancy_proof",
                "additional_inflation_m": AGENT_ADDITIONAL_INFLATION_M,
                "footprint_radius_m": final_map["agent"]["footprint_radius_m"],
                "numeric_clearance_measured": False,
                "agent_cell_free": final_map_collision["valid"] is True,
                "reason": final_map_collision.get("reason"),
            }
            if final_map_collision["valid"] is not True:
                raise NavigationError(
                    "arrival_final_map_agent_out_of_bounds"
                    if final_map_collision.get("reason") == "out_of_bounds"
                    else "arrival_final_map_agent_blocked_after_inflation"
                )
            ground_detection_payload = {
                "detection_id": "arrival-detection:" + uuid.uuid4().hex,
                "label": label,
                "confidence": float(confidence),
                "bbox_norm": bbox,
            }
            expected_surface_role = job.get("expected_surface_role")
            if expected_surface_role in {"navigation_obstacle", "walkable_ground"}:
                ground_detection_payload["expected_surface_role"] = expected_surface_role
            reference_surface = job.get("target_surface_points_m")
            if not isinstance(reference_surface, list) or not reference_surface:
                raise NavigationError("arrival_reference_surface_missing")
            continuity_surface = {
                "target_id": job["target_id"],
                "points_m": reference_surface,
                "maximum_distance_m": ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M,
            }
            reference_surface_id = self._surface_ids.get(job["request_id"])
            if isinstance(reference_surface_id, str):
                continuity_surface["surface_id"] = reference_surface_id
            reference_surface_role = self._surface_roles.get(job["request_id"])
            if (
                isinstance(reference_surface_id, str)
                and isinstance(reference_surface_role, str)
            ):
                continuity_surface["surface_role"] = reference_surface_role
            if fragment_union_used and not (
                isinstance(reference_surface_id, str)
                and isinstance(reference_surface_role, str)
                and reference_surface_role == expected_surface_role
            ):
                raise NavigationError("ambiguous_arrival_visual_target")
            reference_sha256 = portable_surface_sha256(reference_surface)
            if fragment_union_used:
                (
                    arrival_occluder_bboxes,
                    fragment_occluder_audit,
                ) = portable_arrival_fragment_occluder_policy(
                    (semantic_capture, grounding_capture),
                    target_bbox=bbox,
                    occluder_bboxes=arrival_occluder_bboxes,
                    reference_surface_points=reference_surface,
                    reference_surface_id=reference_surface_id,
                    reference_surface_role=reference_surface_role,
                    expected_surface_role=expected_surface_role,
                    surface_id_semantics_revisions=(
                        job.get("grounding_surface_id_semantics_revision"),
                        (semantic_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                        (grounding_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                    ),
                )
                audit["arrival_fragment_union"]["occluder_policy"] = (
                    fragment_occluder_audit
                )
                audit["grounding_occluder_bbox_raw_count"] = (
                    fragment_occluder_audit[
                        "raw_visual_inventory_occluder_count"
                    ]
                )
                audit["grounding_occluder_bbox_count"] = len(
                    arrival_occluder_bboxes
                )
            if arrival_occluder_bboxes:
                ground_detection_payload["occluder_bboxes_norm"] = (
                    arrival_occluder_bboxes
                )
            audit.update(
                reference_surface_target_id=job["target_id"],
                reference_surface_identity_basis=(
                    "opaque_physics_surface_id"
                    if isinstance(reference_surface_id, str)
                    else "metric_geometry_only"
                ),
                reference_surface_id_present=isinstance(reference_surface_id, str),
                reference_surface_role=reference_surface_role,
                reference_surface_role_present=isinstance(reference_surface_role, str),
                reference_surface_sha256=reference_sha256,
                reference_surface_point_count=len(reference_surface),
                reference_surface_maximum_distance_m=(
                    ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                ),
                reference_surface_applied_to_frames=[
                    semantic_capture["frame_id"],
                    grounding_capture["frame_id"],
                ],
            )
            ground_request = {
                **self._portable_common_request(GROUND_REQUEST_SCHEMA, "arrival-ground:" + uuid.uuid4().hex),
                "source_frame_id": grounding_capture["frame_id"],
                "map_id": final_map["map_id"],
                "detection": ground_detection_payload,
                "arrival_continuity_surface": continuity_surface,
            }
            semantic_ground_request = {
                **ground_request,
                "request_id": "arrival-ground-check:" + uuid.uuid4().hex,
                "requested_unix": time.time(),
                "source_frame_id": semantic_capture["frame_id"],
                "map_id": semantic_map["map_id"],
            }
            semantic_grounded = ground_bbox_to_depth(
                semantic_ground_request,
                semantic_capture,
                grounded_unix=time.time(),
            )
            grounded = validate_ground_result(
                await self.portable_adapter.ground(ground_request),
                request=ground_request,
                now_unix=time.time(),
            )
            sofa_part_union_candidate_generated = False
            normal_groundings = (semantic_grounded, grounded)
            normal_dual_ambiguous = all(
                result.get("status") == "unresolved"
                and result.get("reason") == "ambiguous"
                for result in normal_groundings
            )
            if normal_dual_ambiguous and not fragment_union_used:
                audit["arrival_sofa_part_union_recovery"] = {
                    "attempted": True,
                    "trigger": "normal_dual_grounding_unresolved_ambiguous",
                    "accepted": False,
                }
                part_union = portable_arrival_sofa_part_union(
                    detection,
                    requested_category=job["target_category"],
                    original_color=job["target_color"],
                    reference_surface_id=reference_surface_id,
                    reference_surface_role=reference_surface_role,
                    expected_surface_role=expected_surface_role,
                    normal_groundings=normal_groundings,
                    surface_id_semantics_revisions=(
                        job.get("grounding_surface_id_semantics_revision"),
                        (semantic_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                        (grounding_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                    ),
                    initial_surface_sha256=reference_sha256,
                    expected_initial_surface_sha256=job.get(
                        "target_surface_sha256"
                    ),
                )
                if part_union is not None:
                    normal_result_audit = {
                        basis: {
                            "status": result.get("status"),
                            "reason": result.get("reason"),
                            "surface_point_count": len(
                                result.get("surface_points_m") or []
                            ),
                        }
                        for basis, result in (
                            ("semantic", semantic_grounded),
                            ("post_inference", grounded),
                        )
                    }
                    part_bbox = valid_bbox(part_union.get("bbox"))
                    part_occluder_bboxes = portable_occluder_bboxes(
                        part_union, part_bbox
                    )
                    part_detection_payload = {
                        "detection_id": "arrival-part-detection:" + uuid.uuid4().hex,
                        "label": label,
                        "confidence": float(confidence),
                        "bbox_norm": part_bbox,
                        "expected_surface_role": expected_surface_role,
                    }
                    if part_occluder_bboxes:
                        part_detection_payload["occluder_bboxes_norm"] = (
                            part_occluder_bboxes
                        )
                    part_ground_request = {
                        **self._portable_common_request(
                            GROUND_REQUEST_SCHEMA,
                            "arrival-part-ground:" + uuid.uuid4().hex,
                        ),
                        "source_frame_id": grounding_capture["frame_id"],
                        "map_id": final_map["map_id"],
                        "detection": part_detection_payload,
                        "arrival_continuity_surface": continuity_surface,
                    }
                    semantic_part_ground_request = {
                        **part_ground_request,
                        "request_id": (
                            "arrival-part-ground-check:" + uuid.uuid4().hex
                        ),
                        "requested_unix": time.time(),
                        "source_frame_id": semantic_capture["frame_id"],
                        "map_id": semantic_map["map_id"],
                    }
                    semantic_grounded = ground_bbox_to_depth(
                        semantic_part_ground_request,
                        semantic_capture,
                        grounded_unix=time.time(),
                    )
                    grounded = validate_ground_result(
                        await self.portable_adapter.ground(part_ground_request),
                        request=part_ground_request,
                        now_unix=time.time(),
                    )
                    part_union_audit = part_union["perception_audit"][
                        "arrival_sofa_part_union"
                    ]
                    anchor_bbox = [
                        component / 1000.0
                        for component in part_union_audit["anchor_bbox"]
                    ]
                    support_audits = {
                        "semantic": portable_arrival_sofa_part_surface_support(
                            semantic_capture,
                            anchor_bbox=anchor_bbox,
                            union_bbox=part_bbox,
                            reference_surface_points=reference_surface,
                            reference_surface_id=reference_surface_id,
                            reference_surface_role=reference_surface_role,
                        ),
                        "post_inference": portable_arrival_sofa_part_surface_support(
                            grounding_capture,
                            anchor_bbox=anchor_bbox,
                            union_bbox=part_bbox,
                            reference_surface_points=reference_surface,
                            reference_surface_id=reference_surface_id,
                            reference_surface_role=reference_surface_role,
                        ),
                    }
                    audit["arrival_sofa_part_union"] = {
                        **part_union_audit,
                        "normal_grounding_results": normal_result_audit,
                        "second_stage_grounding_bbox": part_bbox,
                        "second_stage_grounding_occluder_bbox_count": len(
                            part_occluder_bboxes
                        ),
                        "surface_support": support_audits,
                    }
                    audit["grounding_occluder_bbox_count"] = len(
                        part_occluder_bboxes
                    )
                    if not all(
                        support.get("passed") is True
                        for support in support_audits.values()
                    ):
                        raise NavigationError(
                            "arrival_target_identity_discontinuous"
                        )
                    sofa_part_union_candidate_generated = True
                    audit["arrival_sofa_part_union_recovery"].update(
                        candidate_generated=True,
                        support_verified=True,
                    )
            audit["reference_grounding_results"] = {
                "semantic": {
                    "status": semantic_grounded.get("status"),
                    "reason": semantic_grounded.get("reason"),
                    "target_id": semantic_grounded.get("target_id"),
                    "surface_id_match": (
                        reference_surface_id is None
                        or semantic_grounded.get("surface_id") == reference_surface_id
                    ),
                    "surface_role_match": (
                        reference_surface_role is None
                        or semantic_grounded.get("surface_role") == reference_surface_role
                    ),
                    "surface_point_count": len(
                        semantic_grounded.get("surface_points_m") or []
                    ),
                },
                "post_inference": {
                    "status": grounded.get("status"),
                    "reason": grounded.get("reason"),
                    "target_id": grounded.get("target_id"),
                    "surface_id_match": (
                        reference_surface_id is None
                        or grounded.get("surface_id") == reference_surface_id
                    ),
                    "surface_role_match": (
                        reference_surface_role is None
                        or grounded.get("surface_role") == reference_surface_role
                    ),
                    "surface_point_count": len(grounded.get("surface_points_m") or []),
                },
            }
            unresolved_reference_checks = [
                {
                    "basis": basis,
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "distance_m": None,
                    "maximum_allowed_distance_m": (
                        ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                    ),
                    "continuous": False,
                }
                for basis, result in (
                    ("semantic", semantic_grounded),
                    ("post_inference", grounded),
                )
                if result.get("status") != "grounded"
            ]
            if unresolved_reference_checks:
                audit["target_continuity_checks"] = unresolved_reference_checks
            for grounded_result in (semantic_grounded, grounded):
                if grounded_result.get("status") != "grounded":
                    reason = str(grounded_result.get("reason") or "unsupported")
                    if reason == "ambiguous":
                        raise NavigationError("arrival_target_identity_discontinuous")
                    raise NavigationError("portable_ground_" + reason)
                if (
                    float(grounded_result["uncertainty_radius_m"]) > .35
                    or float(grounded_result["confidence"]) < .50
                ):
                    raise NavigationError("portable_ground_uncertain")
                if (
                    isinstance(reference_surface_id, str)
                    and grounded_result.get("surface_id") != reference_surface_id
                ):
                    raise NavigationError("arrival_target_identity_discontinuous")
                if (
                    isinstance(reference_surface_role, str)
                    and grounded_result.get("surface_role") != reference_surface_role
                ):
                    raise NavigationError("arrival_target_identity_discontinuous")
                current_surface_role = grounded_result.get("surface_role")
                if (
                    isinstance(expected_surface_role, str)
                    and isinstance(current_surface_role, str)
                    and current_surface_role != expected_surface_role
                ):
                    raise NavigationError("arrival_target_identity_discontinuous")
            arrival_surface_roles = {
                result.get("surface_role")
                for result in (semantic_grounded, grounded)
                if isinstance(result.get("surface_role"), str)
            }
            if len(arrival_surface_roles) > 1:
                raise NavigationError("arrival_target_identity_discontinuous")
            if (
                semantic_grounded["coordinate_frame_id"] != grounded["coordinate_frame_id"]
                or grounded["coordinate_frame_id"] != job["coordinate_frame_id"]
            ):
                raise NavigationError("arrival_coordinate_frame_changed")
            surface_tolerance = min(
                .30,
                max(
                    .12,
                    float(semantic_grounded["uncertainty_radius_m"])
                    + float(grounded["uncertainty_radius_m"])
                    + .05,
                ),
            )
            if distance(semantic_grounded["position_m"], grounded["position_m"]) > surface_tolerance:
                raise NavigationError("target_changed_during_arrival_revalidation")
            initial_target_position = _vector(job.get("target_position"))
            continuity_checks = []
            for basis, current_grounding in (
                ("semantic", semantic_grounded),
                ("post_inference", grounded),
            ):
                current_uncertainty = float(current_grounding["uncertainty_radius_m"])
                continuity_distance = portable_surface_evidence_distance(
                    current_grounding,
                    reference_surface,
                )
                continuity_checks.append({
                    "basis": basis,
                    "distance_m": continuity_distance,
                    "maximum_allowed_distance_m": (
                        ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                    ),
                    "current_target_position_m": list(
                        _vector(current_grounding["position_m"])
                    ),
                    "current_uncertainty_radius_m": current_uncertainty,
                    "current_surface_point_count": len(
                        current_grounding.get("surface_points_m") or []
                    ),
                    "continuous": (
                        continuity_distance
                        <= ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
                    ),
                })
            audit.update(
                initial_target_position_m=list(initial_target_position),
                target_continuity_formula=(
                    "third_nearest_fresh_rgbd_ray_to_initial_certified_surface<=0.20m"
                ),
                target_continuity_checks=continuity_checks,
            )
            if not all(check["continuous"] for check in continuity_checks):
                raise NavigationError("arrival_target_identity_discontinuous")
            audit["metric_surface_continuity_passed"] = True
            if provisional_color_match:
                audit["provisional_photometric_color_continuity"].update(
                    status="accepted_after_dual_rgbd_reference_match",
                    accepted=True,
                    reference_surface_sha256=reference_sha256,
                )
            uncertainty = float(grounded["uncertainty_radius_m"])
            initial_uncertainty = float(job["target_uncertainty_radius_m"])
            agent_position = _vector(final_map["agent"]["position_m"])
            target_position = _vector(grounded["position_m"])
            fresh_maximum = (
                arrival_radius
                + uncertainty
                + ARRIVAL_REVALIDATION_TOLERANCE_M
            )
            initial_maximum = (
                arrival_radius
                + initial_uncertainty
                + ARRIVAL_REVALIDATION_TOLERANCE_M
            )
            remaining, arrival_distance_evidence = (
                select_portable_arrival_planar_distance(
                    grounded,
                    reference_surface,
                    agent_position,
                    fresh_maximum_allowed_distance_m=fresh_maximum,
                    initial_maximum_allowed_distance_m=initial_maximum,
                    reference_surface_id=reference_surface_id,
                    reference_surface_role=reference_surface_role,
                    expected_surface_role=expected_surface_role,
                    surface_id_semantics_revisions=(
                        job.get("grounding_surface_id_semantics_revision"),
                        (semantic_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                        (grounding_capture.get("depth") or {}).get(
                            "surface_id_semantics_revision"
                        ),
                    ),
                    dual_fresh_groundings=(semantic_grounded, grounded),
                    continuity_checks=continuity_checks,
                    initial_surface_sha256=reference_sha256,
                    expected_initial_surface_sha256=job.get(
                        "target_surface_sha256"
                    ),
                )
            )
            audit.update(arrival_distance_evidence)
            selected_maximum = float(
                arrival_distance_evidence["selected_maximum_allowed_distance_m"]
            )
            audit.update(
                status="passed" if remaining <= selected_maximum else "failed",
                finished_unix=time.time(),
                fresh_agent_position_m=list(agent_position),
                fresh_target_position_m=list(target_position),
                target_uncertainty_radius_m=uncertainty,
                initial_target_uncertainty_radius_m=initial_uncertainty,
                arrival_radius_m=arrival_radius,
                tolerance_m=ARRIVAL_REVALIDATION_TOLERANCE_M,
                measured_planar_distance_m=remaining,
                maximum_allowed_distance_m=selected_maximum,
                grounded_target_id=grounded["target_id"],
                final_map_id=final_map["map_id"],
                navigation_map_id=job["map_id"],
                navigation_grounding_frame_id=job["grounding_frame_id"],
                final_map_source_frame_id=final_map["source_frame_id"],
                semantic_depth_source_frame_id=semantic_capture["frame_id"],
                semantic_depth_map_id=semantic_map["map_id"],
                semantic_depth_target_position_m=semantic_grounded["position_m"],
                semantic_depth_uncertainty_radius_m=semantic_grounded["uncertainty_radius_m"],
                synchronized_depth_surface_tolerance_m=surface_tolerance,
            )
            if remaining > selected_maximum:
                raise NavigationError("arrival_target_moved_away")
            if fragment_union_used:
                audit["arrival_fragment_union"].update(
                    accepted=True,
                    completed=True,
                    dual_fresh_grounding_verified=True,
                    dual_exact_surface_id_role_verified=True,
                    surface_id_world_pose_epoch_verified=True,
                    dual_metric_continuity_verified=True,
                    initial_surface_integrity_verified=True,
                    unchanged_distance_threshold_verified=True,
                )
            if sofa_part_union_candidate_generated:
                audit["arrival_sofa_part_union"]["accepted"] = True
                audit["arrival_sofa_part_union_recovery"].update(
                    accepted=True,
                    completed=True,
                    expanded_dual_grounding_verified=True,
                    expanded_dual_exact_surface_id_role_verified=True,
                    expanded_dual_coordinate_and_pair_tolerance_verified=True,
                    expanded_dual_metric_continuity_verified=True,
                    initial_surface_integrity_verified=True,
                    unchanged_distance_threshold_verified=True,
                )
            job.update(
                arrival_distance_m=remaining,
                arrival_target_position=grounded["position_m"],
                arrival_target_uncertainty_radius_m=uncertainty,
            )
            return remaining
        except asyncio.CancelledError:
            audit.update(status="cancelled", finished_unix=time.time(), error="cancelled")
            raise
        except Exception as exc:
            audit.setdefault("status", "failed")
            audit["status"] = "failed"
            audit["finished_unix"] = time.time()
            if isinstance(exc, (PortableNavigationContractError, PortableNavigationCoreError)):
                audit["error"] = exc.code.lower()
            elif isinstance(exc, (NavigationError, PhaseLeaseError)):
                audit["error"] = str(exc)
            else:
                audit["error"] = type(exc).__name__
            if isinstance(getattr(exc, "perception_audit", None), dict):
                audit["perception_audit"] = exc.perception_audit
            raise

    async def _run_portable_job_body(self, job: dict, text: str) -> None:
        """Run RGB semantics over engine-neutral depth, map and motion contracts."""

        initial_map = None
        async with inference_lease():
            try:
                capture_after = job["started_unix"]
                if phase_enabled():
                    job["phase_lease_held"] = True
                    await self._wake_model(job)
                    capture_after = time.time()
                initial = await self._portable_capture_after(
                    capture_after,
                    require_depth=bool(job["allow_move"]),
                )
                image = portable_rgb_bytes(initial)
                job.update(
                    frame_id=initial["frame_id"],
                    image_sha256=initial["rgb"]["sha256"],
                    captured_unix=initial["captured_unix"],
                    depth_available=initial.get("depth") is not None,
                )
                if job["allow_move"]:
                    initial_map_request = {
                        **self._portable_common_request(
                            MAP_REQUEST_SCHEMA,
                            "recognition-semantic-map:" + uuid.uuid4().hex,
                        ),
                        "source_frame_id": initial["frame_id"],
                        "resolution_m": .20,
                        "footprint_radius_m": .35,
                    }
                    initial_map = validate_map_result(
                        await self.portable_adapter.build_map(initial_map_request),
                        request=initial_map_request,
                        now_unix=time.time(),
                    )
                    if (
                        initial_map["coordinate_frame_id"]
                        != initial["camera"]["coordinate_frame_id"]
                    ):
                        raise NavigationError("recognition_coordinate_frame_changed")
                    job.update(
                        initial_semantic_map_id=initial_map["map_id"],
                        initial_semantic_map_source_frame_id=initial_map["source_frame_id"],
                    )
                started = time.monotonic()
                detection = await self._infer(image, text)
                job.update(
                    detection=detection,
                    recognition_latency_ms=round((time.monotonic() - started) * 1000),
                )
            finally:
                if "phase_lease_held" in job:
                    job["phase_lease_held"] = False
                if "model_wake" in job:
                    job["model_wake"]["phase_lease_held"] = False

        if detection.get("visible") is not True:
            await self._finish(job, "not_visible", "今は見つけられないよ。移動せず待つね。")
            return
        confidence = detection.get("confidence")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not OPEN_VOCAB_CONFIDENCE_THRESHOLD <= confidence <= 1.0):
            raise NavigationError("not_visible")
        bbox = valid_bbox(detection.get("bbox"))
        occluder_bboxes = portable_occluder_bboxes(detection, bbox)
        label, category, color = self._portable_detection_descriptor(detection)
        expected_surface_role = portable_expected_surface_role(category)
        job.update(
            target_inventory_label=label,
            target_category=category,
            target_color=color,
            grounding_occluder_bbox_count=len(occluder_bboxes),
            expected_surface_role=expected_surface_role,
            surface_role_policy_revision=PORTABLE_SURFACE_ROLE_POLICY_REVISION,
        )
        if not job["allow_move"] or detection.get("intent") != "approach":
            await self._finish(job, "observed", "対象が見えるよ。移動はしていないよ。")
            return

        # Qwen sees only the first RGB plane.  A post-inference RGB-D capture
        # must be in the same camera pose before its depth may ground that box.
        current = await self._portable_capture_after(
            float(initial["captured_unix"]),
            require_depth=True,
        )
        if not portable_pose_matches(initial, current):
            raise NavigationError("view_changed_during_recognition")
        job.update(
            grounding_frame_id=current["frame_id"],
            grounding_image_sha256=current["rgb"]["sha256"],
            grounding_captured_unix=current["captured_unix"],
            grounding_surface_id_semantics_revision=(
                (current.get("depth") or {}).get(
                    "surface_id_semantics_revision"
                )
            ),
        )

        map_request = {
            **self._portable_common_request(MAP_REQUEST_SCHEMA, "map:" + uuid.uuid4().hex),
            "source_frame_id": current["frame_id"],
            "resolution_m": .20,
            "footprint_radius_m": .35,
        }
        portable_map = validate_map_result(
            await self.portable_adapter.build_map(map_request),
            request=map_request,
            now_unix=time.time(),
        )
        if initial_map is None:
            raise NavigationError("recognition_semantic_map_missing")
        if portable_map["coordinate_frame_id"] != initial_map["coordinate_frame_id"]:
            raise NavigationError("recognition_coordinate_frame_changed")
        job.update(
            navigation_map_id=portable_map["map_id"],
            navigation_map_source_frame_id=portable_map["source_frame_id"],
        )
        ground_detection_payload = {
            "detection_id": "detection:" + uuid.uuid4().hex,
            "label": label,
            "confidence": float(confidence),
            "bbox_norm": bbox,
        }
        if expected_surface_role is not None:
            ground_detection_payload["expected_surface_role"] = expected_surface_role
        if occluder_bboxes:
            ground_detection_payload["occluder_bboxes_norm"] = occluder_bboxes
        ground_request = {
            **self._portable_common_request(GROUND_REQUEST_SCHEMA, "ground:" + uuid.uuid4().hex),
            "source_frame_id": current["frame_id"],
            "map_id": portable_map["map_id"],
            "detection": ground_detection_payload,
        }
        # Ground the bbox once against the RGB-synchronized initial depth and
        # again against the post-inference depth.  The second result is usable
        # only when the same metric surface remains in place.
        initial_ground_request = {
            **ground_request,
            "request_id": "ground-check:" + uuid.uuid4().hex,
            "requested_unix": time.time(),
            "source_frame_id": initial["frame_id"],
            "map_id": initial_map["map_id"],
        }
        initial_grounded = ground_bbox_to_depth(
            initial_ground_request,
            initial,
            grounded_unix=time.time(),
        )
        if initial_grounded.get("status") != "grounded":
            job["recognition_grounding_results"] = {
                "semantic": {
                    key: initial_grounded.get(key)
                    for key in (
                        "status", "position_m", "confidence", "uncertainty_radius_m",
                        "surface_role", "reason",
                    )
                },
                "post_inference": {"status": "not_attempted", "reason": "semantic_unresolved"},
            }
            reason = str(initial_grounded.get("reason") or "unsupported")
            raise NavigationError("ambiguous" if reason == "ambiguous" else "portable_ground_" + reason)

        recognition_reference_surface = initial_grounded.get("surface_points_m")
        if not isinstance(recognition_reference_surface, list) or not recognition_reference_surface:
            raise NavigationError("recognition_reference_surface_missing")
        recognition_continuity_surface = {
            "target_id": initial_grounded["target_id"],
            "points_m": recognition_reference_surface,
            "maximum_distance_m": RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M,
        }
        recognition_surface_id = initial_grounded.get("surface_id")
        if isinstance(recognition_surface_id, str):
            recognition_continuity_surface["surface_id"] = recognition_surface_id
        recognition_surface_role = initial_grounded.get("surface_role")
        if (
            isinstance(recognition_surface_id, str)
            and isinstance(recognition_surface_role, str)
        ):
            recognition_continuity_surface["surface_role"] = recognition_surface_role
        if (
            isinstance(expected_surface_role, str)
            and isinstance(recognition_surface_role, str)
            and recognition_surface_role != expected_surface_role
        ):
            raise NavigationError("target_changed_during_recognition")
        # The contract field is shared with arrival revalidation.  Here it pins
        # the later RGB-D grounding to the surface certified in the exact frame
        # shown to perception, so a sparse-frame ranking change cannot silently
        # select another plane inside the same semantic bbox.
        ground_request["arrival_continuity_surface"] = recognition_continuity_surface
        grounded = validate_ground_result(
            await self.portable_adapter.ground(ground_request),
            request=ground_request,
            now_unix=time.time(),
        )
        job["recognition_grounding_results"] = {
            "semantic": {
                key: initial_grounded.get(key)
                for key in (
                    "status", "position_m", "confidence", "uncertainty_radius_m",
                    "surface_role", "reason",
                )
            },
            "post_inference": {
                key: grounded.get(key)
                for key in (
                    "status", "position_m", "confidence", "uncertainty_radius_m",
                    "surface_role", "reason",
                )
            },
        }
        if grounded.get("status") != "grounded":
            reason = str(grounded.get("reason") or "unsupported")
            if reason == "ambiguous":
                raise NavigationError("target_changed_during_recognition")
            raise NavigationError("portable_ground_" + reason)
        for grounded_result in (initial_grounded, grounded):
            uncertainty = float(grounded_result["uncertainty_radius_m"])
            if uncertainty > .35 or float(grounded_result["confidence"]) < .50:
                raise NavigationError("portable_ground_uncertain")
        if initial_grounded["coordinate_frame_id"] != grounded["coordinate_frame_id"]:
            raise NavigationError("target_changed_during_recognition")
        if (
            isinstance(recognition_surface_id, str)
            and grounded.get("surface_id") != recognition_surface_id
        ):
            raise NavigationError("target_changed_during_recognition")
        post_inference_surface_role = grounded.get("surface_role")
        if (
            isinstance(recognition_surface_role, str)
            or isinstance(post_inference_surface_role, str)
        ) and recognition_surface_role != post_inference_surface_role:
            raise NavigationError("target_changed_during_recognition")
        if (
            isinstance(expected_surface_role, str)
            and isinstance(post_inference_surface_role, str)
            and post_inference_surface_role != expected_surface_role
        ):
            raise NavigationError("target_changed_during_recognition")
        job["recognition_grounding_lineage"] = {
            "semantic_frame_id": initial["frame_id"],
            "semantic_map_id": initial_map["map_id"],
            "semantic_map_source_frame_id": initial_map["source_frame_id"],
            "semantic_ground_source_frame_id": initial_grounded["source_frame_id"],
            "semantic_ground_map_id": initial_grounded["map_id"],
            "post_inference_frame_id": current["frame_id"],
            "post_inference_map_id": portable_map["map_id"],
            "post_inference_map_source_frame_id": portable_map["source_frame_id"],
            "post_inference_ground_source_frame_id": grounded["source_frame_id"],
            "post_inference_ground_map_id": grounded["map_id"],
            "reference_surface_target_id": initial_grounded["target_id"],
            "reference_surface_identity_basis": (
                "opaque_physics_surface_id"
                if isinstance(recognition_surface_id, str)
                else "metric_geometry_only"
            ),
            "reference_surface_id_present": isinstance(recognition_surface_id, str),
            "expected_surface_role": expected_surface_role,
            "reference_surface_role": recognition_surface_role,
            "reference_surface_role_present": isinstance(recognition_surface_role, str),
            "surface_id_semantics_revision": (
                job.get("grounding_surface_id_semantics_revision")
            ),
            "reference_surface_sha256": portable_surface_sha256(
                recognition_reference_surface
            ),
            "reference_surface_point_count": len(recognition_reference_surface),
            "reference_surface_maximum_distance_m": (
                RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M
            ),
            "reference_surface_applied_to_frame_id": current["frame_id"],
        }
        surface_tolerance = min(
            .30,
            max(
                .12,
                float(initial_grounded["uncertainty_radius_m"])
                + float(grounded["uncertainty_radius_m"])
                + .05,
            ),
        )
        surface_distance = distance(initial_grounded["position_m"], grounded["position_m"])
        job.update(
            recognition_grounding_surface_distance_m=surface_distance,
            recognition_grounding_surface_tolerance_m=surface_tolerance,
        )
        if surface_distance > surface_tolerance:
            raise NavigationError("target_changed_during_recognition")

        minimum_clearance = .10
        visibility_standoff = portable_wide_low_target_needs_visibility_standoff(
            bbox
        )
        route, arrival_radius, rejected_approach_radii = plan_portable_approach(
            portable_map,
            grounded,
            minimum_clearance_m=minimum_clearance,
            target_bbox_norm=bbox,
        )
        if not route.get("waypoints_m"):
            raise NavigationError("portable_route_empty")
        path_length = sum(
            math.dist(route["path_m"][index - 1], route["path_m"][index])
            for index in range(1, len(route["path_m"]))
        )
        target_surface_points = grounded.get("surface_points_m")
        if not isinstance(target_surface_points, list) or not target_surface_points:
            target_surface_points = [grounded["position_m"]]
        job.update(
            target_id=grounded["target_id"],
            target_label=grounded["label"],
            target_position=grounded["position_m"],
            target_uncertainty_radius_m=grounded["uncertainty_radius_m"],
            target_surface_points_m=target_surface_points,
            target_surface_sha256=portable_surface_sha256(target_surface_points),
            map_id=portable_map["map_id"],
            coordinate_frame_id=portable_map["coordinate_frame_id"],
            planned_waypoints_m=route["waypoints_m"],
            planned_path_length_m=path_length,
            planned_expanded_cells=route["expanded_cells"],
            target_surface_point_count=route["target_surface_point_count"],
            planned_arrival_reference_position_m=route["arrival_reference_position_m"],
            effective_planning_arrival_radius_m=route["effective_arrival_radius_m"],
            selected_approach_radius_m=arrival_radius,
            rejected_no_safe_arrival_radii_m=rejected_approach_radii,
            approach_radius_policy=(
                "connectivity_guarded_largest_safe_arrival_radius_for_wide_low_bbox_0p8_to_1p2_v3"
                if visibility_standoff
                else "nearest_safe_surface_standoff_0p8_to_1p2_v1"
            ),
            visibility_standoff_radius_policy_applied=visibility_standoff,
            visibility_standoff_decision_basis=(
                "validated_rgb_bbox_geometry_only"
                if visibility_standoff
                else "not_applicable"
            ),
            visibility_standoff_connectivity_guard_radius_m=route.get(
                "visibility_standoff_connectivity_guard_radius_m"
            ),
            # The policy increases stand-off probability; it does not claim a
            # post-route camera/frustum measurement that was never performed.
            visibility_preservation_verification=(
                "not_measured" if visibility_standoff else "not_applicable"
            ),
        )
        if isinstance(grounded.get("surface_id"), str):
            self._surface_ids[job["request_id"]] = grounded["surface_id"]
        if isinstance(post_inference_surface_role, str):
            self._surface_roles[job["request_id"]] = post_inference_surface_role
        if job.get("arrival_fast_path_enabled") is True:
            self._record_arrival_certification(
                job,
                semantic_capture=initial,
                semantic_map=initial_map,
                post_inference_capture=current,
                navigation_map=portable_map,
                semantic_grounded=initial_grounded,
                post_inference_grounded=grounded,
                semantic_ground_request_id=initial_ground_request["request_id"],
                post_inference_ground_request_id=ground_request["request_id"],
                expected_surface_role=expected_surface_role,
                surface_points=target_surface_points,
            )

        now = time.time()
        navigation_id = "navigation:" + uuid.uuid4().hex
        # Caller ownership is allocated before dispatch.  Cancellation or a
        # lost response can therefore stop the exact potentially accepted ID.
        command_id = uuid.uuid4().hex
        job.update(
            command_id=command_id,
            navigation_id=navigation_id,
            command_transport="portable",
            command_dispatch_state="pending",
        )
        navigate_request = {
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": navigation_id,
            "command_id": command_id,
            "environment_id": self.portable_environment_id,
            "session_id": self.portable_session_id,
            "requested_unix": now,
            "deadline_unix": now + 60.0,
            "source_frame_id": current["frame_id"],
            "map_id": portable_map["map_id"],
            "target_id": grounded["target_id"],
            "coordinate_frame_id": portable_map["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": route["waypoints_m"],
            "arrival_radius_m": arrival_radius,
            "max_speed_mps": 1.0,
            "minimum_clearance_m": minimum_clearance,
        }
        navigation = validate_navigate_result(
            await self.portable_adapter.navigate(navigate_request),
            request=navigate_request,
            now_unix=time.time(),
        )
        if navigation.get("status") != "accepted":
            job["command_dispatch_state"] = "rejected"
            raise NavigationError("portable_navigation_rejected:" + str(navigation.get("reason") or "unknown"))
        job.update(
            status="moving",
            command_dispatch_state="accepted",
            dispatched_unix=time.time(),
        )

        sequence = -1
        deadline = time.monotonic() + 60.0
        next_model_keepalive = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if phase_enabled() and time.monotonic() >= next_model_keepalive:
                await self._refresh_model_idle_timer(job)
                next_model_keepalive = time.monotonic() + 1.5
            feedback_request = {
                **self._portable_common_request(FEEDBACK_REQUEST_SCHEMA, "feedback:" + uuid.uuid4().hex),
                "command_id": command_id,
                "after_sequence": sequence,
            }
            feedback = validate_feedback_result(
                await self.portable_adapter.feedback(feedback_request),
                request=feedback_request,
                now_unix=time.time(),
            )
            sequence = int(feedback["sequence"])
            job["last_feedback"] = feedback
            if feedback.get("collision_detected") is True:
                raise NavigationError("portable_collision_detected")
            if float(feedback["minimum_clearance_m"]) + 1e-6 < minimum_clearance:
                raise NavigationError("portable_clearance_violation")
            if feedback.get("status") == "arrived":
                if feedback.get("target_reached") is not True:
                    raise NavigationError("arrival_not_verified")
                job.update(
                    provisional_arrival_distance_m=feedback["remaining_distance_m"],
                    arrival_clearance_m=feedback["minimum_clearance_m"],
                )
                fast_path_confirmed = False
                slow_path_previous_capture = current
                # The flag is sampled once at job start; a mid-job environment
                # change never switches the arrival path of a running job.
                if job.get("arrival_fast_path_enabled") is True:
                    fast_path_confirmed, fast_frame = await self._portable_arrival_fast_path(
                        job,
                        feedback,
                        feedback_request,
                        current,
                        arrival_radius=arrival_radius,
                    )
                    if fast_frame is not None:
                        # The unchanged slow path must capture strictly newer
                        # post-Qwen frames than the fast path's newest frame.
                        slow_path_previous_capture = fast_frame
                if not fast_path_confirmed:
                    await self._portable_revalidate_arrival(
                        job,
                        feedback,
                        slow_path_previous_capture,
                        arrival_radius=arrival_radius,
                    )
                await self._finish(job, "arrived", "対象のところまで着いたよ。")
                return
            if feedback.get("status") in {"blocked", "failed", "stopped"}:
                raise NavigationError("portable_movement_" + str(feedback.get("status")))
            await asyncio.sleep(.20)
        raise NavigationError("movement_timeout")

    async def _wake_model(
        self,
        job: dict,
        *,
        audit_field: str = "model_wake",
        latency_field: str = "model_wake_latency_ms",
    ) -> None:
        """Prepare before capture; caller must already hold inference_lease.

        Cancelling the HTTP wait cannot attest that llama.cpp's native reload
        stopped. Failed/cancelled stages are not permission to start TTS.
        """
        started = time.monotonic()
        stamp = time.time()
        audit = {"request_id": job["request_id"], "stage": "checking",
                 "started_unix": stamp, "deadline_unix": stamp + 30,
                 "phase_lease_held": True}
        job[audit_field] = audit

        async def prepare() -> None:
            props = await self._http("GET", "/props", model=True)
            if type(props.get("is_sleeping")) is not bool:
                raise NavigationError("visual_heavy_phase_model_state_unknown")
            audit["was_sleeping"] = props["is_sleeping"]
            if props["is_sleeping"]:
                await self.resource_budget.check(minimum_free_percent=40)
                audit["stage"] = "loading"
                slots = await self._http("GET", "/slots", model=True)
                _idle_model_slots(slots.get("slots"))
                props = await self._http("GET", "/props", model=True)
                if props.get("is_sleeping") is not False:
                    raise NavigationError("visual_heavy_phase_model_wake_not_ready")
            # Non-waking props cannot keep a hot model alive. A later resleep
            # is rejected before inference, never silently charged to its 45s.
            audit.update(stage="ready", ready_unix=time.time(), ready_is_sleeping=False)

        try:
            async with asyncio.timeout(30):
                await self.resource_budget.run(prepare)
        except asyncio.CancelledError:
            audit["stage"] = "cancelled"
            raise
        except VisualResourceError as exc:
            audit["stage"] = "failed"
            raise NavigationError(str(exc)) from exc
        except TimeoutError as exc:
            audit["stage"] = "failed"
            try:
                await self.resource_budget.check()
            except VisualResourceError as resource_error:
                raise NavigationError(str(resource_error)) from resource_error
            raise NavigationError("visual_heavy_phase_model_wake_timeout") from exc
        except httpx.HTTPError as exc:
            audit["stage"] = "failed"
            raise NavigationError("visual_heavy_phase_model_wake_http_error") from exc
        except NavigationError as exc:
            audit["stage"] = "failed"
            if str(exc).startswith("visual_heavy_phase_"):
                raise
            raise NavigationError("visual_heavy_phase_model_wake_invalid_response") from exc
        except Exception as exc:
            audit["stage"] = "failed"
            raise NavigationError("visual_heavy_phase_model_wake_failed") from exc
        finally:
            audit["latency_ms"] = round((time.monotonic() - started) * 1000)
            job[latency_field] = audit["latency_ms"]

    async def _refresh_model_idle_timer(self, job: dict) -> None:
        """Keep an already-hot visual model resident during physical motion."""

        started = time.monotonic()
        audit = job.setdefault(
            "model_motion_keepalive",
            {
                "revision": "idle_task_during_owned_motion_v1",
                "count": 0,
                "phase_lease_held": False,
            },
        )

        async def refresh() -> None:
            await self._http("GET", "/slots", model=True)
            props = await self._http("GET", "/props", model=True)
            if props.get("is_sleeping") is not False:
                raise NavigationError("visual_heavy_phase_model_keepalive_not_ready")

        try:
            async with inference_lease():
                audit["phase_lease_held"] = True
                await self.resource_budget.run(refresh)
        except (VisualResourceError, NavigationError):
            audit["status"] = "failed"
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            audit["status"] = "failed"
            raise NavigationError("visual_heavy_phase_model_keepalive_http_error") from exc
        except Exception as exc:
            audit["status"] = "failed"
            raise NavigationError("visual_heavy_phase_model_keepalive_failed") from exc
        finally:
            audit["phase_lease_held"] = False
            audit["last_latency_ms"] = round((time.monotonic() - started) * 1000)
        audit.update(
            status="ready",
            count=int(audit["count"]) + 1,
            last_ready_unix=time.time(),
        )

    async def _infer(self, image: bytes, text: str) -> dict:
        async def recognize() -> dict:
            if enabled() and phase_enabled():
                props = await self._http("GET", "/props", model=True)
                if type(props.get("is_sleeping")) is not bool:
                    raise NavigationError("visual_heavy_phase_model_state_unknown")
                if props["is_sleeping"]:
                    raise NavigationError("visual_heavy_phase_model_reslept_before_inference")
            if portable_enabled():
                return await perceive(self._http, image, text, open_vocabulary=True)
            return await perceive(self._http, image, text)

        try:
            async with asyncio.timeout(VISUAL_INFERENCE_TIMEOUT_SECONDS):
                if enabled():
                    return await self.resource_budget.run(recognize)
                return await recognize()
        except VisualResourceError as exc:
            raise NavigationError(str(exc)) from exc
        except PerceptionError as exc:
            error = NavigationError(str(exc))
            error.perception_audit = getattr(exc, "audit", None)
            raise error from exc
        except TimeoutError as exc:
            if enabled():
                try:
                    await self.resource_budget.check()
                except VisualResourceError as resource_error:
                    raise NavigationError(str(resource_error)) from resource_error
            raise NavigationError("visual_inference_timeout") from exc

    async def _safe_stop(self, job: dict) -> bool:
        owned_id = job.get("speech_command_id") or job.get("command_id")
        if not owned_id:
            return True
        self.unconfirmed_stop = job
        job["stop_confirmed"] = False
        job["stop_expected_command_id"] = owned_id
        if job.get("command_transport") == "portable" and not job.get("speech_command_id"):
            if job.get("command_dispatch_state") == "rejected":
                job["stop_confirmed"] = True
                self.unconfirmed_stop = None
                return True
            try:
                stop_request_id = job.setdefault("portable_stop_request_id", "stop:" + uuid.uuid4().hex)
                stop_request = {
                    **self._portable_common_request(STOP_REQUEST_SCHEMA, stop_request_id),
                    "command_id": str(owned_id),
                    "reason": "visual_navigation_cancelled",
                }
                stopped = validate_stop_result(
                    await self.portable_adapter.stop(stop_request),
                    request=stop_request,
                    now_unix=time.time(),
                )
                job["stop_sent"] = True
                job["portable_stop_result"] = dict(stopped)
                if stopped.get("confirmed") is True and stopped.get("owned_command_id") == owned_id:
                    job["stop_confirmed"] = True
                    self.unconfirmed_stop = None
                    return True
            except (PortableNavigationContractError, PortableNavigationCoreError, KeyError, TypeError, ValueError):
                job.setdefault("stop_sent", False)
            return False
        try:
            stop_id, _ = await self._command("stop", job["request_id"], params={"expected_command_id": owned_id})
            job["stop_sent"] = True
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                results = await self._http("GET", "/results")
                acknowledged = any(r.get("command_id") == stop_id and r.get("ok") is True and r.get("status") == "completed"
                                   for r in results.get("items", []))
                if acknowledged and (await self._world()).get("active_command_id") != owned_id:
                    job["stop_confirmed"] = True
                    self.unconfirmed_stop = None
                    return True
                await asyncio.sleep(.1)
        except (httpx.HTTPError, NavigationError, ValueError):
            job.setdefault("stop_sent", False)
        return False

    async def _finish(self, job: dict, status: str, answer: str) -> None:
        job.update(status=status, answer=answer, arrived=status == "arrived", finished_unix=time.time())
        if job.get("speak") and job.get("stop_confirmed") is not False:
            job.update(speech_command_id=uuid.uuid4().hex, speech_status="pending")
            try:
                speech_id, _ = await self._command("speak", job["request_id"], command_id=job["speech_command_id"],
                    params={"text": answer, "emotion": "neutral"})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    events = (await self._http("GET", "/results")).get("items", [])
                    event = next((r for r in events if r.get("command_id") == speech_id and r.get("status") not in {"sent", "accepted", "queued"}), None)
                    if event:
                        job["speech_completion_event"] = event
                        job["speech_status"] = "completed" if event.get("ok") is True and event.get("status") == "completed" else "failed"
                        return
                    await asyncio.sleep(.2)
                job["speech_status"] = "timeout"
                await self._safe_stop(job)
            except (httpx.HTTPError, NavigationError):
                job["speech_dispatch_failed"] = True
                job["speech_status"] = "failed"
                # A lost dispatch/poll response does not prove Godot rejected
                # the speech. Cancel its preallocated ID before releasing the
                # job; an unconfirmed stop blocks the next interaction.
                await self._safe_stop(job)

    async def _run(self, job: dict, text: str) -> None:
        monitored = enabled() and phase_enabled()
        worker = asyncio.create_task(self._run_monitored(job, text, monitored))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Forward cancellation once. A resource trip already cancels and
            # drains the inner task; cancelling that cleanup again could abandon
            # its exact-owned stop or HTTP context/lease exit.
            if not (monitored and self.resource_budget.blocked_reason):
                worker.cancel()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            if not worker.cancelled():
                worker.result()
            raise
        finally:
            # Neither a provisional terminal status nor the inner coroutine's
            # exit releases request ownership before the final resource sample.
            if self.active_id == job["request_id"]:
                self.active_id = None

    async def _run_monitored(self, job: dict, text: str, monitored: bool) -> None:
        try:
            if monitored:
                job["resource_monitor_scope"] = "visual_job_including_speech"
                await self.resource_budget.run(lambda: self._run_job(job, text))
            else:
                await self._run_job(job, text)
        except asyncio.CancelledError:
            if monitored:
                # Cancellation may have interrupted the final resource read.
                # The outer shield keeps this post-drain check and scoped stop
                # alive even if the caller repeats its cancellation request.
                try:
                    await self.resource_budget.check()
                except VisualResourceError as exc:
                    await self._fail_job_budget(job, exc)
            raise
        except VisualResourceError as exc:
            await self._fail_job_budget(job, exc)

    async def _fail_job_budget(self, job: dict, exc: VisualResourceError) -> None:
        # run() has drained its child coroutine, not necessarily an external
        # native TTS worker. That worker retains its own EX lease until real
        # cleanup; never force-unlock it or report that Torch was aborted.
        job.update(status="failed", arrived=False, speak=False, error=str(exc),
                   resource_budget=self.resource_budget.status())
        wake = job.get("model_wake")
        if isinstance(wake, dict) and wake.get("stage") in {"checking", "loading", "cancelled"}:
            wake.update(stage="failed", error=str(exc))
        owned_id = job.get("speech_command_id") or job.get("command_id")
        already_stopped = (job.get("stop_confirmed") is True
                           and job.get("stop_expected_command_id") == owned_id)
        stopped = already_stopped or await self._safe_stop(job)
        if job.get("speech_status") == "pending":
            job["speech_status"] = "cancelled" if stopped else "stop_unconfirmed"
        await self._finish(job, "failed", "メモリの安全基準を満たせないため、処理の停止を要求したよ。動作環境の確認が必要だよ。")

    async def _run_job(self, job: dict, text: str) -> None:
        try:
            if portable_enabled():
                await self._run_portable_job_body(job, text)
                return
            await self._world()
            # Capture after the prior native TTS worker has released its lease,
            # not before a potentially long cancellation/queue drain.
            async with inference_lease():
                try:
                    capture_after = job["started_unix"]
                    if phase_enabled():
                        job["phase_lease_held"] = True
                        await self._wake_model(job)
                        # The frame must postdate model readiness, not merely
                        # the request that may have spent 16s reloading it.
                        capture_after = time.time()
                    metadata, image = await self._fresh_frame(capture_after)
                    job.update(frame_id=metadata.get("frame_id"), image_sha256=metadata["image_sha256"], captured_unix=metadata["captured_unix"])
                    started = time.monotonic()
                    detection = await self._infer(image, text)
                    job.update(detection=detection, recognition_latency_ms=round((time.monotonic() - started) * 1000))
                finally:
                    if "phase_lease_held" in job:
                        job["phase_lease_held"] = False
                    if "model_wake" in job:
                        job["model_wake"]["phase_lease_held"] = False
            if detection.get("visible") is not True:
                await self._finish(job, "not_visible", "今は見つけられないよ。移動せず待つね。")
                return
            target = ground_detection(detection, metadata)
            current, _ = await self._fresh_frame(float(metadata["captured_unix"]))
            if time.time() - float(metadata["captured_unix"]) > 45 or not pose_matches(metadata, current):
                raise NavigationError("view_changed_during_recognition")
            new_targets = [item for item in current.get("evaluator_geometry", {}).get("objects", []) if item.get("name") == target["name"]]
            if (len(new_targets) != 1 or new_targets[0].get("occluded") is not False
                or new_targets[0].get("in_frustum") is not True
                or (target.get("instance_id") is not None and new_targets[0].get("instance_id") != target["instance_id"])
                or distance(new_targets[0]["world_position"], target["world_position"]) > .25):
                raise NavigationError("target_changed_during_recognition")
            target_instance_id = target.get("instance_id")
            if isinstance(target_instance_id, bool) or not isinstance(target_instance_id, int) or target_instance_id <= 0:
                raise NavigationError("target_identity_unavailable")
            job.update(target_node=target["name"], target_instance_id=target_instance_id,
                       target_position=target["world_position"], visual_box_iou=target["visual_box_iou"])
            if not job["allow_move"] or detection.get("intent") != "approach":
                await self._finish(job, "observed", "対象が見えるよ。移動はしていないよ。")
                return
            await self._world()
            # Allocate ownership before awaiting dispatch so cancellation also
            # stops a command accepted during an HTTP response race.
            command_id = uuid.uuid4().hex
            job["command_id"] = command_id
            dispatch = await self._http("POST", "/command", {"command_id": command_id, "correlation_id": job["request_id"],
                "actor_id": "toha", "action": "move_to_node", "target_node": target["name"],
                "params": {"stop_distance": 1.0, "expected_target_instance_id": target_instance_id},
                "priority": 5, "expires_in_ms": 3000, "reason": "image-grounded-approach"})
            if dispatch.get("ok") is not True or dispatch.get("status") != "sent" or dispatch.get("godot_connected") is not True:
                raise NavigationError("command_not_sent")
            job.update(status="moving", dispatched_unix=time.time())
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                world = await self._world()
                results = await self._http("GET", "/results")
                events = [r for r in results.get("items", []) if r.get("command_id") == command_id and r.get("status") not in {"accepted", "sent", "queued"}]
                if events:
                    event = max(events, key=lambda r: r.get("received_at", 0))
                    job["completion_event"] = event
                    if event.get("ok") is not True or event.get("status") != "completed":
                        raise NavigationError("movement_" + str(event.get("status", "failed")))
                    # Completion is an event AND a fresh spatial check, never an ACK.
                    await asyncio.sleep(.3)
                    world = await self._world()
                    job["arrival_distance_m"] = verified_arrival(job, world)
                    await self._finish(job, "arrived", "対象のところまで着いたよ。")
                    return
                await asyncio.sleep(.2)
            raise NavigationError("movement_timeout")
        except asyncio.CancelledError:
            stopped = await self._safe_stop(job)
            if job.get("speech_status") == "pending":
                job["speech_status"] = "cancelled" if stopped else "stop_unconfirmed"
            if job["status"] not in TERMINAL:
                job.update(status="cancelled" if stopped else "failed",
                           answer="移動を中止したよ。" if stopped else "停止を要求したけれど、停止はまだ確認できていないよ。",
                           arrived=False, finished_unix=time.time())
                if not stopped:
                    job["error"] = "stop_unconfirmed"
            raise
        except Exception as exc:
            stopped = await self._safe_stop(job)
            if isinstance(exc, (PortableNavigationContractError, PortableNavigationCoreError)):
                code = exc.code.lower()
            else:
                code = str(exc) if isinstance(exc, (NavigationError, PhaseLeaseError)) else type(exc).__name__
            job["error"] = code
            if isinstance(getattr(exc, "perception_audit", None), dict):
                job["perception_audit"] = exc.perception_audit
            status = "ambiguous" if code.startswith("ambiguous") else code if code == "not_visible" else "failed"
            answer = ("対象を確認できないので、移動せず待つね。" if not job.get("command_id")
                      else "移動を中止したよ。到着は確認できていないよ。" if stopped
                      else "停止を要求したけれど、停止はまだ確認できていないよ。")
            if status == "ambiguous" and not job.get("command_id"):
                answer = "まだ動いていないよ。色や場所を教えてね。"
            if code.startswith("visual_resource_"):
                # Do not start a second memory-heavy stage after a budget trip.
                job.update(speak=False, resource_budget=self.resource_budget.status())
                answer = "メモリの安全基準を満たせないため、画像認識と移動を停止したよ。動作環境の確認が必要だよ。"
            if code.startswith("visual_heavy_phase_"):
                job["speak"] = False
                answer = "音声と認識の実行状態を確認できないため、移動していないよ。"
            await self._finish(job, status, answer)
