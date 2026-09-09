"""Pure, defensive human-autonomy planning for Lumina.

The planner describes intent only.  It does not move an avatar, send speech,
write memory, read a clock, or call a provider.  A caller supplies an
observation snapshot and receives a bounded plan of state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Mapping, Sequence


class AutonomyState(str, Enum):
    """States in the human-autonomy state machine."""

    OBSERVE = "observe"
    DECIDE = "decide"
    MOVE = "move"
    APPROACH = "approach"
    GREET = "greet"
    CONVERSE = "converse"
    WAIT = "wait"
    REFLECT = "reflect"
    REST = "rest"
    SLEEP = "sleep"
    RECOVER = "recover"


MOVEMENT_STATES = frozenset({AutonomyState.MOVE, AutonomyState.APPROACH})
ALL_STATES = frozenset(AutonomyState)


def _bounded(value: Any, default: float = 0.5) -> float:
    """Return a finite float in [0, 1], without allowing bad input to escape."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, result)) if isfinite(result) else default


def _non_negative(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, result) if isfinite(result) else default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _text(value: Any) -> str:
    return str(value or "").strip()[:200]


def _state(value: Any, default: AutonomyState = AutonomyState.OBSERVE) -> AutonomyState:
    if isinstance(value, AutonomyState):
        return value
    try:
        return AutonomyState(str(value).strip().casefold())
    except (TypeError, ValueError):
        return default


def _sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_text(value),) if value.strip() else ()
    if not isinstance(value, Sequence):
        return ()
    return tuple(_text(item).casefold() for item in value if _text(item))[-32:]


def _cooldowns(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, remaining in value.items():
        action = _text(key).casefold()
        if action:
            result[action] = _non_negative(remaining)
    return result


@dataclass(frozen=True)
class AutonomyObservation:
    """Read-only input to a planning turn.

    Drive values and cooldowns are normalized by :meth:`from_mapping`.
    ``cooldowns`` contains remaining seconds, not wall-clock timestamps.
    """

    current_state: AutonomyState = AutonomyState.OBSERVE
    human_present: bool = False
    human_engaged: bool = False
    human_spoke: bool = False
    human_id: str = ""
    human_distance_m: float | None = None
    movement_allowed: bool = True
    safe_to_approach: bool = True
    sleep_due: bool = False
    recovery_needed: bool = False
    interest: float = 0.5
    fatigue: float = 0.2
    relationship: float = 0.5
    cooldowns: Mapping[str, float] = field(default_factory=dict)
    recent_actions: Sequence[str] = field(default_factory=tuple)
    recent_states: Sequence[str] = field(default_factory=tuple)
    max_turns: int = 6

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "AutonomyObservation":
        """Build a safe snapshot from an untrusted mapping.

        Common aliases are accepted to keep the boundary tolerant of world
        adapters while preserving one canonical internal representation.
        """

        data = value if isinstance(value, Mapping) else {}
        distance_value = data.get("human_distance_m", data.get("distance_m"))
        distance: float | None
        try:
            parsed_distance = float(distance_value)
            distance = parsed_distance if isfinite(parsed_distance) and parsed_distance >= 0 else None
        except (TypeError, ValueError):
            distance = None
        max_turns = int(_non_negative(data.get("max_turns", 6), 6.0))
        return cls(
            current_state=_state(data.get("current_state", data.get("state"))),
            human_present=_bool(data.get("human_present", data.get("human_nearby", False))),
            human_engaged=_bool(data.get("human_engaged", data.get("conversation_active", False))),
            human_spoke=_bool(data.get("human_spoke", data.get("input_received", False))),
            human_id=_text(data.get("human_id", data.get("user_id", ""))),
            human_distance_m=distance,
            movement_allowed=_bool(data.get("movement_allowed", data.get("can_move", True)), True),
            safe_to_approach=_bool(data.get("safe_to_approach", data.get("safe", True)), True),
            sleep_due=_bool(data.get("sleep_due", False)),
            recovery_needed=_bool(data.get("recovery_needed", data.get("needs_recovery", False))),
            interest=_bounded(data.get("interest", data.get("curiosity", 0.5))),
            fatigue=_bounded(data.get("fatigue", 0.2), 0.2),
            relationship=_bounded(data.get("relationship", data.get("rapport", 0.5))),
            cooldowns=_cooldowns(data.get("cooldowns", {})),
            recent_actions=_sequence(data.get("recent_actions", data.get("action_history", ()))),
            recent_states=_sequence(data.get("recent_states", data.get("state_history", ()))),
            max_turns=max(2, min(6, max_turns)),
        )


@dataclass(frozen=True)
class PlanStep:
    """One declarative state in an autonomy plan."""

    state: AutonomyState
    reason: str = ""
    target: str = ""

    @property
    def action(self) -> str:
        """String action alias for adapters that do not use enums."""

        return self.state.value

    def as_dict(self) -> dict[str, str]:
        result = {"state": self.state.value, "action": self.state.value}
        if self.reason:
            result["reason"] = self.reason
        if self.target:
            result["target"] = self.target
        return result


@dataclass(frozen=True)
class AutonomyPlan:
    """A bounded, side-effect-free plan returned by the planner."""

    steps: tuple[PlanStep, ...]
    selected_state: AutonomyState
    rationale: str
    blocked_by: tuple[str, ...] = ()

    @property
    def states(self) -> tuple[str, ...]:
        return tuple(step.state.value for step in self.steps)

    @property
    def actions(self) -> tuple[str, ...]:
        return self.states

    @property
    def turn_count(self) -> int:
        return len(self.steps)

    @property
    def contains_movement(self) -> bool:
        return any(step.state in MOVEMENT_STATES for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "states": list(self.states),
            "steps": [step.as_dict() for step in self.steps],
            "selected_state": self.selected_state.value,
            "rationale": self.rationale,
            "blocked_by": list(self.blocked_by),
        }


def cooldown_remaining(cooldowns: Mapping[str, Any] | None, action: str) -> float:
    """Return remaining cooldown seconds for an action, including state aliases."""

    values = _cooldowns(cooldowns)
    key = _text(action).casefold()
    aliases = {
        "approach": ("approach", "approach_user"),
        "converse": ("converse", "conversation", "speak", "talk"),
        "greet": ("greet", "greeting", "speech"),
        "move": ("move", "walk", "walk_to"),
    }
    return max((values.get(candidate, 0.0) for candidate in aliases.get(key, (key,))), default=0.0)


class HumanAutonomy:
    """Deterministic human-like autonomy planner with no runtime side effects.

    Every plan contains two to six states.  At most one movement state is
    emitted, and every movement plan settles into ``reflect`` or ``wait``.
    This makes repeated movement-only loops impossible at this boundary.
    """

    def __init__(
        self,
        *,
        approach_distance_m: float = 1.6,
        rest_fatigue: float = 0.72,
        sleep_fatigue: float = 0.92,
        movement_interest: float = 0.62,
    ) -> None:
        self.approach_distance_m = max(0.0, _non_negative(approach_distance_m, 1.6))
        self.rest_fatigue = _bounded(rest_fatigue, 0.72)
        self.sleep_fatigue = _bounded(sleep_fatigue, 0.92)
        self.movement_interest = _bounded(movement_interest, 0.62)

    def observe(self, value: AutonomyObservation | Mapping[str, Any] | None) -> AutonomyObservation:
        """Normalize an observation without mutating it or any caller-owned data."""

        if isinstance(value, AutonomyObservation):
            return value
        return AutonomyObservation.from_mapping(value)

    def decide(self, value: AutonomyObservation | Mapping[str, Any] | None) -> AutonomyState:
        """Select the next meaningful state from one observation snapshot."""

        observation = self.observe(value)
        state, _, _ = self._decision(observation)
        return state

    def plan(self, value: AutonomyObservation | Mapping[str, Any] | None) -> AutonomyPlan:
        """Return a bounded declarative plan for the supplied snapshot."""

        observation = self.observe(value)
        selected, blocked, rationale = self._decision(observation)
        steps = self._build_steps(observation, selected, rationale)
        limit = max(2, min(6, observation.max_turns))
        return AutonomyPlan(
            steps=tuple(steps[:limit]),
            selected_state=selected,
            rationale=rationale,
            blocked_by=tuple(blocked),
        )

    build_plan = plan

    def _decision(self, observation: AutonomyObservation) -> tuple[AutonomyState, list[str], str]:
        blocked: list[str] = []
        if observation.recovery_needed:
            return AutonomyState.RECOVER, blocked, "recovery is explicitly required"
        if observation.sleep_due or observation.fatigue >= self.sleep_fatigue:
            return AutonomyState.SLEEP, blocked, "sleep is due or fatigue is high"

        movement_streak = sum(
            1 for action in tuple(observation.recent_actions) + tuple(observation.recent_states)
            if action in {state.value for state in MOVEMENT_STATES} or action in {"walk", "walk_to", "approach_user"}
        )
        movement_blocked = not observation.movement_allowed
        if movement_blocked:
            blocked.append("movement_not_allowed")
        if not observation.safe_to_approach:
            blocked.append("approach_not_safe")
        if movement_streak >= 2:
            blocked.append("movement_loop_guard")

        human_active = observation.human_present and (
            observation.human_engaged or observation.human_spoke
        )
        if human_active:
            if observation.human_distance_m is not None and observation.human_distance_m > self.approach_distance_m:
                if movement_blocked or not observation.safe_to_approach or movement_streak >= 2:
                    return AutonomyState.CONVERSE, blocked, "human is active; conversation takes priority over blocked approach"
                if cooldown_remaining(observation.cooldowns, "approach") > 0:
                    blocked.append("approach_cooldown")
                else:
                    return AutonomyState.APPROACH, blocked, "human is active and outside the comfortable distance"
            if cooldown_remaining(observation.cooldowns, "converse") > 0:
                blocked.append("conversation_cooldown")
            else:
                return AutonomyState.CONVERSE, blocked, "human engagement or speech is present"

        if observation.human_present:
            if cooldown_remaining(observation.cooldowns, "greet") > 0:
                blocked.append("greeting_cooldown")
            elif observation.fatigue < self.rest_fatigue:
                return AutonomyState.GREET, blocked, "a nearby human has not been greeted recently"

        if observation.fatigue >= self.rest_fatigue:
            return AutonomyState.REST, blocked, "fatigue is above the rest threshold"
        if (
            observation.interest >= self.movement_interest
            and observation.movement_allowed
            and observation.safe_to_approach
            and movement_streak < 2
            and cooldown_remaining(observation.cooldowns, "move") <= 0
        ):
            return AutonomyState.MOVE, blocked, "interest supports one bounded exploratory movement"
        if observation.interest < 0.35 or observation.relationship >= 0.8:
            return AutonomyState.REFLECT, blocked, "low novelty or a strong relationship favors reflection"
        return AutonomyState.WAIT, blocked, "no socially useful or safe action is currently due"

    @staticmethod
    def _step(state: AutonomyState, rationale: str, target: str = "") -> PlanStep:
        return PlanStep(state=state, reason=rationale, target=target)

    def _build_steps(
        self,
        observation: AutonomyObservation,
        selected: AutonomyState,
        rationale: str,
    ) -> list[PlanStep]:
        start = [
            self._step(AutonomyState.OBSERVE, "sample the current world snapshot"),
            self._step(AutonomyState.DECIDE, rationale),
        ]
        target = observation.human_id
        if selected is AutonomyState.RECOVER:
            return start + [self._step(AutonomyState.RECOVER, rationale), self._step(AutonomyState.REFLECT, "reassess after recovery")]
        if selected is AutonomyState.SLEEP:
            return start + [self._step(AutonomyState.REST, "reduce stimulation before sleep"), self._step(AutonomyState.SLEEP, rationale)]
        if selected is AutonomyState.APPROACH:
            if observation.human_engaged or observation.human_spoke:
                return start + [self._step(AutonomyState.APPROACH, rationale, target), self._step(AutonomyState.GREET, "arrive respectfully", target), self._step(AutonomyState.CONVERSE, "respond after arrival", target), self._step(AutonomyState.WAIT, "leave room for the human response")]
            return start + [self._step(AutonomyState.APPROACH, rationale, target), self._step(AutonomyState.GREET, "arrive respectfully", target), self._step(AutonomyState.WAIT, "leave room for the human response")]
        if selected is AutonomyState.MOVE:
            return start + [self._step(AutonomyState.MOVE, rationale), self._step(AutonomyState.REFLECT, "evaluate the result of movement"), self._step(AutonomyState.WAIT, "avoid immediate repeated movement")]
        if selected is AutonomyState.GREET:
            return start + [self._step(AutonomyState.GREET, rationale, target), self._step(AutonomyState.CONVERSE, "offer conversation only if welcomed", target), self._step(AutonomyState.WAIT, "leave room for the human response")]
        if selected is AutonomyState.CONVERSE:
            return start + [self._step(AutonomyState.CONVERSE, rationale, target), self._step(AutonomyState.REFLECT, "notice the human response", target), self._step(AutonomyState.WAIT, "pause before another initiative")]
        if selected is AutonomyState.REST:
            return start + [self._step(AutonomyState.REST, rationale), self._step(AutonomyState.REFLECT, "check whether rest helped")]
        if selected is AutonomyState.REFLECT:
            return start + [self._step(AutonomyState.REFLECT, rationale), self._step(AutonomyState.WAIT, "let the next observation arrive")]
        return start + [self._step(AutonomyState.WAIT, rationale), self._step(AutonomyState.REFLECT, "retain a quiet reassessment point")]


# Explicit aliases make the contract discoverable to small adapters and tests.
HumanAutonomyStateMachine = HumanAutonomy
HumanAutonomyPlanner = HumanAutonomy
AutonomyStateMachine = HumanAutonomy


def build_plan(observation: AutonomyObservation | Mapping[str, Any] | None) -> AutonomyPlan:
    """Convenience entry point using the conservative default thresholds."""

    return HumanAutonomy().plan(observation)


def decide_next_state(observation: AutonomyObservation | Mapping[str, Any] | None) -> AutonomyState:
    """Convenience entry point for callers that only need one transition."""

    return HumanAutonomy().decide(observation)


__all__ = [
    "ALL_STATES",
    "MOVEMENT_STATES",
    "AutonomyObservation",
    "AutonomyPlan",
    "AutonomyState",
    "AutonomyStateMachine",
    "HumanAutonomy",
    "HumanAutonomyPlanner",
    "HumanAutonomyStateMachine",
    "PlanStep",
    "build_plan",
    "cooldown_remaining",
    "decide_next_state",
]
