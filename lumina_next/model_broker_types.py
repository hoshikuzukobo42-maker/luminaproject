"""Public types for the local Lumina model broker.

The broker deliberately depends on a small runtime protocol instead of a
specific model server.  A runtime implementation may manage Ollama,
llama.cpp, or another local process, but the broker itself never performs
network access or starts a process implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, TypeAlias
from uuid import uuid4


class ModelKind(str, Enum):
    """The two mutually exclusive heavy model roles supported by the broker."""

    LLM = "llm"
    VLM = "vlm"


class BrokerState(str, Enum):
    """Observable lifecycle states of :class:`ModelBroker`."""

    IDLE = "idle"
    LLM_LOADING = "llm_loading"
    LLM_READY = "llm_ready"
    VLM_LOADING = "vlm_loading"
    VLM_READY = "vlm_ready"
    RESTORING_LLM = "restoring_llm"
    UNLOADING = "unloading"
    COOLDOWN = "cooldown"
    BUSY = "busy"
    FAULT = "fault"


ChatMessage: TypeAlias = Mapping[str, str]


class BrokerError(RuntimeError):
    """Base error raised by the broker."""


class BrokerBusyError(BrokerError):
    """The broker cannot accept work in its current lifecycle state."""


class BrokerClosedError(BrokerError):
    """The broker has been closed or is draining."""


class BrokerFaultError(BrokerError):
    """A runtime operation failed and the broker entered ``fault``."""


class BrokerQueueFullError(BrokerBusyError):
    """The bounded chat queue has no capacity."""


class BrokerTimeoutError(BrokerError, TimeoutError):
    """A lifecycle, chat, or drain operation exceeded its deadline."""


class InvalidStateTransition(BrokerError):
    """An operation requested a transition that cannot be performed safely."""


@dataclass(frozen=True)
class ChatRequest:
    """One queued chat request.

    ``messages`` is copied to a tuple by the broker before enqueueing.  The
    values are intentionally plain mappings so existing provider adapters can
    pass them through without a serialization dependency.
    """

    messages: tuple[ChatMessage, ...]
    request_id: str = field(default_factory=lambda: uuid4().hex)
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("chat request must contain at least one message")
        for message in messages:
            if not isinstance(message, Mapping):
                raise TypeError("chat messages must be mappings")
            if not str(message.get("role", "")).strip():
                raise ValueError("each chat message needs a non-empty role")
            if not isinstance(message.get("content"), str):
                raise TypeError("each chat message content must be a string")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("chat timeout_seconds must be greater than zero")
        if not str(self.request_id).strip():
            raise ValueError("request_id must not be empty")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "request_id", str(self.request_id))


@dataclass(frozen=True)
class ChatResponse:
    """Normalized response returned by a local runtime."""

    text: str
    model: str
    request_id: str
    latency_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("chat response text must be non-empty")
        if not str(self.model).strip():
            raise ValueError("chat response model must be non-empty")
        object.__setattr__(self, "text", self.text.strip())
        object.__setattr__(self, "model", str(self.model))


@dataclass(frozen=True)
class BrokerStatus:
    """Immutable broker status suitable for health endpoints and logs."""

    state: BrokerState
    state_since: str
    active_kind: ModelKind | None
    active_model: str | None
    queue_depth: int
    queue_capacity: int
    accepting: bool
    draining: bool
    closed: bool
    worker_running: bool
    last_error: str | None = None
    in_flight_requests: int = 0
    lifecycle_phase: str | None = None
    circuit_state: str | None = None
    cooldown_remaining_sec: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible status payload."""

        return {
            "state": self.state.value,
            "state_since": self.state_since,
            "active_kind": self.active_kind.value if self.active_kind else None,
            "active_model": self.active_model,
            "queue_depth": self.queue_depth,
            "queue_capacity": self.queue_capacity,
            "accepting": self.accepting,
            "draining": self.draining,
            "closed": self.closed,
            "worker_running": self.worker_running,
            "last_error": self.last_error,
            "in_flight_requests": self.in_flight_requests,
            "lifecycle_phase": self.lifecycle_phase,
            "circuit_state": self.circuit_state,
            "cooldown_remaining_sec": self.cooldown_remaining_sec,
        }


class LocalModelRuntime(Protocol):
    """Minimal async adapter implemented by the local heavy-process owner.

    Calls are serialized by the broker.  ``start`` and ``stop`` must be
    idempotent from the adapter's perspective.  ``chat`` must not use a remote
    service; local-only policy is an adapter/configuration responsibility.
    """

    async def start(self, kind: ModelKind, model: str) -> None:
        """Start or load one model role."""

    async def stop(self, kind: ModelKind, model: str) -> None:
        """Stop or unload one model role."""

    async def chat(self, kind: ModelKind, request: ChatRequest) -> ChatResponse | str | Mapping[str, Any]:
        """Run one request against the already active local model."""


def normalize_model_kind(value: ModelKind | str) -> ModelKind:
    """Convert a public string value to ``ModelKind`` with a clear error."""

    if isinstance(value, ModelKind):
        return value
    try:
        return ModelKind(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported model kind: {value!r}") from exc


def copy_messages(messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    """Copy and validate messages without introducing a third-party model."""

    return ChatRequest(tuple(messages)).messages


__all__ = [
    "BrokerBusyError",
    "BrokerClosedError",
    "BrokerError",
    "BrokerFaultError",
    "BrokerQueueFullError",
    "BrokerState",
    "BrokerStatus",
    "BrokerTimeoutError",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "InvalidStateTransition",
    "LocalModelRuntime",
    "ModelKind",
    "copy_messages",
    "normalize_model_kind",
]
