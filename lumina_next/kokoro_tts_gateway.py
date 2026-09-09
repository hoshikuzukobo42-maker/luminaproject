#!/usr/bin/env python3
"""Local Kokoro ONNX TTS gateway for the Lumina compatibility boundary.

The gateway deliberately owns the model boundary instead of proxying to an
MLX or hosted service. It exposes the existing ``/health`` and
``/voice-lipsync`` contracts, OpenAI-compatible audio endpoints, and a
sentence-chunked NDJSON stream. Model loading is lazy so an operator can
inspect a useful health payload even when the local runtime is incomplete.
"""

from __future__ import annotations

import argparse
import base64
import inspect
import json
import math
import os
import re
import struct
import threading
import time
import wave
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5058
DEFAULT_VOICE = "jf_alpha"
DEFAULT_LANG_CODE = "ja"
DEFAULT_SPEAKER_CREDIT = "Kokoro 82M local ONNX (jf_alpha, Japanese)"
DEFAULT_CHUNK_CHARS = 80
DEFAULT_MAX_TEXT_CHARS = 4000
DEFAULT_STREAM_CONTENT_TYPE = "application/x-ndjson; charset=utf-8"
_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])|\n+")

try:
    from kokoro_onnx import Kokoro as _Kokoro
except ImportError as exc:  # pragma: no cover - depends on the local runtime.
    _Kokoro = None
    _KOKORO_IMPORT_ERROR = f"kokoro_onnx import failed: {exc}"
else:
    _KOKORO_IMPORT_ERROR = ""


class GatewayError(RuntimeError):
    """A controlled client-facing gateway failure."""


@dataclass(frozen=True)
class Settings:
    """Immutable process configuration sourced from CLI flags and the env."""

    host: str
    port: int
    runtime_root: Path
    model_path: Path
    voices_path: Path
    voice: str
    lang_code: str
    speaker_credit: str
    chunk_chars: int
    max_text_chars: int
    speed_min: float
    speed_max: float
    request_timeout: float
    lipsync_fps: float


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _first_existing(root: Path, names: tuple[str, ...]) -> Path:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return root / names[0]


def _resolve_model_paths(runtime_root: Path, model_arg: str, voices_arg: str) -> tuple[Path, Path]:
    """Resolve an explicit file or the conventional local Kokoro model dir."""

    model_value = Path(model_arg).expanduser()
    if not model_value.is_absolute():
        model_value = (runtime_root / model_value).resolve()
    model_root = model_value if model_value.is_dir() else model_value.parent
    if model_value.suffix.lower() == ".onnx":
        model_path = model_value
    else:
        model_path = _first_existing(
            model_root,
            ("kokoro-v1.0.onnx", "kokoro-v1.1.onnx", "kokoro.onnx", "model.onnx"),
        )

    voices_value = Path(voices_arg).expanduser()
    if not voices_value.is_absolute():
        voices_value = (runtime_root / voices_value).resolve()
    if voices_value.is_file():
        voices_path = voices_value
    else:
        voices_path = _first_existing(
            voices_value if voices_value.is_dir() else model_root,
            ("voices-v1.0.bin", "voices-v1.1.bin", "voices.bin"),
        )
    return model_path.resolve(), voices_path.resolve()


def settings_from_args(argv: list[str] | None = None) -> Settings:
    """Build settings without touching the model or starting a service."""

    runtime_root = Path(
        os.environ.get("LUMINA_RUNTIME_ROOT") or Path(__file__).resolve().parents[1]
    ).expanduser().resolve()
    default_model = os.environ.get(
        "KOKORO_TTS_MODEL_DIR", str(runtime_root / "data" / "kokoro_tts" / "models" / "kokoro")
    )
    default_model = os.environ.get("KOKORO_TTS_MODEL", default_model)
    default_voices = os.environ.get("KOKORO_TTS_VOICES_FILE")
    if not default_voices:
        default_voices = str(Path(default_model).expanduser().parent) if Path(default_model).suffix.lower() == ".onnx" else default_model

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("KOKORO_TTS_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("KOKORO_TTS_PORT", DEFAULT_PORT, 1, 65535),
    )
    parser.add_argument("--model", default=default_model, help="ONNX model file or model directory")
    parser.add_argument("--voices", default=default_voices, help="voice .bin file or model directory")
    parser.add_argument("--voice", default=os.environ.get("KOKORO_TTS_VOICE", DEFAULT_VOICE))
    parser.add_argument("--lang-code", default=os.environ.get("KOKORO_TTS_LANG_CODE", DEFAULT_LANG_CODE))
    parser.add_argument(
        "--speaker-credit",
        default=os.environ.get("KOKORO_TTS_SPEAKER_CREDIT", DEFAULT_SPEAKER_CREDIT),
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=_env_int("KOKORO_TTS_CHUNK_CHARS", DEFAULT_CHUNK_CHARS, 20, 240),
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=_env_int("KOKORO_TTS_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS, 80, 20000),
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=_env_float("KOKORO_TTS_REQUEST_TIMEOUT", 180.0, 1.0, 900.0),
    )
    parser.add_argument(
        "--lipsync-fps",
        type=float,
        default=_env_float("KOKORO_TTS_LIPSYNC_FPS", 75.0, 10.0, 120.0),
    )
    args = parser.parse_args(argv)
    host = str(args.host).strip() or DEFAULT_HOST
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"KOKORO TTS host must remain loopback-only; refused: {host}")
    model_path, voices_path = _resolve_model_paths(runtime_root, args.model, args.voices)
    return Settings(
        host=host,
        port=int(args.port),
        runtime_root=runtime_root,
        model_path=model_path,
        voices_path=voices_path,
        voice=str(args.voice).strip() or DEFAULT_VOICE,
        lang_code=str(args.lang_code).strip() or DEFAULT_LANG_CODE,
        speaker_credit=str(args.speaker_credit).strip() or DEFAULT_SPEAKER_CREDIT,
        chunk_chars=max(20, min(240, int(args.chunk_chars))),
        max_text_chars=max(80, min(20000, int(args.max_text_chars))),
        speed_min=0.5,
        speed_max=2.0,
        request_timeout=max(1.0, min(900.0, float(args.request_timeout))),
        lipsync_fps=max(10.0, min(120.0, float(args.lipsync_fps))),
    )


def split_text_chunks(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split Japanese prose at sentence or soft punctuation boundaries."""

    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return []
    max_chars = max(20, min(240, int(max_chars)))
    result: list[str] = []
    for sentence in _BOUNDARY_RE.split(normalized):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            candidates = [sentence.rfind(mark, 0, max_chars + 1) for mark in ("、", "，", ",", " ")]
            cut = max(candidates)
            if cut < 20:
                cut = max_chars
            end = cut + 1 if sentence[cut:cut + 1] in {"、", "，", ","} else cut
            piece = sentence[:end].strip()
            if piece:
                result.append(piece)
            sentence = sentence[end:].strip()
        if sentence:
            result.append(sentence)
    return result


def _clamp_speed(value: Any, settings: Settings) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        speed = 1.0
    if not math.isfinite(speed):
        speed = 1.0
    return max(settings.speed_min, min(settings.speed_max, speed))


def _pcm16(samples: Any) -> bytes:
    """Convert numpy-like float samples to signed mono PCM16."""

    if hasattr(samples, "tolist"):
        samples = samples.tolist()
    while isinstance(samples, (list, tuple)) and samples and isinstance(samples[0], (list, tuple)):
        samples = samples[0]
    if not isinstance(samples, (list, tuple)):
        raise GatewayError("Kokoro returned an unsupported sample buffer")
    packed = bytearray()
    for sample in samples:
        try:
            value = float(sample)
        except (TypeError, ValueError) as exc:
            raise GatewayError("Kokoro returned a non-numeric sample") from exc
        if not math.isfinite(value):
            value = 0.0
        if -1.5 <= value <= 1.5:
            value *= 32767.0
        packed.extend(struct.pack("<h", max(-32768, min(32767, int(round(value))))))
    if not packed:
        raise GatewayError("Kokoro returned empty audio")
    return bytes(packed)


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap mono PCM16 in a standard RIFF/WAVE envelope."""

    if not pcm or sample_rate <= 0:
        raise GatewayError("audio buffer or sample rate is invalid")
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def lipsync_from_pcm(pcm: bytes, sample_rate: int, fps: float) -> dict[str, Any]:
    """Produce deterministic five-channel mouth-envelope frames."""

    samples = memoryview(pcm).cast("h")
    window = max(1, int(round(sample_rate / fps)))
    frames: list[list[float]] = []
    for start in range(0, len(samples), window):
        window_samples = samples[start:start + window]
        if not window_samples:
            continue
        energy = math.sqrt(sum(sample * sample for sample in window_samples) / len(window_samples)) / 32768.0
        energy = max(0.0, min(1.0, energy * 4.0))
        frames.append([
            round(min(1.0, energy * 1.20), 4),
            round(min(1.0, energy * 0.85), 4),
            round(min(1.0, energy * 0.65), 4),
            round(min(1.0, energy * 0.95), 4),
            round(min(1.0, energy * 0.75), 4),
        ])
    return {"source": "wav_envelope", "fps": fps, "frames": frames}


class KokoroEngine:
    """Thread-safe lazy Kokoro ONNX model wrapper."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any = None
        self._error = ""
        self._load_lock = threading.Lock()
        self._synthesis_lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            if _Kokoro is None:
                self._error = _KOKORO_IMPORT_ERROR or "kokoro_onnx is unavailable"
                raise GatewayError(self._error)
            if not self.settings.model_path.is_file():
                self._error = f"ONNX model not found: {self.settings.model_path}"
                raise GatewayError(self._error)
            if not self.settings.voices_path.is_file():
                self._error = f"voice pack not found: {self.settings.voices_path}"
                raise GatewayError(self._error)
            try:
                kwargs: dict[str, Any] = {}
                try:
                    parameters = inspect.signature(_Kokoro).parameters
                except (TypeError, ValueError):
                    parameters = {}
                if "providers" in parameters:
                    kwargs["providers"] = ["CPUExecutionProvider"]
                self._model = _Kokoro(str(self.settings.model_path), str(self.settings.voices_path), **kwargs)
            except Exception as exc:  # Model errors should become actionable health data.
                self._error = f"Kokoro CPU ONNX initialization failed: {exc}"
                raise GatewayError(self._error) from exc
            self._error = ""
            return self._model

    def health(self) -> dict[str, Any]:
        """Return a JSON-safe health snapshot, including local prerequisites."""

        try:
            self._load()
        except GatewayError:
            pass
        return {
            "status": "ok" if self._model is not None else "unavailable",
            "ok": self._model is not None,
            "ready": self._model is not None,
            "gateway": True,
            "backend": "kokoro",
            "configured_backend": "kokoro",
            "runtime": "onnx",
            "device": "cpu",
            "onnx_providers": ["CPUExecutionProvider"],
            "model": str(self.settings.model_path),
            "model_file": str(self.settings.model_path),
            "model_exists": self.settings.model_path.is_file(),
            "voices_file": str(self.settings.voices_path),
            "voices_exists": self.settings.voices_path.is_file(),
            "voice": self.settings.voice,
            "lang_code": self.settings.lang_code,
            "tts_speaker_credit": self.settings.speaker_credit,
            "chunk_chars": self.settings.chunk_chars,
            "error": self._error or None,
        }

    def synthesize(self, text: str, voice: str, speed: float, lang_code: str) -> tuple[bytes, int]:
        """Synthesize one text chunk and return PCM16 plus sample rate."""

        model = self._load()
        with self._synthesis_lock:
            try:
                created = model.create(text, voice=voice, speed=speed, lang=lang_code)
            except Exception as exc:
                self._error = f"Kokoro synthesis failed: {exc}"
                raise GatewayError(self._error) from exc
        if not isinstance(created, tuple) or len(created) != 2:
            raise GatewayError("Kokoro returned an unexpected synthesis result")
        samples, sample_rate = created
        try:
            sample_rate = int(sample_rate)
        except (TypeError, ValueError) as exc:
            raise GatewayError("Kokoro returned an invalid sample rate") from exc
        return _pcm16(samples), sample_rate


def _request_text(payload: dict[str, Any], settings: Settings) -> str:
    text = payload.get("text", payload.get("input", ""))
    if not isinstance(text, str):
        raise GatewayError("text must be a string")
    text = text.strip()
    if not text:
        raise GatewayError("text is required")
    if len(text) > settings.max_text_chars:
        raise GatewayError(f"text exceeds {settings.max_text_chars} characters")
    return text


def _synthesis_options(payload: dict[str, Any], settings: Settings) -> tuple[str, float, str]:
    voice = str(payload.get("voice") or settings.voice).strip() or settings.voice
    lang_code = str(payload.get("lang_code", payload.get("lang", settings.lang_code))).strip() or settings.lang_code
    return voice, _clamp_speed(payload.get("speed", 1.0), settings), lang_code


def synthesize_payload(payload: dict[str, Any], engine: KokoroEngine) -> dict[str, Any]:
    """Build the established Lumina JSON response from sentence chunks."""

    text = _request_text(payload, engine.settings)
    voice, speed, lang_code = _synthesis_options(payload, engine.settings)
    chunks = split_text_chunks(text, engine.settings.chunk_chars)
    pcm_parts: list[bytes] = []
    sample_rate = 0
    started = time.perf_counter()
    for chunk in chunks:
        pcm, rate = engine.synthesize(chunk, voice, speed, lang_code)
        if sample_rate and rate != sample_rate:
            raise GatewayError("Kokoro changed sample rate between chunks")
        sample_rate = rate
        pcm_parts.append(pcm)
    pcm = b"".join(pcm_parts)
    wav_bytes = pcm_to_wav(pcm, sample_rate)
    return {
        "ok": True,
        "ready": True,
        "backend": "kokoro",
        "configured_backend": "kokoro",
        "runtime": "onnx",
        "device": "cpu",
        "text": text,
        "voice": voice,
        "lang_code": lang_code,
        "speed": speed,
        "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
        "audio_mime": "audio/wav",
        "audio_bytes": len(wav_bytes),
        "sample_rate": sample_rate,
        "duration_seconds": round(len(pcm) / (2 * sample_rate), 4),
        "chunk_count": len(chunks),
        "chunks": chunks,
        "lipsync": lipsync_from_pcm(pcm, sample_rate, engine.settings.lipsync_fps),
        "tts_speaker_credit": engine.settings.speaker_credit,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


def iter_stream_payload(payload: dict[str, Any], engine: KokoroEngine) -> Iterator[dict[str, Any]]:
    """Yield sentence-level audio events without buffering the complete utterance."""

    text = _request_text(payload, engine.settings)
    voice, speed, lang_code = _synthesis_options(payload, engine.settings)
    chunks = split_text_chunks(text, engine.settings.chunk_chars)
    started = time.perf_counter()
    yield {
        "event": "start",
        "ok": True,
        "backend": "kokoro",
        "runtime": "onnx",
        "device": "cpu",
        "text": text,
        "voice": voice,
        "lang_code": lang_code,
        "speed": speed,
        "chunk_count": len(chunks),
        "tts_speaker_credit": engine.settings.speaker_credit,
    }
    total_bytes = 0
    for index, chunk in enumerate(chunks):
        pcm, sample_rate = engine.synthesize(chunk, voice, speed, lang_code)
        wav_bytes = pcm_to_wav(pcm, sample_rate)
        total_bytes += len(wav_bytes)
        yield {
            "event": "chunk",
            "index": index,
            "text": chunk,
            "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
            "audio_mime": "audio/wav",
            "audio_bytes": len(wav_bytes),
            "sample_rate": sample_rate,
            "duration_seconds": round(len(pcm) / (2 * sample_rate), 4),
            "lipsync": lipsync_from_pcm(pcm, sample_rate, engine.settings.lipsync_fps),
        }
    yield {
        "event": "done",
        "ok": True,
        "chunk_count": len(chunks),
        "audio_bytes": total_bytes,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class GatewayHandler(BaseHTTPRequestHandler):
    """HTTP compatibility handler with bounded input and explicit errors."""

    server_version = "LuminaKokoroONNXGateway/1.0"
    protocol_version = "HTTP/1.1"
    engine: KokoroEngine

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[kokoro-gateway] {fmt % args}", flush=True)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"ok": False, "error": code, "message": message, "backend": "kokoro"})

    def _read_payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise GatewayError("Content-Length must be an integer") from exc
        if length <= 0:
            raise GatewayError("JSON request body is required")
        if length > 1_048_576:
            raise GatewayError("request body exceeds 1 MiB")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("request body is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise GatewayError("request body must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self._send_json(200, self.engine.health())
            return
        if path == "/":
            self._send_json(200, {"ok": True, "service": "kokoro-tts-gateway", "health": "/health"})
            return
        self._send_error(404, "not_found", "endpoint not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            payload = self._read_payload()
            if path in {"/voice-lipsync/stream", "/stream"} or (
                path == "/voice-lipsync" and str(payload.get("stream", "")).lower() in {"1", "true", "yes"}
            ):
                self._send_stream(payload)
                return
            if path in {"/voice-lipsync", "/tts", "/synthesize"}:
                result = synthesize_payload(payload, self.engine)
                self._send_json(200, result)
                return
            if path in {"/voice", "/v1/audio/speech"}:
                result = synthesize_payload(payload, self.engine)
                response_format = str(payload.get("response_format", "wav")).lower()
                if response_format in {"json", "base64"}:
                    self._send_json(200, result)
                    return
                audio = base64.b64decode(result["audio_base64"])
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(audio)
                return
            self._send_error(404, "not_found", "endpoint not found")
        except GatewayError as exc:
            status = 503 if "Kokoro" in str(exc) or "not found" in str(exc) else 422
            self._send_error(status, "tts_unavailable" if status == 503 else "invalid_request", str(exc))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # Defensive boundary: do not leak a traceback to clients.
            self._send_error(500, "gateway_error", f"unexpected gateway error: {exc}")

    def _send_stream(self, payload: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", DEFAULT_STREAM_CONTENT_TYPE)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for event in iter_stream_payload(payload, self.engine):
                self.wfile.write(_json_bytes(event))
                self.wfile.flush()
        except GatewayError as exc:
            self.wfile.write(_json_bytes({"event": "error", "ok": False, "error": str(exc)}))
            self.wfile.flush()


def run(settings: Settings) -> None:
    """Run the local-only threaded HTTP server until interrupted."""

    engine = KokoroEngine(settings)
    GatewayHandler.engine = engine
    server = ThreadingHTTPServer((settings.host, settings.port), GatewayHandler)
    server.daemon_threads = True
    print(
        f"Lumina Kokoro CPU ONNX gateway listening on {settings.host}:{settings.port} "
        f"model={settings.model_path} voice={settings.voice} lang={settings.lang_code}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Lumina Kokoro gateway stopped", flush=True)
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    settings = settings_from_args(argv)
    try:
        run(settings)
    except OSError as exc:
        print(f"[kokoro-gateway] failed to bind {settings.host}:{settings.port}: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
