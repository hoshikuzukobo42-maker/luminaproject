from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

import httpx

from .llm import LLMResult, ProviderError
from ..visual_phase import inference_lease


class OpenAICompatibleProvider:
    """Local llama.cpp OpenAI-compatible HTTP provider."""

    def __init__(
        self,
        base_url: str,
        primary_model: str,
        timeout_seconds: float = 120.0,
        num_predict: int = 700,
        temperature: float = 0.78,
        top_p: float = 0.92,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model
        self.timeout_seconds = float(timeout_seconds)
        self.num_predict = int(num_predict)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.top_k = int(top_k) if top_k is not None else None
        self.repeat_penalty = float(repeat_penalty) if repeat_penalty is not None else None
        self._lock = asyncio.Lock()

    def _uses_companion_contract(self) -> bool:
        return (
            self.primary_model == "qwen3.5-4b-q4_K_M"
            or self.primary_model.startswith("way-sft-plamo-3-8b")
        )

    def _apply_local_stop_sequences(self, payload: dict[str, Any]) -> None:
        if self._uses_companion_contract():
            stops = [
                "\nHuman:",
                "\nUser:",
                "\nAssistant:",
                "\nユーザー:",
                "\nアシスタント:",
            ]
            # PLaMo uses a model-specific textual EOT. Qwen's native end token
            # remains owned by its embedded llama.cpp chat template.
            if self.primary_model.startswith("way-sft-plamo-3-8b"):
                stops.append("<|plamo:eos|>")
            payload["stop"] = stops

    def _sanitize_local_text(self, text: str) -> str:
        clean = str(text or "").strip()
        if not self._uses_companion_contract():
            return clean
        clean = re.sub(
            r"^(?:Assistant|アシスタント|ルミナ|Lumina|私|わたし)\s*[:：]\s*",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        markers = [
            "\nHuman:", "\nUser:", "\nAssistant:", "\nユーザー:", "\nアシスタント:",
            "Human:", "User:", "Assistant:", "ユーザー:", "アシスタント:", "<|im_end|>",
        ]
        if self.primary_model.startswith("way-sft-plamo-3-8b"):
            markers.append("<|plamo:eos|>")
        for marker in markers:
            if marker in clean:
                clean = clean.split(marker, 1)[0].rstrip()
        return clean

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise ProviderError(f"llama.cpp {method} {path} failed: {exc}") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"llama.cpp {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError(f"llama.cpp {path} returned a non-object JSON payload")
        return value

    async def health(self) -> dict[str, Any]:
        import os
        if os.environ.get("LUMINA_VISUAL_PHASE_SERIALIZATION", "0") == "1":
            # The serialized profile uses only the explicitly non-waking
            # health/props contract, including the actual configured alias.
            from urllib.parse import urlsplit
            try:
                parsed = urlsplit(self.base_url)
                if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.port != 11436 or parsed.username or parsed.password
                    or parsed.path not in {"", "/v1"} or parsed.query or parsed.fragment):
                    raise ProviderError("serialized health requires the dedicated loopback llama.cpp endpoint")
                origin = self.base_url.removesuffix("/v1")
                async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                    live_response = await client.get(origin + "/health")
                    live_response.raise_for_status()
                    live = live_response.json()
                    props_response = await client.get(origin + "/props")
                    props_response.raise_for_status()
                    props = props_response.json()
                if not isinstance(live, dict) or live.get("status") != "ok" or not isinstance(props, dict):
                    raise ProviderError("invalid non-waking llama.cpp health/props response")
                alias, sleeping = props.get("model_alias"), props.get("is_sleeping")
                if not isinstance(alias, str) or alias != self.primary_model or type(sleeping) is not bool:
                    raise ProviderError("llama.cpp alias/sleep status unavailable or mismatched")
                return {"ok": True, "provider": "llama_cpp", "base_url": self.base_url,
                        "primary_model": self.primary_model, "primary_available": True,
                        "models": [alias], "sleeping": sleeping, "health_probe": "non_waking_health_props"}
            except (ProviderError, httpx.HTTPError, ValueError, OSError) as exc:
                return {"ok": False, "provider": "llama_cpp", "base_url": self.base_url,
                        "primary_model": self.primary_model, "primary_available": False,
                        "models": [], "error": str(exc), "health_probe": "non_waking_health_props"}
        try:
            payload = await asyncio.to_thread(self._request_json, "GET", "/models", None, 5.0)
            rows = payload.get("data")
            models = [
                str(item.get("id"))
                for item in rows
                if isinstance(item, dict) and item.get("id")
            ] if isinstance(rows, list) else []
        except ProviderError as exc:
            return {
                "ok": False,
                "provider": "llama_cpp",
                "base_url": self.base_url,
                "primary_model": self.primary_model,
                "models": [],
                "error": str(exc),
            }
        return {
            "ok": True,
            "provider": "llama_cpp",
            "base_url": self.base_url,
            "primary_model": self.primary_model,
            "primary_available": self.primary_model in models,
            "models": models,
        }

    def _nonstream_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.primary_model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.num_predict,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "cache_prompt": True,
        }
        if self.top_k is not None:
            payload["top_k"] = self.top_k
        if self.repeat_penalty is not None:
            payload["repeat_penalty"] = self.repeat_penalty
        self._apply_local_stop_sequences(payload)
        return payload

    def _chat_sync(self, messages: list[dict[str, str]]) -> LLMResult:
        started = time.perf_counter()
        response = self._request_json(
            "POST",
            "/chat/completions",
            self._nonstream_payload(messages),
            timeout=self.timeout_seconds,
        )
        return self._nonstream_result(response, started)

    async def _chat_async(self, messages: list[dict[str, str]]) -> LLMResult:
        # Never dispatch through to_thread here: cancellation cannot stop a
        # native worker from sending a delayed request after the phase unlocks.
        started = time.perf_counter()
        client = httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False)
        try:
            try:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    json=self._nonstream_payload(messages),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            finally:
                # HTTP cleanup must finish while chat still owns its phase.
                # A second cancel must not interrupt that cleanup and release
                # the lease around an open transport.
                close_task = asyncio.create_task(client.aclose())
                cancelled = False
                while not close_task.done():
                    try:
                        await asyncio.shield(close_task)
                    except asyncio.CancelledError:
                        if close_task.cancelled():
                            raise
                        cancelled = True
                close_task.result()
                if cancelled:
                    raise asyncio.CancelledError
        except httpx.HTTPError as exc:
            raise ProviderError(f"llama.cpp POST /chat/completions failed: {exc}") from exc
        try:
            value = response.json()
        except ValueError as exc:
            raise ProviderError("llama.cpp /chat/completions returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("llama.cpp /chat/completions returned a non-object JSON payload")
        return self._nonstream_result(value, started)

    def _nonstream_result(self, response: dict[str, Any], started: float) -> LLMResult:
        choices = response.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("llama.cpp returned no assistant content")
        return LLMResult(
            text=self._sanitize_local_text(text),
            model=self.primary_model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            attempted_models=(self.primary_model,),
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        num_ctx: int | None = None,
        max_tokens: int | None = None,
        task_kind: str | None = None,
        thinking_budget: int | None = None,
        stream: bool = True,
    ) -> LLMResult:
        # Context size is fixed at llama-server startup; num_ctx / thinking_* are
        # accepted to keep the provider contract compatible with MLX / Ollama paths.
        _ = (task_kind, thinking_budget)
        async with self._lock, inference_lease():
            if not stream:
                return await self._chat_async(messages)
            started = time.perf_counter()
            payload = {
                "model": self.primary_model,
                "messages": messages,
                "stream": True,
                "max_tokens": int(max_tokens or self.num_predict),
                "temperature": self.temperature,
                "top_p": self.top_p,
                "cache_prompt": True,
            }
            if self.top_k is not None:
                payload["top_k"] = self.top_k
            if self.repeat_penalty is not None:
                payload["repeat_penalty"] = self.repeat_penalty
            self._apply_local_stop_sequences(payload)
            chunks: list[str] = []
            first_content_at: float | None = None
            finish_reason: str | None = None
            try:
                timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    async with client.stream(
                        "POST",
                        self.base_url + "/chat/completions",
                        json=payload,
                    ) as response:
                        if response.status_code >= 400:
                            detail = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                            raise ProviderError(
                                f"llama.cpp POST /chat/completions failed: HTTP {response.status_code}: {detail}"
                            )
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except ValueError:
                                continue
                            choices = event.get("choices") if isinstance(event, dict) else None
                            first = choices[0] if isinstance(choices, list) and choices else None
                            event_finish = first.get("finish_reason") if isinstance(first, dict) else None
                            if event_finish is not None:
                                finish_reason = str(event_finish)
                            delta = first.get("delta") if isinstance(first, dict) else None
                            content = delta.get("content") if isinstance(delta, dict) else None
                            if isinstance(content, str) and content:
                                if first_content_at is None:
                                    first_content_at = time.perf_counter()
                                chunks.append(content)
            except httpx.HTTPError as exc:
                raise ProviderError(f"llama.cpp streaming request failed: {exc}") from exc
            text = self._sanitize_local_text("".join(chunks))
            if not text:
                raise ProviderError("llama.cpp returned no assistant content")
            return LLMResult(
                text=text,
                model=self.primary_model,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                attempted_models=(self.primary_model,),
                first_token_latency_ms=(
                    (first_content_at - started) * 1000.0
                    if first_content_at is not None
                    else None
                ),
                finish_reason=finish_reason,
            )

    async def unload(self, model: str | None = None) -> None:
        # Dedicated llama-server owns one model. Pressure shutdown is handled by
        # its supervisor, not by an undocumented inference endpoint.
        return None
