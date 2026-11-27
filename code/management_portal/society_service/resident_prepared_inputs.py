"""Immutable intent for a Resident input that has yet to be delivered."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from management_common.async_questions import answered_set
from management_common.portal_database import _database_schema
from management_portal.permission_system import inline

from . import resident_intake_domain, resident_message_contract
from .resident_message_contract import MessageIdentityConflict
from .resident_prepared_input_admission import (  # noqa: F401  (admission is part of this module's public surface)
    accepting, approval_for, build_intent, check_admission, check_authored_input, releasing, releasing_approval,
    require_identical,
)
from .resident_prepared_input_storage import Approval, read_row, stored_approval  # noqa: F401
from .state_store import get_lineage_data_store


def _database():
    return get_lineage_data_store().database


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
    intent = build_intent(resident_id, issuer, message, context_policy=context_policy,
                          execution_constraints=execution_constraints, business_ref=business_ref)
    content, *_ = resident_message_contract.input_parts(message)
    if content.lstrip().startswith("/"):
        raise ValueError("即时命令没有可预备的输入轮次")
    with _database().read() as connection:
        row = read_row(connection, intent.message_id)
    if row is not None:
        require_identical(row, intent)
        return _receipt(intent.resident_id, intent.message_id, row["status"], stored_approval(row))
    # Preflight may read another domain. It never holds the permission lock.
    resident_intake_domain.preflight_resident_input(intent.resident_id, intent.issuer, message, business_ref=business_ref)
    with _database().transaction() as connection:
        inline.lock(connection, intent.message_id)
        row = read_row(connection, intent.message_id)
        if row is not None:
            require_identical(row, intent)
            return _receipt(intent.resident_id, intent.message_id, row["status"], stored_approval(row))
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
        row = read_row(connection, message_id)
        if row is None or row["resident_id"] != resident_id or row["issuer"] != issuer or row["status"] == "released":
            return None
        if row["status"] == "prepared":
            connection.execute("""
                UPDATE resident_prepared_inputs SET status='cancelled', settled_at=now() WHERE message_id=%s
            """, (message_id,))
            approval = stored_approval(row)
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
        row = read_row(connection, message_id) if message_id else None
    if row is None:
        yield False
        return
    if activation_policy != "enqueue_and_trigger" or row["status"] == "cancelled":
        raise MessageIdentityConflict("该消息 ID 不能用于本次投递")
    intent = build_intent(resident_id, issuer, message, context_policy=context_policy,
                          execution_constraints=execution_constraints, business_ref=business_ref)
    require_identical(row, intent)
    with releasing(replace(intent, approval=stored_approval(row))):
        yield True
