"""Opt-in cross-process lease for Next inference and the Irodori worker.

This is an advisory lock among the product's cooperating callers, not protection
against unrelated direct requests to llama.cpp. Never unlink or replace its inode.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
import fcntl
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

import httpx
from .visual_resource_budget import SharedResourceEpoch, VisualResourceError, read_visual_resources, shared_epoch_enabled

LOCK_PATH = Path(__file__).resolve().parents[1] / "logs/visual_heavy_phase.lock"
TTS_HEALTH_URL = "http://127.0.0.1:5088/health"
SHARED_EPOCH = SharedResourceEpoch(source="next_phase")
PHASE_STATUS_REVISION = "offloop_singleflight_phase_status_v1"
STATUS_PROBE_TIMEOUT_SECONDS = 0.25


class PhaseLeaseError(RuntimeError):
    pass


async def _check_shared_epoch() -> None:
    try:
        await SHARED_EPOCH.record(reader=read_visual_resources)
    except VisualResourceError as exc:
        error = PhaseLeaseError(str(exc))
        error.snapshot = exc.snapshot
        raise error from exc


def enabled() -> bool:
    return os.environ.get("LUMINA_VISUAL_PHASE_SERIALIZATION", "0") == "1"


def _engine_epoch_state(shared: object) -> dict:
    """Whitelist the engine's shared contract; never trust a legacy local budget."""
    if (not isinstance(shared, dict) or shared.get("enabled") is not True
        or "error" not in shared or type(shared.get("observed_unix")) not in (int, float)
        or not math.isfinite(shared["observed_unix"]) or shared["observed_unix"] <= 0):
        raise PhaseLeaseError("visual_heavy_phase_tts_epoch_contract_mismatch")
    if shared["error"] is not None:
        raise PhaseLeaseError("visual_heavy_phase_tts_epoch_unavailable")
    state = shared.get("state")
    if (not isinstance(state, dict) or state.get("baseline_scope") != "shared_exhibition_epoch"
        or not isinstance(state.get("epoch_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", state["epoch_id"])
        or type(state.get("minimum_free_percent")) is not int or state["minimum_free_percent"] != 22
        or type(state.get("maximum_swap_growth_mib")) is not int or state["maximum_swap_growth_mib"] != 256
        or "blocked_reason" not in state):
        raise PhaseLeaseError("visual_heavy_phase_tts_epoch_contract_mismatch")
    if state["blocked_reason"] is not None:
        raise PhaseLeaseError("visual_heavy_phase_tts_epoch_latched")
    for key in ("baseline_swap_mib", "peak_swap_growth_mib"):
        if type(state.get(key)) not in (int, float) or not math.isfinite(state[key]) or state[key] < 0:
            raise PhaseLeaseError("visual_heavy_phase_tts_epoch_contract_mismatch")
    if state["peak_swap_growth_mib"] > 256:
        raise PhaseLeaseError("visual_heavy_phase_tts_epoch_latched")
    for key in ("last_free_percent", "minimum_observed_free_percent"):
        if key not in state or (state[key] is not None and (type(state[key]) is not int or not 22 <= state[key] <= 100)):
            raise PhaseLeaseError("visual_heavy_phase_tts_epoch_contract_mismatch")
    return {key: state[key] for key in ("epoch_id", "baseline_scope", "baseline_swap_mib",
            "peak_swap_growth_mib", "minimum_free_percent", "maximum_swap_growth_mib")}


async def _check_tts_health(path: Path) -> dict | None:
    """Read the existing engine's latch under the exclusive phase lock.

    Health does not initialize synthesis. Never redirect or retain its raw
    body: only stable failure codes cross the provider/route boundary.
    """
    try:
        async with asyncio.timeout(3.0):
            async with httpx.AsyncClient(timeout=2.0, trust_env=False, follow_redirects=False) as client:
                response = await client.get(TTS_HEALTH_URL)
                response.raise_for_status()
                payload = response.json()
    except (httpx.HTTPError, TimeoutError, OSError, ValueError):
        raise PhaseLeaseError("visual_heavy_phase_tts_health_unavailable") from None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise PhaseLeaseError("visual_heavy_phase_tts_state_unknown")
    engine_phase = payload.get("phase_serialization")
    if not isinstance(engine_phase, dict) or "blocked_reason" not in engine_phase:
        raise PhaseLeaseError("visual_heavy_phase_tts_state_unknown")
    if engine_phase.get("enabled") is not True or engine_phase.get("lock_path") != str(path):
        raise PhaseLeaseError("visual_heavy_phase_tts_contract_mismatch")
    if type(engine_phase.get("active_workers")) is not int:
        raise PhaseLeaseError("visual_heavy_phase_tts_state_unknown")
    if engine_phase["blocked_reason"] is not None:
        raise PhaseLeaseError("visual_heavy_phase_tts_latched")
    if engine_phase["active_workers"] != 0:
        raise PhaseLeaseError("visual_heavy_phase_tts_active")
    if engine_phase.get("idle_residency_policy") != "unload_after_worker":
        raise PhaseLeaseError("visual_heavy_phase_tts_contract_mismatch")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or any(
        type(runtime.get(field)) is not bool for field in ("loaded", "loading", "unloading")
    ):
        raise PhaseLeaseError("visual_heavy_phase_tts_state_unknown")
    if "unload_error" not in runtime:
        raise PhaseLeaseError("visual_heavy_phase_tts_state_unknown")
    if runtime["unload_error"] is not None:
        raise PhaseLeaseError("visual_heavy_phase_tts_cleanup_failed")
    if any(runtime[field] for field in ("loaded", "loading", "unloading")):
        raise PhaseLeaseError("visual_heavy_phase_tts_resident")
    # An idle worker is not enough: the previous native worker must have
    # actually dropped its model residency before this process wakes Qwen.
    # The exclusive lease prevents a cooperating queued worker from loading
    # the voice runtime after this read and before our inference completes.
    # Waiting workers may be queued on our flock; requiring an empty queue
    # would deadlock normal handover. Only zero active workers is mandatory.
    if shared_epoch_enabled():
        return _engine_epoch_state(engine_phase.get("shared_epoch"))
    return None


@asynccontextmanager
async def inference_lease(*, path: Path | None = None, timeout: float = 70.0):
    if not enabled():
        yield
        return
    path = path or LOCK_PATH
    fd = None
    locked = False
    try:
        # The existing runtime logs directory is created by the launcher. Do
        # not create a replacement directory or follow an unexpected symlink.
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PhaseLeaseError("visual_heavy_phase_unavailable")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PhaseLeaseError("visual_heavy_phase_busy")
                    await asyncio.sleep(.05)
        except OSError as exc:
            raise PhaseLeaseError("visual_heavy_phase_unavailable") from exc
        engine_epoch = await _check_tts_health(path)
        await _check_shared_epoch()
        if shared_epoch_enabled():
            current = SHARED_EPOCH.snapshot
            if (not isinstance(engine_epoch, dict) or not isinstance(current, dict)
                or any(engine_epoch.get(key) != current.get(key) for key in (
                    "epoch_id", "baseline_scope", "baseline_swap_mib", "minimum_free_percent", "maximum_swap_growth_mib"))
                or engine_epoch["peak_swap_growth_mib"] > current["peak_swap_growth_mib"]):
                raise PhaseLeaseError("visual_heavy_phase_tts_epoch_mismatch")
        try:
            yield
        finally:
            # Runs after provider HTTP cleanup, still under EX ownership. A
            # postphase trip suppresses a successful result; its durable writer
            # cannot be abandoned by cancellation before flock is released.
            await _check_shared_epoch()
    finally:
        if fd is not None:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _lock_status(path: Path) -> dict:
    """Read-only file work; no shared epoch or mutable job state is read here."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {"busy": None, "error": "phase_lease_unavailable"}
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"busy": True}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"busy": False}
    except FileNotFoundError:
        return {"busy": False, "initialized": False}
    except OSError:
        return {"busy": None, "error": "phase_lease_unavailable"}
    finally:
        if fd is not None:
            os.close(fd)


def status(*, path: Path | None = None) -> dict:
    """Legacy synchronous contract; HTTP routes use async_status instead."""
    if not enabled():
        return {"enabled": False, "busy": False}
    path = path or LOCK_PATH
    return {"enabled": True, "lock_path": str(path), "shared_epoch": SHARED_EPOCH.status(),
            **_lock_status(path)}


class _StatusProbe:
    """One file probe across callers/event loops; abandoned results are not reused.

    A timeout cannot abort a filesystem syscall. Keep its Future until the
    worker completes, and never queue replacement workers behind a hung one.
    This read-only worker never owns or releases the inference EX lease.
    """
    def __init__(self):
        self._mutex = threading.Lock()
        self._pending = None

    def _flight(self, path: Path, timeout: float) -> dict:
        with self._mutex:
            if self._pending is not None and not self._pending["future"].done():
                return self._pending
            # Even a successful old observation must not become a new sample.
            future = Future()
            flight = {"future": future, "path": path, "started_unix": time.time(),
                      "deadline": time.monotonic() + timeout}
            self._pending = flight

            def read():
                try:
                    result = _lock_status(path)
                except Exception:
                    result = {"busy": None, "error": "phase_lease_unavailable"}
                future.set_result((result, time.time(), time.monotonic()))

            try:
                threading.Thread(target=read, name="lumina-phase-status", daemon=True).start()
            except (OSError, RuntimeError):
                future.set_result(({"busy": None, "error": "phase_lease_unavailable"},
                                   time.time(), time.monotonic()))
            return flight

    async def sample(self, path: Path, timeout: float) -> dict:
        flight = self._flight(path, timeout)
        future = flight["future"]
        metadata = {"probe_revision": PHASE_STATUS_REVISION,
                    "probe_started_unix": flight["started_unix"], "probe_observed_unix": None}

        def unknown(reason: str) -> dict:
            return {**metadata, "busy": None, "error": reason, "probe_pending": not future.done()}

        if flight["path"] != path:
            return unknown("phase_status_probe_pending")
        remaining = flight["deadline"] - time.monotonic()
        if remaining <= 0:
            return unknown("phase_status_probe_timeout")
        try:
            # Shield a loop-local wrapper, never cancel the cross-loop Future.
            # Concurrent callers share the first probe's remaining budget.
            result, observed, finished = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)), timeout=remaining)
        except TimeoutError:
            return unknown("phase_status_probe_timeout")
        # Cancellation propagates to this HTTP caller but not the worker. The
        # next poll can only join its original deadline, never extend it.
        if finished > flight["deadline"] or time.monotonic() > flight["deadline"]:
            return unknown("phase_status_probe_timeout")
        return {**metadata, **result, "probe_observed_unix": observed, "probe_pending": False}


_STATUS_PROBE = _StatusProbe()


async def async_status(*, path: Path | None = None,
                       timeout: float = STATUS_PROBE_TIMEOUT_SECONDS) -> dict:
    """Fresh bounded off-loop file probe, with cached epoch state read on-loop."""
    if not enabled():
        return {"enabled": False, "busy": False, "probe_revision": PHASE_STATUS_REVISION,
                "probe_pending": False, "probe_started_unix": None, "probe_observed_unix": None}
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= STATUS_PROBE_TIMEOUT_SECONDS:
        raise ValueError("invalid phase status timeout")
    path = path or LOCK_PATH
    probe = await _STATUS_PROBE.sample(path, timeout)
    # No mutable epoch or navigation state is accessed from the file worker.
    return {"enabled": True, "lock_path": str(path), "shared_epoch": SHARED_EPOCH.status(), **probe}
