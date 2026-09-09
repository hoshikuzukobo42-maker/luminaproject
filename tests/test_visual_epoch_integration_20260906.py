"""CPU-only shared epoch integration; temporary journals, fake sensors/HTTP."""
from __future__ import annotations

import asyncio
import copy
import fcntl
import os
import threading
import time

import httpx
import pytest

from services import lumina_resource_epoch as epoch
from lumina_next import visual_phase as phase
from lumina_next import visual_navigation as navigation
from lumina_next.visual_resource_budget import SharedResourceEpoch, VisualResourceBudget, VisualResourceError
REAL_HEALTH_GATE = phase._check_tts_health


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    monkeypatch.setenv("LUMINA_VISUAL_RESOURCE_EPOCH", "1")
    path = tmp_path / "epoch.json"
    epoch.initialize_epoch(900, epoch_id="fixture-epoch", source_notes="PRIVATE_NOT_FOR_STATUS", path=path)
    real_record, real_read = epoch.record_resources, epoch.read_epoch
    def record(free, swap, **kwargs):
        return real_record(free, swap, path=path, **kwargs)
    def read(**kwargs):
        return real_read(path=path, **kwargs)
    monkeypatch.setattr(epoch, "record_resources", record)
    monkeypatch.setattr(epoch, "read_epoch", read)
    monkeypatch.setattr(phase, "SHARED_EPOCH", SharedResourceEpoch(source="next_phase"))
    monkeypatch.setattr(phase, "read_visual_resources", lambda: (50, 1000))
    async def healthy(path):
        assert phase.status(path=path)["busy"] is True
        return phase._engine_epoch_state({"enabled": True, "error": None,
                                          "observed_unix": time.time(), "state": read()})
    monkeypatch.setattr(phase, "_check_tts_health", healthy)
    return path, record, read


def lock_is_held(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def test_shared_budget_is_authority_without_rebasing_local_telemetry(shared):
    readings = iter([(50, 1000), (50, 1100), (50, 1160)])
    budget = VisualResourceBudget(lambda: next(readings))
    async def run():
        await budget.check()
        await budget.check()
        with pytest.raises(VisualResourceError, match="visual_resource_epoch_") as caught:
            await budget.check()
        assert caught.value.snapshot["baseline_swap_mib"] == 900
        assert caught.value.snapshot["peak_swap_growth_mib"] == 260
    asyncio.run(run())
    assert budget.baseline_swap_mib == 1000 and budget.peak_swap_growth_mib == 160
    assert shared[2]()["blocked_reason"] == "visual_resource_epoch_swap_growth_above_limit"
    assert budget.status()["shared_epoch"]["snapshot"]["epoch_id"] == "fixture-epoch"


def test_restart_local_swap_delta_cannot_override_healthy_shared_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    monkeypatch.setenv("LUMINA_VISUAL_RESOURCE_EPOCH", "1")
    path = tmp_path / "restart-local-epoch.json"
    epoch.initialize_epoch(5816.25, epoch_id="retained-exhibit", path=path)
    real_record, real_read = epoch.record_resources, epoch.read_epoch
    monkeypatch.setattr(epoch, "record_resources",
                        lambda free, swap, **kwargs: real_record(free, swap, path=path, **kwargs))
    monkeypatch.setattr(epoch, "read_epoch", lambda **kwargs: real_read(path=path, **kwargs))
    readings = iter([(50, 3934.81), (23, 4304.44)])
    budget = VisualResourceBudget(lambda: next(readings))
    async def run():
        await budget.check()
        await budget.check()
    asyncio.run(run())
    status = budget.status()
    assert status["baseline_swap_mib"] == 3934.81
    assert status["peak_swap_growth_mib"] == pytest.approx(369.63)
    assert status["peak_swap_growth_mib"] > status["maximum_swap_growth_mib"]
    assert status["blocked_reason"] is None
    assert status["enforcement_revision"] == "shared_epoch_authoritative_local_telemetry_v2"
    assert status["enforcement_scope"] == "shared_exhibition_epoch"
    assert status["local_lifetime_measurement_only"] is True
    authoritative = real_read(path=path)
    assert authoritative["baseline_swap_mib"] == 5816.25
    assert authoritative["peak_swap_growth_mib"] == 0
    assert authoritative["last_free_percent"] == 23
    assert authoritative["blocked_reason"] is None


def test_shared_epoch_does_not_weaken_current_preload_free_gate(shared):
    budget = VisualResourceBudget(lambda: (39, 500))
    with pytest.raises(VisualResourceError, match="visual_resource_preload_memory_below_limit"):
        asyncio.run(budget.check(minimum_free_percent=40))
    state = shared[2]()
    assert state["last_free_percent"] == 39 and state["blocked_reason"] is None
    assert budget.blocked_reason == "visual_resource_preload_memory_below_limit"


def test_cached_status_never_reads_journal_or_exposes_notes(shared, monkeypatch):
    budget = VisualResourceBudget(lambda: (50, 1000))
    asyncio.run(budget.check())
    def forbidden(**kwargs):
        pytest.fail("status attempted journal I/O")
    monkeypatch.setattr(epoch, "read_epoch", forbidden)
    monkeypatch.setattr(epoch, "record_resources", forbidden)
    result = budget.status()["shared_epoch"]
    assert result["enabled"] is True and result["cached_only"] is True
    assert result["last_observed_unix"] > 0 and result["error"] is None
    assert "PRIVATE_NOT_FOR_STATUS" not in repr(result)
    result["snapshot"]["peak_swap_growth_mib"] = 99999
    assert budget.status()["shared_epoch"]["snapshot"]["peak_swap_growth_mib"] == 100


def test_shared_latch_survives_fresh_next_budget_and_healthy_engine(shared, tmp_path):
    with pytest.raises(epoch.EpochError):
        shared[1](21, 1000, source="engine_fixture")
    fresh = VisualResourceBudget(lambda: (99, 1000))
    async def run():
        with pytest.raises(VisualResourceError, match="epoch_"):
            await fresh.check()
        with pytest.raises(phase.PhaseLeaseError, match="epoch_"):
            async with phase.inference_lease(path=tmp_path / "phase.lock"):
                pytest.fail("healthy older engine hid shared latch")
    asyncio.run(run())
    assert fresh.shared_epoch.snapshot["minimum_observed_free_percent"] == 21


def test_missing_epoch_never_initializes_or_admits_phase(shared, monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    def absent(*args, **kwargs):
        raise epoch.EpochError("visual_resource_epoch_missing")
    monkeypatch.setattr(epoch, "record_resources", absent)
    monkeypatch.setattr(epoch, "read_epoch", absent)
    async def run():
        with pytest.raises(phase.PhaseLeaseError, match="epoch_missing") as caught:
            async with phase.inference_lease(path=tmp_path / "phase.lock"):
                pytest.fail("missing epoch admitted inference")
        assert caught.value.snapshot is None
    asyncio.run(run())
    assert not missing.exists() and not (tmp_path / "missing.json.lock").exists()


def test_epoch_identity_change_fails_without_replacing_cached_identity(shared, monkeypatch):
    tracker = SharedResourceEpoch(source="next_visual")
    asyncio.run(tracker.record(50, 1000))
    previous = copy.deepcopy(tracker.snapshot)
    def changed(*args, **kwargs):
        assert kwargs["expected_epoch_id"] == "fixture-epoch"
        return dict(previous, epoch_id="replacement-epoch", revision=100)
    monkeypatch.setattr(epoch, "record_resources", changed)
    with pytest.raises(VisualResourceError, match="epoch_epoch_changed"):
        asyncio.run(tracker.record(50, 1000))
    assert tracker.snapshot == previous and tracker.epoch_id == "fixture-epoch"


def test_older_observation_cannot_lower_peak_or_clear_first_latch(shared):
    tracker = SharedResourceEpoch(source="next_visual")
    early = shared[2]()
    tracker._adopt(shared[1](30, 1100, source="one"))
    tracker._adopt(early)
    assert tracker.snapshot["peak_swap_growth_mib"] == 200
    assert tracker.snapshot["minimum_observed_free_percent"] == 30
    assert tracker.snapshot["revision"] == 1
    with pytest.raises(epoch.EpochError):
        shared[1](21, 1100, source="two")
    tracker._adopt(shared[2]())
    tracker._adopt(early)
    assert tracker.snapshot["blocked_reason"] == "visual_resource_epoch_free_below_limit"
    assert tracker.snapshot["minimum_observed_free_percent"] == 21


def test_uncommitted_error_snapshot_is_replaced_by_authoritative_readback(shared, monkeypatch):
    initial = shared[2]()
    candidate = dict(initial, revision=99, peak_swap_growth_mib=999, blocked_reason="candidate_only")
    def failed_write(*args, **kwargs):
        raise epoch.EpochError("visual_resource_epoch_io", snapshot=candidate)
    monkeypatch.setattr(epoch, "record_resources", failed_write)
    tracker = SharedResourceEpoch(source="next_visual")
    with pytest.raises(VisualResourceError, match="epoch_io") as caught:
        asyncio.run(tracker.record(50, 1000))
    assert caught.value.snapshot["revision"] == 0
    assert caught.value.snapshot["blocked_reason"] is None
    assert tracker.status()["error"] == "visual_resource_epoch_io"


@pytest.mark.parametrize("reading", [(21, 1000), (50, 1160), (None, None)])
def test_generic_phase_postcheck_suppresses_result_after_trip(shared, monkeypatch, tmp_path, reading):
    current = {"reading": (50, 1000)}
    monkeypatch.setattr(phase, "read_visual_resources", lambda: current["reading"])
    path = tmp_path / "phase.lock"
    async def produce():
        async with phase.inference_lease(path=path):
            current["reading"] = reading
            return "must not escape after postphase failure"
    with pytest.raises(phase.PhaseLeaseError, match="visual_resource_epoch_"):
        asyncio.run(produce())
    assert not lock_is_held(path)
    state = shared[2]()
    assert state["last_source"] == "next_phase" and state["blocked_reason"] is not None


def test_generic_phase_body_error_is_preserved_when_postcheck_safe(shared, tmp_path):
    async def run():
        with pytest.raises(OSError, match="original body error"):
            async with phase.inference_lease(path=tmp_path / "phase.lock"):
                raise OSError("original body error")
    asyncio.run(run())
    assert shared[2]()["revision"] == 2


@pytest.mark.parametrize("blocked_call", [1, 2])
def test_cancelled_phase_drains_actual_writer_before_unlock(shared, monkeypatch, tmp_path, blocked_call):
    real_record = shared[1]
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(free, swap, **kwargs):
        calls.append(kwargs["source"])
        if len(calls) == blocked_call:
            entered.set()
            assert release.wait(2), "test did not release journal writer"
        return real_record(free, swap, **kwargs)
    monkeypatch.setattr(epoch, "record_resources", blocked)
    path = tmp_path / "phase.lock"
    body = []
    async def operation():
        async with phase.inference_lease(path=path):
            body.append(True)
    async def run():
        task = asyncio.create_task(operation())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.005)
        task.cancel()
        await asyncio.sleep(.005)
        assert not task.done() and lock_is_held(path)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lock_is_held(path)
    asyncio.run(run())
    assert body == ([] if blocked_call == 1 else [True])
    assert shared[2]()["revision"] == blocked_call


def test_cancelled_queued_check_still_records_latch_before_return(shared, monkeypatch):
    real_record = shared[1]
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(free, swap, **kwargs):
        calls.append(swap)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return real_record(free, swap, **kwargs)
    monkeypatch.setattr(epoch, "record_resources", blocked)
    tracker = SharedResourceEpoch(source="next_visual")
    async def run():
        first = asyncio.create_task(tracker.record(50, 1000))
        assert await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(tracker.record(50, 1160))
        await asyncio.sleep(.005)
        second.cancel()
        await asyncio.sleep(.005)
        second.cancel()
        assert not second.done() and calls == [1000]
        release.set()
        await first
        with pytest.raises(asyncio.CancelledError):
            await second
        assert tracker.error is not None and tracker.snapshot["peak_swap_growth_mib"] == 260
    asyncio.run(run())
    assert calls == [1000, 1160] and shared[2]()["blocked_reason"] is not None


def test_cancelled_budget_adopts_writer_failure_before_post_cancel_caller_can_exit(shared, monkeypatch):
    real_record = shared[1]
    entered, release = threading.Event(), threading.Event()
    def blocked(free, swap, **kwargs):
        entered.set()
        assert release.wait(2)
        return real_record(free, swap, **kwargs)
    monkeypatch.setattr(epoch, "record_resources", blocked)
    budget = VisualResourceBudget(lambda: (50, 1160))
    async def run():
        task = asyncio.create_task(budget.check())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.blocked_reason.startswith("visual_resource_epoch_")
        assert budget.shared_epoch.snapshot["peak_swap_growth_mib"] == 260
        with pytest.raises(VisualResourceError, match="epoch_"):
            await budget.check()
    asyncio.run(run())


@pytest.mark.parametrize("phase_flag,epoch_flag", [("0", "1"), ("1", "0")])
def test_disabled_epoch_has_no_extra_journal_or_sensor_io(shared, monkeypatch, phase_flag, epoch_flag):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", phase_flag)
    monkeypatch.setenv("LUMINA_VISUAL_RESOURCE_EPOCH", epoch_flag)
    def forbidden(*args, **kwargs):
        pytest.fail("disabled epoch performed extra I/O")
    monkeypatch.setattr(epoch, "record_resources", forbidden)
    monkeypatch.setattr(epoch, "read_epoch", forbidden)
    tracker = SharedResourceEpoch(source="next_visual")
    budget = VisualResourceBudget(lambda: (50, 1000))
    async def run():
        await tracker.record(reader=forbidden)
        await budget.check()
    asyncio.run(run())
    assert tracker.status()["enabled"] is False and tracker.snapshot is None
    assert budget.baseline_swap_mib == 1000 and budget.blocked_reason is None


@pytest.mark.parametrize("visual,phase_flag,expected", [("1", "1", "visual_job_including_speech"),
                                                        ("0", "1", "perception_only"),
                                                        ("1", "0", "perception_only")])
def test_loaded_monitor_status_attestation_does_not_read_resources(shared, monkeypatch, tmp_path, visual, phase_flag, expected):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", visual)
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", phase_flag)
    def forbidden(*args, **kwargs):
        pytest.fail("status performed resource or epoch I/O")
    monkeypatch.setattr(epoch, "record_resources", forbidden)
    monkeypatch.setattr(epoch, "read_epoch", forbidden)
    monkeypatch.setattr(navigation, "phase_status", lambda: {"enabled": phase_flag == "1"})
    service = navigation.VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    service.resource_budget = VisualResourceBudget(forbidden)
    state = service.status()
    assert state["resource_monitor_scope"] == expected
    assert state["resource_budget"]["shared_epoch"]["snapshot"] is None
    assert state["resource_budget"]["shared_epoch"]["last_observed_unix"] is None


def engine_health_fixture(shared, monkeypatch, path, change=None):
    state = shared[2]()
    attestation = {"enabled": True, "error": None, "observed_unix": time.time(), "state": state}
    payload = {"status": "ok", "runtime": {"loaded": False, "loading": False, "unloading": False, "unload_error": None},
               "phase_serialization": {"enabled": True, "lock_path": str(path), "blocked_reason": None,
                    "active_workers": 0, "waiting_workers": 2, "idle_residency_policy": "unload_after_worker",
                    "shared_epoch": attestation}}
    if change is not None:
        change(payload, attestation, state)
    calls = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            assert lock_is_held(path)
        async def get(self, url):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), json=payload)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(phase, "_check_tts_health", REAL_HEALTH_GATE)
    return calls


@pytest.mark.parametrize("change", [
    "missing", "disabled", "error", "missing_error", "missing_time", "boolean_time",
    "unknown_state", "wrong_scope", "missing_id", "latched", "unknown_baseline",
    "unknown_peak", "peak_over_limit", "wrong_minimum", "wrong_maximum", "free_below_limit",
])
def test_mixed_or_unknown_engine_epoch_never_admits_model(shared, monkeypatch, tmp_path, change):
    path = tmp_path / "phase.lock"
    def alter(payload, attestation, state):
        if change == "missing": payload["phase_serialization"].pop("shared_epoch")
        elif change == "disabled": attestation["enabled"] = False
        elif change == "error": attestation["error"] = "private failure detail"
        elif change == "missing_error": attestation.pop("error")
        elif change == "missing_time": attestation.pop("observed_unix")
        elif change == "boolean_time": attestation["observed_unix"] = True
        elif change == "unknown_state": attestation["state"] = None
        elif change == "wrong_scope": state["baseline_scope"] = "engine_lifetime_first_serialized_worker"
        elif change == "missing_id": state.pop("epoch_id")
        elif change == "latched": state["blocked_reason"] = "some_engine_latch"
        elif change == "unknown_baseline": state["baseline_swap_mib"] = None
        elif change == "unknown_peak": state["peak_swap_growth_mib"] = None
        elif change == "peak_over_limit": state["peak_swap_growth_mib"] = 257
        elif change == "wrong_minimum": state["minimum_free_percent"] = 21
        elif change == "wrong_maximum": state["maximum_swap_growth_mib"] = 512
        elif change == "free_below_limit": state["minimum_observed_free_percent"] = 21
    calls = engine_health_fixture(shared, monkeypatch, path, alter)
    async def run():
        with pytest.raises(phase.PhaseLeaseError, match="visual_heavy_phase_tts_epoch_") as caught:
            async with phase.inference_lease(path=path):
                pytest.fail("mixed engine admitted model")
        assert "private" not in str(caught.value)
    asyncio.run(run())
    assert len(calls) == 1 and not lock_is_held(path)


@pytest.mark.parametrize("field,value", [("epoch_id", "different-epoch"),
                                        ("baseline_swap_mib", 1200), ("baseline_swap_mib", 800),
                                        ("peak_swap_growth_mib", 200)])
def test_engine_epoch_must_match_actual_record_not_cached_permission(shared, monkeypatch, tmp_path, field, value):
    path = tmp_path / "phase.lock"
    calls = engine_health_fixture(shared, monkeypatch, path, lambda payload, attestation, state: state.update({field: value}))
    async def run():
        with pytest.raises(phase.PhaseLeaseError, match="visual_heavy_phase_tts_epoch_mismatch"):
            async with phase.inference_lease(path=path):
                pytest.fail("mismatched shared baseline admitted model")
    asyncio.run(run())
    assert len(calls) == 1
    assert shared[2]()["revision"] == 1  # Real authoritative record preceded comparison.
    assert phase.SHARED_EPOCH.snapshot["baseline_swap_mib"] == 900


def test_matching_engine_with_waiting_workers_is_accepted_without_extra_http(shared, monkeypatch, tmp_path):
    path = tmp_path / "phase.lock"
    calls = engine_health_fixture(shared, monkeypatch, path)
    async def run():
        async with phase.inference_lease(path=path):
            assert lock_is_held(path)
    asyncio.run(run())
    assert len(calls) == 1 and shared[2]()["revision"] == 2


def test_epoch_flag_off_keeps_legacy_engine_health_compatible(shared, monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_VISUAL_RESOURCE_EPOCH", "0")
    path = tmp_path / "phase.lock"
    calls = engine_health_fixture(shared, monkeypatch, path,
        lambda payload, attestation, state: payload["phase_serialization"].pop("shared_epoch"))
    async def run():
        async with phase.inference_lease(path=path):
            pass
    asyncio.run(run())
    assert len(calls) == 1 and shared[2]()["revision"] == 0
