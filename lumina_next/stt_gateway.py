"""Local CPU SenseVoice speech-to-text gateway.

Audio is decoded in memory. Non-WAV decoding uses an ffmpeg stdin/stdout pipe,
so this service has no audio persistence path.

Performance notes (same SenseVoice INT8 weights):
- PCM is decoded to compact float32 buffers (numpy when available) instead of
  a Python ``list[float]``, which cuts decode CPU and peak object churn.
- Default recognizer threads are capped at 2 to leave headroom for TTS/bridge
  on 16GB Macs; override with ``LUMINA_STT_THREADS``.
- Short adjacent Silero segments are coalesced before decode to reduce the
  number of SenseVoice forward passes without changing the model.
"""

from __future__ import annotations

import gc
import io
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

LOGGER = logging.getLogger("lumina.stt")
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_AUDIO_SECONDS = 300
DEFAULT_PORT = 5057
SERVER_REVISION = "2026-08-10-product-v7"
DEFAULT_MODEL_DIR = Path(
    "data/stt_models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
)
DEFAULT_INFERENCE_TIMEOUT_SECONDS = 120.0
DEFAULT_VAD_PAD_SECONDS = 0.25
# Coalesce chatty Silero splits: each piece ≤1.5s, merged span ≤8s.
COALESCE_MAX_PIECE_SAMPLES = int(SAMPLE_RATE * 1.5)
COALESCE_MAX_MERGED_SAMPLES = int(SAMPLE_RATE * 8.0)
SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]+\|>")
JAPANESE_CHAR_CLASS = r"\u3040-\u30ff\u3400-\u9fffー々"
SUPPORTED_STT_LANGUAGES = frozenset({"auto", "en", "ja"})

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover - product venv ships numpy
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


class GatewayError(Exception):
    """Expected client or local-runtime error with an HTTP mapping."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def normalize_stt_language(value: Any) -> str:
    """Validate the product's per-request SenseVoice language contract."""

    normalized = str(value or "auto").strip().lower()
    if normalized not in SUPPORTED_STT_LANGUAGES:
        raise GatewayError(
            "language must be auto, en, or ja",
            400,
            "invalid_language",
        )
    return normalized


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    model: Path
    tokens: Path
    vad_model: Path
    ffmpeg: str
    threads: int
    language: str
    vad_threshold: float
    min_silence_seconds: float
    min_speech_seconds: float
    max_speech_seconds: float
    max_audio_seconds: int
    inference_timeout_seconds: float
    vad_buffer_seconds: float
    vad_pad_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.environ.get("LUMINA_RUNTIME_ROOT", Path.cwd())).expanduser()
        model_dir = Path(
            os.environ.get("LUMINA_STT_MODEL_DIR", str(root / DEFAULT_MODEL_DIR))
        ).expanduser()
        default_vad = root / "data/stt_models/silero_vad.onnx"
        if not default_vad.is_file():
            default_vad = model_dir / "silero_vad.onnx"

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise GatewayError(
                    f"{name} must be an integer", 500, "invalid_configuration"
                ) from exc
            return max(minimum, min(maximum, value))

        def number(name: str, default: float, minimum: float, maximum: float) -> float:
            try:
                value = float(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise GatewayError(
                    f"{name} must be numeric", 500, "invalid_configuration"
                ) from exc
            if not math.isfinite(value):
                raise GatewayError(f"{name} must be finite", 500, "invalid_configuration")
            return max(minimum, min(maximum, value))

        def loopback_host(name: str, fallback: str) -> str:
            value = os.environ.get(name, fallback).strip() or fallback
            if value not in {"127.0.0.1", "localhost", "::1"}:
                raise GatewayError(
                    f"{name} must remain loopback-only", 500, "invalid_configuration"
                )
            return value

        # Default 2 threads: SenseVoice INT8 saturates quickly; extra threads
        # mostly contend with Irodori TTS / bridge on 16GB machines.
        default_threads = min(2, os.cpu_count() or 1)
        max_speech = number(
            "LUMINA_STT_VAD_MAX_SPEECH_SECONDS", 30.0, 1.0, 120.0
        )
        return cls(
            host=loopback_host("LUMINA_STT_HOST", "127.0.0.1"),
            port=integer("LUMINA_STT_PORT", DEFAULT_PORT, 1, 65535),
            model=Path(
                os.environ.get(
                    "LUMINA_STT_SENSEVOICE_MODEL", str(model_dir / "model.int8.onnx")
                )
            ).expanduser(),
            tokens=Path(
                os.environ.get("LUMINA_STT_TOKENS", str(model_dir / "tokens.txt"))
            ).expanduser(),
            vad_model=Path(
                os.environ.get("LUMINA_STT_SILERO_VAD_MODEL", str(default_vad))
            ).expanduser(),
            ffmpeg=os.environ.get("LUMINA_STT_FFMPEG_BIN", "ffmpeg"),
            threads=integer("LUMINA_STT_THREADS", default_threads, 1, 32),
            language=normalize_stt_language(
                os.environ.get("LUMINA_STT_LANGUAGE", "auto") or "auto"
            ),
            vad_threshold=number("LUMINA_STT_VAD_THRESHOLD", 0.5, 0.05, 0.99),
            min_silence_seconds=number(
                "LUMINA_STT_VAD_MIN_SILENCE_SECONDS", 0.25, 0.05, 5.0
            ),
            min_speech_seconds=number(
                "LUMINA_STT_VAD_MIN_SPEECH_SECONDS", 0.1, 0.03, 5.0
            ),
            max_speech_seconds=max_speech,
            max_audio_seconds=integer(
                "LUMINA_STT_MAX_AUDIO_SECONDS",
                DEFAULT_MAX_AUDIO_SECONDS,
                1,
                DEFAULT_MAX_AUDIO_SECONDS,
            ),
            inference_timeout_seconds=number(
                "LUMINA_STT_INFERENCE_TIMEOUT_SECONDS",
                DEFAULT_INFERENCE_TIMEOUT_SECONDS,
                1.0,
                600.0,
            ),
            # Keep VAD ring buffer just above max speech span (not a fixed 30s
            # when operators lower max speech for lighter RAM).
            vad_buffer_seconds=number(
                "LUMINA_STT_VAD_BUFFER_SECONDS",
                max(max_speech + 1.0, 8.0),
                2.0,
                120.0,
            ),
            vad_pad_seconds=number(
                "LUMINA_STT_VAD_PAD_SECONDS",
                DEFAULT_VAD_PAD_SECONDS,
                0.05,
                1.0,
            ),
        )


def clean_text(value: Any) -> str:
    text = SPECIAL_TOKEN_RE.sub("", str(value or ""))
    text = " ".join(text.replace("\u3000", " ").split())
    # SenseVoice sometimes emits tokenization spaces between Japanese
    # characters (``疲れ てる みた い``).  They are not word boundaries and
    # degrade both the visible transcript and downstream translation.  Remove
    # only Japanese-to-Japanese spacing so ordinary English word spaces and
    # mixed identifiers such as ``USB C`` remain untouched.
    text = re.sub(
        rf"(?<=[{JAPANESE_CHAR_CLASS}])\s+(?=[{JAPANESE_CHAR_CLASS}])",
        "",
        text,
    )
    text = re.sub(r"\s+([、。！？!?])", r"\1", text)
    # ASR punctuation can split a Japanese copula mid-token (``で、すね``).
    # This narrow repair also preserves the meaning of ordinary ``で、すぐ``
    # contexts while avoiding broad punctuation deletion.
    text = re.sub(r"で[、,]\s*す(?=(?:ね|よ|。|！|!|$))", "です", text)
    return text.strip()


def join_transcript_segments(values: list[str]) -> str:
    """Join independently normalized VAD segments without losing boundaries.

    ``clean_text`` intentionally removes Japanese tokenization spaces.  Calling
    it again over a space-joined list of independently decoded segments would
    therefore merge two Japanese speech spans into one token stream.  Preserve
    a Japanese VAD boundary with a newline (which the translation client later
    normalizes to a space), while keeping the ordinary word-space boundary used
    by English and mixed text.  A Silero segment is not necessarily a sentence,
    so this function must not invent punctuation.
    """

    segments = [clean_text(value) for value in values]
    segments = [value for value in segments if value]
    if not segments:
        return ""
    joined = segments[0]
    japanese = re.compile(rf"[{JAPANESE_CHAR_CLASS}]")
    previous = segments[0]
    for segment in segments[1:]:
        left_is_japanese = bool(japanese.search(previous))
        right_is_japanese = bool(japanese.search(segment))
        if left_is_japanese and right_is_japanese:
            joined += "\n" + segment
        else:
            joined += " " + segment
        previous = segment
    return joined.strip()


def _pcm_to_float_python(
    raw: bytes, sample_width: int, channels: int, frames: int
) -> array:
    """Stdlib fallback PCM→mono float32 ``array('f')``."""

    frame_size = sample_width * channels
    expected = frames * frame_size
    scale = float(1 << (sample_width * 8 - 1))
    result = array("f")
    append = result.append
    for offset in range(0, expected, frame_size):
        total = 0.0
        for channel in range(channels):
            start = offset + channel * sample_width
            chunk = raw[start : start + sample_width]
            if sample_width == 1:
                value, channel_scale = chunk[0] - 128, 128.0
            elif sample_width == 3:
                signed = chunk + (b"\xff" if chunk[2] & 0x80 else b"\x00")
                value = int.from_bytes(signed, "little", signed=True)
                channel_scale = scale
            else:
                value = int.from_bytes(chunk, "little", signed=True)
                channel_scale = scale
            total += max(-1.0, min(1.0, value / channel_scale))
        append(total / channels)
    return result


def pcm_to_float(raw: bytes, sample_width: int, channels: int, frames: int) -> Any:
    """Convert PCM frames to mono float32 samples without a temporary file.

    Returns a numpy ``float32`` vector when numpy is available, otherwise an
    ``array('f')``. Either form is accepted by sherpa-onnx waveform APIs.
    """

    if channels < 1 or sample_width not in (1, 2, 3, 4):
        raise GatewayError("unsupported PCM WAV format", 415, "unsupported_audio")
    frame_size = sample_width * channels
    expected = frames * frame_size
    if expected > len(raw):
        raise GatewayError("truncated WAV audio", 400, "invalid_audio")
    raw = raw[:expected]

    if _HAS_NUMPY:
        assert np is not None
        if sample_width == 2 and channels == 1:
            # Hot path: product clients send 16 kHz mono s16le WAV.
            return (
                np.frombuffer(raw, dtype="<i2").astype(np.float32, copy=False)
                * np.float32(1.0 / 32768.0)
            )
        if sample_width == 2:
            multi = np.frombuffer(raw, dtype="<i2").reshape(frames, channels)
            return multi.mean(axis=1, dtype=np.float32) * np.float32(1.0 / 32768.0)
        if sample_width == 1 and channels == 1:
            return (
                np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - np.float32(128.0)
            ) * np.float32(1.0 / 128.0)
        if sample_width == 4 and channels == 1:
            return (
                np.frombuffer(raw, dtype="<i4").astype(np.float32, copy=False)
                * np.float32(1.0 / 2147483648.0)
            )

    return _pcm_to_float_python(raw, sample_width, channels, frames)


def _s16le_mono_to_float(raw: bytes) -> Any:
    """Decode ffmpeg s16le mono PCM into a compact float32 buffer."""

    trimmed = raw[: len(raw) - (len(raw) % 2)]
    if _HAS_NUMPY:
        assert np is not None
        return (
            np.frombuffer(trimmed, dtype="<i2").astype(np.float32, copy=False)
            * np.float32(1.0 / 32768.0)
        )
    pcm = array("h")
    pcm.frombytes(trimmed)
    return array("f", (value / 32768.0 for value in pcm))


def sample_length(samples: Any) -> int:
    return int(len(samples))


def as_float_waveform(samples: Any) -> Any:
    """Normalize samples to a sherpa-friendly float32 waveform buffer."""

    if isinstance(samples, array) and samples.typecode == "f":
        return samples
    if _HAS_NUMPY:
        assert np is not None
        if isinstance(samples, np.ndarray):
            if samples.dtype == np.float32 and samples.flags.c_contiguous:
                return samples
            return np.ascontiguousarray(samples, dtype=np.float32)
        return np.ascontiguousarray(samples, dtype=np.float32)
    return array("f", samples)


def coalesce_speech_segments(
    segments: Sequence[Any],
    *,
    max_piece_samples: int = COALESCE_MAX_PIECE_SAMPLES,
    max_merged_samples: int = COALESCE_MAX_MERGED_SAMPLES,
) -> list[Any]:
    """Merge chatty short Silero pieces to cut SenseVoice forward passes."""

    if len(segments) <= 1:
        return list(segments)
    if _HAS_NUMPY:
        assert np is not None
        out: list[Any] = []
        buf: Any | None = None
        for segment in segments:
            piece = as_float_waveform(segment)
            if buf is None:
                buf = piece
                continue
            if (
                sample_length(buf) <= max_piece_samples
                and sample_length(piece) <= max_piece_samples
                and sample_length(buf) + sample_length(piece) <= max_merged_samples
            ):
                buf = np.concatenate([buf, piece])
            else:
                out.append(buf)
                buf = piece
        if buf is not None:
            out.append(buf)
        return out

    out_arr: list[array] = []
    buf_arr: array | None = None
    for segment in segments:
        piece_arr = array("f", segment) if not isinstance(segment, array) else segment
        if buf_arr is None:
            buf_arr = array("f", piece_arr)
            continue
        if (
            len(buf_arr) <= max_piece_samples
            and len(piece_arr) <= max_piece_samples
            and len(buf_arr) + len(piece_arr) <= max_merged_samples
        ):
            buf_arr.extend(piece_arr)
        else:
            out_arr.append(buf_arr)
            buf_arr = array("f", piece_arr)
    if buf_arr is not None:
        out_arr.append(buf_arr)
    return out_arr


def decode_wav(audio: bytes, max_seconds: int) -> Any:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise GatewayError("compressed WAV is unsupported", 415, "unsupported_audio")
            rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            frames = wav.getnframes()
            if rate != SAMPLE_RATE:
                raise GatewayError(
                    "WAV must be 16 kHz; use a client or ffmpeg to resample",
                    415,
                    "unsupported_audio",
                )
            if frames > SAMPLE_RATE * max_seconds:
                raise GatewayError("audio exceeds the maximum duration", 413, "audio_too_large")
            return pcm_to_float(wav.readframes(frames), width, channels, frames)
    except (wave.Error, EOFError) as exc:
        raise GatewayError(f"invalid WAV audio: {exc}", 400, "invalid_audio") from exc


def decode_audio(audio: bytes, settings: Settings) -> Any:
    if not audio:
        raise GatewayError("audio file is empty", 400, "invalid_audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise GatewayError("audio exceeds 25 MiB", 413, "audio_too_large")
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return decode_wav(audio, settings.max_audio_seconds)
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-t",
        str(settings.max_audio_seconds),
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            input=audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GatewayError("audio decode timed out", 504, "timeout") from exc
    except OSError as exc:
        raise GatewayError("audio decoder is unavailable", 415, "unsupported_audio") from exc
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace")[-300:].strip()
        suffix = f": {detail}" if detail else ""
        raise GatewayError(f"unable to decode audio{suffix}", 415, "unsupported_audio")
    if len(result.stdout) > settings.max_audio_seconds * SAMPLE_RATE * 2:
        raise GatewayError("audio exceeds the maximum duration", 413, "audio_too_large")
    return _s16le_mono_to_float(result.stdout)


class SenseVoiceRuntime:
    """Own one CPU recognizer and VAD; inference is serialized by a lock."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._recognizer: Any = None
        self._active_language = ""
        self._sherpa_onnx: Any = None
        self._vad: Any = None
        self._sherpa_version = "unknown"
        self._error = ""
        self._load()

    def _load(self) -> None:
        required = (self.settings.model, self.settings.tokens, self.settings.vad_model)
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            self._error = "missing model artifacts: " + ", ".join(missing)
            return
        try:
            import sherpa_onnx  # type: ignore[import-not-found]

            self._sherpa_onnx = sherpa_onnx
            self._sherpa_version = str(getattr(sherpa_onnx, "__version__", "installed"))
            initial_language = normalize_stt_language(self.settings.language)
            self._recognizer = self._create_recognizer(initial_language)
            self._active_language = initial_language
            silero = sherpa_onnx.SileroVadModelConfig(
                model=str(self.settings.vad_model),
                threshold=self.settings.vad_threshold,
                min_silence_duration=self.settings.min_silence_seconds,
                min_speech_duration=self.settings.min_speech_seconds,
                max_speech_duration=self.settings.max_speech_seconds,
                window_size=512,
            )
            vad_config = sherpa_onnx.VadModelConfig(
                silero_vad=silero,
                sample_rate=SAMPLE_RATE,
                num_threads=1,
                provider="cpu",
            )
            self._vad = sherpa_onnx.VoiceActivityDetector(
                vad_config, buffer_size_in_seconds=float(self.settings.vad_buffer_seconds)
            )
        except Exception as exc:
            self._recognizer = None
            self._active_language = ""
            self._vad = None
            self._error = f"sherpa-onnx initialization failed: {exc}"
            LOGGER.exception("unable to initialize SenseVoice runtime")

    def _create_recognizer(self, language: str) -> Any:
        return self._sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(self.settings.model),
                tokens=str(self.settings.tokens),
                num_threads=self.settings.threads,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
                language=language,
                use_itn=True,
            )

    def _select_language_locked(self, language: str) -> None:
        """Keep exactly one resident recognizer, replacing it on mode changes."""

        requested = normalize_stt_language(language)
        if self._recognizer is not None and requested == self._active_language:
            return
        # SenseVoice binds its language prompt when the recognizer is created;
        # mutating the Python config later does not affect decoding.  Drop the
        # prior native object before loading the replacement so fixed-direction
        # accuracy does not cost another permanently resident ~280 MB model.
        previous_language = self._active_language
        previous = self._recognizer
        self._recognizer = None
        self._active_language = ""
        del previous
        gc.collect()
        try:
            self._recognizer = self._create_recognizer(requested)
            self._active_language = requested
            self._error = ""
        except Exception as exc:
            restored = False
            if previous_language:
                try:
                    self._recognizer = self._create_recognizer(previous_language)
                    self._active_language = previous_language
                    self._error = ""
                    restored = True
                except Exception as restore_exc:
                    self._recognizer = None
                    self._active_language = ""
                    self._error = (
                        f"sherpa-onnx language switch failed: {exc}; "
                        f"restore of {previous_language} failed: {restore_exc}"
                    )
            if not restored and not self._error:
                self._error = f"sherpa-onnx language switch failed: {exc}"
            raise GatewayError(
                "speech runtime could not switch language",
                503,
                "runtime_unavailable",
            ) from exc

    def health(self) -> dict[str, Any]:
        # Snapshot without waiting on a long transcription lock so /health stays
        # responsive while still reflecting the last successful language mode.
        language = self._active_language or self.settings.language
        error = self._error
        return {
            "gateway": True,
            "ok": not error,
            "ready": not error,
            "provider": "sherpa-onnx",
            "model": self.settings.model.name,
            "vad": "silero",
            "device": "cpu",
            "sample_rate": SAMPLE_RATE,
            "threads": self.settings.threads,
            "language": language,
            "vad_buffer_seconds": self.settings.vad_buffer_seconds,
            "sherpa_onnx_version": self._sherpa_version,
            "server_revision": SERVER_REVISION,
            "error": error or None,
            "audio_persistence": False,
            "numpy_decode": _HAS_NUMPY,
        }

    def speech_segments(self, samples: Any) -> list[Any]:
        if self._vad is None:
            raise GatewayError("speech runtime is not ready", 503, "runtime_unavailable")
        reset = getattr(self._vad, "reset", None)
        if callable(reset):
            reset()
        waveform = as_float_waveform(samples)
        pad_n = int(SAMPLE_RATE * float(self.settings.vad_pad_seconds))
        if _HAS_NUMPY:
            assert np is not None
            if isinstance(waveform, np.ndarray):
                if pad_n > 0:
                    padded = np.concatenate(
                        [waveform, np.zeros(pad_n, dtype=np.float32)]
                    )
                else:
                    padded = waveform
            else:
                padded = array("f", waveform)
                if pad_n > 0:
                    padded.extend(array("f", [0.0]) * pad_n)
        else:
            padded = array("f", waveform) if not isinstance(waveform, array) else waveform
            if pad_n > 0:
                padded = array("f", padded)
                padded.extend(array("f", [0.0]) * pad_n)
        self._vad.accept_waveform(padded)
        flush = getattr(self._vad, "flush", None)
        if callable(flush):
            flush()
        segments: list[Any] = []
        while not self._vad.empty():
            segment = getattr(self._vad, "front")
            if callable(segment):
                segment = segment()
            values = getattr(segment, "samples", segment)
            if values is not None and sample_length(values) > 0:
                segments.append(as_float_waveform(values))
            self._vad.pop()
        return segments

    def transcribe(
        self,
        samples: Any,
        *,
        language: str | None = None,
    ) -> tuple[str, float]:
        if self._error or self._recognizer is None:
            raise GatewayError(
                self._error or "speech runtime is not ready", 503, "runtime_unavailable"
            )
        if samples is None or sample_length(samples) == 0:
            raise GatewayError("audio contains no samples", 400, "invalid_audio")
        started = time.perf_counter()
        deadline = started + float(self.settings.inference_timeout_seconds)
        with self._lock:
            self._select_language_locked(language or self.settings.language)
            texts: list[str] = []
            segments = self.speech_segments(samples)
            if not segments:
                # Never run the recognizer over an all-noise/full microphone
                # clip.  This is the essential hallucination guard.
                raise GatewayError("no speech detected", 422, "empty_transcript")
            speech_samples = sum(sample_length(segment) for segment in segments)
            # Once Silero has positively found speech, a very short segment may
            # safely use the whole clip so the first/last phoneme is retained.
            if speech_samples < int(SAMPLE_RATE * 0.35):
                segments = [as_float_waveform(samples)]
            elif os.environ.get("LUMINA_STT_COALESCE_SEGMENTS", "0").strip() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                # Optional: merge chatty micro-splits. Off by default so Japanese
                # VAD boundaries stay visible to downstream translation.
                segments = coalesce_speech_segments(segments)
            for segment in segments:
                if time.perf_counter() > deadline:
                    raise GatewayError(
                        "speech recognition timed out",
                        504,
                        "timeout",
                    )
                stream = self._recognizer.create_stream()
                stream.accept_waveform(SAMPLE_RATE, segment)
                self._recognizer.decode_stream(stream)
                result = getattr(stream, "result", None)
                text = clean_text(getattr(result, "text", ""))
                if text:
                    texts.append(text)
        return join_transcript_segments(texts), (time.perf_counter() - started) * 1000.0


def parse_multipart(body: bytes, content_type: str) -> tuple[bytes, dict[str, str]]:
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    try:
        message = BytesParser(policy=email_policy).parsebytes(envelope)
    except Exception as exc:
        raise GatewayError("invalid multipart form data", 400, "invalid_request") from exc
    if not message.is_multipart():
        raise GatewayError("invalid multipart form data", 400, "invalid_request")
    audio: bytes | None = None
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        params = dict(part.get_params(header="content-disposition", failobj=[]))
        name = str(params.get("name", "")).strip('"')
        payload = part.get_payload(decode=True) or b""
        if name == "file" and audio is None:
            audio = payload
        elif name:
            fields[name] = payload.decode("utf-8", errors="replace")
    if audio is None:
        raise GatewayError("multipart field 'file' is required", 400, "missing_file")
    return audio, fields


class GatewayHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local OpenAI-compatible transcription endpoint."""

    server_version = "LuminaSenseVoice/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def runtime(self) -> SenseVoiceRuntime:
        return self.server.runtime  # type: ignore[attr-defined, no-any-return]

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, exc: GatewayError) -> None:
        self.send_json(
            {"error": {"message": str(exc), "type": exc.code, "code": exc.code}},
            exc.status,
        )

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] in ("/health", "/readyz"):
            health = self.runtime.health()
            self.send_json(health, 200 if health["ok"] else 503)
            return
        self.send_json(
            {"error": {"message": "not found", "type": "not_found", "code": "not_found"}},
            404,
        )

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/v1/audio/transcriptions":
            self.send_json(
                {
                    "error": {
                        "message": "not found",
                        "type": "not_found",
                        "code": "not_found",
                    }
                },
                404,
            )
            return
        try:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                raise GatewayError("Content-Length is required", 411, "length_required")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise GatewayError("invalid Content-Length", 400, "invalid_request") from exc
            if length <= 0:
                raise GatewayError("request body is empty", 400, "invalid_request")
            if length > MAX_AUDIO_BYTES + 1024 * 1024:
                raise GatewayError("request body is too large", 413, "audio_too_large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise GatewayError("request body was truncated", 400, "invalid_request")
            content_type = self.headers.get("Content-Type", "application/octet-stream")
            if content_type.lower().startswith("multipart/form-data"):
                audio, fields = parse_multipart(body, content_type)
            else:
                audio, fields = body, {}
            response_format = fields.get("response_format", "json").lower()
            if response_format not in ("json", "verbose_json", "text"):
                raise GatewayError(
                    "response_format must be json, verbose_json, or text",
                    400,
                    "invalid_request",
                )
            samples = decode_audio(audio, self.runtime.settings)
            if sample_length(samples) > self.runtime.settings.max_audio_seconds * SAMPLE_RATE:
                raise GatewayError("audio exceeds the maximum duration", 413, "audio_too_large")
            request_language = normalize_stt_language(
                fields.get("language", self.runtime.settings.language)
            )
            text, latency_ms = self.runtime.transcribe(
                samples,
                language=request_language,
            )
            if not text:
                raise GatewayError("no speech detected", 422, "empty_transcript")
            if response_format == "text":
                data = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            response: dict[str, Any] = {"text": text}
            if response_format == "verbose_json":
                response.update(
                    {
                        "task": "transcribe",
                        "language": request_language,
                        "duration": round(sample_length(samples) / SAMPLE_RATE, 3),
                        "model": self.runtime.settings.model.name,
                        "latency_ms": round(latency_ms, 2),
                    }
                )
            self.send_json(response)
        except GatewayError as exc:
            self.send_error_json(exc)
        except Exception:
            LOGGER.exception("unexpected transcription failure")
            self.send_error_json(
                GatewayError("internal transcription failure", 500, "internal_error")
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    """Start the gateway using environment-only configuration."""
    logging.basicConfig(
        level=os.environ.get("LUMINA_STT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings.from_env()
    runtime = SenseVoiceRuntime(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), GatewayHandler)
    server.runtime = runtime  # type: ignore[attr-defined]
    LOGGER.info(
        "SenseVoice STT listening on http://%s:%d ready=%s revision=%s",
        settings.host,
        settings.port,
        runtime.health()["ready"],
        SERVER_REVISION,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("shutdown requested")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
