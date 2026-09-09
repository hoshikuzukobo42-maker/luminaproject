"""Bonsai Q1 provider: local OpenAI-compatible + output guard + self-repair."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from lumina_next.bonsai_output_guard import guard_and_repair
from lumina_next.providers.llm import LLMResult, ProviderError
from lumina_next.providers.openai_compatible import OpenAICompatibleProvider
from tools.bonsai_promotion_v2.client import LocalOpenAIClient, strip_thinking_blocks
from tools.bonsai_promotion_v2.safety import apply_safe_env, assert_localhost_url


class BonsaiQ1Provider:
    """Wraps local Bonsai with final-only guard and same-model self-repair."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11437/v1",
        primary_model: str = "bonsai-q1",
        timeout_seconds: float = 120.0,
        temperature: float = 0.3,
        top_p: float = 0.9,
        top_k: int = 20,
        enable_thinking: bool = False,
        enable_self_repair: bool = True,
        num_predict: int = 512,
    ) -> None:
        apply_safe_env(overwrite=False)
        self.base_url = assert_localhost_url(base_url.rstrip("/"), label="bonsai_q1_base_url")
        self.primary_model = primary_model
        self.timeout_seconds = float(timeout_seconds)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.top_k = int(top_k)
        self.enable_thinking = bool(enable_thinking)
        self.enable_self_repair = bool(enable_self_repair)
        self.num_predict = int(num_predict)
        self._inner = OpenAICompatibleProvider(
            base_url=self.base_url,
            primary_model=primary_model,
            timeout_seconds=timeout_seconds,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        self._client = LocalOpenAIClient(
            base_url=self.base_url,
            model=primary_model,
            timeout_s=timeout_seconds,
            thinking_enable=enable_thinking,
        )
        self._lock = asyncio.Lock()

    async def health(self) -> dict[str, Any]:
        inner = await self._inner.health()
        inner["provider"] = "bonsai_q1"
        inner["self_repair"] = self.enable_self_repair
        return inner

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        expect_json: bool = False,
        required_json_keys: list[str] | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        async with self._lock:
            t0 = time.perf_counter()
            try:
                result = await asyncio.to_thread(
                    self._client.chat,
                    messages,
                    temperature=float(kwargs.get("temperature", self.temperature)),
                    top_p=float(kwargs.get("top_p", self.top_p)),
                    top_k=int(kwargs.get("top_k", self.top_k)),
                    max_tokens=int(kwargs.get("max_tokens", self.num_predict)),
                    thinking_enable=bool(kwargs.get("enable_thinking", self.enable_thinking)),
                )
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"bonsai_q1 chat failed: {exc}") from exc

            if not result.get("ok"):
                raise ProviderError(result.get("error") or "bonsai_q1 chat failed")

            text = strip_thinking_blocks(result.get("text") or "")
            guarded = guard_and_repair(
                text,
                client=self._client if self.enable_self_repair else None,
                messages=messages if self.enable_self_repair else None,
                expect_json=expect_json,
                required_json_keys=required_json_keys,
                enable_repair=self.enable_self_repair,
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return LLMResult(
                text=str(guarded.get("text") or ""),
                model=self.primary_model,
                latency_ms=latency_ms,
                attempted_models=(self.primary_model,),
                usage={"guard_repaired": bool(guarded.get("repaired")), "guard_ok": bool(guarded.get("ok"))},
            )
