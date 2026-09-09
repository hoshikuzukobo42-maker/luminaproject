"""Offline acceptance tests for the engine-portable navigation boundary.

These tests deliberately do not start Godot, contact Qwen, or touch the live
exhibition world.  Synthetic detections are contract fixtures, not evidence of
VLM quality.  Their purpose is to prove name-independent grounding, portable
route planning, and fail-closed boundary behaviour.
"""
from __future__ import annotations

import base64
import asyncio
import copy
import hashlib
import json
import struct
import uuid
from pathlib import Path

import pytest

from lumina_next.spatial_navigation_v1 import build_route_plan
from lumina_next.visual_navigation import NavigationError, ground_detection
from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_REQUEST_SCHEMA,
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    FEEDBACK_RESULT_SCHEMA,
    GROUND_REQUEST_SCHEMA,
    GROUND_RESULT_SCHEMA,
    MAP_REQUEST_SCHEMA,
    NAVIGATE_REQUEST_SCHEMA,
    NAVIGATE_RESULT_SCHEMA,
    PortableNavigationAdapter,
    PortableNavigationContractError,
    STOP_REQUEST_SCHEMA,
    STOP_RESULT_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    validate_capture_result,
    validate_feedback_result,
    validate_ground_result,
    validate_navigate_request,
    validate_navigate_result,
    validate_stop_result,
)
from lumina_next.godot_portable_navigation_adapter_v1 import (
    GodotPortableNavigationAdapter,
    canonical_to_godot,
)
from lumina_next.portable_navigation_core_v1 import (
    PortableNavigationCoreError,
    build_occupancy_map,
    ground_bbox_to_depth,
    plan_path,
    project_depth_samples,
    validate_path_collision,
)


def _detection(box: list[float]) -> dict:
    return {
        "intent": "approach",
        "visible": True,
        "label": "sofa",
        "confidence": 0.93,
        "bbox": box,
    }


def _visual_world() -> dict:
    return {
        "evaluator_geometry": {
            "objects": [
                {
                    "name": "Sofa",
                    "instance_id": 101,
                    "world_position": [4.5, 0.0, -7.25],
                    "projected_bbox": [0.08, 0.28, 0.42, 0.74],
                    "occluded": False,
                    "in_frustum": True,
                    "can_approach": True,
                },
                {
                    "name": "PlantFrontRight",
                    "instance_id": 202,
                    "world_position": [-3.0, 0.0, -5.0],
                    "projected_bbox": [0.66, 0.20, 0.89, 0.68],
                    "occluded": False,
                    "in_frustum": True,
                    "can_approach": True,
                },
            ]
        }
    }


def _opaque_name(seed: str) -> str:
    return "opaque:" + uuid.uuid5(uuid.NAMESPACE_URL, seed).hex


def _opaque_target_id(seed: str) -> str:
    return "target:" + uuid.uuid5(uuid.NAMESPACE_URL, seed).hex


def _common_request(schema: str, *, request_id: str, now: float = 100.0) -> dict:
    return {
        "schema_version": schema,
        "request_id": request_id,
        "environment_id": "environment:non-godot-fixture",
        "session_id": "session:portable-acceptance",
        "requested_unix": now,
    }


def _inline_plane(raw: bytes, encoding: str, width: int = 1, height: int = 1) -> dict:
    return {
        "encoding": encoding,
        "width_px": width,
        "height_px": height,
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _assert_contract_code(expected: str, function, *args, **kwargs) -> None:
    with pytest.raises(PortableNavigationContractError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == expected


def _sparse_capture(samples: list[dict], *, depth: bool = True) -> dict:
    rgb = b"\x89PNG\r\n\x1a\nportable-core-fixture"
    normalized_samples = [
        {
            "uv_norm": [float(sample["uv_norm"][0]), float(sample["uv_norm"][1])],
            "distance_m": float(sample["distance_m"]),
            "confidence": float(sample.get("confidence", 1.0)),
        }
        for sample in samples
    ]
    depth_value = None
    if depth:
        encoded = json.dumps(
            normalized_samples,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        depth_value = {
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": "registered_normalized_to_rgb",
            "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
            "samples": normalized_samples,
            "samples_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": "capture:core",
        "environment_id": "environment:non-godot-fixture",
        "session_id": "session:portable-acceptance",
        "frame_id": "frame:core:1",
        "captured_unix": 100.0,
        "rgb": _inline_plane(rgb, "png", width=100, height=100),
        "depth": depth_value,
        "camera": {
            "coordinate_frame_id": "world:core:1",
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": [0.0, 0.0, 0.0],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "intrinsics": {"fx_px": 100.0, "fy_px": 100.0, "cx_px": 50.0, "cy_px": 50.0},
        },
    }


def _core_ground_request() -> dict:
    return {
        **_common_request(GROUND_REQUEST_SCHEMA, request_id="ground:core", now=100.0),
        "source_frame_id": "frame:core:1",
        "map_id": "map:core:1",
        "detection": {
            "detection_id": "detection:core:1",
            "label": "unseen portable object",
            "confidence": 0.95,
            "bbox_norm": [0.40, 0.40, 0.60, 0.60],
        },
    }


def test_grounding_is_invariant_when_every_scene_node_name_is_randomized() -> None:
    """Scene names may carry identity, but must not select the visual target."""

    original = _visual_world()
    renamed = copy.deepcopy(original)
    for index, item in enumerate(renamed["evaluator_geometry"]["objects"]):
        item["name"] = _opaque_name(f"portable-acceptance:{index}")
        item["type"] = _opaque_name(f"portable-type:{index}")

    detection = _detection([0.09, 0.29, 0.41, 0.73])
    before = ground_detection(detection, original)
    after = ground_detection(detection, renamed)

    assert before["instance_id"] == after["instance_id"] == 101
    assert before["world_position"] == after["world_position"]
    assert before["visual_box_iou"] == after["visual_box_iou"]
    assert after["name"].startswith("opaque:")
    assert detection["label"].casefold() not in after["name"].casefold()


def test_grounding_accepts_a_never_seen_synthetic_world_layout() -> None:
    """Grounding uses the supplied camera projection, not the demo-room layout."""

    world = {
        "evaluator_geometry": {
            "objects": [
                {
                    "name": _opaque_name("alternate-world-target"),
                    "instance_id": 900_001,
                    "world_position": [-12.75, 1.25, 31.5],
                    "projected_bbox": [0.71, 0.11, 0.94, 0.48],
                    "occluded": False,
                    "in_frustum": True,
                    "can_approach": True,
                },
                {
                    "name": _opaque_name("alternate-world-distractor"),
                    "instance_id": 900_002,
                    "world_position": [18.0, -2.0, 4.0],
                    "projected_bbox": [0.04, 0.52, 0.25, 0.95],
                    "occluded": False,
                    "in_frustum": True,
                    "can_approach": True,
                },
            ]
        }
    }
    detection = _detection([0.72, 0.12, 0.93, 0.47])
    detection["label"] = "fire extinguisher"

    grounded = ground_detection(detection, world)

    assert grounded["instance_id"] == 900_001
    assert grounded["world_position"] == [-12.75, 1.25, 31.5]
    assert "fire" not in grounded["name"]


def test_alternate_world_route_detours_around_wall_and_unknown_cells() -> None:
    """The common planner consumes a portable grid and produces a real detour."""

    blocked = {(3, 1), (3, 2), (3, 3), (3, 4), (3, 5)}
    occluded = {(1, 2), (2, 2)}
    request = {
        "navigation_id": "portable-acceptance:alternate-world",
        "correlation_id": "portable-acceptance:alternate-world",
        "user_id": "portable-acceptance:agent",
        "destination": {
            "target_id": _opaque_target_id("route-target"),
            "kind": "object",
            "arrival_radius_cells": 1,
        },
        "max_waypoints": 64,
        "action_request": {
            "intent": {
                "type": "approach",
                "parameters": {"max_travel_distance_m": 100.0},
            }
        },
    }
    world = {
        "snapshot_id": "alternate-world:snapshot:1",
        "width": 7,
        "height": 7,
        "cell_size_m": 0.5,
        "blocked": blocked,
        "occluded": occluded,
        "start": (0, 3),
        "goal": (6, 3),
    }

    plan = build_route_plan(request, world)
    path = [tuple(cell) for cell in plan["waypoints"]]

    assert path[0] == world["start"]
    assert abs(path[-1][0] - world["goal"][0]) + abs(path[-1][1] - world["goal"][1]) == 1
    assert not (blocked | occluded).intersection(path)
    assert any(y in {0, 6} for _, y in path), "route did not go around the wall"
    assert len(path) > 6, "a straight-line route would incorrectly cross the wall"


def test_overlapping_candidates_remain_ambiguous_after_name_randomization() -> None:
    world = _visual_world()
    target = world["evaluator_geometry"]["objects"][0]
    duplicate = copy.deepcopy(target)
    duplicate.update(name=_opaque_name("ambiguous"), instance_id=303)
    world["evaluator_geometry"]["objects"] = [target, duplicate]

    with pytest.raises(NavigationError, match="^ambiguous$"):
        ground_detection(_detection([0.09, 0.29, 0.41, 0.73]), world)


def test_adapter_protocol_is_engine_agnostic_and_requires_all_six_operations() -> None:
    class AlternateEngineAdapter:
        async def capture(self, request):
            return request

        async def build_map(self, request):
            return request

        async def ground(self, request):
            return request

        async def navigate(self, request):
            return request

        async def stop(self, request):
            return request

        async def feedback(self, request):
            return request

    class MissingSafetyStop:
        async def capture(self, request):
            return request

        async def build_map(self, request):
            return request

        async def ground(self, request):
            return request

        async def navigate(self, request):
            return request

        async def feedback(self, request):
            return request

    assert isinstance(AlternateEngineAdapter(), PortableNavigationAdapter)
    assert not isinstance(MissingSafetyStop(), PortableNavigationAdapter)


def test_portable_navigation_request_accepts_opaque_ids_and_rejects_godot_fields() -> None:
    request = {
        "schema_version": NAVIGATE_REQUEST_SCHEMA,
        "navigation_id": "navigation:alternate-engine:1",
        "command_id": "command:caller-owned:1",
        "environment_id": "environment:alternate-engine",
        "session_id": "session:portable-acceptance",
        "requested_unix": 100.0,
        "deadline_unix": 120.0,
        "source_frame_id": "frame:alternate:1",
        "map_id": "map:alternate:1",
        "target_id": _opaque_target_id("portable-target"),
        "coordinate_frame_id": "world:alternate:1",
        "coordinate_convention": COORDINATE_CONVENTION,
        "waypoints_m": [[0.0, 0.0, 0.0], [1.0, -2.0, 0.0]],
        "arrival_radius_m": 0.7,
        "max_speed_mps": 1.2,
        "minimum_clearance_m": 0.35,
    }
    normalized = validate_navigate_request(request)
    assert normalized["target_id"] == request["target_id"]
    assert normalized["command_id"] == request["command_id"]
    assert "node" not in normalized["target_id"]

    leaked_native_request = dict(request, target_node="Sofa", godot_instance_id=123)
    _assert_contract_code("PORTABLE_FIELDS_UNKNOWN", validate_navigate_request, leaked_native_request)
    node_name_as_identity = dict(request, target_id="Sofa")
    _assert_contract_code("PORTABLE_TARGET_ID_NOT_OPAQUE", validate_navigate_request, node_name_as_identity)

    accepted = {
        "schema_version": NAVIGATE_RESULT_SCHEMA,
        "navigation_id": request["navigation_id"],
        "environment_id": request["environment_id"],
        "session_id": request["session_id"],
        "map_id": request["map_id"],
        "target_id": request["target_id"],
        "status": "accepted",
        "command_id": request["command_id"],
        "accepted_unix": request["requested_unix"],
        "reason": None,
    }
    assert validate_navigate_result(accepted, request=request)["command_id"] == request["command_id"]
    wrong_owner = dict(accepted, command_id="command:adapter-substituted")
    _assert_contract_code(
        "PORTABLE_COMMAND_OWNERSHIP_MISMATCH",
        validate_navigate_result,
        wrong_owner,
        request=request,
    )


def test_stale_depth_capture_is_rejected_at_the_portable_boundary() -> None:
    request = {
        **_common_request(CAPTURE_REQUEST_SCHEMA, request_id="capture:stale", now=100.0),
        "require_depth": True,
        "max_age_ms": 100,
    }
    rgb = b"\x89PNG\r\n\x1a\nportable-fixture"
    depth = struct.pack("<f", 2.5)
    result = {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "environment_id": request["environment_id"],
        "session_id": request["session_id"],
        "frame_id": "frame:stale:1",
        "captured_unix": 99.899,
        "rgb": _inline_plane(rgb, "png"),
        "depth": {
            "representation": "dense",
            "alignment": "registered_normalized_to_rgb",
            "measurement_model": "optical_axis_z_m",
            "invalid_value": "nan",
            **_inline_plane(depth, "f32_le_m"),
        },
        "camera": {
            "coordinate_frame_id": "world:alternate:1",
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": [0.0, 0.0, 1.4],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "intrinsics": {"fx_px": 1.0, "fy_px": 1.0, "cx_px": 0.5, "cy_px": 0.5},
        },
    }
    _assert_contract_code(
        "PORTABLE_CAPTURE_STALE",
        validate_capture_result,
        result,
        request=request,
        now_unix=100.0,
    )
    fresh_without_depth = dict(result, captured_unix=100.0, depth=None)
    _assert_contract_code(
        "PORTABLE_DEPTH_REQUIRED",
        validate_capture_result,
        fresh_without_depth,
        request=request,
        now_unix=100.0,
    )


def test_ambiguous_grounding_is_explicit_and_cannot_smuggle_a_motion_target() -> None:
    request = {
        **_common_request(GROUND_REQUEST_SCHEMA, request_id="ground:ambiguous"),
        "source_frame_id": "frame:alternate:1",
        "map_id": "map:alternate:1",
        "detection": {
            "detection_id": "detection:qwen:1",
            "label": "sofa",
            "confidence": 0.91,
            "bbox_norm": [0.2, 0.3, 0.6, 0.8],
        },
    }
    result = {
        "schema_version": GROUND_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "environment_id": request["environment_id"],
        "session_id": request["session_id"],
        "source_frame_id": request["source_frame_id"],
        "map_id": request["map_id"],
        "detection_id": request["detection"]["detection_id"],
        "grounded_unix": 100.0,
        "status": "unresolved",
        "target_id": None,
        "label": request["detection"]["label"],
        "confidence": request["detection"]["confidence"],
        "coordinate_frame_id": None,
        "coordinate_convention": COORDINATE_CONVENTION,
        "position_m": None,
        "uncertainty_radius_m": None,
        "reason": "ambiguous",
    }
    unresolved = validate_ground_result(result, request=request)
    assert unresolved["status"] == "unresolved"
    assert unresolved["target_id"] is None

    smuggled = dict(result, target_id=_opaque_target_id("wrongly-selected"))
    _assert_contract_code(
        "PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN",
        validate_ground_result,
        smuggled,
        request=request,
    )


def test_collision_feedback_cannot_be_reported_as_moving_or_arrived() -> None:
    request = {
        **_common_request(FEEDBACK_REQUEST_SCHEMA, request_id="feedback:collision"),
        "command_id": "command:owned:1",
        "after_sequence": 4,
    }
    result = {
        "schema_version": FEEDBACK_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "environment_id": request["environment_id"],
        "session_id": request["session_id"],
        "command_id": request["command_id"],
        "sequence": 5,
        "observed_unix": 100.0,
        "status": "blocked",
        "position_m": [0.4, -0.2, 0.0],
        "velocity_mps": [0.0, 0.0, 0.0],
        "remaining_distance_m": 2.3,
        "minimum_clearance_m": 0.0,
        "collision_detected": True,
        "target_reached": False,
        "reason": "collision_sensor_triggered",
    }
    blocked = validate_feedback_result(result, request=request, now_unix=100.0)
    assert blocked["status"] == "blocked" and blocked["collision_detected"] is True

    still_moving = dict(result, status="moving", reason=None)
    _assert_contract_code(
        "PORTABLE_COLLISION_STATUS_INVALID",
        validate_feedback_result,
        still_moving,
        request=request,
        now_unix=100.0,
    )
    false_arrival = dict(result, status="arrived", target_reached=True, reason=None)
    _assert_contract_code(
        "PORTABLE_COLLISION_STATUS_INVALID",
        validate_feedback_result,
        false_arrival,
        request=request,
        now_unix=100.0,
    )


def test_stop_confirmation_is_command_owned_and_stale_feedback_is_rejected() -> None:
    feedback_request = {
        **_common_request(FEEDBACK_REQUEST_SCHEMA, request_id="feedback:sequence"),
        "command_id": "command:owned:1",
        "after_sequence": 7,
    }
    stale_feedback = {
        "schema_version": FEEDBACK_RESULT_SCHEMA,
        "request_id": feedback_request["request_id"],
        "environment_id": feedback_request["environment_id"],
        "session_id": feedback_request["session_id"],
        "command_id": feedback_request["command_id"],
        "sequence": 7,
        "observed_unix": 100.0,
        "status": "stopped",
        "position_m": [0.0, 0.0, 0.0],
        "velocity_mps": [0.0, 0.0, 0.0],
        "remaining_distance_m": 2.0,
        "minimum_clearance_m": 0.5,
        "collision_detected": False,
        "target_reached": False,
        "reason": None,
    }
    _assert_contract_code(
        "PORTABLE_FEEDBACK_SEQUENCE_STALE",
        validate_feedback_result,
        stale_feedback,
        request=feedback_request,
        now_unix=100.0,
    )

    stop_request = {
        **_common_request(STOP_REQUEST_SCHEMA, request_id="stop:owned"),
        "command_id": "command:owned:1",
        "reason": "collision_feedback",
    }
    stop_result = {
        "schema_version": STOP_RESULT_SCHEMA,
        "request_id": stop_request["request_id"],
        "environment_id": stop_request["environment_id"],
        "session_id": stop_request["session_id"],
        "owned_command_id": stop_request["command_id"],
        "status": "confirmed",
        "confirmed": True,
        "stopped_unix": 100.01,
        "final_sequence": 8,
        "reason": None,
    }
    confirmed = validate_stop_result(stop_result, request=stop_request, now_unix=100.01)
    assert confirmed["confirmed"] is True

    wrong_owner = dict(stop_result, owned_command_id="command:someone-else")
    _assert_contract_code(
        "PORTABLE_COMMAND_OWNERSHIP_MISMATCH",
        validate_stop_result,
        wrong_owner,
        request=stop_request,
        now_unix=100.01,
    )


def test_core_projects_metric_depth_and_never_needs_scene_names() -> None:
    capture = _sparse_capture([
        {"uv_norm": [0.48, 0.48], "distance_m": 2.00},
        {"uv_norm": [0.52, 0.48], "distance_m": 2.02},
        {"uv_norm": [0.48, 0.52], "distance_m": 2.01},
        {"uv_norm": [0.52, 0.52], "distance_m": 2.03},
    ])

    points = project_depth_samples(capture, bbox_norm=[0.40, 0.40, 0.60, 0.60])
    grounded = ground_bbox_to_depth(_core_ground_request(), capture, grounded_unix=100.0)

    assert len(points) == 4
    assert all(1.95 < point["range_m"] < 2.10 for point in points)
    assert grounded["status"] == "grounded"
    assert grounded["target_id"].startswith("target:")
    assert grounded["coordinate_frame_id"] == capture["camera"]["coordinate_frame_id"]
    serialized = json.dumps(grounded)
    assert "node" not in serialized.casefold()
    assert "instance" not in serialized.casefold()


def test_core_depth_grounding_fails_closed_for_missing_or_competing_surfaces() -> None:
    request = _core_ground_request()
    no_depth = _sparse_capture([], depth=False)
    assert ground_bbox_to_depth(request, no_depth, grounded_unix=100.0)["reason"] == "no_depth"

    competing = _sparse_capture([
        {"uv_norm": [0.47, 0.48], "distance_m": 2.00},
        {"uv_norm": [0.49, 0.52], "distance_m": 2.02},
        {"uv_norm": [0.51, 0.48], "distance_m": 4.00},
        {"uv_norm": [0.53, 0.52], "distance_m": 4.02},
    ])
    ambiguous = ground_bbox_to_depth(request, competing, grounded_unix=100.0)
    assert ambiguous["status"] == "unresolved"
    assert ambiguous["reason"] == "ambiguous"
    assert ambiguous["target_id"] is None and ambiguous["position_m"] is None


def test_core_plans_a_collision_checked_detour_in_an_alternate_world() -> None:
    map_request = {
        **_common_request(MAP_REQUEST_SCHEMA, request_id="map:core:detour", now=100.0),
        "source_frame_id": "frame:alternate-grid:1",
        "resolution_m": 0.25,
        "footprint_radius_m": 0.20,
    }
    # Both endpoints remain safely inside the newly explicit map-boundary
    # footprint band; the wall still forces a substantial top/bottom detour.
    start = [0.75, 4.0, 0.0]
    target = [7.25, 4.0, 0.0]
    portable_map = build_occupancy_map(
        map_request,
        bounds_xy=[0.0, 8.0, 0.0, 8.0],
        obstacles=[{"kind": "rect", "center_m": [4.0, 4.0], "half_extents_m": [0.20, 2.25]}],
        agent_position_m=start,
        minimum_clearance_m=0.0,
        created_unix=100.0,
    )

    assert not validate_path_collision(portable_map, [start, target], additional_inflation_m=0.10)
    plan = plan_path(
        portable_map,
        target,
        arrival_radius_m=0.40,
        minimum_clearance_m=0.10,
    )

    assert plan["status"] == "planned"
    assert validate_path_collision(portable_map, plan["path_m"], additional_inflation_m=0.10)
    assert any(point[1] < 1.2 or point[1] > 6.8 for point in plan["path_m"]), "planner crossed the wall"
    assert len(plan["waypoints_m"]) >= 2


def test_core_treats_unknown_and_out_of_bounds_cells_as_collisions() -> None:
    map_request = {
        **_common_request(MAP_REQUEST_SCHEMA, request_id="map:core:unknown", now=100.0),
        "source_frame_id": "frame:unknown-grid:1",
        "resolution_m": 1.0,
        "footprint_radius_m": 0.1,
    }
    portable_map = build_occupancy_map(
        map_request,
        bounds_xy=[0.0, 5.0, 0.0, 5.0],
        obstacles=[],
        agent_position_m=[1.5, 2.5, 0.0],
        created_unix=100.0,
    )
    grid = portable_map["grid"]
    occupancy = bytearray(base64.b64decode(grid["occupancy_base64"]))
    occupancy[2 * grid["width_cells"] + 2] = grid["unknown_value"]
    unknown_map = copy.deepcopy(portable_map)
    unknown_map["grid"]["occupancy_base64"] = base64.b64encode(occupancy).decode("ascii")
    unknown_map["grid"]["occupancy_sha256"] = hashlib.sha256(occupancy).hexdigest()

    assert not validate_path_collision(unknown_map, [[1.5, 2.5, 0.0], [3.5, 2.5, 0.0]])
    assert not validate_path_collision(unknown_map, [[1.5, 2.5, 0.0], [-0.5, 2.5, 0.0]])

    fully_blocked = copy.deepcopy(unknown_map)
    occupancy = bytearray(base64.b64decode(fully_blocked["grid"]["occupancy_base64"]))
    for row in range(fully_blocked["grid"]["height_cells"]):
        occupancy[row * fully_blocked["grid"]["width_cells"] + 2] = fully_blocked["grid"]["unknown_value"]
    fully_blocked["grid"]["occupancy_base64"] = base64.b64encode(occupancy).decode("ascii")
    fully_blocked["grid"]["occupancy_sha256"] = hashlib.sha256(occupancy).hexdigest()
    with pytest.raises(PortableNavigationCoreError) as caught:
        plan_path(fully_blocked, [3.5, 2.5, 0.0], arrival_radius_m=0.2)
    assert caught.value.code == "PORTABLE_CORE_NO_PATH"


def test_saved_case46_legacy_eye_sidecar_is_now_rejected_read_only(tmp_path) -> None:
    """A saved pre-attestation frame can no longer cross the adapter boundary."""

    case_dir = Path(
        "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-09-04/lu/work/lumina_completion/"
        "live_e2e/46_final_recovery_default_sofa"
    )
    source_json = case_dir / "recognition_eye.json"
    source_png = case_dir / "recognition_eye.png"
    if not source_json.is_file() or not source_png.is_file():
        pytest.skip("saved Case46 read-only evidence is not installed")
    original_json = source_json.read_bytes()
    original_png = source_png.read_bytes()
    metadata = json.loads(original_json)
    copied_png = tmp_path / "case46.png"
    copied_png.write_bytes(original_png)
    metadata["image_path"] = str(copied_png)
    (tmp_path / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")
    now = float(metadata["captured_unix"]) + 0.05
    adapter = GodotPortableNavigationAdapter(tmp_path, clock=lambda: now)
    request = {
        **_common_request(CAPTURE_REQUEST_SCHEMA, request_id="capture:case46", now=now),
        "require_depth": False,
        "max_age_ms": 500,
    }

    _assert_contract_code(
        "PORTABLE_GODOT_RENDER_ATTESTATION_INVALID",
        asyncio.run,
        adapter.capture(request),
    )
    assert source_json.read_bytes() == original_json
    assert source_png.read_bytes() == original_png


def test_godot_adapter_fake_transport_runs_portable_collision_to_owned_stop_chain(tmp_path) -> None:
    """Full adapter exchange; all Godot-native names remain behind the boundary."""

    async def exercise() -> None:
        now = 100.0
        image = b"\x89PNG\r\n\x1a\nportable-adapter-fixture"
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(image)
        native_name = _opaque_name("native-node-in-alternate-world")
        native_instance = 771_234
        metadata = {
            "captured_unix": now,
            "frame_id": "native-frame:alternate:1",
            "image_path": str(image_path),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "width": 2,
            "height": 2,
            "fov": 70.0,
            "camera_position": {"x": 0.0, "y": 1.4, "z": 0.0},
            "camera_forward": {"x": 0.0, "y": 0.0, "z": -1.0},
            "camera_right": {"x": 1.0, "y": 0.0, "z": 0.0},
            "camera_up": {"x": 0.0, "y": 1.0, "z": 0.0},
            "debug_target": "",
            "debug_target_alias": "",
            "debug_target_canonical": "",
            "render_source": "dedicated_eye_subviewport",
            "render_viewport_instance_id": 40,
            "render_camera_instance_id": 41,
            "active_camera_instance_id": 41,
            "portable_depth": {
                "available": True,
                "representation": "sparse_rays",
                "measurement_model": "ray_range_m",
                "alignment": "registered_normalized_to_rgb",
                "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
                "samples": [
                    {"uv_norm": [0.28, 0.38], "distance_m": 2.00, "confidence": 1.0},
                    {"uv_norm": [0.36, 0.38], "distance_m": 2.03, "confidence": 1.0},
                    {"uv_norm": [0.28, 0.52], "distance_m": 2.01, "confidence": 1.0},
                    {"uv_norm": [0.36, 0.52], "distance_m": 2.02, "confidence": 1.0},
                    {"uv_norm": [0.32, 0.45], "distance_m": 2.01, "confidence": 1.0},
                ],
            },
            # Deliberately poisoned simulator truth. Strict portable grounding
            # must use sparse depth and must never read this position or name.
            "evaluator_geometry": {
                "objects": [{
                    "name": "POISONED_EVALUATOR_NODE",
                    "instance_id": native_instance,
                    "world_position": {"x": 999.0, "y": 999.0, "z": 999.0},
                    "projected_bbox": [0.20, 0.20, 0.45, 0.72],
                    "occluded": False,
                    "in_frustum": True,
                    "can_approach": True,
                }]
            },
        }
        (tmp_path / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")
        portable_snapshot = {
            "schema_version": "lumina.godot.portable-navigation-snapshot.v1",
            "captured_unix": now,
            "coordinate_frame": "godot_x_right_y_up_z_back",
            "bounds_xz": {"min_x": -6.0, "max_x": 6.0, "min_z": -6.0, "max_z": 6.0},
            "agent": {
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
                "forward": {"x": 0.0, "y": 0.0, "z": -1.0},
                "footprint_radius_m": 0.30,
                "height_m": 1.6,
            },
            "obstacles": [{
                "obstacle_id": "native-obstacle:unrelated",
                "kind": "circle",
                "center_xz": {"x": 3.0, "z": 3.0},
                "radius_m": 0.4,
            }],
            "route": {
                "active": False,
                "blocked": False,
                "status": "idle",
                "detour_active": False,
                "remaining_waypoints": [],
                "replan_count": 0,
                "path_length_m": 0.0,
            },
            "collision": {
                "contact_count": 0,
                "on_floor": True,
                "on_wall": False,
                "on_ceiling": False,
                "route_segment_blocked": False,
            },
        }
        world = {
            "updated_at": now,
            "navigation_ready": True,
            "active_command_id": "",
            "nearby_objects": [],
            "raw": {"avatar_state": {"portable_navigation": portable_snapshot, "last_navigation_result": {}}},
        }
        calls: list[tuple[str, str, dict | None]] = []
        owned_command_id: str | None = None
        stop_command_id: str | None = None

        async def transport(method: str, path: str, body):
            nonlocal owned_command_id, stop_command_id
            captured_body = copy.deepcopy(dict(body)) if body is not None else None
            calls.append((method, path, captured_body))
            if method == "GET" and path == "/world-state":
                return copy.deepcopy(world)
            if method == "POST" and path == "/command":
                assert body is not None
                if body["action"] == "move_to_position":
                    owned_command_id = str(body["command_id"])
                    world["active_command_id"] = owned_command_id
                    portable_snapshot["route"].update(active=True, status="moving")
                elif body["action"] == "stop":
                    stop_command_id = str(body["command_id"])
                    assert body["params"]["expected_command_id"] == owned_command_id
                    world["active_command_id"] = ""
                    portable_snapshot["route"].update(active=False, status="stopped")
                return {"ok": True, "status": "sent", "godot_connected": True}
            if method == "GET" and path == "/results":
                if stop_command_id is None:
                    return {"items": []}
                return {"items": [
                    {"command_id": stop_command_id, "ok": True, "status": "completed", "received_at": now},
                    {"command_id": owned_command_id, "ok": False, "status": "interrupted", "received_at": now},
                ]}
            raise AssertionError(f"unexpected fake transport call: {method} {path}")

        async def no_wait(_seconds: float) -> None:
            return None

        adapter = GodotPortableNavigationAdapter(
            tmp_path,
            transport=transport,
            clock=lambda: now,
            sleep=no_wait,
            stop_timeout_seconds=0.0,
        )
        assert adapter.allow_evaluator_geometry_fallback is False
        capture_request = {
            **_common_request(CAPTURE_REQUEST_SCHEMA, request_id="capture:adapter", now=now),
            "require_depth": True,
            "max_age_ms": 500,
        }
        capture = await adapter.capture(capture_request)
        assert capture["depth"]["representation"] == "sparse_rays"

        map_request = {
            **_common_request(MAP_REQUEST_SCHEMA, request_id="map:adapter", now=now),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.5,
            "footprint_radius_m": 0.3,
        }
        portable_map = await adapter.build_map(map_request)
        occupancy = base64.b64decode(portable_map["grid"]["occupancy_base64"])
        assert 255 in occupancy

        ground_request = {
            **_common_request(GROUND_REQUEST_SCHEMA, request_id="ground:adapter", now=now),
            "source_frame_id": capture["frame_id"],
            "map_id": portable_map["map_id"],
            "detection": {
                "detection_id": "detection:adapter:1",
                "label": "sofa",
                "confidence": 0.93,
                "bbox_norm": [0.21, 0.21, 0.44, 0.71],
            },
        }
        grounded = await adapter.ground(ground_request)
        assert grounded["status"] == "grounded"
        assert grounded["target_id"].startswith("target:")
        assert native_name not in json.dumps(grounded)
        assert "POISONED_EVALUATOR_NODE" not in json.dumps(grounded)
        assert max(abs(component) for component in grounded["position_m"]) < 10.0

        midpoint = [
            (portable_map["agent"]["position_m"][axis] + grounded["position_m"][axis]) / 2.0
            for axis in range(3)
        ]
        navigate_request = {
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": "navigation:adapter:1",
            "command_id": "command:adapter-caller-owned:1",
            "environment_id": capture_request["environment_id"],
            "session_id": capture_request["session_id"],
            "requested_unix": now,
            "deadline_unix": now + 10.0,
            "source_frame_id": capture["frame_id"],
            "map_id": portable_map["map_id"],
            "target_id": grounded["target_id"],
            "coordinate_frame_id": portable_map["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": [midpoint, grounded["position_m"]],
            "arrival_radius_m": 0.7,
            "max_speed_mps": 1.0,
            "minimum_clearance_m": 0.2,
        }
        navigation = await adapter.navigate(navigate_request)
        assert navigation["status"] == "accepted"
        assert navigation["command_id"] == navigate_request["command_id"]
        assert owned_command_id == navigation["command_id"]
        movement_calls = [
            body for method, path, body in calls
            if method == "POST" and path == "/command" and body["action"].startswith("move_")
        ]
        assert len(movement_calls) == 1
        assert movement_calls[0]["action"] == "move_to_position"
        assert movement_calls[0]["position"] == canonical_to_godot(grounded["position_m"])
        assert movement_calls[0]["params"]["portable_route"] is True
        assert movement_calls[0]["params"]["portable_minimum_clearance_m"] == 0.2
        assert movement_calls[0]["params"]["portable_waypoints"] == [
            canonical_to_godot(waypoint) for waypoint in navigate_request["waypoints_m"]
        ]
        assert "target_node" not in movement_calls[0]
        assert "POISONED_EVALUATOR_NODE" not in json.dumps(movement_calls[0])

        portable_snapshot["route"].update(active=False, blocked=True, status="blocked_dynamic")
        portable_snapshot["collision"].update(contact_count=1, on_wall=True, route_segment_blocked=True)
        feedback_request = {
            **_common_request(FEEDBACK_REQUEST_SCHEMA, request_id="feedback:adapter", now=now),
            "command_id": navigation["command_id"],
            "after_sequence": -1,
        }
        feedback = await adapter.feedback(feedback_request)
        assert feedback["status"] == "blocked"
        assert feedback["collision_detected"] is True
        assert feedback["target_reached"] is False

        stop_request = {
            **_common_request(STOP_REQUEST_SCHEMA, request_id="stop:adapter", now=now),
            "command_id": navigation["command_id"],
            "reason": "collision_feedback",
        }
        stopped = await adapter.stop(stop_request)
        assert stopped["status"] == "confirmed"
        assert stopped["owned_command_id"] == navigation["command_id"]
        stop_calls = [body for method, path, body in calls if method == "POST" and path == "/command" and body["action"] == "stop"]
        assert len(stop_calls) == 1
        assert stop_calls[0]["params"]["expected_command_id"] == navigation["command_id"]

    asyncio.run(exercise())
