from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .llm import ProviderError


@dataclass(frozen=True)
class STTResult:
    text: str
    raw_text: str
    latency_ms: float
    corrected: bool
    model: str


class STTProvider(Protocol):
    def health(self) -> dict[str, Any]: ...

    async def transcribe(self, audio: bytes, language: str | None = None) -> STTResult: ...


def _load_corrections(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(source): str(target)
        for source, target in raw.items()
        if str(source) and str(target)
    }


def normalize_transcript(text: str, corrections: dict[str, str]) -> str:
    normalized = " ".join(str(text or "").replace("\u3000", " ").split())
    normalized = re.sub(r"\s+([、。！？!?])", r"\1", normalized)
    for source in sorted(corrections, key=len, reverse=True):
        normalized = normalized.replace(source, corrections[source])
    return normalized.strip()


class SenseVoiceHttpProvider:
    """Product STT: OpenAI-compatible SenseVoice gateway (default :5057)."""

    def __init__(
        self,
        base_url: str,
        corrections_path: Path,
        *,
        language: str = "ja",
        timeout_seconds: float = 90.0,
        model_name: str = "sensevoice-int8",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.corrections_path = Path(corrections_path)
        self.language = str(language or "ja")
        self.timeout_seconds = float(timeout_seconds)
        self.model_name = str(model_name or "sensevoice-int8")
        self._lock = asyncio.Lock()
        self._corrections = _load_corrections(self.corrections_path)
        self._ready_cache: dict[str, Any] | None = None
        self._ready_cache_at = 0.0
        self._ready_cache_ttl_seconds = float(
            os.environ.get("LUMINA_STT_READY_CACHE_SECONDS", "5") or "5"
        )

    def health(self) -> dict[str, Any]:
        if not self.base_url:
            return {
                "ok": False,
                "provider": "sensevoice_http",
                "base_url": self.base_url,
                "error": "stt_url is empty",
            }
        request = urllib.request.Request(
            self.base_url + "/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                raw = response.read().decode("utf-8")
            payload = json.loads(raw)
        except (OSError, urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
            self._ready_cache = None
            return {
                "ok": False,
                "provider": "sensevoice_http",
                "base_url": self.base_url,
                "language": self.language,
                "error": str(exc),
            }
        if not isinstance(payload, dict):
            self._ready_cache = None
            return {
                "ok": False,
                "provider": "sensevoice_http",
                "base_url": self.base_url,
                "error": "health returned non-object JSON",
            }
        ready = bool(payload.get("ready") or payload.get("ok"))
        result = {
            "ok": ready,
            "provider": "sensevoice_http",
            "base_url": self.base_url,
            "model": payload.get("model") or self.model_name,
            "language": self.language,
            "gateway": payload,
            "corrections": len(self._corrections),
        }
        if ready:
            self._ready_cache = result
            self._ready_cache_at = time.perf_counter()
        else:
            self._ready_cache = None
        return result

    def _ensure_ready(self) -> dict[str, Any]:
        """Skip a synchronous /health round-trip when recently confirmed ready."""

        ttl = max(0.0, float(self._ready_cache_ttl_seconds))
        cached = self._ready_cache
        if (
            cached
            and cached.get("ok")
            and (time.perf_counter() - self._ready_cache_at) <= ttl
        ):
            return cached
        return self.health()

    def _transcribe_sync(self, audio: bytes, language: str | None = None) -> STTResult:
        if not audio:
            raise ProviderError("stt audio is empty")
        if len(audio) > 50 * 1024 * 1024:
            raise ProviderError("stt audio exceeds 50 MiB")
        health = self._ensure_ready()
        if not health["ok"]:
            raise ProviderError(f"stt gateway is not ready: {health.get('error') or health}")

        boundary = f"----lumina-stt-{int(time.time() * 1000)}"
        lang = str(language or self.language or "ja")
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode("utf-8")
            + audio
            + (
                f"\r\n--{boundary}\r\n"
                'Content-Disposition: form-data; name="language"\r\n\r\n'
                f"{lang}\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8")
        )
        request = urllib.request.Request(
            self.base_url + "/v1/audio/transcriptions",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            # Force the next call to re-check readiness after gateway errors.
            if exc.code in {503, 504}:
                self._ready_cache = None
            raise ProviderError(f"stt transcription failed: {detail[-1000:]}") from exc
        except (OSError, urllib.error.URLError) as exc:
            self._ready_cache = None
            raise ProviderError(f"stt transcription failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("stt returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("stt returned a non-object JSON payload")
        raw_text = str(payload.get("text") or "").strip()
        text = normalize_transcript(raw_text, self._corrections)
        if not text:
            raise ProviderError("stt returned an empty transcript")
        return STTResult(
            text=text,
            raw_text=raw_text,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            corrected=text != raw_text,
            model=str(payload.get("model") or health.get("model") or self.model_name),
        )

    async def transcribe(self, audio: bytes, language: str | None = None) -> STTResult:
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio, language)


class WhisperCppProvider:
    def __init__(
        self,
        binary_path: Path,
        model_path: Path,
        vad_model_path: Path,
        corrections_path: Path,
        *,
        language: str = "ja",
        threads: int = 6,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.binary_path = Path(binary_path)
        self.model_path = Path(model_path)
        self.vad_model_path = Path(vad_model_path)
        self.corrections_path = Path(corrections_path)
        self.language = str(language or "ja")
        self.threads = max(1, min(8, int(threads)))
        self.timeout_seconds = float(timeout_seconds)
        self._lock = asyncio.Lock()
        self._corrections = _load_corrections(self.corrections_path)

    @staticmethod
    def normalize_transcript(text: str, corrections: dict[str, str]) -> str:
        return normalize_transcript(text, corrections)

    def health(self) -> dict[str, Any]:
        missing = [
            str(path)
            for path in (self.binary_path, self.model_path, self.vad_model_path)
            if not path.is_file()
        ]
        return {
            "ok": not missing,
            "provider": "whisper_cpp",
            "binary": str(self.binary_path),
            "model": str(self.model_path),
            "vad_model": str(self.vad_model_path),
            "language": self.language,
            "threads": self.threads,
            "corrections": len(self._corrections),
            "missing": missing,
        }

    def _transcribe_sync(self, audio: bytes, language: str | None = None) -> STTResult:
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ProviderError("stt requires RIFF/WAVE audio")
        if len(audio) > 50 * 1024 * 1024:
            raise ProviderError("stt audio exceeds 50 MiB")
        health = self.health()
        if not health["ok"]:
            raise ProviderError("stt artifacts are missing: " + ", ".join(health["missing"]))

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="lumina-stt-", dir="/private/tmp") as temp_dir:
            input_wav = Path(temp_dir) / "input.wav"
            normalized_wav = Path(temp_dir) / "input-16k.wav"
            input_wav.write_bytes(audio)
            try:
                subprocess.run(
                    [
                        "/usr/bin/afconvert",
                        "-f", "WAVE",
                        "-d", "LEI16@16000",
                        "-c", "1",
                        str(input_wav),
                        str(normalized_wav),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProviderError(f"stt audio normalization failed: {exc}") from exc

            bin_dir = self.binary_path.parent
            env = os.environ.copy()
            env["DYLD_LIBRARY_PATH"] = str(bin_dir)
            prompt_terms = ["ルミナ", "音声認識", "Godot", "Qwen", "AIコンパニオン", "JP-Extra"]
            command = [
                str(self.binary_path),
                "-m", str(self.model_path),
                "-f", str(normalized_wav),
                "-l", str(language or self.language),
                "-t", str(self.threads),
                "-np",
                "-nt",
                "--prompt", "。".join(prompt_terms) + "。",
                "--vad",
                "--vad-model", str(self.vad_model_path),
                "--vad-threshold", "0.50",
                "--vad-min-speech-duration-ms", "220",
                "--vad-min-silence-duration-ms", "650",
                "--vad-max-speech-duration-s", "30",
                "--vad-speech-pad-ms", "280",
                "--vad-samples-overlap", "0.10",
            ]
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError("stt transcription timed out") from exc
            except (OSError, subprocess.CalledProcessError) as exc:
                stderr = getattr(exc, "stderr", b"")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                raise ProviderError(f"stt transcription failed: {str(stderr)[-1000:]}") from exc

        raw_text = " ".join(result.stdout.strip().split())
        text = self.normalize_transcript(raw_text, self._corrections)
        if not text:
            raise ProviderError("stt returned an empty transcript")
        return STTResult(
            text=text,
            raw_text=raw_text,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            corrected=text != raw_text,
            model=self.model_path.name,
        )

    async def transcribe(self, audio: bytes, language: str | None = None) -> STTResult:
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio, language)


def build_stt_provider(
    *,
    backend: str,
    stt_url: str | None,
    binary_path: Path,
    model_path: Path,
    vad_model_path: Path,
    corrections_path: Path,
    language: str = "ja",
    threads: int = 4,
    timeout_seconds: float = 90.0,
) -> SenseVoiceHttpProvider | WhisperCppProvider:
    """Select product STT. Default is SenseVoice HTTP gateway."""
    name = str(backend or "sensevoice").strip().lower().replace("-", "_")
    if name in {"whisper", "whisper_cpp", "whispercpp"}:
        return WhisperCppProvider(
            binary_path,
            model_path,
            vad_model_path,
            corrections_path,
            language=language,
            threads=threads,
            timeout_seconds=timeout_seconds,
        )
    base = (stt_url or "http://127.0.0.1:5057").rstrip("/")
    return SenseVoiceHttpProvider(
        base,
        corrections_path,
        language=language,
        timeout_seconds=timeout_seconds,
        model_name="sensevoice-int8",
    )
