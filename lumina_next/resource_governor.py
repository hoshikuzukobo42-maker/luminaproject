from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_PERCENT_RE = re.compile(r"memory\s+free\s+percentage:\s*(\d{1,3})\s*%", re.IGNORECASE)
_VMSTAT_VALUE_RE = re.compile(r"^([^:]+):\s*([0-9,]+)(?:\.)?\s*$")
_VMSTAT_PAGESIZE_RE = re.compile(r"page size of\s+(\d+)\s+bytes", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedMemoryPressure:
    free_percent: int | None
    raw: str

    def classify(self, warning_percent: int = 25, critical_percent: int = 12) -> str:
        if self.free_percent is None:
            return "unknown"
        if self.free_percent < critical_percent:
            return "critical"
        if self.free_percent < warning_percent:
            return "warning"
        return "normal"


@dataclass(frozen=True)
class ParsedVmStat:
    page_size: int | None
    swapins: int | None
    swapouts: int | None
    free_pages: int | None
    raw: str


@dataclass(frozen=True)
class ResourceGovernorDecision:
    pressure_level: str
    effective_level: str
    gates: tuple[str, ...]
    free_percent: int | None
    swap_delta_bytes: int | None
    loaded_heavy_models: tuple[str, ...]
    unload_models: tuple[str, ...]
    restore_model: str | None
    restore_cooldown_remaining_sec: float | None
    one_heavy_model_invariant: bool


def _to_int(value: str) -> int | None:
    clean = value.replace(",", "").strip()
    if not clean:
        return None
    try:
        return int(clean)
    except ValueError:
        return None


def parse_memory_pressure_output(output: str | None) -> ParsedMemoryPressure:
    text = str(output or "").strip()
    if not text:
        return ParsedMemoryPressure(free_percent=None, raw=text)

    match = _PERCENT_RE.search(text)
    if not match:
        return ParsedMemoryPressure(free_percent=None, raw=text)

    value = _to_int(match.group(1))
    if value is None:
        return ParsedMemoryPressure(free_percent=None, raw=text)

    return ParsedMemoryPressure(free_percent=max(0, min(100, value)), raw=text)


def _normalize_vm_stat_name(name: str) -> str:
    return " ".join(name.strip().replace("\"", "").replace("'", "").split()).lower()


def parse_vm_stat_output(output: str | None) -> ParsedVmStat:
    text = str(output or "").strip()
    page_size: int | None = None
    swapins: int | None = None
    swapouts: int | None = None
    free_pages: int | None = None

    for line in text.splitlines():
        pagesize_match = _VMSTAT_PAGESIZE_RE.search(line)
        if pagesize_match:
            page_size = _to_int(pagesize_match.group(1))

        value_match = _VMSTAT_VALUE_RE.match(line)
        if not value_match:
            continue

        name = _normalize_vm_stat_name(value_match.group(1))
        value = _to_int(value_match.group(2))
        if value is None:
            continue

        if name.startswith("swapins"):
            swapins = value
        elif name.startswith("swapouts"):
            swapouts = value
        elif name == "pages free":
            free_pages = value

    return ParsedVmStat(page_size=page_size, swapins=swapins, swapouts=swapouts, free_pages=free_pages, raw=text)


class ResourceGovernor:
    """Deterministic parser and policy evaluator for memory-pressure gating."""

    def __init__(
        self,
        *,
        heavy_models: Iterable[str],
        warning_percent: int = 25,
        critical_percent: int = 12,
        warning_swap_delta_bytes: int = 128 * 1024 * 1024,
        critical_swap_delta_bytes: int = 512 * 1024 * 1024,
        restore_cooldown_seconds: float = 60.0,
    ) -> None:
        if not 0 <= critical_percent < warning_percent <= 100:
            raise ValueError("critical_percent must be lower than warning_percent and both within 0-100")
        if warning_swap_delta_bytes < 0 or critical_swap_delta_bytes < 0:
            raise ValueError("swap delta thresholds must be non-negative")
        if critical_swap_delta_bytes < warning_swap_delta_bytes:
            raise ValueError("critical swap threshold must be >= warning swap threshold")
        if restore_cooldown_seconds < 0:
            raise ValueError("restore_cooldown_seconds must be non-negative")

        self.warning_percent = int(warning_percent)
        self.critical_percent = int(critical_percent)
        self.warning_swap_delta_bytes = int(warning_swap_delta_bytes)
        self.critical_swap_delta_bytes = int(critical_swap_delta_bytes)
        self.restore_cooldown_seconds = float(restore_cooldown_seconds)

        self.heavy_models = tuple(
            dict.fromkeys(
                [
                    m.strip()
                    for m in heavy_models
                    if m is not None and str(m).strip()
                ]
            )
        )

        self._restore_after: float | None = None
        self._last_vm_stat: ParsedVmStat | None = None

    @property
    def preferred_heavy_model(self) -> str | None:
        return self.heavy_models[0] if self.heavy_models else None

    def _classify_pressure(self, free_percent: int | None) -> str:
        if free_percent is None:
            return "unknown"
        if free_percent < self.critical_percent:
            return "critical"
        if free_percent < self.warning_percent:
            return "warning"
        return "normal"

    def _ordered_loaded(self, loaded_heavy_models: Iterable[str] | None) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in loaded_heavy_models or ():
            model = str(raw).strip()
            if not model or model in seen:
                continue
            seen.add(model)
            normalized.append(model)

        loaded_set = set(normalized)
        ordered: list[str] = []
        for model in self.heavy_models:
            if model in loaded_set:
                ordered.append(model)
                loaded_set.remove(model)

        for model in normalized:
            if model in loaded_set:
                ordered.append(model)
                loaded_set.remove(model)

        return tuple(ordered)

    def _compute_swap_delta_bytes(self, current: ParsedVmStat) -> int | None:
        previous = self._last_vm_stat
        if previous is None:
            return None
        if current.swapouts is None or previous.swapouts is None:
            return None

        page_size = current.page_size or previous.page_size or 4096
        if page_size <= 0:
            return None

        swapout_delta = max(0, current.swapouts - previous.swapouts)
        if current.swapins is None or previous.swapins is None:
            return swapout_delta * page_size

        swapin_delta = max(0, current.swapins - previous.swapins)
        return (swapout_delta + swapin_delta) * page_size

    def _effective_level(self, pressure_level: str, swap_delta_bytes: int | None) -> tuple[str, tuple[str, ...]]:
        gates: list[str] = []
        swap_warning = False

        if swap_delta_bytes is not None:
            if swap_delta_bytes >= self.critical_swap_delta_bytes:
                gates.append("swap_delta_critical")
                return "critical", tuple(gates)
            if swap_delta_bytes >= self.warning_swap_delta_bytes:
                gates.append("swap_delta_warning")
                swap_warning = True

        if pressure_level in {"warning", "critical"}:
            gates.append(f"memory_pressure_{pressure_level}")

        if swap_warning:
            return "warning", tuple(gates)

        if pressure_level in {"warning", "unknown"}:
            return "warning", tuple(gates)
        if pressure_level == "critical":
            return "critical", tuple(gates)
        return "normal", tuple(gates)

    def evaluate(
        self,
        memory_pressure_output: str | None,
        vm_stat_output: str | None,
        *,
        loaded_heavy_models: Iterable[str] | None = None,
        now: float | None = None,
    ) -> ResourceGovernorDecision:
        timestamp = float(now) if now is not None else __import__("time").time()

        pressure = parse_memory_pressure_output(memory_pressure_output)
        vm_stat = parse_vm_stat_output(vm_stat_output)

        swap_delta_bytes = self._compute_swap_delta_bytes(vm_stat)
        self._last_vm_stat = vm_stat

        pressure_level = self._classify_pressure(pressure.free_percent)
        effective_level, gates = self._effective_level(pressure_level, swap_delta_bytes)

        loaded = self._ordered_loaded(loaded_heavy_models)
        loaded_after_policy = tuple(loaded)
        unload_models: tuple[str, ...] = ()
        restore_model: str | None = None
        restore_cooldown_remaining: float | None = None

        allowed_loaded = 1 if effective_level in {"normal", "warning"} else 0
        if len(loaded_after_policy) > allowed_loaded:
            unload_models = loaded_after_policy[allowed_loaded:]
            loaded_after_policy = loaded_after_policy[:allowed_loaded]
            gates = (*gates, "one_heavy_model_invariant")
            if effective_level == "critical":
                self._restore_after = timestamp + self.restore_cooldown_seconds

        if effective_level == "normal" and self._restore_after is not None:
            if timestamp >= self._restore_after:
                if not loaded_after_policy:
                    restore_model = self.preferred_heavy_model
                self._restore_after = None
            else:
                remaining = self._restore_after - timestamp
                restore_cooldown_remaining = remaining if remaining > 0 else 0.0

        if effective_level != "normal":
            self._restore_after = None if len(loaded_after_policy) > 0 else self._restore_after

        if loaded_after_policy:
            self._restore_after = None

        return ResourceGovernorDecision(
            pressure_level=pressure_level,
            effective_level=effective_level,
            gates=gates,
            free_percent=pressure.free_percent,
            swap_delta_bytes=swap_delta_bytes,
            loaded_heavy_models=loaded_after_policy,
            unload_models=unload_models,
            restore_model=restore_model,
            restore_cooldown_remaining_sec=restore_cooldown_remaining,
            one_heavy_model_invariant=len(loaded_after_policy) <= 1,
        )
