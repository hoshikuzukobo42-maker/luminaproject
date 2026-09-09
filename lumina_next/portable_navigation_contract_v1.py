"""Engine-neutral I/O contracts for visual navigation.

This module is deliberately independent from Godot, Unity, Unreal, ROS, and
any particular model.  Environment adapters translate native sensor and motion
APIs into these JSON-compatible envelopes.  The navigation core can therefore
depend on validated RGB/depth, occupancy, grounding, command ownership, and
feedback without depending on scene-node names or engine coordinates.

Navigation command ownership is established before dispatch: the caller puts
its preallocated ``command_id`` in every navigate request, and an accepted
result must echo that exact id.  This lets cancellation address a command even
when the adapter accepts it but its response is delayed or lost.

All positions and orientations crossing this boundary use the canonical frame
``right_handed_x_forward_y_left_z_up``.  Camera intrinsics use the conventional
optical frame (+x right, +y down, +z forward); ``orientation_xyzw`` rotates that
optical frame into the canonical coordinate frame.

Dense depth is row-major.  Its normalized RGB alignment permits a lower depth
resolution than the RGB image.  ``optical_axis_z_m`` measures along optical +z;
``ray_range_m`` measures Euclidean range from the camera origin.  Sparse depth
always uses ray range, and an omitted image location is unknown rather than
free space.  Occupancy bytes are row-major probabilities: cell ``(column,row)``
is byte ``row * width + column`` in the grid-local XY plane.  The grid origin
pose transforms grid-local +x/+y/+z into the canonical frame.

An adapter-provided ``surface_id`` is a process/session-local pose-epoch
identity for exactly one collision shape (or one indivisible connected
physical component when the engine cannot expose shapes separately).  One id
must never be reused for a distinct shape/component.  It must also change when
that component's world pose or collision geometry changes, including changes
smaller than the navigation-grid resolution.  Therefore an exact id match
between initial and fresh arrival captures is evidence that the same physical
component was observed without an intervening pose/geometry change; it is not
a persistent semantic object identifier.  If an adapter cannot guarantee
these semantics, it must omit the id (and therefore the paired role), causing
identity-dependent recovery to fail closed.  Every occurrence of one id in a
capture must report one role.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from typing import Any, Mapping, Protocol, runtime_checkable


CAPTURE_REQUEST_SCHEMA = "lumina.portable.capture.request.v1"
CAPTURE_RESULT_SCHEMA = "lumina.portable.capture.result.v1"
MAP_REQUEST_SCHEMA = "lumina.portable.map.request.v1"
MAP_RESULT_SCHEMA = "lumina.portable.map.result.v1"
GROUND_REQUEST_SCHEMA = "lumina.portable.ground.request.v1"
GROUND_RESULT_SCHEMA = "lumina.portable.ground.result.v1"
NAVIGATE_REQUEST_SCHEMA = "lumina.portable.navigate.request.v1"
NAVIGATE_RESULT_SCHEMA = "lumina.portable.navigate.result.v1"
STOP_REQUEST_SCHEMA = "lumina.portable.stop.request.v1"
STOP_RESULT_SCHEMA = "lumina.portable.stop.result.v1"
FEEDBACK_REQUEST_SCHEMA = "lumina.portable.feedback.request.v1"
FEEDBACK_RESULT_SCHEMA = "lumina.portable.feedback.result.v1"

COORDINATE_CONVENTION = "right_handed_x_forward_y_left_z_up"
SURFACE_ID_SEMANTICS_REVISION = "single_connected_component_world_pose_epoch_v2"
RGB_ENCODINGS = {"png", "jpeg"}
DEPTH_ENCODINGS = {"f32_le_m", "u16_le_mm"}
DEPTH_ALIGNMENTS = {"registered_normalized_to_rgb"}
DEPTH_MEASUREMENT_MODELS = {"optical_axis_z_m", "ray_range_m"}
SURFACE_ROLES = frozenset({
    "navigation_obstacle",
    "walkable_ground",
    "physical_surface",
})
GROUND_STATUSES = {"grounded", "unresolved"}
GROUND_FAILURE_REASONS = {
    "ambiguous",
    "insufficient_depth_support",
    "no_depth",
    "out_of_range",
    "not_visible",
    "stale_frame",
    "unsupported",
}
NAVIGATE_STATUSES = {"accepted", "rejected"}
FEEDBACK_STATUSES = {"queued", "moving", "arrived", "stopped", "blocked", "failed"}
STOP_STATUSES = {"confirmed", "already_stopped", "rejected"}

MAX_FRAME_DIMENSION_PX = 8192
MAX_INLINE_PLANE_BYTES = 64 * 1024 * 1024
MAX_GRID_DIMENSION_CELLS = 4096
MAX_GRID_CELLS = 16 * 1024 * 1024
MAX_WAYPOINTS = 4096
MAX_SPARSE_DEPTH_SAMPLES = 65_536
MAX_GROUND_OCCLUDER_BBOXES = 8
MAX_GROUND_SURFACE_POINTS = 256
MAX_GROUND_CONTINUITY_DISTANCE_M = 0.45
MAX_CLOCK_SKEW_SECONDS = 1.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TARGET_ID_PATTERN = re.compile(r"^target:[0-9a-f]{16,64}$")
_SURFACE_ID_PATTERN = re.compile(r"^surface:[0-9a-f]{64}$")


class PortableNavigationContractError(ValueError):
    """A fail-closed contract violation with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@runtime_checkable
class PortableNavigationAdapter(Protocol):
    """Minimal asynchronous boundary implemented by each environment.

    Implementations may use native APIs internally, but values returned across
    this protocol must pass the corresponding validator in this module.
    """

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def navigate(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def stop(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def feedback(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PortableNavigationContractError("PORTABLE_OBJECT_REQUIRED", field_name)
    return value


def _fields(value: Mapping[str, Any], required: set[str], optional: set[str], field_name: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise PortableNavigationContractError("PORTABLE_FIELDS_MISSING", f"{field_name}:{','.join(missing)}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        raise PortableNavigationContractError("PORTABLE_FIELDS_UNKNOWN", f"{field_name}:{','.join(unknown)}")


def _schema(value: Mapping[str, Any], expected: str, field_name: str) -> None:
    if value.get("schema_version") != expected:
        raise PortableNavigationContractError("PORTABLE_SCHEMA_MISMATCH", field_name)


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise PortableNavigationContractError("PORTABLE_ID_INVALID", field_name)
    return value


def _target_identifier(value: Any, field_name: str) -> str:
    """Require an opaque core-owned id, never an engine node/object name."""

    if not isinstance(value, str) or not _TARGET_ID_PATTERN.fullmatch(value):
        raise PortableNavigationContractError("PORTABLE_TARGET_ID_NOT_OPAQUE", field_name)
    return value


def _surface_identifier(value: Any, field_name: str) -> str:
    """Require opaque pose-epoch identity for one physical component."""

    if not isinstance(value, str) or not _SURFACE_ID_PATTERN.fullmatch(value):
        raise PortableNavigationContractError("PORTABLE_SURFACE_ID_NOT_OPAQUE", field_name)
    return value


def _surface_role(value: Any, field_name: str) -> str:
    """Require one portable, engine-neutral physical surface role."""

    if not isinstance(value, str) or value not in SURFACE_ROLES:
        raise PortableNavigationContractError("PORTABLE_SURFACE_ROLE_INVALID", field_name)
    return value


def _text(value: Any, field_name: str, *, maximum: int = 256, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise PortableNavigationContractError("PORTABLE_TEXT_INVALID", field_name)
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise PortableNavigationContractError("PORTABLE_BOOLEAN_INVALID", field_name)
    return value


def _number(
    value: Any,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PortableNavigationContractError("PORTABLE_NUMBER_INVALID", field_name)
    result = float(value)
    if minimum is not None and result < minimum:
        raise PortableNavigationContractError("PORTABLE_NUMBER_RANGE", field_name)
    if maximum is not None and result > maximum:
        raise PortableNavigationContractError("PORTABLE_NUMBER_RANGE", field_name)
    return result


def _integer(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PortableNavigationContractError("PORTABLE_INTEGER_INVALID", field_name)
    return value


def _timestamp(value: Any, field_name: str) -> float:
    return _number(value, field_name, minimum=0.0)


def _vector3(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise PortableNavigationContractError("PORTABLE_VECTOR3_INVALID", field_name)
    return [_number(component, f"{field_name}[{index}]") for index, component in enumerate(value)]


def _quaternion(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PortableNavigationContractError("PORTABLE_QUATERNION_INVALID", field_name)
    result = [_number(component, f"{field_name}[{index}]") for index, component in enumerate(value)]
    norm = math.sqrt(sum(component * component for component in result))
    if not 0.999 <= norm <= 1.001:
        raise PortableNavigationContractError("PORTABLE_QUATERNION_NOT_NORMALIZED", field_name)
    return result


def _bbox(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PortableNavigationContractError("PORTABLE_BBOX_INVALID", field_name)
    result = [_number(component, f"{field_name}[{index}]", minimum=0.0, maximum=1.0)
              for index, component in enumerate(value)]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise PortableNavigationContractError("PORTABLE_BBOX_INVALID", field_name)
    return result


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise PortableNavigationContractError("PORTABLE_SHA256_INVALID", field_name)
    return value


def _inline_bytes(value: Any, expected_sha256: str, field_name: str) -> bytes:
    if not isinstance(value, str) or len(value) > (MAX_INLINE_PLANE_BYTES * 4 // 3 + 8):
        raise PortableNavigationContractError("PORTABLE_INLINE_DATA_INVALID", field_name)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PortableNavigationContractError("PORTABLE_BASE64_INVALID", field_name) from exc
    if len(raw) > MAX_INLINE_PLANE_BYTES:
        raise PortableNavigationContractError("PORTABLE_INLINE_DATA_TOO_LARGE", field_name)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise PortableNavigationContractError("PORTABLE_DATA_HASH_MISMATCH", field_name)
    return raw


def _canonical_json(value: Any, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PortableNavigationContractError("PORTABLE_NONCANONICAL_JSON", field_name) from exc


def _canonical_sha256(value: Any, field_name: str) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PortableNavigationContractError("PORTABLE_NONCANONICAL_JSON", field_name) from exc
    return hashlib.sha256(encoded).hexdigest()


def _common_request(
    value: Any,
    schema: str,
    extra: set[str],
    optional: set[str] | None = None,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    source = _mapping(value, "request")
    required = {"schema_version", "request_id", "environment_id", "session_id", "requested_unix"} | extra
    _fields(source, required, optional or set(), "request")
    _schema(source, schema, "request.schema_version")
    normalized = {
        "schema_version": schema,
        "request_id": _identifier(source.get("request_id"), "request.request_id"),
        "environment_id": _identifier(source.get("environment_id"), "request.environment_id"),
        "session_id": _identifier(source.get("session_id"), "request.session_id"),
        "requested_unix": _timestamp(source.get("requested_unix"), "request.requested_unix"),
    }
    return source, normalized


def _common_result(
    value: Any,
    schema: str,
    extra: set[str],
    optional: set[str] | None = None,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    source = _mapping(value, "result")
    required = {"schema_version", "request_id", "environment_id", "session_id"} | extra
    _fields(source, required, optional or set(), "result")
    _schema(source, schema, "result.schema_version")
    normalized = {
        "schema_version": schema,
        "request_id": _identifier(source.get("request_id"), "result.request_id"),
        "environment_id": _identifier(source.get("environment_id"), "result.environment_id"),
        "session_id": _identifier(source.get("session_id"), "result.session_id"),
    }
    return source, normalized


def _cross_common(request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    for field_name in ("request_id", "environment_id", "session_id"):
        if request.get(field_name) != result.get(field_name):
            raise PortableNavigationContractError("PORTABLE_LINEAGE_MISMATCH", field_name)


def _not_future(timestamp: float, now_unix: float | None, field_name: str) -> None:
    if now_unix is None:
        return
    now = _timestamp(now_unix, "now_unix")
    if timestamp > now + MAX_CLOCK_SKEW_SECONDS:
        raise PortableNavigationContractError("PORTABLE_TIMESTAMP_IN_FUTURE", field_name)


def _validate_rgb_plane(value: Any) -> dict[str, Any]:
    source = _mapping(value, "result.rgb")
    _fields(source, {"encoding", "width_px", "height_px", "data_base64", "sha256"}, set(), "result.rgb")
    encoding = source.get("encoding")
    if encoding not in RGB_ENCODINGS:
        raise PortableNavigationContractError("PORTABLE_RGB_ENCODING_UNSUPPORTED", "result.rgb.encoding")
    width = _integer(source.get("width_px"), "result.rgb.width_px", minimum=1, maximum=MAX_FRAME_DIMENSION_PX)
    height = _integer(source.get("height_px"), "result.rgb.height_px", minimum=1, maximum=MAX_FRAME_DIMENSION_PX)
    digest = _sha256(source.get("sha256"), "result.rgb.sha256")
    raw = _inline_bytes(source.get("data_base64"), digest, "result.rgb.data_base64")
    if not raw:
        raise PortableNavigationContractError("PORTABLE_RGB_EMPTY", "result.rgb.data_base64")
    if encoding == "png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PortableNavigationContractError("PORTABLE_RGB_SIGNATURE_INVALID", "result.rgb.data_base64")
    if encoding == "jpeg" and not raw.startswith(b"\xff\xd8"):
        raise PortableNavigationContractError("PORTABLE_RGB_SIGNATURE_INVALID", "result.rgb.data_base64")
    return {"encoding": encoding, "width_px": width, "height_px": height,
            "data_base64": source["data_base64"], "sha256": digest}


def _validate_depth(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    source = _mapping(value, "result.depth")
    representation = source.get("representation")
    if representation == "dense":
        _fields(
            source,
            {
                "representation", "encoding", "measurement_model", "width_px", "height_px", "alignment",
                "invalid_value", "data_base64", "sha256",
            },
            set(),
            "result.depth",
        )
        alignment = source.get("alignment")
        if alignment not in DEPTH_ALIGNMENTS:
            raise PortableNavigationContractError("PORTABLE_DEPTH_ALIGNMENT_UNSUPPORTED", "result.depth.alignment")
        encoding = source.get("encoding")
        if encoding not in DEPTH_ENCODINGS:
            raise PortableNavigationContractError("PORTABLE_DEPTH_ENCODING_UNSUPPORTED", "result.depth.encoding")
        measurement_model = source.get("measurement_model")
        if measurement_model not in DEPTH_MEASUREMENT_MODELS:
            raise PortableNavigationContractError(
                "PORTABLE_DEPTH_MEASUREMENT_MODEL_UNSUPPORTED", "result.depth.measurement_model"
            )
        invalid_value = source.get("invalid_value")
        if encoding == "f32_le_m":
            if invalid_value not in {"nan", "zero"}:
                raise PortableNavigationContractError("PORTABLE_DEPTH_INVALID_VALUE_UNSUPPORTED", "result.depth.invalid_value")
        elif type(invalid_value) is not int or not 0 <= invalid_value <= 65535:
            raise PortableNavigationContractError("PORTABLE_DEPTH_INVALID_VALUE_UNSUPPORTED", "result.depth.invalid_value")
        width = _integer(source.get("width_px"), "result.depth.width_px", minimum=1, maximum=MAX_FRAME_DIMENSION_PX)
        height = _integer(source.get("height_px"), "result.depth.height_px", minimum=1, maximum=MAX_FRAME_DIMENSION_PX)
        digest = _sha256(source.get("sha256"), "result.depth.sha256")
        raw = _inline_bytes(source.get("data_base64"), digest, "result.depth.data_base64")
        bytes_per_sample = 4 if encoding == "f32_le_m" else 2
        if len(raw) != width * height * bytes_per_sample:
            raise PortableNavigationContractError("PORTABLE_DEPTH_SIZE_MISMATCH", "result.depth.data_base64")
        return {
            "representation": "dense",
            "encoding": encoding,
            "measurement_model": measurement_model,
            "invalid_value": invalid_value,
            "width_px": width,
            "height_px": height,
            "alignment": alignment,
            "data_base64": source["data_base64"],
            "sha256": digest,
        }
    if representation == "sparse_rays":
        _fields(
            source,
            {"representation", "measurement_model", "alignment", "samples", "samples_sha256"},
            {"surface_id_semantics_revision"},
            "result.depth",
        )
        alignment = source.get("alignment")
        if alignment not in DEPTH_ALIGNMENTS:
            raise PortableNavigationContractError("PORTABLE_DEPTH_ALIGNMENT_UNSUPPORTED", "result.depth.alignment")
        if source.get("measurement_model") != "ray_range_m":
            raise PortableNavigationContractError(
                "PORTABLE_DEPTH_MEASUREMENT_MODEL_UNSUPPORTED", "result.depth.measurement_model"
            )
        raw_samples = source.get("samples")
        if not isinstance(raw_samples, list) or not 1 <= len(raw_samples) <= MAX_SPARSE_DEPTH_SAMPLES:
            raise PortableNavigationContractError("PORTABLE_DEPTH_SAMPLES_INVALID", "result.depth.samples")
        samples: list[dict[str, Any]] = []
        seen_uv: set[tuple[float, float]] = set()
        roles_by_surface_id: dict[str, str] = {}
        has_surface_identity = False
        for index, raw_sample in enumerate(raw_samples):
            sample = _mapping(raw_sample, f"result.depth.samples[{index}]")
            _fields(
                sample,
                {"uv_norm", "distance_m", "confidence"},
                {"surface_id", "surface_role"},
                f"result.depth.samples[{index}]",
            )
            uv = sample.get("uv_norm")
            if not isinstance(uv, (list, tuple)) or len(uv) != 2:
                raise PortableNavigationContractError("PORTABLE_DEPTH_SAMPLE_UV_INVALID", f"result.depth.samples[{index}]")
            normalized_uv = [
                _number(uv[0], f"result.depth.samples[{index}].uv_norm[0]", minimum=0.0, maximum=1.0),
                _number(uv[1], f"result.depth.samples[{index}].uv_norm[1]", minimum=0.0, maximum=1.0),
            ]
            uv_key = normalized_uv[0], normalized_uv[1]
            if uv_key in seen_uv:
                raise PortableNavigationContractError("PORTABLE_DEPTH_SAMPLE_DUPLICATE", f"result.depth.samples[{index}]")
            seen_uv.add(uv_key)
            normalized_sample = {
                "uv_norm": normalized_uv,
                "distance_m": _number(
                    sample.get("distance_m"), f"result.depth.samples[{index}].distance_m", minimum=0.001, maximum=10000.0
                ),
                "confidence": _number(
                    sample.get("confidence"), f"result.depth.samples[{index}].confidence", minimum=0.0, maximum=1.0
                ),
            }
            if "surface_id" in sample:
                normalized_sample["surface_id"] = _surface_identifier(
                    sample.get("surface_id"),
                    f"result.depth.samples[{index}].surface_id",
                )
                has_surface_identity = True
            if "surface_role" in sample:
                normalized_sample["surface_role"] = _surface_role(
                    sample.get("surface_role"),
                    f"result.depth.samples[{index}].surface_role",
                )
                if "surface_id" not in normalized_sample:
                    raise PortableNavigationContractError(
                        "PORTABLE_SURFACE_ROLE_REQUIRES_SURFACE_ID",
                        f"result.depth.samples[{index}].surface_role",
                    )
                surface_id = normalized_sample["surface_id"]
                surface_role = normalized_sample["surface_role"]
                previous_role = roles_by_surface_id.setdefault(
                    surface_id, surface_role
                )
                if previous_role != surface_role:
                    raise PortableNavigationContractError(
                        "PORTABLE_SURFACE_ID_ROLE_CONFLICT",
                        f"result.depth.samples[{index}].surface_role",
                    )
            samples.append(normalized_sample)
        semantics_revision = source.get("surface_id_semantics_revision")
        if semantics_revision is not None and semantics_revision != SURFACE_ID_SEMANTICS_REVISION:
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH",
                "result.depth.surface_id_semantics_revision",
            )
        if has_surface_identity and semantics_revision != SURFACE_ID_SEMANTICS_REVISION:
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ID_SEMANTICS_MISMATCH",
                "result.depth.surface_id_semantics_revision",
            )
        expected_digest = _sha256(source.get("samples_sha256"), "result.depth.samples_sha256")
        if _canonical_sha256(samples, "result.depth.samples") != expected_digest:
            raise PortableNavigationContractError("PORTABLE_DATA_HASH_MISMATCH", "result.depth.samples")
        result = {
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": alignment,
            "samples": samples,
            "samples_sha256": expected_digest,
        }
        if semantics_revision is not None:
            result["surface_id_semantics_revision"] = semantics_revision
        return result
    raise PortableNavigationContractError("PORTABLE_DEPTH_REPRESENTATION_UNSUPPORTED", "result.depth.representation")


def _validate_camera(value: Any) -> dict[str, Any]:
    source = _mapping(value, "result.camera")
    _fields(
        source,
        {"coordinate_frame_id", "coordinate_convention", "position_m", "orientation_xyzw", "intrinsics"},
        set(),
        "result.camera",
    )
    if source.get("coordinate_convention") != COORDINATE_CONVENTION:
        raise PortableNavigationContractError("PORTABLE_COORDINATE_CONVENTION_MISMATCH", "result.camera")
    intrinsics = _mapping(source.get("intrinsics"), "result.camera.intrinsics")
    _fields(intrinsics, {"fx_px", "fy_px", "cx_px", "cy_px"}, set(), "result.camera.intrinsics")
    return {
        "coordinate_frame_id": _identifier(source.get("coordinate_frame_id"), "result.camera.coordinate_frame_id"),
        "coordinate_convention": COORDINATE_CONVENTION,
        "position_m": _vector3(source.get("position_m"), "result.camera.position_m"),
        "orientation_xyzw": _quaternion(source.get("orientation_xyzw"), "result.camera.orientation_xyzw"),
        "intrinsics": {
            "fx_px": _number(intrinsics.get("fx_px"), "result.camera.intrinsics.fx_px", minimum=0.001),
            "fy_px": _number(intrinsics.get("fy_px"), "result.camera.intrinsics.fy_px", minimum=0.001),
            "cx_px": _number(intrinsics.get("cx_px"), "result.camera.intrinsics.cx_px", minimum=0.0),
            "cy_px": _number(intrinsics.get("cy_px"), "result.camera.intrinsics.cy_px", minimum=0.0),
        },
    }


def validate_capture_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source, result = _common_request(value, CAPTURE_REQUEST_SCHEMA, {"require_depth", "max_age_ms"})
    result.update({
        "require_depth": _boolean(source.get("require_depth"), "request.require_depth"),
        "max_age_ms": _integer(source.get("max_age_ms"), "request.max_age_ms", minimum=1, maximum=5000),
    })
    _canonical_json(result, "request")
    return result


def validate_capture_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source, result = _common_result(
        value,
        CAPTURE_RESULT_SCHEMA,
        {"frame_id", "captured_unix", "rgb", "depth", "camera"},
    )
    rgb = _validate_rgb_plane(source.get("rgb"))
    depth = _validate_depth(source.get("depth"))
    result.update({
        "frame_id": _identifier(source.get("frame_id"), "result.frame_id"),
        "captured_unix": _timestamp(source.get("captured_unix"), "result.captured_unix"),
        "rgb": rgb,
        "depth": depth,
        "camera": _validate_camera(source.get("camera")),
    })
    _not_future(result["captured_unix"], now_unix, "result.captured_unix")
    if request is not None:
        expected = validate_capture_request(request)
        _cross_common(expected, result)
        earliest = expected["requested_unix"] - expected["max_age_ms"] / 1000.0
        if result["captured_unix"] < earliest:
            raise PortableNavigationContractError("PORTABLE_CAPTURE_STALE", "result.captured_unix")
        if expected["require_depth"] and result["depth"] is None:
            raise PortableNavigationContractError("PORTABLE_DEPTH_REQUIRED", "result.depth")
    _canonical_json(result, "result")
    return result


def validate_map_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source, result = _common_request(
        value,
        MAP_REQUEST_SCHEMA,
        {"source_frame_id", "resolution_m", "footprint_radius_m"},
    )
    result.update({
        "source_frame_id": _identifier(source.get("source_frame_id"), "request.source_frame_id"),
        "resolution_m": _number(source.get("resolution_m"), "request.resolution_m", minimum=0.01, maximum=2.0),
        "footprint_radius_m": _number(
            source.get("footprint_radius_m"), "request.footprint_radius_m", minimum=0.01, maximum=5.0
        ),
    })
    _canonical_json(result, "request")
    return result


def _validate_grid(value: Any) -> dict[str, Any]:
    source = _mapping(value, "result.grid")
    _fields(
        source,
        {
            "width_cells",
            "height_cells",
            "resolution_m",
            "origin_position_m",
            "origin_orientation_xyzw",
            "occupancy_encoding",
            "occupancy_base64",
            "occupancy_sha256",
            "unknown_value",
            "occupied_threshold",
        },
        set(),
        "result.grid",
    )
    width = _integer(source.get("width_cells"), "result.grid.width_cells", minimum=1, maximum=MAX_GRID_DIMENSION_CELLS)
    height = _integer(source.get("height_cells"), "result.grid.height_cells", minimum=1, maximum=MAX_GRID_DIMENSION_CELLS)
    if width * height > MAX_GRID_CELLS:
        raise PortableNavigationContractError("PORTABLE_GRID_TOO_LARGE", "result.grid")
    if source.get("occupancy_encoding") != "u8_probability":
        raise PortableNavigationContractError("PORTABLE_OCCUPANCY_ENCODING_UNSUPPORTED", "result.grid.occupancy_encoding")
    digest = _sha256(source.get("occupancy_sha256"), "result.grid.occupancy_sha256")
    raw = _inline_bytes(source.get("occupancy_base64"), digest, "result.grid.occupancy_base64")
    if len(raw) != width * height:
        raise PortableNavigationContractError("PORTABLE_OCCUPANCY_SIZE_MISMATCH", "result.grid.occupancy_base64")
    unknown = _integer(source.get("unknown_value"), "result.grid.unknown_value", minimum=0, maximum=255)
    threshold = _integer(source.get("occupied_threshold"), "result.grid.occupied_threshold", minimum=1, maximum=255)
    if unknown >= threshold:
        raise PortableNavigationContractError("PORTABLE_OCCUPANCY_SEMANTICS_INVALID", "result.grid")
    return {
        "width_cells": width,
        "height_cells": height,
        "resolution_m": _number(source.get("resolution_m"), "result.grid.resolution_m", minimum=0.01, maximum=2.0),
        "origin_position_m": _vector3(source.get("origin_position_m"), "result.grid.origin_position_m"),
        "origin_orientation_xyzw": _quaternion(
            source.get("origin_orientation_xyzw"), "result.grid.origin_orientation_xyzw"
        ),
        "occupancy_encoding": "u8_probability",
        "occupancy_base64": source["occupancy_base64"],
        "occupancy_sha256": digest,
        "unknown_value": unknown,
        "occupied_threshold": threshold,
    }


def _validate_agent(value: Any) -> dict[str, Any]:
    source = _mapping(value, "result.agent")
    _fields(source, {"position_m", "orientation_xyzw", "footprint_radius_m"}, set(), "result.agent")
    return {
        "position_m": _vector3(source.get("position_m"), "result.agent.position_m"),
        "orientation_xyzw": _quaternion(source.get("orientation_xyzw"), "result.agent.orientation_xyzw"),
        "footprint_radius_m": _number(
            source.get("footprint_radius_m"), "result.agent.footprint_radius_m", minimum=0.01, maximum=5.0
        ),
    }


def validate_map_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source, result = _common_result(
        value,
        MAP_RESULT_SCHEMA,
        {"map_id", "source_frame_id", "created_unix", "coordinate_frame_id", "coordinate_convention", "grid", "agent"},
    )
    if source.get("coordinate_convention") != COORDINATE_CONVENTION:
        raise PortableNavigationContractError("PORTABLE_COORDINATE_CONVENTION_MISMATCH", "result.coordinate_convention")
    result.update({
        "map_id": _identifier(source.get("map_id"), "result.map_id"),
        "source_frame_id": _identifier(source.get("source_frame_id"), "result.source_frame_id"),
        "created_unix": _timestamp(source.get("created_unix"), "result.created_unix"),
        "coordinate_frame_id": _identifier(source.get("coordinate_frame_id"), "result.coordinate_frame_id"),
        "coordinate_convention": COORDINATE_CONVENTION,
        "grid": _validate_grid(source.get("grid")),
        "agent": _validate_agent(source.get("agent")),
    })
    _not_future(result["created_unix"], now_unix, "result.created_unix")
    if request is not None:
        expected = validate_map_request(request)
        _cross_common(expected, result)
        if result["created_unix"] < expected["requested_unix"] - MAX_CLOCK_SKEW_SECONDS:
            raise PortableNavigationContractError("PORTABLE_MAP_STALE", "result.created_unix")
        if result["source_frame_id"] != expected["source_frame_id"]:
            raise PortableNavigationContractError("PORTABLE_LINEAGE_MISMATCH", "source_frame_id")
        if not math.isclose(result["grid"]["resolution_m"], expected["resolution_m"], rel_tol=0.0, abs_tol=1e-9):
            raise PortableNavigationContractError("PORTABLE_MAP_RESOLUTION_MISMATCH", "result.grid.resolution_m")
        if result["agent"]["footprint_radius_m"] < expected["footprint_radius_m"]:
            raise PortableNavigationContractError("PORTABLE_FOOTPRINT_UNDERSIZED", "result.agent.footprint_radius_m")
    _canonical_json(result, "result")
    return result


def _validate_detection(value: Any) -> dict[str, Any]:
    source = _mapping(value, "request.detection")
    _fields(
        source,
        {"detection_id", "label", "confidence", "bbox_norm"},
        {"occluder_bboxes_norm", "expected_surface_role"},
        "request.detection",
    )
    result = {
        "detection_id": _identifier(source.get("detection_id"), "request.detection.detection_id"),
        "label": _text(source.get("label"), "request.detection.label", maximum=120),
        "confidence": _number(source.get("confidence"), "request.detection.confidence", minimum=0.0, maximum=1.0),
        "bbox_norm": _bbox(source.get("bbox_norm"), "request.detection.bbox_norm"),
    }
    if "expected_surface_role" in source:
        result["expected_surface_role"] = _surface_role(
            source.get("expected_surface_role"),
            "request.detection.expected_surface_role",
        )
    if "occluder_bboxes_norm" in source:
        raw_occluders = source["occluder_bboxes_norm"]
        if not isinstance(raw_occluders, (list, tuple)) or len(raw_occluders) > MAX_GROUND_OCCLUDER_BBOXES:
            raise PortableNavigationContractError(
                "PORTABLE_OCCLUDER_BBOXES_INVALID", "request.detection.occluder_bboxes_norm"
            )
        result["occluder_bboxes_norm"] = [
            _bbox(item, f"request.detection.occluder_bboxes_norm[{index}]")
            for index, item in enumerate(raw_occluders)
        ]
    return result


def _validate_arrival_continuity_surface(value: Any) -> dict[str, Any]:
    source = _mapping(value, "request.arrival_continuity_surface")
    _fields(
        source,
        {"target_id", "points_m", "maximum_distance_m"},
        {"surface_id", "surface_role"},
        "request.arrival_continuity_surface",
    )
    raw_points = source.get("points_m")
    if (
        not isinstance(raw_points, (list, tuple))
        or not 1 <= len(raw_points) <= MAX_GROUND_SURFACE_POINTS
    ):
        raise PortableNavigationContractError(
            "PORTABLE_GROUND_CONTINUITY_POINTS_INVALID",
            "request.arrival_continuity_surface.points_m",
        )
    result = {
        "target_id": _target_identifier(
            source.get("target_id"),
            "request.arrival_continuity_surface.target_id",
        ),
        "points_m": [
            _vector3(
                point,
                f"request.arrival_continuity_surface.points_m[{index}]",
            )
            for index, point in enumerate(raw_points)
        ],
        "maximum_distance_m": _number(
            source.get("maximum_distance_m"),
            "request.arrival_continuity_surface.maximum_distance_m",
            minimum=0.0,
            maximum=MAX_GROUND_CONTINUITY_DISTANCE_M,
        ),
    }
    if "surface_id" in source:
        result["surface_id"] = _surface_identifier(
            source.get("surface_id"),
            "request.arrival_continuity_surface.surface_id",
        )
    if "surface_role" in source:
        result["surface_role"] = _surface_role(
            source.get("surface_role"),
            "request.arrival_continuity_surface.surface_role",
        )
        if "surface_id" not in result:
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ROLE_REQUIRES_SURFACE_ID",
                "request.arrival_continuity_surface.surface_role",
            )
    return result


def validate_ground_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source, result = _common_request(
        value,
        GROUND_REQUEST_SCHEMA,
        {"source_frame_id", "map_id", "detection"},
        {"arrival_continuity_surface"},
    )
    result.update({
        "source_frame_id": _identifier(source.get("source_frame_id"), "request.source_frame_id"),
        "map_id": _identifier(source.get("map_id"), "request.map_id"),
        "detection": _validate_detection(source.get("detection")),
    })
    if "arrival_continuity_surface" in source:
        result["arrival_continuity_surface"] = _validate_arrival_continuity_surface(
            source.get("arrival_continuity_surface")
        )
    _canonical_json(result, "request")
    return result


def validate_ground_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source, result = _common_result(
        value,
        GROUND_RESULT_SCHEMA,
        {
            "source_frame_id",
            "map_id",
            "detection_id",
            "grounded_unix",
            "status",
            "target_id",
            "label",
            "confidence",
            "coordinate_frame_id",
            "coordinate_convention",
            "position_m",
            "uncertainty_radius_m",
            "reason",
        },
        {"surface_points_m", "surface_id", "surface_role"},
    )
    status = source.get("status")
    if status not in GROUND_STATUSES:
        raise PortableNavigationContractError("PORTABLE_GROUND_STATUS_INVALID", "result.status")
    label = _text(source.get("label"), "result.label", maximum=120)
    confidence = _number(source.get("confidence"), "result.confidence", minimum=0.0, maximum=1.0)
    if source.get("coordinate_convention") != COORDINATE_CONVENTION:
        raise PortableNavigationContractError("PORTABLE_COORDINATE_CONVENTION_MISMATCH", "result.coordinate_convention")
    if status == "grounded":
        target_id = _target_identifier(source.get("target_id"), "result.target_id")
        coordinate_frame_id = _identifier(source.get("coordinate_frame_id"), "result.coordinate_frame_id")
        position = _vector3(source.get("position_m"), "result.position_m")
        uncertainty = _number(
            source.get("uncertainty_radius_m"), "result.uncertainty_radius_m", minimum=0.0, maximum=100.0
        )
        if source.get("reason") is not None:
            raise PortableNavigationContractError("PORTABLE_GROUND_REASON_UNEXPECTED", "result.reason")
        reason = None
        surface_id = (
            _surface_identifier(source.get("surface_id"), "result.surface_id")
            if "surface_id" in source
            else None
        )
        surface_role = (
            _surface_role(source.get("surface_role"), "result.surface_role")
            if "surface_role" in source
            else None
        )
        if surface_role is not None and surface_id is None:
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ROLE_REQUIRES_SURFACE_ID",
                "result.surface_role",
            )
        surface_points = None
        if "surface_points_m" in source:
            raw_surface_points = source.get("surface_points_m")
            if (
                not isinstance(raw_surface_points, list)
                or not 1 <= len(raw_surface_points) <= MAX_GROUND_SURFACE_POINTS
            ):
                raise PortableNavigationContractError(
                    "PORTABLE_GROUND_SURFACE_POINTS_INVALID", "result.surface_points_m"
                )
            surface_points = [
                _vector3(item, f"result.surface_points_m[{index}]")
                for index, item in enumerate(raw_surface_points)
            ]
    else:
        if "surface_role" in source:
            raise PortableNavigationContractError(
                "PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN", "result.surface_role"
            )
        if any(source.get(name) is not None for name in (
            "target_id", "coordinate_frame_id", "position_m",
            "uncertainty_radius_m", "surface_points_m", "surface_id",
        )):
            raise PortableNavigationContractError("PORTABLE_UNRESOLVED_TARGET_DATA_FORBIDDEN", "result")
        reason = source.get("reason")
        if reason not in GROUND_FAILURE_REASONS:
            raise PortableNavigationContractError("PORTABLE_GROUND_REASON_INVALID", "result.reason")
        target_id = coordinate_frame_id = position = uncertainty = None
        surface_points = None
        surface_id = None
        surface_role = None
    result.update({
        "source_frame_id": _identifier(source.get("source_frame_id"), "result.source_frame_id"),
        "map_id": _identifier(source.get("map_id"), "result.map_id"),
        "detection_id": _identifier(source.get("detection_id"), "result.detection_id"),
        "grounded_unix": _timestamp(source.get("grounded_unix"), "result.grounded_unix"),
        "status": status,
        "target_id": target_id,
        "label": label,
        "confidence": confidence,
        "coordinate_frame_id": coordinate_frame_id,
        "coordinate_convention": COORDINATE_CONVENTION,
        "position_m": position,
        "uncertainty_radius_m": uncertainty,
        "reason": reason,
    })
    if surface_points is not None:
        result["surface_points_m"] = surface_points
    if surface_id is not None:
        result["surface_id"] = surface_id
    if surface_role is not None:
        result["surface_role"] = surface_role
    _not_future(result["grounded_unix"], now_unix, "result.grounded_unix")
    if request is not None:
        expected = validate_ground_request(request)
        _cross_common(expected, result)
        if result["grounded_unix"] < expected["requested_unix"] - MAX_CLOCK_SKEW_SECONDS:
            raise PortableNavigationContractError("PORTABLE_GROUNDING_STALE", "result.grounded_unix")
        for field_name in ("source_frame_id", "map_id"):
            if result[field_name] != expected[field_name]:
                raise PortableNavigationContractError("PORTABLE_LINEAGE_MISMATCH", field_name)
        if result["detection_id"] != expected["detection"]["detection_id"]:
            raise PortableNavigationContractError("PORTABLE_LINEAGE_MISMATCH", "detection_id")
        if result["label"] != expected["detection"]["label"]:
            raise PortableNavigationContractError("PORTABLE_GROUND_LABEL_MISMATCH", "label")
        expected_detection_surface_role = expected["detection"].get(
            "expected_surface_role"
        )
        if (
            result["status"] == "grounded"
            and expected_detection_surface_role is not None
            and result.get("surface_role") != expected_detection_surface_role
        ):
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ROLE_MISMATCH",
                "result.surface_role",
            )
        continuity = expected.get("arrival_continuity_surface")
        expected_surface_id = (
            continuity.get("surface_id")
            if isinstance(continuity, Mapping)
            else None
        )
        expected_surface_role = (
            continuity.get("surface_role")
            if isinstance(continuity, Mapping)
            else None
        )
        if (
            result["status"] == "grounded"
            and expected_surface_id is not None
            and result.get("surface_id") != expected_surface_id
        ):
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ID_MISMATCH",
                "result.surface_id",
            )
        if (
            result["status"] == "grounded"
            and expected_surface_role is not None
            and result.get("surface_role") != expected_surface_role
        ):
            raise PortableNavigationContractError(
                "PORTABLE_SURFACE_ROLE_MISMATCH",
                "result.surface_role",
            )
    _canonical_json(result, "result")
    return result


def validate_navigate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value, "request")
    required = {
        "schema_version",
        "navigation_id",
        "command_id",
        "environment_id",
        "session_id",
        "requested_unix",
        "deadline_unix",
        "source_frame_id",
        "map_id",
        "target_id",
        "coordinate_frame_id",
        "coordinate_convention",
        "waypoints_m",
        "arrival_radius_m",
        "max_speed_mps",
        "minimum_clearance_m",
    }
    _fields(source, required, set(), "request")
    _schema(source, NAVIGATE_REQUEST_SCHEMA, "request.schema_version")
    requested = _timestamp(source.get("requested_unix"), "request.requested_unix")
    deadline = _timestamp(source.get("deadline_unix"), "request.deadline_unix")
    if not requested < deadline <= requested + 300.0:
        raise PortableNavigationContractError("PORTABLE_NAVIGATION_DEADLINE_INVALID", "request.deadline_unix")
    if source.get("coordinate_convention") != COORDINATE_CONVENTION:
        raise PortableNavigationContractError("PORTABLE_COORDINATE_CONVENTION_MISMATCH", "request.coordinate_convention")
    raw_waypoints = source.get("waypoints_m")
    if not isinstance(raw_waypoints, list) or not 1 <= len(raw_waypoints) <= MAX_WAYPOINTS:
        raise PortableNavigationContractError("PORTABLE_WAYPOINTS_INVALID", "request.waypoints_m")
    result = {
        "schema_version": NAVIGATE_REQUEST_SCHEMA,
        "navigation_id": _identifier(source.get("navigation_id"), "request.navigation_id"),
        "command_id": _identifier(source.get("command_id"), "request.command_id"),
        "environment_id": _identifier(source.get("environment_id"), "request.environment_id"),
        "session_id": _identifier(source.get("session_id"), "request.session_id"),
        "requested_unix": requested,
        "deadline_unix": deadline,
        "source_frame_id": _identifier(source.get("source_frame_id"), "request.source_frame_id"),
        "map_id": _identifier(source.get("map_id"), "request.map_id"),
        "target_id": _target_identifier(source.get("target_id"), "request.target_id"),
        "coordinate_frame_id": _identifier(source.get("coordinate_frame_id"), "request.coordinate_frame_id"),
        "coordinate_convention": COORDINATE_CONVENTION,
        "waypoints_m": [_vector3(item, f"request.waypoints_m[{index}]") for index, item in enumerate(raw_waypoints)],
        "arrival_radius_m": _number(
            source.get("arrival_radius_m"), "request.arrival_radius_m", minimum=0.05, maximum=5.0
        ),
        "max_speed_mps": _number(source.get("max_speed_mps"), "request.max_speed_mps", minimum=0.01, maximum=10.0),
        "minimum_clearance_m": _number(
            source.get("minimum_clearance_m"), "request.minimum_clearance_m", minimum=0.0, maximum=10.0
        ),
    }
    _canonical_json(result, "request")
    return result


def validate_navigate_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source = _mapping(value, "result")
    required = {
        "schema_version", "navigation_id", "environment_id", "session_id", "map_id", "target_id",
        "status", "command_id", "accepted_unix", "reason",
    }
    _fields(source, required, set(), "result")
    _schema(source, NAVIGATE_RESULT_SCHEMA, "result.schema_version")
    status = source.get("status")
    if status not in NAVIGATE_STATUSES:
        raise PortableNavigationContractError("PORTABLE_NAVIGATE_STATUS_INVALID", "result.status")
    if status == "accepted":
        command_id = _identifier(source.get("command_id"), "result.command_id")
        accepted = _timestamp(source.get("accepted_unix"), "result.accepted_unix")
        _not_future(accepted, now_unix, "result.accepted_unix")
        if source.get("reason") is not None:
            raise PortableNavigationContractError("PORTABLE_NAVIGATE_REASON_UNEXPECTED", "result.reason")
        reason = None
    else:
        if source.get("command_id") is not None or source.get("accepted_unix") is not None:
            raise PortableNavigationContractError("PORTABLE_REJECTED_COMMAND_DATA_FORBIDDEN", "result")
        command_id = accepted = None
        reason = _text(source.get("reason"), "result.reason", maximum=160)
    result = {
        "schema_version": NAVIGATE_RESULT_SCHEMA,
        "navigation_id": _identifier(source.get("navigation_id"), "result.navigation_id"),
        "environment_id": _identifier(source.get("environment_id"), "result.environment_id"),
        "session_id": _identifier(source.get("session_id"), "result.session_id"),
        "map_id": _identifier(source.get("map_id"), "result.map_id"),
        "target_id": _target_identifier(source.get("target_id"), "result.target_id"),
        "status": status,
        "command_id": command_id,
        "accepted_unix": accepted,
        "reason": reason,
    }
    if request is not None:
        expected = validate_navigate_request(request)
        for field_name in ("navigation_id", "environment_id", "session_id", "map_id", "target_id"):
            if result[field_name] != expected[field_name]:
                raise PortableNavigationContractError("PORTABLE_LINEAGE_MISMATCH", field_name)
        if accepted is not None and result["command_id"] != expected["command_id"]:
            raise PortableNavigationContractError("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
        if accepted is not None and not expected["requested_unix"] - MAX_CLOCK_SKEW_SECONDS <= accepted <= expected["deadline_unix"]:
            raise PortableNavigationContractError("PORTABLE_ACCEPTANCE_TIME_INVALID", "result.accepted_unix")
    _canonical_json(result, "result")
    return result


def validate_feedback_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source, result = _common_request(
        value,
        FEEDBACK_REQUEST_SCHEMA,
        {"command_id", "after_sequence"},
    )
    result.update({
        "command_id": _identifier(source.get("command_id"), "request.command_id"),
        "after_sequence": _integer(source.get("after_sequence"), "request.after_sequence", minimum=-1, maximum=2**63 - 1),
    })
    _canonical_json(result, "request")
    return result


def validate_feedback_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source, result = _common_result(
        value,
        FEEDBACK_RESULT_SCHEMA,
        {
            "command_id", "sequence", "observed_unix", "status", "position_m", "velocity_mps",
            "remaining_distance_m", "minimum_clearance_m", "collision_detected", "target_reached", "reason",
        },
    )
    status = source.get("status")
    if status not in FEEDBACK_STATUSES:
        raise PortableNavigationContractError("PORTABLE_FEEDBACK_STATUS_INVALID", "result.status")
    collision = _boolean(source.get("collision_detected"), "result.collision_detected")
    reached = _boolean(source.get("target_reached"), "result.target_reached")
    if status == "arrived" and not reached:
        raise PortableNavigationContractError("PORTABLE_ARRIVAL_UNCONFIRMED", "result.target_reached")
    if reached and status != "arrived":
        raise PortableNavigationContractError("PORTABLE_TARGET_REACHED_STATUS_INVALID", "result.status")
    if collision and status not in {"blocked", "failed", "stopped"}:
        raise PortableNavigationContractError("PORTABLE_COLLISION_STATUS_INVALID", "result.status")
    reason = source.get("reason")
    if status in {"blocked", "failed"}:
        reason = _text(reason, "result.reason", maximum=160)
    elif reason is not None:
        raise PortableNavigationContractError("PORTABLE_FEEDBACK_REASON_UNEXPECTED", "result.reason")
    result.update({
        "command_id": _identifier(source.get("command_id"), "result.command_id"),
        "sequence": _integer(source.get("sequence"), "result.sequence", minimum=0, maximum=2**63 - 1),
        "observed_unix": _timestamp(source.get("observed_unix"), "result.observed_unix"),
        "status": status,
        "position_m": _vector3(source.get("position_m"), "result.position_m"),
        "velocity_mps": _vector3(source.get("velocity_mps"), "result.velocity_mps"),
        "remaining_distance_m": _number(
            source.get("remaining_distance_m"), "result.remaining_distance_m", minimum=0.0
        ),
        "minimum_clearance_m": _number(
            source.get("minimum_clearance_m"), "result.minimum_clearance_m", minimum=0.0
        ),
        "collision_detected": collision,
        "target_reached": reached,
        "reason": reason,
    })
    _not_future(result["observed_unix"], now_unix, "result.observed_unix")
    if request is not None:
        expected = validate_feedback_request(request)
        _cross_common(expected, result)
        if result["observed_unix"] < expected["requested_unix"] - MAX_CLOCK_SKEW_SECONDS:
            raise PortableNavigationContractError("PORTABLE_FEEDBACK_STALE", "result.observed_unix")
        if result["command_id"] != expected["command_id"]:
            raise PortableNavigationContractError("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
        if result["sequence"] <= expected["after_sequence"]:
            raise PortableNavigationContractError("PORTABLE_FEEDBACK_SEQUENCE_STALE", "result.sequence")
    _canonical_json(result, "result")
    return result


def validate_stop_request(value: Mapping[str, Any]) -> dict[str, Any]:
    source, result = _common_request(value, STOP_REQUEST_SCHEMA, {"command_id", "reason"})
    result.update({
        "command_id": _identifier(source.get("command_id"), "request.command_id"),
        "reason": _text(source.get("reason"), "request.reason", maximum=160),
    })
    _canonical_json(result, "request")
    return result


def validate_stop_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    now_unix: float | None = None,
) -> dict[str, Any]:
    source, result = _common_result(
        value,
        STOP_RESULT_SCHEMA,
        {"owned_command_id", "status", "confirmed", "stopped_unix", "final_sequence", "reason"},
    )
    status = source.get("status")
    if status not in STOP_STATUSES:
        raise PortableNavigationContractError("PORTABLE_STOP_STATUS_INVALID", "result.status")
    confirmed = _boolean(source.get("confirmed"), "result.confirmed")
    if confirmed != (status in {"confirmed", "already_stopped"}):
        raise PortableNavigationContractError("PORTABLE_STOP_CONFIRMATION_INVALID", "result.confirmed")
    if confirmed:
        stopped = _timestamp(source.get("stopped_unix"), "result.stopped_unix")
        _not_future(stopped, now_unix, "result.stopped_unix")
        if source.get("reason") is not None:
            raise PortableNavigationContractError("PORTABLE_STOP_REASON_UNEXPECTED", "result.reason")
        reason = None
    else:
        if source.get("stopped_unix") is not None:
            raise PortableNavigationContractError("PORTABLE_REJECTED_STOP_TIME_FORBIDDEN", "result.stopped_unix")
        stopped = None
        reason = _text(source.get("reason"), "result.reason", maximum=160)
    result.update({
        "owned_command_id": _identifier(source.get("owned_command_id"), "result.owned_command_id"),
        "status": status,
        "confirmed": confirmed,
        "stopped_unix": stopped,
        "final_sequence": _integer(source.get("final_sequence"), "result.final_sequence", minimum=0, maximum=2**63 - 1),
        "reason": reason,
    })
    if request is not None:
        expected = validate_stop_request(request)
        _cross_common(expected, result)
        if result["owned_command_id"] != expected["command_id"]:
            raise PortableNavigationContractError("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "owned_command_id")
        if stopped is not None and stopped < expected["requested_unix"] - MAX_CLOCK_SKEW_SECONDS:
            raise PortableNavigationContractError("PORTABLE_STOP_TIME_INVALID", "result.stopped_unix")
    _canonical_json(result, "result")
    return result


__all__ = [
    "CAPTURE_REQUEST_SCHEMA",
    "CAPTURE_RESULT_SCHEMA",
    "MAP_REQUEST_SCHEMA",
    "MAP_RESULT_SCHEMA",
    "GROUND_REQUEST_SCHEMA",
    "GROUND_RESULT_SCHEMA",
    "NAVIGATE_REQUEST_SCHEMA",
    "NAVIGATE_RESULT_SCHEMA",
    "STOP_REQUEST_SCHEMA",
    "STOP_RESULT_SCHEMA",
    "FEEDBACK_REQUEST_SCHEMA",
    "FEEDBACK_RESULT_SCHEMA",
    "COORDINATE_CONVENTION",
    "SURFACE_ROLES",
    "MAX_GROUND_CONTINUITY_DISTANCE_M",
    "MAX_GROUND_SURFACE_POINTS",
    "PortableNavigationAdapter",
    "PortableNavigationContractError",
    "validate_capture_request",
    "validate_capture_result",
    "validate_map_request",
    "validate_map_result",
    "validate_ground_request",
    "validate_ground_result",
    "validate_navigate_request",
    "validate_navigate_result",
    "validate_feedback_request",
    "validate_feedback_result",
    "validate_stop_request",
    "validate_stop_result",
]
