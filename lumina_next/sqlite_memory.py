from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


_QUERY_TERM_RE = re.compile(r"[A-Za-z0-9_]{2,}|[一-龥々〆ヵヶァ-ヴー]{2,}")
_INITIALIZE_LOCK_ATTEMPTS = 3
_INITIALIZE_LOCK_RETRY_SECONDS = 0.25


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


@dataclass(frozen=True)
class ImportSummary:
    profile_facts: int = 0
    conversation_turns: int = 0


class SQLiteMemoryAdapter:
    def __init__(self, db_path: Path, write_enabled: bool = True) -> None:
        self.db_path = Path(db_path)
        self.write_enabled = bool(write_enabled)
        self.sqlite_vec_version: str | None = None
        if self.write_enabled:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(_INITIALIZE_LOCK_ATTEMPTS):
                try:
                    self.initialize()
                    break
                except sqlite3.OperationalError as error:
                    retryable = "locked" in str(error).lower()
                    last_attempt = attempt + 1 >= _INITIALIZE_LOCK_ATTEMPTS
                    if not retryable or last_attempt:
                        raise
                    # Product startup can overlap the final commit/checkpoint of
                    # a just-stopped owner. SQLite already waits for 10 seconds
                    # per connection; retry only this transient lock condition,
                    # with a small bounded backoff and no database mutation or
                    # lock-file deletion outside SQLite itself.
                    time.sleep(_INITIALIZE_LOCK_RETRY_SECONDS * (attempt + 1))
        elif not self.db_path.is_file():
            # A read-only candidate must never turn a missing history database
            # into a new, silently empty persona.  It also must not create the
            # parent directory as a side effect of configuration loading.
            raise FileNotFoundError(
                f"read-only SQLite memory database does not exist: {self.db_path}"
            )
        else:
            live_sidecars = [
                path
                for path in (
                    Path(f"{self.db_path}-wal"),
                    Path(f"{self.db_path}-shm"),
                    Path(f"{self.db_path}-journal"),
                )
                if path.exists()
            ]
            if live_sidecars:
                # immutable=1 is what guarantees zero filesystem writes, but it
                # intentionally ignores WAL/journal state.  Reject a live or
                # uncheckpointed database instead of serving stale persona data.
                names = ", ".join(path.name for path in live_sidecars)
                raise RuntimeError(
                    "read-only SQLite memory requires a checkpointed database; "
                    f"sidecar files are present: {names}"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.write_enabled:
            connection = sqlite3.connect(self.db_path, timeout=10.0)
        else:
            # immutable=1 avoids journal/WAL/-shm creation and filesystem locks.
            # The candidate session owns a frozen snapshot: another process must
            # not update this database while it is open read-only.
            uri = self.db_path.resolve().as_uri() + "?mode=ro&immutable=1"
            connection = sqlite3.connect(uri, timeout=10.0, uri=True)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA temp_store=MEMORY")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            self.sqlite_vec_version = str(connection.execute("SELECT vec_version()").fetchone()[0])
        except Exception:
            self.sqlite_vec_version = None
        try:
            # sqlite3.Connection.__exit__ commits or rolls back the transaction,
            # while the outer finally closes the native SQLite handle.  Using
            # only ``with connection`` does not close the handle and leaks one
            # file descriptor per memory operation during long autonomous runs.
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversation_turns_session_created
                    ON conversation_turns(session_id, created_at);

                CREATE TABLE IF NOT EXISTS profile_facts (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    source_turn_ids TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    supersedes_id TEXT
                );

                CREATE TABLE IF NOT EXISTS episodic_memories (
                    id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    occurred_at TEXT,
                    emotional_salience REAL NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source_turn_ids TEXT NOT NULL,
                    last_accessed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS relationship_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    familiarity REAL NOT NULL,
                    trust REAL NOT NULL,
                    rapport REAL NOT NULL,
                    boundary_confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dialogue_state (
                    session_id TEXT PRIMARY KEY,
                    mood_valence REAL NOT NULL,
                    mood_arousal REAL NOT NULL,
                    energy REAL NOT NULL,
                    topic_stack_json TEXT NOT NULL,
                    style_hint_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_audit (
                    id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    approval_state TEXT NOT NULL,
                    result_summary TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_vector_meta (
                    rowid INTEGER PRIMARY KEY,
                    item_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    id UNINDEXED,
                    kind UNINDEXED,
                    content,
                    source UNINDEXED,
                    tokenize='trigram'
                );

                CREATE TABLE IF NOT EXISTS raw_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_actor TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    consolidated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS raw_events_pending
                    ON raw_events(consolidated_at, created_at);

                CREATE TABLE IF NOT EXISTS commitments (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    owner_actor TEXT NOT NULL,
                    status TEXT NOT NULL,
                    due_at TEXT,
                    source_event_ids TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source_event_ids TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    id TEXT PRIMARY KEY,
                    memory_kind TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    deleted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cognitive_state TEXT NOT NULL,
                    drives_json TEXT NOT NULL,
                    active_goal TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    last_activity_at REAL NOT NULL,
                    last_state_change_at REAL NOT NULL,
                    sleep_started_at REAL,
                    last_sleep_at REAL,
                    last_deep_sleep_date TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sleep_runs (
                    id TEXT PRIMARY KEY,
                    trigger_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    events_scanned INTEGER NOT NULL DEFAULT 0,
                    episodes_created INTEGER NOT NULL DEFAULT 0,
                    events_consolidated INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS consolidation_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            for table, column, declaration in (
                ("profile_facts", "source_type", "TEXT NOT NULL DEFAULT 'legacy_import'"),
                ("profile_facts", "source_actor", "TEXT NOT NULL DEFAULT ''"),
                ("profile_facts", "retrieval_weight", "REAL NOT NULL DEFAULT 1.0"),
                ("profile_facts", "created_at", "TEXT"),
                ("profile_facts", "last_confirmed_at", "TEXT"),
                ("profile_facts", "privacy_scope", "TEXT NOT NULL DEFAULT 'private'"),
                ("profile_facts", "superseded_by", "TEXT"),
                ("profile_facts", "provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("episodic_memories", "source_type", "TEXT NOT NULL DEFAULT 'legacy_import'"),
                ("episodic_memories", "source_actor", "TEXT NOT NULL DEFAULT ''"),
                ("episodic_memories", "retrieval_weight", "REAL NOT NULL DEFAULT 1.0"),
                ("episodic_memories", "created_at", "TEXT"),
                ("episodic_memories", "last_confirmed_at", "TEXT"),
                ("episodic_memories", "privacy_scope", "TEXT NOT NULL DEFAULT 'private'"),
                ("episodic_memories", "superseded_by", "TEXT"),
                ("episodic_memories", "provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("dialogue_state", "style_hint_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                _ensure_column(connection, table, column, declaration)
            if self.sqlite_vec_version:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(embedding float[384])"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO relationship_state
                    (id, familiarity, trust, rapport, boundary_confidence, updated_at)
                VALUES (1, 0.0, 0.5, 0.3, 1.0, ?)
                """,
                (_now(),),
            )
            now_epoch = datetime.now().timestamp()
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_state
                    (id, cognitive_state, drives_json, active_goal, focus,
                     last_activity_at, last_state_change_at, sleep_started_at,
                     last_sleep_at, last_deep_sleep_date, updated_at)
                VALUES (1, 'AWAKE', ?, '世界を理解し、無理なく関係を育てる', '',
                        ?, ?, NULL, NULL, '', ?)
                """,
                (
                    json.dumps(
                        {
                            "social_need": 0.45,
                            "curiosity": 0.55,
                            "fatigue": 0.2,
                            "comfort": 0.55,
                            "caution": 0.35,
                            "loneliness": 0.25,
                            "novelty_need": 0.5,
                            "conversation_satisfaction": 0.5,
                            "unfinished_business": 0.0,
                            "confidence": 0.55,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now_epoch,
                    now_epoch,
                    now_epoch,
                ),
            )

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            vector_rows = 0
            if self.sqlite_vec_version:
                vector_rows = int(connection.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0])
            return {
                "conversation_turns": int(connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]),
                "profile_facts": int(connection.execute("SELECT COUNT(*) FROM profile_facts").fetchone()[0]),
                "episodic_memories": int(connection.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]),
                "fts_rows": int(connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]),
                "sqlite_vec": bool(self.sqlite_vec_version),
                "sqlite_vec_version": self.sqlite_vec_version,
                "vector_rows": vector_rows,
                "raw_events": int(connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]),
                "pending_raw_events": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM raw_events WHERE consolidated_at IS NULL"
                    ).fetchone()[0]
                ),
                "commitments": int(connection.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]),
                "hypotheses": int(connection.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]),
                "sleep_runs": int(connection.execute("SELECT COUNT(*) FROM sleep_runs").fetchone()[0]),
            }

    def upsert_embedding(
        self,
        item_id: str,
        embedding: list[float],
        *,
        kind: str,
        content: str,
        source: str,
    ) -> int:
        if not self.sqlite_vec_version:
            raise RuntimeError("sqlite-vec is not available")
        if len(embedding) != 384:
            raise ValueError("embedding must contain exactly 384 float values")
        from sqlite_vec import serialize_float32

        with self._connect() as connection:
            row = connection.execute(
                "SELECT rowid FROM memory_vector_meta WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_vector_meta(item_id, kind, content, source, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item_id, kind, content, source, _now()),
                )
                rowid = int(cursor.lastrowid)
            else:
                rowid = int(row["rowid"])
                connection.execute(
                    """
                    UPDATE memory_vector_meta
                    SET kind = ?, content = ?, source = ?, updated_at = ?
                    WHERE rowid = ?
                    """,
                    (kind, content, source, _now(), rowid),
                )
                connection.execute("DELETE FROM memory_vectors WHERE rowid = ?", (rowid,))
            connection.execute(
                "INSERT INTO memory_vectors(rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )
        return rowid

    def vector_search(self, embedding: list[float], limit: int = 8) -> list[dict[str, Any]]:
        if not self.sqlite_vec_version:
            return []
        if len(embedding) != 384:
            raise ValueError("embedding must contain exactly 384 float values")
        from sqlite_vec import serialize_float32

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT v.rowid, v.distance, m.item_id, m.kind, m.content, m.source
                FROM memory_vectors AS v
                JOIN memory_vector_meta AS m ON m.rowid = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (serialize_float32(embedding), max(1, min(int(limit), 30))),
            ).fetchall()
        return [
            {
                "item_id": row["item_id"],
                "kind": row["kind"],
                "content": row["content"],
                "source": row["source"],
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def embedding_items(self, pending_only: bool = True) -> list[dict[str, str]]:
        """Return durable-memory rows eligible for offline embedding maintenance."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id AS item_id,
                       'profile_fact' AS kind,
                       value AS content,
                       'profile_facts:' || key AS source
                FROM profile_facts
                UNION ALL
                SELECT id AS item_id,
                       'episodic_memory' AS kind,
                       summary AS content,
                       'episodic_memories' AS source
                FROM episodic_memories
                ORDER BY kind, item_id
                """
            ).fetchall()
            if not pending_only:
                return [dict(row) for row in rows]

            meta_rows = connection.execute(
                "SELECT rowid, item_id, kind, content, source FROM memory_vector_meta"
            ).fetchall()
            meta = {str(row["item_id"]): row for row in meta_rows}
            vector_rowids: set[int] = set()
            if self.sqlite_vec_version:
                vector_rowids = {
                    int(row["rowid"])
                    for row in connection.execute("SELECT rowid FROM memory_vectors").fetchall()
                }

        pending: list[dict[str, str]] = []
        for raw_row in rows:
            row = dict(raw_row)
            existing = meta.get(str(row["item_id"]))
            if (
                existing is None
                or int(existing["rowid"]) not in vector_rowids
                or str(existing["kind"]) != str(row["kind"])
                or str(existing["content"]) != str(row["content"])
                or str(existing["source"]) != str(row["source"])
            ):
                pending.append(row)
        return pending

    @staticmethod
    def session_key(guild_id: str, channel_id: str, session_id: str | None = None) -> str:
        if session_id:
            return str(session_id)
        return f"g:{guild_id}|c:{channel_id}"

    def _fts_query(self, query: str) -> str:
        terms = [term.replace('"', "") for term in _QUERY_TERM_RE.findall(query)]
        return " OR ".join(f'"{term}"' for term in terms[:8])

    def retrieve(
        self,
        session_key: str,
        query: str,
        max_chars: int = 1800,
        query_embedding: list[float] | None = None,
        max_results: int = 6,
    ) -> str:
        configured_max_chars = int(os.environ.get("LUMINA_NEXT_MEMORY_MAX_CHARS", max_chars))
        max_chars = max(160, min(int(max_chars), configured_max_chars))
        recent_messages = max(
            0,
            min(8, int(os.environ.get("LUMINA_NEXT_RECENT_MESSAGES", "8"))),
        )
        recent_message_chars = max(
            40,
            min(320, int(os.environ.get("LUMINA_NEXT_RECENT_MESSAGE_CHARS", "240"))),
        )
        lines: list[str] = []
        vector_rows = self.vector_search(query_embedding, limit=30) if query_embedding else []
        fts_rows: list[sqlite3.Row] = []
        quality: dict[str, tuple[float, float]] = {}
        with self._connect() as connection:
            recent = connection.execute(
                """
                SELECT role, content FROM conversation_turns
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (session_key, recent_messages),
            ).fetchall()
            for row in reversed(recent):
                role = "ユーザー" if row["role"] == "user" else "Lumina"
                content = str(row["content"]).strip()[:recent_message_chars]
                lines.append(f"recent:{role}: {content}")

            fts_query = self._fts_query(query)
            if fts_query:
                fts_rows = connection.execute(
                    """
                    SELECT id, kind, content, source FROM memory_fts
                    WHERE memory_fts MATCH ?
                    ORDER BY rank LIMIT 30
                    """,
                    (fts_query,),
                ).fetchall()

            signal_rows = connection.execute(
                """
                SELECT id, importance, confidence, retrieval_weight, source_type FROM profile_facts
                UNION ALL
                SELECT id, importance, confidence, retrieval_weight, source_type FROM episodic_memories
                """
            ).fetchall()
            quality = {
                str(row["id"]): (
                    float(row["importance"]),
                    float(row["confidence"]),
                    float(row["retrieval_weight"]),
                    str(row["source_type"]),
                )
                for row in signal_rows
            }

        candidates: dict[str, dict[str, Any]] = {}

        def add_candidate(row: Any, rank: int, source_kind: str) -> None:
            item_id = str(row["item_id"] if source_kind == "vector" else row["id"])
            current = candidates.setdefault(
                item_id,
                {
                    "item_id": item_id,
                    "content": str(row["content"]),
                    "score": 0.0,
                    "distance": float(row.get("distance", 99.0)) if isinstance(row, dict) else 99.0,
                },
            )
            current["score"] += 1.0 / (60.0 + float(rank))
            if source_kind == "vector":
                current["distance"] = float(row["distance"])

        for rank, row in enumerate(fts_rows, start=1):
            add_candidate(row, rank, "fts")
        for rank, row in enumerate(vector_rows, start=1):
            add_candidate(row, rank, "vector")

        for item_id, candidate in candidates.items():
            importance, confidence, retrieval_weight, source_type = quality.get(
                item_id, (0.5, 0.5, 1.0, "unknown")
            )
            candidate["score"] += (
                importance * 0.0015
                + confidence * 0.001
                + retrieval_weight * 0.001
            )
            candidate["confidence"] = confidence
            candidate["source_type"] = source_type

        memory_seen: set[str] = set()
        memory_count = 0
        for candidate in sorted(
            candidates.values(),
            key=lambda item: (-float(item["score"]), float(item["distance"]), str(item["item_id"])),
        ):
            content = str(candidate["content"]).strip()
            content_key = content.lower()
            if not content or content_key in memory_seen:
                continue
            memory_seen.add(content_key)
            lines.append(
                "memory:"
                f"[source={candidate.get('source_type', 'unknown')};"
                f"confidence={float(candidate.get('confidence', 0.5)):.2f}] {content}"
            )
            memory_count += 1
            if memory_count >= max(1, int(max_results)):
                break

        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            key = line.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(line)
        return "\n".join(deduped)[:max_chars]

    def state_policy(self, session_key: str) -> str:
        from .speaking_style_growth import normalize_style_state, style_policy_clause

        with self._connect() as connection:
            relationship = connection.execute(
                "SELECT familiarity, trust, rapport FROM relationship_state WHERE id = 1"
            ).fetchone()
            dialogue = connection.execute(
                "SELECT mood_valence, mood_arousal, energy, topic_stack_json, style_hint_json FROM dialogue_state WHERE session_id = ?",
                (session_key,),
            ).fetchone()
        if relationship is None:
            base = "口調は固定しない。距離を詰めすぎず、質問を連続させず、断定しすぎない。"
        else:
            familiarity = float(relationship["familiarity"])
            closeness = "少し近い距離感" if familiarity >= 0.2 else "控えめな距離感"
            if dialogue is None:
                base = f"口調は固定しない。{closeness}。質問を連続させず、断定しすぎない。"
            else:
                energy = float(dialogue["energy"])
                pace = "静かな間を大切に" if energy < 0.4 else "自然な間で"
                base = f"口調は固定しない。{closeness}、{pace}。質問を連続させず、断定しすぎない。"
        style_raw = {}
        if dialogue is not None:
            try:
                style_raw = json.loads(dialogue["style_hint_json"] or "{}")
            except (TypeError, ValueError, KeyError):
                style_raw = {}
        style_clause = style_policy_clause(normalize_style_state(style_raw))
        if style_clause:
            # Grown style takes priority over the soft default clause.
            return f"{style_clause} {base}"
        return base

    def _update_state(
        self,
        connection: sqlite3.Connection,
        session_key: str,
        user_text: str,
        *,
        previous_assistant: str = "",
    ) -> None:
        from .speaking_style_growth import apply_style_reaction, empty_style_state

        now = _now()
        connection.execute(
            """
            UPDATE relationship_state
            SET familiarity = MIN(1.0, familiarity + 0.002),
                rapport = MIN(1.0, rapport + 0.001),
                updated_at = ?
            WHERE id = 1
            """,
            (now,),
        )
        current = connection.execute(
            "SELECT mood_valence, mood_arousal, energy, topic_stack_json, style_hint_json FROM dialogue_state WHERE session_id = ?",
            (session_key,),
        ).fetchone()
        topics = []
        style_state = empty_style_state()
        if current is not None:
            try:
                topics = json.loads(current["topic_stack_json"])
            except (TypeError, ValueError):
                topics = []
            try:
                style_state = json.loads(current["style_hint_json"] or "{}")
            except (TypeError, ValueError, KeyError):
                style_state = empty_style_state()
        topic = " ".join(user_text.strip().split())[:80]
        if topic:
            topics = [topic, *[item for item in topics if item != topic]][:5]
        energy = 0.7 if current is None else max(0.25, float(current["energy"]) - 0.002)
        if previous_assistant or user_text.strip():
            style_state = apply_style_reaction(
                style_state,
                user_text,
                previous_reply=previous_assistant,
                updated_at=now,
            )
        connection.execute(
            """
            INSERT INTO dialogue_state
                (session_id, mood_valence, mood_arousal, energy, topic_stack_json, style_hint_json, updated_at)
            VALUES (?, 0.0, 0.2, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                energy=excluded.energy,
                topic_stack_json=excluded.topic_stack_json,
                style_hint_json=excluded.style_hint_json,
                updated_at=excluded.updated_at
            """,
            (
                session_key,
                energy,
                json.dumps(topics, ensure_ascii=False),
                json.dumps(style_state, ensure_ascii=False),
                now,
            ),
        )

    def record_turn(self, session_key: str, user_text: str, answer_text: str) -> bool:
        if not self.write_enabled:
            return False
        now = _now()
        with self._connect() as connection:
            previous = connection.execute(
                """
                SELECT content FROM conversation_turns
                WHERE session_id = ? AND role = 'assistant'
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_key,),
            ).fetchone()
            previous_assistant = str(previous["content"]) if previous is not None else ""
            for role, content in (("user", user_text), ("assistant", answer_text)):
                turn_id = f"turn-{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO conversation_turns(id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (turn_id, session_key, role, content, now),
                )
                source_type = "human_statement" if role == "user" else "lumina_generated"
                source_actor = session_key if role == "user" else "lumina"
                connection.execute(
                    """
                    INSERT INTO raw_events
                        (id, session_id, event_type, content, source_type,
                         source_actor, metadata_json, importance, created_at,
                         consolidated_at)
                    VALUES (?, ?, 'conversation_turn', ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        f"event-{turn_id}",
                        session_key,
                        content,
                        source_type,
                        source_actor,
                        json.dumps({"role": role}, ensure_ascii=False),
                        0.6 if role == "user" else 0.25,
                        now,
                    ),
                )
            self._update_state(
                connection,
                session_key,
                user_text,
                previous_assistant=previous_assistant,
            )
        return True

    def record_event(
        self,
        *,
        event_type: str,
        content: str,
        source_type: str,
        source_actor: str = "",
        session_id: str = "system",
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if not self.write_enabled:
            return None
        event_id = f"event-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO raw_events
                    (id, session_id, event_type, content, source_type,
                     source_actor, metadata_json, importance, created_at,
                     consolidated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    event_id,
                    str(session_id)[:300],
                    str(event_type)[:100],
                    str(content)[:8000],
                    str(source_type)[:100],
                    str(source_actor)[:300],
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))[:8000],
                    max(0.0, min(float(importance), 1.0)),
                    _now(),
                ),
            )
        return event_id

    def pending_event_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM raw_events WHERE consolidated_at IS NULL"
                ).fetchone()[0]
            )

    def cognitive_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agent_state WHERE id = 1").fetchone()
        if row is None:
            return {}
        result = dict(row)
        try:
            result["drives"] = json.loads(result.pop("drives_json"))
        except (TypeError, ValueError):
            result["drives"] = {}
            result.pop("drives_json", None)
        return result

    def save_cognitive_state(self, state: dict[str, Any]) -> bool:
        if not self.write_enabled:
            return False
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_state
                SET cognitive_state = ?, drives_json = ?, active_goal = ?,
                    focus = ?, last_activity_at = ?, last_state_change_at = ?,
                    sleep_started_at = ?, last_sleep_at = ?,
                    last_deep_sleep_date = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    str(state.get("cognitive_state") or "AWAKE")[:80],
                    json.dumps(state.get("drives") or {}, ensure_ascii=False, separators=(",", ":")),
                    str(state.get("active_goal") or "")[:500],
                    str(state.get("focus") or "")[:500],
                    float(state.get("last_activity_at") or datetime.now().timestamp()),
                    float(state.get("last_state_change_at") or datetime.now().timestamp()),
                    state.get("sleep_started_at"),
                    state.get("last_sleep_at"),
                    str(state.get("last_deep_sleep_date") or "")[:20],
                    float(state.get("updated_at") or datetime.now().timestamp()),
                ),
            )
        return True

    def consolidate_memory(
        self,
        *,
        trigger: str,
        mode: str,
        max_events: int = 400,
    ) -> dict[str, Any]:
        if not self.write_enabled:
            return {"ok": False, "status": "read_only"}
        run_id = f"sleep-{uuid.uuid4().hex}"
        started_epoch = datetime.now().timestamp()
        started_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE consolidation_runs
                SET status = 'abandoned', completed_at = ?,
                    detail_json = '{"reason":"stale_lock"}'
                WHERE status = 'running' AND started_at < ?
                """,
                (started_epoch, started_epoch - 3600.0),
            )
            active = connection.execute(
                "SELECT id FROM consolidation_runs WHERE status = 'running' LIMIT 1"
            ).fetchone()
            if active is not None:
                return {"ok": False, "status": "busy", "run_id": str(active["id"])}
            connection.execute(
                "INSERT INTO consolidation_runs(id, status, started_at) VALUES (?, 'running', ?)",
                (run_id, started_epoch),
            )
            connection.execute(
                """
                INSERT INTO sleep_runs
                    (id, trigger_name, mode, status, started_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, str(trigger)[:120], str(mode)[:40], started_at),
            )

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT id, session_id, event_type, content, source_type,
                           source_actor, metadata_json, importance, created_at
                    FROM raw_events
                    WHERE consolidated_at IS NULL
                    ORDER BY created_at, id
                    LIMIT ?
                    """,
                    (max(1, min(int(max_events), 2000)),),
                ).fetchall()
                groups: dict[str, list[sqlite3.Row]] = {}
                for row in rows:
                    day = str(row["created_at"])[:10]
                    key = f"{row['session_id']}|{day}"
                    groups.setdefault(key, []).append(row)

                episodes_created = 0
                for group_key, group_rows in groups.items():
                    for offset in range(0, len(group_rows), 12):
                        chunk = group_rows[offset : offset + 12]
                        grounded = [
                            row
                            for row in chunk
                            if row["source_type"]
                            in {"human_statement", "other_ai_statement", "world_observation"}
                        ]
                        if not grounded:
                            continue
                        snippets = []
                        for row in grounded[:4]:
                            compact = " ".join(str(row["content"] or "").split())[:160]
                            if compact:
                                snippets.append(compact)
                        if not snippets:
                            continue
                        source_ids = [str(row["id"]) for row in chunk]
                        actors = sorted(
                            {
                                str(row["source_actor"])
                                for row in grounded
                                if str(row["source_actor"])
                            }
                        )
                        episode_id = _stable_id("episode", "|".join(source_ids))
                        summary = "会話・行動エピソード: " + " / ".join(snippets)
                        confidence = min(
                            float(row["importance"]) for row in grounded
                        )
                        importance = min(
                            0.9,
                            max(float(row["importance"]) for row in grounded)
                            + min(0.2, len(grounded) * 0.02),
                        )
                        cursor = connection.execute(
                            """
                            INSERT OR IGNORE INTO episodic_memories
                                (id, summary, occurred_at, emotional_salience,
                                 importance, confidence, source_turn_ids,
                                 last_accessed_at, source_type, source_actor,
                                 retrieval_weight, created_at, last_confirmed_at,
                                 privacy_scope, superseded_by, provenance_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, NULL,
                                    'consolidated_episode', ?, 1.0, ?, ?,
                                    'private', NULL, ?)
                            """,
                            (
                                episode_id,
                                summary[:1200],
                                str(chunk[-1]["created_at"]),
                                min(1.0, importance * 0.6),
                                importance,
                                max(0.35, min(confidence, 0.85)),
                                json.dumps(source_ids, ensure_ascii=False),
                                ",".join(actors)[:500],
                                started_at,
                                started_at,
                                json.dumps(
                                    {
                                        "source_event_ids": source_ids,
                                        "source_types": sorted(
                                            {str(row["source_type"]) for row in grounded}
                                        ),
                                        "group": group_key,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ),
                        )
                        if cursor.rowcount:
                            episodes_created += 1
                            self._upsert_fts(
                                connection,
                                episode_id,
                                "episodic_memory",
                                summary[:1200],
                                "sleep_consolidation",
                            )

                event_ids = [str(row["id"]) for row in rows]
                if event_ids:
                    placeholders = ",".join("?" for _ in event_ids)
                    connection.execute(
                        f"UPDATE raw_events SET consolidated_at = ? WHERE id IN ({placeholders})",
                        (started_at, *event_ids),
                    )
                decay = 0.985 if mode == "deep" else 0.997
                connection.execute(
                    """
                    UPDATE episodic_memories
                    SET retrieval_weight = MAX(0.15, retrieval_weight * ?)
                    WHERE COALESCE(created_at, '') < ?
                    """,
                    (decay, started_at),
                )
                connection.execute(
                    """
                    UPDATE profile_facts
                    SET retrieval_weight = MAX(0.25, retrieval_weight * ?)
                    WHERE pinned = 0 AND COALESCE(created_at, '') < ?
                    """,
                    (0.998 if mode == "deep" else 0.9995, started_at),
                )
                completed_at = _now()
                detail = {
                    "events_scanned": len(rows),
                    "events_consolidated": len(event_ids),
                    "episodes_created": episodes_created,
                    "weight_decay": decay,
                    "weight_training": False,
                }
                connection.execute(
                    """
                    UPDATE sleep_runs
                    SET status = 'completed', completed_at = ?,
                        events_scanned = ?, episodes_created = ?,
                        events_consolidated = ?, detail_json = ?
                    WHERE id = ?
                    """,
                    (
                        completed_at,
                        len(rows),
                        episodes_created,
                        len(event_ids),
                        json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                        run_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE consolidation_runs
                    SET status = 'completed', completed_at = ?, detail_json = ?
                    WHERE id = ?
                    """,
                    (
                        datetime.now().timestamp(),
                        json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                        run_id,
                    ),
                )
            return {"ok": True, "status": "completed", "run_id": run_id, **detail}
        except Exception as exc:
            failure = {"error": type(exc).__name__, "detail": str(exc)[:500]}
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE sleep_runs
                    SET status = 'failed', completed_at = ?, detail_json = ?
                    WHERE id = ?
                    """,
                    (_now(), json.dumps(failure, ensure_ascii=False), run_id),
                )
                connection.execute(
                    """
                    UPDATE consolidation_runs
                    SET status = 'failed', completed_at = ?, detail_json = ?
                    WHERE id = ?
                    """,
                    (datetime.now().timestamp(), json.dumps(failure, ensure_ascii=False), run_id),
                )
            return {"ok": False, "status": "failed", "run_id": run_id, **failure}

    def sleep_status(self, limit: int = 10) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, trigger_name, mode, status, started_at, completed_at,
                       events_scanned, episodes_created, events_consolidated,
                       detail_json
                FROM sleep_runs ORDER BY started_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        runs = []
        for raw in rows:
            row = dict(raw)
            try:
                row["detail"] = json.loads(row.pop("detail_json"))
            except (TypeError, ValueError):
                row["detail"] = {}
                row.pop("detail_json", None)
            runs.append(row)
        return {
            "pending_events": self.pending_event_count(),
            "recent_runs": runs,
        }

    def wake_brief(self, limit: int = 5) -> dict[str, Any]:
        with self._connect() as connection:
            episodes = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, summary, occurred_at, importance, confidence,
                           source_type, source_actor
                    FROM episodic_memories
                    WHERE superseded_by IS NULL
                    ORDER BY importance * retrieval_weight DESC,
                             occurred_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 12)),),
                ).fetchall()
            ]
            commitments = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, description, owner_actor, status, due_at,
                           confidence, importance
                    FROM commitments
                    WHERE status IN ('open', 'in_progress')
                    ORDER BY importance DESC, updated_at DESC LIMIT ?
                    """,
                    (max(1, min(int(limit), 12)),),
                ).fetchall()
            ]
        return {
            "cognition": self.cognitive_state(),
            "open_commitments": commitments,
            "important_episodes": episodes,
        }

    def management_snapshot(self, limit: int = 100) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            profile_facts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, key, value, confidence, importance, sensitivity,
                           valid_from, valid_until, pinned, supersedes_id
                    FROM profile_facts
                    ORDER BY pinned DESC, importance DESC, key, id
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
            ]
            episodic_memories = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, summary, occurred_at, emotional_salience,
                           importance, confidence, last_accessed_at
                    FROM episodic_memories
                    ORDER BY importance DESC, occurred_at DESC, id
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
            ]
            relationship_row = connection.execute(
                """
                SELECT familiarity, trust, rapport, boundary_confidence, updated_at
                FROM relationship_state WHERE id = 1
                """
            ).fetchone()
            dialogue_rows = connection.execute(
                """
                SELECT session_id, mood_valence, mood_arousal, energy,
                       topic_stack_json, updated_at
                FROM dialogue_state
                ORDER BY updated_at DESC LIMIT 20
                """
            ).fetchall()
            recent_turns = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, session_id, role, content, created_at
                    FROM conversation_turns
                    ORDER BY created_at DESC LIMIT 20
                    """
                ).fetchall()
            ]

        dialogue_states: list[dict[str, Any]] = []
        for raw_row in dialogue_rows:
            row = dict(raw_row)
            try:
                row["topic_stack"] = json.loads(str(row.pop("topic_stack_json")))
            except (TypeError, ValueError):
                row["topic_stack"] = []
                row.pop("topic_stack_json", None)
            dialogue_states.append(row)
        for fact in profile_facts:
            fact["pinned"] = bool(fact["pinned"])
        return {
            "stats": self.stats(),
            "write_enabled": self.write_enabled,
            "profile_facts": profile_facts,
            "episodic_memories": episodic_memories,
            "relationship": dict(relationship_row) if relationship_row is not None else {},
            "dialogue_states": dialogue_states,
            "recent_turns": recent_turns,
        }

    def set_profile_fact_pinned(self, item_id: str, pinned: bool) -> bool:
        if not self.write_enabled:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE profile_facts SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, str(item_id)),
            )
        return bool(cursor.rowcount)

    def forget(self, kind: str, item_id: str) -> bool:
        if not self.write_enabled:
            return False
        table = {
            "profile_fact": "profile_facts",
            "episodic_memory": "episodic_memories",
        }.get(str(kind))
        if table is None:
            raise ValueError(f"unsupported memory kind: {kind}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT rowid FROM memory_vector_meta WHERE item_id = ?",
                (str(item_id),),
            ).fetchone()
            if row is not None and self.sqlite_vec_version:
                connection.execute("DELETE FROM memory_vectors WHERE rowid = ?", (int(row["rowid"]),))
            connection.execute("DELETE FROM memory_vector_meta WHERE item_id = ?", (str(item_id),))
            connection.execute("DELETE FROM memory_fts WHERE id = ?", (str(item_id),))
            cursor = connection.execute(f"DELETE FROM {table} WHERE id = ?", (str(item_id),))
        return bool(cursor.rowcount)

    def _upsert_fts(self, connection: sqlite3.Connection, item_id: str, kind: str, content: str, source: str) -> None:
        connection.execute("DELETE FROM memory_fts WHERE id = ?", (item_id,))
        connection.execute(
            "INSERT INTO memory_fts(id, kind, content, source) VALUES (?, ?, ?, ?)",
            (item_id, kind, content, source),
        )

    def import_legacy(self, legacy_dir: Path) -> ImportSummary:
        legacy_dir = Path(legacy_dir)
        fact_count = 0
        turn_count = 0
        try:
            long_term = json.loads((legacy_dir / "long_term.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            long_term = {}
        try:
            sessions = json.loads((legacy_dir / "sessions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sessions = {}

        facts: list[tuple[str, str]] = []
        if isinstance(long_term, dict):
            for key in ("project_facts", "facts", "rules"):
                values = long_term.get(key)
                if isinstance(values, list):
                    for value in values:
                        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if text.strip():
                            facts.append((key, text.strip()))
            for key in ("user_preferences", "preferences"):
                values = long_term.get(key)
                if isinstance(values, dict):
                    for item_key, item_value in values.items():
                        facts.append((key, f"{item_key}={item_value}"))

        with self._connect() as connection:
            for kind, text in facts:
                item_id = _stable_id("fact", f"{kind}:{text}")
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO profile_facts
                        (id, key, value, confidence, importance, sensitivity, source_turn_ids, pinned)
                    VALUES (?, ?, ?, 0.7, 0.5, 'normal', '[]', 0)
                    """,
                    (item_id, kind, text),
                )
                if cursor.rowcount:
                    fact_count += 1
                self._upsert_fts(connection, item_id, kind, text, "legacy:long_term.json")

            all_sessions = sessions.get("sessions", {}) if isinstance(sessions, dict) else {}
            if isinstance(all_sessions, dict):
                for session_key, entry in all_sessions.items():
                    history = entry.get("history", []) if isinstance(entry, dict) else []
                    if not isinstance(history, list):
                        continue
                    for index, item in enumerate(history):
                        if not isinstance(item, dict):
                            continue
                        role = str(item.get("role") or "")
                        content = str(item.get("text") or "").strip()
                        if role not in {"user", "assistant"} or not content:
                            continue
                        created_at = str(item.get("ts") or _now())
                        turn_id = _stable_id("legacy-turn", f"{session_key}:{index}:{role}:{content}")
                        cursor = connection.execute(
                            """
                            INSERT OR IGNORE INTO conversation_turns
                                (id, session_id, role, content, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (turn_id, str(session_key), role, content, created_at),
                        )
                        if cursor.rowcount:
                            turn_count += 1
        return ImportSummary(profile_facts=fact_count, conversation_turns=turn_count)
