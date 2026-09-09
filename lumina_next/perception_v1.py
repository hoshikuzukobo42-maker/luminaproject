"""Explainable, confidence-preserving perception adapters for Lumina."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "lumina.perception.v1"
RULE_VERSION = "lumina.perception.rules.v1"
LABELS = ("trouble", "interest", "disengagement", "none")


class PerceptionContractError(ValueError):
    pass


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PerceptionContractError(f"{name} must be an object")
    return value


def _bounded(value: Any, default: float = 1.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise PerceptionContractError("confidence must be finite and in [0, 1]")
    return result


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result):
        raise PerceptionContractError("numeric fact must be finite")
    return result


@dataclass(frozen=True)
class _Signal:
    score: float
    fact_refs: tuple[str, ...]
    rationale: str


class PerceptionAdapter:
    """Convert observable facts into explicitly separate, explainable inferences."""

    trouble_words = ("困", "わから", "分から", "助け", "help", "lost", "how do", "できない")
    interest_words = ("詳しく", "もっと", "これは", "何", "どう", "why", "what", "tell me")
    trouble_actions = ("failed", "invalid", "retry")
    interest_actions = ("select", "approach", "inspect", "open_detail")
    disengagement_actions = ("exit", "leave", "cancel_session")

    def infer(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        source = _mapping(observation, "observation")
        facts: list[dict[str, Any]] = []

        speech = source.get("speech")
        if speech is not None:
            speech_map = _mapping(speech, "speech")
            text = str(speech_map.get("text") or "").strip()
            if text:
                facts.append({
                    "fact_id": "fact-speech",
                    "kind": "speech",
                    "observed": {"text": text, "final": bool(speech_map.get("final", True))},
                    "confidence": _bounded(speech_map.get("confidence"), 0.5),
                })

        position = source.get("position")
        if position is not None:
            position_map = _mapping(position, "position")
            facts.append({
                "fact_id": "fact-position",
                "kind": "position",
                "observed": {
                    "distance_m": _number(position_map.get("distance_m")),
                    "previous_distance_m": _number(position_map.get("previous_distance_m")),
                },
                "confidence": _bounded(position_map.get("confidence"), 1.0),
            })

        gaze = source.get("gaze")
        if gaze is not None:
            gaze_map = _mapping(gaze, "gaze")
            facts.append({
                "fact_id": "fact-gaze",
                "kind": "gaze",
                "observed": {
                    "target": str(gaze_map.get("target") or "unknown"),
                    "dwell_seconds": max(0.0, _number(gaze_map.get("dwell_seconds"))),
                    "away_seconds": max(0.0, _number(gaze_map.get("away_seconds"))),
                },
                "confidence": _bounded(gaze_map.get("confidence"), 1.0),
            })

        operations = source.get("operations", [])
        if not isinstance(operations, list):
            raise PerceptionContractError("operations must be an array")
        if operations:
            facts.append({
                "fact_id": "fact-operations",
                "kind": "operations",
                "observed": {"actions": [str(item).strip().lower() for item in operations if str(item).strip()]},
                "confidence": 1.0,
            })

        fact_by_kind = {fact["kind"]: fact for fact in facts}
        signals = self._signals(fact_by_kind)
        inferences = []
        for label in ("trouble", "interest", "disengagement"):
            signal = signals[label]
            present = signal.score >= 0.5
            inferences.append({
                "inference_id": f"inference-{label}",
                "label": label,
                "state": "present" if present else "not_observed",
                "confidence": signal.score if present else 1.0 - signal.score,
                "evidence_fact_ids": list(signal.fact_refs),
                "rule_version": RULE_VERSION,
                "rationale": signal.rationale,
            })

        present = [item for item in inferences if item["state"] == "present"]
        primary = max(present, key=lambda item: item["confidence"])["label"] if present else "none"
        primary_confidence = max((item["confidence"] for item in present), default=0.8)
        return {
            "schema_version": SCHEMA_VERSION,
            "facts": copy.deepcopy(facts),
            "inferences": inferences,
            "primary_state": primary,
            "primary_confidence": primary_confidence,
            "separation_enforced": True,
        }

    def _signals(self, facts: Mapping[str, Mapping[str, Any]]) -> dict[str, _Signal]:
        trouble = _Signal(0.0, (), "no trouble signal")
        interest = _Signal(0.0, (), "no interest signal")
        disengagement = _Signal(0.0, (), "no disengagement signal")

        speech = facts.get("speech")
        if speech:
            text = str(speech["observed"]["text"]).lower()
            confidence = float(speech["confidence"])
            if any(word in text for word in self.trouble_words):
                trouble = _Signal(0.95 * confidence, ("fact-speech",), "trouble phrase observed")
            if any(word in text for word in self.interest_words):
                interest = _Signal(max(interest.score, 0.78 * confidence), ("fact-speech",), "inquiry phrase observed")

        operations = facts.get("operations")
        if operations:
            actions = tuple(str(item) for item in operations["observed"]["actions"])
            if any(item in self.trouble_actions for item in actions):
                trouble = max(trouble, _Signal(0.85, ("fact-operations",), "failed/retry operation observed"), key=lambda item: item.score)
            if any(item in self.interest_actions for item in actions):
                interest = max(interest, _Signal(0.88, ("fact-operations",), "engagement operation observed"), key=lambda item: item.score)
            if any(item in self.disengagement_actions for item in actions):
                disengagement = _Signal(0.98, ("fact-operations",), "explicit leave operation observed")

        gaze = facts.get("gaze")
        if gaze:
            observed = gaze["observed"]
            confidence = float(gaze["confidence"])
            if float(observed["dwell_seconds"]) >= 2.0 and observed["target"] != "unknown":
                interest = max(interest, _Signal(0.9 * confidence, ("fact-gaze",), "sustained target gaze observed"), key=lambda item: item.score)
            if float(observed["away_seconds"]) >= 5.0:
                disengagement = max(disengagement, _Signal(0.82 * confidence, ("fact-gaze",), "sustained gaze-away observed"), key=lambda item: item.score)

        position = facts.get("position")
        if position:
            observed = position["observed"]
            delta = float(observed["distance_m"]) - float(observed["previous_distance_m"])
            if delta >= 1.5:
                refs = ("fact-position",)
                score = 0.72 * float(position["confidence"])
                if gaze and float(gaze["observed"]["away_seconds"]) >= 3.0:
                    refs = ("fact-position", "fact-gaze")
                    score = 0.92 * min(float(position["confidence"]), float(gaze["confidence"]))
                disengagement = max(disengagement, _Signal(score, refs, "moving away observed"), key=lambda item: item.score)

        return {"trouble": trouble, "interest": interest, "disengagement": disengagement}


def evaluate_perception_cases(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    adapter = PerceptionAdapter()
    confusion = {label: {candidate: 0 for candidate in LABELS} for label in LABELS}
    calibration: list[tuple[float, int]] = []
    count = 0
    for case in cases:
        expected = str(case.get("expected") or "")
        if expected not in LABELS:
            raise PerceptionContractError(f"unknown expected label: {expected}")
        result = adapter.infer(_mapping(case.get("observation"), "case.observation"))
        predicted = result["primary_state"]
        confusion[expected][predicted] += 1
        calibration.append((float(result["primary_confidence"]), int(predicted == expected)))
        count += 1
    if count == 0:
        raise PerceptionContractError("evaluation requires at least one case")

    f1_by_label: dict[str, float] = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[actual][label] for actual in LABELS if actual != label)
        fn = sum(confusion[label][candidate] for candidate in LABELS if candidate != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_by_label[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    ece = 0.0
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        upper = lower + 0.2
        bucket = [(confidence, correct) for confidence, correct in calibration if lower <= confidence <= upper if not (upper < 1.0 and confidence == upper)]
        if bucket:
            mean_confidence = sum(item[0] for item in bucket) / len(bucket)
            accuracy = sum(item[1] for item in bucket) / len(bucket)
            ece += len(bucket) / count * abs(mean_confidence - accuracy)

    return {
        "schema_version": "lumina.perception.evaluation.v1",
        "case_count": count,
        "confusion": confusion,
        "f1_by_label": f1_by_label,
        "macro_f1": sum(f1_by_label.values()) / len(LABELS),
        "expected_calibration_error": ece,
    }


__all__ = [
    "LABELS",
    "PerceptionAdapter",
    "PerceptionContractError",
    "SCHEMA_VERSION",
    "evaluate_perception_cases",
]
