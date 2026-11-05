"""Prove the authenticated User input a Resident is currently acting on.

Resident interprets intent. Society checks immutable source text and executing
input ownership; the permission owner signs and audits in this transaction.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from management_common.portal_database import _database_schema
from management_common.json_columns import json_value
from management_common.principal import actor_principal, validate_message_origin
from management_portal import permission_system
from management_portal.permission_system import inline

from . import resident_execution_storage, resident_execution_work, resident_record_domain, resident_conversation_stream
from .lineage_data_mailbox import lock_active_resident
from .state_store import get_lineage_data_store


def _refuse(reason: str):
    raise PermissionError("User authorization requires " + reason)


def _source(item: Mapping, message_id: str) -> Mapping | None:
    if item.get("user_message_id") == message_id:
        return item
    for member in item.get("mailbox_members") or []:
        found = _source(member, message_id)
        if found is not None:
            return found
    return None


def _source_proof(message: Mapping, message_id: str, evidence: str, *, actor: str = "") -> tuple[str, str]:
    if message.get("message_id") != message_id or message.get("role") != "user":
        _refuse("the original accepted User message")
    try:
        origin = validate_message_origin(message.get("origin") or {})
    except (ValueError, TypeError):
        _refuse("an authenticated source origin")
    sender = origin["sender"]
    if (sender["kind"] != "user" or message.get("author_id") != sender["id"]
            or (actor and actor != sender["id"])
            or origin.get("initiated_by", sender) != sender):
        _refuse("a direct authenticated User sender, not a forwarded identity")
    texts = [part["text"] for part in message.get("content_parts") or []
             if isinstance(part, Mapping) and part.get("type") == "text"
             and isinstance(part.get("text"), str)]
    if not any(evidence in text for text in texts):
        _refuse("evidence appearing verbatim in a direct User text part")
    # Keep text-part boundaries and all exact Unicode/codepoints. Attachments,
    # quotes and context references cannot contribute evidence or this digest.
    source_digest = hashlib.sha256(json.dumps(
        texts, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return sender["id"], source_digest


def _original(repository, resident_id: str, message_id: str) -> Mapping:
    """Read the first accepted segment, never an inherited/harness display copy."""
    from management_portal.execution.agent_data_store import chat_segment_id
    from management_portal.execution.agent_data_chat import chat_payload

    try:
        segment = repository.agent_data.segment(chat_segment_id(resident_id, message_id))
    except KeyError:
        _refuse("the original accepted message segment")
    if segment.get("created_by_agent_id") != resident_id:
        _refuse("the source conversation's own accepted message")
    return chat_payload(segment["segment_id"], segment["metadata"], segment["content"])


def _delivered_inputs(connection, repository, caller: str, execution_id: str, job: Mapping):
    """Original messages actually admitted to this execution, not queued controls."""
    prefix = '"' + _database_schema("agent") + '".'
    bound = job["user_message_id"]
    rows = connection.execute(
        f"SELECT message_id,role FROM {prefix}execution_inputs WHERE execution_id=%s",
        (execution_id,),
    ).fetchall()
    for row in rows:
        mid = row["message_id"]
        item = _source(job, mid)
        if row["role"] in {"primary", "member"} and item is not None:
            yield mid, item.get("input_message") or {}, str(item.get("actor") or "")
        elif row["role"] == "info":
            shown = repository.message(caller, mid) or {}
            state = connection.execute(
                "SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s",
                (caller, mid),
            ).fetchone()
            if shown.get("released_with") == bound and state and state["state"] == "completed":
                yield mid, _original(repository, caller, mid), ""
    # The execution owner persists provider_input.append relations only after
    # confirmed Provider delivery. Pending controls have no such relation.
    rows = connection.execute(
        f"SELECT source.segment_id,source.metadata_json FROM {prefix}segments delivered "
        f"JOIN {prefix}segment_relations relation ON relation.target_segment_id=delivered.segment_id "
        f"JOIN {prefix}segments source ON source.segment_id=relation.source_segment_id "
        "WHERE delivered.execution_id=%s AND delivered.created_by_agent_id=%s "
        "AND delivered.metadata_json::jsonb->>'kind'='appended_provider_input' "
        "AND relation.operation_type='provider_input.append' "
        "AND source.created_by_agent_id=%s", (execution_id, caller, caller),
    ).fetchall()
    for row in rows:
        mid = (json_value(row["metadata_json"]).get("message") or {}).get("message_id")
        if mid:
            yield mid, _original(repository, caller, mid), ""


def _resolve_source(connection, repository, caller: str, execution_id: str, job: Mapping, message_id: str):
    """Resolve a delivered input or accepted relay alias to its original source."""
    from . import family_facade

    for delivered_id, message, actor in _delivered_inputs(connection, repository, caller, execution_id, job):
        holder, held_id = caller, delivered_id
        visited = {(holder, held_id)}
        matched = held_id == message_id
        for hops in range(family_facade.MAX_RELAY_HOPS + 1):
            # A business_ref alone is not authority. Validate the accepted
            # reverse link and retain its row through permission commit.
            source = family_facade.parse_relay_mark(repository.message_business_ref(holder, held_id))
            if source is None:
                if matched:
                    if hops:
                        message, actor = _original(repository, holder, held_id), ""
                    return holder, held_id, message, actor
                break
            if source in visited or hops == family_facade.MAX_RELAY_HOPS:
                break
            source_resident, source_id = source
            ledger = connection.execute(
                "SELECT relay_json FROM family_relays WHERE resident_id=%s FOR SHARE", (source_resident,),
            ).fetchone()
            link = (json_value(ledger["relay_json"]).get("links") or {}).get(source_id) if ledger else None
            if not link or (link.get("resident_id"), link.get("message_id")) != (holder, held_id):
                break
            visited.add(source)
            holder, held_id = source
            matched = matched or held_id == message_id
    _refuse("a delivered input or its persisted accepted Family relay source")


def accept(*, caller: str, execution_id: str, message_id: str,
           credentials: list[dict[str, str]], evidence: str, expires_in_seconds: int | None = None) -> dict:
    """Accept only this authenticated Resident's current executing User input."""
    if actor_principal(caller).kind != "resident":
        _refuse("an authenticated Resident caller")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id is required")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence is a nonempty direct User text excerpt")
    if not isinstance(credentials, list) or not 1 <= len(credentials) <= 20:
        raise ValueError("credentials requires 1..20 exact permissions")
    if any(not isinstance(item, dict) or set(item) != {"a", "resource_id"}
           or any(not isinstance(item[key], str) or not item[key].strip()
                  for key in ("a", "resource_id")) for item in credentials):
        raise ValueError("each credential contains only nonempty a and resource_id")
    if not execution_id:
        _refuse("the calling execution association")
    store = get_lineage_data_store()
    repository = resident_conversation_stream.get_repository()
    # Resident mutation lock has the same ordering as stop/admission. Pin its
    # work row too, so SQL control/cancellation cannot change ownership at commit.
    with resident_record_domain.state_lock(caller), store.database.transaction() as connection:
        try:
            lock_active_resident(connection, caller)
        except KeyError:
            _refuse("a live Resident identity")
        row = connection.execute(
            "SELECT * FROM resident_execution_work WHERE resident_id=%s FOR SHARE", (caller,),
        ).fetchone()
        if row is None:
            _refuse("a current business execution")
        job = resident_execution_storage.unpack(row)
        bound = job.get("user_message_id")
        if not bound:
            _refuse("a business input lifetime anchor")
        inline.lock(connection, bound)
        # These facts are read after the exact same lifetime lock as terminal
        # credential cleanup. A terminal writer may finish before us, or clean
        # our committed grant afterwards; it cannot leave a surviving grant.
        state = connection.execute(
            "SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s",
            (caller, bound),
        ).fetchone()
        if (not state or state["state"] != "running"
                or job.get("execution_id") != execution_id
                or not resident_execution_work.is_active(job)
                or not job.get("submitted_at") or job.get("operation")
                or job.get("stop_requested_at") or job.get("execution_terminal_status")):
            _refuse("the actively executing input, not queued, stopped or historical work")
        prefix = '"' + _database_schema("agent") + '".'
        execution = connection.execute(
            f"SELECT agent_id,status FROM {prefix}executions WHERE execution_id=%s FOR SHARE",
            (execution_id,),
        ).fetchone()
        if not execution or execution["agent_id"] != caller or execution["status"] != "running":
            _refuse("this Resident's running execution")
        source_resident, source_id, source_message, source_actor = _resolve_source(
            connection, repository, caller, execution_id, job, message_id,
        )
        issuer, source_digest = _source_proof(source_message, source_id, evidence, actor=source_actor)
        return permission_system.accept_user_authorization(
            connection, issuer=issuer, resident_id=caller, message_id=source_id,
            bound_input_message_id=bound, execution_id=execution_id, source_resident_id=source_resident,
            credentials=credentials, evidence=evidence, source_digest=source_digest,
            expires_in_seconds=expires_in_seconds,
        )
