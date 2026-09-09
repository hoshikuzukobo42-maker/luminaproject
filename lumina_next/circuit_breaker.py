"""Circuit breaker with exponential backoff for local model process control."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Track consecutive failures and gate restart attempts."""

    failure_threshold: int = 3
    success_threshold: int = 1
    base_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 120.0
    max_crash_loops: int = 5
    history_max: int = 32

    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    crash_loops: int = 0
    opened_at: float | None = None
    next_attempt_at: float | None = None
    last_error: str | None = None
    _history: list[str] = field(default_factory=list)

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.last_error = None
        if self.state is CircuitState.HALF_OPEN and self.consecutive_successes >= self.success_threshold:
            self.state = CircuitState.CLOSED
            self.opened_at = None
            self.next_attempt_at = None
            self.crash_loops = 0

    def record_failure(self, error: str, *, now: float | None = None) -> float:
        timestamp = time.monotonic() if now is None else float(now)
        detail = str(error).strip()[:500] or "unknown failure"
        self.last_error = detail
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        self._history.append(detail)
        limit = max(1, int(self.history_max))
        if len(self._history) > limit:
            self._history = self._history[-limit:]

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = timestamp
            self.crash_loops += 1
            backoff = self._compute_backoff()
            self.next_attempt_at = timestamp + backoff
            return backoff

        if self.state is CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            backoff = self._compute_backoff()
            self.next_attempt_at = timestamp + backoff
            return backoff

        return 0.0

    def _compute_backoff(self) -> float:
        exponent = max(0, self.consecutive_failures - self.failure_threshold)
        delay = self.base_backoff_seconds * math.pow(2.0, exponent)
        return min(self.max_backoff_seconds, delay)

    def allow_attempt(self, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else float(now)
        if self.crash_loops >= self.max_crash_loops:
            return False
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.HALF_OPEN:
            return True
        if self.next_attempt_at is None:
            return False
        if timestamp >= self.next_attempt_at:
            self.state = CircuitState.HALF_OPEN
            self.consecutive_successes = 0
            return True
        return False

    def cooldown_remaining(self, *, now: float | None = None) -> float:
        timestamp = time.monotonic() if now is None else float(now)
        if self.next_attempt_at is None:
            return 0.0
        return max(0.0, self.next_attempt_at - timestamp)

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    def as_dict(self) -> dict[str, object]:
        tail = min(5, max(1, int(self.history_max)))
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "crash_loops": self.crash_loops,
            "next_attempt_at": self.next_attempt_at,
            "cooldown_remaining_sec": self.cooldown_remaining(),
            "last_error": self.last_error,
            "history_max": self.history_max,
            "history_tail": list(self._history[-tail:]),
        }


__all__ = ["CircuitBreaker", "CircuitState"]
