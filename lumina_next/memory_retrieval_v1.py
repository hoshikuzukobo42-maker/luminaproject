"""User-scoped, provenance-first local memory retrieval."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "lumina.memory.retrieval.v1"
_TOKEN_RE = re.compile(r"[a-z0-9_]{2,}|[一-龥々〆ヵヶァ-ヴー]{2,}", re.IGNORECASE)


class MemoryRetrievalError(ValueError):
    pass


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryRetrievalError("invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise MemoryRetrievalError("timestamp timezone required")
    return parsed.astimezone(timezone.utc)


def _tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    return {item.casefold() for item in _TOKEN_RE.findall(text)}


def _bounded(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoryRetrievalError("confidence must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise MemoryRetrievalError("confidence must be in [0, 1]")
    return result


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    tenant_id: str
    user_id: str
    layer: str
    key: str
    value: str
    content: str
    keywords: tuple[str, ...]
    confidence: float
    provenance: Mapping[str, Any]
    status: str = "active"
    expires_at: str | None = None

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "MemoryRecord":
        if not isinstance(value, Mapping):
            raise MemoryRetrievalError("memory record must be an object")
        memory_id = str(value.get("memory_id") or "").strip()
        tenant_id = str(value.get("tenant_id") or "").strip()
        user_id = str(value.get("user_id") or "").strip()
        layer = str(value.get("layer") or "").strip()
        key = str(value.get("key") or "").strip().casefold()
        content = str(value.get("content") or "").strip()
        record_value = str(value.get("value") or content).strip()
        if not all((memory_id, tenant_id, user_id, layer, key, content, record_value)):
            raise MemoryRetrievalError("memory identity, scope, layer, key, value, and content are required")
        if layer not in {"episodic", "semantic", "profile"}:
            raise MemoryRetrievalError("unsupported retrieval layer")
        keywords = value.get("keywords", [])
        if not isinstance(keywords, list) or not all(str(item).strip() for item in keywords):
            raise MemoryRetrievalError("keywords must be a non-empty string array")
        provenance = value.get("provenance")
        if not isinstance(provenance, Mapping) or not provenance.get("source_id"):
            raise MemoryRetrievalError("provenance.source_id is required")
        _parse_time(value.get("expires_at"))
        return cls(
            memory_id=memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
            layer=layer,
            key=key,
            value=record_value,
            content=content,
            keywords=tuple(str(item).strip().casefold() for item in keywords),
            confidence=_bounded(value.get("confidence", 0.0)),
            provenance=copy.deepcopy(dict(provenance)),
            status=str(value.get("status") or "active"),
            expires_at=str(value["expires_at"]) if value.get("expires_at") else None,
        )


class MemoryRetrievalService:
    def __init__(self, records: Iterable[Mapping[str, Any] | MemoryRecord]) -> None:
        normalized = [item if isinstance(item, MemoryRecord) else MemoryRecord.from_value(item) for item in records]
        ids = [item.memory_id for item in normalized]
        if len(ids) != len(set(ids)):
            raise MemoryRetrievalError("duplicate memory_id")
        self.records = tuple(normalized)

    def retrieve(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        as_of: datetime,
        top_k: int = 5,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MemoryRetrievalError("as_of must be timezone-aware")
        if not str(tenant_id).strip() or not str(user_id).strip() or not str(query).strip():
            raise MemoryRetrievalError("tenant_id, user_id, and query are required")
        if not 1 <= int(top_k) <= 20:
            raise MemoryRetrievalError("top_k must be in [1, 20]")

        eligible = [
            item for item in self.records
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and item.status == "active"
            and item.confidence >= 0.5
            and (_parse_time(item.expires_at) is None or _parse_time(item.expires_at) > as_of.astimezone(timezone.utc))
        ]
        values_by_key: dict[str, set[str]] = {}
        for item in eligible:
            values_by_key.setdefault(item.key, set()).add(item.value.casefold())
        conflicted_keys = {key for key, values in values_by_key.items() if len(values) > 1}

        query_text = query.casefold()
        query_tokens = _tokens(query)
        ranked: list[tuple[float, MemoryRecord, list[str]]] = []
        for item in eligible:
            if item.key in conflicted_keys:
                continue
            matched = [keyword for keyword in item.keywords if keyword in query_text or keyword in query_tokens]
            token_overlap = query_tokens & (_tokens(item.content) | _tokens(item.value))
            if not matched and not token_overlap:
                continue
            layer_weight = {"profile": 1.0, "semantic": 0.95, "episodic": 0.85}[item.layer]
            score = round((2.0 * len(matched) + len(token_overlap)) * item.confidence * layer_weight, 6)
            ranked.append((score, item, matched or sorted(token_overlap)))
        ranked.sort(key=lambda entry: (-entry[0], entry[1].memory_id))

        results = []
        for score, item, matched in ranked[: int(top_k)]:
            results.append({
                "memory_id": item.memory_id,
                "layer": item.layer,
                "key": item.key,
                "content": item.content,
                "confidence": item.confidence,
                "score": score,
                "matched_terms": list(matched),
                "grounding": {
                    "memory_id": item.memory_id,
                    "source_id": item.provenance["source_id"],
                    "correlation_id": item.provenance.get("correlation_id"),
                },
            })
        return {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query": query,
            "results": results,
            "unsupported": len(results) == 0,
            "answer_policy": "grounded_results_only",
            "conflicted_keys_quarantined": sorted(conflicted_keys),
        }


def evaluate_retrieval_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    records = fixture.get("records")
    cases = fixture.get("cases")
    if not isinstance(records, list) or not isinstance(cases, list) or not cases:
        raise MemoryRetrievalError("fixture records and non-empty cases are required")
    service = MemoryRetrievalService(records)
    as_of = _parse_time(fixture.get("as_of"))
    if as_of is None:
        raise MemoryRetrievalError("fixture as_of is required")
    precisions: list[float] = []
    leakage = 0
    unsupported_correct = 0
    evaluated_positive = 0
    for case in cases:
        expected = set(case.get("expected_memory_ids") or [])
        result = service.retrieve(
            tenant_id=str(case.get("tenant_id")),
            user_id=str(case.get("user_id")),
            query=str(case.get("query")),
            as_of=as_of,
            top_k=5,
        )
        returned = [item["memory_id"] for item in result["results"]]
        leakage += sum(1 for item in result["results"] if item["memory_id"].startswith("other-user-"))
        if expected:
            evaluated_positive += 1
            precisions.append(len(expected.intersection(returned[:5])) / 5.0)
        else:
            unsupported_correct += int(result["unsupported"] is True and not returned)
    negative_count = sum(1 for case in cases if not case.get("expected_memory_ids"))
    return {
        "schema_version": "lumina.memory.retrieval.evaluation.v1",
        "case_count": len(cases),
        "positive_case_count": evaluated_positive,
        "precision_at_5": sum(precisions) / len(precisions) if precisions else 0.0,
        "cross_user_leakage_count": leakage,
        "unsupported_empty_accuracy": unsupported_correct / negative_count if negative_count else 1.0,
    }


__all__ = ["MemoryRecord", "MemoryRetrievalError", "MemoryRetrievalService", "evaluate_retrieval_fixture"]
