"""Atomic background admission and direct-request endpoint routing."""
import asyncio

import pytest

from lumina_next.visual_navigation import NavigationError, VisualNavigation, relevant


@pytest.mark.parametrize("text", [
    "ルミナ、右の植物を見てください", "ソファを観察して", "ランプを確認してください",
    "ソファがあるか確認して", "please look at the lamp", "observe the lamp",
])
def test_direct_observation_is_routed_to_visual_service(text):
    assert relevant(text)


def test_idle_admission_does_not_cancel_existing_or_newer_human_request(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")

    async def run():
        service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
        cancellations = []

        async def forbidden_cancel():
            cancellations.append(True)
            pytest.fail("background request attempted to cancel human work")

        service._cancel_active = forbidden_cancel
        # The earlier idle check saw no work. While start waits for the lock,
        # another caller becomes active. Admission must re-check inside lock.
        await service.lock.acquire()
        background = asyncio.create_task(service.start("ソファまで行って", "auto", only_if_idle=True))
        await asyncio.sleep(0)
        service.active_id = "new-human-request"
        service.lock.release()
        with pytest.raises(NavigationError, match="visual_navigation_busy"):
            await background
        assert service.active_id == "new-human-request"
        assert "auto" not in service.jobs
        assert not cancellations

    asyncio.run(run())


@pytest.mark.parametrize("state", ["task_without_id", "unconfirmed_stop"])
def test_uncertain_idle_state_is_not_admitted(tmp_path, monkeypatch, state):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")

    async def run():
        service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
        pending = None
        if state == "task_without_id":
            pending = asyncio.create_task(asyncio.Event().wait())
            service.task = pending
        else:
            service.unconfirmed_stop = {"request_id": "old-human"}
        try:
            with pytest.raises(NavigationError, match="visual_navigation_busy"):
                await service.start("ソファまで行って", "auto", only_if_idle=True)
            assert not service.jobs
        finally:
            if pending:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending

    asyncio.run(run())


def test_idle_request_can_start_but_observation_has_no_movement_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")

    async def run():
        service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")

        async def no_work(job, text):
            return None

        service._run = no_work
        job = await service.start("ソファを見てください", "auto-observe", speak=False,
                                  allow_move=False, only_if_idle=True)
        assert job["admission_policy"] == "idle_only"
        assert job["allow_move"] is False
        await service.task

    asyncio.run(run())


def test_idle_policy_requires_actual_boolean(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    service = VisualNavigation(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:11436/v1")
    with pytest.raises(NavigationError, match="invalid_idle_admission_policy"):
        asyncio.run(service.start("ソファまで行って", only_if_idle="false"))
