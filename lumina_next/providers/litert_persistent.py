from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from .llm import LLMResult, ProviderError


_PROVIDER = "litert_persistent"
_SIDECAR_SERVICE = "litert-gemma4-e4b-sidecar"
_LOOPBACK = "127.0.0.1"
_ALLOWED_TASK_KINDS = frozenset({"", "chat", "conversation"})
_THINK_MARKER_RE = re.compile(r"</?think(?:ing)?\b", re.IGNORECASE)
_ROLE_MARKER_RE = re.compile(
    r"(?:Human|User|Assistant|System|ユーザー|アシスタント)\s*[:：]",
    re.IGNORECASE,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not let a compromised loopback service redirect outside the host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class _Message:
    role: str
    content: str

    def wire(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class _WireResult:
    text: str
    latency_ms: float
    first_token_latency_ms: float | None
    finish_reason: str
    usage: dict[str, Any] | None


def _session_key(value: str) -> str:
    key = str(value or "")
    if not key.strip() or len(key) > 512 or any(ord(ch) < 32 for ch in key):
        raise ProviderError("litert_persistent requires a non-empty, printable session_key")
    return key


def _text_message(raw: Any, index: int) -> _Message:
    if not isinstance(raw, Mapping):
        raise ProviderError(f"litert_persistent message[{index}] must be an object")
    role = raw.get("role")
    content = raw.get("content")
    if role not in {"system", "user", "assistant"}:
        raise ProviderError(f"litert_persistent message[{index}] has an unsupported role")
    if not isinstance(content, str) or not content.strip():
        raise ProviderError(f"litert_persistent message[{index}] must contain non-empty text")
    return _Message(str(role), content)


def _parse_system_only(messages: Sequence[Mapping[str, str]]) -> str:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise ProviderError("litert_persistent messages must be a sequence")
    parsed = tuple(_text_message(raw, i) for i, raw in enumerate(messages))
    if len(parsed) != 1 or parsed[0].role != "system":
        raise ProviderError("litert_persistent prepare requires exactly one system message")
    return parsed[0].content


def _parse_chat(
    messages: Sequence[Mapping[str, str]],
) -> tuple[str, tuple[_Message, ...], _Message]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise ProviderError("litert_persistent messages must be a sequence")
    parsed = tuple(_text_message(raw, i) for i, raw in enumerate(messages))
    if len(parsed) < 2 or parsed[0].role != "system":
        raise ProviderError("litert_persistent chat requires a leading system message")
    if any(message.role == "system" for message in parsed[1:]):
        raise ProviderError("litert_persistent permits exactly one leading system message")
    conversation = parsed[1:]
    for index, message in enumerate(conversation):
        expected = "user" if index % 2 == 0 else "assistant"
        if message.role != expected:
            raise ProviderError(
                f"litert_persistent transcript must alternate user/assistant; "
                f"message[{index + 1}] expected {expected}"
            )
    if conversation[-1].role != "user":
        raise ProviderError("litert_persistent final message must be the new user turn")
    return parsed[0].content, conversation[:-1], conversation[-1]


def _strict_base_url(value: str) -> tuple[str, str, int]:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlsplit(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderError("litert_persistent base_url has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != _LOOPBACK
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
    ):
        raise ProviderError(
            "litert_persistent base_url must be exactly http://127.0.0.1:<port>/v1"
        )
    if not 1 <= port <= 65535:
        raise ProviderError("litert_persistent base_url port is out of range")
    return normalized, f"http://{_LOOPBACK}:{port}", port


def _assert_safe_assistant_text(text: str) -> str:
    if not text.strip():
        raise ProviderError("litert_persistent returned empty assistant content")
    if _THINK_MARKER_RE.search(text):
        raise ProviderError("litert_persistent rejected thinking-tag leakage")
    if _ROLE_MARKER_RE.search(text):
        raise ProviderError("litert_persistent rejected role-marker leakage")
    return text


class LiteRTPersistentProvider:
    """Fail-closed owner of the sidecar's single resident Conversation.

    ``chat_session`` deliberately requires a session key and the complete
    caller-visible transcript.  A missing/changed system prompt or transcript
    is rejected before inference, preventing silent KV-cache divergence.
    """

    def __init__(
        self,
        base_url: str,
        primary_model: str,
        timeout_seconds: float = 120.0,
        num_predict: int = 64,
        temperature: float = 0.7,
        top_p: float = 0.8,
        top_k: int = 20,
        seed: int = 20260809,
        repeat_penalty: float | None = None,
        connect_timeout_seconds: float = 5.0,
        prime_on_session_start: bool = True,
    ) -> None:
        self.base_url, self._root_url, self._port = _strict_base_url(base_url)
        self.primary_model = str(primary_model or "").strip()
        if not self.primary_model:
            raise ProviderError("litert_persistent primary_model is required")
        if timeout_seconds <= 0 or connect_timeout_seconds <= 0:
            raise ProviderError("litert_persistent timeouts must be positive")
        if num_predict <= 0 or top_k <= 0 or seed < 0:
            raise ProviderError("litert_persistent generation settings are invalid")
        if temperature < 0 or not 0 <= top_p <= 1:
            raise ProviderError("litert_persistent sampling settings are invalid")
        if repeat_penalty is not None and repeat_penalty < 1:
            raise ProviderError("litert_persistent repeat_penalty must be >= 1")
        self.timeout_seconds = float(timeout_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.num_predict = int(num_predict)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.top_k = int(top_k)
        self.seed = int(seed)
        self.repeat_penalty = (
            float(repeat_penalty) if repeat_penalty is not None else None
        )
        self.prime_on_session_start = bool(prime_on_session_start)
        self._lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None
        self._active_session_key: str | None = None
        self._stable_system: str | None = None
        self._visible_transcript: list[_Message] = []
        self._conversation_ready = False
        self._primed = False
        self._poisoned = False
        self._recovery_required = False
        self._remote_session_generation: int | None = None
        self._remote_completed_turns = 0
        self._context_generation = 0
        self._last_error: str | None = None

    @property
    def active_session_key(self) -> str | None:
        return self._active_session_key

    @property
    def stable_system_sha256(self) -> str | None:
        if self._stable_system is None:
            return None
        return hashlib.sha256(self._stable_system.encode("utf-8")).hexdigest()

    @property
    def visible_transcript(self) -> tuple[dict[str, str], ...]:
        return tuple(message.wire() for message in self._visible_transcript)

    def _url(self, path: str, *, root: bool = False) -> str:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ProviderError("litert_persistent refused an invalid local path")
        return (self._root_url if root else self.base_url) + path

    def _request_json_sync(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != _LOOPBACK or parsed.port != self._port:
            raise ProviderError("litert_persistent refused a non-sidecar request URL")
        body = None
        headers = {"Accept": "application/json", "Connection": "close"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        wait = self.timeout_seconds if timeout is None else float(timeout)
        try:
            with opener.open(request, timeout=wait) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                detail = str(exc)
            raise ProviderError(
                f"litert_persistent {method} {parsed.path} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                f"litert_persistent {method} {parsed.path} timed out after {wait:.1f}s"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ProviderError(
                f"litert_persistent {method} {parsed.path} failed: {exc}"
            ) from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(
                f"litert_persistent {parsed.path} returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ProviderError(
                f"litert_persistent {parsed.path} returned non-object JSON"
            )
        return value

    async def _request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request_json_sync, method, url, payload, timeout
        )

    def _generation_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.primary_model,
            "messages": messages,
            "max_tokens": self.num_predict,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "seed": self.seed,
            "n": 1,
            "reasoning_effort": "none",
            "enable_thinking": False,
            "thinking": False,
        }
        if self.repeat_penalty is not None:
            payload["repeat_penalty"] = self.repeat_penalty
        return payload

    def _validate_remote_health(self, value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "status": "ok",
            "service": _SIDECAR_SERVICE,
            "loopback_only": True,
            "offline": True,
            "model": self.primary_model,
            "engine_ready": True,
            "single_session": True,
            "thinking": False,
        }
        for key, expected in required.items():
            if value.get(key) != expected:
                raise ProviderError(
                    f"litert_persistent rejected sidecar health field {key}={value.get(key)!r}"
                )
        for key in ("conversation_ready", "primed", "poisoned", "busy"):
            if not isinstance(value.get(key), bool):
                raise ProviderError(f"litert_persistent sidecar health lacks boolean {key}")
        for key in ("session_generation", "completed_turns"):
            if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
                raise ProviderError(f"litert_persistent sidecar health lacks integer {key}")
        return value

    async def _identity_health_locked(self) -> dict[str, Any]:
        models = await self._request_json("GET", self._url("/models"), timeout=5.0)
        rows = models.get("data")
        aliases = [
            item.get("id")
            for item in rows
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ] if isinstance(rows, list) else []
        if aliases != [self.primary_model]:
            raise ProviderError(
                f"litert_persistent requires exactly alias {self.primary_model!r}; got {aliases!r}"
            )
        health = await self._request_json(
            "GET", self._url("/health", root=True), timeout=5.0
        )
        return self._validate_remote_health(health)

    def _remember_remote(self, health: dict[str, Any]) -> None:
        self._conversation_ready = bool(health["conversation_ready"])
        self._primed = bool(health["primed"])
        self._poisoned = bool(health["poisoned"])
        self._remote_session_generation = int(health["session_generation"])
        self._remote_completed_turns = int(health["completed_turns"])

    def _mark_poisoned(self, error: BaseException | str) -> None:
        self._poisoned = True
        self._recovery_required = True
        self._conversation_ready = False
        self._primed = False
        self._last_error = str(error)

    async def _reset_remote_locked(self) -> None:
        health = await self._identity_health_locked()
        # Cancelling the client stream does not guarantee that the native
        # generation callback has finished unwinding.  Resetting while the
        # sidecar still reports busy returns 409 and makes the first barge-in
        # utterance fail.  Wait briefly for that exact local engine to quiesce;
        # never retry or replace the model process.
        deadline = time.monotonic() + min(5.0, max(0.5, self.connect_timeout_seconds))
        while health["busy"]:
            if time.monotonic() >= deadline:
                raise ProviderError(
                    "litert_persistent sidecar remained busy during context reset"
                )
            await asyncio.sleep(0.05)
            value = await self._request_json(
                "GET", self._url("/health", root=True), timeout=1.0
            )
            health = self._validate_remote_health(value)
        value = await self._request_json(
            "POST",
            self._url("/lumina/session/reset"),
            {"confirm": True},
        )
        health = self._validate_remote_health(value)
        if health["conversation_ready"] or health["primed"] or health["poisoned"]:
            raise ProviderError("litert_persistent reset did not produce a clean sidecar session")
        self._remember_remote(health)
        self._context_generation += 1

    async def _prepare_remote_locked(self, *, prime: bool) -> None:
        if self._stable_system is None:
            raise ProviderError("litert_persistent has no stable system to prepare")
        payload = self._generation_payload(
            [{"role": "system", "content": self._stable_system}]
        )
        value = await self._request_json(
            "POST", self._url("/lumina/session/prepare"), payload
        )
        health = self._validate_remote_health(value)
        if not health["conversation_ready"] or health["poisoned"]:
            raise ProviderError("litert_persistent sidecar prepare did not become ready")
        self._remember_remote(health)
        if prime:
            value = await self._request_json(
                "POST", self._url("/lumina/session/prime"), payload
            )
            health = self._validate_remote_health(value)
            if not health["conversation_ready"] or not health["primed"] or health["poisoned"]:
                raise ProviderError("litert_persistent sidecar prime did not become ready")
            self._remember_remote(health)

    async def _adopt_context_locked(
        self,
        session_key: str,
        system: str,
        transcript: tuple[_Message, ...],
        *,
        prime: bool,
    ) -> None:
        try:
            await self._reset_remote_locked()
            self._active_session_key = session_key
            self._stable_system = system
            self._visible_transcript = list(transcript)
            self._recovery_required = False
            self._poisoned = False
            self._last_error = None
            if not transcript:
                await self._prepare_remote_locked(prime=prime)
        except asyncio.CancelledError:
            self._mark_poisoned("context activation cancelled")
            raise
        except Exception as exc:
            self._mark_poisoned(exc)
            raise

    async def prepare(
        self,
        session_key: str,
        messages: Sequence[Mapping[str, str]],
        *,
        prime: bool = True,
    ) -> dict[str, Any]:
        """Reset, prepare, and optionally prime an empty visible session."""
        key = _session_key(session_key)
        system = _parse_system_only(messages)
        async with self._lock:
            if (
                self._active_session_key == key
                and self._stable_system == system
                and not self._visible_transcript
                and not self._recovery_required
                and self._conversation_ready
            ):
                if prime and not self._primed:
                    await self._prepare_remote_locked(prime=True)
                return await self._health_locked()
            await self._adopt_context_locked(key, system, (), prime=prime)
            return await self._health_locked()

    async def reset_context(
        self,
        session_key: str,
        messages: Sequence[Mapping[str, str]],
        *,
        prime: bool = True,
    ) -> dict[str, Any]:
        """Explicitly discard visible history and rotate the native context."""
        key = _session_key(session_key)
        system = _parse_system_only(messages)
        async with self._lock:
            await self._adopt_context_locked(key, system, (), prime=prime)
            return await self._health_locked()

    async def _recover_locked(self) -> None:
        if not self._recovery_required:
            return
        if self._active_session_key is None or self._stable_system is None:
            raise ProviderError("litert_persistent cannot recover without an active session")
        # LiteRT's prepare endpoint cannot inject a non-empty role transcript.
        # Preserve exact visible history by leaving the clean sidecar unprepared;
        # the next chat request cold-hydrates that history atomically.  Empty
        # sessions retain the fast path and are prepared/primed immediately.
        await self._adopt_context_locked(
            self._active_session_key,
            self._stable_system,
            tuple(self._visible_transcript),
            prime=self.prime_on_session_start,
        )

    async def chat_session(
        self,
        messages: Sequence[Mapping[str, str]],
        session_key: str,
        num_ctx: int | None = None,
        max_tokens: int | None = None,
        task_kind: str | None = None,
        thinking_budget: int | None = None,
        stream: bool = True,
        context_reset: bool = False,
    ) -> LLMResult:
        """Generate one turn after exact session/system/history validation."""
        del num_ctx  # Engine context is fixed by the sidecar launcher.
        key = _session_key(session_key)
        system, history, user = _parse_chat(messages)
        kind = str(task_kind or "").strip().lower()
        if kind not in _ALLOWED_TASK_KINDS:
            raise ProviderError("litert_persistent supports conversation tasks only")
        if thinking_budget not in (None, 0):
            raise ProviderError("litert_persistent thinking is hard-disabled")
        requested_max = self.num_predict if max_tokens is None else int(max_tokens)
        if requested_max <= 0 or requested_max > self.num_predict:
            raise ProviderError(
                f"litert_persistent max_tokens must be in 1..{self.num_predict}"
            )

        async with self._lock:
            if self._active_session_key is None or self._active_session_key != key:
                await self._adopt_context_locked(
                    key,
                    system,
                    history,
                    prime=self.prime_on_session_start and not history,
                )
            elif self._stable_system != system:
                raise ProviderError(
                    "litert_persistent system_drift: use reset_context for a deliberate change"
                )
            elif context_reset:
                await self._adopt_context_locked(
                    key,
                    system,
                    history,
                    prime=self.prime_on_session_start and not history,
                )
            else:
                await self._recover_locked()

            if tuple(self._visible_transcript) != history:
                raise ProviderError(
                    "litert_persistent history_drift: caller-visible transcript does not "
                    "match the resident session; use context_reset deliberately"
                )

            payload = self._generation_payload(
                [
                    {"role": "system", "content": system},
                    *(message.wire() for message in history),
                    user.wire(),
                ]
            )
            payload["max_tokens"] = requested_max
            payload["stream"] = bool(stream)
            if stream:
                payload["stream_options"] = {"include_usage": True}

            current = asyncio.current_task()
            self._active_task = current
            try:
                wire = (
                    await self._stream_request(payload)
                    if stream
                    else await self._complete_request(payload)
                )
            except asyncio.CancelledError:
                self._mark_poisoned("active generation cancelled")
                raise
            except Exception as exc:
                self._mark_poisoned(exc)
                raise
            finally:
                if self._active_task is current:
                    self._active_task = None

            self._visible_transcript.extend((user, _Message("assistant", wire.text)))
            self._conversation_ready = True
            self._poisoned = False
            self._recovery_required = False
            self._remote_completed_turns += 1
            self._last_error = None
            return LLMResult(
                text=wire.text,
                model=self.primary_model,
                latency_ms=wire.latency_ms,
                attempted_models=(self.primary_model,),
                fallback_errors=(),
                usage=wire.usage,
                first_token_latency_ms=wire.first_token_latency_ms,
                finish_reason=wire.finish_reason,
            )

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResult:
        """Reject the legacy contract so a caller cannot omit session identity."""
        del args, kwargs
        raise ProviderError("litert_persistent requires chat_session(..., session_key=...)")

    async def _complete_request(self, payload: dict[str, Any]) -> _WireResult:
        started = time.perf_counter()
        response = await self._request_json(
            "POST", self._url("/chat/completions"), payload
        )
        if response.get("model") != self.primary_model:
            raise ProviderError("litert_persistent completion returned the wrong model alias")
        choices = response.get("choices")
        first = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        message = first.get("message") if isinstance(first, Mapping) else None
        text = message.get("content") if isinstance(message, Mapping) else None
        finish = first.get("finish_reason") if isinstance(first, Mapping) else None
        if not isinstance(text, str) or finish != "stop":
            raise ProviderError("litert_persistent completion was empty or incomplete")
        _assert_safe_assistant_text(text)
        if message.get("role") not in (None, "assistant"):
            raise ProviderError("litert_persistent completion returned an invalid role")
        metrics = response.get("lumina_metrics")
        ttft = metrics.get("ttft_ms") if isinstance(metrics, Mapping) else None
        usage = response.get("usage")
        return _WireResult(
            text=text,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            first_token_latency_ms=float(ttft) if isinstance(ttft, (int, float)) else None,
            finish_reason="stop",
            usage=dict(usage) if isinstance(usage, Mapping) else None,
        )

    async def _stream_request(self, payload: dict[str, Any]) -> _WireResult:
        started = time.perf_counter()
        first_content_at: float | None = None
        chunks: list[str] = []
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None
        saw_done = False
        timeout = httpx.Timeout(
            self.timeout_seconds, connect=self.connect_timeout_seconds
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            ) as client:
                async with client.stream(
                    "POST", self._url("/chat/completions"), json=payload
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(
                            "utf-8", errors="replace"
                        )[:600]
                        raise ProviderError(
                            f"litert_persistent stream failed: HTTP {response.status_code}: {detail}"
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("text/event-stream"):
                        raise ProviderError("litert_persistent stream returned the wrong content type")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        if raw == "[DONE]":
                            saw_done = True
                            break
                        try:
                            event = json.loads(raw)
                        except ValueError as exc:
                            raise ProviderError("litert_persistent stream returned invalid JSON") from exc
                        if not isinstance(event, Mapping):
                            raise ProviderError("litert_persistent stream returned a non-object event")
                        if event.get("error"):
                            raise ProviderError(
                                f"litert_persistent generation error: {event.get('error')!r}"
                            )
                        if event.get("model") != self.primary_model:
                            raise ProviderError("litert_persistent stream returned the wrong model alias")
                        maybe_usage = event.get("usage")
                        if isinstance(maybe_usage, Mapping):
                            usage = dict(maybe_usage)
                        choices = event.get("choices")
                        if not isinstance(choices, list):
                            raise ProviderError("litert_persistent stream event lacks choices")
                        if not choices:
                            continue
                        if len(choices) != 1 or not isinstance(choices[0], Mapping):
                            raise ProviderError("litert_persistent stream returned multiple choices")
                        first = choices[0]
                        event_finish = first.get("finish_reason")
                        if event_finish is not None:
                            if event_finish != "stop":
                                raise ProviderError("litert_persistent stream did not finish cleanly")
                            finish_reason = "stop"
                        delta = first.get("delta")
                        if not isinstance(delta, Mapping):
                            raise ProviderError("litert_persistent stream returned an invalid delta")
                        if any(key in delta for key in ("reasoning", "reasoning_content", "thinking", "channels")):
                            raise ProviderError("litert_persistent rejected leaked thinking content")
                        if delta.get("role") not in (None, "assistant"):
                            raise ProviderError("litert_persistent stream returned an invalid role")
                        content = delta.get("content")
                        if content is not None and not isinstance(content, str):
                            raise ProviderError("litert_persistent stream returned non-text content")
                        if isinstance(content, str) and content:
                            if first_content_at is None:
                                first_content_at = time.perf_counter()
                            chunks.append(content)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"litert_persistent stream timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"litert_persistent stream failed: {exc}") from exc

        text = "".join(chunks)
        if not saw_done or finish_reason != "stop":
            raise ProviderError("litert_persistent stream ended without a complete answer")
        _assert_safe_assistant_text(text)
        finished = time.perf_counter()
        return _WireResult(
            text=text,
            latency_ms=(finished - started) * 1000.0,
            first_token_latency_ms=(
                (first_content_at - started) * 1000.0
                if first_content_at is not None
                else None
            ),
            finish_reason="stop",
            usage=usage,
        )

    async def cancel(self) -> bool:
        """Cancel the active HTTP stream; the next call performs safe recovery."""
        task = self._active_task
        if task is None or task.done() or task is asyncio.current_task():
            return False
        self._mark_poisoned("active generation cancelled by provider")
        task.cancel()
        # Do not await the owner here.  Orchestrator cancellation may hold its
        # active-request lock while calling this method, while the owner's
        # finally block needs that same lock.  The task's normal unwind closes
        # the HTTP stream and chat_session marks the context for recovery.
        return True

    async def _health_locked(self) -> dict[str, Any]:
        remote = await self._identity_health_locked()
        alignment_errors: list[str] = []
        if self._conversation_ready != remote["conversation_ready"]:
            alignment_errors.append("conversation_ready_drift")
        if self._primed != remote["primed"]:
            alignment_errors.append("primed_drift")
        if remote["poisoned"]:
            alignment_errors.append("remote_poisoned")
        if (
            self._remote_session_generation is not None
            and remote["session_generation"] != self._remote_session_generation
        ):
            alignment_errors.append("session_generation_drift")
        if remote["completed_turns"] != self._remote_completed_turns:
            alignment_errors.append("completed_turns_drift")
        if alignment_errors:
            self._mark_poisoned(",".join(alignment_errors))
        return {
            "ok": not alignment_errors and not self._recovery_required,
            "provider": _PROVIDER,
            "base_url": self.base_url,
            "model": self.primary_model,
            "primary_model": self.primary_model,
            "primary_available": True,
            "engine_ready": True,
            "conversation_ready": bool(remote["conversation_ready"]),
            "primed": bool(remote["primed"]),
            "poisoned": bool(remote["poisoned"] or self._poisoned),
            "active_session_key": self._active_session_key,
            "stable_system_sha256": self.stable_system_sha256,
            "visible_messages": len(self._visible_transcript),
            "visible_turns": len(self._visible_transcript) // 2,
            "context_generation": self._context_generation,
            "remote_session_generation": int(remote["session_generation"]),
            "completed_turns": int(remote["completed_turns"]),
            "recovery_required": self._recovery_required,
            "last_error": self._last_error,
            "loopback_only": True,
            "external_llm_enabled": False,
            "proxy_env_trusted": False,
        }

    async def health(self) -> dict[str, Any]:
        async with self._lock:
            try:
                return await self._health_locked()
            except ProviderError as exc:
                self._mark_poisoned(exc)
                return {
                    "ok": False,
                    "provider": _PROVIDER,
                    "base_url": self.base_url,
                    "model": self.primary_model,
                    "primary_model": self.primary_model,
                    "primary_available": False,
                    "engine_ready": False,
                    "conversation_ready": False,
                    "primed": False,
                    "poisoned": True,
                    "active_session_key": self._active_session_key,
                    "stable_system_sha256": self.stable_system_sha256,
                    "visible_messages": len(self._visible_transcript),
                    "visible_turns": len(self._visible_transcript) // 2,
                    "context_generation": self._context_generation,
                    "remote_session_generation": self._remote_session_generation,
                    "completed_turns": self._remote_completed_turns,
                    "recovery_required": True,
                    "last_error": str(exc),
                    "loopback_only": True,
                    "external_llm_enabled": False,
                    "proxy_env_trusted": False,
                }

    async def unload(self, model: str | None = None) -> None:
        # Engine lifetime belongs to its PID-verified supervisor.
        if model not in (None, self.primary_model):
            raise ProviderError("litert_persistent refused to unload a different alias")


__all__ = ["LiteRTPersistentProvider"]
