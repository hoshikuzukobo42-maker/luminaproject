"""Deterministic prompt-contract and output-leak detector for P1-02."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

from .orchestrator import LuminaOrchestrator, TurnInput


PROMPT_CONTRACT_SCHEMA = "lumina.conversation.prompt-contract.v1"
PROMPT_FIXTURE_SCHEMA = "lumina.conversation.prompt-fixture.v1"

_BRAND = re.compile(
    r"(?:OpenAI|ChatGPT|GPT(?:-[A-Za-z0-9.]+)?|Qwen(?:[A-Za-z0-9._-]+)?|"
    r"Alibaba|PLaMo|WayBob|Claude)",
    re.IGNORECASE,
)
_SELF_MODEL = re.compile(
    r"(?:私は|わたしは|僕は|I\s+am)\s*(?:an?\s+)?(?:AI|人工知能|LLM|言語モデル|chatbot)",
    re.IGNORECASE,
)
_SYSTEM_LEAK = re.compile(
    r"(?:persona/system:|現在の認知状態:|会話履歴・記憶:|system\s*prompt)",
    re.IGNORECASE,
)
_UNRECEIPTED_ACTION = re.compile(
    r"(?:送信|削除|購入|予約|起動|停止|投稿|連絡)(?:を)?(?:しました|しておきました|完了しました)"
)
_ANALYSIS_LEAK = re.compile(r"(?:分析過程|思考過程|採点|内部推論)\s*[:：]")
_BULLET = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass(frozen=True)
class PromptBuild:
    prompt: str
    sha256: str
    section_offsets: dict[str, int]


class _FixtureMemory:
    def __init__(self, state_policy: str) -> None:
        self._state_policy = state_policy

    @staticmethod
    def session_key(guild_id: str, channel_id: str, session_id: str | None = None) -> str:
        return session_id or f"{guild_id}:{channel_id}"

    def state_policy(self, session_key: str) -> str:
        return self._state_policy


class _FixtureCognition:
    def __init__(self, context: str) -> None:
        self._context = context

    def prompt_context(self) -> str:
        return self._context


def _markers(input_mode: str, memory_context: str) -> list[tuple[str, str]]:
    length_marker = (
        "音声向けに一文・80文字以内。"
        if input_mode == "voice"
        else "短い雑談として1〜2文・原則80文字以内。"
    )
    memory_marker = (
        "履歴とmemoryは発言記録。"
        if memory_context
        else "未記載の過去や好みを創作しない。"
    )
    return [
        ("persona_system", "persona/system:"),
        ("identity", "あなたはローカル会話相棒のルミナ。"),
        ("response_length", length_marker),
        ("interaction_style", "相手の発言から一歩だけ反応し"),
        ("state_policy", "現在の話し方:"),
        ("cognitive_state", "現在の認知状態:"),
        ("memory_grounding", memory_marker),
        ("memory_context", "会話履歴・記憶:"),
    ]


def build_prompt_fixture(case: Mapping[str, Any]) -> PromptBuild:
    input_mode = str(case.get("input_mode", "text"))
    memory_context = str(case.get("memory_context", ""))
    orchestrator = object.__new__(LuminaOrchestrator)
    orchestrator.config = SimpleNamespace(primary_model="qwen3.5-4b-q4_K_M")
    orchestrator.memory = _FixtureMemory(str(case.get("state_policy", "標準")))
    orchestrator.cognition = _FixtureCognition(str(case.get("cognitive_state", "AWAKE")))
    turn = TurnInput(
        guild_id=str(case.get("guild_id", "fixture")),
        channel_id=str(case.get("channel_id", "prompt")),
        user_id=str(case.get("user_id", "fixture-user")),
        text=str(case.get("text", "こんにちは")),
        persona=str(case.get("persona", "lumina")),
        persona_mode=str(case.get("persona_mode", "auto")),
        session_id=str(case.get("session_id", "fixture-session")),
        input_mode=input_mode,
    )
    prompt = orchestrator._system_prompt(
        turn,
        memory_context,
        semantic_memory=bool(case.get("semantic_memory", False)),
    )
    offsets = {name: prompt.find(marker) for name, marker in _markers(input_mode, memory_context)}
    return PromptBuild(
        prompt=prompt,
        sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        section_offsets=offsets,
    )


def validate_prompt_fixture(case: Mapping[str, Any]) -> dict[str, Any]:
    first = build_prompt_fixture(case)
    second = build_prompt_fixture(case)
    offsets = list(first.section_offsets.values())
    state_policy = str(case.get("state_policy", "標準"))
    cognitive_state = str(case.get("cognitive_state", "AWAKE"))
    memory_context = str(case.get("memory_context", ""))
    requested_mode = str(case.get("persona_mode", "auto")).strip().lower()
    expected_mode = (
        requested_mode
        if requested_mode
        in {"auto", "lumina_chat", "lumina_chat_fast", "lumina_mod", "lumina_announce"}
        else "auto"
    )
    checks = {
        "schema": case.get("schema_version") == PROMPT_FIXTURE_SCHEMA,
        "deterministic": first.sha256 == second.sha256 and first.prompt == second.prompt,
        "all_sections_present": all(offset >= 0 for offset in offsets),
        "section_order_exact": offsets == sorted(offsets) and len(set(offsets)) == len(offsets),
        "state_policy_preserved": state_policy in first.prompt,
        "cognition_preserved": cognitive_state in first.prompt,
        "memory_context_preserved": not memory_context or memory_context in first.prompt,
        "persona_mode_preserved": f"現在の会話モード: {expected_mode}" in first.prompt,
    }
    return {
        "case_id": str(case.get("case_id", "")),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "prompt_sha256": first.sha256,
        "checks": checks,
        "section_offsets": first.section_offsets,
    }


def detect_prohibited_output(
    text: str,
    *,
    input_mode: str = "text",
    operation_receipt: bool = False,
) -> list[str]:
    value = str(text or "").strip()
    violations: list[str] = []
    if not value:
        violations.append("empty_output")
    if _BRAND.search(value):
        violations.append("brand_leak")
    if _SELF_MODEL.search(value):
        violations.append("self_model_claim")
    if _SYSTEM_LEAK.search(value):
        violations.append("system_prompt_leak")
    if not operation_receipt and _UNRECEIPTED_ACTION.search(value):
        violations.append("unreceipted_action_claim")
    if _ANALYSIS_LEAK.search(value):
        violations.append("analysis_leak")
    if _BULLET.search(value):
        violations.append("forbidden_list_format")
    if input_mode == "voice" and len(value) > 80:
        violations.append("voice_length_exceeded")
    return sorted(set(violations))


def contract_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "PROMPT_CONTRACT_SCHEMA",
    "PROMPT_FIXTURE_SCHEMA",
    "PromptBuild",
    "build_prompt_fixture",
    "contract_digest",
    "detect_prohibited_output",
    "validate_prompt_fixture",
]
