"""Configurable resource policies for M1 16GB local inference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


_JA_CHAR_RE = re.compile(
    r"[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff]"
)
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


SAMPLE_POLICIES: dict[str, Mapping[str, object]] = {
    "default_m1_16gb": {
        "total_ram_bytes": 16 * 1024 * 1024 * 1024,
        "safety_margin_bytes": 2 * 1024 * 1024 * 1024,
        "browser_reserve_bytes": 1 * 1024 * 1024 * 1024,
        "max_context_tokens": 8192,
        "kv_bytes_per_token": 64,
    },
    "conservative_chat": {
        "max_context_tokens": 4096,
        "min_context_tokens": 1024,
        "warning_free_percent": 30,
        "critical_free_percent": 15,
    },
    "vision_heavy": {
        "max_context_tokens": 6144,
        "max_image_edge_px": 768,
        "max_image_bytes": 2 * 1024 * 1024,
        "browser_reserve_bytes": 2 * 1024 * 1024 * 1024,
    },
}


def estimate_ja_tokens(text: str) -> int:
    """Estimate token count for mixed Japanese / ASCII assistant text."""

    if not text:
        return 0
    ja_chars = len(_JA_CHAR_RE.findall(text))
    ascii_words = _ASCII_WORD_RE.findall(text)
    ascii_chars = sum(len(word) for word in ascii_words)
    punctuation = max(0, len(text) - ja_chars - ascii_chars)
    # Japanese chars are roughly 1.6-2.0 tokens each in local LLM tokenizers.
    ja_tokens = int(ja_chars * 0.55) + (1 if ja_chars else 0)
    ascii_tokens = max(len(ascii_words), ascii_chars // 4)
    punct_tokens = punctuation // 6
    return max(1, ja_tokens + ascii_tokens + punct_tokens)


@dataclass(frozen=True)
class ResourcePolicy:
    """Testable memory and context limits for one heavy model role."""

    total_ram_bytes: int = 16 * 1024 * 1024 * 1024
    safety_margin_bytes: int = 2 * 1024 * 1024 * 1024
    browser_reserve_bytes: int = 1 * 1024 * 1024 * 1024
    max_swap_bytes: int = 256 * 1024 * 1024
    warning_free_percent: int = 25
    critical_free_percent: int = 12
    max_context_tokens: int = 8192
    min_context_tokens: int = 1024
    kv_bytes_per_token: int = 64
    max_image_edge_px: int = 1024
    max_image_bytes: int = 4 * 1024 * 1024
    max_base64_chars: int = 6 * 1024 * 1024
    metal_release_wait_seconds: float = 2.0
    emergency_swap_delta_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "ResourcePolicy":
        if not data:
            return cls()
        kwargs: dict[str, object] = {}
        for field_name in cls.__dataclass_fields__:
            if field_name in data:
                kwargs[field_name] = data[field_name]
        return cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_sample(cls, name: str) -> "ResourcePolicy":
        sample = SAMPLE_POLICIES.get(name)
        if sample is None:
            raise KeyError(f"unknown resource policy sample: {name}")
        return cls.from_mapping(sample)

    @classmethod
    def sample_names(cls) -> tuple[str, ...]:
        return tuple(SAMPLE_POLICIES.keys())

    def available_for_models_bytes(self) -> int:
        return max(0, self.total_ram_bytes - self.safety_margin_bytes - self.browser_reserve_bytes)

    def kv_cache_budget_bytes(self, *, context_tokens: int | None = None) -> int:
        tokens = context_tokens if context_tokens is not None else self.max_context_tokens
        tokens = max(self.min_context_tokens, min(self.max_context_tokens, int(tokens)))
        return tokens * self.kv_bytes_per_token

    def max_safe_context_tokens(
        self,
        *,
        model_weight_bytes: int,
        free_bytes: int | None = None,
    ) -> int:
        budget = free_bytes if free_bytes is not None else self.available_for_models_bytes()
        remaining = max(0, budget - int(model_weight_bytes))
        if remaining <= 0:
            return self.min_context_tokens
        tokens = remaining // max(1, self.kv_bytes_per_token)
        return max(self.min_context_tokens, min(self.max_context_tokens, tokens))

    def shorten_messages(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        target_tokens: int | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Trim oldest non-system turns until estimated token budget fits."""

        limit = target_tokens or self.max_context_tokens
        system_msgs: list[dict[str, str]] = []
        other_msgs: list[dict[str, str]] = []
        for msg in messages:
            copy = {"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))}
            if copy["role"] == "system":
                system_msgs.append(copy)
            else:
                other_msgs.append(copy)

        def estimate(msgs: Iterable[Mapping[str, str]]) -> int:
            total = 0
            for msg in msgs:
                total += estimate_ja_tokens(str(msg.get("content", "")))
            return total

        trimmed = list(other_msgs)
        while trimmed and estimate(system_msgs) + estimate(trimmed) > limit:
            trimmed.pop(0)
        return tuple(system_msgs + trimmed)

    def clamp_image_edge(self, width: int, height: int) -> tuple[int, int]:
        edge = max(width, height)
        if edge <= self.max_image_edge_px:
            return width, height
        scale = self.max_image_edge_px / float(edge)
        return max(1, int(width * scale)), max(1, int(height * scale))

    def allow_model_load(
        self,
        *,
        model_bytes: int,
        free_percent: int | None,
        swap_delta_bytes: int | None,
        loaded_models: Iterable[str] | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        gates: list[str] = []
        loaded = tuple(dict.fromkeys(str(m).strip() for m in (loaded_models or ()) if str(m).strip()))
        if len(loaded) >= 1:
            gates.append("one_heavy_model_invariant")
            return False, tuple(gates)

        if free_percent is not None and free_percent < self.critical_free_percent:
            gates.append("memory_critical")
            return False, tuple(gates)

        if swap_delta_bytes is not None and swap_delta_bytes >= self.emergency_swap_delta_bytes:
            gates.append("swap_emergency")
            return False, tuple(gates)

        needed = model_bytes + self.kv_cache_budget_bytes()
        if needed > self.available_for_models_bytes():
            gates.append("model_plus_kv_exceeds_budget")
            return False, tuple(gates)

        if free_percent is not None and free_percent < self.warning_free_percent:
            gates.append("memory_warning_allowed")
        return True, tuple(gates)

    def allow_new_request(
        self,
        *,
        free_percent: int | None,
        swap_delta_bytes: int | None,
        in_flight: int,
        max_in_flight: int = 2,
    ) -> tuple[bool, tuple[str, ...]]:
        gates: list[str] = []
        if in_flight >= max_in_flight:
            gates.append("in_flight_limit")
            return False, tuple(gates)
        if free_percent is not None and free_percent < self.critical_free_percent:
            gates.append("memory_critical")
            return False, tuple(gates)
        if swap_delta_bytes is not None and swap_delta_bytes >= self.emergency_swap_delta_bytes:
            gates.append("swap_emergency")
            return False, tuple(gates)
        return True, tuple(gates)


__all__ = [
    "ResourcePolicy",
    "SAMPLE_POLICIES",
    "estimate_ja_tokens",
]
