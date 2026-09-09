from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lumina_next.autonomy_supervisor import AutonomySupervisor
from lumina_next.providers.llm import LLMResult


ROOT = Path(__file__).resolve().parents[1]


def _inventory_job() -> dict[str, Any]:
    return {
        "request_id": "prior-visual-observation",
        "status": "observed",
        "recognition_basis": "raw_eye_image_only",
        "image_sha256": "a" * 64,
        "finished_unix": time.time(),
        "detection": {
            "visible": True,
            "perception_audit": {
                "basis": "target_free_image_inventory_then_deterministic_selection",
                "inventory": [
                    {
                        "id": 0,
                        "label": "sofa",
                        "color": "purple",
                        "bbox": [100, 200, 700, 850],
                    }
                ],
            },
        },
    }


class FakeVisualNavigation:
    def __init__(self, *, returned_status: str = "observing") -> None:
        self.active_id: str | None = None
        self.lock = asyncio.Lock()
        self.jobs = {"prior-visual-observation": _inventory_job()}
        self.returned_status = returned_status
        self.portable_ready = True
        self.calls: list[dict[str, Any]] = []

    def portable_navigation_enabled(self) -> bool:
        return self.portable_ready

    @staticmethod
    def _portable_detection_descriptor(item: dict[str, Any]) -> tuple[str, str, str]:
        if item.get("label") != "sofa" or item.get("color") != "purple":
            raise ValueError("unvalidated visual descriptor")
        return "sofa", "sofa", "purple"

    async def start(
        self,
        text: str,
        request_id: str,
        *,
        speak: bool,
        allow_move: bool,
        only_if_idle: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "text": text,
                "request_id": request_id,
                "speak": speak,
                "allow_move": allow_move,
                "only_if_idle": only_if_idle,
            }
        )
        job = {
            "request_id": request_id,
            "status": self.returned_status,
            "arrived": self.returned_status == "arrived",
        }
        if isinstance(self.returned_status, str) and self.returned_status in {
            "arrived",
            "observed",
            "not_visible",
            "ambiguous",
            "failed",
            "cancelled",
        }:
            job["finished_unix"] = time.time()
        self.jobs[request_id] = job
        if isinstance(self.returned_status, str) and self.returned_status in {
            "observing",
            "moving",
        }:
            self.active_id = request_id
        return dict(job)


class FakeMemory:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def wake_brief(self, *, limit: int) -> dict[str, Any]:
        return {"limit": limit, "items": []}

    def record_event(self, **payload: Any) -> None:
        self.events.append(payload)


class FakeCognition:
    def __init__(self) -> None:
        self.observed: list[dict[str, Any]] = []

    def tick(self) -> dict[str, Any]:
        return {"cognitive_state": "AWAKE", "drives": {}}

    def deep_sleep_due(self) -> tuple[bool, str]:
        return False, ""

    def observe_autonomous_action(
        self, action: str, *, target: str, success: bool, reason: str
    ) -> None:
        self.observed.append(
            {"action": action, "target": target, "success": success, "reason": reason}
        )


class FakeSleepCycle:
    def status(self) -> dict[str, Any]:
        return {"enabled": True, "run_active": False}


class FakeBridge:
    def __init__(self) -> None:
        self.body_calls: list[dict[str, Any]] = []

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "godot_connected": True,
            "autonomy_state": {
                "energy": 0.8,
                "curiosity": 0.7,
                "social_attention": 0.2,
                "looking_at": "Chair_A",
                "next_desire": "walk_to:Chair_A",
                "last_observation": ["Chair_A", {"position": [1, 2, 3]}],
            },
        }

    async def dispatch_action(self, action: str, **payload: Any) -> dict[str, Any]:
        self.body_calls.append({"action": action, **payload})
        raise AssertionError("spatial action bypassed VisualNavigation")


class FakeLLM:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.messages: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **_kwargs: Any) -> LLMResult:
        self.messages.append(messages)
        return LLMResult(
            text=json.dumps(self.plan, ensure_ascii=False),
            model="qwen3.5-4b-q4_K_M",
            latency_ms=1.0,
        )


class FakeOrchestrator:
    def __init__(
        self,
        plan: dict[str, Any] | None = None,
        *,
        active_sequences: list[list[str]] | None = None,
    ) -> None:
        self.config = SimpleNamespace(
            runtime_root=ROOT,
            llm_provider="llama_cpp",
            bridge_enabled=True,
            tts_enabled=False,
            num_ctx=1024,
        )
        self.resources = SimpleNamespace(
            snapshot=self._resource_snapshot,
        )
        self.memory = FakeMemory()
        self.cognition = FakeCognition()
        self.sleep_cycle = FakeSleepCycle()
        self.bridge = FakeBridge()
        self.llm = FakeLLM(plan or {"action": "rest", "reason": "test"})
        self._active_sequences = list(active_sequences or [[]])

    @staticmethod
    async def _resource_snapshot() -> dict[str, Any]:
        return {"pressure": "normal", "memory_free_percent": 70.0}

    async def active_request_ids(self) -> list[str]:
        if len(self._active_sequences) > 1:
            return self._active_sequences.pop(0)
        return list(self._active_sequences[0])


def _context(candidates: list[str] | None = None) -> dict[str, Any]:
    return {
        "bridge": {"godot_connected": True},
        "visual_observation": {
            "available": bool(candidates),
            "busy": False,
            "candidates": candidates or [],
            "basis": "recent_target_free_eye_inventory" if candidates else "none",
        },
    }


def test_move_tick_uses_pixel_candidate_and_shared_visual_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    monkeypatch.delenv("LUMINA_LLM_DISABLED", raising=False)
    orchestrator = FakeOrchestrator(
        {
            "action": "move",
            "reason": "画像で確認した対象へ近づく",
            "target_description": "purple sofa",
        }
    )
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        visual,
    )

    result = asyncio.run(supervisor.tick("mock-integration"))

    assert result["status"] == "action_started"
    assert result["ok"] is True
    assert result["plan"]["visual_target_verified"] is True
    assert result["plan"]["target_node"] == ""
    assert result["plan"]["position"] is None
    assert result["execution"]["completed"] is False
    assert result["execution"]["succeeded"] is None
    assert len(visual.calls) == 1
    assert visual.calls[0]["text"] == "go to the purple sofa"
    assert visual.calls[0]["allow_move"] is True
    assert visual.calls[0]["speak"] is False
    assert visual.calls[0]["only_if_idle"] is True
    assert orchestrator.bridge.body_calls == []
    planner_wire = json.dumps(orchestrator.llm.messages, ensure_ascii=False)
    assert "Chair_A" not in planner_wire
    assert '"position"' not in planner_wire
    assert "purple sofa" in planner_wire
    assert orchestrator.cognition.observed == []
    status = supervisor.status()
    assert status["action_count"] == 1
    assert status["completed_action_count"] == 0
    assert status["succeeded_action_count"] == 0
    assert status["pending_visual_action"]["action"] == "move"


def test_targeted_look_is_visual_only_and_arrival_is_a_separate_completion() -> None:
    orchestrator = FakeOrchestrator()
    visual = FakeVisualNavigation(returned_status="observed")
    supervisor = AutonomySupervisor(orchestrator, SimpleNamespace(), visual)
    plan = supervisor._normalize_plan(
        {"action": "look", "target_description": "purple sofa", "reason": "確認"},
        _context(["purple sofa"]),
    )

    result = asyncio.run(supervisor._execute(plan))

    assert result["status"] == "visual_navigation_completed"
    assert result["completed"] is True
    assert result["succeeded"] is True
    assert result["arrived"] is False
    assert visual.calls[0]["text"] == "where is purple sofa"
    assert visual.calls[0]["allow_move"] is False
    assert visual.calls[0]["only_if_idle"] is True


def test_pending_move_collects_stable_arrival_once_on_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    orchestrator = FakeOrchestrator(
        {"action": "move", "target_description": "purple sofa", "reason": "確認済み"}
    )
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        visual,
    )
    started = asyncio.run(supervisor.tick("start"))
    request_id = started["execution"]["visual_request_id"]
    visual.jobs[request_id].update(
        status="arrived",
        arrived=True,
        finished_unix=time.time(),
    )
    visual.active_id = None

    completed = asyncio.run(supervisor.tick("collect"))

    assert completed["status"] == "action_completed"
    assert completed["execution"]["visual_job_status"] == "arrived"
    assert completed["execution"]["succeeded"] is True
    assert len(orchestrator.llm.messages) == 1
    assert orchestrator.cognition.observed == [
        {
            "action": "move",
            "target": "purple sofa",
            "success": True,
            "reason": "確認済み",
        }
    ]
    assert supervisor.status()["pending_visual_action"] is None
    assert supervisor.status()["completed_action_count"] == 1
    assert supervisor.status()["succeeded_action_count"] == 1
    assert supervisor._reconcile_pending_visual_action("duplicate") is None
    assert supervisor.status()["completed_action_count"] == 1
    assert len(orchestrator.cognition.observed) == 1


def test_terminal_failure_is_recorded_then_a_later_action_can_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    orchestrator = FakeOrchestrator(
        {"action": "move", "target_description": "purple sofa", "reason": "first"}
    )
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        visual,
    )
    started = asyncio.run(supervisor.tick("start"))
    request_id = started["execution"]["visual_request_id"]
    visual.jobs[request_id].update(
        status="failed",
        arrived=False,
        finished_unix=time.time(),
        error="movement_blocked",
    )
    visual.active_id = None

    failed = asyncio.run(supervisor.tick("collect-failure"))

    assert failed["status"] == "action_failed"
    assert failed["ok"] is False
    assert orchestrator.cognition.observed[-1]["success"] is False
    assert supervisor.status()["pending_visual_action"] is None
    assert supervisor.status()["completed_action_count"] == 1
    assert supervisor.status()["succeeded_action_count"] == 0

    orchestrator.llm.plan = {
        "action": "look",
        "target_description": "purple sofa",
        "reason": "recover",
    }
    visual.returned_status = "observed"
    recovered = asyncio.run(supervisor.tick("recover"))

    assert recovered["status"] == "acted"
    assert recovered["execution"]["succeeded"] is True
    assert supervisor.status()["completed_action_count"] == 2
    assert supervisor.status()["succeeded_action_count"] == 1
    assert [entry["success"] for entry in orchestrator.cognition.observed] == [False, True]


def test_missing_owned_job_blocks_new_planning_until_evidence_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    orchestrator = FakeOrchestrator(
        {"action": "move", "target_description": "purple sofa"}
    )
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        visual,
    )
    started = asyncio.run(supervisor.tick("start"))
    request_id = started["execution"]["visual_request_id"]
    visual.jobs.pop(request_id)
    visual.active_id = None

    unknown = asyncio.run(supervisor.tick("missing"))

    assert unknown["status"] == "action_completion_unknown"
    assert unknown["ok"] is False
    assert supervisor.status()["pending_visual_action"]["request_id"] == request_id
    assert len(orchestrator.llm.messages) == 1
    assert len(visual.calls) == 1
    assert orchestrator.cognition.observed == []

    visual.jobs[request_id] = {
        "request_id": request_id,
        "status": "arrived",
        "arrived": True,
        "finished_unix": time.time(),
    }
    recovered = asyncio.run(supervisor.tick("evidence-returned"))
    assert recovered["status"] == "action_completed"
    assert supervisor.status()["pending_visual_action"] is None


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (
            {
                "action": "move",
                "target_description": "purple sofa",
                "target_node": "Sofa",
            },
            "native_spatial_reference_forbidden",
        ),
        (
            {
                "action": "move",
                "target_description": "purple sofa",
                "position": {"x": 1, "y": 0, "z": 2},
            },
            "native_spatial_reference_forbidden",
        ),
        (
            {"action": "move", "target_description": "green plant"},
            "visual_target_not_observed",
        ),
    ],
)
def test_native_or_unobserved_spatial_targets_fail_without_substitute(
    raw: dict[str, Any], error: str
) -> None:
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(FakeOrchestrator(), SimpleNamespace(), visual)

    plan = supervisor._normalize_plan(raw, _context(["purple sofa"]))
    result = asyncio.run(supervisor._execute(plan))

    assert plan["action"] == "move"
    assert error in plan["validation_errors"]
    assert result["status"] == "plan_rejected"
    assert result["ok"] is False
    assert result["executed"] is False
    assert visual.calls == []


def test_observe_is_not_reported_as_success_without_an_observation_contract() -> None:
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(FakeOrchestrator(), SimpleNamespace(), visual)
    plan = supervisor._normalize_plan({"action": "observe"}, _context())

    result = asyncio.run(supervisor._execute(plan))

    assert result == {
        "ok": False,
        "executed": False,
        "completed": False,
        "succeeded": False,
        "accepted": False,
        "status": "visual_observation_not_supported",
    }
    assert visual.calls == []


def test_portable_depth_map_contract_is_required_before_visual_start() -> None:
    visual = FakeVisualNavigation()
    visual.portable_ready = False
    supervisor = AutonomySupervisor(FakeOrchestrator(), SimpleNamespace(), visual)
    plan = supervisor._normalize_plan(
        {"action": "move", "target_description": "purple sofa"},
        _context(["purple sofa"]),
    )

    result = asyncio.run(supervisor._execute(plan))

    assert result["status"] == "portable_navigation_required"
    assert result["executed"] is False
    assert visual.calls == []


def test_malformed_visual_start_status_fails_closed_without_tick_exception() -> None:
    visual = FakeVisualNavigation(returned_status=[])  # type: ignore[arg-type]
    supervisor = AutonomySupervisor(FakeOrchestrator(), SimpleNamespace(), visual)
    plan = supervisor._normalize_plan(
        {"action": "look", "target_description": "purple sofa"},
        _context(["purple sofa"]),
    )

    result = asyncio.run(supervisor._execute(plan))

    assert result["status"] == "visual_navigation_invalid_response"
    assert result["executed"] is False


def test_local_tts_generation_is_not_reported_as_playback_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    orchestrator = FakeOrchestrator(
        {"action": "speak", "text": "ここにいるよ", "reason": "声をかける"}
    )
    orchestrator.config.bridge_enabled = False
    orchestrator.config.tts_enabled = True

    class TTS:
        async def synthesize_first_chunk(
            self, text: str, emotion: str, speed: float
        ) -> tuple[Any, list[str]]:
            return (
                SimpleNamespace(
                    text=text,
                    audio_base64="ZmFrZQ==",
                    latency_ms=2.0,
                    payload={"emotion": emotion, "speed": speed},
                ),
                [text],
            )

    orchestrator.tts = TTS()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        FakeVisualNavigation(),
    )

    result = asyncio.run(supervisor.tick("tts-generation"))

    assert result["status"] == "action_started"
    assert result["execution"]["status"] == "tts_generated"
    assert result["execution"]["output_generated"] is True
    assert result["execution"]["completed"] is False
    assert result["execution"]["succeeded"] is None
    assert orchestrator.cognition.observed == []
    assert supervisor.status()["completed_action_count"] == 0


@pytest.mark.parametrize("busy_source", ["request", "visual_job", "visual_lock"])
def test_human_or_visual_busy_state_prevents_autonomous_start(busy_source: str) -> None:
    active = [["human-chat"]] if busy_source == "request" else [[]]
    orchestrator = FakeOrchestrator(active_sequences=active)
    visual = FakeVisualNavigation()
    if busy_source == "visual_job":
        visual.active_id = "human-visual"
    if busy_source == "visual_lock":
        asyncio.run(visual.lock.acquire())
    supervisor = AutonomySupervisor(orchestrator, SimpleNamespace(), visual)
    plan = supervisor._normalize_plan(
        {"action": "move", "target_description": "purple sofa"},
        _context(["purple sofa"]),
    )

    result = asyncio.run(supervisor._execute(plan))

    expected = "deferred_user_request" if busy_source == "request" else "visual_navigation_busy"
    assert result["status"] == expected
    assert result["executed"] is False
    assert visual.calls == []


def test_request_arriving_after_plan_defers_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    orchestrator = FakeOrchestrator(
        {"action": "move", "target_description": "purple sofa"},
        active_sequences=[[], ["human-arrived-during-plan"]],
    )
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator,
        SimpleNamespace(health=lambda: {"configured": False}),
        visual,
    )

    result = asyncio.run(supervisor.tick("race"))

    assert result["status"] == "deferred_user_request"
    assert result["phase"] == "pre_execution"
    assert result["active_request_ids"] == ["human-arrived-during-plan"]
    assert visual.calls == []


def test_planner_is_blocked_when_shared_heavy_phase_contract_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.delenv("LUMINA_VISUAL_PHASE_SERIALIZATION", raising=False)
    orchestrator = FakeOrchestrator({"action": "rest"})
    supervisor = AutonomySupervisor(orchestrator, SimpleNamespace(), FakeVisualNavigation())

    result = asyncio.run(supervisor.tick("resource-contract"))

    assert result["status"] == "blocked_planner_resource_contract"
    assert result["ok"] is False
    assert orchestrator.llm.messages == []


@pytest.mark.parametrize("action", ["move", "look", "speak", "gesture", "rest", "sleep"])
@pytest.mark.parametrize("busy_source", ["active", "lock", "task", "unconfirmed_stop"])
@pytest.mark.parametrize("arrival", ["before_plan", "during_plan"])
def test_all_actions_defer_for_visual_ownership_or_cleanup(
    monkeypatch: pytest.MonkeyPatch, action: str, busy_source: str, arrival: str,
) -> None:
    monkeypatch.setenv("LUMINA_NEXT_AUTONOMY_ENABLED", "1")
    monkeypatch.setenv("LUMINA_VISUAL_PHASE_SERIALIZATION", "1")
    monkeypatch.delenv("LUMINA_LLM_DISABLED", raising=False)
    orchestrator = FakeOrchestrator()
    visual = FakeVisualNavigation()
    supervisor = AutonomySupervisor(
        orchestrator, SimpleNamespace(health=lambda: {"configured": False}), visual,
    )
    planned = []

    async def make_busy() -> None:
        if busy_source == "active":
            visual.active_id = "human-visual"
        elif busy_source == "lock":
            await visual.lock.acquire()
        elif busy_source == "task":
            visual.task = SimpleNamespace(done=lambda: False)
        else:
            visual.unconfirmed_stop = {"request_id": "human-visual"}

    async def plan(_context: dict[str, Any]) -> dict[str, Any]:
        planned.append(action)
        await make_busy()
        return {
            "action": action, "text": "こんにちは。",
            "target_description": "purple sofa" if action in {"move", "look"} else "",
        }

    async def forbidden_execute(_plan: dict[str, Any]) -> dict[str, Any]:
        pytest.fail("background body action crossed the common visual admission gate")

    monkeypatch.setattr(supervisor, "_plan", plan)
    monkeypatch.setattr(supervisor, "_execute", forbidden_execute)

    async def run() -> dict[str, Any]:
        if arrival == "before_plan":
            await make_busy()
        return await supervisor.tick("visual-race")

    result = asyncio.run(run())
    assert result["status"] == "deferred_visual_navigation"
    assert len(planned) == (1 if arrival == "during_plan" else 0)
    if arrival == "during_plan":
        assert result["phase"] == "pre_execution"
    assert supervisor.status()["action_count"] == 0
    assert orchestrator.cognition.observed == []
    assert visual.calls == []


@pytest.mark.parametrize("bad_status", [[], {}, ["observed"], {"status": "observed"}, None, True, 1])
@pytest.mark.parametrize("keep_valid_job", [False, True])
def test_malformed_history_status_is_ignored_without_losing_valid_inventory(
    bad_status: Any, keep_valid_job: bool,
) -> None:
    visual = FakeVisualNavigation()
    if not keep_valid_job:
        visual.jobs.clear()
    invalid = _inventory_job()
    invalid["status"] = bad_status
    visual.jobs["malformed-history"] = invalid
    supervisor = AutonomySupervisor(FakeOrchestrator(), SimpleNamespace(), visual)
    result = supervisor._visual_observation_context()
    assert result["available"] is keep_valid_job
    assert result["candidates"] == (["purple sofa"] if keep_valid_job else [])


def test_app_injects_the_human_facing_visual_navigation_instance() -> None:
    source = (ROOT / "lumina_next" / "app.py").read_text(encoding="utf-8")
    visual_index = source.index("VISUAL_NAVIGATION = VisualNavigation(")
    autonomy_index = source.index("AUTONOMY = AutonomySupervisor(")
    assert visual_index < autonomy_index
    constructor = source[autonomy_index : autonomy_index + 180]
    assert "VISUAL_NAVIGATION," in constructor
    assert "portable_navigation_enabled," in constructor
