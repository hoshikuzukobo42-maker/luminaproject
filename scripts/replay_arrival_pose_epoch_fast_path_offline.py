#!/usr/bin/env python
"""Offline replay of the arrival pose-epoch fast path over saved RGB-D spool frames.

Scope: **sensor-contract compatibility diagnosis only.**  The replay never
claims arrival accuracy or a speed improvement.  It is read-only with respect
to the spool directory and contacts no adapter, bridge, Qwen, or service; the
frames' embedded ``portable_navigation`` snapshot is rasterized locally with
the same static helpers the Godot adapter uses.

Replay units
------------
Frames are ordered by ``captured_unix``.  For every window
``(reference, fresh_1, fresh_2)`` of three consecutive frames, one replay
certification is synthesized per candidate surface id observed with at least
three rays in the reference frame.  The arrived feedback is a proxy built from
``fresh_2``'s embedded snapshot (agent position/velocity).  A window is
evaluable only when both fresh frames were captured *after* that feedback
observation; otherwise it is recorded as ``not_evaluable_no_post_feedback_pair``
and excluded from evidence and pass-rate accounting.

Because no Qwen grounding exists offline, the certification is a **replay
proxy**: the certified surface points are the reference frame's rays for that
surface id and the recorded role (when uniform).  The proxy never relaxes a
threshold.

Variants
--------
``as_recorded``
    The evidence.  Frames are converted exactly as recorded.
``diagnostic_revision_injected``
    Diagnostic only, not evidence: the depth plane gets the current identity
    semantics revision string so that the identity checks can run.
``diagnostic_revision_and_role_injected``
    Diagnostic only, not evidence: additionally every identified ray is given
    ``surface_role=navigation_obstacle``.  Isolates the geometry/time gates.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lumina_next.arrival_pose_epoch_fast_path_v1 import (  # noqa: E402
    ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
    INITIAL_CERTIFICATION_REVISION,
    compute_initial_certification_digest,
    evaluate_arrival_pose_epoch_fast_path,
)
from lumina_next.godot_portable_navigation_adapter_v1 import (  # noqa: E402
    GodotPortableNavigationAdapter,
    _camera_quaternion,
    godot_to_canonical,
)
from lumina_next.portable_navigation_contract_v1 import (  # noqa: E402
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    FEEDBACK_RESULT_SCHEMA,
    MAP_REQUEST_SCHEMA,
    MAP_RESULT_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    validate_capture_result,
    validate_feedback_result,
    validate_map_request,
    validate_map_result,
)
from lumina_next.portable_navigation_core_v1 import project_depth_samples  # noqa: E402
from lumina_next.visual_navigation import (  # noqa: E402
    PORTABLE_APPROACH_RADIUS_CANDIDATES_M,
    portable_surface_sha256,
)

ENVIRONMENT_ID = "environment:offline-replay"
SESSION_ID = "session:offline-replay"
COORDINATE_FRAME_ID = "world:offline-replay"
VARIANTS = (
    "as_recorded",
    "diagnostic_revision_injected",
    "diagnostic_revision_and_role_injected",
)
EVIDENCE_VARIANT = "as_recorded"
NOT_EVALUABLE = "not_evaluable_no_post_feedback_pair"
SCOPE = "sensor_contract_compatibility_diagnosis_only"


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_frames(spool: Path) -> list[dict[str, Any]]:
    frames: dict[str, dict[str, Any]] = {}
    for path in sorted(spool.glob("*.json")):
        if path.name.startswith("._"):
            continue
        try:
            metadata = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict) or not isinstance(metadata.get("portable_depth"), dict):
            continue
        if not isinstance(metadata.get("portable_navigation"), dict):
            continue
        frame_id = str(metadata.get("frame_id") or "")
        if not frame_id or frame_id in frames:
            continue
        image_path = spool / Path(str(metadata.get("image_path") or "")).name
        if not image_path.exists():
            continue
        metadata["_json_path"] = str(path)
        metadata["_image_path"] = str(image_path)
        frames[frame_id] = metadata
    return sorted(frames.values(), key=lambda item: float(item["captured_unix"]))


def _depth_plane(metadata: dict[str, Any], variant: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    source = metadata.get("portable_depth") or {}
    recorded = source.get("samples") or []
    findings = {
        "recorded_revision": source.get("surface_id_semantics_revision"),
        "recorded_ray_count": len(recorded),
        "recorded_identified_ray_count": sum(1 for s in recorded if "surface_id" in s),
        "recorded_role_ray_count": sum(1 for s in recorded if "surface_role" in s),
    }
    if source.get("available") is not True:
        return None, findings
    samples = []
    for raw in recorded:
        sample = {
            "uv_norm": [float(raw["uv_norm"][0]), float(raw["uv_norm"][1])],
            "distance_m": float(raw["distance_m"]),
            "confidence": float(raw["confidence"]),
        }
        if "surface_id" in raw:
            sample["surface_id"] = raw["surface_id"]
        if "surface_role" in raw:
            sample["surface_role"] = raw["surface_role"]
        elif variant == "diagnostic_revision_and_role_injected" and "surface_id" in sample:
            sample["surface_role"] = "navigation_obstacle"
        samples.append(sample)
    plane = {
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "samples": samples,
        "samples_sha256": _canonical_sha256(samples),
    }
    revision = source.get("surface_id_semantics_revision")
    if variant != EVIDENCE_VARIANT and revision is None:
        revision = SURFACE_ID_SEMANTICS_REVISION
    if revision is not None:
        plane["surface_id_semantics_revision"] = revision
    return plane, findings


def _capture_result(metadata: dict[str, Any], variant: str, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    image = Path(metadata["_image_path"]).read_bytes()
    width, height = int(metadata["width"]), int(metadata["height"])
    focal = float(height) / (2.0 * math.tan(math.radians(float(metadata["fov"])) / 2.0))
    depth, findings = _depth_plane(metadata, variant)
    result = {
        "schema_version": CAPTURE_RESULT_SCHEMA,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "frame_id": "frame:replay-" + str(metadata["frame_id"]).replace("-", "_"),
        "captured_unix": float(metadata["captured_unix"]),
        "rgb": {
            "encoding": "png",
            "width_px": width,
            "height_px": height,
            "data_base64": base64.b64encode(image).decode("ascii"),
            "sha256": hashlib.sha256(image).hexdigest(),
        },
        "depth": depth,
        "camera": {
            "coordinate_frame_id": COORDINATE_FRAME_ID,
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": godot_to_canonical(metadata["camera_position"]),
            "orientation_xyzw": _camera_quaternion(metadata),
            "intrinsics": {"fx_px": focal, "fy_px": focal, "cx_px": width / 2.0, "cy_px": height / 2.0},
        },
    }
    return result, findings


def _map_from_snapshot(
    metadata: dict[str, Any],
    source_frame_id: str,
    request_id: str,
    *,
    frame_captured_unix: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = metadata["portable_navigation"]
    # A live map is built after its source frame; mirror that ordering so the
    # replay never predates the map relative to the frame it was built from.
    built_unix = max(float(snapshot["captured_unix"]), float(frame_captured_unix))
    request = validate_map_request({
        "schema_version": MAP_REQUEST_SCHEMA,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "requested_unix": built_unix,
        "source_frame_id": source_frame_id,
        "resolution_m": 0.20,
        "footprint_radius_m": 0.35,
    })
    bounds = GodotPortableNavigationAdapter._snapshot_bounds(snapshot)
    obstacles = GodotPortableNavigationAdapter._snapshot_obstacles(snapshot)
    agent = snapshot["agent"]
    footprint = max(float(agent["footprint_radius_m"]), request["footprint_radius_m"])
    width, height, occupancy = GodotPortableNavigationAdapter._rasterize(
        bounds, obstacles, request["resolution_m"], footprint
    )
    digest = hashlib.sha256(occupancy).hexdigest()
    result = {
        "schema_version": MAP_RESULT_SCHEMA,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "map_id": "map:replay-" + digest[:32],
        "source_frame_id": source_frame_id,
        "created_unix": built_unix,
        "coordinate_frame_id": COORDINATE_FRAME_ID,
        "coordinate_convention": COORDINATE_CONVENTION,
        "grid": {
            "width_cells": width,
            "height_cells": height,
            "resolution_m": request["resolution_m"],
            "origin_position_m": [bounds[0], bounds[2], 0.0],
            "origin_orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "occupancy_encoding": "u8_probability",
            "occupancy_base64": base64.b64encode(occupancy).decode("ascii"),
            "occupancy_sha256": digest,
            "unknown_value": 127,
            "occupied_threshold": 200,
        },
        "agent": {
            "position_m": godot_to_canonical(agent["position"]),
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "footprint_radius_m": footprint,
        },
    }
    return request, validate_map_result(result, request=request)


def _feedback_proxy(metadata: dict[str, Any], request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Synthesize a contract-shaped arrived feedback from the embedded snapshot.

    The snapshot has no motion command, so the command id and sequence are
    replay placeholders.  The feedback is a proxy for the agent state at the
    snapshot time, not a recorded arrival event.
    """

    snapshot = metadata["portable_navigation"]
    agent = snapshot["agent"]
    velocity = agent.get("velocity") or {"x": 0.0, "y": 0.0, "z": 0.0}
    observed = float(snapshot["captured_unix"])
    request = {
        "schema_version": FEEDBACK_REQUEST_SCHEMA,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "requested_unix": observed,
        "command_id": "replay-command",
        "after_sequence": 0,
    }
    result = {
        "schema_version": FEEDBACK_RESULT_SCHEMA,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "command_id": "replay-command",
        "sequence": 1,
        "observed_unix": observed,
        "status": "arrived",
        "position_m": godot_to_canonical(agent["position"]),
        "velocity_mps": godot_to_canonical(velocity),
        "remaining_distance_m": 0.0,
        "minimum_clearance_m": 1.0,
        "collision_detected": False,
        "target_reached": True,
        "reason": None,
    }
    return request, validate_feedback_result(result, request=request)


def _candidate_targets(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group identified rays by surface id; keep ids with at least three rays.

    The recorded role is carried along when every ray of the id reports the
    same role (the contract forbids conflicting roles within one frame).
    """

    projected = project_depth_samples(capture, min_confidence=0.25)
    by_id: dict[str, dict[str, Any]] = {}
    for point in projected:
        surface_id = point.get("surface_id")
        if isinstance(surface_id, str):
            entry = by_id.setdefault(surface_id, {"points": [], "roles": set()})
            entry["points"].append([float(c) for c in point["position_m"]])
            entry["roles"].add(point.get("surface_role"))
    targets: dict[str, dict[str, Any]] = {}
    for surface_id, entry in by_id.items():
        if len(entry["points"]) < 3:
            continue
        roles = entry["roles"]
        role = next(iter(roles)) if len(roles) == 1 else None
        targets[surface_id] = {"points": entry["points"], "role": role if isinstance(role, str) else None}
    return targets


def _certification(reference: dict[str, Any], surface_id: str, points: list[list[float]], role: str | None, index: int) -> dict[str, Any]:
    depth = reference.get("depth") or {}
    revision = depth.get("surface_id_semantics_revision")
    certification = {
        "certification_revision": INITIAL_CERTIFICATION_REVISION,
        "certified_unix": float(reference["captured_unix"]),
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "coordinate_frame_id": COORDINATE_FRAME_ID,
        "target_id": "target:" + hashlib.sha256(surface_id.encode()).hexdigest()[:32],
        "expected_surface_role": role,
        "surface_id": surface_id,
        "surface_role": role,
        "surface_id_semantics_revision": revision,
        "surface_points_m": points[:256],
        "semantic_frame_id": reference["frame_id"] + ":semantic",
        "semantic_capture_request_id": f"capture:replay-semantic-{index}",
        "post_inference_frame_id": reference["frame_id"],
        "post_inference_capture_request_id": f"capture:replay-post-{index}",
        "semantic_map_id": f"map:replay-semantic-{index}",
        "semantic_map_request_id": f"recognition-semantic-map:replay-{index}",
        "navigation_map_id": f"map:replay-navigation-{index}",
        "navigation_map_request_id": f"map:replay-{index}",
        "semantic_ground_request_id": f"ground-check:replay-{index}",
        "post_inference_ground_request_id": f"ground:replay-{index}",
        "dual_grounding_surface_id_match": True,
        "dual_grounding_surface_role_match": True,
        "dual_grounding_semantics_revision_match": True,
    }
    certification["surface_sha256"] = portable_surface_sha256(certification["surface_points_m"])
    certification["certification_sha256"] = compute_initial_certification_digest(certification)
    return certification


def _redact(result: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "outcome", "reason", "contract_error", "contract_detail", "frame_index",
        "arrival_distance_m", "maximum_allowed_distance_m", "feedback_agent_planar_divergence_m",
        "arrived_feedback_speed_mps", "checks", "inflated_occupancy_proof",
    }
    redacted = {key: result.get(key) for key in keep if key in result}
    redacted["frames"] = [
        {k: v for k, v in frame.items() if k != "_exact_reference_rays"}
        for frame in result.get("frames", [])
    ]
    return redacted


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None}
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}


def replay(spool: Path, *, arrival_radius_m: float) -> dict[str, Any]:
    started = time.monotonic()
    frames = _load_frames(spool)
    report: dict[str, Any] = {
        "fast_path_revision": ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
        "scope": SCOPE,
        "arrival_accuracy_or_speed_evidence": False,
        "spool_directory": str(spool),
        "frame_count": len(frames),
        "frames": [
            {
                "native_frame_id": f["frame_id"],
                "captured_unix": f["captured_unix"],
                "json": Path(f["_json_path"]).name,
                "png": Path(f["_image_path"]).name,
                "png_sha256": hashlib.sha256(Path(f["_image_path"]).read_bytes()).hexdigest(),
                "json_sha256": hashlib.sha256(Path(f["_json_path"]).read_bytes()).hexdigest(),
            }
            for f in frames
        ],
        "arrival_radius_m_assumed": arrival_radius_m,
        "arrival_radius_note": (
            "No planning stage exists offline; the largest planning candidate radius is "
            "assumed so that distance gating is as permissive as the live planner allows."
        ),
        "certification_proxy_note": (
            "Certifications are replay proxies built from the reference frame's rays per "
            "surface id with the recorded role.  No Qwen grounding was performed.  The "
            "arrived feedback is a proxy synthesized from the newest frame's embedded "
            "navigation snapshot; the spool contains no recorded arrival event."
        ),
        "evaluability_rule": (
            "A window is evaluable only when both fresh frames were captured after the "
            "feedback observation; otherwise it is counted as "
            f"{NOT_EVALUABLE} and excluded from evidence and pass rate."
        ),
        "variants": {},
        "timing": {},
    }
    inter_capture = [
        float(f["capture_timing"]["inter_capture_start_ms"])
        for f in frames
        if isinstance(f.get("capture_timing"), dict) and "inter_capture_start_ms" in f["capture_timing"]
    ]
    total_capture = [
        float(f["previous_capture_timing"]["total_capture_ms"])
        for f in frames
        if isinstance(f.get("previous_capture_timing"), dict) and "total_capture_ms" in f["previous_capture_timing"]
    ]
    report["timing"]["recorded_inter_capture_start_ms"] = _stats(inter_capture)
    report["timing"]["recorded_total_capture_ms"] = _stats(total_capture)

    for variant in VARIANTS:
        outcomes: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        units: list[dict[str, Any]] = []
        conversion_errors: list[dict[str, Any]] = []
        evaluation_ms: list[float] = []
        request_counter = 0
        for index in range(len(frames) - 2):
            reference_meta, first_meta, second_meta = frames[index], frames[index + 1], frames[index + 2]
            window = [reference_meta["frame_id"], first_meta["frame_id"], second_meta["frame_id"]]
            converted = []
            findings = None
            try:
                for meta in (reference_meta, first_meta, second_meta):
                    request_counter += 1
                    raw, findings = _capture_result(meta, variant, f"capture:replay-{variant}-{request_counter}")
                    converted.append(validate_capture_result(raw))
            except Exception as exc:  # conversion failure is itself a finding
                conversion_errors.append({
                    "window": window,
                    "error": type(exc).__name__ + ": " + str(exc),
                    "depth_findings": findings,
                })
                outcomes["conversion_failed"] += 1
                continue
            reference, first, second = converted
            request_counter += 1
            map_request, fresh_map = _map_from_snapshot(
                second_meta,
                second["frame_id"],
                f"arrival-fast-path-map:replay-{variant}-{request_counter}",
                frame_captured_unix=float(second["captured_unix"]),
            )
            request_counter += 1
            feedback_request, feedback = _feedback_proxy(
                second_meta, f"feedback:replay-{variant}-{request_counter}"
            )
            targets = _candidate_targets(reference)
            post_feedback_pair = (
                float(first["captured_unix"]) > float(feedback["observed_unix"])
                and float(second["captured_unix"]) > float(first["captured_unix"])
            )
            if not post_feedback_pair:
                outcomes[NOT_EVALUABLE] += len(targets)
                units.append({
                    "window": window,
                    "target_count": len(targets),
                    "outcome": NOT_EVALUABLE,
                    "feedback_observed_unix": feedback["observed_unix"],
                    "fresh_frame_captured_unix": [first["captured_unix"], second["captured_unix"]],
                })
                continue
            cutoff = max(float(reference["captured_unix"]), float(feedback["observed_unix"]))
            for target_index, (surface_id, target) in enumerate(sorted(targets.items())):
                points = target["points"]
                certification = _certification(
                    reference, surface_id, points, target["role"], request_counter * 1000 + target_index
                )
                evaluation_started = time.perf_counter()
                result = evaluate_arrival_pose_epoch_fast_path(
                    certification,
                    (first, second),
                    fresh_map,
                    fresh_map_request=map_request,
                    arrived_feedback=feedback,
                    arrived_feedback_request=feedback_request,
                    arrival_radius_m=arrival_radius_m,
                    not_before_unix=cutoff,
                    now_unix=float(second["captured_unix"]) + 0.5,
                )
                evaluation_ms.append((time.perf_counter() - evaluation_started) * 1000.0)
                outcomes[result["outcome"]] += 1
                reasons[f"{result['outcome']}:{result['reason']}"] += 1
                units.append({
                    "window": window,
                    "target_index": target_index,
                    "reference_point_count": len(points),
                    "recorded_role": target["role"],
                    "result": _redact(result),
                })
        evaluable = sum(
            count for key, count in outcomes.items() if key not in {"conversion_failed", NOT_EVALUABLE}
        )
        report["variants"][variant] = {
            "is_evidence": variant == EVIDENCE_VARIANT,
            "windows": max(0, len(frames) - 2),
            "evaluable_target_count": evaluable,
            "not_evaluable_target_count": outcomes[NOT_EVALUABLE],
            "outcomes": dict(outcomes),
            "pass_rate": (outcomes["pass"] / evaluable) if evaluable else None,
            "reasons": dict(reasons),
            "conversion_errors": conversion_errors,
            "evaluation_ms": {
                "count": len(evaluation_ms),
                "mean": sum(evaluation_ms) / len(evaluation_ms) if evaluation_ms else None,
                "max": max(evaluation_ms) if evaluation_ms else None,
            },
            "units": units,
        }
    report["timing"]["replay_wall_seconds"] = time.monotonic() - started
    return report


def _fmt(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_summary(report: dict[str, Any]) -> str:
    """Render the Markdown summary from the report dict so that values match."""

    lines: list[str] = []
    lines.append("# Offline replay: arrival pose-epoch fast path (sensor-contract compatibility diagnosis)\n")
    lines.append(f"- scope: `{report['scope']}`; arrival accuracy / speed-improvement evidence: **{report['arrival_accuracy_or_speed_evidence']}**")
    lines.append(f"- fast path revision: `{report['fast_path_revision']}`")
    lines.append(f"- spool (read-only): `{report['spool_directory']}`")
    frames = report["frames"]
    if frames:
        lines.append(
            f"- distinct saved RGB-D frames with `portable_depth` + embedded navigation snapshot: **{report['frame_count']}** "
            f"(native frame ids {frames[0]['native_frame_id']} … {frames[-1]['native_frame_id']})"
        )
    else:
        lines.append("- distinct saved RGB-D frames with `portable_depth` + embedded navigation snapshot: **0**")
    lines.append(f"- assumed arrival radius: {report['arrival_radius_m_assumed']} m ({report['arrival_radius_note']})")
    lines.append(f"- {report['certification_proxy_note']}")
    lines.append(f"- {report['evaluability_rule']}\n")
    timing = report["timing"]
    inter = timing["recorded_inter_capture_start_ms"]
    total = timing["recorded_total_capture_ms"]
    lines.append("## Recorded sensor timing (from spool `capture_timing`)\n")
    lines.append(f"- inter-capture start interval: mean {_fmt(inter['mean'])} ms (min {_fmt(inter['min'])}, max {_fmt(inter['max'])})")
    lines.append(f"- total capture time per frame: mean {_fmt(total['mean'])} ms (min {_fmt(total['min'])}, max {_fmt(total['max'])})")
    lines.append(f"- replay wall time (all variants, includes PNG decode + contract validation): {_fmt(timing['replay_wall_seconds'])} s\n")
    lines.append("## Results per variant\n")
    lines.append("| variant | evidence? | windows | evaluable targets | not evaluable | outcomes | pass rate | reasons | eval ms mean/max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name in VARIANTS:
        variant = report["variants"].get(name)
        if variant is None:
            continue
        ms = variant["evaluation_ms"]
        ms_text = f"{_fmt(ms['mean'])} / {_fmt(ms['max'])}" if ms["mean"] is not None else "n/a"
        rate = f"{variant['pass_rate']:.0%}" if variant["pass_rate"] is not None else "n/a"
        lines.append(
            f"| {name} | {'YES' if variant['is_evidence'] else 'no (diagnostic)'} | {variant['windows']} | "
            f"{variant['evaluable_target_count']} | {variant['not_evaluable_target_count']} | "
            f"{json.dumps(variant['outcomes'], sort_keys=True)} | {rate} | {json.dumps(variant['reasons'], sort_keys=True)} | {ms_text} |"
        )
    lines.append("")
    evidence = report["variants"].get(EVIDENCE_VARIANT)
    lines.append(f"## {EVIDENCE_VARIANT} (evidence) detail\n")
    if evidence is None:
        lines.append("- no evidence variant in report")
    else:
        errors = evidence["conversion_errors"]
        if errors:
            lines.append(f"- {len(errors)} of {evidence['windows']} windows failed contract conversion: `{errors[0]['error']}`")
            lines.append(f"- recorded depth findings (first window): {json.dumps(errors[0]['depth_findings'], sort_keys=True)}")
        else:
            lines.append("- all windows converted under the contract")
        lines.append(
            f"- evaluable targets: {evidence['evaluable_target_count']}; not evaluable ({NOT_EVALUABLE}): "
            f"{evidence['not_evaluable_target_count']}; pass rate: "
            f"{'n/a' if evidence['pass_rate'] is None else f'{evidence['pass_rate']:.0%}'}"
        )
    lines.append("")
    lines.append("## Diagnostics (not evidence)\n")
    for name in VARIANTS[1:]:
        variant = report["variants"].get(name)
        if variant is None:
            continue
        lines.append(
            f"- `{name}`: outcomes {json.dumps(variant['outcomes'], sort_keys=True)}; reasons "
            f"{json.dumps(variant['reasons'], sort_keys=True)}; evaluable {variant['evaluable_target_count']}, "
            f"not evaluable {variant['not_evaluable_target_count']}"
        )
    lines.append("")
    lines.append("## Interpretation limits\n")
    lines.append("- This replay diagnoses whether the recorded sensor output satisfies the portable contract the fast path depends on. It does not measure arrival accuracy and does not demonstrate a speed improvement.")
    lines.append(
        f"- The fast path itself waits for two fresh frames after the arrived feedback (recorded publish interval mean {_fmt(inter['mean'])} ms, "
        f"capture {_fmt(total['mean'])} ms) plus one local map rasterization; the slow path additionally runs a Qwen inference whose latency was not measured here."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spool", required=True, type=Path, help="Directory holding saved *.json + *.png spool frames (read-only)")
    parser.add_argument("--output", required=True, type=Path, help="Where to write the JSON replay report")
    parser.add_argument("--summary-output", type=Path, default=None, help="Optional Markdown summary rendered from the same report")
    parser.add_argument(
        "--arrival-radius-m",
        type=float,
        default=max(PORTABLE_APPROACH_RADIUS_CANDIDATES_M),
        help="Assumed arrival radius (default: the largest planning candidate)",
    )
    args = parser.parse_args()
    report = replay(args.spool.resolve(), arrival_radius_m=args.arrival_radius_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if args.summary_output is not None:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(render_summary(report), encoding="utf-8")
    summary = {
        variant: {
            k: v
            for k, v in data.items()
            if k in {"is_evidence", "windows", "evaluable_target_count", "not_evaluable_target_count", "outcomes", "pass_rate", "reasons", "evaluation_ms"}
        }
        for variant, data in report["variants"].items()
    }
    print(json.dumps({"scope": report["scope"], "frame_count": report["frame_count"], "timing": report["timing"], "variants": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
