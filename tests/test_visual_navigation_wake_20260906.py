"""Offline wake/capture sequencing: never contacts model, bridge, or sensors."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from lumina_next import visual_navigation as module
from lumina_next.visual_navigation import NavigationError, VisualNavigation
from lumina_next.visual_resource_budget import VisualResourceBudget


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    def forbidden(*args, **kwargs):
        pytest.fail("test attempted real HTTP or system resource access")
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(module, "VisualResourceBudget", lambda: VisualResourceBudget(lambda: (52, 100), interval=.005))


def setup_service(tmp_path, monkeypatch, *, speak=False):
    service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    events = []
    held = {"value": False}
    job = {"request_id": "wake-test", "status": "observing", "started_unix": time.time() - 20,
           "arrived": False, "speak": speak, "allow_move": False}
    service.jobs[job["request_id"]] = job
    service.active_id = job["request_id"]

    @asynccontextmanager
    async def lease():
        held["value"] = True
        events.append("lease_enter")
        try:
            yield
        finally:
            assert not job.get("phase_lease_held", False)
            assert not job.get("model_wake", {}).get("phase_lease_held", False)
            held["value"] = False
            events.append("lease_exit")
    monkeypatch.setattr(module, "inference_lease", lease)

    async def world():
        return {}
    async def frame(newer_than):
        assert held["value"]
        events.append(("frame", newer_than))
        return {"frame_id": "f1", "image_sha256": "image-test", "captured_unix": time.time()}, b"image"
    async def infer(image, text):
        assert held["value"]
        events.append("infer")
        return {"visible": False}
    async def forbidden(*args, **kwargs):
        pytest.fail("wake failure dispatched motion or speech")
    service._world, service._fresh_frame, service._infer, service._command = world, frame, infer, forbidden
    return service, job, events, held


def cold_http(events, held, *, final_sleep=False):
    count = 0
    async def request(method, path, body=None, *, model=False):
        nonlocal count
        assert held["value"] and method == "GET" and model is True and body is None
        events.append(path)
        if path == "/props":
            count += 1
            return {"is_sleeping": True if count == 1 else final_sleep}
        assert path == "/slots"
        return {"slots": [{"id": 0, "is_processing": False}]}
    return request


def test_cold_wake_precedes_fresh_capture_without_resetting_request_start(tmp_path, monkeypatch):
    service, job, events, held = setup_service(tmp_path, monkeypatch)
    service._http = cold_http(events, held)
    initial_start = job["started_unix"]
    checks = []
    original_check = service.resource_budget.check
    async def check(*, minimum_free_percent=22):
        checks.append(minimum_free_percent)
        await original_check(minimum_free_percent=minimum_free_percent)
    service.resource_budget.check = check
    asyncio.run(service._run(job, "左の緑の植物が見える？"))
    assert job["status"] == "not_visible"
    assert events[:4] == ["lease_enter", "/props", "/slots", "/props"]
    frame_event = events[4]
    assert frame_event[0] == "frame" and frame_event[1] >= job["model_wake"]["started_unix"]
    assert frame_event[1] > initial_start and job["started_unix"] == initial_start
    assert events[5:] == ["infer", "lease_exit"]
    # Whole-job admission/final checks now surround the original wake checks;
    # the distinct 40% cold-load gate remains between wake admission and exit.
    assert checks == [22, 22, 40, 22, 22]
    assert job["model_wake"]["stage"] == "ready"
    assert job["model_wake"]["was_sleeping"] is True
    assert job["model_wake"]["phase_lease_held"] is False
    assert job["phase_lease_held"] is False
    assert job["model_wake"]["ready_is_sleeping"] is False
    assert job["model_wake"]["started_unix"] <= job["model_wake"]["ready_unix"] <= frame_event[1]
    assert job["model_wake"]["request_id"] == job["request_id"]
    assert job["model_wake"]["deadline_unix"] - job["model_wake"]["started_unix"] == 30
    assert job["model_wake_latency_ms"] == job["model_wake"]["latency_ms"] >= 0
    assert service.resource_budget.baseline_swap_mib == 100


def test_hot_model_needs_no_cold_gate_or_waking_probe(tmp_path, monkeypatch):
    service, job, events, held = setup_service(tmp_path, monkeypatch)
    service.resource_budget = VisualResourceBudget(lambda: (24, 100))
    async def request(method, path, **kwargs):
        assert held["value"] and path == "/props"
        events.append(path)
        return {"is_sleeping": False}
    service._http = request
    asyncio.run(service._run(job, "植物が見える？"))
    assert job["status"] == "not_visible" and job["model_wake"]["was_sleeping"] is False
    assert events.count("/props") == 1 and "/slots" not in events
    assert service.resource_budget.blocked_reason is None


def test_owned_motion_keepalive_refreshes_hot_model_under_phase_lease(
    tmp_path, monkeypatch
):
    service, job, events, held = setup_service(tmp_path, monkeypatch)

    async def request(method, path, body=None, *, model=False):
        assert held["value"] and method == "GET" and body is None and model is True
        events.append(path)
        if path == "/slots":
            return {"slots": [{"id": 0, "is_processing": False}]}
        assert path == "/props"
        return {"is_sleeping": False}

    service._http = request
    asyncio.run(service._refresh_model_idle_timer(job))

    assert events == ["lease_enter", "/slots", "/props", "lease_exit"]
    assert job["model_motion_keepalive"]["status"] == "ready"
    assert job["model_motion_keepalive"]["count"] == 1
    assert job["model_motion_keepalive"]["phase_lease_held"] is False
    assert job["model_motion_keepalive"]["last_latency_ms"] >= 0


def test_phase_flag_zero_preserves_no_wake_and_old_capture_boundary(tmp_path, monkeypatch):
    service, job, events, _ = setup_service(tmp_path, monkeypatch)
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "0")
    async def forbidden(*args, **kwargs):
        pytest.fail("phase flag zero attempted model readiness I/O")
    service._http = forbidden
    asyncio.run(service._run(job, "植物が見える？"))
    assert "model_wake" not in job and "model_wake_latency_ms" not in job
    assert events[1] == ("frame", job["started_unix"])
    assert job["status"] == "not_visible"


def test_preload_under_40_refuses_slots_frame_and_speech(tmp_path, monkeypatch):
    service, job, events, held = setup_service(tmp_path, monkeypatch, speak=True)
    service.resource_budget = VisualResourceBudget(lambda: (39, 100))
    service._http = cold_http(events, held)
    asyncio.run(service._run(job, "植物が見える？"))
    assert events == ["lease_enter", "/props", "lease_exit"]
    assert job["status"] == "failed" and job["speak"] is False
    assert job["error"] == "visual_resource_preload_memory_below_limit"
    assert job["model_wake"]["stage"] == "failed"


@pytest.mark.parametrize("after,code", [((21, 100), "visual_resource_free_memory_below_limit"),
                                        ((30, 357), "visual_resource_swap_growth_above_limit")])
def test_resource_monitor_covers_blocked_reload_and_drains_http(tmp_path, monkeypatch, after, code):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    loading = {"value": False}
    cleaned = []
    service.resource_budget = VisualResourceBudget(lambda: after if loading["value"] else (52, 100), interval=.005)
    async def request(method, path, **kwargs):
        events.append(path)
        if path == "/props":
            return {"is_sleeping": True}
        assert job["model_wake"]["stage"] == "loading"
        assert job["model_wake"]["phase_lease_held"] is True
        assert job["phase_lease_held"] is True
        loading["value"] = True
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append("http_drained")
            assert job["model_wake"]["phase_lease_held"] is True
    service._http = request
    asyncio.run(service._run(job, "植物が見える？"))
    assert cleaned == ["http_drained"] and events == ["lease_enter", "/props", "/slots", "lease_exit"]
    assert job["error"] == code and job["speak"] is False
    assert job["model_wake"]["stage"] == "failed"
    assert service.resource_budget.baseline_swap_mib == 100


def test_service_lifetime_swap_baseline_is_not_rebased_for_wake(tmp_path, monkeypatch):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    service.resource_budget = VisualResourceBudget(lambda: (52, 357))
    service.resource_budget.baseline_swap_mib = 100
    asyncio.run(service._run(job, "植物が見える？"))
    assert events == []  # Whole-job admission rejects before any lease/model I/O.
    assert job["error"] == "visual_resource_swap_growth_above_limit"
    assert service.resource_budget.baseline_swap_mib == 100


@pytest.mark.parametrize("props", [{}, {"is_sleeping": None}, {"is_sleeping": 0}, {"is_sleeping": "true"}])
def test_unknown_state_never_wakes_or_speaks(tmp_path, monkeypatch, props):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    async def request(method, path, **kwargs):
        assert path == "/props"
        return props
    service._http = request
    asyncio.run(service._run(job, "植物が見える？"))
    assert events == ["lease_enter", "lease_exit"]
    assert job["error"] == "visual_heavy_phase_model_state_unknown" and job["speak"] is False


def test_sleeping_after_slots_is_not_ready(tmp_path, monkeypatch):
    service, job, events, held = setup_service(tmp_path, monkeypatch, speak=True)
    service._http = cold_http(events, held, final_sleep=True)
    asyncio.run(service._run(job, "植物が見える？"))
    assert events == ["lease_enter", "/props", "/slots", "/props", "lease_exit"]
    assert job["error"] == "visual_heavy_phase_model_wake_not_ready"
    assert job["speak"] is False


@pytest.mark.parametrize("error,code", [
    (httpx.ReadTimeout("private response detail"), "visual_heavy_phase_model_wake_http_error"),
    (NavigationError("invalid_service_response"), "visual_heavy_phase_model_wake_invalid_response"),
    (ValueError("private malformed JSON"), "visual_heavy_phase_model_wake_failed")])
def test_unavailable_or_invalid_wake_response_suppresses_speech(tmp_path, monkeypatch, error, code):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    async def request(*args, **kwargs):
        raise error
    service._http = request
    asyncio.run(service._run(job, "植物が見える？"))
    assert job["error"] == code and job["speak"] is False
    assert job["model_wake"]["stage"] == "failed" and "private" not in repr(job)
    assert events == ["lease_enter", "lease_exit"]


@pytest.mark.parametrize("post_timeout_free,code", [(52, "visual_heavy_phase_model_wake_timeout"),
                                                    (21, "visual_resource_free_memory_below_limit")])
def test_wake_deadline_and_post_timeout_resource_check(tmp_path, monkeypatch, post_timeout_free, code):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    cleaned = {"value": False}
    original_timeout = asyncio.timeout
    timeouts = []
    def timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(.015 if seconds == 30 else seconds)
    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    service.resource_budget = VisualResourceBudget(lambda: (post_timeout_free if cleaned["value"] else 52, 100), interval=1)
    async def request(method, path, **kwargs):
        events.append(path)
        if path == "/props":
            return {"is_sleeping": True}
        try:
            await asyncio.Event().wait()
        finally:
            cleaned["value"] = True
    service._http = request
    asyncio.run(service._run(job, "植物が見える？"))
    assert cleaned["value"] and timeouts == [30]
    assert job["error"] == code and job["speak"] is False
    assert job["model_wake"]["stage"] == "failed"
    assert service.resource_budget.last_free_percent == post_timeout_free
    assert events == ["lease_enter", "/props", "/slots", "lease_exit"]


def test_cancelled_reload_is_drained_before_lease_release_not_claimed_native_aborted(tmp_path, monkeypatch):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    async def run():
        loading = asyncio.Event()
        async def request(method, path, **kwargs):
            if path == "/props":
                return {"is_sleeping": True}
            loading.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                assert job["model_wake"]["phase_lease_held"] is True
                events.append("http_drained")
        service._http = request
        task = asyncio.create_task(service._run(job, "植物が見える？"))
        await loading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert events == ["lease_enter", "http_drained", "lease_exit"]
    assert job["status"] == "cancelled" and job["model_wake"]["stage"] == "cancelled"
    assert "native_aborted" not in repr(job)


def test_resleep_after_fresh_capture_fails_without_image_dispatch(tmp_path, monkeypatch):
    service, job, events, _ = setup_service(tmp_path, monkeypatch, speak=True)
    service._infer = VisualNavigation._infer.__get__(service)
    count = 0
    async def request(method, path, **kwargs):
        nonlocal count
        assert path == "/props"
        count += 1
        return {"is_sleeping": count > 1}
    async def forbidden(*args, **kwargs):
        pytest.fail("reslept model was implicitly reloaded by image inference")
    service._http = request
    monkeypatch.setattr(module, "perceive", forbidden)
    asyncio.run(service._run(job, "植物が見える？"))
    assert count == 2
    assert job["error"] == "visual_heavy_phase_model_reslept_before_inference"
    assert job["speak"] is False and job["status"] == "failed"


def test_image_timeout_uses_live_measured_budget_after_wake(tmp_path, monkeypatch):
    service, job, events, held = setup_service(tmp_path, monkeypatch)
    service._infer = VisualNavigation._infer.__get__(service)
    service._http = cold_http(events, held)
    timeouts = []
    original_timeout = asyncio.timeout
    def timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(seconds)
    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    async def perceive(http, image, text):
        assert held["value"]
        return {"visible": False}
    monkeypatch.setattr(module, "perceive", perceive)
    asyncio.run(service._run(job, "植物が見える？"))
    assert job["status"] == "not_visible" and timeouts == [
        30,
        module.VISUAL_INFERENCE_TIMEOUT_SECONDS,
    ]
    assert service.resource_budget.baseline_swap_mib == 100
    assert "recognition_latency_ms" in job and "model_wake_latency_ms" in job


@pytest.mark.parametrize("value,code", [([], "slots_invalid"), ([1], "slots_invalid"),
    ([{"id": 0}], "slots_invalid"), ([{"id": False, "is_processing": False}], "slots_invalid"),
    ([{"id": 1, "is_processing": False}], "slots_invalid"),
    ([{"id": 0, "is_processing": 0}], "slots_invalid"),
    ([{"id": 0, "is_processing": False}] * 2, "slots_invalid"),
    ({"slots": [{"id": 0, "is_processing": False}]}, "slots_invalid"),
    ([{"id": 0, "is_processing": True}], "slot_busy")])
def test_http_slots_requires_exact_single_idle_slot(tmp_path, monkeypatch, value, code):
    async def request(self, method, url, **kwargs):
        return httpx.Response(200, json=value, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    with pytest.raises(NavigationError, match="visual_heavy_phase_model_" + code):
        asyncio.run(service._http("GET", "/slots", model=True))


@pytest.mark.parametrize("method,path,model,valid", [("GET", "/slots", True, True),
    ("GET", "/props", True, False), ("POST", "/slots", True, False),
    ("GET", "/slots", False, False), ("GET", "/slots?extra=1", True, False)])
def test_only_exact_model_get_slots_accepts_array(tmp_path, monkeypatch, method, path, model, valid):
    value = [{"id": 0, "is_processing": False, "n_ctx": 2048}]
    async def request(self, method, url, **kwargs):
        assert self.trust_env is False and self.follow_redirects is False
        return httpx.Response(200, json=value, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    if valid:
        assert asyncio.run(service._http(method, path, model=model)) == {"slots": value}
    else:
        with pytest.raises(NavigationError, match="invalid_service_response"):
            asyncio.run(service._http(method, path, model=model))
