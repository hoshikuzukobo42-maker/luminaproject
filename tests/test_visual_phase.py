import asyncio
import fcntl
import os
from pathlib import Path
import threading
import time

import httpx
import pytest

import lumina_next.visual_phase as phase_module
from lumina_next.visual_phase import PhaseLeaseError, inference_lease, status

REAL_HEALTH_GATE = phase_module._check_tts_health


def install_probe(monkeypatch, reader):
    probe = phase_module._StatusProbe()
    monkeypatch.setattr(phase_module, "_STATUS_PROBE", probe)
    monkeypatch.setattr(phase_module, "_lock_status", reader)
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    return probe


async def wait_entered(event):
    deadline = time.monotonic() + 1
    while not event.is_set():
        assert time.monotonic() < deadline
        await asyncio.sleep(.001)


def test_async_disabled_does_not_start_thread_or_file_probe(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled status attempted a thread/file probe")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "0")
    monkeypatch.setattr(phase_module._STATUS_PROBE, "_flight", forbidden)
    monkeypatch.setattr(phase_module, "_lock_status", forbidden)
    result = asyncio.run(phase_module.async_status())
    assert result["enabled"] is False and result["busy"] is False
    assert result["probe_observed_unix"] is None and result["probe_pending"] is False
    assert result["probe_revision"] == phase_module.PHASE_STATUS_REVISION


def test_async_file_io_off_loop_but_epoch_snapshot_on_loop(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    def read(path):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(1)
        return {"busy": True}
    probe = install_probe(monkeypatch, read)
    def cached_epoch():
        assert threading.get_ident() == loop_thread
        return {"cached_only": True}
    monkeypatch.setattr(phase_module.SHARED_EPOCH, "status", cached_epoch)
    async def run():
        task = asyncio.create_task(phase_module.async_status(path=tmp_path/"lock"))
        await wait_entered(entered)
        await asyncio.sleep(.01)  # Progress on this loop while file worker is blocked.
        assert not task.done()
        release.set()
        result = await task
        assert result["busy"] is True and result["probe_pending"] is False
        assert result["probe_started_unix"] <= result["probe_observed_unix"] <= time.time()
    try:
        asyncio.run(run())
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


def test_normal_concurrent_polls_share_one_fresh_probe_and_deadline(monkeypatch, tmp_path):
    entered, release, calls = threading.Event(), threading.Event(), []
    def read(path):
        calls.append(path)
        entered.set()
        assert release.wait(1)
        return {"busy": False}
    probe = install_probe(monkeypatch, read)
    async def run():
        first = asyncio.create_task(phase_module.async_status(path=tmp_path/"lock"))
        await wait_entered(entered)
        second = asyncio.create_task(phase_module.async_status(path=tmp_path/"lock"))
        await asyncio.sleep(.01)
        release.set()
        a, b = await asyncio.gather(first, second)
        assert a["busy"] is False and b["busy"] is False
        assert a["probe_started_unix"] == b["probe_started_unix"]
        assert a["probe_observed_unix"] == b["probe_observed_unix"]
        assert len(calls) == 1
    try:asyncio.run(run())
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


def test_timeout_pending_never_spawns_more_workers_or_relabels_late_success(monkeypatch, tmp_path):
    release, calls = threading.Event(), []
    def read(path):
        calls.append(path)
        if len(calls) == 1:assert release.wait(1)
        return {"busy": False}
    probe = install_probe(monkeypatch, read)
    async def run():
        first = await phase_module.async_status(path=tmp_path/"lock", timeout=.01)
        assert first["busy"] is None and first["probe_pending"] is True
        assert first["probe_observed_unix"] is None
        for _ in range(20):
            result = await phase_module.async_status(path=tmp_path/"lock")
            assert result["error"] == "phase_status_probe_timeout"
            assert result["probe_started_unix"] == first["probe_started_unix"]
            assert result["probe_observed_unix"] is None
        assert len(calls) == 1
        release.set()
        while not probe._pending["future"].done():await asyncio.sleep(.001)
        fresh = await phase_module.async_status(path=tmp_path/"lock")
        assert fresh["busy"] is False and len(calls) == 2
        assert fresh["probe_started_unix"] > first["probe_started_unix"]
    try:asyncio.run(run())
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


def test_cancelled_joiner_keeps_probe_for_other_caller(monkeypatch, tmp_path):
    entered, release, calls = threading.Event(), threading.Event(), []
    def read(path):
        calls.append(path); entered.set()
        assert release.wait(1)
        return {"busy": True}
    probe=install_probe(monkeypatch,read)
    async def run():
        first=asyncio.create_task(phase_module.async_status(path=tmp_path/"lock"))
        await wait_entered(entered)
        second=asyncio.create_task(phase_module.async_status(path=tmp_path/"lock"))
        await asyncio.sleep(.005)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        assert not probe._pending["future"].cancelled()
        release.set()
        assert (await second)["busy"] is True
        assert len(calls)==1
    try:asyncio.run(run())
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


def test_pending_future_survives_closed_event_loop_without_wrong_loop_or_stale_reuse(monkeypatch, tmp_path):
    release,calls=threading.Event(),[]
    def read(path):
        calls.append(path)
        if len(calls)==1:assert release.wait(1)
        return {"busy":False}
    probe=install_probe(monkeypatch,read)
    try:
        first=asyncio.run(phase_module.async_status(path=tmp_path/"lock",timeout=.005))
        second=asyncio.run(phase_module.async_status(path=tmp_path/"lock"))
        assert first["busy"] is None and second["busy"] is None and len(calls)==1
        release.set();probe._pending["future"].result(timeout=1)
        third=asyncio.run(phase_module.async_status(path=tmp_path/"lock"))
        assert third["busy"] is False and len(calls)==2
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


def test_other_path_does_not_join_wrong_lock_probe(monkeypatch,tmp_path):
    entered,release=threading.Event(),threading.Event()
    def read(path):
        entered.set();assert release.wait(1)
        return {"busy":False}
    probe=install_probe(monkeypatch,read)
    async def run():
        first=asyncio.create_task(phase_module.async_status(path=tmp_path/"a"))
        await wait_entered(entered)
        other=await phase_module.async_status(path=tmp_path/"b")
        assert other["busy"] is None and other["error"]=="phase_status_probe_pending"
        release.set();await first
    try:asyncio.run(run())
    finally:
        release.set()
        if probe._pending:probe._pending["future"].result(timeout=1)


@pytest.mark.parametrize("kind",["missing","file","busy","directory","symlink"])
def test_async_real_lock_probe_preserves_sync_semantics(monkeypatch,tmp_path,kind):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION","1")
    monkeypatch.setattr(phase_module,"_STATUS_PROBE",phase_module._StatusProbe())
    path=tmp_path/"lock";fd=None
    if kind=="directory":path.mkdir()
    elif kind=="symlink":path.symlink_to(tmp_path/"missing")
    elif kind in {"file","busy"}:
        path.touch()
        if kind=="busy":
            fd=os.open(path,os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX)
    try:
        before=status(path=path)
        result=asyncio.run(phase_module.async_status(path=path))
        for key in ("enabled","busy","initialized","error","lock_path"):
            assert result.get(key)==before.get(key)
    finally:
        if fd is not None:os.close(fd)


def test_async_worker_error_is_unknown_not_private_exception(monkeypatch,tmp_path):
    def fail(path):raise RuntimeError("private fixture content")
    install_probe(monkeypatch,fail)
    result=asyncio.run(phase_module.async_status(path=tmp_path/"lock"))
    assert result["busy"] is None and result["error"]=="phase_lease_unavailable"
    assert "private fixture content" not in str(result)


@pytest.fixture(autouse=True)
def idle_engine_without_network(monkeypatch):
    async def idle(path):
        assert status(path=path)["busy"] is True
    monkeypatch.setattr(phase_module, "_check_tts_health", idle)


def test_disabled_phase_has_no_file_io(tmp_path, monkeypatch):
    monkeypatch.delenv("LUMINA_VISUAL_PHASE_SERIALIZATION", raising=False)
    path = tmp_path / "missing" / "lock"
    async def forbidden_health(path):
        pytest.fail("disabled phase attempted engine health I/O")
    monkeypatch.setattr(phase_module, "_check_tts_health", forbidden_health)
    async def run():
        async with inference_lease(path=path):
            assert status(path=path) == {"enabled": False, "busy": False}
    asyncio.run(run())
    assert not path.exists()


def test_exclusive_lease_and_same_inode_survive_repeated_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    identities = []
    async def run():
        for _ in range(2):
            async with inference_lease(path=path):
                assert status(path=path)["busy"] is True
                identities.append(path.stat().st_ino)
            assert status(path=path)["busy"] is False
    asyncio.run(run())
    assert identities[0] == identities[1]


def test_native_worker_lease_blocks_inference_until_released(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    entered = []
    async def operation():
        async with inference_lease(path=path):
            entered.append(True)
    async def run():
        task = asyncio.create_task(operation())
        await asyncio.sleep(.08)
        assert not entered
        fcntl.flock(fd, fcntl.LOCK_UN)
        await task
    try:
        asyncio.run(run())
    finally:
        os.close(fd)
    assert entered == [True]


def test_waiting_cancellation_never_releases_someone_elses_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    async def operation():
        async with inference_lease(path=path):
            pytest.fail("cancelled waiter entered the GPU phase")
    async def run():
        task = asyncio.create_task(operation())
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert status(path=path)["busy"] is True
    try:
        asyncio.run(run())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_wait_deadline_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    async def run():
        with pytest.raises(PhaseLeaseError, match="busy"):
            async with inference_lease(path=path, timeout=.01):
                pytest.fail("busy phase was entered")
    try:
        asyncio.run(run())
    finally:
        os.close(fd)


def test_symlink_lock_is_rejected_without_modifying_target(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    target = tmp_path / "unrelated"
    target.write_text("keep")
    path = tmp_path / "phase.lock"
    path.symlink_to(target)
    async def run():
        with pytest.raises(PhaseLeaseError, match="unavailable"):
            async with inference_lease(path=path):
                pytest.fail("symlink was followed")
    asyncio.run(run())
    assert target.read_text() == "keep" and status(path=path)["busy"] is None


def test_body_failure_releases_only_owned_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    async def run():
        with pytest.raises(ValueError, match="body"):
            async with inference_lease(path=path):
                raise ValueError("body")
        assert status(path=path)["busy"] is False
    asyncio.run(run())


def test_body_oserror_keeps_its_original_cause(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    async def run():
        with pytest.raises(OSError, match="body read failed"):
            async with inference_lease(path=path):
                raise OSError("body read failed")
        assert status(path=path)["busy"] is False
    asyncio.run(run())


def test_non_regular_lock_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    path = tmp_path / "phase.lock"
    path.mkdir()
    async def run():
        with pytest.raises(PhaseLeaseError, match="unavailable"):
            async with inference_lease(path=path):
                pytest.fail("directory was accepted as the phase lease")
    asyncio.run(run())
    assert status(path=path)["busy"] is None


def install_health_fixture(monkeypatch, path, payload, *, status_code=200, content=None, error=None, wait=None):
    calls = []
    constructor = []
    closed = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            # Cleanup, including failed/cancelled probes, remains under flock.
            assert status(path=path)["busy"] is True
            closed.append(True)
        async def get(self, url):
            assert status(path=path)["busy"] is True
            calls.append(url)
            if wait is not None:
                await wait.wait()
            if error:
                raise error
            values = {"content":content} if content is not None else {"json":payload}
            return httpx.Response(status_code, request=httpx.Request("GET",url), **values)
    def create_client(**kwargs):
        constructor.append(kwargs)
        return Client()
    monkeypatch.setattr(httpx,"AsyncClient",create_client)
    monkeypatch.setattr(phase_module,"_check_tts_health",REAL_HEALTH_GATE)
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION","1")
    return calls, constructor, closed


def engine_payload(path):
    return {"status":"ok","runtime":{"loaded":False,"loading":False,"unloading":False,"unload_error":None},"phase_serialization":{
        "enabled":True,"lock_path":str(path),"active_workers":0,
        "idle_residency_policy":"unload_after_worker",
        "waiting_workers":0,"outstanding_workers":0,"blocked_reason":None}}


def test_health_checked_under_exclusive_lock_without_waking_or_redirects(tmp_path, monkeypatch):
    path=tmp_path / "phase.lock"
    calls,constructor,closed=install_health_fixture(monkeypatch,path,engine_payload(path))
    async def run():
        async with inference_lease(path=path):
            assert closed == [True]
    asyncio.run(run())
    assert calls == ["http://127.0.0.1:5088/health"]
    assert constructor == [{"timeout":2.0,"trust_env":False,"follow_redirects":False}]
    assert status(path=path)["busy"] is False


def test_waiting_workers_allowed_but_latch_rechecked_on_each_admission(tmp_path, monkeypatch):
    path=tmp_path / "phase.lock"
    payload=engine_payload(path)
    payload["phase_serialization"].update(waiting_workers=2,outstanding_workers=2,idle=False)
    calls,_,_=install_health_fixture(monkeypatch,path,payload)
    async def run():
        async with inference_lease(path=path):
            pass
        payload["phase_serialization"]["blocked_reason"]="tts_phase_free_below_limit"
        with pytest.raises(PhaseLeaseError,match="visual_heavy_phase_tts_latched"):
            async with inference_lease(path=path):
                pytest.fail("latched TTS engine admitted another model request")
        assert status(path=path)["busy"] is False
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("change", [
    "missing_phase","missing_latch","missing_workers","bad_status","non_object",
    "disabled","bad_path","active","negative_active","bool_active","float_active",
    "latched","empty_latch",
])
def test_unknown_mismatched_active_or_latched_engine_fails_closed(tmp_path,monkeypatch,change):
    path=tmp_path / "phase.lock"
    payload=engine_payload(path)
    engine=payload["phase_serialization"]
    if change == "missing_phase":payload.pop("phase_serialization")
    elif change == "missing_latch":engine.pop("blocked_reason")
    elif change == "missing_workers":engine.pop("active_workers")
    elif change == "bad_status":payload["status"]="loading"
    elif change == "non_object":payload=[]
    elif change == "disabled":engine["enabled"]=False
    elif change == "bad_path":engine["lock_path"]="/somewhere/else"
    elif change == "active":engine["active_workers"]=1
    elif change == "negative_active":engine["active_workers"]=-1
    elif change == "bool_active":engine["active_workers"]=False
    elif change == "float_active":engine["active_workers"]=0.0
    elif change == "latched":engine["blocked_reason"]="private fixture explanation"
    elif change == "empty_latch":engine["blocked_reason"]=""
    _,_,closed=install_health_fixture(monkeypatch,path,payload)
    async def run():
        with pytest.raises(PhaseLeaseError,match="visual_heavy_phase_tts_") as caught:
            async with inference_lease(path=path):
                pytest.fail("unsafe engine admitted model inference")
        assert "private fixture" not in str(caught.value)
        assert status(path=path)["busy"] is False
    asyncio.run(run())
    assert closed == [True]


@pytest.mark.parametrize("change", [
    "missing_policy", "resident_policy", "missing_runtime", "invalid_runtime",
    "missing_loaded", "missing_loading", "missing_unloading", "null_loaded",
    "numeric_loaded", "numeric_loading", "numeric_unloading",
    "loaded", "loading", "unloading", "missing_unload_error", "cleanup_failed", "empty_cleanup_error",
])
def test_tts_residency_must_be_explicitly_released_before_model_admission(tmp_path, monkeypatch, change):
    path = tmp_path / "phase.lock"
    payload = engine_payload(path)
    if change == "missing_policy":
        payload["phase_serialization"].pop("idle_residency_policy")
    elif change == "resident_policy":
        payload["phase_serialization"]["idle_residency_policy"] = "resident"
    elif change == "missing_runtime":
        payload.pop("runtime")
    elif change == "invalid_runtime":
        payload["runtime"] = []
    elif change.startswith("missing_"):
        payload["runtime"].pop(change.removeprefix("missing_"))
    elif change == "null_loaded":
        payload["runtime"]["loaded"] = None
    elif change == "cleanup_failed":
        payload["runtime"]["unload_error"] = "tts_phase_runtime_unload_failed"
    elif change == "empty_cleanup_error":
        payload["runtime"]["unload_error"] = ""
    elif change.startswith("numeric_"):
        payload["runtime"][change.removeprefix("numeric_")] = 0
    else:
        payload["runtime"][change] = True
    calls, _, closed = install_health_fixture(monkeypatch, path, payload)
    async def run():
        with pytest.raises(PhaseLeaseError, match="visual_heavy_phase_tts_"):
            async with inference_lease(path=path):
                pytest.fail("resident or unknown TTS runtime admitted inference")
        assert status(path=path)["busy"] is False
    asyncio.run(run())
    assert calls == ["http://127.0.0.1:5088/health"] and closed == [True]


@pytest.mark.parametrize("stream", [False, True])
def test_resident_engine_blocks_public_provider_before_model_http(tmp_path, monkeypatch, stream):
    from lumina_next.providers.openai_compatible import OpenAICompatibleProvider
    path = tmp_path / "phase.lock"
    monkeypatch.setattr(phase_module, "LOCK_PATH", path)
    payload = engine_payload(path)
    payload["runtime"]["loaded"] = True
    calls, constructors, closed = install_health_fixture(monkeypatch, path, payload)
    async def run():
        provider = OpenAICompatibleProvider("http://127.0.0.1:11436/v1", "qwen3.5-4b-q4_K_M")
        with pytest.raises(PhaseLeaseError, match="^visual_heavy_phase_tts_resident$"):
            await provider.chat([{"role":"user", "content":"fixture"}], stream=stream)
    asyncio.run(run())
    assert calls == ["http://127.0.0.1:5088/health"]
    assert len(constructors) == 1 and closed == [True]


@pytest.mark.parametrize("failure",["unavailable","http_error","redirect","invalid_json"])
def test_health_transport_or_parse_failure_never_admits_inference(tmp_path,monkeypatch,failure):
    path=tmp_path / "phase.lock"
    kwargs={}
    if failure == "unavailable":kwargs["error"]=httpx.ConnectError("private transport detail")
    elif failure == "http_error":kwargs["status_code"]=503
    elif failure == "redirect":kwargs["status_code"]=302
    elif failure == "invalid_json":kwargs["content"]=b"private malformed response"
    calls,_,closed=install_health_fixture(monkeypatch,path,engine_payload(path),**kwargs)
    async def run():
        with pytest.raises(PhaseLeaseError,match="^visual_heavy_phase_tts_health_unavailable$"):
            async with inference_lease(path=path):
                pytest.fail("failed health probe admitted inference")
        assert status(path=path)["busy"] is False
    asyncio.run(run())
    assert calls == ["http://127.0.0.1:5088/health"] and closed == [True]


def test_total_health_deadline_and_cancellation_cleanup(tmp_path,monkeypatch):
    path=tmp_path / "phase.lock"
    original_timeout=asyncio.timeout
    def short_deadline(seconds):
        assert seconds == 3.0
        return original_timeout(.02)
    monkeypatch.setattr(asyncio,"timeout",short_deadline)
    async def run():
        calls,_,closed=install_health_fixture(monkeypatch,path,engine_payload(path),wait=asyncio.Event())
        with pytest.raises(PhaseLeaseError,match="health_unavailable"):
            async with inference_lease(path=path):
                pytest.fail("unbounded health wait entered inference")
        assert calls and closed == [True] and status(path=path)["busy"] is False
    asyncio.run(run())


def test_user_cancellation_during_health_does_not_enter_body(tmp_path,monkeypatch):
    path=tmp_path / "phase.lock"
    async def run():
        calls,_,closed=install_health_fixture(monkeypatch,path,engine_payload(path),wait=asyncio.Event())
        async def request():
            async with inference_lease(path=path):
                pytest.fail("cancelled health gate entered inference")
        task=asyncio.create_task(request())
        while not calls:
            await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True] and status(path=path)["busy"] is False
    asyncio.run(run())


@pytest.mark.parametrize("stream",[False,True])
def test_latched_engine_blocks_public_provider_before_any_model_http(tmp_path,monkeypatch,stream):
    from lumina_next.providers.openai_compatible import OpenAICompatibleProvider
    path=tmp_path / "phase.lock"
    monkeypatch.setattr(phase_module,"LOCK_PATH",path)
    payload=engine_payload(path)
    payload["phase_serialization"]["blocked_reason"]="tts_phase_free_below_limit"
    calls,constructors,closed=install_health_fixture(monkeypatch,path,payload)
    async def run():
        provider=OpenAICompatibleProvider("http://127.0.0.1:11436/v1","qwen3.5-4b-q4_K_M")
        with pytest.raises(PhaseLeaseError,match="visual_heavy_phase_tts_latched"):
            await provider.chat([{"role":"user","content":"fixture"}],stream=stream)
    asyncio.run(run())
    assert calls == ["http://127.0.0.1:5088/health"]
    assert len(constructors) == 1 and closed == [True]
