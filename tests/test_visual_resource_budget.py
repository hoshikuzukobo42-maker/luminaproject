import asyncio
import subprocess
from unittest.mock import patch

import pytest

from lumina_next.visual_resource_budget import VisualResourceBudget, VisualResourceError, read_visual_resources


def test_system_reading_parses_mib_and_gib():
    for value, expected in [("123.50M", 123.5), ("1.5G", 1536)]:
        with patch("subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout="System-wide memory free percentage: 24%"),
            subprocess.CompletedProcess([], 0, stdout=f"total = 10000M used = {value} free = 0M")]):
            assert read_visual_resources() == (24, expected)


@pytest.mark.parametrize("reading", [(None, 1), (23, None), (True, 0), (101, 0), (23, float("nan")), (23, -1)])
def test_unknown_or_invalid_measurements_fail_closed(reading):
    budget = VisualResourceBudget(lambda: reading)
    with pytest.raises(VisualResourceError, match="reading_unavailable"):
        asyncio.run(budget.check())


def test_memory_command_failure_returns_unknown_without_paths():
    with patch("subprocess.run", side_effect=OSError("private path")):
        assert read_visual_resources() == (None, None)


def test_exact_resource_boundaries_pass_but_overage_latches_across_requests():
    readings = iter([(22, 1000), (22, 1100), (22, 1256), (23, 1256.01), (99, 0)])
    budget = VisualResourceBudget(lambda: next(readings))
    async def check():
        await budget.check()
        await budget.check()
        await budget.check()
        with pytest.raises(VisualResourceError, match="swap_growth_above_limit"):
            await budget.check()
        with pytest.raises(VisualResourceError, match="swap_growth_above_limit"):
            await budget.check()
    asyncio.run(check())
    assert budget.baseline_swap_mib == 1000
    assert next(readings) == (99, 0)  # A trip is not auto-reset by a newer reading.


def test_bad_precheck_does_not_create_inference():
    called = []
    budget = VisualResourceBudget(lambda: (21, 1000))
    async def op():
        called.append(True)
    with pytest.raises(VisualResourceError, match="free_memory_below_limit"):
        asyncio.run(budget.run(op))
    assert not called


def test_sleeping_model_wake_requires_preload_margin():
    budget = VisualResourceBudget(lambda: (39, 1000))
    with pytest.raises(VisualResourceError, match="preload_memory_below_limit"):
        asyncio.run(budget.check(minimum_free_percent=40))
    assert budget.baseline_swap_mib == 1000


def test_preload_check_does_not_lower_or_reset_runtime_threshold():
    readings = iter([(40, 1000), (22, 1100), (21, 1100)])
    budget = VisualResourceBudget(lambda: next(readings))
    async def check():
        await budget.check(minimum_free_percent=40)
        await budget.check()
        with pytest.raises(VisualResourceError, match="free_memory_below_limit"):
            await budget.check(minimum_free_percent=0)
    asyncio.run(check())
    assert budget.baseline_swap_mib == 1000


def test_limit_crossing_cancels_and_awaits_inflight_inference():
    readings = iter([(24, 1000), (24, 1300)])
    budget = VisualResourceBudget(lambda: next(readings), interval=.01)
    completed = []
    async def op():
        try:
            await asyncio.sleep(10)
        finally:
            completed.append("cancelled")
    with pytest.raises(VisualResourceError, match="swap_growth_above_limit"):
        asyncio.run(budget.run(op))
    assert completed == ["cancelled"]


def test_success_checks_after_inference_and_retains_baseline():
    readings = iter([(24, 1000), (24, 1100), (24, 1150), (24, 1200)])
    budget = VisualResourceBudget(lambda: next(readings))
    async def op():
        return "pixels-only result"
    async def run():
        assert await budget.run(op) == "pixels-only result"
        assert await budget.run(op) == "pixels-only result"
    asyncio.run(run())
    assert budget.baseline_swap_mib == 1000 and budget.peak_swap_growth_mib == 200


def test_caller_cancellation_propagates_to_child_without_orphans():
    budget = VisualResourceBudget(lambda: (24, 1000), interval=.01)
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def op():
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        task = asyncio.create_task(budget.run(op))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    asyncio.run(run())
