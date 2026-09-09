"""Explicit lifecycle state machine for local LLM/VLM model management.

Maps broker-visible states to the marathon-required vocabulary:
unloaded, loading, ready, busy, draining, unloading, failed, cooldown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class LifecyclePhase(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    UNLOADING = "unloading"
    FAILED = "failed"
    COOLDOWN = "cooldown"


class LifecycleEvent(str, Enum):
    REQUEST_LOAD = "request_load"
    LOAD_SUCCEEDED = "load_succeeded"
    LOAD_FAILED = "load_failed"
    REQUEST_INFERENCE = "request_inference"
    INFERENCE_DONE = "inference_done"
    REQUEST_UNLOAD = "request_unload"
    UNLOAD_SUCCEEDED = "unload_succeeded"
    UNLOAD_FAILED = "unload_failed"
    REQUEST_DRAIN = "request_drain"
    DRAIN_DONE = "drain_done"
    ENTER_COOLDOWN = "enter_cooldown"
    COOLDOWN_EXPIRED = "cooldown_expired"
    RESET = "reset"


class LifecycleTransitionError(RuntimeError):
    """Raised when an event is illegal in the current phase."""


@dataclass(frozen=True)
class LifecycleSnapshot:
    phase: LifecyclePhase
    active_kind: str | None = None
    active_model: str | None = None
    in_flight_requests: int = 0
    draining: bool = False
    last_error: str | None = None
    cooldown_until: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "active_kind": self.active_kind,
            "active_model": self.active_model,
            "in_flight_requests": self.in_flight_requests,
            "draining": self.draining,
            "last_error": self.last_error,
            "cooldown_until": self.cooldown_until,
        }


_TRANSITIONS: dict[tuple[LifecyclePhase, LifecycleEvent], LifecyclePhase] = {
    (LifecyclePhase.UNLOADED, LifecycleEvent.REQUEST_LOAD): LifecyclePhase.LOADING,
    (LifecyclePhase.LOADING, LifecycleEvent.LOAD_SUCCEEDED): LifecyclePhase.READY,
    (LifecyclePhase.LOADING, LifecycleEvent.LOAD_FAILED): LifecyclePhase.FAILED,
    (LifecyclePhase.READY, LifecycleEvent.REQUEST_INFERENCE): LifecyclePhase.BUSY,
    (LifecyclePhase.BUSY, LifecycleEvent.REQUEST_INFERENCE): LifecyclePhase.BUSY,
    (LifecyclePhase.BUSY, LifecycleEvent.INFERENCE_DONE): LifecyclePhase.READY,
    (LifecyclePhase.READY, LifecycleEvent.REQUEST_DRAIN): LifecyclePhase.DRAINING,
    (LifecyclePhase.BUSY, LifecycleEvent.REQUEST_DRAIN): LifecyclePhase.DRAINING,
    (LifecyclePhase.DRAINING, LifecycleEvent.DRAIN_DONE): LifecyclePhase.READY,
    (LifecyclePhase.READY, LifecycleEvent.REQUEST_UNLOAD): LifecyclePhase.UNLOADING,
    (LifecyclePhase.DRAINING, LifecycleEvent.REQUEST_UNLOAD): LifecyclePhase.UNLOADING,
    (LifecyclePhase.UNLOADING, LifecycleEvent.UNLOAD_SUCCEEDED): LifecyclePhase.UNLOADED,
    (LifecyclePhase.UNLOADING, LifecycleEvent.UNLOAD_FAILED): LifecyclePhase.FAILED,
    (LifecyclePhase.FAILED, LifecycleEvent.ENTER_COOLDOWN): LifecyclePhase.COOLDOWN,
    (LifecyclePhase.COOLDOWN, LifecycleEvent.COOLDOWN_EXPIRED): LifecyclePhase.UNLOADED,
    (LifecyclePhase.FAILED, LifecycleEvent.RESET): LifecyclePhase.UNLOADED,
    (LifecyclePhase.COOLDOWN, LifecycleEvent.RESET): LifecyclePhase.UNLOADED,
    (LifecyclePhase.UNLOADED, LifecycleEvent.RESET): LifecyclePhase.UNLOADED,
}


@dataclass
class ModelLifecycleFSM:
    """Deterministic lifecycle FSM with in-flight request counting."""

    snapshot: LifecycleSnapshot = field(default_factory=lambda: LifecycleSnapshot(LifecyclePhase.UNLOADED))

    @property
    def phase(self) -> LifecyclePhase:
        return self.snapshot.phase

    def can_load(self) -> bool:
        return self.phase in {LifecyclePhase.UNLOADED, LifecyclePhase.COOLDOWN, LifecyclePhase.FAILED}

    def can_unload(self) -> bool:
        if self.phase in {LifecyclePhase.LOADING, LifecyclePhase.UNLOADING, LifecyclePhase.UNLOADED}:
            return False
        if self.snapshot.in_flight_requests > 0 and self.phase is LifecyclePhase.BUSY:
            return False
        return self.phase in {LifecyclePhase.READY, LifecyclePhase.DRAINING}

    def can_accept_inference(self) -> bool:
        return self.phase in {LifecyclePhase.READY, LifecyclePhase.BUSY} and not self.snapshot.draining

    def apply(self, event: LifecycleEvent, *, error: str | None = None, now: float | None = None) -> LifecycleSnapshot:
        current = self.snapshot.phase
        if event is LifecycleEvent.REQUEST_INFERENCE:
            if not self.can_accept_inference():
                raise LifecycleTransitionError(f"cannot start inference in {current.value}")
            in_flight = self.snapshot.in_flight_requests + 1
            next_phase = LifecyclePhase.BUSY if in_flight > 0 else current
            self.snapshot = LifecycleSnapshot(
                phase=next_phase,
                active_kind=self.snapshot.active_kind,
                active_model=self.snapshot.active_model,
                in_flight_requests=in_flight,
                draining=self.snapshot.draining,
                last_error=self.snapshot.last_error,
                cooldown_until=self.snapshot.cooldown_until,
            )
            return self.snapshot

        if event is LifecycleEvent.INFERENCE_DONE:
            in_flight = max(0, self.snapshot.in_flight_requests - 1)
            if self.snapshot.draining and in_flight == 0:
                next_phase = LifecyclePhase.DRAINING
            elif in_flight > 0:
                next_phase = LifecyclePhase.BUSY
            else:
                next_phase = LifecyclePhase.READY
            self.snapshot = LifecycleSnapshot(
                phase=next_phase,
                active_kind=self.snapshot.active_kind,
                active_model=self.snapshot.active_model,
                in_flight_requests=in_flight,
                draining=self.snapshot.draining,
                last_error=self.snapshot.last_error,
                cooldown_until=self.snapshot.cooldown_until,
            )
            return self.snapshot

        key = (current, event)
        if key not in _TRANSITIONS:
            raise LifecycleTransitionError(f"illegal transition {current.value} + {event.value}")

        next_phase = _TRANSITIONS[key]
        draining = self.snapshot.draining
        if event is LifecycleEvent.REQUEST_DRAIN:
            draining = True
        if event is LifecycleEvent.DRAIN_DONE:
            draining = False

        cooldown_until = self.snapshot.cooldown_until
        last_error = self.snapshot.last_error
        if event is LifecycleEvent.LOAD_FAILED:
            last_error = (error or "load failed")[:1000]
        if event is LifecycleEvent.UNLOAD_FAILED:
            last_error = (error or "unload failed")[:1000]
        if event is LifecycleEvent.ENTER_COOLDOWN and now is not None:
            cooldown_until = now + float(error or 30.0)
        if event in {LifecycleEvent.COOLDOWN_EXPIRED, LifecycleEvent.RESET}:
            cooldown_until = None
            last_error = None if event is LifecycleEvent.RESET else last_error

        active_kind = self.snapshot.active_kind
        active_model = self.snapshot.active_model
        if event is LifecycleEvent.REQUEST_LOAD:
            pass
        if next_phase is LifecyclePhase.UNLOADED:
            active_kind = None
            active_model = None

        self.snapshot = LifecycleSnapshot(
            phase=next_phase,
            active_kind=active_kind,
            active_model=active_model,
            in_flight_requests=0 if next_phase is not LifecyclePhase.BUSY else self.snapshot.in_flight_requests,
            draining=draining,
            last_error=last_error,
            cooldown_until=cooldown_until,
        )
        return self.snapshot

    def bind_model(self, kind: str, model: str) -> None:
        self.snapshot = LifecycleSnapshot(
            phase=self.snapshot.phase,
            active_kind=kind,
            active_model=model,
            in_flight_requests=self.snapshot.in_flight_requests,
            draining=self.snapshot.draining,
            last_error=self.snapshot.last_error,
            cooldown_until=self.snapshot.cooldown_until,
        )


def map_broker_state_to_phase(
    broker_state: str,
    *,
    in_flight: int = 0,
    draining: bool = False,
) -> LifecyclePhase:
    """Map legacy broker state strings to marathon lifecycle phases."""

    normalized = str(broker_state).strip().lower()
    if draining:
        return LifecyclePhase.DRAINING
    if in_flight > 0:
        return LifecyclePhase.BUSY
    mapping: Mapping[str, LifecyclePhase] = {
        "idle": LifecyclePhase.UNLOADED,
        "llm_loading": LifecyclePhase.LOADING,
        "vlm_loading": LifecyclePhase.LOADING,
        "llm_ready": LifecyclePhase.READY,
        "vlm_ready": LifecyclePhase.READY,
        "restoring_llm": LifecyclePhase.LOADING,
        "unloading": LifecyclePhase.UNLOADING,
        "cooldown": LifecyclePhase.COOLDOWN,
        "fault": LifecyclePhase.FAILED,
    }
    return mapping.get(normalized, LifecyclePhase.UNLOADED)


def explain_transition(phase: LifecyclePhase, event: LifecycleEvent) -> str:
    """Describe whether *event* is legal from *phase* and the resulting phase."""

    if event is LifecycleEvent.REQUEST_INFERENCE:
        if phase in {LifecyclePhase.READY, LifecyclePhase.BUSY}:
            return f"{phase.value} + {event.value} → busy (in-flight++)"
        return f"{phase.value} + {event.value} → illegal (inference not accepted)"
    if event is LifecycleEvent.INFERENCE_DONE:
        return f"{phase.value} + {event.value} → ready|busy|draining (in-flight--)"
    key = (phase, event)
    if key not in _TRANSITIONS:
        return f"{phase.value} + {event.value} → illegal"
    return f"{phase.value} + {event.value} → {_TRANSITIONS[key].value}"


def explain_state(fsm: ModelLifecycleFSM) -> str:
    """Human-readable summary of the current lifecycle snapshot."""

    snap = fsm.snapshot
    parts = [f"phase={snap.phase.value}"]
    if snap.active_kind:
        parts.append(f"kind={snap.active_kind}")
    if snap.active_model:
        parts.append(f"model={snap.active_model}")
    if snap.in_flight_requests:
        parts.append(f"in_flight={snap.in_flight_requests}")
    if snap.draining:
        parts.append("draining=true")
    if snap.last_error:
        parts.append("last_error=set")
    if snap.cooldown_until is not None:
        parts.append(f"cooldown_until={snap.cooldown_until}")
    parts.append(f"can_load={fsm.can_load()}")
    parts.append(f"can_unload={fsm.can_unload()}")
    parts.append(f"can_accept_inference={fsm.can_accept_inference()}")
    return ", ".join(parts)


__all__ = [
    "LifecycleEvent",
    "LifecyclePhase",
    "LifecycleSnapshot",
    "LifecycleTransitionError",
    "ModelLifecycleFSM",
    "explain_state",
    "explain_transition",
    "map_broker_state_to_phase",
]
