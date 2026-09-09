"""Whole visual-job budget regressions: all sensors, bridge and native work fake."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time

import httpx
import pytest

from lumina_next import visual_navigation as module
from lumina_next.visual_navigation import NavigationError, VisualNavigation
from lumina_next.visual_resource_budget import VisualResourceBudget, VisualResourceError


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    def forbidden(*args, **kwargs):
        pytest.fail("real HTTP or resource sensor access is forbidden")
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(module, "VisualResourceBudget", lambda: VisualResourceBudget(forbidden))


def setup(tmp_path, monkeypatch):
    service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    state = {"free": 50, "swap": 3632.44, "held": False, "calls": [], "native_held": False,
             "speech_started": asyncio.Event(), "native_release": asyncio.Event()}
    service.resource_budget = VisualResourceBudget(lambda: (state["free"], state["swap"]), interval=.005)
    job = {"request_id": "job-budget", "status": "observing", "started_unix": time.time(),
           "arrived": False, "speak": True, "allow_move": False}
    service.jobs[job["request_id"]] = job
    service.active_id = job["request_id"]

    @asynccontextmanager
    async def lease():
        assert not state["native_held"]
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False
    monkeypatch.setattr(module, "inference_lease", lease)

    async def world():
        return {"active_command_id": ""}
    async def wake(current):
        assert state["held"]
        await service.resource_budget.check(minimum_free_percent=40)
    async def frame(_):
        return {"frame_id": "test", "image_sha256": "fixture", "captured_unix": time.time()}, b"pixels"
    async def infer(*args):
        assert state["held"]
        return {"visible": False}
    async def native():
        state["native_held"] = True
        try:
            await state["native_release"].wait()
        finally:
            state["native_held"] = False
    async def command(action, request_id, **kwargs):
        state["calls"].append((action, request_id, kwargs))
        if action == "speak":
            assert not state["held"]
            state["native_task"] = asyncio.create_task(native())
            if "speech_reading" in state:
                state["free"], state["swap"] = state["speech_reading"]
            state["speech_started"].set()
            return kwargs["command_id"], {}
        assert action == "stop"
        state["stop_id"] = "scoped-stop"
        if "stop_started" in state:
            state["stop_started"].set()
            await state["stop_release"].wait()
        return state["stop_id"], {}
    async def http(*args, **kwargs):
        if state.get("stop_id"):
            return {"items": [{"command_id": state["stop_id"], "ok": True, "status": "completed"}]}
        if state.get("speech_completed"):
            state["native_release"].set()
            await state["native_task"]
            return {"items": [{"command_id": job["speech_command_id"], "ok": True, "status": "completed"}]}
        await asyncio.sleep(.001)
        return {"items": []}
    service._world, service._wake_model, service._fresh_frame = world, wake, frame
    service._infer, service._command, service._http = infer, command, http
    return service, job, state


async def release_native(state):
    state["native_release"].set()
    if "native_task" in state:
        await state["native_task"]


@pytest.mark.parametrize("reading,code", [
    ((29, 4163.25), "visual_resource_swap_growth_above_limit"),
    ((21, 3632.44), "visual_resource_free_memory_below_limit"),
])
def test_provisional_arrival_is_failed_when_final_tts_exceeds_same_job_budget(tmp_path, monkeypatch, reading, code):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["speech_reading"] = reading
        async def arrived(current, text):
            current["command_id"] = "completed-motion"
            await service._finish(current, "arrived", "着いたよ。")
        service._run_job = arrived
        task = asyncio.create_task(service._run(job, "ソファに近づいて"))
        await state["speech_started"].wait()
        assert job["arrived"] is True and service.active_id == job["request_id"]
        await task
        assert job["status"] == "failed" and job["arrived"] is False
        assert job["speak"] is False and job["error"] == code
        assert job["resource_budget"]["baseline_swap_mib"] == 3632.44
        assert job["resource_monitor_scope"] == "visual_job_including_speech"
        assert [row[0] for row in state["calls"]] == ["speak", "stop"]
        assert state["calls"][-1][2]["params"] == {"expected_command_id": job["speech_command_id"]}
        assert state["native_held"]  # HTTP/job completion is not native abort/drain.
        assert service.active_id is None
        if code.endswith("swap_growth_above_limit"):
            assert reading[1] - 3936.50 < 256  # The retained engine baseline would miss Case17.
            assert job["resource_budget"]["peak_swap_growth_mib"] > 530
        await release_native(state)
    asyncio.run(run())


def test_resource_trip_during_error_report_speech_overrides_original_ambiguity(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["speech_reading"] = (29, 4163.25)
        async def infer(*args):
            raise NavigationError("ambiguous_visual_target")
        service._infer = infer
        await service._run(job, "植物が見える？")
        assert job["status"] == "failed" and not job["arrived"]
        assert job["error"] == "visual_resource_swap_growth_above_limit"
        assert job["speak"] is False
        assert [row[0] for row in state["calls"]] == ["speak", "stop"]
        assert state["native_held"]
        await release_native(state)
    asyncio.run(run())


def test_resource_stop_failure_keeps_exact_unconfirmed_owner_and_blocks_new_job(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["speech_reading"] = (29, 4163.25)
        original = service._command
        async def command(action, request_id, **kwargs):
            if action == "stop":
                state["calls"].append((action, request_id, kwargs))
                raise httpx.ReadTimeout("stop response lost")
            return await original(action, request_id, **kwargs)
        service._command = command
        await service._run(job, "植物が見える？")
        assert job["status"] == "failed" and job["arrived"] is False
        assert job["speech_status"] == "stop_unconfirmed" and job["stop_confirmed"] is False
        assert service.unconfirmed_stop is job
        with pytest.raises(NavigationError, match="previous_stop_unconfirmed"):
            await service.start("植物が見える？", "new", speak=False)
        assert "new" not in service.jobs
        assert all(row[2]["params"]["expected_command_id"] == job["speech_command_id"]
                   for row in state["calls"] if row[0] == "stop")
        assert state["native_held"]
        await release_native(state)
    asyncio.run(run())


def test_same_monitor_also_stops_owned_movement_without_fallback_speech(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        async def moving(current, text):
            current.update(status="moving", command_id="owned-motion")
            state["swap"] = 4163.25
            await asyncio.Event().wait()
        service._run_job = moving
        await service._run(job, "ソファに近づいて")
        assert job["status"] == "failed" and not job["arrived"]
        assert [row[0] for row in state["calls"]] == ["stop"]
        assert state["calls"][0][2]["params"] == {"expected_command_id": "owned-motion"}
        assert service.resource_budget.baseline_swap_mib == 3632.44
    asyncio.run(run())


def test_success_keeps_monitor_through_actual_speech_completion_and_final_sample(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["speech_completed"] = True
        checks = []
        original = service.resource_budget.check
        async def check(**kwargs):
            checks.append((job.get("speech_status"), service.active_id))
            await original(**kwargs)
        service.resource_budget.check = check
        await service._run(job, "植物が見える？")
        assert job["status"] == "not_visible" and job["speech_status"] == "completed"
        assert checks[-1] == ("completed", job["request_id"])
        assert service.active_id is None and not state["native_held"]
        assert service.resource_budget.baseline_swap_mib == 3632.44
    asyncio.run(run())


def test_final_sample_can_reject_even_completed_speech_without_erasing_its_event(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["speech_completed"] = True
        original = service._http
        async def http(*args, **kwargs):
            result = await original(*args, **kwargs)
            state["swap"] = 4163.25
            return result
        service._http = http
        await service._run(job, "植物が見える？")
        assert job["status"] == "failed" and not job["arrived"]
        assert job["speech_status"] == "completed"
        assert job["speech_completion_event"]["status"] == "completed"
        assert job["error"] == "visual_resource_swap_growth_above_limit"
        assert service.active_id is None
    asyncio.run(run())


def test_repeated_user_cancel_does_not_interrupt_exact_stop_or_free_native_lease(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        state["stop_started"], state["stop_release"] = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service._run(job, "植物が見える？"))
        await state["speech_started"].wait()
        task.cancel()
        await state["stop_started"].wait()
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and service.active_id == job["request_id"]
        assert state["native_held"]
        state["stop_release"].set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert job["stop_confirmed"] is True and job["speech_status"] == "cancelled"
        assert len([row for row in state["calls"] if row[0] == "stop"]) == 1
        assert state["calls"][-1][2]["params"]["expected_command_id"] == job["speech_command_id"]
        assert state["native_held"] and service.active_id is None
        await release_native(state)
    asyncio.run(run())


def test_resource_trip_then_repeated_cancel_keeps_nested_http_cleanup_under_lease(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event()
        async def inference(*args):
            async def operation():
                state["swap"] = 4163.25
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await cleanup_release.wait()
                    assert state["held"] and job["phase_lease_held"]
            try:
                return await service.resource_budget.run(operation)
            except VisualResourceError as exc:
                raise NavigationError(str(exc)) from exc
        service._infer = inference
        task = asyncio.create_task(service._run(job, "植物が見える？"))
        await cleanup_started.wait()
        task.cancel()
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and state["held"] and service.active_id == job["request_id"]
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not state["held"] and not job["phase_lease_held"]
        assert job["status"] == "failed" and job["error"] == "visual_resource_swap_growth_above_limit"
        assert state["calls"] == [] and service.active_id is None
    asyncio.run(run())


def test_cancel_during_final_check_cannot_admit_new_job_before_post_cancel_check(tmp_path, monkeypatch):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        final_check, post_cancel_check, release_check = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = service.resource_budget.check
        calls, body_calls = 0, []
        async def check(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                final_check.set()
                await asyncio.Event().wait()
            elif calls == 3:
                post_cancel_check.set()
                await release_check.wait()
            await original(**kwargs)
        async def body(current, text):
            body_calls.append(current["request_id"])
            current.update(status="arrived", arrived=True)
        service.resource_budget.check, service._run_job = check, body
        service.task = asyncio.create_task(service._run(job, "ソファに近づいて"))
        await final_check.wait()
        assert service.active_id == job["request_id"]
        state["swap"] = 4163.25
        new_submission = asyncio.create_task(service.start("植物が見える？", "new", speak=False))
        await post_cancel_check.wait()
        assert not new_submission.done() and "new" not in service.jobs
        assert service.active_id == job["request_id"]
        release_check.set()
        await new_submission
        await service.task
        assert body_calls == [job["request_id"]]
        assert job["status"] == "failed" and job["arrived"] is False
        assert service.jobs["new"]["status"] == "failed"
        assert service.jobs["new"]["error"] == "visual_resource_swap_growth_above_limit"
        assert state["calls"] == [] and service.active_id is None
    asyncio.run(run())


@pytest.mark.parametrize("visual,phase", [("1", "0"), ("0", "1")])
def test_flag_off_has_no_outer_resource_io_or_contract_change(tmp_path, monkeypatch, visual, phase):
    async def run():
        service, job, state = setup(tmp_path, monkeypatch)
        monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", visual)
        monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", phase)
        async def forbidden(**kwargs):
            pytest.fail("disabled whole-job monitor performed resource I/O")
        async def body(current, text):
            current.update(status="observed", arrived=False)
        service.resource_budget.check, service._run_job = forbidden, body
        await service._run(job, "植物が見える？")
        assert job["status"] == "observed" and "resource_monitor_scope" not in job
        assert service.active_id is None and state["calls"] == []
    asyncio.run(run())
