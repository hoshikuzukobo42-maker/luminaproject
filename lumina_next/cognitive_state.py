from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .config import RuntimeConfig


DEFAULT_DRIVES = {
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
}


def _bounded(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


class CognitiveController:
    """Persistent, deterministic state used around the LLM, never as weight training."""

    def __init__(self, memory: Any, config: RuntimeConfig) -> None:
        self.memory = memory
        self.config = config

    def snapshot(self) -> dict[str, Any]:
        if hasattr(self.memory, "cognitive_state"):
            state = dict(self.memory.cognitive_state() or {})
        else:
            state = {}
        drives = dict(DEFAULT_DRIVES)
        drives.update(state.get("drives") or {})
        state["drives"] = {key: _bounded(value, DEFAULT_DRIVES[key]) for key, value in drives.items() if key in DEFAULT_DRIVES}
        state.setdefault("cognitive_state", "AWAKE")
        state.setdefault("active_goal", "世界を理解し、無理なく関係を育てる")
        state.setdefault("focus", "")
        now = time.time()
        state.setdefault("last_activity_at", now)
        state.setdefault("last_state_change_at", now)
        state.setdefault("sleep_started_at", None)
        state.setdefault("last_sleep_at", None)
        state.setdefault("last_deep_sleep_date", "")
        state.setdefault("updated_at", now)
        return state

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = time.time()
        if hasattr(self.memory, "save_cognitive_state"):
            self.memory.save_cognitive_state(state)
        return state

    def tick(self) -> dict[str, Any]:
        state = self.snapshot()
        now = time.time()
        elapsed_hours = max(0.0, min((now - float(state.get("updated_at") or now)) / 3600.0, 6.0))
        drives = state["drives"]
        sleeping = state["cognitive_state"] in {
            "WIND_DOWN",
            "NREM_CONSOLIDATION",
            "MEMORY_AUDIT",
            "SAFE_SIMULATION",
            "SLEEPING",
        }
        if sleeping:
            drives["fatigue"] = _bounded(drives["fatigue"] - 0.14 * elapsed_hours)
            drives["comfort"] = _bounded(drives["comfort"] + 0.04 * elapsed_hours)
        else:
            drives["fatigue"] = _bounded(drives["fatigue"] + 0.035 * elapsed_hours)
            drives["social_need"] = _bounded(drives["social_need"] + 0.012 * elapsed_hours)
            drives["loneliness"] = _bounded(drives["loneliness"] + 0.008 * elapsed_hours)
            drives["curiosity"] = _bounded(drives["curiosity"] + 0.01 * elapsed_hours)
            drives["novelty_need"] = _bounded(drives["novelty_need"] + 0.015 * elapsed_hours)
        return self._save(state)

    def is_sleeping(self) -> bool:
        return self.snapshot().get("cognitive_state") != "AWAKE"

    def wake(self, reason: str) -> dict[str, Any]:
        state = self.tick()
        if state["cognitive_state"] == "AWAKE":
            return state
        now = time.time()
        state["cognitive_state"] = "AWAKE"
        state["last_state_change_at"] = now
        state["last_sleep_at"] = now
        state["sleep_started_at"] = None
        state["focus"] = str(reason)[:500]
        state["drives"]["fatigue"] = min(state["drives"]["fatigue"], 0.25)
        state["drives"]["curiosity"] = max(state["drives"]["curiosity"], 0.45)
        return self._save(state)

    def begin_sleep(self, trigger: str, mode: str) -> dict[str, Any]:
        state = self.tick()
        now = time.time()
        state["cognitive_state"] = "NREM_CONSOLIDATION" if mode == "deep" else "MEMORY_AUDIT"
        state["last_state_change_at"] = now
        state["sleep_started_at"] = state.get("sleep_started_at") or now
        state["focus"] = f"memory_consolidation:{trigger}"[:500]
        return self._save(state)

    def finish_consolidation(
        self,
        result: dict[str, Any],
        *,
        mode: str,
        remain_asleep: bool,
    ) -> dict[str, Any]:
        state = self.snapshot()
        now = time.time()
        state["last_state_change_at"] = now
        state["focus"] = f"consolidation:{result.get('status', 'unknown')}"[:500]
        if mode == "deep" and result.get("ok"):
            state["last_deep_sleep_date"] = datetime.now().date().isoformat()
            state["drives"]["fatigue"] = max(0.08, state["drives"]["fatigue"] - 0.35)
            state["drives"]["unfinished_business"] = max(
                0.0, state["drives"]["unfinished_business"] - 0.08
            )
        if remain_asleep and result.get("ok"):
            state["cognitive_state"] = "SLEEPING"
        else:
            state["cognitive_state"] = "AWAKE"
            state["sleep_started_at"] = None
            if mode == "deep":
                state["last_sleep_at"] = now
        return self._save(state)

    def observe_world_action(
        self,
        command: str,
        *,
        notification_id: str,
        notification_kind: str,
        target: str = "",
        success: bool = True,
    ) -> dict[str, Any]:
        state = self.tick()
        drives = state["drives"]
        social = command.startswith("conversation") or command in {"speak", "talk", "reply", "join_conversation"}
        exploratory = command == "move" or command.startswith("get_")
        if social:
            drives["social_need"] = _bounded(drives["social_need"] - 0.18)
            drives["loneliness"] = _bounded(drives["loneliness"] - 0.15)
            drives["conversation_satisfaction"] = _bounded(
                drives["conversation_satisfaction"] + (0.06 if success else -0.08)
            )
            state["active_goal"] = "相手の反応を尊重し、会話を自然に続ける"
        elif exploratory:
            drives["curiosity"] = _bounded(drives["curiosity"] - 0.08)
            drives["novelty_need"] = _bounded(drives["novelty_need"] - 0.1)
            state["active_goal"] = "周囲を理解し、次の自然な交流機会を探す"
        elif command == "wait":
            drives["comfort"] = _bounded(drives["comfort"] + 0.03)
            drives["fatigue"] = _bounded(drives["fatigue"] - 0.01)
        drives["fatigue"] = _bounded(drives["fatigue"] + 0.015)
        state["last_activity_at"] = time.time()
        state["focus"] = f"{command}:{target}"[:500]
        if hasattr(self.memory, "record_event"):
            self.memory.record_event(
                event_type="karakuri_world_action",
                content=f"command={command}; target={target}; success={success}",
                source_type="world_observation",
                source_actor="karakuri_world",
                session_id="karakuri_world",
                importance=0.65 if social else 0.45,
                metadata={
                    "notification_id": notification_id,
                    "notification_kind": notification_kind,
                    "command": command,
                    "target": target,
                    "success": success,
                },
            )
        return self._save(state)

    # Preserved for the dormant Autonomy API; Qwen product startup keeps the
    # supervisor disabled unless a future, separately accepted profile opts in.
    def observe_autonomous_action(
        self,
        action: str,
        *,
        target: str = "",
        success: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply a self-initiated local action without mislabeling it as Karakuri input."""
        state = self.tick()
        drives = state["drives"]
        normalized = str(action or "observe").strip().lower()
        if normalized == "speak":
            drives["social_need"] = _bounded(drives["social_need"] - 0.1 if success else drives["social_need"] + 0.02)
            drives["loneliness"] = _bounded(drives["loneliness"] - 0.08 if success else drives["loneliness"] + 0.02)
            drives["conversation_satisfaction"] = _bounded(
                drives["conversation_satisfaction"] + (0.05 if success else -0.05)
            )
            state["active_goal"] = "自分の関心を表現し、相手の反応を待つ"
        elif normalized in {"move", "look", "gesture", "observe"}:
            drives["curiosity"] = _bounded(drives["curiosity"] - (0.06 if success else -0.01))
            drives["novelty_need"] = _bounded(drives["novelty_need"] - (0.05 if success else -0.01))
            state["active_goal"] = "周囲を観察し、次に関わる対象を選ぶ"
        elif normalized in {"rest", "sleep"}:
            drives["comfort"] = _bounded(drives["comfort"] + 0.04)
            drives["fatigue"] = _bounded(drives["fatigue"] - 0.03)
            state["active_goal"] = "疲労を整え、記憶と関心を保つ"
        drives["fatigue"] = _bounded(drives["fatigue"] + (0.008 if normalized != "sleep" else -0.05))
        state["last_activity_at"] = time.time()
        state["focus"] = f"autonomy:{normalized}:{target}"[:500]
        if hasattr(self.memory, "record_event"):
            self.memory.record_event(
                event_type="autonomy_action_observed",
                content=f"action={normalized}; target={target}; success={success}; reason={reason}",
                source_type="lumina_generated",
                source_actor="lumina_autonomy",
                session_id="lumina:autonomy",
                importance=0.6 if normalized == "speak" else 0.4,
                metadata={
                    "action": normalized,
                    "target": target,
                    "success": success,
                    "reason": str(reason)[:500],
                },
            )
        return self._save(state)

    def observe_human_interaction(self, user_id: str) -> dict[str, Any]:
        state = self.wake("human_interaction") if self.is_sleeping() else self.tick()
        state["last_activity_at"] = time.time()
        state["drives"]["social_need"] = _bounded(state["drives"]["social_need"] - 0.12)
        state["drives"]["loneliness"] = _bounded(state["drives"]["loneliness"] - 0.1)
        state["drives"]["fatigue"] = _bounded(state["drives"]["fatigue"] + 0.02)
        state["focus"] = f"conversation_with:{str(user_id)[:200]}"
        return self._save(state)

    def within_sleep_window(self, hour: int | None = None) -> bool:
        current_hour = datetime.now().hour if hour is None else int(hour)
        start = self.config.sleep_start_hour
        wake = self.config.sleep_wake_hour
        if start == wake:
            return False
        if start < wake:
            return start <= current_hour < wake
        return current_hour >= start or current_hour < wake

    def deep_sleep_due(self) -> tuple[bool, str]:
        if not self.config.sleep_enabled:
            return False, "disabled"
        state = self.tick()
        today = datetime.now().date().isoformat()
        if self.within_sleep_window() and state.get("last_deep_sleep_date") != today:
            return True, "circadian_window"
        idle_seconds = max(0.0, time.time() - float(state.get("last_activity_at") or time.time()))
        if state["drives"]["fatigue"] >= 0.88 and idle_seconds >= self.config.sleep_idle_seconds:
            return True, "fatigue_and_idle"
        return False, "not_due"

    def should_wake(self) -> bool:
        state = self.snapshot()
        if state["cognitive_state"] == "AWAKE":
            return False
        started = float(state.get("sleep_started_at") or time.time())
        return (
            not self.within_sleep_window()
            and time.time() - started >= self.config.sleep_min_duration_seconds
        )

    def prompt_context(self) -> str:
        state = self.snapshot()
        payload = {
            "state": state["cognitive_state"],
            "active_goal": state["active_goal"],
            "focus": state["focus"],
            "drives": {key: round(float(value), 2) for key, value in state["drives"].items()},
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
