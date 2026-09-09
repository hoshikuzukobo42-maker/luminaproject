"""Safe configuration tuner for M1 16GB local inference profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence

from .llama_compat import LlamaCapabilities, build_launch_plan
from .resource_policy import ResourcePolicy


@dataclass(frozen=True)
class TuningCandidate:
    profile: str
    context_size: int
    threads: int
    gpu_layers: int
    flash_attention: bool
    kv_quant: str | None
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    repeat_penalty: float
    presence_penalty: float
    max_tokens: int
    image_edge_px: int
    safe: bool
    notes: tuple[str, ...]


_M1_LIMITS = {
    "max_context": 16384,
    "max_threads": 8,
    "max_gpu_layers": 999,
    "max_tokens": 1024,
    "max_image_edge": 1280,
}


def _is_safe_for_m1(candidate: Mapping[str, object], policy: ResourcePolicy) -> tuple[bool, tuple[str, ...]]:
    notes: list[str] = []
    ctx = int(candidate.get("context_size", 4096))
    threads = int(candidate.get("threads", 4))
    gpu_layers = int(candidate.get("gpu_layers", 0))
    max_tokens = int(candidate.get("max_tokens", 256))
    image_edge = int(candidate.get("image_edge_px", policy.max_image_edge_px))

    if ctx > _M1_LIMITS["max_context"]:
        notes.append("context exceeds M1 safe limit")
    if threads > _M1_LIMITS["max_threads"]:
        notes.append("threads exceed M1 safe limit")
    if max_tokens > _M1_LIMITS["max_tokens"]:
        notes.append("max_tokens exceed safe limit")
    if image_edge > _M1_LIMITS["max_image_edge"]:
        notes.append("image edge exceeds safe limit")

    model_bytes = int(candidate.get("model_bytes", 8_890_306_208))
    if model_bytes + policy.kv_cache_budget_bytes(context_tokens=ctx) > policy.available_for_models_bytes():
        notes.append("model+KV exceeds available RAM budget")

    if gpu_layers <= 0 and ctx >= 8192:
        notes.append("large CPU-only context is risky on 16GB")

    return len(notes) == 0, tuple(notes)


def generate_candidates(
    *,
    profile: str,
    policy: ResourcePolicy | None = None,
    capabilities: LlamaCapabilities | None = None,
) -> list[TuningCandidate]:
    policy = policy or ResourcePolicy()
    capabilities = capabilities or LlamaCapabilities(version="0.0.0", flags=frozenset({"--ctx-size", "--threads"}), raw_help="")
    base_plan = build_launch_plan(profile=profile, model_path="mock.gguf", capabilities=capabilities)

    variants = [
        {"context_size": 4096, "threads": 4, "gpu_layers": 999, "flash_attention": True, "max_tokens": 256},
        {"context_size": 8192, "threads": 6, "gpu_layers": 999, "flash_attention": True, "max_tokens": 512},
        {"context_size": 2048, "threads": 4, "gpu_layers": 999, "flash_attention": False, "max_tokens": 180},
    ]
    candidates: list[TuningCandidate] = []
    for variant in variants:
        safe, notes = _is_safe_for_m1({**variant, "model_bytes": 8890306208}, policy)
        candidates.append(
            TuningCandidate(
                profile=profile,
                context_size=int(variant["context_size"]),
                threads=int(variant["threads"]),
                gpu_layers=int(variant["gpu_layers"]),
                flash_attention=bool(variant["flash_attention"]),
                kv_quant="q8_0" if profile == "low-memory" else None,
                temperature=0.7,
                top_p=0.9,
                top_k=40,
                min_p=0.05,
                repeat_penalty=1.08,
                presence_penalty=0.0,
                max_tokens=int(variant["max_tokens"]),
                image_edge_px=policy.max_image_edge_px,
                safe=safe,
                notes=(*notes, *base_plan.notes),
            )
        )
    return candidates


def pick_best(
    candidates: Sequence[TuningCandidate],
    *,
    prefer_quality: bool = False,
    prefer_latency: bool = False,
) -> TuningCandidate | None:
    if prefer_quality and prefer_latency:
        raise ValueError("prefer_quality and prefer_latency are mutually exclusive")
    safe_candidates = [item for item in candidates if item.safe]
    pool = safe_candidates or list(candidates)
    if not pool:
        return None
    if prefer_quality:
        return max(pool, key=lambda item: (item.context_size, item.max_tokens))
    if prefer_latency:
        return min(
            pool,
            key=lambda item: (
                item.max_tokens,
                item.context_size,
                0 if item.flash_attention else 1,
                item.threads,
            ),
        )
    return min(pool, key=lambda item: (item.context_size, item.max_tokens))


_CI_ENV_MARKERS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI")


def is_ci_environment(env: Mapping[str, str] | None = None) -> bool:
    """Return True when running under a known CI provider."""

    source = env or os.environ
    return any(str(source.get(key, "")).strip().lower() in {"1", "true", "yes"} for key in _CI_ENV_MARKERS)


def ci_unsafe_operation_blocked(operation: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Return True when *operation* must not run in CI (real model startup, etc.)."""

    if not is_ci_environment(env):
        return False
    normalized = str(operation).strip().lower()
    blocked_tokens = (
        "real_model",
        "model_startup",
        "benchmark_live",
        "load_model",
        "start_server",
    )
    return any(token in normalized for token in blocked_tokens)


__all__ = [
    "TuningCandidate",
    "ci_unsafe_operation_blocked",
    "generate_candidates",
    "is_ci_environment",
    "pick_best",
]
