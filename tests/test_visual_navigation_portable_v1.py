"""End-to-end acceptance tests for VisualNavigation's portable branch.

These tests deliberately provide no Godot bridge, scene tree, evaluator
geometry, or native object name to the orchestration layer.  A synthetic
alternate-engine adapter implements the six portable operations and validates
every request/result at the contract boundary.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_RESULT_SCHEMA,
    NAVIGATE_RESULT_SCHEMA,
    STOP_RESULT_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
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
from lumina_next.portable_navigation_core_v1 import (
    PortableNavigationCoreError,
    build_occupancy_map,
    ground_bbox_to_depth,
    validate_path_collision,
)
from lumina_next.visual_navigation import (
    ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M,
    ARRIVAL_REVALIDATION_TOLERANCE_M,
    PORTABLE_APPROACH_RADIUS_CANDIDATES_M,
    PORTABLE_SURFACE_ROLE_POLICY_REVISION,
    RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M,
    VisualNavigation,
    plan_portable_approach,
    portable_expected_surface_role,
    portable_wide_low_target_needs_visibility_standoff,
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AlternateEngineAdapter:
    """Strict in-memory adapter with no simulator-specific public fields."""

    native_node_name = "POISON_NATIVE_NODE_MUST_NEVER_CROSS_THE_ADAPTER"
    rgb_bytes = b"\x89PNG\r\n\x1a\nqwen-rgb-only-fixture"
    target_position_m = [4.0, 0.0, 1.4]

    def __init__(
        self,
        *,
        collide: bool = False,
        clearance_violation: bool = False,
        block_navigate: bool = False,
    ) -> None:
        self.collide = collide
        self.clearance_violation = clearance_violation
        self.block_navigate = block_navigate
        self.operations: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.captures: dict[str, dict[str, Any]] = {}
        self.maps: dict[str, dict[str, Any]] = {}
        self.map_result: dict[str, Any] | None = None
        self.command_id: str | None = None
        self.last_capture_unix = 0.0
        self.feedback_sequence = -1
        self.navigate_entered = asyncio.Event()

    def _record(self, operation: str, request: Mapping[str, Any]) -> None:
        self.operations.append(operation)
        self.requests.append(dict(request))

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_capture_request(request)
        self._record("capture", normalized)
        captured_unix = max(
            time.time(),
            normalized["requested_unix"],
            self.last_capture_unix + 0.001,
        )
        self.last_capture_unix = captured_unix
        samples = [
            {"uv_norm": [0.48, 0.48], "distance_m": 4.0, "confidence": 0.99},
            {"uv_norm": [0.50, 0.50], "distance_m": 4.0, "confidence": 0.99},
            {"uv_norm": [0.52, 0.52], "distance_m": 4.0, "confidence": 0.99},
            # Marker for the depth plane; it is outside the semantic bbox and
            # must never appear in the bytes delivered to perception/Qwen.
            {"uv_norm": [0.90, 0.90], "distance_m": 6.54321, "confidence": 0.99},
        ]
        result = {
            "schema_version": CAPTURE_RESULT_SCHEMA,
            "request_id": normalized["request_id"],
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "frame_id": f"frame:alternate-{len([op for op in self.operations if op == 'capture'])}",
            "captured_unix": captured_unix,
            "rgb": {
                "encoding": "png",
                "width_px": 64,
                "height_px": 48,
                "data_base64": base64.b64encode(self.rgb_bytes).decode("ascii"),
                "sha256": hashlib.sha256(self.rgb_bytes).hexdigest(),
            },
            "depth": {
                "representation": "sparse_rays",
                "measurement_model": "ray_range_m",
                "alignment": "registered_normalized_to_rgb",
                "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
                "samples": samples,
                "samples_sha256": _canonical_sha256(samples),
            },
            "camera": {
                "coordinate_frame_id": "world:alternate-engine-test",
                "coordinate_convention": COORDINATE_CONVENTION,
                "position_m": [0.0, 0.0, 1.4],
                # Optical +z -> canonical +x, image-right -> canonical -y,
                # and image-down -> canonical -z.
                "orientation_xyzw": [0.5, -0.5, 0.5, -0.5],
                "intrinsics": {"fx_px": 50.0, "fy_px": 50.0, "cx_px": 32.0, "cy_px": 24.0},
            },
        }
        validated = validate_capture_result(result, request=normalized, now_unix=captured_unix)
        self.captures[validated["frame_id"]] = validated
        return validated

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_map_request(request)
        self._record("build_map", normalized)
        # A wall intersects the direct start-to-target segment but leaves room
        # above and below it, forcing the common planner to produce a detour.
        result = build_occupancy_map(
            normalized,
            bounds_xy=[-1.0, 5.0, -3.0, 3.0],
            obstacles=[{
                "kind": "rect",
                "center_m": [2.0, 0.0],
                "half_extents_m": [0.2, 0.8],
            }],
            agent_position_m=(
                [3.3, 0.0, 0.0]
                if len(self.captures) > 2
                else [0.0, 0.0, 0.0]
            ),
            created_unix=normalized["requested_unix"],
        )
        result["coordinate_frame_id"] = "world:alternate-engine-test"
        self.map_result = validate_map_result(
            result,
            request=normalized,
            now_unix=normalized["requested_unix"],
        )
        self.maps[self.map_result["map_id"]] = self.map_result
        return self.map_result

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_ground_request(request)
        source_map = self.maps.get(normalized["map_id"])
        if source_map is None:
            raise PortableNavigationContractError("PORTABLE_MAP_UNKNOWN", "map_id")
        if source_map["source_frame_id"] != normalized["source_frame_id"]:
            raise PortableNavigationContractError(
                "PORTABLE_LINEAGE_MISMATCH",
                "source_frame_id",
            )
        self._record("ground", normalized)
        assert self.map_result is not None
        result = ground_bbox_to_depth(
            normalized,
            self.captures[normalized["source_frame_id"]],
            grounded_unix=normalized["requested_unix"],
        )
        validated = validate_ground_result(
            result,
            request=normalized,
            now_unix=normalized["requested_unix"],
        )
        if validated["status"] == "grounded":
            self.target_position_m = validated["position_m"]
        return validated

    async def navigate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_navigate_request(request)
        self._record("navigate", normalized)
        self.command_id = normalized["command_id"]
        self.navigate_entered.set()
        if self.block_navigate:
            # Model an adapter which may have handed the command to its engine
            # but whose acknowledgement has not reached the caller yet.
            await asyncio.Event().wait()
        result = {
            "schema_version": NAVIGATE_RESULT_SCHEMA,
            "navigation_id": normalized["navigation_id"],
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "map_id": normalized["map_id"],
            "target_id": normalized["target_id"],
            "status": "accepted",
            "command_id": normalized["command_id"],
            "accepted_unix": normalized["requested_unix"],
            "reason": None,
        }
        return validate_navigate_result(
            result,
            request=normalized,
            now_unix=normalized["requested_unix"],
        )

    async def feedback(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_feedback_request(request)
        self._record("feedback", normalized)
        self.feedback_sequence = max(self.feedback_sequence + 1, normalized["after_sequence"] + 1)
        if self.collide:
            status, collision, reached, remaining, clearance, reason = (
                "blocked", True, False, 2.6, 0.0, "dynamic_obstacle",
            )
        elif self.clearance_violation:
            status, collision, reached, remaining, clearance, reason = (
                "moving", False, False, 1.8, 0.01, None,
            )
        else:
            status, collision, reached, remaining, clearance, reason = (
                "arrived", False, True, 0.45, 0.55, None,
            )
        result = {
            "schema_version": FEEDBACK_RESULT_SCHEMA,
            "request_id": normalized["request_id"],
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "command_id": normalized["command_id"],
            "sequence": self.feedback_sequence,
            "observed_unix": normalized["requested_unix"],
            "status": status,
            "position_m": [3.3, 0.0, 0.0] if reached else [1.0, 0.0, 0.0],
            "velocity_mps": [0.0, 0.0, 0.0],
            "remaining_distance_m": remaining,
            "minimum_clearance_m": clearance,
            "collision_detected": collision,
            "target_reached": reached,
            "reason": reason,
        }
        return validate_feedback_result(
            result,
            request=normalized,
            now_unix=normalized["requested_unix"],
        )

    async def stop(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_stop_request(request)
        self._record("stop", normalized)
        result = {
            "schema_version": STOP_RESULT_SCHEMA,
            "request_id": normalized["request_id"],
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "owned_command_id": normalized["command_id"],
            "status": "confirmed",
            "confirmed": True,
            "stopped_unix": normalized["requested_unix"],
            "final_sequence": max(0, self.feedback_sequence),
            "reason": None,
        }
        return validate_stop_result(
            result,
            request=normalized,
            now_unix=normalized["requested_unix"],
        )


def _portable_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter: AlternateEngineAdapter,
) -> VisualNavigation:
    from lumina_next import visual_navigation as module

    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    monkeypatch.setenv("LUMINA_PORTABLE_NAVIGATION", "1")
    monkeypatch.setenv("LUMINA_PORTABLE_ENVIRONMENT_ID", "environment:alternate-engine-42")
    monkeypatch.setattr(module, "phase_enabled", lambda: False)
    service = VisualNavigation(
        tmp_path,
        "http://127.0.0.1:1",
        "http://127.0.0.1:2/v1",
        portable_adapter=adapter,
    )

    async def forbidden(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        pytest.fail("portable mode touched a Godot/legacy bridge or frame path")

    service._world = forbidden
    service._http = forbidden
    service._command = forbidden
    service._fresh_frame = forbidden
    return service


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("sofa", "navigation_obstacle"),
        ("chair", "navigation_obstacle"),
        ("table", "navigation_obstacle"),
        ("plant", "navigation_obstacle"),
        ("floor", "walkable_ground"),
        ("rug", "walkable_ground"),
        ("sidewalk", "walkable_ground"),
        ("unknown artifact", None),
        ("exhibit chair", None),
        ("rectangle", None),
        (None, None),
    ],
)
def test_portable_expected_surface_role_is_an_exact_conservative_allowlist(
    category: object,
    expected: str | None,
) -> None:
    assert portable_expected_surface_role(category) == expected


def test_exact_sofa_role_reaches_both_grounding_requests_but_not_qwen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RoleTaggedSofaAdapter(AlternateEngineAdapter):
        async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = copy.deepcopy(await super().capture(request))
            for sample in result["depth"]["samples"]:
                if sample["uv_norm"][0] < .70:
                    sample["surface_id"] = "surface:" + "d" * 64
                    sample["surface_role"] = "navigation_obstacle"
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

    adapter = RoleTaggedSofaAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    infer_inputs: list[tuple[bytes, str]] = []

    async def infer(image: bytes, text: str) -> dict[str, Any]:
        infer_inputs.append((image, text))
        return {
            "intent": "approach",
            "visible": True,
            "label": "purple sofa",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("紫のソファまで行って", "portable-sofa-role", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-sofa-role"]
    ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ]
    assert job["status"] == "arrived", job
    assert job["expected_surface_role"] == "navigation_obstacle"
    assert job["surface_role_policy_revision"] == PORTABLE_SURFACE_ROLE_POLICY_REVISION
    assert job["arrival_revalidation"]["expected_surface_role"] == "navigation_obstacle"
    assert len(ground_requests) == 2
    assert all(
        request["detection"]["expected_surface_role"] == "navigation_obstacle"
        for request in ground_requests
    )
    assert all(image == adapter.rgb_bytes for image, _text in infer_inputs)
    assert all("surface_role" not in text for _image, text in infer_inputs)
    assert all("navigation_obstacle" not in text for _image, text in infer_inputs)


def test_portable_visual_navigation_gives_qwen_only_rgb_and_navigates_astar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumina_next import visual_navigation as module

    adapter = AlternateEngineAdapter()
    assert isinstance(adapter, PortableNavigationAdapter)
    service = _portable_service(tmp_path, monkeypatch, adapter)
    infer_calls: list[tuple[bytes, str]] = []

    async def pass_resource_check(operation: Any) -> Any:
        return await operation()

    async def qwen_boundary(
        _http: Any,
        image: bytes,
        text: str,
        *,
        open_vocabulary: bool = False,
    ) -> dict[str, Any]:
        # Each semantic RGB-D frame first gets its own target-free map lineage;
        # Qwen still receives exactly the extracted RGB bytes plus selector
        # text. Arrival revalidation invokes it again after a fresh capture.
        assert open_vocabulary is True
        if not infer_calls:
            assert adapter.operations == ["capture", "build_map"]
        else:
            assert adapter.operations[-2:] == ["capture", "build_map"]
        assert type(image) is bytes and image == adapter.rgb_bytes
        assert adapter.native_node_name not in text
        assert b"6.54321" not in image
        infer_calls.append((image, text))
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
            "target_id": 0,
            "perception_audit": {
                "inventory": [
                    {"id": 0, "label": "chair", "color": "blue", "bbox": [350, 300, 650, 750]},
                    {"id": 1, "label": "person", "color": "black", "bbox": [450, 350, 550, 700]},
                ],
            },
        }

    # Keep this an orchestration test rather than a host-memory probe while
    # exercising the real VisualNavigation._infer -> perception/Qwen boundary.
    service.resource_budget.run = pass_resource_check
    monkeypatch.setattr(module, "perceive", qwen_boundary)

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-arrival", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-arrival"]
    assert job["status"] == "arrived", job
    assert job["arrived"] is True
    assert job["command_transport"] == "portable"
    assert job["approach_radius_policy"] == (
        "nearest_safe_surface_standoff_0p8_to_1p2_v1"
    )
    assert job["visibility_standoff_radius_policy_applied"] is False
    assert job["visibility_standoff_decision_basis"] == "not_applicable"
    assert job["visibility_standoff_connectivity_guard_radius_m"] is None
    assert job["visibility_preservation_verification"] == "not_applicable"
    assert job["last_feedback"]["status"] == "arrived"
    assert infer_calls == [
        (adapter.rgb_bytes, "青い椅子まで行って"),
        (adapter.rgb_bytes, "revalidate unique visible category: exhibit chair"),
    ]
    assert job["arrival_revalidation"]["status"] == "passed"
    assert adapter.operations == [
        "capture", "build_map", "capture", "build_map", "ground", "navigate", "feedback",
        "capture", "build_map", "capture", "build_map", "ground",
    ]
    ground_requests = [
        request
        for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "ground"
    ]
    assert len(ground_requests) == 2
    assert all(
        request["detection"]["occluder_bboxes_norm"] == [[0.45, 0.35, 0.55, 0.70]]
        for request in ground_requests
    )
    assert job["expected_surface_role"] is None
    assert all("expected_surface_role" not in request["detection"] for request in ground_requests)

    navigate_request = next(
        request for operation, request in zip(adapter.operations, adapter.requests)
        if operation == "navigate"
    )
    assert adapter.map_result is not None
    assert len(navigate_request["waypoints_m"]) >= 2
    assert not validate_path_collision(
        adapter.map_result,
        [[0.0, 0.0, 0.0], adapter.target_position_m],
        additional_inflation_m=navigate_request["minimum_clearance_m"],
    )
    assert validate_path_collision(
        adapter.map_result,
        [[0.0, 0.0, 0.0], *navigate_request["waypoints_m"]],
        additional_inflation_m=navigate_request["minimum_clearance_m"],
    )

    serialized_requests = json.dumps(adapter.requests, ensure_ascii=False)
    assert adapter.native_node_name not in serialized_requests
    assert "target_node" not in serialized_requests
    assert "world_position" not in serialized_requests
    assert "evaluator_geometry" not in serialized_requests


def test_portable_visual_navigation_collision_stops_exact_owned_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter(collide=True)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(image: bytes, text: str) -> dict[str, Any]:
        assert image == adapter.rgb_bytes
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-collision", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-collision"]
    assert job["status"] == "failed"
    assert job["arrived"] is False
    assert job["error"] == "portable_collision_detected"
    assert job["stop_confirmed"] is True
    assert job["stop_expected_command_id"] == adapter.command_id
    assert job["portable_stop_result"]["owned_command_id"] == adapter.command_id
    assert adapter.operations == [
        "capture", "build_map", "capture", "build_map", "ground", "navigate", "feedback", "stop",
    ]
    stop_request = adapter.requests[-1]
    assert stop_request["command_id"] == adapter.command_id
    assert stop_request["reason"] == "visual_navigation_cancelled"


def test_portable_visual_navigation_clearance_violation_stops_owned_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter(clearance_violation=True)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-clearance", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-clearance"]
    assert job["status"] == "failed"
    assert job["error"] == "portable_clearance_violation"
    assert job["last_feedback"]["collision_detected"] is False
    assert job["last_feedback"]["minimum_clearance_m"] < 0.10
    assert job["stop_confirmed"] is True
    assert adapter.operations[-2:] == ["feedback", "stop"]
    assert adapter.requests[-1]["command_id"] == job["command_id"] == adapter.command_id


def test_portable_visual_navigation_rejects_adapter_command_id_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SubstitutingAdapter(AlternateEngineAdapter):
        async def navigate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            normalized = validate_navigate_request(request)
            self._record("navigate", normalized)
            self.command_id = normalized["command_id"]
            self.navigate_entered.set()
            # Deliberately skip the adapter-side validator.  The orchestrator
            # must independently reject an adapter that substitutes ownership.
            return {
                "schema_version": NAVIGATE_RESULT_SCHEMA,
                "navigation_id": normalized["navigation_id"],
                "environment_id": normalized["environment_id"],
                "session_id": normalized["session_id"],
                "map_id": normalized["map_id"],
                "target_id": normalized["target_id"],
                "status": "accepted",
                "command_id": "command:adapter-substituted",
                "accepted_unix": normalized["requested_unix"],
                "reason": None,
            }

    adapter = SubstitutingAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-owner-mismatch", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-owner-mismatch"]
    assert job["status"] == "failed"
    assert job["error"] == "portable_command_ownership_mismatch"
    assert job["stop_confirmed"] is True
    assert adapter.operations[-2:] == ["navigate", "stop"]
    assert adapter.requests[-1]["command_id"] == job["command_id"] == adapter.command_id
    assert adapter.requests[-1]["command_id"] != "command:adapter-substituted"


def test_cancel_during_portable_navigate_await_stops_preallocated_owned_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlternateEngineAdapter(block_navigate=True)
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-dispatch-race", speak=False)
        await asyncio.wait_for(adapter.navigate_entered.wait(), timeout=1.0)
        job = service.jobs["portable-dispatch-race"]
        navigate_request = adapter.requests[-1]
        assert adapter.operations[-1] == "navigate"
        assert job["command_dispatch_state"] == "pending"
        assert job["command_transport"] == "portable"
        assert job["command_id"] == navigate_request["command_id"] == adapter.command_id

        assert await service.cancel("portable-dispatch-race") is True

    asyncio.run(run())

    job = service.jobs["portable-dispatch-race"]
    assert job["status"] == "cancelled"
    assert job["arrived"] is False
    assert job["stop_confirmed"] is True
    assert adapter.operations[-2:] == ["navigate", "stop"]
    assert adapter.requests[-1]["command_id"] == job["command_id"] == adapter.command_id
    assert job["portable_stop_result"]["owned_command_id"] == adapter.command_id


@pytest.mark.parametrize("change", ["pose", "intrinsics"])
def test_initial_and_current_camera_change_never_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    class ChangedCameraAdapter(AlternateEngineAdapter):
        async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = dict(await super().capture(request))
            if self.operations.count("capture") == 2:
                if change == "pose":
                    result["camera"]["position_m"][1] += 0.40
                else:
                    result["camera"]["intrinsics"]["fx_px"] *= 1.8
                    result["camera"]["intrinsics"]["fy_px"] *= 1.8
            return result

    adapter = ChangedCameraAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", f"portable-camera-{change}", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs[f"portable-camera-{change}"]
    assert job["status"] == "failed"
    assert job["error"] == "view_changed_during_recognition"
    assert adapter.operations == ["capture", "build_map", "capture"]
    assert "navigate" not in adapter.operations and "stop" not in adapter.operations


def test_current_depth_surface_change_is_rejected_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChangedSurfaceAdapter(AlternateEngineAdapter):
        async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = dict(await super().capture(request))
            if self.operations.count("capture") == 2:
                for sample in result["depth"]["samples"]:
                    if sample["uv_norm"][0] < 0.70:
                        sample["distance_m"] = 5.0
                result["depth"]["samples_sha256"] = _canonical_sha256(
                    result["depth"]["samples"]
                )
            return result

    adapter = ChangedSurfaceAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start("青い椅子まで行って", "portable-surface-change", speak=False)
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs["portable-surface-change"]
    assert job["status"] == "failed"
    assert job["error"] == "target_changed_during_recognition"
    assert adapter.operations == ["capture", "build_map", "capture", "build_map", "ground"]
    assert "navigate" not in adapter.operations and "stop" not in adapter.operations


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [("uncertainty_radius_m", 0.36), ("confidence", 0.49)],
)
def test_uncertain_or_low_confidence_ground_result_never_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    unsafe_value: float,
) -> None:
    class UnsafeGroundAdapter(AlternateEngineAdapter):
        async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            result = dict(await super().ground(request))
            result[field] = unsafe_value
            return result

    adapter = UnsafeGroundAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)

    async def infer(_image: bytes, _text: str) -> dict[str, Any]:
        return {
            "intent": "approach",
            "visible": True,
            "label": "blue exhibit chair",
            "confidence": 0.93,
            "bbox": [0.35, 0.30, 0.65, 0.75],
        }

    service._infer = infer

    async def run() -> None:
        await service.start(
            "青い椅子まで行って",
            f"portable-ground-quality-{field}",
            speak=False,
        )
        assert service.task is not None
        await service.task

    asyncio.run(run())

    job = service.jobs[f"portable-ground-quality-{field}"]
    assert job["status"] == "failed"
    assert job["error"] == "portable_ground_uncertain"
    assert adapter.operations == ["capture", "build_map", "capture", "build_map", "ground"]
    assert "navigate" not in adapter.operations and "stop" not in adapter.operations


def test_portable_approach_uses_nearest_bounded_safe_standoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[float] = []
    expected_route = {"status": "planned", "waypoints_m": [[1.0, 1.0, 0.0]]}

    def fake_plan_path(
        _portable_map: Mapping[str, Any],
        _grounded: Mapping[str, Any],
        *,
        arrival_radius_m: float,
        minimum_clearance_m: float,
    ) -> dict[str, Any]:
        assert minimum_clearance_m == 0.1
        attempts.append(arrival_radius_m)
        if arrival_radius_m < 0.95:
            raise PortableNavigationCoreError("PORTABLE_CORE_NO_SAFE_ARRIVAL", "target")
        return expected_route

    monkeypatch.setattr("lumina_next.visual_navigation.plan_path", fake_plan_path)
    route, selected, rejected = plan_portable_approach(
        {}, {}, minimum_clearance_m=0.1,
        target_bbox_norm=[0.35, 0.30, 0.65, 0.75],
    )

    assert route is expected_route
    assert selected == 0.95
    assert rejected == [0.8, 0.85, 0.9]
    assert attempts == [0.8, 0.85, 0.9, 0.95]


def test_portable_wide_low_target_uses_farthest_bounded_safe_standoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[float] = []
    expected_route = {"status": "planned", "waypoints_m": [[1.2, 0.0, 0.0]]}

    def fake_plan_path(
        _portable_map: Mapping[str, Any],
        _grounded: Mapping[str, Any],
        *,
        arrival_radius_m: float,
        minimum_clearance_m: float,
    ) -> dict[str, Any]:
        assert minimum_clearance_m == 0.1
        attempts.append(arrival_radius_m)
        return expected_route

    wide_low_bbox = [0.0, 0.72, 1.0, 0.90]
    assert portable_wide_low_target_needs_visibility_standoff(wide_low_bbox) is True
    monkeypatch.setattr("lumina_next.visual_navigation.plan_path", fake_plan_path)

    route, selected, rejected = plan_portable_approach(
        {}, {}, minimum_clearance_m=0.1, target_bbox_norm=wide_low_bbox
    )

    assert route is expected_route
    assert selected == 1.2
    assert rejected == []
    assert attempts == [0.8, 1.2]
    assert route["visibility_standoff_connectivity_guard_radius_m"] == 0.8


def test_portable_wide_low_standoff_cannot_bypass_disconnected_near_goals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[float] = []

    def disconnected_near_but_loose_radius_looks_reachable(
        _portable_map: Mapping[str, Any],
        _grounded: Mapping[str, Any],
        *,
        arrival_radius_m: float,
        minimum_clearance_m: float,
    ) -> dict[str, Any]:
        assert minimum_clearance_m == 0.1
        attempts.append(arrival_radius_m)
        if arrival_radius_m == 0.8:
            raise PortableNavigationCoreError("PORTABLE_CORE_NO_PATH", "wall")
        return {"status": "planned", "waypoints_m": [[0.0, 0.0, 0.0]]}

    monkeypatch.setattr(
        "lumina_next.visual_navigation.plan_path",
        disconnected_near_but_loose_radius_looks_reachable,
    )
    with pytest.raises(PortableNavigationCoreError) as caught:
        plan_portable_approach(
            {},
            {},
            minimum_clearance_m=0.1,
            target_bbox_norm=[0.0, 0.72, 1.0, 0.90],
        )

    assert caught.value.code == "PORTABLE_CORE_NO_PATH"
    assert attempts == [0.8]


def test_portable_visibility_standoff_keeps_safety_thresholds_unchanged() -> None:
    assert PORTABLE_APPROACH_RADIUS_CANDIDATES_M == (
        0.8, 0.85, 0.9, 0.95, 1.0, 1.1, 1.2,
    )
    assert ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M == 0.20
    assert RECOGNITION_REFERENCE_SURFACE_MAX_DISTANCE_M == 0.20
    assert ARRIVAL_REVALIDATION_TOLERANCE_M == 0.08
    assert portable_wide_low_target_needs_visibility_standoff(
        [0.35, 0.30, 0.65, 0.75]
    ) is False


def test_portable_approach_never_expands_a_disconnected_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[float] = []

    def disconnected(
        _portable_map: Mapping[str, Any],
        _grounded: Mapping[str, Any],
        *,
        arrival_radius_m: float,
        minimum_clearance_m: float,
    ) -> dict[str, Any]:
        attempts.append(arrival_radius_m)
        raise PortableNavigationCoreError("PORTABLE_CORE_NO_PATH", "disconnected")

    monkeypatch.setattr("lumina_next.visual_navigation.plan_path", disconnected)
    with pytest.raises(PortableNavigationCoreError) as caught:
        plan_portable_approach({}, {}, minimum_clearance_m=0.1)

    assert caught.value.code == "PORTABLE_CORE_NO_PATH"
    assert attempts == [0.8]
