"""Godot implementation of the engine-neutral portable navigation contract.

The adapter is intentionally a thin boundary.  Native node names and Godot's
axis convention never cross the public contract: eye-frame geometry is kept in
private lineage caches, targets are represented by salted digest identifiers,
and every position is converted to the canonical +X-forward/+Y-left/+Z-up
frame.  Godot remains responsible for its fast collision checks and dynamic
route replanning.

Godot's registered sparse ray depth is converted into the same camera contract
used by other engines.  Bounding boxes are grounded from those measurements;
simulator evaluator geometry is available only as an explicit compatibility
fallback for captures made before the depth sensor existed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .portable_navigation_core_v1 import (
    PortableNavigationCoreError,
    ground_bbox_to_depth,
    path_collision_report,
)
from .portable_navigation_contract_v1 import (
    CAPTURE_RESULT_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_RESULT_SCHEMA,
    GROUND_RESULT_SCHEMA,
    MAP_RESULT_SCHEMA,
    NAVIGATE_RESULT_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    SURFACE_ROLES,
    STOP_RESULT_SCHEMA,
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


Transport = Callable[[str, str, Mapping[str, Any] | None], Awaitable[Mapping[str, Any]]]
_PORTABLE_SNAPSHOT_SCHEMA = "lumina.godot.portable-navigation-snapshot.v1"
_GODOT_COORDINATE_CONVENTION = "godot_x_right_y_up_z_back"
_MAX_FRAME_METADATA_BYTES = 2_000_000
_MAX_FRAME_BYTES = 4_000_000
_MAX_CACHED_ITEMS = 128
_GROUNDING_MIN_CONFIDENCE = 0.70
_GROUNDING_MIN_IOU = 0.12
_GROUNDING_AMBIGUITY_GAP = 0.08
_NATIVE_POSITION_STOP_DISTANCE_M = 0.10
_ARRIVAL_VERIFICATION_TOLERANCE_M = 0.05
_DEFAULT_MAP_CAPTURE_MAX_SKEW_SECONDS = 0.50
_MAP_CAPTURE_MIN_SKEW_SECONDS = -0.10
_MAP_SNAPSHOT_SYNC_ATTEMPTS = 11
_MAP_SNAPSHOT_SYNC_INTERVAL_SECONDS = 0.05
_DEFAULT_TERMINAL_SETTLE_TIMEOUT_SECONDS = 0.30
_TERMINAL_SETTLE_MAX_POLLS = 8
_TIME_EPSILON_SECONDS = 1e-9
_NATIVE_NON_TERMINAL_STATUSES = frozenset({"sent", "accepted", "queued", "running"})
_NATIVE_STOPPED_STATUSES = frozenset({"interrupted", "stopped", "cancelled"})
_NATIVE_ENDED_STATUSES = frozenset({"completed", "failed", "unhandled"})
_SURFACE_ID_PATTERN = re.compile(r"^surface:[0-9a-f]{64}$")


def godot_to_canonical(value: Any) -> list[float]:
    """Convert Godot (X right, Y up, -Z forward) to the canonical frame."""

    x, y, z = _vector3(value, "godot_vector")
    return [-z, -x, y]


def canonical_to_godot(value: Any) -> dict[str, float]:
    """Convert canonical (+X forward, +Y left, +Z up) to Godot."""

    forward, left, up = _vector3(value, "canonical_vector")
    return {"x": -left, "y": up, "z": -forward}


def _target_planar_distance(
    target_result: Mapping[str, Any], position: Sequence[float]
) -> float:
    """Distance to the certified target surface, or its point estimate."""

    references = target_result.get("surface_points_m")
    if not isinstance(references, list) or not references:
        references = [target_result["position_m"]]
    px, py, _ = _vector3(position, "position")
    return min(
        math.hypot(px - float(reference[0]), py - float(reference[1]))
        for reference in references
    )


def _error(code: str, detail: str) -> PortableNavigationContractError:
    return PortableNavigationContractError(code, detail)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", field)
    return float(value)


def _vector3(value: Any, field: str) -> tuple[float, float, float]:
    if isinstance(value, Mapping):
        value = [value.get("x"), value.get("y"), value.get("z")]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", field)
    return tuple(_number(component, f"{field}[{index}]") for index, component in enumerate(value))


def _normalize(value: Sequence[float], field: str) -> list[float]:
    length = math.sqrt(sum(float(component) ** 2 for component in value))
    if length <= 1e-8:
        raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", field)
    return [float(component) / length for component in value]


def _cross(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _matrix_columns_to_quaternion(
    x_axis: Sequence[float], y_axis: Sequence[float], z_axis: Sequence[float]
) -> list[float]:
    """Return normalized xyzw for an orthonormal column-basis rotation."""

    x_axis = _normalize(x_axis, "orientation.x_axis")
    y_axis = _normalize(y_axis, "orientation.y_axis")
    z_axis = _normalize(z_axis, "orientation.z_axis")
    m00, m01, m02 = x_axis[0], y_axis[0], z_axis[0]
    m10, m11, m12 = x_axis[1], y_axis[1], z_axis[1]
    m20, m21, m22 = x_axis[2], y_axis[2], z_axis[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (m21 - m12) / scale
        qy = (m02 - m20) / scale
        qz = (m10 - m01) / scale
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(max(0.0, 1.0 + m00 - m11 - m22)) * 2.0
        qw = (m21 - m12) / scale
        qx = 0.25 * scale
        qy = (m01 + m10) / scale
        qz = (m02 + m20) / scale
    elif m11 > m22:
        scale = math.sqrt(max(0.0, 1.0 + m11 - m00 - m22)) * 2.0
        qw = (m02 - m20) / scale
        qx = (m01 + m10) / scale
        qy = 0.25 * scale
        qz = (m12 + m21) / scale
    else:
        scale = math.sqrt(max(0.0, 1.0 + m22 - m00 - m11)) * 2.0
        qw = (m10 - m01) / scale
        qx = (m02 + m20) / scale
        qy = (m12 + m21) / scale
        qz = 0.25 * scale
    quaternion = _normalize([qx, qy, qz, qw], "orientation.quaternion")
    return quaternion


def _camera_quaternion(metadata: Mapping[str, Any]) -> list[float]:
    forward = _normalize(godot_to_canonical(metadata.get("camera_forward")), "camera.forward")
    up = _normalize(godot_to_canonical(metadata.get("camera_up")), "camera.up")
    raw_right = metadata.get("camera_right")
    if raw_right is None:
        # optical right = forward x world-up for a non-vertical camera
        right = _normalize(_cross(forward, up), "camera.right")
    else:
        right = _normalize(godot_to_canonical(raw_right), "camera.right")
    down = [-component for component in up]
    return _matrix_columns_to_quaternion(right, down, forward)


def _agent_quaternion(snapshot: Mapping[str, Any]) -> list[float]:
    agent = snapshot.get("agent")
    if not isinstance(agent, Mapping) or agent.get("forward") is None:
        return [0.0, 0.0, 0.0, 1.0]
    forward = _normalize(godot_to_canonical(agent.get("forward")), "agent.forward")
    up = [0.0, 0.0, 1.0]
    if abs(sum(a * b for a, b in zip(forward, up))) > 0.98:
        raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "agent.forward_vertical")
    left = _normalize(_cross(up, forward), "agent.left")
    corrected_up = _normalize(_cross(forward, left), "agent.up")
    return _matrix_columns_to_quaternion(forward, left, corrected_up)


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0.0 else 0.0


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in value):
        return None
    result = [float(number) for number in value]
    if any(not math.isfinite(number) or number < 0.0 or number > 1.0 for number in result):
        return None
    if result[2] <= result[0] or result[3] <= result[1]:
        return None
    return result


def _digest_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _sparse_depth(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate Godot's registered sparse ray telemetry into the contract."""

    source = metadata.get("portable_depth")
    if source is None:
        return None
    if not isinstance(source, Mapping) or source.get("representation") != "sparse_rays":
        raise _error("PORTABLE_GODOT_DEPTH_INVALID", "portable_depth")
    if source.get("available") is not True:
        return None
    if source.get("measurement_model") != "ray_range_m":
        raise _error("PORTABLE_GODOT_DEPTH_INVALID", "portable_depth.measurement_model")
    if source.get("alignment") != "registered_normalized_to_rgb":
        raise _error("PORTABLE_GODOT_DEPTH_INVALID", "portable_depth.alignment")
    if source.get("surface_id_semantics_revision") != SURFACE_ID_SEMANTICS_REVISION:
        raise _error(
            "PORTABLE_GODOT_DEPTH_INVALID",
            "portable_depth.surface_id_semantics_revision",
        )
    raw_samples = source.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise _error("PORTABLE_GODOT_DEPTH_INVALID", "portable_depth.samples")
    samples: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_samples):
        if not isinstance(raw, Mapping):
            raise _error("PORTABLE_GODOT_DEPTH_INVALID", f"portable_depth.samples[{index}]")
        allowed_fields = {
            "uv_norm",
            "distance_m",
            "confidence",
            "surface_id",
            "surface_role",
        }
        if set(raw) - allowed_fields:
            raise _error("PORTABLE_GODOT_DEPTH_INVALID", f"portable_depth.samples[{index}]")
        uv = raw.get("uv_norm")
        if not isinstance(uv, (list, tuple)) or len(uv) != 2:
            raise _error("PORTABLE_GODOT_DEPTH_INVALID", f"portable_depth.samples[{index}].uv_norm")
        sample = {
            "uv_norm": [
                _number(uv[0], f"portable_depth.samples[{index}].uv_norm[0]"),
                _number(uv[1], f"portable_depth.samples[{index}].uv_norm[1]"),
            ],
            "distance_m": _number(raw.get("distance_m"), f"portable_depth.samples[{index}].distance_m"),
            "confidence": _number(raw.get("confidence"), f"portable_depth.samples[{index}].confidence"),
        }
        if "surface_id" in raw:
            surface_id = raw.get("surface_id")
            if not isinstance(surface_id, str) or not _SURFACE_ID_PATTERN.fullmatch(surface_id):
                raise _error(
                    "PORTABLE_GODOT_DEPTH_INVALID",
                    f"portable_depth.samples[{index}].surface_id",
                )
            sample["surface_id"] = surface_id
        if "surface_role" in raw:
            surface_role = raw.get("surface_role")
            if not isinstance(surface_role, str) or surface_role not in SURFACE_ROLES:
                raise _error(
                    "PORTABLE_GODOT_DEPTH_INVALID",
                    f"portable_depth.samples[{index}].surface_role",
                )
            if "surface_id" not in sample:
                raise _error(
                    "PORTABLE_GODOT_DEPTH_INVALID",
                    f"portable_depth.samples[{index}].surface_role",
                )
            sample["surface_role"] = surface_role
        samples.append(sample)
    encoded = json.dumps(
        samples, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {
        "representation": "sparse_rays",
        "measurement_model": "ray_range_m",
        "alignment": "registered_normalized_to_rgb",
        "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
        "samples": samples,
        "samples_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _bounded_store(store: dict[Any, Any], key: Any, value: Any) -> None:
    store[key] = value
    while len(store) > _MAX_CACHED_ITEMS:
        store.pop(next(iter(store)))


class GodotPortableNavigationAdapter:
    """Adapt an existing local Godot bridge to ``PortableNavigationAdapter``.

    ``transport`` is injectable for deterministic offline tests.  It receives
    ``(method, path, body)`` and must return a JSON-compatible mapping.
    """

    def __init__(
        self,
        frame_dir: Path | str,
        bridge_url: str = "http://127.0.0.1:8765",
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        actor_id: str = "toha",
        world_max_age_seconds: float = 3.0,
        map_capture_max_skew_seconds: float = _DEFAULT_MAP_CAPTURE_MAX_SKEW_SECONDS,
        stop_timeout_seconds: float = 3.0,
        poll_interval_seconds: float = 0.05,
        terminal_settle_timeout_seconds: float = _DEFAULT_TERMINAL_SETTLE_TIMEOUT_SECONDS,
        allow_evaluator_geometry_fallback: bool = False,
    ) -> None:
        self.frame_dir = Path(frame_dir).expanduser().resolve()
        self.bridge_url = bridge_url.rstrip("/")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self.actor_id = actor_id
        self.world_max_age_seconds = max(0.05, float(world_max_age_seconds))
        map_capture_skew = float(map_capture_max_skew_seconds)
        if not math.isfinite(map_capture_skew) or map_capture_skew < 0.0:
            raise ValueError("map_capture_max_skew_seconds must be finite and non-negative")
        self.map_capture_max_skew_seconds = min(map_capture_skew, self.world_max_age_seconds)
        self.stop_timeout_seconds = max(0.0, float(stop_timeout_seconds))
        self.poll_interval_seconds = max(0.001, float(poll_interval_seconds))
        terminal_settle_timeout = float(terminal_settle_timeout_seconds)
        if not math.isfinite(terminal_settle_timeout) or terminal_settle_timeout < 0.0:
            raise ValueError(
                "terminal_settle_timeout_seconds must be finite and non-negative"
            )
        # A feedback call must never turn reconciliation into an unbounded
        # wait.  One second is already far longer than the Godot frame-order
        # race this grace period is intended to cover.
        self.terminal_settle_timeout_seconds = min(terminal_settle_timeout, 1.0)
        self.allow_evaluator_geometry_fallback = bool(allow_evaluator_geometry_fallback)
        self._captures: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._maps: dict[str, dict[str, Any]] = {}
        self._targets: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, dict[str, Any]] = {}
        # Active reservations are removed as soon as a command is known not to
        # be runnable.  Small bounded tombstones retain only enough ownership
        # evidence for a later idempotent stop; they are not live reservations.
        self._retired_commands: dict[str, dict[str, Any]] = {}
        self._navigation_results: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._stop_results: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        if self._transport is not None:
            result = await self._transport(method, path, body)
        else:
            parsed = urlparse(self.bridge_url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username
                or parsed.password
            ):
                raise _error("PORTABLE_GODOT_LOCAL_BRIDGE_REQUIRED", "bridge_url")
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                    response = await client.request(method, self.bridge_url + path, json=body)
                    response.raise_for_status()
                    result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise _error("PORTABLE_GODOT_BRIDGE_UNAVAILABLE", path) from exc
        if not isinstance(result, Mapping):
            raise _error("PORTABLE_GODOT_BRIDGE_INVALID", path)
        return result

    def _read_frame(self, max_age_ms: int, requested_unix: float) -> tuple[Mapping[str, Any], bytes]:
        try:
            raw = (self.frame_dir / "latest.json").read_bytes()
            if len(raw) > _MAX_FRAME_METADATA_BYTES:
                raise _error("PORTABLE_GODOT_FRAME_METADATA_TOO_LARGE", "latest.json")
            metadata = json.loads(raw)
            if not isinstance(metadata, Mapping):
                raise _error("PORTABLE_GODOT_FRAME_INVALID", "latest.json")
            captured = _number(metadata.get("captured_unix"), "captured_unix")
            earliest = max(requested_unix, self._clock()) - max_age_ms / 1000.0
            if captured < earliest or captured > self._clock() + 1.0:
                raise _error("PORTABLE_CAPTURE_STALE", "captured_unix")
            if any(str(metadata.get(field) or "").strip() for field in (
                "debug_target", "debug_target_alias", "debug_target_canonical"
            )):
                raise _error("PORTABLE_GODOT_DEBUG_CAPTURE_FORBIDDEN", "debug_target")
            render_source = metadata.get("render_source")
            render_viewport = metadata.get("render_viewport_instance_id")
            render_camera = metadata.get("render_camera_instance_id")
            active_camera = metadata.get("active_camera_instance_id")
            if (
                render_source != "dedicated_eye_subviewport"
                or type(render_viewport) is not int
                or render_viewport <= 0
                or type(render_camera) is not int
                or render_camera <= 0
                or type(active_camera) is not int
                or active_camera <= 0
                or active_camera != render_camera
            ):
                raise _error(
                    "PORTABLE_GODOT_RENDER_ATTESTATION_INVALID",
                    "dedicated_eye_subviewport_active_camera",
                )
            image_path = Path(str(metadata.get("image_path") or "")).expanduser().resolve()
            if self.frame_dir not in image_path.parents or image_path.suffix.lower() != ".png":
                raise _error("PORTABLE_GODOT_FRAME_PATH_INVALID", "image_path")
            if image_path.stat().st_size > _MAX_FRAME_BYTES:
                raise _error("PORTABLE_GODOT_FRAME_TOO_LARGE", "image_path")
            image = image_path.read_bytes()
            digest = str(metadata.get("image_sha256") or "")
            if not image.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(image).hexdigest() != digest:
                raise _error("PORTABLE_GODOT_FRAME_HASH_MISMATCH", "image_sha256")
            _vector3(metadata.get("camera_position"), "camera_position")
            _vector3(metadata.get("camera_forward"), "camera_forward")
            return metadata, image
        except PortableNavigationContractError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _error("PORTABLE_GODOT_FRAME_UNAVAILABLE", "latest.json") from exc

    async def _world(self) -> Mapping[str, Any]:
        world = await self._request("GET", "/world-state", None)
        updated = _number(world.get("updated_at"), "world.updated_at")
        age = self._clock() - updated
        if age < -1.0 or age > self.world_max_age_seconds:
            raise _error("PORTABLE_GODOT_WORLD_STALE", "world.updated_at")
        if world.get("navigation_ready") is not True:
            raise _error("PORTABLE_GODOT_NAVIGATION_NOT_READY", "world.navigation_ready")
        return world

    @staticmethod
    def _portable_snapshot(world: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = world.get("raw")
        avatar = raw.get("avatar_state") if isinstance(raw, Mapping) else None
        snapshot = avatar.get("portable_navigation") if isinstance(avatar, Mapping) else None
        if snapshot is None:
            avatar = world.get("avatar_state")
            snapshot = avatar.get("portable_navigation") if isinstance(avatar, Mapping) else None
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != _PORTABLE_SNAPSHOT_SCHEMA:
            raise _error("PORTABLE_GODOT_MAP_UNAVAILABLE", "portable_navigation")
        if snapshot.get("coordinate_frame") != _GODOT_COORDINATE_CONVENTION:
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "coordinate_frame")
        return snapshot

    @staticmethod
    def _capture_key(request: Mapping[str, Any], frame_id: str) -> tuple[str, str, str]:
        return request["environment_id"], request["session_id"], frame_id

    @staticmethod
    def _common(result_schema: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": result_schema,
            "request_id": request["request_id"],
            "environment_id": request["environment_id"],
            "session_id": request["session_id"],
        }

    @staticmethod
    def _coordinate_frame_id(environment_id: str, session_id: str) -> str:
        return _digest_id("world", environment_id, session_id)

    async def capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_capture_request(request)
        metadata, image = self._read_frame(normalized["max_age_ms"], normalized["requested_unix"])
        depth = _sparse_depth(metadata)
        if normalized["require_depth"] and depth is None:
            raise _error("PORTABLE_DEPTH_REQUIRED", "Godot sparse ray sensor has not published this frame")
        native_frame_id = str(metadata.get("frame_id") or "")
        captured_unix = _number(metadata.get("captured_unix"), "captured_unix")
        width = metadata.get("width")
        height = metadata.get("height")
        if type(width) is not int or type(height) is not int:
            raise _error("PORTABLE_GODOT_FRAME_INVALID", "frame_dimensions")
        fov = _number(metadata.get("fov"), "fov")
        focal = float(height) / (2.0 * math.tan(math.radians(fov) / 2.0))
        coordinate_frame_id = self._coordinate_frame_id(
            normalized["environment_id"], normalized["session_id"]
        )
        camera_position = godot_to_canonical(metadata["camera_position"])
        camera_orientation = _camera_quaternion(metadata)
        intrinsics = {
            "fx_px": focal,
            "fy_px": focal,
            "cx_px": float(width) / 2.0,
            "cy_px": float(height) / 2.0,
        }
        depth_identity = None
        if depth is not None:
            depth_identity = {
                "representation": depth["representation"],
                "measurement_model": depth["measurement_model"],
                "alignment": depth["alignment"],
                "surface_id_semantics_revision": depth[
                    "surface_id_semantics_revision"
                ],
                "samples_sha256": depth["samples_sha256"],
            }
        # Frame identity covers every metric input that can change grounding.
        # Reusing a producer-native id/time/RGB tuple with altered camera or
        # depth telemetry therefore cannot overwrite an existing lineage key.
        frame_id = _digest_id(
            "frame",
            normalized["environment_id"],
            normalized["session_id"],
            native_frame_id,
            captured_unix,
            {
                "encoding": "png",
                "width_px": width,
                "height_px": height,
                "sha256": metadata.get("image_sha256"),
            },
            {
                "coordinate_frame_id": coordinate_frame_id,
                "position_m": camera_position,
                "orientation_xyzw": camera_orientation,
                "intrinsics": intrinsics,
            },
            depth_identity,
        )
        result = self._common(CAPTURE_RESULT_SCHEMA, normalized)
        result.update({
            "frame_id": frame_id,
            "captured_unix": captured_unix,
            "rgb": {
                "encoding": "png",
                "width_px": width,
                "height_px": height,
                "data_base64": base64.b64encode(image).decode("ascii"),
                "sha256": hashlib.sha256(image).hexdigest(),
            },
            "depth": depth,
            "camera": {
                "coordinate_frame_id": coordinate_frame_id,
                "coordinate_convention": COORDINATE_CONVENTION,
                "position_m": camera_position,
                "orientation_xyzw": camera_orientation,
                "intrinsics": intrinsics,
            },
        })
        validated = validate_capture_result(result, request=normalized, now_unix=self._clock())
        _bounded_store(self._captures, self._capture_key(normalized, frame_id), {
            "request": normalized,
            "result": validated,
            "metadata": metadata,
        })
        return validated

    @staticmethod
    def _snapshot_bounds(snapshot: Mapping[str, Any]) -> tuple[float, float, float, float]:
        bounds = snapshot.get("bounds_xz")
        if not isinstance(bounds, Mapping):
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "bounds_xz")
        min_gx = _number(bounds.get("min_x"), "bounds.min_x")
        max_gx = _number(bounds.get("max_x"), "bounds.max_x")
        min_gz = _number(bounds.get("min_z"), "bounds.min_z")
        max_gz = _number(bounds.get("max_z"), "bounds.max_z")
        if not min_gx < max_gx or not min_gz < max_gz:
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "bounds_xz")
        return -max_gz, -min_gz, -max_gx, -min_gx

    @staticmethod
    def _snapshot_obstacles(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_obstacles = snapshot.get("obstacles")
        if not isinstance(raw_obstacles, list) or len(raw_obstacles) > 10000:
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "obstacles")
        obstacles: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_obstacles):
            if not isinstance(raw, Mapping):
                raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", f"obstacles[{index}]")
            kind = raw.get("kind")
            center = raw.get("center_xz")
            if kind not in {"rect", "circle"} or not isinstance(center, Mapping):
                raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", f"obstacles[{index}]")
            gx = _number(center.get("x"), f"obstacles[{index}].center.x")
            gz = _number(center.get("z"), f"obstacles[{index}].center.z")
            item: dict[str, Any] = {"kind": kind, "center": [-gz, -gx]}
            if kind == "rect":
                half = raw.get("half_extents_xz")
                if not isinstance(half, Mapping):
                    raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", f"obstacles[{index}].half_extents")
                hx = _number(half.get("x"), f"obstacles[{index}].half.x")
                hz = _number(half.get("z"), f"obstacles[{index}].half.z")
                if hx <= 0.0 or hz <= 0.0:
                    raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", f"obstacles[{index}].half_extents")
                item["half"] = [hz, hx]
            else:
                radius = _number(raw.get("radius_m"), f"obstacles[{index}].radius")
                if radius <= 0.0:
                    raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", f"obstacles[{index}].radius")
                item["radius"] = radius
            obstacles.append(item)
        return obstacles

    @staticmethod
    def _rasterize(
        bounds: tuple[float, float, float, float],
        obstacles: Sequence[Mapping[str, Any]],
        resolution: float,
        padding: float,
    ) -> tuple[int, int, bytes]:
        min_x, max_x, min_y, max_y = bounds
        width = int(math.ceil((max_x - min_x) / resolution))
        height = int(math.ceil((max_y - min_y) / resolution))
        if width < 1 or height < 1 or width > 4096 or height > 4096 or width * height > 16 * 1024 * 1024:
            raise _error("PORTABLE_GODOT_MAP_TOO_LARGE", "grid")
        occupancy = bytearray(width * height)
        cell_half_diagonal = math.sqrt(2.0) * resolution / 2.0
        conservative_padding = padding + cell_half_diagonal

        def clamp_index(value: int, maximum: int) -> int:
            return max(0, min(maximum - 1, value))

        for obstacle in obstacles:
            cx, cy = obstacle["center"]
            if obstacle["kind"] == "rect":
                hx, hy = obstacle["half"]
                x0, x1 = cx - hx - conservative_padding, cx + hx + conservative_padding
                y0, y1 = cy - hy - conservative_padding, cy + hy + conservative_padding
                ix0 = clamp_index(int(math.floor((x0 - min_x) / resolution)), width)
                ix1 = clamp_index(int(math.floor((x1 - min_x) / resolution)), width)
                iy0 = clamp_index(int(math.floor((y0 - min_y) / resolution)), height)
                iy1 = clamp_index(int(math.floor((y1 - min_y) / resolution)), height)
                for iy in range(iy0, iy1 + 1):
                    row = iy * width
                    occupancy[row + ix0 : row + ix1 + 1] = b"\xff" * (ix1 - ix0 + 1)
            else:
                radius = obstacle["radius"] + conservative_padding
                ix0 = clamp_index(int(math.floor((cx - radius - min_x) / resolution)), width)
                ix1 = clamp_index(int(math.floor((cx + radius - min_x) / resolution)), width)
                iy0 = clamp_index(int(math.floor((cy - radius - min_y) / resolution)), height)
                iy1 = clamp_index(int(math.floor((cy + radius - min_y) / resolution)), height)
                radius_squared = radius * radius
                for iy in range(iy0, iy1 + 1):
                    sample_y = min_y + (iy + 0.5) * resolution
                    row = iy * width
                    for ix in range(ix0, ix1 + 1):
                        sample_x = min_x + (ix + 0.5) * resolution
                        if (sample_x - cx) ** 2 + (sample_y - cy) ** 2 <= radius_squared:
                            occupancy[row + ix] = 255
        boundary_clearance = padding + cell_half_diagonal
        for iy in range(height):
            sample_y = min_y + (iy + 0.5) * resolution
            row = iy * width
            for ix in range(width):
                sample_x = min_x + (ix + 0.5) * resolution
                if min(
                    sample_x - min_x,
                    max_x - sample_x,
                    sample_y - min_y,
                    max_y - sample_y,
                ) <= boundary_clearance + 1e-9:
                    occupancy[row + ix] = 255
        return width, height, bytes(occupancy)

    async def build_map(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_map_request(request)
        capture_key = self._capture_key(normalized, normalized["source_frame_id"])
        capture = self._captures.get(capture_key)
        if capture is None:
            raise _error("PORTABLE_FRAME_UNKNOWN", "source_frame_id")
        frame_captured = _number(
            capture["result"].get("captured_unix"), "capture.captured_unix"
        )
        metadata = capture["metadata"]
        embedded_snapshot = metadata.get("portable_navigation")
        if isinstance(embedded_snapshot, Mapping):
            snapshot = embedded_snapshot
            if snapshot.get("source_frame_id") != metadata.get("frame_id"):
                raise _error(
                    "PORTABLE_GODOT_MAP_FRAME_ID_MISMATCH",
                    "portable_navigation.source_frame_id",
                )
            captured = _number(
                snapshot.get("captured_unix"), "portable_navigation.captured_unix"
            )
            checked_at = self._clock()
            if checked_at - captured > self.world_max_age_seconds or captured > checked_at + 1.0:
                raise _error("PORTABLE_GODOT_WORLD_STALE", "portable_navigation.captured_unix")
        else:
            # Compatibility for adapter fixtures and older producers. Eye
            # capture and bridge snapshots are emitted on separate Godot ticks;
            # briefly reacquire when the bridge is behind. New production
            # frames publish the map snapshot atomically beside RGB-D instead.
            for attempt in range(_MAP_SNAPSHOT_SYNC_ATTEMPTS):
                world = await self._world()
                snapshot = self._portable_snapshot(world)
                captured = _number(
                    snapshot.get("captured_unix"), "portable_navigation.captured_unix"
                )
                checked_at = self._clock()
                if checked_at - captured > self.world_max_age_seconds or captured > checked_at + 1.0:
                    raise _error("PORTABLE_GODOT_WORLD_STALE", "portable_navigation.captured_unix")
                if captured + _TIME_EPSILON_SECONDS >= frame_captured:
                    break
                if attempt + 1 < _MAP_SNAPSHOT_SYNC_ATTEMPTS:
                    await self._sleep(_MAP_SNAPSHOT_SYNC_INTERVAL_SECONDS)
        frame_to_snapshot_skew = captured - frame_captured
        if (
            frame_to_snapshot_skew
            < _MAP_CAPTURE_MIN_SKEW_SECONDS - _TIME_EPSILON_SECONDS
            or frame_to_snapshot_skew
            > self.map_capture_max_skew_seconds + _TIME_EPSILON_SECONDS
        ):
            raise _error(
                "PORTABLE_GODOT_MAP_FRAME_SKEW",
                "portable_navigation.captured_unix must be within the bounded "
                "source-frame synchronization window",
            )
        bounds = self._snapshot_bounds(snapshot)
        obstacles = self._snapshot_obstacles(snapshot)
        agent = snapshot.get("agent")
        if not isinstance(agent, Mapping):
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "agent")
        native_footprint = _number(agent.get("footprint_radius_m"), "agent.footprint_radius_m")
        footprint = max(native_footprint, normalized["footprint_radius_m"])
        width, height, occupancy = self._rasterize(
            bounds, obstacles, normalized["resolution_m"], footprint
        )
        grid_digest = hashlib.sha256(occupancy).hexdigest()
        agent_position = godot_to_canonical(agent.get("position"))
        agent_orientation = _agent_quaternion(snapshot)
        map_id = _digest_id(
            "map", normalized["environment_id"], normalized["session_id"],
            normalized["source_frame_id"], captured, list(bounds),
            normalized["resolution_m"], footprint, grid_digest,
            agent_position, agent_orientation,
        )
        coordinate_frame_id = self._coordinate_frame_id(normalized["environment_id"], normalized["session_id"])
        result = self._common(MAP_RESULT_SCHEMA, normalized)
        result.update({
            "map_id": map_id,
            "source_frame_id": normalized["source_frame_id"],
            "created_unix": self._clock(),
            "coordinate_frame_id": coordinate_frame_id,
            "coordinate_convention": COORDINATE_CONVENTION,
            "grid": {
                "width_cells": width,
                "height_cells": height,
                "resolution_m": normalized["resolution_m"],
                "origin_position_m": [bounds[0], bounds[2], 0.0],
                "origin_orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "occupancy_encoding": "u8_probability",
                "occupancy_base64": base64.b64encode(occupancy).decode("ascii"),
                "occupancy_sha256": grid_digest,
                "unknown_value": 127,
                "occupied_threshold": 200,
            },
            "agent": {
                "position_m": agent_position,
                "orientation_xyzw": agent_orientation,
                "footprint_radius_m": footprint,
            },
        })
        validated = validate_map_result(result, request=normalized, now_unix=self._clock())
        _bounded_store(self._maps, map_id, {
            "request": normalized,
            "result": validated,
            "snapshot": snapshot,
            "bounds": bounds,
            "obstacles": obstacles,
        })
        return validated

    @staticmethod
    def _ground_unresolved(request: Mapping[str, Any], reason: str) -> dict[str, Any]:
        detection = request["detection"]
        result = GodotPortableNavigationAdapter._common(GROUND_RESULT_SCHEMA, request)
        result.update({
            "source_frame_id": request["source_frame_id"],
            "map_id": request["map_id"],
            "detection_id": detection["detection_id"],
            "grounded_unix": request["requested_unix"],
            "status": "unresolved",
            "target_id": None,
            "label": detection["label"],
            "confidence": detection["confidence"],
            "coordinate_frame_id": None,
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": None,
            "uncertainty_radius_m": None,
            "reason": reason,
        })
        return result

    def _ground_from_evaluator_geometry(
        self,
        request: Mapping[str, Any],
        capture: Mapping[str, Any],
        map_record: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Compatibility-only grounding for pre-depth capture producers."""

        geometry = capture["metadata"].get("evaluator_geometry")
        raw_objects = geometry.get("objects") if isinstance(geometry, Mapping) else None
        if not isinstance(raw_objects, list):
            unresolved = self._ground_unresolved(request, "unsupported")
            unresolved["grounded_unix"] = self._clock()
            return validate_ground_result(unresolved, request=request, now_unix=self._clock())
        box = request["detection"]["bbox_norm"]
        candidates: list[tuple[float, list[float], list[float]]] = []
        for item in raw_objects:
            if not isinstance(item, Mapping):
                continue
            if (
                item.get("occluded") is not False
                or item.get("in_frustum") is not True
                or item.get("can_approach", True) is not True
            ):
                continue
            projected = _bbox(item.get("projected_bbox"))
            if projected is None:
                continue
            try:
                position = godot_to_canonical(item.get("world_position"))
            except PortableNavigationContractError:
                continue
            score = _iou(box, projected)
            if score >= _GROUNDING_MIN_IOU:
                candidates.append((score, position, projected))
        candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
        if not candidates:
            unresolved = self._ground_unresolved(request, "not_visible")
            unresolved["grounded_unix"] = self._clock()
            return validate_ground_result(unresolved, request=request, now_unix=self._clock())
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < _GROUNDING_AMBIGUITY_GAP:
            unresolved = self._ground_unresolved(request, "ambiguous")
            unresolved["grounded_unix"] = self._clock()
            return validate_ground_result(unresolved, request=request, now_unix=self._clock())

        score, position, projected = candidates[0]
        target_id = _digest_id(
            "target",
            request["environment_id"],
            request["session_id"],
            request["source_frame_id"],
            request["map_id"],
            request["detection"]["detection_id"],
            position,
            projected,
        )
        result = self._common(GROUND_RESULT_SCHEMA, request)
        result.update({
            "source_frame_id": request["source_frame_id"],
            "map_id": request["map_id"],
            "detection_id": request["detection"]["detection_id"],
            "grounded_unix": self._clock(),
            "status": "grounded",
            "target_id": target_id,
            "label": request["detection"]["label"],
            "confidence": request["detection"]["confidence"],
            "coordinate_frame_id": map_record["result"]["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "position_m": position,
            "uncertainty_radius_m": max(0.05, min(1.0, 0.05 + (1.0 - score) * 0.45)),
            "reason": None,
        })
        return validate_ground_result(result, request=request, now_unix=self._clock())

    async def ground(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_ground_request(request)
        capture = self._captures.get(self._capture_key(normalized, normalized["source_frame_id"]))
        map_record = self._maps.get(normalized["map_id"])
        if capture is None:
            raise _error("PORTABLE_FRAME_UNKNOWN", "source_frame_id")
        if map_record is None:
            raise _error("PORTABLE_MAP_UNKNOWN", "map_id")
        if map_record["result"]["source_frame_id"] != normalized["source_frame_id"]:
            raise _error("PORTABLE_LINEAGE_MISMATCH", "source_frame_id")
        if normalized["detection"]["confidence"] < _GROUNDING_MIN_CONFIDENCE:
            unresolved = self._ground_unresolved(normalized, "not_visible")
            unresolved["grounded_unix"] = self._clock()
            return validate_ground_result(unresolved, request=normalized, now_unix=self._clock())

        try:
            depth_result = ground_bbox_to_depth(
                normalized,
                capture["result"],
                grounded_unix=self._clock(),
            )
        except PortableNavigationCoreError as exc:
            raise _error(exc.code, exc.detail) from exc
        if depth_result["status"] == "grounded":
            if depth_result["coordinate_frame_id"] != map_record["result"]["coordinate_frame_id"]:
                raise _error("PORTABLE_LINEAGE_MISMATCH", "coordinate_frame_id")
            validated = validate_ground_result(
                depth_result, request=normalized, now_unix=self._clock()
            )
            _bounded_store(self._targets, validated["target_id"], {
                "request": normalized,
                "result": validated,
                "grounding_source": "depth",
            })
            return validated

        # If a frame has depth but this bbox has no unambiguous surface, never
        # replace that metric evidence with simulator-only evaluator metadata.
        if capture["result"]["depth"] is not None or not self.allow_evaluator_geometry_fallback:
            return validate_ground_result(
                depth_result, request=normalized, now_unix=self._clock()
            )
        fallback = self._ground_from_evaluator_geometry(normalized, capture, map_record)
        if fallback["status"] == "grounded":
            _bounded_store(self._targets, fallback["target_id"], {
                "request": normalized,
                "result": fallback,
                "grounding_source": "evaluator_compatibility",
            })
        return fallback

    @staticmethod
    def _reject_navigation(request: Mapping[str, Any], reason: str) -> dict[str, Any]:
        return {
            "schema_version": NAVIGATE_RESULT_SCHEMA,
            "navigation_id": request["navigation_id"],
            "environment_id": request["environment_id"],
            "session_id": request["session_id"],
            "map_id": request["map_id"],
            "target_id": request["target_id"],
            "status": "rejected",
            "command_id": None,
            "accepted_unix": None,
            "reason": reason[:160],
        }

    @staticmethod
    def _new_command_record(request: Mapping[str, Any]) -> dict[str, Any]:
        command_id = request["command_id"]
        return {
            "request": request,
            "result": {
                "command_id": command_id,
                "environment_id": request["environment_id"],
                "session_id": request["session_id"],
            },
            "environment_id": request["environment_id"],
            "session_id": request["session_id"],
            "navigation_id": request["navigation_id"],
            "sequence": -1,
            "terminal": None,
            "dispatch_state": "reserved",
        }

    @staticmethod
    def _command_owner_matches(command: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
        result = command.get("result")
        return isinstance(result, Mapping) and all(
            result.get(field) == request[field]
            for field in ("environment_id", "session_id")
        )

    def _retire_command(
        self,
        command: dict[str, Any],
        *,
        terminal: str,
        dispatch_state: str | None = None,
    ) -> dict[str, Any]:
        """Release an active reservation but retain bounded stop ownership."""

        command_id = command["request"]["command_id"]
        existing = self._retired_commands.get(command_id)
        if self._commands.get(command_id) is not command and existing is not None:
            return existing
        if self._commands.get(command_id) is command:
            self._commands.pop(command_id, None)
        if dispatch_state is not None:
            command["dispatch_state"] = dispatch_state
        command["terminal"] = terminal
        tombstone = {
            "request": command["request"],
            "result": {
                "command_id": command_id,
                "environment_id": command["environment_id"],
                "session_id": command["session_id"],
            },
            "environment_id": command["environment_id"],
            "session_id": command["session_id"],
            "navigation_id": command["navigation_id"],
            "sequence": int(command.get("sequence", -1)),
            "terminal": terminal,
            "dispatch_state": command["dispatch_state"],
        }
        _bounded_store(self._retired_commands, command_id, tombstone)
        return tombstone

    def _finish_rejected_navigation(
        self,
        request: Mapping[str, Any],
        command: dict[str, Any],
        idempotency_key: tuple[str, str, str],
        reason: str,
    ) -> dict[str, Any]:
        now = self._clock()
        rejected = validate_navigate_result(
            self._reject_navigation(request, reason), request=request, now_unix=now
        )
        _bounded_store(
            self._navigation_results,
            idempotency_key,
            {"request": request, "result": rejected},
        )
        self._retire_command(command, terminal="rejected", dispatch_state="rejected")
        return rejected

    def _reserve_command(self, request: Mapping[str, Any]) -> dict[str, Any]:
        command_id = request["command_id"]
        existing = self._commands.get(command_id)
        if existing is not None:
            if existing.get("request") != request:
                raise _error("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
            raise _error("PORTABLE_NAVIGATION_IN_PROGRESS", "command_id")
        retired = self._retired_commands.get(command_id)
        if retired is not None:
            if retired.get("request") != request:
                raise _error("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
            raise _error("PORTABLE_COMMAND_ALREADY_TERMINAL", "command_id")
        command = self._new_command_record(request)
        # Active ownership must never be evicted to make room for another
        # command: doing so would make a targeted emergency stop impossible.
        self._commands[command_id] = command
        return command

    def _owned_command(
        self, request: Mapping[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        command_id = request["command_id"]
        command = self._commands.get(command_id)
        retired = False
        if command is None:
            command = self._retired_commands.get(command_id)
            retired = command is not None
        if command is None or not self._command_owner_matches(command, request):
            raise _error("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
        return command, retired

    async def navigate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_navigate_request(request)
        idempotency_key = (normalized["environment_id"], normalized["session_id"], normalized["navigation_id"])
        previous = self._navigation_results.get(idempotency_key)
        if previous is not None:
            if previous["request"] != normalized:
                raise _error("PORTABLE_NAVIGATION_IDEMPOTENCY_CONFLICT", "navigation_id")
            return dict(previous["result"])
        # Reserve the caller-owned ID before the first await.  A cancellation
        # or bridge failure in the fresh-world check can then be proven locally
        # never-dispatched and acknowledged by stop() without touching Godot.
        command_id = normalized["command_id"]
        command_record = self._reserve_command(normalized)
        try:
            map_record = self._maps.get(normalized["map_id"])
            target = self._targets.get(normalized["target_id"])
            if map_record is None:
                raise _error("PORTABLE_MAP_UNKNOWN", "map_id")
            if target is None:
                raise _error("PORTABLE_TARGET_UNKNOWN", "target_id")
            target_result = target["result"]
            if (
                target_result["map_id"] != normalized["map_id"]
                or target_result["source_frame_id"] != normalized["source_frame_id"]
                or target_result["coordinate_frame_id"] != normalized["coordinate_frame_id"]
                or map_record["result"]["environment_id"] != normalized["environment_id"]
                or map_record["result"]["session_id"] != normalized["session_id"]
            ):
                raise _error("PORTABLE_LINEAGE_MISMATCH", "navigate")
            now = self._clock()
            if now > normalized["deadline_unix"]:
                return self._finish_rejected_navigation(
                    normalized, command_record, idempotency_key, "deadline_expired"
                )
            last = normalized["waypoints_m"][-1]
            if _target_planar_distance(target_result, last) > (
                normalized["arrival_radius_m"] + target_result["uncertainty_radius_m"]
            ):
                return self._finish_rejected_navigation(
                    normalized, command_record, idempotency_key, "target_waypoint_mismatch"
                )
            path_report = path_collision_report(
                map_record["result"],
                [map_record["result"]["agent"]["position_m"], *normalized["waypoints_m"]],
                additional_inflation_m=normalized["minimum_clearance_m"],
            )
            if path_report["valid"] is not True:
                return self._finish_rejected_navigation(
                    normalized, command_record, idempotency_key, "waypoint_path_blocked"
                )
            command_record.update({
                "target": target,
                "map": map_record,
                "goal_position": list(normalized["waypoints_m"][-1]),
            })
            # Confirm that the engine is connected and its collision snapshot
            # is fresh immediately before dispatch.
            command_record["dispatch_state"] = "awaiting_world"
            await self._world()
        except asyncio.CancelledError:
            self._retire_command(
                command_record, terminal="cancelled", dispatch_state="never_dispatched"
            )
            raise
        except PortableNavigationCoreError as exc:
            self._retire_command(
                command_record, terminal="failed", dispatch_state="never_dispatched"
            )
            raise _error(exc.code, exc.detail) from exc
        except Exception:
            self._retire_command(
                command_record, terminal="failed", dispatch_state="never_dispatched"
            )
            raise

        # stop() may have retired the reservation while _world() was pending.
        # In that case movement must never be dispatched after the stop ack.
        if self._commands.get(command_id) is not command_record:
            return self._finish_rejected_navigation(
                normalized, command_record, idempotency_key, "stopped_before_dispatch"
            )
        now = self._clock()
        if now > normalized["deadline_unix"]:
            return self._finish_rejected_navigation(
                normalized, command_record, idempotency_key, "deadline_expired"
            )
        expires_ms = max(100, min(120_000, int((normalized["deadline_unix"] - now) * 1000.0)))
        goal_position = list(normalized["waypoints_m"][-1])
        command_record["dispatch_state"] = "dispatching"
        try:
            dispatch = await self._request("POST", "/command", {
                "command_id": command_id,
                "correlation_id": normalized["navigation_id"],
                "actor_id": self.actor_id,
                "action": "move_to_position",
                "position": canonical_to_godot(goal_position),
                "params": {
                    # The final waypoint may already be arrival_radius away
                    # from the semantic surface.  Reusing that radius here
                    # could stop the avatar almost twice as far away.
                    "stop_distance": _NATIVE_POSITION_STOP_DISTANCE_M,
                    "walk_speed": normalized["max_speed_mps"],
                    "portable_route": True,
                    "portable_footprint_radius_m": map_record["result"]["agent"]["footprint_radius_m"],
                    "portable_minimum_clearance_m": normalized["minimum_clearance_m"],
                    "portable_waypoints": [
                        canonical_to_godot(waypoint) for waypoint in normalized["waypoints_m"]
                    ],
                },
                "priority": 5,
                "expires_in_ms": expires_ms,
                "reason": "portable-navigation-v1",
            })
        except (asyncio.CancelledError, Exception):
            # Once POST has started, cancellation or response loss is
            # ambiguous: Godot may own the move.  Keep the exact reservation
            # so stop() must issue a targeted native stop.
            if self._commands.get(command_id) is command_record:
                command_record["dispatch_state"] = "dispatch_unknown"
            raise
        # The targeted stop may have completed while the move POST response
        # was still in flight.  Its tombstone is authoritative: a late ACK
        # must never resurrect the command or report it as accepted.
        if self._commands.get(command_id) is not command_record:
            return self._finish_rejected_navigation(
                normalized, command_record, idempotency_key, "stopped_before_accept"
            )
        if dispatch.get("ok") is not True or dispatch.get("godot_connected") is not True or dispatch.get("status") not in {
            "sent", "accepted", "queued"
        }:
            return self._finish_rejected_navigation(
                normalized, command_record, idempotency_key, "dispatch_rejected"
            )
        accepted_unix = self._clock()
        result = {
            "schema_version": NAVIGATE_RESULT_SCHEMA,
            "navigation_id": normalized["navigation_id"],
            "environment_id": normalized["environment_id"],
            "session_id": normalized["session_id"],
            "map_id": normalized["map_id"],
            "target_id": normalized["target_id"],
            "status": "accepted",
            "command_id": command_id,
            "accepted_unix": accepted_unix,
            "reason": None,
        }
        validated = validate_navigate_result(result, request=normalized, now_unix=accepted_unix)
        command_record["result"] = validated
        command_record["dispatch_state"] = "accepted"
        _bounded_store(self._navigation_results, idempotency_key, {"request": normalized, "result": validated})
        return validated

    @staticmethod
    def _events(payload: Mapping[str, Any], command_id: str) -> list[Mapping[str, Any]]:
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, Mapping) and item.get("command_id") == command_id]

    def _current_command_event(
        self,
        payload: Mapping[str, Any],
        command: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Return only an event attributable to this accepted command epoch.

        The native results endpoint is historical.  Caller-selected command
        IDs can therefore collide with an event retained across an adapter
        restart.  Acceptance time, and correlation when the bridge supplies
        it, keep such stale or substituted events from becoming terminal
        evidence for the current reservation.
        """

        result = command.get("result")
        if not isinstance(result, Mapping):
            return None
        command_id = result.get("command_id")
        accepted_raw = result.get("accepted_unix")
        if (
            not isinstance(command_id, str)
            or isinstance(accepted_raw, bool)
            or not isinstance(accepted_raw, (int, float))
            or not math.isfinite(float(accepted_raw))
        ):
            return None
        accepted_unix = float(accepted_raw)
        navigation_id = command.get("navigation_id")
        newest: Mapping[str, Any] | None = None
        newest_key = (-math.inf, -1)
        for index, event in enumerate(self._events(payload, command_id)):
            received_raw = event.get("received_at")
            if (
                isinstance(received_raw, bool)
                or not isinstance(received_raw, (int, float))
                or not math.isfinite(float(received_raw))
            ):
                continue
            received_at = float(received_raw)
            if received_at + _TIME_EPSILON_SECONDS < accepted_unix:
                continue
            correlation_id = event.get("correlation_id")
            if correlation_id is not None and correlation_id != navigation_id:
                continue
            key = (received_at, index)
            if key > newest_key:
                newest = event
                newest_key = key
        return newest

    @staticmethod
    def _terminal_event(event: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        if event is None:
            return None
        status = str(event.get("status") or "").strip().lower()
        return event if status not in _NATIVE_NON_TERMINAL_STATUSES else None

    @staticmethod
    def _terminal_signature(event: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            event.get("command_id"),
            str(event.get("status") or "").strip().lower(),
            event.get("ok"),
            event.get("received_at"),
        )

    async def _settle_terminal_live_state(
        self,
        command: Mapping[str, Any],
        world: Mapping[str, Any],
        result_payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        """Reconcile the bounded event-before-live-state completion race.

        Godot can append the exact terminal event one frame before clearing
        route ownership.  We accept neither side in isolation: the same
        current-epoch terminal event must coexist with two consecutive fresh
        observations in which this command and the route are both inactive.
        If the discrepancy persists, transport fails, or the budget expires,
        the original inconsistent observation is returned so feedback remains
        fail-closed as ``terminal_state_inconsistent``.
        """

        initial_world = world
        initial_results = result_payload
        snapshot = self._portable_snapshot(world)
        route, _ = self._navigation_state(snapshot)
        terminal = self._terminal_event(self._current_command_event(result_payload, command))
        command_id = command["result"]["command_id"]
        motion_active = (
            world.get("active_command_id") == command_id
            or route.get("active") is True
        )
        if terminal is None or not motion_active or self.terminal_settle_timeout_seconds <= 0.0:
            return world, result_payload

        deadline = time.monotonic() + self.terminal_settle_timeout_seconds
        inactive_signature: tuple[Any, ...] | None = None
        for _ in range(_TERMINAL_SETTLE_MAX_POLLS):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            await self._sleep(min(self.poll_interval_seconds, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            try:
                fresh_world, fresh_results = await asyncio.wait_for(
                    asyncio.gather(
                        self._world(),
                        self._request("GET", "/results", None),
                    ),
                    timeout=remaining,
                )
            except Exception:
                break
            fresh_snapshot = self._portable_snapshot(fresh_world)
            fresh_route, _ = self._navigation_state(fresh_snapshot)
            fresh_event = self._current_command_event(fresh_results, command)
            fresh_terminal = self._terminal_event(fresh_event)
            if fresh_terminal is None:
                # A newer non-terminal event (or disappearance of historical
                # evidence) is not completion proof.  Let normal feedback use
                # this fresh live observation without retiring ownership.
                return fresh_world, fresh_results
            fresh_motion_active = (
                fresh_world.get("active_command_id") == command_id
                or fresh_route.get("active") is True
            )
            if fresh_motion_active:
                inactive_signature = None
                continue
            signature = self._terminal_signature(fresh_terminal)
            if signature == inactive_signature:
                return fresh_world, fresh_results
            inactive_signature = signature

        return initial_world, initial_results

    @staticmethod
    def _navigation_state(snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        route = snapshot.get("route")
        collision = snapshot.get("collision")
        if not isinstance(route, Mapping) or not isinstance(collision, Mapping):
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "route_or_collision")
        return route, collision

    @staticmethod
    def _clearance(snapshot: Mapping[str, Any], position: Sequence[float], footprint: float) -> float:
        obstacles = GodotPortableNavigationAdapter._snapshot_obstacles(snapshot)
        px, py = position[0], position[1]
        distances: list[float] = []
        for obstacle in obstacles:
            cx, cy = obstacle["center"]
            if obstacle["kind"] == "circle":
                distance = math.hypot(px - cx, py - cy) - obstacle["radius"]
            else:
                hx, hy = obstacle["half"]
                dx = max(abs(px - cx) - hx, 0.0)
                dy = max(abs(py - cy) - hy, 0.0)
                distance = math.hypot(dx, dy)
            distances.append(max(0.0, distance - footprint))
        min_x, max_x, min_y, max_y = GodotPortableNavigationAdapter._snapshot_bounds(snapshot)
        # Clearance is measured from the avatar footprint, not its centre.
        distances.extend([
            max(0.0, px - min_x - footprint),
            max(0.0, max_x - px - footprint),
            max(0.0, py - min_y - footprint),
            max(0.0, max_y - py - footprint),
        ])
        return max(0.0, min(distances)) if distances else 0.0

    @staticmethod
    def _position_arrival_verified(
        arrival: Any,
        command: Mapping[str, Any],
        current_position: Sequence[float],
    ) -> bool:
        """Verify a completed position command without trusting a node identity."""

        if not isinstance(arrival, Mapping):
            return False
        if (
            arrival.get("command_id") != command["result"]["command_id"]
            or arrival.get("target_instance_id") != 0
            or arrival.get("target_valid") is not True
            or arrival.get("ok") is not True
            or arrival.get("status") != "completed"
        ):
            return False
        try:
            reported_goal = godot_to_canonical(
                arrival.get("approach_position", arrival.get("target_position"))
            )
            expected_goal = command["goal_position"]
            if math.hypot(
                reported_goal[0] - expected_goal[0], reported_goal[1] - expected_goal[1]
            ) > 0.08:
                return False
            raw_avatar = arrival.get("avatar_position")
            observed_avatar = (
                godot_to_canonical(raw_avatar) if raw_avatar is not None else list(current_position)
            )
            effective_radius = max(
                _NATIVE_POSITION_STOP_DISTANCE_M,
                _number(arrival.get("effective_arrival_radius", 0.0), "arrival.effective_arrival_radius"),
            )
            if math.hypot(
                observed_avatar[0] - expected_goal[0], observed_avatar[1] - expected_goal[1]
            ) > effective_radius + _ARRIVAL_VERIFICATION_TOLERANCE_M:
                return False
            semantic_target = command["target"]["result"]
            semantic_radius = (
                command["request"]["arrival_radius_m"]
                + command["target"]["result"]["uncertainty_radius_m"]
                + _ARRIVAL_VERIFICATION_TOLERANCE_M
            )
            reported_target_distance = _target_planar_distance(semantic_target, observed_avatar)
            current_target_distance = _target_planar_distance(semantic_target, current_position)
            return reported_target_distance <= semantic_radius and current_target_distance <= semantic_radius
        except (PortableNavigationContractError, KeyError, TypeError):
            return False

    async def feedback(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_feedback_request(request)
        command = self._commands.get(normalized["command_id"])
        if command is None or any(
            command["result"][field] != normalized[field] for field in ("environment_id", "session_id")
        ):
            raise _error("PORTABLE_COMMAND_OWNERSHIP_MISMATCH", "command_id")
        world, result_payload = await asyncio.gather(self._world(), self._request("GET", "/results", None))
        world, result_payload = await self._settle_terminal_live_state(
            command, world, result_payload
        )
        snapshot = self._portable_snapshot(world)
        route, collision = self._navigation_state(snapshot)
        agent = snapshot.get("agent")
        if not isinstance(agent, Mapping):
            raise _error("PORTABLE_GODOT_TELEMETRY_INVALID", "agent")
        position = godot_to_canonical(agent.get("position"))
        velocity = godot_to_canonical(agent.get("velocity"))
        newest = self._current_command_event(result_payload, command)
        newest_status = str(newest.get("status") or "").strip().lower() if newest is not None else ""
        terminal = self._terminal_event(newest)
        collision_detected = (
            int(collision.get("contact_count", 0) or 0) > 0
            or collision.get("on_wall") is True
            or collision.get("on_ceiling") is True
        )
        route_status = str(route.get("status") or "")
        blocked = route.get("blocked") is True or route_status.startswith(("blocked", "replan_failed", "failed:route"))
        reached = False
        reason: str | None = None
        status = "queued"
        native_motion_inactive = (
            world.get("active_command_id") != normalized["command_id"]
            and route.get("active") is not True
        )
        if terminal is not None and not native_motion_inactive:
            # Terminal history and live movement ownership disagree.  Preserve
            # the reservation so the caller must issue an exact owned stop;
            # never let any stale completed/failed/stopped event release it.
            status = "blocked"
            reason = "terminal_state_inconsistent"
        elif terminal is not None and terminal.get("ok") is True and terminal.get("status") == "completed":
            raw = world.get("raw")
            avatar = raw.get("avatar_state") if isinstance(raw, Mapping) else None
            arrival = avatar.get("last_navigation_result") if isinstance(avatar, Mapping) else None
            reached = self._position_arrival_verified(arrival, command, position)
            status = "arrived" if reached else "failed"
            reason = None if reached else "arrival_not_verified"
        elif terminal is not None and terminal.get("status") in _NATIVE_STOPPED_STATUSES:
            status = "stopped"
        elif terminal is not None:
            status = "failed"
            reason = "native_navigation_failed"
        elif collision_detected or blocked:
            status = "blocked"
            reason = "collision_detected" if collision_detected else "route_blocked"
        elif world.get("active_command_id") == normalized["command_id"] or route.get("active") is True:
            status = "moving"
        elif command.get("terminal") == "stopped":
            status = "stopped"

        remaining = _target_planar_distance(command["target"]["result"], position)
        sequence = max(int(command.get("sequence", -1)) + 1, normalized["after_sequence"] + 1)
        command["sequence"] = sequence
        if status in {"arrived", "stopped", "failed"}:
            command["terminal"] = status
        footprint = command["map"]["result"]["agent"]["footprint_radius_m"]
        output = self._common(FEEDBACK_RESULT_SCHEMA, normalized)
        output.update({
            "command_id": normalized["command_id"],
            "sequence": sequence,
            "observed_unix": self._clock(),
            "status": status,
            "position_m": position,
            "velocity_mps": velocity,
            "remaining_distance_m": remaining,
            "minimum_clearance_m": self._clearance(snapshot, position, footprint),
            "collision_detected": collision_detected,
            "target_reached": reached,
            "reason": reason,
        })
        validated = validate_feedback_result(output, request=normalized, now_unix=self._clock())
        if status in {"arrived", "stopped", "failed"}:
            self._retire_command(command, terminal=status)
        return validated

    @staticmethod
    def _reject_stop(request: Mapping[str, Any], command: Mapping[str, Any], reason: str) -> dict[str, Any]:
        result = GodotPortableNavigationAdapter._common(STOP_RESULT_SCHEMA, request)
        result.update({
            "owned_command_id": request["command_id"],
            "status": "rejected",
            "confirmed": False,
            "stopped_unix": None,
            "final_sequence": max(0, int(command.get("sequence", -1)) + 1),
            "reason": reason[:160],
        })
        return result

    async def stop(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_stop_request(request)
        idempotency_key = (normalized["environment_id"], normalized["session_id"], normalized["request_id"])
        previous = self._stop_results.get(idempotency_key)
        if previous is not None:
            if previous["request"] != normalized:
                raise _error("PORTABLE_STOP_IDEMPOTENCY_CONFLICT", "request_id")
            return dict(previous["result"])
        command, retired = self._owned_command(normalized)
        locally_terminal = (
            retired
            or command.get("terminal") in {"arrived", "stopped", "failed", "cancelled", "rejected"}
            or command.get("dispatch_state") in {
                "reserved", "awaiting_world", "never_dispatched", "rejected"
            }
        )
        if locally_terminal:
            command["sequence"] = max(0, int(command.get("sequence", -1)) + 1)
            output = self._common(STOP_RESULT_SCHEMA, normalized)
            output.update({
                "owned_command_id": normalized["command_id"],
                "status": "already_stopped",
                "confirmed": True,
                "stopped_unix": self._clock(),
                "final_sequence": command["sequence"],
                "reason": None,
            })
            validated = validate_stop_result(output, request=normalized, now_unix=self._clock())
            if not retired:
                self._retire_command(
                    command, terminal="stopped", dispatch_state="never_dispatched"
                )
            _bounded_store(
                self._stop_results,
                idempotency_key,
                {"request": normalized, "result": validated},
            )
            return validated

        stop_command_id = uuid.uuid4().hex
        dispatch = await self._request("POST", "/command", {
            "command_id": stop_command_id,
            "correlation_id": normalized["request_id"],
            "actor_id": self.actor_id,
            "action": "stop",
            "params": {"expected_command_id": normalized["command_id"]},
            "priority": 10,
            "expires_in_ms": 3000,
            "reason": "portable-navigation-stop-v1",
        })
        if dispatch.get("ok") is not True or dispatch.get("godot_connected") is not True or dispatch.get("status") not in {
            "sent", "accepted", "queued"
        }:
            rejected = self._reject_stop(normalized, command, "stop_dispatch_rejected")
            return validate_stop_result(rejected, request=normalized, now_unix=self._clock())

        deadline = time.monotonic() + self.stop_timeout_seconds
        confirmed = False
        while True:
            results, world = await asyncio.gather(
                self._request("GET", "/results", None),
                self._world(),
            )
            stop_events = self._events(results, stop_command_id)
            owned_events = self._events(results, normalized["command_id"])
            stop_acknowledged = any(
                event.get("ok") is True and event.get("status") in {"completed", "stopped"}
                for event in stop_events
            )
            owned_stopped = any(
                event.get("status") in _NATIVE_STOPPED_STATUSES for event in owned_events
            )
            owned_ended_before_stop = any(
                event.get("status") in _NATIVE_ENDED_STATUSES for event in owned_events
            )
            owned_is_active = world.get("active_command_id") == normalized["command_id"]
            fresh_snapshot = self._portable_snapshot(world)
            fresh_route, _ = self._navigation_state(fresh_snapshot)
            route_is_active = fresh_route.get("active") is True
            confirmed = (
                stop_acknowledged
                and not owned_is_active
                and not route_is_active
                and (owned_stopped or owned_ended_before_stop)
            )
            if confirmed or time.monotonic() >= deadline:
                break
            await self._sleep(self.poll_interval_seconds)
        if not confirmed:
            rejected = self._reject_stop(normalized, command, "stop_unconfirmed")
            return validate_stop_result(rejected, request=normalized, now_unix=self._clock())

        command["terminal"] = "stopped"
        command["sequence"] = max(0, int(command.get("sequence", -1)) + 1)
        output = self._common(STOP_RESULT_SCHEMA, normalized)
        output.update({
            "owned_command_id": normalized["command_id"],
            "status": "confirmed",
            "confirmed": True,
            "stopped_unix": self._clock(),
            "final_sequence": command["sequence"],
            "reason": None,
        })
        validated = validate_stop_result(output, request=normalized, now_unix=self._clock())
        _bounded_store(
            self._stop_results,
            idempotency_key,
            {"request": normalized, "result": validated},
        )
        self._retire_command(command, terminal="stopped")
        return validated


__all__ = [
    "GodotPortableNavigationAdapter",
    "canonical_to_godot",
    "godot_to_canonical",
]
