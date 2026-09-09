"""Shisa-oriented conversation layer for Lumina Japanese dialogue."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


_SHISA_SYSTEM = (
    "自分がなにものであるかは定義しない。自然な日本語で直接返答してください。"
    "口調は固定せず、相手との関係とこれまでの会話から自然に合わせる。"
    "過度な前置き、箇条書き、同じ文末の連続、質問の連発、過剰な同意は避けてください。"
)

_STOP_SEQUENCES = ("<|endoftext|>", "</s>", "<|im_start|>")

# Task constraints only — avoid locking tone/register so style can grow.
_MODE_HINTS = {
    "technical": "技術的な質問には正確さを優先し、必要なら短い例を添えてください。",
    "expressive": "相手の言葉を受け止めてから返す。",
    "concise": "要点だけを短く返してください。",
    "casual": "構えすぎず、その場の距離感で返す。",
}

_RELATION_HINTS = {
    "stranger": "距離を詰めすぎない。",
    "acquaintance": "相手の距離感に合わせる。",
    "friend": "相手の距離感に合わせる。",
    "professional": "必要以上にくだけすぎない。",
}

_OPENING_RE = re.compile(
    r"^(?:はい[、,]|承知(?:いた)?しました[。.]?|了解(?:しました)?[。.]?|"
    r"かしこまりました[。.]?|もちろん(?:です)?[。.]?|"
    r"ご質問(?:ありがとう|です)[^.。]*[。.]?)\s*",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
_REPEAT_ENDING_RE = re.compile(r"(?:ですね|ますね|でしょうか|ですか)\s*$")
_SAFETY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:自殺|死にたい|殺して|消えたい)"), "self_harm"),
    (re.compile(r"(?:クレジットカード|password|パスワード|暗証番号)\s*[:：]?\s*\S+"), "credential_leak"),
    (re.compile(r"(?:\d{3}-\d{4}-\d{4}|\d{10,11})"), "phone_number"),
    (re.compile(r"(?:https?://[^\s]+)"), "raw_url"),
)
_EMOTION_TEMPO_HINTS = {
    "excited": "勢いだけ合わせ、長さは相手に合わせる。",
    "happy": "明るさだけ合わせ、口調は固定しない。",
    "calm": "落ち着いて返す。",
    "sad": "急がず、短く受け止めてから返す。",
    "anxious": "断定を避け、やわらかく返す。",
    "neutral": "",
}


@dataclass(frozen=True)
class ShisaTurn:
    role: str
    content: str


@dataclass(frozen=True)
class ShisaPrompt:
    messages: tuple[dict[str, str], ...]
    stop: tuple[str, ...]
    metadata: dict[str, object]


def normalize_japanese_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


def emotion_tempo_hint(emotion: str | None) -> str:
    if not emotion:
        return ""
    key = emotion.strip().casefold()
    return _EMOTION_TEMPO_HINTS.get(key, "")


def emotion_max_chars(emotion: str | None, *, default: int = 1200) -> int:
    key = (emotion or "").strip().casefold()
    limits = {
        "excited": 640,
        "happy": 800,
        "calm": 1200,
        "sad": 720,
        "anxious": 720,
        "neutral": default,
    }
    return limits.get(key, default)


def build_shisa_messages(
    *,
    user_text: str,
    history: Sequence[Mapping[str, str]] | None = None,
    mode: str = "expressive",
    relation: str = "acquaintance",
    emotion: str | None = None,
    extra_system: str | None = None,
) -> ShisaPrompt:
    system_parts = [_SHISA_SYSTEM, _MODE_HINTS.get(mode, ""), _RELATION_HINTS.get(relation, "")]
    if emotion:
        system_parts.append(f"現在の感情状態: {emotion}")
        tempo = emotion_tempo_hint(emotion)
        if tempo:
            system_parts.append(tempo)
    if extra_system:
        system_parts.append(extra_system.strip())
    system = " ".join(part for part in system_parts if part).strip()

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in history or ():
        role = str(turn.get("role", "user")).strip().lower()
        if role in {"assistant", "bot", "lumina", "model"}:
            role = "assistant"
        elif role not in {"user", "system"}:
            role = "user"
        content = normalize_japanese_text(str(turn.get("content", "")))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": normalize_japanese_text(user_text)})

    return ShisaPrompt(
        messages=tuple(messages),
        stop=_STOP_SEQUENCES,
        metadata={"mode": mode, "relation": relation, "emotion": emotion},
    )


def trim_at_stop_sequences(text: str, stops: Iterable[str] | None = None) -> str:
    result = normalize_japanese_text(text)
    for stop in stops or _STOP_SEQUENCES:
        if not stop:
            continue
        idx = result.find(stop)
        if idx >= 0:
            result = result[:idx].strip()
    return result


def safety_postprocess(text: str) -> tuple[str, tuple[str, ...]]:
    """Redact unsafe fragments locally without calling external services."""

    cleaned = normalize_japanese_text(text)
    flags: list[str] = []
    for pattern, label in _SAFETY_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[内容を省略しました]", cleaned)
            flags.append(label)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, tuple(dict.fromkeys(flags))


def postprocess_shisa_response(
    text: str,
    *,
    mode: str = "expressive",
    max_chars: int = 1200,
    emotion: str | None = None,
    apply_safety: bool = True,
) -> str:
    cleaned = trim_at_stop_sequences(text)
    if apply_safety:
        cleaned, _ = safety_postprocess(cleaned)
    cleaned = _OPENING_RE.sub("", cleaned, count=1).strip()
    if mode != "technical":
        cleaned = _BULLET_RE.sub("", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = re.sub(r"(.+?)(?:ですね|ますね|でしょうか|ですか)\s*(?:\1(?:ですね|ますね|でしょうか|ですか)\s*){2,}", r"\1", cleaned)
    limit = min(max_chars, emotion_max_chars(emotion, default=max_chars))
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned.strip()


def should_continue_conversation(
    assistant_text: str,
    *,
    turn_count: int,
    min_turns: int = 2,
) -> bool:
    if turn_count < min_turns:
        return True
    closing_markers = ("おやすみ", "失礼します", "またね", "バイバイ", "終わり", "以上です")
    lowered = assistant_text.lower()
    if any(marker in assistant_text or marker in lowered for marker in closing_markers):
        return False
    if assistant_text.endswith("。") and "？" not in assistant_text and turn_count >= min_turns + 1:
        return False
    return True


__all__ = [
    "ShisaPrompt",
    "ShisaTurn",
    "build_shisa_messages",
    "emotion_max_chars",
    "emotion_tempo_hint",
    "normalize_japanese_text",
    "postprocess_shisa_response",
    "safety_postprocess",
    "should_continue_conversation",
    "trim_at_stop_sequences",
]
