from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    latency_ms: float
    attempted_models: tuple[str, ...] = ()
    fallback_errors: tuple[str, ...] = ()
    thinking: str | None = None
    usage: dict[str, Any] | None = None
    first_token_latency_ms: float | None = None
    finish_reason: str | None = None


class OllamaProvider:
    """Ollama adapter kept behind a provider boundary for later llama.cpp swap."""

    def __init__(
        self,
        base_url: str,
        primary_model: str,
        fallback_models: tuple[str, ...],
        timeout_seconds: float = 120.0,
        num_ctx: int = 2048,
        num_predict: int = 256,
        temperature: float = 0.55,
        top_p: float = 0.85,
        keep_alive: str = "5m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model
        self.fallback_models = fallback_models
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = temperature
        self.top_p = top_p
        self.keep_alive = str(keep_alive or "5m")
        self._lock = asyncio.Lock()

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise ProviderError(f"ollama {method} {path} failed: {exc}") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"ollama {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError(f"ollama {path} returned a non-object JSON payload")
        return value

    def _tags(self) -> list[str]:
        payload = self._request_json("GET", "/api/tags", timeout=5.0)
        models = payload.get("models")
        if not isinstance(models, list):
            return []
        return [str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")]

    async def health(self) -> dict[str, Any]:
        try:
            models = await asyncio.to_thread(self._tags)
        except ProviderError as exc:
            return {
                "ok": False,
                "provider": "ollama",
                "base_url": self.base_url,
                "primary_model": self.primary_model,
                "models": [],
                "error": str(exc),
            }
        return {
            "ok": True,
            "provider": "ollama",
            "base_url": self.base_url,
            "primary_model": self.primary_model,
            "primary_available": self.primary_model in models,
            "models": models,
        }

    def _unload_sync(self, model: str) -> None:
        self._request_json(
            "POST",
            "/api/generate",
            {"model": model, "keep_alive": 0},
            timeout=30.0,
        )

    async def unload(self, model: str | None = None) -> None:
        target = str(model or self.primary_model).strip()
        if not target:
            return
        await asyncio.to_thread(self._unload_sync, target)

    def _chat_sync(
        self,
        model: str,
        messages: list[dict[str, str]],
        num_ctx: int | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "num_ctx": int(num_ctx or self.num_ctx),
                "num_predict": int(max_tokens or self.num_predict),
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }
        response = self._request_json("POST", "/api/chat", payload, timeout=self.timeout_seconds)
        message = response.get("message")
        text = message.get("content") if isinstance(message, dict) else response.get("response")
        if not isinstance(text, str) or not text.strip():
            raise ProviderError(f"ollama returned no assistant content for model={model}")
        return LLMResult(text=text.strip(), model=model, latency_ms=(time.perf_counter() - started) * 1000.0)

    async def chat(
        self,
        messages: list[dict[str, str]],
        num_ctx: int | None = None,
        max_tokens: int | None = None,
        task_kind: str | None = None,
        thinking_budget: int | None = None,
        stream: bool = False,
    ) -> LLMResult:
        _ = (task_kind, thinking_budget, stream)
        models: list[str] = []
        for model in (self.primary_model, *self.fallback_models):
            if model and model not in models:
                models.append(model)
        errors: list[str] = []
        attempted: list[str] = []
        async with self._lock:
            for model in models:
                attempted.append(model)
                try:
                    result = await asyncio.to_thread(
                        self._chat_sync,
                        model,
                        messages,
                        num_ctx,
                        max_tokens,
                    )
                    return LLMResult(
                        text=result.text,
                        model=result.model,
                        latency_ms=result.latency_ms,
                        attempted_models=tuple(attempted),
                        fallback_errors=tuple(errors),
                    )
                except ProviderError as exc:
                    errors.append(str(exc))
        raise ProviderError("; ".join(errors) or "no Ollama model configured")
