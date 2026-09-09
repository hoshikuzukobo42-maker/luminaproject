"""Fail-closed multi-user identity and scope binding for P6-01.

The module is an isolated, in-memory reference model.  It authenticates no live
user and performs no external action.  HMAC-sealed, one-time scope tokens bind
tenant, user, session, role, permissions, memory partition, world-state scope,
and provenance before existing P3 retrieval or P4 risk evaluation is called.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .memory_retrieval_v1 import MemoryRetrievalService
from .risk_policy_v1 import evaluate_action_request


IDENTITY_CONTEXT_SCHEMA = "lumina.identity.context.v1"
SCOPE_TOKEN_SCHEMA = "lumina.identity.scope-token.v1"
MEMORY_REQUEST_SCHEMA = "lumina.identity.memory-request.v1"
ACTION_ENVELOPE_SCHEMA = "lumina.identity.action-envelope.v1"
AUTHORIZATION_SCHEMA = "lumina.identity.authorization.v1"
FIXTURE_SCHEMA = "lumina.identity.fixture.v1"
EVALUATION_SCHEMA = "lumina.identity.evaluation.v1"
POLICY_VERSION = "lumina.identity.policy.v1"
MAX_TOKEN_TTL_SECONDS = 300

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({
        "memory:read_self",
        "world:read_self",
        "action:evaluate_self",
    }),
    "member": frozenset({
        "memory:read_self",
        "world:read_self",
        "action:evaluate_self",
    }),
    "guest": frozenset({"world:read_self"}),
}
AUDIENCE_OPERATIONS = {
    "memory_retrieval": "memory:read_self",
    "action_authorization": "action:evaluate_self",
}
REFERENCE_TEST_SECRET = hashlib.sha256(
    b"lumina-p6-01-deterministic-local-reference-key-not-production"
).digest()

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class MultiUserIdentityError(ValueError):
    """Stable fail-closed identity error."""

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
        raise MultiUserIdentityError(
            "IDENTITY_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _seal(value: Mapping[str, Any], secret: bytes) -> str:
    return hmac.new(secret, _canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MultiUserIdentityError(code, detail)
    return value


def _exact_fields(
    value: Mapping[str, Any],
    fields: set[str],
    code: str,
    detail: str,
) -> None:
    if set(value) != fields:
        raise MultiUserIdentityError(code, detail)


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MultiUserIdentityError("IDENTITY_ID_INVALID", field_name)
    return value


def _time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MultiUserIdentityError("IDENTITY_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MultiUserIdentityError("IDENTITY_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise MultiUserIdentityError(
            "IDENTITY_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime, field_name: str = "now") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MultiUserIdentityError(
            "IDENTITY_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def memory_partition_id(tenant_id: str, user_id: str) -> str:
    return f"memory:{tenant_id}:{user_id}"


def world_state_id(tenant_id: str, user_id: str, session_id: str) -> str:
    return f"world:{tenant_id}:{user_id}:{session_id}"


def create_identity_context(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    role: str,
    permissions: Iterable[str],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate an exact identity context."""

    value = {
        "schema_version": IDENTITY_CONTEXT_SCHEMA,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
        "role": role,
        "permissions": sorted(permissions),
        "memory_scope": {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "partition_id": memory_partition_id(tenant_id, user_id),
        },
        "world_state_scope": {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "state_id": world_state_id(tenant_id, user_id, session_id),
        },
        "provenance": _copy(dict(provenance)),
    }
    return validate_identity_context(value)


def validate_identity_context(value: Mapping[str, Any]) -> dict[str, Any]:
    context = _mapping(
        value,
        "IDENTITY_CONTEXT_OBJECT_REQUIRED",
        "context must be an object",
    )
    fields = {
        "schema_version",
        "tenant_id",
        "user_id",
        "session_id",
        "role",
        "permissions",
        "memory_scope",
        "world_state_scope",
        "provenance",
    }
    _exact_fields(
        context,
        fields,
        "IDENTITY_CONTEXT_FIELDS_INVALID",
        "identity context fields must match v1 exactly",
    )
    if context["schema_version"] != IDENTITY_CONTEXT_SCHEMA:
        raise MultiUserIdentityError(
            "IDENTITY_CONTEXT_SCHEMA_MISMATCH",
            "schema_version",
        )
    tenant_id = _identifier(context["tenant_id"], "tenant_id")
    user_id = _identifier(context["user_id"], "user_id")
    session_id = _identifier(context["session_id"], "session_id")
    role = context["role"]
    if role not in ROLE_PERMISSIONS:
        raise MultiUserIdentityError("IDENTITY_ROLE_UNKNOWN", str(role))
    permissions = context["permissions"]
    if (
        not isinstance(permissions, list)
        or not permissions
        or any(not isinstance(item, str) or not item for item in permissions)
        or len(permissions) != len(set(permissions))
    ):
        raise MultiUserIdentityError(
            "IDENTITY_PERMISSIONS_INVALID",
            "permissions must be a non-empty unique string array",
        )
    normalized_permissions = sorted(permissions)
    if not set(normalized_permissions).issubset(ROLE_PERMISSIONS[role]):
        raise MultiUserIdentityError(
            "IDENTITY_ROLE_PERMISSION_ESCALATION",
            role,
        )

    memory_scope = _mapping(
        context["memory_scope"],
        "IDENTITY_MEMORY_SCOPE_REQUIRED",
        "memory_scope",
    )
    _exact_fields(
        memory_scope,
        {"tenant_id", "user_id", "partition_id"},
        "IDENTITY_MEMORY_SCOPE_FIELDS_INVALID",
        "memory_scope",
    )
    if (
        memory_scope["tenant_id"] != tenant_id
        or memory_scope["user_id"] != user_id
        or memory_scope["partition_id"] != memory_partition_id(tenant_id, user_id)
    ):
        raise MultiUserIdentityError(
            "IDENTITY_MEMORY_SCOPE_MISMATCH",
            "memory scope must match tenant and subject",
        )

    world_scope = _mapping(
        context["world_state_scope"],
        "IDENTITY_WORLD_SCOPE_REQUIRED",
        "world_state_scope",
    )
    _exact_fields(
        world_scope,
        {"tenant_id", "user_id", "session_id", "state_id"},
        "IDENTITY_WORLD_SCOPE_FIELDS_INVALID",
        "world_state_scope",
    )
    if (
        world_scope["tenant_id"] != tenant_id
        or world_scope["user_id"] != user_id
        or world_scope["session_id"] != session_id
        or world_scope["state_id"] != world_state_id(tenant_id, user_id, session_id)
    ):
        raise MultiUserIdentityError(
            "IDENTITY_WORLD_SCOPE_MISMATCH",
            "world-state scope must match tenant, subject, and session",
        )

    provenance = _mapping(
        context["provenance"],
        "IDENTITY_PROVENANCE_REQUIRED",
        "provenance",
    )
    _exact_fields(
        provenance,
        {"source_id", "correlation_id", "observed_at", "authentication_method"},
        "IDENTITY_PROVENANCE_FIELDS_INVALID",
        "provenance",
    )
    _identifier(provenance["source_id"], "provenance.source_id")
    _identifier(provenance["correlation_id"], "provenance.correlation_id")
    _time(provenance["observed_at"], "provenance.observed_at")
    if provenance["authentication_method"] not in {
        "local_authenticated_session",
        "deterministic_fixture",
    }:
        raise MultiUserIdentityError(
            "IDENTITY_AUTHENTICATION_METHOD_INVALID",
            "provenance.authentication_method",
        )

    normalized = {
        **_copy(dict(context)),
        "permissions": normalized_permissions,
    }
    _canonical(normalized)
    return normalized


class MultiUserIdentityModel:
    """HMAC-sealed, one-time scope-token issuer and authorization guard."""

    def __init__(self, *, issuer_id: str, secret: bytes) -> None:
        self.issuer_id = _identifier(issuer_id, "issuer_id")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise MultiUserIdentityError(
                "IDENTITY_SECRET_INVALID",
                "secret must contain at least 32 bytes",
            )
        self._secret = secret
        self._issued: set[str] = set()
        self._consumed: dict[str, str] = {}

    def issue_scope_token(
        self,
        context: Mapping[str, Any],
        *,
        token_id: str,
        audience: str,
        operation: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        identity = validate_identity_context(context)
        token = _identifier(token_id, "token_id")
        if token in self._issued:
            raise MultiUserIdentityError("IDENTITY_TOKEN_ID_DUPLICATE", token)
        if AUDIENCE_OPERATIONS.get(audience) != operation:
            raise MultiUserIdentityError(
                "IDENTITY_AUDIENCE_OPERATION_MISMATCH",
                f"{audience}:{operation}",
            )
        if operation not in identity["permissions"]:
            raise MultiUserIdentityError(
                "IDENTITY_PERMISSION_DENIED",
                operation,
            )
        issued = _as_utc(issued_at, "issued_at")
        expires = _as_utc(expires_at, "expires_at")
        ttl = (expires - issued).total_seconds()
        if ttl <= 0 or ttl > MAX_TOKEN_TTL_SECONDS:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_TTL_INVALID",
                str(ttl),
            )
        observed = _time(
            identity["provenance"]["observed_at"],
            "provenance.observed_at",
        )
        if observed > issued:
            raise MultiUserIdentityError(
                "IDENTITY_PROVENANCE_FROM_FUTURE",
                token,
            )
        body = {
            "schema_version": SCOPE_TOKEN_SCHEMA,
            "policy_version": POLICY_VERSION,
            "token_id": token,
            "issuer_id": self.issuer_id,
            "audience": audience,
            "operation": operation,
            "tenant_id": identity["tenant_id"],
            "user_id": identity["user_id"],
            "session_id": identity["session_id"],
            "role": identity["role"],
            "permissions": _copy(identity["permissions"]),
            "memory_scope": _copy(identity["memory_scope"]),
            "world_state_scope": _copy(identity["world_state_scope"]),
            "provenance": _copy(identity["provenance"]),
            "issued_at": _iso(issued),
            "expires_at": _iso(expires),
            "one_time": True,
            "context_sha256": _sha(identity),
        }
        result = {**body, "token_hmac_sha256": _seal(body, self._secret)}
        self._issued.add(token)
        return result

    def validate_scope_token(
        self,
        value: Mapping[str, Any],
        *,
        audience: str,
        operation: str,
        now: datetime,
        consume: bool = False,
    ) -> dict[str, Any]:
        token = _mapping(
            value,
            "IDENTITY_TOKEN_OBJECT_REQUIRED",
            "scope token",
        )
        fields = {
            "schema_version",
            "policy_version",
            "token_id",
            "issuer_id",
            "audience",
            "operation",
            "tenant_id",
            "user_id",
            "session_id",
            "role",
            "permissions",
            "memory_scope",
            "world_state_scope",
            "provenance",
            "issued_at",
            "expires_at",
            "one_time",
            "context_sha256",
            "token_hmac_sha256",
        }
        _exact_fields(
            token,
            fields,
            "IDENTITY_TOKEN_FIELDS_INVALID",
            "scope token fields must match v1 exactly",
        )
        signature = token["token_hmac_sha256"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_SIGNATURE_INVALID",
                "token_hmac_sha256",
            )
        body = {key: _copy(item) for key, item in token.items() if key != "token_hmac_sha256"}
        if not hmac.compare_digest(signature, _seal(body, self._secret)):
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_TAMPERED",
                "HMAC mismatch",
            )
        if token["schema_version"] != SCOPE_TOKEN_SCHEMA or token["policy_version"] != POLICY_VERSION:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_SCHEMA_MISMATCH",
                "schema or policy version",
            )
        if token["issuer_id"] != self.issuer_id:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_ISSUER_MISMATCH",
                "issuer_id",
            )
        token_id = _identifier(token["token_id"], "token_id")
        if token["one_time"] is not True:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_NOT_ONE_TIME",
                token_id,
            )
        context = validate_identity_context({
            "schema_version": IDENTITY_CONTEXT_SCHEMA,
            "tenant_id": token["tenant_id"],
            "user_id": token["user_id"],
            "session_id": token["session_id"],
            "role": token["role"],
            "permissions": _copy(token["permissions"]),
            "memory_scope": _copy(token["memory_scope"]),
            "world_state_scope": _copy(token["world_state_scope"]),
            "provenance": _copy(token["provenance"]),
        })
        if token["context_sha256"] != _sha(context):
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_CONTEXT_MISMATCH",
                token_id,
            )
        if token["audience"] != audience:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_AUDIENCE_MISMATCH",
                audience,
            )
        if token["operation"] != operation or AUDIENCE_OPERATIONS.get(audience) != operation:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_OPERATION_MISMATCH",
                operation,
            )
        if operation not in context["permissions"]:
            raise MultiUserIdentityError(
                "IDENTITY_PERMISSION_DENIED",
                operation,
            )
        issued = _time(token["issued_at"], "token.issued_at")
        expires = _time(token["expires_at"], "token.expires_at")
        current = _as_utc(now)
        ttl = (expires - issued).total_seconds()
        if ttl <= 0 or ttl > MAX_TOKEN_TTL_SECONDS:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_TTL_INVALID",
                token_id,
            )
        if issued > current:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_FROM_FUTURE",
                token_id,
            )
        if expires <= current:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_EXPIRED",
                token_id,
            )
        if token_id in self._consumed:
            raise MultiUserIdentityError(
                "IDENTITY_TOKEN_REPLAYED",
                token_id,
            )
        normalized = _copy(dict(token))
        if consume:
            self._consumed[token_id] = signature
        return normalized

    def authorize_memory_retrieval(
        self,
        token: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        records: Iterable[Mapping[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        current = _as_utc(now)
        scope = self.validate_scope_token(
            token,
            audience="memory_retrieval",
            operation="memory:read_self",
            now=current,
        )
        value = _mapping(
            request,
            "IDENTITY_MEMORY_REQUEST_OBJECT_REQUIRED",
            "memory request",
        )
        fields = {
            "schema_version",
            "request_id",
            "tenant_id",
            "user_id",
            "session_id",
            "memory_partition_id",
            "query",
            "top_k",
            "requested_at",
        }
        _exact_fields(
            value,
            fields,
            "IDENTITY_MEMORY_REQUEST_FIELDS_INVALID",
            "memory request fields",
        )
        if value["schema_version"] != MEMORY_REQUEST_SCHEMA:
            raise MultiUserIdentityError(
                "IDENTITY_MEMORY_REQUEST_SCHEMA_MISMATCH",
                "schema_version",
            )
        _identifier(value["request_id"], "request_id")
        tenant = _identifier(value["tenant_id"], "tenant_id")
        user = _identifier(value["user_id"], "user_id")
        session = _identifier(value["session_id"], "session_id")
        if tenant != scope["tenant_id"]:
            raise MultiUserIdentityError("IDENTITY_TENANT_MISMATCH", tenant)
        if user != scope["user_id"]:
            raise MultiUserIdentityError("IDENTITY_SUBJECT_MISMATCH", user)
        if session != scope["session_id"]:
            raise MultiUserIdentityError("IDENTITY_SESSION_MISMATCH", session)
        if value["memory_partition_id"] != scope["memory_scope"]["partition_id"]:
            raise MultiUserIdentityError(
                "IDENTITY_MEMORY_SCOPE_MISMATCH",
                str(value["memory_partition_id"]),
            )
        query = value["query"]
        top_k = value["top_k"]
        if not isinstance(query, str) or not query.strip():
            raise MultiUserIdentityError("IDENTITY_QUERY_INVALID", "query")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise MultiUserIdentityError("IDENTITY_TOP_K_INVALID", "top_k")
        if _time(value["requested_at"], "requested_at") != current:
            raise MultiUserIdentityError(
                "IDENTITY_REQUEST_TIME_MISMATCH",
                "requested_at",
            )

        self.validate_scope_token(
            scope,
            audience="memory_retrieval",
            operation="memory:read_self",
            now=current,
            consume=True,
        )
        retrieval = MemoryRetrievalService(records).retrieve(
            tenant_id=tenant,
            user_id=user,
            query=query,
            as_of=current,
            top_k=top_k,
        )
        if retrieval["tenant_id"] != tenant or retrieval["user_id"] != user:
            raise MultiUserIdentityError(
                "IDENTITY_DOWNSTREAM_SCOPE_VIOLATION",
                "P3 retrieval scope",
            )
        if any(
            not item.get("grounding", {}).get("source_id")
            or not item.get("grounding", {}).get("correlation_id")
            for item in retrieval["results"]
        ):
            raise MultiUserIdentityError(
                "IDENTITY_DOWNSTREAM_PROVENANCE_INCOMPLETE",
                "P3 retrieval grounding",
            )
        result = {
            "schema_version": AUTHORIZATION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "authorization_type": "MEMORY_RETRIEVAL",
            "decision": "ALLOW",
            "identity_authorized": True,
            "execution_permitted": False,
            "external_effect_executed": False,
            "request_id": value["request_id"],
            "token_id": scope["token_id"],
            "identity_scope": {
                "tenant_id": tenant,
                "user_id": user,
                "session_id": session,
                "role": scope["role"],
                "permission": "memory:read_self",
                "memory_partition_id": value["memory_partition_id"],
                "world_state_id": scope["world_state_scope"]["state_id"],
            },
            "retrieval_result": retrieval,
            "policy_decision": None,
            "provenance": {
                **_copy(scope["provenance"]),
                "issuer_id": scope["issuer_id"],
                "token_id": scope["token_id"],
                "context_sha256": scope["context_sha256"],
            },
            "request_sha256": _sha(value),
        }
        result["decision_sha256"] = _sha(result)
        return result

    def authorize_action(
        self,
        token: Mapping[str, Any],
        envelope: Mapping[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        current = _as_utc(now)
        scope = self.validate_scope_token(
            token,
            audience="action_authorization",
            operation="action:evaluate_self",
            now=current,
        )
        value = _mapping(
            envelope,
            "IDENTITY_ACTION_ENVELOPE_OBJECT_REQUIRED",
            "action envelope",
        )
        fields = {
            "schema_version",
            "request_id",
            "tenant_id",
            "user_id",
            "session_id",
            "world_state_id",
            "requested_at",
            "action",
        }
        _exact_fields(
            value,
            fields,
            "IDENTITY_ACTION_ENVELOPE_FIELDS_INVALID",
            "action envelope fields",
        )
        if value["schema_version"] != ACTION_ENVELOPE_SCHEMA:
            raise MultiUserIdentityError(
                "IDENTITY_ACTION_ENVELOPE_SCHEMA_MISMATCH",
                "schema_version",
            )
        request_id = _identifier(value["request_id"], "request_id")
        tenant = _identifier(value["tenant_id"], "tenant_id")
        user = _identifier(value["user_id"], "user_id")
        session = _identifier(value["session_id"], "session_id")
        state_id = _identifier(value["world_state_id"], "world_state_id")
        if tenant != scope["tenant_id"]:
            raise MultiUserIdentityError("IDENTITY_TENANT_MISMATCH", tenant)
        if user != scope["user_id"]:
            raise MultiUserIdentityError("IDENTITY_SUBJECT_MISMATCH", user)
        if session != scope["session_id"]:
            raise MultiUserIdentityError("IDENTITY_SESSION_MISMATCH", session)
        if state_id != scope["world_state_scope"]["state_id"]:
            raise MultiUserIdentityError(
                "IDENTITY_WORLD_SCOPE_MISMATCH",
                state_id,
            )
        if _time(value["requested_at"], "requested_at") != current:
            raise MultiUserIdentityError(
                "IDENTITY_REQUEST_TIME_MISMATCH",
                "requested_at",
            )
        action = _mapping(
            value["action"],
            "IDENTITY_ACTION_OBJECT_REQUIRED",
            "action",
        )
        if action.get("request_id") != request_id:
            raise MultiUserIdentityError(
                "IDENTITY_ACTION_REQUEST_BINDING_MISMATCH",
                request_id,
            )
        if action.get("user_id") != user:
            raise MultiUserIdentityError(
                "IDENTITY_SUBJECT_MISMATCH",
                str(action.get("user_id")),
            )
        if action.get("correlation_id") != scope["provenance"]["correlation_id"]:
            raise MultiUserIdentityError(
                "IDENTITY_CORRELATION_MISMATCH",
                str(action.get("correlation_id")),
            )
        if action.get("target_scope") != state_id:
            raise MultiUserIdentityError(
                "IDENTITY_WORLD_SCOPE_MISMATCH",
                str(action.get("target_scope")),
            )

        self.validate_scope_token(
            scope,
            audience="action_authorization",
            operation="action:evaluate_self",
            now=current,
            consume=True,
        )
        policy = evaluate_action_request(action, now=current)
        result = {
            "schema_version": AUTHORIZATION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "authorization_type": "ACTION_EVALUATION",
            "decision": "ALLOW" if policy["execution_permitted"] else "BLOCK",
            "identity_authorized": True,
            "execution_permitted": policy["execution_permitted"],
            "external_effect_executed": False,
            "request_id": request_id,
            "token_id": scope["token_id"],
            "identity_scope": {
                "tenant_id": tenant,
                "user_id": user,
                "session_id": session,
                "role": scope["role"],
                "permission": "action:evaluate_self",
                "memory_partition_id": scope["memory_scope"]["partition_id"],
                "world_state_id": state_id,
            },
            "retrieval_result": None,
            "policy_decision": policy,
            "provenance": {
                **_copy(scope["provenance"]),
                "issuer_id": scope["issuer_id"],
                "token_id": scope["token_id"],
                "context_sha256": scope["context_sha256"],
            },
            "request_sha256": _sha(value),
        }
        result["decision_sha256"] = _sha(result)
        return result


def _fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(
        value,
        "IDENTITY_FIXTURE_OBJECT_REQUIRED",
        "fixture",
    )
    fields = {
        "schema_version",
        "issuer",
        "primary_context",
        "records",
        "positive_requests",
        "attack_generation",
        "foreign_markers",
        "expectations",
    }
    _exact_fields(
        fixture,
        fields,
        "IDENTITY_FIXTURE_FIELDS_INVALID",
        "fixture fields must match v1 exactly",
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise MultiUserIdentityError(
            "IDENTITY_FIXTURE_SCHEMA_MISMATCH",
            "schema_version",
        )
    issuer = _mapping(fixture["issuer"], "IDENTITY_ISSUER_REQUIRED", "issuer")
    _exact_fields(
        issuer,
        {"issuer_id", "reference_secret_id"},
        "IDENTITY_ISSUER_FIELDS_INVALID",
        "issuer",
    )
    _identifier(issuer["issuer_id"], "issuer_id")
    if issuer["reference_secret_id"] != "p6-01-local-test-key-v1":
        raise MultiUserIdentityError(
            "IDENTITY_REFERENCE_SECRET_ID_INVALID",
            "reference_secret_id",
        )
    normalized_context = validate_identity_context(fixture["primary_context"])
    if not isinstance(fixture["records"], list) or not fixture["records"]:
        raise MultiUserIdentityError("IDENTITY_RECORDS_REQUIRED", "records")
    requests = _mapping(
        fixture["positive_requests"],
        "IDENTITY_POSITIVE_REQUESTS_REQUIRED",
        "positive_requests",
    )
    _exact_fields(
        requests,
        {"memory", "action"},
        "IDENTITY_POSITIVE_REQUESTS_FIELDS_INVALID",
        "positive_requests",
    )
    generation = _mapping(
        fixture["attack_generation"],
        "IDENTITY_ATTACK_GENERATION_REQUIRED",
        "attack_generation",
    )
    _exact_fields(
        generation,
        {"tenant_id", "attacker_prefix", "victim_prefix", "attacker_count", "victim_count"},
        "IDENTITY_ATTACK_GENERATION_FIELDS_INVALID",
        "attack_generation",
    )
    _identifier(generation["tenant_id"], "attack_generation.tenant_id")
    _identifier(generation["attacker_prefix"], "attack_generation.attacker_prefix")
    _identifier(generation["victim_prefix"], "attack_generation.victim_prefix")
    if generation["attacker_prefix"] == generation["victim_prefix"]:
        raise MultiUserIdentityError(
            "IDENTITY_ATTACK_PREFIX_COLLISION",
            "attacker and victim prefixes",
        )
    for count_field in ("attacker_count", "victim_count"):
        count = generation[count_field]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise MultiUserIdentityError(
                "IDENTITY_ATTACK_COUNT_INVALID",
                count_field,
            )
    if generation["attacker_count"] * generation["victim_count"] < 1000:
        raise MultiUserIdentityError(
            "IDENTITY_ATTACK_MATRIX_TOO_SMALL",
            "at least 1000 attacker/victim pairs are required",
        )
    markers = fixture["foreign_markers"]
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise MultiUserIdentityError(
            "IDENTITY_FOREIGN_MARKERS_INVALID",
            "foreign_markers",
        )
    expectations = _mapping(
        fixture["expectations"],
        "IDENTITY_EXPECTATIONS_REQUIRED",
        "expectations",
    )
    _exact_fields(
        expectations,
        {"memory_id", "memory_content", "action_execution_permitted"},
        "IDENTITY_EXPECTATIONS_FIELDS_INVALID",
        "expectations",
    )
    _canonical(fixture)
    normalized = _copy(dict(fixture))
    normalized["primary_context"] = normalized_context
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda item: str(item.get("memory_id", "")) if isinstance(item, Mapping) else "",
    )
    return normalized


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


def _raises_code(call: Any, code: str) -> bool:
    try:
        call()
    except MultiUserIdentityError as exc:
        return exc.code == code
    return False


def _attack_context(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    now: datetime,
    correlation_id: str,
) -> dict[str, Any]:
    return create_identity_context(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        role="member",
        permissions=("memory:read_self", "world:read_self", "action:evaluate_self"),
        provenance={
            "source_id": f"source:{user_id}",
            "correlation_id": correlation_id,
            "observed_at": _iso(now - timedelta(seconds=1)),
            "authentication_method": "deterministic_fixture",
        },
    )


def _token_provenance_complete(token: Mapping[str, Any]) -> bool:
    provenance = token.get("provenance")
    return (
        isinstance(provenance, Mapping)
        and _provenance_complete(provenance)
        and bool(token.get("context_sha256"))
        and bool(token.get("token_hmac_sha256"))
    )


def evaluate_identity_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate golden paths plus a deterministic cross-user attack matrix."""

    fixture = _fixture(value)
    issuer_id = fixture["issuer"]["issuer_id"]
    context = validate_identity_context(fixture["primary_context"])
    memory_request = _copy(fixture["positive_requests"]["memory"])
    action_envelope = _copy(fixture["positive_requests"]["action"])
    now = _time(memory_request["requested_at"], "memory.requested_at")
    if _time(action_envelope["requested_at"], "action.requested_at") != now:
        raise MultiUserIdentityError(
            "IDENTITY_POSITIVE_REQUEST_TIME_MISMATCH",
            "memory/action",
        )
    model = MultiUserIdentityModel(issuer_id=issuer_id, secret=REFERENCE_TEST_SECRET)
    positive_memory_token = model.issue_scope_token(
        context,
        token_id="token:p6-01:positive:memory",
        audience="memory_retrieval",
        operation="memory:read_self",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=120),
    )
    positive_memory = model.authorize_memory_retrieval(
        positive_memory_token,
        memory_request,
        records=fixture["records"],
        now=now,
    )
    positive_action_token = model.issue_scope_token(
        context,
        token_id="token:p6-01:positive:action",
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=120),
    )
    positive_action = model.authorize_action(
        positive_action_token,
        action_envelope,
        now=now,
    )

    attack_generation = fixture["attack_generation"]
    attack_case_ids: list[str] = []
    retrieval_attempts = 0
    retrieval_rejected = 0
    retrieval_authorized = 0
    action_attempts = 0
    action_rejected = 0
    action_authorized = 0
    leakage_count = 0
    unauthorized_action_count = 0
    provenance_total = 2
    provenance_passed = sum(
        _token_provenance_complete(item)
        for item in (positive_memory_token, positive_action_token)
    )

    tenant_id = attack_generation["tenant_id"]
    for attacker_index in range(attack_generation["attacker_count"]):
        attacker = f"{attack_generation['attacker_prefix']}:{attacker_index:03d}"
        session = f"session:p6-01:attack:{attacker_index:03d}"
        for victim_index in range(attack_generation["victim_count"]):
            victim = f"{attack_generation['victim_prefix']}:{victim_index:03d}"
            pair = f"{attacker_index:03d}:{victim_index:03d}"
            correlation = f"corr:p6-01:attack:{pair}"
            attack_context = _attack_context(
                tenant_id=tenant_id,
                user_id=attacker,
                session_id=session,
                now=now,
                correlation_id=correlation,
            )

            memory_case_id = f"cross-user-memory:{pair}"
            memory_token = model.issue_scope_token(
                attack_context,
                token_id=f"token:p6-01:m:{pair}",
                audience="memory_retrieval",
                operation="memory:read_self",
                issued_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(seconds=120),
            )
            provenance_total += 1
            provenance_passed += int(_token_provenance_complete(memory_token))
            victim_marker = f"VICTIM_SECRET_{attacker_index:03d}_{victim_index:03d}"
            victim_records = [{
                "memory_id": f"memory:victim:{pair}",
                "tenant_id": tenant_id,
                "user_id": victim,
                "layer": "profile",
                "key": "victim_secret",
                "value": victim_marker,
                "content": victim_marker,
                "keywords": ["victim", "secret"],
                "confidence": 1.0,
                "provenance": {
                    "source_id": f"source:victim:{pair}",
                    "correlation_id": f"corr:victim:{pair}",
                },
                "status": "active",
            }]
            victim_memory_request = {
                "schema_version": MEMORY_REQUEST_SCHEMA,
                "request_id": f"request:p6-01:m:{pair}",
                "tenant_id": tenant_id,
                "user_id": victim,
                "session_id": session,
                "memory_partition_id": memory_partition_id(tenant_id, victim),
                "query": "victim secret",
                "top_k": 5,
                "requested_at": _iso(now),
            }
            retrieval_attempts += 1
            try:
                attack_output = model.authorize_memory_retrieval(
                    memory_token,
                    victim_memory_request,
                    records=victim_records,
                    now=now,
                )
            except MultiUserIdentityError as exc:
                retrieval_rejected += int(exc.code == "IDENTITY_SUBJECT_MISMATCH")
            else:
                retrieval_authorized += 1
                leakage_count += json.dumps(
                    attack_output,
                    ensure_ascii=False,
                    sort_keys=True,
                ).count(victim_marker)
            attack_case_ids.append(memory_case_id)

            action_case_id = f"cross-user-action:{pair}"
            action_token = model.issue_scope_token(
                attack_context,
                token_id=f"token:p6-01:a:{pair}",
                audience="action_authorization",
                operation="action:evaluate_self",
                issued_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(seconds=120),
            )
            provenance_total += 1
            provenance_passed += int(_token_provenance_complete(action_token))
            victim_state_id = world_state_id(tenant_id, victim, session)
            victim_action = {
                "schema_version": ACTION_ENVELOPE_SCHEMA,
                "request_id": f"request:p6-01:a:{pair}",
                "tenant_id": tenant_id,
                "user_id": victim,
                "session_id": session,
                "world_state_id": victim_state_id,
                "requested_at": _iso(now),
                "action": {
                    "schema_version": "lumina.action.request.v1",
                    "request_id": f"request:p6-01:a:{pair}",
                    "correlation_id": correlation,
                    "user_id": victim,
                    "action_type": "read_status",
                    "effect_class": "read_public",
                    "target_scope": victim_state_id,
                    "requested_at": _iso(now),
                    "payload": {},
                    "rollback_ref": None,
                    "approval_receipt": None,
                },
            }
            action_attempts += 1
            try:
                attack_output = model.authorize_action(
                    action_token,
                    victim_action,
                    now=now,
                )
            except MultiUserIdentityError as exc:
                action_rejected += int(exc.code == "IDENTITY_SUBJECT_MISMATCH")
            else:
                action_authorized += 1
                unauthorized_action_count += int(attack_output["execution_permitted"])
            attack_case_ids.append(action_case_id)

    def fresh_model() -> MultiUserIdentityModel:
        return MultiUserIdentityModel(issuer_id=issuer_id, secret=REFERENCE_TEST_SECRET)

    def fresh_token(
        *,
        token_id: str,
        audience: str = "memory_retrieval",
        operation: str = "memory:read_self",
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[MultiUserIdentityModel, dict[str, Any]]:
        local = fresh_model()
        return local, local.issue_scope_token(
            context,
            token_id=token_id,
            audience=audience,
            operation=operation,
            issued_at=issued_at or now - timedelta(seconds=1),
            expires_at=expires_at or now + timedelta(seconds=120),
        )

    tamper_model, tampered = fresh_token(token_id="token:negative:tamper")
    tampered = _copy(tampered)
    tampered["session_id"] = "session:forged"
    expired_model, expired = fresh_token(
        token_id="token:negative:expired",
        issued_at=now - timedelta(seconds=50),
        expires_at=now - timedelta(seconds=1),
    )
    future_model, future = fresh_token(
        token_id="token:negative:future",
        issued_at=now + timedelta(seconds=1),
        expires_at=now + timedelta(seconds=120),
    )
    replay_model, replay_token = fresh_token(
        token_id="token:negative:replay",
        audience="action_authorization",
        operation="action:evaluate_self",
    )
    replay_model.authorize_action(replay_token, action_envelope, now=now)
    impersonation_model, impersonation_token = fresh_token(
        token_id="token:negative:impersonation"
    )
    impersonation_request = _copy(memory_request)
    impersonation_request["user_id"] = "user:impersonated"
    impersonation_request["memory_partition_id"] = memory_partition_id(
        context["tenant_id"],
        impersonation_request["user_id"],
    )
    tenant_model, tenant_token = fresh_token(token_id="token:negative:tenant")
    tenant_request = _copy(memory_request)
    tenant_request["tenant_id"] = "tenant:other"
    session_model, session_token = fresh_token(token_id="token:negative:session")
    session_request = _copy(memory_request)
    session_request["session_id"] = "session:other"
    partition_model, partition_token = fresh_token(token_id="token:negative:partition")
    partition_request = _copy(memory_request)
    partition_request["memory_partition_id"] = "memory:tenant:other:user:other"
    world_model, world_token = fresh_token(
        token_id="token:negative:world",
        audience="action_authorization",
        operation="action:evaluate_self",
    )
    world_envelope = _copy(action_envelope)
    world_envelope["world_state_id"] = "world:tenant:other:user:other:session:other"
    audience_model, audience_token = fresh_token(token_id="token:negative:audience")
    unknown_model, unknown_token = fresh_token(token_id="token:negative:unknown")
    unknown_token = {**unknown_token, "unexpected": True}
    signature_model, signature_token = fresh_token(token_id="token:negative:signature")
    signature_token = _copy(signature_token)
    signature_token["token_hmac_sha256"] = "0" * 64
    correlation_model, correlation_token = fresh_token(
        token_id="token:negative:correlation",
        audience="action_authorization",
        operation="action:evaluate_self",
    )
    correlation_envelope = _copy(action_envelope)
    correlation_envelope["action"]["correlation_id"] = "corr:forged"
    elevated_model, elevated_token = fresh_token(
        token_id="token:negative:elevated",
        audience="action_authorization",
        operation="action:evaluate_self",
    )
    elevated_envelope = _copy(action_envelope)
    elevated_envelope["request_id"] = "request:p6-01:elevated"
    elevated_envelope["action"].update({
        "request_id": "request:p6-01:elevated",
        "action_type": "send_message",
        "effect_class": "external_communication",
    })
    elevated_result = elevated_model.authorize_action(
        elevated_token,
        elevated_envelope,
        now=now,
    )
    guest_escalation = _copy(context)
    guest_escalation["role"] = "guest"
    missing_provenance = _copy(context)
    missing_provenance["provenance"].pop("source_id")

    negative_controls = {
        "token_hmac_tamper_rejected": _raises_code(
            lambda: tamper_model.validate_scope_token(
                tampered,
                audience="memory_retrieval",
                operation="memory:read_self",
                now=now,
            ),
            "IDENTITY_TOKEN_TAMPERED",
        ),
        "expired_token_rejected": _raises_code(
            lambda: expired_model.validate_scope_token(
                expired,
                audience="memory_retrieval",
                operation="memory:read_self",
                now=now,
            ),
            "IDENTITY_TOKEN_EXPIRED",
        ),
        "future_token_rejected": _raises_code(
            lambda: future_model.validate_scope_token(
                future,
                audience="memory_retrieval",
                operation="memory:read_self",
                now=now,
            ),
            "IDENTITY_TOKEN_FROM_FUTURE",
        ),
        "one_time_token_replay_rejected": _raises_code(
            lambda: replay_model.authorize_action(replay_token, action_envelope, now=now),
            "IDENTITY_TOKEN_REPLAYED",
        ),
        "impersonation_rejected": _raises_code(
            lambda: impersonation_model.authorize_memory_retrieval(
                impersonation_token,
                impersonation_request,
                records=fixture["records"],
                now=now,
            ),
            "IDENTITY_SUBJECT_MISMATCH",
        ),
        "cross_tenant_rejected": _raises_code(
            lambda: tenant_model.authorize_memory_retrieval(
                tenant_token,
                tenant_request,
                records=fixture["records"],
                now=now,
            ),
            "IDENTITY_TENANT_MISMATCH",
        ),
        "session_swap_rejected": _raises_code(
            lambda: session_model.authorize_memory_retrieval(
                session_token,
                session_request,
                records=fixture["records"],
                now=now,
            ),
            "IDENTITY_SESSION_MISMATCH",
        ),
        "memory_partition_swap_rejected": _raises_code(
            lambda: partition_model.authorize_memory_retrieval(
                partition_token,
                partition_request,
                records=fixture["records"],
                now=now,
            ),
            "IDENTITY_MEMORY_SCOPE_MISMATCH",
        ),
        "world_state_swap_rejected": _raises_code(
            lambda: world_model.authorize_action(world_token, world_envelope, now=now),
            "IDENTITY_WORLD_SCOPE_MISMATCH",
        ),
        "wrong_audience_rejected": _raises_code(
            lambda: audience_model.validate_scope_token(
                audience_token,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=now,
            ),
            "IDENTITY_TOKEN_AUDIENCE_MISMATCH",
        ),
        "unknown_token_field_rejected": _raises_code(
            lambda: unknown_model.validate_scope_token(
                unknown_token,
                audience="memory_retrieval",
                operation="memory:read_self",
                now=now,
            ),
            "IDENTITY_TOKEN_FIELDS_INVALID",
        ),
        "corrupt_signature_rejected": _raises_code(
            lambda: signature_model.validate_scope_token(
                signature_token,
                audience="memory_retrieval",
                operation="memory:read_self",
                now=now,
            ),
            "IDENTITY_TOKEN_TAMPERED",
        ),
        "correlation_impersonation_rejected": _raises_code(
            lambda: correlation_model.authorize_action(
                correlation_token,
                correlation_envelope,
                now=now,
            ),
            "IDENTITY_CORRELATION_MISMATCH",
        ),
        "guest_permission_escalation_rejected": _raises_code(
            lambda: validate_identity_context(guest_escalation),
            "IDENTITY_ROLE_PERMISSION_ESCALATION",
        ),
        "missing_provenance_rejected": _raises_code(
            lambda: validate_identity_context(missing_provenance),
            "IDENTITY_PROVENANCE_FIELDS_INVALID",
        ),
        "elevated_action_without_receipt_blocked": (
            elevated_result["identity_authorized"] is True
            and elevated_result["execution_permitted"] is False
            and elevated_result["external_effect_executed"] is False
            and elevated_result["policy_decision"]["reason"] == "approval_required"
        ),
    }

    expected_memory_ids = [
        item["memory_id"] for item in positive_memory["retrieval_result"]["results"]
    ]
    positive_serialized = json.dumps(
        {"memory": positive_memory, "action": positive_action},
        ensure_ascii=False,
        sort_keys=True,
    )
    leakage_count += sum(
        positive_serialized.count(marker)
        for marker in fixture["foreign_markers"]
    )
    positive_provenance = [
        _provenance_complete(positive_memory["provenance"])
        and all(
            item["grounding"].get("source_id")
            and item["grounding"].get("correlation_id")
            for item in positive_memory["retrieval_result"]["results"]
        ),
        _provenance_complete(positive_action["provenance"])
        and bool(positive_action["policy_decision"].get("request_sha256"))
        and bool(positive_action["policy_decision"].get("decision_sha256")),
    ]
    provenance_total += len(positive_provenance)
    provenance_passed += sum(positive_provenance)

    attack_attempts = retrieval_attempts + action_attempts
    attacks_rejected = retrieval_rejected + action_rejected
    metrics = {
        "cross_user_pair_count": (
            attack_generation["attacker_count"] * attack_generation["victim_count"]
        ),
        "cross_user_attack_attempts": attack_attempts,
        "cross_user_retrieval_attempts": retrieval_attempts,
        "cross_user_action_attempts": action_attempts,
        "cross_user_attacks_rejected": attacks_rejected,
        "cross_user_rejection_percent": 100.0 * attacks_rejected / attack_attempts,
        "cross_user_retrieval_authorized_count": retrieval_authorized,
        "cross_user_action_authorized_count": action_authorized,
        "cross_user_leakage_count": leakage_count,
        "unauthorized_action_count": unauthorized_action_count,
        "provenance_checks_passed": provenance_passed,
        "provenance_checks_total": provenance_total,
        "provenance_completeness_percent": 100.0 * provenance_passed / provenance_total,
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_total": len(negative_controls),
        "negative_controls_percent": (
            100.0 * sum(negative_controls.values()) / len(negative_controls)
        ),
        "external_effects_executed": 0,
    }
    local_candidate = (
        metrics["cross_user_attack_attempts"] >= 1000
        and metrics["cross_user_rejection_percent"] == 100.0
        and metrics["cross_user_retrieval_authorized_count"] == 0
        and metrics["cross_user_action_authorized_count"] == 0
        and metrics["cross_user_leakage_count"] == 0
        and metrics["unauthorized_action_count"] == 0
        and metrics["provenance_completeness_percent"] == 100.0
        and metrics["negative_controls_percent"] == 100.0
        and expected_memory_ids == [fixture["expectations"]["memory_id"]]
        and positive_memory["retrieval_result"]["results"][0]["content"]
        == fixture["expectations"]["memory_content"]
        and positive_action["execution_permitted"]
        is fixture["expectations"]["action_execution_permitted"]
    )
    output = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "fixture_sha256": _sha(fixture),
        "dependencies": {
            "p3_retrieval_service": MemoryRetrievalService.__name__,
            "p4_risk_policy": "evaluate_action_request",
            "p4_08_status": "NOT_APPROVED_NOT_PRESENT_AS_PROMOTED_GATE",
            "g4_status": "NOT_APPROVED",
        },
        "positive_results": {
            "memory": positive_memory,
            "action": positive_action,
        },
        "attack_matrix": {
            "attacker_count": attack_generation["attacker_count"],
            "victim_count": attack_generation["victim_count"],
            "pair_count": metrics["cross_user_pair_count"],
            "attack_count": attack_attempts,
            "case_ids_sha256": _sha(attack_case_ids),
            "first_case_id": attack_case_ids[0],
            "last_case_id": attack_case_ids[-1],
        },
        "negative_controls": negative_controls,
        "metrics": metrics,
        "wbs_promotion": {
            "p6_01_local_mechanical_candidate": local_candidate,
            "formal_wbs_promotion_allowed": False,
            "p4_08_approved": False,
            "g4_approved": False,
            "live_authentication_integrated": False,
            "production_sqlite_integrated": False,
            "human_multi_user_evaluation_complete": False,
        },
        "claims": {
            "gate_claim": "NONE",
            "g4_claim": "NONE",
            "p4_08_claim": "NONE",
            "live_authentication_claim": "NONE",
            "external_effect_claim": "NONE",
        },
    }
    output["evaluation_sha256"] = _sha(output)
    return output


def validate_identity_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(
        value,
        "IDENTITY_EVALUATION_OBJECT_REQUIRED",
        "evaluation",
    )
    if result.get("schema_version") != EVALUATION_SCHEMA:
        raise MultiUserIdentityError(
            "IDENTITY_EVALUATION_SCHEMA_MISMATCH",
            "schema_version",
        )
    expected = _sha({
        key: _copy(item)
        for key, item in result.items()
        if key != "evaluation_sha256"
    })
    if result.get("evaluation_sha256") != expected:
        raise MultiUserIdentityError(
            "IDENTITY_EVALUATION_TAMPERED",
            "evaluation_sha256",
        )
    promotion = result.get("wbs_promotion", {})
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("p4_08_approved") is not False
        or promotion.get("g4_approved") is not False
    ):
        raise MultiUserIdentityError(
            "IDENTITY_FORMAL_PROMOTION_OVERCLAIM",
            "P4-08/G4 are not approved",
        )
    if result.get("metrics", {}).get("external_effects_executed") != 0:
        raise MultiUserIdentityError(
            "IDENTITY_EXTERNAL_EFFECT_OVERCLAIM",
            "external effects are prohibited",
        )
    return _copy(dict(result))


__all__ = [
    "ACTION_ENVELOPE_SCHEMA",
    "AUTHORIZATION_SCHEMA",
    "EVALUATION_SCHEMA",
    "FIXTURE_SCHEMA",
    "IDENTITY_CONTEXT_SCHEMA",
    "MAX_TOKEN_TTL_SECONDS",
    "MEMORY_REQUEST_SCHEMA",
    "MultiUserIdentityError",
    "MultiUserIdentityModel",
    "POLICY_VERSION",
    "REFERENCE_TEST_SECRET",
    "ROLE_PERMISSIONS",
    "SCOPE_TOKEN_SCHEMA",
    "create_identity_context",
    "evaluate_identity_fixture",
    "memory_partition_id",
    "validate_identity_context",
    "validate_identity_evaluation",
    "world_state_id",
]
