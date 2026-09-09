from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from .orchestrator import LuminaOrchestrator
from .providers.llm import ProviderError


NOTIFICATION_ID_PATTERN = re.compile(r"^notif-[A-Za-z0-9-]{8,160}$")
GRID_NODE_PATTERN = re.compile(
    r"^(?:([a-z0-9][a-z0-9-]*):)?(\d+)-(\d+)$"
)
DEFAULT_API_BASE = "https://api.karakuri.world"
DEFAULT_AUTONOMY_GOAL = (
    "状況に応じて柔軟に考え、からくりワールドで自然に過ごす。"
    "同じ行動のループや直前の癖に引きずられすぎず、"
    "お金が足りないときだけ働き、余裕があるときは会話や探索を楽しむ"
)
WEBHOOK_MAX_BYTES = 64 * 1024
SOCIAL_COMMANDS = frozenset(
    {
        "conversation_start",
        "conversation_accept",
        "conversation_reject",
        "conversation_join",
        "conversation_stay",
        "conversation_leave",
        "conversation_speak",
        "conversation_end",
    }
)
REACTIVE_SOCIAL_COMMANDS = frozenset(
    {
        "conversation_speak",
        "conversation_accept",
        "conversation_join",
        "conversation_stay",
        "conversation_leave",
        "conversation_end",
        "conversation_reject",
    }
)
EARN_ACTION_HINT = re.compile(
    r"(?:^work-)|バイト|仕事|勤務|労働|手伝い|稼",
    re.IGNORECASE,
)


class KarakuriWorldError(RuntimeError):
    pass


class KarakuriWorldAPIError(KarakuriWorldError):
    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(f"Karakuri World API returned HTTP {status_code}")


@dataclass(frozen=True)
class KarakuriWorldConfig:
    runtime_root: Path
    api_base: str = DEFAULT_API_BASE
    api_key: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    ledger_path: Path = Path("karakuri_world.sqlite3")
    request_timeout_seconds: float = 20.0
    decision_max_tokens: int = 360
    autonomy_enabled: bool = True
    autonomy_goal: str = DEFAULT_AUTONOMY_GOAL
    autonomy_history_limit: int = 12

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    @property
    def webhook_ready(self) -> bool:
        return bool(self.api_key and self.webhook_secret)

    @classmethod
    def from_env(cls, runtime_root: Path) -> "KarakuriWorldConfig":
        root = runtime_root.resolve()
        configured_base = str(os.environ.get("KARAKURI_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        if configured_base != DEFAULT_API_BASE and os.environ.get("KARAKURI_ALLOW_CUSTOM_API_BASE") != "1":
            configured_base = DEFAULT_API_BASE
        ledger_path = Path(
            os.environ.get("KARAKURI_WORLD_LEDGER_PATH")
            or root / "data" / "lumina_next" / "karakuri_world.sqlite3"
        ).resolve()
        return cls(
            runtime_root=root,
            api_base=configured_base,
            api_key=str(os.environ.get("KARAKURI_API_KEY") or "").strip(),
            webhook_secret=str(os.environ.get("KARAKURI_WEBHOOK_SECRET") or "").strip(),
            ledger_path=ledger_path,
            request_timeout_seconds=float(os.environ.get("KARAKURI_REQUEST_TIMEOUT_SECONDS") or "20"),
            decision_max_tokens=int(os.environ.get("KARAKURI_DECISION_MAX_TOKENS") or "360"),
            autonomy_enabled=str(
                os.environ.get("KARAKURI_AUTONOMY_ENABLED") or "1"
            ).strip().lower() not in {"0", "false", "no", "off"},
            autonomy_goal=str(
                os.environ.get("KARAKURI_AUTONOMY_GOAL") or DEFAULT_AUTONOMY_GOAL
            ).strip()[:500],
            autonomy_history_limit=max(
                1,
                min(
                    int(os.environ.get("KARAKURI_AUTONOMY_HISTORY_LIMIT") or "12"),
                    20,
                ),
            ),
        )


class KarakuriWorldClient:
    def __init__(self, config: KarakuriWorldConfig) -> None:
        self.config = config

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise KarakuriWorldError("KARAKURI_API_KEY is not configured")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(self.config.request_timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    self.config.api_base + path,
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise KarakuriWorldError(f"Karakuri World request failed: {type(exc).__name__}") from exc

        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = {"detail": response.text[:500]}
        if response.status_code < 200 or response.status_code >= 300:
            raise KarakuriWorldAPIError(response.status_code, body)
        if body is None:
            return {"ok": True}
        if not isinstance(body, dict):
            raise KarakuriWorldError("Karakuri World API returned non-object JSON")
        return body

    async def get_notification(self, notification_id: str) -> dict[str, Any]:
        _validate_notification_id(notification_id)
        return await self._request("GET", f"/agents/notifications/{notification_id}")

    async def command(
        self,
        notification_id: str,
        command: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_notification_id(notification_id)
        if not command.strip():
            raise KarakuriWorldError("command is empty")
        return await self._request(
            "POST",
            "/agents/command",
            {
                "notification_id": notification_id,
                "command": command,
                "params": params,
            },
        )

    async def login(self, node_id: str | None = None) -> dict[str, Any]:
        payload = {"node_id": node_id} if node_id else None
        return await self._request("POST", "/agents/login", payload)

    async def logout(self) -> dict[str, Any]:
        return await self._request("POST", "/agents/logout")


class KarakuriWorldLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            # ``with sqlite3.Connection`` commits but does not close the native
            # handle.  Close every ledger connection so repeated notification
            # checks cannot accumulate file descriptors over an overnight run.
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    request_id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    next_retry_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            delivery_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(deliveries)").fetchall()
            }
            if "attempt_count" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE deliveries ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1"
                )
            if "next_retry_at" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE deliveries ADD COLUMN next_retry_at REAL NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                UPDATE deliveries
                SET status = 'rejected', next_retry_at = 0
                WHERE status = 'failed'
                  AND json_extract(result_json, '$.error') = 'api_error'
                  AND CAST(json_extract(result_json, '$.status_code') AS INTEGER)
                      BETWEEN 400 AND 499
                  AND CAST(json_extract(result_json, '$.status_code') AS INTEGER)
                      NOT IN (408, 425, 429)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS world_visits (
                    node_id TEXT PRIMARY KEY,
                    visit_count INTEGER NOT NULL,
                    last_visited_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS world_action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    acted_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS world_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    observed_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_episodes (
                    notification_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    command TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    follow_up TEXT NOT NULL,
                    participants_json TEXT NOT NULL,
                    claims_json TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    model TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_relationships (
                    agent_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    interaction_count INTEGER NOT NULL,
                    affinity REAL NOT NULL,
                    last_topic TEXT NOT NULL,
                    follow_up TEXT NOT NULL,
                    last_interaction_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_claims (
                    source_agent_id TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    notification_id TEXT NOT NULL,
                    last_seen_at REAL NOT NULL,
                    PRIMARY KEY (source_agent_id, claim_text)
                )
                """
            )

    def claim(self, request_id: str, notification_id: str) -> bool:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO deliveries
                    (request_id, notification_id, status, received_at, updated_at,
                     result_json, attempt_count, next_retry_at)
                VALUES (?, ?, 'received', ?, ?, '{}', 1, 0)
                """,
                (request_id, notification_id, now, now),
            )
            if cursor.rowcount == 1:
                return True
            retry = connection.execute(
                """
                UPDATE deliveries
                SET request_id = ?, status = 'received', updated_at = ?,
                    result_json = '{}', attempt_count = attempt_count + 1,
                    next_retry_at = 0
                WHERE notification_id = ?
                  AND attempt_count < 3
                  AND (
                    (status = 'failed' AND next_retry_at <= ?)
                    OR (
                      status IN ('received', 'processing')
                      AND updated_at <= ?
                    )
                  )
                """,
                (request_id, now, notification_id, now, now - 180.0),
            )
            return retry.rowcount == 1

    def mark(self, notification_id: str, status: str, result: dict[str, Any]) -> None:
        safe_result = json.dumps(result, ensure_ascii=False, separators=(",", ":"))[:8000]
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM deliveries WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            attempt_count = int(row["attempt_count"]) if row is not None else 1
            retry_delay = min(300.0, 30.0 * (2 ** max(0, attempt_count - 1)))
            next_retry_at = now + retry_delay if status == "failed" else 0.0
            connection.execute(
                """
                UPDATE deliveries
                SET status = ?, updated_at = ?, result_json = ?, next_retry_at = ?
                WHERE notification_id = ?
                """,
                (status, now, safe_result, next_retry_at, notification_id),
            )

    def delivery_status(self, notification_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return str(row["status"]) if row is not None else ""

    def retry_info(self, notification_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, attempt_count, next_retry_at
                FROM deliveries WHERE notification_id = ?
                """,
                (notification_id,),
            ).fetchone()
        if row is None:
            return {"eligible": False, "attempt_count": 0, "retry_after_seconds": 0.0}
        status = str(row["status"])
        attempt_count = int(row["attempt_count"])
        retry_after = max(0.0, float(row["next_retry_at"]) - now)
        return {
            "eligible": status == "failed" and attempt_count < 3,
            "status": status,
            "attempt_count": attempt_count,
            "retry_after_seconds": retry_after,
        }

    def record_world_action(
        self,
        notification_id: str,
        command: str,
        params: dict[str, Any],
        notification: dict[str, Any],
        result: dict[str, Any] | None = None,
        outcome: str = "acted",
    ) -> None:
        target_node_id = _extract_target_node(params)
        if not target_node_id:
            target_node_id = _extract_current_node(notification)
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO world_action_log
                    (notification_id, command, target_node_id, outcome, acted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    _safe_text(command, 120),
                    _safe_text(target_node_id, 200),
                    _safe_text(outcome, 80),
                    now,
                ),
            )
            if command == "move" and target_node_id:
                connection.execute(
                    """
                    INSERT INTO world_visits (node_id, visit_count, last_visited_at)
                    VALUES (?, 1, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        visit_count = world_visits.visit_count + 1,
                        last_visited_at = excluded.last_visited_at
                    """,
                    (target_node_id, now),
                )
            if isinstance(result, dict) and (
                "data" in result or command.startswith("get_")
            ):
                observation = result.get("data", result)
                observation_json = json.dumps(
                    observation,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )[:20000]
                connection.execute(
                    """
                    INSERT INTO world_observations
                        (notification_id, command, data_json, observed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (notification_id, command, observation_json, now),
                )
            connection.execute(
                """
                DELETE FROM world_action_log
                WHERE id NOT IN (
                    SELECT id FROM world_action_log ORDER BY id DESC LIMIT 500
                )
                """
            )
            connection.execute(
                """
                DELETE FROM world_observations
                WHERE id NOT IN (
                    SELECT id FROM world_observations ORDER BY id DESC LIMIT 80
                )
                """
            )

    def exploration_context(self, limit: int = 30) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            visit_rows = connection.execute(
                """
                SELECT node_id, visit_count, last_visited_at
                FROM world_visits
                ORDER BY last_visited_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
            action_rows = connection.execute(
                """
                SELECT command, target_node_id, outcome, acted_at
                FROM world_action_log
                ORDER BY id DESC
                LIMIT 12
                """
            ).fetchall()
        return {
            "visits": [
                {
                    "node_id": row["node_id"],
                    "visit_count": int(row["visit_count"]),
                    "last_visited_at": float(row["last_visited_at"]),
                }
                for row in visit_rows
            ],
            "recent_route": [
                {
                    "command": row["command"],
                    "target_node_id": row["target_node_id"],
                    "outcome": row["outcome"],
                    "acted_at": float(row["acted_at"]),
                }
                for row in action_rows
            ],
        }

    def operational_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM deliveries GROUP BY status"
            ).fetchall()
            latest_success = connection.execute(
                "SELECT MAX(updated_at) AS value FROM deliveries WHERE status = 'acted'"
            ).fetchone()
            unique_nodes = int(
                connection.execute("SELECT COUNT(*) FROM world_visits").fetchone()[0]
            )
            observation_count = int(
                connection.execute("SELECT COUNT(*) FROM world_observations").fetchone()[0]
            )
        return {
            "delivery_status_counts": {
                str(row["status"]): int(row["count"]) for row in status_rows
            },
            "last_success_at": (
                float(latest_success["value"])
                if latest_success is not None and latest_success["value"] is not None
                else None
            ),
            "unique_nodes_visited": unique_nodes,
            "observation_count": observation_count,
        }

    def observation_context(self, limit: int = 8) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 20))
        now = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT command, data_json, observed_at
                FROM world_observations
                ORDER BY id DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        observations: list[dict[str, Any]] = []
        for row in rows:
            try:
                data = json.loads(row["data_json"])
            except ValueError:
                data = {}
            observations.append(
                {
                    "command": row["command"],
                    "data": data,
                    "observed_at": float(row["observed_at"]),
                    "age_seconds": round(max(0.0, now - float(row["observed_at"])), 1),
                }
            )
        return observations

    def snapshot(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, notification_id, status, received_at, updated_at, result_json
                FROM deliveries
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                detail = json.loads(row["result_json"])
            except ValueError:
                detail = {}
            result.append(
                {
                    "request_id": row["request_id"],
                    "notification_id": row["notification_id"],
                    "status": row["status"],
                    "received_at": row["received_at"],
                    "updated_at": row["updated_at"],
                    "result": detail,
                }
            )
        return result

    def autonomy_context(self, limit: int = 8) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 20))
        with self._connect() as connection:
            turn_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM deliveries WHERE status = 'acted'"
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT updated_at, result_json
                FROM deliveries
                WHERE status = 'acted'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        recent_actions: list[dict[str, Any]] = []
        for row in rows:
            try:
                detail = json.loads(row["result_json"])
            except ValueError:
                detail = {}
            if not isinstance(detail, dict):
                continue
            recent_actions.append(
                {
                    "command": str(detail.get("command") or "")[:80],
                    "reason": str(detail.get("reason") or "")[:240],
                    "focus": str(detail.get("focus") or "")[:240],
                    "acted_at": float(row["updated_at"]),
                }
            )
        return {
            "turn_count": turn_count,
            "recent_actions": recent_actions,
        }

    def social_context(self, limit: int = 6) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 12))
        now = time.time()
        with self._connect() as connection:
            relationship_rows = connection.execute(
                """
                SELECT agent_id, display_name, interaction_count, affinity,
                       last_topic, follow_up, last_interaction_at
                FROM social_relationships
                ORDER BY last_interaction_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
            episode_rows = connection.execute(
                """
                SELECT kind, command, summary, topic, follow_up,
                       participants_json, importance, confidence, created_at
                FROM social_episodes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
            claim_rows = connection.execute(
                """
                SELECT source_agent_id, claim_text, confidence, last_seen_at
                FROM social_claims
                WHERE confidence >= 0.55
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (bounded_limit * 2,),
            ).fetchall()
            contact_rows = connection.execute(
                """
                SELECT
                    json_extract(result_json, '$.command') AS command,
                    COALESCE(
                        json_extract(result_json, '$.params.target_agent_id'),
                        json_extract(result_json, '$.params.next_speaker_agent_id')
                    ) AS target_agent_id,
                    status,
                    updated_at
                FROM deliveries
                WHERE status = 'acted'
                  AND json_extract(result_json, '$.command') LIKE 'conversation_%'
                  AND COALESCE(
                        json_extract(result_json, '$.params.target_agent_id'),
                        json_extract(result_json, '$.params.next_speaker_agent_id')
                      ) IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (bounded_limit * 4,),
            ).fetchall()
        relationships = [
            {
                "agent_id": row["agent_id"],
                "display_name": row["display_name"],
                "interaction_count": int(row["interaction_count"]),
                "affinity": round(float(row["affinity"]), 2),
                "last_topic": row["last_topic"],
                "follow_up": row["follow_up"],
                "minutes_since_contact": round(
                    max(0.0, now - float(row["last_interaction_at"])) / 60.0,
                    1,
                ),
            }
            for row in relationship_rows
        ]
        episodes = []
        for row in episode_rows:
            try:
                participants = json.loads(row["participants_json"] or "[]")
            except (TypeError, ValueError):
                participants = []
            episodes.append(
                {
                    "kind": row["kind"],
                    "command": row["command"],
                    "summary": row["summary"],
                    "topic": row["topic"],
                    "follow_up": row["follow_up"],
                    "participants": participants if isinstance(participants, list) else [],
                    "minutes_since_event": round(
                        max(0.0, now - float(row["created_at"])) / 60.0,
                        1,
                    ),
                    "importance": round(float(row["importance"]), 2),
                    "confidence": round(float(row["confidence"]), 2),
                }
            )
        claims = [
            {
                "source_agent_id": row["source_agent_id"],
                "claim": row["claim_text"],
                "confidence": round(float(row["confidence"]), 2),
            }
            for row in claim_rows
        ]
        contact_attempts = [
            {
                "command": row["command"],
                "target_agent_id": str(row["target_agent_id"] or ""),
                "status": row["status"],
                "minutes_since_action": round(
                    max(0.0, now - float(row["updated_at"])) / 60.0,
                    1,
                ),
            }
            for row in contact_rows
        ]
        return {
            "relationships": relationships,
            "recent_episodes": episodes,
            "attributed_claims": claims,
            "recent_contact_attempts": contact_attempts,
        }

    def record_social_episode(
        self,
        notification_id: str,
        kind: str,
        command: str,
        reflection: dict[str, Any],
        model: str,
    ) -> bool:
        summary = _safe_text(reflection.get("summary"), 700)
        topic = _safe_text(reflection.get("topic"), 240)
        follow_up = _safe_text(reflection.get("follow_up"), 300)
        importance = _bounded_float(reflection.get("importance"), 0.0, 1.0, 0.5)
        confidence = _bounded_float(reflection.get("confidence"), 0.0, 1.0, 0.5)

        participants: list[dict[str, Any]] = []
        seen_agents: set[str] = set()
        raw_participants = reflection.get("participants")
        if isinstance(raw_participants, list):
            for item in raw_participants[:12]:
                if not isinstance(item, dict):
                    continue
                display_name = _safe_text(
                    item.get("display_name") or item.get("name"), 120
                )
                agent_id = _safe_text(item.get("agent_id") or item.get("id"), 160)
                if not agent_id and display_name:
                    agent_id = "name:" + display_name.casefold()
                if not agent_id or agent_id in seen_agents:
                    continue
                seen_agents.add(agent_id)
                participants.append(
                    {
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "relationship_delta": _bounded_float(
                            item.get("relationship_delta"), -1.0, 1.0, 0.0
                        ),
                    }
                )

        claims: list[dict[str, Any]] = []
        raw_claims = reflection.get("claims")
        if isinstance(raw_claims, list):
            for item in raw_claims[:16]:
                if not isinstance(item, dict):
                    continue
                claim_text = _safe_text(item.get("text") or item.get("claim"), 500)
                source_agent_id = _safe_text(item.get("source_agent_id"), 160)
                if not source_agent_id and len(participants) == 1:
                    source_agent_id = participants[0]["agent_id"]
                claim_confidence = _bounded_float(
                    item.get("confidence"), 0.0, 1.0, 0.35
                )
                if claim_text and source_agent_id:
                    claims.append(
                        {
                            "source_agent_id": source_agent_id,
                            "text": claim_text,
                            "confidence": claim_confidence,
                        }
                    )

        created_at = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO social_episodes
                    (notification_id, kind, command, summary, topic, follow_up,
                     participants_json, claims_json, importance, confidence,
                     model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    _safe_text(kind, 120),
                    _safe_text(command, 120),
                    summary,
                    topic,
                    follow_up,
                    json.dumps(participants, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(claims, ensure_ascii=False, separators=(",", ":")),
                    importance,
                    confidence,
                    _safe_text(model, 160),
                    created_at,
                ),
            )
            if cursor.rowcount != 1:
                return False
            for participant in participants:
                connection.execute(
                    """
                    INSERT INTO social_relationships
                        (agent_id, display_name, interaction_count, affinity,
                         last_topic, follow_up, last_interaction_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        display_name = CASE
                            WHEN excluded.display_name <> '' THEN excluded.display_name
                            ELSE social_relationships.display_name
                        END,
                        interaction_count = social_relationships.interaction_count + 1,
                        affinity = MIN(
                            10.0,
                            MAX(-10.0, social_relationships.affinity + excluded.affinity)
                        ),
                        last_topic = CASE
                            WHEN excluded.last_topic <> '' THEN excluded.last_topic
                            ELSE social_relationships.last_topic
                        END,
                        follow_up = CASE
                            WHEN excluded.follow_up <> '' THEN excluded.follow_up
                            ELSE social_relationships.follow_up
                        END,
                        last_interaction_at = excluded.last_interaction_at
                    """,
                    (
                        participant["agent_id"],
                        participant["display_name"],
                        participant["relationship_delta"],
                        topic,
                        follow_up,
                        created_at,
                    ),
                )
            for claim in claims:
                if claim["confidence"] < 0.45:
                    continue
                connection.execute(
                    """
                    INSERT INTO social_claims
                        (source_agent_id, claim_text, confidence,
                         notification_id, last_seen_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_agent_id, claim_text) DO UPDATE SET
                        confidence = MAX(social_claims.confidence, excluded.confidence),
                        notification_id = excluded.notification_id,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        claim["source_agent_id"],
                        claim["text"],
                        claim["confidence"],
                        notification_id,
                        created_at,
                    ),
                )
        return True

    def social_stats(self) -> dict[str, int]:
        with self._connect() as connection:
            episodes = int(connection.execute("SELECT COUNT(*) FROM social_episodes").fetchone()[0])
            relationships = int(
                connection.execute("SELECT COUNT(*) FROM social_relationships").fetchone()[0]
            )
            claims = int(connection.execute("SELECT COUNT(*) FROM social_claims").fetchone()[0])
        return {
            "episodes": episodes,
            "relationships": relationships,
            "attributed_claims": claims,
        }


class LuminaKarakuriWorldService:
    def __init__(
        self,
        config: KarakuriWorldConfig,
        orchestrator: LuminaOrchestrator,
    ) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.client = KarakuriWorldClient(config)
        self.ledger = KarakuriWorldLedger(config.ledger_path)
        self._turn_lock = asyncio.Lock()

    def accept_webhook(
        self,
        raw_body: bytes,
        authorization: str,
        signature: str,
        request_id: str,
    ) -> tuple[str, bool]:
        if not self.config.webhook_ready:
            raise KarakuriWorldError("Karakuri World integration is not configured")
        if len(raw_body) > WEBHOOK_MAX_BYTES:
            raise KarakuriWorldError("webhook body is too large")
        expected_authorization = f"Bearer {self.config.webhook_secret}"
        if not hmac.compare_digest(authorization, expected_authorization):
            raise PermissionError("webhook bearer token is invalid")
        expected_signature = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature.lower(), expected_signature):
            raise PermissionError("webhook signature is invalid")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise KarakuriWorldError("webhook body is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise KarakuriWorldError("webhook body must be a JSON object")
        notification_id = str(payload.get("notification_id") or "").strip()
        _validate_notification_id(notification_id)
        delivery_id = request_id.strip() or hashlib.sha256(raw_body).hexdigest()
        claimed = self.ledger.claim(delivery_id[:200], notification_id)
        return notification_id, claimed

    async def process_notification(self, notification_id: str) -> None:
        async with self._turn_lock:
            self.ledger.mark(notification_id, "processing", {})
            try:
                envelope = await self.client.get_notification(notification_id)
                notification = envelope.get("notification")
                if not isinstance(notification, dict):
                    raise KarakuriWorldError("saved notification has no notification object")
                if _is_expired(envelope.get("expires_at") or notification.get("expires_at")):
                    self.ledger.mark(notification_id, "expired", {})
                    return
                notification_kind = str(notification.get("kind") or "").strip()
                if not self.config.autonomy_enabled and notification_kind == "idle_reminder":
                    self.ledger.mark(
                        notification_id,
                        "no_action",
                        {"reason": "autonomy_disabled"},
                    )
                    return
                choices = notification.get("choices")
                if not isinstance(choices, list) or not choices:
                    if _is_skill_instruction_notification(notification):
                        self.ledger.mark(
                            notification_id,
                            "acknowledged_system_notice",
                            {"reason": "karakuri_skill_instruction_boundary"},
                        )
                        return
                    self.ledger.mark(notification_id, "no_action", {"reason": "choices_empty"})
                    return

                decision, model = await self._choose_action(notification, choices)
                choice_index = decision.get("choice_index")
                if choice_index is None:
                    self.ledger.mark(
                        notification_id,
                        "no_action",
                        {
                            "reason": _safe_reason(decision.get("reason")),
                            "focus": _safe_reason(decision.get("focus")),
                            "model": model,
                            "rewrite_reason": str(decision.get("rewrite_reason") or "")[:80]
                            or None,
                        },
                    )
                    return
                if isinstance(choice_index, bool) or not isinstance(choice_index, int):
                    raise KarakuriWorldError("Lumina decision choice_index must be an integer or null")
                if choice_index < 0 or choice_index >= len(choices):
                    raise KarakuriWorldError("Lumina selected an out-of-range choice")
                choice = choices[choice_index]
                if not isinstance(choice, dict):
                    raise KarakuriWorldError("selected choice is not an object")
                command = str(choice.get("command") or "").strip()
                if not command:
                    raise KarakuriWorldError("selected choice has no command")
                try:
                    params = _merge_and_validate_params(choice, decision.get("params"))
                except KarakuriWorldError as exc:
                    # Common after rewrite: move/action without concrete params.
                    # Exclude the bad choice and keep picking until one validates.
                    detail = str(exc)
                    if (
                        "exactly one target param" not in detail
                        and "required params" not in detail
                    ):
                        raise
                    recent_actions = self.ledger.autonomy_context(
                        self.config.autonomy_history_limit
                    ).get("recent_actions")
                    exploration_memory = self.ledger.exploration_context(limit=30)
                    world_observations = self.ledger.observation_context(limit=8)
                    social_memory = self.ledger.social_context(limit=6)
                    economy = _economy_context(notification, world_observations)
                    excluded_indices = {choice_index}
                    resolved = False
                    last_detail = detail
                    for _ in range(min(6, max(1, len(choices)))):
                        replacement = _replacement_after_unresolved_move(
                            choices,
                            notification,
                            recent_actions,
                            exploration_memory,
                            world_observations,
                            excluded_indices=excluded_indices,
                            economy=economy,
                            social_memory=social_memory,
                        )
                        replacement_index = replacement.get("choice_index")
                        if (
                            not isinstance(replacement_index, int)
                            or isinstance(replacement_index, bool)
                            or replacement_index < 0
                            or replacement_index >= len(choices)
                            or not isinstance(choices[replacement_index], dict)
                            or replacement_index in excluded_indices
                        ):
                            # Absolute last resort: any executable observe/wait.
                            fallback_index = _best_actionable_index(
                                choices,
                                excluded_indices=excluded_indices,
                                allow_observe=True,
                                allow_wait=True,
                                notification=notification,
                                world_observations=world_observations,
                                exploration_memory=exploration_memory,
                            )
                            if fallback_index is None or fallback_index in excluded_indices:
                                break
                            replacement = {
                                "choice_index": fallback_index,
                                "params": dict(choices[fallback_index].get("params") or {})
                                if isinstance(choices[fallback_index].get("params"), dict)
                                else {},
                                "reason": "パラメータ不足の候補を避け、実行可能な別候補へ切り替える。",
                                "focus": "停滞せず世界に関与する",
                                "rewrite_reason": "param_unresolved",
                            }
                            replacement_index = fallback_index
                        try:
                            choice_index = replacement_index
                            choice = choices[choice_index]
                            command = str(choice.get("command") or "").strip()
                            params = _merge_and_validate_params(
                                choice, replacement.get("params")
                            )
                            decision = replacement
                            if not decision.get("rewrite_reason"):
                                decision["rewrite_reason"] = "param_unresolved"
                            model = f"{model}+param-fallback"
                            resolved = True
                            break
                        except KarakuriWorldError as retry_exc:
                            last_detail = str(retry_exc)
                            excluded_indices.add(replacement_index)
                            continue
                    if not resolved:
                        self.ledger.mark(
                            notification_id,
                            "no_action",
                            {
                                "reason": "実行可能な候補パラメータを確定できないため、次の通知を待つ。",
                                "focus": "位置確認か別の候補を待つ",
                                "model": model,
                                "rewrite_reason": "param_unresolved",
                                "detail": last_detail[:300],
                            },
                        )
                        return
                    # fall through with resolved choice/params/command

                if command == "move" and _move_params_target_current_node(
                    params, notification
                ):
                    replacement = _deterministic_decision(
                        choices,
                        notification,
                        self.ledger.autonomy_context(
                            self.config.autonomy_history_limit
                        ).get("recent_actions"),
                        self.ledger.exploration_context(limit=30),
                        self.ledger.observation_context(limit=8),
                        excluded_indices={choice_index},
                    )
                    replacement_index = replacement.get("choice_index")
                    if (
                        isinstance(replacement_index, int)
                        and not isinstance(replacement_index, bool)
                        and 0 <= replacement_index < len(choices)
                        and isinstance(choices[replacement_index], dict)
                    ):
                        choice_index = replacement_index
                        choice = choices[choice_index]
                        command = str(choice.get("command") or "").strip()
                        params = _merge_and_validate_params(
                            choice, replacement.get("params")
                        )
                        decision = replacement
                    else:
                        self.ledger.mark(
                            notification_id,
                            "skipped",
                            {
                                "reason": "same_node_move_prevented",
                                "current_node_id": _extract_current_node(notification),
                            },
                        )
                        return

                result = await self.client.command(notification_id, command, params)
                self.ledger.record_world_action(
                    notification_id,
                    command,
                    params,
                    notification,
                    result=result,
                )
                self.orchestrator.cognition.observe_world_action(
                    command,
                    notification_id=notification_id,
                    notification_kind=notification_kind,
                    target=_extract_target_node(params),
                    success=bool(result.get("ok", True)),
                )
                social_memory_status = "not_social"
                if _is_social_turn(notification, command):
                    try:
                        recorded = await self._remember_social_turn(
                            notification_id,
                            notification,
                            command,
                            decision,
                        )
                        social_memory_status = "recorded" if recorded else "duplicate"
                    except (KarakuriWorldError, ProviderError, ValueError, TypeError) as exc:
                        social_memory_status = "failed:" + type(exc).__name__
                self.ledger.mark(
                    notification_id,
                    "acted",
                    {
                        "choice_index": choice_index,
                        "command": command,
                        "model": model,
                        "reason": _safe_reason(decision.get("reason")),
                        "focus": _safe_reason(decision.get("focus")),
                        "rewrite_reason": str(decision.get("rewrite_reason") or "")[:80] or None,
                        "params": params,
                        "target_node_id": _extract_target_node(params),
                        "api_ok": bool(result.get("ok", True)),
                        "social_memory": social_memory_status,
                    },
                )
            except KarakuriWorldAPIError as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                hint = str(detail.get("hint") or "")[:300]
                retryable = exc.status_code >= 500 or exc.status_code in {408, 425, 429}
                self.ledger.mark(
                    notification_id,
                    "failed" if retryable else "rejected",
                    {
                        "error": "api_error",
                        "status_code": exc.status_code,
                        "hint": hint,
                        "latest_notification_id": str(
                            (detail.get("details") or {}).get("latest_notification_id")
                            if isinstance(detail.get("details"), dict)
                            else ""
                        )[:200],
                    },
                )
            except (KarakuriWorldError, ProviderError, ValueError, TypeError) as exc:
                self.ledger.mark(
                    notification_id,
                    "failed",
                    {"error": type(exc).__name__, "detail": str(exc)[:500]},
                )
            except Exception as exc:
                # Never leave a delivery permanently stuck in "processing".
                # The Discord worker can safely retry the failed notification.
                self.ledger.mark(
                    notification_id,
                    "failed",
                    {
                        "error": "unexpected_internal_error",
                        "exception_type": type(exc).__name__,
                        "detail": str(exc)[:500],
                    },
                )

    async def _choose_action(
        self,
        notification: dict[str, Any],
        choices: list[Any],
    ) -> tuple[dict[str, Any], str]:
        compact = _compact_notification(notification)
        available_commands = [
            str(choice.get("command") or "").strip()
            for choice in choices
            if isinstance(choice, dict)
        ]
        if self.orchestrator.cognition.is_sleeping():
            for preferred in ("wait", "get_available_actions", "get_map"):
                for index, choice in enumerate(choices):
                    if (
                        isinstance(choice, dict)
                        and str(choice.get("command") or "").strip() == preferred
                        and _choice_is_executable(choice)
                    ):
                        return (
                            {
                                "choice_index": index,
                                "params": {},
                                "reason": "睡眠中のため、通知を処理しつつ低影響行動を選ぶ。",
                                "focus": "記憶統合と安全な待機",
                            },
                            "sleep-policy",
                        )
            fallback = _deterministic_decision(
                choices,
                compact,
                self.ledger.autonomy_context(self.config.autonomy_history_limit).get("recent_actions"),
                self.ledger.exploration_context(limit=30),
                self.ledger.observation_context(limit=8),
                economy=_economy_context(
                    compact, self.ledger.observation_context(limit=8)
                ),
            )
            # Sleep should not start long work shifts.
            if str(
                (fallback.get("params") or {}).get("action_id")
                if isinstance(fallback.get("params"), dict)
                else ""
            ).startswith("work-") or _looks_like_earn_action(
                str(
                    (fallback.get("params") or {}).get("action_id")
                    if isinstance(fallback.get("params"), dict)
                    else ""
                ),
                None,
            ):
                for preferred in ("wait", "get_map", "get_available_actions"):
                    for index, choice in enumerate(choices):
                        if (
                            isinstance(choice, dict)
                            and str(choice.get("command") or "").strip() == preferred
                            and _choice_is_executable(choice)
                        ):
                            return (
                                {
                                    "choice_index": index,
                                    "params": {},
                                    "reason": "睡眠中のため、バイトではなく低影響行動を選ぶ。",
                                    "focus": "記憶統合と安全な待機",
                                },
                                "sleep-policy",
                            )
            fallback["reason"] = "睡眠中だが通知を未処理にしないため、候補内の安全な行動を選ぶ。"
            return fallback, "sleep-policy-fallback"
        social_memory = self.ledger.social_context(limit=6)
        exploration_memory = self.ledger.exploration_context(limit=12)
        world_observations = self.ledger.observation_context(limit=4)
        recent_actions = self.ledger.autonomy_context(
            max(12, self.config.autonomy_history_limit)
        ).get("recent_actions")
        repeat_pressure = _repeat_pressure(recent_actions)
        economy = _economy_context(compact, world_observations)
        standing_goal = self.config.autonomy_goal
        if economy.get("needs_income"):
            standing_goal = (
                "所持金が足りない。目の前で稼げるバイトがあれば先に働き、"
                "払えない食事や遊びは選ばない。"
            )
        elif economy.get("comfortable"):
            standing_goal = (
                "所持金に余裕がある。バイトの連打はせず、"
                "会話・探索・食事など自然な過ごし方を選ぶ。"
            )
        if _llm_disabled_by_environment():
            return (
                {
                    "choice_index": None,
                    "params": {},
                    "reason": "会話モデルが停止中のため、自律行動を保留する。",
                    "focus": "モデル復帰後に会話または行動を再開する",
                    "rewrite_reason": "llm_disabled_no_action",
                },
                "llm-disabled",
            )
        autonomy = {
            "enabled": self.config.autonomy_enabled,
            "standing_goal": standing_goal,
            "social_opportunity": any(
                command in SOCIAL_COMMANDS for command in available_commands
            ),
            "social_memory": social_memory,
            "exploration_memory": exploration_memory,
            "world_observations": world_observations,
            "economy": economy,
            "repeat_pressure": repeat_pressure,
            "turn_count": self.ledger.autonomy_context(
                self.config.autonomy_history_limit
            ).get("turn_count"),
            "recent_actions": recent_actions if isinstance(recent_actions, list) else [],
        }
        decision_num_ctx = min(int(self.orchestrator.config.num_ctx), 2048)
        # Hard budget so IQ2 (-c 2048) never receives an oversized prompt.
        # JP tokens are dense; keep system+user well under ~1600 tokens.
        decision_prompt_budget = max(480, min(decision_num_ctx - 700, 1400))
        slim_notification = _slim_notification_for_decision(compact)
        user_prompt = _bounded_decision_prompt(
            autonomy,
            slim_notification,
            decision_prompt_budget,
        )
        system_prompt = _decision_system_prompt()
        llm_ok = False
        try:
            result = await self.orchestrator.llm.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                num_ctx=decision_num_ctx,
                max_tokens=max(80, min(self.config.decision_max_tokens, 180)),
                task_kind="json_action",
            )
            decision = _parse_decision(result.text)
            model = result.model
            llm_ok = True
        except (ProviderError, KarakuriWorldError, ValueError, TypeError, RuntimeError) as exc:
            LOGGER = __import__("logging").getLogger(__name__)
            LOGGER.warning(
                "Karakuri LLM decision failed; using deterministic fallback: %s",
                exc,
            )
            decision = _deterministic_decision(
                choices,
                compact,
                recent_actions,
                exploration_memory,
                world_observations,
                excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                economy=economy,
            )
            decision = _ensure_decision_text(
                decision,
                reason="候補内から次の一歩を選ぶ。",
                focus="場所・相手・話題のどれかを変える",
            )
            model = f"deterministic-fallback:{type(exc).__name__}"
            decision["llm_error"] = str(exc)[:240]
        if decision.get("choice_index") is None and self.config.autonomy_enabled:
            actionable = _has_actionable_choices(choices)
            if economy.get("needs_income"):
                decision = _deterministic_decision(
                    choices,
                    compact,
                    recent_actions,
                    exploration_memory,
                    world_observations,
                    excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                    economy=economy,
                )
                decision["reason"] = _safe_reason(decision.get("reason")) or (
                    "所持金が足りないので、稼げる行動を優先する。"
                )
                decision["focus"] = _safe_reason(decision.get("focus")) or (
                    "バイトで収入を得てから食事や遊びを考える"
                )
                if not decision.get("rewrite_reason"):
                    decision["rewrite_reason"] = "survival_earn"
                model = f"{model}+survival"
                if decision.get("choice_index") is None and actionable:
                    decision = _force_action_required(
                        choices,
                        compact,
                        recent_actions,
                        exploration_memory,
                        world_observations,
                        social_memory,
                        excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                        economy=economy,
                    )
                    model = f"{model}+action_required"
            elif not actionable:
                decision.setdefault("rewrite_reason", "no_actionable")
            else:
                # IQ2 here is too slow for a second full decision pass; force.
                decision = _force_action_required(
                    choices,
                    compact,
                    recent_actions,
                    exploration_memory,
                    world_observations,
                    social_memory,
                    excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                    economy=economy,
                )
                model = f"{model}+action_required"
                llm_ok = False
        # When IQ2 decided successfully, keep its choice. Guards only hard-stop
        # impossible or extreme-loop actions so the large model can stay flexible.
        if not llm_ok:
            decision = _prefer_active_choice(
                decision,
                choices,
                recent_actions,
                exploration_memory,
                world_observations,
                compact,
                social_memory,
            )
            if (
                (
                    decision.get("choice_index") is None
                    or _selected_choice_command(decision, choices) == "wait"
                )
                and self.config.autonomy_enabled
                and _has_actionable_choices(choices)
            ):
                decision = _force_action_required(
                    choices,
                    compact,
                    recent_actions,
                    exploration_memory,
                    world_observations,
                    social_memory,
                    excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                    economy=economy,
                )
                if "+action_required" not in model:
                    model = f"{model}+action_required"
        decision, anti_reason = _apply_anti_repeat_rewrite(
            decision,
            choices,
            recent_actions,
            exploration_memory,
            world_observations,
            compact,
            social_memory,
            repeat_pressure,
            trust_llm=llm_ok,
        )
        if anti_reason:
            model = f"{model}+{anti_reason}"
        if (
            (
                decision.get("choice_index") is None
                or _selected_choice_command(decision, choices) == "wait"
            )
            and self.config.autonomy_enabled
            and _has_actionable_choices(choices)
        ):
            decision = _force_action_required(
                choices,
                compact,
                recent_actions,
                exploration_memory,
                world_observations,
                social_memory,
                excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                economy=economy,
            )
            if "+action_required" not in model:
                model = f"{model}+action_required"
            llm_ok = False
        decision, survival_reason = _apply_survival_prefer(
            decision,
            choices,
            economy,
            hard_only=llm_ok,
        )
        if survival_reason:
            model = f"{model}+{survival_reason}"
        decision, lifestyle_reason = _apply_avoid_unnecessary_work(
            decision,
            choices,
            economy,
            recent_actions,
            exploration_memory,
            world_observations,
            compact,
            social_memory,
            hard_only=llm_ok,
        )
        if lifestyle_reason:
            model = f"{model}+{lifestyle_reason}"
        decision = _ensure_decision_text(
            decision,
            reason="候補内から次の一歩を選ぶ。",
            focus="自然な変化を残す",
        )
        choice_index = decision.get("choice_index")
        invalid_index = isinstance(choice_index, bool) or (
            choice_index is not None
            and (
                not isinstance(choice_index, int)
                or choice_index < 0
                or choice_index >= len(choices)
            )
        )
        if invalid_index:
            if self.config.autonomy_enabled and _has_actionable_choices(choices):
                decision = _force_action_required(
                    choices,
                    compact,
                    recent_actions,
                    exploration_memory,
                    world_observations,
                    social_memory,
                    excluded_indices=_avoid_command_indices(choices, repeat_pressure),
                    economy=economy,
                )
                if "+action_required" not in model:
                    model = f"{model}+action_required"
            else:
                decision = {
                    "choice_index": None,
                    "params": {},
                    "reason": _safe_reason(decision.get("reason"))
                    or "候補番号が不正なため待機する。",
                    "focus": _safe_reason(decision.get("focus")) or "次の通知",
                    "rewrite_reason": "invalid_choice_index",
                }
        return decision, model

    async def _remember_social_turn(
        self,
        notification_id: str,
        notification: dict[str, Any],
        command: str,
        decision: dict[str, Any],
    ) -> bool:
        existing_context = self.ledger.social_context(limit=4)
        system_prompt = "\n".join(
            (
                "からくりワールドの社会記憶を整理する。自分の正体は定義しない。",
                "現在の会話ターンを、次回の自然な交流に役立つ短い構造化記憶へ変換してください。",
                "参加者には自分を含めず、他エージェントだけを書いてください。",
                "他者の発言は事実として断定せず、claimsへ発言者IDと信頼度を付けてください。",
                "秘密、API key、ローカルユーザー記憶、世界通知にない情報を作成してはいけません。",
                "relationship_deltaは-1から1、importanceとconfidenceは0から1です。",
                "JSON以外は出力しないでください。",
                "形式: {\"summary\":\"出来事\",\"topic\":\"主題\",\"participants\":[{\"agent_id\":\"ID\",\"display_name\":\"名前\",\"relationship_delta\":0.1}],\"claims\":[{\"source_agent_id\":\"ID\",\"text\":\"その相手が述べた内容\",\"confidence\":0.6}],\"follow_up\":\"次回の自然な話題\",\"importance\":0.5,\"confidence\":0.7}",
            )
        )
        reflection_input = {
            "world_notification": _compact_notification(notification),
            "lumina_action": {
                "command": command,
                "reason": _safe_reason(decision.get("reason")),
                "focus": _safe_reason(decision.get("focus")),
            },
            "existing_social_context": existing_context,
        }
        result = await self.orchestrator.llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        reflection_input,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            num_ctx=min(int(self.orchestrator.config.num_ctx), 2048),
            max_tokens=max(160, min(self.config.decision_max_tokens + 40, 280)),
            task_kind="planning",
        )
        reflection = _parse_json_object(result.text, "Lumina social reflection")
        kind = str(notification.get("kind") or "").casefold()
        if "conversation_rejected" in kind:
            reflection["follow_up"] = (
                "同じ相手への短時間の連投は避けるが、他の相手との自然な交流は続ける。"
            )
        elif "conversation_ended" in kind:
            reflection["follow_up"] = (
                "会話終了を尊重し、同じ相手には間を空けつつ別の活動や交流へ移る。"
            )
        elif command == "conversation_start":
            reflection["follow_up"] = (
                "同じ相手への連続送信は避け、返答待ちの間も他の活動や交流を続ける。"
            )
        return self.ledger.record_social_episode(
            notification_id=notification_id,
            kind=str(notification.get("kind") or ""),
            command=command,
            reflection=reflection,
            model=result.model,
        )

    def health(self) -> dict[str, Any]:
        recent = self.ledger.snapshot(limit=10)
        return {
            "ok": self.config.ready,
            "configured": self.config.ready,
            "api_base": self.config.api_base,
            "api_key_present": bool(self.config.api_key),
            "webhook_secret_present": bool(self.config.webhook_secret),
            "webhook_configured": self.config.webhook_ready,
            "ledger_path": str(self.config.ledger_path),
            "one_notification_one_action": True,
            "autonomy": {
                "enabled": self.config.autonomy_enabled,
                "trigger_mode": "world_notifications_and_idle_reminders",
                "standing_goal": self.config.autonomy_goal,
                "behavior_guards": {
                    "anti_repeat": True,
                    "intentional_wait": False,
                    "action_required": True,
                    "llm_disabled_no_action": True,
                    "llm_null_retry": False,
                    "social_prefer": True,
                    "survival_earn": True,
                    "avoid_work_spam": True,
                    "trust_llm_when_available": True,
                    "history_window": self.config.autonomy_history_limit,
                },
                **self.ledger.autonomy_context(self.config.autonomy_history_limit),
            },
            "economy": _economy_context(
                {},
                self.ledger.observation_context(limit=8),
            ),
            "social_memory": {
                "policy": "conversation_preferred_with_cooldowns",
                "storage": "local_attributed_world_memory",
                **self.ledger.social_stats(),
            },
            "exploration": self.ledger.exploration_context(limit=20),
            "world_observations": self.ledger.observation_context(limit=8),
            "operations": self.ledger.operational_stats(),
            "cognition": self.orchestrator.cognition.snapshot(),
            "sleep": self.orchestrator.sleep_cycle.status(),
            "recent": recent,
        }


def _llm_disabled_by_environment() -> bool:
    """Prevent movement-only autonomy while the local conversation model is off."""

    value = str(os.environ.get("LUMINA_LLM_DISABLED") or "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def _is_skill_instruction_notification(notification: dict[str, Any]) -> bool:
    """Recognize a system skill hint without consuming real world choices."""
    if not isinstance(notification, dict):
        return False
    parts: list[str] = []
    for key in ("kind", "title", "text", "message", "content", "prompt"):
        value = notification.get(key)
        if isinstance(value, str):
            parts.append(value)
    text = " ".join(parts).strip()
    if not text or len(text) > 800:
        return False
    normalized = re.sub(r"\s+", " ", text).casefold()
    world_hint = "karakuri-world" in normalized or "からくりワールド" in normalized
    skill_hint = "スキル" in text or "skill" in normalized
    use_hint = (
        "利用" in text
        or "使って" in text
        or "use" in normalized
        or "invoke" in normalized
    )
    return world_hint and skill_hint and use_hint


def _validate_notification_id(notification_id: str) -> None:
    if not NOTIFICATION_ID_PATTERN.fullmatch(notification_id):
        raise KarakuriWorldError("notification_id is invalid")


def _is_expired(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip().replace("Z", "+00:00")
    try:
        expires_at = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _compact_notification(notification: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "kind",
        "summary",
        "perception",
        "location",
        "current_map",
        "location_label",
        "agent",
        "state",
        "conversation",
        "conversation_context",
        "participants",
        "speaker",
        "message",
        "recent_messages",
        "payload",
        "choices",
    )
    compact = {key: notification[key] for key in allowed if key in notification}
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= 6000:
        return compact
    return {
        "kind": notification.get("kind"),
        "summary": str(notification.get("summary") or "")[:1500],
        "perception": str(notification.get("perception") or "")[:1800],
        "location": notification.get("location"),
        "state": notification.get("state"),
        "participants": notification.get("participants"),
        "payload": notification.get("payload"),
        "choices": notification.get("choices") if isinstance(notification.get("choices"), list) else [],
    }


def _parse_json_object(text: str, label: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise KarakuriWorldError(f"{label} did not contain a JSON object")
    try:
        value = json.loads(raw[start : end + 1])
    except ValueError as exc:
        raise KarakuriWorldError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise KarakuriWorldError(f"{label} must be a JSON object")
    return value


def _parse_decision(text: str) -> dict[str, Any]:
    value = _parse_json_object(text, "Lumina decision")
    params = value.get("params")
    if params is None:
        value["params"] = {}
    elif not isinstance(params, dict):
        raise KarakuriWorldError("Lumina decision params must be a JSON object")
    return value


def _safe_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _bounded_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def _is_social_turn(notification: dict[str, Any], command: str) -> bool:
    kind = str(notification.get("kind") or "").casefold()
    return command in SOCIAL_COMMANDS or "conversation" in kind or "social" in kind


OBSERVE_LIKE_COMMANDS = frozenset(
    {
        "action",
        "get_perception",
        "get_status",
        "get_map",
        "get_available_actions",
        "get_action_details",
        "get_nearby_agents",
        "get_world_agents",
        "get_bulletin",
        "get_event",
        "wait",
    }
)


def _is_observe_family(command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    if normalized in OBSERVE_LIKE_COMMANDS:
        return True
    return normalized.startswith("get_")


def _is_actionable_command(command: str) -> bool:
    """Real world acts: social / move / travel / action. Not wait or get_*."""
    normalized = str(command or "").strip()
    if not normalized or normalized == "wait":
        return False
    if normalized.startswith("get_"):
        return False
    return True


def _is_actionable_choice(choice: Any) -> bool:
    if not isinstance(choice, dict) or not _choice_is_executable(choice):
        return False
    return _is_actionable_command(str(choice.get("command") or "").strip())


def _has_actionable_choices(choices: list[Any]) -> bool:
    return any(_is_actionable_choice(choice) for choice in choices)


def _actionable_rank(command: str) -> int:
    normalized = str(command or "").strip()
    if normalized in SOCIAL_COMMANDS:
        return 0
    if normalized == "action":
        return 1
    if normalized == "travel":
        return 2
    if normalized == "move":
        return 3
    if _is_actionable_command(normalized):
        return 4
    if _is_observe_family(normalized) and normalized != "wait":
        return 5
    if normalized == "wait":
        return 6
    return 7


def _best_actionable_index(
    choices: list[Any],
    *,
    excluded_indices: set[int] | None = None,
    allow_observe: bool = False,
    allow_wait: bool = False,
    notification: dict[str, Any] | None = None,
    world_observations: Any = None,
    exploration_memory: Any = None,
) -> int | None:
    """Pick social > action > travel/move > (optional observe) > wait."""
    excluded = excluded_indices or set()
    current_node = ""
    known_agent_nodes: set[str] = set()
    if notification is not None:
        current_node = _infer_current_node(
            notification, exploration_memory, world_observations
        )
        known_agent_nodes = _agent_nodes_from_observations(world_observations)
        if current_node:
            known_agent_nodes.discard(current_node)
            known_agent_nodes = _nodes_on_same_map(current_node, known_agent_nodes)

    ranked: list[tuple[tuple[int, int], int]] = []
    for index, choice in enumerate(choices):
        if index in excluded or not isinstance(choice, dict):
            continue
        if not _choice_is_executable(choice):
            continue
        command = str(choice.get("command") or "").strip()
        if not command:
            continue
        if command == "wait" and not allow_wait:
            continue
        if _is_observe_family(command) and command != "action" and not allow_observe:
            # get_* family only when explicitly allowed as last resort
            if command.startswith("get_") or command in {
                "get_map",
                "get_available_actions",
            }:
                continue
        if command == "move":
            params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
            target = _extract_target_node(params)
            if not target and known_agent_nodes and current_node:
                target = _nearest_agent_node(current_node, known_agent_nodes) or ""
            if not target and not any(
                str(params.get(key) or "").strip()
                for key in ("target_building_id", "target_npc_id")
            ):
                # Unresolved move shells are not actionable replacements.
                continue
            if (
                current_node
                and target
                and not _same_map_nodes(current_node, target)
            ):
                continue
        rank = _actionable_rank(command)
        if rank >= 5 and not allow_observe and not allow_wait:
            continue
        ranked.append(((rank, index), index))
    if ranked:
        return min(ranked)[1]
    if allow_observe:
        ranked = []
        for index, choice in enumerate(choices):
            if index in excluded or not isinstance(choice, dict):
                continue
            if not _choice_is_executable(choice):
                continue
            command = str(choice.get("command") or "").strip()
            if command.startswith("get_") or command in OBSERVE_LIKE_COMMANDS:
                if command == "wait" and not allow_wait:
                    continue
                if command == "action":
                    continue
                ranked.append(((_actionable_rank(command), index), index))
        if ranked:
            return min(ranked)[1]
    return None


def _force_action_required(
    choices: list[Any],
    notification: dict[str, Any],
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    social_memory: Any,
    *,
    excluded_indices: set[int] | None = None,
    economy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Force a real act when null/wait would stall autonomy."""
    excluded = set(excluded_indices or set())
    decision = _deterministic_decision(
        choices,
        notification,
        recent_actions,
        exploration_memory,
        world_observations,
        excluded_indices=excluded,
        economy=economy,
    )
    selected = decision.get("choice_index")
    selected_command = ""
    if (
        isinstance(selected, int)
        and not isinstance(selected, bool)
        and 0 <= selected < len(choices)
        and isinstance(choices[selected], dict)
    ):
        selected_command = str(choices[selected].get("command") or "").strip()
    needs_replace = (
        selected is None
        or selected_command == "wait"
        or (
            selected_command.startswith("get_")
            and _has_actionable_choices(choices)
        )
    )
    if needs_replace:
        index = _best_actionable_index(
            choices,
            excluded_indices=excluded,
            allow_observe=False,
            allow_wait=False,
            notification=notification,
            world_observations=world_observations,
            exploration_memory=exploration_memory,
        )
        if index is None:
            index = _best_actionable_index(
                choices,
                excluded_indices=excluded,
                allow_observe=True,
                allow_wait=False,
                notification=notification,
                world_observations=world_observations,
                exploration_memory=exploration_memory,
            )
        if index is None:
            index = _best_actionable_index(
                choices,
                excluded_indices=excluded,
                allow_observe=True,
                allow_wait=True,
                notification=notification,
                world_observations=world_observations,
                exploration_memory=exploration_memory,
            )
        if index is not None:
            choice = choices[index]
            params = (
                dict(choice.get("params") or {})
                if isinstance(choice.get("params"), dict)
                else {}
            )
            command = str(choice.get("command") or "").strip()
            if command == "move" and not _extract_target_node(params):
                current_node = _infer_current_node(
                    notification, exploration_memory, world_observations
                )
                known = _agent_nodes_from_observations(world_observations)
                if current_node:
                    known.discard(current_node)
                    known = _nodes_on_same_map(current_node, known)
                nearest = _nearest_agent_node(current_node, known)
                if nearest:
                    params["target_node_id"] = nearest
                params = _ensure_move_target_params(params, params)
            decision = {
                "choice_index": index,
                "params": params,
                "reason": "待機で停滞させず、実行可能な次の一手を選ぶ。",
                "focus": "会話・移動・行動のどれかで世界に関与する",
            }
    decision = _prefer_active_choice(
        decision,
        choices,
        recent_actions,
        exploration_memory,
        world_observations,
        notification,
        social_memory,
    )
    decision = _ensure_decision_text(
        decision,
        reason="待機で停滞させず、実行可能な次の一手を選ぶ。",
        focus="会話・移動・行動のどれかで世界に関与する",
    )
    decision["rewrite_reason"] = "action_required"
    return decision


def _replacement_after_unresolved_move(
    choices: list[Any],
    notification: dict[str, Any],
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    *,
    excluded_indices: set[int] | None = None,
    economy: dict[str, Any] | None = None,
    social_memory: Any = None,
) -> dict[str, Any]:
    """After a doomed move, prefer social/action over observe/wait."""
    excluded = set(excluded_indices or set())
    decision = _force_action_required(
        choices,
        notification,
        recent_actions,
        exploration_memory,
        world_observations,
        social_memory or {},
        excluded_indices=excluded,
        economy=economy,
    )
    decision["rewrite_reason"] = "move_target_unresolved"
    return decision


def _repeat_pressure(
    recent_actions: Any,
    *,
    short_window: int = 5,
    long_window: int = 12,
) -> dict[str, Any]:
    actions = [
        item
        for item in (recent_actions if isinstance(recent_actions, list) else [])
        if isinstance(item, dict)
    ]
    short = actions[:short_window]
    long = actions[:long_window]
    short_commands = [
        str(item.get("command") or "").strip()
        for item in short
        if str(item.get("command") or "").strip()
    ]
    long_commands = [
        str(item.get("command") or "").strip()
        for item in long
        if str(item.get("command") or "").strip()
    ]
    short_counts = Counter(short_commands)
    long_counts = Counter(long_commands)
    avoid: list[str] = []
    for command, count in short_counts.items():
        if count >= 3:
            avoid.append(command)
    for command, count in long_counts.items():
        if count >= 5 and command not in avoid:
            avoid.append(command)
    observe_short = sum(1 for command in short_commands if _is_observe_family(command))
    if observe_short >= 3:
        for command in short_commands:
            if _is_observe_family(command) and command not in avoid:
                avoid.append(command)
    high = (
        bool(avoid)
        or (len(short) >= 4 and len(short_counts) <= 2)
        or observe_short >= 3
    )
    prefer_change = (
        "直近が同じcommandや観察系に偏っています。会話・移動・食事・仕事など実行動で変化を作ってください。待機は選ばない。"
        if high
        else "意味のある変化を優先し、同じ観察の連打は避けてください。待機で逃げない。"
    )
    return {
        "high": high,
        "avoid_commands": avoid,
        "short_counts": dict(short_counts),
        "long_counts": dict(long_counts),
        "prefer_change": prefer_change,
        "observe_family_short": observe_short,
    }


def _should_allow_intentional_wait(recent_actions: Any, pressure: dict[str, Any]) -> bool:
    if pressure.get("high"):
        return True
    commands = [
        str(item.get("command") or "").strip()
        for item in (recent_actions if isinstance(recent_actions, list) else [])[:5]
        if isinstance(item, dict) and str(item.get("command") or "").strip()
    ]
    observe_hits = sum(1 for command in commands if command in OBSERVE_LIKE_COMMANDS)
    return observe_hits >= 3


def _selected_choice_command(decision: dict[str, Any], choices: list[Any]) -> str:
    choice_index = decision.get("choice_index")
    if isinstance(choice_index, bool) or not isinstance(choice_index, int):
        return ""
    if choice_index < 0 or choice_index >= len(choices):
        return ""
    choice = choices[choice_index]
    if not isinstance(choice, dict):
        return ""
    return str(choice.get("command") or "").strip()


def _avoid_command_indices(choices: list[Any], pressure: dict[str, Any]) -> set[int]:
    avoid = {
        str(command).strip()
        for command in (pressure.get("avoid_commands") or [])
        if str(command).strip()
    }
    if not avoid:
        return set()
    return {
        index
        for index, choice in enumerate(choices)
        if isinstance(choice, dict)
        and str(choice.get("command") or "").strip() in avoid
    }


def _follow_up_agent_ids(social_memory: Any) -> set[str]:
    if not isinstance(social_memory, dict):
        return set()
    result: set[str] = set()
    for item in social_memory.get("relationships") or []:
        if not isinstance(item, dict):
            continue
        follow_up = str(item.get("follow_up") or "").strip()
        agent_id = str(item.get("agent_id") or "").strip()
        if follow_up and agent_id:
            result.add(agent_id)
    return result


def _ensure_decision_text(
    decision: dict[str, Any],
    *,
    reason: str,
    focus: str,
) -> dict[str, Any]:
    updated = dict(decision)
    if not _safe_reason(updated.get("reason")):
        updated["reason"] = reason
    if not _safe_reason(updated.get("focus")):
        updated["focus"] = focus
    return updated


def _apply_anti_repeat_rewrite(
    decision: dict[str, Any],
    choices: list[Any],
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    notification: dict[str, Any],
    social_memory: Any,
    pressure: dict[str, Any] | None = None,
    *,
    trust_llm: bool = False,
) -> tuple[dict[str, Any], str | None]:
    pressure = pressure or _repeat_pressure(recent_actions)
    command = _selected_choice_command(decision, choices)
    if not command:
        return decision, None
    short_commands = [
        str(item.get("command") or "").strip()
        for item in (recent_actions if isinstance(recent_actions, list) else [])[:5]
        if isinstance(item, dict) and str(item.get("command") or "").strip()
    ]
    short_count = short_commands.count(command)
    # When the large model decided, only break truly stuck loops.
    threshold = 5 if trust_llm else 3
    needs_rewrite = short_count >= threshold or (
        not trust_llm and command in set(pressure.get("avoid_commands") or [])
    )
    if not needs_rewrite:
        return decision, None
    excluded = _avoid_command_indices(choices, pressure)
    for index, choice in enumerate(choices):
        if (
            isinstance(choice, dict)
            and str(choice.get("command") or "").strip() == command
        ):
            excluded.add(index)
    replacement = _deterministic_decision(
        choices,
        notification,
        recent_actions,
        exploration_memory,
        world_observations,
        excluded_indices=excluded,
    )
    replacement_index = replacement.get("choice_index")
    if replacement_index is None:
        if _has_actionable_choices(choices):
            updated = _force_action_required(
                choices,
                notification,
                recent_actions,
                exploration_memory,
                world_observations,
                social_memory,
                excluded_indices=excluded,
            )
            updated["rewrite_reason"] = "anti_repeat"
            return updated, "anti_repeat"
        updated = {
            "choice_index": None,
            "params": {},
            "reason": "同じ行動の連打を避け、一拍おいて次の変化を待つ。",
            "focus": "場所か相手を変える次の機会",
            "rewrite_reason": "anti_repeat",
        }
        return updated, "anti_repeat"
    updated = _ensure_decision_text(
        replacement,
        reason="同じcommandの連打を避け、別の候補へ切り替える。",
        focus="場所・相手・話題のどれかを変える",
    )
    updated["rewrite_reason"] = "anti_repeat"
    # Keep social params enrichment when the rewritten choice is social.
    updated = _prefer_active_choice(
        updated,
        choices,
        recent_actions,
        exploration_memory,
        world_observations,
        notification,
        social_memory,
    )
    if not updated.get("rewrite_reason"):
        updated["rewrite_reason"] = "anti_repeat"
    return updated, "anti_repeat"


def _prefer_active_choice(
    decision: dict[str, Any],
    choices: list[Any],
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    notification: dict[str, Any],
    social_memory: Any,
) -> dict[str, Any]:
    choice_index = decision.get("choice_index")
    if isinstance(choice_index, bool) or not isinstance(choice_index, int):
        return decision
    if choice_index < 0 or choice_index >= len(choices):
        return decision
    selected = choices[choice_index]
    if not isinstance(selected, dict):
        return decision
    selected_command = str(selected.get("command") or "").strip()
    passive_commands = {"wait", "get_map", "get_available_actions"}
    recent_commands = [
        str(item.get("command") or "").strip()
        for item in (recent_actions if isinstance(recent_actions, list) else [])[:4]
        if isinstance(item, dict)
    ]
    passive_streak = bool(recent_commands and recent_commands[0] in passive_commands)
    recent_route_targets = _recent_route_targets(exploration_memory)
    known_agent_nodes = _agent_nodes_from_observations(world_observations)
    current_node = _infer_current_node(
        notification, exploration_memory, world_observations
    )
    if current_node:
        known_agent_nodes.discard(current_node)
    fresh_agent_lookup = any(
        isinstance(item, dict)
        and item.get("command") == "get_world_agents"
        and float(item.get("age_seconds") or 0) < 300
        for item in (world_observations if isinstance(world_observations, list) else [])
    )
    social_choices: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    social_params_by_index: dict[int, dict[str, Any]] = {}
    blocked_start_indices: dict[int, str] = {}
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        command = str(choice.get("command") or "").strip()
        if command not in SOCIAL_COMMANDS:
            continue
        social_params = _social_choice_params(
            choice,
            decision.get("params"),
            notification,
            current_node,
            world_observations,
        )
        if social_params is not None:
            social_params_by_index[index] = social_params
            if command == "conversation_start":
                cooldown_reason = _conversation_start_cooldown_reason(
                    social_params,
                    social_memory,
                    recent_actions,
                )
                if cooldown_reason:
                    blocked_start_indices[index] = cooldown_reason
                    continue
            social_choices.append((index, choice, social_params))

    selected_social_unusable = (
        selected_command in SOCIAL_COMMANDS
        and choice_index not in social_params_by_index
    )
    if choice_index in blocked_start_indices or selected_social_unusable:
        excluded_indices = set(blocked_start_indices)
        if selected_social_unusable:
            excluded_indices.add(choice_index)
        fallback_choices: list[tuple[tuple[int, int], int, dict[str, Any]]] = []
        fallback_rank = {
            "conversation_speak": 0,
            "conversation_accept": 0,
            "conversation_join": 0,
            "conversation_stay": 0,
            "get_perception": 1,
            "get_nearby_agents": 1,
            "get_world_agents": 1,
            "get_status": 1,
            "action": 2,
            "move": 3,
            "wait": 4,
            "get_map": 5,
            "get_available_actions": 5,
        }
        for index, choice in enumerate(choices):
            if index in excluded_indices or not isinstance(choice, dict):
                continue
            command = str(choice.get("command") or "").strip()
            if command in SOCIAL_COMMANDS:
                params = social_params_by_index.get(index)
                if params is None:
                    continue
            else:
                if not _choice_is_executable(choice):
                    continue
                params = dict(choice.get("params") or {})
            fallback_choices.append(
                ((fallback_rank.get(command, 6), index), index, params)
            )
        if fallback_choices:
            _, fallback_index, fallback_params = min(fallback_choices)
            updated = dict(decision)
            updated["choice_index"] = fallback_index
            updated["params"] = fallback_params
            updated["reason"] = blocked_start_indices.get(
                choice_index,
                "会話に必要な参加者情報を確定できないため、失敗させず別の安全な行動を選ぶ。",
            )
            updated["focus"] = "相手の返答を急かさず、自分の活動を続ける"
            return updated

    reactive_social_choices = [
        item
        for item in social_choices
        if str(item[1].get("command") or "").strip()
        in {
            "conversation_speak",
            "conversation_accept",
            "conversation_join",
            "conversation_stay",
        }
    ]
    if reactive_social_choices and selected_command not in SOCIAL_COMMANDS:
        replacement_index, replacement, replacement_params = min(
            reactive_social_choices,
            key=lambda item: (_social_command_rank(str(item[1].get("command") or "")), item[0]),
        )
        replacement_command = str(replacement.get("command") or "").strip()
        updated = dict(decision)
        updated["choice_index"] = replacement_index
        updated["params"] = replacement_params
        updated["reason"] = f"会話可能な相手がいるため、{replacement_command}を優先する。"
        updated["focus"] = "相手の反応を受け止め、自然な短い会話を続ける"
        updated["rewrite_reason"] = "social_prefer"
        return updated
    follow_up_ids = _follow_up_agent_ids(social_memory)
    if (
        social_choices
        and follow_up_ids
        and selected_command not in SOCIAL_COMMANDS
        and selected_command in OBSERVE_LIKE_COMMANDS
    ):
        ranked: list[tuple[int, int, int, dict[str, Any], dict[str, Any]]] = []
        for index, choice, params in social_choices:
            target = str(
                params.get("target_agent_id")
                or params.get("next_speaker_agent_id")
                or params.get("agent_id")
                or ""
            ).strip()
            boost = 0 if target and target in follow_up_ids else 1
            ranked.append(
                (
                    boost,
                    _social_command_rank(str(choice.get("command") or "").strip()),
                    index,
                    params,
                    choice,
                )
            )
        if ranked:
            _boost, _rank, replacement_index, replacement_params, replacement = min(
                ranked
            )
            replacement_command = str(replacement.get("command") or "").strip()
            updated = dict(decision)
            updated["choice_index"] = replacement_index
            updated["params"] = replacement_params
            updated["reason"] = (
                f"follow_upがある相手との関係を育てるため、{replacement_command}を優先する。"
            )
            updated["focus"] = "前回の話の続きを自然に拾う"
            updated["rewrite_reason"] = "social_prefer"
            return updated
    if choice_index in social_params_by_index:
        updated = dict(decision)
        updated["params"] = social_params_by_index[choice_index]
        return updated
    if selected_command == "move" and not known_agent_nodes and not fresh_agent_lookup:
        for index, choice in enumerate(choices):
            if (
                isinstance(choice, dict)
                and str(choice.get("command") or "").strip() == "get_world_agents"
                and _choice_is_executable(choice)
            ):
                updated = dict(decision)
                updated["choice_index"] = index
                updated["params"] = {}
                updated["reason"] = "無目的な移動を避け、交流相手の現在位置を先に確認する。"
                updated["focus"] = "近い相手へ向かい、自然な会話機会を作る"
                return updated
    if selected_command == "move" and known_agent_nodes:
        selected_params = dict(
            selected.get("params") if isinstance(selected.get("params"), dict) else {}
        )
        proposed_params = decision.get("params")
        if isinstance(proposed_params, dict):
            for key, value in proposed_params.items():
                selected_params.setdefault(str(key), value)
        selected_target = _extract_target_node(selected_params)
        if not selected_target:
            nearest_agent = _nearest_agent_node(current_node, known_agent_nodes)
            if nearest_agent:
                updated = dict(decision)
                updated["params"] = {"target_node_id": nearest_agent}
                updated["reason"] = (
                    "位置を確認できた最寄りの他AIへ直接移動し、会話機会を作る。"
                )
                updated["focus"] = "到着後に相手の様子を見て自然に話しかける"
                return updated
        selected_distance = _nearest_agent_distance(selected_target, known_agent_nodes)
        move_choices = [
            (index, choice)
            for index, choice in enumerate(choices)
            if isinstance(choice, dict)
            and str(choice.get("command") or "").strip() == "move"
            and _choice_is_executable(choice)
            and not _choice_moves_to_current_node(choice, notification)
        ]
        if move_choices:
            replacement_index, replacement = min(
                move_choices,
                key=lambda item: (
                    _nearest_agent_distance(
                        _extract_target_node(
                            item[1].get("params")
                            if isinstance(item[1].get("params"), dict)
                            else {}
                        ),
                        known_agent_nodes,
                    ),
                    item[0],
                ),
            )
            replacement_target = _extract_target_node(
                replacement.get("params")
                if isinstance(replacement.get("params"), dict)
                else {}
            )
            replacement_distance = _nearest_agent_distance(
                replacement_target, known_agent_nodes
            )
            if replacement_distance < selected_distance:
                updated = dict(decision)
                updated["choice_index"] = replacement_index
                replacement_params = (
                    replacement.get("params")
                    if isinstance(replacement.get("params"), dict)
                    else {}
                )
                updated["params"] = _ensure_move_target_params(dict(replacement_params))
                updated["reason"] = "既知の他AIへ近づく経路を選び、無目的な移動を避ける。"
                updated["focus"] = "接近後に相手の様子を見て会話を始める"
                return updated
    if selected_command not in passive_commands:
        if selected_command != "move":
            return decision
        selected_params = (
            selected.get("params") if isinstance(selected.get("params"), dict) else {}
        )
        selected_target = _extract_target_node(selected_params)
        if selected_target in known_agent_nodes:
            return decision
        if selected_target and selected_target not in recent_route_targets[:3]:
            return decision
    if selected_command != "wait" and not passive_streak:
        return decision

    active_choices = [
        (index, choice)
        for index, choice in enumerate(choices)
        if isinstance(choice, dict)
        and index not in blocked_start_indices
        and str(choice.get("command") or "").strip()
        and str(choice.get("command") or "").strip() not in passive_commands
        and _choice_is_executable(choice)
        and not _choice_moves_to_current_node(choice, notification)
    ]
    if not active_choices:
        return decision

    visit_counts = _visit_counts(exploration_memory)

    def rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int, int, int]:
        index, choice = item
        command = str(choice.get("command") or "").strip()
        social_rank = 0 if command in SOCIAL_COMMANDS else 1
        repetition = recent_commands.count(command)
        target = _extract_target_node(
            choice.get("params") if isinstance(choice.get("params"), dict) else {}
        )
        visited = visit_counts.get(target, 0) if target else 0
        agent_rank = 0 if target and target in known_agent_nodes else 1
        recent_target_penalty = recent_route_targets[:4].count(target) if target else 0
        return (
            social_rank,
            agent_rank,
            repetition,
            recent_target_penalty,
            visited,
            index,
        )

    replacement_index, replacement = min(active_choices, key=rank)
    replacement_command = str(replacement.get("command") or "").strip()
    updated = dict(decision)
    updated["choice_index"] = replacement_index
    replacement_params = (
        replacement.get("params") if isinstance(replacement.get("params"), dict) else {}
    )
    if replacement_command == "move":
        updated["params"] = _ensure_move_target_params(dict(replacement_params))
    else:
        updated["params"] = dict(replacement_params)
    updated["reason"] = (
        f"受動行動の連続を避け、{replacement_command}で状況を前へ進める。"
    )
    updated["focus"] = "他者との会話を優先し、いなければ新しい場所を探索する"
    return updated


def _decision_system_prompt() -> str:
    return "\n".join(
        (
            "からくりワールドの次の一手だけをJSONで返す。自分の正体は定義しない。",
            '形式: {"choice_index":0,"params":{},"reason":"短文","focus":"短文"}',
            "choice_indexは候補配列の添字（整数必須）。実行可能な会話・移動・actionがあるときnull禁止。",
            "待機や観察の連打で逃げない。会話・移動・食事・探索など実行動を選ぶ。",
            "直近行動に引きずられすぎない。同じループを避け、状況に応じて柔軟に選ぶ。",
            "所持金が足りないときだけバイト。余裕があるのにバイト連打はしない。",
            "払えない食事は選ばない。会話中なら返答を優先。屋外グリッドを屋内で作らない。",
            "JSON以外禁止。",
        )
    )


def _coerce_money(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        compact = value.strip().replace(",", "").replace("円", "")
        if compact.isdigit() or (compact.startswith("-") and compact[1:].isdigit()):
            return int(compact)
    return None


def _money_from_mapping(data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in ("money", "wallet", "balance", "cash", "funds"):
        money = _coerce_money(data.get(key))
        if money is not None:
            return money
    for key in ("status", "agent", "state", "payload", "perception"):
        nested = _money_from_mapping(data.get(key))
        if nested is not None:
            return nested
    return None


def _action_entries_from_observations(world_observations: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for observation in world_observations if isinstance(world_observations, list) else []:
        if not isinstance(observation, dict):
            continue
        if str(observation.get("command") or "").strip() != "get_action_details":
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        for item in data.get("actions") or []:
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else item
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("action_id") or "").strip()
            if not action_id:
                continue
            name = str(action.get("name") or item.get("name") or action_id).strip()
            cost = _coerce_money(action.get("cost_money"))
            reward = _coerce_money(
                action.get("reward_money")
                if action.get("reward_money") is not None
                else action.get("earn_money")
            )
            looks_earn = bool(
                (reward is not None and reward > 0)
                or EARN_ACTION_HINT.search(action_id)
                or EARN_ACTION_HINT.search(name)
            )
            entries.append(
                {
                    "action_id": action_id,
                    "name": name,
                    "cost_money": cost if cost is not None else 0,
                    "reward_money": reward if reward is not None else 0,
                    "earn": looks_earn and (reward or 0) >= 0 and (cost or 0) <= 0,
                }
            )
    # Deduplicate by action_id keeping highest reward / latest.
    dedup: dict[str, dict[str, Any]] = {}
    for entry in entries:
        prior = dedup.get(entry["action_id"])
        if prior is None or int(entry["reward_money"]) >= int(prior["reward_money"]):
            dedup[entry["action_id"]] = entry
    return list(dedup.values())


def _economy_context(
    notification: dict[str, Any],
    world_observations: Any,
) -> dict[str, Any]:
    money = _money_from_mapping(notification)
    if money is None:
        for observation in world_observations if isinstance(world_observations, list) else []:
            if not isinstance(observation, dict):
                continue
            money = _money_from_mapping(observation.get("data"))
            if money is not None:
                break
    actions = _action_entries_from_observations(world_observations)
    earn = [item for item in actions if item.get("earn")]
    spend = [item for item in actions if int(item.get("cost_money") or 0) > 0]
    min_spend = min((int(item["cost_money"]) for item in spend), default=None)
    needs_income = False
    if money is not None:
        if money <= 100:
            needs_income = True
        elif min_spend is not None and money < min_spend:
            needs_income = True
        elif earn and money < 500:
            needs_income = True
    comfort_floor = max(2000, int(min_spend or 500) * 3)
    comfortable = bool(money is not None and money >= comfort_floor and not needs_income)
    prefer = ""
    if needs_income and earn:
        best = max(earn, key=lambda item: int(item.get("reward_money") or 0))
        prefer = f"action_id={best['action_id']}で稼ぐ"
    elif needs_income:
        prefer = "まず仕事内容を確認し、稼げる行動を探す"
    elif comfortable:
        prefer = "バイトせず会話・探索・食事を優先"
    return {
        "money": money,
        "needs_income": needs_income,
        "comfortable": comfortable,
        "min_spend": min_spend,
        "earn": [
            {
                "action_id": item["action_id"],
                "name": _safe_text(item["name"], 40),
                "reward_money": item["reward_money"],
            }
            for item in sorted(
                earn, key=lambda item: int(item.get("reward_money") or 0), reverse=True
            )[:4]
        ],
        "spend": [
            {
                "action_id": item["action_id"],
                "name": _safe_text(item["name"], 40),
                "cost_money": item["cost_money"],
            }
            for item in sorted(spend, key=lambda item: int(item.get("cost_money") or 0))[
                :4
            ]
        ],
        "prefer": prefer,
    }


def _best_earn_action(economy: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(economy, dict):
        return None
    earn = economy.get("earn")
    if not isinstance(earn, list) or not earn:
        return None
    return max(earn, key=lambda item: int(item.get("reward_money") or 0))


def _selected_action_id(decision: dict[str, Any], choices: list[Any]) -> str:
    params = decision.get("params") if isinstance(decision.get("params"), dict) else {}
    direct = str(params.get("action_id") or "").strip()
    if direct:
        return direct
    command = _selected_choice_command(decision, choices)
    if command != "action":
        return ""
    choice_index = decision.get("choice_index")
    if not isinstance(choice_index, int) or isinstance(choice_index, bool):
        return ""
    if choice_index < 0 or choice_index >= len(choices):
        return ""
    choice = choices[choice_index]
    if not isinstance(choice, dict):
        return ""
    choice_params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
    return str(choice_params.get("action_id") or "").strip()


def _find_action_choice_index(choices: list[Any]) -> int | None:
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict) or not _choice_is_executable(choice):
            continue
        if str(choice.get("command") or "").strip() == "action":
            return index
    return None


def _apply_survival_prefer(
    decision: dict[str, Any],
    choices: list[Any],
    economy: dict[str, Any] | None,
    *,
    hard_only: bool = False,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(economy, dict):
        return decision, None
    money = economy.get("money")
    selected_command = _selected_choice_command(decision, choices)
    selected_action_id = _selected_action_id(decision, choices)
    spend_costs = {
        str(item.get("action_id") or "").strip(): int(item.get("cost_money") or 0)
        for item in (economy.get("spend") or [])
        if isinstance(item, dict) and str(item.get("action_id") or "").strip()
    }
    unaffordable = bool(
        selected_action_id
        and money is not None
        and selected_action_id in spend_costs
        and money < spend_costs[selected_action_id]
    )
    needs_income = bool(economy.get("needs_income"))
    best_earn = _best_earn_action(economy)
    if selected_command in REACTIVE_SOCIAL_COMMANDS and not unaffordable:
        return decision, None
    # Hard safety: never execute a purchase the agent cannot pay for.
    if unaffordable and best_earn and needs_income:
        action_index = _find_action_choice_index(choices)
        if action_index is not None:
            return (
                {
                    "choice_index": action_index,
                    "params": {"action_id": best_earn["action_id"]},
                    "reason": (
                        f"所持金{money}円では払えないので、"
                        f"{best_earn.get('name') or best_earn['action_id']}で稼ぐ。"
                    ),
                    "focus": "払えない行動を避けて収入を得る",
                    "rewrite_reason": "survival_earn",
                },
                "survival_earn",
            )
    if unaffordable:
        updated = dict(decision)
        updated["choice_index"] = None
        updated["params"] = {}
        updated["reason"] = "払えない行動を避け、次の通知で稼ぎを探す。"
        updated["focus"] = "所持金に見合う行動だけ選ぶ"
        updated["rewrite_reason"] = "unaffordable"
        return updated, "unaffordable"
    if hard_only:
        # Trust the large model for ordinary earn / explore tradeoffs.
        return decision, None
    if best_earn and needs_income:
        if selected_action_id == str(best_earn.get("action_id") or "").strip():
            return decision, None
        action_index = _find_action_choice_index(choices)
        if action_index is not None:
            updated = {
                "choice_index": action_index,
                "params": {"action_id": best_earn["action_id"]},
                "reason": (
                    f"所持金{money}円では生活が苦しいので、"
                    f"{best_earn.get('name') or best_earn['action_id']}で稼ぐ。"
                ),
                "focus": "まず働いて所持金を確保する",
                "rewrite_reason": "survival_earn",
            }
            return updated, "survival_earn"
    if needs_income:
        for preferred in ("get_action_details", "get_available_actions", "get_map"):
            for index, choice in enumerate(choices):
                if not isinstance(choice, dict) or not _choice_is_executable(choice):
                    continue
                if str(choice.get("command") or "").strip() != preferred:
                    continue
                if selected_command == preferred:
                    return decision, None
                updated = {
                    "choice_index": index,
                    "params": {},
                    "reason": "所持金が足りないので、働ける行動を確認する。",
                    "focus": "稼げるactionを見つける",
                    "rewrite_reason": "survival_seek",
                }
                return updated, "survival_seek"
    return decision, None


def _looks_like_earn_action(action_id: str, economy: dict[str, Any] | None) -> bool:
    normalized = str(action_id or "").strip()
    if not normalized:
        return False
    if EARN_ACTION_HINT.search(normalized):
        return True
    if not isinstance(economy, dict):
        return False
    earn_ids = {
        str(item.get("action_id") or "").strip()
        for item in (economy.get("earn") or [])
        if isinstance(item, dict)
    }
    return normalized in earn_ids


def _recent_work_count(recent_actions: Any, *, window: int = 6) -> int:
    count = 0
    for item in (recent_actions if isinstance(recent_actions, list) else [])[:window]:
        if not isinstance(item, dict):
            continue
        if str(item.get("command") or "").strip() != "action":
            continue
        blob = " ".join(
            (
                str(item.get("reason") or ""),
                str(item.get("focus") or ""),
                str((item.get("params") or {}).get("action_id") or "")
                if isinstance(item.get("params"), dict)
                else "",
            )
        )
        if EARN_ACTION_HINT.search(blob) or "バイト" in blob or "稼" in blob:
            count += 1
    return count


def _apply_avoid_unnecessary_work(
    decision: dict[str, Any],
    choices: list[Any],
    economy: dict[str, Any] | None,
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    notification: dict[str, Any],
    social_memory: Any,
    *,
    hard_only: bool = False,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(economy, dict) or economy.get("needs_income"):
        return decision, None
    selected_action_id = _selected_action_id(decision, choices)
    if not _looks_like_earn_action(selected_action_id, economy):
        return decision, None
    comfortable = bool(economy.get("comfortable"))
    recent_work = _recent_work_count(recent_actions)
    # With a trusted LLM, only break extreme work loops.
    if hard_only:
        if not (comfortable and recent_work >= 3):
            return decision, None
    elif not comfortable and recent_work < 1:
        return decision, None
    money = economy.get("money")
    action_index = _find_action_choice_index(choices)
    if action_index is not None and money is not None:
        affordable = [
            item
            for item in (economy.get("spend") or [])
            if isinstance(item, dict)
            and int(item.get("cost_money") or 0) > 0
            and money >= int(item.get("cost_money") or 0)
        ]
        if affordable:
            meal = min(affordable, key=lambda item: int(item.get("cost_money") or 0))
            return (
                {
                    "choice_index": action_index,
                    "params": {"action_id": meal["action_id"]},
                    "reason": (
                        f"所持金{money}円あるのでバイト連打をやめ、"
                        f"{meal.get('name') or meal['action_id']}を選ぶ。"
                    ),
                    "focus": "稼いだお金で少し生活を楽しむ",
                    "rewrite_reason": "avoid_work_spam",
                },
                "avoid_work_spam",
            )
    excluded = {
        index
        for index, choice in enumerate(choices)
        if isinstance(choice, dict)
        and str(choice.get("command") or "").strip() == "action"
    }
    replacement = _deterministic_decision(
        choices,
        notification,
        recent_actions,
        exploration_memory,
        world_observations,
        excluded_indices=excluded,
        economy=economy,
    )
    replacement_index = replacement.get("choice_index")
    if (
        isinstance(replacement_index, int)
        and not isinstance(replacement_index, bool)
        and 0 <= replacement_index < len(choices)
    ):
        replacement_command = str(
            choices[replacement_index].get("command") or ""
        ).strip()
        if replacement_command == "action":
            return decision, None
        updated = _ensure_decision_text(
            replacement,
            reason="所持金に余裕があるので、バイト連打をやめて別の過ごし方を選ぶ。",
            focus="会話・探索・休息のどれかを自然に選ぶ",
        )
        updated["rewrite_reason"] = "avoid_work_spam"
        return updated, "avoid_work_spam"
    for preferred in (
        "conversation_speak",
        "conversation_start",
        "wait",
        "get_perception",
        "get_map",
    ):
        for index, choice in enumerate(choices):
            if index in excluded:
                continue
            if not isinstance(choice, dict) or not _choice_is_executable(choice):
                continue
            if str(choice.get("command") or "").strip() != preferred:
                continue
            return (
                {
                    "choice_index": index,
                    "params": {},
                    "reason": "所持金に余裕があるので、バイトを休んで別の行動にする。",
                    "focus": "生活に余裕があるときの自然な過ごし方",
                    "rewrite_reason": "avoid_work_spam",
                },
                "avoid_work_spam",
            )
    return (
        {
            "choice_index": None,
            "params": {},
            "reason": "所持金に余裕があるので、無理に働かず次の通知を待つ。",
            "focus": "バイト連打を避ける",
            "rewrite_reason": "avoid_work_spam",
        },
        "avoid_work_spam",
    )


def _slim_notification_for_decision(notification: dict[str, Any]) -> dict[str, Any]:
    choices_raw = notification.get("choices")
    slim_choices: list[dict[str, Any]] = []
    if isinstance(choices_raw, list):
        for choice in choices_raw[:28]:
            if not isinstance(choice, dict):
                slim_choices.append({"command": ""})
                continue
            command = str(choice.get("command") or "").strip()
            params_in = (
                choice.get("params") if isinstance(choice.get("params"), dict) else {}
            )
            tiny_params: dict[str, Any] = {}
            for key in (
                "target_node_id",
                "target_building_id",
                "target_npc_id",
                "target_agent_id",
                "action_id",
                "message",
                "text",
            ):
                value = params_in.get(key)
                if value is None or value == "":
                    continue
                if isinstance(value, str):
                    tiny_params[key] = value[:96]
                elif isinstance(value, (int, float, bool)):
                    tiny_params[key] = value
            entry: dict[str, Any] = {"command": command}
            if tiny_params:
                entry["params"] = tiny_params
            required = choice.get("required_params")
            if isinstance(required, list) and required:
                entry["required"] = [str(item) for item in required[:5]]
            slim_choices.append(entry)
    location = notification.get("location")
    slim_location: Any = None
    if isinstance(location, dict):
        slim_location = {
            key: location.get(key)
            for key in ("node_id", "map_id", "building_id", "label", "name")
            if location.get(key) is not None
        }
    elif isinstance(location, str):
        slim_location = location[:120]
    participants = []
    for item in (notification.get("participants") or [])[:4]:
        if not isinstance(item, dict):
            continue
        participants.append(
            {
                "agent_id": item.get("agent_id"),
                "display_name": _safe_text(item.get("display_name"), 40),
            }
        )
    return {
        "kind": notification.get("kind"),
        "summary": _safe_text(notification.get("summary"), 240),
        "perception": _safe_text(notification.get("perception"), 280),
        "location": slim_location or {},
        "participants": participants,
        "choices": slim_choices,
    }


def _bounded_decision_prompt(
    autonomy: dict[str, Any],
    notification: dict[str, Any],
    max_chars: int,
) -> str:
    recent_actions = autonomy.get("recent_actions")
    social_memory = autonomy.get("social_memory")
    exploration_memory = autonomy.get("exploration_memory")
    pressure = autonomy.get("repeat_pressure")
    if not isinstance(pressure, dict):
        pressure = _repeat_pressure(recent_actions)
    recent_compact = []
    for item in (recent_actions if isinstance(recent_actions, list) else [])[:6]:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            recent_compact.append(command)
    bounded_autonomy = {
        "enabled": autonomy.get("enabled"),
        "goal": _safe_text(autonomy.get("standing_goal"), 80),
        "social_opportunity": autonomy.get("social_opportunity"),
        "turn_count": autonomy.get("turn_count"),
        "recent": recent_compact,
        "avoid": pressure.get("avoid_commands") or [],
        "prefer_change": _safe_text(pressure.get("prefer_change"), 120),
        "economy": {
            "money": (autonomy.get("economy") or {}).get("money")
            if isinstance(autonomy.get("economy"), dict)
            else None,
            "needs_income": bool(
                (autonomy.get("economy") or {}).get("needs_income")
            )
            if isinstance(autonomy.get("economy"), dict)
            else False,
            "comfortable": bool(
                (autonomy.get("economy") or {}).get("comfortable")
            )
            if isinstance(autonomy.get("economy"), dict)
            else False,
            "prefer": _safe_text(
                (autonomy.get("economy") or {}).get("prefer"), 80
            )
            if isinstance(autonomy.get("economy"), dict)
            else "",
            "earn": (autonomy.get("economy") or {}).get("earn")[:3]
            if isinstance(autonomy.get("economy"), dict)
            and isinstance((autonomy.get("economy") or {}).get("earn"), list)
            else [],
        },
        "social": _bounded_social_memory(social_memory),
        "explore": _bounded_exploration_memory(exploration_memory),
        "obs": _bounded_world_observations(autonomy.get("world_observations")),
    }
    budget = max(400, int(max_chars))
    prompt = "\n".join(
        (
            "自律:",
            json.dumps(bounded_autonomy, ensure_ascii=False, separators=(",", ":")),
            "通知:",
            json.dumps(notification, ensure_ascii=False, separators=(",", ":")),
        )
    )
    if len(prompt) <= budget:
        return prompt
    reduced = {
        "kind": notification.get("kind"),
        "summary": _safe_text(notification.get("summary"), 160),
        "location": notification.get("location"),
        "choices": (notification.get("choices") or [])[:16]
        if isinstance(notification.get("choices"), list)
        else [],
    }
    bounded_autonomy["obs"] = []
    bounded_autonomy["explore"] = {
        "visits": (bounded_autonomy.get("explore") or {}).get("visits", [])[:4]
        if isinstance(bounded_autonomy.get("explore"), dict)
        else [],
        "recent_route": [],
    }
    return "\n".join(
        (
            "自律:",
            json.dumps(bounded_autonomy, ensure_ascii=False, separators=(",", ":")),
            "通知:",
            json.dumps(reduced, ensure_ascii=False, separators=(",", ":")),
        )
    )[:budget]


def _bounded_social_memory(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    relationships = []
    for item in (value.get("relationships") or [])[:4]:
        if not isinstance(item, dict):
            continue
        relationships.append(
            {
                "agent_id": item.get("agent_id"),
                "display_name": _safe_text(item.get("display_name"), 32),
                "affinity": item.get("affinity"),
                "follow_up": _safe_text(item.get("follow_up"), 80),
            }
        )
    episodes = []
    for item in (value.get("recent_episodes") or [])[:2]:
        if not isinstance(item, dict):
            continue
        episodes.append(
            {
                "topic": _safe_text(item.get("topic"), 40),
                "follow_up": _safe_text(item.get("follow_up"), 60),
            }
        )
    return {
        "relationships": relationships,
        "recent_episodes": episodes,
        "follow_up_agent_ids": sorted(_follow_up_agent_ids(value))[:4],
    }


def _bounded_exploration_memory(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    visits = []
    for item in (value.get("visits") or [])[:6]:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        if node_id:
            visits.append({"node": node_id, "n": int(item.get("visit_count") or 0)})
    route = []
    for item in (value.get("recent_route") or [])[:4]:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target_node_id") or "").strip()
        if target:
            route.append(target)
    return {"visits": visits, "recent_route": route}


def _bounded_world_observations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    bounded: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        encoded = json.dumps(
            item.get("data"), ensure_ascii=False, separators=(",", ":")
        )
        bounded.append(
            {
                "command": _safe_text(item.get("command"), 48),
                "data": encoded[:280],
                "age": item.get("age_seconds"),
            }
        )
    return bounded


def _choice_is_executable(choice: dict[str, Any]) -> bool:
    params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
    required = _required_param_names(choice)
    return all(name in params and params[name] is not None for name in required)


def _node_id_from_value(value: Any) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()
    if not isinstance(value, dict):
        return ""
    for key in ("node_id", "location_id", "current_node_id", "destination_node_id", "target_node_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            return str(candidate).strip()
    return ""


def _extract_current_node(notification: dict[str, Any]) -> str:
    for key in ("current_node_id", "location_id", "node_id"):
        candidate = notification.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            return str(candidate).strip()
    for key in ("location", "state", "agent"):
        candidate = _node_id_from_value(notification.get(key))
        if candidate:
            return candidate
    return ""


def _extract_target_node(params: dict[str, Any]) -> str:
    for key in ("destination_node_id", "target_node_id", "node_id", "location_id", "destination", "target", "to"):
        candidate = params.get(key)
        node_id = _node_id_from_value(candidate)
        if node_id:
            return node_id
    return ""


def _choice_moves_to_current_node(
    choice: dict[str, Any],
    notification: dict[str, Any],
) -> bool:
    if str(choice.get("command") or "").strip() != "move":
        return False
    params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
    current = _extract_current_node(notification)
    target = _extract_target_node(params)
    return bool(current and target and current == target)


def _move_params_target_current_node(
    params: dict[str, Any],
    notification: dict[str, Any],
) -> bool:
    current = _extract_current_node(notification)
    target = _extract_target_node(params)
    return bool(current and target and current == target)


def _visit_counts(exploration_memory: Any) -> dict[str, int]:
    if not isinstance(exploration_memory, dict):
        return {}
    result: dict[str, int] = {}
    for item in exploration_memory.get("visits") or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        if node_id:
            result[node_id] = int(item.get("visit_count") or 0)
    return result


def _grid_coordinates(node_id: str) -> tuple[str, int, int] | None:
    match = GRID_NODE_PATTERN.fullmatch(str(node_id or "").strip())
    if match is None:
        return None
    return match.group(1) or "", int(match.group(2)), int(match.group(3))


def _nearest_agent_distance(node_id: str, agent_nodes: set[str]) -> int:
    source = _grid_coordinates(node_id)
    if source is None or not agent_nodes:
        return 1_000_000
    distances = []
    for agent_node in agent_nodes:
        target = _grid_coordinates(agent_node)
        if target is not None and source[0] == target[0]:
            distances.append(abs(source[1] - target[1]) + abs(source[2] - target[2]))
    return min(distances) if distances else 1_000_000


def _same_map_nodes(left: str, right: str) -> bool:
    source = _grid_coordinates(left)
    target = _grid_coordinates(right)
    if source is None or target is None:
        return False
    return source[0] == target[0]


def _nodes_on_same_map(current_node: str, nodes: set[str]) -> set[str]:
    if not current_node:
        return set(nodes)
    return {node for node in nodes if _same_map_nodes(current_node, node)}


def _nearest_agent_node(current_node: str, agent_nodes: set[str]) -> str:
    same_map = _nodes_on_same_map(current_node, agent_nodes)
    source = _grid_coordinates(current_node)
    candidates = []
    for agent_node in same_map:
        target = _grid_coordinates(agent_node)
        if target is None:
            continue
        distance = (
            abs(source[1] - target[1]) + abs(source[2] - target[2])
            if source is not None
            else 1_000_000
        )
        candidates.append((distance, agent_node))
    return min(candidates)[1] if candidates else ""


def _infer_current_node(
    notification: dict[str, Any],
    exploration_memory: Any,
    world_observations: Any,
) -> str:
    current = _extract_current_node(notification)
    if current:
        return current
    if isinstance(exploration_memory, dict):
        for item in exploration_memory.get("recent_route") or []:
            if not isinstance(item, dict):
                continue
            if item.get("command") != "move" or item.get("outcome") != "acted":
                continue
            target = str(item.get("target_node_id") or "").strip()
            if target:
                return target
    for observation in world_observations if isinstance(world_observations, list) else []:
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        for key in ("current_node_id", "node_id", "location_id"):
            value = data.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value).strip()
        for key in ("current_node", "location", "state"):
            value = _node_id_from_value(data.get(key))
            if value:
                return value
    return ""


def _social_command_rank(command: str) -> int:
    priorities = {
        "conversation_speak": 0,
        "conversation_accept": 1,
        "conversation_join": 2,
        "conversation_stay": 3,
        "conversation_start": 4,
        "conversation_leave": 5,
        "conversation_end": 6,
        "conversation_reject": 7,
    }
    return priorities.get(str(command or "").strip(), 99)


def _social_choice_params(
    choice: dict[str, Any],
    proposed: Any,
    notification: dict[str, Any],
    current_node: str,
    world_observations: Any,
) -> dict[str, Any] | None:
    command = str(choice.get("command") or "").strip()
    params = dict(choice.get("params") or {}) if isinstance(choice.get("params"), dict) else {}
    required = _required_param_names(choice)
    schema = choice.get("param_schema") if isinstance(choice.get("param_schema"), dict) else {}
    allowed = required | {str(key) for key in schema.keys()}
    if isinstance(proposed, dict):
        for key, value in proposed.items():
            normalized = str(key)
            if normalized in allowed and normalized not in params:
                params[normalized] = value

    if "target_agent_id" in required and not params.get("target_agent_id"):
        target_id, target_name = _nearest_agent_identity(
            current_node, world_observations
        )
        if target_id:
            params["target_agent_id"] = target_id
            params.setdefault("_target_name", target_name)

    if "message" in required and not str(params.get("message") or "").strip():
        target_name = str(params.pop("_target_name", "") or "").strip()
        if command == "conversation_start":
            prefix = f"{target_name}さん、" if target_name else ""
            params["message"] = (
                prefix
                + "こんにちは。近くに来たので声をかけてみました。今は何をしていたんですか？"
            )
        elif command in {"conversation_accept", "conversation_join"}:
            params["message"] = "声をかけてくれてありがとう。ぜひ少しお話ししたいです。"
        else:
            params["message"] = "その話、もう少し聞いてみたいです。どんなところが印象に残りましたか？"
    params.pop("_target_name", None)

    if "conversation_id" in required and not params.get("conversation_id"):
        for container in (
            notification,
            notification.get("conversation"),
            notification.get("conversation_context"),
            notification.get("payload"),
        ):
            if not isinstance(container, dict):
                continue
            value = container.get("conversation_id") or container.get("id")
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                params["conversation_id"] = str(value)
                break

    if "next_speaker_agent_id" in required and not params.get(
        "next_speaker_agent_id"
    ):
        next_speaker_id = str(params.get("target_agent_id") or "").strip()
        if not next_speaker_id:
            next_speaker_id = _other_conversation_agent_id(notification)
        if not next_speaker_id:
            next_speaker_id, _ = _nearest_agent_identity(
                current_node, world_observations
            )
        if next_speaker_id:
            params["next_speaker_agent_id"] = next_speaker_id

    missing = [
        name for name in sorted(required) if name not in params or params[name] in {None, ""}
    ]
    return None if missing else params


def _conversation_start_cooldown_reason(
    params: dict[str, Any],
    social_memory: Any,
    recent_actions: Any,
) -> str:
    target_agent_id = str(params.get("target_agent_id") or "").strip()
    if not target_agent_id:
        return ""
    attempts = (
        social_memory.get("recent_contact_attempts")
        if isinstance(social_memory, dict)
        else []
    )
    start_ages: list[float] = []
    for attempt in attempts if isinstance(attempts, list) else []:
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("target_agent_id") or "").strip() != target_agent_id:
            continue
        if str(attempt.get("command") or "").strip() != "conversation_start":
            break
        try:
            age_minutes = max(
                0.0, float(attempt.get("minutes_since_action") or 0.0)
            )
        except (TypeError, ValueError):
            continue
        start_ages.append(age_minutes)
    if not start_ages:
        return ""
    latest_age = min(start_ages)
    if latest_age < 12:
        return "同じ相手への直前の呼びかけから12分以内のため、連続送信を避ける。"
    if sum(age < 60 for age in start_ages) >= 2 and latest_age < 30:
        return "同じ相手へ1時間以内に複数回呼びかけたため、30分の間隔を確保する。"
    if sum(age < 6 * 60 for age in start_ages) >= 3 and latest_age < 2 * 60:
        return "同じ相手への呼びかけが集中したため、2時間は別の相手や活動を選ぶ。"
    return ""


def _other_conversation_agent_id(notification: dict[str, Any]) -> str:
    candidates: list[str] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, list):
            for item in value[:40]:
                walk(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        display_name = str(
            value.get("agent_name")
            or value.get("display_name")
            or value.get("name")
            or ""
        ).strip()
        if display_name.casefold() != "lumina":
            for key in (
                "agent_id",
                "speaker_agent_id",
                "participant_agent_id",
                "target_agent_id",
                "id",
            ):
                agent_id = value.get(key)
                if isinstance(agent_id, (str, int)) and not isinstance(agent_id, bool):
                    normalized = str(agent_id).strip()
                    if normalized:
                        candidates.append(normalized)
                        break
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, depth + 1)

    for key in (
        "conversation",
        "conversation_context",
        "participants",
        "speaker",
        "agent",
        "payload",
    ):
        walk(notification.get(key))
    return candidates[0] if candidates else ""


def _nearest_agent_identity(
    current_node: str, world_observations: Any
) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    source = _grid_coordinates(current_node)

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, list):
            for child in value[:80]:
                walk(child, depth + 1)
            return
        if not isinstance(value, dict):
            return
        agent_id = value.get("agent_id")
        agent_name = str(value.get("agent_name") or value.get("display_name") or "").strip()
        node_id = ""
        for key in ("node_id", "location_id", "current_node_id", "position", "location"):
            node_id = _node_id_from_value(value.get(key))
            if node_id:
                break
        if (
            isinstance(agent_id, (str, int))
            and not isinstance(agent_id, bool)
            and node_id
            and agent_name.casefold() != "lumina"
        ):
            target = _grid_coordinates(node_id)
            if source is not None and target is not None and source[0] == target[0]:
                distance = abs(source[1] - target[1]) + abs(source[2] - target[2])
                if distance <= 1:
                    candidates.append((distance, str(agent_id), agent_name))
            elif node_id == current_node:
                candidates.append((0, str(agent_id), agent_name))
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, depth + 1)

    for observation in world_observations if isinstance(world_observations, list) else []:
        if isinstance(observation, dict):
            walk(observation.get("data"))
    if not candidates:
        return "", ""
    _, agent_id, agent_name = min(candidates)
    return agent_id, agent_name


def _recent_route_targets(exploration_memory: Any) -> list[str]:
    if not isinstance(exploration_memory, dict):
        return []
    return [
        str(item.get("target_node_id") or "").strip()
        for item in exploration_memory.get("recent_route") or []
        if isinstance(item, dict) and str(item.get("target_node_id") or "").strip()
    ]


def _agent_nodes_from_observations(observations: Any) -> set[str]:
    result: set[str] = set()

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, list):
            for item in value[:50]:
                walk(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        keys = {str(key).casefold() for key in value.keys()}
        looks_like_agent = bool(
            keys
            & {
                "agent_id",
                "agent_name",
                "display_name",
                "username",
                "npc_id",
            }
        )
        if looks_like_agent:
            for key in ("node_id", "location_id", "current_node_id", "position", "location"):
                node_id = _node_id_from_value(value.get(key))
                if node_id:
                    result.add(node_id)
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, depth + 1)

    for observation in observations if isinstance(observations, list) else []:
        if isinstance(observation, dict):
            try:
                age_seconds = float(observation.get("age_seconds") or 0)
            except (TypeError, ValueError):
                age_seconds = 0
            if age_seconds > 900:
                continue
            walk(observation.get("data"))
    return result


def _deterministic_decision(
    choices: list[Any],
    notification: dict[str, Any],
    recent_actions: Any,
    exploration_memory: Any,
    world_observations: Any,
    excluded_indices: set[int] | None = None,
    economy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent_commands = [
        str(item.get("command") or "").strip()
        for item in (recent_actions if isinstance(recent_actions, list) else [])[:5]
        if isinstance(item, dict)
    ]
    pressure = _repeat_pressure(recent_actions)
    economy = economy or _economy_context(notification, world_observations)
    best_earn = _best_earn_action(economy)
    needs_income = bool(economy.get("needs_income"))
    visit_counts = _visit_counts(exploration_memory)
    recent_route_targets = _recent_route_targets(exploration_memory)
    known_agent_nodes = _agent_nodes_from_observations(world_observations)
    current_node = _infer_current_node(
        notification, exploration_memory, world_observations
    )
    if current_node:
        known_agent_nodes.discard(current_node)
    known_agent_nodes = _nodes_on_same_map(current_node, known_agent_nodes)
    fresh_agent_lookup = any(
        isinstance(item, dict)
        and item.get("command") == "get_world_agents"
        and float(item.get("age_seconds") or 0) < 300
        for item in (world_observations if isinstance(world_observations, list) else [])
    )
    excluded = excluded_indices or set()
    observe_spam = int(pressure.get("observe_family_short") or 0) >= 3 or bool(
        pressure.get("high")
    )
    if needs_income and best_earn:
        action_index = _find_action_choice_index(choices)
        if action_index is not None and action_index not in excluded:
            return {
                "choice_index": action_index,
                "params": {"action_id": best_earn["action_id"]},
                "reason": (
                    f"所持金が足りないため、{best_earn.get('name') or best_earn['action_id']}"
                    "で稼ぐ。"
                ),
                "focus": "まず働いて所持金を確保する",
                "rewrite_reason": "survival_earn",
            }
    candidates: list[
        tuple[tuple[int, int, int, int, int, int, int], int, dict[str, Any]]
    ] = []
    for index, choice in enumerate(choices):
        if index in excluded:
            continue
        if not isinstance(choice, dict) or not _choice_is_executable(choice):
            continue
        if _choice_moves_to_current_node(choice, notification):
            continue
        command = str(choice.get("command") or "").strip()
        if not command:
            continue
        params = choice.get("params")
        if not isinstance(params, dict):
            params = {}
        target = _extract_target_node(params)
        if command == "move" and current_node and target and not _same_map_nodes(
            current_node, target
        ):
            continue
        if needs_income and command in REACTIVE_SOCIAL_COMMANDS:
            category = 0
        elif needs_income and command == "action" and best_earn:
            category = 0
        elif needs_income and command == "get_action_details":
            category = 0
        elif command in SOCIAL_COMMANDS:
            category = 1 if needs_income else 0
        elif command == "get_world_agents" and not fresh_agent_lookup and not observe_spam:
            category = 2 if needs_income else 1
        elif command == "move" and target in known_agent_nodes:
            category = 2 if needs_income else 1
        elif command == "move":
            # Skip unresolved move shells; they fail param validation later.
            if not target and not known_agent_nodes:
                continue
            category = 3 if needs_income else 2
        elif command == "wait" and observe_spam:
            category = 3
        elif _is_observe_family(command):
            category = 6 if (observe_spam or needs_income) else 4
        elif command not in {"wait", "get_map", "get_available_actions"}:
            category = 3
        else:
            category = 5
        candidates.append(
            (
                (
                    category,
                    _social_command_rank(command) if command in SOCIAL_COMMANDS else 99,
                    _nearest_agent_distance(target, known_agent_nodes)
                    if command == "move"
                    else 1_000_000,
                    0 if target and target in known_agent_nodes else 1,
                    recent_commands.count(command),
                    recent_route_targets[:4].count(target) if target else 0,
                    visit_counts.get(target, 0) if target else 0,
                ),
                index,
                choice,
            )
        )
    if not candidates:
        return {
            "choice_index": None,
            "params": {},
            "reason": "安全に実行できる候補がないため、次の通知を待つ。",
            "focus": "実行可能な会話または移動候補",
        }
    _, index, choice = min(candidates, key=lambda item: item[0])
    command = str(choice.get("command") or "").strip()
    params: dict[str, Any] = {}
    if command == "action" and best_earn and needs_income:
        params["action_id"] = best_earn["action_id"]
        return {
            "choice_index": index,
            "params": params,
            "reason": (
                f"所持金が足りないため、{best_earn.get('name') or best_earn['action_id']}"
                "で稼ぐ。"
            ),
            "focus": "まず働いて所持金を確保する",
            "rewrite_reason": "survival_earn",
        }
    if command == "move":
        choice_params = choice.get("params")
        if isinstance(choice_params, dict):
            params.update(choice_params)
        if not _extract_target_node(params):
            nearest_agent = _nearest_agent_node(current_node, known_agent_nodes)
            if nearest_agent:
                params["target_node_id"] = nearest_agent
        params = _ensure_move_target_params(params, params)
        target = _extract_target_node(params)
        if current_node and target and not _same_map_nodes(current_node, target):
            params.pop("target_node_id", None)
        if not any(
            str(params.get(key) or "").strip()
            for key in ("target_node_id", "target_building_id", "target_npc_id")
        ):
            # Prefer social/action over wait/null when move target is unresolved.
            alt_excluded = set(excluded)
            alt_excluded.add(index)
            alt_index = _best_actionable_index(
                choices,
                excluded_indices=alt_excluded,
                allow_observe=False,
                allow_wait=False,
                notification=notification,
                world_observations=world_observations,
                exploration_memory=exploration_memory,
            )
            if alt_index is None:
                alt_index = _best_actionable_index(
                    choices,
                    excluded_indices=alt_excluded,
                    allow_observe=True,
                    allow_wait=False,
                    notification=notification,
                    world_observations=world_observations,
                    exploration_memory=exploration_memory,
                )
            if alt_index is not None:
                alt_choice = choices[alt_index]
                alt_params = (
                    dict(alt_choice.get("params") or {})
                    if isinstance(alt_choice.get("params"), dict)
                    else {}
                )
                return {
                    "choice_index": alt_index,
                    "params": alt_params,
                    "reason": "移動先が未確定のため、会話や行動など別の実行可能候補へ切り替える。",
                    "focus": "失敗する移動より実行動を優先する",
                    "rewrite_reason": "move_target_unresolved",
                }
            return {
                "choice_index": None,
                "params": {},
                "reason": "移動先が未確定のため、次の通知を待つ。",
                "focus": "位置確認後に移動する",
                "rewrite_reason": "move_target_unresolved",
            }
    return {
        "choice_index": index,
        "params": params,
        "reason": f"判断フォールバックにより{command}を安全候補として選択した。",
        "focus": "会話を優先し、未訪問地点を探索する",
    }


def _required_param_names(choice: dict[str, Any]) -> set[str]:
    required = choice.get("required_params")
    if isinstance(required, list):
        return {str(item) for item in required if str(item)}
    if isinstance(required, dict):
        return {str(key) for key in required.keys()}
    schema = choice.get("param_schema")
    if isinstance(schema, dict) and isinstance(schema.get("required"), list):
        return {str(item) for item in schema["required"] if str(item)}
    return set()


def _merge_and_validate_params(choice: dict[str, Any], proposed: Any) -> dict[str, Any]:
    base = choice.get("params")
    params = dict(base) if isinstance(base, dict) else {}
    proposed_params = proposed if isinstance(proposed, dict) else {}
    required = _required_param_names(choice)
    schema = choice.get("param_schema") if isinstance(choice.get("param_schema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if not properties:
        properties = {
            str(key): value
            for key, value in schema.items()
            if isinstance(value, dict) and key not in {"required", "description", "type"}
        }
    allowed = set(params.keys()) | required | {str(key) for key in properties.keys()}

    for key, value in proposed_params.items():
        normalized_key = str(key)
        if normalized_key not in allowed:
            # The selected choice and its schema are authoritative. Local
            # models occasionally echo command/reason metadata inside params;
            # discard it instead of failing an otherwise valid world turn.
            continue
        # Proposed decision params must be able to override choice defaults
        # (e.g. survival rewrite: work-* instead of default eat-*).
        params[normalized_key] = value

    command = str(choice.get("command") or "").strip()
    if command == "move":
        params = _ensure_move_target_params(params, proposed_params)
    if command == "action":
        proposed_action_id = ""
        if isinstance(proposed_params, dict):
            proposed_action_id = str(proposed_params.get("action_id") or "").strip()
        if proposed_action_id:
            params["action_id"] = proposed_action_id
    if command == "wait":
        # World wait choices often require duration; fill a short default so
        # autonomy can clear the notification instead of stalling.
        if "duration" in required and params.get("duration") in {None, ""}:
            params["duration"] = 30
        elif "duration" not in params and "duration" in allowed:
            params["duration"] = 30

    constraints = (
        choice.get("param_constraints")
        if isinstance(choice.get("param_constraints"), dict)
        else {}
    )
    exactly_one = constraints.get("exactly_one_of")
    if isinstance(exactly_one, list) and exactly_one:
        present = [
            str(key)
            for key in exactly_one
            if str(key) in params and params[str(key)] not in {None, ""}
        ]
        if len(present) != 1:
            # Last chance: promote a destination-like alias into the first
            # exactly_one slot (usually target_node_id).
            if command == "move":
                params = _ensure_move_target_params(params, proposed_params)
                present = [
                    str(key)
                    for key in exactly_one
                    if str(key) in params and params[str(key)] not in {None, ""}
                ]
            if len(present) != 1:
                raise KarakuriWorldError(
                    "exactly one target param is required: "
                    + ", ".join(str(key) for key in exactly_one)
                )

    missing = [key for key in sorted(required) if key not in params or params[key] is None]
    if missing:
        raise KarakuriWorldError("required params are missing: " + ", ".join(missing))
    for key, rule in properties.items():
        if key in params and isinstance(rule, dict):
            _validate_schema_value(str(key), params[key], rule)
    return params


def _ensure_move_target_params(
    params: dict[str, Any],
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = dict(params)
    target_keys = ("target_node_id", "target_building_id", "target_npc_id")
    if any(str(updated.get(key) or "").strip() for key in target_keys):
        return updated
    merged = dict(updated)
    if isinstance(proposed, dict):
        for key, value in proposed.items():
            merged.setdefault(str(key), value)
    node = _extract_target_node(merged)
    if node:
        updated["target_node_id"] = node
        return updated
    for key in ("target_building_id", "building_id"):
        value = merged.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            updated["target_building_id"] = str(value).strip()
            return updated
    for key in ("target_npc_id", "npc_id"):
        value = merged.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            updated["target_npc_id"] = str(value).strip()
            return updated
    return updated


def _validate_schema_value(name: str, value: Any, rule: dict[str, Any]) -> None:
    expected = rule.get("type")
    valid = True
    if expected == "string":
        valid = isinstance(value, str)
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "boolean":
        valid = isinstance(value, bool)
    elif expected == "array":
        valid = isinstance(value, list)
    elif expected == "object":
        valid = isinstance(value, dict)
    if not valid:
        raise KarakuriWorldError(f"param {name} does not match type {expected}")
    enum = rule.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise KarakuriWorldError(f"param {name} is outside its enum")
    if isinstance(value, str):
        max_length = rule.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            raise KarakuriWorldError(f"param {name} exceeds maxLength")


def _safe_reason(value: Any) -> str:
    return " ".join(str(value or "").split())[:300]


def create_karakuri_world_router(orchestrator: LuminaOrchestrator) -> APIRouter:
    config = KarakuriWorldConfig.from_env(orchestrator.config.runtime_root)
    service = LuminaKarakuriWorldService(config, orchestrator)
    router = APIRouter(prefix="/integrations/karakuri", tags=["karakuri-world"])

    @router.get("/health")
    async def karakuri_health() -> dict[str, Any]:
        return service.health()

    @router.post("/webhook")
    async def karakuri_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> Response:
        raw_body = await request.body()
        try:
            notification_id, claimed = service.accept_webhook(
                raw_body=raw_body,
                authorization=str(request.headers.get("Authorization") or ""),
                signature=str(request.headers.get("X-Webhook-Signature") or ""),
                request_id=str(request.headers.get("X-Request-ID") or ""),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except KarakuriWorldError as exc:
            status = 503 if not config.ready else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        if claimed:
            background_tasks.add_task(service.process_notification, notification_id)
        return Response(status_code=200)

    setattr(router, "karakuri_service", service)
    return router
