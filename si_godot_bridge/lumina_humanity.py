from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_humanity_context(
    *,
    mode: str,
    autonomy_state: Any | None = None,
    visitor_memory: Any | None = None,
    recent_journal: Sequence[Any] | None = None,
    world_state: Mapping[str, Any] | None = None,
    existing_candidates: Sequence[Any] | None = None,
    vision_summary: str | None = None,
) -> dict[str, Any]:
    """Build a compact, serializable context for Lumina-like decisions."""
    internal = _as_mapping(autonomy_state)
    memory = _as_mapping(visitor_memory)
    world = world_state if isinstance(world_state, Mapping) else {}
    recent_keys = _recent_intent_keys(recent_journal)
    inner_state = _inner_state_summary(internal)
    relationship = _relationship_summary(memory)
    care_priorities = _care_priorities(
        inner_state=inner_state,
        relationship=relationship,
        world=world,
        mode=str(mode or "").strip().lower(),
        vision_summary=vision_summary,
    )
    action_bias = _action_bias_hints(
        inner_state=inner_state,
        recent_keys=recent_keys,
        existing_candidates=existing_candidates,
    )
    avoid = _avoidance_rules(inner_state=inner_state, recent_keys=recent_keys, relationship=relationship)

    return {
        "identity": {
            "name": "Lumina",
            "stance": "warm, observant, honest, not pretending to be human",
            "goal": "notice the user, remember context, and choose embodied actions with care",
        },
        "inner_state": inner_state,
        "relationship": relationship,
        "care_priorities": care_priorities[:5],
        "action_bias": action_bias[:6],
        "avoid": avoid[:6],
        "recent_intents": recent_keys[:5],
        "reason_style": "short visible reason: what Lumina noticed + why this action fits her current state",
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _inner_state_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    mood = _short_text(value.get("mood"), default="calm", max_chars=32)
    current_goal = _short_text(value.get("current_goal"), max_chars=60)
    next_desire = _short_text(value.get("next_desire"), default="observe", max_chars=60)
    user_awareness = _short_text(value.get("user_awareness"), default="unaware", max_chars=40)
    posture = _short_text(value.get("posture"), default="idle", max_chars=40)
    return {
        "mood": mood,
        "curiosity": round(_clamp01(value.get("curiosity"), 0.45), 3),
        "social_attention": round(_clamp01(value.get("social_attention"), 0.35), 3),
        "energy": round(_clamp01(value.get("energy"), 0.78), 3),
        "current_goal": current_goal,
        "next_desire": next_desire,
        "posture": posture,
        "user_awareness": user_awareness,
        "last_observation": _short_text(value.get("last_observation"), max_chars=120),
    }


def _relationship_summary(memory: Mapping[str, Any]) -> dict[str, Any]:
    accessibility = _as_mapping(memory.get("accessibility"))
    preferences = _as_mapping(memory.get("preferences") or memory.get("user_preferences"))
    boundaries = _as_mapping(memory.get("boundaries") or memory.get("user_boundaries"))
    recent_topics = memory.get("recent_topics") or memory.get("topics") or []
    if not isinstance(recent_topics, Sequence) or isinstance(recent_topics, (str, bytes)):
        recent_topics = [recent_topics]
    return {
        "known_user": bool(memory),
        "name": _short_text(memory.get("name") or memory.get("user_name") or memory.get("display_name"), max_chars=40),
        "preferred_address": _short_text(memory.get("preferred_address") or memory.get("how_to_address_user"), max_chars=40),
        "preferences": _compact_mapping(preferences, limit=4),
        "boundaries": _compact_mapping(boundaries, limit=4),
        "accessibility": _compact_mapping(accessibility, limit=4),
        "recent_topics": [_short_text(item, max_chars=48) for item in recent_topics[:4] if _short_text(item, max_chars=48)],
    }


def _care_priorities(
    *,
    inner_state: Mapping[str, Any],
    relationship: Mapping[str, Any],
    world: Mapping[str, Any],
    mode: str,
    vision_summary: str | None,
) -> list[str]:
    priorities: list[str] = []
    user_awareness = str(inner_state.get("user_awareness") or "")
    social_attention = float(inner_state.get("social_attention") or 0.0)
    energy = float(inner_state.get("energy") or 0.0)
    avatar_view = _as_mapping(world.get("avatar_view"))
    player_visible = bool(avatar_view.get("player_visible"))
    if player_visible or user_awareness in {"looking_at_you", "aware_nearby"} or social_attention >= 0.7:
        priorities.append("acknowledge the user before exploring the room")
    if _relationship_prefers_low_motion(relationship):
        priorities.append("prefer low-motion gaze, wait, and short speech over walking")
    if energy <= 0.42:
        priorities.append("prefer rest, sit, or wait instead of another walk")
    if mode == "life" and not priorities:
        priorities.append("choose a small believable action, not a dramatic demonstration")
    if vision_summary:
        priorities.append("ground the reason in what Lumina currently notices")
    priorities.append("be honest about uncertainty and do not claim unseen objects are visible")
    return _dedupe(priorities)


def _action_bias_hints(
    *,
    inner_state: Mapping[str, Any],
    recent_keys: Sequence[str],
    existing_candidates: Sequence[Any] | None,
) -> list[str]:
    hints: list[str] = []
    social_attention = float(inner_state.get("social_attention") or 0.0)
    curiosity = float(inner_state.get("curiosity") or 0.0)
    energy = float(inner_state.get("energy") or 0.0)
    latest = recent_keys[0] if recent_keys else ""
    candidate_keys = [
        str(item.get("key") or "").strip()
        for item in (existing_candidates or [])
        if isinstance(item, Mapping) and str(item.get("key") or "").strip()
    ]
    if social_attention >= 0.72:
        hints.append("boost look_at, wait, or short speech when the user has Lumina's attention")
    if curiosity >= 0.66 and energy >= 0.45:
        hints.append("allow one grounded explore action when the user is not being ignored")
    if energy <= 0.45:
        hints.append("boost sit or wait; reduce repeated walking")
    if latest:
        hints.append(f"avoid immediately repeating {latest}")
    if any(key.startswith("walk_to:") for key in candidate_keys) and latest.startswith("walk_to:"):
        hints.append("after walking, prefer inspect, look_at, wait, or small gesture")
    return _dedupe(hints)


def _avoidance_rules(
    *,
    inner_state: Mapping[str, Any],
    recent_keys: Sequence[str],
    relationship: Mapping[str, Any],
) -> list[str]:
    rules = [
        "do not present Lumina as a real human",
        "do not use nearby_objects as visual proof unless they are in avatar_view.visible_objects",
    ]
    if recent_keys:
        rules.append("do not loop the same intent without a visible reason")
    if _relationship_prefers_low_motion(relationship):
        rules.append("do not choose unnecessary movement in low-motion contexts")
    if float(inner_state.get("social_attention") or 0.0) >= 0.72:
        rules.append("do not walk away from the user before acknowledging them")
    return _dedupe(rules)


def _recent_intent_keys(recent_journal: Sequence[Any] | None) -> list[str]:
    keys: list[str] = []
    for entry in reversed(list(recent_journal or [])):
        if not isinstance(entry, Mapping):
            continue
        key = _short_text(entry.get("intent_key") or entry.get("key") or entry.get("selected_key"), max_chars=64)
        if key:
            keys.append(key)
    return _dedupe(keys)


def _relationship_prefers_low_motion(relationship: Mapping[str, Any]) -> bool:
    accessibility = _as_mapping(relationship.get("accessibility"))
    boundaries = _as_mapping(relationship.get("boundaries"))
    combined = {**accessibility, **boundaries}
    for key, value in combined.items():
        lowered = f"{key} {value}".lower()
        if any(marker in lowered for marker in ("low_motion", "low motion", "text_only", "quiet", "motion_sickness")):
            return True
    return False


def _compact_mapping(value: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, item in list(value.items())[:limit]:
        clean_key = _short_text(key, max_chars=36)
        if not clean_key:
            continue
        clean_value = _short_text(item, max_chars=70)
        if clean_value:
            compact[clean_key] = clean_value
    return compact


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(1.0, max(0.0, number))


def _short_text(value: Any, *, default: str = "", max_chars: int) -> str:
    text = " ".join(str(value if value is not None else default).split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
