from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from si_godot_bridge.protocol import AIIntent, NearbyObject, WorldState, is_seat_furniture_target
from si_godot_bridge.tts_text import normalize_for_tts_speak


_SEAT_KEYWORDS = (
    "chair",
    "chairs",
    "sofa",
    "sofas",
    "ソファ",
    "椅子",
    "ベンチ",
    "bench",
    "stool",
    "seat",
    "座席",
    "lounge",
)
MIN_FREE_MOVEMENT_USER_DISTANCE_METERS = 1.0


@dataclass(frozen=True)
class AutonomyPlanCandidate:
    intent: AIIntent
    score: float
    reason: str
    key: str


@dataclass(frozen=True)
class AutonomyPlanResult:
    selected: AIIntent | None
    candidates: list[AutonomyPlanCandidate]
    selected_score: float | None
    reason: str


def autonomy_step_key(intent: AIIntent) -> str:
    if intent.intent == "act_sequence":
        steps = intent.params.get("steps", []) if isinstance(intent.params, dict) else []
        step_keys: list[str] = []
        for item in steps[:4]:
            if not isinstance(item, dict):
                continue
            try:
                step_keys.append(autonomy_step_key(AIIntent.model_validate(item)))
            except Exception:
                continue
        return "act_sequence:%s" % "+".join(step_keys) if step_keys else "act_sequence"
    if intent.intent == "walk_to":
        target = _normalize_text_for_key(intent.target) or ""
        return f"{intent.intent}:{target}"
    if intent.intent == "sit":
        target = _normalize_text_for_key(intent.target) or ""
        return f"{intent.intent}:{target}"
    if intent.intent in {"look_around", "approach_user", "idle_gesture"}:
        return intent.intent
    if intent.intent == "stand":
        return "stand"
    if intent.intent == "set_mood":
        mood = _normalize_text_for_key(intent.mood) or ""
        return f"{intent.intent}:{mood}"
    if intent.intent == "demo":
        name = _normalize_text_for_key(intent.name) or ""
        return f"{intent.intent}:{name}"
    if intent.intent == "speak":
        return "speak"
    if intent.intent == "wait":
        return "wait"
    return intent.intent


def build_autonomy_plan(
    *,
    mode: str,
    world_state: WorldState,
    allow_speech: bool,
    allow_movement: bool,
    allow_demo: bool,
    previous_intent_key: str | None = None,
) -> AutonomyPlanResult:
    normalized_mode = _normalize_mode(mode)
    player_pos = world_state.player_position
    avatar_pos = world_state.avatar_position
    distance = _distance(player_pos, avatar_pos)

    nearby_objects = list(world_state.nearby_objects or [])
    raw = world_state.raw if isinstance(world_state.raw, dict) else {}
    avatar_state = raw.get("avatar_state") if isinstance(raw.get("avatar_state"), dict) else {}
    avatar_is_sitting = bool(avatar_state.get("is_sitting")) or str(avatar_state.get("posture", "")).lower() == "sit"
    table_target = _find_nearby_furniture_target(nearby_objects, ("table", "desk", "coffee table", "机", "テーブル"))
    sofa_target = _find_nearby_furniture_target(nearby_objects, ("sofa", "ソファ", "椅子", "chair", "chairs"))
    seat_target = _find_nearby_furniture_target(nearby_objects, _SEAT_KEYWORDS)
    has_objects = bool(nearby_objects)
    free_movement_allowed = distance is None or distance >= MIN_FREE_MOVEMENT_USER_DISTANCE_METERS

    candidates: list[tuple[AIIntent, float, str]] = []
    if allow_movement:
        if free_movement_allowed and table_target is not None:
            table_name, table_distance = table_target
            base = _movement_walk_score(distance=distance, object_distance=table_distance, normalized_mode=normalized_mode)
            reason = _movement_reason(table_name, table_distance, base, normalized_mode)
            candidates.append(
                (
                    AIIntent(intent="walk_to", target=table_name),
                    max(base, 0.0),
                    reason,
                )
            )

        if free_movement_allowed and sofa_target is not None:
            sofa_name, sofa_distance = sofa_target
            base = _movement_walk_score(distance=distance, object_distance=sofa_distance, normalized_mode=normalized_mode)
            reason = _movement_reason(sofa_name, sofa_distance, base, normalized_mode)
            candidates.append((AIIntent(intent="walk_to", target=sofa_name), max(base, 0.0), reason))
        elif free_movement_allowed and normalized_mode in {"auto", "movement", "walk", "explore", "showcase"}:
            base = _movement_walk_score(distance=distance, object_distance=None, normalized_mode=normalized_mode)
            candidates.append(
                (
                    AIIntent(intent="walk_to", target="Table"),
                    max(base - 1.2, 0.0),
                    "fallback walk target Table",
                )
            )
            candidates.append(
                (
                    AIIntent(intent="walk_to", target="Sofa"),
                    max(base - 1.4, 0.0),
                    "fallback walk target Sofa",
                )
            )

        if free_movement_allowed and seat_target is not None:
            seat_name, seat_distance = seat_target
            if is_seat_furniture_target(seat_name):
                base = _approach_score(distance=distance, object_distance=seat_distance, normalized_mode=normalized_mode)
                candidates.append(
                    (
                        AIIntent(intent="sit", target=seat_name),
                        max(base, 0.0),
                        f"sit candidate target={seat_name}; mode={normalized_mode}; base={round(base, 3)}",
                    )
                )

        if avatar_is_sitting and normalized_mode in {"auto", "movement", "explore", "attention", "life", "showcase"}:
            candidates.append(
                (
                    AIIntent(intent="stand"),
                    round(_stand_score(normalized_mode), 3),
                    f"stand candidate after seated posture; mode={normalized_mode}",
                )
            )

    if allow_speech or allow_demo:
        if distance is not None and distance <= 3.0:
            candidates.append(
                (
                    AIIntent(intent="look_at", target="user"),
                    round(_look_score(distance=distance, normalized_mode=normalized_mode), 3),
                    _look_reason(distance, normalized_mode),
                )
            )
        else:
            candidates.append(
                (
                    AIIntent(intent="look_at", target="user"),
                    round(_look_score(distance=None if distance is None else distance, normalized_mode=normalized_mode), 3),
                    _look_reason(distance, normalized_mode, fallback=True),
                )
            )

    if allow_speech:
        text = "こんにちは" if distance is not None and distance <= 4.0 else "ここにいます。"
        text = normalize_for_tts_speak(text)
        candidates.append(
            (
                AIIntent(intent="speak", text=text),
                round(_speak_score(distance=distance, normalized_mode=normalized_mode), 3),
                _speak_reason(distance, normalized_mode),
            )
        )

    if (allow_speech or allow_movement or allow_demo) and normalized_mode not in {"panic", "off", "disabled"}:
        wait_duration_ms = _wait_duration_ms(distance=distance, normalized_mode=normalized_mode)
        candidates.append(
            (
                AIIntent(intent="wait", params={"duration_ms": wait_duration_ms}),
                round(_wait_score(distance=distance, normalized_mode=normalized_mode), 3),
                _wait_reason(distance=distance, normalized_mode=normalized_mode),
            )
        )

    if allow_movement and normalized_mode in {"auto", "mood", "environment"}:
        mood = "bright" if (previous_intent_key == "set_mood:night") else "night"
        candidates.append(
            (
                AIIntent(intent="set_mood", mood=mood),
                round(_mood_score(normalized_mode), 3),
                f"mood cadence: {mood}",
            )
        )

    if allow_movement and normalized_mode in {"auto", "explore", "movement", "attention", "showcase", "life", "idle", "standby"}:
        if has_objects:
            look_around_base = _look_around_object_score(distance=distance, normalized_mode=normalized_mode)
            candidates.append(
                (
                    AIIntent(intent="look_around"),
                    round(max(look_around_base, 0.0), 3),
                    f"look around objects; mode={normalized_mode}; base={round(max(look_around_base, 0.0), 3)}",
                )
            )
        else:
            look_around_base = _look_around_default_score(distance=distance, normalized_mode=normalized_mode)
            if look_around_base > 0.0:
                candidates.append(
                    (
                        AIIntent(intent="look_around"),
                        round(look_around_base, 3),
                        f"ambient look_around; mode={normalized_mode}; base={round(look_around_base, 3)}",
                    )
                )

    if player_pos is not None and avatar_pos is not None and normalized_mode in {"auto", "attention", "life", "showcase", "movement", "explore", "standby"}:
        approach_base = _approach_user_score(distance=distance, normalized_mode=normalized_mode)
        if approach_base > 0.0:
            candidates.append(
                (
                    AIIntent(intent="approach_user"),
                    round(approach_base, 3),
                    f"approach user intent; distance={_format_distance(distance)}; mode={normalized_mode}",
                )
            )

    if allow_demo and normalized_mode in {"auto", "attention", "life", "standby", "idle"}:
        idle_base = _idle_gesture_score(distance=distance, normalized_mode=normalized_mode)
        if idle_base > 0.0:
            candidates.append(
                (
                    AIIntent(intent="idle_gesture", params={"gesture": _select_idle_gesture(normalized_mode)}),
                    round(idle_base, 3),
                    f"idle gesture candidate; mode={normalized_mode}; base={round(idle_base, 3)}",
                )
            )

    if allow_demo and normalized_mode in {"auto", "showcase", "demo", "presence", "life", "standby", "idle", "attention"}:
        presence_score = _presence_cycle_score(normalized_mode)
        candidates.append(
            (
                AIIntent(intent="demo", name="presence_cycle"),
                round(presence_score, 3),
                f"presence cycle intent; mode={normalized_mode}; base={round(presence_score, 3)}",
            )
        )

    if allow_demo and normalized_mode in {"auto", "showcase", "demo"}:
        score = 0.15 if normalized_mode == "auto" else 1.6
        candidates.append(
            (
                AIIntent(intent="demo", name="autonomy_showcase"),
                round(score, 3),
                "autonomy showcase intent",
            )
        )

    deduped = _dedupe_candidates(candidates)
    if not deduped:
        return AutonomyPlanResult(
            selected=None,
            candidates=[],
            selected_score=None,
            reason="no candidate generated from current state and settings",
        )

    scored: list[AutonomyPlanCandidate] = []
    for candidate_intent, score, reason in deduped:
        key = autonomy_step_key(candidate_intent)
        adjusted = score - (1.1 if key == (previous_intent_key or "") else 0.0)
        if key == (previous_intent_key or ""):
            reason = f"{reason}; avoids immediate repetition"
        scored.append(
            AutonomyPlanCandidate(
                intent=candidate_intent,
                score=max(0.0, round(float(adjusted), 3)),
                reason=reason,
                key=key,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.key))
    selected = scored[0] if scored else None
    if selected is None:
        return AutonomyPlanResult(selected=None, candidates=[], selected_score=None, reason="no viable scored candidate")

    return AutonomyPlanResult(
        selected=selected.intent,
        candidates=scored,
        selected_score=selected.score,
        reason=f"selected={selected.key}; score={selected.score}; {selected.reason}",
    )


def _movement_walk_score(*, distance: float | None, object_distance: float | None, normalized_mode: str) -> float:
    base = 0.8 if normalized_mode == "showcase" else 0.5
    if distance is not None:
        if distance >= 2.5:
            base += 0.8
        elif distance <= 1.3:
            base -= 0.6
    if object_distance is not None:
        if object_distance <= 1.5:
            base += 1.8
        elif object_distance <= 3.0:
            base += 1.2
        elif object_distance <= 6.0:
            base += 0.8
        else:
            base += 0.2
    else:
        base -= 0.2
    if normalized_mode in {"movement", "walk", "explore"}:
        base += 1.4
    if normalized_mode in {"attention", "speech", "chat", "conversation"}:
        base -= 0.3
    return base


def _movement_reason(target: str, object_distance: float | None, base_score: float, normalized_mode: str) -> str:
    if object_distance is None:
        distance_part = "fallback"
    elif object_distance <= 1.5:
        distance_part = "nearby furniture"
    elif object_distance <= 4.0:
        distance_part = "furniture within arm's reach"
    else:
        distance_part = "distant furniture target"
    return f"walk candidate target={target} ({distance_part}); mode={normalized_mode}; base={round(base_score, 3)}"


def _look_score(*, distance: float | None, normalized_mode: str) -> float:
    base = 1.6
    if distance is None:
        base += 0.2
    elif distance <= 1.0:
        base += 2.4
    elif distance <= 2.5:
        base += 1.4
    elif distance <= 4.0:
        base += 0.6
    if normalized_mode in {"auto", "attention", "speech", "conversation", "chat"}:
        base += 0.8
    if normalized_mode in {"movement", "walk", "explore"}:
        base -= 0.4
    return base


def _look_reason(distance: float | None, normalized_mode: str, fallback: bool = False) -> str:
    if fallback:
        return f"look at user for social continuity when not close; mode={normalized_mode}"
    return f"look at user; distance={_format_distance(distance)}; mode={normalized_mode}"


def _speak_score(*, distance: float | None, normalized_mode: str) -> float:
    base = 1.3
    if distance is None:
        base += 0.4
    elif distance <= 1.5:
        base += 1.9
    elif distance <= 3.5:
        base += 1.2
    elif distance <= 6.0:
        base += 0.4
    if normalized_mode in {"auto", "attention", "speech", "conversation", "chat"}:
        base += 0.8
    if normalized_mode in {"movement", "walk", "explore"}:
        base -= 0.3
    return base


def _speak_reason(distance: float | None, normalized_mode: str) -> str:
    return f"speak intent; distance={_format_distance(distance)}; mode={normalized_mode}"


def _wait_score(*, distance: float | None, normalized_mode: str) -> float:
    base = 1.1
    if distance is None:
        base += 0.2
    elif distance <= 1.2:
        base += 0.4
    elif distance <= 3.5:
        base += 0.2
    if normalized_mode in {"movement", "walk", "explore"}:
        base -= 0.1
    if normalized_mode in {"attention", "speech", "conversation", "chat"}:
        base -= 0.2
    return base


def _wait_reason(distance: float | None, normalized_mode: str) -> str:
    if distance is None:
        return f"safety pause when distance unknown; mode={normalized_mode}"
    return f"safety pause; distance={_format_distance(distance)}; mode={normalized_mode}"


def _wait_duration_ms(*, distance: float | None, normalized_mode: str) -> int:
    base = 900
    if distance is None:
        return base
    if distance <= 1.0:
        return 700
    if normalized_mode in {"presence", "life", "standby", "idle", "attention"}:
        return 1200
    if distance >= 5.0:
        return 1400
    return base


def _mood_score(normalized_mode: str) -> float:
    if normalized_mode == "mood":
        return 1.8
    if normalized_mode in {"showcase", "demo"}:
        return 0.7
    return 0.5


def _presence_cycle_score(normalized_mode: str) -> float:
    if normalized_mode == "presence":
        return 5.0
    if normalized_mode in {"life", "standby", "idle"}:
        return 2.6
    if normalized_mode == "attention":
        return 1.2
    if normalized_mode == "showcase":
        return 1.1
    if normalized_mode == "demo":
        return 0.9
    return 0.45


def _approach_score(*, distance: float | None, object_distance: float | None, normalized_mode: str) -> float:
    base = 1.25
    if distance is not None and distance <= 1.5:
        return 0.0
    if distance is not None and distance > 4.5:
        base += 0.3
    if object_distance is not None:
        if object_distance <= 1.2:
            base += 1.6
        elif object_distance <= 3.0:
            base += 1.1
        elif object_distance <= 6.0:
            base += 0.6
    if normalized_mode in {"showcase", "movement", "explore", "attention"}:
        base += 0.5
    return base


def _stand_score(normalized_mode: str) -> float:
    if normalized_mode in {"movement", "explore"}:
        return 3.0
    if normalized_mode in {"attention", "life", "showcase"}:
        return 2.2
    return 1.4


def _look_around_object_score(*, distance: float | None, normalized_mode: str) -> float:
    base = 1.2
    if normalized_mode in {"showcase", "movement", "explore"}:
        base += 0.8
    if distance is not None and distance < 1.0:
        base -= 0.5
    if normalized_mode == "attention":
        base += 0.2
    return base


def _look_around_default_score(*, distance: float | None, normalized_mode: str) -> float:
    base = 0.55
    if normalized_mode in {"auto", "standby", "idle"}:
        base += 0.35
    if distance is None:
        base += 0.3
    return base


def _approach_user_score(*, distance: float | None, normalized_mode: str) -> float:
    if distance is None:
        return 0.0
    if distance <= 1.0:
        return 0.0
    if normalized_mode in {"attention", "speech", "conversation", "chat", "life"}:
        return 1.2 - (distance / 12.0)
    if normalized_mode in {"showcase", "movement", "explore"}:
        return 1.0
    return 0.75


def _idle_gesture_score(*, distance: float | None, normalized_mode: str) -> float:
    if distance is None:
        distance = 999.0
    if distance <= 0.7:
        return 0.0
    if normalized_mode == "attention":
        return 0.7
    if normalized_mode in {"auto", "standby", "idle", "life"}:
        return 1.0
    return 0.45


def _select_idle_gesture(normalized_mode: str) -> str:
    if normalized_mode in {"attention", "speech"}:
        return "nod"
    if normalized_mode in {"showcase", "life"}:
        return "shrug"
    return "wave"


def _dedupe_candidates(candidates: list[tuple[AIIntent, float, str]]) -> list[tuple[AIIntent, float, str]]:
    used: dict[str, tuple[AIIntent, float, str]] = {}
    for candidate, score, reason in candidates:
        key = autonomy_step_key(candidate)
        if key not in used:
            used[key] = (candidate, score, reason)
    return list(used.values())


def _distance(a: Any, b: Any) -> float | None:
    if not a or not b:
        return None
    ax = getattr(a, "x", None)
    az = getattr(a, "z", None)
    bx = getattr(b, "x", None)
    bz = getattr(b, "z", None)
    if ax is None or az is None or bx is None or bz is None:
        return None
    return ((float(ax) - float(bx)) ** 2 + (float(az) - float(bz)) ** 2) ** 0.5


def _find_nearby_furniture_target(
    nearby_objects: list[NearbyObject],
    aliases: tuple[str, ...],
) -> tuple[str, float] | None:
    lowered_aliases = tuple(alias.lower() for alias in aliases)
    candidates = []
    for obj in nearby_objects:
        if not obj.name:
            continue
        label = f"{obj.name} {obj.type}".lower()
        if not any(alias in label for alias in lowered_aliases):
            continue
        candidates.append((obj, obj.distance if obj.distance is not None else 9999.0))
    if not candidates:
        return None
    nearest = sorted(candidates, key=lambda item: float(item[1]))[0][0]
    return nearest.name, _normalize_distance(nearest.distance)


def _normalize_distance(distance: float | None) -> float:
    if distance is None:
        return 9999.0
    return float(distance)


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "auto").strip().lower()
    return normalized or "auto"


def _format_distance(distance: float | None) -> str:
    if distance is None:
        return "unknown"
    return f"{distance:.2f}"


def _normalize_text_for_key(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()
