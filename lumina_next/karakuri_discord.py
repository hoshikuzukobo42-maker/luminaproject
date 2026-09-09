from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter

try:
    import discord
except ImportError:  # The integration remains disabled until discord.py is installed.
    discord = None


LOGGER = logging.getLogger(__name__)
NOTIFICATION_ID_PATTERN = re.compile(
    r"\bnotification_id\s*[:=]\s*`?(notif-[A-Za-z0-9_-]+)`?",
    re.IGNORECASE,
)


def _optional_snowflake(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        LOGGER.error("%s must be a Discord numeric ID", name)
        return None


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class KarakuriDiscordConfig:
    token: str
    guild_id: int | None
    channel_id: int | None
    notification_author_id: int | None
    cleanup_processed: bool

    @classmethod
    def from_env(cls) -> "KarakuriDiscordConfig":
        return cls(
            token=os.getenv("KARAKURI_DISCORD_BOT_TOKEN", "").strip(),
            guild_id=_optional_snowflake("KARAKURI_DISCORD_GUILD_ID"),
            channel_id=_optional_snowflake("KARAKURI_DISCORD_CHANNEL_ID"),
            notification_author_id=_optional_snowflake(
                "KARAKURI_DISCORD_NOTIFICATION_AUTHOR_ID"
            ),
            # Karakuri's Discord channel is also user-visible conversation
            # history. Process notifications without deleting source messages.
            cleanup_processed=False,
        )


class KarakuriDiscordBridge:
    """Receive Karakuri Discord notifications and hand them to Lumina once."""

    def __init__(self, service: Any, config: KarakuriDiscordConfig | None = None) -> None:
        self.service = service
        self.config = config or KarakuriDiscordConfig.from_env()
        self._client: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._retry_tasks: set[asyncio.Task[None]] = set()
        self._notification_queue: asyncio.Queue[tuple[Any, str, str]] = asyncio.Queue(
            maxsize=64
        )
        self._queued_notification_ids: set[str] = set()
        self._last_notification_id: str | None = None
        self._last_error: str | None = None
        self._last_processed_at: float | None = None
        self._last_cleanup_error: str | None = None
        self._can_manage_messages: bool | None = None

    async def start(self) -> None:
        if not self.config.token or self._task is not None:
            return
        if discord is None:
            self._last_error = "discord.py is not installed"
            LOGGER.error(self._last_error)
            return

        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._client.event(self.on_ready)
        self._client.event(self.on_message)
        self._worker_task = asyncio.create_task(
            self._notification_worker(), name="lumina-karakuri-notification-worker"
        )
        self._task = asyncio.create_task(
            self._run_client(), name="lumina-karakuri-discord"
        )

    async def stop(self) -> None:
        if self._client is not None and not self._client.is_closed():
            await self._client.close()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        for task in tuple(self._retry_tasks):
            task.cancel()
        if self._retry_tasks:
            await asyncio.gather(*self._retry_tasks, return_exceptions=True)
        self._retry_tasks.clear()

    async def _run_client(self) -> None:
        try:
            await self._client.start(self.config.token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Karakuri Discord bridge stopped")

    async def on_ready(self) -> None:
        self._last_error = None
        LOGGER.info("Karakuri Discord bridge connected as %s", self._client.user)
        await self._backfill_recent_notifications()

    async def on_message(self, message: Any) -> None:
        if self._client.user is not None and message.author.id == self._client.user.id:
            return
        if message.guild is None:
            return
        if self.config.guild_id is not None and message.guild.id != self.config.guild_id:
            return
        if (
            self.config.channel_id is not None
            and message.channel.id != self.config.channel_id
        ):
            return
        if (
            self.config.notification_author_id is not None
            and message.author.id != self.config.notification_author_id
        ):
            return

        notification_id = self._extract_notification_id(message)
        if notification_id is None:
            return

        delivery_id = f"discord:{message.guild.id}:{message.channel.id}:{message.id}"
        if notification_id in self._queued_notification_ids:
            return
        if self._notification_queue.full():
            self._last_error = "notification queue is full"
            try:
                await message.add_reaction("⚠️")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return
        self._queued_notification_ids.add(notification_id)
        await self._notification_queue.put((message, notification_id, delivery_id))

    async def _notification_worker(self) -> None:
        while True:
            message, notification_id, delivery_id = await self._notification_queue.get()
            try:
                await self._process_queued_message(
                    message, notification_id, delivery_id
                )
            finally:
                self._queued_notification_ids.discard(notification_id)
                self._notification_queue.task_done()

    async def _process_queued_message(
        self,
        message: Any,
        notification_id: str,
        delivery_id: str,
    ) -> None:
        try:
            claimed = self.service.ledger.claim(delivery_id, notification_id)
            if inspect.isawaitable(claimed):
                claimed = await claimed
            if not claimed:
                await self._finalize_message(message, notification_id)
                return

            result = self.service.process_notification(notification_id)
            if inspect.isawaitable(result):
                await result
            retry = self.service.ledger.retry_info(notification_id)
            if retry.get("eligible"):
                self._schedule_retry(message, notification_id)
            await self._finalize_message(message, notification_id)
            self._last_notification_id = notification_id
            self._last_processed_at = time.time()
            self._last_error = None
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception(
                "Failed to process Karakuri notification %s", notification_id
            )

    def _schedule_retry(self, message: Any, notification_id: str) -> None:
        if any(not task.done() and task.get_name().endswith(notification_id) for task in self._retry_tasks):
            return
        task = asyncio.create_task(
            self._retry_after_delay(message, notification_id),
            name=f"lumina-karakuri-retry-{notification_id}",
        )
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)

    async def _retry_after_delay(self, message: Any, notification_id: str) -> None:
        retry = self.service.ledger.retry_info(notification_id)
        if not retry.get("eligible"):
            return
        await asyncio.sleep(max(0.25, float(retry.get("retry_after_seconds") or 0.0)))
        if notification_id in self._queued_notification_ids:
            return
        self._queued_notification_ids.add(notification_id)
        delivery_id = f"discord-retry:{notification_id}:{time.time_ns()}"
        await self._notification_queue.put((message, notification_id, delivery_id))

    async def _finalize_message(self, message: Any, notification_id: str) -> None:
        status = self.service.ledger.delivery_status(notification_id)
        self._can_manage_messages = None
        self._last_cleanup_error = None
        retry = self.service.ledger.retry_info(notification_id)
        reaction = (
            "✅"
            if status == "acted"
            else "⏳"
            if retry.get("eligible")
            else "❌"
            if status in {"failed", "rejected"}
            else "⏳"
        )
        try:
            await message.add_reaction(reaction)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.debug(
                "Could not mark Karakuri notification %s with %s",
                notification_id,
                reaction,
            )

    async def _backfill_recent_notifications(self) -> None:
        if self.config.channel_id is None:
            return
        try:
            channel = self._client.get_channel(self.config.channel_id)
            if channel is None:
                channel = await self._client.fetch_channel(self.config.channel_id)
            messages = [message async for message in channel.history(limit=10)]
            for message in reversed(messages):
                await self.on_message(message)
        except Exception as exc:
            self._last_error = f"backfill {type(exc).__name__}: {exc}"
            LOGGER.exception("Failed to backfill Karakuri Discord notifications")

    @staticmethod
    def _extract_notification_id(message: Any) -> str | None:
        parts = [message.content or ""]
        for embed in message.embeds:
            parts.extend([embed.title or "", embed.description or ""])
            for field in embed.fields:
                parts.extend([field.name or "", field.value or ""])
        match = NOTIFICATION_ID_PATTERN.search("\n".join(parts))
        return match.group(1) if match else None

    def health(self) -> dict[str, Any]:
        connected = bool(self._client is not None and self._client.is_ready())
        return {
            "enabled": bool(self.config.token),
            "dependency_ready": discord is not None,
            "connected": connected,
            "guild_restricted": self.config.guild_id is not None,
            "channel_restricted": self.config.channel_id is not None,
            "author_restricted": self.config.notification_author_id is not None,
            "cleanup_processed": self.config.cleanup_processed,
            "can_manage_messages": self._can_manage_messages,
            "queue_depth": self._notification_queue.qsize(),
            "queue_capacity": self._notification_queue.maxsize,
            "worker_running": bool(
                self._worker_task is not None and not self._worker_task.done()
            ),
            "retry_tasks": len(self._retry_tasks),
            "last_processed_at": self._last_processed_at,
            "last_cleanup_error": self._last_cleanup_error,
            "last_notification_id": self._last_notification_id,
            "last_error": self._last_error,
        }


def create_karakuri_discord_router(bridge: KarakuriDiscordBridge) -> APIRouter:
    router = APIRouter(prefix="/integrations/karakuri/discord", tags=["karakuri"])

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return bridge.health()

    return router
