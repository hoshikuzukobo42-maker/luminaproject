from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Generic, Iterator, Mapping, Sequence, TypeVar


T = TypeVar("T")

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])|\n+")
_SPLIT_CANDIDATES = ("、", "，", ",", " ", ".", "。", "！", "!", "？", "?")


def split_tts_sentence_chunks(
    text: str,
    *,
    max_chars: int = 80,
) -> list[str]:
    """Split plain text into sentence-oriented chunks for streaming synthesis.

    This keeps sentence punctuation where possible, enforces a hard upper bound,
    and preserves input order.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    max_chars = max(20, min(300, int(max_chars)))
    stripped = str(text or "").strip()
    if not stripped:
        return []

    sentence_parts: list[str] = []
    for part in _SENTENCE_BOUNDARY_RE.split(stripped):
        clean = _normalize_text(part)
        if not clean:
            continue
        sentence_parts.extend(_split_long_sentence(clean, max_chars=max_chars))
    return sentence_parts


def _normalize_text(value: str) -> str:
    compacted = " ".join(value.strip().split())
    return compacted


def _split_long_sentence(sentence: str, *, max_chars: int) -> list[str]:
    sentence = sentence.strip()
    if len(sentence) <= max_chars:
        return [sentence]

    chunks: list[str] = []
    remainder = sentence
    while remainder:
        if len(remainder) <= max_chars:
            chunks.append(remainder)
            break

        break_candidates: list[int] = [
            remainder.rfind(mark, 0, max_chars + 1) for mark in _SPLIT_CANDIDATES
        ]
        cut = max(break_candidates)
        if cut <= 0:
            cut = max_chars
            piece = remainder[:cut].strip()
        else:
            mark = remainder[cut]
            if mark.isspace():
                piece = remainder[:cut].strip()
            else:
                piece = (remainder[: cut + 1]).strip()

        if not piece:
            piece = remainder[:max_chars]
            remainder = remainder[max_chars:]
        else:
            remainder = remainder[len(piece) :].strip()

        chunks.append(piece)

    return chunks


@dataclass(frozen=True)
class OrderedBoundedQueue(Generic[T]):
    """FIFO queue with hard upper bound that drops oldest entries when full."""

    max_size: int
    _items: Deque[T] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_size <= 0:
            raise ValueError("max_size must be > 0")

    def enqueue(self, item: T) -> list[T]:
        """Insert ``item`` and return dropped entries, preserving queue order."""
        dropped: list[T] = []
        while len(self._items) >= self.max_size:
            popped = self._items.popleft()
            dropped.append(popped)
        self._items.append(item)
        return dropped

    def dequeue(self) -> T | None:
        """Remove and return the oldest item, or ``None`` when empty."""
        if not self._items:
            return None
        return self._items.popleft()

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(list(self._items))


@dataclass(frozen=True)
class TTSChunkPayload:
    """Chunk payload queued for TTS synthesis and lipsync dispatch."""

    session_key: str
    generation_id: int
    chunk_index: int
    chunks_total: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_first_chunk(self) -> bool:
        return self.chunk_index == 0


def build_chunk_metadata(
    *,
    generation_id: int,
    chunk_index: int,
    chunks_total: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if chunks_total <= 0:
        raise ValueError("chunks_total must be >= 1")
    if chunk_index < 0 or chunk_index >= chunks_total:
        raise ValueError("chunk_index is out of bounds")

    payload: dict[str, Any] = {
        "generation_id": generation_id,
        "chunk_index": chunk_index,
        "chunks_total": chunks_total,
        "deferred": chunk_index < chunks_total - 1,
        "is_first_chunk": chunk_index == 0,
        "is_last_chunk": chunk_index == chunks_total - 1,
    }
    if extra:
        payload.update(extra)
    return payload


def build_first_chunk_metadata(
    *,
    generation_id: int,
    chunks_total: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_chunk_metadata(
        generation_id=generation_id,
        chunk_index=0,
        chunks_total=chunks_total,
        extra=extra,
    )


def is_stale_generation(current_generation: int, observed_generation: int) -> bool:
    if not isinstance(current_generation, int) or not isinstance(observed_generation, int):
        raise TypeError("generation ids must be int")
    return observed_generation < current_generation


class TTSStreamingCoordinator:
    """Create and queue TTS chunks with generation-aware stale filtering."""

    def __init__(
        self,
        *,
        queue_max_size: int = 8,
        chunk_max_chars: int = 80,
    ) -> None:
        self._queue_max_size = int(queue_max_size)
        self._chunk_max_chars = int(chunk_max_chars)
        self._session_generation: dict[str, int] = {}
        self._queues: dict[str, OrderedBoundedQueue[TTSChunkPayload]] = {}

    def start_generation(self, session_key: str, text: str, *, metadata: Mapping[str, Any] | None = None) -> list[TTSChunkPayload]:
        """Start a new generation for a session and enqueue all chunks for that text."""
        if not isinstance(session_key, str) or not session_key.strip():
            raise ValueError("session_key must be a non-empty string")
        generation_id = self._bump_generation(session_key)
        chunks = split_tts_sentence_chunks(text, max_chars=self._chunk_max_chars)
        payloads = [
            self._make_payload(
                session_key=session_key,
                generation_id=generation_id,
                chunk_index=index,
                chunks_total=len(chunks),
                chunk_text=chunk,
                metadata=metadata,
            )
            for index, chunk in enumerate(chunks)
        ]
        if session_key not in self._queues:
            self._queues[session_key] = OrderedBoundedQueue[TTSChunkPayload](
                max_size=self._queue_max_size
            )
        self._queues[session_key].clear()
        for payload in payloads:
            self._queues[session_key].enqueue(payload)
        return payloads

    def cancel_generation(self, session_key: str) -> int:
        """Advance session generation and discard queued items from prior generations."""
        generation_id = self._bump_generation(session_key)
        queue = self._queues.get(session_key)
        if queue is not None:
            queue.clear()
        return generation_id

    def is_stale_chunk(self, chunk: TTSChunkPayload) -> bool:
        current = self.current_generation(chunk.session_key)
        return is_stale_generation(current, chunk.generation_id)

    def dequeue(self, session_key: str) -> TTSChunkPayload | None:
        """Pop next non-stale chunk for a session, discarding stale queue entries."""
        queue = self._queues.get(session_key)
        if queue is None:
            return None
        current = self.current_generation(session_key)
        while True:
            chunk = queue.dequeue()
            if chunk is None:
                return None
            if is_stale_generation(current, chunk.generation_id):
                continue
            return chunk

    def drain(self, session_key: str) -> list[TTSChunkPayload]:
        """Drain all non-stale chunks for a session."""
        out: list[TTSChunkPayload] = []
        while True:
            chunk = self.dequeue(session_key)
            if chunk is None:
                return out
            out.append(chunk)

    def current_generation(self, session_key: str) -> int:
        return self._session_generation.get(session_key, 0)

    def _bump_generation(self, session_key: str) -> int:
        next_generation = self.current_generation(session_key) + 1
        self._session_generation[session_key] = next_generation
        return next_generation

    def _make_payload(
        self,
        *,
        session_key: str,
        generation_id: int,
        chunk_index: int,
        chunks_total: int,
        chunk_text: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> TTSChunkPayload:
        return TTSChunkPayload(
            session_key=session_key,
            generation_id=generation_id,
            chunk_index=chunk_index,
            chunks_total=chunks_total,
            text=chunk_text,
            metadata=build_chunk_metadata(
                generation_id=generation_id,
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                extra=metadata,
            ),
        )


Point = tuple[float, float]


def _sanitize_envelope_points(points: Sequence[Point] | Sequence[Mapping[str, float]] | None) -> list[Point]:
    if points is None:
        return []

    out: list[Point] = []
    for raw in points:
        if isinstance(raw, Mapping):
            if "time" not in raw or "value" not in raw:
                raise TypeError("mapping envelope point must contain time and value")
            t = float(raw["time"])
            v = float(raw["value"])
        else:
            point = tuple(raw)
            if len(point) != 2:
                raise TypeError("envelope point must be a 2-tuple or dict")
            t = float(point[0])
            v = float(point[1])

        if not math.isfinite(t) or not math.isfinite(v):
            continue
        out.append((t, v))

    out.sort(key=lambda item: item[0])
    merged: list[Point] = []
    for t, v in out:
        if merged and merged[-1][0] == t:
            merged[-1] = (t, v)
        else:
            merged.append((t, v))
    return merged


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize_envelope_bounds(points: list[Point]) -> list[Point]:
    if not points:
        return []

    start = points[0][0]
    end = points[-1][0]
    if end <= start:
        return [(0.0, 0.0), (1.0, 0.0)]

    span = end - start
    normalized: list[Point] = [
        ((t - start) / span, _clamp(v, 0.0, 1.0))
        for t, v in points
    ]
    if normalized[0][0] == 0.0:
        normalized[0] = (0.0, 0.0)
    else:
        normalized.insert(0, (0.0, 0.0))

    if normalized[-1][0] == 1.0:
        normalized[-1] = (1.0, 0.0)
    else:
        normalized.append((1.0, 0.0))
    return normalized


def normalize_envelopes(
    amplitude: Sequence[Point] | Sequence[Mapping[str, float]] | None,
    lipsync: Sequence[Point] | Sequence[Mapping[str, float]] | None,
) -> dict[str, list[Point]]:
    """Normalize and boundary-pad amplitude/lipsync envelopes for chunk boundaries."""
    amplitude_points = _normalize_envelope_bounds(_sanitize_envelope_points(amplitude))
    lipsync_points = _normalize_envelope_bounds(_sanitize_envelope_points(lipsync))
    return {"amplitude": amplitude_points, "lipsync": lipsync_points}


def boundary_envelope_pack(
    amplitude: Sequence[Point] | Sequence[Mapping[str, float]] | None,
    lipsync: Sequence[Point] | Sequence[Mapping[str, float]] | None,
) -> dict[str, list[Point]]:
    return normalize_envelopes(amplitude=amplitude, lipsync=lipsync)


__all__ = [
    "OrderedBoundedQueue",
    "TTSChunkPayload",
    "TTSStreamingCoordinator",
    "build_chunk_metadata",
    "build_first_chunk_metadata",
    "split_tts_sentence_chunks",
    "is_stale_generation",
    "boundary_envelope_pack",
    "normalize_envelopes",
]
