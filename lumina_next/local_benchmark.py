"""Dry-run local inference benchmark harness (no real model startup)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class BenchmarkScenario:
    name: str
    model_role: str
    prompt_tokens: int
    completion_tokens: int
    include_vision: bool = False


@dataclass
class BenchmarkMetrics:
    model_load_ms: float = 0.0
    prompt_processing_tps: float = 0.0
    ttft_ms: float = 0.0
    generation_tps: float = 0.0
    tokens_100_ms: float = 0.0
    tokens_300_ms: float = 0.0
    tokens_500_ms: float = 0.0
    rss_bytes: int = 0
    memory_pressure_free_percent: int | None = None
    swap_bytes: int = 0
    kv_bytes: int = 0
    unload_ms: float = 0.0
    llm_to_vlm_switch_ms: float = 0.0
    vlm_to_llm_restore_ms: float = 0.0
    stt_rtf: float = 0.0
    tts_first_audio_ms: float = 0.0
    error_rate: float = 0.0
    timeout_rate: float = 0.0
    repetition_rate: float = 0.0
    japanese_quality_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkResult:
    scenario: str
    dry_run: bool
    metrics: BenchmarkMetrics
    elapsed_ms: float


def _mock_latency(prompt_tokens: int, completion_tokens: int, *, vision: bool, role: str = "llm") -> BenchmarkMetrics:
    load = 1200.0 + prompt_tokens * 0.4
    ttft = 180.0 + prompt_tokens * 0.08
    tps = max(8.0, 22.0 - prompt_tokens / 1000.0)
    gen_ms = (completion_tokens / tps) * 1000.0
    stt_rtf = 0.28 if role == "aux" else 0.35
    tts_first = 180.0 if role == "aux" else 240.0
    return BenchmarkMetrics(
        model_load_ms=load,
        prompt_processing_tps=max(1.0, prompt_tokens / (ttft / 1000.0)),
        ttft_ms=ttft,
        generation_tps=tps,
        tokens_100_ms=(100 / tps) * 1000.0,
        tokens_300_ms=(300 / tps) * 1000.0,
        tokens_500_ms=(500 / tps) * 1000.0,
        rss_bytes=8_900_000_000 if not vision else 3_200_000_000,
        memory_pressure_free_percent=28,
        swap_bytes=0,
        kv_bytes=prompt_tokens * 64,
        unload_ms=900.0,
        llm_to_vlm_switch_ms=2100.0 if vision else 0.0,
        vlm_to_llm_restore_ms=1800.0 if vision else 0.0,
        stt_rtf=stt_rtf,
        tts_first_audio_ms=tts_first,
        error_rate=0.0,
        timeout_rate=0.0,
        repetition_rate=0.08,
        japanese_quality_score=0.78,
        notes=["mock-mode"] if role != "aux" else ["mock-mode", "stt-tts-pipeline"],
    )


def run_benchmark(
    scenario: BenchmarkScenario,
    *,
    dry_run: bool = True,
    executor: Callable[[BenchmarkScenario], BenchmarkMetrics] | None = None,
) -> BenchmarkResult:
    if not dry_run:
        raise RuntimeError("real-model benchmark is disabled in marathon mode")
    started = time.perf_counter()
    if executor is not None:
        metrics = executor(scenario)
    else:
        metrics = _mock_latency(
            scenario.prompt_tokens,
            scenario.completion_tokens,
            vision=scenario.include_vision,
            role=scenario.model_role,
        )
    elapsed = (time.perf_counter() - started) * 1000.0
    return BenchmarkResult(scenario=scenario.name, dry_run=True, metrics=metrics, elapsed_ms=elapsed)


def default_scenarios(*, include_stt_tts: bool = False) -> tuple[BenchmarkScenario, ...]:
    base = (
        BenchmarkScenario("shisa_chat", "llm", 512, 180),
        BenchmarkScenario("qwen_vision", "vlm", 256, 96, include_vision=True),
        BenchmarkScenario("switch_back", "llm", 128, 64, include_vision=True),
    )
    if include_stt_tts:
        return (*base, BenchmarkScenario("stt_tts_pipeline", "aux", 64, 32))
    return base


def load_scenarios_from_fixture(path: Path | str) -> tuple[BenchmarkScenario, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios: list[BenchmarkScenario] = []
    for item in payload.get("scenarios", ()):
        if not isinstance(item, Mapping):
            continue
        scenarios.append(
            BenchmarkScenario(
                name=str(item["name"]),
                model_role=str(item.get("model_role", "llm")),
                prompt_tokens=int(item.get("prompt_tokens", 128)),
                completion_tokens=int(item.get("completion_tokens", 64)),
                include_vision=bool(item.get("include_vision", False)),
            )
        )
    return tuple(scenarios)


def run_suite(
    scenarios: Sequence[BenchmarkScenario] | None = None,
    *,
    dry_run: bool = True,
    fixture_path: Path | str | None = None,
) -> list[BenchmarkResult]:
    if scenarios is not None:
        selected = scenarios
    elif fixture_path is not None:
        selected = load_scenarios_from_fixture(fixture_path)
    else:
        selected = default_scenarios()
    return [run_benchmark(item, dry_run=dry_run) for item in selected]


def write_report(path: str, results: Sequence[BenchmarkResult]) -> None:
    payload = {
        "dry_run": True,
        "results": [
            {
                "scenario": item.scenario,
                "elapsed_ms": item.elapsed_ms,
                "metrics": item.metrics.as_dict(),
            }
            for item in results
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def write_markdown_report(path: str, results: Sequence[BenchmarkResult]) -> None:
    """Write a human-readable markdown summary of benchmark results."""

    lines = [
        "# Local Benchmark Report",
        "",
        f"- dry_run: `{all(item.dry_run for item in results)}`",
        f"- scenarios: {len(results)}",
        "",
        "| Scenario | TTFT ms | Gen TPS | STT RTF | TTS first ms | Elapsed ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in results:
        m = item.metrics
        lines.append(
            f"| {item.scenario} | {m.ttft_ms:.1f} | {m.generation_tps:.2f} | "
            f"{m.stt_rtf:.2f} | {m.tts_first_audio_ms:.1f} | {item.elapsed_ms:.1f} |"
        )
    lines.append("")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = [
    "BenchmarkMetrics",
    "BenchmarkResult",
    "BenchmarkScenario",
    "default_scenarios",
    "load_scenarios_from_fixture",
    "run_benchmark",
    "run_suite",
    "write_markdown_report",
    "write_report",
]
