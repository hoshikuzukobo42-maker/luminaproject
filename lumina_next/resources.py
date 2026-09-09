from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable


_FREE_PERCENT_RE = re.compile(r"memory free percentage:\s*(\d+)%", re.IGNORECASE)
VALID_STATES = {"idle", "listening", "thinking", "speaking", "maintenance"}


def read_memory_free_percent() -> int | None:
    try:
        result = subprocess.run(
            ["memory_pressure", "-Q"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _FREE_PERCENT_RE.search(result.stdout)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ResourcePolicy:
    level: str
    context_size: int
    memory_results: int
    vector_search: bool
    maintenance_allowed: bool
    avatar_fps: int
    tts_lookahead: int


class ResourceScheduler:
    """Small deterministic state machine for M1 unified-memory time sharing."""

    def __init__(
        self,
        configured_context_size: int,
        warning_percent: int = 25,
        critical_percent: int = 12,
        memory_reader: Callable[[], int | None] = read_memory_free_percent,
    ) -> None:
        if not 0 <= critical_percent < warning_percent <= 100:
            raise ValueError("resource thresholds must satisfy 0 <= critical < warning <= 100")
        self.configured_context_size = max(512, int(configured_context_size))
        self.warning_percent = int(warning_percent)
        self.critical_percent = int(critical_percent)
        self.memory_reader = memory_reader
        self.state = "idle"
        self.state_since = datetime.now().isoformat(timespec="seconds")
        self._state_lock = asyncio.Lock()

    async def set_state(self, state: str) -> None:
        normalized = str(state).strip().lower()
        if normalized not in VALID_STATES:
            raise ValueError(f"invalid resource state: {state}")
        async with self._state_lock:
            if normalized != self.state:
                self.state = normalized
                self.state_since = datetime.now().isoformat(timespec="seconds")

    def classify(self, free_percent: int | None) -> str:
        if free_percent is None:
            return "unknown"
        if free_percent < self.critical_percent:
            return "critical"
        if free_percent < self.warning_percent:
            return "warning"
        return "normal"

    def policy(self, free_percent: int | None) -> ResourcePolicy:
        level = self.classify(free_percent)
        if level == "critical":
            return ResourcePolicy(
                level=level,
                context_size=min(self.configured_context_size, 4096),
                memory_results=2,
                vector_search=False,
                maintenance_allowed=False,
                avatar_fps=0,
                tts_lookahead=0,
            )
        if level in {"warning", "unknown"}:
            return ResourcePolicy(
                level=level,
                context_size=min(self.configured_context_size, 4096),
                memory_results=4,
                vector_search=False,
                maintenance_allowed=False,
                avatar_fps=15,
                tts_lookahead=0,
            )
        return ResourcePolicy(
            level=level,
            context_size=self.configured_context_size,
            memory_results=8,
            vector_search=True,
            maintenance_allowed=self.state == "idle",
            avatar_fps=30,
            tts_lookahead=1,
        )

    async def snapshot(self) -> dict:
        free_percent = await asyncio.to_thread(self.memory_reader)
        policy = self.policy(free_percent)
        return {
            "state": self.state,
            "state_since": self.state_since,
            "memory_free_percent": free_percent,
            "pressure": policy.level,
            "thresholds": {
                "warning_below_percent": self.warning_percent,
                "critical_below_percent": self.critical_percent,
            },
            "policy": {
                "context_size": policy.context_size,
                "memory_results": policy.memory_results,
                "vector_search": policy.vector_search,
                "maintenance_allowed": policy.maintenance_allowed,
                "avatar_fps": policy.avatar_fps,
                "tts_lookahead": policy.tts_lookahead,
            },
        }
