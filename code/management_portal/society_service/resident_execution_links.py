"""Attach graph input sources to an Execution; Agent owns its lifecycle."""
from __future__ import annotations

from typing import Any, Mapping
from . import trigger_paths


def link_inputs(resident_id: str, execution: Mapping[str, Any], *, connection: Any) -> bool:
    from management_portal.execution import execution_inputs
    identity = str(execution.get("execution_id") or "")
    if not identity:
        return False
    main_message = str(execution.get("user_message_id") or "")
    messages = {main_message: "primary"} if main_message else {}
    for member in execution.get("mailbox_members") or []:
        message = str(member.get("message_id") or member.get("user_message_id") or "") if isinstance(member, Mapping) else str(member)
        if message and message != main_message:
            messages[message] = "member"
    at = str(execution.get("enqueued_at") or execution.get("started_at") or "")
    from management_common.portal_database import _database_schema
    execution_inputs.attach_in(connection, identity, [{"message_id":message,"role":role,"accepted_at":at}
                                                     for message,role in messages.items()],
                               prefix='"' + _database_schema("agent") + '".')
    if execution.get("retry_of_execution_id"):
        from management_portal.execution import input_retries
        input_retries.record(connection, predecessor=str(execution["retry_of_execution_id"]),
                             execution_id=identity, resident_id=resident_id, message_id=main_message, at=at)
    changed = False
    # A delivery may originate in another Lineage, or predate a Resident move.
    # The admitted source steps, rather than current membership, own these links.
    sources = connection.execute(
        "SELECT DISTINCT p.lineage_id,s.input_message_id FROM trigger_path_steps s "
        "JOIN trigger_paths p USING(trigger_path_id) WHERE s.kind='resident' "
        "AND s.node_id=%s AND s.input_message_id=ANY(%s)",
        (resident_id, list(messages)),
    ).fetchall()
    for source in sources:
        changed = trigger_paths.link_input(source["lineage_id"], resident_id, source["input_message_id"], identity) or changed
    return changed
