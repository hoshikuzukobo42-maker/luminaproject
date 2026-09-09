"""Deterministic, fail-closed social turn-taking reference for P6-02.

This module mutates only an in-memory protocol state.  Every accepted event is
bound to a one-time P6-01 identity token and provenance.  It never emits audio,
controls Godot, or performs a live/social external action.
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
    validate_identity_context,
)


SOCIAL_EVENT_SCHEMA = "lumina.social.event.v1"
SOCIAL_DECISION_SCHEMA = "lumina.social.decision.v1"
SOCIAL_STATE_SCHEMA = "lumina.social.state.v1"
FIXTURE_SCHEMA = "lumina.social.fixture.v1"
EVALUATION_SCHEMA = "lumina.social.evaluation.v1"
POLICY_VERSION = "lumina.social.protocol.v1"
EVENT_KINDS = (
    "JOIN",
    "SPEAK",
    "WAIT",
    "CONSENT",
    "INTERRUPT",
    "YIELD",
    "LEAVE",
    "REJOIN",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class SocialInteractionError(ValueError):
    """Stable fail-closed social protocol error."""

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
        raise SocialInteractionError(
            "SOCIAL_NONCANONICAL_JSON",
            "payload must be finite canonical JSON",
        ) from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SocialInteractionError(code, detail)
    return value


def _exact(value: Mapping[str, Any], fields: set[str], code: str, detail: str) -> None:
    if set(value) != fields:
        raise SocialInteractionError(code, detail)


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SocialInteractionError("SOCIAL_ID_INVALID", field_name)
    return value


def _time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SocialInteractionError("SOCIAL_TIMESTAMP_REQUIRED", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SocialInteractionError("SOCIAL_TIMESTAMP_INVALID", field_name) from exc
    if parsed.tzinfo is None:
        raise SocialInteractionError(
            "SOCIAL_TIMESTAMP_TIMEZONE_REQUIRED",
            field_name,
        )
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SocialInteractionError(
            "SOCIAL_TIMESTAMP_TIMEZONE_REQUIRED",
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


class SocialInteractionProtocol:
    """One room's deterministic turn-taking state machine."""

    def __init__(
        self,
        *,
        room_id: str,
        tenant_id: str,
        identity_model: MultiUserIdentityModel,
    ) -> None:
        self.room_id = _identifier(room_id, "room_id")
        self.tenant_id = _identifier(tenant_id, "tenant_id")
        if not isinstance(identity_model, MultiUserIdentityModel):
            raise SocialInteractionError(
                "SOCIAL_IDENTITY_MODEL_REQUIRED",
                "identity_model",
            )
        self.identity_model = identity_model
        self._participants: dict[str, dict[str, Any]] = {}
        self._presence: dict[str, str] = {}
        self._active_speaker: str | None = None
        self._queue: list[str] = []
        self._consents: dict[str, dict[str, Any]] = {}
        self._event_ids: set[str] = set()
        self._audit: list[dict[str, Any]] = []
        self._turn_sequence = 0

    def register_participant(self, context: Mapping[str, Any]) -> dict[str, Any]:
        identity = validate_identity_context(context)
        if identity["tenant_id"] != self.tenant_id:
            raise SocialInteractionError(
                "SOCIAL_TENANT_MISMATCH",
                identity["tenant_id"],
            )
        if "action:evaluate_self" not in identity["permissions"]:
            raise SocialInteractionError(
                "SOCIAL_PERMISSION_DENIED",
                "action:evaluate_self",
            )
        user_id = identity["user_id"]
        if user_id in self._participants:
            raise SocialInteractionError(
                "SOCIAL_PARTICIPANT_DUPLICATE",
                user_id,
            )
        self._participants[user_id] = identity
        self._presence[user_id] = "ABSENT"
        return {
            "room_id": self.room_id,
            "tenant_id": self.tenant_id,
            "user_id": user_id,
            "session_id": identity["session_id"],
            "role": identity["role"],
            "presence": "ABSENT",
            "context_sha256": _sha(identity),
        }

    def _validate_event(self, value: Mapping[str, Any], current: datetime) -> dict[str, Any]:
        event = _mapping(
            value,
            "SOCIAL_EVENT_OBJECT_REQUIRED",
            "event",
        )
        fields = {
            "schema_version",
            "event_id",
            "room_id",
            "tenant_id",
            "user_id",
            "session_id",
            "kind",
            "target_user_id",
            "consent_ref",
            "content",
            "requested_at",
            "provenance",
        }
        _exact(
            event,
            fields,
            "SOCIAL_EVENT_FIELDS_INVALID",
            "event fields must match v1 exactly",
        )
        if event["schema_version"] != SOCIAL_EVENT_SCHEMA:
            raise SocialInteractionError(
                "SOCIAL_EVENT_SCHEMA_MISMATCH",
                "schema_version",
            )
        event_id = _identifier(event["event_id"], "event_id")
        if event_id in self._event_ids:
            raise SocialInteractionError(
                "SOCIAL_EVENT_REPLAYED",
                event_id,
            )
        room_id = _identifier(event["room_id"], "room_id")
        tenant_id = _identifier(event["tenant_id"], "tenant_id")
        user_id = _identifier(event["user_id"], "user_id")
        session_id = _identifier(event["session_id"], "session_id")
        if room_id != self.room_id:
            raise SocialInteractionError("SOCIAL_ROOM_MISMATCH", room_id)
        if tenant_id != self.tenant_id:
            raise SocialInteractionError("SOCIAL_TENANT_MISMATCH", tenant_id)
        if user_id not in self._participants:
            raise SocialInteractionError(
                "SOCIAL_PARTICIPANT_NOT_REGISTERED",
                user_id,
            )
        kind = event["kind"]
        if kind not in EVENT_KINDS:
            raise SocialInteractionError("SOCIAL_EVENT_KIND_UNKNOWN", str(kind))
        if _time(event["requested_at"], "requested_at") != current:
            raise SocialInteractionError(
                "SOCIAL_REQUEST_TIME_MISMATCH",
                event_id,
            )
        provenance = _mapping(
            event["provenance"],
            "SOCIAL_PROVENANCE_REQUIRED",
            "provenance",
        )
        _exact(
            provenance,
            {"source_id", "correlation_id", "observed_at", "authentication_method"},
            "SOCIAL_PROVENANCE_FIELDS_INVALID",
            "provenance",
        )
        if not _provenance_complete(provenance):
            raise SocialInteractionError(
                "SOCIAL_PROVENANCE_INCOMPLETE",
                event_id,
            )
        _time(provenance["observed_at"], "provenance.observed_at")

        target = event["target_user_id"]
        consent_ref = event["consent_ref"]
        content = event["content"]
        if kind in {"CONSENT", "INTERRUPT"}:
            _identifier(target, "target_user_id")
            _identifier(consent_ref, "consent_ref")
        elif target is not None or consent_ref is not None:
            raise SocialInteractionError(
                "SOCIAL_EVENT_BINDING_UNEXPECTED",
                kind,
            )
        if kind in {"SPEAK", "INTERRUPT"}:
            if not isinstance(content, str) or not content.strip():
                raise SocialInteractionError(
                    "SOCIAL_CONTENT_REQUIRED",
                    kind,
                )
        elif content is not None:
            raise SocialInteractionError(
                "SOCIAL_CONTENT_UNEXPECTED",
                kind,
            )
        _canonical(event)
        return _copy(dict(event))

    def _identity_scope_matches(
        self,
        token: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> None:
        if token["tenant_id"] != event["tenant_id"]:
            raise SocialInteractionError(
                "SOCIAL_TENANT_TOKEN_MISMATCH",
                event["tenant_id"],
            )
        if token["user_id"] != event["user_id"]:
            raise SocialInteractionError(
                "SOCIAL_ACTOR_TOKEN_MISMATCH",
                event["user_id"],
            )
        if token["session_id"] != event["session_id"]:
            raise SocialInteractionError(
                "SOCIAL_SESSION_TOKEN_MISMATCH",
                event["session_id"],
            )
        registered = self._participants[event["user_id"]]
        for field in (
            "tenant_id",
            "user_id",
            "session_id",
            "role",
            "permissions",
            "memory_scope",
            "world_state_scope",
        ):
            if token[field] != registered[field]:
                raise SocialInteractionError(
                    "SOCIAL_REGISTERED_IDENTITY_MISMATCH",
                    field,
                )
        if token["provenance"] != event["provenance"]:
            raise SocialInteractionError(
                "SOCIAL_PROVENANCE_TOKEN_MISMATCH",
                event["event_id"],
            )
        if token["provenance"] != registered["provenance"]:
            raise SocialInteractionError(
                "SOCIAL_REGISTERED_PROVENANCE_MISMATCH",
                event["event_id"],
            )

    def _promote_next(self) -> str | None:
        while self._queue:
            candidate = self._queue.pop(0)
            if self._presence.get(candidate) == "PRESENT":
                self._active_speaker = candidate
                self._turn_sequence += 1
                return candidate
        self._active_speaker = None
        return None

    def process_event(
        self,
        token: Mapping[str, Any],
        event: Mapping[str, Any],
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
            raise SocialInteractionError(
                "SOCIAL_IDENTITY_TOKEN_REJECTED",
                exc.code,
            ) from exc
        normalized = self._validate_event(event, current)
        self._identity_scope_matches(scope, normalized)

        user_id = normalized["user_id"]
        kind = normalized["kind"]
        target = normalized["target_user_id"]
        consent_ref = normalized["consent_ref"]
        presence = self._presence[user_id]

        if kind == "JOIN":
            if presence != "ABSENT":
                raise SocialInteractionError("SOCIAL_JOIN_STATE_INVALID", user_id)
            effect = "PARTICIPANT_JOINED"
        elif kind == "REJOIN":
            if presence != "LEFT":
                raise SocialInteractionError("SOCIAL_REJOIN_STATE_INVALID", user_id)
            effect = "PARTICIPANT_REJOINED"
        elif presence != "PRESENT":
            raise SocialInteractionError("SOCIAL_PARTICIPANT_NOT_PRESENT", user_id)
        elif kind == "SPEAK":
            if self._active_speaker is not None:
                raise SocialInteractionError(
                    "SOCIAL_TURN_NOT_AVAILABLE",
                    str(self._active_speaker),
                )
            if self._queue and self._queue[0] != user_id:
                raise SocialInteractionError(
                    "SOCIAL_QUEUE_ORDER_VIOLATION",
                    user_id,
                )
            effect = "TURN_GRANTED"
        elif kind == "WAIT":
            if self._active_speaker == user_id or user_id in self._queue:
                raise SocialInteractionError(
                    "SOCIAL_WAIT_STATE_INVALID",
                    user_id,
                )
            effect = "PARTICIPANT_QUEUED"
        elif kind == "CONSENT":
            if self._active_speaker != user_id:
                raise SocialInteractionError(
                    "SOCIAL_CONSENT_GRANTOR_NOT_SPEAKING",
                    user_id,
                )
            if target == user_id or self._presence.get(str(target)) != "PRESENT":
                raise SocialInteractionError(
                    "SOCIAL_CONSENT_TARGET_INVALID",
                    str(target),
                )
            if consent_ref in self._consents:
                raise SocialInteractionError(
                    "SOCIAL_CONSENT_DUPLICATE",
                    str(consent_ref),
                )
            effect = "INTERRUPT_CONSENT_GRANTED"
        elif kind == "INTERRUPT":
            if target != self._active_speaker or target == user_id:
                raise SocialInteractionError(
                    "SOCIAL_INTERRUPT_TARGET_INVALID",
                    str(target),
                )
            consent = self._consents.get(str(consent_ref))
            if (
                consent is None
                or consent["grantor_user_id"] != target
                or consent["grantee_user_id"] != user_id
                or consent["turn_sequence"] != self._turn_sequence
                or consent["consumed"] is True
            ):
                raise SocialInteractionError(
                    "SOCIAL_INTERRUPT_CONSENT_INVALID",
                    str(consent_ref),
                )
            effect = "TURN_INTERRUPTED"
        elif kind == "YIELD":
            if self._active_speaker != user_id:
                raise SocialInteractionError(
                    "SOCIAL_YIELD_NOT_ACTIVE",
                    user_id,
                )
            effect = "TURN_YIELDED"
        elif kind == "LEAVE":
            effect = "PARTICIPANT_LEFT"
        else:
            raise SocialInteractionError("SOCIAL_EVENT_KIND_UNKNOWN", str(kind))

        try:
            self.identity_model.validate_scope_token(
                scope,
                audience="action_authorization",
                operation="action:evaluate_self",
                now=current,
                consume=True,
            )
        except MultiUserIdentityError as exc:
            raise SocialInteractionError(
                "SOCIAL_IDENTITY_TOKEN_REJECTED",
                exc.code,
            ) from exc

        if kind == "JOIN":
            self._presence[user_id] = "PRESENT"
        elif kind == "REJOIN":
            self._presence[user_id] = "PRESENT"
        elif kind == "SPEAK":
            if self._queue and self._queue[0] == user_id:
                self._queue.pop(0)
            self._active_speaker = user_id
            self._turn_sequence += 1
        elif kind == "WAIT":
            self._queue.append(user_id)
        elif kind == "CONSENT":
            self._consents[str(consent_ref)] = {
                "consent_id": consent_ref,
                "grantor_user_id": user_id,
                "grantee_user_id": target,
                "turn_sequence": self._turn_sequence,
                "consumed": False,
                "provenance": _copy(normalized["provenance"]),
            }
        elif kind == "INTERRUPT":
            previous = str(target)
            self._consents[str(consent_ref)]["consumed"] = True
            if user_id in self._queue:
                self._queue.remove(user_id)
            if previous not in self._queue:
                self._queue.append(previous)
            self._active_speaker = user_id
            self._turn_sequence += 1
        elif kind == "YIELD":
            self._active_speaker = None
            promoted = self._promote_next()
            if promoted is not None:
                effect = "TURN_TRANSFERRED"
        elif kind == "LEAVE":
            self._presence[user_id] = "LEFT"
            self._queue = [item for item in self._queue if item != user_id]
            self._consents = {
                key: item
                for key, item in self._consents.items()
                if item["grantor_user_id"] != user_id
                and item["grantee_user_id"] != user_id
            }
            if self._active_speaker == user_id:
                self._active_speaker = None
                self._promote_next()

        self._event_ids.add(normalized["event_id"])
        content_sha = (
            hashlib.sha256(normalized["content"].encode("utf-8")).hexdigest()
            if normalized["content"] is not None
            else None
        )
        decision = {
            "schema_version": SOCIAL_DECISION_SCHEMA,
            "policy_version": POLICY_VERSION,
            "decision": "ACCEPT",
            "effect": effect,
            "event_id": normalized["event_id"],
            "event_kind": kind,
            "event_sha256": _sha(normalized),
            "content_sha256": content_sha,
            "room_id": self.room_id,
            "tenant_id": self.tenant_id,
            "user_id": user_id,
            "session_id": normalized["session_id"],
            "token_id": scope["token_id"],
            "active_speaker_user_id": self._active_speaker,
            "queue_user_ids": _copy(self._queue),
            "presence": _copy(self._presence),
            "provenance": {
                **_copy(normalized["provenance"]),
                "identity_token_id": scope["token_id"],
                "identity_context_sha256": scope["context_sha256"],
            },
            "live_delivery_performed": False,
            "external_social_action_executed": False,
        }
        decision["state_sha256"] = self._state_sha256()
        decision["decision_sha256"] = _sha(decision)
        self._audit.append(_copy(decision))
        return decision

    def _state_body(self) -> dict[str, Any]:
        return {
            "schema_version": SOCIAL_STATE_SCHEMA,
            "policy_version": POLICY_VERSION,
            "room_id": self.room_id,
            "tenant_id": self.tenant_id,
            "participants": {
                user_id: {
                    "session_id": context["session_id"],
                    "role": context["role"],
                    "presence": self._presence[user_id],
                    "memory_partition_id": context["memory_scope"]["partition_id"],
                    "world_state_id": context["world_state_scope"]["state_id"],
                }
                for user_id, context in sorted(self._participants.items())
            },
            "active_speaker_user_id": self._active_speaker,
            "queue_user_ids": _copy(self._queue),
            "turn_sequence": self._turn_sequence,
            "consents": [
                {
                    "consent_id": item["consent_id"],
                    "grantor_user_id": item["grantor_user_id"],
                    "grantee_user_id": item["grantee_user_id"],
                    "turn_sequence": item["turn_sequence"],
                    "consumed": item["consumed"],
                    "provenance": _copy(item["provenance"]),
                }
                for item in sorted(
                    self._consents.values(),
                    key=lambda item: str(item["consent_id"]),
                )
            ],
            "accepted_event_count": len(self._audit),
            "external_social_actions_executed": 0,
        }

    def _state_sha256(self) -> str:
        return _sha(self._state_body())

    def snapshot(self) -> dict[str, Any]:
        body = self._state_body()
        body["state_sha256"] = _sha(body)
        return body

    def audit(self) -> list[dict[str, Any]]:
        return _copy(self._audit)


def _validate_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    fixture = _mapping(value, "SOCIAL_FIXTURE_OBJECT_REQUIRED", "fixture")
    fields = {
        "schema_version",
        "issuer",
        "generation",
        "participants",
        "protocol",
        "foreign_markers",
        "expectations",
    }
    _exact(
        fixture,
        fields,
        "SOCIAL_FIXTURE_FIELDS_INVALID",
        "fixture fields must match v1 exactly",
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA:
        raise SocialInteractionError(
            "SOCIAL_FIXTURE_SCHEMA_MISMATCH",
            "schema_version",
        )
    issuer = _mapping(fixture["issuer"], "SOCIAL_ISSUER_REQUIRED", "issuer")
    _exact(
        issuer,
        {"issuer_id", "reference_secret_id"},
        "SOCIAL_ISSUER_FIELDS_INVALID",
        "issuer",
    )
    _identifier(issuer["issuer_id"], "issuer_id")
    if issuer["reference_secret_id"] != "p6-01-local-test-key-v1":
        raise SocialInteractionError(
            "SOCIAL_REFERENCE_SECRET_ID_INVALID",
            "reference_secret_id",
        )
    generation = _mapping(
        fixture["generation"],
        "SOCIAL_GENERATION_REQUIRED",
        "generation",
    )
    _exact(
        generation,
        {"tenant_id", "scenario_count", "start_at"},
        "SOCIAL_GENERATION_FIELDS_INVALID",
        "generation",
    )
    _identifier(generation["tenant_id"], "generation.tenant_id")
    count = generation["scenario_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 100:
        raise SocialInteractionError(
            "SOCIAL_SCENARIO_COUNT_TOO_SMALL",
            "at least 100 scenarios are required",
        )
    _time(generation["start_at"], "generation.start_at")
    participants = fixture["participants"]
    if (
        not isinstance(participants, list)
        or len(participants) < 3
        or len(participants) != len(set(participants))
        or not all(isinstance(item, str) and _ID.fullmatch(item) for item in participants)
    ):
        raise SocialInteractionError(
            "SOCIAL_PARTICIPANTS_INVALID",
            "three unique participant labels are required",
        )
    protocol = _mapping(
        fixture["protocol"],
        "SOCIAL_PROTOCOL_CONFIG_REQUIRED",
        "protocol",
    )
    _exact(
        protocol,
        {"room_prefix", "token_ttl_seconds"},
        "SOCIAL_PROTOCOL_CONFIG_FIELDS_INVALID",
        "protocol",
    )
    _identifier(protocol["room_prefix"], "room_prefix")
    ttl = protocol["token_ttl_seconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 300:
        raise SocialInteractionError("SOCIAL_TOKEN_TTL_INVALID", str(ttl))
    markers = fixture["foreign_markers"]
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise SocialInteractionError(
            "SOCIAL_FOREIGN_MARKERS_INVALID",
            "foreign_markers",
        )
    expectations = _mapping(
        fixture["expectations"],
        "SOCIAL_EXPECTATIONS_REQUIRED",
        "expectations",
    )
    _exact(
        expectations,
        {"minimum_turn_taking_success_percent", "expected_final_active_speaker", "expected_final_queue"},
        "SOCIAL_EXPECTATIONS_FIELDS_INVALID",
        "expectations",
    )
    _canonical(fixture)
    normalized = _copy(dict(fixture))
    normalized["participants"] = sorted(normalized["participants"])
    return normalized


def _context(
    *,
    tenant_id: str,
    scenario_index: int,
    label: str,
    observed_at: datetime,
) -> dict[str, Any]:
    user_id = f"user:s{scenario_index:03d}:{label}"
    session_id = f"session:s{scenario_index:03d}:{label}"
    return create_identity_context(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        role="member",
        permissions=("memory:read_self", "world:read_self", "action:evaluate_self"),
        provenance={
            "source_id": f"source:social:{scenario_index:03d}:{label}",
            "correlation_id": f"corr:social:{scenario_index:03d}:{label}",
            "observed_at": _iso(observed_at),
            "authentication_method": "deterministic_fixture",
        },
    )


def _event(
    *,
    event_id: str,
    room_id: str,
    context: Mapping[str, Any],
    kind: str,
    requested_at: datetime,
    target_user_id: str | None = None,
    consent_ref: str | None = None,
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
        "target_user_id": target_user_id,
        "consent_ref": consent_ref,
        "content": content,
        "requested_at": _iso(requested_at),
        "provenance": _copy(context["provenance"]),
    }


def _issue(
    model: MultiUserIdentityModel,
    context: Mapping[str, Any],
    *,
    token_id: str,
    now: datetime,
    ttl_seconds: int,
) -> dict[str, Any]:
    return model.issue_scope_token(
        context,
        token_id=token_id,
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


def _raises(
    call: Any,
    code: str,
    detail: str | None = None,
) -> bool:
    try:
        call()
    except SocialInteractionError as exc:
        return exc.code == code and (detail is None or exc.detail == detail)
    return False


def _control_setup(
    *,
    issuer_id: str,
    tenant_id: str,
    now: datetime,
    active: bool = False,
) -> tuple[
    MultiUserIdentityModel,
    SocialInteractionProtocol,
    dict[str, dict[str, Any]],
    int,
]:
    model = MultiUserIdentityModel(issuer_id=issuer_id, secret=REFERENCE_TEST_SECRET)
    protocol = SocialInteractionProtocol(
        room_id="room:p6-02:control",
        tenant_id=tenant_id,
        identity_model=model,
    )
    contexts = {
        label: _context(
            tenant_id=tenant_id,
            scenario_index=999,
            label=label,
            observed_at=now - timedelta(seconds=10),
        )
        for label in ("alpha", "beta", "gamma")
    }
    offset = 0
    for label in ("alpha", "beta", "gamma"):
        event_time = now + timedelta(seconds=offset)
        token = _issue(
            model,
            contexts[label],
            token_id=f"token:control:join:{label}",
            now=event_time,
            ttl_seconds=60,
        )
        protocol.register_participant(contexts[label])
        protocol.process_event(
            token,
            _event(
                event_id=f"event:control:join:{label}",
                room_id=protocol.room_id,
                context=contexts[label],
                kind="JOIN",
                requested_at=event_time,
            ),
            now=event_time,
        )
        offset += 1
    if active:
        event_time = now + timedelta(seconds=offset)
        token = _issue(
            model,
            contexts["alpha"],
            token_id="token:control:speak:alpha",
            now=event_time,
            ttl_seconds=60,
        )
        protocol.process_event(
            token,
            _event(
                event_id="event:control:speak:alpha",
                room_id=protocol.room_id,
                context=contexts["alpha"],
                kind="SPEAK",
                requested_at=event_time,
                content="control utterance",
            ),
            now=event_time,
        )
        offset += 1
    return model, protocol, contexts, offset


def evaluate_social_fixture(value: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate 100+ deterministic social turn-taking scenarios."""

    fixture = _validate_fixture(value)
    issuer_id = fixture["issuer"]["issuer_id"]
    generation = fixture["generation"]
    tenant_id = generation["tenant_id"]
    start = _time(generation["start_at"], "generation.start_at")
    labels = fixture["participants"][:3]
    ttl = fixture["protocol"]["token_ttl_seconds"]

    scenario_results: list[dict[str, Any]] = []
    attack_case_ids: list[str] = []
    accepted_events = 0
    turn_checks_total = 0
    turn_checks_passed = 0
    scenario_passed = 0
    provenance_total = 0
    provenance_passed = 0
    cross_user_attempts = 0
    cross_user_rejected = 0
    cross_user_authorized = 0
    leakage_count = 0
    unauthorized_interaction_count = 0
    event_kind_counts = {kind: 0 for kind in EVENT_KINDS}
    accepted_plaintext_retention_count = 0

    for scenario_index in range(generation["scenario_count"]):
        scenario_id = f"scenario:p6-02:{scenario_index:03d}"
        room_id = f"{fixture['protocol']['room_prefix']}:{scenario_index:03d}"
        scenario_start = start + timedelta(minutes=scenario_index)
        model = MultiUserIdentityModel(
            issuer_id=issuer_id,
            secret=REFERENCE_TEST_SECRET,
        )
        protocol = SocialInteractionProtocol(
            room_id=room_id,
            tenant_id=tenant_id,
            identity_model=model,
        )
        contexts = {
            label: _context(
                tenant_id=tenant_id,
                scenario_index=scenario_index,
                label=label,
                observed_at=scenario_start,
            )
            for label in labels
        }
        for label in labels:
            protocol.register_participant(contexts[label])

        alpha, beta, gamma = labels
        consent_id = f"consent:p6-02:{scenario_index:03d}:{alpha}:{gamma}"
        sequence = [
            (alpha, "JOIN", None, None, None, "PARTICIPANT_JOINED", None, False),
            (beta, "JOIN", None, None, None, "PARTICIPANT_JOINED", None, False),
            (gamma, "JOIN", None, None, None, "PARTICIPANT_JOINED", None, False),
            (alpha, "SPEAK", None, None, f"utterance-{scenario_index:03d}-alpha", "TURN_GRANTED", contexts[alpha]["user_id"], True),
            (beta, "WAIT", None, None, None, "PARTICIPANT_QUEUED", contexts[alpha]["user_id"], True),
            (alpha, "CONSENT", contexts[gamma]["user_id"], consent_id, None, "INTERRUPT_CONSENT_GRANTED", contexts[alpha]["user_id"], True),
            (gamma, "INTERRUPT", contexts[alpha]["user_id"], consent_id, f"interrupt-{scenario_index:03d}-gamma", "TURN_INTERRUPTED", contexts[gamma]["user_id"], True),
            (gamma, "YIELD", None, None, None, "TURN_TRANSFERRED", contexts[beta]["user_id"], True),
            (alpha, "LEAVE", None, None, None, "PARTICIPANT_LEFT", contexts[beta]["user_id"], True),
            (alpha, "REJOIN", None, None, None, "PARTICIPANT_REJOINED", contexts[beta]["user_id"], True),
            (beta, "YIELD", None, None, None, "TURN_YIELDED", None, True),
            (alpha, "SPEAK", None, None, f"return-{scenario_index:03d}-alpha", "TURN_GRANTED", contexts[alpha]["user_id"], True),
            (alpha, "YIELD", None, None, None, "TURN_YIELDED", None, True),
        ]
        decisions: list[dict[str, Any]] = []
        for offset, (
            label,
            kind,
            target,
            consent_ref,
            content,
            expected_effect,
            expected_active,
            is_turn_check,
        ) in enumerate(sequence, start=1):
            event_time = scenario_start + timedelta(seconds=offset)
            context = contexts[label]
            token = _issue(
                model,
                context,
                token_id=f"token:p6-02:{scenario_index:03d}:{offset:02d}",
                now=event_time,
                ttl_seconds=ttl,
            )
            provenance_total += 1
            provenance_passed += int(
                _provenance_complete(token["provenance"])
                and bool(token["context_sha256"])
                and bool(token["token_hmac_sha256"])
            )
            decision = protocol.process_event(
                token,
                _event(
                    event_id=f"event:p6-02:{scenario_index:03d}:{offset:02d}:{kind.lower()}",
                    room_id=room_id,
                    context=context,
                    kind=kind,
                    requested_at=event_time,
                    target_user_id=target,
                    consent_ref=consent_ref,
                    content=content,
                ),
                now=event_time,
            )
            decisions.append(decision)
            accepted_events += 1
            event_kind_counts[kind] += 1
            if content is not None:
                accepted_plaintext_retention_count += json.dumps(
                    decision,
                    ensure_ascii=False,
                    sort_keys=True,
                ).count(content)
            provenance_total += 1
            provenance_passed += int(
                _provenance_complete(decision["provenance"])
                and bool(decision["provenance"]["identity_token_id"])
                and bool(decision["provenance"]["identity_context_sha256"])
                and bool(decision["decision_sha256"])
            )
            if is_turn_check:
                turn_checks_total += 1
                turn_checks_passed += int(
                    decision["effect"] == expected_effect
                    and decision["active_speaker_user_id"] == expected_active
                    and decision["external_social_action_executed"] is False
                )

        attack_time = scenario_start + timedelta(seconds=30)
        attacker_context = contexts[alpha]
        victim_context = contexts[beta]
        attack_token = _issue(
            model,
            attacker_context,
            token_id=f"token:p6-02:attack:{scenario_index:03d}",
            now=attack_time,
            ttl_seconds=ttl,
        )
        provenance_total += 1
        provenance_passed += int(
            _provenance_complete(attack_token["provenance"])
            and bool(attack_token["context_sha256"])
            and bool(attack_token["token_hmac_sha256"])
        )
        attack_event = _event(
            event_id=f"event:p6-02:attack:{scenario_index:03d}",
            room_id=room_id,
            context=victim_context,
            kind="SPEAK",
            requested_at=attack_time,
            content=f"FOREIGN_SOCIAL_SECRET_91-{scenario_index:03d}",
        )
        attack_case_ids.append(attack_event["event_id"])
        cross_user_attempts += 1
        try:
            attack_decision = protocol.process_event(
                attack_token,
                attack_event,
                now=attack_time,
            )
        except SocialInteractionError as exc:
            cross_user_rejected += int(exc.code == "SOCIAL_ACTOR_TOKEN_MISMATCH")
        else:
            cross_user_authorized += 1
            unauthorized_interaction_count += int(attack_decision["decision"] == "ACCEPT")
            leakage_count += json.dumps(
                attack_decision,
                ensure_ascii=False,
                sort_keys=True,
            ).count("FOREIGN_SOCIAL_SECRET_91")

        snapshot = protocol.snapshot()
        serialized = json.dumps(
            {"decisions": decisions, "snapshot": snapshot},
            ensure_ascii=False,
            sort_keys=True,
        )
        leakage_count += sum(
            serialized.count(marker)
            for marker in fixture["foreign_markers"]
        )
        final_ok = (
            snapshot["active_speaker_user_id"]
            == fixture["expectations"]["expected_final_active_speaker"]
            and snapshot["queue_user_ids"]
            == fixture["expectations"]["expected_final_queue"]
            and all(
                participant["presence"] == "PRESENT"
                for participant in snapshot["participants"].values()
            )
            and len(decisions) == len(sequence)
        )
        scenario_passed += int(final_ok)
        scenario_results.append({
            "scenario_id": scenario_id,
            "accepted_event_count": len(decisions),
            "turn_check_count": sum(item[-1] for item in sequence),
            "cross_user_attack_rejected": cross_user_rejected == scenario_index + 1,
            "final_state_sha256": snapshot["state_sha256"],
            "passed": final_ok,
        })

    control_now = start + timedelta(days=1)

    tamper_model, tamper_protocol, tamper_contexts, tamper_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    tamper_time = control_now + timedelta(seconds=tamper_offset)
    tamper_token = _issue(
        tamper_model,
        tamper_contexts["alpha"],
        token_id="token:negative:tamper",
        now=tamper_time,
        ttl_seconds=ttl,
    )
    tamper_token["user_id"] = tamper_contexts["beta"]["user_id"]

    replay_model, replay_protocol, replay_contexts, replay_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    replay_time = control_now + timedelta(seconds=replay_offset)
    replay_token = _issue(
        replay_model,
        replay_contexts["alpha"],
        token_id="token:negative:replay",
        now=replay_time,
        ttl_seconds=ttl,
    )
    replay_event = _event(
        event_id="event:negative:replay:first",
        room_id=replay_protocol.room_id,
        context=replay_contexts["alpha"],
        kind="SPEAK",
        requested_at=replay_time,
        content="first replay control",
    )
    replay_protocol.process_event(replay_token, replay_event, now=replay_time)
    replay_second = _copy(replay_event)
    replay_second["event_id"] = "event:negative:replay:second"

    active_model, active_protocol, active_contexts, active_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
        active=True,
    )
    active_time = control_now + timedelta(seconds=active_offset)

    def active_token(label: str, suffix: str) -> dict[str, Any]:
        return _issue(
            active_model,
            active_contexts[label],
            token_id=f"token:negative:{suffix}",
            now=active_time,
            ttl_seconds=ttl,
        )

    no_consent_event = _event(
        event_id="event:negative:no-consent",
        room_id=active_protocol.room_id,
        context=active_contexts["beta"],
        kind="INTERRUPT",
        requested_at=active_time,
        target_user_id=active_contexts["alpha"]["user_id"],
        consent_ref="consent:missing",
        content="unconsented interrupt",
    )
    out_of_turn_event = _event(
        event_id="event:negative:out-of-turn",
        room_id=active_protocol.room_id,
        context=active_contexts["beta"],
        kind="SPEAK",
        requested_at=active_time,
        content="out of turn",
    )
    provenance_event = _event(
        event_id="event:negative:provenance",
        room_id=active_protocol.room_id,
        context=active_contexts["beta"],
        kind="WAIT",
        requested_at=active_time,
    )
    provenance_event["provenance"]["correlation_id"] = "corr:forged"

    cross_model, cross_protocol, cross_contexts, cross_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    cross_time = control_now + timedelta(seconds=cross_offset)
    cross_token = _issue(
        cross_model,
        cross_contexts["alpha"],
        token_id="token:negative:cross-user",
        now=cross_time,
        ttl_seconds=ttl,
    )
    cross_event = _event(
        event_id="event:negative:cross-user",
        room_id=cross_protocol.room_id,
        context=cross_contexts["beta"],
        kind="SPEAK",
        requested_at=cross_time,
        content="impersonation",
    )
    session_event = _copy(_event(
        event_id="event:negative:session",
        room_id=cross_protocol.room_id,
        context=cross_contexts["alpha"],
        kind="SPEAK",
        requested_at=cross_time,
        content="session swap",
    ))
    session_event["session_id"] = cross_contexts["beta"]["session_id"]
    tenant_event = _copy(_event(
        event_id="event:negative:tenant",
        room_id=cross_protocol.room_id,
        context=cross_contexts["alpha"],
        kind="SPEAK",
        requested_at=cross_time,
        content="tenant swap",
    ))
    tenant_event["tenant_id"] = "tenant:other"

    unknown_model, unknown_protocol, unknown_contexts, unknown_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    unknown_time = control_now + timedelta(seconds=unknown_offset)
    unknown_token = _issue(
        unknown_model,
        unknown_contexts["alpha"],
        token_id="token:negative:unknown",
        now=unknown_time,
        ttl_seconds=ttl,
    )
    unknown_event = _event(
        event_id="event:negative:unknown",
        room_id=unknown_protocol.room_id,
        context=unknown_contexts["alpha"],
        kind="SPEAK",
        requested_at=unknown_time,
        content="unknown field",
    )
    unknown_event["unexpected"] = True

    expired_model, expired_protocol, expired_contexts, expired_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    expired_time = control_now + timedelta(seconds=expired_offset)
    expired_token = expired_model.issue_scope_token(
        expired_contexts["alpha"],
        token_id="token:negative:expired",
        audience="action_authorization",
        operation="action:evaluate_self",
        issued_at=expired_time - timedelta(seconds=5),
        expires_at=expired_time - timedelta(seconds=1),
    )
    expired_event = _event(
        event_id="event:negative:expired",
        room_id=expired_protocol.room_id,
        context=expired_contexts["alpha"],
        kind="SPEAK",
        requested_at=expired_time,
        content="expired token",
    )

    rejoin_model, rejoin_protocol, rejoin_contexts, rejoin_offset = _control_setup(
        issuer_id=issuer_id,
        tenant_id=tenant_id,
        now=control_now,
    )
    rejoin_time = control_now + timedelta(seconds=rejoin_offset)
    rejoin_token = _issue(
        rejoin_model,
        rejoin_contexts["alpha"],
        token_id="token:negative:rejoin",
        now=rejoin_time,
        ttl_seconds=ttl,
    )
    rejoin_event = _event(
        event_id="event:negative:rejoin",
        room_id=rejoin_protocol.room_id,
        context=rejoin_contexts["alpha"],
        kind="REJOIN",
        requested_at=rejoin_time,
    )

    negative_controls = {
        "identity_token_hmac_tamper_rejected": _raises(
            lambda: tamper_protocol.process_event(
                tamper_token,
                _event(
                    event_id="event:negative:tamper",
                    room_id=tamper_protocol.room_id,
                    context=tamper_contexts["alpha"],
                    kind="SPEAK",
                    requested_at=tamper_time,
                    content="tamper",
                ),
                now=tamper_time,
            ),
            "SOCIAL_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_TAMPERED",
        ),
        "identity_token_replay_rejected": _raises(
            lambda: replay_protocol.process_event(
                replay_token,
                replay_second,
                now=replay_time,
            ),
            "SOCIAL_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_REPLAYED",
        ),
        "identity_token_expiry_rejected": _raises(
            lambda: expired_protocol.process_event(
                expired_token,
                expired_event,
                now=expired_time,
            ),
            "SOCIAL_IDENTITY_TOKEN_REJECTED",
            "IDENTITY_TOKEN_EXPIRED",
        ),
        "cross_user_impersonation_rejected": _raises(
            lambda: cross_protocol.process_event(
                cross_token,
                cross_event,
                now=cross_time,
            ),
            "SOCIAL_ACTOR_TOKEN_MISMATCH",
        ),
        "cross_session_impersonation_rejected": _raises(
            lambda: cross_protocol.process_event(
                cross_token,
                session_event,
                now=cross_time,
            ),
            "SOCIAL_SESSION_TOKEN_MISMATCH",
        ),
        "cross_tenant_impersonation_rejected": _raises(
            lambda: cross_protocol.process_event(
                cross_token,
                tenant_event,
                now=cross_time,
            ),
            "SOCIAL_TENANT_MISMATCH",
        ),
        "provenance_swap_rejected": _raises(
            lambda: active_protocol.process_event(
                active_token("beta", "provenance"),
                provenance_event,
                now=active_time,
            ),
            "SOCIAL_PROVENANCE_TOKEN_MISMATCH",
        ),
        "interrupt_without_consent_rejected": _raises(
            lambda: active_protocol.process_event(
                active_token("beta", "no-consent"),
                no_consent_event,
                now=active_time,
            ),
            "SOCIAL_INTERRUPT_CONSENT_INVALID",
        ),
        "speak_out_of_turn_rejected": _raises(
            lambda: active_protocol.process_event(
                active_token("beta", "out-of-turn"),
                out_of_turn_event,
                now=active_time,
            ),
            "SOCIAL_TURN_NOT_AVAILABLE",
        ),
        "unknown_event_field_rejected": _raises(
            lambda: unknown_protocol.process_event(
                unknown_token,
                unknown_event,
                now=unknown_time,
            ),
            "SOCIAL_EVENT_FIELDS_INVALID",
        ),
        "rejoin_without_leave_rejected": _raises(
            lambda: rejoin_protocol.process_event(
                rejoin_token,
                rejoin_event,
                now=rejoin_time,
            ),
            "SOCIAL_REJOIN_STATE_INVALID",
        ),
        "plaintext_not_retained_in_accepted_decisions": (
            accepted_plaintext_retention_count == 0
        ),
    }

    metrics = {
        "scenario_count": generation["scenario_count"],
        "scenarios_passed": scenario_passed,
        "scenario_success_percent": 100.0 * scenario_passed / generation["scenario_count"],
        "accepted_social_events": accepted_events,
        "accepted_events_by_kind": event_kind_counts,
        "accepted_plaintext_retention_count": accepted_plaintext_retention_count,
        "turn_taking_checks_passed": turn_checks_passed,
        "turn_taking_checks_total": turn_checks_total,
        "turn_taking_success_percent": 100.0 * turn_checks_passed / turn_checks_total,
        "cross_user_attack_attempts": cross_user_attempts,
        "cross_user_attacks_rejected": cross_user_rejected,
        "cross_user_rejection_percent": 100.0 * cross_user_rejected / cross_user_attempts,
        "cross_user_authorized_count": cross_user_authorized,
        "cross_user_leakage_count": leakage_count,
        "unauthorized_interaction_count": unauthorized_interaction_count,
        "provenance_checks_passed": provenance_passed,
        "provenance_checks_total": provenance_total,
        "provenance_completeness_percent": 100.0 * provenance_passed / provenance_total,
        "negative_controls_passed": sum(negative_controls.values()),
        "negative_controls_total": len(negative_controls),
        "negative_controls_percent": 100.0 * sum(negative_controls.values()) / len(negative_controls),
        "external_social_actions_executed": 0,
    }
    local_candidate = (
        metrics["scenario_count"] >= 100
        and metrics["turn_taking_success_percent"]
        >= fixture["expectations"]["minimum_turn_taking_success_percent"]
        and metrics["cross_user_rejection_percent"] == 100.0
        and metrics["cross_user_authorized_count"] == 0
        and metrics["cross_user_leakage_count"] == 0
        and metrics["unauthorized_interaction_count"] == 0
        and metrics["provenance_completeness_percent"] == 100.0
        and metrics["negative_controls_percent"] == 100.0
        and metrics["external_social_actions_executed"] == 0
    )
    output = {
        "schema_version": EVALUATION_SCHEMA,
        "policy_version": POLICY_VERSION,
        "fixture_sha256": _sha(fixture),
        "dependencies": {
            "p6_01_identity_model": MultiUserIdentityModel.__name__,
            "p6_01_token_audience": "action_authorization",
            "p6_01_token_operation": "action:evaluate_self",
            "p4_08_status": "NOT_APPROVED",
            "g6_status": "NOT_APPROVED",
        },
        "scenario_results": scenario_results,
        "attack_matrix": {
            "attack_count": cross_user_attempts,
            "case_ids_sha256": _sha(attack_case_ids),
            "first_case_id": attack_case_ids[0],
            "last_case_id": attack_case_ids[-1],
        },
        "negative_controls": negative_controls,
        "metrics": metrics,
        "wbs_promotion": {
            "p6_02_local_mechanical_candidate": local_candidate,
            "formal_wbs_promotion_allowed": False,
            "p4_08_approved": False,
            "g6_approved": False,
            "godot_social_integration_complete": False,
            "live_social_action_complete": False,
            "human_social_evaluation_complete": False,
        },
        "claims": {
            "gate_claim": "NONE",
            "p4_08_claim": "NONE",
            "g6_claim": "NONE",
            "godot_claim": "NONE",
            "live_social_action_claim": "NONE",
        },
    }
    output["evaluation_sha256"] = _sha(output)
    return output


def validate_social_evaluation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(value, "SOCIAL_EVALUATION_OBJECT_REQUIRED", "evaluation")
    if result.get("schema_version") != EVALUATION_SCHEMA:
        raise SocialInteractionError(
            "SOCIAL_EVALUATION_SCHEMA_MISMATCH",
            "schema_version",
        )
    expected = _sha({
        key: _copy(item)
        for key, item in result.items()
        if key != "evaluation_sha256"
    })
    if result.get("evaluation_sha256") != expected:
        raise SocialInteractionError(
            "SOCIAL_EVALUATION_TAMPERED",
            "evaluation_sha256",
        )
    promotion = result.get("wbs_promotion", {})
    if (
        promotion.get("formal_wbs_promotion_allowed") is not False
        or promotion.get("p4_08_approved") is not False
        or promotion.get("g6_approved") is not False
    ):
        raise SocialInteractionError(
            "SOCIAL_PROMOTION_OVERCLAIM",
            "P4-08/G6 are not approved",
        )
    if result.get("metrics", {}).get("external_social_actions_executed") != 0:
        raise SocialInteractionError(
            "SOCIAL_EXTERNAL_ACTION_OVERCLAIM",
            "external action prohibited",
        )
    return _copy(dict(result))


__all__ = [
    "EVALUATION_SCHEMA",
    "EVENT_KINDS",
    "FIXTURE_SCHEMA",
    "POLICY_VERSION",
    "SOCIAL_DECISION_SCHEMA",
    "SOCIAL_EVENT_SCHEMA",
    "SOCIAL_STATE_SCHEMA",
    "SocialInteractionError",
    "SocialInteractionProtocol",
    "evaluate_social_fixture",
    "validate_social_evaluation",
]
