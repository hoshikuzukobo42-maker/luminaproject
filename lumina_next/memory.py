from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .sqlite_memory import SQLiteMemoryAdapter


def _bounded_env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _trim_latest_lines(lines: list[str], max_chars: int) -> str:
    """Keep chronological order while dropping the oldest overflow first."""
    bounded = [line for line in lines if line.strip()]
    while len(bounded) > 1 and len("\n".join(bounded)) > max_chars:
        bounded.pop(0)
    return "\n".join(bounded)[-max_chars:]


class JsonMemoryAdapter:
    """Read the existing Lumina JSON memory without changing its schema."""

    def __init__(self, memory_dir: Path, write_enabled: bool = False) -> None:
        self.memory_dir = Path(memory_dir)
        self.write_enabled = write_enabled
        self._turn_log = self.memory_dir / "lumina_next_turns.jsonl"

    def _read_json(self, name: str, default: Any) -> Any:
        path = self.memory_dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def session_key(guild_id: str, channel_id: str, session_id: str | None = None) -> str:
        if session_id:
            return str(session_id)
        return f"g:{guild_id}|c:{channel_id}"

    def retrieve(
        self,
        session_key: str,
        query: str,
        max_chars: int = 1800,
        query_embedding: list[float] | None = None,
        max_results: int = 6,
    ) -> str:
        max_chars = min(max_chars, _bounded_env_int("LUMINA_NEXT_MEMORY_MAX_CHARS", max_chars))
        recent_count = _bounded_env_int("LUMINA_NEXT_RECENT_MESSAGES", 8)
        message_chars = _bounded_env_int("LUMINA_NEXT_RECENT_MESSAGE_CHARS", 240)
        sessions = self._read_json("sessions.json", {})
        long_term = self._read_json("long_term.json", {})
        recent_lines: list[str] = []
        memory_lines: list[str] = []

        entry = sessions.get("sessions", {}).get(session_key, {}) if isinstance(sessions, dict) else {}
        history = entry.get("history", []) if isinstance(entry, dict) else []
        if isinstance(history, list):
            for item in history[-recent_count:]:
                if not isinstance(item, dict):
                    continue
                role = "ユーザー" if item.get("role") == "user" else "Lumina"
                text = str(item.get("text") or "").strip()[:message_chars]
                if text:
                    recent_lines.append(f"recent:{role}: {text}")

        if isinstance(long_term, dict):
            for key in ("project_facts", "facts", "rules"):
                value = long_term.get(key)
                if isinstance(value, list):
                    for item in value[-6:]:
                        if isinstance(item, str) and item.strip():
                            memory_lines.append(f"memory:{item.strip()}")
                        elif isinstance(item, dict):
                            compact = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                            memory_lines.append(f"memory:{compact}")
            for key in ("user_preferences", "preferences"):
                value = long_term.get(key)
                if isinstance(value, dict):
                    for item_key, item_value in list(value.items())[-8:]:
                        memory_lines.append(f"preference:{item_key}={item_value}")

        query_terms = set(
            re.findall(r"[A-Za-z0-9_]{2,}|[一-龥々〆ヵヶァ-ヴー]{2,}", str(query or ""))
        )
        ranked: list[tuple[int, str]] = []
        for line in memory_lines:
            score = sum(term.lower() in line.lower() for term in query_terms)
            if score > 0:
                ranked.append((score, line))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = recent_lines + [line for _, line in ranked[: max(1, max_results)]]
        return _trim_latest_lines(selected, max_chars)

    def record_turn(self, session_key: str, user_text: str, answer_text: str) -> bool:
        if not self.write_enabled:
            return False
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "session_key": session_key,
            "user": user_text,
            "assistant": answer_text,
        }
        with self._turn_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True

    def state_policy(self, session_key: str) -> str:
        return "口調は固定しない。距離を詰めすぎず、質問を連続させず、断定しすぎない。"

    def stats(self) -> dict[str, Any]:
        return {
            "long_term_exists": (self.memory_dir / "long_term.json").is_file(),
            "sessions_exists": (self.memory_dir / "sessions.json").is_file(),
            "write_enabled": self.write_enabled,
        }


class ShadowMemoryAdapter:
    """Read legacy JSON plus SQLite, while writing only to the new database."""

    def __init__(self, legacy: JsonMemoryAdapter, sqlite: SQLiteMemoryAdapter) -> None:
        self.legacy = legacy
        self.sqlite = sqlite
        self.write_enabled = sqlite.write_enabled

    @staticmethod
    def session_key(guild_id: str, channel_id: str, session_id: str | None = None) -> str:
        return SQLiteMemoryAdapter.session_key(guild_id, channel_id, session_id)

    def retrieve(
        self,
        session_key: str,
        query: str,
        max_chars: int = 1800,
        query_embedding: list[float] | None = None,
        max_results: int = 6,
    ) -> str:
        max_chars = min(max_chars, _bounded_env_int("LUMINA_NEXT_MEMORY_MAX_CHARS", max_chars))
        blocks = [
            # Legacy JSON predates the SQLite dialogue.  Preserve real
            # chronology so the role list handed to Way is old -> new.
            self.legacy.retrieve(session_key, query, max_chars, max_results=max_results),
            self.sqlite.retrieve(
                session_key,
                query,
                max_chars,
                query_embedding=query_embedding,
                max_results=max_results,
            ),
        ]
        lines: list[str] = []
        seen: set[str] = set()
        for block in blocks:
            for line in block.splitlines():
                key = line.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    lines.append(line)
        return _trim_latest_lines(lines, max_chars)

    def record_turn(self, session_key: str, user_text: str, answer_text: str) -> bool:
        return self.sqlite.record_turn(session_key, user_text, answer_text)

    def state_policy(self, session_key: str) -> str:
        return self.sqlite.state_policy(session_key)

    def stats(self) -> dict[str, Any]:
        return {"legacy": self.legacy.stats(), "sqlite": self.sqlite.stats()}


def build_memory_adapter(provider: str, legacy_dir: Path, sqlite_path: Path, write_enabled: bool):
    normalized = str(provider or "shadow").strip().lower()
    legacy = JsonMemoryAdapter(legacy_dir, write_enabled=False)
    if normalized == "json":
        return legacy
    sqlite = SQLiteMemoryAdapter(sqlite_path, write_enabled=write_enabled)
    if normalized == "sqlite":
        return sqlite
    return ShadowMemoryAdapter(legacy, sqlite)
