"""Async local-only broker for exclusive LLM/VLM model lifecycles.

The broker owns ordering and admission control.  A ``LocalModelRuntime`` owns
the actual process or model-server details.  Exactly one model role may be
active at a time, and every runtime call is serialized so a stop can never
race a load or chat operation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .circuit_breaker import CircuitBreaker
from .model_lifecycle_fsm import map_broker_state_to_phase
from .model_broker_types import (
    BrokerBusyError,
    BrokerClosedError,
    BrokerFaultError,
    BrokerQueueFullError,
    BrokerState,
    BrokerStatus,
    BrokerTimeoutError,
    ChatRequest,
    ChatResponse,
    InvalidStateTransition,
    LocalModelRuntime,
    ModelKind,
    normalize_model_kind,
)


@dataclass
class _QueuedChat:
    request: ChatRequest
    result: asyncio.Future[ChatResponse]
    deadline_monotonic: float


class ModelBroker:
    """Serialize local heavy-model lifecycle and bounded chat work.

    ``runtime`` is intentionally injected.  It should be a local adapter that
    starts/stops the heavy process and executes chat.  The broker performs no
    HTTP, subprocess, or model discovery itself.

    ``chat`` is non-blocking with respect to queue admission: it raises
    :class:`BrokerQueueFullError` immediately when the configured capacity is
    exhausted.  Once admitted, it waits for the request's response and uses
    ``operation_timeout_seconds`` when the request has no explicit deadline.
    """

    def __init__(
        self,
        runtime: LocalModelRuntime,
        *,
        llm_model: str,
        vlm_model: str,
        chat_queue_capacity: int = 8,
        operation_timeout_seconds: float = 120.0,
        drain_timeout_seconds: float = 15.0,
    ) -> None:
        if not str(llm_model).strip() or not str(vlm_model).strip():
            raise ValueError("llm_model and vlm_model are required")
        if chat_queue_capacity <= 0:
            raise ValueError("chat_queue_capacity must be greater than zero")
        if operation_timeout_seconds <= 0 or drain_timeout_seconds <= 0:
            raise ValueError("broker timeouts must be greater than zero")
        self.runtime = runtime
        self.llm_model = str(llm_model).strip()
        self.vlm_model = str(vlm_model).strip()
        self.chat_queue_capacity = int(chat_queue_capacity)
        self.operation_timeout_seconds = float(operation_timeout_seconds)
        self.drain_timeout_seconds = float(drain_timeout_seconds)

        self._state = BrokerState.IDLE
        self._state_since = self._timestamp()
        self._active_kind: ModelKind | None = None
        self._active_model: str | None = None
        self._last_error: str | None = None
        self._accepting = True
        self._draining = False
        self._closed = False

        self._queue: asyncio.Queue[_QueuedChat] = asyncio.Queue(maxsize=self.chat_queue_capacity)
        self._worker_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._runtime_lock = asyncio.Lock()
        self._state_changed = asyncio.Event()
        self._in_flight_requests = 0
        self._circuit = CircuitBreaker()

    def _lifecycle_phase(self) -> str:
        return map_broker_state_to_phase(
            self._state.value,
            in_flight=self._in_flight_requests,
            draining=self._draining,
        ).value

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def state(self) -> BrokerState:
        """Return the current state without an await point."""

        return self._state

    def status(self) -> BrokerStatus:
        """Return an immutable, JSON-friendly snapshot of broker state."""

        return BrokerStatus(
            state=self._state,
            state_since=self._state_since,
            active_kind=self._active_kind,
            active_model=self._active_model,
            queue_depth=self._queue.qsize(),
            queue_capacity=self.chat_queue_capacity,
            accepting=self._accepting,
            draining=self._draining,
            closed=self._closed,
            worker_running=self._worker_task is not None and not self._worker_task.done(),
            last_error=self._last_error,
            in_flight_requests=self._in_flight_requests,
            lifecycle_phase=self._lifecycle_phase(),
            circuit_state=self._circuit.state.value,
            cooldown_remaining_sec=self._circuit.cooldown_remaining(),
        )

    async def snapshot(self) -> dict[str, Any]:
        """Return a status mapping matching existing Lumina health payloads."""

        return self.status().as_dict()

    async def wait_for_state(
        self,
        expected: BrokerState | str,
        *,
        timeout_seconds: float | None = None,
    ) -> BrokerStatus:
        """Wait until ``expected`` is observable, or raise a broker timeout."""

        target = expected if isinstance(expected, BrokerState) else BrokerState(str(expected))
        timeout = timeout_seconds or self.operation_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        deadline = time.monotonic() + timeout
        while self._state != target:
            self._state_changed.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrokerTimeoutError(f"timed out waiting for state {target.value}")
            try:
                await asyncio.wait_for(self._state_changed.wait(), remaining)
            except asyncio.TimeoutError as exc:
                raise BrokerTimeoutError(f"timed out waiting for state {target.value}") from exc
        return self.status()

    def _set_state(self, state: BrokerState) -> None:
        if self._state != state:
            self._state = state
            self._state_since = self._timestamp()
            self._state_changed.set()

    def _set_fault(self, error: BaseException | str) -> None:
        detail = str(error).strip() or error.__class__.__name__ if isinstance(error, BaseException) else str(error)
        self._last_error = detail[:1000]
        self._set_state(BrokerState.FAULT)

    def _ensure_open_for_work(self) -> None:
        if self._closed:
            raise BrokerClosedError("model broker is closed")
        if self._draining or not self._accepting:
            raise BrokerClosedError("model broker is draining")

    def _model_for(self, kind: ModelKind) -> str:
        return self.llm_model if kind is ModelKind.LLM else self.vlm_model

    def _ready_state(self, kind: ModelKind) -> BrokerState:
        return BrokerState.LLM_READY if kind is ModelKind.LLM else BrokerState.VLM_READY

    def _loading_state(self, kind: ModelKind) -> BrokerState:
        return BrokerState.LLM_LOADING if kind is ModelKind.LLM else BrokerState.VLM_LOADING

    async def _runtime_call_locked(self, method: str, *args: Any, timeout_seconds: float) -> Any:
        operation = getattr(self.runtime, method, None)
        if not callable(operation):
            raise InvalidStateTransition(f"runtime does not implement async {method}()")
        try:
            return await asyncio.wait_for(operation(*args), timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise BrokerTimeoutError(f"runtime {method} timed out after {timeout_seconds:.2f}s") from exc

    async def activate(self, kind: ModelKind | str) -> BrokerStatus:
        """Make exactly one model role ready, stopping any other role first."""

        target = normalize_model_kind(kind)
        self._ensure_open_for_work()
        if not self._circuit.allow_attempt():
            raise BrokerBusyError(
                f"circuit breaker open; retry in {self._circuit.cooldown_remaining():.1f}s"
            )
        async with self._lifecycle_lock:
            if self._active_kind is target and self._state is self._ready_state(target):
                return self.status()

            previous_kind = self._active_kind
            previous_model = self._active_model
            if target is ModelKind.LLM and previous_kind is ModelKind.VLM:
                self._set_state(BrokerState.RESTORING_LLM)
            else:
                self._set_state(self._loading_state(target))

            async with self._runtime_lock:
                try:
                    if previous_kind is not None:
                        await self._runtime_call_locked(
                            "stop",
                            previous_kind,
                            previous_model or self._model_for(previous_kind),
                            timeout_seconds=self.operation_timeout_seconds,
                        )
                        self._active_kind = None
                        self._active_model = None
                    model = self._model_for(target)
                    await self._runtime_call_locked(
                        "start",
                        target,
                        model,
                        timeout_seconds=self.operation_timeout_seconds,
                    )
                except (BrokerTimeoutError, InvalidStateTransition) as exc:
                    self._circuit.record_failure(str(exc))
                    self._set_fault(exc)
                    raise
                except asyncio.CancelledError:
                    self._circuit.record_failure("activation cancelled")
                    self._set_fault("activation cancelled")
                    raise
                except Exception as exc:
                    self._circuit.record_failure(str(exc))
                    self._set_fault(exc)
                    raise BrokerFaultError(f"failed to activate {target.value}: {exc}") from exc

            self._active_kind = target
            self._active_model = self._model_for(target)
            self._last_error = None
            self._circuit.record_success()
            self._set_state(self._ready_state(target))
            return self.status()

    async def ensure_llm(self) -> BrokerStatus:
        """Activate the LLM, restoring it after a VLM session when needed."""

        return await self.activate(ModelKind.LLM)

    async def ensure_vlm(self) -> BrokerStatus:
        """Activate the VLM, unloading the LLM first when necessary."""

        return await self.activate(ModelKind.VLM)

    async def _deactivate(self, *, timeout_seconds: float) -> BrokerStatus:
        async with self._lifecycle_lock:
            if self._active_kind is None:
                self._set_state(BrokerState.IDLE)
                return self.status()
            if self._in_flight_requests > 0:
                raise BrokerBusyError("cannot unload while inference is in flight")
            kind = self._active_kind
            model = self._active_model or self._model_for(kind)
            self._set_state(BrokerState.UNLOADING)
            async with self._runtime_lock:
                try:
                    await self._runtime_call_locked("stop", kind, model, timeout_seconds=timeout_seconds)
                except (BrokerTimeoutError, InvalidStateTransition) as exc:
                    self._circuit.record_failure(str(exc))
                    self._set_fault(exc)
                    raise
                except Exception as exc:
                    self._circuit.record_failure(str(exc))
                    self._set_fault(exc)
                    raise BrokerFaultError(f"failed to stop {kind.value}: {exc}") from exc
            self._active_kind = None
            self._active_model = None
            self._set_state(BrokerState.IDLE)
            return self.status()

    async def unload(self, *, timeout_seconds: float | None = None) -> BrokerStatus:
        """Stop the active model while leaving the broker open for later use."""

        if self._closed:
            raise BrokerClosedError("model broker is closed")
        timeout = timeout_seconds or self.operation_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        # Serialize against in-flight generation by taking the same locks as chat/use_model.
        return await self._deactivate(timeout_seconds=float(timeout))

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._chat_worker(), name="lumina-model-broker-chat")

    async def chat(
        self,
        messages: Sequence[Mapping[str, str]] | ChatRequest,
        *,
        request_id: str | None = None,
        timeout_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ChatResponse:
        """Queue one LLM request and wait for its normalized response."""

        self._ensure_open_for_work()
        request = messages if isinstance(messages, ChatRequest) else ChatRequest(
            tuple(messages),
            request_id=request_id or f"chat-{time.monotonic_ns()}",
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )
        if not request.request_id:
            request = ChatRequest(
                request.messages,
                timeout_seconds=request.timeout_seconds,
                metadata=request.metadata,
            )
        loop = asyncio.get_running_loop()
        result: asyncio.Future[ChatResponse] = loop.create_future()
        request_timeout = request.timeout_seconds or self.operation_timeout_seconds
        if request_timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        queued = _QueuedChat(
            request=request,
            result=result,
            deadline_monotonic=time.monotonic() + float(request_timeout),
        )
        try:
            self._queue.put_nowait(queued)
        except asyncio.QueueFull as exc:
            raise BrokerQueueFullError(
                f"chat queue is full ({self.chat_queue_capacity} requests)"
            ) from exc
        self._ensure_worker()
        try:
            return await asyncio.wait_for(asyncio.shield(result), float(request_timeout))
        except asyncio.TimeoutError as exc:
            if not result.done():
                result.cancel()
            raise BrokerTimeoutError(
                f"chat request timed out after {request_timeout:.2f}s including queue wait"
            ) from exc

    async def use_model(
        self,
        kind: ModelKind | str,
        operation: Callable[[], Awaitable[Any]],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Run one operation while retaining exclusive ownership of a model."""

        target = normalize_model_kind(kind)
        self._ensure_open_for_work()
        if not callable(operation):
            raise TypeError("operation must be callable")
        timeout = float(timeout_seconds or self.operation_timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrokerTimeoutError(
                    f"{target.value} operation timed out before model ownership was acquired"
                )
            try:
                await asyncio.wait_for(self.activate(target), remaining)
            except asyncio.TimeoutError as exc:
                raise BrokerTimeoutError(
                    f"{target.value} activation exceeded the operation deadline"
                ) from exc
            async with self._lifecycle_lock:
                if (
                    self._active_kind is not target
                    or self._state is not self._ready_state(target)
                ):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BrokerTimeoutError(
                        f"{target.value} operation timed out before execution"
                    )
                async with self._runtime_lock:
                    try:
                        return await asyncio.wait_for(operation(), remaining)
                    except asyncio.TimeoutError as exc:
                        error = BrokerTimeoutError(
                            f"{target.value} operation timed out after {timeout:.2f}s"
                        )
                        self._set_fault(error)
                        raise error from exc

    async def _chat_worker(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                if queued.result.done():
                    continue
                try:
                    while True:
                        remaining = queued.deadline_monotonic - time.monotonic()
                        if remaining <= 0:
                            raise BrokerTimeoutError(
                                "chat request deadline expired while queued or loading"
                            )
                        try:
                            await asyncio.wait_for(self.ensure_llm(), remaining)
                        except asyncio.TimeoutError as exc:
                            raise BrokerTimeoutError(
                                "chat request deadline expired while loading the LLM"
                            ) from exc
                        async with self._lifecycle_lock:
                            if (
                                self._active_kind is not ModelKind.LLM
                                or self._state is not BrokerState.LLM_READY
                            ):
                                continue
                            remaining = queued.deadline_monotonic - time.monotonic()
                            if remaining <= 0:
                                raise BrokerTimeoutError(
                                    "chat request deadline expired before inference"
                                )
                            started = time.perf_counter()
                            self._in_flight_requests += 1
                            if self._state in {BrokerState.LLM_READY, BrokerState.VLM_READY}:
                                self._set_state(BrokerState.BUSY)
                        async with self._runtime_lock:
                            try:
                                raw = await self._runtime_call_locked(
                                    "chat",
                                    ModelKind.LLM,
                                    queued.request,
                                    timeout_seconds=remaining,
                                )
                            finally:
                                async with self._lifecycle_lock:
                                    self._in_flight_requests = max(0, self._in_flight_requests - 1)
                                    if self._in_flight_requests == 0 and self._active_kind is ModelKind.LLM:
                                        self._set_state(BrokerState.LLM_READY)
                        break
                    response = self._normalize_response(raw, queued.request, time.perf_counter() - started)
                    if not queued.result.done():
                        queued.result.set_result(response)
                except asyncio.CancelledError:
                    if not queued.result.done():
                        queued.result.set_exception(BrokerClosedError("chat worker was cancelled"))
                    raise
                except BrokerTimeoutError as exc:
                    self._set_fault(exc)
                    if not queued.result.done():
                        queued.result.set_exception(exc)
                except BrokerFaultError as exc:
                    if not queued.result.done():
                        queued.result.set_exception(exc)
                except Exception as exc:
                    self._set_fault(exc)
                    error = BrokerFaultError(f"chat request failed: {exc}")
                    if not queued.result.done():
                        queued.result.set_exception(error)
            finally:
                self._queue.task_done()

    def _normalize_response(
        self,
        raw: ChatResponse | str | Mapping[str, Any],
        request: ChatRequest,
        elapsed_seconds: float,
    ) -> ChatResponse:
        if isinstance(raw, ChatResponse):
            if raw.request_id == request.request_id:
                return raw
            return ChatResponse(raw.text, raw.model, request.request_id, raw.latency_ms, raw.metadata)
        if isinstance(raw, str):
            return ChatResponse(
                text=raw,
                model=self.llm_model,
                request_id=request.request_id,
                latency_ms=elapsed_seconds * 1000.0,
            )
        if isinstance(raw, Mapping):
            text = raw.get("text") or raw.get("content") or raw.get("response")
            if not isinstance(text, str) or not text.strip():
                raise BrokerFaultError("runtime returned a response without text")
            model = str(raw.get("model") or self.llm_model)
            latency = raw.get("latency_ms", elapsed_seconds * 1000.0)
            try:
                latency_value = float(latency)
            except (TypeError, ValueError):
                latency_value = elapsed_seconds * 1000.0
            return ChatResponse(text, model, request.request_id, latency_value, raw)
        raise BrokerFaultError(f"runtime returned unsupported response type: {type(raw).__name__}")

    async def drain(self, *, timeout_seconds: float | None = None) -> BrokerStatus:
        """Stop admission and wait for all admitted chat requests to finish."""

        if self._closed:
            return self.status()
        timeout = timeout_seconds or self.drain_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._accepting = False
        self._draining = True
        try:
            await asyncio.wait_for(asyncio.shield(self._queue.join()), float(timeout))
        except asyncio.TimeoutError as exc:
            error = BrokerTimeoutError(f"drain timed out after {timeout:.2f}s")
            self._set_fault(error)
            raise error from exc
        return self.status()

    async def close(self, *, timeout_seconds: float | None = None) -> BrokerStatus:
        """Drain queued work, stop the active model, and close the broker."""

        if self._closed:
            return self.status()
        timeout = timeout_seconds or self.drain_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        drain_error: BrokerTimeoutError | None = None
        try:
            await self.drain(timeout_seconds=timeout)
        except BrokerTimeoutError as exc:
            drain_error = exc
        try:
            remaining = max(0.001, float(timeout))
            await asyncio.wait_for(self._deactivate(timeout_seconds=remaining), remaining)
        except (BrokerTimeoutError, InvalidStateTransition, BrokerFaultError) as exc:
            if drain_error is None:
                drain_error = exc if isinstance(exc, BrokerTimeoutError) else BrokerTimeoutError(str(exc))
            self._set_fault(exc)
        finally:
            self._closed = True
            self._accepting = False
            self._draining = True
            if self._worker_task is not None and not self._worker_task.done():
                self._worker_task.cancel()
                await asyncio.gather(self._worker_task, return_exceptions=True)
        if drain_error is not None:
            raise drain_error
        return self.status()

    async def shutdown(self, *, timeout_seconds: float | None = None) -> BrokerStatus:
        """Alias for :meth:`close` for lifecycle-manager integrations."""

        return await self.close(timeout_seconds=timeout_seconds)


__all__ = ["ModelBroker"]
