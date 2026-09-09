from __future__ import annotations

import asyncio
import gc
import os
import time
from pathlib import Path
from typing import Any


class EmbeddingError(RuntimeError):
    pass


class LocalE5Provider:
    """Lazy, local-only E5 query encoder; never downloads at runtime."""

    def __init__(self, model_path: Path, enabled: bool = True, batch_size: int = 4) -> None:
        self.model_path = Path(model_path)
        self.enabled = bool(enabled)
        self.batch_size = max(1, int(batch_size))
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self.last_latency_ms: float | None = None
        self.last_error: str | None = None

    def status(self) -> dict[str, Any]:
        complete = self.model_path.joinpath("model.safetensors").is_file()
        return {
            "enabled": self.enabled,
            "provider": "multilingual-e5-small",
            "model_path": str(self.model_path),
            "model_complete": complete,
            "loaded": self._model is not None,
            "dimensions": 384,
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
        }

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.model_path.joinpath("model.safetensors").is_file():
            raise EmbeddingError(f"embedding model is incomplete: {self.model_path}")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        self._model = SentenceTransformer(
            str(self.model_path),
            device="cpu",
            local_files_only=True,
        )
        return self._model

    def _embed_query_sync(self, text: str) -> list[float]:
        started = time.perf_counter()
        model = self._load()
        vector = model.encode(
            ["query: " + text.strip()],
            batch_size=1,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        result = vector.astype("float32").tolist()
        if len(result) != 384:
            raise EmbeddingError(f"unexpected E5 dimensions: {len(result)}")
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        self.last_error = None
        return result

    async def embed_query(self, text: str) -> list[float]:
        if not self.enabled:
            raise EmbeddingError("embedding provider is disabled")
        async with self._lock:
            try:
                return await asyncio.to_thread(self._embed_query_sync, text)
            except Exception as exc:
                self.last_error = str(exc)
                if isinstance(exc, EmbeddingError):
                    raise
                raise EmbeddingError(str(exc)) from exc

    async def unload(self) -> None:
        async with self._lock:
            self._model = None
            await asyncio.to_thread(gc.collect)
