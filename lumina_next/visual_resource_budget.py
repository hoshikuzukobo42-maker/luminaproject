"""Fail-closed resource budget for opt-in local visual inference only.

The local swap baseline is retained for service-lifetime diagnostics.  When the
opt-in shared exhibition epoch is active, its persistent cross-process baseline
is the swap-growth authority; otherwise the legacy local baseline is enforced.
System-wide readings are safety bounds, not process attribution.
"""
from __future__ import annotations

import asyncio
import copy
import math
import os
import re
import subprocess
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class VisualResourceError(RuntimeError):
    def __init__(self, message: str, *, snapshot: dict | None = None):
        super().__init__(message)
        self.snapshot = snapshot


def shared_epoch_enabled() -> bool:
    return (os.environ.get("LUMINA_VISUAL_PHASE_SERIALIZATION") == "1"
            and os.environ.get("LUMINA_VISUAL_RESOURCE_EPOCH") == "1")


class SharedResourceEpoch:
    """Cached observations only in status; all durable work is tracked/drained."""

    def __init__(self, *, source: str):
        self.source = source
        self.epoch_id: str | None = None
        self.snapshot: dict | None = None
        self.error: str | None = None
        self.last_observed_unix: float | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        return {"enabled": shared_epoch_enabled(), "error": self.error,
                "last_observed_unix": self.last_observed_unix,
                "snapshot": copy.deepcopy(self.snapshot), "cached_only": True}

    def _adopt(self, state: dict) -> None:
        if (not isinstance(state, dict) or state.get("schema") != "lumina-resource-epoch-v1"
            or state.get("baseline_scope") != "shared_exhibition_epoch"
            or not isinstance(state.get("epoch_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", state["epoch_id"])
            or type(state.get("revision")) is not int or state["revision"] < 0
            or type(state.get("minimum_free_percent")) is not int or state["minimum_free_percent"] != 22
            or type(state.get("maximum_swap_growth_mib")) is not int or state["maximum_swap_growth_mib"] != 256
            or "blocked_reason" not in state):
            raise VisualResourceError("visual_resource_epoch_invalid_snapshot")
        for key in ("baseline_swap_mib", "peak_swap_growth_mib", "updated_unix"):
            if type(state.get(key)) not in (int, float) or not math.isfinite(state[key]) or state[key] < 0:
                raise VisualResourceError("visual_resource_epoch_invalid_snapshot")
        if self.epoch_id is not None and state["epoch_id"] != self.epoch_id:
            raise VisualResourceError("visual_resource_epoch_epoch_changed")
        previous = self.snapshot
        if previous is not None and state["baseline_swap_mib"] != previous["baseline_swap_mib"]:
            raise VisualResourceError("visual_resource_epoch_baseline_changed")
        if previous is not None and any(state.get(key) != previous.get(key) for key in ("created_unix", "lock_identity")):
            raise VisualResourceError("visual_resource_epoch_epoch_changed")
        # Do not expose arbitrary source notes or accept an older completion as
        # permission to lower a peak, clear a first latch, or replace identity.
        keys = ("schema", "epoch_id", "baseline_scope", "baseline_swap_mib", "peak_swap_growth_mib",
                "minimum_free_percent", "maximum_swap_growth_mib", "minimum_observed_free_percent",
                "last_free_percent", "last_swap_mib", "last_sample_valid", "last_source",
                "blocked_reason", "created_unix", "updated_unix", "revision", "lock_identity")
        current = {key: copy.deepcopy(state[key]) for key in keys if key in state}
        if previous is not None:
            if current["revision"] < previous["revision"]:
                current = copy.deepcopy(previous)
            current["peak_swap_growth_mib"] = max(previous["peak_swap_growth_mib"], state["peak_swap_growth_mib"])
            minima = [value for value in (previous.get("minimum_observed_free_percent"),
                                         state.get("minimum_observed_free_percent")) if value is not None]
            current["minimum_observed_free_percent"] = min(minima) if minima else None
            current["blocked_reason"] = previous["blocked_reason"] or state["blocked_reason"]
        self.epoch_id, self.snapshot = state["epoch_id"], current
        self.last_observed_unix = time.time()

    async def record(self, free=None, swap=None, *, reader: Callable | None = None) -> None:
        if not shared_epoch_enabled():
            return

        async def serialized() -> None:
            async with self._lock:
                def write():
                    # Import failure must fail closed, never initialize an epoch.
                    from services import lumina_resource_epoch as epoch
                    sampled_free, sampled_swap = free, swap
                    if reader is not None:
                        try:
                            sampled_free, sampled_swap = reader()
                        except Exception:
                            sampled_free, sampled_swap = None, None
                    try:
                        state = epoch.record_resources(sampled_free, sampled_swap, source=self.source,
                                                       expected_epoch_id=self.epoch_id)
                        return state, None
                    except epoch.EpochError as exc:
                        # A failed atomic write may carry an uncommitted candidate.
                        # Only a fresh read may attest that snapshot as durable.
                        try:
                            state = epoch.read_epoch(expected_epoch_id=self.epoch_id)
                        except Exception:
                            state = None
                        return state, str(exc)
                try:
                    state, error = await asyncio.to_thread(write)
                    if state is not None:
                        self._adopt(state)
                    if error is not None:
                        self.error = self.error or (error if re.fullmatch(r"visual_resource_epoch_[a-z0-9_]+", error)
                                                   else "visual_resource_epoch_unavailable")
                    elif state is not None and state.get("last_sample_valid") is not True:
                        self.error = self.error or "visual_resource_epoch_invalid_snapshot"
                    if self.snapshot is not None and self.snapshot["blocked_reason"] is not None:
                        self.error = self.error or "visual_resource_epoch_blocked"
                    if state is None and self.error is None:
                        self.error = "visual_resource_epoch_unavailable"
                except VisualResourceError as exc:
                    self.error = self.error or str(exc)
                except Exception:
                    self.error = self.error or "visual_resource_epoch_unavailable"
                if self.error:
                    raise VisualResourceError(self.error, snapshot=copy.deepcopy(self.snapshot))

        # Shield the queue wait as well as the thread: a cancelled lease owner
        # cannot abandon its write behind a concurrent wake/image/outer check.
        writer = asyncio.create_task(serialized())
        cancelled = False
        while not writer.done():
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not writer.cancelled():
                try:
                    writer.result()
                except Exception:
                    pass  # Cached error/authoritative snapshot already retained.
            raise asyncio.CancelledError
        writer.result()


def read_visual_resources() -> tuple[int | None, float | None]:
    try:
        pressure = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True,
                                  text=True, check=True, timeout=3).stdout
        swap = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], capture_output=True,
                              text=True, check=True, timeout=3).stdout
        free = re.search(r"memory free percentage:\s*(\d+)%", pressure, re.I)
        used = re.search(r"\bused\s*=\s*([\d.]+)([MG])\b", swap)
        return (int(free[1]) if free else None,
                float(used[1]) * (1024 if used[2] == "G" else 1) if used else None)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None


class VisualResourceBudget:
    minimum_free_percent = 22
    maximum_swap_growth_mib = 256
    enforcement_revision = "shared_epoch_authoritative_local_telemetry_v2"

    def __init__(self, reader: Callable = read_visual_resources, *, interval: float = 1.0):
        self.reader = reader
        self.interval = interval
        self.baseline_swap_mib: float | None = None
        self.last_free_percent: int | None = None
        self.peak_swap_growth_mib: float = 0
        self.blocked_reason: str | None = None
        self.shared_epoch = SharedResourceEpoch(source="next_visual")

    def status(self) -> dict:
        shared = shared_epoch_enabled()
        return {"minimum_free_percent": self.minimum_free_percent,
                "maximum_swap_growth_mib": self.maximum_swap_growth_mib,
                "baseline_scope": "service_lifetime_first_visual_request",
                "enforcement_revision": self.enforcement_revision,
                "enforcement_scope": ("shared_exhibition_epoch"
                                      if shared else "service_lifetime_first_visual_request"),
                "local_lifetime_measurement_only": shared,
                "baseline_swap_mib": self.baseline_swap_mib,
                "last_free_percent": self.last_free_percent,
                "peak_swap_growth_mib": self.peak_swap_growth_mib,
                "blocked_reason": self.blocked_reason,
                "measurement_scope": "system_wide_not_process_attribution",
                "shared_epoch": self.shared_epoch.status()}

    async def check(self, *, minimum_free_percent: int = 22) -> None:
        if self.blocked_reason:
            raise VisualResourceError(self.blocked_reason, snapshot=copy.deepcopy(self.shared_epoch.snapshot))
        shared = shared_epoch_enabled()
        required_free = max(self.minimum_free_percent, minimum_free_percent)
        try:
            free, swap = await asyncio.to_thread(self.reader)
        except Exception:
            # Preserve task cancellation (a BaseException), but treat sensor
            # failures or malformed results as unknown, never as permission.
            free, swap = None, None
        local_reason = None
        if (type(free) is not int or not 0 <= free <= 100 or type(swap) not in (int, float)
            or not math.isfinite(swap) or swap < 0):
            local_reason = "visual_resource_reading_unavailable"
        else:
            self.last_free_percent = free
            if self.baseline_swap_mib is None:
                self.baseline_swap_mib = swap
            self.peak_swap_growth_mib = max(self.peak_swap_growth_mib, swap - self.baseline_swap_mib)
            if free < required_free:
                local_reason = (
                    "visual_resource_preload_memory_below_limit" if required_free > self.minimum_free_percent
                    else "visual_resource_free_memory_below_limit")
            elif self.peak_swap_growth_mib > self.maximum_swap_growth_mib:
                local_reason = "visual_resource_swap_growth_above_limit"
        try:
            await self.shared_epoch.record(free, swap)
        except asyncio.CancelledError:
            self.blocked_reason = self.blocked_reason or self.shared_epoch.error
            raise
        except VisualResourceError as exc:
            self.blocked_reason = self.blocked_reason or str(exc)
        # With the opt-in persistent epoch, its fixed exhibition baseline is the
        # one swap-growth authority shared by Next and TTS.  A service restart
        # can place this local diagnostic baseline below the retained epoch and
        # must not invent a second, stricter delta latch.  The fresh sample is
        # still durably checked above: unavailable readings, free <22%, shared
        # growth >256MiB, journal/identity errors and an old latch all fail
        # closed.  The operation-specific cold-load free>=40% gate remains local
        # because it is deliberately stricter than the epoch's lifetime floor.
        if self.blocked_reason is None:
            if shared != shared_epoch_enabled():
                self.blocked_reason = "visual_resource_epoch_configuration_changed"
            elif not shared or local_reason == "visual_resource_preload_memory_below_limit":
                self.blocked_reason = local_reason
        if self.blocked_reason:
            raise VisualResourceError(self.blocked_reason, snapshot=copy.deepcopy(self.shared_epoch.snapshot))

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self.check()
        task = asyncio.create_task(operation())
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.interval)
                if done:
                    # A failed HTTP/model operation can also leave memory
                    # pressure behind. Latch that before its error path may
                    # attempt to synthesize an audible failure report.
                    await self.check()
                    return await task
                await self.check()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
