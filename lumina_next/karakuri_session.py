"""Local Karakuri conversation session primitives.

This module deliberately does not know how to reach Karakuri World.  It accepts
already-delivered notification data and delegates response generation to a
local, dependency-injected callable.  That boundary makes notification
idempotency, retries, and conversation state testable without credentials or
network access.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence, Union


DEFAULT_RESPONSE_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.25
DEFAULT_RECONTACT_COOLDOWN_SECONDS = 30.0
DEFAULT_MAX_HISTORY_TURNS = 20
DEFAULT_MAX_NOTIFICATIONS = 2048
DEFAULT_MAX_RESPONSE_CHARS = 4000


class KarakuriSessionError(ValueError):
    """Base error for invalid local session configuration or input."""


class NotificationValidationError(KarakuriSessionError):
    """Raised when a notification has no usable local identity or text."""


@dataclass(frozen=True)
class KarakuriSessionConfig:
    """Bounds for a local session.

    ``max_retries`` is the number of retries after the first attempt.  Thus a
    value of two permits at most three provider calls for one notification.
    """

    response_timeout_seconds: float = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    recontact_cooldown_seconds: float = DEFAULT_RECONTACT_COOLDOWN_SECONDS
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS
    max_notifications: int = DEFAULT_MAX_NOTIFICATIONS
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS

    def __post_init__(self) -> None:
        _require_finite_nonnegative(
            self.response_timeout_seconds, "response_timeout_seconds"
        )
        _require_finite_nonnegative(
            self.retry_backoff_seconds, "retry_backoff_seconds"
        )
        _require_finite_nonnegative(
            self.recontact_cooldown_seconds, "recontact_cooldown_seconds"
        )
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool):
            raise KarakuriSessionError("max_retries must be an integer")
        if not 0 <= self.max_retries <= 5:
            raise KarakuriSessionError("max_retries must be between 0 and 5")
        for name in ("max_history_turns", "max_notifications", "max_response_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise KarakuriSessionError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ConversationTurn:
    """A clean turn retained for local multi-turn response generation."""

    role: str
    text: str
    notification_id: str | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "notification_id": self.notification_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ResponseRequest:
    """The only input supplied to the local response provider.

    Warning text is intentionally absent.  ``warnings_consumed`` is metadata,
    not warning content, so a provider cannot accidentally echo an internal
    skill warning into a user-facing response.
    """

    notification_id: str
    conversation_id: str
    text: str
    history: tuple[ConversationTurn, ...]
    warnings_consumed: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionResult:
    """Stable result returned by :meth:`KarakuriSession.handle_notification`."""

    notification_id: str
    conversation_id: str
    status: str
    response: str = ""
    attempts: int = 0
    duplicate: bool = False
    timed_out: bool = False
    warnings_consumed: int = 0
    cooldown_until: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "response": self.response,
            "attempts": self.attempts,
            "duplicate": self.duplicate,
            "timed_out": self.timed_out,
            "warnings_consumed": self.warnings_consumed,
            "cooldown_until": self.cooldown_until,
            "error": self.error,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass
class _NotificationRecord:
    future: asyncio.Future[SessionResult]
    result: SessionResult | None = None


ResponseValue = Union[str, Mapping[str, Any]]
ResponseProvider = Callable[..., Union[ResponseValue, Awaitable[ResponseValue]]]


class KarakuriSession:
    """Run local, idempotent, multi-turn notification sessions.

    The provider may accept one of these signatures:

    * ``provider(request: ResponseRequest)`` (recommended)
    * ``provider(text, history)``
    * ``provider(text, history, metadata)``

    The provider must be local.  This class performs no HTTP, filesystem,
    environment, or secret access.
    """

    def __init__(
        self,
        responder: ResponseProvider,
        *,
        config: KarakuriSessionConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not callable(responder):
            raise KarakuriSessionError("responder must be callable")
        self.responder = responder
        self.config = config or KarakuriSessionConfig()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._notifications: OrderedDict[str, _NotificationRecord] = OrderedDict()
        self._histories: dict[str, deque[ConversationTurn]] = {}
        self._last_recontact: dict[str, float] = {}

    async def handle_notification(
        self,
        notification: Mapping[str, Any] | str,
        text: str | None = None,
        *,
        conversation_id: str | None = None,
        sender_id: str | None = None,
        recontact: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionResult:
        """Consume one notification exactly once and return its local result.

        A mapping must contain ``notification_id``.  Text is read from
        ``text``, ``message``, or ``content`` when not passed explicitly.
        ``skill_warning``/``skill_warnings``/``warnings`` fields are consumed
        as internal metadata and are never included in ``ResponseRequest``.
        """

        payload = _as_mapping(notification)
        notification_id = _clean_required(
            payload.get("notification_id", payload.get("id")), "notification_id"
        )
        raw_text = text if text is not None else _first_value(
            payload, ("text", "message", "content", "prompt")
        )
        clean_text = _clean_text(raw_text)
        clean_conversation_id = _clean_required(
            conversation_id
            or payload.get("conversation_id")
            or payload.get("session_id")
            or sender_id
            or payload.get("sender_id")
            or "default",
            "conversation_id",
        )
        clean_sender_id = _clean_text(
            sender_id if sender_id is not None else payload.get("sender_id")
        )
        warning_count = _warning_count(payload)
        explicit_recontact = (
            recontact
            if recontact is not None
            else bool(payload.get("recontact") or payload.get("is_recontact"))
        )
        raw_metadata = metadata if metadata is not None else payload.get("metadata")
        safe_metadata = _safe_metadata(raw_metadata)
        safe_metadata = {**safe_metadata, "sender_id": clean_sender_id}

        async with self._lock:
            existing = self._notifications.get(notification_id)
            if existing is not None:
                future = existing.future
                if existing.result is not None:
                    return _duplicate_result(existing.result)
                duplicate_future = future
            else:
                loop = asyncio.get_running_loop()
                record = _NotificationRecord(loop.create_future())
                self._notifications[notification_id] = record
                self._trim_notifications()
                duplicate_future = None

            if duplicate_future is not None:
                # Waiting for the original result preserves idempotency even if
                # a duplicate arrives while the first response is in flight.
                pass
            else:
                record = self._notifications[notification_id]

        if duplicate_future is not None:
            result = await duplicate_future
            return _duplicate_result(result)

        try:
            result = await self._process(
                notification_id,
                clean_conversation_id,
                clean_text,
                warning_count,
                clean_sender_id,
                bool(explicit_recontact),
                safe_metadata,
            )
        except asyncio.CancelledError:
            async with self._lock:
                record = self._notifications.get(notification_id)
                if record is not None and not record.future.done():
                    record.future.cancel()
            raise
        except Exception as exc:  # defensive boundary: one notification must settle
            result = SessionResult(
                notification_id=notification_id,
                conversation_id=clean_conversation_id,
                status="error",
                warnings_consumed=warning_count,
                error=type(exc).__name__,
            )

        async with self._lock:
            record = self._notifications.get(notification_id)
            if record is not None:
                record.result = result
                if not record.future.done():
                    record.future.set_result(result)
                self._trim_notifications()
        return result

    async def _process(
        self,
        notification_id: str,
        conversation_id: str,
        text: str,
        warning_count: int,
        sender_id: str,
        recontact: bool,
        metadata: Mapping[str, Any],
    ) -> SessionResult:
        now = self._clock()
        contact_key = sender_id or conversation_id
        if recontact and self.config.recontact_cooldown_seconds > 0:
            async with self._lock:
                last = self._last_recontact.get(contact_key)
            if last is not None:
                cooldown_until = last + self.config.recontact_cooldown_seconds
                if now < cooldown_until:
                    return SessionResult(
                        notification_id=notification_id,
                        conversation_id=conversation_id,
                        status="cooldown",
                        warnings_consumed=warning_count,
                        cooldown_until=cooldown_until,
                    )

        if not text:
            return SessionResult(
                notification_id=notification_id,
                conversation_id=conversation_id,
                status="warning_consumed" if warning_count else "no_input",
                warnings_consumed=warning_count,
            )

        async with self._lock:
            history = tuple(self._histories.get(conversation_id, ()))
        request = ResponseRequest(
            notification_id=notification_id,
            conversation_id=conversation_id,
            text=text,
            history=history,
            warnings_consumed=warning_count,
            metadata=metadata,
        )
        response, attempts, timed_out, error = await self._respond(request)
        if response is None:
            return SessionResult(
                notification_id=notification_id,
                conversation_id=conversation_id,
                status="timeout" if timed_out else "error",
                attempts=attempts,
                timed_out=timed_out,
                warnings_consumed=warning_count,
                error=error,
            )

        created_at = self._clock()
        user_turn = ConversationTurn("user", text, notification_id, created_at)
        assistant_turn = ConversationTurn("assistant", response, notification_id, created_at)
        async with self._lock:
            turns = self._histories.setdefault(
                conversation_id, deque(maxlen=self.config.max_history_turns * 2)
            )
            turns.extend((user_turn, assistant_turn))
            if recontact:
                self._last_recontact[contact_key] = created_at
        return SessionResult(
            notification_id=notification_id,
            conversation_id=conversation_id,
            status="responded",
            response=response,
            attempts=attempts,
            warnings_consumed=warning_count,
        )

    async def _respond(
        self, request: ResponseRequest
    ) -> tuple[str | None, int, bool, str | None]:
        attempts = 0
        timed_out = False
        last_error: str | None = None
        total_attempts = self.config.max_retries + 1
        for attempt in range(total_attempts):
            attempts += 1
            try:
                value = await asyncio.wait_for(
                    self._invoke_responder_bounded(request),
                    timeout=self.config.response_timeout_seconds,
                )
                response = _normalize_response(value, self.config.max_response_chars)
                if not response:
                    raise ValueError("empty response")
                return response, attempts, False, None
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                timed_out = True
                last_error = "response_timeout"
            except Exception as exc:  # provider failures are bounded and reported
                timed_out = False
                last_error = type(exc).__name__
            if attempt + 1 < total_attempts and self.config.retry_backoff_seconds:
                delay = self.config.retry_backoff_seconds * (2**attempt)
                await self._sleep(delay)
        return None, attempts, timed_out, last_error

    async def _invoke_responder_bounded(self, request: ResponseRequest) -> Any:
        """Run sync providers off-loop so the timeout also covers blocking code."""

        responder_call = getattr(self.responder, "__call__", self.responder)
        if inspect.iscoroutinefunction(responder_call):
            return await _maybe_await(self._invoke_responder(request))
        value = await asyncio.to_thread(self._invoke_responder, request)
        return await _maybe_await(value)

    def _invoke_responder(self, request: ResponseRequest) -> Any:
        """Invoke supported provider shapes without forwarding warning text."""

        try:
            signature = inspect.signature(self.responder)
        except (TypeError, ValueError):
            return self.responder(request)
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        if has_varargs or len(positional) <= 1:
            return self.responder(request)
        if len(positional) == 2:
            return self.responder(request.text, request.history)
        return self.responder(request.text, request.history, request.metadata)

    def history(self, conversation_id: str = "default") -> tuple[ConversationTurn, ...]:
        """Return a snapshot of clean local history for one conversation."""

        return tuple(self._histories.get(str(conversation_id), ()))

    def can_recontact(self, contact_id: str, *, now: float | None = None) -> bool:
        """Return whether a cooldown-gated recontact is currently allowed."""

        if self.config.recontact_cooldown_seconds == 0:
            return True
        last = self._last_recontact.get(_clean_required(contact_id, "contact_id"))
        return last is None or (now if now is not None else self._clock()) >= (
            last + self.config.recontact_cooldown_seconds
        )

    def reset(self, conversation_id: str | None = None) -> None:
        """Forget local conversation state; notification idempotency is retained."""

        if conversation_id is None:
            self._histories.clear()
            self._last_recontact.clear()
            return
        key = str(conversation_id)
        self._histories.pop(key, None)
        self._last_recontact.pop(key, None)

    def _trim_notifications(self) -> None:
        while len(self._notifications) > self.config.max_notifications:
            key, record = next(iter(self._notifications.items()))
            if not record.future.done():
                break
            self._notifications.pop(key, None)


def _as_mapping(notification: Mapping[str, Any] | str) -> Mapping[str, Any]:
    if isinstance(notification, Mapping):
        return notification
    if isinstance(notification, str):
        return {"notification_id": notification}
    raise NotificationValidationError("notification must be a mapping or notification_id string")


def _clean_required(value: Any, name: str) -> str:
    clean = _clean_text(value)
    if not clean:
        raise NotificationValidationError(f"{name} is required")
    return clean


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_value(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return ""


def _warning_count(payload: Mapping[str, Any]) -> int:
    count = 0
    for key in ("skill_warning", "skill_warnings", "warnings"):
        value = payload.get(key)
        if value is None or value is False:
            continue
        if isinstance(value, (list, tuple, set)):
            count += len(value)
        else:
            count += 1
    return count


def _safe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    # Metadata is useful for local routing but must remain small and scalar.
    safe: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = _clean_text(key)
        if not clean_key or "warning" in clean_key.casefold():
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[clean_key[:80]] = item
    return safe


def _normalize_response(value: Any, max_chars: int) -> str:
    if isinstance(value, Mapping):
        value = value.get("response", value.get("text", ""))
    if not isinstance(value, str):
        raise TypeError("response provider must return text or a mapping with text")
    return value.strip()[:max_chars]


def _duplicate_result(result: SessionResult) -> SessionResult:
    return SessionResult(
        notification_id=result.notification_id,
        conversation_id=result.conversation_id,
        status="duplicate",
        response=result.response,
        attempts=result.attempts,
        duplicate=True,
        timed_out=result.timed_out,
        warnings_consumed=result.warnings_consumed,
        cooldown_until=result.cooldown_until,
        error=result.error,
    )


def _require_finite_nonnegative(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KarakuriSessionError(f"{name} must be a finite non-negative number")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise KarakuriSessionError(f"{name} must be a finite non-negative number")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "ConversationTurn",
    "KarakuriSession",
    "KarakuriSessionConfig",
    "KarakuriSessionError",
    "NotificationValidationError",
    "ResponseRequest",
    "SessionResult",
]
