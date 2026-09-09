"""P6-03 deterministic role packs integrated with P6-01 and P6-02.

The three packs are local reference policies.  They classify requests and
produce handoff/exit decisions without invoking Godot, a live operator, or any
external action.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .multi_user_identity_v1 import (
    REFERENCE_TEST_SECRET,
    MultiUserIdentityError,
    MultiUserIdentityModel,
    create_identity_context,
)
from .social_interaction_protocol_v1 import (
    SOCIAL_EVENT_SCHEMA,
    SocialInteractionProtocol,
)


ROLE_REQUEST_SCHEMA = "lumina.role-pack.request.v1"
ROLE_DECISION_SCHEMA = "lumina.role-pack.decision.v1"
FIXTURE_SCHEMA = "lumina.role-pack.fixture.v1"
EVALUATION_SCHEMA = "lumina.role-pack.evaluation.v1"
POLICY_VERSION = "lumina.role-pack.policy.v1"
PACK_IDS = ("collaboration", "consultation", "visitor_guidance")

ROLE_PACKS: dict[str, dict[str, Any]] = {
    "visitor_guidance": {
        "title": "来訪案内",
        "allowed_identity_roles": ("owner", "member"),
        "required_permissions": ("action:evaluate_self", "world:read_self"),
        "allowed_intents": (
            "explain_exhibit",
            "provide_directions",
            "queue_assistance",
        ),
        "forbidden_intents": (
            "collect_sensitive_data",
            "external_contact",
            "financial_advice",
            "medical_advice",
        ),
        "handoff_triggers": (
            "accessibility_assistance",
            "emergency",
            "lost_person",
            "out_of_scope",
        ),
        "exit_triggers": ("task_complete", "user_exit", "visitor_departed"),
        "handoff_target": "on_site_human_guide",
    },
    "consultation": {
        "title": "相談",
        "allowed_identity_roles": ("owner", "member"),
        "required_permissions": ("action:evaluate_self", "memory:read_self"),
        "allowed_intents": (
            "clarify_request",
            "record_user_decision",
            "summarize_options",
        ),
        "forbidden_intents": (
            "coerce_decision",
            "financial_transaction",
            "legal_determination",
            "medical_diagnosis",
        ),
        "handoff_triggers": (
            "crisis",
            "low_confidence",
            "out_of_scope",
            "regulated_advice",
        ),
        "exit_triggers": ("consultation_complete", "user_exit", "user_withdrew"),
        "handoff_target": "qualified_human_advisor",
    },
    "collaboration": {
        "title": "共同作業",
        "allowed_identity_roles": ("owner",),
        "required_permissions": (
            "action:evaluate_self",
            "memory:read_self",
            "world:read_self",
        ),
        "allowed_intents": (
            "edit_local_draft",
            "propose_task",
            "review_progress",
        ),
        "forbidden_intents": (
            "credential_access",
            "delete_without_consent",
            "external_publish",
            "impersonate_user",
        ),
        "handoff_triggers": (
            "conflict",
            "external_effect",
            "out_of_scope",
            "permission_escalation",
        ),
        "exit_triggers": ("collaboration_complete", "session_closed", "user_exit"),
        "handoff_target": "human_collaboration_owner",
    },
}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class RolePackError(ValueError):
    """Stable fail-closed role-pack error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RolePackError(
            "ROLE_PACK_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RolePackError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise RolePackError(code, detail)


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RolePackError("ROLE_PACK_ID_INVALID", field_name)
    return value


def _time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RolePackError("ROLE_PACK_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RolePackError("ROLE_PACK_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise RolePackError(
            "ROLE_PACK_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RolePackError(
            "ROLE_PACK_TIMESTAMP_TIMEZONE_REQUIRED",
            "now",
        )
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _provenance_complete(value: Mapping[str, Any]) -> bool:
    return all(
        value.get(field)
        for field in (
            "source_id",
            "correlation_id",
            "observed_at",
            "authentication_method",
        )
    )


def get_role_pack_policy(pack_id: str) -> dict[str, Any]:
    if pack_id not in ROLE_PACKS:
        raise RolePackError("ROLE_PACK_UNKNOWN", str(pack_id))
    policy = ROLE_PACKS[pack_id]
    return {
        "pack_id": pack_id,
        **{
            key: list(item) if isinstance(item, tuple) else item
            for key, item in policy.items()
        },
    }


class RolePackEngine:
    """Evaluate one role pack against current P6 identity and social state."""

    def __init__(
        self,
        *,
        pack_id: str,
        identity_model: MultiUserIdentityModel,
        social_protocol: SocialInteractionProtocol,
    ) -> None:
        self.pack_id = pack_id
        self.policy = get_role_pack_policy(pack_id)
        if not isinstance(identity_model, MultiUserIdentityModel):
            raise RolePackError("ROLE_PACK_IDENTITY_MODEL_REQUIRED", "identity_model")
        if not isinstance(social_protocol, SocialInteractionProtocol):
            raise RolePackError("ROLE_PACK_SOCIAL_PROTOCOL_REQUIRED", "social_protocol")
        self.identity_model = identity_model
        self.social_protocol = social_protocol
        self._request_ids: set[str] = set()
        self._exited_users: set[str] = set()

    def _request(self, value: Mapping[str, Any], current: datetime) -> dict[str, Any]:
        request = _mapping(value, "ROLE_PACK_REQUEST_OBJECT_REQUIRED", "request")
        fields = {
            "schema_version",
            "request_id",
            "role_pack_id",
            "room_id",
            "tenant_id",
            "user_id",
            "session_id",
            "intent",
            "trigger",
            "requested_at",
            "payload",
            "provenance",
        }
        _exact(
            request,
            fields,
            "ROLE_PACK_REQUEST_FIELDS_INVALID",
            "request fields must match v1 exactly",
        )
        if request["schema_version"] != ROLE_REQUEST_SCHEMA:
            raise RolePackError("ROLE_PACK_REQUEST_SCHEMA_MISMATCH", "schema_version")
        request_id = _id(request["request_id"], "request_id")
        if request_id in self._request_ids:
            raise RolePackError("ROLE_PACK_REQUEST_REPLAYED", request_id)
        if request["role_pack_id"] != self.pack_id:
            raise RolePackError(
                "ROLE_PACK_BINDING_MISMATCH",
                str(request["role_pack_id"]),
            )
        _id(request["room_id"], "room_id")
        _id(request["tenant_id"], "tenant_id")
        _id(request["user_id"], "user_id")
        _id(request["session_id"], "session_id")
        _id(request["intent"], "intent")
        if request["trigger"] is not None:
            _id(request["trigger"], "trigger")
        if _time(request["requested_at"], "requested_at") != current:
            raise RolePackError(
                "ROLE_PACK_REQUEST_TIME_MISMATCH",
                request_id,
            )
        if not isinstance(request["payload"], Mapping):
            raise RolePackError("ROLE_PACK_PAYLOAD_OBJECT_REQUIRED", request_id)
        provenance = _mapping(
            request["provenance"],
            "ROLE_PACK_PROVENANCE_REQUIRED",
            "provenance",
        )
        _exact(
            provenance,
            {"source_id", "correlation_id", "observed_at", "authentication_method"},
            "ROLE_PACK_PROVENANCE_FIELDS_INVALID",
            "provenance",
        )
        if not _provenance_complete(provenance):
            raise RolePackError("ROLE_PACK_PROVENANCE_INCOMPLETE", request_id)
        _time(provenance["observed_at"], "provenance.observed_at")
        _canonical(request)
        return _copy(dict(request))

    def evaluate(
        self,
        token: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        current = _utc(now)
        try:
            scope = self.identity_model.validate_scope_token(
                token,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
            )
        except MultiUserIdentityError as exc:
            raise RolePackError(
                "ROLE_PACK_IDENTITY_TOKEN_REJECTED",
                exc.code,
            ) from exc
        normalized = self._request(request, current)
        for field in ("tenant_id", "user_id", "session_id"):
            if normalized[field] != scope[field]:
                code = {
                    "tenant_id": "ROLE_PACK_TENANT_TOKEN_MISMATCH",
                    "user_id": "ROLE_PACK_ACTOR_TOKEN_MISMATCH",
                    "session_id": "ROLE_PACK_SESSION_TOKEN_MISMATCH",
                }[field]
                raise RolePackError(code, str(normalized[field]))
        if normalized["provenance"] != scope["provenance"]:
            raise RolePackError(
                "ROLE_PACK_PROVENANCE_TOKEN_MISMATCH",
                normalized["request_id"],
            )
        if scope["role"] not in self.policy["allowed_identity_roles"]:
            raise RolePackError(
                "ROLE_PACK_IDENTITY_ROLE_DENIED",
                scope["role"],
            )
        missing_permissions = sorted(
            set(self.policy["required_permissions"]) - set(scope["permissions"])
        )
        if missing_permissions:
            raise RolePackError(
                "ROLE_PACK_PERMISSION_DENIED",
                ",".join(missing_permissions),
            )

        snapshot = self.social_protocol.snapshot()
        if normalized["room_id"] != snapshot["room_id"]:
            raise RolePackError("ROLE_PACK_ROOM_MISMATCH", normalized["room_id"])
        if normalized["tenant_id"] != snapshot["tenant_id"]:
            raise RolePackError(
                "ROLE_PACK_TENANT_SOCIAL_MISMATCH",
                normalized["tenant_id"],
            )
        participant = snapshot["participants"].get(normalized["user_id"])
        if participant is None:
            raise RolePackError(
                "ROLE_PACK_PARTICIPANT_NOT_REGISTERED",
                normalized["user_id"],
            )
        if (
            participant["session_id"] != scope["session_id"]
            or participant["role"] != scope["role"]
            or participant["memory_partition_id"] != scope["memory_scope"]["partition_id"]
            or participant["world_state_id"] != scope["world_state_scope"]["state_id"]
        ):
            raise RolePackError(
                "ROLE_PACK_SOCIAL_IDENTITY_MISMATCH",
                normalized["user_id"],
            )
        if participant["presence"] != "PRESENT":
            raise RolePackError(
                "ROLE_PACK_PARTICIPANT_NOT_PRESENT",
                normalized["user_id"],
            )
        if snapshot["active_speaker_user_id"] != normalized["user_id"]:
            raise RolePackError(
                "ROLE_PACK_ACTOR_NOT_CURRENT_SPEAKER",
                normalized["user_id"],
            )
        if normalized["user_id"] in self._exited_users:
            raise RolePackError(
                "ROLE_PACK_ALREADY_EXITED",
                normalized["user_id"],
            )
        if normalized["payload"].get("external_effect_requested") is not False:
            raise RolePackError(
                "ROLE_PACK_EXTERNAL_EFFECT_PROHIBITED",
                normalized["request_id"],
            )

        intent = normalized["intent"]
        trigger = normalized["trigger"]
        if intent in self.policy["forbidden_intents"]:
            raise RolePackError("ROLE_PACK_FORBIDDEN_INTENT", intent)
        if intent in self.policy["allowed_intents"]:
            if trigger is not None:
                raise RolePackError("ROLE_PACK_TRIGGER_UNEXPECTED", str(trigger))
            decision = "ALLOW_LOCAL_REFERENCE"
            handoff_target = None
            exit_applied = False
        elif intent == "request_handoff":
            if trigger not in self.policy["handoff_triggers"]:
                raise RolePackError(
                    "ROLE_PACK_HANDOFF_TRIGGER_INVALID",
                    str(trigger),
                )
            decision = "HANDOFF_REQUIRED"
            handoff_target = self.policy["handoff_target"]
            exit_applied = False
        elif intent == "exit_role":
            if trigger not in self.policy["exit_triggers"]:
                raise RolePackError(
                    "ROLE_PACK_EXIT_TRIGGER_INVALID",
                    str(trigger),
                )
            decision = "EXIT_ROLE_PACK"
            handoff_target = None
            exit_applied = True
        else:
            raise RolePackError("ROLE_PACK_INTENT_UNKNOWN", intent)

        try:
            self.identity_model.validate_scope_token(
                scope,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
                consume=True,
            )
        except MultiUserIdentityError as exc:
            raise RolePackError(
                "ROLE_PACK_IDENTITY_TOKEN_REJECTED",
                exc.code,
            ) from exc
        self._request_ids.add(normalized["request_id"])
        if exit_applied:
            self._exited_users.add(normalized["user_id"])
        result = {
            "schema_version": ROLE_DECISION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "decision": decision,
            "role_pack_id": self.pack_id,
            "request_id": normalized["request_id"],
            "request_sha256": _sha(normalized),
            "payload_sha256": _sha(normalized["payload"]),
            "intent": intent,
            "trigger": trigger,
            "handoff_target": handoff_target,
            "handoff_executed": False,
            "exit_applied": exit_applied,
            "tenant_id": scope["tenant_id"],
            "user_id": scope["user_id"],
            "session_id": scope["session_id"],
            "room_id": normalized["room_id"],
            "identity_role": scope["role"],
            "identity_token_id": scope["token_id"],
            "provenance": {
                **_copy(normalized["provenance"]),
                "identity_token_id": scope["token_id"],
                "identity_context_sha256": scope["context_sha256"],
            },
            "godot_action_executed": False,
            "live_action_executed": False,
            "external_action_executed": False,
        }
        result["decision_sha256"] = _sha(result)
        return result


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "ROLE_PACK_FIXTURE_OBJECT_REQUIRED", "fixture")
    fields = {
        "schema_version",
        "issuer",
        "generation",
        "pack_ids",
        "protocol",
        "foreign_markers",
        "expectations",
    }
    _exact(
        fixture,
        fields,
        "ROLE_PACK_FIXTURE_FIELDS_INVALID",
        "fixture fields must match v1 exactly",
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise RolePackError("ROLE_PACK_FIXTURE_SCHEMA_MISMATCH", "schema_version")
    issuer = _mapping(fixture["issuer"], "ROLE_PACK_ISSUER_REQUIRED", "issuer")
    _exact(
        issuer,
        {"issuer_id", "reference_secret_id"},
        "ROLE_PACK_ISSUER_FIELDS_INVALID",
        "issuer",
    )
    _id(issuer["issuer_id"], "issuer_id")
    if issuer["reference_secret_id"] != "p6-01-local-test-key-v1":
        raise RolePackError(
            "ROLE_PACK_REFERENCE_SECRET_ID_INVALID",
            "reference_secret_id",
        )
    generation = _mapping(
        fixture["generation"],
        "ROLE_PACK_GENERATION_REQUIRED",
        "generation",
    )
    _exact(
        generation,
        {"tenant_id", "scenarios_per_pack", "start_at"},
        "ROLE_PACK_GENERATION_FIELDS_INVALID",
        "generation",
    )
    _id(generation["tenant_id"], "generation.tenant_id")
    count = generation["scenarios_per_pack"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 40:
        raise RolePackError(
            "ROLE_PACK_SCENARIO_COUNT_TOO_SMALL",
            "at least 40 scenarios per pack are required",
        )
    _time(generation["start_at"], "generation.start_at")
    if not isinstance(fixture["pack_ids"], list) or set(fixture["pack_ids"]) != set(PACK_IDS):
        raise RolePackError(
            "ROLE_PACK_CATALOG_INVALID",
            "exactly three v1 packs are required",
        )
    protocol = _mapping(
        fixture["protocol"],
        "ROLE_PACK_PROTOCOL_CONFIG_REQUIRED",
        "protocol",
    )
    _exact(
        protocol,
        {"room_prefix", "token_ttl_seconds"},
        "ROLE_PACK_PROTOCOL_CONFIG_FIELDS_INVALID",
        "protocol",
    )
    _id(protocol["room_prefix"], "room_prefix")
    ttl = protocol["token_ttl_seconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 300:
        raise RolePackError("ROLE_PACK_TOKEN_TTL_INVALID", str(ttl))
    markers = fixture["foreign_markers"]
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise RolePackError("ROLE_PACK_FOREIGN_MARKERS_INVALID", "foreign_markers")
    expectations = _mapping(
        fixture["expectations"],
        "ROLE_PACK_EXPECTATIONS_REQUIRED",
        "expectations",
    )
    _exact(
        expectations,
        {"role_boundary_pass_percent_min", "handoff_executed", "external_action_executed"},
        "ROLE_PACK_EXPECTATIONS_FIELDS_INVALID",
        "expectations",
    )
    normalized = _copy(dict(fixture))
    normalized["pack_ids"] = sorted(normalized["pack_ids"])
    _canonical(normalized)
    return normalized


def _context(
    *,
    tenant_id: str,
    pack_id: str,
    scenario_index: int,
    label: str,
    role: str,
    observed_at: datetime,
) -> dict[str, Any]:
    tag = f"{pack_id}:{scenario_index:03d}:{label}"
    return create_identity_context(
        tenant_id=tenant_id,
        user_id=f"user:{tag}",
        session_id=f"session:{tag}",
        role=role,
        permissions=("action:evaluate_self", "memory:read_self", "world:read_self"),
        provenance={
            "source_id": f"source:role-pack:{tag}",
            "correlation_id": f"corr:role-pack:{tag}",
            "observed_at": _iso(observed_at),
            "authentication_method": "deterministic_fixture",
        },
    )


def _issue(
    model: MultiUserIdentityModel,
    context: Mapping[str, Any],
    *,
    token_id: str,
    now: datetime,
    ttl: int,
) -> dict[str, Any]:
    return model.issue_scope_token(
        context,
        token_id=token_id,
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=ttl),
    )


def _social_event(
    *,
    event_id: str,
    room_id: str,
    context: Mapping[str, Any],
    kind: str,
    now: datetime,
    content: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SOCIAL_EVENT_SCHEMA,
        "event_id": event_id,
        "room_id": room_id,
        "tenant_id": context["tenant_id"],
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "kind": kind,
        "target_user_id": None,
        "consent_ref": None,
        "content": content,
        "requested_at": _iso(now),
        "provenance": _copy(context["provenance"]),
    }


def _role_request(
    *,
    request_id: str,
    pack_id: str,
    room_id: str,
    context: Mapping[str, Any],
    intent: str,
    trigger: str | None,
    now: datetime,
    note: str,
) -> dict[str, Any]:
    return {
        "schema_version": ROLE_REQUEST_SCHEMA,
        "request_id": request_id,
        "role_pack_id": pack_id,
        "room_id": room_id,
        "tenant_id": context["tenant_id"],
        "user_id": context["user_id"],
        "session_id": context["session_id"],
        "intent": intent,
        "trigger": trigger,
        "requested_at": _iso(now),
        "payload": {
            "external_effect_requested": False,
            "note": note,
        },
        "provenance": _copy(context["provenance"]),
    }


def _raises(call: Any, code: str, detail: str | None = None) -> bool:
    try:
        call()
    except RolePackError as exc:
        return exc.code == code and (detail is None or exc.detail == detail)
    return False


def _setup(
    *,
    issuer_id: str,
    tenant_id: str,
    pack_id: str,
    scenario_index: int,
    start: datetime,
    ttl: int,
    actor_role: str = "owner",
) -> tuple[
    MultiUserIdentityModel,
    SocialInteractionProtocol,
    RolePackEngine,
    dict[str, Any],
    dict[str, Any],
    datetime,
    list[tuple[dict[str, Any], dict[str, Any]]],
]:
    model = MultiUserIdentityModel(issuer_id=issuer_id, secret=REFERENCE_TEST_SECRET)
    room_id = f"room:p6-03:{pack_id}:{scenario_index:03d}"
    protocol = SocialInteractionProtocol(
        room_id=room_id,
        tenant_id=tenant_id,
        identity_model=model,
    )
    actor = _context(
        tenant_id=tenant_id,
        pack_id=pack_id,
        scenario_index=scenario_index,
        label="actor",
        role=actor_role,
        observed_at=start,
    )
    peer = _context(
        tenant_id=tenant_id,
        pack_id=pack_id,
        scenario_index=scenario_index,
        label="peer",
        role="member",
        observed_at=start,
    )
    protocol.register_participant(actor)
    protocol.register_participant(peer)
    social_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    social_steps = (
        (actor, "JOIN", None),
        (peer, "JOIN", None),
        (actor, "SPEAK", f"role-pack-{pack_id}-{scenario_index:03d}"),
        (peer, "WAIT", None),
    )
    for offset, (context, kind, content) in enumerate(social_steps, start=1):
        current = start + timedelta(seconds=offset)
        token = _issue(
            model,
            context,
            token_id=f"token:setup:{pack_id}:{scenario_index:03d}:{offset}",
            now=current,
            ttl=ttl,
        )
        decision = protocol.process_event(
            token,
            _social_event(
                event_id=f"event:setup:{pack_id}:{scenario_index:03d}:{offset}",
                room_id=room_id,
                context=context,
                kind=kind,
                now=current,
                content=content,
            ),
            now=current,
        )
        social_pairs.append((token, decision))
    engine = RolePackEngine(
        pack_id=pack_id,
        identity_model=model,
        social_protocol=protocol,
    )
    return model, protocol, engine, actor, peer, start + timedelta(seconds=10), social_pairs


def evaluate_role_pack_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate three packs across at least 40 deterministic scenarios each."""

    fixture = _validate_fixture(value)
    issuer_id = fixture["issuer"]["issuer_id"]
    generation = fixture["generation"]
    tenant_id = generation["tenant_id"]
    start = _time(generation["start_at"], "generation.start_at")
    ttl = fixture["protocol"]["token_ttl_seconds"]

    pack_results: dict[str, dict[str, Any]] = {}
    scenario_results: list[dict[str, Any]] = []
    attack_ids: list[str] = []
    boundary_total = 0
    boundary_passed = 0
    provenance_total = 0
    provenance_passed = 0
    cross_user_attempts = 0
    cross_user_rejected = 0
    cross_user_authorized = 0
    leakage_count = 0
    unauthorized_count = 0
    external_count = 0
    forbidden_rejected_total = 0
    handoff_safe_total = 0
    exit_safe_total = 0
    post_exit_rejected_total = 0

    for pack_offset, pack_id in enumerate(fixture["pack_ids"]):
        policy = get_role_pack_policy(pack_id)
        pack_checks_total = 0
        pack_checks_passed = 0
        pack_scenarios_passed = 0
        for scenario_index in range(generation["scenarios_per_pack"]):
            scenario_start = start + timedelta(
                minutes=pack_offset * generation["scenarios_per_pack"] + scenario_index
            )
            model, protocol, engine, actor, peer, request_time, social_pairs = _setup(
                issuer_id=issuer_id,
                tenant_id=tenant_id,
                pack_id=pack_id,
                scenario_index=scenario_index,
                start=scenario_start,
                ttl=ttl,
            )
            for social_token, social_decision in social_pairs:
                provenance_total += 2
                provenance_passed += int(
                    _provenance_complete(social_token["provenance"])
                    and bool(social_token["context_sha256"])
                    and bool(social_token["token_hmac_sha256"])
                )
                provenance_passed += int(
                    _provenance_complete(social_decision["provenance"])
                    and bool(social_decision["decision_sha256"])
                )

            def role_token(suffix: str, context: Mapping[str, Any] = actor) -> dict[str, Any]:
                token = _issue(
                    model,
                    context,
                    token_id=f"token:role:{pack_id}:{scenario_index:03d}:{suffix}",
                    now=request_time,
                    ttl=ttl,
                )
                nonlocal provenance_total, provenance_passed
                provenance_total += 1
                provenance_passed += int(
                    _provenance_complete(token["provenance"])
                    and bool(token["context_sha256"])
                    and bool(token["token_hmac_sha256"])
                )
                return token

            allowed_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:allowed",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=actor,
                intent=policy["allowed_intents"][0],
                trigger=None,
                now=request_time,
                note="local reference only",
            )
            allowed = engine.evaluate(
                role_token("allowed"),
                allowed_request,
                now=request_time,
            )
            provenance_total += 1
            provenance_passed += int(
                _provenance_complete(allowed["provenance"])
                and bool(allowed["decision_sha256"])
            )
            allowed_ok = (
                allowed["decision"] == "ALLOW_LOCAL_REFERENCE"
                and allowed["external_action_executed"] is False
            )

            forbidden_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:forbidden",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=actor,
                intent=policy["forbidden_intents"][0],
                trigger=None,
                now=request_time,
                note="must reject",
            )
            forbidden_ok = _raises(
                lambda: engine.evaluate(
                    role_token("forbidden"),
                    forbidden_request,
                    now=request_time,
                ),
                "ROLE_PACK_FORBIDDEN_INTENT",
            )
            forbidden_rejected_total += int(forbidden_ok)

            handoff_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:handoff",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=actor,
                intent="request_handoff",
                trigger=policy["handoff_triggers"][0],
                now=request_time,
                note="classification only",
            )
            handoff = engine.evaluate(
                role_token("handoff"),
                handoff_request,
                now=request_time,
            )
            provenance_total += 1
            provenance_passed += int(
                _provenance_complete(handoff["provenance"])
                and bool(handoff["decision_sha256"])
            )
            handoff_ok = (
                handoff["decision"] == "HANDOFF_REQUIRED"
                and handoff["handoff_target"] == policy["handoff_target"]
                and handoff["handoff_executed"] is False
                and handoff["external_action_executed"] is False
            )
            handoff_safe_total += int(handoff_ok)

            attack_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:cross-user",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=peer,
                intent=policy["allowed_intents"][0],
                trigger=None,
                now=request_time,
                note="FOREIGN_ROLE_SECRET_44",
            )
            attack_ids.append(attack_request["request_id"])
            cross_user_attempts += 1
            try:
                attack = engine.evaluate(
                    role_token("cross-user", actor),
                    attack_request,
                    now=request_time,
                )
            except RolePackError as exc:
                cross_user_rejected += int(exc.code == "ROLE_PACK_ACTOR_TOKEN_MISMATCH")
            else:
                cross_user_authorized += 1
                unauthorized_count += 1
                leakage_count += json.dumps(
                    attack,
                    ensure_ascii=False,
                    sort_keys=True,
                ).count("FOREIGN_ROLE_SECRET_44")

            exit_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:exit",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=actor,
                intent="exit_role",
                trigger=policy["exit_triggers"][0],
                now=request_time,
                note="local exit",
            )
            exit_decision = engine.evaluate(
                role_token("exit"),
                exit_request,
                now=request_time,
            )
            provenance_total += 1
            provenance_passed += int(
                _provenance_complete(exit_decision["provenance"])
                and bool(exit_decision["decision_sha256"])
            )
            exit_ok = (
                exit_decision["decision"] == "EXIT_ROLE_PACK"
                and exit_decision["exit_applied"] is True
                and exit_decision["external_action_executed"] is False
            )
            exit_safe_total += int(exit_ok)

            post_request = _role_request(
                request_id=f"request:role:{pack_id}:{scenario_index:03d}:post-exit",
                pack_id=pack_id,
                room_id=protocol.room_id,
                context=actor,
                intent=policy["allowed_intents"][0],
                trigger=None,
                now=request_time,
                note="must fail after exit",
            )
            post_ok = _raises(
                lambda: engine.evaluate(
                    role_token("post-exit"),
                    post_request,
                    now=request_time,
                ),
                "ROLE_PACK_ALREADY_EXITED",
            )
            post_exit_rejected_total += int(post_ok)

            checks = [allowed_ok, forbidden_ok, handoff_ok, exit_ok, post_ok]
            pack_checks_total += len(checks)
            pack_checks_passed += sum(checks)
            boundary_total += len(checks)
            boundary_passed += sum(checks)
            scenario_ok = all(checks)
            pack_scenarios_passed += int(scenario_ok)
            external_count += sum(
                int(item["external_action_executed"])
                for item in (allowed, handoff, exit_decision)
            )
            serialized = json.dumps(
                {"allowed": allowed, "handoff": handoff, "exit": exit_decision},
                ensure_ascii=False,
                sort_keys=True,
            )
            leakage_count += sum(
                serialized.count(marker)
                for marker in fixture["foreign_markers"]
            )
            scenario_results.append({
                "scenario_id": f"scenario:role:{pack_id}:{scenario_index:03d}",
                "pack_id": pack_id,
                "boundary_checks_passed": sum(checks),
                "boundary_checks_total": len(checks),
                "cross_user_attack_rejected": cross_user_rejected == len(attack_ids),
                "social_state_sha256": protocol.snapshot()["state_sha256"],
                "passed": scenario_ok,
            })
        pack_results[pack_id] = {
            "scenario_count": generation["scenarios_per_pack"],
            "scenarios_passed": pack_scenarios_passed,
            "role_boundary_checks_passed": pack_checks_passed,
            "role_boundary_checks_total": pack_checks_total,
            "role_boundary_pass_percent": 100.0 * pack_checks_passed / pack_checks_total,
            "policy_sha256": _sha(policy),
        }

    # Focused controls use fresh isolated setup instances.
    control_start = start + timedelta(days=1)

    def fresh_control(
        *,
        pack_id: str = "visitor_guidance",
        actor_role: str = "owner",
        index: int = 900,
    ) -> tuple[Any, ...]:
        return _setup(
            issuer_id=issuer_id,
            tenant_id=tenant_id,
            pack_id=pack_id,
            scenario_index=index,
            start=control_start,
            ttl=ttl,
            actor_role=actor_role,
        )

    tamper_model, tamper_protocol, tamper_engine, tamper_actor, _, tamper_time, _ = fresh_control(index=901)
    tamper_token = _issue(
        tamper_model,
        tamper_actor,
        token_id="token:control:tamper",
        now=tamper_time,
        ttl=ttl,
    )
    tamper_token["user_id"] = "user:forged"
    tamper_request = _role_request(
        request_id="request:control:tamper",
        pack_id="visitor_guidance",
        room_id=tamper_protocol.room_id,
        context=tamper_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=tamper_time,
        note="tamper",
    )

    replay_model, replay_protocol, replay_engine, replay_actor, _, replay_time, _ = fresh_control(index=902)
    replay_token = _issue(
        replay_model,
        replay_actor,
        token_id="token:control:replay",
        now=replay_time,
        ttl=ttl,
    )
    replay_request = _role_request(
        request_id="request:control:replay:first",
        pack_id="visitor_guidance",
        room_id=replay_protocol.room_id,
        context=replay_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=replay_time,
        note="first",
    )
    replay_engine.evaluate(replay_token, replay_request, now=replay_time)
    replay_second = _copy(replay_request)
    replay_second["request_id"] = "request:control:replay:second"

    expiry_model, expiry_protocol, expiry_engine, expiry_actor, _, expiry_time, _ = fresh_control(index=903)
    expired_token = expiry_model.issue_scope_token(
        expiry_actor,
        token_id="token:control:expired",
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=expiry_time - timedelta(seconds=5),
        expires_at=expiry_time - timedelta(seconds=1),
    )
    expired_request = _role_request(
        request_id="request:control:expired",
        pack_id="visitor_guidance",
        room_id=expiry_protocol.room_id,
        context=expiry_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=expiry_time,
        note="expired",
    )

    wrong_model, wrong_protocol, wrong_engine, wrong_actor, _, wrong_time, _ = fresh_control(index=904)
    wrong_token = _issue(
        wrong_model,
        wrong_actor,
        token_id="token:control:wrong-pack",
        now=wrong_time,
        ttl=ttl,
    )
    wrong_request = _role_request(
        request_id="request:control:wrong-pack",
        pack_id="consultation",
        room_id=wrong_protocol.room_id,
        context=wrong_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=wrong_time,
        note="wrong pack",
    )

    inactive_model, inactive_protocol, inactive_engine, _, inactive_peer, inactive_time, _ = fresh_control(index=905)
    inactive_token = _issue(
        inactive_model,
        inactive_peer,
        token_id="token:control:inactive",
        now=inactive_time,
        ttl=ttl,
    )
    inactive_request = _role_request(
        request_id="request:control:inactive",
        pack_id="visitor_guidance",
        room_id=inactive_protocol.room_id,
        context=inactive_peer,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=inactive_time,
        note="not speaker",
    )

    member_model, member_protocol, member_engine, member_actor, _, member_time, _ = fresh_control(
        pack_id="collaboration",
        actor_role="member",
        index=906,
    )
    member_token = _issue(
        member_model,
        member_actor,
        token_id="token:control:member-collaboration",
        now=member_time,
        ttl=ttl,
    )
    member_request = _role_request(
        request_id="request:control:member-collaboration",
        pack_id="collaboration",
        room_id=member_protocol.room_id,
        context=member_actor,
        intent=ROLE_PACKS["collaboration"]["allowed_intents"][0],
        trigger=None,
        now=member_time,
        note="role escalation",
    )

    external_model, external_protocol, external_engine, external_actor, _, external_time, _ = fresh_control(index=907)
    external_token = _issue(
        external_model,
        external_actor,
        token_id="token:control:external",
        now=external_time,
        ttl=ttl,
    )
    external_request = _role_request(
        request_id="request:control:external",
        pack_id="visitor_guidance",
        room_id=external_protocol.room_id,
        context=external_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=external_time,
        note="external",
    )
    external_request["payload"]["external_effect_requested"] = True

    provenance_model, provenance_protocol, provenance_engine, provenance_actor, _, provenance_time, _ = fresh_control(index=908)
    provenance_token = _issue(
        provenance_model,
        provenance_actor,
        token_id="token:control:provenance",
        now=provenance_time,
        ttl=ttl,
    )
    provenance_request = _role_request(
        request_id="request:control:provenance",
        pack_id="visitor_guidance",
        room_id=provenance_protocol.room_id,
        context=provenance_actor,
        intent=ROLE_PACKS["visitor_guidance"]["allowed_intents"][0],
        trigger=None,
        now=provenance_time,
        note="provenance",
    )
    provenance_request["provenance"]["correlation_id"] = "corr:forged"

    negative_controls = {
        "identity_token_hmac_tamper_rejected": _raises(
            lambda: tamper_engine.evaluate(tamper_token, tamper_request, now=tamper_time),
            "ROLE_PACK_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_TAMPERED",
        ),
        "identity_token_replay_rejected": _raises(
            lambda: replay_engine.evaluate(replay_token, replay_second, now=replay_time),
            "ROLE_PACK_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_REPLAYED",
        ),
        "identity_token_expiry_rejected": _raises(
            lambda: expiry_engine.evaluate(expired_token, expired_request, now=expiry_time),
            "ROLE_PACK_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_EXPIRED",
        ),
        "wrong_pack_binding_rejected": _raises(
            lambda: wrong_engine.evaluate(wrong_token, wrong_request, now=wrong_time),
            "ROLE_PACK_BINDING_MISMATCH",
        ),
        "non_speaker_role_request_rejected": _raises(
            lambda: inactive_engine.evaluate(inactive_token, inactive_request, now=inactive_time),
            "ROLE_PACK_ACTOR_NOT_CURRENT_SPEAKER",
        ),
        "collaboration_member_role_escalation_rejected": _raises(
            lambda: member_engine.evaluate(member_token, member_request, now=member_time),
            "ROLE_PACK_IDENTITY_ROLE_DENIED",
        ),
        "external_effect_request_rejected": _raises(
            lambda: external_engine.evaluate(external_token, external_request, now=external_time),
            "ROLE_PACK_EXTERNAL_EFFECT_PROHIBITED",
        ),
        "provenance_swap_rejected": _raises(
            lambda: provenance_engine.evaluate(provenance_token, provenance_request, now=provenance_time),
            "ROLE_PACK_PROVENANCE_TOKEN_MISMATCH",
        ),
        "all_forbidden_intents_rejected": forbidden_rejected_total == len(scenario_results),
        "all_post_exit_requests_rejected": post_exit_rejected_total == len(scenario_results),
        "all_handoffs_are_classification_only": handoff_safe_total == len(scenario_results),
        "all_exits_are_local_state_only": exit_safe_total == len(scenario_results),
        "all_cross_user_attacks_rejected": cross_user_rejected == cross_user_attempts,
    }

    scenario_count = len(scenario_results)
    metrics = {
        "pack_count": len(pack_results),
        "scenario_count": scenario_count,
        "scenarios_per_pack": generation["scenarios_per_pack"],
        "role_boundary_checks_passed": boundary_passed,
        "role_boundary_checks_total": boundary_total,
        "role_boundary_pass_percent": 100.0 * boundary_passed / boundary_total,
        "cross_user_attack_attempts": cross_user_attempts,
        "cross_user_attacks_rejected": cross_user_rejected,
        "cross_user_rejection_percent": 100.0 * cross_user_rejected / cross_user_attempts,
        "cross_user_authorized_count": cross_user_authorized,
        "cross_user_leakage_count": leakage_count,
        "unauthorized_role_action_count": unauthorized_count,
        "external_actions_executed": external_count,
        "provenance_checks_passed": provenance_passed,
        "provenance_checks_total": provenance_total,
        "provenance_completeness_percent": 100.0 * provenance_passed / provenance_total,
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_total": len(negative_controls),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
    }
    local_candidate = (
        metrics["pack_count"] == 3
        and metrics["scenarios_per_pack"] >= 40
        and metrics["role_boundary_pass_percent"] == 100.0
        and metrics["cross_user_rejection_percent"] == 100.0
        and metrics["cross_user_authorized_count"] == 0
        and metrics["cross_user_leakage_count"] == 0
        and metrics["unauthorized_role_action_count"] == 0
        and metrics["external_actions_executed"] == 0
        and metrics["provenance_completeness_percent"] == 100.0
        and metrics["negative_controls_percent"] == 100.0
    )
    output = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "fixture_sha256": _sha(fixture),
        "dependencies": {
            "p6_01_identity_model": MultiUserIdentityModel.__name__,
            "p6_02_social_protocol": SocialInteractionProtocol.__name__,
            "g6_status": "NOT_APPROVED",
        },
        "role_policies": {
            pack_id: get_role_pack_policy(pack_id)
            for pack_id in fixture["pack_ids"]
        },
        "pack_results": pack_results,
        "scenario_results": scenario_results,
        "attack_matrix": {
            "attack_count": cross_user_attempts,
            "case_ids_sha256": _sha(attack_ids),
            "first_case_id": attack_ids[0],
            "last_case_id": attack_ids[-1],
        },
        "negative_controls": negative_controls,
        "metrics": metrics,
        "wbs_promotion": {
            "p6_03_local_mechanical_candidate": local_candidate,
            "formal_wbs_promotion_allowed": False,
            "g6_approved": False,
            "godot_role_integration_complete": False,
            "live_role_action_complete": False,
            "human_role_evaluation_complete": False,
        },
        "claims": {
            "gate_claim": "NONE",
            "g6_claim": "NONE",
            "godot_claim": "NONE",
            "live_role_action_claim": "NONE",
        },
    }
    output["evaluation_sha256"] = _sha(output)
    return output


def validate_role_pack_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(value, "ROLE_PACK_EVALUATION_OBJECT_REQUIRED", "evaluation")
    if result.get("schema_version") != EVALUATION_SCHEMA:
        raise RolePackError("ROLE_PACK_EVALUATION_SCHEMA_MISMATCH", "schema_version")
    expected = _sha({
        key: _copy(item)
        for key, item in result.items()
        if key != "evaluation_sha256"
    })
    if result.get("evaluation_sha256") != expected:
        raise RolePackError("ROLE_PACK_EVALUATION_TAMPERED", "evaluation_sha256")
    promotion = result.get("wbs_promotion", {})
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("g6_approved") is not False
    ):
        raise RolePackError(
            "ROLE_PACK_PROMOTION_OVERCLAIM",
            "G6 is not approved",
        )
    if result.get("metrics", {}).get("external_actions_executed") != 0:
        raise RolePackError(
            "ROLE_PACK_EXTERNAL_ACTION_OVERCLAIM",
            "external action prohibited",
        )
    return _copy(dict(result))


__all__ = [
    "EVALUATION_SCHEMA",
    "FIXTURE_SCHEMA",
    "PACK_IDS",
    "POLICY_VERSION",
    "ROLE_DECISION_SCHEMA",
    "ROLE_PACKS",
    "ROLE_REQUEST_SCHEMA",
    "RolePackEngine",
    "RolePackError",
    "evaluate_role_pack_fixture",
    "get_role_pack_policy",
    "validate_role_pack_evaluation",
]
