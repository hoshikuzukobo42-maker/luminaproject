"""Release-race and map-boundary adversarial tests for the Godot adapter.

The transport is entirely in memory.  These cases exercise ownership and
terminal-state races without contacting or restarting the exhibition runtime.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from lumina_next.godot_portable_navigation_adapter_v1 import (
    GodotPortableNavigationAdapter,
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
)
from lumina_next.portable_navigation_core_v1 import path_collision_report


NOW = 1_788_720_000.0
ENVIRONMENT_ID = "environment:release-race-test"
SESSION_ID = "session:release-race-test"


def _common(schema: str, request_id: str) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "request_id": request_id,
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "requested_unix": NOW,
    }


def _write_frame(directory: Path) -> None:
    image = b"\x89PNG\r\n\x1a\nrelease-race-fixture"
    image_path = directory / "eye.png"
    image_path.write_bytes(image)
    metadata = {
        "frame_id": "native-frame:release-race",
        "captured_unix": NOW,
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
            "available": True,
            "representation": "sparse_rays",
            "measurement_model": "ray_range_m",
            "alignment": "registered_normalized_to_rgb",
            "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION,
            "samples": [
                {"uv_norm": [0.48, 0.48], "distance_m": 2.0, "confidence": 1.0},
                {"uv_norm": [0.50, 0.50], "distance_m": 2.0, "confidence": 1.0},
                {"uv_norm": [0.52, 0.52], "distance_m": 2.0, "confidence": 1.0},
            ],
        },
        "evaluator_geometry": {"objects": []},
    }
    (directory / "latest.json").write_text(json.dumps(metadata), encoding="utf-8")


def _snapshot(*, bound: float = 3.0) -> dict[str, Any]:
    return {
        "schema_version": "lumina.godot.portable-navigation-snapshot.v1",
        "captured_unix": NOW,
        "coordinate_frame": "godot_x_right_y_up_z_back",
        "bounds_xz": {
            "min_x": -bound,
            "max_x": bound,
            "min_z": -bound,
            "max_z": bound,
        },
        "agent": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
            "forward": {"x": 0.0, "y": 0.0, "z": -1.0},
            "footprint_radius_m": 0.30,
            "height_m": 1.6,
        },
        "obstacles": [],
        "route": {
            "active": False,
            "blocked": False,
            "status": "idle",
            "detour_active": False,
            "remaining_waypoints": [],
            "replan_count": 0,
            "path_length_m": 0.0,
        },
        "collision": {
            "contact_count": 0,
            "on_floor": True,
            "on_wall": False,
            "on_ceiling": False,
            "route_segment_blocked": False,
        },
    }


class RaceBridge:
    def __init__(self, *, bound: float = 3.0) -> None:
        self.snapshot = _snapshot(bound=bound)
        self.world: dict[str, Any] = {
            "updated_at": NOW,
            "navigation_ready": True,
            "active_command_id": None,
            "raw": {
                "avatar_state": {
                    "portable_navigation": self.snapshot,
                    "last_navigation_result": {},
                }
            },
        }
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.results: list[dict[str, Any]] = []
        self.block_world = False
        self.world_entered = asyncio.Event()
        self.move_command_id: str | None = None
        self.stop_command_id: str | None = None
        self.owned_terminal_status: str | None = None

    async def __call__(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        captured = dict(body) if body is not None else None
        self.calls.append((method, path, captured))
        if method == "GET" and path == "/world-state":
            if self.block_world:
                self.world_entered.set()
                await asyncio.Event().wait()
            return self.world
        if method == "POST" and path == "/command":
            assert body is not None
            if body["action"] == "move_to_position":
                self.move_command_id = str(body["command_id"])
                self.world["active_command_id"] = self.move_command_id
                self.snapshot["route"].update(active=True, status="moving")
            elif body["action"] == "stop":
                self.stop_command_id = str(body["command_id"])
                assert body["params"]["expected_command_id"] == self.move_command_id
                self.world["active_command_id"] = None
                self.snapshot["route"].update(active=False, status="stopped")
            return {"ok": True, "status": "sent", "godot_connected": True}
        if method == "GET" and path == "/results":
            items = list(self.results)
            if self.owned_terminal_status is not None and self.move_command_id is not None:
                items.append({
                    "command_id": self.move_command_id,
                    "ok": self.owned_terminal_status == "completed",
                    "status": self.owned_terminal_status,
                    "received_at": NOW,
                })
            if self.stop_command_id is not None:
                items.append({
                    "command_id": self.stop_command_id,
                    "ok": True,
                    "status": "completed",
                    "received_at": NOW + 0.01,
                })
            return {"items": items}
        raise AssertionError((method, path, body))


def _adapter(directory: Path, bridge: RaceBridge) -> GodotPortableNavigationAdapter:
    async def no_wait(_seconds: float) -> None:
        return None

    return GodotPortableNavigationAdapter(
        directory,
        transport=bridge,
        clock=lambda: NOW,
        sleep=no_wait,
        stop_timeout_seconds=0.0,
    )


async def _prepare_navigation(
    adapter: GodotPortableNavigationAdapter,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    capture_request = {
        **_common(CAPTURE_REQUEST_SCHEMA, "capture:release-race"),
        "require_depth": True,
        "max_age_ms": 1000,
    }
    capture = dict(await adapter.capture(capture_request))
    map_request = {
        **_common(MAP_REQUEST_SCHEMA, "map:release-race"),
        "source_frame_id": capture["frame_id"],
        "resolution_m": 0.25,
        "footprint_radius_m": 0.30,
    }
    portable_map = dict(await adapter.build_map(map_request))
    ground_request = {
        **_common(GROUND_REQUEST_SCHEMA, "ground:release-race"),
        "source_frame_id": capture["frame_id"],
        "map_id": portable_map["map_id"],
        "detection": {
            "detection_id": "detection:release-race",
            "label": "unseen fixture",
            "confidence": 0.95,
            "bbox_norm": [0.35, 0.30, 0.65, 0.70],
        },
    }
    ground = dict(await adapter.ground(ground_request))
    assert ground["status"] == "grounded"
    navigation_request = {
        "schema_version": NAVIGATE_REQUEST_SCHEMA,
        "navigation_id": "navigation:release-race",
        "command_id": "command:release-race-owned",
        "environment_id": ENVIRONMENT_ID,
        "session_id": SESSION_ID,
        "requested_unix": NOW,
        "deadline_unix": NOW + 30.0,
        "source_frame_id": capture["frame_id"],
        "map_id": portable_map["map_id"],
        "target_id": ground["target_id"],
        "coordinate_frame_id": portable_map["coordinate_frame_id"],
        "coordinate_convention": COORDINATE_CONVENTION,
        "waypoints_m": [ground["position_m"]],
        "arrival_radius_m": 0.60,
        "max_speed_mps": 1.0,
        "minimum_clearance_m": 0.10,
    }
    return capture, portable_map, ground, navigation_request


def _feedback_request(command_id: str, request_id: str) -> dict[str, Any]:
    return {
        **_common(FEEDBACK_REQUEST_SCHEMA, request_id),
        "command_id": command_id,
        "after_sequence": -1,
    }


def _stop_request(command_id: str, request_id: str) -> dict[str, Any]:
    return {
        **_common(STOP_REQUEST_SCHEMA, request_id),
        "command_id": command_id,
        "reason": "release_race_test",
    }


def test_cancel_while_pre_dispatch_world_check_keeps_owned_stop_addressable(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path)
    bridge = RaceBridge()
    adapter = _adapter(tmp_path, bridge)

    async def run() -> None:
        _, _, _, navigate_request = await _prepare_navigation(adapter)
        bridge.block_world = True
        task = asyncio.create_task(adapter.navigate(navigate_request))
        await asyncio.wait_for(bridge.world_entered.wait(), timeout=1.0)

        # Ownership must exist before the first awaited pre-dispatch world read.
        assert navigate_request["command_id"] in adapter._commands
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        calls_before_stop = len(bridge.calls)
        stopped = await adapter.stop(
            _stop_request(navigate_request["command_id"], "stop:pre-world-cancel")
        )
        assert stopped["status"] == "already_stopped"
        assert stopped["confirmed"] is True
        assert stopped["owned_command_id"] == navigate_request["command_id"]
        assert len(bridge.calls) == calls_before_stop

    asyncio.run(run())


@pytest.mark.parametrize(
    ("event_status", "engine_active", "expected_feedback"),
    [
        ("accepted", True, "moving"),
        ("queued", True, "moving"),
        ("running", True, "moving"),
        ("sent", True, "moving"),
        ("accepted", False, "queued"),
        ("queued", False, "queued"),
        ("running", False, "queued"),
        ("sent", False, "queued"),
    ],
)
def test_nonterminal_native_event_never_becomes_failed_feedback(
    tmp_path: Path,
    event_status: str,
    engine_active: bool,
    expected_feedback: str,
) -> None:
    _write_frame(tmp_path)
    bridge = RaceBridge()
    adapter = _adapter(tmp_path, bridge)

    async def run() -> None:
        _, _, _, navigate_request = await _prepare_navigation(adapter)
        navigation = await adapter.navigate(navigate_request)
        if not engine_active:
            bridge.world["active_command_id"] = None
            bridge.snapshot["route"].update(active=False, status=event_status)
        # A stale terminal event must not win merely because an implementation
        # filters for terminal statuses before selecting the newest owned
        # event.  The newer non-terminal event is authoritative.
        bridge.results = [
            {
                "command_id": navigation["command_id"],
                "ok": False,
                "status": "failed",
                "received_at": NOW - 1.0,
            },
            {
                "command_id": navigation["command_id"],
                "ok": True,
                "status": event_status,
                "received_at": NOW,
            },
        ]
        feedback = await adapter.feedback(
            _feedback_request(navigation["command_id"], f"feedback:{event_status}")
        )
        assert feedback["status"] == expected_feedback
        assert feedback["collision_detected"] is False
        assert feedback["target_reached"] is False
        assert feedback["reason"] is None

    asyncio.run(run())


@pytest.mark.parametrize("owned_terminal", ["completed", "failed"])
def test_stop_confirms_when_move_became_terminal_during_stop_race(
    tmp_path: Path,
    owned_terminal: str,
) -> None:
    _write_frame(tmp_path)
    bridge = RaceBridge()
    adapter = _adapter(tmp_path, bridge)

    async def run() -> None:
        _, _, _, navigate_request = await _prepare_navigation(adapter)
        navigation = await adapter.navigate(navigate_request)
        bridge.world["active_command_id"] = None
        bridge.snapshot["route"].update(active=False, status=owned_terminal)
        bridge.owned_terminal_status = owned_terminal
        world_reads_before = sum(
            method == "GET" and path == "/world-state"
            for method, path, _ in bridge.calls
        )
        stopped = await adapter.stop(
            _stop_request(navigation["command_id"], f"stop:terminal-{owned_terminal}")
        )
        assert stopped["status"] == "confirmed"
        assert stopped["confirmed"] is True
        assert stopped["owned_command_id"] == navigation["command_id"]
        world_reads_after = sum(
            method == "GET" and path == "/world-state"
            for method, path, _ in bridge.calls
        )
        assert world_reads_after > world_reads_before

    asyncio.run(run())


def test_map_boundary_is_eroded_by_footprint_then_navigation_clearance(
    tmp_path: Path,
) -> None:
    _write_frame(tmp_path)
    bridge = RaceBridge(bound=1.0)
    adapter = _adapter(tmp_path, bridge)

    async def run() -> None:
        capture_request = {
            **_common(CAPTURE_REQUEST_SCHEMA, "capture:boundary"),
            "require_depth": True,
            "max_age_ms": 1000,
        }
        capture = await adapter.capture(capture_request)
        portable_map = await adapter.build_map({
            **_common(MAP_REQUEST_SCHEMA, "map:boundary"),
            "source_frame_id": capture["frame_id"],
            "resolution_m": 0.10,
            "footprint_radius_m": 0.30,
        })

        grid = portable_map["grid"]
        occupancy = base64.b64decode(grid["occupancy_base64"], validate=True)
        width, height = grid["width_cells"], grid["height_cells"]
        origin_x, origin_y = grid["origin_position_m"][:2]
        resolution = grid["resolution_m"]
        cell_half_diagonal = math.sqrt(2.0) * resolution / 2.0
        for row in range(height):
            for column in range(width):
                x = origin_x + (column + 0.5) * resolution
                y = origin_y + (row + 0.5) * resolution
                distance_to_boundary = min(x + 1.0, 1.0 - x, y + 1.0, 1.0 - y)
                # A cell is usable only when its complete square—not merely
                # its centre—fits inside the footprint-eroded map bounds.
                if distance_to_boundary <= 0.30 + cell_half_diagonal + 1e-9:
                    assert occupancy[row * width + column] >= grid["occupied_threshold"]

        assert path_collision_report(
            portable_map,
            [[0.0, 0.0, 0.0], [0.35, 0.0, 0.0]],
            additional_inflation_m=0.20,
        )["valid"] is True
        unsafe = path_collision_report(
            portable_map,
            [[0.0, 0.0, 0.0], [0.55, 0.0, 0.0]],
            additional_inflation_m=0.20,
        )
        assert unsafe["valid"] is False
        assert unsafe["reason"] == "blocked"

    asyncio.run(run())
