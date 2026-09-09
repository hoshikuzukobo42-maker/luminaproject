"""Pure arrival fast path over pose-epoch surface identity (stage 1, revision 2).

This module contains **no I/O, no Qwen call, no adapter call, no lease, and no
model wake**.  It receives already-captured engine-neutral envelopes (two fresh
RGB-D capture results, one fresh map request/result pair, the arrived motion
feedback) plus the initial certification recorded after the dual grounding at
recognition time, and returns one of three outcomes:

``pass``
    Two mutually distinct, pose-synchronized fresh RGB-D frames each contain at
    least three unique-UV rays (confidence >= ``MINIMUM_RAY_CONFIDENCE``) whose
    opaque ``surface_id`` and ``surface_role`` exactly match the initial
    certification and whose 3-D positions lie within
    ``ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M`` of the initially certified
    surface.  The arrival distance is computed from the newest frame's rays and
    is within the unchanged arrival threshold.  The agent is stationary, its
    fresh-map position agrees with the arrived feedback, and its cell stays free
    on the fresh map after an additional binary inflation.

``fallback_eligible``
    The fast path could not *confirm* arrival because the observation is
    insufficient (too few exact rays, target hidden, identity/role/depth not
    available, expected role missing or not ``navigation_obstacle``, distance
    not confirmable).  Nothing indicates a safety or integrity violation, so
    the existing semantic (Qwen) revalidation may run.

``hard_fail``
    A safety or integrity violation that a semantic fallback must never
    rescue: coordinate frame change, certification tamper/digest mismatch,
    recorded dual-grounding disagreement, environment/session mismatch (frames,
    map or feedback), request replay or duplicate request ids, map lineage or
    map policy mismatch, blocked/out-of-bounds agent cell on the newest map
    after inflation, future-dated observations, contract violations, feedback
    that reports a collision, insufficient clearance, motion, or a position
    disagreeing with the fresh map, a cutoff older than the feedback, or
    contradictory pose-epoch evidence (frame reuse, timestamp regression,
    camera pose change, same-id role conflict, identity semantics revision
    mismatch).

Audit constraints.  The fast path cannot see material, colour, appearance, or
semantic-category changes; every result therefore records
``semantic_revalidation_performed=False``.  Raw ``surface_id`` values are never
copied into the result: only match booleans and counts are reported.  The
occupancy check is a binary inflated-occupancy proof, not a numeric clearance
measurement, and is labelled as such.  The certification digest is an
integrity (corruption-detection) digest, not an authentication signature.
Nothing here reads scene node names, node paths, native object names, or
engine instance ids; only the portable contract envelopes are consumed.

Shared numeric policy (0.20 m reference distance, 0.08 m arrival tolerance,
synchronized camera tolerance) is imported from ``visual_navigation`` at call
time so that the thresholds stay single-sourced and no import cycle is created
when the live module imports this one.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from .portable_navigation_contract_v1 import (
    MAX_CLOCK_SKEW_SECONDS,
    MAX_GROUND_SURFACE_POINTS,
    SURFACE_ID_SEMANTICS_REVISION,
    SURFACE_ROLES,
    PortableNavigationContractError,
    validate_capture_result,
    validate_feedback_request,
    validate_feedback_result,
    validate_map_request,
    validate_map_result,
)
from .portable_navigation_core_v1 import (
    PortableNavigationCoreError,
    path_collision_report,
    project_depth_samples,
)

ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION = (
    "arrival_pose_epoch_dual_fresh_rgbd_exact_surface_fast_path_v2"
)
INITIAL_CERTIFICATION_REVISION = "arrival_pose_epoch_initial_certification_v2"

OUTCOME_PASS = "pass"
OUTCOME_FALLBACK_ELIGIBLE = "fallback_eligible"
OUTCOME_HARD_FAIL = "hard_fail"
OUTCOMES = frozenset({OUTCOME_PASS, OUTCOME_FALLBACK_ELIGIBLE, OUTCOME_HARD_FAIL})

REQUIRED_SURFACE_ROLE = "navigation_obstacle"
REQUIRED_FRESH_FRAME_COUNT = 2
MINIMUM_EXACT_REFERENCE_RAYS_PER_FRAME = 3
MINIMUM_RAY_CONFIDENCE = 0.25
AGENT_ADDITIONAL_INFLATION_M = 0.10
FRESH_MAP_RESOLUTION_M = 0.20
FRESH_MAP_MINIMUM_FOOTPRINT_RADIUS_M = 0.35
MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M = 0.20
MAX_ARRIVED_SPEED_MPS = 0.05
MINIMUM_ARRIVED_CLEARANCE_M = 0.10
MAX_CERTIFIED_SURFACE_POINTS = MAX_GROUND_SURFACE_POINTS

# Digest-covered certification fields.  ``certification_sha256`` itself is
# excluded.  The order is irrelevant: the digest is over sorted-key canonical
# JSON.
INITIAL_CERTIFICATION_DIGEST_FIELDS = (
    "certification_revision",
    "certified_unix",
    "environment_id",
    "session_id",
    "coordinate_frame_id",
    "target_id",
    "expected_surface_role",
    "surface_id",
    "surface_role",
    "surface_id_semantics_revision",
    "surface_points_m",
    "surface_sha256",
    "semantic_frame_id",
    "semantic_capture_request_id",
    "post_inference_frame_id",
    "post_inference_capture_request_id",
    "semantic_map_id",
    "semantic_map_request_id",
    "navigation_map_id",
    "navigation_map_request_id",
    "semantic_ground_request_id",
    "post_inference_ground_request_id",
    "dual_grounding_surface_id_match",
    "dual_grounding_surface_role_match",
    "dual_grounding_semantics_revision_match",
)
_INITIAL_CERTIFICATION_FIELDS = frozenset(INITIAL_CERTIFICATION_DIGEST_FIELDS) | {
    "certification_sha256",
}
_CERTIFICATION_IDENTIFIER_FIELDS = (
    "environment_id",
    "session_id",
    "coordinate_frame_id",
    "semantic_frame_id",
    "semantic_capture_request_id",
    "post_inference_frame_id",
    "post_inference_capture_request_id",
    "semantic_map_id",
    "semantic_map_request_id",
    "navigation_map_id",
    "navigation_map_request_id",
    "semantic_ground_request_id",
    "post_inference_ground_request_id",
)
_CERTIFICATION_REQUEST_ID_FIELDS = (
    "semantic_capture_request_id",
    "post_inference_capture_request_id",
    "semantic_map_request_id",
    "navigation_map_request_id",
    "semantic_ground_request_id",
    "post_inference_ground_request_id",
)
_CERTIFICATION_BOOLEAN_FIELDS = (
    "dual_grounding_surface_id_match",
    "dual_grounding_surface_role_match",
    "dual_grounding_semantics_revision_match",
)

# Closed registries of the reason codes this module can emit.  Log and audit
# consumers may rely on them; adding a reason requires adding it here (the
# unit tests cross-check the source against these sets).
PASS_REASON = "dual_fresh_exact_reference_rays_within_arrival_threshold"
HARD_FAIL_REASONS = frozenset({
    "contract_invalid",
    "initial_certification_digest_mismatch",
    "surface_id_semantics_revision_mismatch",
    "dual_grounding_mismatch",
    "certification_role_inconsistent",
    "future_dated_observation",
    "environment_mismatch",
    "session_mismatch",
    "request_id_duplicate",
    "request_replay",
    "frame_reused",
    "frame_timestamps_not_monotonic",
    "fresh_frame_predates_certification",
    "fresh_frame_predates_arrival_window",
    "coordinate_frame_changed",
    "camera_pose_changed_between_frames",
    "map_lineage_mismatch",
    "map_policy_mismatch",
    "agent_position_out_of_bounds",
    "agent_position_blocked_after_inflation",
    "feedback_collision_detected",
    "feedback_clearance_violation",
    "cutoff_predates_feedback",
    "agent_not_stationary",
    "agent_position_inconsistent_with_feedback",
    "surface_role_contradiction",
})
FALLBACK_REASONS = frozenset({
    "certification_surface_identity_unavailable",
    "certification_surface_role_unavailable",
    "expected_surface_role_unavailable",
    "fast_path_role_not_supported",
    "depth_unavailable",
    "surface_identity_unavailable",
    "reference_surface_not_observed",
    "insufficient_exact_reference_rays",
    "arrival_distance_not_confirmed",
})
REASONS_BY_OUTCOME = {
    OUTCOME_PASS: frozenset({PASS_REASON}),
    OUTCOME_HARD_FAIL: HARD_FAIL_REASONS,
    OUTCOME_FALLBACK_ELIGIBLE: FALLBACK_REASONS,
}

_EPSILON = 1e-9
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TARGET_ID_PATTERN = re.compile(r"^target:[0-9a-f]{16,64}$")
_SURFACE_ID_PATTERN = re.compile(r"^surface:[0-9a-f]{64}$")


class _Decision(Exception):
    """Internal control flow carrying a final outcome and reason."""

    def __init__(self, outcome: str, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason
        self.details = details


def _hard_fail(reason: str, **details: Any) -> _Decision:
    return _Decision(OUTCOME_HARD_FAIL, reason, **details)


def _fallback(reason: str, **details: Any) -> _Decision:
    return _Decision(OUTCOME_FALLBACK_ELIGIBLE, reason, **details)


def _contract_invalid(code: str, detail: Any = None, **details: Any) -> _Decision:
    if detail is not None:
        details["contract_detail"] = detail
    return _hard_fail("contract_invalid", contract_error=code, **details)


def _shared_policy() -> dict[str, Any]:
    """Load the single-sourced thresholds and pose matcher lazily.

    ``visual_navigation`` is imported inside the function so that the live
    integration (``visual_navigation`` importing this module) does not form an
    import cycle at module load time.
    """

    from . import visual_navigation as shared

    return {
        "reference_max_distance_m": float(
            shared.ARRIVAL_REFERENCE_SURFACE_MAX_DISTANCE_M
        ),
        "arrival_tolerance_m": float(shared.ARRIVAL_REVALIDATION_TOLERANCE_M),
        "pose_matches": shared.portable_pose_matches,
        "surface_sha256": shared.portable_surface_sha256,
    }


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _vector3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(_finite_number(component) for component in value):
        return None
    return float(value[0]), float(value[1]), float(value[2])


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and _ID_PATTERN.match(value) is not None


def compute_initial_certification_digest(certification: Mapping[str, Any]) -> str:
    """Return the integrity digest over the certification fields.

    The digest covers every field in ``INITIAL_CERTIFICATION_DIGEST_FIELDS``
    (missing fields are serialized as ``null``).  It detects accidental or
    unsigned modification and corruption only; it is **not** a keyed
    signature and the certification is not "authenticated" by it.
    """

    payload = {
        field: certification.get(field)
        for field in INITIAL_CERTIFICATION_DIGEST_FIELDS
    }
    return _canonical_sha256(payload)


def _validate_certification(
    certification: Any,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a normalized certification or raise a hard failure.

    Structural problems are ``contract_invalid``; a digest mismatch is
    ``initial_certification_digest_mismatch``.  Identity *absence* is not a
    failure here (it becomes fallback-eligible later); identity *malformation*
    is a contract violation.
    """

    if not isinstance(certification, Mapping):
        raise _contract_invalid("CERTIFICATION_OBJECT_REQUIRED")
    unknown = set(certification) - _INITIAL_CERTIFICATION_FIELDS
    missing = _INITIAL_CERTIFICATION_FIELDS - set(certification)
    if unknown or missing:
        raise _contract_invalid("CERTIFICATION_FIELDS_INVALID", sorted(unknown | missing))
    if certification.get("certification_revision") != INITIAL_CERTIFICATION_REVISION:
        raise _contract_invalid("CERTIFICATION_REVISION_UNSUPPORTED")
    certified_unix = certification.get("certified_unix")
    if not _finite_number(certified_unix) or certified_unix < 0:
        raise _contract_invalid("CERTIFICATION_TIMESTAMP_INVALID")
    for field in _CERTIFICATION_IDENTIFIER_FIELDS:
        if not _identifier(certification.get(field)):
            raise _contract_invalid("CERTIFICATION_IDENTIFIER_INVALID", field)
    request_ids = [certification[field] for field in _CERTIFICATION_REQUEST_ID_FIELDS]
    if len(set(request_ids)) != len(request_ids):
        raise _contract_invalid("CERTIFICATION_REQUEST_IDS_NOT_DISTINCT")
    if certification["semantic_frame_id"] == certification["post_inference_frame_id"]:
        raise _contract_invalid("CERTIFICATION_FRAME_IDS_NOT_DISTINCT")
    if certification["semantic_map_id"] == certification["navigation_map_id"]:
        raise _contract_invalid("CERTIFICATION_MAP_IDS_NOT_DISTINCT")
    for field in _CERTIFICATION_BOOLEAN_FIELDS:
        if type(certification.get(field)) is not bool:
            raise _contract_invalid("CERTIFICATION_BOOLEAN_INVALID", field)
    target_id = certification.get("target_id")
    if not isinstance(target_id, str) or not _TARGET_ID_PATTERN.match(target_id):
        raise _contract_invalid("CERTIFICATION_IDENTIFIER_INVALID", "target_id")
    expected_surface_role = certification.get("expected_surface_role")
    if expected_surface_role is not None and expected_surface_role not in SURFACE_ROLES:
        raise _contract_invalid("CERTIFICATION_EXPECTED_SURFACE_ROLE_INVALID")
    surface_id = certification.get("surface_id")
    if surface_id is not None and (
        not isinstance(surface_id, str) or not _SURFACE_ID_PATTERN.match(surface_id)
    ):
        raise _contract_invalid("CERTIFICATION_SURFACE_ID_INVALID")
    surface_role = certification.get("surface_role")
    if surface_role is not None and surface_role not in SURFACE_ROLES:
        raise _contract_invalid("CERTIFICATION_SURFACE_ROLE_INVALID")
    if surface_role is not None and surface_id is None:
        raise _contract_invalid("CERTIFICATION_SURFACE_ROLE_REQUIRES_SURFACE_ID")
    semantics_revision = certification.get("surface_id_semantics_revision")
    if semantics_revision is not None and not isinstance(semantics_revision, str):
        raise _contract_invalid("CERTIFICATION_SEMANTICS_REVISION_INVALID")
    raw_points = certification.get("surface_points_m")
    if not isinstance(raw_points, (list, tuple)) or not raw_points:
        raise _contract_invalid("CERTIFICATION_SURFACE_POINTS_INVALID")
    if len(raw_points) > MAX_CERTIFIED_SURFACE_POINTS:
        raise _contract_invalid(
            "CERTIFICATION_SURFACE_POINTS_TOO_MANY",
            f"{len(raw_points)} > {MAX_CERTIFIED_SURFACE_POINTS}",
        )
    points: list[tuple[float, float, float]] = []
    for raw_point in raw_points:
        point = _vector3(raw_point)
        if point is None:
            raise _contract_invalid("CERTIFICATION_SURFACE_POINTS_INVALID")
        points.append(point)
    surface_sha256 = certification.get("surface_sha256")
    certification_sha256 = certification.get("certification_sha256")
    if not isinstance(surface_sha256, str) or not isinstance(certification_sha256, str):
        raise _contract_invalid("CERTIFICATION_DIGEST_INVALID")
    surface_digest_match = policy["surface_sha256"](list(raw_points)) == surface_sha256
    certification_digest_match = (
        compute_initial_certification_digest(certification) == certification_sha256
    )
    if not (surface_digest_match and certification_digest_match):
        raise _hard_fail(
            "initial_certification_digest_mismatch",
            initial_surface_digest_match=surface_digest_match,
            initial_certification_digest_match=certification_digest_match,
        )
    # A certification that carries an opaque id must have been recorded under
    # the exact identity semantics this fast path relies on.  Any other
    # revision string means the id was minted under different guarantees.
    digest_ok = {
        "initial_certification_digest_match": True,
        "initial_surface_digest_match": True,
    }
    if surface_id is not None and semantics_revision != SURFACE_ID_SEMANTICS_REVISION:
        raise _hard_fail(
            "surface_id_semantics_revision_mismatch",
            semantics_revision_source="initial_certification",
            **digest_ok,
        )
    # The certification is only valid when both recognition-time groundings
    # agreed on identity, role and semantics.  A recorded disagreement (even a
    # re-signed one) is a contradiction, never an observation gap.
    for field in _CERTIFICATION_BOOLEAN_FIELDS:
        if certification[field] is not True:
            raise _hard_fail("dual_grounding_mismatch", dual_grounding_field=field, **digest_ok)
    if (
        isinstance(expected_surface_role, str)
        and isinstance(surface_role, str)
        and expected_surface_role != surface_role
    ):
        raise _hard_fail("certification_role_inconsistent", **digest_ok)
    return {
        "certified_unix": float(certified_unix),
        "environment_id": certification["environment_id"],
        "session_id": certification["session_id"],
        "coordinate_frame_id": certification["coordinate_frame_id"],
        "target_id": target_id,
        "expected_surface_role": expected_surface_role,
        "surface_id": surface_id,
        "surface_role": surface_role,
        "surface_id_semantics_revision": semantics_revision,
        "surface_points_m": points,
        "frame_ids": {
            certification["semantic_frame_id"],
            certification["post_inference_frame_id"],
        },
        "map_ids": {certification["semantic_map_id"], certification["navigation_map_id"]},
        "request_ids": set(request_ids),
    }


def _validate_frames(frames: Any, *, now_unix: float) -> list[dict[str, Any]]:
    if not isinstance(frames, (list, tuple)) or len(frames) != REQUIRED_FRESH_FRAME_COUNT:
        raise _contract_invalid(
            "FRESH_FRAME_COUNT_INVALID",
            f"expected exactly {REQUIRED_FRESH_FRAME_COUNT} fresh frames",
        )
    validated: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        try:
            validated.append(validate_capture_result(frame))
        except PortableNavigationContractError as exc:
            raise _contract_invalid(exc.code, exc.detail, frame_index=index) from exc
    for index, frame in enumerate(validated):
        if frame["captured_unix"] > now_unix + MAX_CLOCK_SKEW_SECONDS:
            raise _hard_fail(
                "future_dated_observation",
                future_dated_source=f"fresh_frame[{index}]",
            )
    return validated


def _validate_map(
    fresh_map: Any,
    fresh_map_request: Any,
    *,
    now_unix: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        request = validate_map_request(fresh_map_request)
        result = validate_map_result(fresh_map, request=request)
    except PortableNavigationContractError as exc:
        raise _contract_invalid(exc.code, exc.detail, source="fresh_map") from exc
    if result["created_unix"] > now_unix + MAX_CLOCK_SKEW_SECONDS:
        raise _hard_fail("future_dated_observation", future_dated_source="fresh_map")
    if request["requested_unix"] > now_unix + MAX_CLOCK_SKEW_SECONDS:
        raise _hard_fail("future_dated_observation", future_dated_source="fresh_map_request")
    return request, result


def _validate_feedback(
    feedback: Any,
    feedback_request: Any,
    *,
    now_unix: float,
) -> dict[str, Any]:
    """Fully validate the arrived feedback against its request and the clock."""

    try:
        request = validate_feedback_request(feedback_request)
        result = validate_feedback_result(feedback, request=request, now_unix=now_unix)
    except PortableNavigationContractError as exc:
        if exc.code == "PORTABLE_TIMESTAMP_IN_FUTURE":
            raise _hard_fail(
                "future_dated_observation", future_dated_source="arrived_feedback"
            ) from exc
        raise _contract_invalid(exc.code, exc.detail, source="arrived_feedback") from exc
    if result["status"] != "arrived" or result["target_reached"] is not True:
        raise _contract_invalid("ARRIVED_FEEDBACK_STATUS_INVALID", source="arrived_feedback")
    velocity = result["velocity_mps"]
    result["speed_mps"] = math.sqrt(sum(component * component for component in velocity))
    return result


def _check_feedback(
    feedback: Mapping[str, Any],
    certification: Mapping[str, Any],
    agent_position: Sequence[float],
    *,
    not_before_unix: float,
) -> dict[str, Any]:
    """Hard safety and consistency checks on the arrived feedback.

    None of these may be downgraded to a semantic fallback: a collision, a
    clearance below ``MINIMUM_ARRIVED_CLEARANCE_M``, a moving agent, a fresh
    map that disagrees with the feedback position, or a cutoff older than the
    feedback observation all mean the arrival claim itself is unsafe.
    """

    if feedback["environment_id"] != certification["environment_id"]:
        raise _hard_fail("environment_mismatch", mismatch_source="arrived_feedback")
    if feedback["session_id"] != certification["session_id"]:
        raise _hard_fail("session_mismatch", mismatch_source="arrived_feedback")
    if feedback["collision_detected"] is not False:
        raise _hard_fail("feedback_collision_detected")
    if feedback["minimum_clearance_m"] < MINIMUM_ARRIVED_CLEARANCE_M - _EPSILON:
        raise _hard_fail(
            "feedback_clearance_violation",
            feedback_minimum_clearance_m=feedback["minimum_clearance_m"],
        )
    if not_before_unix < feedback["observed_unix"] - _EPSILON:
        raise _hard_fail("cutoff_predates_feedback")
    divergence = _planar_distance(agent_position, feedback["position_m"])
    if feedback["speed_mps"] > MAX_ARRIVED_SPEED_MPS + _EPSILON:
        raise _hard_fail(
            "agent_not_stationary",
            arrived_feedback_speed_mps=feedback["speed_mps"],
            feedback_agent_planar_divergence_m=divergence,
        )
    if divergence > MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M + _EPSILON:
        raise _hard_fail(
            "agent_position_inconsistent_with_feedback",
            arrived_feedback_speed_mps=feedback["speed_mps"],
            feedback_agent_planar_divergence_m=divergence,
        )
    return {
        "feedback_environment_session_match": True,
        "feedback_collision_free": True,
        "feedback_clearance_ok": True,
        "cutoff_not_before_feedback": True,
        "agent_stationary": True,
        "agent_position_consistent_with_feedback": True,
    }


def _check_envelope_identity(
    frames: Sequence[Mapping[str, Any]],
    fresh_map: Mapping[str, Any],
    feedback: Mapping[str, Any],
    certification: Mapping[str, Any],
) -> dict[str, Any]:
    envelopes = [(f"fresh_frame[{index}]", frame) for index, frame in enumerate(frames)]
    envelopes.append(("fresh_map", fresh_map))
    envelopes.append(("arrived_feedback", feedback))
    for source, envelope in envelopes:
        if envelope["environment_id"] != certification["environment_id"]:
            raise _hard_fail("environment_mismatch", mismatch_source=source)
        if envelope["session_id"] != certification["session_id"]:
            raise _hard_fail("session_mismatch", mismatch_source=source)
    request_ids = [envelope["request_id"] for _source, envelope in envelopes]
    if len(set(request_ids)) != len(request_ids):
        raise _hard_fail("request_id_duplicate")
    for source, envelope in envelopes:
        if envelope["request_id"] in certification["request_ids"]:
            raise _hard_fail("request_replay", replay_source=source)
    for source, frame in envelopes[:REQUIRED_FRESH_FRAME_COUNT]:
        if frame["frame_id"] in certification["frame_ids"]:
            raise _hard_fail(
                "frame_reused",
                lineage_detail="certification frame presented as fresh",
                reuse_source=source,
            )
    if fresh_map["map_id"] in certification["map_ids"]:
        raise _hard_fail(
            "map_lineage_mismatch",
            lineage_detail="fresh_map.map_id reuses a certification map id",
        )
    return {
        "environment_match": True,
        "session_match": True,
        "request_ids_distinct": True,
        "request_replay_detected": False,
    }


def _check_frame_pair_epoch(
    frames: Sequence[Mapping[str, Any]],
    certification: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    not_before_unix: float,
) -> dict[str, Any]:
    first, second = frames
    if first["frame_id"] == second["frame_id"]:
        raise _hard_fail("frame_reused", frame_ids_distinct=False)
    if not second["captured_unix"] > first["captured_unix"]:
        raise _hard_fail(
            "frame_timestamps_not_monotonic",
            frame_ids_distinct=True,
            timestamps_monotonic=False,
        )
    if not first["captured_unix"] > certification["certified_unix"]:
        raise _hard_fail("fresh_frame_predates_certification")
    if not first["captured_unix"] > not_before_unix:
        raise _hard_fail("fresh_frame_predates_arrival_window")
    for index, frame in enumerate(frames):
        if frame["camera"]["coordinate_frame_id"] != certification["coordinate_frame_id"]:
            raise _hard_fail(
                "coordinate_frame_changed",
                coordinate_frame_source=f"fresh_frame[{index}]",
            )
    if not policy["pose_matches"](first, second):
        raise _hard_fail("camera_pose_changed_between_frames")
    content_identical = (
        first["rgb"]["sha256"] == second["rgb"]["sha256"]
        and _depth_digest(first) == _depth_digest(second)
    )
    return {
        "frame_ids_distinct": True,
        "timestamps_monotonic": True,
        "fresh_frames_after_cutoff": True,
        "synchronized_camera_match": True,
        # Identical bytes across two distinct frames are legal for a static
        # scene rendered deterministically; reported for transparency only.
        "frame_content_identical": content_identical,
    }


def _depth_digest(frame: Mapping[str, Any]) -> str | None:
    depth = frame.get("depth")
    if not isinstance(depth, Mapping):
        return None
    return depth.get("samples_sha256") or depth.get("sha256")


def _check_map(
    fresh_map: Mapping[str, Any],
    map_request: Mapping[str, Any],
    newest_frame: Mapping[str, Any],
    certification: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if fresh_map["coordinate_frame_id"] != certification["coordinate_frame_id"]:
        raise _hard_fail("coordinate_frame_changed", coordinate_frame_source="fresh_map")
    if fresh_map["source_frame_id"] != newest_frame["frame_id"]:
        raise _hard_fail(
            "map_lineage_mismatch",
            lineage_detail="fresh_map.source_frame_id != newest fresh frame_id",
        )
    if fresh_map["created_unix"] < newest_frame["captured_unix"] - MAX_CLOCK_SKEW_SECONDS:
        raise _hard_fail(
            "map_lineage_mismatch",
            lineage_detail="fresh_map.created_unix predates its source frame",
        )
    if not math.isclose(
        map_request["resolution_m"], FRESH_MAP_RESOLUTION_M, rel_tol=0.0, abs_tol=1e-9
    ) or not math.isclose(
        fresh_map["grid"]["resolution_m"], FRESH_MAP_RESOLUTION_M, rel_tol=0.0, abs_tol=1e-9
    ):
        raise _hard_fail("map_policy_mismatch", map_policy_field="resolution_m")
    if (
        map_request["footprint_radius_m"] < FRESH_MAP_MINIMUM_FOOTPRINT_RADIUS_M - _EPSILON
        or fresh_map["agent"]["footprint_radius_m"] < FRESH_MAP_MINIMUM_FOOTPRINT_RADIUS_M - _EPSILON
    ):
        raise _hard_fail("map_policy_mismatch", map_policy_field="footprint_radius_m")
    agent_position = list(fresh_map["agent"]["position_m"])
    try:
        # A zero-length segment reuses the shared collision checker while
        # still distinguishing an out-of-bounds cell from a blocked one.  The
        # additional radius is a binary occupancy inflation, not a measured
        # clearance distance.
        report = path_collision_report(
            fresh_map,
            [agent_position, agent_position],
            additional_inflation_m=AGENT_ADDITIONAL_INFLATION_M,
        )
    except PortableNavigationCoreError as exc:
        raise _contract_invalid(exc.code, exc.detail, source="fresh_map") from exc
    proof = {
        "kind": "binary_inflated_occupancy_proof",
        "additional_inflation_m": AGENT_ADDITIONAL_INFLATION_M,
        "footprint_radius_m": fresh_map["agent"]["footprint_radius_m"],
        "numeric_clearance_measured": False,
        "agent_cell_free": report["valid"] is True,
        "reason": report.get("reason"),
    }
    if report["valid"] is not True:
        reason = report.get("reason")
        raise _hard_fail(
            "agent_position_out_of_bounds"
            if reason == "out_of_bounds"
            else "agent_position_blocked_after_inflation",
            map_safety_reason=reason,
            inflated_occupancy_proof=proof,
        )
    return {
        "map_coordinate_frame_match": True,
        "map_lineage_match": True,
        "map_policy_match": True,
        "agent_cell_free_after_additional_inflation": True,
    }, proof


def _planar_distance(agent: Sequence[float], point: Sequence[float]) -> float:
    return math.hypot(agent[0] - point[0], agent[1] - point[1])


def count_exact_reference_rays(
    projected_rays: Sequence[Mapping[str, Any]],
    *,
    reference_surface_id: str,
    reference_surface_role: str,
    reference_points: Sequence[Sequence[float]],
    maximum_distance_m: float,
) -> dict[str, Any]:
    """Count unique-UV rays that exactly attest the certified surface.

    A ray counts only when its opaque ``surface_id`` and ``surface_role`` both
    equal the certification and its 3-D position is within
    ``maximum_distance_m`` of the nearest initially certified point.  Same-id
    rays farther away, rays on other ids, unidentified rays, rays whose role is
    missing, and repeated UV coordinates are excluded and reported as counts.
    A same-id ray carrying a *different* role is a contradiction and is
    reported separately so the caller can fail closed.  Confidence filtering
    happens before this function (see ``MINIMUM_RAY_CONFIDENCE``).
    """

    unique_valid: dict[tuple[float, float], dict[str, Any]] = {}
    duplicate_uv_excluded = 0
    far_same_id = 0
    other_id = 0
    unidentified = 0
    role_missing = 0
    role_conflict = 0
    for ray in projected_rays:
        surface_id = ray.get("surface_id")
        if surface_id is None:
            unidentified += 1
            continue
        if surface_id != reference_surface_id:
            other_id += 1
            continue
        role = ray.get("surface_role")
        if role is None:
            role_missing += 1
            continue
        if role != reference_surface_role:
            role_conflict += 1
            continue
        position = ray["position_m"]
        nearest = min(math.dist(position, reference) for reference in reference_points)
        if nearest > maximum_distance_m + _EPSILON:
            far_same_id += 1
            continue
        uv = (float(ray["uv_norm"][0]), float(ray["uv_norm"][1]))
        if uv in unique_valid:
            duplicate_uv_excluded += 1
            continue
        unique_valid[uv] = {
            "position_m": [float(component) for component in position],
            "nearest_reference_distance_m": nearest,
        }
    return {
        "exact_reference_ray_count": len(unique_valid),
        "exact_reference_rays": list(unique_valid.values()),
        "duplicate_uv_excluded": duplicate_uv_excluded,
        "reference_rays_beyond_maximum_distance_excluded": far_same_id,
        "nonreference_ray_count_excluded": other_id,
        "unidentified_ray_count_excluded": unidentified,
        "reference_role_missing_ray_count_excluded": role_missing,
        "reference_role_conflict_ray_count": role_conflict,
        "surface_id_match": bool(unique_valid) or far_same_id > 0 or role_missing > 0 or role_conflict > 0,
        "surface_role_match": bool(unique_valid) or far_same_id > 0,
    }


def _evaluate_frame_rays(
    frame_index: int,
    frame: Mapping[str, Any],
    certification: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    depth = frame.get("depth")
    audit: dict[str, Any] = {
        "frame_index": frame_index,
        "frame_id": frame["frame_id"],
        "captured_unix": frame["captured_unix"],
        "depth_available": depth is not None,
        "semantics_revision_match": False,
        "exact_reference_ray_count": 0,
        "minimum_ray_confidence": MINIMUM_RAY_CONFIDENCE,
    }
    if depth is None:
        audit["insufficiency"] = "depth_unavailable"
        return audit
    semantics_revision = depth.get("surface_id_semantics_revision")
    audit["semantics_revision_match"] = (
        semantics_revision == SURFACE_ID_SEMANTICS_REVISION
        and semantics_revision == certification["surface_id_semantics_revision"]
    )
    try:
        projected = project_depth_samples(frame, min_confidence=MINIMUM_RAY_CONFIDENCE)
    except (PortableNavigationContractError, PortableNavigationCoreError) as exc:
        raise _contract_invalid(exc.code, exc.detail, frame_index=frame_index) from exc
    if depth.get("representation") == "sparse_rays":
        audit["low_confidence_rays_excluded"] = len(depth["samples"]) - len(projected)
    else:
        audit["low_confidence_rays_excluded"] = None
    counts = count_exact_reference_rays(
        projected,
        reference_surface_id=certification["surface_id"],
        reference_surface_role=certification["surface_role"],
        reference_points=certification["surface_points_m"],
        maximum_distance_m=policy["reference_max_distance_m"],
    )
    audit.update(
        projected_ray_count=len(projected),
        exact_reference_ray_count=counts["exact_reference_ray_count"],
        duplicate_uv_excluded=counts["duplicate_uv_excluded"],
        reference_rays_beyond_maximum_distance_excluded=(
            counts["reference_rays_beyond_maximum_distance_excluded"]
        ),
        nonreference_ray_count_excluded=counts["nonreference_ray_count_excluded"],
        unidentified_ray_count_excluded=counts["unidentified_ray_count_excluded"],
        reference_role_missing_ray_count_excluded=(
            counts["reference_role_missing_ray_count_excluded"]
        ),
        reference_role_conflict_ray_count=counts["reference_role_conflict_ray_count"],
        surface_id_match=counts["surface_id_match"],
        surface_role_match=counts["surface_role_match"],
    )
    audit["_exact_reference_rays"] = counts["exact_reference_rays"]
    if counts["reference_role_conflict_ray_count"] > 0:
        audit["contradiction"] = "surface_role_contradiction"
    elif not audit["semantics_revision_match"]:
        # The contract already rejects a *wrong* revision string, so reaching
        # here means the revision is absent: identity is unavailable.
        audit["insufficiency"] = "surface_identity_unavailable"
    elif counts["exact_reference_ray_count"] < MINIMUM_EXACT_REFERENCE_RAYS_PER_FRAME:
        if counts["exact_reference_ray_count"] == 0 and not counts["surface_id_match"]:
            audit["insufficiency"] = "reference_surface_not_observed"
        else:
            audit["insufficiency"] = "insufficient_exact_reference_rays"
    return audit


def evaluate_arrival_pose_epoch_fast_path(
    initial_certification: Mapping[str, Any],
    fresh_frames: Sequence[Mapping[str, Any]],
    fresh_map: Mapping[str, Any],
    *,
    fresh_map_request: Mapping[str, Any],
    arrived_feedback: Mapping[str, Any],
    arrived_feedback_request: Mapping[str, Any],
    arrival_radius_m: float,
    not_before_unix: float,
    now_unix: float,
) -> dict[str, Any]:
    """Classify an arrival claim as ``pass``, ``fallback_eligible`` or ``hard_fail``.

    Parameters
    ----------
    initial_certification
        Mapping recorded once after the dual grounding at recognition time (see
        ``INITIAL_CERTIFICATION_DIGEST_FIELDS`` and
        ``compute_initial_certification_digest``).
    fresh_frames
        Exactly two portable capture results in chronological order, both
        captured after ``not_before_unix`` and after the certification.
        Neither may be a certification frame.
    fresh_map, fresh_map_request
        Portable map result and the request that produced it.  The request's
        ``source_frame_id`` must be the newest fresh frame, its resolution must
        be ``FRESH_MAP_RESOLUTION_M`` and its footprint at least
        ``FRESH_MAP_MINIMUM_FOOTPRINT_RADIUS_M``.
    arrived_feedback, arrived_feedback_request
        The ``arrived`` motion feedback and the request that produced it.  The
        pair is re-validated with the contract (``validate_feedback_result``
        with request and clock).  The feedback must belong to the certified
        environment/session, report no collision, keep at least
        ``MINIMUM_ARRIVED_CLEARANCE_M``, agree with the fresh map's agent
        position within ``MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M`` and be
        stationary (``MAX_ARRIVED_SPEED_MPS``); each violation is a hard failure.
    arrival_radius_m
        The unchanged arrival radius selected during planning.  The maximum
        allowed planar distance is ``arrival_radius_m`` plus the existing
        ``ARRIVAL_REVALIDATION_TOLERANCE_M``; no grounding uncertainty is added,
        so this fast path is at least as strict as the existing slow path.
    not_before_unix
        Required cutoff (``max(previous_capture.captured_unix,
        feedback.observed_unix)`` at integration).  Both fresh frames must be
        newer than it.
    now_unix
        Required wall-clock reference.  Frames or maps dated after it (beyond
        the contract clock skew) are hard failures.

    The function is pure: it performs no I/O and calls no model or adapter.
    All hard-fail conditions are evaluated before any fallback condition so a
    violation can never be downgraded to "insufficient observation".
    """

    result: dict[str, Any] = {
        "revision": ARRIVAL_POSE_EPOCH_FAST_PATH_REVISION,
        "outcome": None,
        "reason": None,
        "semantic_revalidation_performed": False,
        "semantic_change_detectable": False,
        "appearance_material_category_changes_detectable": False,
        "qwen_invoked": False,
        "io_performed": False,
        "engine_specific_identifiers_used": False,
        "certification_digest_role": "integrity_only_not_authentication",
        "required_surface_role": REQUIRED_SURFACE_ROLE,
        "policy": {
            "minimum_exact_reference_rays_per_frame": MINIMUM_EXACT_REFERENCE_RAYS_PER_FRAME,
            "minimum_ray_confidence": MINIMUM_RAY_CONFIDENCE,
            "agent_additional_inflation_m": AGENT_ADDITIONAL_INFLATION_M,
            "fresh_map_resolution_m": FRESH_MAP_RESOLUTION_M,
            "fresh_map_minimum_footprint_radius_m": FRESH_MAP_MINIMUM_FOOTPRINT_RADIUS_M,
            "max_feedback_agent_planar_divergence_m": MAX_FEEDBACK_AGENT_PLANAR_DIVERGENCE_M,
            "max_arrived_speed_mps": MAX_ARRIVED_SPEED_MPS,
            "minimum_arrived_clearance_m": MINIMUM_ARRIVED_CLEARANCE_M,
            "max_certified_surface_points": MAX_CERTIFIED_SURFACE_POINTS,
            "dual_grounding_match_required": True,
            "pass_requires_expected_and_actual_role": REQUIRED_SURFACE_ROLE,
        },
        "checks": {},
        "frames": [],
        "arrival_distance_m": None,
        "maximum_allowed_distance_m": None,
    }
    checks = result["checks"]
    try:
        if not _finite_number(arrival_radius_m) or arrival_radius_m <= 0:
            raise _contract_invalid("ARRIVAL_RADIUS_INVALID")
        if not _finite_number(not_before_unix):
            raise _contract_invalid("NOT_BEFORE_UNIX_INVALID")
        if not _finite_number(now_unix):
            raise _contract_invalid("NOW_UNIX_INVALID")
        policy = _shared_policy()
        result["policy"]["reference_surface_maximum_distance_m"] = policy["reference_max_distance_m"]
        result["policy"]["arrival_tolerance_m"] = policy["arrival_tolerance_m"]
        result["reference_surface_maximum_distance_m"] = policy["reference_max_distance_m"]
        result["arrival_tolerance_m"] = policy["arrival_tolerance_m"]
        result["arrival_radius_m"] = float(arrival_radius_m)
        result["not_before_unix"] = float(not_before_unix)
        result["now_unix"] = float(now_unix)
        result["maximum_allowed_distance_m"] = (
            float(arrival_radius_m) + policy["arrival_tolerance_m"]
        )

        certification = _validate_certification(initial_certification, policy)
        checks["initial_certification_digest_match"] = True
        checks["initial_surface_digest_match"] = True
        frames = _validate_frames(fresh_frames, now_unix=float(now_unix))
        map_request, validated_map = _validate_map(
            fresh_map, fresh_map_request, now_unix=float(now_unix)
        )
        feedback = _validate_feedback(
            arrived_feedback, arrived_feedback_request, now_unix=float(now_unix)
        )
        checks["contract_valid"] = True
        checks["not_future_dated"] = True

        checks.update(_check_envelope_identity(frames, validated_map, feedback, certification))
        checks.update(
            _check_frame_pair_epoch(
                frames,
                certification,
                policy,
                not_before_unix=float(not_before_unix),
            )
        )
        checks["coordinate_frame_match"] = True
        map_checks, proof = _check_map(validated_map, map_request, frames[-1], certification)
        checks.update(map_checks)
        result["inflated_occupancy_proof"] = proof
        agent_position = validated_map["agent"]["position_m"]
        result.update(
            fresh_agent_position_m=[float(component) for component in agent_position],
            fresh_agent_position_basis="fresh_map_agent_synchronized_to_newest_frame",
            feedback_agent_planar_divergence_m=_planar_distance(
                agent_position, feedback["position_m"]
            ),
            arrived_feedback_speed_mps=feedback["speed_mps"],
            arrived_feedback_minimum_clearance_m=feedback["minimum_clearance_m"],
        )
        checks.update(
            _check_feedback(
                feedback,
                certification,
                agent_position,
                not_before_unix=float(not_before_unix),
            )
        )

        # --- Everything below can only pass or become fallback-eligible. ---
        if certification["surface_id"] is None:
            raise _fallback("certification_surface_identity_unavailable")
        if certification["surface_role"] is None:
            raise _fallback("certification_surface_role_unavailable")
        if certification["expected_surface_role"] is None:
            raise _fallback("expected_surface_role_unavailable")
        if (
            certification["surface_role"] != REQUIRED_SURFACE_ROLE
            or certification["expected_surface_role"] != REQUIRED_SURFACE_ROLE
        ):
            raise _fallback(
                "fast_path_role_not_supported",
                certification_surface_role=certification["surface_role"],
                certification_expected_surface_role=certification["expected_surface_role"],
            )
        checks["certification_surface_role_supported"] = True
        checks["expected_and_actual_role_match_required_role"] = True

        frame_audits = [
            _evaluate_frame_rays(index, frame, certification, policy)
            for index, frame in enumerate(frames)
        ]
        exact_rays = [audit.pop("_exact_reference_rays", []) for audit in frame_audits]
        result["frames"] = frame_audits
        contradictions = [audit for audit in frame_audits if "contradiction" in audit]
        if contradictions:
            raise _hard_fail(
                contradictions[0]["contradiction"],
                frame_index=contradictions[0]["frame_index"],
            )
        checks["surface_role_consistent"] = True
        checks["semantics_revision_match_all_frames"] = all(
            audit["semantics_revision_match"] for audit in frame_audits
        )
        checks["surface_id_match_all_frames"] = all(
            audit.get("surface_id_match") is True for audit in frame_audits
        )
        checks["surface_role_match_all_frames"] = all(
            audit.get("surface_role_match") is True for audit in frame_audits
        )

        insufficient = [audit for audit in frame_audits if "insufficiency" in audit]
        if insufficient:
            raise _fallback(
                insufficient[0]["insufficiency"],
                frame_index=insufficient[0]["frame_index"],
            )
        checks["dual_fresh_exact_reference_rays"] = True

        newest_rays = exact_rays[-1]
        distances = sorted(
            _planar_distance(agent_position, ray["position_m"]) for ray in newest_rays
        )
        arrival_distance = distances[0]
        result.update(
            arrival_distance_m=arrival_distance,
            arrival_distance_basis="newest_fresh_frame_exact_reference_rays_min_planar",
            newest_frame_exact_reference_ray_count=len(newest_rays),
        )
        if arrival_distance > result["maximum_allowed_distance_m"] + _EPSILON:
            # Identity and continuity are intact, so a moved target cannot be
            # the explanation (its id would have changed).  The fast path
            # cannot distinguish viewpoint sampling bias from a short stop, so
            # it declines to confirm and leaves the decision to the full path.
            raise _fallback("arrival_distance_not_confirmed")
        checks["arrival_distance_within_unchanged_threshold"] = True
        result["outcome"] = OUTCOME_PASS
        result["reason"] = PASS_REASON
    except _Decision as decision:
        result["outcome"] = decision.outcome
        result["reason"] = decision.reason
        for key, value in decision.details.items():
            result[key] = value
        if decision.outcome == OUTCOME_HARD_FAIL:
            for key in (
                "initial_certification_digest_match",
                "initial_surface_digest_match",
            ):
                if key in decision.details:
                    checks[key] = decision.details[key]
    result["qwen_fallback_allowed"] = result["outcome"] == OUTCOME_FALLBACK_ELIGIBLE
    result["arrival_confirmed"] = result["outcome"] == OUTCOME_PASS
    assert result["outcome"] in OUTCOMES
    assert result["reason"] in REASONS_BY_OUTCOME[result["outcome"]], result["reason"]
    return result
