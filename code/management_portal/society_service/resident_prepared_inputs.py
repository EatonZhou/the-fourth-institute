"""Immutable intent for a Resident input that has yet to be delivered."""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_common.async_questions import ANSWER_PART, answered_set, approved_credentials, question_set
from management_common.portal_database import _database_schema
from management_common.principal import Principal, actor_principal
from management_portal.permission_system import inline

from . import invariant_locks, resident_message_contract
from .resident_message_contract import MessageIdentityConflict


@dataclass(frozen=True)
class Intent:
    resident_id: str
    issuer: str
    message_id: str
    canonical: str
    approval: Approval | None = None


@dataclass(frozen=True)
class Approval:
    route: tuple[str, ...]
    credentials: tuple[tuple[str, str], ...]

    @property
    def recipient(self) -> str:
        return self.route[-1]

    def next_hop(self, resident_id: str) -> str:
        if resident_id not in self.route[:-1]:
            return ""
        return self.route[self.route.index(resident_id) + 1]

    def stored(self) -> str:
        return json.dumps([self.route, self.credentials], separators=(",", ":"))


_RELEASE: ContextVar[Intent | None] = ContextVar("prepared_resident_input_release", default=None)


def ensure_schema(connection: Any) -> None:
    exists = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.resident_prepared_inputs') IS NOT NULL AS present"
    ).fetchone()["present"]
    if not exists:
        connection.execute("""
            CREATE TABLE resident_prepared_inputs (
                message_id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                issuer TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                approval_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('prepared', 'released', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                settled_at TIMESTAMPTZ
            )
        """)
    elif not connection.execute("""
        SELECT 1 FROM pg_attribute WHERE attrelid='resident_prepared_inputs'::regclass
        AND attname='approval_json' AND NOT attisdropped
    """).fetchone():
        connection.execute("ALTER TABLE resident_prepared_inputs ADD COLUMN IF NOT EXISTS approval_json TEXT NOT NULL DEFAULT ''")


def _database():
    from .state_store import get_lineage_data_store

    return get_lineage_data_store().database


def _row(connection: Any, message_id: str) -> Mapping[str, Any] | None:
    schema = _database_schema("lineage")
    return connection.execute(
        f'SELECT resident_id, issuer, intent_json, approval_json, status FROM "{schema}".resident_prepared_inputs WHERE message_id=%s',
        (message_id,),
    ).fetchone()


def _stored_approval(row: Mapping[str, Any]) -> Approval | None:
    if not row["approval_json"]:
        return None
    route, credentials = json.loads(row["approval_json"])
    return Approval(tuple(route), tuple(map(tuple, credentials)))


def approval_for(resident_id: str, message: Mapping[str, Any] | None, *, validate_answer=False) -> Approval | None:
    """Read the permission request from the persisted question and its current projection."""
    source_id = answered_set(message)
    if not source_id:
        return None
    from . import family_facade, resident_conversation_stream, resident_record_domain
    from .resident_questions import prepare_question_answer

    repository = resident_conversation_stream.get_repository()
    source = repository.message(resident_id, source_id)
    record = resident_record_domain.find_resident_for_admission(resident_id)
    facade = record if record and family_facade.is_facade(record) else None
    route = (resident_id,)
    if source is None and facade is not None:
        projected = family_facade.relayed_reply(facade, source_id)
        if projected is not None:
            source = projected["reply"]
            route = (resident_id, *projected["via"], projected["worker_id"])
            if len(route) != len(set(route)) or route[1] != projected["member_id"]:
                raise ValueError("原问题的提问成员已不可用，请重新加载问题")
    questions = question_set(source)
    if questions is None:
        raise ValueError("该 Resident 中的问题不存在或已不可用")
    part = next(part for part in message["content_parts"] if part.get("type") == ANSWER_PART)
    permissions = approved_credentials(questions["questions"], part.get("answers"))
    if not permissions:
        return None
    if validate_answer:
        prepare_question_answer(resident_id, message, repository, facade=facade)
    return Approval(route, tuple(sorted((item["a"], item["resource_id"]) for item in permissions)))


def releasing_approval(message_id: str) -> Approval | None:
    intent = _RELEASE.get()
    return intent.approval if intent is not None and intent.message_id == message_id else None


def _intent(
    resident_id: str, issuer: str, message: Mapping[str, Any], *, context_policy: str | None,
    execution_constraints: Mapping[str, Any] | None, business_ref: str,
) -> Intent:
    resident_id = Principal("resident", resident_id).id
    author = actor_principal(canonical_actor_id(issuer))
    if author.kind not in {"user", "resident"}:
        raise PermissionError("只有已认证的 User 或 Resident 可以预备输入")
    issuer = author.id
    resident_message_contract.validate_delivery_message(message)
    if str(message.get("role") or "user") != "user":
        raise ValueError("message.role 只能是 user")
    fixed_id = str(message.get("message_id") or "").strip()
    if not fixed_id:
        raise ValueError("准备输入需要固定的 message.message_id")
    message_id = resident_message_contract.message_identity(fixed_id)
    body = {**message, "message_id": message_id, "role": "user"}
    context = context_policy or "session"
    if context not in {"session", "fresh"}:
        raise ValueError("context_policy 必须是 session 或 fresh")
    if execution_constraints is not None:
        if (not isinstance(execution_constraints, Mapping) or context != "fresh"
                or dict(execution_constraints) != {"version": 1, "text_only": True, "context": "fresh"}
                or type(execution_constraints.get("version")) is not int
                or execution_constraints.get("text_only") is not True
                or not body.get("content_parts")
                or any(part.get("type") != "text" for part in body["content_parts"])):
            raise ValueError("execution_constraints 要求 fresh 上下文和明确的 v1 纯文本输入")
    canonical = json.dumps({
        "message": body, "context_policy": context,
        "execution_constraints": execution_constraints, "business_ref": business_ref or "",
    }, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return Intent(resident_id, issuer, message_id, canonical)


def _require_identical(row: Mapping[str, Any], intent: Intent) -> None:
    if (row["resident_id"], row["issuer"], row["intent_json"]) != (
        intent.resident_id, intent.issuer, intent.canonical,
    ):
        raise MessageIdentityConflict("该消息 ID 已绑定其他准备输入")


def _has_occurrence(connection: Any, message_id: str) -> bool:
    if connection.execute("""
        SELECT 1 FROM resident_mailbox WHERE message_id=%s
        UNION ALL SELECT 1 FROM conversation_input_states WHERE message_id=%s LIMIT 1
    """, (message_id, message_id)).fetchone() is not None:
        return True
    from management_portal.execution.conversation_projection import CHAT_PREDICATE

    schema = _database_schema("agent")
    if connection.execute("SELECT to_regclass(%s) AS table_name", (f'"{schema}".segments',)).fetchone()["table_name"] is None:
        return False
    return connection.execute(f"""
        SELECT 1 FROM "{schema}".segments WHERE {CHAT_PREDICATE}
        AND metadata_json::jsonb #>> '{{message,message_id}}'=%s LIMIT 1
    """, (message_id,)).fetchone() is not None


def _receipt(resident_id: str, message_id: str, status: str, approval: Approval | None = None) -> dict[str, str]:
    result = {"resident_id": resident_id, "message_id": message_id, "status": status}
    if approval is not None:
        result.update(credential_recipient_id=approval.recipient, credential_message_id=message_id)
    return result


def prepare(
    resident_id: str, issuer: str, message: Mapping[str, Any], *, context_policy: str | None = None,
    execution_constraints: Mapping[str, Any] | None = None, business_ref: str = "",
) -> dict[str, str]:
    """Reserve a globally exclusive message ID, without admitting any work."""
    from .resident_intake_domain import preflight_resident_input

    intent = _intent(resident_id, issuer, message, context_policy=context_policy,
                     execution_constraints=execution_constraints, business_ref=business_ref)
    content, *_ = resident_message_contract.input_parts(message)
    if content.lstrip().startswith("/"):
        raise ValueError("即时命令没有可预备的输入轮次")
    with _database().read() as connection:
        row = _row(connection, intent.message_id)
    if row is not None:
        _require_identical(row, intent)
        return _receipt(intent.resident_id, intent.message_id, row["status"], _stored_approval(row))
    # Preflight may read another domain. It never holds the permission lock.
    preflight_resident_input(intent.resident_id, intent.issuer, message, business_ref=business_ref)
    with _database().transaction() as connection:
        inline.lock(connection, intent.message_id)
        row = _row(connection, intent.message_id)
        if row is not None:
            _require_identical(row, intent)
            return _receipt(intent.resident_id, intent.message_id, row["status"], _stored_approval(row))
        if _has_occurrence(connection, intent.message_id):
            raise MessageIdentityConflict("该消息 ID 已有输入记录")
        approval = approval_for(intent.resident_id, message, validate_answer=True)
        if approval is None and answered_set(message):
            raise ValueError("没有批准权限的回答可直接发送，无需预备")
        if approval is not None and len(approval.route) > 1 and context_policy == "fresh":
            raise ValueError("门面问题回答需要原会话上下文")
        connection.execute("""
            INSERT INTO resident_prepared_inputs(message_id, resident_id, issuer, intent_json, approval_json, status)
            VALUES (%s, %s, %s, %s, %s, 'prepared')
        """, (intent.message_id, intent.resident_id, intent.issuer, intent.canonical, approval.stored() if approval else ""))
    return _receipt(intent.resident_id, intent.message_id, "prepared", approval)


def cancel(resident_id: str, issuer: str, message_id: str) -> dict[str, str] | None:
    """Close the issuer's unused preparation, or leave ordinary input cancellation in charge."""
    from management_portal.permission_system import end_task_credentials

    with _database().transaction() as connection:
        inline.lock(connection, message_id)
        row = _row(connection, message_id)
        if row is None or row["resident_id"] != resident_id or row["issuer"] != issuer or row["status"] == "released":
            return None
        if row["status"] == "prepared":
            connection.execute("""
                UPDATE resident_prepared_inputs SET status='cancelled', settled_at=now() WHERE message_id=%s
            """, (message_id,))
            approval = _stored_approval(row)
            end_task_credentials(connection, approval.recipient if approval else resident_id, message_id)
    return _receipt(resident_id, message_id, "cancelled")


@contextmanager
def release(
    resident_id: str, issuer: str, message: Mapping[str, Any], *, activation_policy: str = "enqueue_and_trigger",
    context_policy: str | None = None, execution_constraints: Mapping[str, Any] | None = None, business_ref: str = "",
) -> Iterator[bool]:
    """Bind a caller's checked intent through delivery, without holding locks during preparation."""
    message_id = str(message.get("message_id") or "").strip()
    with _database().read() as connection:
        row = _row(connection, message_id) if message_id else None
    if row is None:
        yield False
        return
    if activation_policy != "enqueue_and_trigger" or row["status"] == "cancelled":
        raise MessageIdentityConflict("该消息 ID 不能用于本次投递")
    intent = _intent(resident_id, issuer, message, context_policy=context_policy,
                     execution_constraints=execution_constraints, business_ref=business_ref)
    _require_identical(row, intent)
    token = _RELEASE.set(replace(intent, approval=_stored_approval(row)))
    try:
        yield True
    finally:
        _RELEASE.reset(token)


def _check(
    row: Mapping[str, Any] | None, resident_id: str, message_id: str, issuer: str, *,
    activation_policy: str, relay_source: tuple[str, str] | None,
) -> bool:
    if row is None:
        return False
    approval = _stored_approval(row)
    if approval is None and row["status"] == "released" and relay_source == (row["resident_id"], message_id):
        # A trusted relay already verified the unchanged accepted source.
        return False
    intent = _RELEASE.get()
    if (approval is not None and intent is not None and (intent.message_id, intent.issuer) == (message_id, issuer)
            and intent.approval == approval and relay_source is not None and relay_source[1] == message_id
            and approval.next_hop(relay_source[0]) == resident_id and activation_policy == "enqueue_and_trigger"
            and row["status"] in {"prepared", "released"}):
        return False
    if (intent is None or (intent.resident_id, intent.message_id, intent.issuer) != (resident_id, message_id, issuer)
            or activation_policy != "enqueue_and_trigger" or row["status"] == "cancelled"):
        raise MessageIdentityConflict("该消息 ID 已绑定准备输入")
    _require_identical(row, intent)
    return row["status"] == "prepared"


def check_authored_input(resident_id: str, message_id: str, issuer: str, message: Mapping[str, Any] | None,
                         *, business_ref: str, automatic_quote: bool) -> None:
    """Match the actual intake envelope before it resolves quotes or question answers."""
    intent = _RELEASE.get()
    if intent is None or (resident_id, message_id, issuer) != (intent.resident_id, intent.message_id, intent.issuer):
        return
    expected = json.loads(intent.canonical)
    body = {**(message or {}), "message_id": message_id, "role": "user"}
    if automatic_quote and "quote" not in expected["message"]:
        body.pop("quote", None)
    actual = _intent(resident_id, issuer, body, context_policy=expected["context_policy"],
                     execution_constraints=expected["execution_constraints"], business_ref=business_ref)
    if actual.canonical != intent.canonical:
        raise MessageIdentityConflict("实际投递内容与准备输入不符")


def check_admission(resident_id: str, message_id: str, issuer: str, *, activation_policy: str,
                    relay_source: tuple[str, str] | None = None) -> None:
    with _database().read() as connection:
        _check(_row(connection, message_id), resident_id, message_id, issuer,
               activation_policy=activation_policy, relay_source=relay_source)


@contextmanager
def _admission_locks(resident_id: str, message_id: str) -> Iterator[None]:
    approval = releasing_approval(message_id)
    with ExitStack() as stack:
        if approval is not None and approval.route[0] == resident_id and len(approval.route) > 1:
            # A question close locks the worker's Conversation before ending
            # credentials. Take all route locks before the same message lock.
            for target in approval.route:
                stack.enter_context(invariant_locks.resident_lock(target))
            for target in approval.route:
                stack.enter_context(invariant_locks.resident_conversation_lock(target))
        else:
            stack.enter_context(invariant_locks.resident_conversation_lock(resident_id))
        yield


@contextmanager
def accepting(resident_id: str, message_id: str, issuer: str, *, activation_policy: str,
              relay_source: tuple[str, str] | None = None) -> Iterator[Any]:
    """Commit acceptance under the same lock as prepare, cancel and task credentials."""
    from management_portal.permission_system import activate_prepared_task_credentials, end_task_credentials

    with _admission_locks(resident_id, message_id), _database().transaction() as connection:
        inline.lock(connection, message_id)
        row = _row(connection, message_id)
        initial = _check(row, resident_id, message_id, issuer,
                         activation_policy=activation_policy, relay_source=relay_source)
        approval = _stored_approval(row) if initial else None
        if initial and approval_for(resident_id, json.loads(row["intent_json"])["message"], validate_answer=True) != approval:
            raise MessageIdentityConflict("原问题的受领者或批准集合已变化，请重新加载问题")
        yield connection
        if initial:
            recipient = approval.recipient if approval else resident_id
            accepted = connection.execute("""
                SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s
            """, (recipient, message_id)).fetchone()
            if accepted is None:
                raise RuntimeError("Prepared delivery must durably accept its input before release")
            if approval is not None:
                if accepted["state"] not in {"queued", "running"}:
                    raise ValueError("提问成员未接收授权回答")
                from management_portal.permission_system import activate_approved_task_credentials

                activate_approved_task_credentials(
                    connection, issuer=issuer, resident_id=recipient, message_id=message_id,
                    credentials=[{"a": a, "resource_id": resource} for a, resource in approval.credentials],
                )
            elif accepted["state"] in {"queued", "running"}:
                activate_prepared_task_credentials(connection, resident_id, message_id)
            else:
                end_task_credentials(connection, resident_id, message_id)
            connection.execute("""
                UPDATE resident_prepared_inputs SET status='released', settled_at=now() WHERE message_id=%s
            """, (message_id,))


def open_for_issuer(connection: Any, resident_id: str, message_id: str, issuer: str) -> bool:
    row = _row(connection, message_id)
    if not row or row["issuer"] != issuer or row["status"] != "prepared":
        return False
    approval = _stored_approval(row)
    return resident_id == (approval.recipient if approval else row["resident_id"])
