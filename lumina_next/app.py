from __future__ import annotations

import asyncio
import base64
import binascii
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .autonomy_supervisor import AutonomySupervisor
from .config import load_runtime_config
from .karakuri_discord import KarakuriDiscordBridge, create_karakuri_discord_router
from .karakuri_world import create_karakuri_world_router
from .memory_consent_v1 import MemoryConsentError
from .orchestrator import LuminaOrchestrator, TurnInput
from .providers.llm import ProviderError
from .health_checks import memory_health
from .visual_navigation import (
    VisualNavigation,
    NavigationError,
    enabled as visual_navigation_enabled,
    portable_enabled as portable_navigation_enabled,
    relevant as visual_navigation_relevant,
    STOP as VISUAL_STOP,
)
from .visual_phase import async_status as async_phase_status


RUNTIME_ROOT = Path(os.environ.get("LUMINA_RUNTIME_ROOT") or Path(__file__).resolve().parents[1]).resolve()
CONFIG = load_runtime_config(RUNTIME_ROOT)
ORCHESTRATOR = LuminaOrchestrator(CONFIG)
MEMORY_CONTROL = ORCHESTRATOR.memory_control

app = FastAPI(title="Lumina Next Orchestrator", version="0.4.0")
KARAKURI_ROUTER = create_karakuri_world_router(ORCHESTRATOR)
app.include_router(KARAKURI_ROUTER)
KARAKURI_DISCORD = KarakuriDiscordBridge(KARAKURI_ROUTER.karakuri_service)
app.include_router(create_karakuri_discord_router(KARAKURI_DISCORD))
VISUAL_NAVIGATION = VisualNavigation(RUNTIME_ROOT, CONFIG.bridge_base_url, CONFIG.llm_base_url)
AUTONOMY = AutonomySupervisor(
    ORCHESTRATOR,
    KARAKURI_ROUTER.karakuri_service,
    VISUAL_NAVIGATION,
    portable_navigation_enabled,
)
ACTIVITY_PROBE_REVISION = "memory_only_activity_v1"


def _companion_input_limit() -> int:
    try:
        configured = int(
            os.environ.get("LUMINA_NEXT_MAX_USER_CHARS")
            or os.environ.get("LUMINA_NEXT_WAY_MAX_USER_CHARS")
            or "200"
        )
    except ValueError:
        configured = 200
    return max(80, min(400, configured))


def _litert_input_limit() -> int:
    try:
        configured = int(os.environ.get("LUMINA_NEXT_LITERT_MAX_USER_CHARS", "160"))
    except ValueError:
        configured = 160
    return max(80, min(400, configured))


def _default_litert_prepare_turn() -> TurnInput:
    return TurnInput(
        guild_id=os.environ.get("LUMINA_NEXT_LITERT_GUILD_ID", "godot"),
        channel_id=os.environ.get("LUMINA_NEXT_LITERT_CHANNEL_ID", "visitor:text"),
        user_id=os.environ.get("LUMINA_NEXT_LITERT_USER_ID", "toha"),
        text="prepare",
        persona="lumina",
        persona_mode="auto",
        session_id=os.environ.get("LUMINA_NEXT_LITERT_SESSION_ID", "godot:toha"),
    )


@app.on_event("startup")
async def start_karakuri_discord_bridge() -> None:
    if CONFIG.llm_provider == "litert_persistent":
        await ORCHESTRATOR.prepare_litert_session(_default_litert_prepare_turn())
    await ORCHESTRATOR.sleep_cycle.start()
    await KARAKURI_DISCORD.start()
    await AUTONOMY.start()


@app.on_event("shutdown")
async def stop_karakuri_discord_bridge() -> None:
    await VISUAL_NAVIGATION.cancel()
    await AUTONOMY.stop()
    await KARAKURI_DISCORD.stop()
    await ORCHESTRATOR.sleep_cycle.stop()
    if CONFIG.llm_provider == "litert_persistent":
        await ORCHESTRATOR.llm.cancel()


class ChatRequest(BaseModel):
    guild_id: str = Field(default="local", min_length=1, max_length=200)
    channel_id: str = Field(default="local", min_length=1, max_length=200)
    user_id: str = Field(default="local-user", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    persona: str = Field(default="lumina", max_length=40)
    persona_mode: str = Field(default="auto", max_length=40)
    session_id: str | None = Field(default=None, max_length=300)
    memory_scope: str = Field(default="default", pattern="^(default|session_only)$")
    synthesize: bool = False
    emotion: str = Field(default="neutral", max_length=40)
    speed: float = Field(default=1.0, ge=0.75, le=1.35)
    dispatch_to_bridge: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    input_mode: str = Field(default="text", pattern="^(text|voice)$")


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(min_length=1, max_length=80_000_000)
    language: str = Field(default="ja", max_length=12)
    respond: bool = False
    guild_id: str = Field(default="local", min_length=1, max_length=200)
    channel_id: str = Field(default="voice", min_length=1, max_length=200)
    user_id: str = Field(default="local-user", min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=300)
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    synthesize: bool = False
    dispatch_to_bridge: bool = False
    emotion: str = Field(default="neutral", max_length=40)
    speed: float = Field(default=1.0, ge=0.75, le=1.35)


class MemoryPinRequest(BaseModel):
    pinned: bool


class MemoryForgetRequest(BaseModel):
    confirm: bool = False


class MemoryConsentRequest(BaseModel):
    user_id: str = Field(default="local-user", min_length=1, max_length=200)
    storage_allowed: bool
    allowed_layers: list[str] = Field(default_factory=list, max_length=5)
    confirm: bool = False


class MemoryControlRequest(BaseModel):
    user_id: str = Field(default="local-user", min_length=1, max_length=200)
    item_ids: list[str] = Field(min_length=1, max_length=500)
    confirmation: str = Field(min_length=1, max_length=100)
    confirm: bool = False


class SleepRequest(BaseModel):
    mode: str = Field(default="deep", pattern="^(deep|micro)$")
    remain_asleep: bool = True


class LLMPrepareRequest(BaseModel):
    guild_id: str = Field(default="godot", min_length=1, max_length=200)
    channel_id: str = Field(default="visitor:text", min_length=1, max_length=200)
    user_id: str = Field(default="toha", min_length=1, max_length=200)
    session_id: str = Field(default="godot:toha", min_length=1, max_length=300)
    persona: str = Field(default="lumina", pattern="^lumina$")
    persona_mode: str = Field(default="auto", max_length=40)


def _management_memory() -> Any:
    memory = ORCHESTRATOR.memory
    if hasattr(memory, "management_snapshot"):
        return memory
    sqlite_memory = getattr(memory, "sqlite", None)
    if sqlite_memory is not None and hasattr(sqlite_memory, "management_snapshot"):
        return sqlite_memory
    raise HTTPException(status_code=503, detail="SQLite memory management is unavailable")


def _memory_control_http_error(exc: MemoryConsentError) -> HTTPException:
    if exc.code == "MEMORY_CONTROL_ITEM_NOT_FOUND":
        status_code = 404
    elif "CONFIRMATION" in exc.code or "RECEIPT" in exc.code or "PROPAGATION" in exc.code:
        status_code = 409
    else:
        status_code = 422
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.detail},
    )


@app.get("/health")
async def health() -> dict:
    llm = await ORCHESTRATOR.llm.health()
    tts = await ORCHESTRATOR.tts.health() if CONFIG.tts_enabled else {
        "ok": False,
        "base_url": CONFIG.tts_base_url,
        "disabled": True,
    }
    bridge = await ORCHESTRATOR.bridge.health() if CONFIG.bridge_enabled else {
        "ok": False,
        "base_url": CONFIG.bridge_base_url,
        "disabled": True,
    }
    resource = await ORCHESTRATOR.resources.snapshot()
    storage = memory_health(ORCHESTRATOR.memory)
    return {
        "ok": bool(llm.get("ok")) and storage["ok"],
        "service": "lumina-next-orchestrator",
        "version": "0.4.0",
        "runtime_root": str(CONFIG.runtime_root),
        "llm": llm,
        "memory": {
            "provider": CONFIG.memory_provider,
            "path": str(CONFIG.memory_dir),
            "database": str(CONFIG.sqlite_db_path),
            "write_enabled": CONFIG.memory_write_enabled,
            **storage,
        },
        "embedding": ORCHESTRATOR.embedding.status(),
        "stt": {
            "enabled": CONFIG.stt_enabled,
            **ORCHESTRATOR.stt.health(),
        },
        "resource": resource,
        "tts": {
            "enabled": CONFIG.tts_enabled,
            "backend": CONFIG.tts_backend,
            "base_url": CONFIG.tts_base_url,
            "health": tts,
        },
        "bridge": {
            "enabled": CONFIG.bridge_enabled,
            "base_url": CONFIG.bridge_base_url,
            "health": bridge,
        },
        "cognition": ORCHESTRATOR.cognition.snapshot(),
        "sleep": ORCHESTRATOR.sleep_cycle.status(),
        "autonomy": AUTONOMY.status(),
        "external_llm_enabled": CONFIG.external_llm_enabled,
        "visual_navigation": {
            "enabled": visual_navigation_enabled(),
            "portable_navigation_enabled": portable_navigation_enabled(),
            "active_request_id": VISUAL_NAVIGATION.active_id,
        },
    }


@app.get("/cognition")
async def cognition() -> dict:
    memory = _management_memory()
    wake_brief = memory.wake_brief() if hasattr(memory, "wake_brief") else {}
    return {
        "ok": True,
        "state": ORCHESTRATOR.cognition.snapshot(),
        "wake_brief": wake_brief,
    }


@app.get("/sleep")
async def sleep_status() -> dict:
    return {"ok": True, "sleep": ORCHESTRATOR.sleep_cycle.status()}


@app.get("/autonomy")
async def autonomy_status() -> dict:
    return {"ok": True, "autonomy": AUTONOMY.status()}


@app.post("/autonomy/tick")
async def autonomy_tick() -> dict:
    return await AUTONOMY.tick(trigger="operator_request")


@app.post("/autonomy/start")
async def autonomy_start() -> dict:
    return {"ok": True, "autonomy": await AUTONOMY.start()}


@app.post("/autonomy/stop")
async def autonomy_stop() -> dict:
    return {"ok": True, "autonomy": await AUTONOMY.stop()}


@app.get("/autonomy/audio")
async def autonomy_audio() -> dict:
    """Consume one locally synthesized autonomous utterance."""
    output = AUTONOMY.take_speech_output()
    return {"ok": True, "available": output is not None, "audio": output}


@app.post("/sleep/start")
async def sleep_start(payload: SleepRequest) -> dict:
    result = await ORCHESTRATOR.sleep_cycle.run_once(
        trigger="operator_request",
        mode=payload.mode,
        remain_asleep=payload.remain_asleep,
    )
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "state": ORCHESTRATOR.cognition.snapshot(),
    }


@app.post("/sleep/wake")
async def sleep_wake() -> dict:
    state = ORCHESTRATOR.cognition.wake("operator_request")
    return {"ok": True, "state": state}


@app.get("/resource")
async def resource(compact: bool = False) -> dict:
    if compact:
        active_ids = await ORCHESTRATOR.active_request_ids()
        return {"ok": True, "activity_only": True, "memory_measured": False,
                "activity_probe_revision": ACTIVITY_PROBE_REVISION,
                "observed_unix": time.time(), "active_request_ids": active_ids}
    return {
        "ok": True,
        "resource": await ORCHESTRATOR.resources.snapshot(),
        "embedding": ORCHESTRATOR.embedding.status(),
        "active_request_ids": await ORCHESTRATOR.active_request_ids(),
    }


@app.post("/chat/{request_id}/cancel")
async def cancel_chat(request_id: str) -> dict:
    visual_cancelled = await VISUAL_NAVIGATION.cancel(request_id)
    cancelled = await ORCHESTRATOR.cancel(request_id) or visual_cancelled
    return {
        "ok": cancelled,
        "request_id": request_id,
        "status": "cancellation_requested" if cancelled else "not_active",
    }


@app.post("/barge-in")
async def barge_in() -> dict:
    started = time.perf_counter()
    cancelled = await ORCHESTRATOR.cancel_all()
    visual_cancelled = await VISUAL_NAVIGATION.cancel()
    bridge_result: dict = {"enabled": CONFIG.bridge_enabled, "sent": False}
    if CONFIG.bridge_enabled:
        try:
            bridge_result = await ORCHESTRATOR.bridge.stop_audio()
            bridge_result["enabled"] = True
        except ProviderError as exc:
            bridge_result = {"enabled": True, "sent": False, "error": str(exc)}
    return {
        "ok": bool(cancelled or visual_cancelled or bridge_result.get("ok")),
        "visual_navigation_cancelled": visual_cancelled,
        "cancelled_request_ids": cancelled,
        "bridge": bridge_result,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


@app.post("/llm/session/prepare")
async def prepare_llm_session(payload: LLMPrepareRequest) -> dict:
    if CONFIG.llm_provider != "litert_persistent":
        raise HTTPException(status_code=409, detail="persistent LiteRT provider is not active")
    try:
        llm = await ORCHESTRATOR.prepare_litert_session(
            TurnInput(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                user_id=payload.user_id,
                text="prepare",
                persona=payload.persona,
                persona_mode=payload.persona_mode,
                session_id=payload.session_id,
            )
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "session_id": payload.session_id, "llm": llm}


@app.post("/transcribe")
async def transcribe(payload: TranscribeRequest) -> dict:
    if not CONFIG.stt_enabled:
        raise HTTPException(status_code=503, detail="stt is disabled")
    try:
        audio = base64.b64decode(payload.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="audio_base64 is invalid") from exc
    await ORCHESTRATOR.resources.set_state("listening")
    try:
        result = await ORCHESTRATOR.stt.transcribe(audio, payload.language)
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await ORCHESTRATOR.resources.set_state("idle")

    response: dict = {
        "ok": True,
        "transcript": result.text,
        "raw_transcript": result.raw_text,
        "corrected": result.corrected,
        "latency_ms": round(result.latency_ms, 1),
        "model": result.model,
        "language": payload.language,
    }
    if payload.respond:
        if (
            ORCHESTRATOR._uses_companion_chat_contract(CONFIG.primary_model)
            and len(result.text) > _companion_input_limit()
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Local companion transcript is limited to {_companion_input_limit()} characters "
                    "so persona, speaking style, and recent history remain inside ctx=1024"
                ),
            )
        if CONFIG.llm_provider == "litert_persistent" and len(result.text) > _litert_input_limit():
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Gemma local transcript is limited to {_litert_input_limit()} characters "
                    "so the persistent 1024-token session remains bounded"
                ),
            )
        response["chat"] = await ORCHESTRATOR.chat(
            TurnInput(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                user_id=payload.user_id,
                text=result.text,
                session_id=payload.session_id,
                synthesize=payload.synthesize,
                dispatch_to_bridge=payload.dispatch_to_bridge,
                emotion=payload.emotion,
                speed=payload.speed,
                request_id=payload.request_id,
                input_mode="voice",
            )
        )
    return response


@app.get("/memory")
async def memory_snapshot(limit: int = 100, user_id: str = "local-user") -> dict:
    try:
        snapshot = MEMORY_CONTROL.management_snapshot(user_id=user_id, limit=limit)
    except MemoryConsentError as exc:
        raise _memory_control_http_error(exc) from exc
    return {"ok": True, "memory": snapshot}


@app.post("/memory/control/consent")
async def memory_consent(payload: MemoryConsentRequest) -> dict:
    try:
        state = MEMORY_CONTROL.set_consent(
            user_id=payload.user_id,
            storage_allowed=payload.storage_allowed,
            allowed_layers=payload.allowed_layers,
            confirmed=payload.confirm,
        )
    except MemoryConsentError as exc:
        raise _memory_control_http_error(exc) from exc
    return {"ok": True, **state}


@app.post("/memory/control/export")
async def memory_export(payload: MemoryControlRequest) -> dict:
    try:
        return MEMORY_CONTROL.export_memory(
            user_id=payload.user_id,
            item_ids=payload.item_ids,
            confirmation_text=payload.confirmation,
        )
    except MemoryConsentError as exc:
        raise _memory_control_http_error(exc) from exc


@app.post("/memory/control/forget-all")
async def memory_forget_all(payload: MemoryControlRequest) -> dict:
    if payload.confirm is not True:
        raise HTTPException(status_code=409, detail="confirm=true is required to forget all memory")
    try:
        return MEMORY_CONTROL.forget_all(
            user_id=payload.user_id,
            item_ids=payload.item_ids,
            confirmation_text=payload.confirmation,
        )
    except MemoryConsentError as exc:
        raise _memory_control_http_error(exc) from exc


@app.post("/memory/profile-facts/{item_id}/pin")
async def memory_pin(item_id: str, payload: MemoryPinRequest) -> dict:
    memory = _management_memory()
    updated = bool(memory.set_profile_fact_pinned(item_id, payload.pinned))
    if not updated:
        raise HTTPException(status_code=404, detail="profile fact was not found or memory is read-only")
    return {"ok": True, "item_id": item_id, "pinned": payload.pinned}


@app.post("/memory/{kind}/{item_id}/forget")
async def memory_forget(kind: str, item_id: str, payload: MemoryForgetRequest) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=409, detail="confirm=true is required to forget memory")
    memory = _management_memory()
    try:
        deleted = bool(memory.forget(kind, item_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="memory item was not found or memory is read-only")
    return {"ok": True, "forgotten": True, "kind": kind, "item_id": item_id}


@app.get("/runtime")
async def runtime() -> dict:
    return {
        "service": "lumina-next-orchestrator",
        "llm_primary_model": CONFIG.primary_model,
        "llm_provider": CONFIG.llm_provider,
        "llm_base_url": CONFIG.llm_base_url,
        "llm_fallback_models": list(CONFIG.fallback_models),
        "context_size": CONFIG.num_ctx,
        "parallel_requests": 1,
        "current_phase": "live-conversation-ready",
        "autonomy": AUTONOMY.status(),
        "completed_phases": [
            "provider-boundary",
            "persona-state",
            "sqlite-memory",
            "sqlite-vec-e5",
            "llama-cpp-qwen3-4b",
            "sensevoice-int8",
            "whisper-cpp-small-rollback",
            "tts-adapter",
            "godot-bridge",
            "audio-barge-in",
            "tauri-control-plane",
            "semantic-memory-on-demand",
            "100-turn-soak",
            "audio-daemon-live-permission",
            "live-microphone-one-turn",
            "persistent-cognitive-state",
            "sleep-memory-consolidation",
            "memory-provenance",
            "karakuri-sleep-aware-autonomy",
        ],
        "next_phases": ["godot-sleep-state-presentation", "seven-day-cognition-soak"],
    }


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict:
    if visual_navigation_enabled() and visual_navigation_relevant(payload.text):
        return await visual_navigation_request(payload)
    if (
        ORCHESTRATOR._uses_companion_chat_contract(CONFIG.primary_model)
        and len(payload.text) > _companion_input_limit()
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Local companion chat input is limited to {_companion_input_limit()} characters "
                "so persona, speaking style, and recent history remain inside ctx=1024"
            ),
        )
    if CONFIG.llm_provider == "litert_persistent" and len(payload.text) > _litert_input_limit():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Gemma local chat input is limited to {_litert_input_limit()} characters "
                "so the persistent 1024-token session remains bounded"
            ),
        )
    try:
        return await ORCHESTRATOR.chat(
            TurnInput(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                user_id=payload.user_id,
                text=payload.text,
                persona=payload.persona,
                persona_mode=payload.persona_mode,
                session_id=payload.session_id,
                memory_scope=payload.memory_scope,
                synthesize=payload.synthesize,
                emotion=payload.emotion,
                speed=payload.speed,
                dispatch_to_bridge=payload.dispatch_to_bridge,
                request_id=payload.request_id,
                input_mode=payload.input_mode,
            )
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=409, detail="chat request cancelled") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _compact_visual_navigation_status(status: dict) -> dict:
    """Explicit diagnostic projection: never return job bodies or private history."""
    def scalar(value: Any, kind: str) -> Any:
        if value is None:
            return None
        if kind == "bool":
            return value if type(value) is bool else None
        if kind == "number":
            return value if type(value) in (int, float) and -1e20 < value < 1e20 else None
        if kind == "id":
            return value if (type(value) is str and 1 <= len(value) <= 100
                             and value.isascii()
                             and all(char.isalnum() or char in "_.:-" for char in value)) else None
        return value if type(value) is str and len(value) <= 512 else None

    def select(source: Any, fields: dict[str, str]) -> dict:
        if not isinstance(source, dict):
            return {}
        return {key: scalar(source[key], kind) for key, kind in fields.items() if key in source}

    active_id = scalar(status.get("active_request_id"), "id")
    result = {
        "enabled": scalar(status.get("enabled"), "bool"),
        "status_probe_revision": scalar(status.get("status_probe_revision"), "str"),
        "observed_unix": time.time(),
        "active_request_id": active_id,
        "unconfirmed_stop_command_id": scalar(status.get("unconfirmed_stop_command_id"), "id"),
        "resource_budget": select(status.get("resource_budget"), {
            "minimum_free_percent": "number", "maximum_swap_growth_mib": "number",
            "baseline_scope": "str", "baseline_swap_mib": "number", "last_free_percent": "number",
            "peak_swap_growth_mib": "number", "blocked_reason": "str", "measurement_scope": "str",
        }),
        "heavy_phase": select(status.get("heavy_phase"), {
            "enabled": "bool", "busy": "bool", "lock_path": "str", "initialized": "bool", "error": "str",
            "probe_revision": "str", "probe_started_unix": "number",
            "probe_observed_unix": "number", "probe_pending": "bool",
        }),
        "active_stage": None,
    }
    jobs = status.get("jobs")
    if active_id is None or not isinstance(jobs, list):
        return result
    matches = [job for job in jobs if isinstance(job, dict) and job.get("request_id") == active_id]
    if len(matches) != 1:
        return result
    job = matches[0]
    job_status = job.get("status")
    active = {
        "status": job_status if type(job_status) is str and job_status in {
            "observing", "moving", "arrived", "observed", "not_visible", "ambiguous", "failed", "cancelled",
        } else None,
        "phase_lease_held": scalar(job.get("phase_lease_held"), "bool"),
        "model_wake": None,
    }
    wake = job.get("model_wake")
    if isinstance(wake, dict) and wake.get("request_id") == active_id:
        stage = wake.get("stage")
        if type(stage) is str and stage in {"checking", "loading", "ready", "failed", "cancelled"}:
            active["model_wake"] = {"stage": stage, **select(wake, {
                "started_unix": "number", "deadline_unix": "number",
                "was_sleeping": "bool", "latency_ms": "number",
            })}
    result["active_stage"] = active
    return result


@app.get("/visual-navigation")
async def visual_navigation_status(compact: bool = False) -> dict:
    phase_snapshot = await async_phase_status()
    # Capture mutable job/active/epoch state together on the event loop after
    # the file probe; never send the whole status builder into a worker thread.
    status = VISUAL_NAVIGATION.status(phase_snapshot=phase_snapshot)
    return _compact_visual_navigation_status(status) if compact else status


@app.post("/visual-navigation")
async def visual_navigation_request(payload: ChatRequest) -> dict:
    try:
        if not visual_navigation_enabled():
            raise NavigationError("visual_navigation_disabled")
        if VISUAL_STOP.fullmatch(payload.text.strip()):
            if not payload.dispatch_to_bridge:
                return {"ok": True, "answer": "停止要求を確認したよ。実行なしのため、動作は変更していないよ。",
                        "request_id": payload.request_id, "model": "navigation-controller", "status": "validated_only"}
            cancelled = await VISUAL_NAVIGATION.cancel()
            return {"ok": True, "answer": "停止を要求したよ。" if cancelled else "この視覚移動の処理は動いていないよ。",
                    "request_id": payload.request_id, "model": "navigation-controller", "status": "cancelled" if cancelled else "idle"}
        job = await VISUAL_NAVIGATION.start(payload.text, payload.request_id,
            speak=payload.dispatch_to_bridge, allow_move=payload.dispatch_to_bridge)
        return {"ok": True, "answer": job["answer"], "request_id": job["request_id"],
                "model": CONFIG.primary_model, "status": job["status"], "navigation": job,
                "memory": {"recorded": False, "scope": "session_only"}}
    except NavigationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
