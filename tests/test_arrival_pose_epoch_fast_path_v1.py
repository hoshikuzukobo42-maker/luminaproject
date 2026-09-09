"""Unit and adversarial tests for the pure arrival pose-epoch fast path (rev 2).

The fixtures use an alternate-engine naming scheme (no Godot node names, node
paths, or instance ids anywhere) so that the fast path is exercised only via
the portable contract envelopes.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import json
import math
from pathlib import Path
from typing import Any

import pytest

from lumina_next import arrival_pose_epoch_fast_path_v1 as fast_path
from lumina_next.arrival_pose_epoch_fast_path_v1 import (
    AGENT_ADDITIONAL_INFLATION_M,
    INITIAL_CERTIFICATION_REVISION,
    MAX_ARRIVED_SPEED_MPS,
    MAX_CERTIFIED_SURFACE_POINTS,
    MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M,
    MINIMUM_EXACT_REFERENCE_RAYS_PER_FRAME,
    MINIMUM_RAY_CONFIDENCE,
    OUTCOME_FALLBACK_ELIGIBLE,
    OUTCOME_HARD_FAIL,
    OUTCOME_PASS,
    compute_initial_certification_digest,
    count_exact_reference_rays,
    evaluate_arrival_pose_epoch_fast_path,
)
from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    FEEDBACK_RESULT_SCHEMA,
    MAP_REQUEST_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    validate_map_result,
)
from lumina_next.portable_navigation_core_v1 import (
    build_occupancy_map,
    path_collision_report,
)
from lumina_next.visual_navigation import (
    ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M,
    ARRIVAL_REVALIDATION_TOLERANCE_M,
    portable_surface_sha256,
)

SURFACE_ID = "surface:" + "c" * 64
OTHER_SURFACE_ID = "surface:" + "d" * 64
SURFACE_ID_HEX = "c" * 64
COORDINATE_FRAME = "world:alternate-engine-test"
ENVIRONMENT_ID = "environment:alternate-engine-42"
SESSION_ID = "session:alternate-engine-7"
TARGET_ID = "target:" + "a" * 16
SEMANTIC_FRAME_ID = "frame:alternate-1"
GROUNDING_FRAME_ID = "frame:alternate-2"
SEMANTIC_MAP_ID = "map:alternate-semantic-initial"
NAVIGATION_MAP_ID = "map:alternate-navigation-initial"
CERTIFICATION_REQUEST_IDS = {
    "semantic_capture_request_id": "capture:alternate-1",
    "post_inference_capture_request_id": "capture:alternate-2",
    "semantic_map_request_id": "recognition-semantic-map:alternate-1",
    "navigation_map_request_id": "map:alternate-2",
    "semantic_ground_request_id": "ground-check:alternate-1",
    "post_inference_ground_request_id": "ground:alternate-2",
}
CERTIFIED_UNIX = 1_000.0
FEEDBACK_OBSERVED_UNIX = 1_009.5
NOT_BEFORE_UNIX = FEEDBACK_OBSERVED_UNIX
FIRST_FRESH_UNIX = 1_010.0
SECOND_FRESH_UNIX = 1_010.5
MAP_CREATED_UNIX = SECOND_FRESH_UNIX + 0.05
NOW_UNIX = SECOND_FRESH_UNIX + 0.5
FIRST_FRESH_FRAME_ID = "frame:alternate-7"
SECOND_FRESH_FRAME_ID = "frame:alternate-8"
RGB_BYTES = b"\x89PNG\r\n\x1a\nalternate-engine-rgb-fixture"
ALTERNATE_RGB_BYTES = b"\x89PNG\r\n\x1a\nalternate-engine-rgb-repainted"
AGENT_POSITION = [3.15, 0.0, 0.0]
CAMERA_POSITION = [3.15, 0.0, 1.4]
ARRIVAL_RADIUS_M = 0.85
TARGET_OBSTACLE = {"kind": "rect", "center_m": [4.3, 0.0], "half_extents_m": [0.3, 0.6]}

# Optical +z -> canonical +x, image-right -> canonical -y, image-down -> -z.
CAMERA_ORIENTATION = [0.5, -0.5, 0.5, -0.5]
INTRINSICS = {"fx_px": 50.0, "fy_px": 50.0, "cx_px": 32.0, "cy_px": 24.0}

# Three unique near-centre UV rays whose range 0.85 m lands on the x = 4.0 m
# certified plane; the fourth is a far marker on a different surface id.
NEAR_UVS = [(0.48, 0.48), (0.50, 0.50), (0.52, 0.52)]
NEAR_RANGE_M = 0.85


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _certified_points() -> list[list[float]]:
    return [
        [4.0, y / 10.0, 1.2 + z / 10.0]
        for y in range(-2, 3)
        for z in range(0, 4)
    ]


def _sign(certification: dict[str, Any]) -> dict[str, Any]:
    certification["surface_sha256"] = portable_surface_sha256(
        certification["surface_points_m"]
    )
    certification["certification_sha256"] = compute_initial_certification_digest(
        certification
    )
    return certification


def _certification(**overrides: Any) -> dict[str, Any]:
    certification: dict[str, Any] = {
        "certification_revision": INITIAL_CERTIFICATION_REVISION,
        "certified_unix": CERTIFIED_UNIX,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "coordinate_frame_id": COORDINATE_FRAME,
        "target_id": TARGET_ID,
        "expected_surface_role": "navigation_obstacle",
        "surface_id": SURFACE_ID,
        "surface_role": "navigation_obstacle",
        "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
        "surface_points_m": _certified_points(),
        "semantic_frame_id": SEMANTIC_FRAME_ID,
        "post_inference_frame_id": GROUNDING_FRAME_ID,
        "semantic_map_id": SEMANTIC_MAP_ID,
        "navigation_map_id": NAVIGATION_MAP_ID,
        **CERTIFICATION_REQUEST_IDS,
        "dual_grounding_surface_id_match": True,
        "dual_grounding_surface_role_match": True,
        "dual_grounding_semantics_revision_match": True,
    }
    certification.update(overrides)
    return _sign(certification)


def _ray(
    uv: tuple[float, float],
    distance_m: float,
    *,
    surface_id: Any = SURFACE_ID,
    surface_role: Any = "navigation_obstacle",
    confidence: float = 0.99,
) -> dict[str, Any]:
    sample: dict[str, Any] = {
        "uv_norm": [uv[0], uv[1]],
        "distance_m": distance_m,
        "confidence": confidence,
    }
    if surface_id is not None:
        sample["surface_id"] = surface_id
    if surface_role is not None:
        sample["surface_role"] = surface_role
    return sample


def _default_rays() -> list[dict[str, Any]]:
    rays = [_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS]
    rays.append(_ray((0.90, 0.90), 6.54321, surface_id=OTHER_SURFACE_ID))
    return rays


def _camera(**overrides: Any) -> dict[str, Any]:
    camera = {
        "coordinate_frame_id": COORDINATE_FRAME,
        "coordinate_convention": COORDINATE_CONVENTION,
        "position_m": list(CAMERA_POSITION),
        "orientation_xyzw": list(CAMERA_ORIENTATION),
        "intrinsics": dict(INTRINSICS),
    }
    camera.update(overrides)
    return camera


def _frame(
    frame_id: str,
    captured_unix: float,
    *,
    rays: list[dict[str, Any]] | None = None,
    camera: dict[str, Any] | None = None,
    rgb_bytes: bytes = RGB_BYTES,
    semantics_revision: Any = SURFACE_ID_SEMANTICS_REVISION,
    depth: bool = True,
    request_id: str | None = None,
    environment_id: str = ENVIRONMENT_ID,
    session_id: str = SESSION_ID,
) -> dict[str, Any]:
    samples = _default_rays() if rays is None else rays
    depth_plane: dict[str, Any] | None = None
    if depth:
        depth_plane = {
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": "registered_normalized_to_rgb",
            "samples": samples,
            "samples_sha256": _canonical_sha256(samples),
        }
        if semantics_revision is not None:
            depth_plane["surface_id_semantics_revision"] = semantics_revision
    return {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": request_id or ("capture:" + frame_id.split(":", 1)[1]),
        "environment_id": environment_id,
        "session_id": session_id,
        "frame_id": frame_id,
        "captured_unix": captured_unix,
        "rgb": {
            "encoding": "png",
            "width_px": 64,
            "height_px": 48,
            "data_base64": base64.b64encode(rgb_bytes).decode("ascii"),
            "sha256": hashlib.sha256(rgb_bytes).hexdigest(),
        },
        "depth": depth_plane,
        "camera": camera or _camera(),
    }


def _frames(**second_overrides: Any) -> list[dict[str, Any]]:
    return [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX, **second_overrides),
    ]


def _map_request(
    *,
    source_frame_id: str = SECOND_FRESH_FRAME_ID,
    requested_unix: float = MAP_CREATED_UNIX,
    resolution_m: float = 0.20,
    footprint_radius_m: float = 0.35,
    request_id: str | None = None,
    environment_id: str = ENVIRONMENT_ID,
    session_id: str = SESSION_ID,
) -> dict[str, Any]:
    return {
        "schema_version": MAP_REQUEST_SCHEMA,
        "request_id": request_id or ("arrival-fast-path-map:" + source_frame_id.split(":", 1)[1]),
        "environment_id": environment_id,
        "session_id": session_id,
        "requested_unix": requested_unix,
        "source_frame_id": source_frame_id,
        "resolution_m": resolution_m,
        "footprint_radius_m": footprint_radius_m,
    }


def _map(
    request: dict[str, Any] | None = None,
    *,
    created_unix: float | None = None,
    agent_position: list[float] | None = None,
    bounds_xy: list[float] | None = None,
    obstacles: list[dict[str, Any]] | None = None,
    coordinate_frame_id: str = COORDINATE_FRAME,
    map_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = request or _map_request()
    result = build_occupancy_map(
        request,
        bounds_xy=bounds_xy or [-1.0, 6.0, -3.0, 3.0],
        obstacles=obstacles if obstacles is not None else [TARGET_OBSTACLE],
        agent_position_m=agent_position or list(AGENT_POSITION),
        created_unix=request["requested_unix"] if created_unix is None else created_unix,
    )
    result["coordinate_frame_id"] = coordinate_frame_id
    if map_id is not None:
        result["map_id"] = map_id
    return request, validate_map_result(result)


FEEDBACK_COMMAND_ID = "command-alternate-0001"
FEEDBACK_REQUEST_ID = "feedback:alternate-9"


def _feedback_request(
    *,
    request_id: str = FEEDBACK_REQUEST_ID,
    requested_unix: float = FEEDBACK_OBSERVED_UNIX,
    command_id: str = FEEDBACK_COMMAND_ID,
    environment_id: str = ENVIRONMENT_ID,
    session_id: str = SESSION_ID,
) -> dict[str, Any]:
    return {
        "schema_version": FEEDBACK_REQUEST_SCHEMA,
        "request_id": request_id,
        "environment_id": environment_id,
        "session_id": session_id,
        "requested_unix": requested_unix,
        "command_id": command_id,
        "after_sequence": 6,
    }


def _feedback(
    *,
    position: list[float] | None = None,
    velocity: list[float] | None = None,
    observed_unix: float = FEEDBACK_OBSERVED_UNIX,
    status: str = "arrived",
    target_reached: bool = True,
    minimum_clearance_m: float = 0.55,
    collision_detected: bool = False,
    request_id: str = FEEDBACK_REQUEST_ID,
    command_id: str = FEEDBACK_COMMAND_ID,
    environment_id: str = ENVIRONMENT_ID,
    session_id: str = SESSION_ID,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": FEEDBACK_RESULT_SCHEMA,
        "request_id": request_id,
        "environment_id": environment_id,
        "session_id": session_id,
        "command_id": command_id,
        "sequence": 7,
        "observed_unix": observed_unix,
        "status": status,
        "position_m": position or list(AGENT_POSITION),
        "velocity_mps": velocity or [0.0, 0.0, 0.0],
        "remaining_distance_m": 0.45,
        "minimum_clearance_m": minimum_clearance_m,
        "collision_detected": collision_detected,
        "target_reached": target_reached,
        "reason": reason,
    }


def _evaluate(
    certification: dict[str, Any] | None = None,
    frames: list[dict[str, Any]] | None = None,
    fresh_map: tuple[dict[str, Any], dict[str, Any]] | None = None,
    feedback: dict[str, Any] | None = None,
    feedback_request: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    request, result = fresh_map if fresh_map is not None else _map()
    kwargs.setdefault("arrival_radius_m", ARRIVAL_RADIUS_M)
    kwargs.setdefault("not_before_unix", NOT_BEFORE_UNIX)
    kwargs.setdefault("now_unix", NOW_UNIX)
    kwargs.setdefault("fresh_map_request", request)
    kwargs.setdefault("arrived_feedback", feedback if feedback is not None else _feedback())
    kwargs.setdefault(
        "arrived_feedback_request",
        feedback_request if feedback_request is not None else _feedback_request(),
    )
    return evaluate_arrival_pose_epoch_fast_path(
        certification if certification is not None else _certification(),
        frames if frames is not None else _frames(),
        result,
        **kwargs,
    )


def _assert_no_surface_id_leak(result: dict[str, Any]) -> None:
    serialized = json.dumps(result, ensure_ascii=False)
    assert SURFACE_ID_HEX not in serialized
    assert "d" * 64 not in serialized
    assert "surface:" not in serialized


def _rotate_quaternion(q: list[float], half_angle: float) -> list[float]:
    """Compose q with a rotation of 2*half_angle about the local x axis."""

    r = [math.sin(half_angle), 0.0, 0.0, math.cos(half_angle)]
    x1, y1, z1, w1 = q
    x2, y2, z2, w2 = r
    return [
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ]


# --------------------------------------------------------------------------
# Normal path
# --------------------------------------------------------------------------


def test_pass_with_dual_fresh_exact_reference_rays() -> None:
    result = _evaluate()

    assert result["outcome"] == OUTCOME_PASS
    assert result["arrival_confirmed"] is True
    assert result["qwen_fallback_allowed"] is False
    assert result["reason"] == "dual_fresh_exact_reference_rays_within_arrival_threshold"
    assert result["arrival_distance_m"] == pytest.approx(0.85, abs=0.01)
    assert result["maximum_allowed_distance_m"] == pytest.approx(
        ARRIVAL_RADIUS_M + ARRIVAL_REVALIDATION_TOLERANCE_M
    )
    assert result["arrival_distance_basis"] == (
        "newest_fresh_frame_exact_reference_rays_min_planar"
    )
    assert result["fresh_agent_position_m"] == AGENT_POSITION
    assert result["feedback_agent_planar_divergence_m"] == pytest.approx(0.0)
    assert result["arrived_feedback_speed_mps"] == pytest.approx(0.0)
    assert [frame["frame_id"] for frame in result["frames"]] == [
        FIRST_FRESH_FRAME_ID,
        SECOND_FRESH_FRAME_ID,
    ]
    for frame in result["frames"]:
        assert frame["exact_reference_ray_count"] == 3
        assert frame["nonreference_ray_count_excluded"] == 1
        assert frame["reference_rays_beyond_maximum_distance_excluded"] == 0
        assert frame["low_confidence_rays_excluded"] == 0
        assert frame["surface_id_match"] is True
        assert frame["surface_role_match"] is True
        assert frame["semantics_revision_match"] is True
    checks = result["checks"]
    for key in (
        "initial_certification_digest_match", "initial_surface_digest_match", "contract_valid",
        "not_future_dated", "environment_match", "session_match", "request_ids_distinct",
        "frame_ids_distinct", "timestamps_monotonic", "fresh_frames_after_cutoff",
        "synchronized_camera_match", "coordinate_frame_match", "map_lineage_match",
        "map_policy_match", "agent_cell_free_after_additional_inflation",
        "certification_surface_role_supported", "expected_and_actual_role_match_required_role",
        "surface_role_consistent", "feedback_environment_session_match", "feedback_collision_free",
        "feedback_clearance_ok", "cutoff_not_before_feedback",
        "agent_stationary", "agent_position_consistent_with_feedback",
        "dual_fresh_exact_reference_rays", "arrival_distance_within_unchanged_threshold",
    ):
        assert checks[key] is True, key
    assert result["arrived_feedback_minimum_clearance_m"] == pytest.approx(0.55)
    assert result["policy"]["dual_grounding_match_required"] is True
    assert result["policy"]["pass_requires_expected_and_actual_role"] == "navigation_obstacle"
    assert checks["request_replay_detected"] is False
    proof = result["inflated_occupancy_proof"]
    assert proof["kind"] == "binary_inflated_occupancy_proof"
    assert proof["additional_inflation_m"] == AGENT_ADDITIONAL_INFLATION_M == 0.10
    assert proof["numeric_clearance_measured"] is False
    assert proof["agent_cell_free"] is True
    assert result["certification_digest_role"] == "integrity_only_not_authentication"
    _assert_no_surface_id_leak(result)


def test_result_always_records_semantic_revalidation_not_performed() -> None:
    passing = _evaluate()
    fallback = _evaluate(frames=_frames(rays=[_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]]))
    hard = _evaluate(frames=[_frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX)] * 2)
    outcomes = {passing["outcome"], fallback["outcome"], hard["outcome"]}
    assert outcomes == {OUTCOME_PASS, OUTCOME_FALLBACK_ELIGIBLE, OUTCOME_HARD_FAIL}
    for result in (passing, fallback, hard):
        assert result["semantic_revalidation_performed"] is False
        assert result["semantic_change_detectable"] is False
        assert result["appearance_material_category_changes_detectable"] is False
        assert result["qwen_invoked"] is False
        assert result["io_performed"] is False
        assert result["engine_specific_identifiers_used"] is False
        assert result["revision"] == fast_path.ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION
        _assert_no_surface_id_leak(result)


def test_fast_path_reuses_existing_safety_thresholds_unchanged() -> None:
    result = _evaluate()
    assert result["reference_surface_maximum_distance_m"] == ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M == 0.20
    assert result["arrival_tolerance_m"] == ARRIVAL_REVALIDATION_TOLERANCE_M == 0.08
    policy = result["policy"]
    assert policy["minimum_exact_reference_rays_per_frame"] == MINIMUM_EXACT_REFERENCE_RAYS_PER_FRAME == 3
    assert policy["minimum_ray_confidence"] == MINIMUM_RAY_CONFIDENCE == 0.25
    assert policy["agent_additional_inflation_m"] == 0.10
    assert policy["fresh_map_resolution_m"] == 0.20
    assert policy["fresh_map_minimum_footprint_radius_m"] == 0.35
    assert policy["max_feedback_agent_planar_divergence_m"] == MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M == 0.20
    assert policy["max_arrived_speed_mps"] == MAX_ARRIVED_SPEED_MPS == 0.05
    assert policy["minimum_arrived_clearance_m"] == fast_path.MINIMUM_ARRIVED_CLEARANCE_M == 0.10
    assert policy["max_certified_surface_points"] == MAX_CERTIFIED_SURFACE_POINTS == 256
    assert result["required_surface_role"] == "navigation_obstacle"


def test_evaluation_is_pure_and_deterministic() -> None:
    certification = _certification()
    frames = _frames()
    fresh_map = _map()
    feedback = _feedback()
    feedback_request = _feedback_request()
    snapshot = copy.deepcopy((certification, frames, fresh_map, feedback, feedback_request))

    first = _evaluate(certification, frames, fresh_map, feedback, feedback_request)
    second = _evaluate(certification, frames, fresh_map, feedback, feedback_request)

    assert first == second
    assert (certification, frames, fresh_map, feedback, feedback_request) == snapshot
    source = inspect.getsource(fast_path)
    for forbidden in ("import httpx", "import asyncio", "import time", "import os", "open("):
        assert forbidden not in source


def test_far_same_id_rays_are_excluded_from_count_and_distance() -> None:
    rays = _default_rays() + [_ray((0.30, 0.30), 1.5), _ray((0.70, 0.70), 2.2)]
    result = _evaluate(frames=_frames(rays=rays))

    assert result["outcome"] == OUTCOME_PASS
    newest = result["frames"][1]
    assert newest["exact_reference_ray_count"] == 3
    assert newest["reference_rays_beyond_maximum_distance_excluded"] == 2
    assert result["arrival_distance_m"] == pytest.approx(0.85, abs=0.01)


def test_ray_just_inside_reference_distance_counts() -> None:
    rays = [
        _ray((0.48, 0.48), NEAR_RANGE_M),
        _ray((0.50, 0.50), NEAR_RANGE_M),
        # 1.03 m range lands at x ~ 4.18 m: 0.18 m from the certified plane.
        _ray((0.52, 0.52), 1.03),
    ]
    result = _evaluate(frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_PASS
    assert result["frames"][1]["exact_reference_ray_count"] == 3


def test_ray_confidence_boundary_is_inclusive_at_0p25() -> None:
    two_good = [_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]]
    boundary = two_good + [_ray((0.52, 0.52), NEAR_RANGE_M, confidence=0.25)]
    result = _evaluate(frames=_frames(rays=boundary))
    assert result["outcome"] == OUTCOME_PASS
    assert result["frames"][1]["exact_reference_ray_count"] == 3
    assert result["frames"][1]["low_confidence_rays_excluded"] == 0

    for low in (0.0, 0.249999):
        rays = two_good + [_ray((0.52, 0.52), NEAR_RANGE_M, confidence=low)]
        result = _evaluate(frames=_frames(rays=rays))
        assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE, low
        assert result["reason"] == "insufficient_exact_reference_rays"
        assert result["frames"][1]["exact_reference_ray_count"] == 2
        assert result["frames"][1]["low_confidence_rays_excluded"] == 1


def test_stationary_feedback_boundaries_are_inclusive() -> None:
    result = _evaluate(feedback=_feedback(velocity=[0.05, 0.0, 0.0]))
    assert result["outcome"] == OUTCOME_PASS
    assert result["arrived_feedback_speed_mps"] == pytest.approx(0.05)

    result = _evaluate(feedback=_feedback(position=[3.15 - 0.20, 0.0, 0.0]))
    assert result["outcome"] == OUTCOME_PASS
    assert result["feedback_agent_planar_divergence_m"] == pytest.approx(0.20)


# --------------------------------------------------------------------------
# Adversarial: frame integrity (hard_fail)
# --------------------------------------------------------------------------


def test_same_frame_reuse_is_hard_fail() -> None:
    frame = _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX)
    result = _evaluate(frames=[frame, copy.deepcopy(frame)])
    assert result["outcome"] == OUTCOME_HARD_FAIL
    # Two envelopes with the same request id are already a replay.
    assert result["reason"] == "request_id_duplicate"

    relabeled = _frame(SECOND_FRESH_FRAME_ID, FIRST_FRESH_UNIX)
    result = _evaluate(frames=[frame, relabeled])
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "frame_timestamps_not_monotonic"

    same_frame_new_request = _frame(FIRST_FRESH_FRAME_ID, SECOND_FRESH_UNIX, request_id="capture:alternate-7b")
    result = _evaluate(frames=[frame, same_frame_new_request])
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "frame_reused"


def test_certification_frames_presented_as_fresh_are_hard_fail() -> None:
    for reused in (SEMANTIC_FRAME_ID, GROUNDING_FRAME_ID):
        frames = [
            _frame(reused, FIRST_FRESH_UNIX, request_id="capture:alternate-fresh-a"),
            _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX),
        ]
        result = _evaluate(frames=frames)
        assert result["outcome"] == OUTCOME_HARD_FAIL
        assert result["reason"] == "frame_reused"
        assert result["reuse_source"] == "fresh_frame[0]"


def test_timestamp_regression_is_hard_fail() -> None:
    frames = [
        _frame(FIRST_FRESH_FRAME_ID, SECOND_FRESH_UNIX),
        _frame(SECOND_FRESH_FRAME_ID, FIRST_FRESH_UNIX),
    ]
    result = _evaluate(frames=frames)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "frame_timestamps_not_monotonic"


def test_frames_older_than_certification_or_window_are_hard_fail() -> None:
    stale = [
        _frame(FIRST_FRESH_FRAME_ID, CERTIFIED_UNIX - 1.0),
        _frame(SECOND_FRESH_FRAME_ID, CERTIFIED_UNIX - 0.5),
    ]
    result = _evaluate(frames=stale)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "fresh_frame_predates_certification"

    # Frames captured before the arrived feedback are not post-arrival frames.
    result = _evaluate(not_before_unix=FIRST_FRESH_UNIX + 0.1)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "fresh_frame_predates_arrival_window"
    result = _evaluate(not_before_unix=FIRST_FRESH_UNIX)
    assert result["reason"] == "fresh_frame_predates_arrival_window"

    assert _evaluate(not_before_unix=FIRST_FRESH_UNIX - 0.1)["outcome"] == OUTCOME_PASS


def test_not_before_unix_is_mandatory() -> None:
    request, result = _map()
    with pytest.raises(TypeError):
        evaluate_arrival_pose_epoch_fast_path(  # type: ignore[call-arg]
            _certification(),
            _frames(),
            result,
            fresh_map_request=request,
            arrived_feedback=_feedback(),
            arrived_feedback_request=_feedback_request(),
            arrival_radius_m=ARRIVAL_RADIUS_M,
            now_unix=NOW_UNIX,
        )
    with pytest.raises(TypeError):
        evaluate_arrival_pose_epoch_fast_path(  # type: ignore[call-arg]
            _certification(),
            _frames(),
            result,
            fresh_map_request=request,
            arrived_feedback=_feedback(),
            arrival_radius_m=ARRIVAL_RADIUS_M,
            not_before_unix=NOT_BEFORE_UNIX,
            now_unix=NOW_UNIX,
        )
    for missing in (None, float("nan"), "1010"):
        outcome = _evaluate(not_before_unix=missing)
        assert outcome["outcome"] == OUTCOME_HARD_FAIL
        assert outcome["contract_error"] == "NOT_BEFORE_UNIX_INVALID"
    outcome = _evaluate(now_unix=None)
    assert outcome["outcome"] == OUTCOME_HARD_FAIL
    assert outcome["contract_error"] == "NOW_UNIX_INVALID"


def test_future_dated_frame_or_map_is_hard_fail() -> None:
    future_frames = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX),
        _frame(SECOND_FRESH_FRAME_ID, NOW_UNIX + 5.0),
    ]
    result = _evaluate(frames=future_frames)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "future_dated_observation"
    assert result["future_dated_source"] == "fresh_frame[1]"

    request = _map_request(requested_unix=NOW_UNIX + 5.0)
    result = _evaluate(fresh_map=_map(request))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "future_dated_observation"
    assert result["future_dated_source"] in {"fresh_map", "fresh_map_request"}

    result = _evaluate(
        feedback=_feedback(observed_unix=NOW_UNIX + 5.0),
        feedback_request=_feedback_request(requested_unix=NOW_UNIX + 5.0),
        not_before_unix=NOW_UNIX + 5.0,
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "future_dated_observation"
    assert result["future_dated_source"] == "arrived_feedback"

    # Within the contract clock skew the observation is still acceptable.
    assert _evaluate(now_unix=SECOND_FRESH_UNIX - 0.5)["outcome"] == OUTCOME_PASS


def test_camera_pose_change_between_frames_is_hard_fail() -> None:
    moved = _camera(position_m=[3.15, 0.20, 1.4])
    result = _evaluate(frames=_frames(camera=moved))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "camera_pose_changed_between_frames"

    rotated = _camera(orientation_xyzw=_rotate_quaternion(CAMERA_ORIENTATION, 0.25))
    assert abs(math.hypot(*rotated["orientation_xyzw"]) - 1.0) < 1e-6
    result = _evaluate(frames=_frames(camera=rotated))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "camera_pose_changed_between_frames"


# --------------------------------------------------------------------------
# Adversarial: identity of envelopes (hard_fail)
# --------------------------------------------------------------------------


def test_cross_environment_or_session_is_hard_fail() -> None:
    other_env = _frames(environment_id="environment:alternate-engine-43")
    result = _evaluate(frames=other_env)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "environment_mismatch"
    assert result["mismatch_source"] == "fresh_frame[1]"

    other_session = _frames(session_id="session:alternate-engine-8")
    result = _evaluate(frames=other_session)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "session_mismatch"

    request = _map_request(session_id="session:alternate-engine-8")
    result = _evaluate(fresh_map=_map(request))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "session_mismatch"
    assert result["mismatch_source"] == "fresh_map"

    certification = _certification(environment_id="environment:alternate-engine-43")
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "environment_mismatch"

    result = _evaluate(
        feedback=_feedback(session_id="session:alternate-engine-8"),
        feedback_request=_feedback_request(session_id="session:alternate-engine-8"),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "session_mismatch"
    assert result["mismatch_source"] == "arrived_feedback"
    result = _evaluate(
        feedback=_feedback(environment_id="environment:alternate-engine-43"),
        feedback_request=_feedback_request(environment_id="environment:alternate-engine-43"),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "environment_mismatch"
    assert result["mismatch_source"] == "arrived_feedback"


def test_request_replay_and_duplicate_request_ids_are_hard_fail() -> None:
    for replayed in CERTIFICATION_REQUEST_IDS.values():
        frames = _frames(request_id=replayed)
        result = _evaluate(frames=frames)
        assert result["outcome"] == OUTCOME_HARD_FAIL, replayed
        assert result["reason"] == "request_replay"
        assert result["replay_source"] == "fresh_frame[1]"

    request = _map_request(request_id=CERTIFICATION_REQUEST_IDS["navigation_map_request_id"])
    result = _evaluate(fresh_map=_map(request))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "request_replay"
    assert result["replay_source"] == "fresh_map"

    duplicate = _frames(request_id="capture:alternate-7")
    result = _evaluate(frames=duplicate)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "request_id_duplicate"

    request = _map_request(request_id="capture:alternate-8")
    result = _evaluate(fresh_map=_map(request))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "request_id_duplicate"

    # The arrived feedback is an envelope too: its request id must be unique
    # among the fresh envelopes and must not replay a certification request.
    result = _evaluate(
        feedback=_feedback(request_id="capture:alternate-8"),
        feedback_request=_feedback_request(request_id="capture:alternate-8"),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "request_id_duplicate"
    replayed = CERTIFICATION_REQUEST_IDS["post_inference_ground_request_id"]
    result = _evaluate(
        feedback=_feedback(request_id=replayed),
        feedback_request=_feedback_request(request_id=replayed),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "request_replay"
    assert result["replay_source"] == "arrived_feedback"


# --------------------------------------------------------------------------
# Adversarial: coordinate frame / certification / map (hard_fail)
# --------------------------------------------------------------------------


def test_coordinate_frame_change_is_hard_fail_everywhere() -> None:
    other_camera = _camera(coordinate_frame_id="world:alternate-engine-reloaded")
    result = _evaluate(frames=_frames(camera=other_camera))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "coordinate_frame_changed"
    assert result["coordinate_frame_source"] == "fresh_frame[1]"

    result = _evaluate(fresh_map=_map(coordinate_frame_id="world:alternate-engine-reloaded"))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "coordinate_frame_changed"
    assert result["coordinate_frame_source"] == "fresh_map"

    both = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, camera=other_camera),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX, camera=other_camera),
    ]
    result = _evaluate(
        frames=both,
        fresh_map=_map(coordinate_frame_id="world:alternate-engine-reloaded"),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "coordinate_frame_changed"


def test_tampered_certification_points_are_hard_fail() -> None:
    certification = _certification()
    certification["surface_points_m"] = [
        [x + 0.5, y, z] for x, y, z in certification["surface_points_m"]
    ]
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "initial_certification_digest_mismatch"
    assert result["checks"]["initial_surface_digest_match"] is False
    assert result["checks"]["initial_certification_digest_match"] is False
    _assert_no_surface_id_leak(result)


def test_tampered_certification_fields_are_hard_fail() -> None:
    certification = _certification()
    certification["surface_id"] = OTHER_SURFACE_ID
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "initial_certification_digest_mismatch"
    assert result["checks"]["initial_surface_digest_match"] is True
    assert result["checks"]["initial_certification_digest_match"] is False

    for field, value in (
        ("surface_role", "walkable_ground"),
        ("expected_surface_role", "walkable_ground"),
        ("coordinate_frame_id", "world:alternate-engine-reloaded"),
        ("environment_id", "environment:alternate-engine-43"),
        ("session_id", "session:alternate-engine-8"),
        ("navigation_map_id", "map:alternate-forged"),
        ("semantic_map_id", "map:alternate-forged"),
        ("post_inference_frame_id", "frame:alternate-forged"),
        ("semantic_capture_request_id", "capture:alternate-forged"),
        ("certified_unix", CERTIFIED_UNIX + 5.0),
        ("surface_id_semantics_revision", "legacy_body_identity_v1"),
        ("dual_grounding_surface_id_match", False),
    ):
        tampered = _certification()
        tampered[field] = value
        result = _evaluate(certification=tampered)
        assert result["outcome"] == OUTCOME_HARD_FAIL, field
        assert result["reason"] == "initial_certification_digest_mismatch", field


def test_resigned_tampered_points_are_not_a_digest_failure_but_never_pass() -> None:
    # A caller with write access to the certification can re-sign it.  The
    # integrity digest cannot detect that (it is not authentication); the
    # geometry check still refuses to confirm arrival because no fresh ray
    # lies near the forged surface.
    forged = _certification(
        surface_points_m=[[x + 0.5, y, z] for x, y, z in _certified_points()],
    )
    result = _evaluate(certification=forged)
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "insufficient_exact_reference_rays"
    assert result["frames"][0]["reference_rays_beyond_maximum_distance_excluded"] == 3


@pytest.mark.parametrize(
    "field",
    [
        "dual_grounding_surface_id_match",
        "dual_grounding_surface_role_match",
        "dual_grounding_semantics_revision_match",
    ],
)
def test_resigned_certification_with_dual_grounding_false_is_hard_fail(field: str) -> None:
    # Re-signed after the flag was flipped: the digest matches, so the
    # disagreement itself must be what fails the certification.
    certification = _certification(**{field: False})
    assert compute_initial_certification_digest(certification) == certification["certification_sha256"]

    result = _evaluate(certification=certification)

    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "dual_grounding_mismatch"
    assert result["dual_grounding_field"] == field
    assert result["checks"]["initial_certification_digest_match"] is True
    assert result["qwen_fallback_allowed"] is False


def test_pass_requires_expected_and_actual_role_navigation_obstacle() -> None:
    # expected_surface_role missing while the grounded role is an obstacle:
    # never a pass, and never a hard failure either (no contradiction).
    certification = _certification(expected_surface_role=None)
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "expected_surface_role_unavailable"
    assert result["arrival_confirmed"] is False

    # expected obstacle but grounded role something else: a recorded
    # contradiction inside the certification.
    certification = _certification(surface_role="physical_surface")
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "certification_role_inconsistent"

    certification = _certification(expected_surface_role="walkable_ground")
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "certification_role_inconsistent"

    # Both agree on a non-obstacle role: not a fast-path target.
    certification = _certification(expected_surface_role="physical_surface", surface_role="physical_surface")
    rays = [_ray(uv, NEAR_RANGE_M, surface_role="physical_surface") for uv in NEAR_UVS]
    result = _evaluate(certification=certification, frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "fast_path_role_not_supported"
    assert result["certification_expected_surface_role"] == "physical_surface"


def test_certification_recorded_under_other_identity_semantics_is_hard_fail() -> None:
    certification = _certification(surface_id_semantics_revision="legacy_body_identity_v1")
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "surface_id_semantics_revision_mismatch"
    assert result["semantics_revision_source"] == "initial_certification"


def test_certification_with_too_many_surface_points_is_hard_fail() -> None:
    points = [[4.0, (index % 50) / 100.0, 1.0 + (index // 50) / 100.0] for index in range(257)]
    certification = _certification(surface_points_m=points)
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["contract_error"] == "CERTIFICATION_SURFACE_POINTS_TOO_MANY"

    exact_limit = _certification(surface_points_m=points[:256])
    result = _evaluate(certification=exact_limit)
    assert result["reason"] != "contract_invalid"


def test_map_lineage_mismatch_is_hard_fail() -> None:
    result = _evaluate(fresh_map=_map(_map_request(source_frame_id=FIRST_FRESH_FRAME_ID)))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "map_lineage_mismatch"

    for reused_map_id in (NAVIGATION_MAP_ID, SEMANTIC_MAP_ID):
        result = _evaluate(fresh_map=_map(map_id=reused_map_id))
        assert result["outcome"] == OUTCOME_HARD_FAIL
        assert result["reason"] == "map_lineage_mismatch"

    result = _evaluate(fresh_map=_map(_map_request(requested_unix=SECOND_FRESH_UNIX - 2.0)))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "map_lineage_mismatch"

    # A result whose source frame differs from its own request is a contract
    # violation rather than a policy mismatch.
    request, result_map = _map()
    request = dict(request, source_frame_id=FIRST_FRESH_FRAME_ID)
    result = _evaluate(fresh_map=(request, result_map))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["contract_error"] == "PORTABLE_LINEAGE_MISMATCH"


def test_map_policy_mismatch_is_hard_fail() -> None:
    result = _evaluate(fresh_map=_map(_map_request(resolution_m=0.10)))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "map_policy_mismatch"
    assert result["map_policy_field"] == "resolution_m"

    result = _evaluate(fresh_map=_map(_map_request(footprint_radius_m=0.30)))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "map_policy_mismatch"
    assert result["map_policy_field"] == "footprint_radius_m"

    # A larger footprint is a stricter map and stays acceptable.
    wide = _map(_map_request(footprint_radius_m=0.42))
    assert _evaluate(fresh_map=wide)["outcome"] == OUTCOME_PASS


def test_blocked_or_out_of_bounds_agent_cell_is_hard_fail() -> None:
    blocked = _map(
        obstacles=[
            TARGET_OBSTACLE,
            {"kind": "rect", "center_m": [3.15, 0.0], "half_extents_m": [0.1, 0.1]},
        ]
    )
    result = _evaluate(fresh_map=blocked)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_blocked_after_inflation"
    assert result["map_safety_reason"] == "blocked"
    assert result["inflated_occupancy_proof"]["agent_cell_free"] is False
    assert result["inflated_occupancy_proof"]["numeric_clearance_measured"] is False

    outside = _map(bounds_xy=[3.5, 6.0, -3.0, 3.0])
    result = _evaluate(fresh_map=outside)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_out_of_bounds"


def test_free_cell_with_obstacle_inside_additional_inflation_is_hard_fail() -> None:
    # The obstacle leaves the agent's own cell free on the footprint-inflated
    # map but lies within the extra 0.10 m binary inflation.
    near_obstacle = {"kind": "rect", "center_m": [3.75, 0.0], "half_extents_m": [0.05, 0.6]}
    request, fresh_map = _map(obstacles=[TARGET_OBSTACLE, near_obstacle])
    baseline = path_collision_report(fresh_map, [AGENT_POSITION, AGENT_POSITION])
    inflated = path_collision_report(
        fresh_map, [AGENT_POSITION, AGENT_POSITION], additional_inflation_m=0.10
    )
    assert baseline["valid"] is True
    assert inflated["valid"] is False

    result = _evaluate(fresh_map=(request, fresh_map))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_blocked_after_inflation"
    assert result["inflated_occupancy_proof"]["kind"] == "binary_inflated_occupancy_proof"
    assert result["inflated_occupancy_proof"]["additional_inflation_m"] == 0.10


def test_contract_violations_are_hard_fail() -> None:
    frames = _frames()
    frames[1]["rgb"]["sha256"] = "0" * 64
    result = _evaluate(frames=frames)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["contract_error"] == "PORTABLE_DATA_HASH_MISMATCH"
    assert result["frame_index"] == 1

    result = _evaluate(frames=[_frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX)])
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "FRESH_FRAME_COUNT_INVALID"

    duplicate_uv = _default_rays() + [_ray((0.50, 0.50), NEAR_RANGE_M)]
    result = _evaluate(frames=_frames(rays=duplicate_uv))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "PORTABLE_DEPTH_SAMPLE_DUPLICATE"

    request, fresh_map = _map()
    fresh_map = dict(fresh_map, grid=dict(fresh_map["grid"], occupancy_sha256="0" * 64))
    result = _evaluate(fresh_map=(request, fresh_map))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"

    for bad_radius in (0.0, -1.0, float("nan"), True):
        result = _evaluate(arrival_radius_m=bad_radius)
        assert result["outcome"] == OUTCOME_HARD_FAIL
        assert result["contract_error"] == "ARRIVAL_RADIUS_INVALID"

    certification = _certification()
    certification["node_path"] = "/root/World/Sofa"
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "CERTIFICATION_FIELDS_INVALID"

    result = _evaluate(feedback=_feedback(status="moving", target_reached=False))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "ARRIVED_FEEDBACK_STATUS_INVALID"
    result = _evaluate(feedback={"status": "arrived", "target_reached": True})
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["source"] == "arrived_feedback"
    # Feedback that does not belong to its request (other command) is a
    # contract violation, as is a collision flag on an arrived status.
    result = _evaluate(feedback=_feedback(command_id="command-other-0002"))
    assert result["contract_error"] == "PORTABLE_COMMAND_OWNERSHIP_MISMATCH"
    result = _evaluate(feedback=_feedback(request_id="feedback:alternate-other"))
    assert result["contract_error"] == "PORTABLE_LINEAGE_MISMATCH"
    result = _evaluate(feedback=_feedback(collision_detected=True))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "PORTABLE_COLLISION_STATUS_INVALID"


def test_feedback_safety_violations_are_hard_fail() -> None:
    result = _evaluate(feedback=_feedback(minimum_clearance_m=0.05))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "feedback_clearance_violation"
    assert result["feedback_minimum_clearance_m"] == pytest.approx(0.05)
    assert _evaluate(feedback=_feedback(minimum_clearance_m=0.10))["outcome"] == OUTCOME_PASS

    # The cutoff handed to the evaluator must be at least the feedback time.
    result = _evaluate(not_before_unix=FEEDBACK_OBSERVED_UNIX - 0.2)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "cutoff_predates_feedback"
    assert _evaluate(not_before_unix=FEEDBACK_OBSERVED_UNIX)["outcome"] == OUTCOME_PASS

    result = _evaluate(feedback=_feedback(velocity=[0.0, 0.1, 0.0]))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_not_stationary"
    assert result["arrived_feedback_speed_mps"] == pytest.approx(0.1)
    assert result["qwen_fallback_allowed"] is False

    result = _evaluate(feedback=_feedback(position=[3.15 - 0.30, 0.0, 0.0]))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_inconsistent_with_feedback"
    assert result["feedback_agent_planar_divergence_m"] == pytest.approx(0.30)
    assert result["qwen_fallback_allowed"] is False

    # Safety violations are not downgraded even when observation is also short.
    two_rays = [_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]]
    result = _evaluate(frames=_frames(rays=two_rays), feedback=_feedback(velocity=[0.2, 0.0, 0.0]))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_not_stationary"
    certification = _certification(surface_id=None, surface_role=None, surface_id_semantics_revision=None)
    result = _evaluate(certification=certification, feedback=_feedback(position=[2.0, 0.0, 0.0]))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_inconsistent_with_feedback"


def test_semantics_revision_mismatch_in_fresh_frame_is_hard_fail() -> None:
    result = _evaluate(frames=_frames(semantics_revision="legacy_body_identity_v1"))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["contract_error"] == "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH"

    result = _evaluate(frames=_frames(semantics_revision=None))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["contract_error"] == "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH"


def test_same_id_with_different_role_is_hard_fail() -> None:
    rays = [_ray(uv, NEAR_RANGE_M, surface_role="walkable_ground") for uv in NEAR_UVS]
    result = _evaluate(frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "surface_role_contradiction"
    assert result["frame_index"] == 1
    assert result["frames"][1]["reference_role_conflict_ray_count"] == 3
    _assert_no_surface_id_leak(result)

    mixed = _default_rays() + [_ray((0.40, 0.40), NEAR_RANGE_M, surface_role="physical_surface")]
    result = _evaluate(frames=_frames(rays=mixed))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "contract_invalid"
    assert result["contract_error"] == "PORTABLE_SURFACE_ID_ROLE_CONFLICT"

    cross_frame = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, rays=rays),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX),
    ]
    result = _evaluate(frames=cross_frame)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "surface_role_contradiction"
    assert result["frame_index"] == 0


def test_hard_fail_takes_precedence_over_insufficient_observation() -> None:
    two_rays = [_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]]
    other_camera = _camera(coordinate_frame_id="world:alternate-engine-reloaded")
    frames = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, rays=two_rays),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX, rays=two_rays, camera=other_camera),
    ]
    result = _evaluate(frames=frames)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "coordinate_frame_changed"

    certification = _certification(surface_id=None, surface_role=None, surface_id_semantics_revision=None)
    result = _evaluate(
        certification=certification,
        fresh_map=_map(_map_request(source_frame_id=FIRST_FRESH_FRAME_ID)),
    )
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "map_lineage_mismatch"

    # A role contradiction in the rays is still a hard failure when the
    # certification identity is intact.
    rays = [_ray(uv, NEAR_RANGE_M, surface_role="walkable_ground") for uv in NEAR_UVS]
    result = _evaluate(frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "surface_role_contradiction"

    # Blocked cell plus too few rays: the safety violation wins.
    blocked = _map(obstacles=[TARGET_OBSTACLE, {"kind": "rect", "center_m": [3.15, 0.0], "half_extents_m": [0.1, 0.1]}])
    result = _evaluate(frames=_frames(rays=two_rays), fresh_map=blocked)
    assert result["outcome"] == OUTCOME_HARD_FAIL
    assert result["reason"] == "agent_position_blocked_after_inflation"


# --------------------------------------------------------------------------
# Adversarial: insufficient observation (fallback_eligible)
# --------------------------------------------------------------------------


def test_only_two_exact_rays_is_fallback_eligible() -> None:
    two_rays = [_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]]
    result = _evaluate(frames=_frames(rays=two_rays))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "insufficient_exact_reference_rays"
    assert result["frame_index"] == 1
    assert result["qwen_fallback_allowed"] is True
    assert result["arrival_confirmed"] is False
    assert result["arrival_distance_m"] is None
    assert result["frames"][0]["exact_reference_ray_count"] == 3
    assert result["frames"][1]["exact_reference_ray_count"] == 2

    first_short = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, rays=two_rays),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX),
    ]
    result = _evaluate(frames=first_short)
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["frame_index"] == 0


def test_rays_beyond_reference_distance_do_not_count() -> None:
    rays = [
        _ray((0.48, 0.48), NEAR_RANGE_M),
        _ray((0.50, 0.50), NEAR_RANGE_M),
        _ray((0.52, 0.52), 1.10),
    ]
    result = _evaluate(frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "insufficient_exact_reference_rays"
    newest = result["frames"][1]
    assert newest["exact_reference_ray_count"] == 2
    assert newest["reference_rays_beyond_maximum_distance_excluded"] == 1


def test_wrong_surface_id_is_fallback_eligible() -> None:
    occluder = [_ray(uv, 0.40, surface_id=OTHER_SURFACE_ID) for uv in NEAR_UVS]
    result = _evaluate(frames=_frames(rays=occluder))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "reference_surface_not_observed"
    newest = result["frames"][1]
    assert newest["exact_reference_ray_count"] == 0
    assert newest["nonreference_ray_count_excluded"] == 3
    assert newest["surface_id_match"] is False
    _assert_no_surface_id_leak(result)


def test_missing_surface_id_is_fallback_eligible() -> None:
    unidentified = [_ray(uv, NEAR_RANGE_M, surface_id=None, surface_role=None) for uv in NEAR_UVS]
    result = _evaluate(frames=_frames(rays=unidentified, semantics_revision=None))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "surface_identity_unavailable"
    assert result["frames"][1]["unidentified_ray_count_excluded"] == 3
    assert result["frames"][1]["semantics_revision_match"] is False

    certification = _certification(surface_id=None, surface_role=None, surface_id_semantics_revision=None)
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "certification_surface_identity_unavailable"


def test_missing_surface_role_is_fallback_eligible() -> None:
    roleless = [_ray(uv, NEAR_RANGE_M, surface_role=None) for uv in NEAR_UVS]
    result = _evaluate(frames=_frames(rays=roleless))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "insufficient_exact_reference_rays"
    assert result["frames"][1]["reference_role_missing_ray_count_excluded"] == 3
    assert result["frames"][1]["surface_role_match"] is False

    certification = _certification(surface_role=None)
    result = _evaluate(certification=certification)
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "certification_surface_role_unavailable"


def test_non_obstacle_certified_role_is_not_a_fast_path_target() -> None:
    certification = _certification(surface_role="walkable_ground", expected_surface_role="walkable_ground")
    rays = [_ray(uv, NEAR_RANGE_M, surface_role="walkable_ground") for uv in NEAR_UVS]
    result = _evaluate(certification=certification, frames=_frames(rays=rays))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "fast_path_role_not_supported"
    assert result["certification_surface_role"] == "walkable_ground"


def test_temporarily_hidden_target_is_fallback_eligible() -> None:
    hidden_second = _default_rays()[:1] + [
        _ray((0.50, 0.50), 0.30, surface_id=OTHER_SURFACE_ID),
        _ray((0.52, 0.52), 0.30, surface_id=OTHER_SURFACE_ID),
    ]
    result = _evaluate(frames=_frames(rays=hidden_second))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "insufficient_exact_reference_rays"
    assert result["frames"][1]["exact_reference_ray_count"] == 1
    assert result["frames"][1]["nonreference_ray_count_excluded"] == 2


def test_missing_depth_is_fallback_eligible() -> None:
    result = _evaluate(frames=_frames(depth=False))
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "depth_unavailable"
    assert result["frames"][1]["depth_available"] is False


def test_vertical_feedback_offset_is_not_planar_divergence() -> None:
    result = _evaluate(feedback=_feedback(position=[3.15, 0.0, 0.5]))
    assert result["outcome"] == OUTCOME_PASS
    assert result["feedback_agent_planar_divergence_m"] == pytest.approx(0.0)


def test_distance_beyond_threshold_with_intact_identity_is_fallback_eligible() -> None:
    far_camera = _camera(position_m=[2.5, 0.0, 1.4])
    rays = [_ray(uv, 1.5) for uv in NEAR_UVS]
    frames = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, rays=rays, camera=far_camera),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX, rays=rays, camera=far_camera),
    ]
    result = _evaluate(
        frames=frames,
        fresh_map=_map(agent_position=[2.5, 0.0, 0.0]),
        feedback=_feedback(position=[2.5, 0.0, 0.0]),
    )
    assert result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    assert result["reason"] == "arrival_distance_not_confirmed"
    assert result["checks"]["agent_stationary"] is True
    assert result["arrival_distance_m"] == pytest.approx(1.5, abs=0.01)
    assert result["checks"]["dual_fresh_exact_reference_rays"] is True
    assert "arrival_distance_within_unchanged_threshold" not in result["checks"]


# --------------------------------------------------------------------------
# Engine neutrality and appearance blindness
# --------------------------------------------------------------------------


def test_alternate_engine_fixture_has_no_godot_names_and_passes() -> None:
    certification = _certification()
    frames = _frames()
    fresh_map = _map()
    serialized_inputs = json.dumps([certification, frames, fresh_map, _feedback(), _feedback_request()])
    for forbidden in ("node_path", "NodePath", "instance_id", "get_node", "Godot", "godot", "/root/"):
        assert forbidden not in serialized_inputs

    result = _evaluate(certification, frames, fresh_map)

    assert result["outcome"] == OUTCOME_PASS
    serialized_result = json.dumps(result)
    for forbidden in ("node_path", "NodePath", "instance_id", "get_node", "Godot", "godot", "/root/"):
        assert forbidden not in serialized_result
    module_source = Path(fast_path.__file__).read_text(encoding="utf-8")
    body = module_source.split('"""', 2)[2]  # skip the module docstring
    for forbidden in ("node_path", "instance_id", "get_node", "godot", "object_name", "target_node"):
        assert forbidden not in body.lower()


def test_appearance_only_change_is_not_detectable_by_fast_path() -> None:
    baseline = _evaluate()
    repainted_frames = [
        _frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX, rgb_bytes=ALTERNATE_RGB_BYTES),
        _frame(SECOND_FRESH_FRAME_ID, SECOND_FRESH_UNIX, rgb_bytes=ALTERNATE_RGB_BYTES),
    ]
    repainted = _evaluate(frames=repainted_frames)

    assert baseline["outcome"] == repainted["outcome"] == OUTCOME_PASS
    assert repainted["arrival_distance_m"] == pytest.approx(baseline["arrival_distance_m"])
    assert repainted["semantic_revalidation_performed"] is False
    assert repainted["semantic_change_detectable"] is False
    assert repainted["appearance_material_category_changes_detectable"] is False
    assert baseline["checks"]["frame_content_identical"] is True
    mixed = _evaluate(frames=_frames(rgb_bytes=ALTERNATE_RGB_BYTES))
    assert mixed["outcome"] == OUTCOME_PASS
    assert mixed["checks"]["frame_content_identical"] is False


# --------------------------------------------------------------------------
# Helper-level behaviour
# --------------------------------------------------------------------------


def test_count_exact_reference_rays_dedupes_uv_and_excludes_far_same_id() -> None:
    reference = [[4.0, 0.0, 1.4]]
    rays = [
        {"uv_norm": [0.5, 0.5], "position_m": [4.0, 0.0, 1.4], "surface_id": SURFACE_ID, "surface_role": "navigation_obstacle"},
        {"uv_norm": [0.5, 0.5], "position_m": [4.01, 0.0, 1.4], "surface_id": SURFACE_ID, "surface_role": "navigation_obstacle"},
        {"uv_norm": [0.6, 0.5], "position_m": [4.1, 0.0, 1.4], "surface_id": SURFACE_ID, "surface_role": "navigation_obstacle"},
        {"uv_norm": [0.7, 0.5], "position_m": [5.0, 0.0, 1.4], "surface_id": SURFACE_ID, "surface_role": "navigation_obstacle"},
        {"uv_norm": [0.8, 0.5], "position_m": [4.0, 0.0, 1.4], "surface_id": OTHER_SURFACE_ID, "surface_role": "navigation_obstacle"},
        {"uv_norm": [0.9, 0.5], "position_m": [4.0, 0.0, 1.4]},
        {"uv_norm": [0.4, 0.5], "position_m": [4.0, 0.0, 1.4], "surface_id": SURFACE_ID},
    ]
    counts = count_exact_reference_rays(
        rays,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
        reference_points=reference,
        maximum_distance_m=0.20,
    )
    assert counts["exact_reference_ray_count"] == 2
    assert counts["duplicate_uv_excluded"] == 1
    assert counts["reference_rays_beyond_maximum_distance_excluded"] == 1
    assert counts["nonreference_ray_count_excluded"] == 1
    assert counts["unidentified_ray_count_excluded"] == 1
    assert counts["reference_role_missing_ray_count_excluded"] == 1
    assert counts["reference_role_conflict_ray_count"] == 0
    assert counts["surface_id_match"] is True
    assert counts["surface_role_match"] is True


def test_reason_code_registries_match_the_source() -> None:
    source = Path(fast_path.__file__).read_text(encoding="utf-8")
    import re

    hard_in_source = set(re.findall(r'_hard_fail\(\s*\n?\s*"([a-z_]+)"', source))
    hard_in_source |= {"contract_invalid"}  # emitted through _contract_invalid()
    hard_in_source |= set(re.findall(r'"(agent_position_[a-z_]+)"\n?\s*if reason == "out_of_bounds"', source))
    hard_in_source |= {"agent_position_blocked_after_inflation", "agent_position_out_of_bounds"}
    hard_in_source |= set(re.findall(r'audit\["contradiction"\] = "([a-z_]+)"', source))
    fallback_in_source = set(re.findall(r'_fallback\(\s*\n?\s*"([a-z_]+)"', source))
    fallback_in_source |= set(re.findall(r'audit\["insufficiency"\] = "([a-z_]+)"', source))

    assert hard_in_source == fast_path.HARD_FAIL_REASONS
    assert fallback_in_source == fast_path.FALLBACK_REASONS
    assert not (fast_path.HARD_FAIL_REASONS & fast_path.FALLBACK_REASONS)
    assert fast_path.PASS_REASON not in fast_path.HARD_FAIL_REASONS | fast_path.FALLBACK_REASONS
    assert set(fast_path.REASONS_BY_OUTCOME) == {OUTCOME_PASS, OUTCOME_HARD_FAIL, OUTCOME_FALLBACK_ELIGIBLE}

    # Every emitted result carries a registered reason for its outcome.
    for result in (
        _evaluate(),
        _evaluate(frames=_frames(rays=[_ray(uv, NEAR_RANGE_M) for uv in NEAR_UVS[:2]])),
        _evaluate(frames=[_frame(FIRST_FRESH_FRAME_ID, FIRST_FRESH_UNIX)] * 2),
        _evaluate(feedback=_feedback(velocity=[0.2, 0.0, 0.0])),
    ):
        assert result["reason"] in fast_path.REASONS_BY_OUTCOME[result["outcome"]]


def test_certification_digest_covers_every_identity_field() -> None:
    certification = _certification()
    baseline = compute_initial_certification_digest(certification)
    for field in fast_path.INITIAL_CERTIFICATION_DIGEST_FIELDS:
        mutated = copy.deepcopy(certification)
        value = mutated[field]
        if isinstance(value, bool):
            mutated[field] = not value
        elif isinstance(value, str):
            mutated[field] = value + "x"
        elif isinstance(value, list):
            mutated[field] = value + [[0.0, 0.0, 0.0]]
        else:
            mutated[field] = float(value) + 1.0
        assert compute_initial_certification_digest(mutated) != baseline, field
    assert "certification_sha256" not in fast_path.INITIAL_CERTIFICATION_DIGEST_FIELDS
    for field in (
        "environment_id", "session_id", "expected_surface_role", "semantic_frame_id",
        "post_inference_frame_id", "semantic_map_id", "navigation_map_id",
        "dual_grounding_surface_id_match", "dual_grounding_surface_role_match",
        "dual_grounding_semantics_revision_match",
    ):
        assert field in fast_path.INITIAL_CERTIFICATION_DIGEST_FIELDS
