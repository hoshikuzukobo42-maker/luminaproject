from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .providers.llm import ProviderError
from .providers.tts import split_tts_chunks


LOGGER = logging.getLogger(__name__)
_AUTONOMY_ACTIONS = {"observe", "look", "move", "gesture", "speak", "rest", "sleep"}
_SPATIAL_ACTIONS = {"observe", "look", "move"}
_VISUAL_TERMINAL_SUCCESS = {"arrived", "observed"}
_VISUAL_TERMINAL_FAILURE = {"not_visible", "ambiguous", "failed", "cancelled"}
_VISUAL_RUNNING = {"observing", "moving"}
_VISUAL_OBSERVATION_MAX_AGE_SECONDS = 300.0
_SLEEPING_STATES = {
    "WIND_DOWN",
    "NREM_CONSOLIDATION",
    "MEMORY_AUDIT",
    "SAFE_SIMULATION",
    "SLEEPING",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _llm_disabled() -> bool:
    return _env_bool("LUMINA_LLM_DISABLED", False)


def _compact(value: Any, limit: int = 7000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


def _parse_json_object(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("autonomy planner did not return a JSON object")
    value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("autonomy planner returned a non-object")
    return value


class AutonomySupervisor:
    """The single owner of Lumina's proactive life cycle.

    Karakuri notifications and human chat are inputs to this state machine, not
    the state machine itself.  Each tick has one bounded decision and one
    bounded execution.  A missing LLM never gets replaced by fake movement.
    """

    def __init__(
        self,
        orchestrator: Any,
        karakuri_service: Any,
        visual_navigation: Any | None = None,
        portable_navigation_check: Any | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.karakuri = karakuri_service
        # This must be the same instance used by the human-facing endpoint.
        # A second service would have a different active-job lock and could
        # cancel or race a visitor's movement.
        self.visual_navigation = visual_navigation
        self._portable_navigation_check = portable_navigation_check or getattr(
            visual_navigation, "portable_navigation_enabled", None
        )
        self.enabled = _env_bool("LUMINA_NEXT_AUTONOMY_ENABLED", False)
        self.interval_seconds = _env_float(
            "LUMINA_NEXT_AUTONOMY_INTERVAL_SECONDS", 75.0, 15.0, 3600.0
        )
        self.speech_cooldown_seconds = _env_float(
            "LUMINA_NEXT_AUTONOMY_SPEECH_COOLDOWN_SECONDS", 180.0, 30.0, 3600.0
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._tick_lock = asyncio.Lock()
        runtime_key = hashlib.sha256(
            str(orchestrator.config.runtime_root).encode("utf-8")
        ).hexdigest()[:16]
        self._lock_path = Path(
            os.environ.get("LUMINA_NEXT_AUTONOMY_LOCK_PATH")
            or Path("/tmp") / f"lumina_autonomy_{runtime_key}.lock"
        )
        self._lock_handle: Any = None
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=12)
        self._last_tick_at = 0.0
        self._last_action_at = 0.0
        self._last_speech_at = 0.0
        self._next_tick_at = 0.0
        self._action_count = 0
        self._completed_action_count = 0
        self._succeeded_action_count = 0
        self._pending_visual_action: dict[str, Any] | None = None
        self._error_count = 0
        self._phase = "disabled" if not self.enabled else "stopped"
        self._last_result: dict[str, Any] | None = None
        self._last_error = ""
        # One-shot local audio handoff for environments without a live visual
        # bridge. Keep the WAV out of health/status responses and expose it
        # only through the explicit autonomy audio endpoint.
        self._pending_audio: dict[str, Any] | None = None

    async def start(self) -> dict[str, Any]:
        if not self.enabled:
            self._phase = "disabled"
            return self.status()
        existing = self._task
        if existing is not None and not existing.done():
            return self.status()
        if not self._acquire_process_lock():
            self._phase = "locked_by_other_process"
            return self.status()
        self._stop_event = asyncio.Event()
        self._phase = "starting"
        self._task = asyncio.create_task(self._worker(), name="lumina-autonomy-supervisor")
        return self.status()

    async def stop(self) -> dict[str, Any]:
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None
        self._release_process_lock()
        if self.enabled:
            self._phase = "stopped"
        return self.status()

    def status(self) -> dict[str, Any]:
        running = self._task is not None and not self._task.done()
        return {
            "enabled": self.enabled,
            "running": running,
            "phase": self._phase,
            "interval_seconds": self.interval_seconds,
            "speech_cooldown_seconds": self.speech_cooldown_seconds,
            "last_tick_at": self._last_tick_at or None,
            "last_action_at": self._last_action_at or None,
            "next_tick_at": self._next_tick_at or None,
            "process_lock": str(self._lock_path),
            "process_lock_held": self._lock_handle is not None,
            "action_count": self._action_count,
            "completed_action_count": self._completed_action_count,
            "succeeded_action_count": self._succeeded_action_count,
            "error_count": self._error_count,
            "last_error": self._last_error or None,
            "last_result": self._last_result,
            "pending_visual_action": (
                dict(self._pending_visual_action)
                if self._pending_visual_action is not None
                else None
            ),
            "speech_output": {
                "available": self._pending_audio is not None,
                "text": (self._pending_audio or {}).get("text"),
                "created_at": (self._pending_audio or {}).get("created_at"),
                "latency_ms": (self._pending_audio or {}).get("latency_ms"),
            },
            "recent_actions": list(self._recent_actions),
            "contract": {
                "one_tick_one_decision": True,
                "one_tick_one_execution": True,
                "sleep_aware": True,
                "persistent_memory": True,
                "duplicate_tick_lock": True,
                "llm_disabled_is_no_action": True,
                "karakuri_is_notification_driven": True,
                "spatial_actions_require_shared_visual_navigation": True,
                "native_target_nodes_forbidden": True,
                "portable_spatial_grounding_required": True,
                "portable_spatial_grounding_ready": self._portable_navigation_ready(),
                "model_plan_is_not_action_completion": True,
                "planner_heavy_phase_serialized": self._planner_resource_contract_ready(),
            },
        }

    async def tick(self, trigger: str = "timer") -> dict[str, Any]:
        if not self.enabled:
            result = self._result("disabled", trigger=trigger)
            self._last_result = result
            return result
        async with self._tick_lock:
            return await self._tick_locked(trigger)

    async def _tick_locked(self, trigger: str) -> dict[str, Any]:
        self._last_tick_at = time.time()
        self._phase = "evaluating"
        self._last_error = ""
        pending = self._reconcile_pending_visual_action(trigger)
        if pending is not None:
            return self._finish(pending)
        if _llm_disabled():
            return self._finish(
                self._result(
                    "blocked_llm_disabled",
                    trigger=trigger,
                    reason="LUMINA_LLM_DISABLED is active; no fake autonomous action was generated",
                )
            )

        if not self._planner_resource_contract_ready():
            return self._finish(
                self._result(
                    "blocked_planner_resource_contract",
                    trigger=trigger,
                    reason=(
                        "autonomy planning requires the llama.cpp provider and "
                        "shared heavy-phase serialization"
                    ),
                )
            )

        resources = await self.orchestrator.resources.snapshot()
        resource_pressure = str(resources.get("pressure") or "").strip().lower()
        if resource_pressure in {"warning", "critical"}:
            self._phase = "resource_guard"
            severity = "critical" if resource_pressure == "critical" else "warning"
            self._record_event(
                "autonomy_resource_guard",
                "Autonomous LLM planning was deferred while local memory pressure was elevated.",
                {"trigger": trigger, "severity": severity, "resources": resources},
                importance=0.45 if severity == "warning" else 0.65,
            )
            return self._finish(
                self._result(
                    "deferred_resource_pressure",
                    trigger=trigger,
                    reason=f"memory pressure is {severity}; autonomous planning deferred",
                    resources=resources,
                )
            )
        active_requests = await self.orchestrator.active_request_ids()
        if active_requests:
            return self._finish(
                self._result(
                    "deferred_user_request",
                    trigger=trigger,
                    active_request_ids=active_requests,
                )
            )
        if self._visual_execution_busy():
            return self._finish(
                self._result(
                    "deferred_visual_navigation",
                    trigger=trigger,
                    reason="a human-facing visual job already owns navigation",
                )
            )

        cognition = self.orchestrator.cognition.tick()
        cognitive_state = str(cognition.get("cognitive_state") or "AWAKE")
        if cognitive_state in _SLEEPING_STATES:
            if self.orchestrator.cognition.should_wake():
                woke = self.orchestrator.cognition.wake("autonomy_timer")
                self._record_event(
                    "autonomy_wake",
                    "Lumina woke herself after the scheduled sleep interval.",
                    {"trigger": trigger, "state": woke},
                    importance=0.55,
                )
                return self._finish(self._result("woke", trigger=trigger, cognition=woke))
            return self._finish(
                self._result("sleeping", trigger=trigger, cognition=cognition)
            )

        sleep_due, sleep_reason = self.orchestrator.cognition.deep_sleep_due()
        if sleep_due:
            self._phase = "sleeping"
            sleep_result = await self.orchestrator.sleep_cycle.run_once(
                trigger=f"autonomy:{sleep_reason}", mode="deep", remain_asleep=True
            )
            self._record_event(
                "autonomy_sleep",
                "Lumina entered scheduled memory-consolidation sleep.",
                {"trigger": trigger, "reason": sleep_reason, "result": sleep_result},
                importance=0.7,
            )
            return self._finish(
                self._result(
                    "sleep_started",
                    trigger=trigger,
                    sleep_reason=sleep_reason,
                    sleep=sleep_result,
                )
            )

        context = await self._context(cognition, resources, trigger)
        self._phase = "planning"
        try:
            plan = await self._plan(context)
        except (ProviderError, ValueError, TypeError, RuntimeError) as exc:
            self._error_count += 1
            self._last_error = str(exc)[:300]
            LOGGER.warning("Lumina autonomy planning failed: %s", exc)
            self._record_event(
                "autonomy_plan_failed",
                "Autonomous planning failed; no body action was sent.",
                {"trigger": trigger, "error": str(exc)[:300]},
                importance=0.45,
            )
            return self._finish(
                self._result("plan_failed", trigger=trigger, error=str(exc)[:300])
            )

        plan = self._normalize_plan(plan, context)
        # Planning can take many seconds on the 16 GB product host. A visitor
        # request that arrived meanwhile always wins before any body action.
        active_requests = await self.orchestrator.active_request_ids()
        if active_requests:
            return self._finish(
                self._result(
                    "deferred_user_request",
                    trigger=trigger,
                    phase="pre_execution",
                    active_request_ids=active_requests,
                    plan=plan,
                )
            )
        # A human visual request can start while the model is planning. This
        # gate applies to speech, gestures and sleep as well as spatial actions.
        # Spatial admission additionally checks atomically inside VN.start.
        if self._visual_execution_busy():
            return self._finish(
                self._result(
                    "deferred_visual_navigation",
                    trigger=trigger,
                    phase="pre_execution",
                    reason="visual execution or stop completion is still pending",
                    plan=plan,
                )
            )
        self._phase = "executing"
        execution = await self._execute(plan)
        self._action_count += 1 if execution.get("executed") else 0
        self._completed_action_count += 1 if execution.get("completed") else 0
        self._succeeded_action_count += 1 if execution.get("succeeded") is True else 0
        decision_at = time.time()
        if execution.get("executed"):
            self._last_action_at = decision_at
        self._recent_actions.append(
            {
                "at": decision_at,
                "action": plan["action"],
                "reason": plan.get("reason", ""),
                "text": plan.get("text", "") if plan["action"] == "speak" else "",
                "executed": bool(execution.get("executed")),
                "completed": bool(execution.get("completed")),
                "succeeded": execution.get("succeeded"),
                "status": execution.get("status"),
                "visual_request_id": execution.get("visual_request_id"),
            }
        )
        if execution.get("completed"):
            self.orchestrator.cognition.observe_autonomous_action(
                plan["action"],
                target=str(plan.get("target_description") or ""),
                success=execution.get("succeeded") is True,
                reason=str(plan.get("reason") or "autonomous plan"),
            )
        self._record_event(
            "autonomy_cycle",
            "Lumina made one self-initiated decision and recorded its execution state.",
            {"trigger": trigger, "plan": plan, "execution": execution},
            importance=0.65 if plan["action"] in {"speak", "move"} else 0.4,
        )
        if not execution.get("executed"):
            result_status = "planned_no_execution"
        elif not execution.get("completed"):
            result_status = "action_started"
        elif execution.get("succeeded") is True:
            result_status = "acted"
        else:
            result_status = "action_failed"
        return self._finish(
            self._result(
                result_status,
                trigger=trigger,
                plan=plan,
                execution=execution,
            )
        )

    async def _context(
        self, cognition: dict[str, Any], resources: dict[str, Any], trigger: str
    ) -> dict[str, Any]:
        bridge = {"ok": False, "reason": "bridge_disabled"}
        if self.orchestrator.config.bridge_enabled:
            bridge = await self.orchestrator.bridge.health()
        try:
            karakuri = self.karakuri.health()
        except Exception as exc:
            karakuri = {"ok": False, "error": type(exc).__name__}
        durable = getattr(self.orchestrator.memory, "sqlite", self.orchestrator.memory)
        wake_brief = durable.wake_brief(limit=4) if hasattr(durable, "wake_brief") else {}
        return {
            "now": datetime.now().isoformat(timespec="seconds"),
            "trigger": trigger,
            "cognition": cognition,
            "resources": resources,
            "bridge": bridge,
            "karakuri": {
                "ready": bool(karakuri.get("configured")),
                "social_memory": karakuri.get("social_memory", {}),
                "exploration": karakuri.get("exploration", {}),
                "recent_actions": (karakuri.get("autonomy") or {}).get("recent_actions", []),
                "notification_driven": True,
            },
            "wake_brief": wake_brief,
            "sleep": self.orchestrator.sleep_cycle.status(),
            "recent_local_actions": list(self._recent_actions),
            "visual_observation": self._visual_observation_context(),
        }

    def _planner_resource_contract_ready(self) -> bool:
        """The product planner must share the same Qwen/TTS heavy-phase lease."""

        provider = str(getattr(self.orchestrator.config, "llm_provider", "") or "")
        return provider == "llama_cpp" and _env_bool(
            "LUMINA_VISUAL_PHASE_SERIALIZATION", False
        )

    def _visual_execution_busy(self) -> bool:
        """Use the shared service's memory-only ownership and cleanup state."""

        service = self.visual_navigation
        if service is None:
            return False
        if getattr(service, "active_id", None) or getattr(service, "unconfirmed_stop", None) is not None:
            return True
        task = getattr(service, "task", None)
        try:
            if task is not None:
                done = getattr(task, "done", None)
                if not callable(done) or done() is not True:
                    return True
            locked = getattr(getattr(service, "lock", None), "locked", None)
            return not callable(locked) or locked() is not False
        except Exception:
            # Unknown cleanup state cannot authorize a new background action.
            return True

    def _portable_navigation_ready(self) -> bool:
        check = self._portable_navigation_check
        if not callable(check):
            return False
        try:
            return check() is True
        except Exception:
            return False

    @staticmethod
    def _safe_body_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if 0.0 <= number <= 1.0 else None

    def _visual_observation_context(self) -> dict[str, Any]:
        """Expose only validated pixel-derived descriptors, never engine names."""

        service = self.visual_navigation
        result: dict[str, Any] = {
            "available": False,
            "busy": self._visual_execution_busy(),
            "candidates": [],
            "basis": "none",
        }
        jobs = getattr(service, "jobs", None) if service is not None else None
        describe = (
            getattr(service, "_portable_detection_descriptor", None)
            if service is not None
            else None
        )
        if not isinstance(jobs, dict) or not callable(describe):
            return result
        now = time.time()

        def finished_at(job: dict[str, Any]) -> float:
            value = job.get("finished_unix")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0.0
            return float(value)

        ordered = sorted(
            (job for job in jobs.values() if isinstance(job, dict)),
            key=finished_at,
            reverse=True,
        )
        for job in ordered:
            finished = job.get("finished_unix")
            if (
                isinstance(finished, bool)
                or not isinstance(finished, (int, float))
                or not 0.0 <= now - float(finished) <= _VISUAL_OBSERVATION_MAX_AGE_SECONDS
                or not isinstance(job.get("status"), str)
                or job.get("status") not in (
                    _VISUAL_TERMINAL_SUCCESS | _VISUAL_TERMINAL_FAILURE
                )
                or job.get("recognition_basis") != "raw_eye_image_only"
                or not re.fullmatch(r"[0-9a-f]{64}", str(job.get("image_sha256") or ""))
            ):
                continue
            detection = job.get("detection")
            audit = detection.get("perception_audit") if isinstance(detection, dict) else None
            inventory = audit.get("inventory") if isinstance(audit, dict) else None
            if (
                not isinstance(audit, dict)
                or not str(audit.get("basis") or "").startswith(
                    "target_free_image_inventory_then_"
                )
                or not isinstance(inventory, list)
                or not 1 <= len(inventory) <= 8
            ):
                continue
            candidates: list[str] = []
            valid_inventory = True
            for index, item in enumerate(inventory):
                if (
                    not isinstance(item, dict)
                    or set(item) != {"id", "label", "color", "bbox"}
                    or item.get("id") != index
                ):
                    valid_inventory = False
                    break
                bbox = item.get("bbox")
                if (
                    not isinstance(bbox, list)
                    or len(bbox) != 4
                    or any(type(value) is not int or not 0 <= value <= 1000 for value in bbox)
                    or bbox[2] <= bbox[0]
                    or bbox[3] <= bbox[1]
                ):
                    valid_inventory = False
                    break
                try:
                    _label, category, color = describe(item)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    valid_inventory = False
                    break
                if (
                    not re.fullmatch(r"[a-z]+(?: [a-z]+){0,2}", category)
                    or not re.fullmatch(r"[a-z]+(?: [a-z]+)?", color)
                ):
                    valid_inventory = False
                    break
                descriptor = f"{color} {category}"
                if descriptor not in candidates:
                    candidates.append(descriptor)
            if valid_inventory and candidates:
                result.update(
                    available=True,
                    candidates=candidates,
                    basis="recent_target_free_eye_inventory",
                )
                return result
        return result

    def _reconcile_pending_visual_action(self, trigger: str) -> dict[str, Any] | None:
        """Collect one stable terminal visual result exactly once on a later tick."""

        pending = self._pending_visual_action
        if pending is None:
            return None
        request_id = str(pending.get("request_id") or "")
        service = self.visual_navigation
        jobs = getattr(service, "jobs", None) if service is not None else None
        job = jobs.get(request_id) if isinstance(jobs, dict) else None
        if not isinstance(job, dict) or job.get("request_id") != request_id:
            return self._result(
                "action_completion_unknown",
                trigger=trigger,
                pending_action=dict(pending),
                reason="owned visual job is unavailable; no new action was started",
            )

        status = str(job.get("status") or "")
        task = getattr(service, "task", None)
        task_running = callable(getattr(task, "done", None)) and not task.done()
        still_owned = getattr(service, "active_id", None) == request_id
        unconfirmed_stop = getattr(service, "unconfirmed_stop", None)
        stop_unconfirmed = (
            isinstance(unconfirmed_stop, dict)
            and unconfirmed_stop.get("request_id") == request_id
        )
        if status in _VISUAL_RUNNING or still_owned or task_running:
            return self._result(
                "action_pending",
                trigger=trigger,
                pending_action=dict(pending),
                visual_job_status=status or "unknown",
                completed=False,
                succeeded=None,
            )
        if stop_unconfirmed or status not in (
            _VISUAL_TERMINAL_SUCCESS | _VISUAL_TERMINAL_FAILURE
        ):
            return self._result(
                "action_completion_unknown",
                trigger=trigger,
                pending_action=dict(pending),
                visual_job_status=status or "unknown",
                reason="visual ownership or stop completion is not proven",
            )

        finished = job.get("finished_unix")
        if (
            isinstance(finished, bool)
            or not isinstance(finished, (int, float))
            or not 0.0 < float(finished) <= time.time() + 1.0
            or (
                status in _VISUAL_TERMINAL_FAILURE
                and job.get("arrived") is not False
            )
        ):
            return self._result(
                "action_completion_unknown",
                trigger=trigger,
                pending_action=dict(pending),
                visual_job_status=status,
                reason="visual terminal evidence is malformed",
            )

        action = str(pending.get("action") or "")
        succeeded = (
            action == "move"
            and status == "arrived"
            and job.get("arrived") is True
        ) or (
            action == "look"
            and status == "observed"
            and job.get("arrived") is False
        )
        self._pending_visual_action = None
        self._completed_action_count += 1
        self._succeeded_action_count += 1 if succeeded else 0
        for recent in reversed(self._recent_actions):
            if recent.get("visual_request_id") == request_id:
                recent.update(
                    completed=True,
                    succeeded=succeeded,
                    status=(
                        "visual_navigation_completed"
                        if succeeded
                        else "visual_navigation_failed"
                    ),
                    visual_job_status=status,
                )
                break
        self.orchestrator.cognition.observe_autonomous_action(
            action,
            target=str(pending.get("target_description") or ""),
            success=succeeded,
            reason=str(pending.get("reason") or "autonomous visual action"),
        )
        execution = {
            "ok": succeeded,
            "executed": True,
            "completed": True,
            "succeeded": succeeded,
            "accepted": True,
            "status": (
                "visual_navigation_completed"
                if succeeded
                else "visual_navigation_failed"
            ),
            "visual_job_status": status,
            "visual_request_id": request_id,
            "arrived": job.get("arrived") is True,
        }
        self._record_event(
            "autonomy_action_completed",
            "Lumina recorded the stable terminal result of one visual action.",
            {"trigger": trigger, "pending_action": pending, "execution": execution},
            importance=0.65 if action == "move" else 0.4,
        )
        return self._result(
            "action_completed" if succeeded else "action_failed",
            trigger=trigger,
            pending_action=dict(pending),
            execution=execution,
        )

    def _planner_packet(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return only state needed for one short autonomous decision.

        The product Way profile intentionally runs with a 1024-token context.
        Passing the full health and memory payload makes autonomy fail before
        generation, so planner input must be a stable, bounded observation.
        """

        bridge = context.get("bridge") if isinstance(context, dict) else {}
        bridge = bridge if isinstance(bridge, dict) else {}
        state = bridge.get("autonomy_state")
        state = state if isinstance(state, dict) else {}
        cognition = context.get("cognition") if isinstance(context, dict) else {}
        cognition = cognition if isinstance(cognition, dict) else {}
        resources = context.get("resources") if isinstance(context, dict) else {}
        resources = resources if isinstance(resources, dict) else {}
        karakuri = context.get("karakuri") if isinstance(context, dict) else {}
        karakuri = karakuri if isinstance(karakuri, dict) else {}
        visual = context.get("visual_observation") if isinstance(context, dict) else {}
        visual = visual if isinstance(visual, dict) else {}
        awareness = str(state.get("user_awareness") or "").casefold()
        looking_at = str(state.get("looking_at") or "").casefold()
        user_visible = awareness in {
            "looking_at_you", "looking_at_user", "aware", "aware_nearby",
            "nearby", "in_proximity",
        } or looking_at in {"user", "player", "visitor"}
        return {
            "trigger": context.get("trigger"),
            "cognition": {
                "state": cognition.get("cognitive_state"),
                "drives": cognition.get("drives"),
            },
            "resources": {
                "pressure": resources.get("pressure"),
                "memory_free_percent": resources.get("memory_free_percent"),
            },
            "body": {
                "connected": bool(bridge.get("godot_connected")),
                "user_visible": user_visible,
                "energy": self._safe_body_number(state.get("energy")),
                "curiosity": self._safe_body_number(state.get("curiosity")),
                "attention": self._safe_body_number(state.get("social_attention")),
            },
            "visual_observation": {
                "available": visual.get("available") is True,
                "busy": visual.get("busy") is True,
                "candidates": list(visual.get("candidates") or [])[:8],
                "basis": visual.get("basis"),
            },
            "sleep": {
                "enabled": (context.get("sleep") or {}).get("enabled")
                if isinstance(context.get("sleep"), dict)
                else None,
                "run_active": (context.get("sleep") or {}).get("run_active")
                if isinstance(context.get("sleep"), dict)
                else None,
            },
            "karakuri": {
                "notification_driven": karakuri.get("notification_driven", True),
                "logged_in": karakuri.get("logged_in"),
            },
            "recent_actions": list(context.get("recent_local_actions") or [])[-3:],
        }

    async def _plan(self, context: dict[str, Any]) -> dict[str, Any]:
        system = (
            "あなたはLuminaの自律生活ループの意思決定器です。"
            "ユーザーから依頼されていない時間にも、現在の認知状態・睡眠・記憶・身体接続を見て、"
            "次の一手を一つだけ選びます。通知待ちや単純な移動連打を生活とは見なしません。"
            "人間らしく、会話、観察、視線、移動、休息、睡眠を状況に応じて変えます。"
            "Karakuri Worldは通知駆動なので、通知がないのに外部会話を捏造しないでください。"
            "移動先や注視対象はvisual_observation.candidatesにある文字列を一字一句そのまま"
            "target_descriptionへコピーする場合だけ選べます。候補がなければmove/lookを選びません。"
            "observeは実画像の周囲inventory契約が未接続なので現在は選ばないでください。"
            "Godotのノード名、内部ID、座標、見えていない物体を作らないでください。"
            "LLMの思考過程、説明、Markdownは禁止。JSONオブジェクトだけを返してください。"
            "形式: {\"action\":\"observe|look|move|gesture|speak|rest|sleep\","
            "\"reason\":\"短い理由\",\"text\":\"話す場合だけ自然な日本語\","
            "\"target_description\":\"move/lookの場合だけvisual_observationの候補\","
            "\"emotion\":\"neutral|curious|calm|happy\","
            "\"speed\":1.0,\"duration_ms\":800}."
            "speakは短い一言だけ。直近2回と同じactionを続けない。"
            "睡眠中ならsleepを選ばず、Supervisorが睡眠を管理します。"
        )
        messages = [
            {"role": "system", "content": system},
            # Way's product profile is intentionally 1024 tokens.  Keep the
            # autonomous observation packet small enough that planning can
            # never consume the whole context before generation starts.
            {"role": "user", "content": _compact(self._planner_packet(context), 1000)},
        ]
        result = None
        plan = None
        parse_error: ValueError | None = None
        for attempt in range(2):
            result = await self.orchestrator.llm.chat(
                messages,
                num_ctx=min(int(self.orchestrator.config.num_ctx), 2048),
                max_tokens=190 if attempt == 0 else 96,
                task_kind="autonomy_plan",
            )
            try:
                plan = _parse_json_object(result.text)
                break
            except ValueError as exc:
                parse_error = exc
                if attempt == 0:
                    messages = [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": (
                                "前回の出力はJSONとして不完全でした。"
                                "説明やMarkdownを付けず、閉じたJSONオブジェクトを一つだけ返してください。"
                                + _compact(self._planner_packet(context), 800)
                            ),
                        },
                    ]
        if plan is None or result is None:
            raise parse_error or ValueError("autonomy planner returned no usable JSON")
        plan["model"] = result.model
        if self._speech_due(context):
            utterance = await self._compose_autonomous_utterance(context)
            if utterance:
                plan["action"] = "speak"
                plan["text"] = utterance
                plan["emotion"] = plan.get("emotion") or "calm"
        return plan

    def _speech_due(self, context: dict[str, Any]) -> bool:
        """Decide when passive observation must become social contact."""

        bridge = context.get("bridge") if isinstance(context, dict) else {}
        if not isinstance(bridge, dict):
            bridge = {}
        state = bridge.get("autonomy_state") if isinstance(bridge, dict) else {}
        if not isinstance(state, dict):
            state = {}
        awareness = str(state.get("user_awareness") or "").casefold()
        looking_at = str(state.get("looking_at") or "").casefold()
        user_visible = awareness in {
            "looking_at_you",
            "looking_at_user",
            "aware",
            "aware_nearby",
            "nearby",
            "in_proximity",
        }
        user_visible = user_visible or looking_at in {"user", "player", "visitor"}
        if time.time() - self._last_speech_at < self.speech_cooldown_seconds:
            return False
        recent = list(self._recent_actions)[-4:]
        if len(recent) < 2:
            return False
        if any(str(item.get("action") or "") == "speak" for item in recent):
            return False
        if _env_bool("LUMINA_NEXT_AUTONOMY_SPEECH_REQUIRE_USER_VISIBLE", False):
            return user_visible
        if user_visible:
            return True
        # Without a live avatar/bridge, local TTS is still a usable output
        # channel. Do not wait hours for a drive threshold before Lumina can
        # demonstrate self-initiated speech.
        if not bool(bridge.get("ok")) and self.orchestrator.config.tts_enabled:
            return True
        cognition = context.get("cognition") if isinstance(context, dict) else {}
        drives = cognition.get("drives") if isinstance(cognition, dict) else {}
        if not isinstance(drives, dict):
            drives = {}
        # Do not turn the life loop into silent bridge polling when no user or
        # avatar is visible. Internal social/novelty pressure can still create
        # a real local utterance through TTS.
        return (
            float(drives.get("social_need") or 0.0) >= 0.62
            or float(drives.get("loneliness") or 0.0) >= 0.60
            or float(drives.get("novelty_need") or 0.0) >= 0.78
        )

    async def _compose_autonomous_utterance(self, context: dict[str, Any]) -> str:
        """Ask the local LLM for one concise, context-grounded spontaneous line."""

        result = await self.orchestrator.llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "あなたはLuminaです。ユーザーへ自分から話しかける短い一言を作ります。"
                        "説明、箇条書き、質問攻め、メタ発言は禁止。今見えている状況に自然に触れ、"
                        "日本語で20〜50文字程度。JSONだけ: {\"text\":\"一言\",\"emotion\":\"calm|curious|happy\"}"
                    ),
                },
                {"role": "user", "content": _compact(self._planner_packet(context), 800)},
            ],
            num_ctx=min(int(self.orchestrator.config.num_ctx), 2048),
            max_tokens=80,
            task_kind="autonomy_spontaneous_speech",
        )
        try:
            payload = _parse_json_object(result.text)
            text = str(payload.get("text") or "").strip()
        except Exception:
            text = ""
        if text:
            return text[:160]
        # Malformed model output is not permission to replace the decision with
        # a canned utterance that the model never selected.
        return ""

    def _normalize_plan(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        requested_action = str(raw.get("action") or "").strip().lower()
        aliases = {"talk": "speak", "say": "speak", "wait": "rest", "scan": "look"}
        action = aliases.get(requested_action, requested_action)
        validation_errors: list[str] = []
        if action not in _AUTONOMY_ACTIONS:
            action = "invalid"
            validation_errors.append("unsupported_action")
        if action == "speak" and time.time() - self._last_speech_at < self.speech_cooldown_seconds:
            validation_errors.append("speech_cooldown_active")
        speech_chunks = split_tts_chunks(str(raw.get("text") or "").strip(), max_chars=80)
        text = speech_chunks[0][:80] if speech_chunks else ""
        if action == "speak" and not text:
            validation_errors.append("speech_text_missing")
        if action == "speak" and text:
            recent_speech = {
                str(item.get("text") or "").strip()
                for item in self._recent_actions
                if str(item.get("action") or "") == "speak"
            }
            if text in recent_speech:
                validation_errors.append("duplicate_speech")

        # Node names, native object IDs and coordinates are never valid planner
        # output. The visual service owns image-to-target grounding.
        if str(raw.get("target_node") or "").strip() or raw.get("position") is not None:
            validation_errors.append("native_spatial_reference_forbidden")
        target_description = re.sub(
            r"\s+", " ", str(raw.get("target_description") or "").strip().casefold()
        )[:80]
        visual = context.get("visual_observation") if isinstance(context, dict) else {}
        candidates = visual.get("candidates") if isinstance(visual, dict) else []
        candidate_map = {
            re.sub(r"\s+", " ", candidate.strip().casefold()): candidate
            for candidate in candidates
            if isinstance(candidate, str) and candidate.strip()
        }
        target_verified = False
        if action in {"move", "look"}:
            canonical_target = candidate_map.get(target_description)
            if canonical_target is None:
                validation_errors.append(
                    "visual_target_missing" if not target_description else "visual_target_not_observed"
                )
                target_description = ""
            else:
                target_description = canonical_target
                target_verified = True
        elif target_description:
            validation_errors.append("visual_target_not_applicable")
            target_description = ""
        try:
            speed = max(0.75, min(1.35, float(raw.get("speed") or 1.0)))
        except (TypeError, ValueError):
            speed = 1.0
        try:
            duration_ms = max(250, min(10_000, int(raw.get("duration_ms") or 800)))
        except (TypeError, ValueError):
            duration_ms = 800
        return {
            "action": action,
            "requested_action": requested_action,
            "reason": str(raw.get("reason") or "次の自然な一歩を選んだ")[:500],
            "text": text,
            "target_description": target_description,
            "visual_target_verified": target_verified,
            "target_node": "",
            "position": None,
            "validation_errors": list(dict.fromkeys(validation_errors)),
            "emotion": str(raw.get("emotion") or "neutral")[:40],
            "speed": speed,
            "duration_ms": duration_ms,
            "model": str(raw.get("model") or "unknown")[:160],
        }

    async def _execute(self, plan: dict[str, Any]) -> dict[str, Any]:
        action = plan["action"]
        validation_errors = plan.get("validation_errors")
        if isinstance(validation_errors, list) and validation_errors:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "plan_rejected",
                "errors": list(validation_errors),
            }
        if action in _SPATIAL_ACTIONS:
            return await self._execute_visual_action(plan)
        if action == "sleep":
            result = await self.orchestrator.sleep_cycle.run_once(
                trigger="autonomy_plan", mode="deep", remain_asleep=True
            )
            succeeded = bool(result.get("ok"))
            return {
                "ok": succeeded,
                "executed": True,
                "completed": True,
                "succeeded": succeeded,
                "accepted": True,
                "status": "sleep",
                "detail": result,
            }
        if action == "speak":
            bridge_result: dict[str, Any] | None = None
            bridge_error = ""
            if self.orchestrator.config.bridge_enabled:
                try:
                    bridge_result = await self.orchestrator.bridge.dispatch_speak(
                        plan["text"], plan["emotion"], plan["speed"]
                    )
                    if bridge_result.get("sent"):
                        self._last_speech_at = time.time()
                        return {
                            **bridge_result,
                            "executed": True,
                            "completed": False,
                            "succeeded": None,
                            "accepted": True,
                            "status": bridge_result.get("status") or "sent",
                        }
                except ProviderError as exc:
                    bridge_error = str(exc)[:300]

            # Speech remains a real local output even when the visual bridge is
            # absent. A local consumer can retrieve this one-shot WAV from
            # /autonomy/audio.
            if self.orchestrator.config.tts_enabled:
                try:
                    tts_result, chunks = await self.orchestrator.tts.synthesize_first_chunk(
                        plan["text"], plan["emotion"], plan["speed"]
                    )
                    created_at = time.time()
                    self._pending_audio = {
                        "text": tts_result.text,
                        "audio_base64": tts_result.audio_base64,
                        "emotion": plan["emotion"],
                        "speed": plan["speed"],
                        "created_at": created_at,
                        "latency_ms": round(tts_result.latency_ms, 1),
                        "chunks_total": len(chunks),
                        "lipsync": tts_result.payload,
                    }
                    self._last_speech_at = created_at
                    return {
                        "ok": True,
                        "executed": True,
                        "completed": False,
                        "succeeded": None,
                        "accepted": True,
                        "sent": False,
                        "status": "tts_generated",
                        "output_generated": True,
                        "audio_available": True,
                        "latency_ms": round(tts_result.latency_ms, 1),
                        "chunks_total": len(chunks),
                        "bridge_error": bridge_error or None,
                    }
                except ProviderError as exc:
                    if not bridge_error:
                        bridge_error = str(exc)[:300]

            return {
                **(bridge_result or {}),
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "speech_output_unavailable",
                "error": bridge_error or "bridge and local TTS are unavailable",
            }
        if not self.orchestrator.config.bridge_enabled:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "bridge_disabled",
            }
        try:
            target_node = str(plan.get("target_node") or "").strip()
            bridge_action = {
                "gesture": "gesture",
                "rest": "wait",
            }.get(action)
            if bridge_action is None:
                return {"ok": False, "executed": False, "status": "unsupported_action"}
            params: dict[str, Any] = {}
            if action == "gesture":
                params = {"gesture": "look_around", "duration": plan["duration_ms"] / 1000.0}
            elif action == "rest":
                params = {"duration_ms": plan["duration_ms"]}
            result = await self.orchestrator.bridge.dispatch_action(
                bridge_action,
                target_node=target_node or None,
                position=plan.get("position"),
                params=params,
                reason=plan["reason"],
            )
            sent = bool(result.get("sent"))
            return {
                **result,
                "executed": sent,
                "completed": False,
                "succeeded": None,
                "accepted": sent,
                "status": result.get("status"),
            }
        except ProviderError as exc:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "execution_failed",
                "error": str(exc)[:300],
            }

    async def _execute_visual_action(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Start one shared, image-grounded action without replacing another job."""

        action = str(plan.get("action") or "")
        if self._pending_visual_action is not None:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "autonomy_visual_action_pending",
            }
        if str(plan.get("target_node") or "").strip() or plan.get("position") is not None:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "native_spatial_reference_forbidden",
            }
        target = str(plan.get("target_description") or "").strip()
        if action in {"move", "look"} and (
            not target or plan.get("visual_target_verified") is not True
        ):
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_target_unverified",
            }
        if action == "observe" and target:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "unexpected_observe_target",
            }
        if action == "observe":
            # VisualNavigation currently exposes only explicit single-target
            # requests. Treating an internal thought as a camera observation, or
            # inventing an implicit target, would be a false observation.
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_observation_not_supported",
            }
        service = self.visual_navigation
        if service is None or not callable(getattr(service, "start", None)):
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_navigation_unavailable",
            }
        try:
            active_requests = await self.orchestrator.active_request_ids()
        except Exception as exc:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "request_activity_unknown",
                "error": type(exc).__name__,
            }
        if active_requests:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "deferred_user_request",
                "active_request_ids": list(active_requests),
            }
        if not self._portable_navigation_ready():
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "portable_navigation_required",
            }

        # VisualNavigation.start normally cancels an existing job. Refuse both
        # an active owner and a contended lock. With an unlocked asyncio.Lock,
        # the following start acquires it before the event loop can schedule a
        # competing human start; a human request arriving later may still cancel
        # this lower-priority autonomous job.
        visual_lock = getattr(service, "lock", None)
        lock_state = getattr(visual_lock, "locked", None)
        if not callable(lock_state):
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_navigation_lock_unavailable",
            }
        if self._visual_execution_busy():
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_navigation_busy",
            }

        request_id = "autonomy-visual-" + uuid.uuid4().hex
        request_text = {
            "observe": "何が見える",
            "look": f"where is {target}",
            # The explicit article keeps known categories on VisualNavigation's
            # deterministic request parser instead of spending two more Qwen
            # passes on open-vocabulary request interpretation.
            "move": f"go to the {target}",
        }[action]
        try:
            job = await service.start(
                request_text,
                request_id,
                speak=False,
                allow_move=action == "move",
                only_if_idle=True,
            )
        except Exception as exc:
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_navigation_rejected",
                "error": str(exc)[:300] or type(exc).__name__,
            }
        job_status_value = job.get("status") if isinstance(job, dict) else None
        if (
            not isinstance(job, dict)
            or job.get("request_id") != request_id
            or not isinstance(job_status_value, str)
            or job_status_value not in (
                _VISUAL_RUNNING | _VISUAL_TERMINAL_SUCCESS | _VISUAL_TERMINAL_FAILURE
            )
        ):
            return {
                "ok": False,
                "executed": False,
                "completed": False,
                "succeeded": False,
                "accepted": False,
                "status": "visual_navigation_invalid_response",
            }
        job_status = job_status_value
        completed = job_status in _VISUAL_TERMINAL_SUCCESS | _VISUAL_TERMINAL_FAILURE
        if completed:
            finished = job.get("finished_unix")
            task = getattr(service, "task", None)
            task_running = callable(getattr(task, "done", None)) and not task.done()
            stop = getattr(service, "unconfirmed_stop", None)
            if (
                isinstance(finished, bool)
                or not isinstance(finished, (int, float))
                or not 0.0 < float(finished) <= time.time() + 1.0
                or getattr(service, "active_id", None) == request_id
                or task_running
                or (isinstance(stop, dict) and stop.get("request_id") == request_id)
                or (
                    job_status in _VISUAL_TERMINAL_FAILURE
                    and job.get("arrived") is not False
                )
            ):
                return {
                    "ok": False,
                    "executed": False,
                    "completed": False,
                    "succeeded": False,
                    "accepted": False,
                    "status": "visual_navigation_invalid_response",
                }
        succeeded = (
            (
                action == "move"
                and job_status == "arrived"
                and job.get("arrived") is True
            )
            or (
                action == "look"
                and job_status == "observed"
                and job.get("arrived") is False
            )
        ) if completed else None
        if not completed:
            self._pending_visual_action = {
                "request_id": request_id,
                "action": action,
                "target_description": target,
                "reason": str(plan.get("reason") or "autonomous visual action")[:500],
                "started_unix": time.time(),
            }
        return {
            "ok": succeeded is not False,
            "executed": True,
            "completed": completed,
            "succeeded": succeeded,
            "accepted": True,
            "status": (
                "visual_navigation_completed"
                if succeeded is True
                else "visual_navigation_failed"
                if succeeded is False
                else "visual_navigation_started"
            ),
            "visual_job_status": job_status,
            "visual_request_id": request_id,
            "arrived": job.get("arrived") is True,
        }

    def take_speech_output(self) -> dict[str, Any] | None:
        """Return and clear the newest locally synthesized autonomous utterance."""
        output = self._pending_audio
        self._pending_audio = None
        return output

    def _record_event(
        self, event_type: str, content: str, metadata: dict[str, Any], *, importance: float
    ) -> None:
        durable = getattr(self.orchestrator.memory, "sqlite", self.orchestrator.memory)
        if not hasattr(durable, "record_event"):
            return
        try:
            durable.record_event(
                event_type=event_type,
                content=content,
                source_type="lumina_generated",
                source_actor="lumina_autonomy",
                session_id="lumina:autonomy",
                importance=importance,
                metadata=metadata,
            )
        except Exception as exc:
            LOGGER.warning("Could not record autonomy event: %s", exc)

    def _acquire_process_lock(self) -> bool:
        if self._lock_handle is not None:
            return True
        try:
            import fcntl

            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                handle.close()
                return False
            self._lock_handle = handle
            return True
        except (OSError, ImportError) as exc:
            self._last_error = f"autonomy process lock unavailable: {exc}"[:300]
            self._error_count += 1
            return False

    def _release_process_lock(self) -> None:
        handle = self._lock_handle
        self._lock_handle = None
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        try:
            handle.close()
        except OSError:
            pass

    def _result(self, status: str, **payload: Any) -> dict[str, Any]:
        failed = {
            "disabled",
            "blocked_llm_disabled",
            "blocked_planner_resource_contract",
            "plan_failed",
            "planned_no_execution",
            "action_failed",
            "action_completion_unknown",
        }
        return {"ok": status not in failed, "status": status, **payload}

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        self._last_result = result
        self._next_tick_at = time.time() + self.interval_seconds
        if result.get("status") == "acted":
            self._phase = "waiting"
        elif result.get("status") in {
            "blocked_llm_disabled",
            "blocked_planner_resource_contract",
            "disabled",
        }:
            self._phase = "blocked"
        else:
            self._phase = "waiting"
        return result

    async def _worker(self) -> None:
        self._phase = "running"
        stop_event = self._stop_event
        if stop_event is None:
            self._phase = "stopped"
            return
        try:
            while not stop_event.is_set():
                try:
                    await self.tick("timer")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._error_count += 1
                    self._last_error = str(exc)[:300]
                    self._phase = "error_backoff"
                    LOGGER.exception("Unhandled Lumina autonomy tick failure")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            if self._phase not in {"disabled", "blocked"}:
                self._phase = "stopped"
            self._release_process_lock()


__all__ = ["AutonomySupervisor"]
