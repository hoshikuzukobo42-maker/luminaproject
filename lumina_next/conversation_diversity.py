"""Conversation diversity engine to reduce template-like Japanese replies."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence


_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]{2,}|[\u3040-\u309f]{2,}|[\u30a0-\u30ff]{2,}")
_SENTENCE_END_RE = re.compile(r"(?:。|！|!|\?|？)")
_QUESTION_RE = re.compile(r"(?:ですか|ますか|でしょうか|？|\?)")
_AIZUCHI_RE = re.compile(r"(?:そうですね|なるほど|確かに|わかります|うん|へえ)")
_APOLOGY_RE = re.compile(r"(?:すみません|申し訳|ごめん)")
_AGREE_RE = re.compile(r"(?:その通り|おっしゃる通り|同感|間違いなく)")


@dataclass
class DiversityTracker:
    recent_phrases: list[str] = field(default_factory=list)
    recent_endings: list[str] = field(default_factory=list)
    recent_lengths: list[int] = field(default_factory=list)
    max_history: int = 24

    def observe(self, text: str) -> None:
        phrases = _extract_phrases(text)
        endings = _extract_endings(text)
        self.recent_phrases.extend(phrases)
        self.recent_endings.extend(endings)
        self.recent_lengths.append(len(text))
        self.recent_phrases = self.recent_phrases[-self.max_history :]
        self.recent_endings = self.recent_endings[-self.max_history :]
        self.recent_lengths = self.recent_lengths[-self.max_history :]


@dataclass(frozen=True)
class DiversityMetrics:
    ngram_repeat_score: float
    ending_repeat_score: float
    question_rate: float
    aizuchi_rate: float
    bullet_rate: float
    apology_rate: float
    agree_rate: float
    lexical_diversity: float
    length_monotony: float
    paragraph_count: int
    avg_paragraph_length: float
    paragraph_length_variance: float
    paragraph_monotony: float
    persona_drift_score: float
    mode_bias_score: float
    needs_rewrite: bool
    rewrite_hints: tuple[str, ...]


def _extract_phrases(text: str, n: int = 3) -> list[str]:
    tokens = _WORD_RE.findall(text)
    if len(tokens) < n:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _extract_endings(text: str) -> list[str]:
    parts = _SENTENCE_END_RE.split(text)
    endings: list[str] = []
    for part in parts:
        chunk = part.strip()
        if len(chunk) >= 4:
            endings.append(chunk[-4:])
    return endings


def _ratio(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return count / total


def _split_paragraphs(text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n+", text) if chunk.strip()]
    if chunks:
        return chunks
    return [text.strip()] if text.strip() else []


def _paragraph_metrics(text: str) -> tuple[int, float, float, float]:
    paragraphs = _split_paragraphs(text)
    count = len(paragraphs)
    if count == 0:
        return 0, 0.0, 0.0, 0.0
    lengths = [len(paragraph) for paragraph in paragraphs]
    avg = sum(lengths) / count
    if count == 1:
        return count, avg, 0.0, 0.0
    variance = sum((length - avg) ** 2 for length in lengths) / count
    spread = variance ** 0.5
    normalized_spread = spread / max(avg, 1.0)
    monotony = max(0.0, min(1.0, 1.0 - normalized_spread))
    return count, avg, variance, monotony


def _length_monotony(lengths: Sequence[int], *, sensitivity: float = 0.55) -> float:
    """Return 0.0 for varied lengths and 1.0 for near-identical lengths."""

    if len(lengths) < 2:
        return 0.0
    avg = sum(lengths) / len(lengths)
    if avg <= 0:
        return 0.0
    spread = (sum((length - avg) ** 2 for length in lengths) / len(lengths)) ** 0.5
    coefficient_of_variation = spread / avg
    return max(0.0, min(1.0, 1.0 - coefficient_of_variation / max(sensitivity, 0.05)))


def analyze_diversity(
    text: str,
    *,
    history: Sequence[str] | None = None,
    persona_markers: Iterable[str] | None = None,
    mode: str = "expressive",
) -> DiversityMetrics:
    history = list(history or ())
    tracker = DiversityTracker()
    for prior in history[-8:]:
        tracker.observe(prior)

    phrases = _extract_phrases(text)
    endings = _extract_endings(text)
    phrase_counter = Counter(phrases)
    ending_counter = Counter(endings)
    ngram_repeat = max((_ratio(c, len(phrases)) for c in phrase_counter.values()), default=0.0)
    ending_repeat = max((_ratio(c, len(endings)) for c in ending_counter.values()), default=0.0)

    sentences = [s for s in _SENTENCE_END_RE.split(text) if s.strip()]
    sentence_count = max(1, len(sentences))
    question_rate = _ratio(len(_QUESTION_RE.findall(text)), sentence_count)
    aizuchi_rate = _ratio(len(_AIZUCHI_RE.findall(text)), sentence_count)
    bullet_lines = len(re.findall(r"(?m)^\s*[-*+]\s+", text))
    bullet_rate = _ratio(bullet_lines, max(sentence_count, bullet_lines, 1))
    apology_rate = _ratio(len(_APOLOGY_RE.findall(text)), sentence_count)
    agree_rate = _ratio(len(_AGREE_RE.findall(text)), sentence_count)

    tokens = _WORD_RE.findall(text)
    lexical_diversity = len(set(tokens)) / max(1, len(tokens))

    paragraph_count, avg_paragraph_length, paragraph_length_variance, paragraph_monotony = _paragraph_metrics(text)

    lengths = tracker.recent_lengths + [len(text)]
    length_monotony = _length_monotony(lengths)

    markers = list(persona_markers or ("Lumina", "ルミナ"))
    persona_drift = 0.0 if any(marker in text for marker in markers) or mode == "technical" else 0.35

    mode_bias = 0.0
    if mode == "technical" and question_rate > 0.5:
        mode_bias = 0.4
    if mode == "concise" and len(text) > 400:
        mode_bias = 0.5

    overlap = 0.0
    if history:
        prior_tokens = Counter(_WORD_RE.findall(" ".join(history[-3:])))
        current_tokens = Counter(_WORD_RE.findall(text))
        shared = sum(min(prior_tokens[t], current_tokens[t]) for t in prior_tokens)
        overlap = shared / max(1, sum(current_tokens.values()))

    hints: list[str] = []
    if ngram_repeat >= 0.34:
        hints.append("reduce repeated phrases")
    if aizuchi_rate >= 0.5:
        hints.append("reduce repeated phrases")
    ending_repeat_trigger = False
    if len(endings) >= 2:
        ending_repeat_trigger = ending_repeat >= (0.75 if sentence_count <= 2 else 0.5)
    if ending_repeat_trigger:
        hints.append("vary sentence endings")
    if question_rate >= 0.6:
        hints.append("reduce question density")
    if agree_rate >= 0.5:
        hints.append("avoid excessive agreement")
    if bullet_rate >= 0.34:
        hints.append("avoid bullet formatting")
    if overlap >= 0.55:
        hints.append("avoid near-duplicate prior wording")
    if length_monotony >= 0.72:
        hints.append("vary response length")
    if paragraph_monotony >= 0.78 and paragraph_count >= 2:
        hints.append("vary paragraph shape")

    needs_rewrite = (
        ngram_repeat >= 0.34
        or aizuchi_rate >= 0.5
        or ending_repeat_trigger
        or question_rate >= 0.6
        or agree_rate >= 0.5
        or bullet_rate >= 0.34
        or overlap >= 0.55
        or mode_bias >= 0.4
        or length_monotony >= 0.72
    )

    return DiversityMetrics(
        ngram_repeat_score=ngram_repeat,
        ending_repeat_score=ending_repeat,
        question_rate=question_rate,
        aizuchi_rate=aizuchi_rate,
        bullet_rate=bullet_rate,
        apology_rate=apology_rate,
        agree_rate=agree_rate,
        lexical_diversity=lexical_diversity,
        length_monotony=length_monotony,
        paragraph_count=paragraph_count,
        avg_paragraph_length=avg_paragraph_length,
        paragraph_length_variance=paragraph_length_variance,
        paragraph_monotony=paragraph_monotony,
        persona_drift_score=persona_drift,
        mode_bias_score=mode_bias,
        needs_rewrite=needs_rewrite,
        rewrite_hints=tuple(hints),
    )


def apply_local_rewrite(text: str, hints: Sequence[str], *, max_passes: int = 2) -> str:
    result = text
    passes = 0
    while hints and passes < max_passes:
        if "reduce repeated phrases" in hints:
            result = re.sub(r"(.{8,}?)\1+", r"\1", result)
        if "vary sentence endings" in hints:
            result = re.sub(r"(ですね)(?=。|$)", "です", result, count=1)
        if "reduce question density" in hints:
            result = re.sub(r"(？|\?)+", "。", result, count=1)
        if "avoid bullet formatting" in hints:
            result = re.sub(r"(?m)^\s*[-*+]\s+", "", result)
        if "avoid excessive agreement" in hints:
            result = re.sub(r"(?:その通りです|おっしゃる通りです)[。、]?", "", result, count=1)
        passes += 1
        hints = analyze_diversity(result).rewrite_hints
    return result.strip()


__all__ = [
    "DiversityMetrics",
    "DiversityTracker",
    "analyze_diversity",
    "apply_local_rewrite",
]
