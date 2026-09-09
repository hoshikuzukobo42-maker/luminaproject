from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .llm import ProviderError


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])|\n+")


def split_tts_chunks(text: str, max_chars: int = 80) -> list[str]:
    max_chars = max(20, min(150, int(max_chars)))
    chunks: list[str] = []
    for sentence in _SENTENCE_BOUNDARY_RE.split(str(text or "")):
        sentence = " ".join(sentence.strip().split())
        if not sentence:
            continue
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(mark, 0, max_chars + 1) for mark in ("、", "，", ",", " "))
            if cut < 20:
                cut = max_chars
            piece = sentence[: cut + (1 if sentence[cut:cut + 1] in {"、", "，", ","} else 0)].strip()
            if piece:
                chunks.append(piece)
            sentence = sentence[len(piece):].strip()
        if sentence:
            chunks.append(sentence)
    return chunks


@dataclass(frozen=True)
class TTSResult:
    text: str
    audio_base64: str
    latency_ms: float
    payload: dict[str, Any]


class TTSProvider:
    def __init__(self, base_url: str, model_name: str, style: str = "Neutral", timeout_seconds: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.style = style
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise ProviderError(f"tts {method} {path} failed: {exc}") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"tts {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError(f"tts {path} returned a non-object JSON payload")
        return value

    async def health(self) -> dict[str, Any]:
        try:
            payload = await asyncio.to_thread(self._request_json, "GET", "/health", None, 5.0)
        except ProviderError as exc:
            return {"ok": False, "base_url": self.base_url, "error": str(exc)}
        return {
            "ok": bool(payload.get("ready") or payload.get("status") == "ok"),
            "base_url": self.base_url,
            "backend": payload.get("backend") or payload.get("configured_backend"),
            "model_name": payload.get("style_bert_vits2_model_name") or payload.get("model"),
            "model_file": payload.get("style_bert_vits2_model_file"),
            "speaker_credit": payload.get("tts_speaker_credit"),
            "device": payload.get("style_bert_vits2_device"),
        }

    def _synthesize_sync(self, text: str, emotion: str, speed: float) -> TTSResult:
        started = time.perf_counter()
        payload = self._request_json(
            "POST",
            "/voice-lipsync",
            {
                "text": text,
                "model_name": self.model_name,
                "style": self.style,
                "speed": max(0.75, min(1.35, float(speed))),
                "emotion": str(emotion or "neutral"),
            },
            self.timeout_seconds,
        )
        audio_base64 = payload.get("audio_base64")
        if not isinstance(audio_base64, str) or not audio_base64:
            raise ProviderError("tts /voice-lipsync returned no audio_base64")
        try:
            audio = base64.b64decode(audio_base64, validate=True)
        except ValueError as exc:
            raise ProviderError("tts returned invalid audio_base64") from exc
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ProviderError("tts returned invalid WAV data")
        public_payload = {key: value for key, value in payload.items() if key != "audio_base64"}
        return TTSResult(
            text=text,
            audio_base64=audio_base64,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            payload=public_payload,
        )

    async def synthesize_first_chunk(self, text: str, emotion: str = "neutral", speed: float = 1.0) -> tuple[TTSResult, list[str]]:
        chunks = split_tts_chunks(text)
        if not chunks:
            raise ProviderError("tts received no speakable text")
        async with self._lock:
            result = await asyncio.to_thread(self._synthesize_sync, chunks[0], emotion, speed)
        return result, chunks
