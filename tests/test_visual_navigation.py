from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import httpx

from lumina_next.health_checks import memory_health
from lumina_next.visual_navigation import (
    NavigationError, VisualNavigation, ground_detection, pose_matches, read_frame,
    relevant, valid_bbox, verified_arrival, STOP,
)


def metadata():
    return {"captured_unix": time.time(), "camera_position": [0, 1.4, 0], "camera_forward": [0, 0, -1],
        "frame_id": "f1", "image_sha256": "example", "evaluator_geometry": {"objects": [
        {"name": "InternalTargetName", "instance_id": 12, "world_position": [0, 0, -4],
         "projected_bbox": [.2, .2, .4, .6], "occluded": False, "in_frustum": True}]}}


def detection():
    return {"intent": "approach", "visible": True, "label": "plant", "confidence": .9, "bbox": [.2, .2, .4, .6]}


def nav(tmp_path):
    return VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")


def test_status_injected_phase_snapshot_does_not_repeat_file_probe(tmp_path, monkeypatch):
    from lumina_next import visual_navigation as module
    def forbidden():pytest.fail("injected status performed synchronous file probe")
    monkeypatch.setattr(module,"phase_status",forbidden)
    service=nav(tmp_path)
    snapshot={"enabled":True,"busy":None,"error":"phase_status_probe_timeout"}
    result=service.status(phase_snapshot=snapshot)
    assert result["heavy_phase"] is snapshot
    assert result["status_probe_revision"]=="offloop_singleflight_phase_status_v1"
    assert service.status(phase_snapshot={})["heavy_phase"]=={}


def test_status_legacy_sync_probe_contract_remains_available(tmp_path,monkeypatch):
    from lumina_next import visual_navigation as module
    calls=[]
    def probe():calls.append(True);return {"enabled":False,"busy":False}
    monkeypatch.setattr(module,"phase_status",probe)
    assert nav(tmp_path).status()["heavy_phase"]=={"enabled":False,"busy":False}
    assert calls==[True]


@pytest.mark.parametrize("code", ["visual_heavy_phase_tts_latched", "visual_heavy_phase_tts_resident",
                                  "visual_heavy_phase_tts_cleanup_failed", "visual_heavy_phase_tts_state_unknown"])
def test_tts_latch_prevents_frame_inference_motion_and_fallback_speech(tmp_path, monkeypatch, code):
    from lumina_next import visual_navigation as module
    from lumina_next.visual_phase import PhaseLeaseError
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    @asynccontextmanager
    async def blocked_phase():
        raise PhaseLeaseError(code)
        yield
    monkeypatch.setattr(module, "inference_lease", blocked_phase)
    async def run():
        service = nav(tmp_path)
        async def world():
            return {}
        async def forbidden(*args, **kwargs):
            pytest.fail("latched engine admitted a frame/model/motion/speech operation")
        service._world = world
        service._fresh_frame = forbidden
        service._infer = forbidden
        service._command = forbidden
        await service.start("植物まで行って", "latched", speak=True)
        await service.task
        job = service.jobs["latched"]
        assert job["status"] == "failed" and job["speak"] is False
        assert job["error"] == code
        assert service.active_id is None
    asyncio.run(run())


def test_storage_unavailable_is_degraded_not_a_crash():
    class Missing:
        def stats(self):
            raise sqlite3.OperationalError("private path")
    result = memory_health(Missing())
    assert result["ok"] is False and result["stats"] is None
    assert "private path" not in json.dumps(result)


def test_storage_stats_healthy():
    class Ready:
        def stats(self):
            return {"conversation_turns": 2}
    assert memory_health(Ready()) == {"ok": True, "stats": {"conversation_turns": 2}}


def test_grounding_uses_overlap_not_label_matching():
    assert ground_detection(detection(), metadata())["name"] == "InternalTargetName"


@pytest.mark.parametrize("change", [{"occluded": True}, {"in_frustum": False}, {"can_approach": False},
                                    {"projected_bbox": [.8, .8, 1, 1]}])
def test_occluded_out_of_view_or_unmatched_never_ground(change):
    frame = metadata()
    frame["evaluator_geometry"]["objects"][0].update(change)
    with pytest.raises(NavigationError, match="ambiguous"):
        ground_detection(detection(), frame)


def test_duplicate_projection_is_ambiguous():
    frame = metadata()
    frame["evaluator_geometry"]["objects"].append(dict(frame["evaluator_geometry"]["objects"][0], name="second"))
    with pytest.raises(NavigationError, match="ambiguous"):
        ground_detection(detection(), frame)


@pytest.mark.parametrize("box", [[0, 0, 0, 0], [0, 0, float("nan"), 1], [False, 0, 1, 1], [-1, 0, 1, 1], "bad"])
def test_invalid_boxes_fail_closed(box):
    with pytest.raises(NavigationError):
        valid_bbox(box)


def test_pose_changes_are_rejected():
    frame = metadata()
    assert pose_matches(frame, dict(frame))
    assert not pose_matches(frame, dict(frame, camera_forward=[1, 0, 0]))
    assert not pose_matches(frame, dict(frame, camera_position=[1, 1.4, 0]))
    assert not pose_matches(frame, dict(frame, camera_forward=[0, 0, 0]))
    assert not pose_matches(dict(frame, frame_id="100-8"), dict(frame, frame_id="101-1"))
    assert pose_matches(dict(frame, frame_id="100-8"), dict(frame, frame_id="100-9"))


def test_frame_requires_hash_freshness_and_contained_path(tmp_path):
    frame = metadata()
    image = b"\x89PNG\r\n\x1a\nTEST"
    path = tmp_path / "slot_1.png"
    path.write_bytes(image)
    frame.update(image_path=str(path), image_sha256=hashlib.sha256(image).hexdigest())
    sidecar = tmp_path / "latest.json"
    sidecar.write_text(json.dumps(frame))
    assert read_frame(tmp_path)[1] == image
    with pytest.raises(NavigationError):
        read_frame(tmp_path, newer_than=time.time() + 1)
    path.write_bytes(image + b"changed")
    with pytest.raises(NavigationError, match="frame_hash_mismatch"):
        read_frame(tmp_path)
    frame["image_path"] = str(tmp_path.parent / "outside.png")
    sidecar.write_text(json.dumps(frame))
    with pytest.raises(NavigationError, match="invalid_frame_path"):
        read_frame(tmp_path)


def test_prompt_excludes_sidecar_and_hidden_names(tmp_path):
    service = nav(tmp_path)
    seen = []
    async def fake_http(method, path, body=None, **kwargs):
        seen.append((path, body))
        if path == "/props":
            return {"modalities": {"vision": True}}
        answer = ({"objects": [["plant", "green", 200, 200, 400, 600]]}
                  if len(seen) == 2 else {"intent": "approach", "target_id": 0, "confidence": .9, "reason": "plant"})
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}
    service._http = fake_http
    # Unsupported polite prefix exercises the unchanged two-call fallback.
    assert asyncio.run(service._infer(b"\x89PNG\r\n\x1a\nTEST", "お願いします、植物まで行って"))["visible"] is True
    serialized = json.dumps(seen, ensure_ascii=False)
    assert "InternalTargetName" not in serialized and "world_position" not in serialized
    assert "data:image/png;base64," in serialized
    assert "植物まで行って" not in json.dumps(seen[1], ensure_ascii=False)
    assert "data:image" not in json.dumps(seen[2])


def test_text_only_backend_cannot_claim_vision(tmp_path):
    service = nav(tmp_path)
    async def fake_http(*args, **kwargs):
        return {"modalities": {"vision": False}}
    service._http = fake_http
    with pytest.raises(NavigationError, match="qwen_vision_disabled"):
        asyncio.run(service._infer(b"\x89PNG\r\n\x1a\nTEST", "look"))


def test_cancellation_before_first_task_step(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        await service.start("植物まで行って", "one", speak=False)
        assert await service.cancel("one")
        assert service.jobs["one"]["status"] == "cancelled"
        assert service.jobs["one"]["arrived"] is False
        assert service.active_id is None
    asyncio.run(run())


def test_duplicate_id_is_not_dispatched_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        await service.start("植物まで行って", "one", speak=False)
        task = service.task
        await service.start("別の場所まで行って", "one", speak=False)
        assert service.task is task
        await service.cancel()
    asyncio.run(run())


def test_missing_detection_never_sends_a_movement(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        async def world():
            return {}
        async def frame(_):
            return metadata(), b"png"
        async def infer(*_):
            return dict(detection(), visible=False)
        async def forbidden(*args, **kwargs):
            pytest.fail("must not send any command")
        service._world, service._fresh_frame, service._infer, service._command = world, frame, infer, forbidden
        await service.start("植物まで行って", "one", speak=False)
        await service.task
        assert service.jobs["one"]["status"] == "not_visible"
        assert not service.jobs["one"]["arrived"]
    asyncio.run(run())


def test_recognition_cancel_never_dispatches_stale_result(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        entered = asyncio.Event()
        async def world():
            return {}
        async def frame(_):
            return metadata(), b"png"
        async def infer(*_):
            entered.set()
            await asyncio.sleep(20)
            return detection()
        async def forbidden(*args, **kwargs):
            pytest.fail("cancelled inference cannot dispatch")
        service._world, service._fresh_frame, service._infer, service._command = world, frame, infer, forbidden
        await service.start("植物まで行って", "one", speak=False)
        await entered.wait()
        await service.cancel()
        assert service.jobs["one"]["status"] == "cancelled"
    asyncio.run(run())


def test_visual_routing_does_not_consume_normal_chat():
    assert relevant("植物まで行って") and relevant("そこに何が見える？") and relevant("止まって")
    assert not relevant("おはよう、今日の調子はどう？")


@pytest.mark.parametrize("text", ["止まってください", "ちょっと止まって", "キャンセル", "停止して", "今すぐ中止してください", "please stop", "stop please", "ルミナ、止まってくれる？"])
def test_stop_instructions_do_not_go_to_slow_vision_or_chat(text):
    assert relevant(text) and STOP.fullmatch(text)


@pytest.mark.parametrize("choices", [[], [None], [3], [{"finish_reason": "stop", "message": None}]])
def test_malformed_model_choices_fail_closed(tmp_path, choices):
    service = nav(tmp_path)
    async def fake_http(method, path, body=None, **kwargs):
        if path == "/props":
            return {"modalities": {"vision": True}}
        return {"choices": choices}
    service._http = fake_http
    with pytest.raises(NavigationError):
        asyncio.run(service._infer(b"\x89PNG\r\n\x1a\nTEST", "test"))


def test_verified_arrival_uses_real_radius_and_identity():
    job = {"command_id": "c", "target_node": "chair", "target_instance_id": 12}
    world = {"avatar_position": [0, 0, 1.65],
        "nearby_objects": [{"name": "chair", "instance_id": 12, "position": [0, 0, 0]}],
        "raw": {"avatar_state": {"last_navigation_result": {
            "command_id": "c", "target_node": "chair", "ok": True, "status": "completed",
            "target_valid": True, "target_instance_id": 12, "target_position": [0, 0, 0],
            "approach_position": [0, 0, 1.65], "effective_arrival_radius": 1.65}}}}
    assert verified_arrival(job, world) == 1.65
    job["target_instance_id"] = 11
    with pytest.raises(NavigationError, match="arrival_target_changed"):
        verified_arrival(job, world)
    job["target_instance_id"] = 12
    world["nearby_objects"][0]["instance_id"] = 13
    with pytest.raises(NavigationError, match="arrival_target_changed"):
        verified_arrival(job, world)
    world["nearby_objects"][0]["instance_id"] = 12
    world["avatar_position"] = [0, 0, 2.5]
    with pytest.raises(NavigationError, match="arrival_not_verified"):
        verified_arrival(job, world)


def test_stop_is_scoped_to_owned_command(tmp_path):
    service = nav(tmp_path)
    seen = []
    async def fake_command(action, request_id, **kwargs):
        seen.append((action, kwargs))
        return "stop", {}
    service._command = fake_command
    async def http(method, path, body=None, **kwargs):
        return {"items": [{"command_id": "stop", "ok": True, "status": "completed"}]}
    async def world():
        return {"active_command_id": ""}
    service._http, service._world = http, world
    job = {"request_id": "r", "command_id": "ours"}
    assert asyncio.run(service._safe_stop(job))
    assert job["stop_confirmed"] and service.unconfirmed_stop is None
    assert seen == [("stop", {"params": {"expected_command_id": "ours"}})]


def test_dry_run_cannot_cancel_live_navigation(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        await service.start("植物まで行って", "live", speak=False)
        original = service.task
        with pytest.raises(NavigationError, match="visual_navigation_busy"):
            await service.start("植物まで行って", "dry", speak=False, allow_move=False)
        assert service.task is original and service.active_id == "live"
        await service.cancel()
    asyncio.run(run())


def test_unconfirmed_stop_blocks_replacement_motion(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    service = nav(tmp_path)
    async def unavailable(*args, **kwargs):
        raise NavigationError("world_not_ready")
    service._command = unavailable
    old = {"request_id": "old", "command_id": "old-command"}
    async def run():
        assert not await service._safe_stop(old)
        assert old["stop_confirmed"] is False
        with pytest.raises(NavigationError, match="previous_stop_unconfirmed"):
            await service.start("ソファまで行って", "new", speak=False)
        assert "new" not in service.jobs
    asyncio.run(run())


def test_speech_waits_for_completed_playback_not_dispatch_ack(tmp_path):
    service = nav(tmp_path)
    job = {"request_id": "report", "speak": True}
    polls = []
    async def command(action, request_id, **kwargs):
        assert action == "speak" and kwargs["command_id"] == job["speech_command_id"]
        return kwargs["command_id"], {"ok": True, "status": "sent"}
    async def http(*args, **kwargs):
        polls.append(1)
        return {"items": [{"command_id": job["speech_command_id"], "ok": True,
                           "status": "accepted" if len(polls) == 1 else "completed"}]}
    service._command, service._http = command, http
    asyncio.run(service._finish(job, "arrived", "対象のところまで着いたよ。"))
    assert len(polls) == 2 and job["speech_status"] == "completed"
    assert job["speech_completion_event"]["status"] == "completed"


def test_cancelling_owned_speech_does_not_stop_an_unrelated_motion(tmp_path):
    service = nav(tmp_path)
    seen = []
    async def command(action, request_id, **kwargs):
        seen.append(kwargs)
        return "scoped-stop", {}
    async def http(*args, **kwargs):
        return {"items": [{"command_id": "scoped-stop", "ok": True, "status": "completed"}]}
    async def world():
        return {"active_command_id": "unrelated-newer-command"}
    service._command, service._http, service._world = command, http, world
    job = {"request_id": "report", "command_id": "old-motion", "speech_command_id": "owned-speech"}
    assert asyncio.run(service._safe_stop(job))
    assert seen == [{"params": {"expected_command_id": "owned-speech"}}]


def test_lost_speech_dispatch_response_stops_preallocated_speech_id(tmp_path):
    service = nav(tmp_path)
    job = {"request_id": "report", "speak": True, "command_id": "completed-motion"}
    stopped = []
    async def command(action, request_id, **kwargs):
        if action == "speak":
            raise httpx.ReadTimeout("response lost after possible acceptance")
        stopped.append(kwargs["params"]["expected_command_id"])
        return "confirmed-stop", {}
    async def http(*args, **kwargs):
        return {"items": [{"command_id": "confirmed-stop", "ok": True, "status": "completed"}]}
    async def world():
        return {"active_command_id": ""}
    service._command, service._http, service._world = command, http, world
    asyncio.run(service._finish(job, "arrived", "対象のところまで着いたよ。"))
    assert job["speech_status"] == "failed" and job["stop_confirmed"] is True
    assert stopped == [job["speech_command_id"]]


def test_lost_speech_poll_and_stop_responses_retain_unconfirmed_ownership(tmp_path):
    service = nav(tmp_path)
    job = {"request_id": "report", "speak": True}
    async def command(action, request_id, **kwargs):
        if action == "stop":
            raise httpx.ConnectError("bridge unavailable")
        return kwargs["command_id"], {"ok": True, "status": "sent"}
    async def http(*args, **kwargs):
        raise httpx.ReadTimeout("bridge became unavailable during playback")
    service._command, service._http = command, http
    asyncio.run(service._finish(job, "observed", "対象を確認できたよ。"))
    assert job["speech_status"] == "failed" and job["stop_confirmed"] is False
    assert service.unconfirmed_stop is job
    assert service.status()["unconfirmed_stop_command_id"] == job["speech_command_id"]


def test_visual_resource_trip_never_moves_or_starts_tts(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    async def run():
        service = nav(tmp_path)
        async def world():
            return {}
        async def frame(_):
            return metadata(), b"pixels"
        async def infer(*_):
            service.resource_budget.blocked_reason = "visual_resource_swap_growth_above_limit"
            raise NavigationError(service.resource_budget.blocked_reason)
        async def forbidden(*args, **kwargs):
            pytest.fail("resource trip must not launch motion or TTS")
        service._world, service._fresh_frame, service._infer, service._command = world, frame, infer, forbidden
        await service.start("ソファに近づいて", "over-budget", speak=True)
        await service.task
        job = service.jobs["over-budget"]
        assert job["status"] == "failed" and job["arrived"] is False
        assert job["speak"] is False and not job.get("speech_command_id")
        assert job["resource_budget"]["blocked_reason"] == "visual_resource_swap_growth_above_limit"
    asyncio.run(run())
