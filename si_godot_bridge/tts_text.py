from __future__ import annotations

import json
import re
import textwrap

from typing import Any

MAX_TTS_TEXT_CHARS = 120
MAX_TTS_CLAUSE_CHARS = 48

_TARGETED_KEYS = (
    "text",
    "message",
    "answer",
    "speech",
    "utterance",
    "content",
    "value",
)

_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_CODE_FENCE_STRIP_RE = re.compile(r"`([^`]+)`")
_LEADING_LIST_PREFIX_RE = re.compile(r"(?m)^\s*[-*•]\s*")
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_PARENTHESIS_RE = re.compile(r"[()\[\]{}<>]")

_SENTENCE_BREAK_RE = re.compile(r"[。！？\n]+")
_SINGLE_SENTENCE_RE = re.compile(r"[。！？]")
_WHITESPACE_RE = re.compile(r"\s+")
_REPEATED_PUNCT_RE = re.compile(r"[、、]{2,}|[。]{2,}|[！？]{2,}")
_JAPANESE_SPACE_RE = re.compile(r"(?<=[ぁ-んァ-ン一-龥ー])\s+(?=[ぁ-んァ-ン一-龥ー])")
_GOJUON_ROWS = (
    "あいうえお",
    "かきくけこ",
    "さしすせそ",
    "たちつてと",
    "なにぬねの",
    "はひふへほ",
    "まみむめも",
    "やゆよ",
    "らりるれろ",
    "わをん",
)
_GOJUON_ROW_PAIRS = tuple(
    (current, following)
    for index, current in enumerate(_GOJUON_ROWS)
    for following in _GOJUON_ROWS[index + 1 :]
)
_GOJUON_FULL = "".join(_GOJUON_ROWS)
_GOJUON_JOINED = "、".join(_GOJUON_ROWS)
_GOJUON_MORA_SEPARATOR = r"[\s、,，・･／/|｜-]*"
_GOJUON_SINGLE_MORA_TTS = {
    "あ": "あー",
    "い": "いー",
    "う": "うー",
    "え": "えー",
    "お": "おー",
    "か": "かー",
    "き": "きー",
    "く": "くー",
    "け": "けー",
    "こ": "こー",
    "さ": "さー",
    "し": "しー",
    "す": "すー",
    "せ": "せー",
    "そ": "そー",
    "た": "たー",
    "ち": "ちー",
    "つ": "つー",
    "て": "てー",
    "と": "とー",
    "な": "なー",
    "に": "にー",
    "ぬ": "ぬー",
    "ね": "ねー",
    "の": "のー",
    "は": "ハー",
    "ハ": "ハー",
    "ひ": "ひー",
    "ふ": "ふー",
    "へ": "へー",
    "ヘ": "へー",
    "ほ": "ほー",
    "ま": "まー",
    "み": "みー",
    "む": "むー",
    "め": "めー",
    "も": "もー",
    "や": "やー",
    "ゆ": "ゆー",
    "よ": "よー",
    "ら": "らー",
    "り": "りー",
    "る": "るー",
    "れ": "れー",
    "ろ": "ろー",
    "わ": "わー",
    "を": "をー",
    "ん": "んー",
}
_GOJUON_SINGLE_MORA_RE = re.compile(r"^\s*([ぁ-んァ-ン])\s*[。！？!?]?\s*$")
_ENGLISH_TOKEN_REPLACEMENTS = [
    (re.compile(r"(?<![A-Za-z0-9_])AI\s+companion(?![A-Za-z0-9_])", re.IGNORECASE), "エーアイコンパニオン"),
    (re.compile(r"(?<![A-Za-z0-9_])AivisSpeech(?![A-Za-z0-9_])", re.IGNORECASE), "アイビススピーチ"),
    (re.compile(r"(?<![A-Za-z0-9_])NavigationAgent(?![A-Za-z0-9_])", re.IGNORECASE), "ナビゲーションエージェント"),
    (re.compile(r"(?<![A-Za-z0-9_])SENA(?![A-Za-z0-9_])", re.IGNORECASE), "ルミナ"),
    (re.compile(r"(?<![A-Za-z0-9_])LUMINA(?![A-Za-z0-9_])", re.IGNORECASE), "ルミナ"),
    (re.compile(r"(?<![A-Za-z0-9_])TOHA(?![A-Za-z0-9_])", re.IGNORECASE), "トハ"),
    (re.compile(r"(?<![A-Za-z0-9_])Godot(?![A-Za-z0-9_])", re.IGNORECASE), "ゴドット"),
    (re.compile(r"(?<![A-Za-z0-9_])VRM(?![A-Za-z0-9_])", re.IGNORECASE), "ブイアールエム"),
    (re.compile(r"(?<![A-Za-z0-9_])Ollama(?![A-Za-z0-9_])", re.IGNORECASE), "オラマ"),
    (re.compile(r"(?<![A-Za-z0-9_])Qwen(?![A-Za-z0-9_])", re.IGNORECASE), "キューウェン"),
    (re.compile(r"(?<![A-Za-z0-9_])VOICEVOX(?![A-Za-z0-9_])", re.IGNORECASE), "ボイスボックス"),
    (re.compile(r"(?<![A-Za-z0-9_])PIPER(?![A-Za-z0-9_])", re.IGNORECASE), "パイパー"),
    (re.compile(r"(?<![A-Za-z0-9_])LLM(?![A-Za-z0-9_])", re.IGNORECASE), "エルエルエム"),
    (re.compile(r"(?<![A-Za-z0-9_])AI(?![A-Za-z0-9_])", re.IGNORECASE), "エーアイ"),
    (re.compile(r"(?<![A-Za-z0-9_])Table(?![A-Za-z0-9_])", re.IGNORECASE), "テーブル"),
    (re.compile(r"(?<![A-Za-z0-9_])Chair(?![A-Za-z0-9_])", re.IGNORECASE), "チェア"),
    (re.compile(r"(?<![A-Za-z0-9_])Sofa(?![A-Za-z0-9_])", re.IGNORECASE), "ソファ"),
    (re.compile(r"(?<![A-Za-z0-9_])are\s+ready(?![A-Za-z0-9_])", re.IGNORECASE), "準備できています"),
    (re.compile(r"(?<![A-Za-z0-9_])is\s+ready(?![A-Za-z0-9_])", re.IGNORECASE), "準備できています"),
    (re.compile(r"(?<![A-Za-z0-9_])status\s+good(?![A-Za-z0-9_])", re.IGNORECASE), "ステータスは良好"),
    (re.compile(r"(?<![A-Za-z0-9_])walk_to(?![A-Za-z0-9_])", re.IGNORECASE), "歩いて向かう"),
    (re.compile(r"(?<![A-Za-z0-9_])look_at(?![A-Za-z0-9_])", re.IGNORECASE), "見る"),
    (re.compile(r"(?<![A-Za-z0-9_])set_mood(?![A-Za-z0-9_])", re.IGNORECASE), "ムードを変える"),
    (re.compile(r"(?<![A-Za-z0-9_])move_to(?![A-Za-z0-9_])", re.IGNORECASE), "移動する"),
    (re.compile(r"(?<![A-Za-z0-9_])look\s+at(?![A-Za-z0-9_])", re.IGNORECASE), "見る"),
    (re.compile(r"(?<![A-Za-z0-9_])walk\s+to(?![A-Za-z0-9_])", re.IGNORECASE), "歩いて向かう"),
    (re.compile(r"(?<![A-Za-z0-9_])move\s+to(?![A-Za-z0-9_])", re.IGNORECASE), "移動する"),
    (re.compile(r"(?<![A-Za-z0-9_])and(?![A-Za-z0-9_])", re.IGNORECASE), "と"),
    (re.compile(r"(?<![A-Za-z0-9_])or(?![A-Za-z0-9_])", re.IGNORECASE), "または"),
    (re.compile(r"(?<![A-Za-z0-9_])from(?![A-Za-z0-9_])", re.IGNORECASE), "から"),
    (re.compile(r"(?<![A-Za-z0-9_])to(?![A-Za-z0-9_])", re.IGNORECASE), "へ"),
    (re.compile(r"(?<![A-Za-z0-9_])then(?![A-Za-z0-9_])", re.IGNORECASE), "それから"),
    (re.compile(r"(?<![A-Za-z0-9_])the(?![A-Za-z0-9_])", re.IGNORECASE), ""),
    (re.compile(r"(?<![A-Za-z0-9_])now(?![A-Za-z0-9_])", re.IGNORECASE), "今"),
    (re.compile(r"(?<![A-Za-z0-9_])ready(?![A-Za-z0-9_])", re.IGNORECASE), "準備完了"),
    (re.compile(r"(?<![A-Za-z0-9_])status(?![A-Za-z0-9_])", re.IGNORECASE), "ステータス"),
    (re.compile(r"(?<![A-Za-z0-9_])good(?![A-Za-z0-9_])", re.IGNORECASE), "良好"),
    (re.compile(r"(?<![A-Za-z0-9_])user(?![A-Za-z0-9_])", re.IGNORECASE), "ユーザー"),
    (re.compile(r"(?<![A-Za-z0-9_])voice(?![A-Za-z0-9_])", re.IGNORECASE), "音声"),
    (re.compile(r"(?<![A-Za-z0-9_])speech(?![A-Za-z0-9_])", re.IGNORECASE), "発話"),
    (re.compile(r"(?<![A-Za-z0-9_])hello(?![A-Za-z0-9_])", re.IGNORECASE), "こんにちは"),
    (re.compile(r"(?<![A-Za-z0-9_])world(?![A-Za-z0-9_])", re.IGNORECASE), "世界"),
    (re.compile(r"(?<![A-Za-z0-9_])test(?![A-Za-z0-9_])", re.IGNORECASE), "テスト"),
    (re.compile(r"(?<![A-Za-z0-9_])idle(?![A-Za-z0-9_])", re.IGNORECASE), "待機"),
    (re.compile(r"(?<![A-Za-z0-9_])wait(?![A-Za-z0-9_])", re.IGNORECASE), "待つ"),
    (re.compile(r"(?<![A-Za-z0-9_])walk(?![A-Za-z0-9_])", re.IGNORECASE), "歩く"),
    (re.compile(r"(?<![A-Za-z0-9_])move(?![A-Za-z0-9_])", re.IGNORECASE), "移動"),
    (re.compile(r"(?<![A-Za-z0-9_])look(?![A-Za-z0-9_])", re.IGNORECASE), "見る"),
]


def normalize_for_tts_speak(value: str | None, *, max_chars: int = MAX_TTS_TEXT_CHARS) -> str:
    """Normalize text into a short, TTS-friendly, natural Japanese utterance."""
    if value is None:
        return ""

    raw = str(value).strip()
    if not raw:
        return ""

    raw = _decode_json_wrappers(raw)
    single_mora = _normalize_single_gojuon_mora(raw)
    if single_mora:
        return single_mora
    normalized = _normalize_symbols_and_punctuation(raw)
    normalized = _normalize_english_proper_nouns(normalized)
    normalized = _normalize_whitespace_and_punctuation(normalized)
    normalized = _insert_breath_markers(normalized, max_chars=max_chars)
    return _strip_terminal_sentence_punctuation(normalized)


def _strip_terminal_sentence_punctuation(text: str) -> str:
    candidate = text.strip()
    if len(candidate) <= 1:
        return candidate
    if candidate[-1:] in {"。", "！", "？"}:
        return candidate[:-1]
    return candidate


def _normalize_single_gojuon_mora(text: str) -> str:
    match = _GOJUON_SINGLE_MORA_RE.match(text)
    if not match:
        return ""
    return _GOJUON_SINGLE_MORA_TTS.get(match.group(1), "")


def _decode_json_wrappers(raw: str) -> str:
    decoded = _extract_text_from_payload(raw)
    if decoded is not None:
        return decoded
    return raw


def _extract_text_from_payload(raw: str) -> str | None:
    cleaned = _JSON_CODE_FENCE_RE.sub(r"\1", raw).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        return _extract_embedded_json_text(raw)
    return _coerce_text_from_payload(value)


def _extract_embedded_json_text(raw: str) -> str | None:
    for match in _JSON_OBJECT_RE.finditer(raw):
        snippet = match.group(0)
        try:
            payload = json.loads(snippet)
        except (json.JSONDecodeError, TypeError):
            continue
        value = _coerce_text_from_payload(payload)
        if value:
            return value
    return None


def _coerce_text_from_payload(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        for key in _TARGETED_KEYS:
            candidate = payload.get(key)
            text = _coerce_text_from_payload(candidate)
            if text:
                return text
        intent_value = payload.get("intent")
        if isinstance(intent_value, dict):
            text = _coerce_text_from_payload(intent_value)
            if text:
                return text
        return None
    if isinstance(payload, list):
        for item in payload:
            text = _coerce_text_from_payload(item)
            if text:
                return text
    return None


def _normalize_symbols_and_punctuation(raw: str) -> str:
    text = raw
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    text = _CODE_FENCE_STRIP_RE.sub(r"\1", text)
    text = _PARENTHESIS_RE.sub("", text)
    text = text.replace("\u3000", " ")
    text = text.replace("\ufeff", "")
    text = _LEADING_LIST_PREFIX_RE.sub("", text)
    text = text.replace("...", "。")
    text = text.replace("…", "。")
    text = text.replace(",", "、")
    text = re.sub(r"(?<!\d)\.(?!\d)", "。", text)
    text = text.replace("!!", "。")
    text = text.replace("??", "。")
    text = text.replace(";;", "、")
    text = text.replace(";", "、")
    text = text.replace(":", "、")
    text = text.replace("！", "。")
    text = text.replace("？", "。")
    text = text.replace("!", "。")
    text = text.replace("?", "。")
    text = re.sub(r"[\"`']", "", text)
    text = _normalize_gojuon_sequences(text)
    return text


def _normalize_gojuon_sequences(text: str) -> str:
    """Keep gojuon rows readable instead of joining row separators away."""
    out = text.replace("五十音", "ごじゅうおん").replace("50音", "ごじゅうおん")
    out = out.replace(_GOJUON_FULL, _GOJUON_JOINED)

    for row in _GOJUON_ROWS:
        separated = _GOJUON_MORA_SEPARATOR.join(map(re.escape, row))
        out = re.sub(separated, row, out)

    for current, following in _GOJUON_ROW_PAIRS:
        out = re.sub(rf"{re.escape(current)}[\s、,，;；:：]+(?={re.escape(following)})", f"{current}、", out)
        out = re.sub(rf"{re.escape(current)}(?={re.escape(following)})", f"{current}、", out)
    return out


def _normalize_english_proper_nouns(raw: str) -> str:
    text = raw
    for pattern, replacement in _ENGLISH_TOKEN_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _normalize_whitespace_and_punctuation(text: str) -> str:
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = re.sub(r"([、。])\s+", r"\1", text)
    text = _JAPANESE_SPACE_RE.sub("", text)
    text = _REPEATED_PUNCT_RE.sub(lambda match: match.group(0)[0], text)
    return text


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_BREAK_RE.split(text) if part.strip()]
    return parts


def _chunk_sentence(sentence: str, *, max_chars: int) -> list[str]:
    if not sentence:
        return []
    if len(sentence) <= max_chars:
        return [sentence]
    chunks = [segment.strip() for segment in sentence.split("、") if segment.strip()]
    output: list[str] = []
    for segment in chunks:
        if len(segment) <= max_chars:
            output.append(segment)
            continue
        output.extend(textwrap.wrap(segment, width=max_chars, break_long_words=True, break_on_hyphens=False))
    if not output:
        output.extend(textwrap.wrap(sentence, width=max_chars, break_long_words=True, break_on_hyphens=False))
    return [chunk for chunk in output if chunk.strip()]


def _join_with_breath_marks(sentence_chunks: list[str]) -> str:
    if not sentence_chunks:
        return ""
    return "、".join(sentence_chunks)


def _insert_breath_markers(text: str, *, max_chars: int) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        return _truncate_to_length(_normalize_whitespace_and_punctuation(text), max_chars=max_chars)

    chunked_segments: list[str] = []
    for sentence in sentences:
        if len(sentence) > MAX_TTS_CLAUSE_CHARS:
            chunks = _chunk_sentence(sentence, max_chars=MAX_TTS_CLAUSE_CHARS)
            joined = _join_with_breath_marks(chunks)
            if joined:
                chunked_segments.append(joined)
        else:
            chunked_segments.append(sentence)

    if not chunked_segments:
        return _truncate_to_length(text, max_chars=max_chars)

    compact = "。".join(chunked_segments)
    compact = compact.strip()
    if _SINGLE_SENTENCE_RE.search(text):
        if not compact.endswith(("。", "！", "？")):
            compact = f"{compact}。"
    compact = _normalize_whitespace_and_punctuation(compact)
    return _truncate_to_length(compact, max_chars=max_chars)


def _truncate_to_length(text: str, *, max_chars: int) -> str:
    candidate = text.strip()
    if len(candidate) <= max_chars:
        return candidate
    if max_chars <= 3:
        return candidate[:max_chars]

    parts = [part.strip() for part in candidate.split("。") if part.strip()]
    kept: list[str] = []
    current = ""
    for part in parts:
        separator = "。" if kept else ""
        next_fragment = f"{separator}{part}"
        if len(current) + len(next_fragment) > max_chars:
            break
        kept.append(part)
        current = f"{current}{separator}{part}"
    if not kept:
        return candidate[: max_chars - 1] + "。"
    return f"{'。'.join(kept)}。"
