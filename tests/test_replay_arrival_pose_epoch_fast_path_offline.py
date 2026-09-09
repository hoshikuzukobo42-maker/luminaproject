"""Tests for the offline replay script of the arrival pose-epoch fast path.

A miniature spool is synthesized in ``tmp_path`` in the Godot spool layout
(``*.json`` metadata + ``*.png``).  The tests pin the properties the replay
report relies on: the recorded variant is the only evidence and fails contract
conversion when the sensor omits the identity semantics revision; windows
without two post-feedback frames are ``not_evaluable_no_post_feedback_pair``
and excluded from evidence and pass rate; diagnostic variants are labelled as
non-evidence; a fully compliant spool with a genuine post-feedback frame pair
produces a ``pass`` through the same evaluator the live integration uses; the
Markdown summary is rendered from the same report values.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import pytest

from lumina_next.portable_navigation_contract_v1 import SURFACE_ID_SEMANTICS_REVISION

SCRIPT = Path(__file__).parents[1] / "scripts" / "replay_arrival_pose_epoch_fast_path_offline.py"
PNG = b"\x89PNG\r\n\x1a\nreplay-fixture-rgb"
TARGET_SURFACE_ID = "surface:" + "e" * 64
FLOOR_SURFACE_ID = "surface:" + "f" * 64
NOT_EVALUABLE = "not_evaluable_no_post_feedback_pair"


def _module():
    spec = importlib.util.spec_from_file_location("replay_arrival_fast_path", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame_metadata(
    spool: Path,
    *,
    index: int,
    captured_unix: float,
    agent_z: float,
    revision: str | None,
    role: str | None,
    target_range_m: float,
    snapshot_lag_s: float,
) -> None:
    """Write one Godot-layout spool frame.

    Godot frame: camera at (0, 1.4, agent_z) looking toward +z; the target is a
    wall-like body ``target_range_m`` ahead at the image centre and the floor is
    hit by one lower ray.  ``snapshot_lag_s`` moves the embedded navigation
    snapshot (the arrived-feedback proxy) before the frame capture so a window
    can contain two post-feedback frames.
    """

    samples: list[dict[str, Any]] = []
    for u, v in ((0.48, 0.48), (0.50, 0.50), (0.52, 0.52), (0.46, 0.50), (0.54, 0.50)):
        sample: dict[str, Any] = {
            "uv_norm": [u, v],
            "distance_m": target_range_m,
            "confidence": 1.0,
            "surface_id": TARGET_SURFACE_ID,
        }
        if role is not None:
            sample["surface_role"] = role
        samples.append(sample)
    floor = {"uv_norm": [0.5, 0.95], "distance_m": 2.9, "confidence": 1.0, "surface_id": FLOOR_SURFACE_ID}
    if role is not None:
        floor["surface_role"] = "walkable_ground"
    samples.append(floor)
    depth: dict[str, Any] = {
        "available": True,
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "samples": samples,
        "attempted_ray_count": len(samples),
        "valid_ray_count": len(samples),
    }
    if revision is not None:
        depth["surface_id_semantics_revision"] = revision
    png_path = spool / f"lumina_binocular_eye_slot_{index:02d}.png"
    png_path.write_bytes(PNG)
    metadata = {
        "schema_version": 2,
        "frame_id": f"1000-{index}",
        "captured_unix": captured_unix,
        "image_path": str(png_path),
        "image_sha256": hashlib.sha256(PNG).hexdigest(),
        "width": 64,
        "height": 48,
        "fov": 70.0,
        "camera_position": {"x": 0.0, "y": 1.4, "z": agent_z},
        "camera_forward": {"x": 0.0, "y": 0.0, "z": 1.0},
        "camera_right": {"x": -1.0, "y": 0.0, "z": 0.0},
        "camera_up": {"x": 0.0, "y": 1.0, "z": 0.0},
        "capture_timing": {"inter_capture_start_ms": 1000.0},
        "previous_capture_timing": {"total_capture_ms": 120.0},
        "portable_depth": depth,
        "portable_navigation": {
            "schema_version": "lumina.godot.portable-navigation-snapshot.v1",
            "coordinate_frame": "godot_x_right_y_up_z_back",
            "source_frame_id": f"1000-{index}",
            "captured_unix": captured_unix - snapshot_lag_s,
            "agent": {
                "position": {"x": 0.0, "y": 0.0, "z": agent_z},
                "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
                "footprint_radius_m": 0.42,
            },
            "bounds_xz": {"min_x": -6.0, "max_x": 6.0, "min_z": -6.0, "max_z": 6.0},
            "obstacles": [
                {
                    "kind": "rect",
                    "center_xz": {"x": 0.0, "z": agent_z + target_range_m + 0.3},
                    "half_extents_xz": {"x": 1.0, "z": 0.3},
                }
            ],
        },
    }
    (spool / f"lumina_binocular_eye_slot_{index:02d}.json").write_text(json.dumps(metadata), encoding="utf-8")


def _spool(
    tmp_path: Path,
    *,
    revision: str | None,
    role: str | None,
    target_range_m: float = 0.9,
    snapshot_lag_s: float = 0.0,
) -> Path:
    spool = tmp_path / "spool"
    spool.mkdir()
    for index in range(4):
        _frame_metadata(
            spool,
            index=index,
            captured_unix=1_000.0 + index,
            agent_z=-4.45,
            revision=revision,
            role=role,
            target_range_m=target_range_m,
            snapshot_lag_s=snapshot_lag_s,
        )
    # A legacy non-depth frame (like the timestamped live spool files) must be ignored.
    (spool / "lumina_binocular_eye_20260907 000000.json").write_text(json.dumps({"frame_id": "x", "captured_unix": 1.0}))
    return spool


def test_recorded_frames_without_revision_are_conversion_failures_not_outcomes(tmp_path: Path) -> None:
    module = _module()
    spool = _spool(tmp_path, revision=None, role=None, snapshot_lag_s=2.5)

    report = module.replay(spool, arrival_radius_m=1.2)

    assert report["scope"] == "sensor_contract_compatibility_diagnosis_only"
    assert report["arrival_accuracy_or_speed_evidence"] is False
    assert report["frame_count"] == 4
    recorded = report["variants"]["as_recorded"]
    assert recorded["is_evidence"] is True
    assert recorded["windows"] == 2
    assert recorded["evaluable_target_count"] == 0
    assert recorded["outcomes"] == {"conversion_failed": 2}
    assert recorded["pass_rate"] is None
    assert all(
        "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH" in error["error"]
        for error in recorded["conversion_errors"]
    )
    assert recorded["conversion_errors"][0]["depth_findings"]["recorded_revision"] is None
    assert recorded["conversion_errors"][0]["depth_findings"]["recorded_role_ray_count"] == 0

    # One candidate per window: the floor has a single ray and is therefore
    # never a target (fewer than three reference rays).
    revision_only = report["variants"]["diagnostic_revision_injected"]
    assert revision_only["is_evidence"] is False
    assert revision_only["evaluable_target_count"] == 2
    assert revision_only["outcomes"] == {"fallback_eligible": 2}
    assert set(revision_only["reasons"]) == {"fallback_eligible:certification_surface_role_unavailable"}

    with_role = report["variants"]["diagnostic_revision_and_role_injected"]
    assert with_role["is_evidence"] is False
    assert with_role["evaluable_target_count"] == 2
    assert with_role["outcomes"] == {"pass": 2}
    assert with_role["pass_rate"] == 1.0
    for unit in with_role["units"]:
        assert unit["result"]["arrival_distance_m"] == pytest.approx(0.9, abs=0.05)
        assert unit["result"]["checks"]["agent_cell_free_after_additional_inflation"] is True
        assert unit["result"]["inflated_occupancy_proof"]["numeric_clearance_measured"] is False
    serialized = json.dumps(report)
    assert TARGET_SURFACE_ID not in serialized
    assert "e" * 64 not in serialized


def test_windows_without_post_feedback_pair_are_not_evaluable(tmp_path: Path) -> None:
    module = _module()
    # Snapshot time equals the frame time (as in the live spool): the fresh
    # frames are not both after the feedback observation.
    spool = _spool(tmp_path, revision=SURFACE_ID_SEMANTICS_REVISION, role="navigation_obstacle")

    report = module.replay(spool, arrival_radius_m=1.2)

    for name, variant in report["variants"].items():
        assert variant["outcomes"] == {NOT_EVALUABLE: 2}, name
        assert variant["evaluable_target_count"] == 0
        assert variant["not_evaluable_target_count"] == 2
        assert variant["pass_rate"] is None
        assert variant["reasons"] == {}
        assert all(unit["outcome"] == NOT_EVALUABLE for unit in variant["units"])
        assert all(
            unit["fresh_frame_captured_unix"][0] <= unit["feedback_observed_unix"]
            for unit in variant["units"]
        )


def test_compliant_recording_with_post_feedback_pair_is_evidence_and_can_pass(tmp_path: Path) -> None:
    module = _module()
    spool = _spool(tmp_path, revision=SURFACE_ID_SEMANTICS_REVISION, role="navigation_obstacle", snapshot_lag_s=2.5)

    report = module.replay(spool, arrival_radius_m=1.2)

    recorded = report["variants"]["as_recorded"]
    assert recorded["is_evidence"] is True
    assert recorded["conversion_errors"] == []
    assert recorded["evaluable_target_count"] == 2
    assert recorded["not_evaluable_target_count"] == 0
    assert recorded["outcomes"] == {"pass": 2}
    assert recorded["reasons"] == {
        "pass:dual_fresh_exact_reference_rays_within_arrival_threshold": 2,
    }
    assert recorded["pass_rate"] == pytest.approx(1.0)
    assert all(unit["recorded_role"] == "navigation_obstacle" for unit in recorded["units"])
    assert report["timing"]["recorded_inter_capture_start_ms"]["mean"] == pytest.approx(1000.0)
    assert report["timing"]["recorded_total_capture_ms"]["mean"] == pytest.approx(120.0)
    for frame in report["frames"]:
        assert frame["png_sha256"] == hashlib.sha256(PNG).hexdigest()


def test_compliant_recording_far_from_target_does_not_pass(tmp_path: Path) -> None:
    module = _module()
    spool = _spool(
        tmp_path,
        revision=SURFACE_ID_SEMANTICS_REVISION,
        role="navigation_obstacle",
        target_range_m=2.5,
        snapshot_lag_s=2.5,
    )

    report = module.replay(spool, arrival_radius_m=1.2)

    recorded = report["variants"]["as_recorded"]
    assert recorded["outcomes"] == {"fallback_eligible": 2}
    assert recorded["reasons"] == {"fallback_eligible:arrival_distance_not_confirmed": 2}
    assert recorded["pass_rate"] == 0.0


def test_replay_cli_writes_report_and_summary_and_never_touches_the_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    spool = _spool(tmp_path, revision=SURFACE_ID_SEMANTICS_REVISION, role="navigation_obstacle", snapshot_lag_s=2.5)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in spool.iterdir()}
    output = tmp_path / "out" / "report.json"
    summary_path = tmp_path / "out" / "summary.md"
    monkeypatch.setattr(
        "sys.argv",
        ["replay", "--spool", str(spool), "--output", str(output), "--summary-output", str(summary_path)],
    )

    assert module.main() == 0

    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in spool.iterdir()}
    assert before == after
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["arrival_radius_m_assumed"] == 1.2
    assert set(report["variants"]) == set(module.VARIANTS)
    summary = summary_path.read_text(encoding="utf-8")
    # The summary is rendered from the same report dict: values must match.
    assert summary == module.render_summary(report)
    wall = report["timing"]["replay_wall_seconds"]
    assert f"{wall:.1f} s" in summary
    assert "sensor_contract_compatibility_diagnosis_only" in summary
    assert "speed-improvement evidence: **False**" in summary
    assert '{"pass": 2}' in summary
    printed = json.loads(capsys.readouterr().out)
    assert printed["frame_count"] == 4
    assert printed["variants"]["as_recorded"]["is_evidence"] is True
    assert not math.isnan(printed["timing"]["replay_wall_seconds"])
