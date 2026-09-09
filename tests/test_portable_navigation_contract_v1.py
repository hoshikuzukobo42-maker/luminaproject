from __future__ import annotations

import base64
import copy
import hashlib
import json

import pytest

from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_REQUEST_SCHEMA,
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    FEEDBACK_RESULT_SCHEMA,
    GROUND_REQUEST_SCHEMA,
    GROUND_RESULT_SCHEMA,
    MAP_REQUEST_SCHEMA,
    MAP_RESULT_SCHEMA,
    NAVIGATE_REQUEST_SCHEMA,
    NAVIGATE_RESULT_SCHEMA,
    STOP_REQUEST_SCHEMA,
    STOP_RESULT_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    SURFACE_ROLES,
    PortableNavigationAdapter,
    PortableNavigationContractError,
    validate_capture_request,
    validate_capture_result,
    validate_feedback_request,
    validate_feedback_result,
    validate_ground_request,
    validate_ground_result,
    validate_map_request,
    validate_map_result,
    validate_navigate_request,
    validate_navigate_result,
    validate_stop_request,
    validate_stop_result,
)


NOW = 2_000_000_000.0
TARGET_ID = "target:0123456789abcdef"
SURFACE_ID = "surface:" + "a" * 64


def test_surface_id_semantics_are_pose_epoch_identity_not_semantic_identity() -> None:
    assert (
        SURFACE_ID_SEMANTICS_REVISION
        == "single_connected_component_world_pose_epoch_v2"
    )


def _encoded(raw: bytes) -> tuple[str, str]:
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def capture_request(**updates):
    value = {
        "schema_version": CAPTURE_REQUEST_SCHEMA,
        "request_id": "capture-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW,
        "require_depth": True,
        "max_age_ms": 500,
    }
    value.update(updates)
    return value


def capture_result(**updates):
    rgb, rgb_hash = _encoded(b"\x89PNG\r\n\x1a\nfixture")
    depth, depth_hash = _encoded(b"\x00" * 16)
    value = {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": "capture-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "frame_id": "frame-1",
        "captured_unix": NOW + 0.1,
        "rgb": {"encoding": "png", "width_px": 2, "height_px": 2, "data_base64": rgb, "sha256": rgb_hash},
        "depth": {
            "representation": "dense",
            "encoding": "f32_le_m",
            "measurement_model": "optical_axis_z_m",
            "invalid_value": "zero",
            "width_px": 2,
            "height_px": 2,
            "alignment": "registered_normalized_to_rgb",
            "data_base64": depth,
            "sha256": depth_hash,
        },
        "camera": {
            "coordinate_frame_id": "map-frame-1",
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": [0, 0, 1.4],
            "orientation_xyzw": [0, 0, 0, 1],
            "intrinsics": {"fx_px": 1, "fy_px": 1, "cx_px": 1, "cy_px": 1},
        },
    }
    value.update(updates)
    return value


def map_request(**updates):
    value = {
        "schema_version": MAP_REQUEST_SCHEMA,
        "request_id": "map-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW + 0.2,
        "source_frame_id": "frame-1",
        "resolution_m": 0.5,
        "footprint_radius_m": 0.3,
    }
    value.update(updates)
    return value


def map_result(**updates):
    occupancy, occupancy_hash = _encoded(bytes([0, 0, 255, 0]))
    value = {
        "schema_version": MAP_RESULT_SCHEMA,
        "request_id": "map-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "map_id": "map-1",
        "source_frame_id": "frame-1",
        "created_unix": NOW + 0.3,
        "coordinate_frame_id": "map-frame-1",
        "coordinate_convention": COORDINATE_CONVENTION,
        "grid": {
            "width_cells": 2,
            "height_cells": 2,
            "resolution_m": 0.5,
            "origin_position_m": [0, 0, 0],
            "origin_orientation_xyzw": [0, 0, 0, 1],
            "occupancy_encoding": "u8_probability",
            "occupancy_base64": occupancy,
            "occupancy_sha256": occupancy_hash,
            "unknown_value": 127,
            "occupied_threshold": 200,
        },
        "agent": {
            "position_m": [0, 0, 0],
            "orientation_xyzw": [0, 0, 0, 1],
            "footprint_radius_m": 0.3,
        },
    }
    value.update(updates)
    return value


def ground_request(**updates):
    value = {
        "schema_version": GROUND_REQUEST_SCHEMA,
        "request_id": "ground-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW + 0.4,
        "source_frame_id": "frame-1",
        "map_id": "map-1",
        "detection": {
            "detection_id": "detection-1",
            "label": "sofa",
            "confidence": 0.9,
            "bbox_norm": [0.1, 0.2, 0.8, 0.9],
        },
    }
    value.update(updates)
    return value


def ground_result(**updates):
    value = {
        "schema_version": GROUND_RESULT_SCHEMA,
        "request_id": "ground-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "source_frame_id": "frame-1",
        "map_id": "map-1",
        "detection_id": "detection-1",
        "grounded_unix": NOW + 0.5,
        "status": "grounded",
        "target_id": TARGET_ID,
        "label": "sofa",
        "confidence": 0.86,
        "coordinate_frame_id": "map-frame-1",
        "coordinate_convention": COORDINATE_CONVENTION,
        "position_m": [2, 0, 0],
        "uncertainty_radius_m": 0.15,
        "reason": None,
    }
    value.update(updates)
    return value


def navigate_request(**updates):
    value = {
        "schema_version": NAVIGATE_REQUEST_SCHEMA,
        "navigation_id": "navigation-1",
        "command_id": "command-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW + 0.5,
        "deadline_unix": NOW + 30,
        "source_frame_id": "frame-1",
        "map_id": "map-1",
        "target_id": TARGET_ID,
        "coordinate_frame_id": "map-frame-1",
        "coordinate_convention": COORDINATE_CONVENTION,
        "waypoints_m": [[0.5, 0, 0], [1.0, 0.5, 0], [1.7, 0, 0]],
        "arrival_radius_m": 0.3,
        "max_speed_mps": 0.6,
        "minimum_clearance_m": 0.2,
    }
    value.update(updates)
    return value


def navigate_result(**updates):
    value = {
        "schema_version": NAVIGATE_RESULT_SCHEMA,
        "navigation_id": "navigation-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "map_id": "map-1",
        "target_id": TARGET_ID,
        "status": "accepted",
        "command_id": "command-1",
        "accepted_unix": NOW + 0.6,
        "reason": None,
    }
    value.update(updates)
    return value


def feedback_request(**updates):
    value = {
        "schema_version": FEEDBACK_REQUEST_SCHEMA,
        "request_id": "feedback-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW + 1,
        "command_id": "command-1",
        "after_sequence": 2,
    }
    value.update(updates)
    return value


def feedback_result(**updates):
    value = {
        "schema_version": FEEDBACK_RESULT_SCHEMA,
        "request_id": "feedback-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "command_id": "command-1",
        "sequence": 3,
        "observed_unix": NOW + 1.1,
        "status": "moving",
        "position_m": [0.5, 0, 0],
        "velocity_mps": [0.3, 0, 0],
        "remaining_distance_m": 1.2,
        "minimum_clearance_m": 0.4,
        "collision_detected": False,
        "target_reached": False,
        "reason": None,
    }
    value.update(updates)
    return value


def stop_request(**updates):
    value = {
        "schema_version": STOP_REQUEST_SCHEMA,
        "request_id": "stop-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "requested_unix": NOW + 2,
        "command_id": "command-1",
        "reason": "user_requested",
    }
    value.update(updates)
    return value


def stop_result(**updates):
    value = {
        "schema_version": STOP_RESULT_SCHEMA,
        "request_id": "stop-request-1",
        "environment_id": "env-fixture",
        "session_id": "session-1",
        "owned_command_id": "command-1",
        "status": "confirmed",
        "confirmed": True,
        "stopped_unix": NOW + 2.1,
        "final_sequence": 4,
        "reason": None,
    }
    value.update(updates)
    return value


def test_complete_validated_exchange_chain_is_engine_neutral():
    assert validate_capture_request(capture_request())["require_depth"] is True
    assert validate_capture_result(capture_result(), request=capture_request(), now_unix=NOW + 0.2)["frame_id"] == "frame-1"
    assert validate_map_request(map_request())["source_frame_id"] == "frame-1"
    assert validate_map_result(map_result(), request=map_request(), now_unix=NOW + 0.4)["map_id"] == "map-1"
    assert validate_ground_request(ground_request())["detection"]["label"] == "sofa"
    assert validate_ground_result(ground_result(), request=ground_request())["target_id"] == TARGET_ID
    assert validate_navigate_request(navigate_request())["waypoints_m"][-1] == [1.7, 0.0, 0.0]
    assert validate_navigate_result(
        navigate_result(), request=navigate_request(), now_unix=NOW + 0.7
    )["command_id"] == "command-1"
    assert validate_feedback_request(feedback_request())["after_sequence"] == 2
    assert validate_feedback_result(
        feedback_result(), request=feedback_request(), now_unix=NOW + 1.2
    )["status"] == "moving"
    assert validate_stop_request(stop_request())["command_id"] == "command-1"
    assert validate_stop_result(stop_result(), request=stop_request(), now_unix=NOW + 2.2)["confirmed"] is True


def test_ground_request_accepts_only_bounded_normalized_occluder_geometry() -> None:
    request = ground_request()
    request["detection"]["occluder_bboxes_norm"] = [
        [0.40, 0.10, 0.60, 0.90],
        [0.05, 0.30, 0.20, 0.70],
    ]
    normalized = validate_ground_request(request)
    assert normalized["detection"]["occluder_bboxes_norm"] == request["detection"]["occluder_bboxes_norm"]

    for invalid in (
        "not-a-list",
        [[0.0, 0.0, 1.0, 1.0]] * 9,
        [[-0.1, 0.0, 0.5, 0.5]],
        [[0.5, 0.5, 0.5, 0.8]],
    ):
        malformed = ground_request()
        malformed["detection"]["occluder_bboxes_norm"] = invalid
        with pytest.raises(PortableNavigationContractError):
            validate_ground_request(malformed)


@pytest.mark.parametrize("surface_role", sorted(SURFACE_ROLES))
def test_ground_request_preserves_an_optional_expected_surface_role(
    surface_role: str,
) -> None:
    request = ground_request()
    request["detection"]["expected_surface_role"] = surface_role

    normalized = validate_ground_request(request)

    assert normalized["detection"]["expected_surface_role"] == surface_role


@pytest.mark.parametrize(
    "surface_role",
    ["floor", "NAVIGATION_OBSTACLE", "", None, 1, True],
)
def test_ground_request_rejects_unknown_or_non_string_expected_surface_role(
    surface_role: object,
) -> None:
    request = ground_request()
    request["detection"]["expected_surface_role"] = surface_role

    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_request(request)

    assert error.value.code == "PORTABLE_SURFACE_ROLE_INVALID"


def test_ground_request_accepts_only_bounded_arrival_continuity_surface() -> None:
    points = [[2.0, -0.2, 0.4], [2.0, 0.0, 0.4], [2.0, 0.2, 0.4]]
    request = ground_request(
        arrival_continuity_surface={
            "target_id": TARGET_ID,
            "points_m": points,
            "maximum_distance_m": 0.45,
        }
    )
    normalized = validate_ground_request(request)
    assert normalized["arrival_continuity_surface"] == request["arrival_continuity_surface"]

    identified_request = ground_request(
        arrival_continuity_surface={
            **request["arrival_continuity_surface"],
            "surface_id": SURFACE_ID,
            "surface_role": "navigation_obstacle",
        }
    )
    normalized_identified = validate_ground_request(identified_request)["arrival_continuity_surface"]
    assert normalized_identified["surface_id"] == SURFACE_ID
    assert normalized_identified["surface_role"] == "navigation_obstacle"

    invalid_surfaces = (
        "not-an-object",
        {"target_id": "Sofa", "points_m": points, "maximum_distance_m": 0.20},
        {"target_id": TARGET_ID, "points_m": [], "maximum_distance_m": 0.20},
        {
            "target_id": TARGET_ID,
            "points_m": [[2.0, 0.0, 0.4]] * 257,
            "maximum_distance_m": 0.20,
        },
        {"target_id": TARGET_ID, "points_m": [[2.0, 0.0]], "maximum_distance_m": 0.20},
        {"target_id": TARGET_ID, "points_m": points, "maximum_distance_m": 0.450001},
        {"target_id": TARGET_ID, "points_m": points, "maximum_distance_m": True},
        {
            "target_id": TARGET_ID,
            "points_m": points,
            "maximum_distance_m": 0.20,
            "engine_object_name": "Sofa",
        },
        {
            "target_id": TARGET_ID,
            "points_m": points,
            "maximum_distance_m": 0.20,
            "surface_id": "SofaBody:42",
        },
        {
            "target_id": TARGET_ID,
            "points_m": points,
            "maximum_distance_m": 0.20,
            "surface_role": "navigation_obstacle",
        },
        {
            "target_id": TARGET_ID,
            "points_m": points,
            "maximum_distance_m": 0.20,
            "surface_id": SURFACE_ID,
            "surface_role": "floor",
        },
    )
    for invalid in invalid_surfaces:
        malformed = ground_request(arrival_continuity_surface=invalid)
        with pytest.raises(PortableNavigationContractError):
            validate_ground_request(malformed)


def test_ground_result_accepts_only_bounded_certified_surface_points() -> None:
    points = [[2.0, -1.0, 0.4], [2.0, 0.0, 0.4], [2.0, 1.0, 0.4]]
    normalized = validate_ground_result(
        ground_result(surface_points_m=points), request=ground_request()
    )
    assert normalized["surface_points_m"] == points

    for invalid in ([], [[2.0, 0.0, 0.4]] * 257, [[2.0, 0.0]], [[2.0, float("nan"), 0.4]]):
        with pytest.raises(PortableNavigationContractError):
            validate_ground_result(
                ground_result(surface_points_m=invalid), request=ground_request()
            )

    unresolved = ground_result(
        status="unresolved",
        target_id=None,
        coordinate_frame_id=None,
        position_m=None,
        uncertainty_radius_m=None,
        reason="ambiguous",
        surface_points_m=points,
    )
    with pytest.raises(PortableNavigationContractError) as caught:
        validate_ground_result(unresolved, request=ground_request())
    assert caught.value.code == "PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN"


def test_ground_result_surface_role_requires_identity_and_is_forbidden_when_unresolved() -> None:
    normalized = validate_ground_result(
        ground_result(
            surface_id=SURFACE_ID,
            surface_role="navigation_obstacle",
        ),
        request=ground_request(),
    )
    assert normalized["surface_role"] == "navigation_obstacle"

    with pytest.raises(PortableNavigationContractError) as caught:
        validate_ground_result(
            ground_result(surface_role="navigation_obstacle"),
            request=ground_request(),
        )
    assert caught.value.code == "PORTABLE_SURFACE_ROLE_REQUIRES_SURFACE_ID"

    with pytest.raises(PortableNavigationContractError) as caught:
        validate_ground_result(
            ground_result(surface_id=SURFACE_ID, surface_role="floor"),
            request=ground_request(),
        )
    assert caught.value.code == "PORTABLE_SURFACE_ROLE_INVALID"

    unresolved = ground_result(
        status="unresolved",
        target_id=None,
        coordinate_frame_id=None,
        position_m=None,
        uncertainty_radius_m=None,
        reason="ambiguous",
        surface_role=None,
    )
    with pytest.raises(PortableNavigationContractError) as caught:
        validate_ground_result(unresolved, request=ground_request())
    assert caught.value.code == "PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN"


def test_ground_result_surface_role_must_match_detection_expectation() -> None:
    request = ground_request()
    request["detection"]["expected_surface_role"] = "navigation_obstacle"
    normalized = validate_ground_result(
        ground_result(
            surface_id=SURFACE_ID,
            surface_role="navigation_obstacle",
        ),
        request=request,
    )
    assert normalized["surface_role"] == "navigation_obstacle"

    for malicious in (
        ground_result(surface_id=SURFACE_ID),
        ground_result(
            surface_id=SURFACE_ID,
            surface_role="physical_surface",
        ),
    ):
        with pytest.raises(PortableNavigationContractError) as caught:
            validate_ground_result(malicious, request=request)
        assert caught.value.code == "PORTABLE_SURFACE_ROLE_MISMATCH"


def test_ground_result_surface_identity_must_match_continuity_request() -> None:
    points = [[2.0, -0.2, 0.4], [2.0, 0.0, 0.4], [2.0, 0.2, 0.4]]
    request = ground_request(arrival_continuity_surface={
        "target_id": TARGET_ID,
        "points_m": points,
        "maximum_distance_m": 0.20,
        "surface_id": SURFACE_ID,
        "surface_role": "navigation_obstacle",
    })
    normalized = validate_ground_result(
        ground_result(
            surface_points_m=points,
            surface_id=SURFACE_ID,
            surface_role="navigation_obstacle",
        ),
        request=request,
    )
    assert normalized["surface_id"] == SURFACE_ID
    assert normalized["surface_role"] == "navigation_obstacle"

    for malicious in (
        ground_result(surface_points_m=points),
        ground_result(surface_points_m=points, surface_id="surface:" + "b" * 64),
    ):
        with pytest.raises(PortableNavigationContractError) as caught:
            validate_ground_result(malicious, request=request)
        assert caught.value.code == "PORTABLE_SURFACE_ID_MISMATCH"

    for malicious_role in (
        ground_result(surface_points_m=points, surface_id=SURFACE_ID),
        ground_result(
            surface_points_m=points,
            surface_id=SURFACE_ID,
            surface_role="physical_surface",
        ),
    ):
        with pytest.raises(PortableNavigationContractError) as caught:
            validate_ground_result(malicious_role, request=request)
        assert caught.value.code == "PORTABLE_SURFACE_ROLE_MISMATCH"

    malformed = ground_result(surface_points_m=points, surface_id="surface:" + "A" * 64)
    with pytest.raises(PortableNavigationContractError) as caught:
        validate_ground_result(malformed, request=ground_request())
    assert caught.value.code == "PORTABLE_SURFACE_ID_NOT_OPAQUE"


def test_protocol_exposes_exact_six_async_environment_operations():
    class Complete:
        async def capture(self, request): pass
        async def build_map(self, request): pass
        async def ground(self, request): pass
        async def navigate(self, request): pass
        async def stop(self, request): pass
        async def feedback(self, request): pass

    class MissingStop:
        async def capture(self, request): pass
        async def build_map(self, request): pass
        async def ground(self, request): pass
        async def navigate(self, request): pass
        async def feedback(self, request): pass

    assert isinstance(Complete(), PortableNavigationAdapter)
    assert not isinstance(MissingStop(), PortableNavigationAdapter)


def test_capture_hash_freshness_and_required_depth_fail_closed():
    stale = capture_result(captured_unix=NOW - 0.6)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(stale, request=capture_request())
    assert error.value.code == "PORTABLE_CAPTURE_STALE"

    no_depth = capture_result(depth=None)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(no_depth, request=capture_request())
    assert error.value.code == "PORTABLE_DEPTH_REQUIRED"

    bad_hash = capture_result()
    bad_hash["rgb"]["sha256"] = "0" * 64
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(bad_hash)
    assert error.value.code == "PORTABLE_DATA_HASH_MISMATCH"


def test_dense_depth_can_be_lower_resolution_when_normalized_to_rgb():
    lower_resolution = capture_result()
    raw, digest = _encoded(b"\x00" * 4)
    lower_resolution["depth"].update(width_px=1, height_px=1, data_base64=raw, sha256=digest)
    normalized = validate_capture_result(lower_resolution, request=capture_request())
    assert normalized["rgb"]["width_px"] == 2
    assert normalized["depth"]["width_px"] == 1
    assert normalized["depth"]["alignment"] == "registered_normalized_to_rgb"


def test_dense_depth_must_declare_alignment_and_have_exact_native_size():
    bad_alignment = capture_result()
    bad_alignment["depth"]["alignment"] = "godot_viewport_pixels"
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(bad_alignment)
    assert error.value.code == "PORTABLE_DEPTH_ALIGNMENT_UNSUPPORTED"

    wrong_size = capture_result()
    raw, digest = _encoded(b"\x00" * 12)
    wrong_size["depth"].update(data_base64=raw, sha256=digest)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(wrong_size)
    assert error.value.code == "PORTABLE_DEPTH_SIZE_MISMATCH"


def test_sparse_raycast_depth_is_supported_and_tamper_evident():
    sparse = [
        {
            "uv_norm": [0.25, 0.5],
            "distance_m": 1.2,
            "confidence": 1.0,
            "surface_id": SURFACE_ID,
            "surface_role": "navigation_obstacle",
        },
        {"uv_norm": [0.75, 0.5], "distance_m": 2.4, "confidence": 0.8},
    ]
    digest = hashlib.sha256(
        json.dumps(sparse, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    packet = capture_result(depth={
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
        "samples": sparse,
        "samples_sha256": digest,
    })
    validated = validate_capture_result(packet, request=capture_request())
    assert validated["depth"]["samples"][0]["surface_id"] == SURFACE_ID
    assert validated["depth"]["samples"][0]["surface_role"] == "navigation_obstacle"
    assert validated["depth"]["samples"][1]["distance_m"] == 2.4

    tampered = copy.deepcopy(packet)
    tampered["depth"]["samples"][1]["distance_m"] = 9.9
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(tampered)
    assert error.value.code == "PORTABLE_DATA_HASH_MISMATCH"

    tampered_role = copy.deepcopy(packet)
    tampered_role["depth"]["samples"][0]["surface_role"] = "physical_surface"
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(tampered_role)
    assert error.value.code == "PORTABLE_DATA_HASH_MISMATCH"

    bad_identity = copy.deepcopy(packet)
    bad_identity["depth"]["samples"][0]["surface_id"] = "surface:" + "A" * 64
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(bad_identity)
    assert error.value.code == "PORTABLE_SURFACE_ID_NOT_OPAQUE"

    bad_role = copy.deepcopy(packet)
    bad_role["depth"]["samples"][0]["surface_role"] = "floor"
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(bad_role)
    assert error.value.code == "PORTABLE_SURFACE_ROLE_INVALID"

    missing_identity = copy.deepcopy(packet)
    missing_identity["depth"]["samples"][0].pop("surface_id")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(missing_identity)
    assert error.value.code == "PORTABLE_SURFACE_ROLE_REQUIRES_SURFACE_ID"

    for bad_revision in (None, "legacy_body_identity_v1", "", 42):
        bad_semantics = copy.deepcopy(packet)
        if bad_revision is None:
            bad_semantics["depth"].pop("surface_id_semantics_revision")
        else:
            bad_semantics["depth"]["surface_id_semantics_revision"] = bad_revision
        with pytest.raises(PortableNavigationContractError) as error:
            validate_capture_result(bad_semantics)
        assert error.value.code == "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH"


def test_sparse_surface_identity_cannot_report_conflicting_roles() -> None:
    sparse = [
        {
            "uv_norm": [0.25, 0.5],
            "distance_m": 1.2,
            "confidence": 1.0,
            "surface_id": SURFACE_ID,
            "surface_role": "navigation_obstacle",
        },
        {
            "uv_norm": [0.75, 0.5],
            "distance_m": 1.3,
            "confidence": 1.0,
            "surface_id": SURFACE_ID,
            "surface_role": "physical_surface",
        },
    ]
    digest = hashlib.sha256(
        json.dumps(
            sparse,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    packet = capture_result(depth={
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
        "samples": sparse,
        "samples_sha256": digest,
    })

    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(packet, request=capture_request())

    assert error.value.code == "PORTABLE_SURFACE_ID_ROLE_CONFLICT"


def test_sparse_semantics_attestation_is_optional_only_without_surface_ids() -> None:
    sparse = [{
        "uv_norm": [0.5, 0.5],
        "distance_m": 1.5,
        "confidence": 1.0,
    }]
    digest = hashlib.sha256(
        json.dumps(
            sparse,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    depth = {
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "samples": sparse,
        "samples_sha256": digest,
    }

    unattested = validate_capture_result(capture_result(depth=depth))
    assert "surface_id_semantics_revision" not in unattested["depth"]

    attested_depth = copy.deepcopy(depth)
    attested_depth["surface_id_semantics_revision"] = SURFACE_ID_SEMANTICS_REVISION
    attested = validate_capture_result(capture_result(depth=attested_depth))
    assert (
        attested["depth"]["surface_id_semantics_revision"]
        == SURFACE_ID_SEMANTICS_REVISION
    )


def test_map_rejects_wrong_lineage_resolution_and_occupancy_shape():
    wrong_frame = map_result(source_frame_id="frame-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_map_result(wrong_frame, request=map_request())
    assert error.value.code == "PORTABLE_LINEAGE_MISMATCH"

    wrong_resolution = map_result()
    wrong_resolution["grid"]["resolution_m"] = 0.25
    with pytest.raises(PortableNavigationContractError) as error:
        validate_map_result(wrong_resolution, request=map_request())
    assert error.value.code == "PORTABLE_MAP_RESOLUTION_MISMATCH"

    wrong_grid = map_result()
    raw, digest = _encoded(bytes([0, 0, 0]))
    wrong_grid["grid"].update(occupancy_base64=raw, occupancy_sha256=digest)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_map_result(wrong_grid)
    assert error.value.code == "PORTABLE_OCCUPANCY_SIZE_MISMATCH"


def test_coordinate_convention_and_quaternion_are_not_engine_defined():
    engine_coordinates = capture_result()
    engine_coordinates["camera"]["coordinate_convention"] = "godot_y_up_minus_z_forward"
    with pytest.raises(PortableNavigationContractError) as error:
        validate_capture_result(engine_coordinates)
    assert error.value.code == "PORTABLE_COORDINATE_CONVENTION_MISMATCH"

    bad_rotation = map_result()
    bad_rotation["agent"]["orientation_xyzw"] = [0, 0, 0, 2]
    with pytest.raises(PortableNavigationContractError) as error:
        validate_map_result(bad_rotation)
    assert error.value.code == "PORTABLE_QUATERNION_NOT_NORMALIZED"


def test_grounding_requires_opaque_target_and_exact_detection_lineage():
    native_name_leak = ground_result(target_id="Sofa")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_result(native_name_leak, request=ground_request())
    assert error.value.code == "PORTABLE_TARGET_ID_NOT_OPAQUE"

    mismatched = ground_result(detection_id="detection-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_result(mismatched, request=ground_request())
    assert error.value.code == "PORTABLE_LINEAGE_MISMATCH"


def test_unresolved_grounding_cannot_smuggle_a_position_or_target():
    unresolved = ground_result(
        status="unresolved",
        target_id=None,
        coordinate_frame_id=None,
        position_m=None,
        uncertainty_radius_m=None,
        reason="ambiguous",
    )
    assert validate_ground_result(unresolved, request=ground_request())["reason"] == "ambiguous"
    unresolved["position_m"] = [1, 0, 0]
    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_result(unresolved, request=ground_request())
    assert error.value.code == "PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN"


def test_map_ground_and_feedback_results_cannot_be_replayed_from_old_time():
    stale_map = map_result(created_unix=NOW - 2)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_map_result(stale_map, request=map_request())
    assert error.value.code == "PORTABLE_MAP_STALE"

    stale_ground = ground_result(grounded_unix=NOW - 2)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_result(stale_ground, request=ground_request())
    assert error.value.code == "PORTABLE_GROUNDING_STALE"

    stale_feedback = feedback_result(observed_unix=NOW - 2)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_feedback_result(stale_feedback, request=feedback_request())
    assert error.value.code == "PORTABLE_FEEDBACK_STALE"


def test_navigation_rejects_nonopaque_targets_and_cross_map_acceptance():
    native_target = navigate_request(target_id="PlantFrontRight")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_navigate_request(native_target)
    assert error.value.code == "PORTABLE_TARGET_ID_NOT_OPAQUE"

    wrong_map = navigate_result(map_id="map-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_navigate_result(wrong_map, request=navigate_request())
    assert error.value.code == "PORTABLE_LINEAGE_MISMATCH"

    wrong_command = navigate_result(command_id="command-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_navigate_result(wrong_command, request=navigate_request())
    assert error.value.code == "PORTABLE_COMMAND_OWNERSHIP_MISMATCH"


def test_navigation_requires_caller_owned_command_before_dispatch():
    missing = navigate_request()
    missing.pop("command_id")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_navigate_request(missing)
    assert error.value.code == "PORTABLE_FIELDS_MISSING"

    normalized = validate_navigate_request(navigate_request(command_id="caller-command-9"))
    assert normalized["command_id"] == "caller-command-9"


def test_feedback_detects_stale_sequence_collision_and_wrong_owner():
    stale = feedback_result(sequence=2)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_feedback_result(stale, request=feedback_request())
    assert error.value.code == "PORTABLE_FEEDBACK_SEQUENCE_STALE"

    collision_while_moving = feedback_result(collision_detected=True)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_feedback_result(collision_while_moving)
    assert error.value.code == "PORTABLE_COLLISION_STATUS_INVALID"

    wrong_owner = feedback_result(command_id="command-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_feedback_result(wrong_owner, request=feedback_request())
    assert error.value.code == "PORTABLE_COMMAND_OWNERSHIP_MISMATCH"


def test_arrival_requires_positive_target_confirmation():
    false_arrival = feedback_result(status="arrived", target_reached=False)
    with pytest.raises(PortableNavigationContractError) as error:
        validate_feedback_result(false_arrival)
    assert error.value.code == "PORTABLE_ARRIVAL_UNCONFIRMED"

    arrived = feedback_result(
        status="arrived",
        target_reached=True,
        velocity_mps=[0, 0, 0],
        remaining_distance_m=0.1,
    )
    assert validate_feedback_result(arrived)["target_reached"] is True


def test_stop_confirmation_is_scoped_to_the_owned_command():
    wrong_owner = stop_result(owned_command_id="command-other")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_stop_result(wrong_owner, request=stop_request())
    assert error.value.code == "PORTABLE_COMMAND_OWNERSHIP_MISMATCH"

    unconfirmed = stop_result(status="rejected", confirmed=False, stopped_unix=None, reason="owner_not_active")
    assert validate_stop_result(unconfirmed, request=stop_request())["confirmed"] is False

    dishonest = stop_result(status="confirmed", confirmed=False, stopped_unix=None, reason="not_really")
    with pytest.raises(PortableNavigationContractError) as error:
        validate_stop_result(dishonest)
    assert error.value.code == "PORTABLE_STOP_CONFIRMATION_INVALID"


def test_unknown_engine_specific_fields_are_rejected_at_the_core_boundary():
    leaked = ground_result()
    leaked["godot_node_name"] = "Sofa"
    with pytest.raises(PortableNavigationContractError) as error:
        validate_ground_result(leaked)
    assert error.value.code == "PORTABLE_FIELDS_UNKNOWN"
