"""Deterministic, pre-integration checks for Lumina conversation quality.

The module deliberately has no model, network, filesystem, or application
dependencies.  It evaluates one candidate assistant response against recent
turns and optional caller-supplied unresolved topics.  The checks are useful
for offline fixtures and post-generation gates; they do not try to judge
semantic correctness.

The main entry point is :func:`evaluate_conversation_quality`.  The returned
``QualityReport`` is a small immutable object and also supports mapping-style
access through ``to_dict``/``__getitem__`` for simple harness integration.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


MAX_TEXT_CHARS = 20_000
MAX_HISTORY_TURNS = 32
MAX_TOPIC_CHARS = 160
MAX_TOPICS = 32

_ROLE_ALIASES = {
    "assistant": "assistant",
    "bot": "assistant",
    "lumina": "assistant",
    "model": "assistant",
    "ai": "assistant",
    "user": "user",
    "human": "user",
    "visitor": "user",
    "customer": "user",
}

_MODE_ALIASES = {
    "brief": "concise",
    "short": "concise",
    "compact": "concise",
    "warm": "expressive",
    "friendly": "expressive",
    "deep": "expressive",
    "engineering": "technical",
    "tech": "technical",
}

_RELATION_ALIASES = {
    "new": "stranger",
    "new_user": "stranger",
    "unknown": "unknown",
    "distant": "stranger",
    "stranger": "stranger",
    "acquaintance": "acquaintance",
    "acquainted": "acquaintance",
    "colleague": "collaborator",
    "teammate": "collaborator",
    "collaborator": "collaborator",
    "professional": "professional",
    "work": "professional",
    "friend": "friend",
    "close": "close",
    "intimate": "close",
}

_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+]|[\u2022\u2023\u25e6\u25aa]|\d+[.)、])\s+")
_JAPANESE_QUESTION_RE = re.compile(r"(?:ですか|ますか|でしょうか|ませんか|かな|かい|どうして|なぜ|何故)")
_ENGLISH_QUESTION_START_RE = re.compile(
    r"^\s*(?:why|what|when|where|who|how|which|can|could|would|is|are|do|does)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[.!?。！？]+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]{2,}|[\u3040-\u309f]{2,}|[\u30a0-\u30ff]{2,}")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_PUNCT_RE = re.compile(r"[^\w\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "can",
        "could",
        "do",
        "does",
        "for",
        "how",
        "i",
        "is",
        "it",
        "me",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "we",
        "what",
        "when",
        "where",
        "who",
        "why",
        "you",
        "your",
        "は",
        "が",
        "を",
        "に",
        "へ",
        "で",
        "と",
        "の",
        "も",
        "です",
        "ます",
        "か",
    }
)

_STRONG_INTIMACY_PATTERNS = (
    "i love you",
    "love you",
    "you are my only",
    "my one and only",
    "only you",
    "always be by your side",
    "ずっとそばにいる",
    "君だけ",
    "あなたしかいない",
    "愛してる",
    "大好き",
    "抱きしめ",
    "恋人",
)
_SOFT_INTIMACY_PATTERNS = (
    "sweetheart",
    "darling",
    "dear",
    "my dear",
    "お姉ちゃん",
    "お兄ちゃん",
    "ねえねえ",
)
_FORMAL_PATTERNS = (
    "dear customer",
    "thank you for contacting",
    "please contact support",
    "ご利用ありがとうございます",
    "お問い合わせありがとうございます",
    "お客様",
)
_TECHNICAL_MARKERS = (
    "step",
    "check",
    "assumption",
    "原因",
    "手順",
    "確認",
    "前提",
    "まず",
    "次に",
    "最後に",
)


@dataclass(frozen=True)
class ConversationTurn:
    """A normalized conversation turn used by the checker."""

    role: str
    text: str


@dataclass(frozen=True)
class QualityPolicy:
    """Thresholds for a deterministic quality evaluation.

    ``mode`` and ``relation`` are intentionally strings so callers can pass
    values from JSON without importing enums.  Unknown values normalize to
    safe defaults.
    """

    mode: str = "concise"
    relation: str = "unknown"
    recent_turns: int = 6
    max_history_turns: int = MAX_HISTORY_TURNS
    repeated_opening_chars: int = 12
    max_topics: int = MAX_TOPICS

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_mode(self.mode))
        object.__setattr__(self, "relation", normalize_relation(self.relation))
        object.__setattr__(self, "recent_turns", max(1, min(16, int(self.recent_turns))))
        object.__setattr__(self, "max_history_turns", max(1, min(MAX_HISTORY_TURNS, int(self.max_history_turns))))
        object.__setattr__(self, "repeated_opening_chars", max(4, min(32, int(self.repeated_opening_chars))))
        object.__setattr__(self, "max_topics", max(1, min(MAX_TOPICS, int(self.max_topics))))

    @property
    def limits(self) -> Mapping[str, int]:
        """Return mode-specific response limits."""

        limits = {
            "concise": {"max_chars": 160, "hard_chars": 280, "max_sentences": 3, "max_bullets": 3, "max_questions": 1},
            "expressive": {"max_chars": 360, "hard_chars": 640, "max_sentences": 7, "max_bullets": 5, "max_questions": 2},
            "technical": {"max_chars": 560, "hard_chars": 960, "max_sentences": 10, "max_bullets": 8, "max_questions": 2},
        }
        return MappingProxyType(limits[self.mode])


@dataclass(frozen=True)
class QualityCheck:
    """Result for one named quality check."""

    name: str
    flagged: bool
    severity: str = "pass"
    message: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.flagged

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "flagged": self.flagged,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "evidence": list(self.evidence),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class QualityReport:
    """Immutable aggregate report returned by the public evaluation API."""

    status: str
    score: float
    mode: str
    relation: str
    response: str
    checks: Mapping[str, QualityCheck]
    metrics: Mapping[str, Any]
    unresolved_topics: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status != "fail"

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(name for name, check in self.checks.items() if check.flagged)

    @property
    def issues(self) -> tuple[QualityCheck, ...]:
        return tuple(check for check in self.checks.values() if check.flagged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "ok": self.ok,
            "score": self.score,
            "mode": self.mode,
            "relation": self.relation,
            "response": self.response,
            "flags": list(self.flags),
            "checks": {name: check.to_dict() for name, check in self.checks.items()},
            "metrics": dict(self.metrics),
            "unresolved_topics": list(self.unresolved_topics),
            "errors": list(self.errors),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class _ModeStats:
    text: str
    normalized: str
    comparison: str
    opening: str
    structure: tuple[Any, ...]
    sentence_count: int
    question_count: int
    bullet_count: int
    paragraph_count: int


def normalize_mode(value: Any) -> str:
    """Normalize a mode to ``concise``, ``expressive``, or ``technical``."""

    value = str(value or "concise").strip().casefold()
    value = _MODE_ALIASES.get(value, value)
    return value if value in {"concise", "expressive", "technical"} else "concise"


def normalize_relation(value: Any) -> str:
    """Normalize a relationship label without making assumptions when absent."""

    value = str(value or "unknown").strip().casefold().replace("-", "_").replace(" ", "_")
    return _RELATION_ALIASES.get(value, "unknown")


def normalize_text(value: Any, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Convert untrusted text to bounded, Unicode-normalized plain text."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize("NFKC", value)
    value = _ZERO_WIDTH_RE.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    return value.strip()[: max(0, int(max_chars))]


def _comparison_text(text: str) -> str:
    return _PUNCT_RE.sub("", text.casefold())


def _opening_signature(text: str, width: int) -> str:
    comparison = _comparison_text(text)
    return comparison[:width]


def _sentence_count(text: str) -> int:
    if not text.strip():
        return 0
    count = len(_SENTENCE_RE.findall(text))
    return max(1, count)


def _question_count(text: str) -> int:
    if not text.strip():
        return 0
    punctuation_count = text.count("?") + text.count("？")
    japanese_count = len(_JAPANESE_QUESTION_RE.findall(text))
    if punctuation_count or japanese_count:
        return max(punctuation_count, japanese_count)
    # Only treat an English question without punctuation as a question when
    # the whole response starts with a question word. Searching every word
    # would misclassify ordinary prose such as "the cache is ready".
    return int(bool(_ENGLISH_QUESTION_START_RE.search(text)))


def _bullet_count(text: str) -> int:
    return len(_BULLET_RE.findall(text))


def _structure_signature(text: str) -> tuple[Any, ...]:
    lines = [line for line in text.splitlines() if line.strip()]
    return (
        _bullet_count(text),
        min(_sentence_count(text), 12),
        min(_question_count(text), 6),
        min(len(lines), 12),
        int(":" in text or "：" in text),
        int("\n" in text),
    )


def _stats(text: str, opening_chars: int) -> _ModeStats:
    normalized = " ".join(text.split())
    return _ModeStats(
        text=text,
        normalized=normalized,
        comparison=_comparison_text(normalized),
        opening=_opening_signature(normalized, opening_chars),
        structure=_structure_signature(text),
        sentence_count=_sentence_count(text),
        question_count=_question_count(text),
        bullet_count=_bullet_count(text),
        paragraph_count=sum(bool(line.strip()) for line in text.splitlines()),
    )


def _coerce_role(value: Any) -> str:
    role = str(value or "unknown").strip().casefold()
    return _ROLE_ALIASES.get(role, role if role in {"user", "assistant"} else "unknown")


def _coerce_turn(value: Any, *, default_role: str = "unknown") -> ConversationTurn:
    if isinstance(value, ConversationTurn):
        return ConversationTurn(_coerce_role(value.role), normalize_text(value.text))
    if isinstance(value, Mapping):
        role = _coerce_role(value.get("role") or value.get("author") or default_role)
        content = value.get("content")
        if content is None:
            content = value.get("text", value.get("message", ""))
        return ConversationTurn(role, normalize_text(content))
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return ConversationTurn(_coerce_role(value[0]), normalize_text(value[1]))
    return ConversationTurn(_coerce_role(default_role), normalize_text(value))


def _coerce_history(history: Any, *, max_turns: int) -> tuple[ConversationTurn, ...]:
    if history is None:
        return ()
    if isinstance(history, (str, bytes, Mapping, ConversationTurn)):
        values: Iterable[Any] = (history,)
    else:
        try:
            iter(history)
            values = history
        except TypeError:
            values = (history,)
    turns = []
    for value in values:
        # A bare history string is treated as an earlier assistant response;
        # callers with user turns should provide role-tagged records.
        default_role = "assistant" if isinstance(value, (str, bytes)) else "unknown"
        turn = _coerce_turn(value, default_role=default_role)
        if turn.text:
            turns.append(turn)
    return tuple(turns[-max_turns:])


def _context_value(context: Mapping[str, Any] | None, *keys: str) -> Any:
    if not isinstance(context, Mapping):
        return None
    for key in keys:
        if key in context:
            return context[key]
    return None


def _safe_topics(values: Any, *, max_topics: int) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        values = (values,)
    try:
        iterator = iter(values)
    except TypeError:
        iterator = iter((values,))
    topics: list[str] = []
    for value in iterator:
        topic = normalize_text(value, max_chars=MAX_TOPIC_CHARS)
        if topic and topic not in topics:
            topics.append(topic)
        if len(topics) >= max_topics:
            break
    return tuple(topics)


def _topic_terms(topic: str) -> tuple[str, ...]:
    words = [word.casefold() for word in _WORD_RE.findall(topic) if word.casefold() not in _STOPWORDS]
    if words:
        return tuple(dict.fromkeys(words))
    compact = _comparison_text(topic)
    if len(compact) <= 1:
        return ()
    return (compact,)


def _topic_matches(response: str, topic: str) -> bool:
    compact_response = _comparison_text(response)
    response_words = set(word.casefold() for word in _WORD_RE.findall(response))
    terms = _topic_terms(topic)
    if not terms:
        return True
    for term in terms:
        if term in response_words or term in compact_response:
            return True
    return False


def _inferred_question_topics(user_text: str) -> tuple[str, ...]:
    if not _question_count(user_text):
        return ()
    terms = _topic_terms(user_text)
    return tuple(term for term in terms if len(term) >= 2)[:4]


def _has_any(text: str, patterns: Sequence[str]) -> bool:
    lowered = text.casefold()
    return any(pattern.casefold() in lowered for pattern in patterns)


def _question_signature(text: str) -> str:
    question = text.split("?", 1)[0].split("？", 1)[0]
    return _comparison_text(question)[:48]


def _check(name: str, flagged: bool, severity: str = "pass", message: str = "", evidence: Iterable[str] = ()) -> QualityCheck:
    return QualityCheck(name, bool(flagged), severity if flagged else "pass", message, tuple(str(item) for item in evidence))


def evaluate_conversation_quality(
    response: Any,
    history: Any = None,
    *,
    mode: Any = None,
    relation: Any = None,
    unresolved_topics: Any = None,
    context: Mapping[str, Any] | None = None,
    policy: QualityPolicy | None = None,
) -> QualityReport:
    """Evaluate a response using deterministic, offline checks.

    ``history`` should contain role-tagged turns such as
    ``{"role": "assistant", "content": "..."}``.  Bare strings are
    accepted as earlier assistant replies for simple fixture use.  Explicit
    ``unresolved_topics`` take precedence over topics inferred from the latest
    user question.  Invalid or oversized inputs produce a safe report rather
    than raising, except for a malformed custom ``QualityPolicy``.
    """

    if policy is None:
        context_mode = _context_value(context, "mode", "conversation_mode", "quality_mode")
        context_relation = _context_value(context, "relation", "relationship", "user_relation")
        policy = QualityPolicy(
            mode=context_mode if mode is None else mode,
            relation=context_relation if relation is None else relation,
        )
    else:
        policy = QualityPolicy(
            mode=policy.mode,
            relation=policy.relation,
            recent_turns=policy.recent_turns,
            max_history_turns=policy.max_history_turns,
            repeated_opening_chars=policy.repeated_opening_chars,
            max_topics=policy.max_topics,
        )

    text = normalize_text(response)
    turns = _coerce_history(history, max_turns=policy.max_history_turns)
    recent_turns = turns[-policy.recent_turns :]
    stats = _stats(text, policy.repeated_opening_chars)
    assistant_stats = [
        _stats(turn.text, policy.repeated_opening_chars)
        for turn in recent_turns
        if turn.role == "assistant" and turn.text
    ]
    previous_openings = [item.opening for item in assistant_stats if len(item.opening) >= 4]
    opening_repeat_count = sum(item == stats.opening for item in previous_openings) if len(stats.opening) >= 4 else 0
    structure_repeat_count = sum(item.structure == stats.structure for item in assistant_stats)

    explicit_topics = _safe_topics(unresolved_topics, max_topics=policy.max_topics)
    if not explicit_topics:
        from_user = next((turn.text for turn in reversed(recent_turns) if turn.role == "user" and turn.text), "")
        explicit_topics = _inferred_question_topics(from_user)
    unresolved = tuple(topic for topic in explicit_topics if not _topic_matches(text, topic))

    trailing_question_turns = 0
    repeated_question = False
    current_question_signature = _question_signature(text) if stats.question_count else ""
    for item in reversed(assistant_stats):
        if item.question_count:
            trailing_question_turns += 1
        else:
            break
    if stats.question_count:
        trailing_question_turns += 1
        previous_questions = [
            _question_signature(turn.text)
            for turn in recent_turns
            if turn.role == "assistant" and _question_count(turn.text)
        ]
        repeated_question = bool(current_question_signature and current_question_signature in previous_questions)

    limits = policy.limits
    checks: dict[str, QualityCheck] = {}
    checks["repeated_opening"] = _check(
        "repeated_opening",
        opening_repeat_count > 0,
        "fail" if opening_repeat_count >= 2 else "warn",
        "The response reuses an opening seen in a recent assistant turn." if opening_repeat_count else "",
        (f"count={opening_repeat_count}", f"opening={stats.opening}"),
    )
    checks["repeated_structure"] = _check(
        "repeated_structure",
        structure_repeat_count >= 2 or (structure_repeat_count >= 1 and opening_repeat_count > 0),
        "warn",
        "The response repeats a recent sentence/list structure." if structure_repeat_count >= 2 or (structure_repeat_count >= 1 and opening_repeat_count > 0) else "",
        (f"count={structure_repeat_count}", f"structure={stats.structure}"),
    )
    bullet_flag = stats.bullet_count > int(limits["max_bullets"]) or (policy.mode == "concise" and stats.bullet_count >= 2 and stats.sentence_count > 5)
    checks["bullets"] = _check(
        "bullets",
        bullet_flag,
        "fail" if stats.bullet_count > int(limits["max_bullets"]) * 2 else "warn",
        "The list shape is too heavy for the selected response mode." if bullet_flag else "",
        (f"count={stats.bullet_count}", f"limit={limits['max_bullets']}"),
    )
    question_flag = repeated_question or trailing_question_turns >= 3 or (
        stats.question_count > 0 and sum(item.question_count > 0 for item in assistant_stats[-4:]) >= 3
    )
    checks["question_loop"] = _check(
        "question_loop",
        question_flag,
        "fail" if repeated_question or trailing_question_turns >= 3 else "warn",
        "The assistant keeps asking questions without a stable answering turn." if question_flag else "",
        (f"trailing_question_turns={trailing_question_turns}", f"repeated_question={repeated_question}"),
    )
    topic_flag = bool(unresolved)
    checks["unresolved_topics"] = _check(
        "unresolved_topics",
        topic_flag,
        "warn",
        "A supplied or inferred user topic is not reflected in the response." if topic_flag else "",
        unresolved,
    )

    lowered = text.casefold()
    strong_intimacy = _has_any(lowered, _STRONG_INTIMACY_PATTERNS)
    soft_intimacy = _has_any(lowered, _SOFT_INTIMACY_PATTERNS)
    formal = _has_any(lowered, _FORMAL_PATTERNS)
    relation_flag = False
    relation_severity = "warn"
    relation_message = ""
    if policy.relation in {"stranger", "acquaintance", "professional", "collaborator"} and strong_intimacy:
        relation_flag = True
        relation_severity = "fail"
        relation_message = "The wording is more intimate than the declared relationship allows."
    elif policy.relation in {"stranger", "acquaintance", "professional", "collaborator"} and soft_intimacy:
        relation_flag = True
        relation_message = "The wording assumes more familiarity than the declared relationship provides."
    elif policy.relation in {"friend", "close"} and formal:
        relation_flag = True
        relation_message = "The wording is unusually service-like for the declared relationship."
    checks["relation_tone"] = _check(
        "relation_tone",
        relation_flag,
        relation_severity,
        relation_message,
        (f"relation={policy.relation}",),
    )

    length_flag = len(text) > int(limits["max_chars"])
    hard_length = len(text) > int(limits["hard_chars"])
    technical_unstructured = (
        policy.mode == "technical"
        and len(text) > 180
        and stats.sentence_count >= 4
        and not _has_any(text, _TECHNICAL_MARKERS)
        and stats.bullet_count == 0
    )
    sentence_over = stats.sentence_count > int(limits["max_sentences"])
    question_over = stats.question_count > int(limits["max_questions"])
    mode_flag = length_flag or sentence_over or question_over or technical_unstructured
    hard_mode_shape = (
        stats.sentence_count > int(limits["max_sentences"]) * 2
        or stats.question_count > int(limits["max_questions"]) * 2
    )
    checks["mode_fit"] = _check(
        "mode_fit",
        mode_flag,
        "fail" if hard_length or hard_mode_shape else "warn",
        "The response is longer or less structured than the selected mode allows." if mode_flag else "",
        (
            f"chars={len(text)}",
            f"limit={limits['max_chars']}",
            f"sentences={stats.sentence_count}/{limits['max_sentences']}",
            f"questions={stats.question_count}/{limits['max_questions']}",
            f"technical_unstructured={technical_unstructured}",
        ),
    )

    errors: tuple[str, ...] = ()
    if not text:
        checks["empty_response"] = _check("empty_response", True, "fail", "The response is empty.")
    else:
        checks["empty_response"] = _check("empty_response", False)

    severity_rank = {"pass": 0, "warn": 1, "fail": 2}
    status = max((check.severity for check in checks.values()), key=lambda value: severity_rank[value])
    penalty = sum(0.22 if check.severity == "fail" else 0.08 for check in checks.values() if check.flagged)
    score = round(max(0.0, min(1.0, 1.0 - penalty)), 3)
    metrics = MappingProxyType(
        {
            "char_count": len(text),
            "sentence_count": stats.sentence_count,
            "question_count": stats.question_count,
            "bullet_count": stats.bullet_count,
            "paragraph_count": stats.paragraph_count,
            "opening_signature": stats.opening,
            "structure_signature": stats.structure,
            "opening_repeat_count": opening_repeat_count,
            "structure_repeat_count": structure_repeat_count,
            "trailing_question_turns": trailing_question_turns,
            "repeated_question": repeated_question,
        }
    )
    return QualityReport(status, score, policy.mode, policy.relation, text, MappingProxyType(checks), metrics, unresolved, errors)


def check_conversation_quality(*args: Any, **kwargs: Any) -> QualityReport:
    """Compatibility alias for :func:`evaluate_conversation_quality`."""

    return evaluate_conversation_quality(*args, **kwargs)


def assess_conversation_quality(*args: Any, **kwargs: Any) -> QualityReport:
    """Compatibility alias for :func:`evaluate_conversation_quality`."""

    return evaluate_conversation_quality(*args, **kwargs)


class ConversationQualityChecker:
    """Reusable checker with fixed mode/relation defaults for fixture suites."""

    def __init__(self, policy: QualityPolicy | None = None, *, mode: Any = "concise", relation: Any = "unknown") -> None:
        self.policy = policy or QualityPolicy(mode=mode, relation=relation)

    def check(self, response: Any, history: Any = None, *, unresolved_topics: Any = None, context: Mapping[str, Any] | None = None) -> QualityReport:
        return evaluate_conversation_quality(
            response,
            history,
            unresolved_topics=unresolved_topics,
            context=context,
            policy=self.policy,
        )

    evaluate = check


QualityConfig = QualityPolicy
ConversationQualityPolicy = QualityPolicy


__all__ = [
    "ConversationQualityChecker",
    "ConversationQualityPolicy",
    "ConversationTurn",
    "QualityCheck",
    "QualityConfig",
    "QualityPolicy",
    "QualityReport",
    "assess_conversation_quality",
    "check_conversation_quality",
    "evaluate_conversation_quality",
    "normalize_mode",
    "normalize_relation",
    "normalize_text",
]
