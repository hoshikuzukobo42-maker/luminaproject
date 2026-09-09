"""Integrated offline quality + diversity gate for Lumina conversation turns.

This module composes ``conversation_quality``, ``conversation_diversity``, and
optional ``japanese_eval`` scoring.  It is intentionally not wired into the
live runtime; callers can use it from tests, fixtures, and acceptance scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from lumina_next.conversation_diversity import DiversityMetrics, analyze_diversity, apply_local_rewrite
from lumina_next.conversation_quality import QualityReport, evaluate_conversation_quality
from lumina_next.japanese_eval import EvalCase, EvalResult, evaluate_response, generate_eval_case


@dataclass(frozen=True)
class RewritePolicy:
    """Rules for when local rewrite is allowed after quality evaluation."""

    allow_on_warn: bool = True
    block_on_quality_fail: bool = True
    max_passes: int = 2
    min_quality_score: float = 0.55
    min_diversity_score: float = 0.45

    def allows_rewrite(self, *, quality: QualityReport, diversity: DiversityMetrics) -> bool:
        if self.block_on_quality_fail and not quality.passed:
            return False
        if quality.score < self.min_quality_score:
            return False
        diversity_score = 1.0 - max(
            diversity.ngram_repeat_score,
            diversity.ending_repeat_score,
            diversity.length_monotony,
        )
        if diversity_score < self.min_diversity_score and not self.allow_on_warn:
            return False
        return diversity.needs_rewrite


@dataclass(frozen=True)
class ConversationGateReport:
    response: str
    quality: QualityReport
    diversity: DiversityMetrics
    eval_result: EvalResult | None
    rewritten: bool
    rewrite_applied: bool
    passed: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "passed": self.passed,
            "rewritten": self.rewritten,
            "rewrite_applied": self.rewrite_applied,
            "issues": list(self.issues),
            "quality": self.quality.to_dict(),
            "diversity": {
                "needs_rewrite": self.diversity.needs_rewrite,
                "rewrite_hints": list(self.diversity.rewrite_hints),
                "ngram_repeat_score": self.diversity.ngram_repeat_score,
                "ending_repeat_score": self.diversity.ending_repeat_score,
                "length_monotony": self.diversity.length_monotony,
                "paragraph_count": self.diversity.paragraph_count,
                "paragraph_monotony": self.diversity.paragraph_monotony,
            },
            "eval_result": None
            if self.eval_result is None
            else {
                "category": self.eval_result.category,
                "score": self.eval_result.score,
                "passed": self.eval_result.passed,
                "notes": list(self.eval_result.notes),
            },
        }


def _history_texts(history: Sequence[Any] | None) -> tuple[str, ...]:
    texts: list[str] = []
    for item in history or ():
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, Mapping):
            content = str(item.get("content", item.get("text", "")))
            if content:
                texts.append(content)
    return tuple(texts)


def score_conversation_turn(
    response: str,
    *,
    history: Sequence[Any] | None = None,
    mode: str = "expressive",
    relation: str = "acquaintance",
    eval_category: str | None = None,
    eval_seed: int = 42,
) -> ConversationGateReport:
    quality = evaluate_conversation_quality(
        response,
        history,
        mode=mode,
        relation=relation,
    )
    diversity = analyze_diversity(
        response,
        history=_history_texts(history),
        mode=mode,
    )
    eval_result = None
    if eval_category:
        case = generate_eval_case(eval_category, seed=eval_seed)
        eval_result = evaluate_response(case, response)
    issues = tuple(
        issue
        for issue in (
            *quality.flags,
            *(hint for hint in diversity.rewrite_hints if hint not in quality.flags),
        )
        if issue
    )
    passed = quality.passed and not diversity.needs_rewrite
    if eval_result is not None and not eval_result.passed:
        passed = False
    return ConversationGateReport(
        response=response,
        quality=quality,
        diversity=diversity,
        eval_result=eval_result,
        rewritten=False,
        rewrite_applied=False,
        passed=passed,
        issues=issues,
    )


def apply_rewrite_policy(
    response: str,
    *,
    history: Sequence[Any] | None = None,
    mode: str = "expressive",
    relation: str = "acquaintance",
    policy: RewritePolicy | None = None,
) -> tuple[str, ConversationGateReport]:
    active_policy = policy or RewritePolicy()
    initial = score_conversation_turn(response, history=history, mode=mode, relation=relation)
    if not active_policy.allows_rewrite(quality=initial.quality, diversity=initial.diversity):
        return response, initial

    rewritten = apply_local_rewrite(
        response,
        initial.diversity.rewrite_hints,
        max_passes=active_policy.max_passes,
    )
    final = score_conversation_turn(rewritten, history=history, mode=mode, relation=relation)
    report = ConversationGateReport(
        response=rewritten,
        quality=final.quality,
        diversity=final.diversity,
        eval_result=final.eval_result,
        rewritten=rewritten != response,
        rewrite_applied=True,
        passed=final.passed,
        issues=final.issues,
    )
    return rewritten, report


def evaluate_conversation_gate(
    response: str,
    *,
    history: Sequence[Any] | None = None,
    mode: str = "expressive",
    relation: str = "acquaintance",
    eval_category: str | None = None,
    eval_seed: int = 42,
    rewrite_policy: RewritePolicy | None = None,
    auto_rewrite: bool = False,
) -> ConversationGateReport:
    if auto_rewrite:
        _, report = apply_rewrite_policy(
            response,
            history=history,
            mode=mode,
            relation=relation,
            policy=rewrite_policy,
        )
        if eval_category:
            case: EvalCase = generate_eval_case(eval_category, seed=eval_seed)
            eval_result = evaluate_response(case, report.response)
            passed = report.passed and eval_result.passed
            return ConversationGateReport(
                response=report.response,
                quality=report.quality,
                diversity=report.diversity,
                eval_result=eval_result,
                rewritten=report.rewritten,
                rewrite_applied=report.rewrite_applied,
                passed=passed,
                issues=report.issues,
            )
        return report
    return score_conversation_turn(
        response,
        history=history,
        mode=mode,
        relation=relation,
        eval_category=eval_category,
        eval_seed=eval_seed,
    )


__all__ = [
    "ConversationGateReport",
    "RewritePolicy",
    "apply_rewrite_policy",
    "evaluate_conversation_gate",
    "score_conversation_turn",
]
