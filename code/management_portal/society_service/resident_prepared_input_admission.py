"""Check and commit the admission of a Resident input against its prepared intent."""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_common.async_questions import ANSWER_PART, answered_set, approved_credentials, question_set
from management_common.principal import Principal, actor_principal
from management_portal.permission_system import inline

from . import invariant_locks, resident_message_contract
from .resident_message_contract import MessageIdentityConflict
from .resident_prepared_input_storage import Approval, read_row, stored_approval


@dataclass(frozen=True)
class Intent:
    resident_id: str
    issuer: str
    message_id: str
    canonical: str
    approval: Approval | None = None


_RELEASE: ContextVar[Intent | None] = ContextVar("prepared_resident_input_release", default=None)


def _database():
    from .state_store import get_lineage_data_store

    return get_lineage_data_store().database


@contextmanager
def releasing(intent: Intent) -> Iterator[None]:
    """Bind the caller's checked intent while its input is delivered."""
    token = _RELEASE.set(intent)
    try:
        yield
    finally:
        _RELEASE.reset(token)


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


def build_intent(
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


def require_identical(row: Mapping[str, Any], intent: Intent) -> None:
    if (row["resident_id"], row["issuer"], row["intent_json"]) != (
        intent.resident_id, intent.issuer, intent.canonical,
    ):
        raise MessageIdentityConflict("该消息 ID 已绑定其他准备输入")


def _check(
    row: Mapping[str, Any] | None, resident_id: str, message_id: str, issuer: str, *,
    activation_policy: str, relay_source: tuple[str, str] | None,
) -> bool:
    if row is None:
        return False
    approval = stored_approval(row)
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
    require_identical(row, intent)
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
    actual = build_intent(resident_id, issuer, body, context_policy=expected["context_policy"],
                          execution_constraints=expected["execution_constraints"], business_ref=business_ref)
    if actual.canonical != intent.canonical:
        raise MessageIdentityConflict("实际投递内容与准备输入不符")


def check_admission(resident_id: str, message_id: str, issuer: str, *, activation_policy: str,
                    relay_source: tuple[str, str] | None = None) -> None:
    with _database().read() as connection:
        _check(read_row(connection, message_id), resident_id, message_id, issuer,
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
        row = read_row(connection, message_id)
        initial = _check(row, resident_id, message_id, issuer,
                         activation_policy=activation_policy, relay_source=relay_source)
        approval = stored_approval(row) if initial else None
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
