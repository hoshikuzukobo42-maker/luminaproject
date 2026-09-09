"""Opt-in integration of the pose-epoch arrival fast path (stage 2).

These E2E tests drive the real ``VisualNavigation`` portable job body with the
alternate-engine adapters and prove: with the flag off nothing changes; a
``pass`` skips Qwen, the model wake and the inference lease; a
``fallback_eligible`` runs the unchanged semantic revalidation exactly once on
frames newer than the fast path's newest frame; a ``hard_fail`` reaches neither;
cancellation, adapter timeouts and resource trips propagate unchanged.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from lumina_next.arrival_pose_epoch_fast_path_v1 import (
    ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
    INITIAL_CERTIFICATION_REVISION,
    compute_initial_certification_digest,
)
from lumina_next.portable_navigation_contract_v1 import (
    PortableNavigationContractError,
    validate_capture_result,
    validate_map_request,
    validate_map_result,
)
from lumina_next.portable_navigation_core_v1 import build_occupancy_map
from lumina_next.visual_navigation import (
    ARRIVAL_REVALIDATION_REVISION,
    NavigationError,
    VisualNavigation,
    arrival_fast_path_enabled,
)
from lumina_next.visual_resource_budget import VisualResourceError
from tests.test_visual_navigation_arrival_revalidation_20260907 import (
    SURFACE_ID,
    SurfaceIdentityAdapter,
    SurfaceRoleResultAdapter,
    _run_job,
    _visible_detection,
)
from tests.test_visual_navigation_portable_v1 import (
    AlternateEngineAdapter,
    _canonical_sha256,
    _portable_service,
)

FLAG = "LUMINA_ARRIVAL_POSE_EPOCH_FAST_PATH"
LEGACY_OPERATIONS = [
    "capture", "build_map", "capture", "build_map", "ground", "navigate", "feedback",
    "capture", "build_map", "capture", "build_map", "ground",
]
FAST_PATH_PREFIX = [
    "capture", "build_map", "capture", "build_map", "ground", "navigate", "feedback",
    "capture", "capture", "build_map",
]
SLOW_PATH_SUFFIX = ["capture", "build_map", "capture", "build_map", "ground"]


def _fast_path_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter: AlternateEngineAdapter,
) -> VisualNavigation:
    service = _portable_service(tmp_path, monkeypatch, adapter)
    monkeypatch.setenv(FLAG, "1")
    assert arrival_fast_path_enabled() is True
    return service


def _sofa_detection() -> dict[str, Any]:
    # ``sofa`` is in the validated obstacle allowlist, so the job records
    # ``expected_surface_role == navigation_obstacle`` (required for a pass).
    return {
        "intent": "approach",
        "visible": True,
        "label": "purple sofa",
        "confidence": .93,
        "bbox": [.35, .30, .65, .75],
    }


def _install_infer(
    service: VisualNavigation,
    detection: dict[str, Any] | None = None,
) -> list[tuple[bytes, str]]:
    calls: list[tuple[bytes, str]] = []
    payload = detection or _sofa_detection()

    async def infer(image: bytes, text: str) -> dict[str, Any]:
        calls.append((image, text))
        return dict(payload)

    service._infer = infer
    return calls


def _count_lease_entries(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from lumina_next import visual_navigation as module

    entries: list[int] = []

    @asynccontextmanager
    async def counting_lease():
        entries.append(1)
        yield

    monkeypatch.setattr(module, "inference_lease", counting_lease)
    return entries


def _count_wakes(service: VisualNavigation) -> list[str]:
    wakes: list[str] = []

    async def wake(job: dict, **kwargs: Any) -> None:
        wakes.append(kwargs.get("audit_field", "model_wake"))

    service._wake_model = wake
    return wakes


def _requests(adapter: AlternateEngineAdapter, operation: str) -> list[dict[str, Any]]:
    return [
        request
        for recorded, request in zip(adapter.operations, adapter.requests)
        if recorded == operation
    ]


class ReloadedFrameAdapter(SurfaceRoleResultAdapter):
    """Report a different coordinate frame for every post-arrival capture."""

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        if self.operations.count("capture") >= 3:
            result["camera"]["coordinate_frame_id"] = "world:alternate-engine-reloaded"
            result = validate_capture_result(result, request=request, now_unix=result["captured_unix"])
            self.captures[result["frame_id"]] = result
        return result


class RoleFlippingAdapter(SurfaceRoleResultAdapter):
    """Keep the same opaque id but flip its role in post-arrival frames."""

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        if self.operations.count("capture") >= 3:
            for sample in result["depth"]["samples"]:
                if "surface_role" in sample:
                    sample["surface_role"] = "walkable_ground"
            result["depth"]["samples_sha256"] = _canonical_sha256(result["depth"]["samples"])
            result = validate_capture_result(result, request=request, now_unix=result["captured_unix"])
            self.captures[result["frame_id"]] = result
        return result


class LowConfidenceArrivalAdapter(SurfaceRoleResultAdapter):
    """Drop one target ray below the 0.25 confidence floor in the fast-path frames.

    Only the third and fourth captures (the fast path's two fresh frames) are
    affected, so the unchanged slow path afterwards sees ordinary frames.
    """

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = copy.deepcopy(await super().capture(request))
        if self.operations.count("capture") in {3, 4}:
            result["depth"]["samples"][0]["confidence"] = 0.249999
            result["depth"]["samples_sha256"] = _canonical_sha256(result["depth"]["samples"])
            result = validate_capture_result(result, request=request, now_unix=result["captured_unix"])
            self.captures[result["frame_id"]] = result
        return result


class StaleMapLineageAdapter(SurfaceRoleResultAdapter):
    """Answer the fast path's map request with a map built from another frame."""

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.operations.count("build_map") == 2 and self.operations.count("capture") == 4:
            # Third map = the fast path's fresh map.  Point it at the previous
            # frame: request/result lineage mismatch must be a contract failure.
            rewritten = dict(request, source_frame_id=list(self.captures)[-2])
            return await super().build_map(rewritten)
        return await super().build_map(request)


class TimeoutAfterArrivalAdapter(SurfaceRoleResultAdapter):
    """Reject every capture after the arrived feedback (sensor outage)."""

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if "feedback" in self.operations:
            self._record("capture", request)
            raise PortableNavigationContractError("PORTABLE_GODOT_FRAME_UNAVAILABLE", "latest.json")
        return await super().capture(request)


class ResourceTripAdapter(SurfaceRoleResultAdapter):
    """Model a resource trip surfacing while the fast path builds its map."""

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.operations.count("build_map") == 2 and self.operations.count("capture") == 4:
            raise VisualResourceError("visual_resource_budget_tripped")
        return await super().build_map(request)


class FinalMapIntrusionAdapter(LowConfidenceArrivalAdapter):
    """Fast map clean, then an obstacle appears at the agent for the post-Qwen map.

    Map order in a fallback run: recognition, navigation, fast path, semantic,
    final.  Only the fifth map (the slow path's ``final_map``) gains an
    obstacle on the agent's own cell.
    """

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.operations.count("build_map") != 4:
            return await super().build_map(request)
        normalized = validate_map_request(request)
        self._record("build_map", normalized)
        result = build_occupancy_map(
            normalized,
            bounds_xy=[-1.0, 5.0, -3.0, 3.0],
            obstacles=[
                {"kind": "rect", "center_m": [2.0, 0.0], "half_extents_m": [0.2, 0.8]},
                {"kind": "rect", "center_m": [3.3, 0.0], "half_extents_m": [0.1, 0.1]},
            ],
            agent_position_m=[3.3, 0.0, 0.0],
            created_unix=normalized["requested_unix"],
        )
        result["coordinate_frame_id"] = "world:alternate-engine-test"
        self.map_result = validate_map_result(result, request=normalized, now_unix=normalized["requested_unix"])
        self.maps[self.map_result["map_id"]] = self.map_result
        return self.map_result


class MovingAtArrivalAdapter(SurfaceRoleResultAdapter):
    """Report ``arrived`` while the agent is still moving."""

    async def feedback(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(await super().feedback(request))
        result["velocity_mps"] = [0.2, 0.0, 0.0]
        return result


class BlockingArrivalCaptureAdapter(SurfaceRoleResultAdapter):
    """Hang the first post-arrival capture so a cancellation can land."""

    def __init__(self) -> None:
        super().__init__()
        self.arrival_capture_entered = asyncio.Event()

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if "feedback" in self.operations:
            self._record("capture", request)
            self.arrival_capture_entered.set()
            await asyncio.Event().wait()
        return await super().capture(request)


# --------------------------------------------------------------------------


def test_flag_off_keeps_existing_arrival_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    monkeypatch.delenv(FLAG, raising=False)
    assert arrival_fast_path_enabled() is False
    infer_calls = _install_infer(service)
    lease_entries = _count_lease_entries(monkeypatch)

    asyncio.run(_run_job(service, "fast-path-flag-off"))

    job = service.jobs["fast-path-flag-off"]
    assert job["status"] == "arrived", job
    assert job["arrival_fast_path_enabled"] is False
    assert "arrival_fast_path" not in job
    assert "arrival_fast_path_certification_recorded" not in job
    assert service._arrival_certifications == {}
    assert len(infer_calls) == 2
    assert len(lease_entries) == 2
    assert adapter.operations == LEGACY_OPERATIONS
    assert job["arrival_revalidation"]["status"] == "passed"
    status = service.status()
    assert status["arrival_fast_path_enabled"] is False
    assert status["arrival_fast_path_revision"] is None
    assert status["arrival_fast_path_last_outcome"] is None


def test_flag_value_other_than_1_stays_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _portable_service(tmp_path, monkeypatch, adapter)
    monkeypatch.setenv(FLAG, "true")
    assert arrival_fast_path_enabled() is False
    _install_infer(service)
    asyncio.run(_run_job(service, "fast-path-flag-true-string"))
    assert adapter.operations == LEGACY_OPERATIONS
    assert "arrival_fast_path" not in service.jobs["fast-path-flag-true-string"]


def test_flag_is_sampled_once_per_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Flag on at start, switched off while the job is moving: the running job
    # keeps its fast path.  Flag off at start, switched on mid-job: the running
    # job never records a certification nor starts fast-path captures.
    class FlagFlippingAdapter(SurfaceRoleResultAdapter):
        def __init__(self, value: str | None) -> None:
            super().__init__()
            self.value = value

        async def navigate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
            if self.value is None:
                monkeypatch.delenv(FLAG, raising=False)
            else:
                monkeypatch.setenv(FLAG, self.value)
            return await super().navigate(request)

    adapter = FlagFlippingAdapter(None)
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    asyncio.run(_run_job(service, "fast-path-flag-on-then-off"))
    job = service.jobs["fast-path-flag-on-then-off"]
    assert job["status"] == "arrived", job
    assert job["arrival_fast_path_enabled"] is True
    assert job["arrival_fast_path"]["outcome"] == "pass"
    assert adapter.operations == FAST_PATH_PREFIX
    assert len(infer_calls) == 1

    adapter = FlagFlippingAdapter("1")
    service = _portable_service(tmp_path, monkeypatch, adapter)
    monkeypatch.delenv(FLAG, raising=False)
    infer_calls = _install_infer(service)
    asyncio.run(_run_job(service, "fast-path-flag-off-then-on"))
    job = service.jobs["fast-path-flag-off-then-on"]
    assert job["status"] == "arrived", job
    assert job["arrival_fast_path_enabled"] is False
    assert "arrival_fast_path" not in job
    assert "fast-path-flag-off-then-on" not in service._arrival_certifications
    assert adapter.operations == LEGACY_OPERATIONS
    assert len(infer_calls) == 2


def test_geometry_only_adapter_records_geometry_only_certification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No adapter surface ids at all (exhibit-chair detection keeps the
    # existing grounding path).  The certification is geometry-only, so the
    # fast path checks fresh safety evidence before falling back to Qwen.
    adapter = AlternateEngineAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service, _visible_detection())
    request_id = "fast-path-geometry-only"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "arrived", job
    assert job["arrival_fast_path_certification_identity_basis"] == "metric_geometry_only"
    assert job["arrival_fast_path_certification_role_present"] is False
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "fallback_eligible"
    assert audit["reason"] == "certification_surface_identity_unavailable"
    assert audit["fresh_capture_count"] == 2
    assert audit["fresh_map_count"] == 1
    assert adapter.operations == FAST_PATH_PREFIX + SLOW_PATH_SUFFIX
    assert len(infer_calls) == 2


def test_fast_path_pass_confirms_arrival_without_qwen_wake_or_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    lease_entries = _count_lease_entries(monkeypatch)
    wakes = _count_wakes(service)
    request_id = "fast-path-pass"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "arrived", job
    assert job["arrived"] is True
    assert job["arrival_fast_path_enabled"] is True
    assert job["arrival_fast_path_certification_recorded"] is True
    assert job["arrival_fast_path_certification_identity_basis"] == "opaque_physics_surface_id"
    assert job["arrival_fast_path_certification_role_present"] is True
    # Recognition only: one Qwen call, one lease entry, zero wakes (phase off).
    assert len(infer_calls) == 1
    assert len(lease_entries) == 1
    assert wakes == []
    assert adapter.operations == FAST_PATH_PREFIX
    assert adapter.operations.count("ground") == 1
    assert "stop" not in adapter.operations

    audit = job["arrival_fast_path"]
    assert audit["revision"] == ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION
    assert audit["outcome"] == "pass"
    assert audit["status"] == "pass"
    assert audit["semantic_revalidation_performed"] is False
    assert audit["semantic_change_detectable"] is False
    assert audit["qwen_invoked"] is False
    assert audit["inference_lease_held"] is False
    assert audit["model_wake_performed"] is False
    assert audit["fresh_capture_count"] == 2
    assert audit["fresh_map_count"] == 1
    assert audit["fresh_frame_ids"] == ["frame:alternate-3", "frame:alternate-4"]
    assert audit["fresh_map_source_frame_id"] == "frame:alternate-4"
    first_unix, second_unix = audit["fresh_frame_captured_unix"]
    assert second_unix > first_unix > audit["cutoff_unix"]
    assert audit["cutoff_unix"] >= job["last_feedback"]["observed_unix"]
    assert audit["cutoff_unix"] >= job["grounding_captured_unix"]
    assert audit["checks"]["map_lineage_match"] is True
    assert audit["checks"]["map_policy_match"] is True
    assert audit["checks"]["agent_cell_free_after_additional_inflation"] is True
    assert audit["inflated_occupancy_proof"]["kind"] == "binary_inflated_occupancy_proof"
    assert audit["inflated_occupancy_proof"]["numeric_clearance_measured"] is False
    assert audit["checks"]["agent_stationary"] is True
    assert audit["checks"]["agent_position_consistent_with_feedback"] is True
    assert audit["checks"]["feedback_environment_session_match"] is True
    assert audit["checks"]["feedback_collision_free"] is True
    assert audit["checks"]["feedback_clearance_ok"] is True
    assert audit["checks"]["cutoff_not_before_feedback"] is True
    assert audit["checks"]["expected_and_actual_role_match_required_role"] is True
    assert all(frame["exact_reference_ray_count"] >= 3 for frame in audit["frames"])
    assert audit["arrival_distance_m"] == pytest.approx(0.7, abs=0.02)
    assert audit["arrival_distance_m"] <= audit["maximum_allowed_distance_m"]
    assert audit["maximum_allowed_distance_m"] == pytest.approx(
        job["selected_approach_radius_m"] + audit["arrival_tolerance_m"]
    )
    assert job["arrival_distance_m"] == audit["arrival_distance_m"]
    assert job["arrival_confirmation_basis"] == (
        "pose_epoch_fast_path_dual_fresh_rgbd_exact_surface"
    )
    revalidation = job["arrival_revalidation"]
    assert revalidation["revision"] == ARRIVAL_REVALIDATION_REVISION
    assert revalidation["status"] == "skipped"
    assert revalidation["skipped_by"] == ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION
    assert revalidation["semantic_revalidation_performed"] is False

    # The fresh map request is bound to the newest fresh frame with the fixed policy.
    fast_map_request = _requests(adapter, "build_map")[-1]
    assert fast_map_request["source_frame_id"] == "frame:alternate-4"
    assert fast_map_request["resolution_m"] == 0.20
    assert fast_map_request["footprint_radius_m"] == 0.35

    # The certification exists privately, is read-only and validates.
    certification = service._arrival_certifications[request_id]
    assert isinstance(certification, Mapping)
    with pytest.raises(TypeError):
        certification["surface_id"] = "surface:" + "d" * 64  # type: ignore[index]
    assert certification["certification_revision"] == INITIAL_CERTIFICATION_REVISION
    assert certification["surface_id"] == SURFACE_ID
    assert certification["surface_role"] == "navigation_obstacle"
    assert certification["expected_surface_role"] == job["expected_surface_role"] == "navigation_obstacle"
    assert certification["dual_grounding_surface_role_match"] is True
    assert certification["dual_grounding_semantics_revision_match"] is True
    assert certification["environment_id"] == service.portable_environment_id
    assert certification["session_id"] == service.portable_session_id
    assert certification["semantic_frame_id"] == job["frame_id"]
    assert certification["post_inference_frame_id"] == job["grounding_frame_id"]
    assert certification["navigation_map_id"] == job["map_id"]
    assert certification["semantic_map_id"] == job["initial_semantic_map_id"]
    assert certification["dual_grounding_surface_id_match"] is True
    assert compute_initial_certification_digest(certification) == certification["certification_sha256"]
    # ...but the opaque id never reaches the job, the status, or the audit.
    assert SURFACE_ID not in str(job)
    status = service.status()
    assert SURFACE_ID not in json.dumps(status, default=str)
    assert status["arrival_fast_path_enabled"] is True
    assert status["arrival_fast_path_revision"] == ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION
    assert status["arrival_fast_path_last_outcome"] == "pass"
    assert status["arrival_fast_path_last_semantic_revalidation_performed"] is False
    serialized_requests = json.dumps(adapter.requests, ensure_ascii=False)
    assert adapter.native_node_name not in serialized_requests
    assert "target_node" not in serialized_requests
    assert "instance_id" not in serialized_requests


def test_fast_path_fallback_runs_unchanged_slow_path_once_on_newer_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lumina_next import visual_navigation as module

    adapter = LowConfidenceArrivalAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    lease_entries = _count_lease_entries(monkeypatch)
    slow_calls: list[dict[str, Any]] = []
    real_slow_path = module.VisualNavigation._portable_revalidate_arrival

    async def counting_slow_path(self, job, feedback, previous_capture, *, arrival_radius):
        slow_calls.append({"previous_capture": previous_capture, "arrival_radius": arrival_radius})
        return await real_slow_path(self, job, feedback, previous_capture, arrival_radius=arrival_radius)

    monkeypatch.setattr(module.VisualNavigation, "_portable_revalidate_arrival", counting_slow_path)
    request_id = "fast-path-fallback"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "arrived", job
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "fallback_eligible"
    assert audit["reason"] == "insufficient_exact_reference_rays"
    assert audit["frames"][0]["low_confidence_rays_excluded"] == 1
    assert audit["qwen_fallback_allowed"] is True
    assert audit["semantic_revalidation_performed"] is False
    assert adapter.operations == FAST_PATH_PREFIX + SLOW_PATH_SUFFIX
    assert len(infer_calls) == 2
    assert infer_calls[1][1] == "revalidate unique visible category: sofa"
    assert len(lease_entries) == 2
    assert len(slow_calls) == 1
    # The slow path was seeded with the fast path's newest frame and captured
    # a strictly newer semantic frame.
    assert slow_calls[0]["previous_capture"]["frame_id"] == audit["fresh_frame_ids"][1]
    assert slow_calls[0]["arrival_radius"] == job["selected_approach_radius_m"]
    revalidation = job["arrival_revalidation"]
    assert revalidation["status"] == "passed"
    assert "skipped_by" not in revalidation
    assert revalidation["semantic_captured_unix"] > audit["fresh_frame_captured_unix"][1]
    assert revalidation["semantic_frame_id"] not in audit["fresh_frame_ids"]
    # The slow path's final map carries the same binary inflated-occupancy proof.
    final_proof = revalidation["final_map_inflated_occupancy_proof"]
    assert final_proof["kind"] == "binary_inflated_occupancy_proof"
    assert final_proof["additional_inflation_m"] == 0.10
    assert final_proof["numeric_clearance_measured"] is False
    assert final_proof["agent_cell_free"] is True
    assert "arrival_confirmation_basis" not in job
    assert service.status()["arrival_fast_path_last_outcome"] == "fallback_eligible"


def test_missing_expected_role_checks_safety_before_semantic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "exhibit chair" is not in the validated category allowlist, so the job
    # has no expected_surface_role even though the adapter grounds an obstacle.
    adapter = SurfaceRoleResultAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service, _visible_detection())
    request_id = "fast-path-expected-role-missing"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "arrived", job
    assert job["expected_surface_role"] is None
    certification = service._arrival_certifications[request_id]
    assert certification["expected_surface_role"] is None
    assert certification["surface_role"] == "navigation_obstacle"
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "fallback_eligible"
    assert audit["reason"] == "expected_surface_role_unavailable"
    assert audit["fresh_capture_count"] == 2
    assert audit["fresh_map_count"] == 1
    assert audit["checks"]["agent_stationary"] is True
    assert audit["checks"]["agent_cell_free_after_additional_inflation"] is True
    assert adapter.operations == FAST_PATH_PREFIX + SLOW_PATH_SUFFIX
    assert len(infer_calls) == 2
    assert job["arrival_revalidation"]["status"] == "passed"
    assert "arrival_confirmation_basis" not in job


def test_final_map_intrusion_after_qwen_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = FinalMapIntrusionAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    request_id = "fast-path-final-map-intrusion"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "failed", job
    assert job["arrived"] is False
    assert job["error"] == "arrival_final_map_agent_blocked_after_inflation"
    # The fast path saw a clean map and deferred (low-confidence ray); the
    # obstacle appeared only for the post-Qwen final map.
    fast = job["arrival_fast_path"]
    assert fast["outcome"] == "fallback_eligible"
    assert fast["checks"]["agent_cell_free_after_additional_inflation"] is True
    revalidation = job["arrival_revalidation"]
    assert revalidation["status"] == "failed"
    proof = revalidation["final_map_inflated_occupancy_proof"]
    assert proof["kind"] == "binary_inflated_occupancy_proof"
    assert proof["agent_cell_free"] is False
    assert proof["reason"] == "blocked"
    assert proof["numeric_clearance_measured"] is False
    assert len(infer_calls) == 2
    assert adapter.operations.count("build_map") == 5
    # No arrival grounding happened after the blocked final map.
    assert adapter.operations.count("ground") == 1
    assert "stop" in adapter.operations
    assert "arrival_distance_m" not in job


def test_moving_agent_at_arrival_is_hard_fail_without_qwen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MovingAtArrivalAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    request_id = "fast-path-moving-feedback"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "failed", job
    assert job["error"] == "arrival_fast_path_hard_fail:agent_not_stationary"
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "hard_fail"
    assert audit["arrived_feedback_speed_mps"] == pytest.approx(0.2)
    assert audit["qwen_fallback_allowed"] is False
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_missing_expected_role_does_not_hide_moving_feedback_hard_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MovingAtArrivalAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service, _visible_detection())
    request_id = "fast-path-missing-role-moving-feedback"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["expected_surface_role"] is None
    assert job["status"] == "failed", job
    assert job["error"] == "arrival_fast_path_hard_fail:agent_not_stationary"
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "hard_fail"
    assert audit["reason"] == "agent_not_stationary"
    assert audit["fresh_capture_count"] == 2
    assert audit["fresh_map_count"] == 1
    assert audit["qwen_fallback_allowed"] is False
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_roleless_certification_checks_safety_before_semantic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Adapter surface ids without roles.  With an allowlisted category the
    # recognition grounding itself refuses roleless rays (ambiguous), so the
    # end-to-end roleless case reaches arrival only when no expected role was
    # recorded; fresh safety evidence is still required before fallback.
    adapter = SurfaceIdentityAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service, _visible_detection())
    request_id = "fast-path-roleless"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "arrived", job
    assert job["arrival_fast_path_certification_identity_basis"] == "opaque_physics_surface_id"
    assert job["arrival_fast_path_certification_role_present"] is False
    certification = service._arrival_certifications[request_id]
    assert certification["surface_id"] == SURFACE_ID
    assert certification["surface_role"] is None
    assert certification["expected_surface_role"] is None
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "fallback_eligible"
    assert audit["reason"] == "certification_surface_role_unavailable"
    assert audit["fresh_capture_count"] == 2
    assert audit["fresh_map_count"] == 1
    assert audit["checks"]["agent_stationary"] is True
    assert audit["checks"]["agent_cell_free_after_additional_inflation"] is True
    assert adapter.operations == FAST_PATH_PREFIX + SLOW_PATH_SUFFIX
    assert len(infer_calls) == 2
    assert job["arrival_revalidation"]["status"] == "passed"
    assert SURFACE_ID not in str(job)


def test_missing_private_certification_fails_closed_without_qwen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    service._record_arrival_certification = lambda *args, **kwargs: None  # type: ignore[method-assign]
    request_id = "fast-path-private-cert-missing"

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "failed", job
    assert job["error"] == "arrival_fast_path_hard_fail:contract_invalid"
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "hard_fail"
    assert audit["reason"] == "contract_invalid"
    assert audit["contract_error"] == "CERTIFICATION_UNAVAILABLE"
    assert audit["fresh_capture_count"] == 0
    assert audit["fresh_map_count"] == 0
    assert audit["qwen_fallback_allowed"] is False
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_allowlisted_category_with_roleless_rays_never_reaches_arrival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Documents why a certification with expected role present but grounded
    # role missing cannot be produced end-to-end: the existing recognition
    # grounding already fails closed before any navigation starts.
    adapter = SurfaceIdentityAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)

    asyncio.run(_run_job(service, "fast-path-roleless-allowlisted"))

    job = service.jobs["fast-path-roleless-allowlisted"]
    assert job["status"] == "ambiguous", job
    assert "navigate" not in adapter.operations
    assert "arrival_fast_path" not in job
    assert "fast-path-roleless-allowlisted" not in service._arrival_certifications
    assert len(infer_calls) == 1


@pytest.mark.parametrize(
    ("adapter_factory", "expected_reason"),
    [
        (ReloadedFrameAdapter, "coordinate_frame_changed"),
        (RoleFlippingAdapter, "surface_role_contradiction"),
    ],
)
def test_fast_path_hard_fail_never_reaches_qwen_or_slow_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_factory: type[SurfaceRoleResultAdapter],
    expected_reason: str,
) -> None:
    adapter = adapter_factory()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    lease_entries = _count_lease_entries(monkeypatch)
    request_id = "fast-path-hard-fail-" + expected_reason

    asyncio.run(_run_job(service, request_id))

    job = service.jobs[request_id]
    assert job["status"] == "failed", job
    assert job["arrived"] is False
    assert job["error"] == "arrival_fast_path_hard_fail:" + expected_reason
    audit = job["arrival_fast_path"]
    assert audit["outcome"] == "hard_fail"
    assert audit["reason"] == expected_reason
    assert audit["qwen_fallback_allowed"] is False
    assert audit["semantic_revalidation_performed"] is False
    assert len(infer_calls) == 1
    assert len(lease_entries) == 1
    assert adapter.operations[:10] == FAST_PATH_PREFIX
    assert adapter.operations.count("ground") == 1
    assert "arrival_revalidation" not in job
    assert "arrival_distance_m" not in job
    assert "stop" in adapter.operations
    assert SURFACE_ID not in str(job)
    assert service.status()["arrival_fast_path_last_outcome"] == "hard_fail"


def test_map_lineage_violation_is_not_downgraded_to_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = StaleMapLineageAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)

    asyncio.run(_run_job(service, "fast-path-map-lineage"))

    job = service.jobs["fast-path-map-lineage"]
    assert job["status"] == "failed", job
    # The result/request lineage mismatch is caught by the shared contract
    # validator before the evaluator runs; the fast path propagates it.
    assert job["error"] == "portable_lineage_mismatch"
    assert job["arrival_fast_path"]["status"] == "failed"
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_adapter_timeout_after_arrival_fails_closed_without_qwen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lumina_next import visual_navigation as module

    adapter = TimeoutAfterArrivalAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)
    # Shrink the capture retry deadline so the outage is observed quickly.
    real_monotonic = module.time.monotonic
    ticks = {"count": 0}

    def fast_monotonic() -> float:
        ticks["count"] += 1
        return real_monotonic() + ticks["count"] * 2.0

    monkeypatch.setattr(module.time, "monotonic", fast_monotonic)

    asyncio.run(_run_job(service, "fast-path-adapter-timeout"))

    job = service.jobs["fast-path-adapter-timeout"]
    assert job["status"] == "failed", job
    assert job["error"] == "portable_godot_frame_unavailable"
    assert job["arrival_fast_path"]["status"] == "failed"
    assert job["arrival_fast_path"]["fresh_capture_count"] == 0
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_resource_trip_during_fast_path_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ResourceTripAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)

    asyncio.run(_run_job(service, "fast-path-resource-trip"))

    job = service.jobs["fast-path-resource-trip"]
    assert job["status"] == "failed", job
    assert job["arrival_fast_path"]["status"] == "failed"
    assert job["arrival_fast_path"]["fresh_capture_count"] == 2
    assert job["arrival_fast_path"]["fresh_map_count"] == 0
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job


def test_cancellation_during_fast_path_capture_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = BlockingArrivalCaptureAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    infer_calls = _install_infer(service)

    async def run() -> None:
        await service.start("左の青い椅子まで行って", "fast-path-cancel", speak=False)
        await asyncio.wait_for(adapter.arrival_capture_entered.wait(), timeout=10)
        assert await service.cancel("fast-path-cancel") is True
        assert service.task is None

    asyncio.run(run())

    job = service.jobs["fast-path-cancel"]
    assert job["status"] == "cancelled", job
    assert job["arrived"] is False
    assert job["arrival_fast_path"]["status"] == "failed"
    assert len(infer_calls) == 1
    assert "arrival_revalidation" not in job
    assert "stop" in adapter.operations


def test_second_fresh_capture_is_always_newer_than_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SurfaceRoleResultAdapter()
    service = _fast_path_service(tmp_path, monkeypatch, adapter)
    _install_infer(service)

    asyncio.run(_run_job(service, "fast-path-order"))

    audit = service.jobs["fast-path-order"]["arrival_fast_path"]
    first_unix, second_unix = audit["fresh_frame_captured_unix"]
    assert second_unix > first_unix
    capture_requests = _requests(adapter, "capture")
    assert len(capture_requests) == 4
    assert capture_requests[3]["requested_unix"] >= capture_requests[2]["requested_unix"]
    assert all(request["require_depth"] is True for request in capture_requests[2:])
