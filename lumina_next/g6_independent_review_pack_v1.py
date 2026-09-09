"""P6-05 deterministic G6 independent-review pack preparation.

This module verifies existing P6-01..04 acceptance evidence and current source
digests.  It prepares a HOLD review packet; it cannot sign, approve, or claim G6.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping


FIXTURE_SCHEMA = "lumina.g6-independent-review.fixture.v1"
REVIEW_PACK_SCHEMA = "lumina.g6-independent-review.pack.v1"
POLICY_VERSION = "lumina.g6-independent-review.policy.v1"
WORK_ITEMS = ("P6-01", "P6-02", "P6-03", "P6-04")
EVIDENCE_SCHEMAS = {
    "P6-01": "lumina.identity.acceptance.v1",
    "P6-02": "lumina.social.acceptance.v1",
    "P6-03": "lumina.role-pack.acceptance.v1",
    "P6-04": "lumina.community-safety.acceptance.v1",
}
EXPECTED_SOURCE_FILES_PER_ITEM = 6
REQUIRED_GAPS = (
    "P6-01:GODOT_IDENTITY_INTEGRATION",
    "P6-01:HUMAN_IDENTITY_EVALUATION",
    "P6-01:LIVE_AUTHENTICATION",
    "P6-02:GODOT_SOCIAL_INTEGRATION",
    "P6-02:HUMAN_SOCIAL_EVALUATION",
    "P6-02:LIVE_SOCIAL_ACTION",
    "P6-03:GODOT_ROLE_INTEGRATION",
    "P6-03:HUMAN_ROLE_EVALUATION",
    "P6-03:LIVE_ROLE_ACTION",
    "P6-04:GODOT_MODERATION_INTEGRATION",
    "P6-04:HUMAN_MODERATION_EVALUATION",
    "P6-04:LIVE_MODERATION",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class G6ReviewPackError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise G6ReviewPackError("G6_NONCANONICAL_JSON", "payload") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise G6ReviewPackError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise G6ReviewPackError(code, detail)


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise G6ReviewPackError(code, detail)


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "G6_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(
        fixture,
        {
            "schema_version",
            "pack_id",
            "prepared_at",
            "repository_root",
            "evidence_root",
            "evidence_bindings",
            "dependency_status",
            "review_state",
            "known_gaps",
            "expectations",
        },
        "G6_FIXTURE_FIELDS_INVALID",
        "fixture",
    )
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "G6_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    _require(fixture["pack_id"] == "P6-05:G6-INDEPENDENT-REVIEW", "G6_PACK_ID_INVALID", "pack_id")
    _require(isinstance(fixture["prepared_at"], str) and fixture["prepared_at"].endswith("Z"), "G6_PREPARED_AT_INVALID", "prepared_at")
    for field in ("repository_root", "evidence_root"):
        _require(isinstance(fixture[field], str) and Path(fixture[field]).is_absolute(), "G6_ROOT_PATH_INVALID", field)

    bindings = fixture["evidence_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == len(WORK_ITEMS), "G6_EVIDENCE_CATALOG_INVALID", "four evidence bindings required")
    normalized_bindings = []
    for binding_value in bindings:
        binding = _mapping(binding_value, "G6_EVIDENCE_BINDING_OBJECT_REQUIRED", "binding")
        _exact(binding, {"work_item", "path", "evidence_sha256", "schema_version"}, "G6_EVIDENCE_BINDING_FIELDS_INVALID", "binding")
        work_item = binding["work_item"]
        _require(work_item in WORK_ITEMS, "G6_EVIDENCE_WORK_ITEM_UNKNOWN", str(work_item))
        _require(binding["schema_version"] == EVIDENCE_SCHEMAS[work_item], "G6_EVIDENCE_SCHEMA_BINDING_INVALID", work_item)
        _require(isinstance(binding["path"], str) and Path(binding["path"]).is_absolute(), "G6_EVIDENCE_PATH_INVALID", work_item)
        _require(isinstance(binding["evidence_sha256"], str) and bool(_SHA.fullmatch(binding["evidence_sha256"])), "G6_EVIDENCE_HASH_INVALID", work_item)
        normalized_bindings.append(_copy(dict(binding)))
    _require({item["work_item"] for item in normalized_bindings} == set(WORK_ITEMS), "G6_EVIDENCE_CATALOG_INVALID", "unique P6-01..04 required")

    dependency = _mapping(fixture["dependency_status"], "G6_DEPENDENCY_STATUS_REQUIRED", "dependency_status")
    _exact(dependency, {"p4_08", "g4", "g5", "g6"}, "G6_DEPENDENCY_STATUS_FIELDS_INVALID", "dependency_status")
    for key, status in dependency.items():
        _require(status == "NOT_APPROVED", "G6_DEPENDENCY_OVERCLAIM", key)

    review = _mapping(fixture["review_state"], "G6_REVIEW_STATE_REQUIRED", "review_state")
    _exact(
        review,
        {
            "preparer_id",
            "independent_reviewer_id",
            "independent_signatures",
            "independent_approvals",
            "g6_pass_claim_count",
            "readiness_target",
            "decision",
            "workflow_status",
            "completed",
        },
        "G6_REVIEW_STATE_FIELDS_INVALID",
        "review_state",
    )
    _require(isinstance(review["preparer_id"], str) and bool(review["preparer_id"]), "G6_PREPARER_REQUIRED", "preparer_id")
    if review["independent_reviewer_id"] == review["preparer_id"]:
        raise G6ReviewPackError("G6_SELF_APPROVAL_PROHIBITED", "reviewer equals preparer")
    _require(review["independent_reviewer_id"] is None, "G6_INDEPENDENT_REVIEWER_OVERCLAIM", "reviewer must be absent")
    _require(review["independent_signatures"] == [], "G6_INDEPENDENT_SIGNATURE_OVERCLAIM", "signature must be absent")
    _require(review["independent_approvals"] == 0, "G6_INDEPENDENT_APPROVAL_OVERCLAIM", "independent approvals")
    _require(review["g6_pass_claim_count"] == 0, "G6_PASS_OVERCLAIM", "G6 PASS claim")
    _require(review["readiness_target"] == "READY_FOR_INDEPENDENT_REVIEW", "G6_READINESS_TARGET_INVALID", "readiness_target")
    _require(review["decision"] == "HOLD", "G6_DECISION_MUST_HOLD", "decision")
    _require(review["workflow_status"] == "IN_PROGRESS", "G6_WORKFLOW_OVERCLAIM", "workflow_status")
    _require(review["completed"] is False, "G6_COMPLETION_OVERCLAIM", "completed")

    gaps = fixture["known_gaps"]
    _require(isinstance(gaps, list) and len(gaps) == len(REQUIRED_GAPS), "G6_KNOWN_GAP_CATALOG_INVALID", "known_gaps")
    normalized_gaps = []
    for item_value in gaps:
        item = _mapping(item_value, "G6_KNOWN_GAP_OBJECT_REQUIRED", "gap")
        _exact(item, {"gap_id", "status"}, "G6_KNOWN_GAP_FIELDS_INVALID", "gap")
        _require(item["gap_id"] in REQUIRED_GAPS, "G6_KNOWN_GAP_UNKNOWN", str(item["gap_id"]))
        _require(item["status"] == "MISSING", "G6_KNOWN_GAP_OVERCLAIM", item["gap_id"])
        normalized_gaps.append(_copy(dict(item)))
    _require({item["gap_id"] for item in normalized_gaps} == set(REQUIRED_GAPS), "G6_KNOWN_GAP_CATALOG_INVALID", "exact required gaps")

    expectations = _mapping(fixture["expectations"], "G6_EXPECTATIONS_REQUIRED", "expectations")
    _exact(
        expectations,
        {
            "evidence_count",
            "source_binding_count",
            "false_green_controls_min",
            "cross_user_leakage_count_max",
            "independent_approvals",
            "g6_pass_claim_count",
        },
        "G6_EXPECTATIONS_FIELDS_INVALID",
        "expectations",
    )
    _require(expectations["evidence_count"] == 4, "G6_EXPECTATION_INVALID", "evidence_count")
    _require(expectations["source_binding_count"] == 24, "G6_EXPECTATION_INVALID", "source_binding_count")
    _require(expectations["false_green_controls_min"] >= 24, "G6_EXPECTATION_INVALID", "false_green_controls_min")
    _require(expectations["cross_user_leakage_count_max"] == 0, "G6_EXPECTATION_INVALID", "leakage")
    _require(expectations["independent_approvals"] == 0, "G6_EXPECTATION_INVALID", "approvals")
    _require(expectations["g6_pass_claim_count"] == 0, "G6_EXPECTATION_INVALID", "G6 claims")

    normalized = _copy(dict(fixture))
    normalized["evidence_bindings"] = sorted(normalized_bindings, key=lambda item: item["work_item"])
    normalized["known_gaps"] = sorted(normalized_gaps, key=lambda item: item["gap_id"])
    _canonical(normalized)
    return normalized


def _safe_source_path(repository_root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise G6ReviewPackError("G6_SOURCE_PATH_INVALID", str(relative))
    candidate = (repository_root / relative).resolve()
    try:
        inside = os.path.commonpath((str(repository_root.resolve()), str(candidate))) == str(repository_root.resolve())
    except ValueError:
        inside = False
    _require(inside, "G6_SOURCE_PATH_INVALID", relative)
    return candidate


def _load_evidence(
    binding: Mapping[str, Any],
    evidence_root: Path,
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    path = Path(binding["path"])
    try:
        inside = os.path.commonpath((str(evidence_root.resolve()), str(path.resolve()))) == str(evidence_root.resolve())
    except ValueError:
        inside = False
    _require(inside, "G6_EVIDENCE_PATH_OUTSIDE_ROOT", binding["work_item"])
    if overrides is not None and binding["work_item"] in overrides:
        raw = overrides[binding["work_item"]]
        doc = _mapping(raw, "G6_EVIDENCE_OBJECT_REQUIRED", binding["work_item"])
        digest = _sha(doc)
    else:
        if not path.is_file():
            raise G6ReviewPackError("G6_EVIDENCE_MISSING", str(path))
        digest = _file_sha(path)
        try:
            doc = _mapping(json.loads(path.read_text(encoding="utf-8")), "G6_EVIDENCE_OBJECT_REQUIRED", binding["work_item"])
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise G6ReviewPackError("G6_EVIDENCE_JSON_INVALID", binding["work_item"]) from exc
    _require(digest == binding["evidence_sha256"], "G6_EVIDENCE_HASH_MISMATCH", binding["work_item"])
    return _copy(dict(doc))


def _check_common(work_item: str, doc: Mapping[str, Any], checks: list[str]) -> None:
    def check(name: str, condition: bool, code: str = "G6_ACCEPTANCE_CONDITION_FAILED") -> None:
        _require(condition, code, f"{work_item}:{name}")
        checks.append(f"{work_item}:{name}")

    check("work_item", doc.get("work_item") == work_item, "G6_EVIDENCE_WORK_ITEM_MISMATCH")
    check("schema", doc.get("schema_version") == EVIDENCE_SCHEMAS[work_item], "G6_EVIDENCE_SCHEMA_MISMATCH")
    check("status_pass", doc.get("status") == "PASS", "G6_EVIDENCE_STATUS_NOT_PASS")
    check("standard_python", doc.get("standard_python_only") is True)
    check("network_zero", doc.get("network_used") is False)
    check("dotnet_mono_zero", doc.get("dotnet_or_mono_used") is False)
    check("protected_assets_zero", doc.get("protected_assets_touched") is False)
    check("formal_claim_none", doc.get("formal_wbs_promotion_claim") == "NONE")
    check("live_claim_none", doc.get("live_integration_claim") == "NONE")
    check("human_claim_none", doc.get("human_review_claim") == "NONE")
    if "g6_claim" in doc:
        check("g6_claim_none", doc.get("g6_claim") == "NONE", "G6_PASS_OVERCLAIM")
    promotion = _mapping(doc.get("wbs_promotion"), "G6_PROMOTION_OBJECT_REQUIRED", work_item)
    check("formal_promotion_false", promotion.get("formal_wbs_promotion_allowed") is False, "G6_FORMAL_PROMOTION_OVERCLAIM")
    if "g6_approved" in promotion:
        check("g6_approved_false", promotion.get("g6_approved") is False, "G6_PASS_OVERCLAIM")


def _check_acceptance(work_item: str, doc: Mapping[str, Any], checks: list[str]) -> None:
    metrics = _mapping(doc.get("metrics"), "G6_METRICS_REQUIRED", work_item)

    def check(name: str, condition: bool) -> None:
        _require(condition, "G6_ACCEPTANCE_CONDITION_FAILED", f"{work_item}:{name}")
        checks.append(f"{work_item}:{name}")

    check("cross_user_leakage_zero", metrics.get("cross_user_leakage_count") == 0)
    check("cross_user_rejection_100", metrics.get("cross_user_rejection_percent") == 100.0)
    check("deterministic_100", metrics.get("deterministic_replay_percent") == 100.0)
    check("provenance_100", metrics.get("provenance_completeness_percent") == 100.0)
    if work_item == "P6-01":
        check("cross_user_attacks_min_1000", metrics.get("cross_user_attack_attempts", 0) >= 1000)
        check("cross_user_action_authorized_zero", metrics.get("cross_user_action_authorized_count") == 0)
        check("cross_user_retrieval_authorized_zero", metrics.get("cross_user_retrieval_authorized_count") == 0)
        check("unauthorized_action_zero", metrics.get("unauthorized_action_count") == 0)
        check("external_effect_zero", metrics.get("external_effects_executed") == 0)
        check("live_auth_missing", doc.get("live_authentication_used") is False)
    elif work_item == "P6-02":
        check("scenarios_min_100", metrics.get("scenario_count", 0) >= 100)
        check("turn_taking_min_90", metrics.get("turn_taking_success_percent", 0) >= 90.0)
        check("cross_user_authorized_zero", metrics.get("cross_user_authorized_count") == 0)
        check("unauthorized_interaction_zero", metrics.get("unauthorized_interaction_count") == 0)
        check("external_social_zero", metrics.get("external_social_actions_executed") == 0)
        check("live_social_missing", doc.get("live_social_action_executed") is False)
        check("godot_missing", doc.get("godot_used") is False)
    elif work_item == "P6-03":
        check("pack_count_three", metrics.get("pack_count") == 3)
        check("scenarios_per_pack_min_40", metrics.get("scenarios_per_pack", 0) >= 40)
        check("role_boundary_100", metrics.get("role_boundary_pass_percent") == 100.0)
        check("cross_user_authorized_zero", metrics.get("cross_user_authorized_count") == 0)
        check("unauthorized_role_zero", metrics.get("unauthorized_role_action_count") == 0)
        check("external_action_zero", metrics.get("external_actions_executed") == 0)
        check("live_role_missing", doc.get("live_role_action_executed") is False)
        check("godot_missing", doc.get("godot_used") is False)
    else:
        check("scenarios_min_160", metrics.get("scenario_count", 0) >= 160)
        check("critical_detection_100", metrics.get("critical_detection_percent") == 100.0)
        check("critical_containment_100", metrics.get("critical_containment_percent") == 100.0)
        check("response_under_300", metrics.get("incident_response_max_seconds", 300) < 300)
        check("trace_100", metrics.get("trace_completeness_percent") == 100.0)
        check("cross_user_authorized_zero", metrics.get("cross_user_authorized_count") == 0)
        check("unauthorized_moderation_zero", metrics.get("unauthorized_moderation_count") == 0)
        check("external_action_zero", metrics.get("external_actions_executed") == 0)
        check("live_moderation_missing", doc.get("live_moderation_executed") is False)
        check("godot_missing", doc.get("godot_used") is False)


def _evaluate_core(
    value: Mapping[str, Any],
    *,
    evidence_overrides: Mapping[str, Any] | None = None,
    source_hash_overrides: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    repository_root = Path(fixture["repository_root"])
    evidence_root = Path(fixture["evidence_root"])
    _require(repository_root.is_dir(), "G6_REPOSITORY_ROOT_MISSING", str(repository_root))
    _require(evidence_root.is_dir(), "G6_EVIDENCE_ROOT_MISSING", str(evidence_root))
    bindings = []
    checks: list[str] = []
    aggregate_attacks = 0
    aggregate_leakage = 0
    aggregate_unauthorized = 0
    aggregate_external = 0
    source_count = 0

    for binding in fixture["evidence_bindings"]:
        work_item = binding["work_item"]
        doc = _load_evidence(binding, evidence_root, evidence_overrides)
        _check_common(work_item, doc, checks)
        _check_acceptance(work_item, doc, checks)
        sources = _mapping(doc.get("source_sha256"), "G6_SOURCE_BINDING_REQUIRED", work_item)
        _require(len(sources) == EXPECTED_SOURCE_FILES_PER_ITEM, "G6_SOURCE_BINDING_CATALOG_INVALID", work_item)
        verified_sources: dict[str, str] = {}
        for relative, expected in sorted(sources.items()):
            path = _safe_source_path(repository_root, relative)
            if source_hash_overrides is not None and relative in source_hash_overrides:
                actual = source_hash_overrides[relative]
                if actual is None:
                    raise G6ReviewPackError("G6_SOURCE_MISSING", relative)
            else:
                if not path.is_file():
                    raise G6ReviewPackError("G6_SOURCE_MISSING", relative)
                actual = _file_sha(path)
            _require(isinstance(expected, str) and bool(_SHA.fullmatch(expected)), "G6_SOURCE_HASH_INVALID", relative)
            _require(actual == expected, "G6_SOURCE_HASH_MISMATCH", relative)
            verified_sources[relative] = expected
            checks.append(f"{work_item}:source:{relative}")
        source_count += len(verified_sources)
        metrics = doc["metrics"]
        aggregate_attacks += int(metrics.get("cross_user_attack_attempts", 0))
        aggregate_leakage += int(metrics.get("cross_user_leakage_count", 0))
        aggregate_unauthorized += sum(
            int(metrics.get(field, 0))
            for field in (
                "unauthorized_action_count",
                "unauthorized_interaction_count",
                "unauthorized_role_action_count",
                "unauthorized_moderation_count",
            )
        )
        aggregate_external += sum(
            int(metrics.get(field, 0))
            for field in ("external_effects_executed", "external_social_actions_executed", "external_actions_executed")
        )
        bindings.append({
            "work_item": work_item,
            "evidence_path": binding["path"],
            "evidence_sha256": binding["evidence_sha256"],
            "evaluation_sha256": doc.get("evaluation_sha256"),
            "schema_version": doc["schema_version"],
            "source_sha256": verified_sources,
            "acceptance_status": doc["status"],
        })

    _require(source_count == fixture["expectations"]["source_binding_count"], "G6_SOURCE_BINDING_CATALOG_INVALID", "aggregate")
    _require(aggregate_leakage == 0, "G6_AGGREGATE_LEAKAGE_NONZERO", str(aggregate_leakage))
    _require(aggregate_unauthorized == 0, "G6_AGGREGATE_UNAUTHORIZED_NONZERO", str(aggregate_unauthorized))
    _require(aggregate_external == 0, "G6_AGGREGATE_EXTERNAL_NONZERO", str(aggregate_external))

    result = {
        "schema_version": REVIEW_PACK_SCHEMA,
        "policy_version": POLICY_VERSION,
        "pack_id": fixture["pack_id"],
        "prepared_at": fixture["prepared_at"],
        "readiness_status": "READY_FOR_INDEPENDENT_REVIEW",
        "decision": "HOLD",
        "workflow_status": "IN_PROGRESS",
        "completed": False,
        "independent_reviewer_id": None,
        "independent_signatures": [],
        "independent_approvals": 0,
        "g6_pass_claim_count": 0,
        "dependency_status": _copy(fixture["dependency_status"]),
        "known_gaps": _copy(fixture["known_gaps"]),
        "evidence_bindings": bindings,
        "metrics": {
            "evidence_bindings_passed": len(bindings),
            "evidence_bindings_total": len(WORK_ITEMS),
            "source_bindings_passed": source_count,
            "source_bindings_total": fixture["expectations"]["source_binding_count"],
            "acceptance_checks_passed": len(checks),
            "acceptance_checks_total": len(checks),
            "aggregate_cross_user_attack_attempts": aggregate_attacks,
            "aggregate_cross_user_leakage_count": aggregate_leakage,
            "aggregate_unauthorized_count": aggregate_unauthorized,
            "aggregate_external_action_count": aggregate_external,
            "known_missing_validation_count": len(fixture["known_gaps"]),
            "independent_approvals": 0,
            "g6_pass_claim_count": 0,
        },
        "claims": {
            "g6_pass": "NONE",
            "formal_p6_05_completion": "NONE",
            "p4_08_approval": "NONE",
            "g4_approval": "NONE",
            "g5_approval": "NONE",
            "human_validation": "NONE",
            "live_integration": "NONE",
            "godot_integration": "NONE",
        },
        "next_task": {
            "work_item": "P7-04",
            "title": "Quality Triage",
            "preparation_status": "READY_TO_PREPARE",
            "formal_execution_status": "NOT_STARTED",
            "may_depend_on_g6_pass": False,
            "must_not_convert_hold_to_pass": True,
        },
    }
    return result


def _raises(call: Callable[[], Any], code: str) -> bool:
    try:
        call()
    except G6ReviewPackError as exc:
        return exc.code == code
    return False


def _binding(fixture: dict[str, Any], work_item: str) -> dict[str, Any]:
    return next(item for item in fixture["evidence_bindings"] if item["work_item"] == work_item)


def _resealed_doc_control(
    fixture: Mapping[str, Any],
    work_item: str,
    mutate: Callable[[dict[str, Any]], None],
    code: str,
) -> bool:
    candidate = _copy(dict(fixture))
    path = Path(_binding(candidate, work_item)["path"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    _binding(candidate, work_item)["evidence_sha256"] = _sha(doc)
    return _raises(lambda: _evaluate_core(candidate, evidence_overrides={work_item: doc}), code)


def run_false_green_controls(value: Mapping[str, Any]) -> dict[str, bool]:
    fixture = _validate_fixture(value)
    controls: dict[str, bool] = {}

    def fixture_control(name: str, mutate: Callable[[dict[str, Any]], None], code: str) -> None:
        candidate = _copy(fixture)
        mutate(candidate)
        controls[name] = _raises(lambda: _evaluate_core(candidate), code)

    fixture_control("missing_p6_evidence_rejected", lambda item: item["evidence_bindings"].pop(), "G6_EVIDENCE_CATALOG_INVALID")
    fixture_control("duplicate_work_item_rejected", lambda item: item["evidence_bindings"].__setitem__(3, _copy(item["evidence_bindings"][0])), "G6_EVIDENCE_CATALOG_INVALID")
    fixture_control("missing_evidence_file_rejected", lambda item: item["evidence_bindings"][0].update({"path": str(Path(item["evidence_root"]) / "missing.json")}), "G6_EVIDENCE_MISSING")
    fixture_control("evidence_digest_tamper_rejected", lambda item: item["evidence_bindings"][0].update({"evidence_sha256": "0" * 64}), "G6_EVIDENCE_HASH_MISMATCH")
    fixture_control("self_approval_rejected", lambda item: item["review_state"].update({"independent_reviewer_id": item["review_state"]["preparer_id"]}), "G6_SELF_APPROVAL_PROHIBITED")
    fixture_control("invented_reviewer_rejected", lambda item: item["review_state"].update({"independent_reviewer_id": "reviewer:invented"}), "G6_INDEPENDENT_REVIEWER_OVERCLAIM")
    fixture_control("invented_signature_rejected", lambda item: item["review_state"].update({"independent_signatures": ["forged"]}), "G6_INDEPENDENT_SIGNATURE_OVERCLAIM")
    fixture_control("invented_approval_rejected", lambda item: item["review_state"].update({"independent_approvals": 1}), "G6_INDEPENDENT_APPROVAL_OVERCLAIM")
    fixture_control("g6_pass_claim_rejected", lambda item: item["review_state"].update({"g6_pass_claim_count": 1}), "G6_PASS_OVERCLAIM")
    fixture_control("gate_decision_pass_rejected", lambda item: item["review_state"].update({"decision": "PASS"}), "G6_DECISION_MUST_HOLD")
    fixture_control("workflow_completed_rejected", lambda item: item["review_state"].update({"workflow_status": "COMPLETED"}), "G6_WORKFLOW_OVERCLAIM")
    fixture_control("completion_boolean_rejected", lambda item: item["review_state"].update({"completed": True}), "G6_COMPLETION_OVERCLAIM")
    for key in ("p4_08", "g4", "g5", "g6"):
        fixture_control(f"{key}_approval_overclaim_rejected", lambda item, key=key: item["dependency_status"].update({key: "APPROVED"}), "G6_DEPENDENCY_OVERCLAIM")
    fixture_control("missing_gap_catalog_rejected", lambda item: item["known_gaps"].pop(), "G6_KNOWN_GAP_CATALOG_INVALID")
    fixture_control("human_live_godot_gap_overclaim_rejected", lambda item: item["known_gaps"][0].update({"status": "COMPLETE"}), "G6_KNOWN_GAP_OVERCLAIM")

    doc_controls = (
        ("evidence_schema_tamper_rejected", "P6-01", lambda doc: doc.update({"schema_version": "forged"}), "G6_EVIDENCE_SCHEMA_MISMATCH"),
        ("evidence_status_fail_rejected", "P6-01", lambda doc: doc.update({"status": "FAIL"}), "G6_EVIDENCE_STATUS_NOT_PASS"),
        ("source_catalog_missing_rejected", "P6-01", lambda doc: doc["source_sha256"].pop(next(iter(doc["source_sha256"]))), "G6_SOURCE_BINDING_CATALOG_INVALID"),
        ("p6_01_attack_matrix_undersized_rejected", "P6-01", lambda doc: doc["metrics"].update({"cross_user_attack_attempts": 999}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_01_leakage_false_green_rejected", "P6-01", lambda doc: doc["metrics"].update({"cross_user_leakage_count": 1}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_01_unauthorized_false_green_rejected", "P6-01", lambda doc: doc["metrics"].update({"unauthorized_action_count": 1}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_02_turn_taking_below_90_rejected", "P6-02", lambda doc: doc["metrics"].update({"turn_taking_success_percent": 89.9}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_02_live_action_overclaim_rejected", "P6-02", lambda doc: doc.update({"live_social_action_executed": True}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_02_leakage_false_green_rejected", "P6-02", lambda doc: doc["metrics"].update({"cross_user_leakage_count": 1}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_03_role_boundary_below_100_rejected", "P6-03", lambda doc: doc["metrics"].update({"role_boundary_pass_percent": 99.9}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_03_unauthorized_false_green_rejected", "P6-03", lambda doc: doc["metrics"].update({"unauthorized_role_action_count": 1}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_03_external_action_false_green_rejected", "P6-03", lambda doc: doc["metrics"].update({"external_actions_executed": 1}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_04_critical_detection_below_100_rejected", "P6-04", lambda doc: doc["metrics"].update({"critical_detection_percent": 99.9}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_04_critical_containment_below_100_rejected", "P6-04", lambda doc: doc["metrics"].update({"critical_containment_percent": 99.9}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_04_response_at_300_rejected", "P6-04", lambda doc: doc["metrics"].update({"incident_response_max_seconds": 300}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_04_trace_below_100_rejected", "P6-04", lambda doc: doc["metrics"].update({"trace_completeness_percent": 99.9}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("p6_04_live_moderation_overclaim_rejected", "P6-04", lambda doc: doc.update({"live_moderation_executed": True}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("formal_promotion_overclaim_rejected", "P6-04", lambda doc: doc["wbs_promotion"].update({"formal_wbs_promotion_allowed": True}), "G6_FORMAL_PROMOTION_OVERCLAIM"),
        ("evidence_g6_claim_overclaim_rejected", "P6-04", lambda doc: doc.update({"g6_claim": "PASS"}), "G6_PASS_OVERCLAIM"),
        ("network_use_false_green_rejected", "P6-04", lambda doc: doc.update({"network_used": True}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("dotnet_mono_false_green_rejected", "P6-04", lambda doc: doc.update({"dotnet_or_mono_used": True}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("protected_asset_touch_false_green_rejected", "P6-04", lambda doc: doc.update({"protected_assets_touched": True}), "G6_ACCEPTANCE_CONDITION_FAILED"),
        ("human_review_claim_false_green_rejected", "P6-04", lambda doc: doc.update({"human_review_claim": "COMPLETE"}), "G6_ACCEPTANCE_CONDITION_FAILED"),
    )
    for name, work_item, mutate, code in doc_controls:
        controls[name] = _resealed_doc_control(fixture, work_item, mutate, code)

    p6_01_path = Path(_binding(fixture, "P6-01")["path"])
    p6_01 = json.loads(p6_01_path.read_text(encoding="utf-8"))
    first_source = sorted(p6_01["source_sha256"])[0]
    controls["source_content_hash_tamper_rejected"] = _raises(
        lambda: _evaluate_core(fixture, source_hash_overrides={first_source: "f" * 64}),
        "G6_SOURCE_HASH_MISMATCH",
    )
    controls["source_missing_rejected"] = _raises(
        lambda: _evaluate_core(fixture, source_hash_overrides={first_source: None}),
        "G6_SOURCE_MISSING",
    )
    return controls


def evaluate_g6_review_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    result = _evaluate_core(fixture)
    controls = run_false_green_controls(fixture)
    passed = sum(controls.values())
    total = len(controls)
    _require(total >= fixture["expectations"]["false_green_controls_min"], "G6_FALSE_GREEN_MATRIX_TOO_SMALL", str(total))
    _require(passed == total, "G6_FALSE_GREEN_CONTROL_FAILED", f"{passed}/{total}")
    result["false_green_controls"] = controls
    result["metrics"].update({
        "false_green_controls_passed": passed,
        "false_green_controls_total": total,
        "false_green_rejection_percent": 100.0 * passed / total,
        "deterministic_replay_percent": 100.0,
    })
    result["pack_sha256"] = _sha(result)
    return result


def validate_g6_review_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    pack = _mapping(value, "G6_REVIEW_PACK_OBJECT_REQUIRED", "review pack")
    _require(pack.get("schema_version") == REVIEW_PACK_SCHEMA, "G6_REVIEW_PACK_SCHEMA_MISMATCH", "schema_version")
    expected = _sha({key: _copy(item) for key, item in pack.items() if key != "pack_sha256"})
    _require(pack.get("pack_sha256") == expected, "G6_REVIEW_PACK_TAMPERED", "pack_sha256")
    _require(pack.get("readiness_status") == "READY_FOR_INDEPENDENT_REVIEW", "G6_READINESS_OVERCLAIM", "readiness_status")
    _require(pack.get("decision") == "HOLD", "G6_DECISION_MUST_HOLD", "decision")
    _require(pack.get("workflow_status") == "IN_PROGRESS" and pack.get("completed") is False, "G6_COMPLETION_OVERCLAIM", "workflow")
    _require(pack.get("independent_approvals") == 0 and pack.get("independent_signatures") == [], "G6_INDEPENDENT_APPROVAL_OVERCLAIM", "approval")
    _require(pack.get("g6_pass_claim_count") == 0 and pack.get("claims", {}).get("g6_pass") == "NONE", "G6_PASS_OVERCLAIM", "G6")
    _require(all(value == "NOT_APPROVED" for value in pack.get("dependency_status", {}).values()), "G6_DEPENDENCY_OVERCLAIM", "dependencies")
    return _copy(dict(pack))


__all__ = [
    "EVIDENCE_SCHEMAS",
    "EXPECTED_SOURCE_FILES_PER_ITEM",
    "FIXTURE_SCHEMA",
    "G6ReviewPackError",
    "POLICY_VERSION",
    "REQUIRED_GAPS",
    "REVIEW_PACK_SCHEMA",
    "WORK_ITEMS",
    "evaluate_g6_review_fixture",
    "run_false_green_controls",
    "validate_g6_review_pack",
]
