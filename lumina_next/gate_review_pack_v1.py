"""Fail-closed evidence packs for independent G2/G3/G4 review."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PACK_SCHEMA = "lumina.governance.gate-review-pack.v1"
SPEC_SCHEMA = "lumina.governance.gate-review-pack-spec.v1"


class GateReviewPackError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_gate_pack(
    gate_id: str,
    gate_spec: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    expected_gate = str(gate_spec.get("gate_id", ""))
    if gate_id != expected_gate or gate_id not in {"G2", "G3", "G4"}:
        raise GateReviewPackError("unknown gate")
    artifacts: list[dict[str, Any]] = []
    for raw in gate_spec.get("evidence", []):
        if not isinstance(raw, Mapping):
            raise GateReviewPackError("invalid evidence spec")
        relative = str(raw.get("path", ""))
        path = evidence_root / relative
        if not path.is_file():
            raise GateReviewPackError(f"missing evidence: {relative}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateReviewPackError(f"invalid evidence json: {relative}") from exc
        if payload.get("status") != "PASS":
            raise GateReviewPackError(f"evidence is not PASS: {relative}")
        if payload.get("gate_claim", "NONE") not in {"NONE", None}:
            raise GateReviewPackError(f"evidence overclaims gate: {relative}")
        artifacts.append(
            {
                "work_items": list(raw.get("work_items", [])),
                "path": relative,
                "sha256": file_sha256(path),
                "status": "PASS",
                "schema_version": str(payload.get("schema_version", "")),
            }
        )
    pack = {
        "schema_version": PACK_SCHEMA,
        "gate_id": gate_id,
        "review_status": "READY_FOR_INDEPENDENT_REVIEW",
        "gate_decision": "HOLD",
        "approved": False,
        "release_permitted": False,
        "self_approval_allowed": False,
        "required_review_roles": list(gate_spec.get("required_review_roles", [])),
        "approval_records": [],
        "artifacts": artifacts,
        "artifact_coverage_percent": 100.0,
        "known_blockers": list(gate_spec.get("known_blockers", [])),
        "network_used": False,
        "dotnet_or_mono_used": False,
    }
    pack["pack_sha256"] = _sha(pack)
    return pack


def verify_gate_pack(
    pack: Mapping[str, Any],
    gate_spec: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["schema"] = pack.get("schema_version") == PACK_SCHEMA
    checks["gate_binding"] = pack.get("gate_id") == gate_spec.get("gate_id")
    expected_hash = _sha({key: value for key, value in pack.items() if key != "pack_sha256"})
    checks["pack_seal"] = pack.get("pack_sha256") == expected_hash
    artifacts = pack.get("artifacts") if isinstance(pack.get("artifacts"), list) else []
    required = gate_spec.get("evidence") if isinstance(gate_spec.get("evidence"), list) else []
    checks["artifact_count"] = len(artifacts) == len(required) and len(artifacts) > 0
    hashes_ok = True
    paths_ok = True
    statuses_ok = True
    for artifact, expected in zip(artifacts, required, strict=False):
        if not isinstance(artifact, Mapping) or not isinstance(expected, Mapping):
            hashes_ok = paths_ok = statuses_ok = False
            continue
        relative = str(expected.get("path", ""))
        path = evidence_root / relative
        paths_ok = paths_ok and artifact.get("path") == relative and path.is_file()
        hashes_ok = hashes_ok and path.is_file() and artifact.get("sha256") == file_sha256(path)
        statuses_ok = statuses_ok and artifact.get("status") == "PASS"
    checks["artifact_paths"] = paths_ok
    checks["artifact_hashes"] = hashes_ok
    checks["artifact_statuses"] = statuses_ok
    checks["coverage_100_percent"] = pack.get("artifact_coverage_percent") == 100.0
    checks["review_boundary"] = (
        pack.get("review_status") == "READY_FOR_INDEPENDENT_REVIEW"
        and pack.get("gate_decision") == "HOLD"
        and pack.get("approved") is False
        and pack.get("release_permitted") is False
        and pack.get("self_approval_allowed") is False
        and pack.get("approval_records") == []
    )
    checks["review_roles"] = (
        pack.get("required_review_roles") == gate_spec.get("required_review_roles")
        and len(pack.get("required_review_roles", [])) >= 3
    )
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def reseal(pack: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(pack))
    copied.pop("pack_sha256", None)
    copied["pack_sha256"] = _sha(copied)
    return copied


__all__ = [
    "GateReviewPackError",
    "PACK_SCHEMA",
    "SPEC_SCHEMA",
    "build_gate_pack",
    "file_sha256",
    "reseal",
    "verify_gate_pack",
]
