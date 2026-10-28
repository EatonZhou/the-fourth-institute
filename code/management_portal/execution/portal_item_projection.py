"""Runtime details of the items Portal itself defines in Provider events.

Every Adapter reports a few items in Portal's own terms, inside any Provider
event's ``params.item``: a question to the user and its answer
(``provider_user_input``), the record of a turn (``provider_turn_artifacts``)
and the card of a finished subagent run (``provider_subagent_runs``).  Each
becomes a message's content part (``CONTENT_PARTS``).  A running subagent's
card arrives on the ``subagent/progress`` method and stays live activity.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from management_common import provider_subagent_runs, provider_turn_artifacts, provider_user_input

from .runtime_details import EventContext, RuntimeDetails, mapping

# The content part of each Portal item type, from the item and its Execution.
CONTENT_PARTS: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {
    provider_user_input.REQUEST_ITEM: provider_user_input.content_part,
    provider_user_input.RESULT_ITEM: provider_user_input.content_part,
    "turnArtifacts": provider_turn_artifacts.content_part,
    "subagentRun": provider_subagent_runs.content_part,
}


def project_subagent_progress(params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    # A subagent the model waits on: its running card is live activity.
    run = mapping(mapping(params.get("item")).get("run"))
    if not str(run.get("tool_use_id") or ""):
        return
    detail = context.detail(
        kind="subagent", title="子 agent", content=str(run.get("description") or ""), status="running",
    )
    detail.update({"execution_id": context.execution_id, "subagent": run})
    details.add_subagent(detail, context.scoped(f"subagent:{run['tool_use_id']}"))


def _part_title(part: dict[str, Any]) -> str:
    if part.get("type") == "native_turn":
        return "本轮记录"
    if part.get("type") == "subagent_run":
        return "子 agent"
    if not part.get("is_blocking"):
        return "提问"
    return "等待你的审批" if part.get("tool_name") == "request_approval" else "等待你的回答"


def _end_running_subagent(part: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    # The run's card is a message now; its running card ends.
    ended = context.detail(
        kind="subagent", title="子 agent", content=str(part.get("description") or ""),
        status="failed" if part.get("status") == "failed" else "completed",
    )
    ended["execution_id"] = context.execution_id
    details.add_subagent(ended, context.scoped(f"subagent:{part['tool_use_id']}"))


def project_portal_item(item: dict[str, Any], context: EventContext, details: RuntimeDetails) -> bool:
    """Add the message of a Portal item; ``False`` when the item is no Portal item."""

    content_part = CONTENT_PARTS.get(item.get("type"))
    if content_part is None:
        return False
    part = content_part(item, context.execution_id)
    detail = context.detail(kind="update", title=_part_title(part), content="", status="completed")
    detail.update({
        "id": item["id"], "execution_id": context.execution_id,
        "content_parts": [part], "turn_id": part["turn_id"],
    })
    details.add(detail)
    if part["type"] == "native_turn" and part.get("thread_id") and part.get("turn_id"):
        # The immutable turn record replaces this turn's transient plan.
        details.end_plan(context, part["thread_id"], part["turn_id"])
    elif part["type"] == "subagent_run":
        _end_running_subagent(part, context, details)
    return True
