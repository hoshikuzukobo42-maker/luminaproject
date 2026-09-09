from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

import httpx

from .llm import LLMResult, ProviderError

_THINK_BLOCK_RE = re.compile(
    r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?\b[^>]*>", re.IGNORECASE)

# Task-kind → thinking token budget. Unsupported server params are omitted safely.
THINKING_BUDGETS: dict[str, int] = {
    "chat": 0,
    "conversation": 0,
    "json_action": 256,
    "memory": 512,
    "memory_consolidation": 512,
    "planning": 1024,
    "complex_planning": 1024,
}

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def assert_local_llm_url(base_url: str) -> str:
    """Reject any non-loopback LLM endpoint. Never auto-select external APIs."""
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        raise ProviderError("MLX base_url is empty")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError(f"MLX base_url must be http(s): {base_url}")
    host = (parsed.hostname or "").casefold()
    if host not in _LOCAL_HOSTS:
        raise ProviderError(
            f"MLX provider refuses non-localhost endpoint host={parsed.hostname!r}; "
            "external/paid LLM auto-selection is disabled"
        )
    return normalized


def strip_thinking_text(text: str) -> tuple[str, str]:
    """Separate or remove model thinking blocks from assistant content."""
    raw = str(text or "")
    if not raw.strip():
        return "", ""
    thinking_chunks: list[str] = []
    for match in _THINK_BLOCK_RE.finditer(raw):
        thinking_chunks.append(match.group(0))
    cleaned = _THINK_BLOCK_RE.sub("", raw)
    # Drop a dangling unclosed thinking prefix if the model truncated mid-think.
    open_match = _THINK_OPEN_RE.search(cleaned)
    if open_match is not None:
        thinking_chunks.append(cleaned[open_match.start() :])
        cleaned = cleaned[: open_match.start()]
    return cleaned.strip(), "\n".join(thinking_chunks).strip()


def resolve_thinking_budget(task_kind: str | None, explicit: int | None = None) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    key = str(task_kind or "chat").strip().lower() or "chat"
    return int(THINKING_BUDGETS.get(key, 0))


class MlxCompatibleProvider:
    """Local MLX OpenAI-compatible HTTP provider (127.0.0.1 only)."""

    def __init__(
        self,
        base_url: str,
        primary_model: str,
        timeout_seconds: float = 120.0,
        num_predict: int = 384,
        temperature: float = 0.78,
        top_p: float = 0.92,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        adapter_path: str | None = None,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = assert_local_llm_url(base_url)
        self.primary_model = str(primary_model or "").strip()
        if not self.primary_model:
            raise ProviderError("MLX primary_model is required")
        self.timeout_seconds = float(timeout_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.num_predict = int(num_predict)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.top_k = int(top_k) if top_k is not None else None
        self.repeat_penalty = float(repeat_penalty) if repeat_penalty is not None else None
        self.adapter_path = str(adapter_path).strip() if adapter_path else None
        self._lock = asyncio.Lock()
        self._optional_keys_supported: dict[str, bool] = {}

    def _headers(self) -> dict[str, str]:
        # Intentionally no Authorization / API key.
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        assert_local_llm_url(self.base_url)
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        wait = float(self.timeout_seconds if timeout is None else timeout)
        try:
            with urllib.request.urlopen(request, timeout=wait) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                detail = str(exc)
            raise ProviderError(
                f"mlx {method} {path} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(f"mlx {method} {path} timed out after {wait:.1f}s") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ProviderError(f"mlx {method} {path} failed: {exc}") from exc
        if not raw.strip():
            raise ProviderError(f"mlx {path} returned an empty body")
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"mlx {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError(f"mlx {path} returned a non-object JSON payload")
        return value

    def _chat_health_probe(self) -> dict[str, Any]:
        payload = {
            "model": self.primary_model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "max_tokens": 1,
            "temperature": 0.0,
        }
        if self.adapter_path:
            payload["adapter_path"] = self.adapter_path
        response = self._request_json(
            "POST",
            "/chat/completions",
            payload,
            timeout=min(20.0, self.timeout_seconds),
        )
        return response

    async def health(self) -> dict[str, Any]:
        base = {
            "provider": "mlx",
            "base_url": self.base_url,
            "primary_model": self.primary_model,
            "adapter_path": self.adapter_path,
            "external_llm_enabled": False,
        }
        models: list[str] = []
        models_error: str | None = None
        try:
            payload = await asyncio.to_thread(self._request_json, "GET", "/models", None, 5.0)
            rows = payload.get("data")
            if isinstance(rows, list):
                models = [
                    str(item.get("id"))
                    for item in rows
                    if isinstance(item, dict) and item.get("id")
                ]
        except ProviderError as exc:
            models_error = str(exc)

        if models:
            return {
                **base,
                "ok": True,
                "health_via": "models",
                "primary_available": self.primary_model in models or not models,
                "models": models,
            }

        # /v1/models unsupported or empty → fall back to a tiny chat probe.
        try:
            await asyncio.to_thread(self._chat_health_probe)
        except ProviderError as exc:
            return {
                **base,
                "ok": False,
                "health_via": "chat",
                "models": models,
                "models_error": models_error,
                "error": str(exc),
            }
        return {
            **base,
            "ok": True,
            "health_via": "chat",
            "primary_available": True,
            "models": models,
            "models_error": models_error,
        }

    def _thinking_payload(self, budget: int) -> dict[str, Any]:
        """Attach thinking controls only in a conservative, optional form."""
        if budget <= 0:
            # Prefer explicit off when servers understand these keys; otherwise ignored.
            return {
                "enable_thinking": False,
                "thinking_budget": 0,
            }
        return {
            "enable_thinking": True,
            "thinking_budget": int(budget),
        }

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        max_tokens: int | None,
        task_kind: str | None,
        thinking_budget: int | None,
        include_optional: bool,
        include_thinking_keys: bool,
        include_adapter: bool,
    ) -> dict[str, Any]:
        budget = resolve_thinking_budget(task_kind, thinking_budget)
        payload: dict[str, Any] = {
            "model": self.primary_model,
            "messages": messages,
            "stream": bool(stream),
            "max_tokens": int(max_tokens or self.num_predict),
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if include_thinking_keys:
            payload.update(self._thinking_payload(budget))
        if include_adapter and self.adapter_path:
            payload["adapter_path"] = self.adapter_path
        if include_optional:
            if self.top_k is not None and self._optional_keys_supported.get("top_k", True):
                payload["top_k"] = self.top_k
            if self.repeat_penalty is not None and self._optional_keys_supported.get(
                "repeat_penalty", True
            ):
                payload["repeat_penalty"] = self.repeat_penalty
        return payload

    def _extract_message_content(self, response: dict[str, Any]) -> tuple[str, str]:
        choices = response.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(first, dict):
            raise ProviderError("mlx returned no choices")
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        text = message.get("content") if isinstance(message, dict) else None
        # Some servers put final text in `text` or reasoning separately.
        if not isinstance(text, str):
            text = first.get("text") if isinstance(first.get("text"), str) else ""
        reasoning = ""
        if isinstance(message, dict):
            for key in ("reasoning", "reasoning_content", "thinking"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    reasoning = value.strip()
                    break
        cleaned, embedded = strip_thinking_text(str(text or ""))
        if not cleaned:
            raise ProviderError("mlx returned an empty assistant response")
        return cleaned, reasoning or embedded

    def _usage_dict(self, response: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {}
        usage = response.get("usage")
        return usage if isinstance(usage, dict) else {}

    def _should_drop_optional(self, error: ProviderError) -> bool:
        detail = str(error).lower()
        return any(
            token in detail
            for token in (
                "top_k",
                "repeat_penalty",
                "adapter_path",
                "enable_thinking",
                "thinking_budget",
                "unexpected keyword",
                "unknown field",
                "extra inputs",
                "400",
                "422",
            )
        )

    def _chat_sync(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        task_kind: str | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        include_optional = True
        include_thinking = True
        include_adapter = bool(self.adapter_path)
        last_error: ProviderError | None = None
        for _attempt in range(4):
            payload = self._build_payload(
                messages,
                stream=False,
                max_tokens=max_tokens,
                task_kind=task_kind,
                thinking_budget=thinking_budget,
                include_optional=include_optional,
                include_thinking_keys=include_thinking,
                include_adapter=include_adapter,
            )
            try:
                response = self._request_json(
                    "POST",
                    "/chat/completions",
                    payload,
                    timeout=self.timeout_seconds,
                )
            except ProviderError as exc:
                last_error = exc
                if include_optional and self._should_drop_optional(exc):
                    include_optional = False
                    self._optional_keys_supported["top_k"] = False
                    self._optional_keys_supported["repeat_penalty"] = False
                    continue
                if include_adapter and "adapter" in str(exc).lower():
                    include_adapter = False
                    continue
                if include_thinking and self._should_drop_optional(exc):
                    include_thinking = False
                    continue
                raise
            text, thinking = self._extract_message_content(response)
            usage = self._usage_dict(response)
            return LLMResult(
                text=text,
                model=str(response.get("model") or self.primary_model),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                attempted_models=(self.primary_model,),
                fallback_errors=(),
                thinking=thinking or None,
                usage=usage or None,
            )
        raise last_error or ProviderError("mlx chat failed")

    async def chat(
        self,
        messages: list[dict[str, str]],
        num_ctx: int | None = None,
        max_tokens: int | None = None,
        task_kind: str | None = None,
        thinking_budget: int | None = None,
        stream: bool = True,
    ) -> LLMResult:
        # num_ctx is accepted for orchestrator compatibility; MLX server owns context.
        _ = num_ctx
        async with self._lock:
            if not stream:
                return await asyncio.to_thread(
                    self._chat_sync,
                    messages,
                    max_tokens,
                    task_kind,
                    thinking_budget,
                )
            return await self._chat_stream(
                messages,
                max_tokens=max_tokens,
                task_kind=task_kind,
                thinking_budget=thinking_budget,
            )

    async def _chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None,
        task_kind: str | None,
        thinking_budget: int | None,
    ) -> LLMResult:
        started = time.perf_counter()
        include_optional = True
        include_thinking = True
        include_adapter = bool(self.adapter_path)
        last_error: ProviderError | None = None
        for _attempt in range(4):
            payload = self._build_payload(
                messages,
                stream=True,
                max_tokens=max_tokens,
                task_kind=task_kind,
                thinking_budget=thinking_budget,
                include_optional=include_optional,
                include_thinking_keys=include_thinking,
                include_adapter=include_adapter,
            )
            chunks: list[str] = []
            usage: dict[str, Any] = {}
            try:
                assert_local_llm_url(self.base_url)
                timeout = httpx.Timeout(
                    self.timeout_seconds,
                    connect=self.connect_timeout_seconds,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        self.base_url + "/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    ) as response:
                        if response.status_code >= 400:
                            detail = (await response.aread()).decode("utf-8", errors="replace")[
                                :1000
                            ]
                            raise ProviderError(
                                f"mlx POST /chat/completions failed: HTTP {response.status_code}: {detail}"
                            )
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except ValueError as exc:
                                raise ProviderError(
                                    f"mlx stream returned invalid JSON: {raw[:200]}"
                                ) from exc
                            if not isinstance(event, dict):
                                continue
                            maybe_usage = event.get("usage")
                            if isinstance(maybe_usage, dict):
                                usage = maybe_usage
                            choices = event.get("choices")
                            first = choices[0] if isinstance(choices, list) and choices else None
                            if not isinstance(first, dict):
                                continue
                            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                            content = delta.get("content") if isinstance(delta, dict) else None
                            if isinstance(content, str) and content:
                                chunks.append(content)
                            # Non-delta fallbacks some servers emit mid-stream.
                            message = first.get("message") if isinstance(first.get("message"), dict) else {}
                            msg_content = message.get("content") if isinstance(message, dict) else None
                            if isinstance(msg_content, str) and msg_content and not chunks:
                                chunks.append(msg_content)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    f"mlx streaming timed out after {self.timeout_seconds:.1f}s"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = ProviderError(f"mlx streaming request failed: {exc}")
                if include_optional and self._should_drop_optional(last_error):
                    include_optional = False
                    self._optional_keys_supported["top_k"] = False
                    self._optional_keys_supported["repeat_penalty"] = False
                    continue
                if include_adapter and "adapter" in str(last_error).lower():
                    include_adapter = False
                    continue
                if include_thinking and self._should_drop_optional(last_error):
                    include_thinking = False
                    continue
                raise last_error from exc
            except ProviderError as exc:
                last_error = exc
                if include_optional and self._should_drop_optional(exc):
                    include_optional = False
                    continue
                if include_adapter and "adapter" in str(exc).lower():
                    include_adapter = False
                    continue
                if include_thinking and self._should_drop_optional(exc):
                    include_thinking = False
                    continue
                raise

            text, thinking = strip_thinking_text("".join(chunks))
            if not text:
                raise ProviderError("mlx returned an empty assistant response")
            return LLMResult(
                text=text,
                model=self.primary_model,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                attempted_models=(self.primary_model,),
                thinking=thinking or None,
                usage=usage or None,
            )
        raise last_error or ProviderError("mlx streaming chat failed")

    async def unload(self, model: str | None = None) -> None:
        _ = model
        return None
