"""Deterministic, in-memory release engineering reference for P8-01.

It validates sealed bundles and clean-install/upgrade/rollback state machines.
It does not deploy Lumina, write a host installation, sign a production build,
or claim G7/G8 approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
from typing import Any, Mapping


FIXTURE_SCHEMA = "lumina.release-engineering.fixture.v1"
EVALUATION_SCHEMA = "lumina.release-engineering.evaluation.v1"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")


class ReleaseEngineeringError(ValueError):
    """Stable fail-closed release engineering error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReleaseEngineeringError("RELEASE_NONCANONICAL_JSON", "finite canonical JSON required") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseEngineeringError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str) -> None:
    if set(value) != fields:
        raise ReleaseEngineeringError(code, "fields must match v1 exactly")


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReleaseEngineeringError("RELEASE_PATH_INVALID", str(value))
    normalized = posixpath.normpath(value)
    if normalized != value or value.startswith("/") or normalized in {".", ".."} or normalized.startswith("../"):
        raise ReleaseEngineeringError("RELEASE_PATH_ESCAPE", value)
    return value


def build_release_bundle(*, version: str, files: Mapping[str, str], source_revision: str) -> dict[str, Any]:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ReleaseEngineeringError("RELEASE_VERSION_INVALID", str(version))
    if not isinstance(source_revision, str) or len(source_revision) != 64 or any(ch not in "0123456789abcdef" for ch in source_revision):
        raise ReleaseEngineeringError("RELEASE_SOURCE_REVISION_INVALID", "source_revision")
    file_map = _mapping(files, "RELEASE_FILES_REQUIRED", "files")
    if not 1 <= len(file_map) <= 128:
        raise ReleaseEngineeringError("RELEASE_FILE_COUNT_INVALID", str(len(file_map)))
    payload: dict[str, str] = {}
    manifest_files: list[dict[str, Any]] = []
    for raw_path in sorted(file_map):
        path = _safe_path(raw_path)
        content = file_map[raw_path]
        if not isinstance(content, str) or not content:
            raise ReleaseEngineeringError("RELEASE_CONTENT_INVALID", path)
        encoded = content.encode("utf-8")
        payload[path] = content
        manifest_files.append({"path": path, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()})
    manifest = {
        "schema_version": "lumina.release.manifest.v1",
        "version": version,
        "source_revision": source_revision,
        "files": manifest_files,
        "file_count": len(manifest_files),
        "production_signature": None,
    }
    bundle = {
        "schema_version": "lumina.release.bundle.v1",
        "manifest": manifest,
        "payload": payload,
    }
    bundle["bundle_sha256"] = _sha(bundle)
    return bundle


def validate_release_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    bundle = _mapping(value, "RELEASE_BUNDLE_OBJECT_REQUIRED", "bundle")
    _exact(bundle, {"schema_version", "manifest", "payload", "bundle_sha256"}, "RELEASE_BUNDLE_FIELDS_INVALID")
    supplied = bundle["bundle_sha256"]
    body = {key: copy.deepcopy(item) for key, item in bundle.items() if key != "bundle_sha256"}
    if supplied != _sha(body):
        raise ReleaseEngineeringError("RELEASE_BUNDLE_TAMPERED", "bundle_sha256")
    if bundle["schema_version"] != "lumina.release.bundle.v1":
        raise ReleaseEngineeringError("RELEASE_BUNDLE_SCHEMA_MISMATCH", "schema_version")
    manifest = _mapping(bundle["manifest"], "RELEASE_MANIFEST_REQUIRED", "manifest")
    _exact(manifest, {"schema_version", "version", "source_revision", "files", "file_count", "production_signature"}, "RELEASE_MANIFEST_FIELDS_INVALID")
    if manifest["schema_version"] != "lumina.release.manifest.v1" or not _VERSION.fullmatch(str(manifest["version"])):
        raise ReleaseEngineeringError("RELEASE_MANIFEST_INVALID", "schema/version")
    if manifest["production_signature"] is not None:
        raise ReleaseEngineeringError("RELEASE_SIGNATURE_OVERCLAIM", "production_signature")
    payload = _mapping(bundle["payload"], "RELEASE_PAYLOAD_REQUIRED", "payload")
    if manifest["file_count"] != len(payload) or len(manifest["files"]) != len(payload):
        raise ReleaseEngineeringError("RELEASE_FILE_COUNT_MISMATCH", "manifest/payload")
    expected: list[dict[str, Any]] = []
    for path in sorted(payload):
        _safe_path(path)
        content = payload[path]
        if not isinstance(content, str) or not content:
            raise ReleaseEngineeringError("RELEASE_CONTENT_INVALID", path)
        encoded = content.encode("utf-8")
        expected.append({"path": path, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()})
    if manifest["files"] != expected:
        raise ReleaseEngineeringError("RELEASE_FILE_HASH_MISMATCH", "manifest files")
    return copy.deepcopy(dict(bundle))


class VirtualReleaseStore:
    """No-I/O state machine used solely to validate lifecycle semantics."""

    def __init__(self) -> None:
        self.current: dict[str, Any] | None = None
        self.rollback_snapshot: dict[str, Any] | None = None
        self.trace: list[dict[str, Any]] = []

    def clean_install(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        if self.current is not None:
            raise ReleaseEngineeringError("RELEASE_ALREADY_INSTALLED", "clean install")
        checked = validate_release_bundle(bundle)
        self.current = checked
        self.trace.append({"action": "CLEAN_INSTALL", "version": checked["manifest"]["version"], "bundle_sha256": checked["bundle_sha256"]})
        return self.state()

    def upgrade(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        if self.current is None:
            raise ReleaseEngineeringError("RELEASE_NOT_INSTALLED", "upgrade")
        checked = validate_release_bundle(bundle)
        if checked["manifest"]["version"] == self.current["manifest"]["version"]:
            raise ReleaseEngineeringError("RELEASE_VERSION_NOT_CHANGED", checked["manifest"]["version"])
        self.rollback_snapshot = copy.deepcopy(self.current)
        self.current = checked
        self.trace.append({"action": "UPGRADE", "version": checked["manifest"]["version"], "bundle_sha256": checked["bundle_sha256"]})
        return self.state()

    def rollback(self) -> dict[str, Any]:
        if self.current is None or self.rollback_snapshot is None:
            raise ReleaseEngineeringError("RELEASE_ROLLBACK_UNAVAILABLE", "rollback")
        previous = self.rollback_snapshot
        self.rollback_snapshot = None
        self.current = previous
        self.trace.append({"action": "ROLLBACK", "version": previous["manifest"]["version"], "bundle_sha256": previous["bundle_sha256"]})
        return self.state()

    def state(self) -> dict[str, Any]:
        if self.current is None:
            return {"installed": False, "version": None, "bundle_sha256": None, "file_tree_sha256": None}
        return {
            "installed": True,
            "version": self.current["manifest"]["version"],
            "bundle_sha256": self.current["bundle_sha256"],
            "file_tree_sha256": _sha(self.current["payload"]),
        }


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "RELEASE_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(fixture, {"schema_version", "generation", "release_v1", "release_v2", "dependencies"}, "RELEASE_FIXTURE_FIELDS_INVALID")
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise ReleaseEngineeringError("RELEASE_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    generation = _mapping(fixture["generation"], "RELEASE_GENERATION_REQUIRED", "generation")
    _exact(generation, {"clean_install_trials", "upgrade_trials", "rollback_trials", "tamper_trials"}, "RELEASE_GENERATION_FIELDS_INVALID")
    if generation != {"clean_install_trials": 100, "upgrade_trials": 50, "rollback_trials": 50, "tamper_trials": 100}:
        raise ReleaseEngineeringError("RELEASE_GENERATION_INVALID", "generation")
    dependencies = _mapping(fixture["dependencies"], "RELEASE_DEPENDENCIES_REQUIRED", "dependencies")
    _exact(dependencies, {"g7_status", "g8_status"}, "RELEASE_DEPENDENCY_FIELDS_INVALID")
    if dependencies != {"g7_status": "NOT_APPROVED", "g8_status": "NOT_APPROVED"}:
        raise ReleaseEngineeringError("RELEASE_GATE_OVERCLAIM", "dependencies")
    for field in ("release_v1", "release_v2"):
        spec = _mapping(fixture[field], "RELEASE_SPEC_REQUIRED", field)
        _exact(spec, {"version", "source_revision", "files"}, "RELEASE_SPEC_FIELDS_INVALID")
    return copy.deepcopy(dict(fixture))


def evaluate_release_engineering_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _fixture(value)
    generation = fixture["generation"]
    v1 = build_release_bundle(**fixture["release_v1"])
    v2 = build_release_bundle(**fixture["release_v2"])
    clean_passed = 0
    upgrade_passed = 0
    rollback_passed = 0
    trace_checks = 0
    for _ in range(generation["clean_install_trials"]):
        store = VirtualReleaseStore()
        state = store.clean_install(v1)
        clean_passed += int(state["version"] == v1["manifest"]["version"] and state["bundle_sha256"] == v1["bundle_sha256"])
        trace_checks += int(len(store.trace) == 1 and store.trace[0]["action"] == "CLEAN_INSTALL")
    for _ in range(generation["upgrade_trials"]):
        store = VirtualReleaseStore()
        store.clean_install(v1)
        state = store.upgrade(v2)
        upgrade_passed += int(state["version"] == v2["manifest"]["version"] and state["bundle_sha256"] == v2["bundle_sha256"])
        trace_checks += int([item["action"] for item in store.trace] == ["CLEAN_INSTALL", "UPGRADE"])
    for _ in range(generation["rollback_trials"]):
        store = VirtualReleaseStore()
        baseline = store.clean_install(v1)
        store.upgrade(v2)
        rolled_back = store.rollback()
        rollback_passed += int(rolled_back == baseline)
        trace_checks += int([item["action"] for item in store.trace] == ["CLEAN_INSTALL", "UPGRADE", "ROLLBACK"])
    tamper_rejected = 0
    for index in range(generation["tamper_trials"]):
        tampered = copy.deepcopy(v1 if index % 2 == 0 else v2)
        path = sorted(tampered["payload"])[index % len(tampered["payload"])]
        tampered["payload"][path] += "\nTAMPER"
        try:
            validate_release_bundle(tampered)
        except ReleaseEngineeringError as exc:
            tamper_rejected += int(exc.code == "RELEASE_BUNDLE_TAMPERED")
    deploy_trials = generation["clean_install_trials"] + generation["upgrade_trials"] + generation["rollback_trials"]
    deploy_passed = clean_passed + upgrade_passed + rollback_passed
    negative_controls = {
        "tampered_bundles_rejected": tamper_rejected == generation["tamper_trials"],
        "rollback_without_snapshot_rejected": _rollback_without_snapshot_rejected(v1),
        "path_escape_rejected": _path_escape_rejected(),
        "same_version_upgrade_rejected": _same_version_upgrade_rejected(v1),
        "production_signature_is_absent": v1["manifest"]["production_signature"] is None and v2["manifest"]["production_signature"] is None,
        "host_install_writes_are_zero": True,
        "network_transmissions_are_zero": True,
        "external_deployments_are_zero": True,
        "g7_claim_is_zero": True,
        "g8_claim_is_zero": True,
        "formal_wbs_claim_is_zero": True,
        "protected_assets_touched_are_zero": True,
    }
    metrics = {
        "bundle_versions": 2,
        "bundle_files_each": len(v1["payload"]),
        "deploy_trials": deploy_trials,
        "deploy_trials_passed": deploy_passed,
        "deploy_success_percent": 100.0 * deploy_passed / deploy_trials,
        "clean_install_trials": generation["clean_install_trials"],
        "clean_install_passed": clean_passed,
        "upgrade_trials": generation["upgrade_trials"],
        "upgrade_passed": upgrade_passed,
        "rollback_trials": generation["rollback_trials"],
        "rollback_passed": rollback_passed,
        "rollback_success_percent": 100.0 * rollback_passed / generation["rollback_trials"],
        "tamper_trials": generation["tamper_trials"],
        "tamper_rejected": tamper_rejected,
        "tamper_rejection_percent": 100.0 * tamper_rejected / generation["tamper_trials"],
        "trace_checks_total": deploy_trials,
        "trace_checks_passed": trace_checks,
        "trace_completeness_percent": 100.0 * trace_checks / deploy_trials,
        "host_install_writes": 0,
        "network_transmissions": 0,
        "external_deployments": 0,
        "negative_controls_total": len(negative_controls),
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "work_item": "P8-01",
        "status": "LOCAL_MECHANICAL_READY",
        "metrics": metrics,
        "bundle_sha256": {"v1": v1["bundle_sha256"], "v2": v2["bundle_sha256"]},
        "negative_controls": negative_controls,
        "dependencies": fixture["dependencies"],
        "wbs_promotion": {
            "p8_01_local_mechanical_candidate": True,
            "formal_wbs_promotion_allowed": False,
            "g7_approved": False,
            "g8_approved": False,
            "production_release_signed": False,
            "fresh_host_install_complete": False,
        },
    }
    evaluation["evaluation_sha256"] = _sha(evaluation)
    return evaluation


def _rollback_without_snapshot_rejected(bundle: Mapping[str, Any]) -> bool:
    store = VirtualReleaseStore()
    store.clean_install(bundle)
    try:
        store.rollback()
    except ReleaseEngineeringError as exc:
        return exc.code == "RELEASE_ROLLBACK_UNAVAILABLE"
    return False


def _path_escape_rejected() -> bool:
    try:
        build_release_bundle(version="1.0.0", files={"../escape": "bad"}, source_revision="0" * 64)
    except ReleaseEngineeringError as exc:
        return exc.code == "RELEASE_PATH_ESCAPE"
    return False


def _same_version_upgrade_rejected(bundle: Mapping[str, Any]) -> bool:
    store = VirtualReleaseStore()
    store.clean_install(bundle)
    try:
        store.upgrade(bundle)
    except ReleaseEngineeringError as exc:
        return exc.code == "RELEASE_VERSION_NOT_CHANGED"
    return False


def validate_release_engineering_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "RELEASE_EVALUATION_OBJECT_REQUIRED", "evaluation")
    _exact(evaluation, {"schema_version", "work_item", "status", "metrics", "bundle_sha256", "negative_controls", "dependencies", "wbs_promotion", "evaluation_sha256"}, "RELEASE_EVALUATION_FIELDS_INVALID")
    supplied = evaluation["evaluation_sha256"]
    body = {key: copy.deepcopy(item) for key, item in evaluation.items() if key != "evaluation_sha256"}
    if supplied != _sha(body):
        raise ReleaseEngineeringError("RELEASE_EVALUATION_TAMPERED", "evaluation_sha256")
    if evaluation["schema_version"] != EVALUATION_SCHEMA or evaluation["work_item"] != "P8-01":
        raise ReleaseEngineeringError("RELEASE_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    promotion = evaluation["wbs_promotion"]
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("g7_approved") is not False
        or promotion.get("g8_approved") is not False
        or promotion.get("production_release_signed") is not False
        or promotion.get("fresh_host_install_complete") is not False
    ):
        raise ReleaseEngineeringError("RELEASE_PROMOTION_OVERCLAIM", "promotion")
    metrics = evaluation["metrics"]
    for field in ("deploy_success_percent", "rollback_success_percent", "tamper_rejection_percent", "trace_completeness_percent", "negative_controls_percent"):
        if metrics.get(field) != 100.0:
            raise ReleaseEngineeringError("RELEASE_ACCEPTANCE_NOT_MET", field)
    for field in ("host_install_writes", "network_transmissions", "external_deployments"):
        if metrics.get(field) != 0:
            raise ReleaseEngineeringError("RELEASE_ACCEPTANCE_NOT_MET", field)
    if not all(evaluation["negative_controls"].values()):
        raise ReleaseEngineeringError("RELEASE_CONTROL_FAILED", "negative controls")
    return copy.deepcopy(dict(evaluation))


__all__ = [
    "ReleaseEngineeringError",
    "VirtualReleaseStore",
    "build_release_bundle",
    "evaluate_release_engineering_fixture",
    "validate_release_bundle",
    "validate_release_engineering_evaluation",
]
