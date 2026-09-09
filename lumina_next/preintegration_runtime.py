"""Local-only composition root for the Lumina pre-integration candidate.

Importing this module has no process or model side effects. Heavy models are
loaded only when a chat or vision request is admitted. No remote inference API
is used.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from .conversation_quality import ConversationQualityChecker
from .human_autonomy import HumanAutonomy
from .karakuri_session import KarakuriSession, ResponseRequest
from .memory_sleep import MemorySleep
from .model_broker import ModelBroker
from .model_broker_types import BrokerError, ChatRequest, ChatResponse, ModelKind
from .preintegration_conversation import (
    finalize_chat_response,
    gate_to_lumina_payload,
    prepare_chat_messages,
)
from .resource_governor import ResourceGovernor
from .resource_policy import ResourcePolicy
from .conversation_gate import evaluate_conversation_gate
from .shisa_conversation import build_shisa_messages, postprocess_shisa_response
from .tts_japanese_normalizer import normalize_for_tts_japanese
from .tts_streaming import split_tts_sentence_chunks
from .vision_preprocess import VisionPreprocessError, preprocess_vision_input
from .vision_runtime import ContractError, SceneGraph


@dataclass(frozen=True)
class CandidateSettings:
    root: Path
    control_host: str
    control_port: int
    shisa_host: str
    shisa_port: int
    shisa_alias: str
    qwen_host: str
    qwen_port: int
    qwen_alias: str
    stt_port: int
    tts_port: int
    operation_timeout_seconds: float
    max_http_response_bytes: int
    resource_policy_name: str

    @classmethod
    def from_environment(cls) -> "CandidateSettings":
        root = Path(
            os.environ.get("LUMINA_RUNTIME_ROOT")
            or Path(__file__).resolve().parents[1]
        ).expanduser().resolve()

        def port(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value < 1 or value > 65535:
                raise ValueError(f"{name} must be in the TCP port range")
            return value

        def local_host(name: str, default: str = "127.0.0.1") -> str:
            value = os.environ.get(name, default).strip()
            if value not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError(f"{name} must remain loopback-only")
            return value

        return cls(
            root=root,
            control_host=local_host("LUMINA_PREINTEGRATION_HOST"),
            control_port=port("LUMINA_PREINTEGRATION_PORT", 8788),
            shisa_host=local_host("LUMINA_SHISA_HOST"),
            shisa_port=port("LUMINA_SHISA_PORT", 11439),
            shisa_alias=os.environ.get(
                "LUMINA_SHISA_MODEL_ALIAS",
                "shisa-v2.1-unphi4-14b-Q4_K_M",
            ).strip(),
            qwen_host=local_host("LUMINA_QWEN_VLM_HOST"),
            qwen_port=port("LUMINA_QWEN_VLM_PORT", 11440),
            qwen_alias=os.environ.get(
                "LUMINA_QWEN_VLM_MODEL_ALIAS",
                "qwen3-vl-4b-instruct-Q4_K_M",
            ).strip(),
            stt_port=port("LUMINA_STT_PORT", 5057),
            tts_port=port("KOKORO_TTS_PORT", 5058),
            operation_timeout_seconds=float(
                os.environ.get("LUMINA_PREINTEGRATION_OPERATION_TIMEOUT_SECONDS", "240")
            ),
            max_http_response_bytes=int(
                os.environ.get(
                    "LUMINA_PREINTEGRATION_MAX_RESPONSE_BYTES",
                    str(8 * 1024 * 1024),
                )
            ),
            resource_policy_name=os.environ.get(
                "LUMINA_RESOURCE_POLICY",
                "default_m1_16gb",
            ).strip(),
        )

    @property
    def shisa_base_url(self) -> str:
        return f"http://{self.shisa_host}:{self.shisa_port}"

    @property
    def qwen_base_url(self) -> str:
        return f"http://{self.qwen_host}:{self.qwen_port}"


def _bounded_text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _sanitize_error_text(value: Any, limit: int = 300) -> str:
    """Bound client-visible errors and strip prompt-like or secret-looking content."""

    text = _bounded_text(value, limit)
    lowered = text.lower()
    for banned in ("authorization", "api_key", "apikey", "bearer ", "password=", "token="):
        if banned in lowered:
            return "local service error (details redacted)"
    if "data:image" in lowered or "image_base64" in lowered:
        return "local service error (binary payload redacted)"
    return text


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    max_bytes: int,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        # Never echo upstream bodies: they may contain prompts or image payloads.
        raise RuntimeError(f"local service returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"local service is unavailable: {_sanitize_error_text(getattr(exc, 'reason', exc))}"
        ) from exc
    if len(raw) > max_bytes:
        raise RuntimeError("local service response exceeded the configured limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("local service returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("local service returned a non-object JSON response")
    return value


def _probe_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(256 * 1024)
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"reachable": True}
    except Exception as exc:
        return {"reachable": False, "error": _bounded_text(exc, 300)}


def _extract_message_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("model response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise RuntimeError("model response choice is malformed")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("model response message is malformed")
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") in {"text", "output_text"}
        )
    else:
        text = ""
    text = text.strip()
    if not text:
        raise RuntimeError("model response content is empty")
    return text


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ContractError("VLM did not return a JSON object")
    try:
        value = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ContractError(f"VLM returned invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ContractError("VLM JSON root must be an object")
    return value


def _scene_graph_from_partial(
    raw_graph: Mapping[str, Any],
    *,
    request_id: str,
    model: str,
) -> dict[str, Any]:
    """Parse a SceneGraph while retaining valid fragments from partial VLM output."""

    limitations: list[str] = []
    raw_limitations = raw_graph.get("limitations", [])
    if isinstance(raw_limitations, list):
        limitations.extend(
            _bounded_text(item, 400) for item in raw_limitations if _bounded_text(item, 400)
        )

    objects: list[Any] = []
    raw_objects = raw_graph.get("objects", [])
    if isinstance(raw_objects, list):
        for index, item in enumerate(raw_objects):
            try:
                from .vision_runtime import SceneObject

                objects.append(SceneObject.from_dict(item))
            except ContractError as exc:
                limitations.append(f"skipped object[{index}]: {_sanitize_error_text(exc, 160)}")
    else:
        limitations.append("objects was not an array; treated as empty")

    object_ids = {item.object_id for item in objects}
    relations: list[Any] = []
    raw_relations = raw_graph.get("relations", [])
    if isinstance(raw_relations, list):
        for index, item in enumerate(raw_relations):
            try:
                from .vision_runtime import SceneRelation

                relation = SceneRelation.from_dict(item)
                if relation.subject_id not in object_ids or relation.object_id not in object_ids:
                    limitations.append(
                        f"skipped relation[{index}]: dangling object reference"
                    )
                    continue
                relations.append(relation)
            except ContractError as exc:
                limitations.append(f"skipped relation[{index}]: {_sanitize_error_text(exc, 160)}")
    elif raw_relations not in (None, []):
        limitations.append("relations was not an array; treated as empty")

    payload = {
        "request_id": request_id,
        "model": model,
        "schema_version": str(raw_graph.get("schema_version") or "1.0"),
        "summary": _bounded_text(raw_graph.get("summary"), 4000),
        "objects": [item.to_dict() for item in objects],
        "relations": [item.to_dict() for item in relations],
        "image_width": raw_graph.get("image_width"),
        "image_height": raw_graph.get("image_height"),
    }
    try:
        graph = SceneGraph.from_dict(payload, request_id=request_id).to_dict()
    except ContractError as exc:
        # Fall back to a summary-only graph so a partial observation remains usable.
        limitations.append(f"strict validation failed: {_sanitize_error_text(exc, 160)}")
        graph = {
            "schema_version": "1.0",
            "request_id": request_id,
            "model": model,
            "objects": [],
            "relations": [],
            "summary": _bounded_text(raw_graph.get("summary"), 4000)
            or "partial scene observation",
            "image_width": None,
            "image_height": None,
        }

    uncertainty = raw_graph.get("uncertainty", [])
    graph["limitations"] = limitations[:32]
    graph["uncertainty"] = (
        [_bounded_text(item, 400) for item in uncertainty if _bounded_text(item, 400)][:16]
        if isinstance(uncertainty, list)
        else []
    )
    graph["safe_for_dialogue"] = bool(raw_graph.get("safe_for_dialogue", False))
    graph["partial"] = bool(limitations) or not graph.get("objects")
    graph["authority"] = {
        "source": "vlm_observation",
        "may_override_godot_geometry": False,
        "single_image_metric_depth": False,
    }
    return graph


def _load_resource_policy(name: str) -> ResourcePolicy:
    sample = name.strip()
    if sample in ResourcePolicy.sample_names():
        return ResourcePolicy.from_sample(sample)
    return ResourcePolicy()


def _model_weight_bytes(settings: CandidateSettings, kind: ModelKind) -> int:
    if kind is ModelKind.LLM:
        path = settings.root / "data/llm_models/shisa-v2.1-unphi4-14b-Q4_K_M.gguf"
    else:
        path = settings.root / "data/vlm_models/Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    if path.is_file():
        return path.stat().st_size
    return 8 * 1024 * 1024 * 1024


class LocalProcessModelRuntime:
    """Own candidate process lifecycle and local HTTP inference calls."""

    def __init__(self, settings: CandidateSettings) -> None:
        self.settings = settings
        self.loaded_kind: ModelKind | None = None
        self.loaded_model = ""
        self.last_resource_decision: dict[str, Any] = {}
        self.resource_policy = _load_resource_policy(settings.resource_policy_name)
        self.governor = ResourceGovernor(
            heavy_models=(settings.shisa_alias, settings.qwen_alias),
            warning_percent=self.resource_policy.warning_free_percent,
            critical_percent=self.resource_policy.critical_free_percent,
            restore_cooldown_seconds=60.0,
            critical_swap_delta_bytes=self.resource_policy.emergency_swap_delta_bytes,
        )

    def _script(self, name: str) -> Path:
        path = self.settings.root / "scripts" / name
        if not path.is_file():
            raise RuntimeError(f"candidate lifecycle script is missing: {path}")
        return path

    def _resource_snapshot(self) -> tuple[str, str]:
        def output(command: Sequence[str]) -> str:
            try:
                result = subprocess.run(
                    list(command),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return (result.stdout or "") + (result.stderr or "")
            except Exception:
                return ""

        return output(("/usr/bin/memory_pressure",)), output(("/usr/bin/vm_stat",))

    def _refresh_resource_decision(self, *, kind: ModelKind | None = None) -> dict[str, Any]:
        pressure, vm_stat = self._resource_snapshot()
        loaded = (self.loaded_model,) if self.loaded_model else ()
        decision = self.governor.evaluate(
            pressure,
            vm_stat,
            loaded_heavy_models=loaded,
            now=time.monotonic(),
        )
        model_bytes = 0
        if kind is not None:
            model_bytes = _model_weight_bytes(self.settings, kind)
        allow_load, policy_gates = self.resource_policy.allow_model_load(
            model_bytes=model_bytes,
            free_percent=decision.free_percent,
            swap_delta_bytes=decision.swap_delta_bytes,
            loaded_models=loaded,
        )
        allow_request, request_gates = self.resource_policy.allow_new_request(
            free_percent=decision.free_percent,
            swap_delta_bytes=decision.swap_delta_bytes,
            in_flight=0,
        )
        self.last_resource_decision = {
            "pressure_level": decision.pressure_level,
            "effective_level": decision.effective_level,
            "free_percent": decision.free_percent,
            "swap_delta_bytes": decision.swap_delta_bytes,
            "gates": list(decision.gates),
            "policy_gates": list(policy_gates),
            "request_gates": list(request_gates),
            "allow_model_load": allow_load,
            "allow_new_request": allow_request,
            "unload_models": list(decision.unload_models),
            "one_heavy_model_invariant": decision.one_heavy_model_invariant,
            "resource_policy": self.settings.resource_policy_name,
        }
        return self.last_resource_decision

    def _guard_load(self, kind: ModelKind, model: str) -> None:
        decision = self._refresh_resource_decision(kind=kind)
        if decision["effective_level"] == "critical":
            raise RuntimeError("resource governor blocked heavy-model load at critical pressure")
        if not decision["allow_model_load"]:
            gates = decision.get("policy_gates") or decision.get("gates") or ()
            raise RuntimeError(
                "resource policy blocked heavy-model load: "
                + ", ".join(str(gate) for gate in gates)
            )
        if self.loaded_model and self.loaded_model != model:
            raise RuntimeError("resource governor refused a second heavy model")

    async def _run_script(self, script: str, timeout: float | None = None) -> str:
        path = self._script(script)
        env = os.environ.copy()
        env["LUMINA_RUNTIME_ROOT"] = str(self.settings.root)
        env["LUMINA_SHISA_PORT"] = str(self.settings.shisa_port)
        env["LUMINA_QWEN_VLM_PORT"] = str(self.settings.qwen_port)
        process = await asyncio.create_subprocess_exec(
            str(path),
            cwd=str(self.settings.root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout or self.settings.operation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise RuntimeError(f"{script} timed out and was terminated")
        text = stdout.decode("utf-8", errors="replace")[-16000:]
        if process.returncode != 0:
            raise RuntimeError(
                f"{script} failed with exit {process.returncode}: {_sanitize_error_text(text, 800)}"
            )
        return text

    async def start(self, kind: ModelKind, model: str) -> None:
        if self.loaded_kind is kind and self.loaded_model == model:
            return
        await asyncio.to_thread(self._guard_load, kind, model)
        if kind is ModelKind.LLM:
            await self._run_script("start_llama_cpp_shisa14b_mac.sh")
        else:
            await self._run_script("start_llama_cpp_qwen3_vl4b_mac.sh")
        self.loaded_kind = kind
        self.loaded_model = model

    async def stop(self, kind: ModelKind, model: str) -> None:
        if kind is ModelKind.LLM:
            await self._run_script("stop_llama_cpp_shisa14b_mac.sh", timeout=45)
        else:
            await self._run_script("stop_llama_cpp_qwen3_vl4b_mac.sh", timeout=45)
        if self.loaded_kind is kind:
            self.loaded_kind = None
            self.loaded_model = ""

    async def chat(self, kind: ModelKind, request: ChatRequest) -> ChatResponse:
        if kind is not ModelKind.LLM:
            raise RuntimeError("chat is supported only by the Shisa LLM role")
        payload = {
            "model": self.settings.shisa_alias,
            "messages": [dict(message) for message in request.messages],
            "stream": False,
            "temperature": float(request.metadata.get("temperature", 0.76)),
            "top_p": float(request.metadata.get("top_p", 0.93)),
            "max_tokens": int(request.metadata.get("max_tokens", 384)),
            "repeat_penalty": float(request.metadata.get("repeat_penalty", 1.03)),
        }
        started = time.perf_counter()
        response = await asyncio.to_thread(
            _post_json,
            f"{self.settings.shisa_base_url}/v1/chat/completions",
            payload,
            request.timeout_seconds or self.settings.operation_timeout_seconds,
            self.settings.max_http_response_bytes,
        )
        text = _extract_message_text(response)
        return ChatResponse(
            text=text,
            model=str(response.get("model") or self.settings.shisa_alias),
            request_id=request.request_id,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            metadata={"usage": response.get("usage", {}), "backend": "llama.cpp-metal"},
        )

    async def vision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = payload.get("image_base64")
        if not isinstance(encoded, str) or not encoded:
            raise VisionPreprocessError("image_base64 is required")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VisionPreprocessError("image_base64 is invalid") from exc
        prepared = preprocess_vision_input(
            image_bytes,
            expected_mime=str(payload.get("mime_type") or "") or None,
            output_mime=None,
            normalize_if_needed=False,
        )
        request_id = _bounded_text(payload.get("request_id"), 128) or f"vision-{uuid4().hex}"
        prompt = _bounded_text(payload.get("prompt"), 4000) or (
            "画像を日本語で解析し、JSONだけを返してください。"
            "schema_version, summary, objects, relations, limitations, uncertainty, "
            "safe_for_dialogue を含めること。objectsは object_id,label,confidence,"
            "bbox{x,y,width,height},attributes、relationsは subject_id,predicate,"
            "object_id,confidence。bboxは0から1の正規化座標。単眼画像から正確な"
            "距離や3D形状を断定せず、不明点をlimitationsへ記録すること。"
        )
        data_url = (
            f"data:{prepared.mime_type};base64,"
            + base64.b64encode(prepared.image_bytes).decode("ascii")
        )
        request_payload = {
            "model": self.settings.qwen_alias,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        }
        response = await asyncio.to_thread(
            _post_json,
            f"{self.settings.qwen_base_url}/v1/chat/completions",
            request_payload,
            self.settings.operation_timeout_seconds,
            self.settings.max_http_response_bytes,
        )
        raw_graph = _extract_json_object(_extract_message_text(response))
        raw_graph["request_id"] = request_id
        raw_graph["model"] = str(response.get("model") or self.settings.qwen_alias)
        raw_graph.setdefault("schema_version", "1.0")
        return _scene_graph_from_partial(
            raw_graph,
            request_id=request_id,
            model=str(response.get("model") or self.settings.qwen_alias),
        )

    def prerequisites(self) -> dict[str, Any]:
        root = self.settings.root
        paths = {
            "shisa_gguf": root / "data/llm_models/shisa-v2.1-unphi4-14b-Q4_K_M.gguf",
            "qwen_vlm_gguf": root / "data/vlm_models/Qwen3VL-4B-Instruct-Q4_K_M.gguf",
            "qwen_mmproj": root / "data/vlm_models/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
            "sensevoice": root / "data/stt_models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/model.int8.onnx",
            "silero_vad": root / "data/stt_models/silero_vad.onnx",
            "kokoro": root / "data/kokoro_tts/models/kokoro/kokoro-v1.0.int8.onnx",
            "kokoro_voices": root / "data/kokoro_tts/models/kokoro/voices-v1.0.bin",
        }
        packages = {
            name: importlib.util.find_spec(name) is not None
            for name in ("fastapi", "uvicorn", "sherpa_onnx", "kokoro_onnx", "onnxruntime")
        }
        return {
            "paths": {
                name: {"path": str(path), "exists": path.is_file()}
                for name, path in paths.items()
            },
            "packages": packages,
            "all_present": all(path.is_file() for path in paths.values())
            and all(packages.values()),
        }

    def status(self) -> dict[str, Any]:
        services = {
            "shisa": _probe_json(f"{self.settings.shisa_base_url}/health"),
            "qwen_vlm": _probe_json(f"{self.settings.qwen_base_url}/health"),
            "stt": _probe_json(f"http://127.0.0.1:{self.settings.stt_port}/health"),
            "tts": _probe_json(f"http://127.0.0.1:{self.settings.tts_port}/health"),
        }
        # STT/TTS outages must never crash the control plane status payload.
        for name in ("stt", "tts"):
            payload = services[name]
            if not isinstance(payload, dict):
                services[name] = {"reachable": False, "degraded": True}
                continue
            if payload.get("reachable") is False or payload.get("ok") is False:
                services[name] = {
                    **payload,
                    "degraded": True,
                    "optional": True,
                }
        return {
            "loaded_kind": self.loaded_kind.value if self.loaded_kind else None,
            "loaded_model": self.loaded_model or None,
            "resource_governor": self.last_resource_decision,
            "prerequisites": self.prerequisites(),
            "services": services,
        }


class PreintegrationKernel:
    """Compose broker, cognition helpers, safety and local-world boundaries."""

    def __init__(self, settings: CandidateSettings) -> None:
        self.settings = settings
        self.runtime = LocalProcessModelRuntime(settings)
        self.broker = ModelBroker(
            self.runtime,
            llm_model=settings.shisa_alias,
            vlm_model=settings.qwen_alias,
            chat_queue_capacity=4,
            operation_timeout_seconds=settings.operation_timeout_seconds,
            drain_timeout_seconds=30.0,
        )
        self.quality = ConversationQualityChecker(
            mode="expressive",
            relation="companion",
        )
        self.resource_policy = _load_resource_policy(settings.resource_policy_name)
        self.memory = MemorySleep()
        self.autonomy = HumanAutonomy()
        self.karakuri = KarakuriSession(self._karakuri_responder)
        self.world_snapshot: dict[str, Any] = {}

    async def _karakuri_responder(self, request: ResponseRequest) -> str:
        history = [
            {
                "role": turn.role
                if turn.role in {"system", "user", "assistant"}
                else "user",
                "content": turn.text,
            }
            for turn in request.history[-10:]
            if turn.text
        ]
        prompt = build_shisa_messages(
            user_text=request.text,
            history=history,
            mode="expressive",
            relation="acquaintance",
            extra_system=(
                "からくり町で相手の発言へ自然に応答する。"
                "一往復で打ち切らず、相手の反応に応じて2から6ターン程度の会話を"
                "続ける。スキル利用警告やnotification_idを発言へ転送しない。"
            ),
        )
        response = await self.broker.chat(
            list(prompt.messages),
            timeout_seconds=self.settings.operation_timeout_seconds,
            metadata={"max_tokens": 220, "temperature": 0.82, "top_p": 0.94},
        )
        return postprocess_shisa_response(response.text, mode="expressive", max_chars=480)

    async def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        normalized: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, Mapping):
                raise ValueError("each message must be an object")
            role = _bounded_text(item.get("role"), 32)
            content = _bounded_text(item.get("content"), 32000)
            if role not in {"system", "user", "assistant"} or not content:
                raise ValueError("message role/content is invalid")
            normalized.append({"role": role, "content": content})

        mode = _bounded_text(payload.get("mode"), 32) or "expressive"
        relation = _bounded_text(payload.get("relation"), 32) or "companion"
        emotion = _bounded_text(payload.get("emotion"), 64) or None
        use_shisa_prompt = payload.get("use_shisa_prompt", True) is not False
        auto_rewrite = payload.get("auto_rewrite", True) is not False
        eval_category = _bounded_text(payload.get("eval_category"), 64) or None

        prepared = prepare_chat_messages(
            normalized,
            mode=mode,
            relation=relation,
            emotion=emotion,
            resource_policy=self.resource_policy,
            use_shisa_prompt=use_shisa_prompt,
            max_context_tokens=int(payload.get("max_context_tokens"))
            if payload.get("max_context_tokens") is not None
            else None,
        )

        resource_decision = await asyncio.to_thread(
            self.runtime._refresh_resource_decision,
            kind=ModelKind.LLM,
        )
        if not resource_decision.get("allow_new_request", True):
            gates = resource_decision.get("request_gates") or resource_decision.get("gates") or ()
            raise RuntimeError(
                "resource policy blocked chat admission: "
                + ", ".join(str(gate) for gate in gates)
            )

        request_id = _bounded_text(payload.get("request_id"), 128) or f"chat-{uuid4().hex}"
        response = await self.broker.chat(
            list(prepared.messages),
            request_id=request_id,
            timeout_seconds=float(
                payload.get("timeout_seconds")
                or self.settings.operation_timeout_seconds
            ),
            metadata={
                "temperature": float(payload.get("temperature", 0.76)),
                "top_p": float(payload.get("top_p", 0.93)),
                "max_tokens": int(payload.get("max_tokens", 384)),
                "repeat_penalty": float(payload.get("repeat_penalty", 1.03)),
            },
        )
        final = finalize_chat_response(
            response.text,
            prepared=prepared,
            eval_category=eval_category,
            auto_rewrite=auto_rewrite,
        )
        quality = self.quality.check(
            final.text,
            history=list(prepared.history),
            unresolved_topics=payload.get("unresolved_topics"),
            context={"mode": mode, "relation": relation},
        )
        gate_payload = gate_to_lumina_payload(
            gate=final.gate,
            prepared=prepared,
            resource_decision=resource_decision,
        )
        return {
            "id": response.request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": final.text},
                    "finish_reason": "stop",
                }
            ],
            "usage": response.metadata.get("usage", {}),
            "lumina": {
                "latency_ms": response.latency_ms,
                "quality": quality.to_dict(),
                "conversation_gate": gate_payload,
                "candidate": True,
            },
        }

    async def analyze_scene(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            return await self.runtime.vision(payload)

        try:
            return await self.broker.use_model(
                ModelKind.VLM,
                operation,
                timeout_seconds=float(
                    payload.get("timeout_seconds")
                    or self.settings.operation_timeout_seconds
                ),
            )
        finally:
            # Load-on-demand: release the VLM after vision so LLM/VLM never stay co-resident.
            try:
                await self.broker.unload()
            except Exception:
                pass

    async def karakuri_notification(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return (await self.karakuri.handle_notification(payload)).to_dict()

    def memory_sleep(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        result = self.memory.sleep(
            events,
            trigger=_bounded_text(payload.get("trigger"), 64) or "manual",
            observed_at=_bounded_text(payload.get("observed_at"), 64),
            sleep_id=_bounded_text(payload.get("sleep_id"), 128) or None,
        )
        return {
            "sleep_id": result.sleep_id,
            "changed": result.changed,
            "state": result.state.to_dict(),
            "report": dict(result.report),
            "rollback_available": True,
        }

    def autonomy_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.autonomy.plan(payload).as_dict()

    def conversation_quality(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.quality.check(
            payload.get("response"),
            history=payload.get("history"),
            unresolved_topics=payload.get("unresolved_topics"),
            context=payload.get("context")
            if isinstance(payload.get("context"), Mapping)
            else None,
        ).to_dict()

    def conversation_gate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = payload.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ValueError("response must be a non-empty string")
        mode = _bounded_text(payload.get("mode"), 32) or "expressive"
        relation = _bounded_text(payload.get("relation"), 32) or "companion"
        eval_category = _bounded_text(payload.get("eval_category"), 64) or None
        auto_rewrite = payload.get("auto_rewrite", True) is not False
        history = payload.get("history")
        if history is not None and not isinstance(history, list):
            raise ValueError("history must be a list when provided")
        report = evaluate_conversation_gate(
            response,
            history=history,
            mode=mode,
            relation=relation,
            eval_category=eval_category,
            auto_rewrite=auto_rewrite,
        )
        return report.to_dict()

    def tts_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_for_tts_japanese(
            payload.get("text"),
            user_dictionary=payload.get("user_dictionary")
            if isinstance(payload.get("user_dictionary"), Mapping)
            else None,
            max_chars=4000,
        )
        chunks = split_tts_sentence_chunks(
            normalized,
            max_chars=int(payload.get("max_chars", 80)),
        )
        tts_health = _probe_json(f"http://127.0.0.1:{self.settings.tts_port}/health")
        return {
            "normalized_text": normalized,
            "chunks": [
                {"index": index, "text": text}
                for index, text in enumerate(chunks)
            ],
            "tts_url": f"http://127.0.0.1:{self.settings.tts_port}",
            "voice": payload.get("voice") or "jf_alpha",
            "lang_code": "j",
            "tts_reachable": bool(tts_health.get("reachable", True))
            and tts_health.get("ok", True) is not False,
            "degraded": tts_health.get("reachable") is False
            or tts_health.get("ok") is False,
        }

    def ingest_world_telemetry(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        authority = payload.get("authority")
        if payload.get("source") != "godot_engine" or not isinstance(
            authority,
            Mapping,
        ):
            raise ValueError("world telemetry must come from godot_engine")
        if (
            authority.get("geometry") != "godot_engine"
            or authority.get("vlm_may_override_geometry") is not False
        ):
            raise ValueError("world telemetry authority is invalid")
        # Reject obviously corrupt numeric fields without accepting the snapshot.
        for key in ("player_position", "avatar_position", "camera_position"):
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, Mapping):
                raise ValueError(f"{key} must be an object when present")
            for axis in ("x", "y", "z"):
                if axis not in value:
                    continue
                try:
                    number = float(value[axis])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key}.{axis} is not numeric") from exc
                if number != number or abs(number) == float("inf"):
                    raise ValueError(f"{key}.{axis} is not a finite number")
        self.world_snapshot = dict(payload)
        return {
            "accepted": True,
            "captured_at_utc": payload.get("captured_at_utc"),
            "valid": payload.get("valid"),
            "error_count": len(payload.get("errors", []))
            if isinstance(payload.get("errors"), list)
            else None,
        }

    async def status(self) -> dict[str, Any]:
        runtime_status = await asyncio.to_thread(self.runtime.status)
        broker = await self.broker.snapshot()
        return {
            "service": "lumina-preintegration-control",
            "candidate": True,
            "integration_test_executed": False,
            "broker": broker,
            "runtime": runtime_status,
            "world_telemetry": {
                "available": bool(self.world_snapshot),
                "captured_at_utc": self.world_snapshot.get("captured_at_utc"),
            },
        }

    async def health(self) -> dict[str, Any]:
        """Liveness only: the control process is up. Does not imply models are ready."""

        broker = await self.broker.snapshot()
        return {
            "service": "lumina-preintegration-control",
            "ok": True,
            "ready": False,
            "health": "alive",
            "candidate": True,
            "broker_state": broker.get("state"),
            "integration_test_executed": False,
        }

    async def ready(self) -> dict[str, Any]:
        """Readiness: prerequisites present and broker not faulted. Models stay unloaded."""

        runtime_status = await asyncio.to_thread(self.runtime.status)
        broker = await self.broker.snapshot()
        prerequisites = runtime_status.get("prerequisites", {})
        services = runtime_status.get("services", {})
        stt = services.get("stt") if isinstance(services, dict) else {}
        tts = services.get("tts") if isinstance(services, dict) else {}
        optional_degraded = []
        if isinstance(stt, dict) and (
            stt.get("reachable") is False or stt.get("ok") is False or stt.get("degraded")
        ):
            optional_degraded.append("stt")
        if isinstance(tts, dict) and (
            tts.get("reachable") is False or tts.get("ok") is False or tts.get("degraded")
        ):
            optional_degraded.append("tts")
        ready = bool(prerequisites.get("all_present")) and broker.get("state") != "fault"
        return {
            "service": "lumina-preintegration-control",
            "ok": ready,
            "ready": ready,
            "health": "ready" if ready else "not_ready",
            "candidate": True,
            "broker_state": broker.get("state"),
            "prerequisites_ok": bool(prerequisites.get("all_present")),
            "optional_degraded": optional_degraded,
            "models_loaded": runtime_status.get("loaded_kind") is not None,
            "integration_test_executed": False,
        }

    async def close(self) -> None:
        await self.broker.close(timeout_seconds=30.0)


SETTINGS = CandidateSettings.from_environment()


@asynccontextmanager
async def _lifespan(application: FastAPI):
    application.state.kernel = PreintegrationKernel(SETTINGS)
    try:
        yield
    finally:
        await application.state.kernel.close()


app = FastAPI(
    title="Lumina Pre-Integration Control",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


def _kernel(request: Request) -> PreintegrationKernel:
    return request.app.state.kernel


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ValueError, VisionPreprocessError, ContractError)):
        return HTTPException(status_code=400, detail=_sanitize_error_text(exc, 400))
    if isinstance(exc, BrokerError):
        return HTTPException(status_code=503, detail=_sanitize_error_text(exc, 400))
    return HTTPException(status_code=500, detail=_sanitize_error_text(exc, 400))


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return await _kernel(request).health()


@app.get("/ready")
@app.get("/readyz")
async def ready(request: Request) -> dict[str, Any]:
    payload = await _kernel(request).ready()
    if not payload.get("ready"):
        raise HTTPException(status_code=503, detail="preintegration control is not ready")
    return payload


@app.get("/v1/status")
async def status(request: Request) -> dict[str, Any]:
    return await _kernel(request).status()


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": SETTINGS.shisa_alias,
                "object": "model",
                "owned_by": "local-lumina-candidate",
            }
        ],
    }


async def _dispatch(
    request: Request,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    kernel = _kernel(request)
    try:
        if operation == "chat":
            return await kernel.chat(payload)
        if operation == "vision":
            return await kernel.analyze_scene(payload)
        if operation == "karakuri":
            return await kernel.karakuri_notification(payload)
        if operation == "memory":
            return kernel.memory_sleep(payload)
        if operation == "autonomy":
            return kernel.autonomy_plan(payload)
        if operation == "quality":
            return kernel.conversation_quality(payload)
        if operation == "gate":
            return kernel.conversation_gate(payload)
        if operation == "tts":
            return kernel.tts_plan(payload)
        if operation == "world":
            return kernel.ingest_world_telemetry(payload)
        raise ValueError("unknown pre-integration operation")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "chat", payload)


@app.post("/v1/vision/scene-graph")
async def scene_graph(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "vision", payload)


@app.post("/v1/memory/sleep")
async def memory_sleep(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "memory", payload)


@app.post("/v1/autonomy/plan")
async def autonomy_plan(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "autonomy", payload)


@app.post("/v1/conversation/quality")
async def conversation_quality(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "quality", payload)


@app.post("/v1/conversation/gate")
async def conversation_gate(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "gate", payload)


@app.post("/v1/karakuri/notification")
async def karakuri_notification(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "karakuri", payload)


@app.post("/v1/tts/plan")
async def tts_plan(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "tts", payload)


@app.post("/v1/world/telemetry")
async def world_telemetry(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object body is required")
    return await _dispatch(request, "world", payload)
