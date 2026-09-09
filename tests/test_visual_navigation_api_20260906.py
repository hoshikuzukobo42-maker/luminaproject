"""Route-level ASGI checks, isolated storage and no live network/services."""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time

import httpx
import pytest


@pytest.fixture
def api(tmp_path, monkeypatch):
    from lumina_next.sqlite_memory import SQLiteMemoryAdapter
    SQLiteMemoryAdapter(tmp_path / "test.db", write_enabled=True)
    monkeypatch.setenv("LUMINA_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LUMINA_NEXT_CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setenv("LUMINA_NEXT_SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LUMINA_NEXT_MEMORY_WRITE", "0")
    monkeypatch.setenv("LUMINA_NEXT_LLM_PROVIDER", "llama_cpp")
    monkeypatch.setenv("LUMINA_NEXT_LLM_BASE_URL", "http://127.0.0.1:11436/v1")
    monkeypatch.setenv("LUMINA_NEXT_PRIMARY_MODEL", "qwen3.5-4b-q4_K_M")
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    previous = sys.modules.pop("lumina_next.app", None)
    module = importlib.import_module("lumina_next.app")
    yield module
    sys.modules.pop("lumina_next.app", None)
    if previous is not None:
        sys.modules["lumina_next.app"] = previous


def post(api, path, body):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post(path, json=body)
    return asyncio.run(run())


def get(api, path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url="http://test") as client:
            return await client.get(path)
    return asyncio.run(run())


@pytest.mark.parametrize("phase",["0","1"])
def test_compact_activity_is_memory_only_and_preserves_actual_ids(api,monkeypatch,phase):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION",phase)
    async def forbidden_async(*args,**kwargs):pytest.fail("activity triggered resource/provider/file work")
    def forbidden(*args,**kwargs):pytest.fail("activity triggered embedding/file work")
    async def active():return ["request-1","request-2"]
    monkeypatch.setattr(api.ORCHESTRATOR.resources,"snapshot",forbidden_async)
    monkeypatch.setattr(api.ORCHESTRATOR.embedding,"status",forbidden)
    monkeypatch.setattr(api,"async_phase_status",forbidden_async)
    monkeypatch.setattr(api.ORCHESTRATOR,"active_request_ids",active)
    before=time.time()
    response=get(api,"/resource?compact=true")
    after=time.time();data=response.json()
    assert response.status_code==200
    assert set(data)=={"ok","activity_only","memory_measured","activity_probe_revision","observed_unix","active_request_ids"}
    assert data["ok"] is True and data["activity_only"] is True and data["memory_measured"] is False
    assert data["activity_probe_revision"]=="memory_only_activity_v1"
    assert data["active_request_ids"]==["request-1","request-2"]
    assert before<=data["observed_unix"]<=after


@pytest.mark.parametrize("url",["/resource","/resource?compact=false"])
def test_default_resource_payload_and_full_measurement_unchanged(api,monkeypatch,url):
    calls=[]
    async def snapshot():calls.append("memory");return {"memory_free_percent":55}
    def embedding():calls.append("embedding");return {"loaded":False}
    async def active():calls.append("activity");return []
    monkeypatch.setattr(api.ORCHESTRATOR.resources,"snapshot",snapshot)
    monkeypatch.setattr(api.ORCHESTRATOR.embedding,"status",embedding)
    monkeypatch.setattr(api.ORCHESTRATOR,"active_request_ids",active)
    data=get(api,url).json()
    assert data=={"ok":True,"resource":{"memory_free_percent":55},"embedding":{"loaded":False},"active_request_ids":[]}
    assert calls==["memory","embedding","activity"]


def test_activity_remains_responsive_while_full_resource_waits(api,monkeypatch):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def snapshot():entered.set();await release.wait();return {"memory_free_percent":55}
        monkeypatch.setattr(api.ORCHESTRATOR.resources,"snapshot",snapshot)
        monkeypatch.setattr(api.ORCHESTRATOR.embedding,"status",lambda:{})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url="http://test") as client:
            full=asyncio.create_task(client.get("/resource"))
            await entered.wait()
            compact=await asyncio.wait_for(client.get("/resource?compact=true"),.2)
            assert compact.json()["memory_measured"] is False and not full.done()
            release.set();assert (await full).status_code==200
    asyncio.run(run())


@pytest.mark.parametrize("compact",[False,True])
def test_visual_routes_await_probe_then_snapshot_jobs_on_loop(api,monkeypatch,compact):
    on_loop=threading.get_ident();calls=[]
    stamp=time.time()
    phase={"enabled":True,"busy":True,"lock_path":"/fixture/lock",
           "probe_revision":"offloop_singleflight_phase_status_v1",
           "probe_started_unix":stamp,"probe_observed_unix":stamp,"probe_pending":False}
    async def probe():
        calls.append("probe")
        await asyncio.sleep(0)
        api.VISUAL_NAVIGATION.active_id="new-request"
        api.VISUAL_NAVIGATION.jobs["new-request"]={"request_id":"new-request","status":"observing",
            "phase_lease_held":True,"text":"PRIVATE_SENTINEL","model_wake":{
                "request_id":"new-request","stage":"loading","started_unix":stamp,"deadline_unix":stamp+30}}
        return phase
    real_status=api.VISUAL_NAVIGATION.status
    def snapshot(*,phase_snapshot):
        calls.append("state")
        assert threading.get_ident()==on_loop and phase_snapshot is phase
        return real_status(phase_snapshot=phase_snapshot)
    monkeypatch.setattr(api,"async_phase_status",probe)
    monkeypatch.setattr(api.VISUAL_NAVIGATION,"status",snapshot)
    api.VISUAL_NAVIGATION.active_id="old-request"
    data=get(api,"/visual-navigation?compact="+str(compact).lower()).json()
    assert calls==["probe","state"] and data["active_request_id"]=="new-request"
    assert data["heavy_phase"]["probe_observed_unix"]==stamp
    assert data["status_probe_revision"]=="offloop_singleflight_phase_status_v1"
    if compact:
        assert data["observed_unix"]>=stamp
        assert data["active_stage"]["phase_lease_held"] is True
        assert "PRIVATE_SENTINEL" not in str(data) and "jobs" not in data
    else:assert "jobs" in data


@pytest.mark.parametrize("compact",[False,True])
def test_pending_phase_is_never_replaced_by_healthy_cached_state(api,monkeypatch,compact):
    async def probe():
        return {"enabled":True,"busy":None,"error":"phase_status_probe_timeout",
                "probe_revision":"offloop_singleflight_phase_status_v1","probe_pending":True,
                "probe_started_unix":time.time()-5,"probe_observed_unix":None}
    monkeypatch.setattr(api,"async_phase_status",probe)
    data=get(api,"/visual-navigation?compact="+str(compact).lower()).json()
    assert data["heavy_phase"]["busy"] is None
    assert data["heavy_phase"]["probe_observed_unix"] is None
    assert data["heavy_phase"]["probe_pending"] is True
    assert data["heavy_phase"]["error"]=="phase_status_probe_timeout"


def test_chat_routes_visual_request_once_with_dispatch_contract(api, monkeypatch):
    calls = []
    async def start(text, request_id, **kwargs):
        calls.append((text, request_id, kwargs))
        return {"request_id": request_id, "answer": "今の視界を確認するね。", "status": "observing"}
    monkeypatch.setattr(api.VISUAL_NAVIGATION, "start", start)
    response = post(api, "/chat", {"text": "ソファまで行って", "request_id": "visual-1", "dispatch_to_bridge": True})
    assert response.status_code == 200
    assert response.json()["status"] == "observing"
    assert response.json()["memory"]["recorded"] is False
    assert calls == [("ソファまで行って", "visual-1", {"speak": True, "allow_move": True})]


def test_dry_run_observation_has_no_movement_or_speech_authority(api, monkeypatch):
    calls = []
    async def start(text, request_id, **kwargs):
        calls.append(kwargs)
        return {"request_id": "dry", "answer": "checking", "status": "observing"}
    monkeypatch.setattr(api.VISUAL_NAVIGATION, "start", start)
    response = post(api, "/visual-navigation", {"text": "ソファまで行って"})
    assert response.status_code == 200
    assert calls == [{"speak": False, "allow_move": False}]


def test_dry_run_stop_does_not_cancel_live_job(api, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("dry-run stop must not cancel")
    monkeypatch.setattr(api.VISUAL_NAVIGATION, "cancel", forbidden)
    response = post(api, "/chat", {"text": "止まって", "dispatch_to_bridge": False})
    assert response.status_code == 200 and response.json()["status"] == "validated_only"


def test_real_stop_cancels_owned_visual_task(api, monkeypatch):
    calls = []
    async def cancel(*args):
        calls.append(args)
        return True
    monkeypatch.setattr(api.VISUAL_NAVIGATION, "cancel", cancel)
    response = post(api, "/chat", {"text": "止まって", "dispatch_to_bridge": True})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert calls == [()]


def test_disabled_direct_endpoint_fails_closed(api, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "0")
    response = post(api, "/visual-navigation", {"text": "ソファまで行って", "dispatch_to_bridge": True})
    assert response.status_code == 409 and response.json()["detail"] == "visual_navigation_disabled"


def test_normal_chat_remains_normal_chat(api, monkeypatch):
    calls = []
    async def chat(turn):
        calls.append(turn.text)
        return {"ok": True, "answer": "おはよう。"}
    monkeypatch.setattr(api.ORCHESTRATOR, "chat", chat)
    response = post(api, "/chat", {"text": "おはよう"})
    assert response.status_code == 200 and calls == ["おはよう"]


def test_bridge_adapter_routes_only_opt_in_and_preserves_dry_run(monkeypatch):
    from si_godot_bridge import lumina_next_adapter as adapter
    monkeypatch.setenv("LUMINA_VISUAL_NAVIGATION", "1")
    monkeypatch.setenv("LUMINA_NEXT_VISITOR_CHAT_URL", "http://127.0.0.1:8790/chat")
    seen = []
    def capture(url, body, timeout):
        seen.append((url, body, timeout))
        return {"ok": True, "answer": "checking"}
    monkeypatch.setattr(adapter, "_post_json", capture)
    assert adapter.visual_navigation_requested("ソファまで行って")
    assert not adapter.visual_navigation_requested("おはよう")
    adapter.request_visual_navigation(text="ソファまで行って", request_id="dry", actor_id="visitor", send=False)
    assert seen[0][0] == "http://127.0.0.1:8790/visual-navigation"
    assert seen[0][1]["dispatch_to_bridge"] is False and seen[0][1]["memory_scope"] == "session_only"
    monkeypatch.setenv("LUMINA_NEXT_VISITOR_CHAT_URL", "https://example.invalid/chat")
    with pytest.raises(RuntimeError, match="local"):
        adapter.request_visual_navigation(text="go to sofa", request_id="bad", actor_id="visitor", send=True)
    assert len(seen) == 1
