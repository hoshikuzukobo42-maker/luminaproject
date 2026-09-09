"""llama.cpp / llama-server capability detection and profile generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_FLAG_RE = re.compile(r"(?:^|\s)(--[\w-]+)")
_VERSION_RE = re.compile(r"(?:version|llama\.cpp)\s+v?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class LlamaCapabilities:
    version: str | None
    flags: frozenset[str]
    raw_help: str

    def supports(self, flag: str) -> bool:
        token = flag if flag.startswith("--") else f"--{flag.lstrip('-')}"
        return token in self.flags

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "flags": sorted(self.flags),
            "metal": self.supports("--n-gpu-layers") or self.supports("--gpu-layers"),
            "flash_attention": self.supports("--flash-attn"),
            "kv_quantization": self.supports("--cache-type-k"),
            "multimodal": self.supports("--mmproj"),
            "reasoning": self.supports("--reasoning-budget"),
        }


def parse_llama_help(help_text: str | None) -> LlamaCapabilities:
    text = str(help_text or "")
    flags = frozenset(match.group(1) for match in _FLAG_RE.finditer(text))
    version_match = _VERSION_RE.search(text)
    version = version_match.group(1) if version_match else None
    return LlamaCapabilities(version=version, flags=flags, raw_help=text)


@dataclass(frozen=True)
class LlamaLaunchPlan:
    profile: str
    args: tuple[str, ...]
    unsupported_requested: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "args": list(self.args),
            "unsupported_requested": list(self.unsupported_requested),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class LaunchPlanRecord:
    """Serializable launch plan with routing metadata for diagnostics."""

    profile: str
    model_path: str
    host: str
    port: int
    args: tuple[str, ...]
    unsupported_requested: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    manifest_role: str | None = None

    @classmethod
    def from_launch_plan(
        cls,
        plan: LlamaLaunchPlan,
        *,
        model_path: str,
        host: str,
        port: int,
        manifest_role: str | None = None,
    ) -> "LaunchPlanRecord":
        return cls(
            profile=plan.profile,
            model_path=model_path,
            host=host,
            port=port,
            args=plan.args,
            unsupported_requested=plan.unsupported_requested,
            notes=plan.notes,
            manifest_role=manifest_role,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "model_path": self.model_path,
            "host": self.host,
            "port": self.port,
            "args": list(self.args),
            "unsupported_requested": list(self.unsupported_requested),
            "notes": list(self.notes),
            "manifest_role": self.manifest_role,
        }


_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "safe": {"ctx": 4096, "threads": 4, "gpu_layers": 0, "flash_attn": False},
    "balanced": {"ctx": 8192, "threads": 6, "gpu_layers": 999, "flash_attn": True},
    "quality": {"ctx": 8192, "threads": 8, "gpu_layers": 999, "flash_attn": True},
    "long-context": {"ctx": 16384, "threads": 6, "gpu_layers": 999, "flash_attn": True},
    "low-memory": {"ctx": 2048, "threads": 4, "gpu_layers": 999, "flash_attn": False},
    "vision": {"ctx": 4096, "threads": 6, "gpu_layers": 999, "flash_attn": True, "mmproj": True},
}


def build_launch_plan(
    *,
    profile: str,
    model_path: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    capabilities: LlamaCapabilities,
    overrides: Mapping[str, object] | None = None,
    mmproj_path: str | None = None,
) -> LlamaLaunchPlan:
    if profile not in _PROFILE_DEFAULTS:
        raise ValueError(f"unsupported profile: {profile}")

    cfg = dict(_PROFILE_DEFAULTS[profile])
    if overrides:
        cfg.update(overrides)

    args: list[str] = ["--model", model_path, "--host", host, "--port", str(port)]
    unsupported: list[str] = []
    notes: list[str] = []

    def add(flag: str, value: str | None = None) -> None:
        if capabilities.supports(flag):
            args.append(flag)
            if value is not None:
                args.append(value)
        else:
            unsupported.append(flag)

    add("--ctx-size", str(int(cfg["ctx"])))
    add("--threads", str(int(cfg["threads"])))
    if int(cfg.get("gpu_layers", 0)) > 0:
        add("--n-gpu-layers", str(int(cfg["gpu_layers"])))
    if cfg.get("flash_attn"):
        add("--flash-attn")
    if cfg.get("mmproj") and mmproj_path:
        add("--mmproj", mmproj_path)
    add("--jinja")
    add("--alias", str(cfg.get("alias", "local-model")))

    if profile == "low-memory":
        add("--cache-type-k", "q8_0")
        notes.append("low-memory profile prefers q8_0 KV when supported")

    return LlamaLaunchPlan(
        profile=profile,
        args=tuple(args),
        unsupported_requested=tuple(unsupported),
        notes=tuple(notes),
    )


def sanitize_unsupported_flags(
    requested: Sequence[str],
    capabilities: LlamaCapabilities,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kept: list[str] = []
    dropped: list[str] = []
    idx = 0
    while idx < len(requested):
        token = requested[idx]
        if token.startswith("--"):
            if capabilities.supports(token):
                kept.append(token)
                if idx + 1 < len(requested) and not requested[idx + 1].startswith("--"):
                    kept.append(requested[idx + 1])
                    idx += 2
                    continue
            else:
                dropped.append(token)
                if idx + 1 < len(requested) and not requested[idx + 1].startswith("--"):
                    dropped.append(requested[idx + 1])
                    idx += 2
                    continue
        else:
            kept.append(token)
        idx += 1
    return tuple(kept), tuple(dropped)


_ROLE_PROFILE_DEFAULTS: dict[str, str] = {
    "llm": "balanced",
    "vlm": "vision",
    "vlm_projector": "vision",
    "stt_model": "safe",
    "stt_archive": "safe",
    "tts": "safe",
}


def load_profile_manifest(path: Path | str) -> dict[str, Any]:
    """Load stack manifest and return role → recommended llama profile mapping."""

    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    roles: dict[str, str] = dict(_ROLE_PROFILE_DEFAULTS)
    for asset in payload.get("assets", ()):
        if not isinstance(asset, Mapping):
            continue
        role = str(asset.get("role", "")).strip()
        if not role:
            continue
        explicit = asset.get("launch_profile") or asset.get("llama_profile")
        if explicit:
            roles[role] = str(explicit)
        elif role not in roles:
            roles[role] = "balanced" if role == "llm" else "safe"
    return {
        "schema_version": payload.get("schema_version"),
        "manifest_path": str(manifest_path),
        "roles": roles,
    }


def recommended_profile_for_role(role: str, *, manifest: Mapping[str, Any] | None = None) -> str:
    """Resolve llama launch profile for a manifest asset role."""

    normalized = str(role).strip().lower()
    if manifest and "roles" in manifest:
        roles = manifest["roles"]
        if isinstance(roles, Mapping) and normalized in roles:
            return str(roles[normalized])
    return _ROLE_PROFILE_DEFAULTS.get(normalized, "balanced")


__all__ = [
    "LaunchPlanRecord",
    "LlamaCapabilities",
    "LlamaLaunchPlan",
    "build_launch_plan",
    "load_profile_manifest",
    "parse_llama_help",
    "recommended_profile_for_role",
    "sanitize_unsupported_flags",
]
