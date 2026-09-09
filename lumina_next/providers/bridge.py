from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from .llm import ProviderError
from .tts import split_tts_chunks


class GodotBridgeProvider:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise ProviderError(f"bridge {method} {path} failed: {exc}") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"bridge {path} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError(f"bridge {path} returned a non-object JSON payload")
        return value

    async def health(self) -> dict[str, Any]:
        try:
            payload = await asyncio.to_thread(self._request_json, "GET", "/health", None, 5.0)
        except ProviderError as exc:
            return {"ok": False, "base_url": self.base_url, "error": str(exc)}
        return {
            "ok": payload.get("status") == "ok",
            "base_url": self.base_url,
            "godot_connected": bool(payload.get("godot_connected")),
            "pending_commands": int(payload.get("pending_commands") or 0),
            "runtime": payload.get("runtime") or {},
            "world_state": payload.get("world_state") or {},
            "autonomy_state": payload.get("autonomy_state") or {},
            "last_world_state_at": payload.get("last_world_state_at"),
        }

    async def dispatch_speak(self, text: str, emotion: str = "neutral", speed: float = 1.0) -> dict[str, Any]:
        health = await self.health()
        if not health.get("ok"):
            raise ProviderError(str(health.get("error") or "Godot bridge is unavailable"))
        connected = bool(health.get("godot_connected"))
        chunks = split_tts_chunks(text, max_chars=80)
        if not chunks:
            raise ProviderError("bridge received no speakable text")
        spoken_text = chunks[0]
        command = {
            "actor_id": "toha",
            "action": "speak",
            "params": {
                "text": spoken_text,
                "emotion": str(emotion or "neutral"),
                "speed": max(0.75, min(1.35, float(speed))),
            },
            "priority": 5,
            "reason": "lumina-next-orchestrator",
            "expires_in_ms": 30000,
        }
        path = "/command" if connected else "/validate-command"
        result = await asyncio.to_thread(self._request_json, "POST", path, command, self.timeout_seconds)
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "godot_connected": bool(result.get("godot_connected")),
            "sent": connected and result.get("status") == "sent",
            "validated_only": not connected,
            "detail": result.get("detail"),
            "text": spoken_text,
            "chunks_total": len(chunks),
            "deferred": len(chunks) > 1,
        }

    # Bounded body-action handoff for the dormant Autonomy API. The product
    # profile cannot reach this path while its supervisor is hard-disabled.
    async def dispatch_action(
        self,
        action: str,
        *,
        target_node: str | None = None,
        position: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        reason: str = "lumina-autonomy",
    ) -> dict[str, Any]:
        """Send one already-decided bounded body action to the Godot bridge."""
        allowed = {
            "move_to_position",
            "move_to_node",
            "look_at_position",
            "look_at_node",
            "look_at_user",
            "gesture",
            "wait",
        }
        normalized = str(action or "").strip()
        if normalized not in allowed:
            raise ProviderError(f"unsupported autonomous bridge action: {normalized}")
        health = await self.health()
        if not health.get("ok"):
            raise ProviderError(str(health.get("error") or "Godot bridge is unavailable"))
        command: dict[str, Any] = {
            "actor_id": "toha",
            "action": normalized,
            "params": dict(params or {}),
            "priority": 4,
            "reason": str(reason or "lumina-autonomy")[:500],
            "expires_in_ms": 30000,
        }
        if target_node:
            command["target_node"] = str(target_node)[:200]
        if isinstance(position, dict):
            command["position"] = {
                "x": float(position.get("x", 0.0)),
                "y": float(position.get("y", 0.0)),
                "z": float(position.get("z", 0.0)),
            }
        connected = bool(health.get("godot_connected"))
        path = "/command" if connected else "/validate-command"
        result = await asyncio.to_thread(
            self._request_json, "POST", path, command, self.timeout_seconds
        )
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "godot_connected": connected,
            "sent": connected and result.get("status") == "sent",
            "validated_only": not connected,
            "detail": result.get("detail"),
            "action": normalized,
        }

    async def stop_audio(self) -> dict[str, Any]:
        health = await self.health()
        if not health.get("ok"):
            raise ProviderError(str(health.get("error") or "Godot bridge is unavailable"))
        connected = bool(health.get("godot_connected"))
        command = {
            "actor_id": "toha",
            "action": "stop_audio",
            "params": {},
            "priority": 10,
            "reason": "lumina-next-barge-in",
            "expires_in_ms": 3000,
        }
        path = "/command" if connected else "/validate-command"
        result = await asyncio.to_thread(self._request_json, "POST", path, command, self.timeout_seconds)
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "godot_connected": bool(result.get("godot_connected")),
            "sent": connected and result.get("status") == "sent",
            "validated_only": not connected,
            "detail": result.get("detail"),
        }
