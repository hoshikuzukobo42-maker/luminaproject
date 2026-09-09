from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path

import pytest

from lumina_next.godot_portable_navigation_adapter_v1 import (
    GodotPortableNavigationAdapter,
    canonical_to_godot,
    godot_to_canonical,
)
from lumina_next.portable_navigation_contract_v1 import (
    CAPTURE_REQUEST_SCHEMA,
    COORDINATE_CONVENTION,
    FEEDBACK_REQUEST_SCHEMA,
    GROUND_REQUEST_SCHEMA,
    MAP_REQUEST_SCHEMA,
    NAVIGATE_REQUEST_SCHEMA,
    STOP_REQUEST_SCHEMA,
    SURFACE_ID_SEMANTICS_REVISION,
    SURFACE_ROLES,
    PortableNavigationAdapter,
    PortableNavigationContractError,
)


NOW = 1_788_700_000.0
SURFACE_ID = "surface:" + "a" * 64


def _common(schema: str, request_id: str) -> dict:
    return {
        "schema_version": schema,
        "request_id": request_id,
        "environment_id": "unknown-room-17",
        "session_id": "offline-session",
        "requested_unix": NOW,
    }


def _write_frame(directory: Path, *, captured_unix: float = NOW, duplicate: bool = False) -> None:
    image = b"\x89PNG\r\n\x1a\nportable-adapter-test"
    image_path = directory / "eye.png"
    image_path.write_bytes(image)
    objects = [{
        "name": "opaque_native_node_7f3a",
        "instance_id": 42,
        "world_position": {"x": 0.0, "y": 0.0, "z": -3.0},
        "projected_bbox": [0.35, 0.30, 0.65, 0.70],
        "in_frustum": True,
        "occluded": False,
    }]
    if duplicate:
        objects.append({
            **objects[0],
            "name": "unrelated_native_node_c2d1",
            "instance_id": 43,
        })
    metadata = {
        "frame_id": "native-process-1",
        "captured_unix": captured_unix,
        "image_path": str(image_path),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "width": 64,
        "height": 48,
        "fov": 70.0,
        "camera_position": {"x": 0.0, "y": 1.4, "z": 0.0},
        "camera_forward": {"x": 0.0, "y": 0.0, "z": -1.0},
        "camera_right": {"x": 1.0, "y": 0.0, "z": 0.0},
        "camera_up": {"x": 0.0, "y": 1.0, "z": 0.0},
        "debug_target": "",
        "debug_target_alias": "",
        "debug_target_canonical": "",
        "render_source": "dedicated_eye_subviewport",
        "render_viewport_instance_id": 40,
        "render_camera_instance_id": 41,
        "active_camera_instance_id": 41,
        "portable_depth": {
            "schema_version": "lumina.portable-depth-sparse-rays.v1",
            "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
            "available": True,
            "representation": "sparse_rays",
            "alignment": "registered_normalized_to_rgb",
            "measurement_model": "ray_range_m",
            "samples": [
                {"uv_norm": [0.48, 0.48], "distance_m": 2.5, "confidence": 1.0},
                {"uv_norm": [0.50, 0.50], "distance_m": 2.5, "confidence": 1.0},
                {"uv_norm": [0.52, 0.52], "distance_m": 2.5, "confidence": 1.0},
                {"uv_norm": [0.75, 0.75], "distance_m": 4.0, "confidence": 1.0},
            ],
        },
        "evaluator_geometry": {"objects": objects},
    }
    (directory / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")


def _snapshot(
    *,
    collision: bool = False,
    agent_position: dict | None = None,
    captured_unix: float = NOW,
    route_active: bool = True,
) -> dict:
    if agent_position is None:
        agent_position = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "schema_version": "lumina.godot.portable-navigation-snapshot.v1",
        "captured_unix": captured_unix,
        "coordinate_frame": "godot_x_right_y_up_z_back",
        "bounds_xz": {"min_x": -5.0, "max_x": 5.0, "min_z": -5.0, "max_z": 5.0},
        "agent": {
            "position": dict(agent_position),
            "velocity": {"x": 0.0, "y": 0.0, "z": -0.4},
            "forward": {"x": 0.0, "y": 0.0, "z": -1.0},
            "footprint_radius_m": 0.42,
            "height_m": 1.35,
        },
        "obstacles": [{
            "obstacle_id": "godot-obstacle-000",
            "kind": "rect",
            "center_xz": {"x": 2.0, "z": 0.0},
            "half_extents_xz": {"x": 0.4, "z": 0.4},
        }],
        "route": {
            "active": route_active,
            "blocked": collision,
            "status": "replan_failed:segment_changed:no_path" if collision else "planned",
            "detour_active": True,
            "remaining_waypoints": [],
            "replan_count": 1 if collision else 0,
            "path_length_m": 3.0,
        },
        "collision": {
            "contact_count": 1 if collision else 0,
            "on_floor": True,
            "on_wall": collision,
            "on_ceiling": False,
            "route_segment_blocked": collision,
        },
    }


class FakeBridge:
    def __init__(self) -> None:
        self.collision = False
        self.active_command_id: str | None = None
        self.events: list[dict] = []
        self.posts: list[dict] = []
        self.last_navigation_result: dict = {}
        self.agent_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.snapshot_captured_unix = NOW
        self.snapshot_bounds = {
            "min_x": -5.0,
            "max_x": 5.0,
            "min_z": -5.0,
            "max_z": 5.0,
        }
        self.include_obstacles = True

    def world(self) -> dict:
        portable_snapshot = _snapshot(
            collision=self.collision,
            agent_position=self.agent_position,
            captured_unix=self.snapshot_captured_unix,
            route_active=self.active_command_id is not None,
        )
        portable_snapshot["bounds_xz"] = dict(self.snapshot_bounds)
        if not self.include_obstacles:
            portable_snapshot["obstacles"] = []
        return {
            "updated_at": NOW,
            "navigation_ready": True,
            "active_command_id": self.active_command_id,
            "avatar_position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "nearby_objects": [{
                "name": "opaque_native_node_7f3a",
                "instance_id": 42,
                "position": {"x": 0.0, "y": 0.0, "z": -3.0},
            }],
            "raw": {
                "avatar_state": {
                    "portable_navigation": portable_snapshot,
                    "last_navigation_result": dict(self.last_navigation_result),
                }
            },
        }

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "GET" and path == "/world-state":
            return self.world()
        if method == "GET" and path == "/results":
            return {"items": list(self.events)}
        if method == "POST" and path == "/command":
            assert body is not None
            self.posts.append(dict(body))
            if body["action"] == "move_to_position":
                self.active_command_id = body["command_id"]
            elif body["action"] == "stop":
                owned = body["params"]["expected_command_id"]
                self.events.extend([
                    {"command_id": owned, "ok": False, "status": "interrupted", "received_at": NOW},
                    {"command_id": body["command_id"], "ok": True, "status": "completed", "received_at": NOW},
                ])
                self.active_command_id = None
            return {"ok": True, "status": "sent", "godot_connected": True}
        raise AssertionError((method, path, body))


class TerminalSettleBridge(FakeBridge):
    """Expose a terminal event before optionally clearing live ownership."""

    def __init__(self, settle_after_feedback_world_reads: int | None) -> None:
        super().__init__()
        self.settle_after_feedback_world_reads = settle_after_feedback_world_reads
        self.feedback_phase = False
        self.feedback_world_reads = 0

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "GET" and path == "/world-state" and self.feedback_phase:
            self.feedback_world_reads += 1
            if (
                self.settle_after_feedback_world_reads is not None
                and self.feedback_world_reads >= self.settle_after_feedback_world_reads
            ):
                self.active_command_id = None
        return await super().__call__(method, path, body)


class CancelDuringDispatchBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_started = asyncio.Event()

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "POST" and path == "/command" and body and body["action"] == "move_to_position":
            self.posts.append(dict(body))
            self.active_command_id = body["command_id"]
            self.dispatch_started.set()
            await asyncio.Future()
        return await super().__call__(method, path, body)


class CatchUpSnapshotBridge(FakeBridge):
    def __init__(self, snapshot_times: list[float]) -> None:
        super().__init__()
        self.snapshot_times = list(snapshot_times)
        self.world_reads = 0

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "GET" and path == "/world-state":
            index = min(self.world_reads, len(self.snapshot_times) - 1)
            self.snapshot_captured_unix = self.snapshot_times[index]
            self.world_reads += 1
        return await super().__call__(method, path, body)


class StopWhileMoveAckPendingBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.move_ack_pending = asyncio.Event()
        self.release_move_ack = asyncio.Event()

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "POST" and path == "/command" and body and body["action"] == "move_to_position":
            self.posts.append(dict(body))
            self.active_command_id = body["command_id"]
            self.move_ack_pending.set()
            await self.release_move_ack.wait()
            return {"ok": True, "status": "sent", "godot_connected": True}
        return await super().__call__(method, path, body)


class StaleActiveAfterStopBridge(FakeBridge):
    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "POST" and path == "/command" and body and body["action"] == "stop":
            self.posts.append(dict(body))
            owned = body["params"]["expected_command_id"]
            self.events.extend([
                {"command_id": owned, "ok": False, "status": "interrupted", "received_at": NOW},
                {"command_id": body["command_id"], "ok": True, "status": "completed", "received_at": NOW},
            ])
            # Deliberately retain the owned active id and active route.  A stop
            # ACK alone is not proof that motion has ceased.
            return {"ok": True, "status": "sent", "godot_connected": True}
        return await super().__call__(method, path, body)


class ControlledFreshWorldBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.block_world = False
        self.fail_world = False
        self.world_entered = asyncio.Event()
        self.release_world = asyncio.Event()

    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "GET" and path == "/world-state" and self.block_world:
            self.world_entered.set()
            await self.release_world.wait()
        if method == "GET" and path == "/world-state" and self.fail_world:
            raise RuntimeError("deterministic fresh-world failure")
        return await super().__call__(method, path, body)


class RejectMoveDispatchBridge(FakeBridge):
    async def __call__(self, method: str, path: str, body: dict | None) -> dict:
        if method == "POST" and path == "/command" and body and body["action"] == "move_to_position":
            self.posts.append(dict(body))
            return {"ok": False, "status": "rejected", "godot_connected": True}
        return await super().__call__(method, path, body)


async def _through_ground(
    adapter: GodotPortableNavigationAdapter, *, require_depth: bool = True
) -> tuple[dict, dict, dict]:
    capture_request = {
        **_common(CAPTURE_REQUEST_SCHEMA, "capture-1"),
        "require_depth": require_depth,
        "max_age_ms": 1000,
    }
    capture = await adapter.capture(capture_request)
    map_request = {
        **_common(MAP_REQUEST_SCHEMA, "map-1"),
        "source_frame_id": capture["frame_id"],
        "resolution_m": 0.25,
        "footprint_radius_m": 0.30,
    }
    map_result = await adapter.build_map(map_request)
    ground_request = {
        **_common(GROUND_REQUEST_SCHEMA, "ground-1"),
        "source_frame_id": capture["frame_id"],
        "map_id": map_result["map_id"],
        "detection": {
            "detection_id": "qwen-detection-1",
            "label": "sofa",
            "confidence": 0.91,
            "bbox_norm": [0.35, 0.30, 0.65, 0.70],
        },
    }
    ground = await adapter.ground(ground_request)
    return capture, map_result, ground


def _navigation_request(
    capture: dict,
    map_result: dict,
    ground: dict,
    *,
    navigation_id: str,
    command_id: str,
) -> dict:
    return {
        "schema_version": NAVIGATE_REQUEST_SCHEMA,
        "navigation_id": navigation_id,
        "command_id": command_id,
        "environment_id": "unknown-room-17",
        "session_id": "offline-session",
        "requested_unix": NOW,
        "deadline_unix": NOW + 30.0,
        "source_frame_id": capture["frame_id"],
        "map_id": map_result["map_id"],
        "target_id": ground["target_id"],
        "coordinate_frame_id": ground["coordinate_frame_id"],
        "coordinate_convention": COORDINATE_CONVENTION,
        "waypoints_m": [[1.0, 0.0, 0.0], [2.5, 0.0, 0.0]],
        "arrival_radius_m": 0.6,
        "max_speed_mps": 1.2,
        "minimum_clearance_m": 0.3,
    }


def test_coordinate_transform_is_invertible_and_protocol_is_implemented(tmp_path: Path) -> None:
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)
    assert isinstance(adapter, PortableNavigationAdapter)
    assert godot_to_canonical({"x": 2.0, "y": 3.0, "z": -4.0}) == [4.0, -2.0, 3.0]
    assert canonical_to_godot([4.0, -2.0, 3.0]) == {"x": 2.0, "y": 3.0, "z": -4.0}


def test_frame_identity_covers_camera_intrinsics_dimensions_and_metric_depth(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    original = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)

    async def capture_variant(request_id: str, manifest: dict) -> dict:
        (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return dict(await adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, request_id),
            "require_depth": True,
            "max_age_ms": 1000,
        }))

    async def run() -> None:
        first = await capture_variant("capture-frame-identity-base", original)
        identical = await capture_variant("capture-frame-identity-identical", original)
        assert identical["frame_id"] == first["frame_id"]

        variants: list[tuple[str, dict]] = []
        dimensions = json.loads(json.dumps(original))
        dimensions["width"] = int(dimensions["width"]) + 1
        variants.append(("dimensions", dimensions))
        field_of_view = json.loads(json.dumps(original))
        field_of_view["fov"] = float(field_of_view["fov"]) + 1.0
        variants.append(("fov", field_of_view))
        camera_pose = json.loads(json.dumps(original))
        camera_pose["camera_position"]["x"] = 0.25
        variants.append(("camera", camera_pose))
        metric_depth = json.loads(json.dumps(original))
        metric_depth["portable_depth"]["samples"][0]["distance_m"] = 2.75
        variants.append(("depth", metric_depth))

        captures = [first]
        seen_frame_ids = {first["frame_id"]}
        for label, manifest in variants:
            capture = await capture_variant(f"capture-frame-identity-{label}", manifest)
            assert capture["frame_id"] not in seen_frame_ids
            seen_frame_ids.add(capture["frame_id"])
            captures.append(capture)

        map_ids: set[str] = set()
        for index, capture in enumerate(captures):
            portable_map = await adapter.build_map({
                **_common(MAP_REQUEST_SCHEMA, f"map-frame-identity-{index}"),
                "source_frame_id": capture["frame_id"],
                "resolution_m": 0.25,
                "footprint_radius_m": 0.30,
            })
            assert portable_map["source_frame_id"] == capture["frame_id"]
            assert portable_map["map_id"] not in map_ids
            map_ids.add(portable_map["map_id"])

        assert len(adapter._captures) == 5
        assert len(map_ids) == 5

    asyncio.run(run())


def test_capture_strictly_validates_and_forwards_surface_metadata(tmp_path: Path) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    expected_roles = sorted(SURFACE_ROLES)
    role_surface_ids = {
        role: "surface:" + str(index + 1) * 64
        for index, role in enumerate(expected_roles)
    }
    for index, sample in enumerate(manifest["portable_depth"]["samples"]):
        sample["surface_id"] = SURFACE_ID
        if index < len(expected_roles):
            role = expected_roles[index]
            sample["surface_id"] = role_surface_ids[role]
            sample["surface_role"] = role
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=FakeBridge(), clock=lambda: NOW)
    request = {
        **_common(CAPTURE_REQUEST_SCHEMA, "capture-surface-id"),
        "require_depth": True,
        "max_age_ms": 1000,
    }

    captured = asyncio.run(adapter.capture(request))

    assert [
        sample.get("surface_id") for sample in captured["depth"]["samples"]
    ] == [*[role_surface_ids[role] for role in expected_roles], SURFACE_ID]
    assert [
        sample.get("surface_role") for sample in captured["depth"]["samples"]
    ] == [*expected_roles, None]
    assert (
        captured["depth"]["surface_id_semantics_revision"]
        == SURFACE_ID_SEMANTICS_REVISION
    )


@pytest.mark.parametrize(
    "semantics_revision",
    [None, "legacy_body_identity_v1", "", 42],
)
def test_capture_rejects_missing_or_wrong_surface_id_semantics_attestation(
    tmp_path: Path, semantics_revision: object
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    if semantics_revision is None:
        manifest["portable_depth"].pop("surface_id_semantics_revision")
    else:
        manifest["portable_depth"]["surface_id_semantics_revision"] = semantics_revision
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(
        tmp_path, transport=FakeBridge(), clock=lambda: NOW
    )

    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, "capture-bad-surface-semantics"),
            "require_depth": True,
            "max_age_ms": 1000,
        }))
    assert caught.value.code == "PORTABLE_GODOT_DEPTH_INVALID"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("surface_id", "SofaBody:42"),
        ("surface_id", "surface:" + "A" * 64),
        ("surface_role", "floor"),
        ("surface_role", "NAVIGATION_OBSTACLE"),
        ("surface_role", None),
        ("surface_role", 42),
        ("surface_role", "navigation_obstacle"),
        ("surface_token", "surface:" + "a" * 64),
        ("collider_name", "SofaBody"),
        ("instance_id", 42),
    ],
)
def test_capture_rejects_malformed_or_identity_leaking_surface_fields(
    tmp_path: Path,
    field_name: str,
    field_value: object,
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    manifest["portable_depth"]["samples"][0][field_name] = field_value
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=FakeBridge(), clock=lambda: NOW)
    request = {
        **_common(CAPTURE_REQUEST_SCHEMA, "capture-bad-surface-id"),
        "require_depth": True,
        "max_age_ms": 1000,
    }

    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.capture(request))
    assert caught.value.code == "PORTABLE_GODOT_DEPTH_INVALID"


@pytest.mark.parametrize("snapshot_skew", [-0.10, 0.0, 0.50])
def test_build_map_accepts_only_coherent_capture_snapshot_time_window(
    tmp_path: Path, snapshot_skew: float
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    bridge = FakeBridge()
    bridge.snapshot_captured_unix = NOW + snapshot_skew

    async def no_wait(_seconds: float) -> None:
        return None

    adapter = GodotPortableNavigationAdapter(
        tmp_path, transport=bridge, clock=lambda: NOW, sleep=no_wait
    )

    async def run() -> None:
        capture_request = {
            **_common(CAPTURE_REQUEST_SCHEMA, f"capture-skew-ok-{snapshot_skew}"),
            "require_depth": True,
            "max_age_ms": 1000,
        }
        capture = await adapter.capture(capture_request)
        portable_map = await adapter.build_map({
            **_common(MAP_REQUEST_SCHEMA, f"map-skew-ok-{snapshot_skew}"),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.25,
            "footprint_radius_m": 0.30,
        })
        assert portable_map["source_frame_id"] == capture["frame_id"]

    asyncio.run(run())


@pytest.mark.parametrize("snapshot_skew", [-2.0, -0.100_002, 0.500_002])
def test_build_map_rejects_snapshot_before_capture_or_beyond_allowed_skew(
    tmp_path: Path, snapshot_skew: float
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    bridge = FakeBridge()
    bridge.snapshot_captured_unix = NOW + snapshot_skew

    async def no_wait(_seconds: float) -> None:
        return None

    adapter = GodotPortableNavigationAdapter(
        tmp_path, transport=bridge, clock=lambda: NOW, sleep=no_wait
    )

    async def run() -> None:
        capture_request = {
            **_common(CAPTURE_REQUEST_SCHEMA, f"capture-skew-bad-{snapshot_skew}"),
            "require_depth": True,
            "max_age_ms": 1000,
        }
        capture = await adapter.capture(capture_request)
        with pytest.raises(PortableNavigationContractError) as caught:
            await adapter.build_map({
                **_common(MAP_REQUEST_SCHEMA, f"map-skew-bad-{snapshot_skew}"),
                "source_frame_id": capture["frame_id"],
                "resolution_m": 0.25,
                "footprint_radius_m": 0.30,
            })
        assert caught.value.code == "PORTABLE_GODOT_MAP_FRAME_SKEW"
        assert adapter._maps == {}

    asyncio.run(run())


def test_build_map_reacquires_one_tick_lagging_snapshot_before_mapping(tmp_path: Path) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    bridge = CatchUpSnapshotBridge([NOW - 0.05, NOW + 0.01])

    async def no_wait(_seconds: float) -> None:
        return None

    adapter = GodotPortableNavigationAdapter(
        tmp_path, transport=bridge, clock=lambda: NOW, sleep=no_wait
    )

    async def run() -> None:
        capture = await adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, "capture-catch-up"),
            "require_depth": True,
            "max_age_ms": 1000,
        })
        portable_map = await adapter.build_map({
            **_common(MAP_REQUEST_SCHEMA, "map-catch-up"),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.25,
            "footprint_radius_m": 0.30,
        })
        assert portable_map["source_frame_id"] == capture["frame_id"]
        assert bridge.world_reads == 2

    asyncio.run(run())


def test_build_map_prefers_frame_bound_embedded_snapshot_without_bridge_skew(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    manifest_path = tmp_path / "latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    embedded = _snapshot(captured_unix=NOW + 0.01)
    embedded["source_frame_id"] = manifest["frame_id"]
    manifest["portable_navigation"] = embedded
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    async def no_bridge(_method: str, _path: str, _body: dict | None) -> dict:
        raise AssertionError("frame-bound map must not read a later bridge snapshot")

    adapter = GodotPortableNavigationAdapter(
        tmp_path, transport=no_bridge, clock=lambda: NOW + 0.02
    )

    async def run() -> None:
        capture = await adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, "capture-embedded-map"),
            "require_depth": True,
            "max_age_ms": 1000,
        })
        portable_map = await adapter.build_map({
            **_common(MAP_REQUEST_SCHEMA, "map-embedded"),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.25,
            "footprint_radius_m": 0.30,
        })
        assert portable_map["source_frame_id"] == capture["frame_id"]

    asyncio.run(run())


def test_build_map_rejects_embedded_snapshot_from_another_frame(tmp_path: Path) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    manifest_path = tmp_path / "latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    embedded = _snapshot(captured_unix=NOW + 0.01)
    embedded["source_frame_id"] = "another-native-frame"
    manifest["portable_navigation"] = embedded
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(tmp_path, clock=lambda: NOW + 0.02)

    async def run() -> None:
        capture = await adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, "capture-wrong-embedded-map"),
            "require_depth": True,
            "max_age_ms": 1000,
        })
        with pytest.raises(PortableNavigationContractError) as caught:
            await adapter.build_map({
                **_common(MAP_REQUEST_SCHEMA, "map-wrong-embedded"),
                "source_frame_id": capture["frame_id"],
                "resolution_m": 0.25,
                "footprint_radius_m": 0.30,
            })
        assert caught.value.code == "PORTABLE_GODOT_MAP_FRAME_ID_MISMATCH"

    asyncio.run(run())


def test_map_identity_covers_snapshot_time_bounds_and_agent_pose(tmp_path: Path) -> None:
    _write_frame(tmp_path, captured_unix=NOW)
    bridge = FakeBridge()
    bridge.include_obstacles = False
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)

    async def run() -> None:
        capture = await adapter.capture({
            **_common(CAPTURE_REQUEST_SCHEMA, "capture-map-identity"),
            "require_depth": True,
            "max_age_ms": 1000,
        })
        request = {
            **_common(MAP_REQUEST_SCHEMA, "map-identity-retry"),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.25,
            "footprint_radius_m": 0.30,
        }
        first = await adapter.build_map(request)
        unchanged_retry = await adapter.build_map(request)
        assert unchanged_retry["map_id"] == first["map_id"]

        bridge.agent_position = {"x": 0.25, "y": 0.0, "z": 0.0}
        moved_agent = await adapter.build_map(request)
        assert moved_agent["grid"]["occupancy_sha256"] == first["grid"]["occupancy_sha256"]
        assert moved_agent["map_id"] != first["map_id"]

        bridge.agent_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        bridge.snapshot_bounds = {
            "min_x": -4.0,
            "max_x": 6.0,
            "min_z": -5.0,
            "max_z": 5.0,
        }
        shifted_bounds = await adapter.build_map(request)
        assert shifted_bounds["grid"]["occupancy_sha256"] == first["grid"]["occupancy_sha256"]
        assert shifted_bounds["grid"]["origin_position_m"] != first["grid"]["origin_position_m"]
        assert shifted_bounds["map_id"] != first["map_id"]

        bridge.snapshot_captured_unix = NOW + 0.10
        newer_snapshot = await adapter.build_map(request)
        assert newer_snapshot["grid"]["occupancy_sha256"] == shifted_bounds["grid"]["occupancy_sha256"]
        assert newer_snapshot["map_id"] != shifted_bounds["map_id"]
        assert len(adapter._maps) == 4

    asyncio.run(run())


def test_world_boundary_clearance_is_measured_from_avatar_footprint() -> None:
    snapshot = _snapshot()
    snapshot["bounds_xz"] = {"min_x": -1.0, "max_x": 1.0, "min_z": -1.0, "max_z": 1.0}
    snapshot["obstacles"] = []
    assert GodotPortableNavigationAdapter._clearance(
        snapshot, [0.5, 0.0, 0.0], 0.30
    ) == pytest.approx(0.20)


def test_capture_map_ground_navigation_feedback_and_owned_stop(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        stop_timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    async def run() -> None:
        capture, map_result, ground = await _through_ground(adapter)
        assert capture["depth"]["representation"] == "sparse_rays"
        assert len(capture["depth"]["samples"]) == 4
        assert map_result["coordinate_convention"] == COORDINATE_CONVENTION
        assert map_result["grid"]["occupancy_sha256"]
        assert ground["status"] == "grounded"
        assert ground["target_id"].startswith("target:")
        assert "opaque_native_node" not in json.dumps(ground)
        target_record = adapter._targets[ground["target_id"]]
        assert set(target_record) == {"request", "result", "grounding_source"}
        assert target_record["grounding_source"] == "depth"

        navigation = await adapter.navigate({
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": "navigation-1",
            "command_id": "caller-command-1",
            "environment_id": "unknown-room-17",
            "session_id": "offline-session",
            "requested_unix": NOW,
            "deadline_unix": NOW + 30.0,
            "source_frame_id": capture["frame_id"],
            "map_id": map_result["map_id"],
            "target_id": ground["target_id"],
            "coordinate_frame_id": ground["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": [[1.0, 0.0, 0.0], [2.5, 0.0, 0.0]],
            "arrival_radius_m": 0.6,
            "max_speed_mps": 1.2,
            "minimum_clearance_m": 0.3,
        })
        assert navigation["status"] == "accepted"
        assert navigation["command_id"] == "caller-command-1"
        native_post = bridge.posts[-1]
        assert native_post["action"] == "move_to_position"
        assert native_post["position"] == {"x": -0.0, "y": 0.0, "z": -2.5}
        assert "target_node" not in native_post
        assert "expected_target_instance_id" not in native_post["params"]
        assert native_post["params"]["stop_distance"] == pytest.approx(0.10)
        assert native_post["params"]["portable_route"] is True
        assert native_post["params"]["portable_footprint_radius_m"] == pytest.approx(0.42)
        assert native_post["params"]["portable_minimum_clearance_m"] == pytest.approx(0.3)
        assert native_post["params"]["portable_waypoints"] == [
            {"x": -0.0, "y": 0.0, "z": -1.0},
            {"x": -0.0, "y": 0.0, "z": -2.5},
        ]

        bridge.collision = True
        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-1"),
            "command_id": navigation["command_id"],
            "after_sequence": -1,
        })
        assert feedback["status"] == "blocked"
        assert feedback["collision_detected"] is True

        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-1"),
            "command_id": navigation["command_id"],
            "reason": "operator requested stop",
        })
        assert stopped["status"] == "confirmed"
        assert stopped["owned_command_id"] == navigation["command_id"]
        assert navigation["command_id"] not in adapter._commands
        assert navigation["command_id"] in adapter._retired_commands

    asyncio.run(run())


def test_feedback_verifies_position_command_arrival_without_instance_id(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)

    async def run() -> None:
        capture, map_result, ground = await _through_ground(adapter)
        navigation = await adapter.navigate({
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": "navigation-position-arrival",
            "command_id": "caller-command-position-arrival",
            "environment_id": "unknown-room-17",
            "session_id": "offline-session",
            "requested_unix": NOW,
            "deadline_unix": NOW + 30.0,
            "source_frame_id": capture["frame_id"],
            "map_id": map_result["map_id"],
            "target_id": ground["target_id"],
            "coordinate_frame_id": ground["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": [[1.0, 0.0, 0.0], [2.5, 0.0, 0.0]],
            "arrival_radius_m": 0.6,
            "max_speed_mps": 1.2,
            "minimum_clearance_m": 0.3,
        })
        command_id = navigation["command_id"]
        bridge.active_command_id = None
        bridge.agent_position = {"x": 0.0, "y": 0.0, "z": -2.47}
        bridge.events = [{
            "command_id": command_id,
            "ok": True,
            "status": "completed",
            "received_at": NOW,
        }]
        bridge.last_navigation_result = {
            "command_id": command_id,
            "target_instance_id": 0,
            "target_valid": True,
            "target_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "approach_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "avatar_position": {"x": 0.0, "y": 0.0, "z": -2.47},
            "effective_arrival_radius": 0.1,
            "ok": True,
            "status": "completed",
        }
        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-position-arrival"),
            "command_id": command_id,
            "after_sequence": -1,
        })
        assert feedback["status"] == "arrived"
        assert feedback["target_reached"] is True
        assert feedback["remaining_distance_m"] == pytest.approx(
            min(
                math.hypot(point[0] - 2.47, point[1])
                for point in ground["surface_points_m"]
            )
        )
        assert command_id not in adapter._commands
        assert command_id in adapter._retired_commands

    asyncio.run(run())


def test_feedback_rejects_double_radius_position_arrival(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)

    async def run() -> None:
        capture, map_result, ground = await _through_ground(adapter)
        target = ground["position_m"]
        approach = [target[0] - 0.55, target[1], target[2]]
        navigation = await adapter.navigate({
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": "navigation-double-radius",
            "command_id": "caller-command-double-radius",
            "environment_id": "unknown-room-17",
            "session_id": "offline-session",
            "requested_unix": NOW,
            "deadline_unix": NOW + 30.0,
            "source_frame_id": capture["frame_id"],
            "map_id": map_result["map_id"],
            "target_id": ground["target_id"],
            "coordinate_frame_id": ground["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": [approach],
            "arrival_radius_m": 0.6,
            "max_speed_mps": 1.2,
            "minimum_clearance_m": 0.3,
        })
        assert bridge.posts[-1]["params"]["stop_distance"] == pytest.approx(0.10)
        command_id = navigation["command_id"]
        far_position = [target[0] - 1.10, target[1], target[2]]
        bridge.active_command_id = None
        bridge.agent_position = canonical_to_godot(far_position)
        bridge.events = [{
            "command_id": command_id,
            "ok": True,
            "status": "completed",
            "received_at": NOW,
        }]
        bridge.last_navigation_result = {
            "command_id": command_id,
            "target_instance_id": 0,
            "target_valid": True,
            "target_position": canonical_to_godot(approach),
            "approach_position": canonical_to_godot(approach),
            "avatar_position": canonical_to_godot(far_position),
            "effective_arrival_radius": 0.6,
            "ok": True,
            "status": "completed",
        }
        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-double-radius"),
            "command_id": command_id,
            "after_sequence": -1,
        })
        assert feedback["status"] == "failed"
        assert feedback["target_reached"] is False
        assert feedback["reason"] == "arrival_not_verified"
        assert feedback["remaining_distance_m"] > 1.0

    asyncio.run(run())


def test_feedback_reconciles_terminal_event_one_frame_before_live_state_settles(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path)
    bridge = TerminalSettleBridge(settle_after_feedback_world_reads=2)

    async def no_wait(_seconds: float) -> None:
        return None

    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        sleep=no_wait,
        poll_interval_seconds=0.001,
        terminal_settle_timeout_seconds=1.0,
    )

    async def run() -> None:
        capture, portable_map, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            portable_map,
            ground,
            navigation_id="navigation-terminal-settle",
            command_id="caller-terminal-settle",
        )
        navigation = await adapter.navigate(request)
        command_id = navigation["command_id"]
        bridge.agent_position = {"x": 0.0, "y": 0.0, "z": -2.47}
        bridge.events = [{
            "command_id": command_id,
            "correlation_id": request["navigation_id"],
            "ok": True,
            "status": "completed",
            "received_at": NOW,
        }]
        bridge.last_navigation_result = {
            "command_id": command_id,
            "target_instance_id": 0,
            "target_valid": True,
            "target_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "approach_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "avatar_position": {"x": 0.0, "y": 0.0, "z": -2.47},
            "effective_arrival_radius": 0.1,
            "ok": True,
            "status": "completed",
        }
        bridge.feedback_phase = True

        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-terminal-settle"),
            "command_id": command_id,
            "after_sequence": -1,
        })

        assert feedback["status"] == "arrived"
        assert feedback["target_reached"] is True
        assert feedback["collision_detected"] is False
        # Initial inconsistent sample, then two matching inactive samples.
        assert bridge.feedback_world_reads == 3
        assert command_id not in adapter._commands
        assert adapter._retired_commands[command_id]["terminal"] == "arrived"

    asyncio.run(run())


def test_feedback_ignores_stale_or_substituted_terminal_history(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path)
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
    )

    async def run() -> None:
        capture, portable_map, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            portable_map,
            ground,
            navigation_id="navigation-current-epoch",
            command_id="caller-reused-native-id",
        )
        navigation = await adapter.navigate(request)
        command_id = navigation["command_id"]
        bridge.events = [
            {
                # Exact caller ID, but predates this adapter's acceptance.
                "command_id": command_id,
                "ok": True,
                "status": "completed",
                "received_at": NOW - 1.0,
            },
            {
                # Fresh timestamp and exact ID, but explicitly belongs to a
                # different native correlation and must not substitute.
                "command_id": command_id,
                "correlation_id": "navigation:other",
                "ok": True,
                "status": "completed",
                "received_at": NOW + 0.01,
            },
        ]

        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-ignore-old-terminal"),
            "command_id": command_id,
            "after_sequence": -1,
        })

        assert feedback["status"] == "moving"
        assert feedback["target_reached"] is False
        assert feedback["reason"] is None
        assert command_id in adapter._commands
        assert command_id not in adapter._retired_commands

    asyncio.run(run())


def test_caller_can_stop_same_command_id_after_dispatch_await_is_cancelled(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = CancelDuringDispatchBridge()
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        stop_timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    async def run() -> None:
        capture, map_result, ground = await _through_ground(adapter)
        caller_command_id = "caller-command-cancelled-await"
        navigate_task = asyncio.create_task(adapter.navigate({
            "schema_version": NAVIGATE_REQUEST_SCHEMA,
            "navigation_id": "navigation-cancelled-await",
            "command_id": caller_command_id,
            "environment_id": "unknown-room-17",
            "session_id": "offline-session",
            "requested_unix": NOW,
            "deadline_unix": NOW + 30.0,
            "source_frame_id": capture["frame_id"],
            "map_id": map_result["map_id"],
            "target_id": ground["target_id"],
            "coordinate_frame_id": ground["coordinate_frame_id"],
            "coordinate_convention": COORDINATE_CONVENTION,
            "waypoints_m": [[1.0, 0.0, 0.0], [2.5, 0.0, 0.0]],
            "arrival_radius_m": 0.6,
            "max_speed_mps": 1.2,
            "minimum_clearance_m": 0.3,
        }))
        await bridge.dispatch_started.wait()
        navigate_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await navigate_task
        assert caller_command_id in adapter._commands
        assert bridge.posts[0]["command_id"] == caller_command_id

        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-after-cancelled-await"),
            "command_id": caller_command_id,
            "reason": "caller cancelled dispatch await",
        })
        assert stopped["status"] == "confirmed"
        assert stopped["owned_command_id"] == caller_command_id
        assert bridge.posts[-1]["params"]["expected_command_id"] == caller_command_id
        assert caller_command_id not in adapter._commands
        assert caller_command_id in adapter._retired_commands

    asyncio.run(run())


def test_stop_during_pending_move_ack_cannot_be_resurrected_as_accepted(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = StopWhileMoveAckPendingBridge()
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        stop_timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    async def run() -> None:
        capture, portable_map, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            portable_map,
            ground,
            navigation_id="navigation-stop-during-move-ack",
            command_id="caller-stop-during-move-ack",
        )
        task = asyncio.create_task(adapter.navigate(request))
        await asyncio.wait_for(bridge.move_ack_pending.wait(), timeout=1.0)

        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-during-move-ack"),
            "command_id": request["command_id"],
            "reason": "cancel while move acknowledgement is pending",
        })
        assert stopped["status"] == "confirmed"
        assert adapter._retired_commands[request["command_id"]]["terminal"] == "stopped"

        bridge.release_move_ack.set()
        navigation = await task
        assert navigation["status"] == "rejected"
        assert navigation["reason"] == "stopped_before_accept"
        assert request["command_id"] not in adapter._commands
        assert adapter._retired_commands[request["command_id"]]["terminal"] == "stopped"
        assert await adapter.navigate(request) == navigation

    asyncio.run(run())


def test_stop_ack_does_not_confirm_while_owned_command_is_still_active(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = StaleActiveAfterStopBridge()
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        stop_timeout_seconds=0.0,
    )

    async def run() -> None:
        capture, portable_map, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            portable_map,
            ground,
            navigation_id="navigation-stale-active-stop",
            command_id="caller-stale-active-stop",
        )
        navigation = await adapter.navigate(request)
        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-stale-active"),
            "command_id": navigation["command_id"],
            "reason": "verify native motion ended",
        })
        assert stopped["status"] == "rejected"
        assert stopped["confirmed"] is False
        assert stopped["reason"] == "stop_unconfirmed"
        assert navigation["command_id"] in adapter._commands
        assert navigation["command_id"] not in adapter._retired_commands

    asyncio.run(run())


@pytest.mark.parametrize(
    ("terminal_status", "terminal_ok"),
    [("completed", True), ("failed", False), ("interrupted", False)],
)
def test_terminal_event_cannot_retire_while_owned_motion_is_still_active(
    tmp_path: Path, terminal_status: str, terminal_ok: bool
) -> None:
    _write_frame(tmp_path)
    bridge = TerminalSettleBridge(settle_after_feedback_world_reads=None)

    async def no_wait(_seconds: float) -> None:
        return None

    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=bridge,
        clock=lambda: NOW,
        sleep=no_wait,
        poll_interval_seconds=0.001,
        terminal_settle_timeout_seconds=1.0,
    )

    async def run() -> None:
        capture, portable_map, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            portable_map,
            ground,
            navigation_id="navigation-completed-but-active",
            command_id="caller-completed-but-active",
        )
        navigation = await adapter.navigate(request)
        command_id = navigation["command_id"]
        bridge.agent_position = {"x": 0.0, "y": 0.0, "z": -2.47}
        bridge.events = [{
            "command_id": command_id,
            "ok": terminal_ok,
            "status": terminal_status,
            "received_at": NOW,
        }]
        bridge.last_navigation_result = {
            "command_id": command_id,
            "target_instance_id": 0,
            "target_valid": True,
            "target_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "approach_position": {"x": 0.0, "y": 0.0, "z": -2.5},
            "avatar_position": {"x": 0.0, "y": 0.0, "z": -2.47},
            "effective_arrival_radius": 0.1,
            "ok": True,
            "status": "completed",
        }
        bridge.feedback_phase = True
        feedback = await adapter.feedback({
            **_common(FEEDBACK_REQUEST_SCHEMA, "feedback-completed-but-active"),
            "command_id": command_id,
            "after_sequence": -1,
        })
        assert feedback["status"] == "blocked"
        assert feedback["target_reached"] is False
        assert feedback["reason"] == "terminal_state_inconsistent"
        assert command_id in adapter._commands
        assert command_id not in adapter._retired_commands
        # The discrepancy is retried, but the fixed poll cap prevents an
        # unbounded wait before preserving the fail-closed result.
        assert bridge.feedback_world_reads == 9

        posts_before_stop = len(bridge.posts)
        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, f"stop-inconsistent-{terminal_status}"),
            "command_id": command_id,
            "reason": "terminal telemetry disagrees with active ownership",
        })
        assert stopped["status"] == "confirmed"
        assert len(bridge.posts) == posts_before_stop + 1
        assert bridge.posts[-1]["action"] == "stop"
        assert bridge.posts[-1]["params"]["expected_command_id"] == command_id

    asyncio.run(run())


def test_stop_during_fresh_world_wait_is_local_and_prevents_late_dispatch(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    bridge = ControlledFreshWorldBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)

    async def run() -> None:
        capture, map_result, ground = await _through_ground(adapter)
        request = _navigation_request(
            capture,
            map_result,
            ground,
            navigation_id="navigation-stop-during-world",
            command_id="caller-stop-during-world",
        )
        bridge.active_command_id = "unrelated-native-command"
        bridge.block_world = True
        task = asyncio.create_task(adapter.navigate(request))
        await asyncio.wait_for(bridge.world_entered.wait(), timeout=1.0)
        assert adapter._commands[request["command_id"]]["dispatch_state"] == "awaiting_world"

        stopped = await adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-during-world"),
            "command_id": request["command_id"],
            "reason": "cancel before dispatch",
        })
        assert stopped["status"] == "already_stopped"
        assert stopped["confirmed"] is True
        assert bridge.posts == []
        assert bridge.active_command_id == "unrelated-native-command"

        bridge.release_world.set()
        navigation = await task
        assert navigation["status"] == "rejected"
        assert navigation["reason"] == "stopped_before_dispatch"
        assert request["command_id"] not in adapter._commands
        assert not any(post["action"] == "move_to_position" for post in bridge.posts)

    asyncio.run(run())


def test_fresh_world_failure_and_explicit_dispatch_rejection_leave_no_active_reservation(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path)

    async def run() -> None:
        failing_bridge = ControlledFreshWorldBridge()
        failing_adapter = GodotPortableNavigationAdapter(
            tmp_path, transport=failing_bridge, clock=lambda: NOW
        )
        capture, map_result, ground = await _through_ground(failing_adapter)
        failed_request = _navigation_request(
            capture,
            map_result,
            ground,
            navigation_id="navigation-world-failure",
            command_id="caller-world-failure",
        )
        failing_bridge.fail_world = True
        with pytest.raises(RuntimeError, match="fresh-world failure"):
            await failing_adapter.navigate(failed_request)
        assert failed_request["command_id"] not in failing_adapter._commands
        posts_before_stop = list(failing_bridge.posts)
        stopped = await failing_adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-world-failure"),
            "command_id": failed_request["command_id"],
            "reason": "world check failed",
        })
        assert stopped["status"] == "already_stopped"
        assert failing_bridge.posts == posts_before_stop

        rejecting_bridge = RejectMoveDispatchBridge()
        rejecting_adapter = GodotPortableNavigationAdapter(
            tmp_path, transport=rejecting_bridge, clock=lambda: NOW
        )
        capture, map_result, ground = await _through_ground(rejecting_adapter)
        rejected_request = _navigation_request(
            capture,
            map_result,
            ground,
            navigation_id="navigation-dispatch-rejected",
            command_id="caller-dispatch-rejected",
        )
        navigation = await rejecting_adapter.navigate(rejected_request)
        assert navigation["status"] == "rejected"
        assert navigation["reason"] == "dispatch_rejected"
        assert rejected_request["command_id"] not in rejecting_adapter._commands
        posts_before_stop = list(rejecting_bridge.posts)
        stopped = await rejecting_adapter.stop({
            **_common(STOP_REQUEST_SCHEMA, "stop-dispatch-rejected"),
            "command_id": rejected_request["command_id"],
            "reason": "dispatch rejected",
        })
        assert stopped["status"] == "already_stopped"
        assert rejecting_bridge.posts == posts_before_stop

    asyncio.run(run())


def test_capture_stale_and_debug_target_fail_closed(tmp_path: Path) -> None:
    _write_frame(tmp_path, captured_unix=NOW - 2.0)
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=FakeBridge(), clock=lambda: NOW)
    request = {**_common(CAPTURE_REQUEST_SCHEMA, "capture-stale"), "require_depth": False, "max_age_ms": 500}
    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.capture(request))
    assert caught.value.code == "PORTABLE_CAPTURE_STALE"

    _write_frame(tmp_path)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    manifest["debug_target"] = "secret-native-target"
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.capture({**request, "request_id": "capture-debug", "max_age_ms": 1000}))
    assert caught.value.code == "PORTABLE_GODOT_DEBUG_CAPTURE_FORBIDDEN"


@pytest.mark.parametrize(
    "overrides",
    [
        {"render_source": None},
        {"render_source": "main_viewport"},
        {"render_viewport_instance_id": None},
        {"render_viewport_instance_id": 0},
        {"render_viewport_instance_id": True},
        {"render_viewport_instance_id": 1.5},
        {"render_camera_instance_id": None},
        {"render_camera_instance_id": -1},
        {"render_camera_instance_id": "41"},
        {"active_camera_instance_id": None},
        {"active_camera_instance_id": True},
        {"active_camera_instance_id": 42},
    ],
)
def test_capture_rejects_missing_or_mismatched_render_attestation(
    tmp_path: Path, overrides: dict
) -> None:
    _write_frame(tmp_path)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    for key, value in overrides.items():
        if value is None:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(tmp_path, clock=lambda: NOW)
    request = {
        **_common(CAPTURE_REQUEST_SCHEMA, "capture-render-attestation"),
        "require_depth": True,
        "max_age_ms": 1000,
    }
    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.capture(request))
    assert caught.value.code == "PORTABLE_GODOT_RENDER_ATTESTATION_INVALID"


def test_sparse_depth_wins_even_when_evaluator_geometry_is_ambiguous(tmp_path: Path) -> None:
    _write_frame(tmp_path, duplicate=True)
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=FakeBridge(), clock=lambda: NOW)

    async def run() -> None:
        _, _, ground = await _through_ground(adapter)
        assert ground["status"] == "grounded"
        assert ground["reason"] is None
        assert ground["target_id"].startswith("target:")

    asyncio.run(run())


def test_strict_mode_does_not_use_evaluator_geometry_without_depth(tmp_path: Path) -> None:
    _write_frame(tmp_path)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    manifest.pop("portable_depth")
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=FakeBridge(), clock=lambda: NOW)

    async def run() -> None:
        _, _, ground = await _through_ground(adapter, require_depth=False)
        assert ground["status"] == "unresolved"
        assert ground["reason"] == "no_depth"
        assert ground["target_id"] is None

    asyncio.run(run())


def test_evaluator_geometry_fallback_is_explicit_and_still_ambiguous(tmp_path: Path) -> None:
    _write_frame(tmp_path, duplicate=True)
    manifest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    manifest.pop("portable_depth")
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = GodotPortableNavigationAdapter(
        tmp_path,
        transport=FakeBridge(),
        clock=lambda: NOW,
        allow_evaluator_geometry_fallback=True,
    )

    async def run() -> None:
        _, _, ground = await _through_ground(adapter, require_depth=False)
        assert ground["status"] == "unresolved"
        assert ground["reason"] == "ambiguous"
        assert ground["target_id"] is None

    asyncio.run(run())


def test_stop_rejects_unowned_command_before_transport(tmp_path: Path) -> None:
    bridge = FakeBridge()
    adapter = GodotPortableNavigationAdapter(tmp_path, transport=bridge, clock=lambda: NOW)
    request = {
        **_common(STOP_REQUEST_SCHEMA, "stop-unowned"),
        "command_id": "not-owned-by-this-adapter",
        "reason": "stop",
    }
    with pytest.raises(PortableNavigationContractError) as caught:
        asyncio.run(adapter.stop(request))
    assert caught.value.code == "PORTABLE_COMMAND_OWNERSHIP_MISMATCH"
    assert bridge.posts == []
