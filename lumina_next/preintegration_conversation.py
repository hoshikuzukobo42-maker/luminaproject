"""Conversation pipeline for the pre-integration control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .conversation_gate import ConversationGateReport, RewritePolicy, evaluate_conversation_gate
from .resource_policy import ResourcePolicy
from .shisa_conversation import build_shisa_messages, postprocess_shisa_response


_RELATION_ALIASES = {
    "companion": "acquaintance",
    "collaborator": "acquaintance",
    "close": "friend",
    "unknown": "stranger",
}


def map_relation(relation: str | None) -> str:
    key = str(relation or "acquaintance").strip().casefold()
    return _RELATION_ALIASES.get(key, key if key in _RELATION_ALIASES.values() else "acquaintance")


@dataclass(frozen=True)
class PreparedChatMessages:
    messages: tuple[dict[str, str], ...]
    user_text: str
    history: tuple[dict[str, str], ...]
    mode: str
    relation: str
    emotion: str | None
    trimmed: bool
    estimated_tokens: int


@dataclass(frozen=True)
class FinalChatResponse:
    text: str
    gate: ConversationGateReport
    prepared: PreparedChatMessages


def _estimate_messages_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    from .resource_policy import estimate_ja_tokens

    return sum(estimate_ja_tokens(str(item.get("content", ""))) for item in messages)


def extract_user_turn(
    messages: Sequence[Mapping[str, str]],
) -> tuple[str, tuple[dict[str, str], ...]]:
    normalized: list[dict[str, str]] = []
    last_user_index = -1
    for item in messages:
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        if role in {"assistant", "bot", "lumina", "model"}:
            role = "assistant"
        normalized.append({"role": role, "content": content})
        if role == "user":
            last_user_index = len(normalized) - 1
    if last_user_index < 0:
        raise ValueError("messages must include at least one user turn")
    user_text = normalized[last_user_index]["content"]
    history = tuple(normalized[:last_user_index])
    return user_text, history


def prepare_chat_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    mode: str = "expressive",
    relation: str = "companion",
    emotion: str | None = None,
    resource_policy: ResourcePolicy | None = None,
    use_shisa_prompt: bool = True,
    max_context_tokens: int | None = None,
) -> PreparedChatMessages:
    user_text, history = extract_user_turn(messages)
    mapped_relation = map_relation(relation)
    policy = resource_policy or ResourcePolicy()

    if use_shisa_prompt:
        prompt = build_shisa_messages(
            user_text=user_text,
            history=history,
            mode=mode,
            relation=mapped_relation,
            emotion=emotion,
        )
        broker_messages = list(prompt.messages)
    else:
        broker_messages = [dict(item) for item in messages]

    before_tokens = _estimate_messages_tokens(broker_messages)
    target_tokens = max_context_tokens or policy.max_context_tokens
    trimmed_messages = policy.shorten_messages(broker_messages, target_tokens=target_tokens)
    after_tokens = _estimate_messages_tokens(trimmed_messages)

    return PreparedChatMessages(
        messages=trimmed_messages,
        user_text=user_text,
        history=history,
        mode=mode,
        relation=mapped_relation,
        emotion=emotion,
        trimmed=after_tokens < before_tokens,
        estimated_tokens=after_tokens,
    )


def finalize_chat_response(
    raw_text: str,
    *,
    prepared: PreparedChatMessages,
    eval_category: str | None = None,
    auto_rewrite: bool = True,
    rewrite_policy: RewritePolicy | None = None,
) -> FinalChatResponse:
    processed = postprocess_shisa_response(
        raw_text,
        mode=prepared.mode,
        emotion=prepared.emotion,
    )
    gate = evaluate_conversation_gate(
        processed,
        history=prepared.history,
        mode=prepared.mode,
        relation=prepared.relation,
        eval_category=eval_category,
        auto_rewrite=auto_rewrite,
        rewrite_policy=rewrite_policy,
    )
    return FinalChatResponse(text=gate.response, gate=gate, prepared=prepared)


def gate_to_lumina_payload(
    *,
    gate: ConversationGateReport,
    prepared: PreparedChatMessages,
    resource_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "gate_passed": gate.passed,
        "issues": list(gate.issues),
        "rewritten": gate.rewritten,
        "rewrite_applied": gate.rewrite_applied,
        "quality": gate.quality.to_dict(),
        "diversity": {
            "needs_rewrite": gate.diversity.needs_rewrite,
            "rewrite_hints": list(gate.diversity.rewrite_hints),
            "ngram_repeat_score": gate.diversity.ngram_repeat_score,
            "ending_repeat_score": gate.diversity.ending_repeat_score,
            "length_monotony": gate.diversity.length_monotony,
        },
        "shisa": {
            "mode": prepared.mode,
            "relation": prepared.relation,
            "emotion": prepared.emotion,
            "context_trimmed": prepared.trimmed,
            "estimated_context_tokens": prepared.estimated_tokens,
        },
    }
    if gate.eval_result is not None:
        payload["eval"] = {
            "category": gate.eval_result.category,
            "score": gate.eval_result.score,
            "passed": gate.eval_result.passed,
            "notes": list(gate.eval_result.notes),
        }
    if resource_decision:
        payload["resource_policy"] = dict(resource_decision)
    return payload


__all__ = [
    "FinalChatResponse",
    "PreparedChatMessages",
    "extract_user_turn",
    "finalize_chat_response",
    "gate_to_lumina_payload",
    "map_relation",
    "prepare_chat_messages",
]
