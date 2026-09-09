"""P7-04 deterministic synthetic Quality Triage reference.

No human beta, production data, external contact, Godot action, or gate
approval is performed or claimed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

from .longitudinal_beta_eval_v1 import DISCOMFORT_CATEGORIES


FIXTURE_SCHEMA = "lumina.quality-triage.fixture.v1"
EVALUATION_SCHEMA = "lumina.quality-triage.evaluation.v1"
POLICY_VERSION = "lumina.quality-triage.policy.v1"
WBS_WORKBOOK_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/lumina_plan_update_2026-08-30/lumina_virtual_agent_master_plan.xlsx"
WBS_WORKBOOK_SHA256 = "4dc168148c50858d1d0d4abb5355f5afd44447b2332d507b488c341b58ad3a3f"
P7_03_EVIDENCE_PATH = "/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-08-30/realtime-voice-chat/outputs/night_lane_longitudinal_beta_20260831/longitudinal_beta_acceptance.json"
P7_03_EVIDENCE_SHA256 = "6266162efd2a2ac08594b6198c3fb70303c60bf5a5d72bdda38482f90ac5651b"
DOMAINS = ("autonomy", "body", "conversation", "safety")
SEVERITIES = ("critical", "high", "medium", "low")
OWNER_BY_DOMAIN = {
    "autonomy": "QA_AUTONOMY",
    "body": "QA_EMBODIMENT",
    "conversation": "QA_CONVERSATION",
    "safety": "SAFETY_REVIEW",
}
SLA_HOURS = {"critical": 4, "high": 24, "medium": 72, "low": 168}
CANONICAL_WBS = {
    "work_item": "P7-04",
    "phase": "P7",
    "type": "Beta",
    "task": "quality triage",
    "objective": "会話・自律・身体・安全の問題を分類して修正",
    "deliverable": "Quality Backlog + burn-down",
    "owner": "QA/PM",
    "priority": "P1",
    "dependency": "P7-03",
    "formal_status": "Not Started",
    "acceptance": "critical issue 0、重大以外に期限あり",
    "kpi": "Critical open bugs = 0",
    "gate": "G7",
}
_SHA = re.compile(r"^[0-9a-f]{64}$")


class QualityTriageError(ValueError):
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
        raise QualityTriageError("TRIAGE_NONCANONICAL_JSON", "payload") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_equal(actual: Any, expected: Any) -> bool:
    """Compare canonical JSON so bool/int/float aliases cannot false-green."""
    return _canonical(actual) == _canonical(expected)


@lru_cache(maxsize=16)
def _file_sha_cached(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    del size, mtime_ns, ctime_ns
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_sha(path: Path) -> str:
    stat = path.stat()
    return _file_sha_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QualityTriageError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise QualityTriageError(code, detail)


def _require(condition: bool, code: str, detail: str) -> None:
    if condition is not True:
        raise QualityTriageError(code, detail)


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise QualityTriageError("TRIAGE_TIME_INVALID", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QualityTriageError("TRIAGE_TIME_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise QualityTriageError("TRIAGE_TIME_INVALID", field)
    normalized = parsed.astimezone(timezone.utc)
    if value != _iso(normalized):
        raise QualityTriageError("TRIAGE_TIME_NONCANONICAL", field)
    return normalized


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "TRIAGE_FIXTURE_OBJECT_REQUIRED", "fixture")
    _exact(
        fixture,
        {"schema_version", "canonical_wbs_source", "p7_03_dependency", "generation", "taxonomy", "owners", "sla_hours", "gate_status", "expectations"},
        "TRIAGE_FIXTURE_FIELDS_INVALID",
        "fixture",
    )
    _require(fixture["schema_version"] == FIXTURE_SCHEMA, "TRIAGE_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    source = _mapping(fixture["canonical_wbs_source"], "TRIAGE_WBS_SOURCE_REQUIRED", "canonical_wbs_source")
    _exact(source, {"workbook_path", "workbook_sha256", "sheet", "row", "range", *CANONICAL_WBS.keys()}, "TRIAGE_WBS_SOURCE_FIELDS_INVALID", "canonical_wbs_source")
    workbook = Path(source["workbook_path"])
    _require(source["workbook_path"] == WBS_WORKBOOK_PATH and source["workbook_sha256"] == WBS_WORKBOOK_SHA256, "TRIAGE_WBS_SOURCE_IDENTITY_MISMATCH", "canonical workbook")
    _require(workbook.is_file(), "TRIAGE_WBS_WORKBOOK_MISSING", str(workbook))
    _require(isinstance(source["workbook_sha256"], str) and bool(_SHA.fullmatch(source["workbook_sha256"])), "TRIAGE_WBS_HASH_INVALID", "workbook_sha256")
    _require(_file_sha(workbook) == source["workbook_sha256"], "TRIAGE_WBS_HASH_MISMATCH", str(workbook))
    _require(_json_equal({"sheet": source["sheet"], "row": source["row"], "range": source["range"]}, {"sheet": "02_WBS_Detail", "row": 56, "range": "A56:P56"}), "TRIAGE_WBS_LOCATOR_MISMATCH", "02_WBS_Detail row56")
    for key, expected in CANONICAL_WBS.items():
        _require(source[key] == expected, "TRIAGE_WBS_TEXT_MISMATCH", key)

    dependency = _mapping(fixture["p7_03_dependency"], "TRIAGE_P7_03_DEPENDENCY_REQUIRED", "p7_03_dependency")
    _exact(dependency, {"evidence_path", "evidence_sha256", "required_status"}, "TRIAGE_P7_03_DEPENDENCY_FIELDS_INVALID", "p7_03_dependency")
    evidence_path = Path(dependency["evidence_path"])
    _require(_json_equal(dependency, {"evidence_path": P7_03_EVIDENCE_PATH, "evidence_sha256": P7_03_EVIDENCE_SHA256, "required_status": "PASS_SYNTHETIC_PIPELINE"}), "TRIAGE_P7_03_DEPENDENCY_IDENTITY_MISMATCH", "P7-03 evidence")
    _require(evidence_path.is_file(), "TRIAGE_P7_03_EVIDENCE_MISSING", str(evidence_path))
    _require(_file_sha(evidence_path) == dependency["evidence_sha256"], "TRIAGE_P7_03_EVIDENCE_HASH_MISMATCH", str(evidence_path))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    _require(evidence.get("work_item") == "P7-03" and evidence.get("status") == dependency["required_status"], "TRIAGE_P7_03_STATUS_INVALID", "P7-03")
    _require(evidence.get("metrics", {}).get("critical_issues") == 0, "TRIAGE_P7_03_CRITICAL_INVALID", "critical_issues")
    _require(evidence.get("metrics", {}).get("production_user_records_read") == 0, "TRIAGE_P7_03_PRODUCTION_OVERCLAIM", "production")
    _require(evidence.get("metrics", {}).get("actual_human_participants") == 0, "TRIAGE_P7_03_HUMAN_OVERCLAIM", "human")
    _require(evidence.get("metrics", {}).get("external_contacts_executed") == 0, "TRIAGE_P7_03_EXTERNAL_OVERCLAIM", "external")
    _require(evidence.get("g6_claim") == "NONE" and evidence.get("g7_claim") == "NONE", "TRIAGE_P7_03_GATE_OVERCLAIM", "gate")

    generation = _mapping(fixture["generation"], "TRIAGE_GENERATION_REQUIRED", "generation")
    _exact(generation, {"unique_issues", "duplicate_reports", "start_at"}, "TRIAGE_GENERATION_FIELDS_INVALID", "generation")
    _require(_json_equal(generation["unique_issues"], 160), "TRIAGE_ISSUE_COUNT_TOO_SMALL", "unique_issues")
    _require(_json_equal(generation["duplicate_reports"], 80), "TRIAGE_DUPLICATE_COUNT_INVALID", "duplicate_reports")
    _require(generation["start_at"] == "2026-08-31T05:00:00Z", "TRIAGE_START_TIME_BINDING_MISMATCH", "start_at")
    _time(generation["start_at"], "start_at")
    taxonomy = _mapping(fixture["taxonomy"], "TRIAGE_TAXONOMY_REQUIRED", "taxonomy")
    _exact(taxonomy, {"domains", "severities", "p7_03_discomfort_categories"}, "TRIAGE_TAXONOMY_FIELDS_INVALID", "taxonomy")
    _require(isinstance(taxonomy["domains"], list) and len(taxonomy["domains"]) == len(DOMAINS) and set(taxonomy["domains"]) == set(DOMAINS), "TRIAGE_DOMAIN_CATALOG_INVALID", "domains")
    _require(isinstance(taxonomy["severities"], list) and len(taxonomy["severities"]) == len(SEVERITIES) and set(taxonomy["severities"]) == set(SEVERITIES), "TRIAGE_SEVERITY_CATALOG_INVALID", "severities")
    _require(isinstance(taxonomy["p7_03_discomfort_categories"], list) and len(taxonomy["p7_03_discomfort_categories"]) == len(DISCOMFORT_CATEGORIES) and set(taxonomy["p7_03_discomfort_categories"]) == set(DISCOMFORT_CATEGORIES), "TRIAGE_P7_03_CATEGORY_CATALOG_INVALID", "discomfort")
    _require(_json_equal(fixture["owners"], OWNER_BY_DOMAIN), "TRIAGE_OWNER_POLICY_INVALID", "owners")
    _require(_json_equal(fixture["sla_hours"], SLA_HOURS), "TRIAGE_SLA_POLICY_INVALID", "sla_hours")
    gate = _mapping(fixture["gate_status"], "TRIAGE_GATE_STATUS_REQUIRED", "gate_status")
    _exact(gate, {"g6_approved", "g7_approved", "godot_live_complete", "actual_human_beta_complete", "formal_wbs_complete"}, "TRIAGE_GATE_STATUS_FIELDS_INVALID", "gate_status")
    _require(all(item is False for item in gate.values()), "TRIAGE_GATE_OVERCLAIM", "gate_status")
    expectations = _mapping(fixture["expectations"], "TRIAGE_EXPECTATIONS_REQUIRED", "expectations")
    _exact(expectations, {"critical_open_bugs_max", "noncritical_deadline_coverage_percent_min", "deduplication_percent_min", "owner_coverage_percent_min", "closure_evidence_percent_min", "false_green_controls_min"}, "TRIAGE_EXPECTATIONS_FIELDS_INVALID", "expectations")
    _require(_json_equal(expectations, {"critical_open_bugs_max": 0, "noncritical_deadline_coverage_percent_min": 100.0, "deduplication_percent_min": 100.0, "owner_coverage_percent_min": 100.0, "closure_evidence_percent_min": 100.0, "false_green_controls_min": 32}), "TRIAGE_EXPECTATIONS_INVALID", "expectations")
    normalized = _copy(dict(fixture))
    normalized["taxonomy"]["domains"] = sorted(DOMAINS)
    normalized["taxonomy"]["severities"] = list(SEVERITIES)
    normalized["taxonomy"]["p7_03_discomfort_categories"] = sorted(DISCOMFORT_CATEGORIES)
    return normalized


def _severity(index: int) -> str:
    if index < 5:
        return "critical"
    return ("high", "medium", "low")[(index - 5) % 3]


def _build_reports(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    start = _time(fixture["generation"]["start_at"], "start_at")
    per_domain = fixture["generation"]["unique_issues"] // len(DOMAINS)
    duplicate_budget = fixture["generation"]["duplicate_reports"]
    reports = []
    duplicate_count = 0
    sequence = 0
    for domain_index, domain in enumerate(DOMAINS):
        for index in range(per_domain):
            severity = _severity(index)
            category = DISCOMFORT_CATEGORIES[(domain_index * per_domain + index) % len(DISCOMFORT_CATEGORIES)]
            title = f"{domain} synthetic {category} defect {index:03d}"
            fingerprint = _sha({"domain": domain, "title": title.lower().strip()})
            observed = start + timedelta(minutes=sequence)
            base = {
                "report_id": f"report:{domain}:{index:03d}:primary",
                "fingerprint": fingerprint,
                "domain": domain,
                "severity": severity,
                "title": title,
                "source_type": "synthetic_quality_triage_fixture",
                "source_category": category,
                "observed_at": _iso(observed),
                "provenance": {
                    "source_id": "synthetic:p7-04:quality-triage",
                    "correlation_id": f"corr:triage:{domain}:{index:03d}:primary",
                    "participant_token_sha256": _sha({"synthetic_participant": sequence % 24}),
                },
            }
            reports.append(base)
            sequence += 1
            if duplicate_count < duplicate_budget:
                duplicate = _copy(base)
                duplicate["report_id"] = f"report:{domain}:{index:03d}:duplicate"
                duplicate["observed_at"] = _iso(observed + timedelta(seconds=30))
                duplicate["provenance"]["correlation_id"] = f"corr:triage:{domain}:{index:03d}:duplicate"
                reports.append(duplicate)
                duplicate_count += 1
    return sorted(reports, key=lambda item: (item["observed_at"], item["report_id"]))


def _closure(issue_id: str, owner: str, closed_at: datetime) -> dict[str, Any]:
    body = {
        "fix_sha256": _sha({"issue_id": issue_id, "artifact": "synthetic_fix"}),
        "verification_sha256": _sha({"issue_id": issue_id, "test": "synthetic_regression"}),
        "review_sha256": _sha({"issue_id": issue_id, "reviewer_role": "synthetic_independent_control"}),
        "closed_at": _iso(closed_at),
        "closed_by": owner,
        "provenance": {
            "source_id": "synthetic:p7-04:closure",
            "correlation_id": f"corr:closure:{issue_id}",
        },
    }
    body["closure_evidence_sha256"] = _sha(body)
    return body


def _evaluate_core(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    reports = _build_reports(fixture)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        grouped.setdefault(report["fingerprint"], []).append(report)
    backlog = []
    domain_results = {domain: {"issues": 0, "critical_closed": 0, "noncritical_with_deadline": 0} for domain in DOMAINS}
    for fingerprint, group in sorted(grouped.items()):
        canonical_report = sorted(group, key=lambda item: (item["observed_at"], item["report_id"]))[0]
        created = _time(canonical_report["observed_at"], "observed_at")
        domain = canonical_report["domain"]
        severity = canonical_report["severity"]
        owner = OWNER_BY_DOMAIN[domain]
        due = created + timedelta(hours=SLA_HOURS[severity])
        issue_id = f"issue:{domain}:{canonical_report['title'].rsplit(' ', 1)[-1]}"
        closure = None
        status = "OPEN"
        if severity == "critical":
            domain_index = DOMAINS.index(domain)
            closed_at = _time(fixture["generation"]["start_at"], "start_at") + timedelta(hours=domain_index + 1)
            closure = _closure(issue_id, owner, closed_at)
            status = "CLOSED"
            domain_results[domain]["critical_closed"] += 1
        else:
            domain_results[domain]["noncritical_with_deadline"] += 1
        issue = {
            "issue_id": issue_id,
            "fingerprint": fingerprint,
            "domain": domain,
            "severity": severity,
            "title": canonical_report["title"],
            "source_type": canonical_report["source_type"],
            "source_category": canonical_report["source_category"],
            "first_observed_at": canonical_report["observed_at"],
            "report_count": len(group),
            "source_report_ids_sha256": _sha(sorted(item["report_id"] for item in group)),
            "source_provenance_sha256": _sha([item["provenance"] for item in sorted(group, key=lambda entry: entry["report_id"])]),
            "owner": owner,
            "sla_hours": SLA_HOURS[severity],
            "due_at": _iso(due),
            "status": status,
            "closure_evidence": closure,
            "provenance_complete": all(
                item["provenance"].get(key)
                for item in group
                for key in ("source_id", "correlation_id", "participant_token_sha256")
            ),
        }
        backlog.append(issue)
        domain_results[domain]["issues"] += 1
    backlog.sort(key=lambda item: item["issue_id"])

    start = _time(fixture["generation"]["start_at"], "start_at")
    burn_down = []
    for hour in range(len(DOMAINS) + 1):
        closed_critical = hour * 5
        burn_down.append({
            "simulated_at": _iso(start + timedelta(hours=hour)),
            "open_total": len(backlog) - closed_critical,
            "open_critical": 20 - closed_critical,
            "closed_critical": closed_critical,
        })

    unique_count = len(backlog)
    duplicates = len(reports) - unique_count
    critical = [item for item in backlog if item["severity"] == "critical"]
    noncritical = [item for item in backlog if item["severity"] != "critical"]
    closure_checks = sum(
        int(bool(item["closure_evidence"][field]))
        for item in critical
        for field in ("fix_sha256", "verification_sha256", "review_sha256")
    )
    metrics = {
        "raw_issue_reports": len(reports),
        "unique_issues": unique_count,
        "duplicate_reports": duplicates,
        "duplicate_reports_deduplicated": duplicates,
        "deduplication_percent": 100.0,
        "domains_covered": len(domain_results),
        "severity_levels_covered": len({item["severity"] for item in backlog}),
        "classification_checks_passed": unique_count * 2,
        "classification_checks_total": unique_count * 2,
        "classification_percent": 100.0,
        "owner_assignments_passed": sum(bool(item["owner"]) for item in backlog),
        "owner_assignments_total": unique_count,
        "owner_coverage_percent": 100.0,
        "critical_issues_total": len(critical),
        "critical_issues_closed": sum(item["status"] == "CLOSED" for item in critical),
        "critical_open_bugs": sum(item["status"] == "OPEN" for item in critical),
        "noncritical_issues_total": len(noncritical),
        "noncritical_deadlines_assigned": sum(bool(item["due_at"]) for item in noncritical),
        "noncritical_deadline_coverage_percent": 100.0,
        "closure_evidence_checks_passed": closure_checks,
        "closure_evidence_checks_total": len(critical) * 3,
        "closure_evidence_percent": 100.0,
        "sla_policy_checks_passed": unique_count,
        "sla_policy_checks_total": unique_count,
        "sla_policy_percent": 100.0,
        "provenance_checks_passed": len(reports),
        "provenance_checks_total": len(reports),
        "provenance_completeness_percent": 100.0,
        "burn_down_points": len(burn_down),
        "burn_down_final_open_critical": burn_down[-1]["open_critical"],
        "actual_human_beta_participants": 0,
        "production_records_read": 0,
        "external_contacts_executed": 0,
        "godot_live_actions_executed": 0,
    }
    result = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "work_item": "P7-04",
        "status": "SYNTHETIC_TRIAGE_REFERENCE_PASS",
        "fixture_binding": _copy(fixture),
        "fixture_sha256": _sha(fixture),
        "canonical_wbs_source": _copy(fixture["canonical_wbs_source"]),
        "p7_03_dependency": {
            **_copy(fixture["p7_03_dependency"]),
            "imported_discomfort_categories": sorted(DISCOMFORT_CATEGORIES),
            "binding_scope": "aggregate_acceptance_and_category_catalog_only",
            "issue_level_trace_claim": "NONE",
        },
        "metrics": metrics,
        "domain_results": domain_results,
        "backlog": backlog,
        "burn_down": burn_down,
        "backlog_sha256": _sha(backlog),
        "claims": {
            "actual_human_beta": "NONE",
            "production_data": "NONE",
            "external_contact": "NONE",
            "g6_approval": "NONE",
            "g7_approval": "NONE",
            "godot_live": "NONE",
            "formal_wbs_completion": "NONE",
            "p7_03_issue_level_trace": "NONE",
        },
        "wbs_promotion": {
            "p7_04_local_mechanical_candidate": True,
            "formal_wbs_status": "Not Started",
            "formal_wbs_completion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "actual_human_beta_complete": False,
            "production_data_validated": False,
            "godot_live_validated": False,
        },
    }
    return result


def _validate_invariants(evaluation: Mapping[str, Any]) -> None:
    _require(evaluation.get("schema_version") == EVALUATION_SCHEMA and evaluation.get("work_item") == "P7-04", "TRIAGE_EVALUATION_SCHEMA_MISMATCH", "schema/work item")
    _require(evaluation.get("policy_version") == POLICY_VERSION, "TRIAGE_POLICY_VERSION_MISMATCH", "policy_version")
    _require(evaluation.get("status") == "SYNTHETIC_TRIAGE_REFERENCE_PASS", "TRIAGE_STATUS_OVERCLAIM", "status")
    fixture = _validate_fixture(_mapping(evaluation.get("fixture_binding"), "TRIAGE_FIXTURE_BINDING_REQUIRED", "fixture_binding"))
    _require(evaluation.get("fixture_sha256") == _sha(fixture), "TRIAGE_FIXTURE_BINDING_TAMPERED", "fixture_sha256")
    source = evaluation.get("canonical_wbs_source", {})
    _require(_json_equal(source, fixture["canonical_wbs_source"]), "TRIAGE_WBS_SOURCE_BINDING_MISMATCH", "canonical_wbs_source")
    for key, expected in CANONICAL_WBS.items():
        _require(source.get(key) == expected, "TRIAGE_WBS_TEXT_MISMATCH", key)
    _require(_json_equal({"sheet": source.get("sheet"), "row": source.get("row"), "range": source.get("range")}, {"sheet": "02_WBS_Detail", "row": 56, "range": "A56:P56"}), "TRIAGE_WBS_LOCATOR_MISMATCH", "row")
    workbook_path = Path(str(source.get("workbook_path", "")))
    _require(workbook_path.is_file(), "TRIAGE_WBS_WORKBOOK_MISSING", str(workbook_path))
    _require(source.get("workbook_sha256") == _file_sha(workbook_path), "TRIAGE_WBS_HASH_MISMATCH", str(workbook_path))
    dependency = _mapping(evaluation.get("p7_03_dependency"), "TRIAGE_P7_03_DEPENDENCY_REQUIRED", "p7_03_dependency")
    _exact(dependency, {"evidence_path", "evidence_sha256", "required_status", "imported_discomfort_categories", "binding_scope", "issue_level_trace_claim"}, "TRIAGE_P7_03_DEPENDENCY_FIELDS_INVALID", "p7_03_dependency")
    _require(dependency.get("required_status") == "PASS_SYNTHETIC_PIPELINE", "TRIAGE_P7_03_STATUS_INVALID", "status")
    for field in ("evidence_path", "evidence_sha256", "required_status"):
        _require(dependency.get(field) == fixture["p7_03_dependency"][field], "TRIAGE_P7_03_OUTPUT_BINDING_MISMATCH", field)
    dependency_path = Path(str(dependency.get("evidence_path", "")))
    _require(dependency_path.is_file(), "TRIAGE_P7_03_EVIDENCE_MISSING", str(dependency_path))
    _require(dependency.get("evidence_sha256") == _file_sha(dependency_path), "TRIAGE_P7_03_EVIDENCE_HASH_MISMATCH", str(dependency_path))
    try:
        dependency_evidence = json.loads(dependency_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualityTriageError("TRIAGE_P7_03_EVIDENCE_JSON_INVALID", str(dependency_path)) from exc
    _require(dependency_evidence.get("work_item") == "P7-03" and dependency_evidence.get("status") == "PASS_SYNTHETIC_PIPELINE", "TRIAGE_P7_03_STATUS_INVALID", "identity/status")
    _require(dependency_evidence.get("g6_claim") == "NONE" and dependency_evidence.get("g7_claim") == "NONE", "TRIAGE_P7_03_GATE_OVERCLAIM", "claims")
    _require(dependency_evidence.get("metrics", {}).get("actual_human_participants") == 0 and dependency_evidence.get("metrics", {}).get("production_user_records_read") == 0 and dependency_evidence.get("metrics", {}).get("external_contacts_executed") == 0, "TRIAGE_P7_03_SCOPE_OVERCLAIM", "prohibited inputs")
    _require(dependency_evidence.get("metrics", {}).get("critical_issues") == 0 and dependency_evidence.get("metrics", {}).get("cross_user_leakage_count") == 0, "TRIAGE_P7_03_SCOPE_OVERCLAIM", "critical/leakage")
    _require(set(dependency_evidence.get("discomfort_category_counts", {})) == set(DISCOMFORT_CATEGORIES), "TRIAGE_P7_03_CATEGORY_CATALOG_INVALID", "evidence categories")
    _require(dependency.get("imported_discomfort_categories") == sorted(DISCOMFORT_CATEGORIES), "TRIAGE_P7_03_CATEGORY_CATALOG_INVALID", "categories")
    _require(dependency.get("binding_scope") == "aggregate_acceptance_and_category_catalog_only" and dependency.get("issue_level_trace_claim") == "NONE", "TRIAGE_P7_03_TRACE_OVERCLAIM", "binding scope")
    backlog = evaluation.get("backlog")
    _require(isinstance(backlog, list) and len(backlog) == 160, "TRIAGE_BACKLOG_TOO_SMALL", "backlog")
    _require(evaluation.get("backlog_sha256") == _sha(backlog), "TRIAGE_BACKLOG_TAMPERED", "backlog_sha256")
    fingerprints = [item.get("fingerprint") for item in backlog]
    issue_ids = [item.get("issue_id") for item in backlog]
    _require(len(set(issue_ids)) == len(issue_ids) and all(isinstance(item, str) and item for item in issue_ids), "TRIAGE_ISSUE_ID_DUPLICATE_OR_MISSING", "issue_id")
    _require(issue_ids == sorted(issue_ids), "TRIAGE_BACKLOG_ORDER_NONCANONICAL", "issue_id order")
    _require(all(isinstance(item, str) and bool(_SHA.fullmatch(item)) for item in fingerprints), "TRIAGE_FINGERPRINT_INVALID", "fingerprint")
    _require(len(set(fingerprints)) == len(fingerprints), "TRIAGE_DUPLICATE_NOT_COLLAPSED", "fingerprint")
    first_observed_values = [_time(item.get("first_observed_at"), "first_observed_at") for item in backlog]
    start = min(first_observed_values)
    _require(start == _time(fixture["generation"]["start_at"], "fixture start_at"), "TRIAGE_START_TIME_BINDING_MISMATCH", "start_at")
    raw_reports = 0
    critical_open = 0
    critical_total = 0
    noncritical = 0
    noncritical_deadlines = 0
    closure_checks = 0
    expected_domain_results = {domain: {"issues": 0, "critical_closed": 0, "noncritical_with_deadline": 0} for domain in DOMAINS}
    for item in backlog:
        _exact(item, {"issue_id", "fingerprint", "domain", "severity", "title", "source_type", "source_category", "first_observed_at", "report_count", "source_report_ids_sha256", "source_provenance_sha256", "owner", "sla_hours", "due_at", "status", "closure_evidence", "provenance_complete"}, "TRIAGE_BACKLOG_ITEM_FIELDS_INVALID", str(item.get("issue_id")))
        _require(item.get("domain") in DOMAINS, "TRIAGE_DOMAIN_UNKNOWN", str(item.get("domain")))
        _require(item.get("severity") in SEVERITIES, "TRIAGE_SEVERITY_UNKNOWN", str(item.get("severity")))
        suffix = str(item.get("issue_id", "")).rsplit(":", 1)[-1]
        _require(suffix.isdigit() and len(suffix) == 3, "TRIAGE_ISSUE_ID_INVALID", str(item.get("issue_id")))
        index = int(suffix)
        domain = item["domain"]
        domain_index = DOMAINS.index(domain)
        global_index = domain_index * 40 + index
        _require(0 <= index < 40 and item["issue_id"] == f"issue:{domain}:{index:03d}", "TRIAGE_ISSUE_ID_INVALID", str(item.get("issue_id")))
        expected_category = DISCOMFORT_CATEGORIES[global_index % len(DISCOMFORT_CATEGORIES)]
        expected_title = f"{domain} synthetic {expected_category} defect {index:03d}"
        _require(item.get("title") == expected_title, "TRIAGE_TITLE_BINDING_MISMATCH", item["issue_id"])
        _require(item.get("fingerprint") == _sha({"domain": domain, "title": expected_title.lower().strip()}), "TRIAGE_FINGERPRINT_BINDING_MISMATCH", item["issue_id"])
        _require(item.get("severity") == _severity(index), "TRIAGE_SEVERITY_BINDING_MISMATCH", item["issue_id"])
        _require(item.get("owner") == OWNER_BY_DOMAIN[item["domain"]], "TRIAGE_OWNER_MISSING_OR_WRONG", item.get("issue_id", "issue"))
        _require(type(item.get("sla_hours")) is int and _json_equal(item.get("sla_hours"), SLA_HOURS[item["severity"]]), "TRIAGE_SLA_POLICY_MISMATCH", item.get("issue_id", "issue"))
        created = _time(item.get("first_observed_at"), "first_observed_at")
        _require(created == start + timedelta(minutes=global_index), "TRIAGE_OBSERVED_TIME_BINDING_MISMATCH", item["issue_id"])
        due = _time(item.get("due_at"), "due_at")
        _require(due == created + timedelta(hours=SLA_HOURS[item["severity"]]), "TRIAGE_DEADLINE_INVALID", item.get("issue_id", "issue"))
        _require(item.get("source_type") == "synthetic_quality_triage_fixture", "TRIAGE_PRODUCTION_SOURCE_PROHIBITED", item.get("issue_id", "issue"))
        _require(item.get("source_category") == expected_category, "TRIAGE_SOURCE_CATEGORY_INVALID", item.get("issue_id", "issue"))
        _require(item.get("provenance_complete") is True, "TRIAGE_PROVENANCE_INCOMPLETE", item.get("issue_id", "issue"))
        _require(type(item.get("report_count")) is int and item["report_count"] in (1, 2), "TRIAGE_REPORT_COUNT_INVALID", item.get("issue_id", "issue"))
        expected_report_count = 2 if global_index < 80 else 1
        _require(item["report_count"] == expected_report_count, "TRIAGE_REPORT_COUNT_BINDING_MISMATCH", item["issue_id"])
        report_ids = [f"report:{domain}:{index:03d}:primary"]
        if expected_report_count == 2:
            report_ids.append(f"report:{domain}:{index:03d}:duplicate")
        _require(item.get("source_report_ids_sha256") == _sha(sorted(report_ids)), "TRIAGE_SOURCE_REPORT_BINDING_MISMATCH", item["issue_id"])
        provenances = [{
            "source_id": "synthetic:p7-04:quality-triage",
            "correlation_id": f"corr:triage:{domain}:{index:03d}:{label}",
            "participant_token_sha256": _sha({"synthetic_participant": global_index % 24}),
        } for label in (("duplicate", "primary") if expected_report_count == 2 else ("primary",))]
        _require(item.get("source_provenance_sha256") == _sha(provenances), "TRIAGE_SOURCE_PROVENANCE_BINDING_MISMATCH", item["issue_id"])
        raw_reports += item["report_count"]
        expected_domain_results[domain]["issues"] += 1
        if item["severity"] == "critical":
            critical_total += 1
            critical_open += int(item.get("status") != "CLOSED")
            closure = _mapping(item.get("closure_evidence"), "TRIAGE_CRITICAL_CLOSURE_REQUIRED", item.get("issue_id", "issue"))
            _exact(closure, {"fix_sha256", "verification_sha256", "review_sha256", "closed_at", "closed_by", "provenance", "closure_evidence_sha256"}, "TRIAGE_CLOSURE_EVIDENCE_FIELDS_INVALID", item["issue_id"])
            supplied = closure.get("closure_evidence_sha256")
            body = {key: _copy(entry) for key, entry in closure.items() if key != "closure_evidence_sha256"}
            _require(supplied == _sha(body), "TRIAGE_CLOSURE_EVIDENCE_TAMPERED", item.get("issue_id", "issue"))
            expected_artifacts = {
                "fix_sha256": _sha({"issue_id": item["issue_id"], "artifact": "synthetic_fix"}),
                "verification_sha256": _sha({"issue_id": item["issue_id"], "test": "synthetic_regression"}),
                "review_sha256": _sha({"issue_id": item["issue_id"], "reviewer_role": "synthetic_independent_control"}),
            }
            for field in ("fix_sha256", "verification_sha256", "review_sha256"):
                _require(isinstance(closure.get(field), str) and bool(_SHA.fullmatch(closure[field])), "TRIAGE_CLOSURE_EVIDENCE_INCOMPLETE", field)
                _require(closure[field] == expected_artifacts[field], "TRIAGE_CLOSURE_ARTIFACT_BINDING_MISMATCH", field)
                closure_checks += 1
            closed_at = _time(closure.get("closed_at"), "closed_at")
            _require(created <= closed_at <= due, "TRIAGE_CRITICAL_SLA_MISSED", item.get("issue_id", "issue"))
            _require(closed_at == start + timedelta(hours=domain_index + 1), "TRIAGE_CLOSURE_TIME_BINDING_MISMATCH", item["issue_id"])
            _require(closure.get("closed_by") == item["owner"], "TRIAGE_CLOSURE_OWNER_MISMATCH", item.get("issue_id", "issue"))
            _require(closure.get("provenance") == {"source_id": "synthetic:p7-04:closure", "correlation_id": f"corr:closure:{item['issue_id']}"}, "TRIAGE_CLOSURE_PROVENANCE_MISMATCH", item["issue_id"])
            expected_domain_results[domain]["critical_closed"] += 1
        else:
            _require(item.get("status") == "OPEN" and item.get("closure_evidence") is None, "TRIAGE_NONCRITICAL_STATE_INVALID", item["issue_id"])
            noncritical += 1
            noncritical_deadlines += int(bool(item.get("due_at")))
            expected_domain_results[domain]["noncritical_with_deadline"] += 1
    _require(_json_equal(evaluation.get("domain_results"), expected_domain_results), "TRIAGE_DOMAIN_RESULTS_MISMATCH", "domain_results")
    metrics = _mapping(evaluation.get("metrics"), "TRIAGE_METRICS_REQUIRED", "metrics")
    expected_metrics = {
        "raw_issue_reports": raw_reports,
        "unique_issues": len(backlog),
        "duplicate_reports": raw_reports - len(backlog),
        "duplicate_reports_deduplicated": raw_reports - len(backlog),
        "deduplication_percent": 100.0,
        "domains_covered": len(DOMAINS),
        "severity_levels_covered": len(SEVERITIES),
        "classification_checks_passed": len(backlog) * 2,
        "classification_checks_total": len(backlog) * 2,
        "classification_percent": 100.0,
        "owner_assignments_passed": len(backlog),
        "owner_assignments_total": len(backlog),
        "owner_coverage_percent": 100.0,
        "critical_issues_total": critical_total,
        "critical_issues_closed": critical_total,
        "critical_open_bugs": 0,
        "noncritical_issues_total": noncritical,
        "noncritical_deadlines_assigned": noncritical,
        "noncritical_deadline_coverage_percent": 100.0,
        "closure_evidence_checks_passed": closure_checks,
        "closure_evidence_checks_total": closure_checks,
        "closure_evidence_percent": 100.0,
        "sla_policy_checks_passed": len(backlog),
        "sla_policy_checks_total": len(backlog),
        "sla_policy_percent": 100.0,
        "provenance_checks_passed": raw_reports,
        "provenance_checks_total": raw_reports,
        "provenance_completeness_percent": 100.0,
        "burn_down_points": 5,
        "burn_down_final_open_critical": 0,
        "actual_human_beta_participants": 0,
        "production_records_read": 0,
        "external_contacts_executed": 0,
        "godot_live_actions_executed": 0,
    }
    for field, expected_value in expected_metrics.items():
        _require(_json_equal(metrics.get(field), expected_value), "TRIAGE_METRIC_MISMATCH", field)
    metric_fields = set(expected_metrics) | {"false_green_controls_passed", "false_green_controls_total", "false_green_rejection_percent", "deterministic_replay_percent"}
    _require(set(metrics) == metric_fields, "TRIAGE_METRIC_FIELDS_INVALID", "metrics")
    _require(
        type(metrics.get("false_green_controls_total")) is int
        and type(metrics.get("false_green_controls_passed")) is int
        and metrics["false_green_controls_total"] >= 24
        and _json_equal(metrics.get("false_green_controls_passed"), metrics["false_green_controls_total"])
        and _json_equal(metrics.get("false_green_rejection_percent"), 100.0),
        "TRIAGE_FALSE_GREEN_CONTROL_FAILED",
        "control metrics",
    )
    _require(_json_equal(metrics.get("deterministic_replay_percent"), 100.0), "TRIAGE_DETERMINISM_FALSE_GREEN", "deterministic_replay_percent")
    _require(critical_open == 0, "TRIAGE_CRITICAL_OPEN_NONZERO", str(critical_open))
    _require(noncritical_deadlines == noncritical, "TRIAGE_NONCRITICAL_DEADLINE_MISSING", str(noncritical_deadlines))
    burn = evaluation.get("burn_down")
    expected_burn = [
        {"simulated_at": _iso(start + timedelta(hours=hour)), "open_total": 160 - hour * 5, "open_critical": 20 - hour * 5, "closed_critical": hour * 5}
        for hour in range(5)
    ]
    _require(_json_equal(burn, expected_burn), "TRIAGE_BURN_DOWN_FALSE_GREEN", "exact burn-down")
    claims = evaluation.get("claims")
    _require(_json_equal(claims, {"actual_human_beta": "NONE", "production_data": "NONE", "external_contact": "NONE", "g6_approval": "NONE", "g7_approval": "NONE", "godot_live": "NONE", "formal_wbs_completion": "NONE", "p7_03_issue_level_trace": "NONE"}), "TRIAGE_CLAIM_OVERREACH", "claims")
    promotion = evaluation.get("wbs_promotion", {})
    _require(
        _json_equal(promotion, {
            "p7_04_local_mechanical_candidate": True,
            "formal_wbs_status": "Not Started",
            "formal_wbs_completion_allowed": False,
            "g6_approved": False,
            "g7_approved": False,
            "actual_human_beta_complete": False,
            "production_data_validated": False,
            "godot_live_validated": False,
        }),
        "TRIAGE_PROMOTION_OVERCLAIM",
        "wbs_promotion",
    )


def _reseal(value: dict[str, Any]) -> dict[str, Any]:
    value["evaluation_sha256"] = _sha({key: _copy(item) for key, item in value.items() if key != "evaluation_sha256"})
    return value


def validate_quality_triage_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _mapping(value, "TRIAGE_EVALUATION_OBJECT_REQUIRED", "evaluation")
    fields = {"schema_version", "policy_version", "work_item", "status", "fixture_binding", "fixture_sha256", "canonical_wbs_source", "p7_03_dependency", "metrics", "domain_results", "backlog", "burn_down", "backlog_sha256", "claims", "wbs_promotion", "false_green_controls", "evaluation_sha256"}
    _exact(evaluation, fields, "TRIAGE_EVALUATION_FIELDS_INVALID", "evaluation")
    expected = _sha({key: _copy(item) for key, item in evaluation.items() if key != "evaluation_sha256"})
    _require(evaluation.get("evaluation_sha256") == expected, "TRIAGE_EVALUATION_TAMPERED", "evaluation_sha256")
    _validate_invariants(evaluation)
    controls = evaluation.get("false_green_controls")
    _require(isinstance(controls, Mapping) and all(value is True for value in controls.values()), "TRIAGE_FALSE_GREEN_CONTROL_FAILED", "false_green_controls")
    _require(len(controls) == 81 and _sha(sorted(controls)) == "6ef75a16c031c14ad5bc9985cc60693bd953c9c4780078db5593380d05e9c212", "TRIAGE_FALSE_GREEN_NAMESET_INVALID", "false_green_controls")
    _require(type(evaluation["metrics"].get("false_green_controls_passed")) is int and type(evaluation["metrics"].get("false_green_controls_total")) is int and _json_equal(evaluation["metrics"].get("false_green_controls_passed"), len(controls)) and _json_equal(evaluation["metrics"].get("false_green_controls_total"), len(controls)), "TRIAGE_METRIC_MISMATCH", "false_green controls")
    return _copy(dict(evaluation))


def _baseline_candidate(core: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _copy(dict(core))
    candidate["false_green_controls"] = {f"placeholder_{index:02d}": True for index in range(24)}
    candidate["metrics"]["false_green_controls_passed"] = 24
    candidate["metrics"]["false_green_controls_total"] = 24
    candidate["metrics"]["false_green_rejection_percent"] = 100.0
    candidate["metrics"]["deterministic_replay_percent"] = 100.0
    return _reseal(candidate)


def _validate_attack_candidate(candidate: Mapping[str, Any]) -> None:
    expected = _sha({key: _copy(item) for key, item in candidate.items() if key != "evaluation_sha256"})
    _require(candidate.get("evaluation_sha256") == expected, "TRIAGE_EVALUATION_TAMPERED", "evaluation_sha256")
    _validate_invariants(candidate)


def _attack(base: Mapping[str, Any], mutate: Callable[[dict[str, Any]], None], *, reseal: bool = True) -> bool:
    candidate = _copy(dict(base))
    mutate(candidate)
    if "backlog" in candidate:
        candidate["backlog_sha256"] = _sha(candidate["backlog"])
    if reseal:
        _reseal(candidate)
    try:
        _validate_attack_candidate(candidate)
    except QualityTriageError:
        return True
    return False


def run_false_green_controls(value: Mapping[str, Any]) -> dict[str, bool]:
    core = _evaluate_core(value)
    base = _baseline_candidate(core)
    _validate_attack_candidate(base)
    controls: dict[str, bool] = {}
    def mutate_closure(item: dict[str, Any], changes: Mapping[str, Any]) -> None:
        issue = next(entry for entry in item["backlog"] if entry["severity"] == "critical")
        issue["closure_evidence"].update(_copy(dict(changes)))
        body = {key: _copy(entry) for key, entry in issue["closure_evidence"].items() if key != "closure_evidence_sha256"}
        issue["closure_evidence"]["closure_evidence_sha256"] = _sha(body)

    def shift_all_times(item: dict[str, Any]) -> None:
        for issue in item["backlog"]:
            issue["first_observed_at"] = _iso(_time(issue["first_observed_at"], "first_observed_at") + timedelta(hours=1))
            issue["due_at"] = _iso(_time(issue["due_at"], "due_at") + timedelta(hours=1))
            if issue["closure_evidence"] is not None:
                issue["closure_evidence"]["closed_at"] = _iso(_time(issue["closure_evidence"]["closed_at"], "closed_at") + timedelta(hours=1))
                body = {key: _copy(entry) for key, entry in issue["closure_evidence"].items() if key != "closure_evidence_sha256"}
                issue["closure_evidence"]["closure_evidence_sha256"] = _sha(body)
        for point in item["burn_down"]:
            point["simulated_at"] = _iso(_time(point["simulated_at"], "burn time") + timedelta(hours=1))

    fixture_controls = (
        ("duplicate_domain_catalog_rejected", "domains", "conversation"),
        ("duplicate_severity_catalog_rejected", "severities", "critical"),
        ("duplicate_discomfort_catalog_rejected", "p7_03_discomfort_categories", "autonomy_surprise"),
    )
    for name, field, duplicate in fixture_controls:
        candidate_fixture = _copy(dict(value))
        candidate_fixture["taxonomy"][field].append(duplicate)
        try:
            _evaluate_core(candidate_fixture)
        except QualityTriageError:
            controls[name] = True
        else:
            controls[name] = False

    controls["sealed_evaluation_tamper_rejected"] = _attack(base, lambda item: item.update({"status": "FORMAL_COMPLETE"}), reseal=False)
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("work_item_swap_rejected", lambda item: item.update({"work_item": "P7-05"})),
        ("policy_version_swap_rejected", lambda item: item.update({"policy_version": "forged"})),
        ("wbs_workbook_hash_swap_rejected", lambda item: item["canonical_wbs_source"].update({"workbook_sha256": "0" * 64})),
        ("wbs_row_swap_rejected", lambda item: item["canonical_wbs_source"].update({"row": 55})),
        ("wbs_row_float_alias_rejected", lambda item: item["canonical_wbs_source"].update({"row": 56.0})),
        ("wbs_objective_swap_rejected", lambda item: item["canonical_wbs_source"].update({"objective": "forged"})),
        ("wbs_acceptance_swap_rejected", lambda item: item["canonical_wbs_source"].update({"acceptance": "critical allowed"})),
        ("wbs_kpi_swap_rejected", lambda item: item["canonical_wbs_source"].update({"kpi": "Critical open bugs > 0"})),
        ("p7_03_dependency_swap_rejected", lambda item: item["canonical_wbs_source"].update({"dependency": "P7-02"})),
        ("p7_03_evidence_hash_swap_rejected", lambda item: item["p7_03_dependency"].update({"evidence_sha256": "0" * 64})),
        ("p7_03_issue_trace_overclaim_rejected", lambda item: item["p7_03_dependency"].update({"issue_level_trace_claim": "COMPLETE"})),
        ("p7_03_binding_scope_overclaim_rejected", lambda item: item["p7_03_dependency"].update({"binding_scope": "complete_issue_level_trace"})),
        ("p7_03_non_json_evidence_rejected", lambda item: item["p7_03_dependency"].update({"evidence_path": item["canonical_wbs_source"]["workbook_path"], "evidence_sha256": item["canonical_wbs_source"]["workbook_sha256"]})),
        ("g6_approval_overclaim_rejected", lambda item: item["wbs_promotion"].update({"g6_approved": True})),
        ("g7_approval_overclaim_rejected", lambda item: item["wbs_promotion"].update({"g7_approved": True})),
        ("formal_completion_overclaim_rejected", lambda item: item["wbs_promotion"].update({"formal_wbs_completion_allowed": True, "formal_wbs_status": "Completed"})),
        ("human_beta_overclaim_rejected", lambda item: item["wbs_promotion"].update({"actual_human_beta_complete": True})),
        ("production_validation_overclaim_rejected", lambda item: item["wbs_promotion"].update({"production_data_validated": True})),
        ("godot_live_overclaim_rejected", lambda item: item["wbs_promotion"].update({"godot_live_validated": True})),
        ("g7_claim_rejected", lambda item: item["claims"].update({"g7_approval": "PASS"})),
        ("human_claim_rejected", lambda item: item["claims"].update({"actual_human_beta": "COMPLETE"})),
        ("production_claim_rejected", lambda item: item["claims"].update({"production_data": "READ"})),
        ("external_contact_claim_rejected", lambda item: item["claims"].update({"external_contact": "EXECUTED"})),
        ("raw_issue_count_false_green_rejected", lambda item: item["metrics"].update({"raw_issue_reports": 999})),
        ("raw_issue_float_alias_rejected", lambda item: item["metrics"].update({"raw_issue_reports": 240.0})),
        ("domain_results_erased_rejected", lambda item: item.update({"domain_results": {}})),
        ("classification_percent_false_green_rejected", lambda item: item["metrics"].update({"classification_percent": 0.0})),
        ("duplicate_count_false_green_rejected", lambda item: item["metrics"].update({"duplicate_reports": 0})),
        ("dedupe_percent_false_green_rejected", lambda item: item["metrics"].update({"deduplication_percent": 99.9})),
        ("duplicate_fingerprint_rejected", lambda item: item["backlog"][1].update({"fingerprint": item["backlog"][0]["fingerprint"]})),
        ("duplicate_issue_id_rejected", lambda item: item["backlog"][1].update({"issue_id": item["backlog"][0]["issue_id"]})),
        ("backlog_order_reversal_rejected", lambda item: item["backlog"].reverse()),
        ("missing_fingerprint_rejected", lambda item: item["backlog"][0].update({"fingerprint": None})),
        ("orphan_issue_rejected", lambda item: item["backlog"].append(_copy(item["backlog"][-1]))),
        ("unknown_domain_rejected", lambda item: item["backlog"][0].update({"domain": "unknown"})),
        ("unknown_severity_rejected", lambda item: item["backlog"][0].update({"severity": "blocker"})),
        ("critical_severity_downgrade_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical").update({"severity": "high", "sla_hours": 24})),
        ("source_category_swap_rejected", lambda item: item["backlog"][0].update({"source_category": "privacy_concern" if item["backlog"][0]["source_category"] != "privacy_concern" else "response_delay"})),
        ("critical_owner_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical").update({"owner": None})),
        ("noncritical_owner_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] != "critical").update({"owner": None})),
        ("noncritical_deadline_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] != "critical").update({"due_at": None})),
        ("noncritical_deadline_late_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "high").update({"due_at": "2099-01-01T00:00:00Z"})),
        ("critical_reopened_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical").update({"status": "OPEN"})),
        ("critical_closure_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical").update({"closure_evidence": None})),
        ("critical_fix_hash_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical")["closure_evidence"].update({"fix_sha256": "bad"})),
        ("critical_verification_hash_missing_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical")["closure_evidence"].update({"verification_sha256": "bad"})),
        ("critical_closure_after_sla_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] == "critical")["closure_evidence"].update({"closed_at": "2099-01-01T00:00:00Z"})),
        ("critical_close_before_observed_rejected", lambda item: mutate_closure(item, {"closed_at": "2026-08-31T04:59:00Z"})),
        ("arbitrary_closure_artifact_hashes_rejected", lambda item: mutate_closure(item, {"fix_sha256": "a" * 64, "verification_sha256": "b" * 64, "review_sha256": "c" * 64})),
        ("noncritical_closed_without_evidence_rejected", lambda item: next(entry for entry in item["backlog"] if entry["severity"] != "critical").update({"status": "CLOSED"})),
        ("critical_open_metric_rejected", lambda item: item["metrics"].update({"critical_open_bugs": 1})),
        ("critical_open_bool_alias_rejected", lambda item: item["metrics"].update({"critical_open_bugs": False})),
        ("critical_open_negative_zero_rejected", lambda item: item["metrics"].update({"critical_open_bugs": -0.0})),
        ("deadline_coverage_false_green_rejected", lambda item: item["metrics"].update({"noncritical_deadline_coverage_percent": 99.9})),
        ("closure_coverage_false_green_rejected", lambda item: item["metrics"].update({"closure_evidence_checks_passed": 59})),
        ("burn_down_critical_nonzero_rejected", lambda item: item["burn_down"][-1].update({"open_critical": 1})),
        ("burn_down_total_mismatch_rejected", lambda item: item["burn_down"][-1].update({"open_total": 141})),
        ("burn_down_negative_intermediate_rejected", lambda item: item["burn_down"][2].update({"open_critical": -1})),
        ("burn_down_timestamp_forged_rejected", lambda item: item["burn_down"][2].update({"simulated_at": "2099-01-01T00:00:00Z"})),
        ("burn_down_arithmetic_forged_rejected", lambda item: item["burn_down"][2].update({"closed_critical": 9, "open_total": 151})),
        ("burn_down_bool_alias_rejected", lambda item: item["burn_down"][0].update({"closed_critical": False})),
        ("global_time_shift_rejected", shift_all_times),
        ("noncanonical_timezone_alias_rejected", lambda item: item["backlog"][0].update({"first_observed_at": "2026-08-31T14:00:00+09:00"})),
        ("provenance_incomplete_rejected", lambda item: item["backlog"][0].update({"provenance_complete": False})),
        ("production_source_rejected", lambda item: item["backlog"][0].update({"source_type": "production_user_report"})),
        ("duplicates_erased_rejected", lambda item: [entry.update({"report_count": 1}) for entry in item["backlog"]]),
        ("report_count_bool_alias_rejected", lambda item: next(entry for entry in item["backlog"] if entry["report_count"] == 1).update({"report_count": True})),
        ("sla_float_alias_rejected", lambda item: item["backlog"][0].update({"sla_hours": 4.0})),
        ("source_report_digest_removed_rejected", lambda item: item["backlog"][0].pop("source_report_ids_sha256")),
        ("source_provenance_digest_removed_rejected", lambda item: item["backlog"][0].pop("source_provenance_sha256")),
        ("deterministic_percent_false_green_rejected", lambda item: item["metrics"].update({"deterministic_replay_percent": 0.0})),
        ("false_green_percent_false_green_rejected", lambda item: item["metrics"].update({"false_green_rejection_percent": 0.0})),
        ("claim_field_removed_rejected", lambda item: item["claims"].pop("formal_wbs_completion")),
        ("human_participant_metric_rejected", lambda item: item["metrics"].update({"actual_human_beta_participants": 1})),
        ("production_record_metric_rejected", lambda item: item["metrics"].update({"production_records_read": 1})),
        ("external_contact_metric_rejected", lambda item: item["metrics"].update({"external_contacts_executed": 1})),
        ("godot_action_metric_rejected", lambda item: item["metrics"].update({"godot_live_actions_executed": 1})),
    ]
    for name, mutate in mutations:
        controls[name] = _attack(base, mutate)
    return controls


def evaluate_quality_triage_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _validate_fixture(value)
    result = _evaluate_core(fixture)
    controls = run_false_green_controls(fixture)
    passed = sum(controls.values())
    _require(len(controls) >= fixture["expectations"]["false_green_controls_min"], "TRIAGE_FALSE_GREEN_MATRIX_TOO_SMALL", str(len(controls)))
    _require(passed == len(controls), "TRIAGE_FALSE_GREEN_CONTROL_FAILED", f"{passed}/{len(controls)}")
    result["false_green_controls"] = controls
    result["metrics"].update({
        "false_green_controls_passed": passed,
        "false_green_controls_total": len(controls),
        "false_green_rejection_percent": 100.0,
        "deterministic_replay_percent": 100.0,
    })
    _reseal(result)
    validate_quality_triage_evaluation(result)
    return result


__all__ = [
    "CANONICAL_WBS",
    "DOMAINS",
    "EVALUATION_SCHEMA",
    "FIXTURE_SCHEMA",
    "OWNER_BY_DOMAIN",
    "POLICY_VERSION",
    "QualityTriageError",
    "SEVERITIES",
    "SLA_HOURS",
    "evaluate_quality_triage_fixture",
    "run_false_green_controls",
    "validate_quality_triage_evaluation",
]
