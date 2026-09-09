from __future__ import annotations

import base64
import hashlib
import json
import math
import struct

import pytest

from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    GROUND_REQUEST_SCHEMA,
    MAP_REQUEST_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
)
from lumina_next.portable_navigation_core_v1 import (
    OCCUPIED_VALUE,
    UNKNOWN_VALUE,
    PortableNavigationCoreError,
    build_occupancy_map,
    ground_bbox_to_depth,
    inflate_occupancy,
    plan_path,
    project_depth_samples,
    rasterize_occupancy,
    simplify_path,
    validate_path_collision,
)


NOW = 2_000_000_000.0
SURFACE_A = "surface:" + "a" * 64
SURFACE_B = "surface:" + "b" * 64
FIXTURE_PERSON_SURFACE = "surface:" + "f" * 64
FIXTURE_EDGE_SURFACE = "surface:" + "9" * 64


def _encoded(raw: bytes) -> tuple[str, str]:
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _sparse_capture(samples: list[dict], *, orientation=None) -> dict:
    rgb, rgb_hash = _encoded(b"\x89PNG\r\n\x1a\ncore-fixture")
    normalized_samples = []
    for sample in samples:
        normalized_sample = {
            "uv_norm": [float(sample["uv_norm"][0]), float(sample["uv_norm"][1])],
            "distance_m": float(sample["distance_m"]),
            "confidence": float(sample.get("confidence", 1.0)),
        }
        if "surface_id" in sample:
            normalized_sample["surface_id"] = sample["surface_id"]
        if "surface_role" in sample:
            normalized_sample["surface_role"] = sample["surface_role"]
        normalized_samples.append(normalized_sample)
    sample_hash = hashlib.sha256(
        json.dumps(
            normalized_samples,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": "capture:core:1",
        "environment_id": "environment:core",
        "session_id": "session:core",
        "frame_id": "frame:core:1",
        "captured_unix": NOW,
        "rgb": {
            "encoding": "png",
            "width_px": 100,
            "height_px": 100,
            "data_base64": rgb,
            "sha256": rgb_hash,
        },
        "depth": {
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": "registered_normalized_to_rgb",
            "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
            "samples": normalized_samples,
            "samples_sha256": sample_hash,
        },
        "camera": {
            "coordinate_frame_id": "world:core",
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": [10.0, 20.0, 1.0],
            "orientation_xyzw": orientation or [0.0, 0.0, 0.0, 1.0],
            "intrinsics": {"fx_px": 100.0, "fy_px": 100.0, "cx_px": 50.0, "cy_px": 50.0},
        },
    }


def _dense_capture(depths: list[float], *, width: int, height: int, model="optical_axis_z_m") -> dict:
    capture = _sparse_capture([{"uv_norm": [0.5, 0.5], "distance_m": 1.0}])
    raw = b"".join(struct.pack("<f", value) for value in depths)
    encoded, digest = _encoded(raw)
    capture["rgb"]["width_px"] = width
    capture["rgb"]["height_px"] = height
    capture["camera"]["position_m"] = [0.0, 0.0, 0.0]
    capture["camera"]["intrinsics"] = {
        "fx_px": 1.0,
        "fy_px": 1.0,
        "cx_px": width / 2.0,
        "cy_px": height / 2.0,
    }
    capture["depth"] = {
        "representation": "dense",
        "encoding": "f32_le_m",
        "measurement_model": model,
        "width_px": width,
        "height_px": height,
        "alignment": "registered_normalized_to_rgb",
        "invalid_value": "nan",
        "data_base64": encoded,
        "sha256": digest,
    }
    return capture


def _ground_request(bbox=None) -> dict:
    return {
        "schema_version": GROUND_REQUEST_SCHEMA,
        "request_id": "ground:core:1",
        "environment_id": "environment:core",
        "session_id": "session:core",
        "requested_unix": NOW,
        "source_frame_id": "frame:core:1",
        "map_id": "map:core:1",
        "detection": {
            "detection_id": "detection:vision:1",
            "label": "unseen furniture category",
            "confidence": 0.9,
            "bbox_norm": bbox or [0.2, 0.2, 0.8, 0.8],
        },
    }


def _map_request(*, resolution=1.0, footprint=0.1) -> dict:
    return {
        "schema_version": MAP_REQUEST_SCHEMA,
        "request_id": "map-request:core:1",
        "environment_id": "environment:core",
        "session_id": "session:core",
        "requested_unix": NOW,
        "source_frame_id": "frame:core:1",
        "resolution_m": resolution,
        "footprint_radius_m": footprint,
    }


def _wall_map(*, gap=False, unknown_cells=None) -> dict:
    obstacles = []
    if gap:
        obstacles.append({"kind": "rect", "center": [4.5, 4.5], "half": [0.49, 2.0]})
    else:
        obstacles.append({"kind": "rect", "center": [4.5, 4.5], "half": [0.49, 3.49]})
    result = build_occupancy_map(
        _map_request(),
        bounds_xy=[0.0, 9.0, 0.0, 9.0],
        obstacles=obstacles,
        agent_position_m=[1.5, 4.5, 0.0],
    )
    if unknown_cells:
        occupancy = bytearray(base64.b64decode(result["grid"]["occupancy_base64"]))
        for x, y in unknown_cells:
            occupancy[y * result["grid"]["width_cells"] + x] = UNKNOWN_VALUE
        result["grid"]["occupancy_base64"] = base64.b64encode(occupancy).decode("ascii")
        result["grid"]["occupancy_sha256"] = hashlib.sha256(occupancy).hexdigest()
    return result


def test_dense_depth_uses_pixel_centres_intrinsics_and_optical_z() -> None:
    points = project_depth_samples(_dense_capture([2.0, 2.0], width=2, height=1))
    assert len(points) == 2
    assert points[0]["uv_norm"] == [0.25, 0.5]
    assert points[0]["position_m"] == pytest.approx([-1.0, 0.0, 2.0])
    assert points[1]["position_m"] == pytest.approx([1.0, 0.0, 2.0])


def test_sparse_ray_uses_intrinsics_range_quaternion_and_camera_translation() -> None:
    half = math.sqrt(0.5)
    capture = _sparse_capture(
        [{"uv_norm": [0.75, 0.5], "distance_m": 2.0, "confidence": 0.8}],
        orientation=[0.0, 0.0, half, half],
    )
    point = project_depth_samples(capture)[0]
    optical = [0.25, 0.0, 1.0]
    scale = 2.0 / math.sqrt(1.0625)
    # A +90 degree rotation around canonical z maps optical +x to +y.
    assert point["position_m"] == pytest.approx(
        [10.0, 20.0 + optical[0] * scale, 1.0 + optical[2] * scale]
    )
    assert point["range_m"] == pytest.approx(2.0)


def test_sparse_projection_preserves_optional_surface_metadata() -> None:
    capture = _sparse_capture([
        {
            "uv_norm": [0.50, 0.50],
            "distance_m": 2.0,
            "surface_id": SURFACE_A,
            "surface_role": "navigation_obstacle",
        },
        {
            "uv_norm": [0.55, 0.50],
            "distance_m": 2.0,
        },
    ])

    projected = project_depth_samples(capture)
    assert projected[0]["surface_id"] == SURFACE_A
    assert projected[0]["surface_role"] == "navigation_obstacle"
    assert "surface_id" not in projected[1]
    assert "surface_role" not in projected[1]


def test_bbox_grounding_returns_only_opaque_identity_and_canonical_position() -> None:
    capture = _sparse_capture(
        [
            {"uv_norm": [0.45, 0.50], "distance_m": 2.0},
            {"uv_norm": [0.50, 0.50], "distance_m": 2.05},
            {"uv_norm": [0.55, 0.50], "distance_m": 2.1},
            {"uv_norm": [0.78, 0.78], "distance_m": 6.0},
        ]
    )
    result = ground_bbox_to_depth(_ground_request(), capture)
    assert result["status"] == "grounded"
    assert result["target_id"].startswith("target:")
    assert len(result["target_id"].split(":", 1)[1]) == 64
    assert result["coordinate_frame_id"] == "world:core"
    assert result["position_m"] is not None
    assert len(result["surface_points_m"]) == 3
    assert "unseen" not in result["target_id"]


def test_target_identity_is_deterministic_within_a_map_and_separated_between_maps() -> None:
    capture = _sparse_capture(
        [
            {"uv_norm": [0.45, 0.50], "distance_m": 2.0},
            {"uv_norm": [0.50, 0.50], "distance_m": 2.05},
            {"uv_norm": [0.55, 0.50], "distance_m": 2.1},
        ]
    )
    first_request = _ground_request()
    repeated = ground_bbox_to_depth(first_request, capture)
    assert ground_bbox_to_depth(first_request, capture)["target_id"] == repeated["target_id"]

    second_request = {**first_request, "request_id": "ground:core:other-map", "map_id": "map:core:2"}
    other_map = ground_bbox_to_depth(second_request, capture)
    assert other_map["position_m"] == pytest.approx(repeated["position_m"])
    assert other_map["target_id"] != repeated["target_id"]


def test_bbox_grounding_fails_closed_for_no_depth_and_competing_surfaces() -> None:
    outside = _sparse_capture([{"uv_norm": [0.95, 0.95], "distance_m": 2.0}])
    no_depth = ground_bbox_to_depth(_ground_request(), outside)
    assert no_depth["status"] == "unresolved"
    assert no_depth["reason"] == "no_depth"
    assert no_depth["target_id"] is None

    ambiguous_capture = _sparse_capture(
        [
            {"uv_norm": [0.45, 0.50], "distance_m": 2.0},
            {"uv_norm": [0.55, 0.50], "distance_m": 2.05},
            {"uv_norm": [0.45, 0.55], "distance_m": 5.0},
            {"uv_norm": [0.55, 0.55], "distance_m": 5.05},
        ]
    )
    ambiguous = ground_bbox_to_depth(_ground_request(), ambiguous_capture)
    assert ambiguous["status"] == "unresolved"
    assert ambiguous["reason"] == "ambiguous"
    assert ambiguous["position_m"] is None


@pytest.mark.parametrize("sample_count", [1, 2])
def test_sparse_grounding_rejects_high_confidence_but_insufficient_support(
    sample_count: int,
) -> None:
    samples = [
        {"uv_norm": [0.46 + index * 0.08, 0.50], "distance_m": 2.0, "confidence": 1.0}
        for index in range(sample_count)
    ]
    result = ground_bbox_to_depth(_ground_request(), _sparse_capture(samples))
    assert result["status"] == "unresolved"
    assert result["reason"] == "insufficient_depth_support"
    assert result["target_id"] is None


def test_sparse_grounding_counts_only_supported_rays_and_exact_bbox_fallback() -> None:
    low_support = _sparse_capture([
        {"uv_norm": [0.45, 0.50], "distance_m": 2.0, "confidence": 1.0},
        {"uv_norm": [0.50, 0.50], "distance_m": 2.0, "confidence": 0.20},
        {"uv_norm": [0.55, 0.50], "distance_m": 2.0, "confidence": 0.20},
    ])
    unresolved = ground_bbox_to_depth(_ground_request(), low_support)
    assert unresolved["status"] == "unresolved"
    assert unresolved["reason"] == "insufficient_depth_support"

    # Only the centre ray survives the inset.  Two more independent rays are
    # still inside the semantic bbox, so the exact-bbox fallback may ground it.
    fallback = _sparse_capture([
        {"uv_norm": [0.21, 0.50], "distance_m": 2.00, "confidence": 1.0},
        {"uv_norm": [0.50, 0.50], "distance_m": 2.02, "confidence": 1.0},
        {"uv_norm": [0.79, 0.50], "distance_m": 2.01, "confidence": 1.0},
    ])
    grounded = ground_bbox_to_depth(_ground_request(), fallback)
    assert grounded["status"] == "grounded"
    assert grounded["reason"] is None


@pytest.mark.parametrize(
    ("depths", "expected_status", "expected_reason"),
    [
        ([math.nan, math.nan, 2.0, math.nan, math.nan], "unresolved", "insufficient_depth_support"),
        ([math.nan, 2.0, 2.0, math.nan, math.nan], "unresolved", "insufficient_depth_support"),
        ([math.nan, 2.0, 2.0, 2.0, math.nan], "grounded", None),
    ],
)
def test_dense_grounding_requires_three_independent_valid_pixels(
    depths: list[float], expected_status: str, expected_reason: str | None
) -> None:
    result = ground_bbox_to_depth(
        _ground_request(), _dense_capture(depths, width=5, height=1)
    )
    assert result["status"] == expected_status
    assert result["reason"] == expected_reason


def test_depth_ambiguity_takes_precedence_when_total_support_is_sufficient() -> None:
    # Four independent rays are enough overall, but neither of the similarly
    # supported surfaces may be selected as the semantic target.
    capture = _sparse_capture([
        {"uv_norm": [0.45, 0.48], "distance_m": 2.00},
        {"uv_norm": [0.55, 0.48], "distance_m": 2.02},
        {"uv_norm": [0.45, 0.52], "distance_m": 4.00},
        {"uv_norm": [0.55, 0.52], "distance_m": 4.02},
    ])
    result = ground_bbox_to_depth(_ground_request(), capture)
    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def _range_for_forward_depth(u: float, v: float, depth: float) -> float:
    ray_x = u - 0.5
    ray_y = v - 0.5
    return depth * math.sqrt(1.0 + ray_x * ray_x + ray_y * ray_y)


def test_arrival_continuity_selects_three_ray_match_from_ambiguous_bbox() -> None:
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, depth),
        }
        for depth, v in ((2.0, 0.48), (4.0, 0.52))
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    assert ground_bbox_to_depth(_ground_request(), capture)["reason"] == "ambiguous"

    references = [
        point["position_m"]
        for point in project_depth_samples(capture)
        if point["forward_depth_m"] > 3.0
    ]
    request = _ground_request()
    original_target_id = "target:" + "a" * 16
    request["arrival_continuity_surface"] = {
        "target_id": original_target_id,
        "points_m": references,
        "maximum_distance_m": 0.20,
    }
    result = ground_bbox_to_depth(request, capture)

    assert result["status"] == "grounded"
    assert result["target_id"] != original_target_id
    assert result["position_m"][2] == pytest.approx(5.0, abs=0.02)
    assert len(result["surface_points_m"]) == len(references)
    for actual, expected in zip(result["surface_points_m"], references):
        assert actual == pytest.approx(expected)
    assert result["confidence"] >= 0.50
    assert result["uncertainty_radius_m"] <= 0.20


def test_arrival_continuity_considers_only_matching_surface_identity() -> None:
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, depth),
            "surface_id": surface_id,
        }
        for depth, v, surface_id in (
            (2.0, 0.48, SURFACE_A),
            (4.0, 0.52, SURFACE_B),
        )
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    references = [
        point["position_m"]
        for point in project_depth_samples(capture)
        if point.get("surface_id") == SURFACE_B
    ]
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "f" * 16,
        "points_m": references,
        "maximum_distance_m": 0.20,
        "surface_id": SURFACE_B,
    }

    result = ground_bbox_to_depth(request, capture)

    assert result["status"] == "grounded"
    assert result["surface_id"] == SURFACE_B
    assert result["position_m"][2] == pytest.approx(5.0, abs=0.02)


def test_arrival_continuity_rejects_metric_match_with_wrong_surface_identity() -> None:
    samples = [
        {
            "uv_norm": [u, 0.50],
            "distance_m": _range_for_forward_depth(u, 0.50, 4.0),
            "surface_id": SURFACE_B,
        }
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "e" * 16,
        "points_m": [point["position_m"] for point in project_depth_samples(capture)],
        "maximum_distance_m": 0.20,
        "surface_id": SURFACE_A,
    }

    result = ground_bbox_to_depth(request, capture)

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_arrival_continuity_requires_three_independently_matching_rays() -> None:
    samples = [
        {
            "uv_norm": [u, 0.50],
            "distance_m": _range_for_forward_depth(u, 0.50, 4.0),
        }
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    references = [point["position_m"] for point in project_depth_samples(capture)[:2]]
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "b" * 16,
        "points_m": references,
        "maximum_distance_m": 0.01,
    }

    result = ground_bbox_to_depth(request, capture)
    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_arrival_continuity_rejects_two_surface_matches_without_unique_margin() -> None:
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, depth),
        }
        for depth, v in ((2.0, 0.48), (4.0, 0.52))
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    references = [point["position_m"] for point in project_depth_samples(capture)]
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "c" * 16,
        "points_m": references,
        "maximum_distance_m": 0.20,
    }

    result = ground_bbox_to_depth(request, capture)
    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_arrival_continuity_selects_only_when_competitor_clears_unique_margin() -> None:
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, depth),
        }
        for depth, v in ((2.0, 0.48), (4.0, 0.52))
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    projected = project_depth_samples(capture)
    references = [
        list(point["position_m"])
        if point["forward_depth_m"] < 3.0
        else [point["position_m"][0], point["position_m"][1], point["position_m"][2] + 0.10]
        for point in projected
    ]
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "d" * 16,
        "points_m": references,
        "maximum_distance_m": 0.20,
    }

    result = ground_bbox_to_depth(request, capture)
    assert result["status"] == "grounded"
    assert result["position_m"][2] == pytest.approx(3.0, abs=0.02)
    assert all(point[2] < 4.0 for point in result["surface_points_m"])


def test_arrival_continuity_match_distance_does_not_exceed_contract_cap() -> None:
    samples = [
        {
            "uv_norm": [u, 0.50],
            "distance_m": _range_for_forward_depth(u, 0.50, 4.0),
        }
        for u in (0.45, 0.50, 0.55)
    ]
    capture = _sparse_capture(samples)
    references = [
        [point["position_m"][0], point["position_m"][1], point["position_m"][2] + 0.45001]
        for point in project_depth_samples(capture)
    ]
    request = _ground_request()
    request["arrival_continuity_surface"] = {
        "target_id": "target:" + "e" * 16,
        "points_m": references,
        "maximum_distance_m": 0.45,
    }

    result = ground_bbox_to_depth(request, capture)
    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_ground_confidence_uses_same_pairwise_runner_as_ambiguity_veto() -> None:
    winner = [
        {
            "uv_norm": [u, 0.50],
            "distance_m": _range_for_forward_depth(u, 0.50, 2.0),
        }
        for u in (0.45, 0.50, 0.55)
    ]
    weak_depths = [4.0 * 1.25**index for index in range(10)]
    weak = [
        {
            "uv_norm": [0.28 + index * 0.001, 0.28],
            "distance_m": _range_for_forward_depth(
                0.28 + index * 0.001,
                0.28,
                depth,
            ),
        }
        for index, depth in enumerate(weak_depths)
    ]

    result = ground_bbox_to_depth(_ground_request(), _sparse_capture(winner + weak))
    assert result["status"] == "grounded"
    assert result["confidence"] >= 0.75


def _wide_occluded_samples(*, include_left: int = 10, second_surface: bool = False) -> list[dict]:
    samples = [
        {"uv_norm": [u, v], "distance_m": _range_for_forward_depth(u, v, 2.0)}
        for v in (0.45, 0.50, 0.55)
        for u in (0.46, 0.50, 0.54)
    ]
    left = (0.02, 0.06, 0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38)[:include_left]
    right = (0.62, 0.66, 0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.94, 0.98)
    samples.extend(
        {"uv_norm": [u, 0.59], "distance_m": _range_for_forward_depth(u, 0.59, 3.2)}
        for u in left + right
    )
    if second_surface:
        samples.extend(
            {"uv_norm": [u, 0.41], "distance_m": _range_for_forward_depth(u, 0.41, 4.0)}
            for u in (
                0.02, 0.06, 0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38,
                0.62, 0.66, 0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.94, 0.98,
            )
        )
    return samples


def _wide_occluded_request(*, with_hint: bool = True) -> dict:
    request = _ground_request([0.0, 0.40, 1.0, 0.60])
    if with_hint:
        request["detection"]["occluder_bboxes_norm"] = [[0.42, 0.20, 0.58, 0.80]]
    return request


def test_wide_surface_rescue_uses_bilateral_depth_behind_detected_occluder() -> None:
    result = ground_bbox_to_depth(
        _wide_occluded_request(),
        _sparse_capture(_wide_occluded_samples()),
    )

    assert result["status"] == "grounded"
    assert result["position_m"][2] == pytest.approx(4.2, abs=0.05)
    assert result["confidence"] >= 0.5
    assert result["uncertainty_radius_m"] <= 0.35


def test_wide_surface_rescue_requires_three_rays_on_each_side() -> None:
    result = ground_bbox_to_depth(
        _wide_occluded_request(),
        _sparse_capture(_wide_occluded_samples(include_left=2)),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_wide_surface_rescue_rejects_comparable_bilateral_surfaces() -> None:
    result = ground_bbox_to_depth(
        _wide_occluded_request(),
        _sparse_capture(_wide_occluded_samples(second_surface=True)),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def _initial_sofa_multilayer_samples(
    *,
    near_surface_id: str | None,
    far_surface_id: str | None,
    far_count_per_side: int = 9,
) -> list[dict]:
    u_values = [(index + 0.5) / 24.0 for index in range(24)]
    central = [u for u in u_values if 0.422 <= u <= 0.577]
    outside = [u for u in u_values if u not in central]
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, 2.0),
            "surface_id": SURFACE_B,
        }
        for v in (0.594, 0.656)
        for u in central
    ]
    for u in outside:
        sample = {
            "uv_norm": [u, 0.656],
            "distance_m": _range_for_forward_depth(u, 0.656, 3.25),
        }
        if near_surface_id is not None:
            sample["surface_id"] = near_surface_id
        samples.append(sample)
    # The real capture exposed 18 far rays with a small, bilaterally symmetric
    # depth slope across the same full-width sofa.
    far_u_values = outside[:far_count_per_side] + outside[-far_count_per_side:]
    for u in far_u_values:
        depth = 3.76 + 0.25 * abs(u - 0.5) / max(abs(value - 0.5) for value in far_u_values)
        sample = {
            "uv_norm": [u, 0.594],
            "distance_m": _range_for_forward_depth(u, 0.594, depth),
        }
        if far_surface_id is not None:
            sample["surface_id"] = far_surface_id
        samples.append(sample)
    return samples


def _initial_sofa_multilayer_request() -> dict:
    request = _ground_request([0.0, 0.558, 0.999, 0.715])
    request["detection"]["occluder_bboxes_norm"] = [[0.422, 0.232, 0.577, 0.860]]
    return request


def _live_sofa_edge_contamination_samples(
    *,
    include_sofa: bool = True,
    split_far_ids: bool = True,
) -> list[dict]:
    """Reconstruct the 24x16 ray layout from the 2026-09-07 canary.

    The two sofa layers use one physical id.  At the middle range, one ray on
    each edge belongs to a different body.  A still farther equal-depth band
    is made from distinct left-only and right-only bodies.  Neither pair may
    contaminate or manufacture a bilateral physical surface.
    """

    surface_c = "surface:" + "c" * 64
    surface_d = "surface:" + "d" * 64
    surface_e = "surface:" + "e" * 64
    u_values = [(index + 0.5) / 24.0 for index in range(24)]
    central = [u for u in u_values if 0.422 <= u <= 0.577]
    outside = [u for u in u_values if u not in central]
    samples = [
        {
            "uv_norm": [u, v],
            "distance_m": _range_for_forward_depth(u, v, 2.20),
            "surface_id": SURFACE_B,
        }
        for v in (0.53125, 0.59375, 0.65625, 0.71875)
        for u in central
    ]
    if include_sofa:
        # Forty rays on the nearest exposed sofa face.
        samples.extend(
            {
                "uv_norm": [u, v],
                "distance_m": _range_for_forward_depth(u, v, 3.27),
                "surface_id": SURFACE_A,
            }
            for v in (0.65625, 0.71875)
            for u in outside
        )
        # Eighteen sofa rays plus two unrelated edge rays in the same depth
        # cluster.  Depth-first grouping used to erase the otherwise uniform A
        # identity here and reject the live frame as ambiguous.
        samples.extend(
            {
                "uv_norm": [u, 0.59375],
                "distance_m": _range_for_forward_depth(u, 0.59375, 3.57),
                "surface_id": (
                    surface_c if index == 0
                    else surface_d if index == len(outside) - 1
                    else SURFACE_A
                ),
            }
            for index, u in enumerate(outside)
        )
    # Same-range left and right background bodies.  Combined by depth they
    # appear full-width; separated by physical id neither is bilateral.
    samples.extend(
        {
            "uv_norm": [u, 0.53125],
            "distance_m": _range_for_forward_depth(u, 0.53125, 10.41),
            "surface_id": surface_d if index < 6 or not split_far_ids else surface_e,
        }
        for index, u in enumerate(outside[:6] + outside[-6:])
    )
    return samples


def _live_sofa_edge_contamination_request() -> dict:
    request = _ground_request([0.0, 0.522, 1.0, 0.720])
    request["detection"]["occluder_bboxes_norm"] = [[0.422, 0.232, 0.577, 0.860]]
    return request


def test_surface_id_first_partition_recovers_live_sofa_from_edge_contamination() -> None:
    result = ground_bbox_to_depth(
        _live_sofa_edge_contamination_request(),
        _sparse_capture(_live_sofa_edge_contamination_samples()),
    )

    assert result["status"] == "grounded"
    assert result["surface_id"] == SURFACE_A
    assert result["position_m"][2] == pytest.approx(4.27, abs=0.03)
    assert result["confidence"] >= 0.50


def test_different_left_right_ids_at_same_depth_cannot_fake_bilateral_surface() -> None:
    result = ground_bbox_to_depth(
        _live_sofa_edge_contamination_request(),
        _sparse_capture(_live_sofa_edge_contamination_samples(include_sofa=False)),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_distinct_full_width_physical_surface_remains_ambiguous_competitor() -> None:
    result = ground_bbox_to_depth(
        _live_sofa_edge_contamination_request(),
        _sparse_capture(_live_sofa_edge_contamination_samples(split_far_ids=False)),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_partial_surface_identity_coverage_fails_closed() -> None:
    samples = _live_sofa_edge_contamination_samples()
    samples[-1].pop("surface_id")
    result = ground_bbox_to_depth(
        _live_sofa_edge_contamination_request(),
        _sparse_capture(samples),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_wide_multilayer_same_physical_surface_uses_nearest_layer_only() -> None:
    result = ground_bbox_to_depth(
        _initial_sofa_multilayer_request(),
        _sparse_capture(_initial_sofa_multilayer_samples(
            near_surface_id=SURFACE_A,
            far_surface_id=SURFACE_A,
        )),
    )

    assert result["status"] == "grounded"
    assert result["surface_id"] == SURFACE_A
    assert result["position_m"][2] == pytest.approx(4.25, abs=0.03)
    assert len(result["surface_points_m"]) == 20
    assert all(point[2] == pytest.approx(4.25, abs=0.03) for point in result["surface_points_m"])
    assert result["confidence"] >= 0.50


@pytest.mark.parametrize(
    ("near_surface_id", "far_surface_id"),
    [
        (SURFACE_A, SURFACE_B),
        (None, None),
    ],
)
def test_wide_multilayer_requires_one_shared_non_null_surface_identity(
    near_surface_id: str | None,
    far_surface_id: str | None,
) -> None:
    result = ground_bbox_to_depth(
        _initial_sofa_multilayer_request(),
        _sparse_capture(_initial_sofa_multilayer_samples(
            near_surface_id=near_surface_id,
            far_surface_id=far_surface_id,
        )),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_wide_multilayer_rejects_weak_runner_with_different_surface_identity() -> None:
    result = ground_bbox_to_depth(
        _initial_sofa_multilayer_request(),
        _sparse_capture(_initial_sofa_multilayer_samples(
            near_surface_id=SURFACE_A,
            far_surface_id=SURFACE_B,
            far_count_per_side=3,
        )),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


@pytest.mark.parametrize(
    ("near_surface_id", "far_surface_id"),
    [
        (SURFACE_A, None),
        (None, None),
    ],
)
def test_wide_multilayer_rejects_weak_runner_without_complete_surface_identity(
    near_surface_id: str | None,
    far_surface_id: str | None,
) -> None:
    result = ground_bbox_to_depth(
        _initial_sofa_multilayer_request(),
        _sparse_capture(_initial_sofa_multilayer_samples(
            near_surface_id=near_surface_id,
            far_surface_id=far_surface_id,
            far_count_per_side=3,
        )),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def _role_tagged_live_sofa_samples(
    *,
    background_role: str = "walkable_ground",
    background_is_navigation_obstacle: bool = False,
) -> list[dict]:
    samples = _live_sofa_edge_contamination_samples(split_far_ids=False)
    shared_far_and_edge_surface = "surface:" + "d" * 64
    for sample in samples:
        _, v = sample["uv_norm"]
        if sample.get("surface_id") == SURFACE_A:
            sample["surface_role"] = "navigation_obstacle"
        elif sample.get("surface_id") == SURFACE_B:
            # The central foreground person is one physical body across all
            # sampled rows; it must not inherit the far-ground row's role.
            sample["surface_role"] = "physical_surface"
        elif v == pytest.approx(0.53125):
            sample["surface_role"] = (
                "navigation_obstacle"
                if background_is_navigation_obstacle
                else background_role
            )
        else:
            # Foreground person and isolated edge contaminants cannot qualify
            # for the role exception.
            if sample.get("surface_id") == shared_far_and_edge_surface:
                sample["surface_id"] = FIXTURE_EDGE_SURFACE
            sample["surface_role"] = "physical_surface"
    return samples


def _role_tagged_live_sofa_request(*, expected: bool = True) -> dict:
    request = _live_sofa_edge_contamination_request()
    if expected:
        request["detection"]["expected_surface_role"] = "navigation_obstacle"
    return request


def test_role_attested_live_sofa_can_ignore_only_weak_walkable_ground() -> None:
    result = ground_bbox_to_depth(
        _role_tagged_live_sofa_request(),
        _sparse_capture(_role_tagged_live_sofa_samples()),
    )

    assert result["status"] == "grounded"
    assert result["surface_id"] == SURFACE_A
    assert result["surface_role"] == "navigation_obstacle"
    assert result["position_m"][2] == pytest.approx(4.27, abs=0.03)


def test_role_exception_is_unavailable_without_caller_expected_role() -> None:
    result = ground_bbox_to_depth(
        _role_tagged_live_sofa_request(expected=False),
        _sparse_capture(_role_tagged_live_sofa_samples()),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_role_exception_rejects_structural_or_unknown_competitor() -> None:
    result = ground_bbox_to_depth(
        _role_tagged_live_sofa_request(),
        _sparse_capture(_role_tagged_live_sofa_samples(
            background_role="physical_surface",
        )),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_role_exception_rejects_second_navigation_obstacle_identity() -> None:
    result = ground_bbox_to_depth(
        _role_tagged_live_sofa_request(),
        _sparse_capture(_role_tagged_live_sofa_samples(
            background_is_navigation_obstacle=True,
        )),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_role_exception_rejects_comparable_walkable_runner() -> None:
    samples = _initial_sofa_multilayer_samples(
        near_surface_id=SURFACE_A,
        far_surface_id=SURFACE_B,
    )
    for sample in samples:
        if sample["surface_id"] == SURFACE_A:
            sample["surface_role"] = "navigation_obstacle"
        elif sample["surface_id"] == SURFACE_B and sample["uv_norm"][0] in (
            (index + 0.5) / 24.0 for index in range(10, 14)
        ):
            sample["surface_id"] = FIXTURE_PERSON_SURFACE
            sample["surface_role"] = "physical_surface"
        else:
            sample["surface_role"] = "walkable_ground"
    request = _initial_sofa_multilayer_request()
    request["detection"]["expected_surface_role"] = "navigation_obstacle"

    result = ground_bbox_to_depth(request, _sparse_capture(samples))

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_expected_surface_role_rejects_unique_mismatched_surface() -> None:
    samples = [
        {
            "uv_norm": [u, 0.50],
            "distance_m": _range_for_forward_depth(u, 0.50, 2.0),
            "surface_id": SURFACE_A,
            "surface_role": "walkable_ground",
        }
        for u in (0.45, 0.50, 0.55)
    ]
    request = _ground_request()
    request["detection"]["expected_surface_role"] = "navigation_obstacle"

    result = ground_bbox_to_depth(request, _sparse_capture(samples))

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_wide_compact_foreground_cannot_ground_without_occluder_evidence() -> None:
    result = ground_bbox_to_depth(
        _wide_occluded_request(with_hint=False),
        _sparse_capture(_wide_occluded_samples()),
    )

    assert result["status"] == "unresolved"
    assert result["reason"] == "ambiguous"


def test_metric_rasterizer_inflates_obstacle_by_footprint_and_clearance() -> None:
    width, height, occupancy = rasterize_occupancy(
        [0.0, 9.0, 0.0, 9.0],
        [{"kind": "circle", "center": [4.5, 4.5], "radius": 0.1}],
        1.0,
        0.6,
        0.5,
        unknown_cells=[(2, 4)],
    )
    assert (width, height) == (9, 9)
    assert occupancy[4 * width + 2] == UNKNOWN_VALUE
    for cell in ((4, 4), (3, 4), (5, 4), (4, 3), (4, 5)):
        assert occupancy[cell[1] * width + cell[0]] == OCCUPIED_VALUE


def test_rasterizer_accounts_for_cell_area_not_only_sample_centre() -> None:
    width, _, occupancy = rasterize_occupancy(
        [0.0, 7.0, 0.0, 7.0],
        [{"kind": "circle", "center": [3.01, 3.5], "radius": 0.01}],
        1.0,
        0.0,
    )
    # The obstacle is inside the neighbouring cell but 0.51 m from this
    # cell's sample centre.  A centre-only rasterizer incorrectly leaves it
    # free even though the cell area touches the obstacle.
    assert occupancy[3 * width + 2] == OCCUPIED_VALUE


def test_bounds_and_grid_inflation_include_cell_extent() -> None:
    width, height, bounded = rasterize_occupancy(
        [0.0, 7.0, 0.0, 7.0], [], 1.0, 0.1
    )
    assert bounded[3 * width] == OCCUPIED_VALUE
    assert bounded[3 * width + 3] != OCCUPIED_VALUE
    assert bounded[3 * width + 6] == OCCUPIED_VALUE

    source = bytearray(width * height)
    source[3 * width + 3] = OCCUPIED_VALUE
    inflated = inflate_occupancy(source, width, height, 1.0, 0.4)
    # 0.4 m is smaller than the 1 m centre spacing, but the neighbouring
    # traversed cell's area can still intrude into the requested margin.
    assert inflated[3 * width + 2] == OCCUPIED_VALUE
    assert inflated[3 * width + 4] == OCCUPIED_VALUE


def test_astar_detours_and_never_traverses_occupied_or_unknown_cells() -> None:
    portable_map = _wall_map(gap=True, unknown_cells=[(2, 1)])
    first = plan_path(portable_map, [7.5, 4.5, 0.0], arrival_radius_m=0.2, simplify=False)
    second = plan_path(portable_map, [7.5, 4.5, 0.0], arrival_radius_m=0.2, simplify=False)
    assert first["path_cells"] == second["path_cells"]
    assert any(y in {1, 7} for _, y in first["path_cells"])
    assert [2, 1] not in first["path_cells"]
    assert validate_path_collision(portable_map, first["path_m"])


def test_astar_approaches_a_wide_certified_surface_around_a_central_obstacle() -> None:
    request = _map_request(resolution=0.2, footprint=0.42)
    portable_map = build_occupancy_map(
        request,
        bounds_xy=[-10.0, 10.0, -10.0, 10.0],
        obstacles=[
            {"kind": "rect", "center": [0.95, 0.0], "half": [0.31, 2.25]},
            {"kind": "circle", "center": [2.05, 0.0], "radius": 0.30},
        ],
        agent_position_m=[4.45, 0.0, 0.0],
    )
    target = {
        "status": "grounded",
        "target_id": "target:" + "1" * 16,
        "map_id": portable_map["map_id"],
        "coordinate_frame_id": portable_map["coordinate_frame_id"],
        "position_m": [1.26, 0.0, 0.0],
        "uncertainty_radius_m": 0.29,
    }
    with pytest.raises(PortableNavigationCoreError) as caught:
        plan_path(
            portable_map,
            target,
            arrival_radius_m=0.8,
            minimum_clearance_m=0.1,
        )
    assert caught.value.code == "PORTABLE_CORE_NO_SAFE_ARRIVAL"

    target["surface_points_m"] = [
        [1.26, -2.0, 0.0],
        [1.26, -1.5, 0.0],
        [1.26, 1.5, 0.0],
        [1.26, 2.0, 0.0],
    ]
    planned = plan_path(
        portable_map,
        target,
        arrival_radius_m=0.8,
        minimum_clearance_m=0.1,
    )
    final = planned["waypoints_m"][-1]
    distance_to_surface = min(
        math.dist(final[:2], point[:2]) for point in target["surface_points_m"]
    )
    assert distance_to_surface <= 1.09 + 1e-9
    assert abs(final[1]) > 1.0
    assert planned["target_surface_point_count"] == 4
    assert planned["effective_arrival_radius_m"] == pytest.approx(1.09)
    assert validate_path_collision(
        portable_map,
        planned["path_m"],
        additional_inflation_m=0.1,
    )


def test_segment_validation_detects_mid_segment_wall_and_simplification_stays_safe() -> None:
    portable_map = _wall_map(gap=True)
    assert not validate_path_collision(
        portable_map,
        [[1.5, 4.5, 0.0], [7.5, 4.5, 0.0]],
    )
    planned = plan_path(portable_map, [7.5, 4.5, 0.0], arrival_radius_m=0.2, simplify=False)
    simplified = simplify_path(portable_map, planned["path_m"])
    assert len(simplified) < len(planned["path_m"])
    assert validate_path_collision(portable_map, simplified)


def test_astar_reports_no_path_instead_of_crossing_closed_wall() -> None:
    with pytest.raises(PortableNavigationCoreError) as caught:
        plan_path(_wall_map(gap=False), [7.5, 4.5, 0.0], arrival_radius_m=0.2)
    assert caught.value.code == "PORTABLE_CORE_NO_PATH"
