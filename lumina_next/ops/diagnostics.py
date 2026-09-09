"""Operational helpers for preintegration diagnostics and redacted bundles."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer\s+\S+|password\s*=|token\s*=)\S*"),
    re.compile(r"(?i)data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]{32,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)jwt\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)(?:secret|private[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]+?-----END"),
)

DIAGNOSTIC_BUNDLE_REQUIRED_KEYS = frozenset({"created_at", "root", "checks", "redacted_config"})
DIAGNOSTIC_CHECK_REQUIRED_KEYS = frozenset({"name", "ok"})


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    checks: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DiagnosticBundle:
    created_at: str
    root: str
    checks: tuple[dict[str, Any], ...]
    redacted_config: dict[str, Any]
    pytest_summary: dict[str, Any] | None = None


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def port_listening(host: str, port: int, *, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except OSError:
            return False
        return True


def check_live_ports_protected(ports: Sequence[int] = (11436, 8765)) -> dict[str, Any]:
    protected = {port: port_listening("127.0.0.1", port) for port in ports}
    return {"name": "live_ports_protected", "ok": True, "ports": protected}


def check_candidate_ports_free(ports: Sequence[int] = (11439, 11440, 5058, 8788, 8792)) -> dict[str, Any]:
    occupied = {port: port_listening("127.0.0.1", port) for port in ports}
    ok = not any(occupied.values())
    return {"name": "candidate_ports_free", "ok": ok, "occupied": occupied}


def run_preflight(root: Path | None = None) -> PreflightResult:
    root = root or Path(__file__).resolve().parents[1]
    checks = [
        check_live_ports_protected(),
        check_candidate_ports_free(),
        {
            "name": "manifest_present",
            "ok": (root / "data/model_manifests/preintegration_local_stack_20260725.json").exists(),
        },
        {
            "name": "python_modules_present",
            "ok": all((root / rel).exists() for rel in (
                "lumina_next/model_broker.py",
                "lumina_next/resource_governor.py",
                "lumina_next/preintegration_runtime.py",
            )),
        },
    ]
    return PreflightResult(ok=all(item["ok"] for item in checks), checks=tuple(checks))


def collect_redacted_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    allowed_prefixes = ("LUMINA_", "KOKORO_", "SHISA_", "QWEN_")
    payload: dict[str, Any] = {}
    for key, value in source.items():
        if not key.startswith(allowed_prefixes):
            continue
        payload[key] = _redact_text(str(value))
    return payload


def validate_diagnostic_bundle(payload: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Validate diagnostic bundle JSON shape before write or export."""

    errors: list[str] = []
    missing = DIAGNOSTIC_BUNDLE_REQUIRED_KEYS - set(payload.keys())
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        errors.append("checks must be a list")
    elif checks:
        for idx, item in enumerate(checks):
            if not isinstance(item, Mapping):
                errors.append(f"checks[{idx}] must be an object")
                continue
            item_missing = DIAGNOSTIC_CHECK_REQUIRED_KEYS - set(item.keys())
            if item_missing:
                errors.append(f"checks[{idx}] missing keys: {sorted(item_missing)}")
    config = payload.get("redacted_config")
    if config is not None and not isinstance(config, dict):
        errors.append("redacted_config must be an object")
    pytest_summary = payload.get("pytest_summary")
    if pytest_summary is not None and not isinstance(pytest_summary, Mapping):
        errors.append("pytest_summary must be an object when present")
    return len(errors) == 0, tuple(errors)


def build_diagnostic_bundle(
    root: Path | None = None,
    *,
    attach_pytest_pattern: str | None = None,
) -> DiagnosticBundle:
    root = root or Path(__file__).resolve().parents[1]
    preflight = run_preflight(root)
    pytest_summary = None
    if attach_pytest_pattern:
        pytest_summary = run_pytest_subset(root, attach_pytest_pattern)
    return DiagnosticBundle(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        root=str(root),
        checks=preflight.checks,
        redacted_config=collect_redacted_config(),
        pytest_summary=pytest_summary,
    )


def write_diagnostic_bundle(path: Path, bundle: DiagnosticBundle | None = None) -> Path:
    bundle = bundle or build_diagnostic_bundle(path.parent)
    payload = {
        "created_at": bundle.created_at,
        "root": bundle.root,
        "checks": list(bundle.checks),
        "redacted_config": bundle.redacted_config,
    }
    if bundle.pytest_summary is not None:
        payload["pytest_summary"] = bundle.pytest_summary
    ok, errors = validate_diagnostic_bundle(payload)
    if not ok:
        raise ValueError("; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_pytest_subset(root: Path, pattern: str) -> dict[str, Any]:
    cmd = ["./.venv/bin/python", "-m", "pytest", pattern, "-q", "--tb=no"]
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=False)
    return {
        "name": "pytest_subset",
        "pattern": pattern,
        "ok": proc.returncode == 0,
        "stdout_tail": _redact_text(proc.stdout[-2000:]),
        "stderr_tail": _redact_text(proc.stderr[-2000:]),
    }


__all__ = [
    "DIAGNOSTIC_BUNDLE_REQUIRED_KEYS",
    "DiagnosticBundle",
    "PreflightResult",
    "build_diagnostic_bundle",
    "collect_redacted_config",
    "run_preflight",
    "run_pytest_subset",
    "validate_diagnostic_bundle",
    "write_diagnostic_bundle",
]
