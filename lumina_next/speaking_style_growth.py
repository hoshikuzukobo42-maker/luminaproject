"""Minimal speaking-style growth from user reactions.

Pure helpers: classify a reaction to the previous reply, update a compact
style state, and render a short hint for the next system/state policy.
"""

from __future__ import annotations

from typing import Any, Mapping

REACTIONS = ("continued", "short", "rejected", "neutral")
PREFERRED = ("short", "natural", "continued")

_SHORT_TERMS = (
    "短く",
    "もっと短",
    "長い",
    "長すぎ",
    "簡潔",
    "一言で",
    "手短",
)
_CONTINUE_TERMS = (
    "続けて",
    "もっと話",
    "もう少し",
    "詳しく",
    "もっと教えて",
    "それで",
    "それから",
    "うんうん",
    "なるほど",
)
_REJECT_TERMS = (
    "違う",
    "違います",
    "ダメ",
    "だめ",
    "おかしい",
    "やめ",
    "やめて",
    "嫌い",
    "つまらない",
    "言い方",
    "自然に",
    "かしこまり",
)


def empty_style_state() -> dict[str, Any]:
    return {
        "preferred": "natural",
        "last_reaction": "neutral",
        "short_score": 0.0,
        "continued_score": 0.0,
        "rejected_score": 0.0,
        "hint": "",
        "updated_at": "",
    }


def normalize_style_state(value: Any) -> dict[str, Any]:
    base = empty_style_state()
    if not isinstance(value, Mapping):
        return base
    for key in ("short_score", "continued_score", "rejected_score"):
        try:
            base[key] = round(max(0.0, min(5.0, float(value.get(key, 0.0)))), 3)
        except (TypeError, ValueError):
            base[key] = 0.0
    preferred = str(value.get("preferred") or "natural").strip()
    base["preferred"] = preferred if preferred in PREFERRED else "natural"
    reaction = str(value.get("last_reaction") or "neutral").strip()
    base["last_reaction"] = reaction if reaction in REACTIONS else "neutral"
    base["hint"] = str(value.get("hint") or "").strip()[:80]
    base["updated_at"] = str(value.get("updated_at") or "")[:40]
    return base


def classify_style_reaction(user_text: str, *, previous_reply: str = "") -> str:
    text = " ".join(str(user_text or "").split())
    if not text:
        return "neutral"
    if any(term in text for term in _REJECT_TERMS):
        return "rejected"
    if any(term in text for term in _SHORT_TERMS):
        return "short"
    if any(term in text for term in _CONTINUE_TERMS):
        return "continued"
    prev = " ".join(str(previous_reply or "").split())
    # Short follow-up after a long reply tends to mean "too long".
    if prev and len(prev) >= 80 and len(text) <= 12 and text.endswith(("？", "?", "ね", "よ", "か")):
        return "short"
    # Engaged follow-up without rejection markers.
    if len(text) >= 8 and not text.endswith(("？", "?")):
        return "continued"
    return "neutral"


def render_style_hint(state: Mapping[str, Any]) -> str:
    preferred = str(state.get("preferred") or "natural")
    rejected = float(state.get("rejected_score") or 0.0)
    if preferred == "short":
        hint = "短めで自然に。質問を重ねすぎない。"
    elif preferred == "continued":
        hint = "少し踏み込んで話を広げてよい。押しつけはしない。"
    else:
        hint = "相手の距離感に合わせた自然な長さで。"
    if rejected >= 1.0:
        hint += "最近の言い方への違和感を避け、やわらかく直す。"
    return hint[:80]


def apply_style_reaction(
    state: Any,
    user_text: str,
    *,
    previous_reply: str = "",
    updated_at: str = "",
) -> dict[str, Any]:
    current = normalize_style_state(state)
    reaction = classify_style_reaction(user_text, previous_reply=previous_reply)
    current["last_reaction"] = reaction
    if reaction == "short":
        current["short_score"] = round(min(5.0, float(current["short_score"]) + 1.0), 3)
        current["continued_score"] = round(max(0.0, float(current["continued_score"]) - 0.25), 3)
    elif reaction == "continued":
        current["continued_score"] = round(min(5.0, float(current["continued_score"]) + 0.75), 3)
        current["short_score"] = round(max(0.0, float(current["short_score"]) - 0.15), 3)
    elif reaction == "rejected":
        current["rejected_score"] = round(min(5.0, float(current["rejected_score"]) + 1.0), 3)
        current["continued_score"] = round(max(0.0, float(current["continued_score"]) - 0.35), 3)
    scores = {
        "short": float(current["short_score"]),
        "continued": float(current["continued_score"]),
        "natural": 0.4,
    }
    # Rejection nudges away from continued expansion toward quieter natural.
    if float(current["rejected_score"]) >= 1.5:
        scores["natural"] += 0.8
        scores["continued"] -= 0.4
    preferred = max(scores.items(), key=lambda item: item[1])[0]
    if preferred not in PREFERRED:
        preferred = "natural"
    current["preferred"] = preferred
    current["hint"] = render_style_hint(current)
    if updated_at:
        current["updated_at"] = str(updated_at)[:40]
    return current


def style_policy_clause(state: Any) -> str:
    normalized = normalize_style_state(state)
    hint = str(normalized.get("hint") or "").strip()
    if not hint:
        return ""
    return f"話し方の育ち: {hint}"
