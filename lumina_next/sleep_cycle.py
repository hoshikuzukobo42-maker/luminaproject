from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from .cognitive_state import CognitiveController
from .config import RuntimeConfig


class LuminaSleepCycle:
    """Runs local memory maintenance without training or changing model weights."""

    def __init__(
        self,
        memory: Any,
        cognition: CognitiveController,
        config: RuntimeConfig,
    ) -> None:
        self.memory = memory
        self.cognition = cognition
        self.config = config
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if not self.config.sleep_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._worker(), name="lumina-sleep-cycle")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _worker(self) -> None:
        while True:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(self.config.sleep_check_interval_seconds)

    async def tick(self) -> None:
        state = self.cognition.tick()
        if state["cognitive_state"] != "AWAKE":
            if self.cognition.should_wake():
                self.cognition.wake("circadian_wake")
            return
        due, trigger = self.cognition.deep_sleep_due()
        if due:
            await self.run_once(trigger=trigger, mode="deep", remain_asleep=True)
            return
        if (
            hasattr(self.memory, "pending_event_count")
            and self.memory.pending_event_count() >= self.config.micro_sleep_event_threshold
        ):
            await self.run_once(
                trigger="event_pressure",
                mode="micro",
                remain_asleep=False,
            )

    async def run_once(
        self,
        *,
        trigger: str,
        mode: str = "deep",
        remain_asleep: bool | None = None,
    ) -> dict[str, Any]:
        normalized_mode = "deep" if mode == "deep" else "micro"
        if remain_asleep is None:
            remain_asleep = normalized_mode == "deep"
        if not hasattr(self.memory, "consolidate_memory"):
            return {"ok": False, "status": "memory_backend_unsupported"}
        async with self._run_lock:
            if normalized_mode == "deep":
                today = datetime.now().date().isoformat()
                current_state = self.cognition.snapshot()
                if current_state.get("last_deep_sleep_date") == today:
                    result = {
                        "ok": True,
                        "status": "already_completed",
                        "trigger": trigger,
                        "mode": normalized_mode,
                        "date": today,
                    }
                    self._last_result = result
                    return result
            self.cognition.begin_sleep(trigger, normalized_mode)
            result = await asyncio.to_thread(
                self.memory.consolidate_memory,
                trigger=trigger,
                mode=normalized_mode,
                max_events=self.config.sleep_max_events,
            )
            self.cognition.finish_consolidation(
                result,
                mode=normalized_mode,
                remain_asleep=bool(remain_asleep),
            )
            self._last_result = result
            return result

    def status(self) -> dict[str, Any]:
        memory_status = (
            self.memory.sleep_status()
            if hasattr(self.memory, "sleep_status")
            else {"pending_events": None, "recent_runs": []}
        )
        return {
            "enabled": self.config.sleep_enabled,
            "scheduler_running": bool(self._task is not None and not self._task.done()),
            "run_active": self._run_lock.locked(),
            "check_interval_seconds": self.config.sleep_check_interval_seconds,
            "sleep_window": {
                "start_hour": self.config.sleep_start_hour,
                "wake_hour": self.config.sleep_wake_hour,
            },
            "last_result": self._last_result,
            "last_error": self._last_error,
            **memory_status,
        }
