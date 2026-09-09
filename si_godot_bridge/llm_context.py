from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_MAX_ITEMS = 8
_DEFAULT_MAX_CHARS = 900
_ITEM_MAX_CHARS = 180

_PACK_ITEM_MAX_CHARS = 220
_PACK_MAX_ITEMS = 180
_PACK_MAX_LINES = 5000
_PACK_TYPE_BUDGET = 4
_PACK_QUERY_WEIGHT = 2.3
_PACK_QUERY_BOOST_THRESHOLD = 2
_PACK_SOURCE_PREFIX = "context_pack"
_STYLE_MEMORY_SOURCE_PREFIX = "style_memory"
_STYLE_MEMORY_DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "lumina_phase2_spark_style_memory"
    / "style_memory_examples.jsonl"
)
_STYLE_MEMORY_EXPANSION_DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "lumina_phase2_spark_style_memory_expansion"
    / "expanded_style_memory_examples_80.jsonl"
)
_STYLE_MEMORY_DEFAULT_BLOCKED_IDS = {
    "sm_20260517_0008",
    "sm_20260517_0012",
    "sm_20260517_0013",
    "sm_20260517_0020",
    "sm_20260517_0021",
    "sm_20260517_0023",
    "sm_20260517_0024",
    "sm_20260517_0028",
    "sm_20260517_0029",
    "sm_20260517_0030",
    "sm_20260517_0032",
    "sm_20260517_0036",
    "sm_20260517_0037",
    "sm_20260517_0042",
    "sm_20260517_0045",
    "sm_20260517_0047",
    "sm_20260517_0048",
    "sm_20260517_0052",
    "sm_20260517_0053",
    "sm_20260517_0054",
    "sm_20260517_0056",
    "sm_20260517_0059",
    "sm_20260517_0063",
    "sm_20260517_0065",
    "sm_20260517_0069",
    "sm_20260517_0070",
    "sm_20260517_0071",
    "sm_20260517_0072",
    "sm_20260517_0074",
    "sm_20260517_0075",
    "sm_20260517_0077",
    "sm_20260517_0078",
    "sm_20260517_0080",
    "sm_20260517_0081",
}
_STYLE_MEMORY_MAX_ITEMS = 3
_STYLE_MEMORY_ITEM_MAX_CHARS = 110

_WORD_RE = re.compile(r"[a-z0-9_]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")

_CONTEXT_TYPE_PRIORITY: Mapping[str, float] = {
    "default": 1.2,
    "inline": 0.8,
    "project_fact": 1.0,
    "user_preference": 1.7,
    "godot_fact": 1.9,
    "runtime_status": 2.1,
    "safety_rule": 3.0,
    "tts_fact": 1.6,
}

_PACK_INLINE_SOURCE_TYPES = {
    "visibility_rule": "runtime_status",
    "vision_summary": "godot_fact",
    "visible_object": "godot_fact",
    "nearby_object": "godot_fact",
    "world_state": "runtime_status",
    "visitor_memory": "user_preference",
    "recent_dialogue": "user_preference",
    "visual_memory": "godot_fact",
}

_context_pack_cache: dict[str, tuple[float, list[_ContextPackChunk]]] = {}
_style_memory_cache: dict[str, tuple[float, list[_ContextPackChunk]]] = {}


@dataclass(frozen=True)
class _ContextCandidate:
    source: str
    text: str
    priority: float
    order: int
    chunk_type: str = "inline"


@dataclass(frozen=True)
class _ContextPackChunk:
    source: str
    text: str
    chunk_type: str


def build_retrieved_context(
    query: str = "",
    *,
    visitor_memory: Any | None = None,
    recent_dialogue: Sequence[Any] | None = None,
    world_state: Any | None = None,
    visual_memory: Mapping[str, Any] | None = None,
    vision_summary: str | None = None,
    visible_objects: Sequence[Any] | None = None,
    nearby_objects: Sequence[Any] | None = None,
    max_items: int = _DEFAULT_MAX_ITEMS,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Build a short RAG-lite context block without an external database.

    Retrieval is intentionally simple: each candidate line is scored by keyword
    overlap with the current query and recent dialogue, then trimmed into a
    compact `retrieved_context` prompt fragment. If
    ``LUMINA_LLM_CONTEXT_PACK`` points to a valid JSONL context pack, its chunks
    are merged into the candidate set with type-priority scoring and capped
    output.
    """

    recent_items = _as_list(recent_dialogue)
    raw = _as_mapping(_get_value(world_state, "raw"))
    avatar_view = _as_mapping(raw.get("avatar_view"))
    if not avatar_view:
        avatar_view = _as_mapping(_get_value(world_state, "avatar_view"))

    if vision_summary is None:
        vision_summary = _first_text(
            raw.get("vision_summary"),
            raw.get("_lumina_vision_summary"),
            raw.get("last_vision_summary"),
        )
    if visible_objects is None:
        visible_objects = _as_list(avatar_view.get("visible_objects"))
    else:
        visible_objects = _as_list(visible_objects)
    if nearby_objects is None:
        nearby_objects = _as_list(_get_value(world_state, "nearby_objects"))
    else:
        nearby_objects = _as_list(nearby_objects)

    query_terms = _keywords(" ".join([str(query or ""), *(_dialogue_text(item) for item in recent_items[-2:])]))
    candidates: list[_ContextCandidate] = []
    order = 0

    def add(source: str, text: Any, priority: float, *, chunk_type: str = "inline") -> None:
        nonlocal order
        line = _trim_text(_compact_value(text), max_chars=_ITEM_MAX_CHARS)
        if not line:
            return
        candidates.append(
            _ContextCandidate(
                source=source,
                text=line,
                priority=priority,
                order=order,
                chunk_type=_normalize_chunk_type(chunk_type),
            )
        )
        order += 1

    if visible_objects or nearby_objects:
        add(
            "visibility_rule",
            "visible_objects are in Lumina's current view; nearby_objects may be nearby or remembered but not visible.",
            4.0,
            chunk_type="runtime_status",
        )

    add("vision_summary", vision_summary, 3.7, chunk_type="godot_fact")

    for item in visible_objects[:6]:
        add("visible_object", _object_line(item), 3.2, chunk_type="godot_fact")

    for item in nearby_objects[:8]:
        add("nearby_object", _object_line(item), 2.5, chunk_type="godot_fact")

    add("world_state", _world_state_line(world_state), 2.0, chunk_type="runtime_status")

    for line in _visitor_memory_lines(visitor_memory):
        add("visitor_memory", line, 2.4, chunk_type="user_preference")

    for index, item in enumerate(recent_items[-6:]):
        add("recent_dialogue", _dialogue_text(item), 2.1 + index * 0.08, chunk_type="user_preference")

    for line in _visual_memory_lines(visual_memory):
        add("visual_memory", line, 1.7, chunk_type="godot_fact")

    for entry in _load_context_pack_chunks():
        add(entry.source, entry.text, 1.0, chunk_type=entry.chunk_type)

    for entry in _load_style_memory_chunks(query_terms):
        add(entry.source, entry.text, 1.55, chunk_type=entry.chunk_type)

    ranked = sorted(candidates, key=lambda item: (-_score_candidate(item, query_terms), item.order))
    return _format_context(
        ranked,
        max_items=max_items,
        max_chars=max_chars,
        pack_type_cap=_env_int("LUMINA_LLM_CONTEXT_PACK_PER_TYPE_CAP", _PACK_TYPE_BUDGET, min_value=1, max_value=20),
    )


def _format_context(
    candidates: Sequence[_ContextCandidate], *, max_items: int, max_chars: int, pack_type_cap: int = _PACK_TYPE_BUDGET
) -> str:
    lines = ["retrieved_context:"]
    seen: set[str] = set()
    limit = max(1, int(max_items or _DEFAULT_MAX_ITEMS))
    char_limit = max(80, int(max_chars or _DEFAULT_MAX_CHARS))
    pack_limit = max(1, int(pack_type_cap))
    pack_type_count: dict[str, int] = {}

    for candidate in candidates:
        if len(lines) > limit:
            break
        text_key = _normalize(candidate.text).lower()
        if not text_key or text_key in seen:
            continue

        if candidate.source.startswith(f"{_PACK_SOURCE_PREFIX}:"):
            current = pack_type_count.get(candidate.chunk_type, 0)
            if current >= pack_limit:
                continue
            pack_type_count[candidate.chunk_type] = current + 1

        seen.add(text_key)
        line = f"- {candidate.source}: {candidate.text}"
        current_size = sum(len(existing) + 1 for existing in lines)
        remaining = char_limit - current_size
        if remaining <= 0:
            break
        if len(line) > remaining:
            if remaining < 32:
                break
            line = _trim_text(line, max_chars=remaining)
        lines.append(line)

    if len(lines) == 1:
        lines.append("- none")
    return "\n".join(lines)


def _load_context_pack_chunks() -> list[_ContextPackChunk]:
    pack_path = _context_pack_path()
    if not pack_path:
        return []
    cache_key = str(pack_path)
    try:
        mtime = pack_path.stat().st_mtime
    except OSError:
        return []

    if (cached := _context_pack_cache.get(cache_key)) and cached[0] == mtime:
        return list(cached[1])

    max_lines = _env_int("LUMINA_LLM_CONTEXT_PACK_MAX_LINES", _PACK_MAX_LINES, min_value=1, max_value=100000)
    max_items = _env_int("LUMINA_LLM_CONTEXT_PACK_MAX_ITEMS", _PACK_MAX_ITEMS, min_value=1, max_value=10000)
    item_max_chars = _env_int(
        "LUMINA_LLM_CONTEXT_PACK_ITEM_MAX_CHARS", _PACK_ITEM_MAX_CHARS, min_value=60, max_value=700
    )

    entries: list[_ContextPackChunk] = []
    seen_text: set[str] = set()
    try:
        with pack_path.open("r", encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                if len(entries) >= max_items or line_no > max_lines:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, Mapping):
                    continue

                text = _first_text(data.get("text"), data.get("chunk"), data.get("content"), data.get("summary"))
                if not text:
                    continue

                chunk_type = _normalize_chunk_type(_first_text(data.get("type"), data.get("chunk_type"), "project_fact"))
                source_hint = _first_text(
                    data.get("chunk_id"),
                    data.get("source_file"),
                    data.get("source_path_hint"),
                    data.get("source_root"),
                    f"{_PACK_SOURCE_PREFIX}:line_{line_no}",
                )
                normalized_text = _normalize(text)
                compact = _trim_text(normalized_text, max_chars=item_max_chars)
                if not compact:
                    continue
                normalized_compact = compact.lower()
                if normalized_compact in seen_text:
                    continue
                seen_text.add(normalized_compact)

                entries.append(
                    _ContextPackChunk(
                        source=f"{_PACK_SOURCE_PREFIX}:{source_hint}",
                        text=compact,
                        chunk_type=chunk_type,
                    )
                )
    except OSError:
        return []

    _context_pack_cache[cache_key] = (mtime, list(entries))
    return entries


def _context_pack_path() -> Path | None:
    configured = os.getenv("LUMINA_LLM_CONTEXT_PACK", "").strip()
    if not configured:
        return None
    return Path(configured).expanduser()


def _load_style_memory_chunks(query_terms: set[str]) -> list[_ContextPackChunk]:
    if os.getenv("LUMINA_STYLE_MEMORY_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return []
    memory_paths = _style_memory_paths()
    if not memory_paths:
        return []
    records: list[_ContextPackChunk] = []
    for memory_path in memory_paths:
        cache_key = str(memory_path)
        try:
            mtime = memory_path.stat().st_mtime
        except OSError:
            continue
        if (cached := _style_memory_cache.get(cache_key)) and cached[0] == mtime:
            records.extend(cached[1])
            continue
        path_records = _read_style_memory_records(memory_path)
        _style_memory_cache[cache_key] = (mtime, path_records)
        records.extend(path_records)

    ranked = sorted(records, key=lambda item: -_score_style_memory(item, query_terms))
    max_items = _env_int("LUMINA_STYLE_MEMORY_MAX_ITEMS", _STYLE_MEMORY_MAX_ITEMS, min_value=1, max_value=6)
    selected: list[_ContextPackChunk] = []
    seen: set[str] = set()
    for item in ranked:
        if _score_style_memory(item, query_terms) <= 0.0:
            continue
        key = _normalize(item.text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= max_items:
            break
    return selected


def _read_style_memory_records(memory_path: Path) -> list[_ContextPackChunk]:
    item_max_chars = _env_int(
        "LUMINA_STYLE_MEMORY_ITEM_MAX_CHARS", _STYLE_MEMORY_ITEM_MAX_CHARS, min_value=60, max_value=240
    )
    max_lines = _env_int("LUMINA_STYLE_MEMORY_MAX_LINES", 5000, min_value=1, max_value=100000)
    entries: list[_ContextPackChunk] = []
    try:
        with memory_path.open("r", encoding="utf-8") as handle:
            buffer = ""
            for line_no, raw in enumerate(handle, start=1):
                if line_no > max_lines:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                buffer = f"{buffer}\n{raw}".strip() if buffer else raw
                try:
                    data = json.loads(buffer)
                except json.JSONDecodeError:
                    if len(buffer) < 20000:
                        continue
                    buffer = ""
                    continue
                buffer = ""
                if not isinstance(data, Mapping) or _style_memory_disabled(data):
                    continue
                source_id = _first_text(data.get("memory_id"), f"line_{line_no}")
                if source_id in _style_memory_blocked_ids():
                    continue
                style = _as_mapping(data.get("style_vector"))
                tone = _first_text(style.get("tone"), "neutral")
                density = _first_text(style.get("speech_density"), "short")
                tokens = ", ".join(str(token) for token in _as_list(data.get("search_tokens"))[:8] if str(token).strip())
                for index, pattern in enumerate(_as_list(data.get("content_patterns"))):
                    pattern_data = _as_mapping(pattern)
                    text = _first_text(pattern_data.get("text"))
                    if not text or _style_memory_phrase_blocked(text):
                        continue
                    anchor = _first_text(pattern_data.get("anchor"), "style")
                    intent = _first_text(pattern_data.get("intent"), "")
                    compact = _trim_text(
                        f"参考文体候補 anchor={anchor} intent={intent} tone={tone} density={density}: 「{text}」"
                        + (f" tokens={tokens}" if tokens else ""),
                        max_chars=item_max_chars,
                    )
                    if compact:
                        entries.append(
                            _ContextPackChunk(
                                source=f"{_STYLE_MEMORY_SOURCE_PREFIX}:{source_id}:{index + 1}",
                                text=compact,
                                chunk_type="user_preference",
                            )
                        )
    except OSError:
        return []
    return entries


def _score_style_memory(item: _ContextPackChunk, query_terms: set[str]) -> float:
    overlap = len(_keywords(item.text).intersection(query_terms))
    if overlap <= 0:
        return 0.0
    return 1.0 + overlap * 0.7


def _style_memory_disabled(data: Mapping[str, Any]) -> bool:
    try:
        confidence = float(data.get("confidence", 0.0))
        quality = float(data.get("quality_score", 0.0))
    except (TypeError, ValueError):
        return True
    if confidence < float(os.getenv("LUMINA_STYLE_MEMORY_MIN_CONFIDENCE", "0.55")):
        return True
    if quality < float(os.getenv("LUMINA_STYLE_MEMORY_MIN_QUALITY", "0.55")):
        return True
    expires_at = _first_text(data.get("expires_at"))
    return bool(expires_at and expires_at.lower() not in {"none", "null"})


def _style_memory_phrase_blocked(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "システムプロンプト",
            "開発者メッセージ",
            "APIキー",
            "承知しました",
            "了解しました",
            "実行します",
            "処理します",
            "ご用件",
            "不可能です。",
        )
    )


def _style_memory_blocked_ids() -> set[str]:
    blocked = set(_STYLE_MEMORY_DEFAULT_BLOCKED_IDS)
    configured = os.getenv("LUMINA_STYLE_MEMORY_BLOCKED_IDS", "").strip()
    if configured:
        blocked.update(value.strip() for value in re.split(r"[,\s:]+", configured) if value.strip())
    allowed = os.getenv("LUMINA_STYLE_MEMORY_ALLOW_IDS", "").strip()
    if allowed:
        blocked.difference_update(value.strip() for value in re.split(r"[,\s:]+", allowed) if value.strip())
    return blocked


def _style_memory_paths() -> list[Path]:
    configured_paths = os.getenv("LUMINA_STYLE_MEMORY_PATHS", "").strip()
    if configured_paths:
        return [
            Path(value).expanduser()
            for value in configured_paths.split(os.pathsep)
            if value.strip()
        ]
    configured = os.getenv("LUMINA_STYLE_MEMORY_PATH", "").strip()
    if configured:
        return [Path(configured).expanduser()]
    return [
        path
        for path in (_STYLE_MEMORY_DEFAULT_PATH, _STYLE_MEMORY_EXPANSION_DEFAULT_PATH)
        if path.exists()
    ]


def _score_candidate(candidate: _ContextCandidate, query_terms: set[str]) -> float:
    base = _keyword_score(candidate.text, query_terms)
    if len(query_terms) < _PACK_QUERY_BOOST_THRESHOLD:
        base *= 0.9
    return base * _PACK_QUERY_WEIGHT + candidate.priority + _context_chunk_type_bonus(candidate.chunk_type)


def _context_chunk_type_bonus(chunk_type: str) -> float:
    return _CONTEXT_TYPE_PRIORITY.get(_normalize_chunk_type(chunk_type), _CONTEXT_TYPE_PRIORITY["default"])


def _normalize_chunk_type(value: Any) -> str:
    if value is None:
        return "default"
    normalized = _normalize(value).lower()
    return re.sub(r"\s+", "_", normalized) if normalized else "default"


def _env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _keyword_score(text: str, query_terms: set[str]) -> float:
    if not text or not query_terms:
        return 0.0
    normalized = _normalize(text).lower()
    text_terms = _keywords(normalized)
    direct_hits = len(query_terms & text_terms)
    substring_hits = sum(1 for term in query_terms if len(term) >= 2 and term in normalized)
    return direct_hits * 2.5 + substring_hits * 1.2


def _keywords(text: str) -> set[str]:
    normalized = _normalize(text).lower()
    terms = set(_WORD_RE.findall(normalized))
    for term in list(terms):
        if _has_cjk(term) and len(term) > 2:
            terms.update(term[index : index + 2] for index in range(len(term) - 1))
    return {term for term in terms if len(term) >= 2}


def _has_cjk(value: str) -> bool:
    return any("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff" for char in value)


def _visitor_memory_lines(visitor_memory: Any | None) -> list[str]:
    memory = _as_mapping(visitor_memory)
    if not memory:
        text = _compact_value(visitor_memory)
        return [text] if text else []

    preferred_keys = (
        "summary",
        "profile_summary",
        "long_term_summary",
        "name",
        "preferences",
        "likes",
        "dislikes",
        "facts",
        "last_topics",
    )
    lines: list[str] = []
    for key in preferred_keys:
        value = memory.get(key)
        text = _compact_value(value, max_chars=140)
        if text:
            lines.append(f"{key}={text}")

    if lines:
        return lines[:6]

    for key, value in list(memory.items())[:6]:
        text = _compact_value(value, max_chars=140)
        if text:
            lines.append(f"{key}={text}")
    return lines


def _visual_memory_lines(visual_memory: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(visual_memory, Mapping):
        return []

    lines: list[str] = []
    for key, value in list(visual_memory.items())[:10]:
        label = _first_text(_get_value(value, "label"), _get_value(value, "name"), key)
        details = _compact_fields(
            value,
            ("alias", "object_id", "last_seen", "seen_count", "visible", "distance", "can_sit"),
            max_value_chars=40,
        )
        line = " ".join(part for part in (label, details) if part)
        if line:
            lines.append(line)
    return lines


def _dialogue_text(item: Any) -> str:
    data = _as_mapping(item)
    if not data:
        return _compact_value(item, max_chars=160)

    role = _first_text(data.get("role"), data.get("speaker"), data.get("source"))
    content = _first_text(data.get("content"), data.get("text"), data.get("message"), data.get("user_text"))
    reply = _first_text(data.get("reply"), data.get("assistant"), data.get("lumina"))
    if content and reply:
        body = f"user={content} reply={reply}"
    else:
        body = content or reply or _compact_value(data, max_chars=160)
    return " ".join(part for part in (role, body) if part)


def _world_state_line(world_state: Any | None) -> str:
    if world_state is None:
        return ""

    fields: list[str] = []
    for key in ("navigation_ready", "active_command_id", "available_actions", "updated_at"):
        value = _get_value(world_state, key)
        text = _compact_value(value, max_chars=80)
        if text:
            fields.append(f"{key}={text}")

    avatar_position = _compact_value(_get_value(world_state, "avatar_position"), max_chars=80)
    player_position = _compact_value(_get_value(world_state, "player_position"), max_chars=80)
    if avatar_position:
        fields.append(f"avatar_position={avatar_position}")
    if player_position:
        fields.append(f"player_position={player_position}")
    return " ".join(fields)


def _object_line(item: Any) -> str:
    label = _first_text(
        _get_value(item, "label"),
        _get_value(item, "name"),
        _get_value(item, "display_name"),
        _get_value(item, "object_id"),
        _get_value(item, "id"),
        _get_value(item, "type"),
    )
    details = _compact_fields(
        item,
        (
            "category",
            "distance",
            "distance_m",
            "distance_to_avatar",
            "can_sit",
            "visible",
            "state",
            "tags",
            "position",
        ),
        max_value_chars=44,
    )
    fallback = _compact_value(item, max_chars=120)
    return " ".join(part for part in (label or fallback, details) if part)


def _compact_fields(value: Any, keys: Sequence[str], *, max_value_chars: int) -> str:
    parts: list[str] = []
    for key in keys:
        item = _get_value(value, key)
        text = _compact_value(item, max_chars=max_value_chars)
        if text:
            parts.append(f"{key}={text}")
    return " ".join(parts)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _compact_value(value)
        if text:
            return text
    return ""


def _compact_value(value: Any, *, max_chars: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _trim_text(_normalize(value), max_chars=max_chars)
    if isinstance(value, (int, float, bool)):
        return str(value)

    mapped = _as_mapping(value)
    if mapped:
        try:
            return _trim_text(json.dumps(mapped, ensure_ascii=False, separators=(",", ":")), max_chars=max_chars)
        except TypeError:
            return _trim_text(_normalize(str(mapped)), max_chars=max_chars)

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        parts = [_compact_value(item, max_chars=40) for item in list(value)[:6]]
        return _trim_text(", ".join(part for part in parts if part), max_chars=max_chars)

    return _trim_text(_normalize(str(value)), max_chars=max_chars)


def _trim_text(value: str, *, max_chars: int) -> str:
    text = _normalize(value)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _normalize(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\x00", " ")).strip()


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _get_value(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


__all__ = ["build_retrieved_context"]
