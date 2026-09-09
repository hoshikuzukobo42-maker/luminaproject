"""Fail-closed mechanical release-gate evaluation for Lumina.

This module never authorizes a release.  It only decides whether a complete,
internally consistent evidence pack is ready for an independent Gate review.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping


PACK_SCHEMA = "lumina.release.evidence-pack.v1"
STAGE_SCHEMA = "lumina.release.stage-evidence.v1"
DECISION_SCHEMA = "lumina.release.gate-decision.v1"
REQUIRED_STAGES = (
    "preflight",
    "closure",
    "acceptance",
    "soak",
    "stop",
    "residual_zero",
    "rollback",
)
MAX_EVIDENCE_AGE_SECONDS = 86_400
MAX_FUTURE_SKEW_SECONDS = 120
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def _sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _metric_errors(stage: str, metrics: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def number(name: str) -> float | None:
        value = metrics.get(name)
        if not _is_number(value):
            errors.append(f"{stage}:metric_invalid:{name}")
            return None
        return float(value)

    if stage == "preflight":
        total = number("checks_total")
        passed = number("checks_passed")
        false_positive = number("false_positive_count")
        if total is not None and total < 1:
            errors.append("preflight:checks_total_below_one")
        if total is not None and passed is not None and passed != total:
            errors.append("preflight:checks_not_all_passed")
        if false_positive is not None and false_positive != 0:
            errors.append("preflight:false_positive_nonzero")
    elif stage == "closure":
        completeness = number("trace_completeness")
        integrity = number("integrity_errors")
        parse = number("parse_errors")
        if completeness is not None and completeness < 0.98:
            errors.append("closure:trace_completeness_below_0_98")
        if integrity is not None and integrity != 0:
            errors.append("closure:integrity_errors_nonzero")
        if parse is not None and parse != 0:
            errors.append("closure:parse_errors_nonzero")
    elif stage == "acceptance":
        total = number("cases_total")
        passed = number("cases_passed")
        critical = number("critical_failures")
        if total is not None and total < 1:
            errors.append("acceptance:cases_total_below_one")
        if total is not None and passed is not None and passed != total:
            errors.append("acceptance:cases_not_all_passed")
        if critical is not None and critical != 0:
            errors.append("acceptance:critical_failures_nonzero")
    elif stage == "soak":
        duration = number("duration_seconds")
        crashes = number("crash_count")
        restarts = number("unexpected_restart_count")
        if duration is not None and duration < 1_800:
            errors.append("soak:duration_below_1800")
        if crashes is not None and crashes != 0:
            errors.append("soak:crash_count_nonzero")
        if restarts is not None and restarts != 0:
            errors.append("soak:unexpected_restart_nonzero")
    elif stage == "stop":
        owned = number("owned_processes_running")
        if metrics.get("stop_confirmed") is not True:
            errors.append("stop:not_confirmed")
        if owned is not None and owned != 0:
            errors.append("stop:owned_processes_running")
    elif stage == "residual_zero":
        for name in ("residual_processes", "residual_listeners", "residual_sessions"):
            value = number(name)
            if value is not None and value != 0:
                errors.append(f"residual_zero:{name}_nonzero")
    elif stage == "rollback":
        recovery = number("recovery_seconds")
        if metrics.get("rollback_verified") is not True:
            errors.append("rollback:not_verified")
        if recovery is not None and recovery > 300:
            errors.append("rollback:recovery_above_300")
        if not _SHA256.fullmatch(str(metrics.get("restore_sha256", ""))):
            errors.append("rollback:restore_sha256_invalid")
    return errors


def evaluate_release_evidence_pack(
    pack: Mapping[str, Any] | object,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate an evidence pack without granting a release or Gate PASS."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []
    stage_results: dict[str, dict[str, Any]] = {}

    if not isinstance(pack, Mapping):
        pack = {}
        errors.append("pack:not_object")

    if pack.get("schema_version") != PACK_SCHEMA:
        errors.append("pack:schema_version_invalid")
    candidate_id = pack.get("candidate_id")
    run_id = pack.get("run_id")
    candidate_sha256 = pack.get("candidate_sha256")
    if not isinstance(candidate_id, str) or not _IDENTIFIER.fullmatch(candidate_id):
        errors.append("pack:candidate_id_invalid")
    if not isinstance(run_id, str) or not _IDENTIFIER.fullmatch(run_id):
        errors.append("pack:run_id_invalid")
    if not isinstance(candidate_sha256, str) or not _SHA256.fullmatch(candidate_sha256):
        errors.append("pack:candidate_sha256_invalid")
    if pack.get("evidence_mode") != "live-local":
        errors.append("pack:evidence_mode_not_live_local")
    if pack.get("synthetic") is not False:
        errors.append("pack:synthetic_not_false")
    if pack.get("dotnet_or_mono_used") is not False:
        errors.append("pack:dotnet_or_mono_not_false")

    generated_at = _parse_utc(pack.get("generated_at"))
    if generated_at is None:
        errors.append("pack:generated_at_invalid")
    else:
        age = (checked_at - generated_at).total_seconds()
        if age > MAX_EVIDENCE_AGE_SECONDS:
            errors.append("pack:stale")
        if age < -MAX_FUTURE_SKEW_SECONDS:
            errors.append("pack:future_dated")

    stages = pack.get("stages")
    if not isinstance(stages, Mapping):
        stages = {}
        errors.append("pack:stages_not_object")
    unknown = sorted(set(stages) - set(REQUIRED_STAGES))
    if unknown:
        errors.append("pack:unknown_stages:" + ",".join(unknown))

    seen_evidence_hashes: set[str] = set()
    for stage in REQUIRED_STAGES:
        item = stages.get(stage)
        stage_errors: list[str] = []
        if not isinstance(item, Mapping):
            stage_errors.append(f"{stage}:missing")
            item = {}
        if item.get("schema_version") != STAGE_SCHEMA:
            stage_errors.append(f"{stage}:schema_version_invalid")
        if item.get("stage") != stage:
            stage_errors.append(f"{stage}:stage_name_mismatch")
        if item.get("status") != "PASS":
            stage_errors.append(f"{stage}:status_not_pass")
        if item.get("run_id") != run_id:
            stage_errors.append(f"{stage}:run_id_mismatch")
        if item.get("candidate_sha256") != candidate_sha256:
            stage_errors.append(f"{stage}:candidate_sha256_mismatch")
        if item.get("synthetic") is not False:
            stage_errors.append(f"{stage}:synthetic_not_false")
        if item.get("dotnet_or_mono_used") is not False:
            stage_errors.append(f"{stage}:dotnet_or_mono_not_false")
        source_path = item.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip() or source_path.startswith(("http://", "https://")):
            stage_errors.append(f"{stage}:source_path_invalid")
        evidence_sha256 = item.get("evidence_sha256")
        if not isinstance(evidence_sha256, str) or not _SHA256.fullmatch(evidence_sha256):
            stage_errors.append(f"{stage}:evidence_sha256_invalid")
        elif evidence_sha256 in seen_evidence_hashes:
            stage_errors.append(f"{stage}:duplicate_evidence_sha256")
        else:
            seen_evidence_hashes.add(evidence_sha256)

        started_at = _parse_utc(item.get("started_at"))
        finished_at = _parse_utc(item.get("finished_at"))
        if started_at is None or finished_at is None:
            stage_errors.append(f"{stage}:timestamp_invalid")
        else:
            if finished_at < started_at:
                stage_errors.append(f"{stage}:timestamp_reversed")
            if generated_at is not None and finished_at > generated_at:
                stage_errors.append(f"{stage}:finished_after_pack")
            age = (checked_at - finished_at).total_seconds()
            if age > MAX_EVIDENCE_AGE_SECONDS:
                stage_errors.append(f"{stage}:stale")
            if age < -MAX_FUTURE_SKEW_SECONDS:
                stage_errors.append(f"{stage}:future_dated")

        metrics = item.get("metrics")
        if not isinstance(metrics, Mapping):
            stage_errors.append(f"{stage}:metrics_not_object")
        else:
            stage_errors.extend(_metric_errors(stage, metrics))
        stage_results[stage] = {"pass": not stage_errors, "errors": sorted(set(stage_errors))}
        errors.extend(stage_errors)

    errors = sorted(set(errors))
    mechanically_ready = not errors
    decision_body = {
        "schema_version": DECISION_SCHEMA,
        "candidate_id": candidate_id,
        "run_id": run_id,
        "mechanical_status": "READY_FOR_INDEPENDENT_REVIEW" if mechanically_ready else "BLOCKED_FAIL_CLOSED",
        "ready_for_independent_review": mechanically_ready,
        "gate_pass": False,
        "release_permitted": False,
        "independent_review_required": True,
        "errors": errors,
        "stage_results": stage_results,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
    }
    decision_body["decision_sha256"] = _sha(decision_body)
    return decision_body


def build_reference_evidence_pack(*, generated_at: str = "2026-08-31T00:30:00Z") -> dict[str, Any]:
    """Build deterministic, side-effect-free reference input for tests/verifier."""
    candidate_sha = hashlib.sha256(b"lumina-release-candidate-v1").hexdigest()
    run_id = "release-run-20260831-0030"
    metrics = {
        "preflight": {"checks_total": 24, "checks_passed": 24, "false_positive_count": 0},
        "closure": {"trace_completeness": 1.0, "integrity_errors": 0, "parse_errors": 0},
        "acceptance": {"cases_total": 40, "cases_passed": 40, "critical_failures": 0},
        "soak": {"duration_seconds": 1_805, "crash_count": 0, "unexpected_restart_count": 0},
        "stop": {"stop_confirmed": True, "owned_processes_running": 0},
        "residual_zero": {"residual_processes": 0, "residual_listeners": 0, "residual_sessions": 0},
        "rollback": {"rollback_verified": True, "recovery_seconds": 12, "restore_sha256": hashlib.sha256(b"rollback-baseline").hexdigest()},
    }
    stages: dict[str, Any] = {}
    for index, stage in enumerate(REQUIRED_STAGES):
        stages[stage] = {
            "schema_version": STAGE_SCHEMA,
            "stage": stage,
            "run_id": run_id,
            "candidate_sha256": candidate_sha,
            "status": "PASS",
            "started_at": f"2026-08-31T00:{index * 3:02d}:00Z",
            "finished_at": f"2026-08-31T00:{index * 3 + 2:02d}:00Z",
            "evidence_sha256": hashlib.sha256(f"{stage}:evidence".encode()).hexdigest(),
            "source_path": f"reports/release-run/{stage}.json",
            "synthetic": False,
            "dotnet_or_mono_used": False,
            "metrics": metrics[stage],
        }
    return {
        "schema_version": PACK_SCHEMA,
        "candidate_id": "lumina-v051-release-candidate",
        "candidate_sha256": candidate_sha,
        "run_id": run_id,
        "generated_at": generated_at,
        "evidence_mode": "live-local",
        "synthetic": False,
        "dotnet_or_mono_used": False,
        "stages": stages,
    }


def clone_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy helper used by the offline verifier."""
    return copy.deepcopy(dict(pack))


__all__ = [
    "DECISION_SCHEMA",
    "PACK_SCHEMA",
    "REQUIRED_STAGES",
    "STAGE_SCHEMA",
    "build_reference_evidence_pack",
    "clone_pack",
    "evaluate_release_evidence_pack",
]
