"""Portable E2E regressions for fresh semantic arrival revalidation."""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_REQUEST_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    PortableNavigationContractError,
    validate_capture_result,
    validate_ground_result,
)
from lumina_next.portable_navigation_core_v1 import (
    ground_bbox_to_depth,
    project_depth_samples,
)
from lumina_next.visual_navigation import (
    ARRIVAL_REVALIDATION_REVISION,
    RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M,
    VisualNavigation,
    portable_arrival_fragment_occluder_policy,
    portable_arrival_fragment_union,
    portable_arrival_sofa_part_surface_support,
    portable_arrival_sofa_part_union,
    portable_surface_sha256,
    select_portable_arrival_planar_distance,
)
from tests.test_visual_navigation_portable_v1 import (
    AlternateEngineAdapter,
    _canonical_sha256,
    _portable_service,
)

SURFACE_ID = "surface:" + "c" * 64


def _visible_detection() -> dict[str, Any]:
    return {
        "intent": "approach",
        "visible": True,
        "label": "blue exhibit chair",
        "confidence": .93,
        "bbox": [.35, .30, .65, .75],
    }


def _ambiguous_fragment_detection(
    rows: list[tuple[str, str, list[int]]],
) -> dict[str, Any]:
    return {
        "intent": "none",
        "visible": False,
        "label": "",
        "confidence": 0.0,
        "bbox": [0, 0, 0, 0],
        "target_id": -1,
        "category": "",
        "color": "",
        "perception_audit": {
            "inventory": [
                {"id": index, "label": label, "color": color, "bbox": bbox}
                for index, (label, color, bbox) in enumerate(rows)
            ],
        },
    }


def _fragment_union(
    detection: dict[str, Any],
    *,
    surface_id: Any = SURFACE_ID,
    surface_role: Any = "navigation_obstacle",
    expected_role: Any = "navigation_obstacle",
) -> dict[str, Any] | None:
    return portable_arrival_fragment_union(
        detection,
        requested_category="sofa",
        original_color="purple",
        reference_surface_id=surface_id,
        reference_surface_role=surface_role,
        expected_surface_role=expected_role,
    )


def _ambiguous_grounding() -> dict[str, Any]:
    return {"status": "unresolved", "reason": "ambiguous"}


def _visible_sofa_parts(
    rows: list[tuple[str, str, list[int]]],
    *,
    target_id: int = 0,
    dropped: list[int] | None = None,
) -> dict[str, Any]:
    inventory = [
        {"id": index, "label": label, "color": color, "bbox": bbox}
        for index, (label, color, bbox) in enumerate(rows)
    ]
    anchor = inventory[target_id]
    return {
        "intent": "approach",
        "visible": True,
        "label": anchor["label"],
        "category": "sofa",
        "color": anchor["color"],
        "confidence": 1.0,
        "bbox": [component / 1000.0 for component in anchor["bbox"]],
        "target_id": target_id,
        "perception_audit": {
            "inventory": inventory,
            "native_inventory_row_count": len(inventory),
            "retained_native_row_indices": list(range(len(inventory))),
            "dropped_degenerate_native_box_rows": dropped or [],
        },
    }


def _sofa_part_union(
    detection: dict[str, Any],
    *,
    normal_groundings: tuple[dict, dict] | None = None,
    semantics: tuple[Any, Any, Any] | None = None,
    surface_id: Any = SURFACE_ID,
    surface_role: Any = "navigation_obstacle",
    expected_role: Any = "navigation_obstacle",
    initial_sha256: Any = "a" * 64,
    expected_initial_sha256: Any = "a" * 64,
) -> dict[str, Any] | None:
    return portable_arrival_sofa_part_union(
        detection,
        requested_category="sofa",
        original_color="purple",
        reference_surface_id=surface_id,
        reference_surface_role=surface_role,
        expected_surface_role=expected_role,
        normal_groundings=normal_groundings or (
            _ambiguous_grounding(),
            _ambiguous_grounding(),
        ),
        surface_id_semantics_revisions=semantics or (
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
        ),
        initial_surface_sha256=initial_sha256,
        expected_initial_surface_sha256=expected_initial_sha256,
    )


def test_arrival_sofa_part_union_accepts_exact_0756_inventory() -> None:
    detection = _visible_sofa_parts([
        ("sofa", "light purple", [580, 660, 1000, 1000]),
        ("cushion", "light purple", [0, 720, 580, 1000]),
        ("cushion", "light purple", [0, 720, 300, 1000]),
        ("cushion", "light purple", [300, 720, 580, 1000]),
        ("cushion", "light purple", [580, 720, 800, 1000]),
        ("cushion", "light purple", [800, 720, 1000, 1000]),
        ("cushion", "light purple", [0, 720, 300, 1000]),
        ("cushion", "light purple", [300, 720, 580, 1000]),
    ])

    recovered = _sofa_part_union(detection)

    assert recovered is not None
    assert recovered["bbox"] == [0.0, .66, 1.0, 1.0]
    assert recovered["target_id"] == 0
    assert recovered["_arrival_sofa_part_inventory_ids"] == list(range(8))
    audit = recovered["perception_audit"]["arrival_sofa_part_union"]
    assert audit["scope"] == "arrival_second_stage_only"
    assert audit["normal_dual_grounding_requirement"] == (
        "both_unresolved_ambiguous"
    )
    assert audit["unique_part_count"] == 5
    assert audit["deduplicated_part_inventory_id_groups"] == [[2, 6], [3, 7]]
    assert audit["union_coverage_ratio"] == pytest.approx(.8976470588)
    assert audit["anchor_area_ratio"] == pytest.approx(.42)
    assert audit["horizontal_expansion_ratio"] == pytest.approx(1000 / 420)


@pytest.mark.parametrize(
    ("case", "detection", "kwargs"),
    [
        (
            "cushion_only",
            _visible_sofa_parts([
                ("cushion", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 580, 1000]),
            ]),
            {},
        ),
        (
            "two_sofa_anchors",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("sofa", "purple", [0, 720, 580, 1000]),
                ("cushion", "purple", [300, 720, 580, 1000]),
            ]),
            {},
        ),
        (
            "pillow_is_not_exact_cushion",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("pillow", "purple", [0, 720, 580, 1000]),
            ]),
            {},
        ),
        (
            "part_color_conflict",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "red", [0, 720, 580, 1000]),
            ]),
            {},
        ),
        (
            "two_pixel_horizontal_gap",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 578, 1000]),
            ]),
            {},
        ),
        (
            "corner_only_contact",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 400, 580, 660]),
            ]),
            {},
        ),
        (
            "vertical_escape",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 600, 580, 1000]),
            ]),
            {},
        ),
        (
            "oversized_horizontal_chain",
            _visible_sofa_parts([
                ("sofa", "purple", [700, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 700, 1000]),
            ]),
            {},
        ),
        (
            "dropped_inventory_row",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 580, 1000]),
            ], dropped=[2]),
            {},
        ),
        (
            "normal_semantic_grounded",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 580, 1000]),
            ]),
            {"normal_groundings": ({"status": "grounded"}, _ambiguous_grounding())},
        ),
        (
            "pose_epoch_missing",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 580, 1000]),
            ]),
            {"semantics": (SURFACE_ID_SEMANTICS_REVISION, None, SURFACE_ID_SEMANTICS_REVISION)},
        ),
        (
            "initial_surface_sha_mismatch",
            _visible_sofa_parts([
                ("sofa", "purple", [580, 660, 1000, 1000]),
                ("cushion", "purple", [0, 720, 580, 1000]),
            ]),
            {"initial_sha256": "a" * 64, "expected_initial_sha256": "b" * 64},
        ),
    ],
)
def test_arrival_sofa_part_union_rejects_unsafe_recovery_evidence(
    case: str,
    detection: dict[str, Any],
    kwargs: dict[str, Any],
) -> None:
    assert _sofa_part_union(detection, **kwargs) is None, case


def test_arrival_fragment_union_accepts_live_positive_overlap_chain() -> None:
    detection = _ambiguous_fragment_detection([
        ("sofa", "pink", [278, 625, 1000, 1000]),
        ("sofa", "pink", [0, 785, 500, 1000]),
        ("sofa", "pink", [0, 720, 280, 1000]),
    ])

    recovered = _fragment_union(detection)

    assert recovered is not None
    assert recovered["visible"] is True
    assert recovered["category"] == "sofa"
    assert recovered["color"] == "pink"
    assert recovered["bbox"] == [0.0, .625, 1.0, 1.0]
    assert recovered["_arrival_fragment_inventory_ids"] == [0, 1, 2]
    union_audit = recovered["perception_audit"]["arrival_fragment_union"]
    assert union_audit["image_candidate_generated"] is True
    assert union_audit["accepted"] is False
    assert union_audit["selection_basis"] == "single_positive_overlap_component"
    assert union_audit["dual_rgbd_exact_surface_continuity_required"] is True
    assert union_audit["reference_surface_id_required"] is True


@pytest.mark.parametrize(
    ("case", "detection", "kwargs"),
    [
        (
            "edge_contact_is_not_positive_area_overlap",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 500, 1000]),
                ("sofa", "pink", [500, 600, 1000, 1000]),
            ]),
            {},
        ),
        (
            "split_components",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 300, 1000]),
                ("sofa", "pink", [200, 600, 500, 1000]),
                ("sofa", "pink", [700, 600, 1000, 1000]),
            ]),
            {},
        ),
        (
            "conflicting_color",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "red", [400, 600, 1000, 1000]),
            ]),
            {},
        ),
        (
            "opposing_shades",
            _ambiguous_fragment_detection([
                ("sofa", "light purple", [0, 600, 600, 1000]),
                ("sofa", "dark purple", [400, 600, 1000, 1000]),
            ]),
            {},
        ),
        (
            "different_category_leaves_only_one_candidate",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("chair", "pink", [400, 600, 1000, 1000]),
            ]),
            {},
        ),
        (
            "malformed_box",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "pink", [400, 600, 400, 1000]),
            ]),
            {},
        ),
        (
            "missing_surface_id",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "pink", [400, 600, 1000, 1000]),
            ]),
            {"surface_id": None},
        ),
        (
            "missing_surface_role",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "pink", [400, 600, 1000, 1000]),
            ]),
            {"surface_role": None},
        ),
        (
            "conflicting_surface_role",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "pink", [400, 600, 1000, 1000]),
            ]),
            {"surface_role": "physical_surface"},
        ),
        (
            "walkable_ground_role_is_out_of_scope",
            _ambiguous_fragment_detection([
                ("sofa", "pink", [0, 600, 600, 1000]),
                ("sofa", "pink", [400, 600, 1000, 1000]),
            ]),
            {
                "surface_role": "walkable_ground",
                "expected_role": "walkable_ground",
            },
        ),
    ],
)
def test_arrival_fragment_union_rejects_unsafe_evidence(
    case: str,
    detection: dict[str, Any],
    kwargs: dict[str, Any],
) -> None:
    assert _fragment_union(detection, **kwargs) is None, case


def test_arrival_fragment_union_never_changes_a_visible_initial_selection() -> None:
    detection = {
        **_visible_detection(),
        "target_id": 0,
        "perception_audit": {
            "inventory": [
                {"id": 0, "label": "sofa", "color": "pink", "bbox": [0, 600, 600, 1000]},
                {"id": 1, "label": "sofa", "color": "pink", "bbox": [400, 600, 1000, 1000]},
            ],
        },
    }

    assert _fragment_union(detection) is None


def _viewpoint_biased_arrival_distance_evidence() -> tuple[
    dict[str, Any], list[list[float]], list[float], list[dict[str, Any]]
]:
    initial_surface = [
        [1.25958, value, 0.44]
        for value in (
            -2.18, -1.75, -1.30, -.85, -.40, 0.0, .40, .80, .95, 1.05,
            1.15, 1.45, 1.70, 1.88, 1.90, 1.92, 2.05, 2.18,
        )
    ]
    fresh_points = [
        [1.25958, 1.88, 0.44],
        [1.25958, 1.90, 0.44],
        [1.25958, 1.92, 0.44],
    ]
    fresh_grounded = {
        "status": "grounded",
        "surface_id": SURFACE_ID,
        "surface_role": "navigation_obstacle",
        "surface_points_m": fresh_points,
        "position_m": fresh_points[1],
    }
    continuity_checks = [
        {"basis": "semantic", "distance_m": 0.0, "continuous": True},
        {"basis": "post_inference", "distance_m": 0.0, "continuous": True},
    ]
    return (
        fresh_grounded,
        initial_surface,
        [2.27143, 1.04823, 0.0],
        continuity_checks,
    )


def test_arrival_distance_uses_initial_full_surface_only_for_live_viewpoint_bias(
) -> None:
    fresh, initial, agent, checks = _viewpoint_biased_arrival_distance_evidence()
    initial_sha256 = portable_surface_sha256(initial)

    selected, audit = select_portable_arrival_planar_distance(
        fresh,
        initial,
        agent,
        fresh_maximum_allowed_distance_m=1.27123,
        initial_maximum_allowed_distance_m=1.096,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
        expected_surface_role="navigation_obstacle",
        surface_id_semantics_revisions=(
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
        ),
        dual_fresh_groundings=(copy.deepcopy(fresh), copy.deepcopy(fresh)),
        continuity_checks=checks,
        initial_surface_sha256=initial_sha256,
        expected_initial_surface_sha256=initial_sha256,
    )

    assert audit["fresh_surface_subset_planar_distance_m"] > 1.27123
    assert audit["initial_certified_full_surface_planar_distance_m"] < 1.27123
    assert selected == audit["initial_certified_full_surface_planar_distance_m"]
    assert audit["dual_fresh_exact_surface_id_role_match"] is True
    assert audit["dual_metric_surface_continuity_passed_for_distance"] is True
    assert audit["initial_certified_surface_integrity_match"] is True
    assert audit["initial_certified_surface_distance_eligible"] is True
    assert audit["surface_id_semantics_revision"] == (
        SURFACE_ID_SEMANTICS_REVISION
    )
    assert set(audit["surface_id_semantics_revision_observations"].values()) == {
        SURFACE_ID_SEMANTICS_REVISION
    }
    assert audit["distance_basis"] == (
        "initial_certified_full_surface_after_dual_exact_id_role_continuity"
    )
    assert audit["distance_selection_reason"] == (
        "viewpoint_sampling_bias_on_same_attested_surface_component"
    )


@pytest.mark.parametrize(
    "broken_guard",
    [
        "surface_id",
        "surface_id_format",
        "surface_role",
        "expected_role",
        "initial_pose_epoch_semantics",
        "arrival_semantic_pose_epoch_semantics",
        "arrival_grounding_pose_epoch_semantics",
        "dual_surface_id",
        "dual_surface_role",
        "dual_ray_count",
        "metric_continuity",
        "initial_surface_integrity",
    ],
)
def test_arrival_distance_never_borrows_initial_surface_without_every_guard(
    broken_guard: str,
) -> None:
    fresh, initial, agent, checks = _viewpoint_biased_arrival_distance_evidence()
    first, second = copy.deepcopy(fresh), copy.deepcopy(fresh)
    reference_surface_id: Any = SURFACE_ID
    reference_surface_role: Any = "navigation_obstacle"
    expected_surface_role: Any = "navigation_obstacle"
    semantics: Any = (
        SURFACE_ID_SEMANTICS_REVISION,
        SURFACE_ID_SEMANTICS_REVISION,
        SURFACE_ID_SEMANTICS_REVISION,
    )
    initial_sha256 = portable_surface_sha256(initial)
    expected_sha256 = initial_sha256
    if broken_guard == "surface_id":
        reference_surface_id = None
    elif broken_guard == "surface_id_format":
        reference_surface_id = "surface:not-a-pose-epoch"
    elif broken_guard == "surface_role":
        reference_surface_role = None
    elif broken_guard == "expected_role":
        expected_surface_role = None
    elif broken_guard.endswith("pose_epoch_semantics"):
        broken_index = {
            "initial_pose_epoch_semantics": 0,
            "arrival_semantic_pose_epoch_semantics": 1,
            "arrival_grounding_pose_epoch_semantics": 2,
        }[broken_guard]
        values = list(semantics)
        values[broken_index] = "legacy_object_identity_only"
        semantics = tuple(values)
    elif broken_guard == "dual_surface_id":
        second["surface_id"] = "surface:" + "d" * 64
    elif broken_guard == "dual_surface_role":
        second["surface_role"] = "physical_surface"
    elif broken_guard == "dual_ray_count":
        second["surface_points_m"] = second["surface_points_m"][:2]
    elif broken_guard == "metric_continuity":
        checks[1] = {
            "basis": "post_inference",
            "distance_m": .20001,
            "continuous": False,
        }
    elif broken_guard == "initial_surface_integrity":
        expected_sha256 = "0" * 64

    selected, audit = select_portable_arrival_planar_distance(
        fresh,
        initial,
        agent,
        fresh_maximum_allowed_distance_m=1.27123,
        initial_maximum_allowed_distance_m=1.096,
        reference_surface_id=reference_surface_id,
        reference_surface_role=reference_surface_role,
        expected_surface_role=expected_surface_role,
        surface_id_semantics_revisions=semantics,
        dual_fresh_groundings=(first, second),
        continuity_checks=checks,
        initial_surface_sha256=initial_sha256,
        expected_initial_surface_sha256=expected_sha256,
    )

    assert selected == audit["fresh_surface_subset_planar_distance_m"]
    assert selected > audit["maximum_allowed_distance_m"]
    assert audit["initial_certified_surface_distance_eligible"] is False
    assert audit["distance_basis"] == "fresh_rgbd_surface_subset"
    assert audit["distance_selection_reason"] == (
        "initial_surface_ineligible_without_dual_exact_id_role_metric_continuity"
    )


class ArrivalDepthAdapter(AlternateEngineAdapter):
    """Replace only target-region depth in both post-arrival RGB-D frames."""

    def __init__(self, arrival_depth_m: float) -> None:
        super().__init__()
        self.arrival_depth_m = arrival_depth_m

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        if self.operations.count("capture") >= 3:
            for sample in result["depth"]["samples"]:
                if sample["uv_norm"][0] < .70:
                    sample["distance_m"] = self.arrival_depth_m
            result["depth"]["samples_sha256"] = _canonical_sha256(result["depth"]["samples"])
            result = validate_capture_result(
                result,
                request=request,
                now_unix=result["captured_unix"],
            )
            self.captures[result["frame_id"]] = result
        return result


class SurfaceIdentityAdapter(AlternateEngineAdapter):
    """Attach one opaque physical-body identity to every target ray."""

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        for sample in result["depth"]["samples"]:
            if sample["uv_norm"][0] < .70:
                sample["surface_id"] = SURFACE_ID
        result["depth"]["surface_id_semantics_revision"] = (
            SURFACE_ID_SEMANTICS_REVISION
        )
        result["depth"]["samples_sha256"] = _canonical_sha256(result["depth"]["samples"])
        result = validate_capture_result(
            result,
            request=request,
            now_unix=result["captured_unix"],
        )
        self.captures[result["frame_id"]] = result
        return result


class SurfaceRoleResultAdapter(SurfaceIdentityAdapter):
    """Model a future adapter result which echoes its selected surface role."""

    def __init__(self, *, arrival_role: str = "navigation_obstacle") -> None:
        super().__init__()
        self.arrival_role = arrival_role

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        for sample in result["depth"]["samples"]:
            if sample["uv_norm"][0] < .70:
                sample["surface_role"] = "navigation_obstacle"
        result["depth"]["samples_sha256"] = _canonical_sha256(result["depth"]["samples"])
        result = validate_capture_result(
            result,
            request=request,
            now_unix=result["captured_unix"],
        )
        self.captures[result["frame_id"]] = result
        return result

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await super().ground(request))
        result["surface_role"] = (
            "navigation_obstacle"
            if self.operations.count("ground") == 1
            else self.arrival_role
        )
        return result


def _as_ambiguous_grounding(
    result: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    ambiguous = dict(result)
    ambiguous.update(
        status="unresolved",
        target_id=None,
        confidence=0.0,
        coordinate_frame_id=None,
        position_m=None,
        uncertainty_radius_m=None,
        reason="ambiguous",
    )
    for field in ("surface_points_m", "surface_id", "surface_role"):
        ambiguous.pop(field, None)
    return validate_ground_result(
        ambiguous,
        request=request,
        now_unix=float(ambiguous["grounded_unix"]),
    )


class SofaPartRecoveryAdapter(AlternateEngineAdapter):
    """Provide exact target rays in both the sofa anchor and added region."""

    normal_anchor_bbox = [.58, .66, 1.0, 1.0]

    def __init__(self, *, remove_additional_from_last_frame: bool = False) -> None:
        super().__init__()
        self.remove_additional_from_last_frame = remove_additional_from_last_frame

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        capture_number = self.operations.count("capture")
        target_samples = [
            {"uv_norm": [u, v], "distance_m": 4.0, "confidence": .99,
             "surface_id": SURFACE_ID, "surface_role": "navigation_obstacle"}
            for u, v in (
                (.52, .74), (.54, .78), (.56, .82),
                (.60, .74), (.62, .78), (.64, .82),
            )
            if not (
                self.remove_additional_from_last_frame
                and capture_number == 4
                and u < .58
            )
        ]
        marker = {
            "uv_norm": [.90, .50],
            "distance_m": 6.54321,
            "confidence": .99,
        }
        result["depth"]["samples"] = [*target_samples, marker]
        result["depth"]["samples_sha256"] = _canonical_sha256(
            result["depth"]["samples"]
        )
        validated = validate_capture_result(
            result,
            request=request,
            now_unix=result["captured_unix"],
        )
        self.captures[validated["frame_id"]] = validated
        return validated

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = await super().ground(request)
        if request["detection"]["bbox_norm"] == self.normal_anchor_bbox:
            return _as_ambiguous_grounding(result, request)
        return result


def _sofa_part_support_fixture(
    *,
    remove_additional: bool = False,
) -> tuple[dict[str, Any], list[list[float]]]:
    adapter = SofaPartRecoveryAdapter(
        remove_additional_from_last_frame=remove_additional
    )

    async def capture_frame() -> dict[str, Any]:
        result: Mapping[str, Any] | None = None
        capture_count = 4 if remove_additional else 1
        for index in range(capture_count):
            result = await adapter.capture({
                "schema_version": CAPTURE_REQUEST_SCHEMA,
                "request_id": f"capture:sofa-part-support-{index}",
                "environment_id": "environment:alternate-engine-42",
                "session_id": "session:alternate-engine-test",
                "requested_unix": 1.0 + index,
                "require_depth": True,
                "max_age_ms": 1500,
            })
        assert result is not None
        return dict(result)

    capture = asyncio.run(capture_frame())
    references = [
        list(point["position_m"])
        for point in project_depth_samples(
            capture,
            bbox_norm=[.50, .66, .70, .90],
            min_confidence=.25,
        )
        if point.get("surface_id") == SURFACE_ID
    ]
    return capture, references


def test_arrival_sofa_part_surface_support_requires_three_rays_in_both_regions() -> None:
    capture, references = _sofa_part_support_fixture()

    support = portable_arrival_sofa_part_surface_support(
        capture,
        anchor_bbox=[.58, .66, 1.0, 1.0],
        union_bbox=[0.0, .66, 1.0, 1.0],
        reference_surface_points=references,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
    )

    assert support["passed"] is True
    assert support["anchor_exact_continuity_ray_count"] == 3
    assert support["additional_exact_continuity_ray_count"] == 3
    assert support["surface_id_world_pose_epoch_match"] is True


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        ("anchor_has_only_two", {"anchor_bbox": [.61, .66, 1.0, 1.0]}),
        ("additional_region_empty", {"union_bbox": [.58, .66, 1.0, 1.0]}),
        ("wrong_surface_id", {"reference_surface_id": "surface:" + "d" * 64}),
        ("wrong_surface_role", {"reference_surface_role": "physical_surface"}),
        ("continuity_over_0p20", {"shift_references": True}),
    ],
)
def test_arrival_sofa_part_surface_support_fails_closed(
    case: str,
    kwargs: dict[str, Any],
) -> None:
    capture, references = _sofa_part_support_fixture()
    if kwargs.pop("shift_references", False):
        references = [[point[0] + 1.0, point[1], point[2]] for point in references]
    arguments = {
        "anchor_bbox": [.58, .66, 1.0, 1.0],
        "union_bbox": [0.0, .66, 1.0, 1.0],
        "reference_surface_points": references,
        "reference_surface_id": SURFACE_ID,
        "reference_surface_role": "navigation_obstacle",
        **kwargs,
    }

    support = portable_arrival_sofa_part_surface_support(capture, **arguments)

    assert support["passed"] is False, case


def test_arrival_sofa_part_surface_support_rejects_missing_pose_epoch() -> None:
    capture, references = _sofa_part_support_fixture()
    capture = copy.deepcopy(capture)
    capture["depth"].pop("surface_id_semantics_revision")

    support = portable_arrival_sofa_part_surface_support(
        capture,
        anchor_bbox=[.58, .66, 1.0, 1.0],
        union_bbox=[0.0, .66, 1.0, 1.0],
        reference_surface_points=references,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
    )

    assert support["passed"] is False
    assert support["surface_id_world_pose_epoch_match"] is False


def _dual_fragment_occluder_policy_captures(
    *,
    wrong_id_ray: bool = False,
) -> tuple[tuple[dict, dict], list[list[float]]]:
    capture, references = _sofa_part_support_fixture()
    semantic = copy.deepcopy(capture)
    semantic["frame_id"] = "frame:fragment-occluder-semantic"
    grounding = copy.deepcopy(capture)
    grounding["frame_id"] = "frame:fragment-occluder-grounding"
    grounding["captured_unix"] = float(semantic["captured_unix"]) + .001
    if wrong_id_ray:
        for frame in (semantic, grounding):
            frame["depth"]["samples"][0]["surface_id"] = "surface:" + "d" * 64
            frame["depth"]["samples_sha256"] = _canonical_sha256(
                frame["depth"]["samples"]
            )
    return (semantic, grounding), references


def test_arrival_fragment_occluder_policy_preserves_dual_exact_reference_rays() -> None:
    captures, references = _dual_fragment_occluder_policy_captures()
    occluder = [.50, .72, .57, .83]

    retained, audit = portable_arrival_fragment_occluder_policy(
        captures,
        target_bbox=[0.0, .66, 1.0, 1.0],
        occluder_bboxes=[occluder],
        reference_surface_points=references,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
        expected_surface_role="navigation_obstacle",
        surface_id_semantics_revisions=(
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
        ),
    )

    assert retained == []
    assert audit["applied"] is True
    assert audit["raw_visual_inventory_occluder_count"] == 1
    assert audit["effective_visual_inventory_occluder_count"] == 0
    assert audit["suppressed_visual_inventory_occluder_indices"] == [0]
    assert audit["pose_epoch_semantics_match"] is True
    assert all(
        frame["exact_reference_ray_count"] == 3
        and frame["passed"] is True
        for frame in audit["per_occluder"][0]["fresh_frames"]
    )


@pytest.mark.parametrize(
    ("case", "wrong_id_ray", "occluder"),
    [
        ("one_wrong_surface_id_leaves_only_two_exact_rays", True, [.50, .72, .57, .83]),
        ("only_two_exact_rays_in_box", False, [.515, .735, .545, .795]),
    ],
)
def test_arrival_fragment_occluder_policy_retains_box_without_three_exact_rays(
    case: str,
    wrong_id_ray: bool,
    occluder: list[float],
) -> None:
    captures, references = _dual_fragment_occluder_policy_captures(
        wrong_id_ray=wrong_id_ray
    )

    retained, audit = portable_arrival_fragment_occluder_policy(
        captures,
        target_bbox=[0.0, .66, 1.0, 1.0],
        occluder_bboxes=[occluder],
        reference_surface_points=references,
        reference_surface_id=SURFACE_ID,
        reference_surface_role="navigation_obstacle",
        expected_surface_role="navigation_obstacle",
        surface_id_semantics_revisions=(
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
            SURFACE_ID_SEMANTICS_REVISION,
        ),
    )

    assert retained == [occluder], case
    assert audit["applied"] is False, case
    assert audit["effective_visual_inventory_occluder_count"] == 1, case
    assert audit["suppressed_visual_inventory_occluder_indices"] == [], case
    assert all(
        frame["exact_reference_ray_count"] == 2
        and frame["passed"] is False
        for frame in audit["per_occluder"][0]["fresh_frames"]
    ), case
    if wrong_id_ray:
        assert all(
            frame["nonreference_ray_count_excluded"] == 1
            for frame in audit["per_occluder"][0]["fresh_frames"]
        )


class ViewpointBiasedSurfaceRoleAdapter(SurfaceRoleResultAdapter):
    """Expose a full static surface initially and only its far edge at arrival."""

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        capture_count = self.operations.count("capture")
        if capture_count <= 2:
            horizontal_samples = [
                .470, .474, .478, .482, .486, .490, .494, .498, .502,
                .506, .510, .514, .518, .522, .526, .530, .534,
                .675, .680, .685,
            ]
        else:
            horizontal_samples = [.675, .680, .685]
        target_samples = [
            {
                "uv_norm": [horizontal, .50],
                "distance_m": 4.0,
                "confidence": .99,
                "surface_id": SURFACE_ID,
                "surface_role": "navigation_obstacle",
            }
            for horizontal in horizontal_samples
        ]
        marker = {
            "uv_norm": [.90, .90],
            "distance_m": 6.54321,
            "confidence": .99,
        }
        result["depth"]["samples"] = target_samples + [marker]
        result["depth"]["samples_sha256"] = _canonical_sha256(
            result["depth"]["samples"]
        )
        result = validate_capture_result(
            result,
            request=request,
            now_unix=result["captured_unix"],
        )
        self.captures[result["frame_id"]] = result
        return result


async def _run_job(service: VisualNavigation, request_id: str) -> None:
    await service.start("左の青い椅子まで行って", request_id, speak=False)
    assert service.task is not None
    await service.task


def _install_sofa_part_recovery_fixture(
    service: VisualNavigation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumina_next import visual_navigation as module

    real_ground = module.ground_bbox_to_depth

    def normal_anchor_is_ambiguous(
        request: Mapping[str, Any],
        capture: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = real_ground(request, capture, **kwargs)
        if request["detection"]["bbox_norm"] == SofaPartRecoveryAdapter.normal_anchor_bbox:
            return _as_ambiguous_grounding(result, request)
        return result

    monkeypatch.setattr(module, "ground_bbox_to_depth", normal_anchor_is_ambiguous)
    arrival_detection = _visible_sofa_parts([
        ("sofa", "light purple", [580, 660, 1000, 1000]),
        ("cushion", "light purple", [0, 720, 580, 1000]),
        ("cushion", "light purple", [0, 720, 300, 1000]),
        ("cushion", "light purple", [300, 720, 580, 1000]),
        ("cushion", "light purple", [580, 720, 800, 1000]),
        ("cushion", "light purple", [800, 720, 1000, 1000]),
        ("cushion", "light purple", [0, 720, 300, 1000]),
        ("cushion", "light purple", [300, 720, 580, 1000]),
    ])
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "intent": "approach",
                "visible": True,
                "label": "sofa",
                "category": "sofa",
                "color": "purple",
                "confidence": 1.0,
                "bbox": [.50, .66, .70, .90],
                "target_id": 0,
                "perception_audit": {
                    "inventory": [
                        {"id": 0, "label": "sofa", "color": "purple",
                         "bbox": [500, 660, 700, 900]},
                    ],
                },
            }
        return copy.deepcopy(arrival_detection)

    service._infer = infer


def test_arrival_sofa_part_union_second_stage_preserves_all_v5_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SofaPartRecoveryAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    _install_sofa_part_recovery_fixture(service, monkeypatch)

    asyncio.run(_run_job(service, "arrival-sofa-part-0756"))

    job = service.jobs["arrival-sofa-part-0756"]
    audit = job["arrival_revalidation"]
    recovery = audit["arrival_sofa_part_union_recovery"]
    union = audit["arrival_sofa_part_union"]
    assert job["status"] == "arrived", job
    assert audit["revision"] == ARRIVAL_REVALIDATION_REVISION
    assert recovery["attempted"] is True
    assert recovery["trigger"] == "normal_dual_grounding_unresolved_ambiguous"
    assert recovery["candidate_generated"] is True
    assert recovery["support_verified"] is True
    assert recovery["accepted"] is True
    assert recovery["completed"] is True
    assert union["image_candidate_generated"] is True
    assert union["accepted"] is True
    assert recovery["expanded_dual_grounding_verified"] is True
    assert recovery["expanded_dual_exact_surface_id_role_verified"] is True
    assert recovery["expanded_dual_coordinate_and_pair_tolerance_verified"] is True
    assert recovery["expanded_dual_metric_continuity_verified"] is True
    assert recovery["initial_surface_integrity_verified"] is True
    assert recovery["unchanged_distance_threshold_verified"] is True
    assert all(
        result == {
            "status": "unresolved",
            "reason": "ambiguous",
            "surface_point_count": 0,
        }
        for result in union["normal_grounding_results"].values()
    )
    assert all(
        support["passed"] is True
        and support["anchor_exact_continuity_ray_count"] >= 3
        and support["additional_exact_continuity_ray_count"] >= 3
        and support["surface_id_world_pose_epoch_match"] is True
        for support in union["surface_support"].values()
    )
    assert all(
        result["status"] == "grounded"
        and result["surface_id_match"] is True
        and result["surface_role_match"] is True
        and result["surface_point_count"] >= 3
        for result in audit["reference_grounding_results"].values()
    )
    assert audit["metric_surface_continuity_passed"] is True
    assert all(
        check["continuous"] is True
        and check["distance_m"] <= .20
        for check in audit["target_continuity_checks"]
    )
    assert audit["surface_id_world_pose_epoch_attested"] is True
    assert audit["initial_certified_surface_integrity_match"] is True
    assert audit["measured_planar_distance_m"] <= audit["maximum_allowed_distance_m"]
    assert adapter.operations.count("ground") == 3


def test_arrival_sofa_part_union_rejects_missing_additional_support_in_one_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SofaPartRecoveryAdapter(remove_additional_from_last_frame=True)
    service = _portable_service(tmp_path, monkeypatch, adapter)
    _install_sofa_part_recovery_fixture(service, monkeypatch)

    asyncio.run(_run_job(service, "arrival-sofa-part-support-missing"))

    job = service.jobs["arrival-sofa-part-support-missing"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "failed"
    assert job["error"] == "arrival_target_identity_discontinuous"
    assert audit["arrival_sofa_part_union_recovery"]["accepted"] is False
    assert audit["arrival_sofa_part_union"]["image_candidate_generated"] is True
    assert audit["arrival_sofa_part_union"]["accepted"] is False
    assert audit["arrival_sofa_part_union"]["surface_support"]["semantic"]["passed"] is True
    post = audit["arrival_sofa_part_union"]["surface_support"]["post_inference"]
    assert post["additional_exact_continuity_ray_count"] == 0
    assert post["passed"] is False
    assert job["stop_confirmed"] is True


def test_stable_target_requires_second_semantic_pass_and_fresh_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    selector_texts: list[str] = []

    async def infer(image: bytes, text: str) -> dict[str, Any]:
        assert image == adapter.rgb_bytes
        selector_texts.append(text)
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-stable"))

    job = service.jobs["arrival-revalidation-stable"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived"
    assert job["arrived"] is True
    assert selector_texts == [
        "左の青い椅子まで行って",
        "revalidate unique visible category: exhibit chair",
    ]
    assert "左" not in selector_texts[1]
    assert audit["revision"] == ARRIVAL_REVALIDATION_REVISION
    assert audit["status"] == "passed"
    assert audit["relative_side_reapplied"] is False
    assert audit["semantic_frame_id"] != audit["grounding_frame_id"]
    assert audit["semantic_map_source_frame_id"] == audit["semantic_frame_id"]
    assert audit["semantic_depth_map_id"] == audit["semantic_map_id"]
    assert audit["final_map_source_frame_id"] == audit["grounding_frame_id"]
    assert audit["final_map_id"] != audit["navigation_map_id"]
    recognition_lineage = job["recognition_grounding_lineage"]
    assert recognition_lineage["semantic_frame_id"] == job["frame_id"]
    assert recognition_lineage["semantic_map_source_frame_id"] == job["frame_id"]
    assert recognition_lineage["semantic_ground_source_frame_id"] == job["frame_id"]
    assert recognition_lineage["semantic_ground_map_id"] == recognition_lineage["semantic_map_id"]
    assert recognition_lineage["post_inference_frame_id"] == job["grounding_frame_id"]
    assert recognition_lineage["post_inference_map_source_frame_id"] == job["grounding_frame_id"]
    assert recognition_lineage["post_inference_ground_source_frame_id"] == job["grounding_frame_id"]
    assert recognition_lineage["post_inference_ground_map_id"] == recognition_lineage["post_inference_map_id"]
    assert recognition_lineage["semantic_map_id"] != recognition_lineage["post_inference_map_id"]
    assert audit["fresh_agent_position_basis"] == "post_inference_capture_synchronized_map"
    assert audit["measured_planar_distance_m"] <= audit["maximum_allowed_distance_m"]
    assert adapter.operations == [
        "capture", "build_map", "capture", "build_map", "ground", "navigate", "feedback",
        "capture", "build_map", "capture", "build_map", "ground",
    ]
    assert "stop" not in adapter.operations
    assert "speech_command_id" not in job


def test_arrival_e2e_recovers_static_surface_viewpoint_sampling_bias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ViewpointBiasedSurfaceRoleAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "purple sofa",
            "color": "purple",
            "category": "sofa",
            "confidence": .93,
            "bbox": [.25, .30, .75, .75],
            "target_id": 0,
        }

    service._infer = infer
    request_id = "arrival-static-surface-viewpoint-bias"
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived", job
    assert audit["status"] == "passed"
    assert audit["fresh_surface_subset_planar_distance_m"] > (
        audit["fresh_surface_subset_maximum_allowed_distance_m"]
    )
    assert audit["initial_certified_full_surface_planar_distance_m"] <= (
        audit["initial_certified_full_surface_maximum_allowed_distance_m"]
    )
    assert audit["measured_planar_distance_m"] == (
        audit["initial_certified_full_surface_planar_distance_m"]
    )
    assert audit["distance_basis"] == (
        "initial_certified_full_surface_after_dual_exact_id_role_continuity"
    )
    assert audit["distance_selection_reason"] == (
        "viewpoint_sampling_bias_on_same_attested_surface_component"
    )
    assert audit["surface_id_world_pose_epoch_attested"] is True
    assert audit["dual_fresh_exact_surface_id_role_match"] is True
    assert audit["dual_metric_surface_continuity_passed_for_distance"] is True
    assert audit["initial_certified_surface_integrity_match"] is True
    assert audit["initial_certified_surface_distance_eligible"] is True
    assert audit["maximum_allowed_distance_m"] == (
        audit["initial_certified_full_surface_maximum_allowed_distance_m"]
    )
    assert audit["fresh_surface_subset_maximum_allowed_distance_m"] == pytest.approx(
        audit["arrival_radius_m"]
        + audit["target_uncertainty_radius_m"]
        + audit["tolerance_m"]
    )
    assert audit[
        "initial_certified_full_surface_maximum_allowed_distance_m"
    ] == pytest.approx(
        audit["arrival_radius_m"]
        + audit["initial_target_uncertainty_radius_m"]
        + audit["tolerance_m"]
    )
    assert all(check["continuous"] for check in audit["target_continuity_checks"])
    assert "stop" not in adapter.operations


def test_surface_identity_is_propagated_privately_across_recognition_and_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SurfaceIdentityAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    request_id = "surface-id-continuity"
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ]
    assert job["status"] == "arrived"
    assert len(ground_requests) == 2
    assert all(
        request["arrival_continuity_surface"]["surface_id"] == SURFACE_ID
        for request in ground_requests
    )
    assert service._surface_ids[request_id] == SURFACE_ID
    assert job["recognition_grounding_lineage"]["reference_surface_identity_basis"] == (
        "opaque_physics_surface_id"
    )
    assert job["arrival_revalidation"]["reference_surface_identity_basis"] == (
        "opaque_physics_surface_id"
    )
    assert SURFACE_ID not in str(job)


def test_surface_role_is_stored_and_checked_across_recognition_and_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    infer_inputs: list[tuple[bytes, str]] = []

    async def infer(image: bytes, text: str) -> dict[str, Any]:
        infer_inputs.append((image, text))
        return {
            "intent": "approach",
            "visible": True,
            "label": "purple sofa",
            "confidence": .93,
            "bbox": [.35, .30, .65, .75],
        }

    service._infer = infer
    request_id = "surface-role-continuity"
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    audit = job["arrival_revalidation"]
    ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ]
    assert job["status"] == "arrived", job
    assert service._surface_roles[request_id] == "navigation_obstacle"
    assert all(
        request["arrival_continuity_surface"]["surface_role"]
        == "navigation_obstacle"
        for request in ground_requests
    )
    assert job["recognition_grounding_results"]["semantic"]["surface_role"] == (
        "navigation_obstacle"
    )
    assert job["recognition_grounding_results"]["post_inference"]["surface_role"] == (
        "navigation_obstacle"
    )
    assert job["recognition_grounding_lineage"]["reference_surface_role"] == (
        "navigation_obstacle"
    )
    assert audit["reference_surface_role"] == "navigation_obstacle"
    assert audit["reference_grounding_results"]["semantic"]["surface_role_match"] is True
    assert audit["reference_grounding_results"]["post_inference"]["surface_role_match"] is True
    assert all("navigation_obstacle" not in text for _image, text in infer_inputs)


def test_arrival_fragment_union_passes_only_after_dual_exact_surface_continuity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "intent": "approach",
                "visible": True,
                "label": "purple sofa",
                "color": "purple",
                "category": "sofa",
                "confidence": .93,
                "bbox": [.35, .30, .65, .75],
                "target_id": 0,
            }
        return _ambiguous_fragment_detection([
            ("sofa", "pink", [300, 300, 550, 700]),
            ("sofa", "pink", [450, 400, 700, 750]),
        ])

    service._infer = infer
    request_id = "arrival-fragment-dual-surface-continuity"
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived", job
    assert audit["arrival_fragment_union"]["accepted"] is True
    assert audit["grounding_occluder_bbox_count"] == 0
    assert audit["metric_surface_continuity_passed"] is True
    assert all(
        result["surface_id_match"] is True
        and result["surface_role_match"] is True
        for result in audit["reference_grounding_results"].values()
    )
    assert all(check["continuous"] for check in audit["target_continuity_checks"])
    assert (
        audit["provisional_photometric_color_continuity"]["status"]
        == "accepted_after_dual_rgbd_reference_match"
    )


class FragmentSurfaceRoleAdapter(SurfaceRoleResultAdapter):
    """Keep unresolved ground results contract-valid for negative cases."""

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return await AlternateEngineAdapter.ground(self, request)


def _install_fragment_union_with_cushion_fixture(
    service: VisualNavigation,
    *,
    request_id: str,
    replace_reference_id_at_arrival: bool = False,
) -> None:
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "intent": "approach",
                "visible": True,
                "label": "purple sofa",
                "color": "purple",
                "category": "sofa",
                "confidence": .93,
                "bbox": [.35, .30, .65, .75],
                "target_id": 0,
            }
        if replace_reference_id_at_arrival:
            service._surface_ids[request_id] = "surface:" + "d" * 64
        return _ambiguous_fragment_detection([
            ("sofa", "pink", [300, 300, 550, 700]),
            ("sofa", "pink", [450, 400, 700, 750]),
            # All three exact target rays are inside this model-declared
            # cushion box.  They may be preserved only by exact physical
            # surface identity, never by the cushion label itself.
            ("cushion", "pink", [470, 470, 530, 530]),
        ])

    service._infer = infer


def test_arrival_fragment_union_ignores_cushion_box_only_for_dual_exact_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FragmentSurfaceRoleAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    request_id = "arrival-fragment-cushion-exact-surface"
    _install_fragment_union_with_cushion_fixture(
        service,
        request_id=request_id,
    )

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    audit = job["arrival_revalidation"]
    union = audit["arrival_fragment_union"]
    policy = union["occluder_policy"]
    arrival_ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
        and str(request.get("request_id", "")).startswith("arrival-ground:")
    ]
    assert job["status"] == "arrived", job
    assert job["arrived"] is True
    assert union["image_candidate_generated"] is True
    assert union["accepted"] is True
    assert policy["applied"] is True
    assert policy["raw_visual_inventory_occluder_count"] == 1
    assert policy["effective_visual_inventory_occluder_count"] == 0
    assert policy["other_surface_ids_count_as_support"] is False
    assert audit["grounding_occluder_bbox_raw_count"] == 1
    assert audit["grounding_occluder_bbox_count"] == 0
    assert len(arrival_ground_requests) == 1
    assert "occluder_bboxes_norm" not in arrival_ground_requests[0]["detection"]
    assert arrival_ground_requests[0]["arrival_continuity_surface"]["surface_id"] == SURFACE_ID
    assert arrival_ground_requests[0]["arrival_continuity_surface"]["surface_role"] == (
        "navigation_obstacle"
    )


def test_arrival_fragment_union_candidate_stays_unaccepted_for_wrong_reference_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FragmentSurfaceRoleAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    request_id = "arrival-fragment-cushion-wrong-reference-id"
    _install_fragment_union_with_cushion_fixture(
        service,
        request_id=request_id,
        replace_reference_id_at_arrival=True,
    )

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    audit = job["arrival_revalidation"]
    union = audit["arrival_fragment_union"]
    policy = union["occluder_policy"]
    assert job["status"] == "failed"
    assert job["arrived"] is False
    assert job["error"] == "arrival_target_identity_discontinuous"
    assert union["image_candidate_generated"] is True
    assert union["accepted"] is False
    assert policy["applied"] is False
    assert policy["effective_visual_inventory_occluder_count"] == 1
    assert all(
        frame["exact_reference_ray_count"] == 0
        for frame in policy["per_occluder"][0]["fresh_frames"]
    )
    assert all(
        result["status"] == "unresolved"
        and result["surface_id_match"] is False
        for result in audit["reference_grounding_results"].values()
    )


def test_arrival_fragment_union_is_refused_without_exact_surface_id_and_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    request_id = "arrival-fragment-no-surface-identity"
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "intent": "approach",
                "visible": True,
                "label": "purple sofa",
                "color": "purple",
                "category": "sofa",
                "confidence": .93,
                "bbox": [.35, .30, .65, .75],
                "target_id": 0,
            }
        service._surface_ids.pop(request_id, None)
        service._surface_roles.pop(request_id, None)
        return _ambiguous_fragment_detection([
            ("sofa", "pink", [300, 300, 550, 700]),
            ("sofa", "pink", [450, 400, 700, 750]),
        ])

    service._infer = infer
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "ambiguous"
    assert job["error"] == "ambiguous_arrival_visual_target"
    assert (
        job["arrival_revalidation"]["error"]
        == "ambiguous_arrival_visual_target"
    )
    assert "arrival_fragment_union" not in job["arrival_revalidation"]
    assert adapter.operations[-1] == "stop"


def test_arrival_rejects_a_changed_reported_surface_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumina_next import visual_navigation as module

    adapter = SurfaceRoleResultAdapter(arrival_role="physical_surface")
    service = _portable_service(tmp_path, monkeypatch, adapter)
    # The strict contract would reject this before returning it. Bypass only
    # the caller-side result validator to prove the orchestrator independently
    # refuses the changed role as well.
    monkeypatch.setattr(
        module,
        "validate_ground_result",
        lambda result, **_kwargs: dict(result),
    )

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "purple sofa",
            "confidence": .93,
            "bbox": [.35, .30, .65, .75],
        }

    service._infer = infer
    request_id = "surface-role-changed"
    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "failed"
    assert job["error"] == "arrival_target_identity_discontinuous"
    assert (
        job["arrival_revalidation"]["reference_grounding_results"]["post_inference"]
        ["surface_role_match"]
        is False
    )


def test_initial_and_arrival_local_depth_checks_use_frame_owned_maps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumina_next import visual_navigation as module

    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    real_ground = module.ground_bbox_to_depth
    local_lineages: list[tuple[str, str]] = []

    def checked_local_ground(
        request: Mapping[str, Any],
        capture: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        source_frame_id = str(request["source_frame_id"])
        map_id = str(request["map_id"])
        assert capture["frame_id"] == source_frame_id
        assert adapter.maps[map_id]["source_frame_id"] == source_frame_id
        local_lineages.append((source_frame_id, map_id))
        return real_ground(request, capture, **kwargs)

    monkeypatch.setattr(module, "ground_bbox_to_depth", checked_local_ground)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-strict-lineage"))

    job = service.jobs["arrival-revalidation-strict-lineage"]
    assert job["status"] == "arrived"
    recognition = job["recognition_grounding_lineage"]
    arrival = job["arrival_revalidation"]
    assert local_lineages == [
        (recognition["semantic_frame_id"], recognition["semantic_map_id"]),
        (arrival["semantic_frame_id"], arrival["semantic_map_id"]),
    ]
    adapter_ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ]
    assert [
        (request["source_frame_id"], adapter.maps[request["map_id"]]["source_frame_id"])
        for request in adapter_ground_requests
    ] == [
        (recognition["post_inference_frame_id"], recognition["post_inference_frame_id"]),
        (arrival["grounding_frame_id"], arrival["grounding_frame_id"]),
    ]


def test_recognition_post_inference_ground_uses_initial_metric_surface_when_generic_is_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SparseRankingChangeAdapter(AlternateEngineAdapter):
        async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = copy.deepcopy(await super().capture(request))
            if self.operations.count("capture") == 2:
                # The 4 m surface is identical to the RGB-synchronized initial
                # capture.  A similarly supported 5 m plane makes ordinary
                # visual-support ranking ambiguous in only this sparse frame.
                target = [
                    {"uv_norm": [0.48, 0.48], "distance_m": 4.0, "confidence": 0.99},
                    {"uv_norm": [0.50, 0.50], "distance_m": 4.0, "confidence": 0.99},
                    {"uv_norm": [0.52, 0.52], "distance_m": 4.0, "confidence": 0.99},
                ]
                competitor = [
                    {"uv_norm": [0.48, 0.57], "distance_m": 5.0, "confidence": 0.99},
                    {"uv_norm": [0.50, 0.55], "distance_m": 5.0, "confidence": 0.99},
                    {"uv_norm": [0.52, 0.53], "distance_m": 5.0, "confidence": 0.99},
                ]
                marker = result["depth"]["samples"][-1]
                result["depth"]["samples"] = target + competitor + [marker]
                result["depth"]["samples_sha256"] = _canonical_sha256(
                    result["depth"]["samples"]
                )
                result = validate_capture_result(
                    result,
                    request=request,
                    now_unix=result["captured_unix"],
                )
                self.captures[result["frame_id"]] = result
            return result

    adapter = SparseRankingChangeAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "recognition-reference-surface-ranking-change"))

    job = service.jobs["recognition-reference-surface-ranking-change"]
    assert job["status"] == "arrived"
    recognition_ground_request = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ][0]
    reference = recognition_ground_request["arrival_continuity_surface"]
    lineage = job["recognition_grounding_lineage"]
    assert reference["target_id"] == lineage["reference_surface_target_id"]
    assert reference["maximum_distance_m"] == RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M
    assert portable_surface_sha256(reference["points_m"]) == lineage["reference_surface_sha256"]
    assert lineage["reference_surface_applied_to_frame_id"] == job["grounding_frame_id"]

    generic_request = dict(recognition_ground_request)
    generic_request.pop("arrival_continuity_surface")
    generic = ground_bbox_to_depth(
        generic_request,
        adapter.captures[generic_request["source_frame_id"]],
        grounded_unix=generic_request["requested_unix"],
    )
    assert generic["status"] == "unresolved"
    assert generic["reason"] == "ambiguous"
    assert job["recognition_grounding_results"]["post_inference"]["status"] == "grounded"


def test_target_moved_away_after_navigation_fails_closed_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ArrivalDepthAdapter(6.0)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-moved"))

    job = service.jobs["arrival-revalidation-moved"]
    assert job["status"] == "failed"
    assert job["arrived"] is False
    assert job["error"] == "arrival_target_identity_discontinuous"
    assert job["arrival_revalidation"]["status"] == "failed"
    assert any(
        not check["continuous"]
        for check in job["arrival_revalidation"]["target_continuity_checks"]
    )
    assert job["stop_confirmed"] is True
    assert adapter.operations[-1] == "stop"
    assert "speech_command_id" not in job


def test_same_attribute_impostor_near_agent_cannot_replace_original_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The final object has the same visual category/color and is inside the
    # arrival radius of the fresh agent position, but it is 0.5 m away from
    # the movement-start target. Semantic similarity must not swap identity.
    adapter = ArrivalDepthAdapter(3.5)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-impostor"))

    job = service.jobs["arrival-revalidation-impostor"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "failed"
    assert job["arrived"] is False
    assert job["error"] == "arrival_target_identity_discontinuous"
    assert audit["status"] == "failed"
    assert len(audit["target_continuity_checks"]) == 2
    assert all(not check["continuous"] for check in audit["target_continuity_checks"])
    assert all(
        check["distance_m"] is None
        and check["maximum_allowed_distance_m"] == .20
        for check in audit["target_continuity_checks"]
    )
    assert all(
        result["status"] == "unresolved" and result["reason"] == "ambiguous"
        for result in audit["reference_grounding_results"].values()
    )
    assert job["stop_confirmed"] is True
    assert adapter.operations[-1] == "stop"
    assert "speech_command_id" not in job


def test_small_dual_depth_noise_preserves_target_continuity_and_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ArrivalDepthAdapter(4.08)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-small-noise"))

    job = service.jobs["arrival-revalidation-small-noise"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived"
    assert job["arrived"] is True
    assert audit["status"] == "passed"
    assert len(audit["target_continuity_checks"]) == 2
    assert all(check["continuous"] for check in audit["target_continuity_checks"])
    assert all(
        check["distance_m"] < check["maximum_allowed_distance_m"] <= .30
        for check in audit["target_continuity_checks"]
    )
    assert "stop" not in adapter.operations


def test_arrival_accepts_same_base_color_with_viewpoint_shade_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _visible_detection()
        return {
            **_visible_detection(),
            "label": "light blue exhibit chair",
            "color": "light blue",
        }

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-shade-change"))

    job = service.jobs["arrival-revalidation-shade-change"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived"
    assert audit["original_color_base"] == "blue"
    assert audit["current_color_base"] == "blue"
    assert audit["shade_variation_accepted"] is True


def test_arrival_accepts_purple_pink_only_after_dual_metric_surface_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        color = "purple" if calls == 1 else "pink"
        return {
            **_visible_detection(),
            "label": f"{color} exhibit chair",
            "color": color,
        }

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-purple-pink"))

    job = service.jobs["arrival-revalidation-purple-pink"]
    audit = job["arrival_revalidation"]
    assert job["status"] == "arrived"
    assert job["arrived"] is True
    provisional = audit["provisional_photometric_color_continuity"]
    assert provisional["pair"] == ["pink", "purple"]
    assert provisional["status"] == "accepted_after_dual_rgbd_reference_match"
    assert provisional["accepted"] is True
    assert provisional["maximum_reference_distance_m"] == .20
    assert audit["metric_surface_continuity_passed"] is True
    assert all(
        result["status"] == "grounded"
        for result in audit["reference_grounding_results"].values()
    )
    assert all(check["continuous"] for check in audit["target_continuity_checks"])
    arrival_ground_request = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ][-1]
    continuity = arrival_ground_request["arrival_continuity_surface"]
    assert continuity["target_id"] == job["target_id"]
    assert continuity["maximum_distance_m"] == .20
    assert continuity["points_m"] == job["target_surface_points_m"]


def test_arrival_rejects_different_base_color(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _visible_detection()
        return {
            **_visible_detection(),
            "label": "purple exhibit chair",
            "color": "purple",
        }

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-base-color-change"))

    job = service.jobs["arrival-revalidation-base-color-change"]
    assert job["status"] == "failed"
    assert job["error"] == "arrival_target_semantics_changed"
    assert job["arrival_revalidation"]["current_color_base"] == "purple"
    assert adapter.operations[-1] == "stop"


def test_arrival_rejects_opposing_explicit_shades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                **_visible_detection(),
                "label": "light blue exhibit chair",
                "color": "light blue",
            }
        return {
            **_visible_detection(),
            "label": "dark blue exhibit chair",
            "color": "dark blue",
        }

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-opposing-shades"))

    job = service.jobs["arrival-revalidation-opposing-shades"]
    assert job["status"] == "failed"
    assert job["error"] == "arrival_target_semantics_changed"
    assert job["arrival_revalidation"]["shade_variation_accepted"] is False
    assert adapter.operations[-1] == "stop"


def test_target_absent_from_post_arrival_inventory_never_reports_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    calls = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _visible_detection()
        return {
            "intent": "none", "visible": False, "label": "", "confidence": 0.0,
            "bbox": [0, 0, 0, 0], "target_id": -1, "category": "", "color": "",
        }

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-absent"))

    job = service.jobs["arrival-revalidation-absent"]
    assert job["status"] == "not_visible"
    assert job["arrived"] is False
    assert job["error"] == "not_visible"
    assert job["arrival_revalidation"]["status"] == "failed"
    assert adapter.operations[-3:] == ["capture", "build_map", "stop"]
    assert "speech_command_id" not in job


def test_camera_change_after_arrival_inference_never_reports_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChangedCameraAdapter(AlternateEngineAdapter):
        async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = copy.deepcopy(await super().capture(request))
            if self.operations.count("capture") == 4:
                result["camera"]["intrinsics"]["fx_px"] *= 1.5
                result = validate_capture_result(
                    result,
                    request=request,
                    now_unix=result["captured_unix"],
                )
                self.captures[result["frame_id"]] = result
            return result

    adapter = ChangedCameraAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return _visible_detection()

    service._infer = infer
    asyncio.run(_run_job(service, "arrival-revalidation-camera"))

    job = service.jobs["arrival-revalidation-camera"]
    assert job["status"] == "failed"
    assert job["arrived"] is False
    assert job["error"] == "view_changed_during_arrival_revalidation"
    assert job["arrival_revalidation"]["synchronized_camera_match"] is False
    assert job["stop_confirmed"] is True
    assert adapter.operations[-1] == "stop"
    after_feedback = adapter.operations[adapter.operations.index("feedback") + 1:]
    assert after_feedback == ["capture", "build_map", "capture", "stop"]
    assert "speech_command_id" not in job


def test_initial_and_arrival_inference_each_hold_the_phase_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumina_next import visual_navigation as module

    adapter = AlternateEngineAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(module, "phase_enabled", lambda: True)
    events: list[str] = []
    held = False

    @asynccontextmanager
    async def lease():
        nonlocal held
        assert held is False
        held = True
        events.append("lease_enter")
        try:
            yield
        finally:
            held = False
            events.append("lease_exit")

    monkeypatch.setattr(module, "inference_lease", lease)

    async def wake(
        job: dict,
        *,
        audit_field: str = "model_wake",
        latency_field: str = "model_wake_latency_ms",
    ) -> None:
        assert held is True
        events.append(audit_field)
        job[audit_field] = {"phase_lease_held": True, "stage": "ready"}
        job[latency_field] = 0

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        assert held is True
        events.append("infer")
        return _visible_detection()

    async def pass_budget(operation: Any) -> Any:
        return await operation()

    service._wake_model = wake
    service._infer = infer
    service.resource_budget.run = pass_budget
    asyncio.run(_run_job(service, "arrival-revalidation-phase"))

    job = service.jobs["arrival-revalidation-phase"]
    assert job["status"] == "arrived"
    assert events == [
        "lease_enter", "model_wake", "infer", "lease_exit",
        "lease_enter", "arrival_model_wake", "infer", "lease_exit",
    ]
    assert job["phase_lease_held"] is False
    assert job["arrival_phase_lease_held"] is False
    assert job["model_wake"]["phase_lease_held"] is False
    assert job["arrival_model_wake"]["phase_lease_held"] is False
