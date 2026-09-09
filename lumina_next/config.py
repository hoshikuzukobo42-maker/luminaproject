from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _coerce_int(value: Any, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_string(value: Any, default: str) -> str:
    if value is None:
        return default
    raw = str(value).strip()
    return raw if raw else default


def _coerce_url(value: Any, default: str) -> str:
    return _coerce_string(value, default).rstrip("/")


def _coerce_path(value: Any, root: Path) -> Path | None:
    text = _coerce_string(value, "")
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def _infer_port(url_value: str) -> int | None:
    parsed = urlparse(url_value)
    if parsed.port:
        return parsed.port
    # Fallback for values like host:port without scheme.
    if parsed.scheme == "" and ":" in parsed.path.rsplit("/", 1)[-1]:
        tail = parsed.path.rsplit("/", 1)[-1]
        token = tail.rsplit(":", 1)[-1]
        if token.isdigit():
            return int(token)
    return None


@dataclass(frozen=True)
class RuntimeConfig:
    runtime_root: Path
    config_path: Path
    memory_dir: Path
    sqlite_db_path: Path
    memory_provider: str
    llm_provider: str
    llm_base_url: str
    ollama_base_url: str
    primary_model: str
    fallback_models: tuple[str, ...]
    api_host: str
    api_port: int
    tts_enabled: bool
    tts_backend: str
    tts_base_url: str
    tts_model_name: str
    tts_style: str
    broker_url: str
    broker_port: int
    shisa_candidate_alias: str
    shisa_candidate_path: Path | None
    shisa_candidate_port: int
    vlm_path: Path | None
    vlm_port: int
    stt_url: str | None
    stt_backend: str
    tts_url: str
    model_swap_timeout_seconds: float
    queue_size: int
    bridge_enabled: bool
    bridge_base_url: str
    stt_enabled: bool
    stt_binary_path: Path
    stt_model_path: Path
    stt_vad_model_path: Path
    stt_corrections_path: Path
    stt_language: str
    stt_threads: int
    memory_write_enabled: bool
    embedding_enabled: bool
    embedding_model_path: Path
    resource_warning_percent: int
    resource_critical_percent: int
    llm_timeout_seconds: float
    llm_keep_alive: str
    num_ctx: int
    num_predict: int
    temperature: float
    top_p: float
    top_k: int | None
    repeat_penalty: float | None
    litert_system_prompt_path: Path | None
    litert_seed: int
    litert_max_history_messages: int
    litert_max_history_chars: int
    mlx_adapter_path: str | None
    external_llm_enabled: bool
    external_llm_hard_disable: bool
    sleep_enabled: bool
    sleep_check_interval_seconds: float
    sleep_start_hour: int
    sleep_wake_hour: int
    sleep_idle_seconds: float
    sleep_min_duration_seconds: float
    sleep_event_threshold: int
    micro_sleep_event_threshold: int
    sleep_max_events: int


GEMMA4_E4B_ALIAS = "lumina-gemma4-e4b"
QWEN35_4B_PRODUCT_ALIAS = "qwen3.5-4b-q4_K_M"


def is_gemma4_e4b_candidate(cfg: RuntimeConfig | None = None, *, primary_model: str = "") -> bool:
    """True for the unpromoted Gemma candidate, regardless of LiteRT vs llama.cpp."""
    model = (cfg.primary_model if cfg is not None else primary_model).strip()
    if model == GEMMA4_E4B_ALIAS:
        return True
    if os.environ.get("LUMINA_LLM_PRODUCT", "").strip() == "gemma4_e4b_candidate":
        return True
    return os.environ.get("LUMINA_LLM_ROUTING_PROFILE", "").strip() == "gemma4_e4b_opt_in"


def is_qwen35_4b_product(cfg: RuntimeConfig | None = None, *, primary_model: str = "") -> bool:
    """True only for the canonical local Qwen3.5 4B product alias."""
    model = (cfg.primary_model if cfg is not None else primary_model).strip()
    return model == QWEN35_4B_PRODUCT_ALIAS


def load_runtime_config(runtime_root: Path | None = None) -> RuntimeConfig:
    root = Path(
        runtime_root
        or os.environ.get("LUMINA_RUNTIME_ROOT")
        or Path(__file__).resolve().parents[1]
    ).resolve()
    config_path = Path(
        os.environ.get("LUMINA_NEXT_CONFIG_PATH") or root / "data" / "config.json"
    ).resolve()
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))

    profiles = raw.get("generation_profiles") or {}
    profile = profiles.get("lumina_chat_fast") or profiles.get("chat") or {}
    if not isinstance(profile, dict):
        profile = {}

    broker_url = _coerce_url(
        os.environ.get("LUMINA_NEXT_BROKER_URL")
        if os.environ.get("LUMINA_NEXT_BROKER_URL") is not None
        else raw.get("broker_url"),
        "http://127.0.0.1:8765",
    )
    broker_port = _env_int("LUMINA_NEXT_BROKER_PORT", _coerce_int(raw.get("broker_port"), None))
    if broker_port is None:
        broker_port = _infer_port(broker_url) or 8765
    shisa_candidate_alias = _coerce_string(
        os.environ.get("LUMINA_NEXT_SHISA_CANDIDATE_ALIAS")
        if os.environ.get("LUMINA_NEXT_SHISA_CANDIDATE_ALIAS") is not None
        else raw.get("shisa_candidate_alias"),
        "",
    )
    shisa_candidate_path = _coerce_path(
        os.environ.get("LUMINA_NEXT_SHISA_CANDIDATE_PATH")
        if os.environ.get("LUMINA_NEXT_SHISA_CANDIDATE_PATH") is not None
        else raw.get("shisa_candidate_path"),
        root,
    )
    shisa_candidate_port = _env_int(
        "LUMINA_NEXT_SHISA_CANDIDATE_PORT",
        _coerce_int(raw.get("shisa_candidate_port"), 0),
    )
    vlm_path = _coerce_path(
        os.environ.get("LUMINA_NEXT_VLM_PATH")
        if os.environ.get("LUMINA_NEXT_VLM_PATH") is not None
        else raw.get("vlm_path"),
        root,
    )
    vlm_port = _env_int(
        "LUMINA_NEXT_VLM_PORT",
        _coerce_int(raw.get("vlm_port"), 0),
    )
    stt_url = _coerce_string(
        os.environ.get("LUMINA_NEXT_STT_URL")
        if os.environ.get("LUMINA_NEXT_STT_URL") is not None
        else raw.get("stt_url"),
        "http://127.0.0.1:5057",
    )
    stt_url = stt_url.rstrip("/") if stt_url else None
    stt_backend = _coerce_string(
        os.environ.get("LUMINA_NEXT_STT_BACKEND")
        if os.environ.get("LUMINA_NEXT_STT_BACKEND") is not None
        else raw.get("stt_backend"),
        "sensevoice",
    ).strip().lower() or "sensevoice"
    broker_hard_disable = os.environ.get("LUMINA_NEXT_EXTERNAL_LLM_HARD_DISABLE")
    external_llm_hard_disable = (
        _coerce_bool(broker_hard_disable, False)
        if broker_hard_disable is not None
        else _coerce_bool(raw.get("external_llm_hard_disable"), False)
    )
    model_swap_timeout_seconds = _env_float(
        "LUMINA_NEXT_MODEL_SWAP_TIMEOUT_SECONDS",
        _env_float("LUMINA_NEXT_MODEL_SWAP_TIMEOUT", None),
    )
    if model_swap_timeout_seconds is None:
        model_swap_timeout_seconds = _coerce_float(raw.get("model_swap_timeout"), None)
    if model_swap_timeout_seconds is None:
        model_swap_timeout_seconds = _coerce_float(raw.get("model_swap_timeout_seconds"), 120.0)
    queue_size = _env_int(
        "LUMINA_NEXT_QUEUE_SIZE",
        _coerce_int(raw.get("queue_size"), None),
    )
    if queue_size is None:
        queue_size = _coerce_int(raw.get("queue_size"), 32)

    llm_provider = str(
        os.environ.get("LUMINA_NEXT_LLM_PROVIDER") or "ollama"
    ).strip().lower()
    selected_primary_model = str(
        os.environ.get("LUMINA_NEXT_PRIMARY_MODEL")
        or raw.get("llm_primary_model")
        or raw.get("fast_model")
        or QWEN35_4B_PRODUCT_ALIAS
    )
    fallback_models = (
        _string_list(raw.get("llm_fallback_models"))
        or ("qwen3.5-35b-a3b-iq2_m",)
    )
    if (
        llm_provider == "litert_persistent"
        or is_gemma4_e4b_candidate(primary_model=selected_primary_model)
        or is_qwen35_4b_product(primary_model=selected_primary_model)
    ):
        # The Gemma candidate is a single exact local alias.  Do not even
        # advertise legacy fallback models in runtime observability.
        fallback_models = ()

    cfg = RuntimeConfig(
        runtime_root=root,
        config_path=config_path,
        memory_dir=root / "data" / "memory_discord",
        sqlite_db_path=Path(
            os.environ.get("LUMINA_NEXT_SQLITE_PATH") or root / "data" / "lumina_next" / "partner.db"
        ).resolve(),
        memory_provider=str(os.environ.get("LUMINA_NEXT_MEMORY_PROVIDER") or "shadow").strip().lower(),
        llm_provider=llm_provider,
        llm_base_url=str(
            os.environ.get("LUMINA_NEXT_LLM_BASE_URL")
            or raw.get("ollama_base_url")
            or "http://127.0.0.1:11434"
        ).rstrip("/"),
        ollama_base_url=str(raw.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/"),
        primary_model=selected_primary_model,
        fallback_models=fallback_models,
        broker_url=broker_url,
        broker_port=broker_port,
        shisa_candidate_alias=shisa_candidate_alias,
        shisa_candidate_path=shisa_candidate_path,
        shisa_candidate_port=shisa_candidate_port,
        vlm_path=vlm_path,
        vlm_port=vlm_port,
        stt_url=stt_url,
        stt_backend=stt_backend,
        tts_url=str(
            os.environ.get("LUMINA_NEXT_TTS_URL")
            if os.environ.get("LUMINA_NEXT_TTS_URL") is not None
            else raw.get("tts_url")
            or raw.get("tts_base_url")
            or "http://127.0.0.1:5056"
        ).rstrip("/"),
        model_swap_timeout_seconds=model_swap_timeout_seconds,
        queue_size=queue_size,
        api_host=str(raw.get("api_host") or "127.0.0.1"),
        api_port=int(raw.get("api_port") or 8787),
        tts_enabled=_env_bool("LUMINA_NEXT_TTS_ENABLED", bool(raw.get("tts_enabled", False))),
        tts_backend=_coerce_string(
            os.environ.get("LUMINA_EXPECTED_TTS_BACKEND")
            if os.environ.get("LUMINA_EXPECTED_TTS_BACKEND") is not None
            else os.environ.get("TTS_BACKEND")
            if os.environ.get("TTS_BACKEND") is not None
            else raw.get("tts_backend"),
            "irodori" if is_qwen35_4b_product(primary_model=selected_primary_model) else "style-bert-vits2",
        ).strip().lower(),
        tts_base_url=str(
            os.environ.get("LUMINA_NEXT_TTS_BASE_URL")
            or raw.get("tts_base_url")
            or "http://127.0.0.1:5056"
        ).rstrip("/"),
        tts_model_name=str(raw.get("tts_model_name") or "tsukuyomi-chan-style-bert-vits2"),
        tts_style=str(raw.get("tts_style") or "Neutral"),
        bridge_enabled=_env_bool("LUMINA_NEXT_BRIDGE_ENABLED", True),
        bridge_base_url=str(os.environ.get("LUMINA_NEXT_BRIDGE_BASE_URL") or "http://127.0.0.1:8765").rstrip("/"),
        stt_enabled=_env_bool("LUMINA_NEXT_STT_ENABLED", True),
        stt_binary_path=Path(
            os.environ.get("LUMINA_NEXT_STT_BINARY")
            or root / "tools" / "whisper.cpp" / "v1.9.1" / "install" / "bin" / "whisper-cli"
        ).resolve(),
        stt_model_path=Path(
            os.environ.get("LUMINA_NEXT_STT_MODEL")
            or root / "data" / "lumina_next" / "models" / "whisper" / "ggml-small.bin"
        ).resolve(),
        stt_vad_model_path=Path(
            os.environ.get("LUMINA_NEXT_STT_VAD_MODEL")
            or root / "data" / "lumina_next" / "models" / "whisper" / "ggml-silero-v6.2.0.bin"
        ).resolve(),
        stt_corrections_path=Path(
            os.environ.get("LUMINA_NEXT_STT_CORRECTIONS")
            or root / "config" / "stt_corrections.json"
        ).resolve(),
        stt_language=str(os.environ.get("LUMINA_NEXT_STT_LANGUAGE") or "ja"),
        stt_threads=int(os.environ.get("LUMINA_NEXT_STT_THREADS") or "6"),
        memory_write_enabled=_env_bool("LUMINA_NEXT_MEMORY_WRITE", True),
        embedding_enabled=_env_bool("LUMINA_NEXT_EMBEDDING_ENABLED", True),
        embedding_model_path=Path(
            os.environ.get("LUMINA_NEXT_EMBEDDING_MODEL_PATH")
            or root / "data" / "lumina_next" / "models" / "multilingual-e5-small"
        ).resolve(),
        resource_warning_percent=int(os.environ.get("LUMINA_NEXT_MEMORY_WARNING_PERCENT", "25")),
        resource_critical_percent=int(os.environ.get("LUMINA_NEXT_MEMORY_CRITICAL_PERCENT", "12")),
        llm_timeout_seconds=float(os.environ.get("LUMINA_NEXT_LLM_TIMEOUT_SECONDS", "120")),
        llm_keep_alive=str(os.environ.get("LUMINA_NEXT_LLM_KEEP_ALIVE", "5m")),
        num_ctx=int(os.environ.get("LUMINA_NEXT_CONTEXT_SIZE") or profile.get("num_ctx") or raw.get("router_context_size") or 2048),
        num_predict=int(os.environ.get("LUMINA_NEXT_MAX_OUTPUT_TOKENS") or profile.get("num_predict") or 256),
        temperature=float(os.environ.get("LUMINA_NEXT_TEMPERATURE") or profile.get("temperature") or 0.55),
        top_p=float(os.environ.get("LUMINA_NEXT_TOP_P") or profile.get("top_p") or 0.85),
        top_k=(
            int(os.environ["LUMINA_NEXT_TOP_K"])
            if str(os.environ.get("LUMINA_NEXT_TOP_K") or "").strip()
            else None
        ),
        repeat_penalty=(
            float(os.environ["LUMINA_NEXT_REPEAT_PENALTY"])
            if str(os.environ.get("LUMINA_NEXT_REPEAT_PENALTY") or "").strip()
            else None
        ),
        litert_system_prompt_path=_coerce_path(
            os.environ.get("LUMINA_NEXT_LITERT_SYSTEM_PROMPT_PATH"),
            root,
        ),
        litert_seed=max(0, int(os.environ.get("LUMINA_NEXT_LITERT_SEED", "20260809"))),
        litert_max_history_messages=max(
            0,
            min(12, int(os.environ.get("LUMINA_NEXT_LITERT_MAX_HISTORY_MESSAGES", "4"))),
        ),
        litert_max_history_chars=max(
            120,
            min(1200, int(os.environ.get("LUMINA_NEXT_LITERT_MAX_HISTORY_CHARS", "240"))),
        ),
        mlx_adapter_path=(
            str(os.environ.get("LUMINA_NEXT_MLX_ADAPTER_PATH") or "").strip() or None
        ),
        external_llm_enabled=_env_bool("LUMINA_NEXT_ALLOW_EXTERNAL_LLM", False)
        and not external_llm_hard_disable,
        external_llm_hard_disable=_coerce_bool(
            os.environ.get("LUMINA_NEXT_EXTERNAL_LLM_HARD_DISABLE"),
            _coerce_bool(raw.get("external_llm_hard_disable"), False),
        ),
        sleep_enabled=_env_bool("LUMINA_NEXT_SLEEP_ENABLED", True),
        sleep_check_interval_seconds=max(
            15.0, float(os.environ.get("LUMINA_NEXT_SLEEP_CHECK_SECONDS", "60"))
        ),
        sleep_start_hour=max(
            0, min(23, int(os.environ.get("LUMINA_NEXT_SLEEP_START_HOUR", "3")))
        ),
        sleep_wake_hour=max(
            0, min(23, int(os.environ.get("LUMINA_NEXT_SLEEP_WAKE_HOUR", "7")))
        ),
        sleep_idle_seconds=max(
            60.0, float(os.environ.get("LUMINA_NEXT_SLEEP_IDLE_SECONDS", "1800"))
        ),
        sleep_min_duration_seconds=max(
            60.0, float(os.environ.get("LUMINA_NEXT_SLEEP_MIN_DURATION_SECONDS", "1800"))
        ),
        sleep_event_threshold=max(
            10, int(os.environ.get("LUMINA_NEXT_SLEEP_EVENT_THRESHOLD", "120"))
        ),
        micro_sleep_event_threshold=max(
            10, int(os.environ.get("LUMINA_NEXT_MICRO_SLEEP_EVENT_THRESHOLD", "40"))
        ),
        sleep_max_events=max(
            20, min(2000, int(os.environ.get("LUMINA_NEXT_SLEEP_MAX_EVENTS", "400")))
        ),
    )
    if cfg.llm_provider == "litert_persistent" or is_gemma4_e4b_candidate(cfg):
        # Unpromoted Gemma candidate: never inherit product defaults that would
        # enable embeddings, SQLite memory writes, external fallback, or a 256
        # token completion that does not fit the attested 1024 ctx.
        # Applies to LiteRT leftover and the Windows CUDA llama.cpp path.
        cfg = replace(
            cfg,
            fallback_models=(),
            memory_write_enabled=False,
            embedding_enabled=False,
            external_llm_hard_disable=True,
            external_llm_enabled=False,
            tts_backend="irodori",
            num_predict=min(max(1, int(cfg.num_predict)), 96),
            num_ctx=min(max(256, int(cfg.num_ctx)), 1024),
        )
    elif is_qwen35_4b_product(cfg):
        # Product Qwen is one exact local llama.cpp route. Never advertise a
        # stale IQ2/Way fallback or load the E5 embedding model beside it.
        cfg = replace(
            cfg,
            fallback_models=(),
            embedding_enabled=False,
            external_llm_hard_disable=True,
            external_llm_enabled=False,
            num_predict=min(max(1, int(cfg.num_predict)), 96),
            num_ctx=min(max(256, int(cfg.num_ctx)), 1024),
        )
    return cfg
