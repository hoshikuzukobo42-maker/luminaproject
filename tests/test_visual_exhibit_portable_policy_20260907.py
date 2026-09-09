from __future__ import annotations

import importlib.util
import hashlib
import json
import time
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_lumina_visual_exhibit_mac.py"


def _checker_module():
    spec = importlib.util.spec_from_file_location("lumina_visual_exhibit_checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_checker_requires_portable_open_vocab_policy() -> None:
    checker = _checker_module()
    assert checker.portable_selection_policy_ready({
        "portable_navigation_enabled": True,
        "selection_policy_revision": checker.PORTABLE_SELECTION_POLICY_REVISION,
        "grounding_policy_revision": checker.PORTABLE_GROUNDING_POLICY_REVISION,
    }) is True
    assert checker.portable_selection_policy_ready({
        "portable_navigation_enabled": True,
        "selection_policy_revision": "closed_ja_inventory_selection_v1",
        "grounding_policy_revision": checker.PORTABLE_GROUNDING_POLICY_REVISION,
    }) is False
    assert checker.portable_selection_policy_ready({
        "portable_navigation_enabled": False,
        "selection_policy_revision": checker.PORTABLE_SELECTION_POLICY_REVISION,
        "grounding_policy_revision": checker.PORTABLE_GROUNDING_POLICY_REVISION,
    }) is False
    assert checker.portable_selection_policy_ready({
        "portable_navigation_enabled": True,
        "selection_policy_revision": checker.PORTABLE_SELECTION_POLICY_REVISION,
        "grounding_policy_revision": "rgbd_bbox_occluder_bilateral_surface_region_continuity_v5",
    }) is False


def test_candidate_checker_uses_internal_capture_spool_by_default(monkeypatch) -> None:
    checker = _checker_module()
    monkeypatch.delenv("LUMINA_EYE_CAPTURE_DIR", raising=False)
    assert checker.configured_capture_directory({
        "capture_directory": str(checker.DEFAULT_CAPTURE_DIRECTORY),
    }) == checker.DEFAULT_CAPTURE_DIRECTORY


def test_candidate_checker_requires_tts_shared_epoch_enforcement_policy() -> None:
    checker = _checker_module()
    phase = {
        "enabled": True,
        "lock_path": str(checker.PHASE_LOCK),
        "idle": True,
        "outstanding_workers": 0,
        "waiting_workers": 0,
        "active_workers": 0,
        "minimum_start_free_percent": 40,
        "minimum_free_percent": 22,
        "maximum_swap_growth_mib": 256,
        "baseline_scope": "engine_lifetime_first_serialized_worker",
        "native_abort_supported": False,
        "enforcement_revision": "shared_epoch_authoritative_local_telemetry_v2",
        "enforcement_scope": "shared_exhibition_epoch",
        "local_lifetime_measurement_only": True,
        "start_memory_recovery_revision": "shared_epoch_bounded_start_memory_recovery_v1",
        "start_memory_recovery_enabled": True,
        "start_memory_recovery_wait_seconds": 60.0,
        "start_memory_recovery_poll_seconds": 1.0,
        "blocked_reason": None,
        "max_chunk_chars": 16,
        "chunking_policy": "punctuation_first_hard_cap",
        "chunk_resource_checkpoint": True,
        "idle_residency_policy": "unload_after_worker",
    }
    checker.check_phase(phase, tts=True, required=True)
    for field, old_value in (
        ("enforcement_revision", "legacy_local_lifetime_v1"),
        ("enforcement_scope", "engine_lifetime_first_serialized_worker"),
        ("local_lifetime_measurement_only", False),
        ("start_memory_recovery_revision", "immediate_start_free_latch_v0"),
        ("start_memory_recovery_enabled", False),
        ("start_memory_recovery_wait_seconds", 61.0),
        ("start_memory_recovery_poll_seconds", 2.0),
    ):
        candidate = dict(phase, **{field: old_value})
        with pytest.raises(ValueError, match="shared resource enforcement policy"):
            checker.check_phase(candidate, tts=True, required=True)


def test_irodori_starter_requires_same_tts_enforcement_attestation() -> None:
    source = (SCRIPT.parent / "start_irodori_product_5088_mac.sh").read_text()
    assert 'phase.get("enforcement_revision") == "shared_epoch_authoritative_local_telemetry_v2"' in source
    assert 'phase.get("enforcement_scope") == "shared_exhibition_epoch"' in source
    assert 'phase.get("local_lifetime_measurement_only") is True' in source
    assert 'phase.get("start_memory_recovery_revision") == "shared_epoch_bounded_start_memory_recovery_v1"' in source
    assert 'phase.get("start_memory_recovery_enabled") is True' in source
    assert 'phase.get("start_memory_recovery_wait_seconds") == 60.0' in source
    assert 'phase.get("start_memory_recovery_poll_seconds") == 1.0' in source


def test_frame_requires_dedicated_active_eye_camera_attestation(tmp_path: Path) -> None:
    checker = _checker_module()
    image = b"\x89PNG\r\n\x1a\nfixture"
    image_path = tmp_path / "eye.png"
    image_path.write_bytes(image)
    metadata = {
        "captured_unix": time.time(),
        "image_path": str(image_path),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "pose_source": "animated_eye_bones",
        "render_source": "dedicated_eye_subviewport",
        "render_viewport_instance_id": 40,
        "render_camera_instance_id": 41,
        "active_camera_instance_id": 41,
        "camera_position": {"x": 0.0, "y": 1.4, "z": 0.0},
        "camera_forward": {"x": 0.0, "y": 0.0, "z": 1.0},
        "frame_id": "7-1",
        "portable_depth": {
            "available": True,
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": "registered_normalized_to_rgb",
            "surface_id_semantics_revision": (
                "single_connected_component_world_pose_epoch_v2"
            ),
            "attempted_ray_count": 1,
            "valid_ray_count": 1,
            "samples": [{
                "uv_norm": [0.5, 0.5],
                "distance_m": 2.0,
                "confidence": 1.0,
                "surface_id": "surface:" + "a" * 64,
            }],
        },
    }
    (tmp_path / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert checker.verify_frame(tmp_path)["frame_id"] == "7-1"

    for invalid_semantics in (None, "legacy_body_identity_v1"):
        candidate = json.loads(json.dumps(metadata))
        if invalid_semantics is None:
            candidate["portable_depth"].pop("surface_id_semantics_revision")
        else:
            candidate["portable_depth"]["surface_id_semantics_revision"] = invalid_semantics
        (tmp_path / "latest.json").write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(ValueError, match="surface identity semantics"):
            checker.verify_frame(tmp_path)

    for invalid_surface_id in (None, "SofaBody:42", "surface:" + "A" * 64):
        candidate = json.loads(json.dumps(metadata))
        if invalid_surface_id is None:
            candidate["portable_depth"]["samples"][0].pop("surface_id")
        else:
            candidate["portable_depth"]["samples"][0]["surface_id"] = invalid_surface_id
        (tmp_path / "latest.json").write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(ValueError, match="surface identity"):
            checker.verify_frame(tmp_path)

    metadata["active_camera_instance_id"] = 42
    (tmp_path / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="attested eye camera"):
        checker.verify_frame(tmp_path)

    invalid_cases = [
        {"render_viewport_instance_id": None},
        {"render_viewport_instance_id": 0},
        {"render_camera_instance_id": True, "active_camera_instance_id": True},
        {"active_camera_instance_id": "41"},
        {"render_source": "main_viewport"},
    ]
    for changes in invalid_cases:
        candidate = dict(metadata)
        candidate["active_camera_instance_id"] = 41
        for key, value in changes.items():
            if value is None:
                candidate.pop(key, None)
            else:
                candidate[key] = value
        (tmp_path / "latest.json").write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(ValueError):
            checker.verify_frame(tmp_path)
