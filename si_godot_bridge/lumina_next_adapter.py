from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from si_godot_bridge.autonomy_llm import VisitorReplyResult


def visual_navigation_requested(text: str) -> bool:
    # Opt-in route replaces legacy label-only action handling, not normal chat.
    if os.environ.get("LUMINA_VISUAL_NAVIGATION", "0") != "1":
        return False
    from lumina_next.visual_navigation import relevant
    return relevant(text)


def request_visual_navigation(*, text: str, request_id: str, actor_id: str, send: bool) -> dict[str, Any]:
    from urllib.parse import urlparse
    url = lumina_next_visitor_url()
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path != "/chat":
        raise RuntimeError("visual navigation requires the local Lumina Next chat route")
    return _post_json(url.removesuffix("/chat") + "/visual-navigation", {
        "guild_id": "godot", "channel_id": "visitor:visual", "user_id": actor_id,
        "text": text, "request_id": request_id, "memory_scope": "session_only",
        "dispatch_to_bridge": send, "synthesize": False,
    }, 10.0)


def lumina_next_visitor_url() -> str:
    return str(os.environ.get("LUMINA_NEXT_VISITOR_CHAT_URL") or "").strip()


def lumina_next_visitor_enabled() -> bool:
    value = lumina_next_visitor_url()
    return bool(value) and value.lower() not in {"0", "false", "off", "none"}


def lumina_next_visitor_fail_closed() -> bool:
    return str(os.environ.get("LUMINA_NEXT_VISITOR_FAIL_CLOSED") or "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def _loading_reply(detail: str) -> VisitorReplyResult:
    return VisitorReplyResult(
        reply_text="まだ準備中。少しだけ待ってね。",
        gesture="nod",
        mood="attentive",
        emotion="attentive",
        thought="Way会話経路の準備完了を待っている",
        next_action="speak",
        reason="lumina-next-not-ready-fail-closed",
        confidence=1.0,
        look_target="user",
        action="reply",
        status="loading",
        raw_response=json.dumps(
            {"provider": "lumina-next", "status": "loading", "detail": detail[:300]},
            ensure_ascii=False,
        ),
    )


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Lumina Next visitor chat failed: {exc}") from exc
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Lumina Next visitor chat returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Lumina Next visitor chat returned a non-object JSON payload")
    return value


def request_lumina_next_visitor_reply(
    *,
    user_text: str,
    actor_id: str,
    source: str,
    timeout_seconds: float,
) -> VisitorReplyResult:
    url = lumina_next_visitor_url()
    if not url:
        raise RuntimeError("LUMINA_NEXT_VISITOR_CHAT_URL is not configured")
    payload = {
        "guild_id": "godot",
        "channel_id": f"visitor:{source or 'text'}",
        "user_id": actor_id or "visitor",
        "session_id": f"godot:{actor_id or 'visitor'}",
        "text": user_text,
        "persona": "lumina",
        "persona_mode": "auto",
        "synthesize": False,
        "dispatch_to_bridge": False,
    }
    try:
        response = _post_json(url, payload, max(1.0, min(float(timeout_seconds), 180.0)))
        answer = str(response.get("answer") or "").strip()
        if not response.get("ok") or not answer:
            raise RuntimeError(str(response.get("detail") or "Lumina Next returned no answer"))
    except RuntimeError as exc:
        if lumina_next_visitor_fail_closed():
            return _loading_reply(str(exc))
        raise
    return VisitorReplyResult(
        reply_text=answer,
        gesture="nod",
        mood="attentive",
        emotion="attentive",
        thought="新しいOrchestratorで会話文脈と人格方針を統合した",
        next_action="speak",
        reason="lumina-next-orchestrator",
        confidence=0.82,
        look_target="user",
        action="reply",
        status="ok",
        raw_response=json.dumps(
            {
                "provider": "lumina-next",
                "model": response.get("model"),
                "latency_ms": response.get("latency_ms"),
                "memory": response.get("memory"),
            },
            ensure_ascii=False,
        ),
    )
