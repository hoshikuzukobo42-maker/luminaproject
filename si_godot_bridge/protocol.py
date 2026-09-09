from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from si_godot_bridge.tts_text import normalize_for_tts_speak


ActionName = Literal[
    "move_to_position",
    "move_to_node",
    "look_at_position",
    "look_at_node",
    "look_at_user",
    "set_gaze_direction",
    "play_animation",
    "set_expression",
    "speak",
    "stop",
    "stop_audio",
    "set_audio_settings",
    "wait",
    "set_world_environment",
    "environment_update",
    "spawn_object",
    "follow_user",
    "gesture",
    "conversation_activity",
    "living_state",
    "semantic_state",
    "living_animation_style",
    "animation_style",
    "motion_style",
    "sit",
    "stand",
    "set_motion_profile",
    "camera_preset",
    "camera_toggle_follow",
    "camera_focus",
    "camera_reset",
    "camera_set",
]

ALLOWED_ACTIONS: set[str] = set(ActionName.__args__)  # type: ignore[attr-defined]
DEFAULT_SEAT_TARGET = "Chair_A"
SEAT_FURNITURE_KEYWORDS = (
    "chair",
    "chairs",
    "sofa",
    "couch",
    "seat",
    "seated",
    "bench",
    "stool",
    "lounge",
    "椅子",
    "いす",
    "イス",
    "ソファ",
    "ベンチ",
    "座席",
)


class Vector3Payload(BaseModel):
    x: float
    y: float = 0.0
    z: float

    @field_validator("x", "y", "z")
    @classmethod
    def _coordinate_bounds(cls, value: float) -> float:
        value = float(value)
        if value < -500.0 or value > 500.0:
            raise ValueError("coordinate out of allowed bounds [-500, 500]")
        return value


class NearbyObject(BaseModel):
    instance_id: int | None = None
    name: str
    display_name: str | None = None
    type: str = "object"
    area: str | None = None
    aliases: list[str] = Field(default_factory=list)
    search_hint: str | None = None
    distance: float | None = None
    position: Vector3Payload | None = None
    can_sit: bool = False
    can_approach: bool = True
    node_path: str | None = None


class WorldState(BaseModel):
    avatar_position: Vector3Payload | None = None
    player_position: Vector3Payload | None = None
    nearby_objects: list[NearbyObject] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    active_command_id: str | None = None
    navigation_ready: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


class SICommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # End-to-end product correlation. This is intentionally distinct from
    # command_id because one visitor turn can fan out to several Godot
    # commands while remaining one auditable turn.
    correlation_id: str = Field(default="", max_length=128)
    actor_id: str = "toha"
    action: ActionName
    target_node: str | None = None
    position: Vector3Payload | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=0, le=10)
    reason: str = Field(default="", max_length=500)
    expires_in_ms: int = Field(default=10_000, ge=100, le=120_000)
    created_at: float = Field(default_factory=time.time)

    @field_validator("actor_id", "target_node", "correlation_id")
    @classmethod
    def _clean_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if len(cleaned) > 120:
            raise ValueError("name is too long")
        return cleaned

    @model_validator(mode="after")
    def _validate_action_shape(self) -> "SICommand":
        action = self.action
        params = self.params or {}
        if action in {"move_to_position", "look_at_position"} and self.position is None:
            raise ValueError(f"{action} requires position")
        if action in {"move_to_node", "look_at_node"} and not self.target_node:
            raise ValueError(f"{action} requires target_node")
        if action == "play_animation" and not _param_text(params, "animation"):
            raise ValueError("play_animation requires params.animation")
        if action == "set_expression" and not _param_text(params, "expression"):
            raise ValueError("set_expression requires params.expression")
        if action == "speak" and not _param_text(params, "text"):
            raise ValueError("speak requires params.text")
        if action == "follow_user" and not isinstance(params, dict):
            raise ValueError("follow_user requires params")
        if action == "gesture" and not _param_text(params, "gesture"):
            raise ValueError("gesture requires params.gesture")
        if action in {"conversation_activity", "living_state", "semantic_state"}:
            if not (
                _param_text(params, "activity")
                or _param_text(params, "state")
                or _param_text(params, "semantic_state")
            ):
                raise ValueError(f"{action} requires params.activity, params.state, or params.semantic_state")
        if action in {"living_animation_style", "animation_style", "motion_style"}:
            if not (_param_text(params, "style") or _param_text(params, "name") or _param_text(params, "activity_style")):
                raise ValueError(f"{action} requires params.style, params.name, or params.activity_style")
        if action == "sit":
            if not isinstance(params, dict):
                raise ValueError("sit requires params")
            normalized_params = dict(params)
            sit_target = _extract_sit_target(self.target_node, normalized_params)
            if sit_target is None:
                raise ValueError("sit requires seat furniture target_node such as Chair_A or Sofa")
            if not _sit_target_is_allowed(sit_target, normalized_params):
                raise ValueError("sit target must be seat furniture such as chair, sofa, bench, or stool")
            normalized_params["target_node"] = sit_target
            object.__setattr__(self, "target_node", sit_target)
            object.__setattr__(self, "params", normalized_params)
        if action == "stand" and not isinstance(params, dict):
            raise ValueError("stand requires params")
        if action == "set_motion_profile":
            if not isinstance(params, dict):
                raise ValueError("set_motion_profile requires params")
            profile_params = params.get("params", {})
            if profile_params and not isinstance(profile_params, dict):
                raise ValueError("set_motion_profile requires params.params object")
        if action == "environment_update":
            params = _normalize_environment_params(params)
            object.__setattr__(self, "params", params)
            if not _has_environment_fields(params):
                raise ValueError("environment_update requires environment/lighting params")
            # keep params shape permissive while still validating at least one supported field
        if action in {"camera_preset", "camera_toggle_follow", "camera_focus", "camera_reset", "camera_set"}:
            if not isinstance(params, dict):
                raise ValueError(f"{action} requires params")
            if action == "camera_preset" and not params:
                raise ValueError("camera_preset requires at least index or preset")
            if action == "camera_set" and not params:
                raise ValueError("camera_set requires at least one camera field")
        if action == "wait":
            duration_ms = int(params.get("duration_ms", 0) or 0)
            if duration_ms < 100 or duration_ms > 60_000:
                raise ValueError("wait requires params.duration_ms in [100, 60000]")
        return self


class SISequence(BaseModel):
    """Container for sequential command execution."""

    model_config = ConfigDict(extra="forbid")

    sequence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default="", max_length=128)
    actor_id: str = "toha"
    label: str = "sequence"
    reason: str = Field(default="", max_length=500)
    continue_on_failure: bool = True
    send: bool = True
    wait_for_completion: bool = False
    step_timeout_ms: int = Field(default=8_000, ge=100, le=120_000)
    commands: list[SICommand] = Field(min_length=1, max_length=32)


class SequenceDispatchResult(BaseModel):
    """Result payload for sequence/routine dispatch API."""

    ok: bool
    status: Literal["validated", "queued", "running", "completed", "partial", "failed"]
    sequence_id: str
    label: str
    reason: str = ""
    command_ids: list[str]
    results: list[CommandDispatchResult]
    events: list[dict[str, Any]] = Field(default_factory=list)
    godot_connected: bool
    detail: str = ""


class AIIntent(BaseModel):
    """Thin, LLM-friendly intent payload."""

    model_config = ConfigDict(extra="allow")

    intent: str = Field(min_length=1, max_length=120)
    actor_id: str = "toha"
    send: bool = True
    target: str | None = None
    text: str | None = None
    name: str | None = None
    mood: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("intent", "target", "text", "name", "mood")
    @classmethod
    def _normalize_text_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("actor_id")
    @classmethod
    def _normalize_actor_id(cls, value: str | None) -> str:
        cleaned = str(value or "").strip()
        return cleaned or "toha"


class AIIntentResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command", "sequence"]
    actor_id: str
    intent: str
    command: SICommand | None = None
    sequence_name: str | None = None

    @model_validator(mode="after")
    def _ensure_shape(self) -> "AIIntentResolution":
        if self.kind == "command" and self.command is None:
            raise ValueError("command intent must contain command")
        if self.kind == "sequence" and not self.sequence_name:
            raise ValueError("sequence intent must contain sequence_name")
        if self.kind == "command" and self.sequence_name:
            raise ValueError("command intent must not include sequence_name")
        if self.kind == "sequence" and self.command is not None:
            raise ValueError("sequence intent must not include command")
        return self


class IncomingLLMOutput(BaseModel):
    output: str | dict[str, Any]
    actor_id: str = "toha"
    send: bool = True


class IntentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    actor_id: str = "toha"
    send: bool = True


class CommandDispatchResult(BaseModel):
    ok: bool
    status: Literal["sent", "queued", "validated", "accepted", "completed", "failed", "timeout", "interrupted", "unhandled"]
    command: SICommand
    godot_connected: bool
    detail: str = ""


class CommandResultEvent(BaseModel):
    command_id: str
    correlation_id: str = Field(default="", max_length=128)
    ok: bool
    status: str
    detail: str = ""
    actor_id: str = "toha"
    received_at: float = Field(default_factory=time.time)


def _param_text(params: dict[str, Any], key: str) -> str:
    value = params.get(key, "")
    return str(value).strip()


def is_seat_furniture_target(value: Any) -> bool:
    """Return true when a node/type name clearly means sit-capable furniture."""
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(keyword.lower() in text for keyword in SEAT_FURNITURE_KEYWORDS)


def _extract_sit_target(target_node: str | None, params: dict[str, Any]) -> str | None:
    for key in ("target_node", "seat", "target"):
        candidate = _normalize_optional_text(params.get(key))
        if candidate:
            return candidate
    return _normalize_optional_text(target_node)


def _sit_target_is_allowed(target_node: str, params: dict[str, Any]) -> bool:
    if is_seat_furniture_target(target_node):
        return True
    for key in ("target_type", "type", "category", "furniture_type", "seat_type"):
        if is_seat_furniture_target(params.get(key)):
            return True
    return False


def command_schema() -> dict[str, Any]:
    return SICommand.model_json_schema()


def sequence_schema() -> dict[str, Any]:
    return SISequence.model_json_schema()


def intent_schema() -> dict[str, Any]:
    return AIIntent.model_json_schema()


def parse_llm_command(output: str | dict[str, Any], *, actor_id: str = "toha") -> SICommand:
    payload = _coerce_json_object(output)
    normalized = normalize_legacy_command(payload)
    normalized.setdefault("actor_id", actor_id)
    return SICommand.model_validate(normalized)


def resolve_ai_intent(intent_payload: AIIntent | dict[str, Any]) -> AIIntentResolution:
    if not isinstance(intent_payload, AIIntent):
        intent_obj = AIIntent.model_validate(intent_payload)
    else:
        intent_obj = intent_payload

    intent = str(intent_obj.intent).strip().lower()
    actor_id = intent_obj.actor_id or "toha"
    top_level_params = dict(getattr(intent_obj, "model_extra", None) or {})
    intent_payload_params = {**top_level_params, **dict(intent_obj.params or {})}
    extras = {
        "target": _normalize_optional_text(intent_obj.target),
        "text": _normalize_optional_text(intent_obj.text),
        "name": _normalize_optional_text(intent_obj.name),
        "mood": _normalize_optional_text(intent_obj.mood),
    }

    if not intent:
        raise ValueError("intent is required")

    if intent in {"demo", "routine", "sequence"}:
        routine_name = (
            extras["name"]
            or _normalize_optional_text(intent_payload_params.get("name"))
            or _normalize_optional_text(intent_payload_params.get("sequence"))
        )
        if not routine_name:
            raise ValueError("demo intent requires name")
        return AIIntentResolution(kind="sequence", actor_id=actor_id, intent=intent_obj.intent.strip(), sequence_name=routine_name)

    if intent in {"walk_to", "move_to", "move", "walk", "go", "walk_to_node", "move_to_node"}:
        target_node = (
            extras["target"]
            or _normalize_optional_text(intent_payload_params.get("target"))
            or _normalize_optional_text(intent_payload_params.get("target_node"))
            or _normalize_optional_text(intent_payload_params.get("node"))
        )
        if target_node:
            return AIIntentResolution(
                kind="command",
                actor_id=actor_id,
                intent=intent_obj.intent.strip(),
                command=SICommand(
                    actor_id=actor_id,
                    action="move_to_node",
                    target_node=target_node,
                    params=_intent_params(intent_payload_params),
                    reason=f"intent:{intent}",
            ),
        )

        position = _coerce_position(intent_payload_params, {"x": intent_payload_params.get("x"), "y": intent_payload_params.get("y"), "z": intent_payload_params.get("z")})
        if position is not None:
            return AIIntentResolution(
                kind="command",
                actor_id=actor_id,
                intent=intent_obj.intent.strip(),
                command=SICommand(
                    actor_id=actor_id,
                    action="move_to_position",
                    position=position,
                    params=_intent_params(intent_payload_params),
                    reason=f"intent:{intent}",
                ),
            )
        raise ValueError("walk_to/move intents require target or position x/y/z")

    if intent in {"move_to_position", "move_to_coordinate", "move_to_coords"}:
        position = _coerce_position(
            intent_payload_params,
            {
                "x": intent_payload_params.get("x"),
                "y": intent_payload_params.get("y"),
                "z": intent_payload_params.get("z"),
            },
        )
        if position is None:
            target_node = (
                extras["target"]
                or _normalize_optional_text(intent_payload_params.get("target"))
                or _normalize_optional_text(intent_payload_params.get("target_node"))
                or _normalize_optional_text(intent_payload_params.get("node"))
            )
            if target_node:
                return AIIntentResolution(
                    kind="command",
                    actor_id=actor_id,
                    intent=intent_obj.intent.strip(),
                    command=SICommand(
                        actor_id=actor_id,
                        action="move_to_node",
                        target_node=target_node,
                        params=_intent_params(intent_payload_params),
                        reason=f"intent:{intent}",
                    ),
                )
            raise ValueError("move_to_position intent requires x/y/z coordinates or target")

        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="move_to_position",
                position=position,
                params=_intent_params(intent_payload_params),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"speak", "talk", "say"}:
        text = (
            extras["text"]
            or _normalize_optional_text(intent_payload_params.get("text"))
            or _normalize_optional_text(intent_payload_params.get("message"))
        )
        if not text:
            raise ValueError("speak intent requires text")
        text = normalize_for_tts_speak(text)
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="speak",
                params=_intent_params({"text": text, **intent_payload_params}),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"look_at_position", "look_at_coordinates"}:
        position = _coerce_position(
            intent_payload_params,
            {
                "x": intent_payload_params.get("x"),
                "y": intent_payload_params.get("y"),
                "z": intent_payload_params.get("z"),
            },
        )
        if position is None:
            raise ValueError("look_at_position intent requires x/y/z coordinates")
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="look_at_position",
                position=position,
                params=_intent_params(intent_payload_params),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"look_at", "look", "look_at_user", "look_user"}:
        target = extras["target"] or _normalize_optional_text(intent_payload_params.get("target")) or _normalize_optional_text(intent_payload_params.get("target_node"))
        if _is_user_target(target, intent_payload_params.get("target") or intent_payload_params.get("user")):
            return AIIntentResolution(
                kind="command",
                actor_id=actor_id,
                intent=intent_obj.intent.strip(),
                command=SICommand(
                    actor_id=actor_id,
                    action="look_at_user",
                    params=_intent_params(intent_payload_params),
                    reason=f"intent:{intent}",
                ),
            )
        if target is None:
            raise ValueError("look_at intent requires target or target_node")
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="look_at_node",
                target_node=target,
                params=_intent_params(intent_payload_params),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"gesture", "gesture_action"}:
        gesture = _normalize_optional_text(intent_payload_params.get("gesture"))
        if not gesture:
            raise ValueError("gesture intent requires params.gesture")
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="gesture",
                params=_intent_params(intent_payload_params),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"idle_gesture", "small_gesture", "micro_gesture"}:
        gesture = _normalize_optional_text(intent_payload_params.get("gesture")) or "nod"
        duration = intent_payload_params.get("duration", 1.4)
        params = _intent_params({**intent_payload_params, "gesture": gesture, "duration": duration})
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="gesture",
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"look_around", "scan_room", "observe_room"}:
        params = _intent_params({**intent_payload_params, "gesture": "look_around", "duration": intent_payload_params.get("duration", 1.4)})
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="gesture",
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"approach_user", "move_closer_to_user", "near_user"}:
        params = _intent_params(intent_payload_params)
        params.setdefault("distance", 1.4)
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="follow_user",
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"sit", "sit_down", "sit_on", "seated", "sitting", "座る"}:
        target = (
            extras["target"]
            or _normalize_optional_text(intent_payload_params.get("target"))
            or _normalize_optional_text(intent_payload_params.get("target_node"))
            or _normalize_optional_text(intent_payload_params.get("seat"))
        )
        params = _intent_params(intent_payload_params)
        if target:
            params.setdefault("target_node", target)
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="sit",
                target_node=target,
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"stand", "stand_up", "立つ"}:
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="stand",
                params=_intent_params(intent_payload_params),
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"set_expression", "expression", "face"}:
        expression = _normalize_optional_text(intent_payload_params.get("expression")) or _normalize_optional_text(intent_payload_params.get("name"))
        if not expression:
            raise ValueError("set_expression intent requires params.expression")
        params = _intent_params(intent_payload_params)
        params.setdefault("expression", expression)
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="set_expression",
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"stop", "emergency_stop", "halt", "cancel"}:
        params = _intent_params(intent_payload_params)
        priority = params.pop("priority", None)
        try:
            priority_value = int(priority) if priority is not None else 10
        except (TypeError, ValueError):
            priority_value = 10
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="stop",
                params=params,
                priority=priority_value,
                reason=f"intent:{intent}",
                expires_in_ms=120_000,
            ),
        )

    if intent in {"wait", "pause"}:
        duration_ms = _coerce_intent_ms(intent_payload_params.get("duration_ms"))
        params = _intent_params(intent_payload_params)
        params["duration_ms"] = duration_ms
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="wait",
                params=params,
                reason=f"intent:{intent}",
            ),
        )

    if intent in {"set_mood", "mood"}:
        mood = extras["mood"] or _normalize_optional_text(intent_payload_params.get("mood"))
        params = _mood_to_environment_params(mood)
        if params is None:
            raise ValueError(f"set_mood intent requires mood (for example: night, bright, fog)")
        params.update(_intent_params(intent_payload_params, include_reserved=("mode",)))
        return AIIntentResolution(
            kind="command",
            actor_id=actor_id,
            intent=intent_obj.intent.strip(),
            command=SICommand(
                actor_id=actor_id,
                action="environment_update",
                params=_normalize_environment_params(params),
                reason=f"intent:{intent}",
            ),
        )

    raise ValueError(f"unsupported intent: {intent}")


def normalize_legacy_command(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    action = str(data.get("action", "")).strip().lower()
    params = dict(data.get("params") or {})

    if not action:
        raise ValueError("command action is required")

    if action in {"move", "walk", "go"}:
        if data.get("target_node"):
            data["action"] = "move_to_node"
        else:
            data["action"] = "move_to_position"
    elif action in {"look", "look_at"}:
        data["action"] = "look_at_node" if data.get("target_node") else "look_at_position"
    elif action in {"animate", "animation"}:
        data["action"] = "play_animation"
    elif action in {"expression", "face"}:
        data["action"] = "set_expression"
    elif action in {"say", "talk"}:
        data["action"] = "speak"
    elif action in {"follow", "follow_me", "追従", "追いかけ"}:
        data["action"] = "follow_user"
    elif action in {"gesture", "pose", "gestures", "手振り", "ジェスチャ", "ジェスチャー"}:
        data["action"] = "gesture"
    elif action in {"sit", "seated", "sitting", "座る", "座"}:
        data["action"] = "sit"
    elif action in {"stand", "立つ", "立"}:
        data["action"] = "stand"
    elif action in {"motion_profile", "profile", "set_profile", "set_motion_profile", "身体プロファイル", "モーションプロファイル"}:
        data["action"] = "set_motion_profile"
    elif action in {"environment", "environment_update", "lighting", "light", "world", "set_world"}:
        data["action"] = "environment_update"

    if "x" in data or "z" in data:
        data["position"] = {
            "x": float(data.get("x", 0.0)),
            "y": float(data.get("y", 0.0)),
            "z": float(data.get("z", 0.0)),
        }
        data.pop("x", None)
        data.pop("y", None)
        data.pop("z", None)

    for key in (
        "animation",
        "expression",
        "text",
        "duration_ms",
        "stop_distance",
        "walk_speed",
        "gesture",
        "distance",
        "index",
        "preset",
        "yaw",
        "pitch",
        "height",
        "mode",
        "follow",
        "focus",
        "target_height",
        "yaw_offset",
        "pitch_offset",
        "distance_offset",
        "x",
        "y",
        "z",
        "target_node",
        "target",
        "seat",
        "lighting",
        "ambient",
        "background",
        "ambient_energy",
        "background_energy",
        "fog_enabled",
        "fog_density",
        "brightness",
        "mode",
    ):
        if key in data:
            params.setdefault(key, data.pop(key))

    if data.get("action") in {"follow_user", "sit", "stand", "environment_update", "gesture", "set_motion_profile"}:
        reserved = {"action", "actor_id", "priority", "reason", "expires_in_ms", "command_id", "params"}
        for key in list(data.keys()):
            if key not in reserved:
                params[key] = data.pop(key)
        data.pop("params", None)

    if "target" in params and "target_node" not in params:
        params["target_node"] = params.pop("target")

    if "seat" in params and "target_node" not in params:
        params["target_node"] = params.pop("seat")

    if "lighting" in params and not isinstance(params.get("lighting"), dict):
        maybe = str(params.pop("lighting")).strip().lower()
        if maybe:
            params.setdefault("lighting", {"mode": maybe})

    if "lighting" in params and "ambient_energy" not in params and "ambient" in params:
        params.setdefault("ambient_energy", params.pop("ambient"))
    if "background" in params and "background_energy" not in params:
        params.setdefault("background_energy", params.pop("background"))
    if "brightness" in params and "ambient_energy" not in params:
        brightness = _coerce_float(params.pop("brightness"))
        if brightness is not None:
            params["ambient_energy"] = brightness

    if "mode" in params and data.get("action") == "environment_update":
        mode = str(params.pop("mode")).strip().lower()
        if mode in {"bright", "brighten", "brightest", "bright_on", "brighten_up"}:
            params.setdefault("ambient_energy", 1.2)
            params.setdefault("background_energy", 1.0)
            params.setdefault("fog_enabled", False)
        elif mode in {"night", "dark", "dim", "dimmed"}:
            params.setdefault("ambient_energy", 0.23)
            params.setdefault("background_energy", 0.35)
            params.setdefault("fog_enabled", False)

    if "lighting" in params and isinstance(params.get("lighting"), dict):
        lighting = dict(params["lighting"])
        for key in ("ambient_energy", "background_energy", "fog_enabled", "fog_density"):
            if key in lighting and key not in params:
                params[key] = lighting[key]
        params.pop("lighting")

    data["params"] = params
    return data


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_intent_ms(value: Any) -> int:
    raw = _coerce_float(value)
    if raw is None:
        return 1000
    return max(100, min(60_000, int(raw)))


def _normalize_environment_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    if "ambient" in normalized and "ambient_energy" not in normalized:
        ambient = _coerce_float(normalized.pop("ambient"))
        if ambient is not None:
            normalized["ambient_energy"] = ambient
    if "background" in normalized and "background_energy" not in normalized:
        background = _coerce_float(normalized.pop("background"))
        if background is not None:
            normalized["background_energy"] = background
    if "brightness" in normalized and "ambient_energy" not in normalized:
        brightness = _coerce_float(normalized.pop("brightness"))
        if brightness is not None:
            normalized["ambient_energy"] = brightness
    if "fog" in normalized and "fog_enabled" not in normalized:
        value = str(normalized.pop("fog")).strip().lower()
        if value in {"true", "1", "on", "yes"}:
            normalized["fog_enabled"] = True
        elif value in {"false", "0", "off", "no"}:
            normalized["fog_enabled"] = False
    if "lighting" in normalized and isinstance(normalized.get("lighting"), dict):
        lighting = dict(normalized["lighting"])
        for key in ("ambient_energy", "background_energy", "fog_enabled", "fog_density", "ambient", "background"):
            if key in lighting and key not in normalized:
                normalized[key] = lighting[key]
        normalized.pop("lighting", None)

    if "mode" in normalized:
        mode = str(normalized.pop("mode")).strip().lower()
        if mode in {"bright", "brighten", "brightest", "bright_on", "brighten_up"}:
            normalized.setdefault("ambient_energy", 1.2)
            normalized.setdefault("background_energy", 1.0)
            normalized.setdefault("fog_enabled", False)
        elif mode in {"night", "dark", "dim", "dimmed", "night_mode"}:
            normalized.setdefault("ambient_energy", 0.23)
            normalized.setdefault("background_energy", 0.35)
            normalized.setdefault("fog_enabled", False)

    if "ambient_energy" in normalized and "ambient" in normalized:
        normalized.pop("ambient", None)
    if "background_energy" in normalized and "background" in normalized:
        normalized.pop("background", None)
    return normalized


def _is_user_target(target: str | None, fallback: Any = None) -> bool:
    if target:
        lowered = target.lower()
        return lowered in {"user", "player", "human", "あなた", "自分", "you"}
    fallback_value = _normalize_optional_text(fallback)
    return fallback_value is not None and fallback_value.lower() in {"user", "player", "human", "あなた", "自分", "you"}


def _coerce_position(values: dict[str, Any], direct: dict[str, Any] | None = None) -> Vector3Payload | None:
    merged = dict(values)
    if direct:
        merged.update({key: value for key, value in direct.items() if value is not None})
    position_value = merged.pop("position", None)
    if isinstance(position_value, dict):
        merged.update({k: merged.get(k, v) for k, v in position_value.items() if k in {"x", "y", "z"}})
    x = _coerce_float(merged.get("x"))
    y = _coerce_float(merged.get("y"))
    z = _coerce_float(merged.get("z"))
    if x is None or z is None:
        return None
    return Vector3Payload(x=x, y=float(y or 0.0), z=z)


def _intent_params(params: dict[str, Any], include_reserved: tuple[str, ...] = ()) -> dict[str, Any]:
    cleaned = dict(params or {})
    for key in include_reserved:
        if key in cleaned:
            cleaned.pop(key)
    return cleaned


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned


def _mood_to_environment_params(mood: str | None) -> dict[str, Any] | None:
    normalized = _normalize_optional_text(mood)
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered in {"night", "dark", "dim", "dimmed"}:
        return {"ambient_energy": 0.23, "background_energy": 0.35, "fog_enabled": False}
    if lowered in {"bright", "brighten", "brightest", "light", "day", "sunny"}:
        return {"ambient_energy": 1.2, "background_energy": 1.0, "fog_enabled": False}
    if lowered in {"mist", "fog", "hazy", "foggy"}:
        return {"fog_enabled": True, "fog_density": 0.02}
    return None


def _has_environment_fields(params: dict[str, Any]) -> bool:
    if not params:
        return False
    keys = set(params)
    if {"ambient_energy", "background_energy", "fog_enabled", "fog_density"} & keys:
        return True
    if {"ambient", "background", "brightness", "mode"} & keys:
        return True
    lighting = params.get("lighting")
    if isinstance(lighting, dict):
        return bool({"ambient_energy", "background_energy", "fog_enabled", "fog_density"} & set(lighting))
    return False


def _coerce_json_object(output: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(output, dict):
        return output
    raw = str(output or "").strip()
    if not raw:
        raise ValueError("empty LLM output")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("LLM output must be a JSON object")
    return obj


def vector_to_dict(vector: Vector3Payload | None) -> dict[str, float] | None:
    if vector is None:
        return None
    return vector.model_dump()
