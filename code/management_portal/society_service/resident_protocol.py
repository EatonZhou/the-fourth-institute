from __future__ import annotations

import copy

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_common.async_questions import question_set
from management_portal.execution.accounts import shared_account_configuration
from management_portal.portal_access_control import get_portal_access_store

from . import (
    family_activity,
    family_facade,
    identity_contract,
    resident_access,
    resident_actual_sent_domain,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_domain,
    resident_execution_provider,
    resident_execution_work,
    resident_held_infos,
    resident_input_recovery,
    resident_intake_domain,
    resident_lineage,
    resident_mailbox,
    resident_management_domain,
    resident_queue_control_domain,
    resident_record_domain,
)
from . import resident_fork_inheritance, resident_message_contract, resident_input_scope
from .input_progress import input_progress
from . import resident_execution_gate


FORWARD_MESSAGE_LIMIT = 20
FORWARD_MESSAGE_CHAR_LIMIT = 6000
FORWARD_TOTAL_CHAR_LIMIT = 60000

_DELIVER_RESIDENT_INPUT_FIELDS = frozenset({
    "resident_id",
    "message",
    "activation_policy",
})
CONTEXT_TEXT_FIELD = "context_text"
INSTRUCTION_SKILLS_FIELD = "instruction_skills"
WORKING_DIRECTORY_FIELD = "working_directory"


@dataclass(frozen=True)
class ResidentDeliveryContext:
    """Trusted delivery provenance, separate from the caller's message fields."""

    lineage_id: str = ""
    business_ref: str = ""
    # A Family reply's source in the actual sender's Conversation, not the facade's.
    reply_source_message_id: str = ""


def _target(resident_id: str, actor: str) -> resident_domain_common.ResidentRuntimeTarget:
    return resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )


def _send_problem(record: Mapping[str, Any], actor: str, *, allowed: bool | None = None) -> tuple[bool, str]:
    """Whether the actor may send the Resident messages, and what stops them now if anything."""

    if allowed is None:
        allowed = resident_access.can_manage_resident(dict(record), actor)
    if record.get("deleted_at"):
        return False, "这个 Resident 已删除，不能发送消息。"
    return allowed, ""


def _conversation_controls(
    target: resident_domain_common.ResidentRuntimeTarget,
    actor: str,
    state: Mapping[str, Any],
    *,
    allowed: bool | None = None,
) -> dict[str, Any]:
    allowed, problem = _send_problem(target.record, actor, allowed=allowed)
    active_message_id = str(state.get("active_message_id") or "")
    closed = state.get("intake_state") == "closed"
    capabilities = resident_execution_provider.provider_for(target.record).capabilities
    return {
        "can_send": allowed and not problem,
        # A facade routing a request has nothing running yet; its Stop withdraws that request.
        "can_stop": allowed and (bool(active_message_id) or state.get("work_phase") == family_facade.ROUTING_PHASE),
        "active_turn_input": capabilities.supports_active_turn_input,
        # What a message to it may carry; the Family Router gives a request with
        # images or files only to a member that reads them.
        "accepts_images": capabilities.supports_media,
        "accepts_files": capabilities.local_tools,
        "disabled_reason": problem if allowed else "当前对话无发送权限。",
        "send_hint": resident_execution_gate.HINTS.get(
            'manual' if closed else (state.get('execution_gate') or {}).get('reason'), ''
        ) if not problem else "",
    }




def _request_source(target, message_id: str) -> tuple[str, dict[str, Any] | None]:
    """Resolve only a message carried by this conversation's durable relay."""
    repository = resident_conversation_stream.get_repository()
    resident_id = target.resident_id
    message = repository.message(resident_id, message_id)
    if message is None and family_facade.is_facade(target.record):
        found = family_facade.relayed_reply(target.record, message_id)
        if found is not None:
            state = family_facade.project_input_state(target.record, found["request_message_id"], {})
            if state.get("state") == "cancelled":
                raise ValueError("原请求已取消，不能回答其问题")
            return found["worker_id"], found["reply"]
    return resident_id, message


def respond_resident_user_input(resident_id: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    from management_common.provider_user_input import validate_response
    from management_common.provider_interactions import INTERACTIVE_TOOL_NAMES
    from . import execution_client

    target = _target(resident_id, actor)
    if not resident_access.can_manage_resident(target.record, actor):
        raise PermissionError("当前对话无回答权限")
    repository = resident_conversation_stream.get_repository()
    resident_id, message = _request_source(target, str(payload.get("message_id") or ""))
    if question_set(message) is not None:
        # A question set in a message is answered by an answer message; this
        # request only closes it unanswered, without telling the Resident.
        if payload.get("action") != "cancel":
            raise ValueError("消息中的问题组用回答消息作答；这里只能关闭（action=cancel）")
        repository.close_question_set(resident_id, message["message_id"], actor=actor)
        return {"status": "cancelled"}
    parts = (message or {}).get("content_parts") or []
    request = next((part for part in parts if part.get("type") == "tool_call"
                    and part.get("tool_name") in INTERACTIVE_TOOL_NAMES), None)
    if not request or (message or {}).get("role") != "agent":
        raise ValueError("找不到原生提问消息")
    source = repository.agent_data.segment(str(request.get("segment_id") or ""))
    if source.get("created_by_agent_id") != resident_id or source.get("content") != request:
        raise ValueError("此请求不属于当前 Resident")
    service = execution_client.service()
    control_id = "control-" + request["request_id"][:24]
    response = validate_response({**payload, "request_id": request["request_id"]}, request["questions"], request.get("interaction"))
    current = repository._tool_status_messages([message], resident_id=resident_id)[0]["content_parts"]
    if any(part.get("type") == "tool_call" and part.get("status") != "pending" for part in current):
        raise ValueError("此请求已回答、取消或失效")
    receipt = service.append_input(
        request["execution_id"], actor=actor, input=[response], expected_turn_id=request["turn_id"],
        control_id=control_id,
    )
    control_status = receipt.get("status")
    status = {"cancel": "cancelled", "decline": "declined"}.get(response["action"], "answered") if control_status == "delivered" else (
        "pending" if control_status in {"pending", "queued"} else "expired"
    )
    return {"status": status, **({"error": receipt["error"]} if receipt.get("error") else {})}


REQUEST_SCAN_MESSAGES = 80


def _request_items(resident_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from management_common.provider_interactions import INTERACTIVE_TOOL_NAMES

    items = []
    for message in messages:
        if message.get("role") != "agent":
            continue
        for part in message.get("content_parts") or []:
            if part.get("type") != "tool_call" or part.get("tool_name") not in INTERACTIVE_TOOL_NAMES:
                continue
            status = str(part.get("status") or "pending")
            if status not in {"pending", "answered", "declined", "cancelled", "expired"}:
                status = "answered" if status in {"completed", "accepted"} else "expired"
            items.append({
                "resident_id": resident_id,
                "message_id": str(message.get("message_id") or ""),
                "kind": "approval" if part.get("tool_name") == "request_approval" else "question",
                "questions": copy.deepcopy(part.get("questions") or []),
                "interaction": copy.deepcopy(part.get("interaction") or {}),
                "status": status,
                "answers": copy.deepcopy(part.get("answers") or {}),
                "created_at": str(message.get("created_at") or ""),
            })
    return items


def _scan_requests(resident_id: str, actor: str) -> list[dict[str, Any]]:
    target = _target(resident_id, actor)
    if not resident_access.can_manage_resident(target.record, actor):
        return []
    repository = resident_conversation_stream.get_repository()
    messages = repository.public_messages(target.resident_id, limit=REQUEST_SCAN_MESSAGES)
    messages = repository._tool_status_messages(messages, resident_id=target.resident_id)
    if family_facade.is_facade(target.record):
        messages = family_facade.project_messages(target.record, messages)
    return _request_items(target.resident_id, messages)


def list_resident_requests(resident_ids: list[str], actor: str) -> list[dict[str, Any]]:
    """Pending native questions and approvals of the given Residents."""

    candidates = [str(item).strip() for item in resident_ids or [] if str(item or "").strip()]
    result = []
    for resident_id in dict.fromkeys(candidates):
        try:
            result.extend(item for item in _scan_requests(resident_id, actor) if item["status"] == "pending")
        except (KeyError, PermissionError):
            continue
    return sorted(result, key=lambda item: item["created_at"])


def get_resident_request(resident_id: str, actor: str, message_id: str) -> dict[str, Any]:
    target = _target(resident_id, actor)
    repository = resident_conversation_stream.get_repository()
    holder_id, message = _request_source(target, message_id)
    if message is None:
        raise KeyError(f"消息不存在：{message_id}")
    messages = repository._tool_status_messages([message], resident_id=holder_id)
    items = _request_items(target.resident_id, messages)
    if not items:
        raise KeyError(f"消息 {message_id} 不是等待回答的请求")
    return items[0]


def _with_conversation_controls(
    result: Mapping[str, Any], resident_id: str, actor: str,
    *, target: resident_domain_common.ResidentRuntimeTarget | None = None,
    state_projected: bool = False, allowed: bool | None = None,
) -> dict[str, Any]:
    """Add controls only after projecting the same response's execution gate."""
    response = dict(result)
    if isinstance(response.get("state"), Mapping):
        if target is None:
            target = resident_record_domain.resolve_resident_runtime_target(resident_id)
        if not state_projected:
            response["state"] = resident_execution_gate.project_states(
                {target.resident_id: {"state": response["state"]}},
                resident_records={target.resident_id: target.record},
            )[target.resident_id]["state"]
        response["controls"] = _conversation_controls(target, actor, response["state"], allowed=allowed)
    return response


def _input_parts(
    message: Mapping[str, Any],
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
    str,
    list[str],
    str,
]:
    parts = message.get("content_parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("message.content_parts required")
    resident_message_contract.validate_content_parts(parts)
    text_parts: list[str] = []
    media: list[dict[str, Any]] = []
    context_refs: dict[str, Any] = {}
    context_text: list[str] = []
    instruction_skills: list[str] = []
    working_directory = ""
    for raw in parts:
        part = dict(raw)
        part_type = str(part.get("type") or "").strip().casefold()
        if part_type == "text":
            value = str(part.get("text") or "")
            text_parts.append(value)
        elif part_type in {"image", "file"}:
            media.append(part)
        elif part_type == "turn_review":
            if not isinstance(part.get("segment_id"), str) or not part["segment_id"]:
                raise ValueError("turn review requires a source Segment")
            text_parts.append("审查指定轮次的修改快照")
        elif part_type in {
            "artifact_reference",
            "resource_reference",
            "context_reference",
        }:
            if (
                part_type == "context_reference"
                and isinstance(part.get("context"), Mapping)
            ):
                context = dict(part["context"])
                raw_context_text = str(
                    context.pop(CONTEXT_TEXT_FIELD, "") or ""
                ).strip()
                if raw_context_text:
                    context_text.append(raw_context_text)
                raw_instruction_skills = context.pop(
                    INSTRUCTION_SKILLS_FIELD,
                    (),
                )
                if isinstance(raw_instruction_skills, (list, tuple)):
                    for skill in raw_instruction_skills:
                        skill_id = str(skill or "").strip()
                        if skill_id and skill_id not in instruction_skills:
                            instruction_skills.append(skill_id)
                supplied_working_directory = str(
                    context.pop(WORKING_DIRECTORY_FIELD, "") or ""
                ).strip()
                if supplied_working_directory:
                    if (
                        working_directory
                        and supplied_working_directory != working_directory
                    ):
                        raise ValueError(
                            "Resident input contains conflicting working directories"
                        )
                    working_directory = supplied_working_directory
                context_refs.update(context)
            else:
                context_refs.setdefault("resources", []).append(part)
        else:
            context_refs.setdefault("content_parts", []).append(part)
    # The stored part lengths locate inline attachments in the captured input.
    # Validation must not change the body that those lengths describe.
    content = "\n".join(text_parts)
    if not content.strip():
        raise ValueError("Resident input requires a text content part")
    return (
        content,
        media,
        context_refs,
        "\n\n".join(context_text),
        instruction_skills,
        working_directory,
    )


def deliver_resident_input(
    payload: dict[str, Any],
    actor: str,
    *,
    delivery_context: ResidentDeliveryContext | None = None,
) -> dict[str, Any]:
    unexpected = sorted(set(payload) - _DELIVER_RESIDENT_INPUT_FIELDS)
    if unexpected:
        raise ValueError(
            "DeliverMessage received unsupported fields: "
            + ", ".join(unexpected)
        )
    resident_id = str(payload.get("resident_id") or "").strip()
    message = payload.get("message")
    if not resident_id:
        raise ValueError("resident_id required")
    if not isinstance(message, Mapping):
        raise ValueError("message required")
    resident_message_contract.validate_delivery_message(message)
    (
        content,
        input_media,
        context_refs,
        runtime_context,
        additional_skills,
        working_directory,
    ) = _input_parts(message)
    supplied_message_id = str(message.get("message_id") or "").strip()
    if not supplied_message_id:
        raise ValueError("message.message_id required")
    message_id = resident_message_contract.message_identity(supplied_message_id)
    trusted_context = delivery_context or ResidentDeliveryContext()
    result = resident_intake_domain.deliver_resident_message_content(
            resident_id,
            actor,
            content,
            context_refs,
            input_media=input_media,
            working_directory=working_directory,
            runtime_context=runtime_context,
            additional_skills=additional_skills,
            activation_policy=str(
                payload.get("activation_policy") or "enqueue_and_trigger"
            ),
            lineage_id=trusted_context.lineage_id,

            message_id=message_id,
            protocol_input=message,
            business_ref=trusted_context.business_ref,
            reply_source_message_id=trusted_context.reply_source_message_id,
        ) or {}
    if resident_input_scope.permits(resident_id, actor, message_id):
        return {key: result[key] for key in ("resident_id", "message_id", "message", "input_state", "cursor") if key in result}
    if not _reads_resident(resident_id, actor):
        # Someone else's personal AMA: the sender learns what became of its own message, nothing of the Conversation.
        return {key: result[key] for key in SENDER_RECEIPT if key in result}
    return _with_conversation_controls(result, resident_id, actor)


# What a delivery tells a sender that cannot read the recipient: its own message and its fate.
SENDER_RECEIPT = ("resident_id", "message_id", "message", "input_state", "accepted", "activation_policy",
                  "injected", "already_injected", "scheduled_next_turn", "delivery_status")


def _reads_resident(resident_id: str, actor: str) -> bool:
    try:
        record = resident_catalog.get_catalog().get_admission(resident_id)
    except KeyError:
        return False
    return resident_access.resident_visible_to_actor(dict(record), canonical_actor_id(actor))


def _forward_item_speaker(item: Mapping[str, Any]) -> str:
    """Who said a forwarded message: its author, else what its role is (user, agent or Portal)."""

    author = str(item.get("author") or "").strip()
    role = str(item.get("role") or "").strip().lower()
    if author:
        return author
    return {"user": "User", "agent": "Agent"}.get(role) or str(item.get("title") or "").strip() or "Portal"


def forward_content(raw_items: object, note: object, *, quoted: bool = False) -> str:
    if not isinstance(raw_items, list):
        raise ValueError("messages required")
    lines = []
    for raw in raw_items[:FORWARD_MESSAGE_LIMIT]:
        if not isinstance(raw, Mapping):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        if len(content) > FORWARD_MESSAGE_CHAR_LIMIT:
            content = (
                content[:FORWARD_MESSAGE_CHAR_LIMIT].rstrip()
                + "\n[truncated]"
            )
        lines.append(f"{_forward_item_speaker(raw)}: {content}")
    if not lines:
        raise ValueError("至少选择一条可转发消息")
    if quoted:
        # Every source line stays quoted, including blank lines and historic @.
        lines = ["转发消息", "", *("> " + line for line in "\n".join(lines).split("\n"))]
    else:
        lines = ["<forward message>", *lines, "</forward message>"]
    extra = str(note or "").strip()
    if extra:
        lines.extend(["", extra])
    content = "\n".join(lines).strip()
    if len(content) > FORWARD_TOTAL_CHAR_LIMIT:
        content = (
            content[:FORWARD_TOTAL_CHAR_LIMIT].rstrip()
            + "\n[forward message truncated]"
        )
    return content


def prepare_resident_forward(
    payload: dict[str, Any],
    actor: str = "",
) -> dict[str, Any]:
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后转发")
    target_resident_id = str(
        payload.get("target_resident_id") or ""
    ).strip()
    if not target_resident_id:
        raise ValueError("target_resident_id required")
    # Forwarding delivers a message: anyone may forward to a personal AMA.
    target = resident_record_domain.resolve_resident_runtime_target(
        target_resident_id, actor_id, require_visible=True, delivery=True)
    return {
        "lineage_id": resident_lineage.lineage_id(target.record),
        "resident_id": target.resident_id,
        "message": {
            "message_id": resident_message_contract.message_identity(),
            "role": "user",
            "content_parts": [{
                "type": "text",
                "text": forward_content(
                    payload.get("messages"),
                    payload.get("message"),
                ),
            }],
        },
    }


def _resident_view(target: Any) -> dict[str, Any]:
    record = target.record
    account = resident_backend.execution_account_projection(record)
    return {
        "resident_id": target.resident_id,
        "title": str(record.get("title") or target.resident_id),
        "lineage_id": resident_lineage.lineage_id(record),
        "lineage_title": str(record.get("lineage_title") or ""),
        "project_id": str(record.get("project_id") or ""),
        "created_by": str(record.get("created_by") or ""),
        # The values CreateResident takes: a provider_api Resident names its model API as the account.
        "provider": resident_backend.execution_backend(record),
        "account": str((account or {}).get("account_id") or ""),
        "workspaces": _workspace_views(record),
        **({"family": dict(record["family"])} if record.get("family") else {}),
    }


def _workspace_views(record: Mapping[str, Any]) -> list[dict[str, str]]:
    """Expose self ownership separately from ordinary usage."""
    from . import resident_record_domain
    return [{"workspace_id": root["workspace_id"], "path": root["path"],
             "title": root["title"], "label": root["label"],
             "is_owned": root["is_primary"], "purpose": root.get("purpose", "")}
            for root in resident_record_domain.resident_workspace_roots(dict(record))]


def get_resident(resident_id: str, actor: str) -> dict[str, Any]:
    return _resident_view(_target(resident_id, actor))


def get_resident_state(resident_id: str, actor: str) -> dict[str, Any]:
    target = _target(resident_id, actor)
    snapshots = read_resident_state_snapshots([target.resident_id])
    if target.resident_id not in snapshots:
        raise KeyError(target.resident_id)
    return _with_conversation_controls(
        snapshots[target.resident_id], target.resident_id, actor,
        target=target, state_projected=True,
    )


def read_resident_state_snapshots(
    resident_ids: Iterable[str],
    *, resident_records: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read Resident state, cursor and Family display activity for projections."""

    selected = list(dict.fromkeys(
        str(resident_id or "").strip().lower()
        for resident_id in resident_ids
        if str(resident_id or "").strip()
    ))
    invalid = [
        resident_id
        for resident_id in selected
        if not identity_contract.is_valid_id(resident_id, "resident")
    ]
    if invalid:
        raise ValueError("Resident state requires canonical resident_* IDs")
    snapshots = resident_conversation_stream.get_repository().state_snapshots(
        selected
    )
    projected = family_activity.project_states({
        resident_id: {
            "resident_id": resident_id,
            "state": dict(snapshot["state"]),
            "cursor": max(0, int(snapshot.get("cursor") or 0)),
            "latest_input_state": dict(
                snapshot.get("latest_input_state") or {}
            ),
        }
        for resident_id, snapshot in snapshots.items()
    }, resident_records=resident_records)
    return resident_execution_gate.project_states(projected, resident_records=resident_records)


def read_resident_states(
    resident_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Read canonical Resident state for trusted server-side projections."""

    return {
        resident_id: dict(snapshot["state"])
        for resident_id, snapshot in read_resident_state_snapshots(resident_ids).items()
    }


def read_work_states(resident_ids: Iterable[str]) -> dict[str, str]:
    """Each live Resident's work state, derived from its active turn and queue.

    Scheduling only needs to know whether a Resident is busy. The state view
    adds input history, Family activity, quota gates and controls, which cost
    an ExecutionService call and account resolution per read. Deleted or
    unknown Residents are absent.
    """
    states = resident_conversation_stream.get_repository().states(resident_ids)
    return {resident_id: str(state.get("work_state") or "idle") for resident_id, state in states.items()}


def get_resident_states(
    resident_ids: list[str],
    actor: str,
) -> dict[str, list[dict[str, Any]]]:
    requested = list(dict.fromkeys(
        str(item or "").strip()
        for item in resident_ids
        if str(item or "").strip()
    ))
    records = {record["resident_id"]: record for record in
               resident_access.visible_resident_records(requested, actor, "portal")}
    visible = list(records)
    snapshots = read_resident_state_snapshots(visible, resident_records=records)
    allowed = resident_access.actor_can_manage_residents(actor) if visible else False
    # Read access is already checked above. Controls use canonical Resident
    # facts; resolving every full runtime target here repeats the same Lineage
    # reads once per member and makes large Chat/graph state refreshes slow.
    with shared_account_configuration():
        result = {
            "items": [
                _with_conversation_controls(
                    snapshots[resident_id], resident_id, actor,
                    target=resident_domain_common.ResidentRuntimeTarget(
                        resident_id=resident_id, record=records[resident_id]),
                    state_projected=True, allowed=allowed,
                )
                for resident_id in visible
                if resident_id in snapshots and resident_id in records
            ]
        }
    return result


SEARCH_RESIDENTS_LIMIT = 50


def _search_rank(record: Mapping[str, Any], term: str) -> int | None:
    """How well a Resident matches a keyword (smaller is better); None when it does not."""

    resident_id = str(record.get("resident_id") or "").casefold()
    title = str(record.get("title") or "").casefold()
    if term in {resident_id, resident_id.removeprefix("resident_"), title}:
        return 0
    if resident_id.removeprefix("resident_").startswith(term) or resident_id.startswith(term) or title.startswith(term):
        return 1
    if term in title or term in resident_id:
        return 2
    if term in str(record.get("lineage_title") or "").casefold():
        return 3
    return None


def search_residents(
    actor: str,
    *,
    q: str = "",
    resident_ids: Iterable[str] = (),
    can_send: bool = False,
    limit: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Visible Residents matching ``q``, and whether the caller can send each a message now.

    Without ``q`` the most recently working come first; ``resident_ids``
    keeps its own order.  A Chatroom's @ list reads it (decision_a6bebf4ac283):
    the Residents the Chatroom called, then the ones the caller can send to.
    Permissions are checked only until ``limit`` Residents are found.
    """

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后查找 Resident")
    limit = max(1, min(int(limit or 20), SEARCH_RESIDENTS_LIMIT))
    term = str(q or "").strip().lstrip("@").strip().casefold()
    wanted = list(dict.fromkeys(str(item or "").strip().lower() for item in resident_ids or () if str(item or "").strip()))
    # Authorization uses resident_id; state projection uses canonical records.
    catalog = resident_catalog.get_catalog()
    records = [
        record for record in (catalog.get_many(wanted) if wanted else catalog.all())
        if record.get("resident_id") and not record.get("deleted_at") and resident_catalog.registration_ready(record)
    ]
    if wanted:
        by_id = {str(record["resident_id"]): record for record in records}
        candidates = [by_id[item] for item in wanted if item in by_id]
    else:
        candidates = sorted(
            records,
            key=lambda record: str(record.get("last_turn_usage_at") or record.get("updated_at") or ""),
            reverse=True,
        )
    if term:
        ranked = [(rank, index, record) for index, record in enumerate(candidates)
                  if (rank := _search_rank(record, term)) is not None]
        candidates = [record for _rank, _index, record in sorted(ranked, key=lambda item: item[:2])]
    found: list[dict[str, Any]] = []
    for record in candidates:
        if not resident_access.resident_visible_to_actor(dict(record), actor_id):
            continue
        allowed, problem = _send_problem(record, actor_id)
        sendable = allowed and not problem
        if can_send and not sendable:
            continue
        found.append({
            "resident_id": str(record["resident_id"]),
            "title": str(record.get("title") or record["resident_id"]),
            "lineage_id": resident_lineage.lineage_id(dict(record)),
            "lineage_title": str(record.get("lineage_title") or ""),
            "can_send": sendable,
            "disabled_reason": "" if sendable else problem or "没有给这个 Resident 发消息的权限。",
        })
        if len(found) >= limit:
            break
    return {"residents": found}


def get_resident_activity_snapshot(
    resident_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return the system-wide activity view derived from protocol state."""

    requested: set[str] | None = None
    if resident_ids is not None:
        requested = set()
        for raw_resident_id in resident_ids:
            resident_id = str(raw_resident_id or "").strip().lower()
            if not identity_contract.is_valid_id(resident_id, "resident"):
                raise ValueError(
                    "Resident activity requires canonical resident_* IDs"
                )
            requested.add(resident_id)

    # Trusted runtime queries need canonical membership, not permission checks.
    # In particular, a selected graph condition must not hydrate every Resident
    # in the installation before filtering down to its actual subjects.
    records = (
        resident_lineage.all()
        if requested is None
        else resident_catalog.get_catalog().get_many(sorted(requested))
    )
    resident_records = {
        str(record.get("resident_id") or "").strip(): record
        for record in records
        if isinstance(record, Mapping)
        and resident_catalog.registration_ready(record)
        and str(record.get("resident_id") or "").strip()
        and str(record.get("session_state") or "") != "deleted"
    }
    residents = read_resident_activity(resident_records)
    active_resident_ids = [
        resident_id
        for resident_id, state in residents.items()
        if state["active"]
    ]
    return {
        "schema": "tfi.resident-activity-snapshot.v2",
        "captured_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "resident_ids": sorted(residents),
        "active_resident_ids": active_resident_ids,
        "active_count": len(active_resident_ids),
        "active_statuses": ["queued", "running", "stopping"],
        "missing_resident_ids": sorted(
            (requested or set()) - set(residents)
        ),
        "residents": residents,
    }


def read_resident_activity(
    resident_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Build canonical activity projections for trusted server consumers."""

    selected = sorted(dict.fromkeys(
        str(resident_id or "").strip().lower()
        for resident_id in resident_ids
        if str(resident_id or "").strip()
    ))
    invalid = [
        resident_id
        for resident_id in selected
        if not identity_contract.is_valid_id(resident_id, "resident")
    ]
    if invalid:
        raise ValueError("Resident activity requires canonical resident_* IDs")
    repository = resident_conversation_stream.get_repository()
    projections = repository.current_projections(selected)
    residents: dict[str, dict[str, Any]] = {}
    for resident_id in selected:
        projection = projections.get(resident_id)
        if projection is None:
            continue
        state = dict(projection.get("state") or {})
        work_state = str(state.get("work_state") or "idle").lower()
        active = work_state in {"queued", "running", "stopping"}
        activity: dict[str, Any] = {
            "resident_id": resident_id,
            "active": active,
            "status": work_state if active else "idle",
        }
        if active:
            activity["work_phase"] = str(state.get("work_phase") or "")
        queue_projection = (
            projection.get("queue")
            if isinstance(projection.get("queue"), Mapping)
            else {}
        )
        queue = [
            dict(item)
            for item in queue_projection.get("items") or []
            if isinstance(item, Mapping)
        ] if active else []
        if active:
            activity["queue_count"] = len(queue)
            activity["queued_messages"] = queue
            queued_at = sorted(
                str(item.get("created_at") or "")
                for item in queue
                if str(item.get("created_at") or "")
            )
            if queued_at:
                activity["oldest_queued_at"] = queued_at[0]
        active_message_id = str(state.get("active_message_id") or "")
        if active and active_message_id:
            activity["message_id"] = active_message_id
            try:
                active_message = repository.public_message(
                    resident_id,
                    active_message_id,
                )
            except KeyError:
                active_message = {}
            activity["active_message"] = active_message or {
                "message_id": active_message_id
            }
            started_at = str(active_message.get("created_at") or "")
            if started_at:
                activity["started_at"] = started_at
        residents[resident_id] = activity
    return residents


def get_resident_conversation_snapshot(
    resident_id: str,
    actor: str = "",
    *,
    history_limit: int = resident_conversation_stream.DEFAULT_HISTORY_LIMIT,
    input_message_id: str = "",
    include_inheritance: bool = False,
    include_inherited_context: bool = False,
) -> dict[str, Any]:
    actor_id = canonical_actor_id(actor)
    scoped = bool(input_message_id) and resident_input_scope.permits(resident_id, actor_id, input_message_id)
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor_id,
        require_visible=not scoped and not get_portal_access_store().is_service_actor(
            actor_id
        ),
    )
    snapshot = resident_conversation_stream.get_repository().snapshot(
        target.resident_id,
        history_limit=history_limit,
    )
    if input_message_id:
        snapshot = resident_input_scope.select_snapshot(
            snapshot, input_message_id, resident_conversation_stream.get_repository(), resident_id,
            history_limit=history_limit,
        )
    snapshot.pop("resident_id", None)
    if family_facade.is_facade(target.record):
        # Infos show with the facade's next request, as on any Resident.
        snapshot = resident_held_infos.project_snapshot(target.record, snapshot, handed_to_steward=True)
        snapshot = family_facade.project_snapshot(target.record, snapshot)
        from . import family_chat_preferences
        if not input_message_id:
            from . import personal_history
            snapshot = personal_history.prepend(target.record, snapshot, limit=history_limit)
        snapshot = family_chat_preferences.project(target.record, actor_id, snapshot)
    else:
        snapshot = resident_held_infos.project_snapshot(target.record, snapshot)
        snapshot["messages"] = resident_mailbox.project_messages(target.resident_id, snapshot.get("messages") or [])
    snapshot["resident"] = _resident_view(target)
    snapshot = _with_conversation_controls(snapshot, target.resident_id, actor_id, target=target)
    if (include_inheritance or include_inherited_context) and not input_message_id:
        from . import resident_conversation_projection_domain

        if include_inherited_context and not resident_conversation_projection_domain.can_view_actual_sent_prompts(actor_id, target.resident_id):
            raise PermissionError("无权读取继承的模型上下文")
        inheritance = resident_fork_inheritance.inheritance_view(
            target.resident_id, target.record, actor_id, include_context=include_inherited_context,
        )
        if inheritance:
            snapshot["inheritance"] = inheritance
    return snapshot


def get_conversation_messages(
    resident_id: str,
    actor: str,
    page_token: str,
    *,
    limit: int = resident_conversation_stream.DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    target = _target(resident_id, actor)
    from . import personal_history
    if page_token.startswith(personal_history.TOKEN_PREFIX):
        page = personal_history.page(target.record, page_token, limit=limit)
        from . import family_chat_preferences
        page["messages"] = family_chat_preferences.filter_messages(target.record, actor, page["messages"])
        return page
    page = resident_conversation_stream.get_repository().messages_page(
        target.resident_id,
        page_token,
        limit=limit,
    )
    facade = family_facade.is_facade(target.record)
    page["messages"] = resident_held_infos.project_page(
        target.record, list(page.get("messages") or []), handed_to_steward=facade,
    )
    if facade:
        page["messages"] = family_facade.project_messages(target.record, list(page.get("messages") or []))
        from . import family_chat_preferences
        page = personal_history.prepend(target.record, page, limit=limit)
        page["messages"] = family_chat_preferences.filter_messages(target.record, actor, page["messages"])
    else:
        page["messages"] = resident_mailbox.project_messages(target.resident_id, page.get("messages") or [])
    return page


def get_input_history(
    resident_id: str,
    actor: str,
    page_token: str = "",
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """What the caller typed in the conversation, newest first: the composer's input history."""

    target = _target(resident_id, actor)
    page = resident_conversation_stream.get_repository().input_history_page(
        target.resident_id, actor, page_token, limit=limit,
    )
    facade = family_facade.is_facade(target.record)
    page["messages"] = resident_held_infos.project_page(
        target.record, list(page.get("messages") or []), handed_to_steward=facade,
    )
    if facade:
        page["messages"] = family_facade.project_messages(target.record, list(page.get("messages") or []))
        from . import family_chat_preferences
        page["messages"] = family_chat_preferences.filter_messages(target.record, actor, page["messages"])
    return page


def search_conversation_messages(
    resident_ids: list[str],
    actor: str,
    query: str,
    *,
    page_token: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    requested = list(dict.fromkeys(
        str(item or "").strip()
        for item in resident_ids
        if str(item or "").strip()
    ))
    visible = [record["resident_id"] for record in
               resident_access.visible_resident_records(requested, actor, "portal")]
    return resident_conversation_stream.get_repository().search_messages(
        visible,
        query,
        page_token=page_token,
        limit=limit,
    )


def get_resident_runtime_settings(
    resident_id: str,
    actor: str,
) -> dict[str, Any]:
    return dict(resident_execution_domain.read_resident_runtime_settings(resident_id, actor))


def get_actual_sent_prompt(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    return dict(
        resident_actual_sent_domain.read_actual_sent_prompt(resident_id, actor, message_id)
    )


def get_resident_input_state(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    include_progress: bool = False,
    include_context: bool = False,
) -> dict[str, Any]:
    # Same read-access rule as the conversation snapshot that references this input.
    require_visible = not resident_input_scope.permits(resident_id, actor, message_id) and not (
        get_portal_access_store().is_service_actor(canonical_actor_id(actor))
    )
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=require_visible)
    return _stored_resident_input_state(
        target.resident_id, message_id, include_progress=include_progress, include_context=include_context,
    )


def _input_in_context(record: Mapping[str, Any], message_id: str, state: str) -> bool:
    """Whether a message reaches, or is still in, the Resident's current context.

    A waiting or running message goes into whatever context the Resident holds
    when it runs.  A finished one stays there until a clear, a new Provider
    session or a compaction takes it out; one the Provider never took is not
    there.  A sender that relies on what an earlier message told the Resident,
    such as the Family Router's brief (decision_44cf587c1721), asks this.
    """

    if state in {"queued", "running"}:
        return True
    from . import execution_client

    return execution_client.message_in_context(
        str(record.get("resident_id") or ""), message_id, since=str(record.get("context_reset_at") or ""),
    )


def _stored_resident_input_state(
    resident_id: str, message_id: str, *, include_progress: bool = False, include_context: bool = False,
) -> dict[str, Any]:
    """Read state after the caller's read or cancellation boundary has admitted it."""
    state = resident_conversation_stream.get_repository().input_state(
        resident_id,
        message_id,
    )
    # A Mailbox group member reports its group and follows it (decision_b5d349190743).
    record = resident_record_domain.find_resident_for_admission(resident_id) or {"resident_id": resident_id}
    projected = resident_mailbox.project_input_state(record, message_id, dict(state))
    if family_facade.is_facade(record):
        return family_facade.project_input_state(
            record, message_id, projected, include_progress=include_progress, include_context=include_context,
        )
    if include_progress:
        # The same progress a Family facade shows under a relayed request: queue place or live work.
        progress = input_progress(
            resident_conversation_stream.get_repository(), resident_id,
            # A Mailbox group member waits and runs as its group.
            str(projected.get("grouped_into_message_id") or message_id), str(projected.get("state") or ""),
        )
        if progress:
            projected = {**projected, "progress": progress}
    if include_context:
        # A Mailbox group member went into the context as its group.
        projected = {**projected, "in_context": _input_in_context(
            record, str(projected.get("grouped_into_message_id") or message_id), str(projected.get("state") or ""),
        )}
    return projected


def get_resident_message(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=not resident_input_scope.permits_message(resident_conversation_stream.get_repository(), resident_id, actor, message_id))
    try:
        return resident_conversation_stream.get_repository().public_message(
            target.resident_id,
            message_id,
        )
    except KeyError:
        # A facade's replies are its members' replies, projected (decision_44cf587c1721).
        projected = (
            family_facade.projected_reply(target.record, message_id)
            if family_facade.is_facade(target.record) else None
        )
        if projected is None:
            raise
        return projected


def _message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()


# A round is read for its topic, so its texts are cut short; the rounds are
# looked for among this many of the newest messages.
ROUND_TEXT_CHARS = 400
ROUND_SCAN_MESSAGES = 200


def _opening(message: Mapping[str, Any] | None) -> str:
    text = _message_text(message or {})
    return text if len(text) <= ROUND_TEXT_CHARS else text[:ROUND_TEXT_CHARS] + "…"


def _sent_to(message: Mapping[str, Any], party: str) -> bool:
    """A message the Resident (or its Family) sent ``party`` of its own accord (decision_c524c775e0be ``recipients``)."""

    return message.get("role") == "agent" and any(
        isinstance(item, Mapping) and str(item.get("id") or "") == party for item in message.get("recipients") or []
    )


def _sent_round(
    message: Mapping[str, Any], target: resident_domain_common.ResidentRuntimeTarget, own: dict[str, str],
) -> dict[str, Any]:
    """A message sent to the sender unasked, as a round; on a facade it is the round of the member that wrote it."""

    writer = own
    initiator = str(((message.get("origin") or {}).get("initiated_by") or {}).get("id") or "")
    if family_facade.is_facade(target.record) and initiator and initiator != target.resident_id:
        record = resident_record_domain.find_resident(initiator) or {}
        writer = {"resident_id": initiator, "title": str(record.get("title") or "")}
    return {
        "kind": "sent",
        "message_id": str(message.get("message_id") or ""),
        "sender": {"kind": "resident", "id": target.resident_id},
        "created_at": str(message.get("created_at") or ""),
        "request": "",
        "answered_by": writer,
        "state": "sent",
        "reply": _opening(message),
    }


def recent_rounds(
    resident_id: str,
    actor: str,
    *,
    sender_id: str = "",
    before_message_id: str = "",
    limit: int = 3,
) -> dict[str, Any]:
    """The last rounds of a Conversation, oldest first: each request, who answered it and how the reply opens.

    A Family Router reads a sender's last rounds to tell whether a new message
    continues one of them (decision_44cf587c1721).  On a facade the one who
    answered is the member the request was relayed to.  A message the
    Resident or its Family sent that sender unasked (``kind`` ``sent``) is a
    round too: a reply to it belongs to the member that wrote it.
    """

    target = _target(resident_id, actor)
    repository = resident_conversation_stream.get_repository()
    snapshot = repository.snapshot(target.resident_id, history_limit=ROUND_SCAN_MESSAGES)
    messages = [item for item in snapshot.get("messages") or [] if isinstance(item, dict)]
    ids = [str(item.get("message_id") or "") for item in messages]
    if before_message_id and before_message_id in ids:
        messages = messages[:ids.index(before_message_id)]
    picked = [
        item for item in messages
        if (item.get("role") == "user" and (not sender_id or str(item.get("author_id") or "") == sender_id))
        or (sender_id and _sent_to(item, sender_id))
    ][-max(1, int(limit or 3)):]
    requests = [item for item in picked if item.get("role") == "user"]
    states = {
        str(item.get("message_id") or ""): item
        for item in snapshot.get("input_states") or []
        if isinstance(item, Mapping)
    }
    relays = (
        family_facade.relayed_rounds(target.record, [str(item.get("message_id") or "") for item in requests])
        if family_facade.is_facade(target.record) else None
    )
    own = {"resident_id": target.resident_id, "title": str(target.record.get("title") or "")}
    rounds = []
    for request in picked:
        if request.get("role") != "user":
            rounds.append(_sent_round(request, target, own))
            continue
        message_id = str(request.get("message_id") or "")
        sender = (request.get("origin") or {}).get("sender") or {}
        state = states.get(message_id) or {}
        if relays is None:
            replies = [
                repository.message(target.resident_id, str(reply_id))
                for reply_id in state.get("reply_message_ids") or []
            ]
            answered_by, result = own, str(state.get("state") or "")
        elif message_id in relays:
            relay, replies = relays[message_id]
            answered_by = {"resident_id": relay["resident_id"], "title": relay["title"]}
            result = relay["state"]
        else:
            replies, answered_by, result = [], {}, str(state.get("state") or "")
        rounds.append({
            "kind": "request",
            "message_id": message_id,
            "sender": {"kind": str(sender.get("kind") or ""), "id": str(request.get("author_id") or "")},
            "created_at": str(request.get("created_at") or ""),
            "request": _opening(request),
            "answered_by": answered_by,
            "state": result,
            "reply": _opening(next((reply for reply in reversed(replies) if reply), None)),
        })
    if relays is None or not sender_id:
        return {"rounds": rounds}
    return {"rounds": rounds, "direct": _direct_member_rounds(target.record, sender_id)}


# A Router reads the sender's latest direct conversations with this many members.
DIRECT_MEMBER_ROUNDS = 3


def _direct_member_rounds(facade: Mapping[str, Any], sender_id: str) -> list[dict[str, Any]]:
    """The sender's latest exchange with each Family member outside the facade, newest first.

    "我们的讨论" may be a conversation the sender had with a member in that
    member's own Chat, which the facade rounds do not show.  The
    copies the facade relays to members do not count.  Each item is the
    sender's last message to the member (``request``) or, when newer, the
    member's last message to the sender (``sent``).
    """

    members = family_facade.family_members(facade)
    if not members:
        return []
    repository = resident_conversation_stream.get_repository()
    summaries = repository.conversation_summaries(
        members, sender_id, excluded_business_ref=family_facade.RELAY_REF.pattern,
    )
    latest = []
    for member_id, summary in summaries.items():
        asked_at = str(summary.get("actor_last_user_message_at") or "")
        told_at = str(summary.get("actor_addressed_at") or "")
        if asked_at and asked_at >= told_at:
            latest.append((asked_at, member_id, "request", str(summary["actor_request_ids"][-1])))
        elif told_at:
            latest.append((told_at, member_id, "sent", str(summary.get("actor_addressed_message_id") or "")))
    latest.sort(reverse=True)
    rounds = []
    for at, member_id, kind, message_id in latest[:DIRECT_MEMBER_ROUNDS]:
        record = resident_record_domain.find_resident(member_id) or {}
        rounds.append({
            "kind": kind, "resident_id": member_id, "title": str(record.get("title") or ""), "created_at": at,
            "message_id": message_id, "text": _opening(repository.message(member_id, message_id)),
        })
    return rounds


def _repository_message(
    repository: resident_conversation_stream.ResidentConversationRepository,
    resident_id: str,
    message_id: object,
) -> dict[str, Any]:
    try:
        return repository.public_message(resident_id, message_id)
    except KeyError:
        return {}


def _completed_input_reference(
    resident_id: str,
    input_state: Mapping[str, Any],
    *,
    requested_by: str = "",
) -> dict[str, Any]:
    repository = resident_conversation_stream.get_repository()
    message_id = str(input_state.get("message_id") or "")
    user_message = _repository_message(repository, resident_id, message_id)
    request_actor = canonical_actor_id(
        user_message.get("author_id") or ""
    )
    requested_actor = canonical_actor_id(requested_by)
    if (
        requested_actor
        and request_actor.casefold() != requested_actor.casefold()
    ):
        return {}
    reply: dict[str, Any] | None = None
    for reply_id in input_state.get("reply_message_ids") or []:
        candidate = _repository_message(repository, resident_id, reply_id)
        if str(candidate.get("role") or "") == "agent":
            reply = candidate
            break
    if reply is None:
        return {}
    return {
        "resident_id": resident_id,
        "message_id": message_id,
        "reply_message_id": str(reply.get("message_id") or ""),
        "completed_at": str(
            reply.get("created_at") or input_state.get("updated_at") or ""
        ),
        "request_actor": request_actor,
    }


def list_completed_message_references(
    resident_id: str,
    actor: str = "",
    *,
    limit: int = 20,
    requested_by: str = "",
) -> list[dict[str, Any]]:
    target_id = _target(resident_id, actor).resident_id
    wanted = max(1, min(int(limit or 20), 200))
    repository = resident_conversation_stream.get_repository()
    # Read the newest inputs in a growing window: long histories hold
    # thousands of completed inputs and filtered ones are rare.
    checked: dict[str, dict[str, Any]] = {}
    window = wanted * 4
    while True:
        states = repository.input_states(target_id, states=("completed",), limit=window)
        references: list[dict[str, Any]] = []
        for state in states:
            message_id = str(state.get("message_id") or "")
            if message_id not in checked:
                checked[message_id] = _completed_input_reference(
                    target_id,
                    state,
                    requested_by=requested_by,
                )
            if checked[message_id]:
                references.append(checked[message_id])
            if len(references) >= wanted:
                return references
        if len(states) < window:
            return references
        window *= 4


def get_latest_completed_message_reference(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    references = list_completed_message_references(
        resident_id,
        actor,
        limit=1,
    )
    return references[0] if references else {}


def get_completed_message_evidence(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    target_id = _target(resident_id, actor).resident_id
    repository = resident_conversation_stream.get_repository()
    state = repository.input_state(target_id, message_id)
    if str(state.get("state") or "") != "completed":
        return {}
    reference = _completed_input_reference(target_id, state)
    if not reference:
        return {}
    user_message = _repository_message(repository, target_id, message_id)
    replies = [
        _repository_message(repository, target_id, reply_id)
        for reply_id in state.get("reply_message_ids") or []
    ]
    return {
        **reference,
        "started_at": str(user_message.get("created_at") or ""),
        "prompt": _message_text(user_message),
        "reply": "\n".join(
            text
            for text in (_message_text(item) for item in replies)
            if text
        ),
        "reply_message_ids": list(state.get("reply_message_ids") or []),
    }


def get_input_reconciliation_state(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    target_id = _target(resident_id, actor).resident_id
    repository = resident_conversation_stream.get_repository()
    try:
        state = repository.input_state(target_id, message_id)
    except KeyError:
        return {"status": "missing", "message_id": message_id, "evidence": {}}
    status = str(state.get("state") or "missing")
    evidence = (
        get_completed_message_evidence(target_id, actor, message_id)
        if status == "completed"
        else {}
    )
    return {
        "status": status if status != "completed" or evidence else "missing",
        "message_id": message_id,
        "completed_at": str(state.get("updated_at") or "") if status in {"completed", "failed", "cancelled"} else "",
        "evidence": evidence,
    }


def list_failed_message_references(
    resident_id: str,
    actor: str = "",
    *,
    limit: int = 20,
    requested_by: str = "",
) -> list[dict[str, Any]]:
    target_id = _target(resident_id, actor).resident_id
    requested_actor = canonical_actor_id(requested_by)
    repository = resident_conversation_stream.get_repository()
    references: list[dict[str, Any]] = []
    for state in repository.input_states(target_id, states=("failed",)):
        message_id = str(state.get("message_id") or "")
        user_message = _repository_message(repository, target_id, message_id)
        request_actor = canonical_actor_id(
            user_message.get("author_id") or ""
        )
        if (
            requested_actor
            and request_actor.casefold() != requested_actor.casefold()
        ):
            continue
        error = state.get("error") if isinstance(state.get("error"), Mapping) else {}
        references.append({
            "resident_id": target_id,
            "message_id": message_id,
            "failed_at": str(state.get("updated_at") or ""),
            "request_actor": request_actor,
            "failure_code": str(error.get("code") or "unknown"),
        })
        if len(references) >= max(1, min(int(limit or 20), 200)):
            break
    return references


def get_subagent_run(
    resident_id: str,
    actor: str,
    trace_ref: str,
    *,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Every step of one subagent run, for whoever may read the Resident's conversation."""

    target = _target(resident_id, actor)
    return resident_conversation_stream.get_repository().subagent_run(
        target.resident_id, trace_ref, offset=offset, limit=limit,
    )


def stop_subagent(resident_id: str, actor: str, execution_id: str, tool_use_id: str) -> dict[str, Any]:
    """Stop one running subagent of the Resident's current turn; the turn goes on.

    The request goes to the running Execution as an input control; the
    Provider then reports the run stopped and its card ends with that.
    """

    import secrets

    from . import execution_client

    target = _target(resident_id, actor)
    if not resident_access.can_manage_resident(target.record, actor):
        raise PermissionError("当前对话无停止权限")
    execution = resident_conversation_stream.get_repository().agent_data.execution(str(execution_id or ""))
    if not execution or execution.get("agent_id") != target.resident_id:
        raise KeyError(str(execution_id or ""))
    # A new control each time: a stop that came before the Provider knew the run may be asked again.
    control_id = "control-" + secrets.token_hex(12)
    receipt = execution_client.service().append_input(
        str(execution_id), actor=actor, input=[{"type": "subagent_stop", "tool_use_id": str(tool_use_id)}],
        control_id=control_id,
    )
    return {
        "control_id": control_id,
        "status": str(receipt.get("status") or "pending"),
        **({"error": str(receipt["error"])} if receipt.get("error") else {}),
    }


def get_resident_artifact(
    resident_id: str,
    actor: str,
    artifact_id: str,
) -> dict[str, Any]:
    target = _target(resident_id, actor)
    return resident_conversation_stream.get_repository().artifact(
        target.resident_id,
        artifact_id,
    )


def get_resident_events(
    resident_id: str,
    actor: str,
    after_cursor: int,
    *,
    limit: int = 500,
    input_message_id: str = "",
) -> dict[str, Any]:
    scoped = bool(input_message_id) and resident_input_scope.permits(resident_id, actor, input_message_id)
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=not scoped)
    result = resident_conversation_stream.get_repository().events_after(
        target.resident_id,
        after_cursor,
        limit=limit,
    )
    state_events = [event for event in result.get("events") or []
                    if event.get("event_type") == resident_conversation_stream.STATE_CHANGED
                    and isinstance((event.get("payload") or {}).get("state"), Mapping)]
    states = resident_execution_gate.project_state_sequence(
        target.resident_id, [event["payload"]["state"] for event in state_events],
        resident_record=target.record,
    )
    for event, state in zip(state_events, states):
        event["payload"] = _with_conversation_controls(
            {**event["payload"], "state": state}, target.resident_id, actor,
            target=target, state_projected=True,
        )
    return resident_input_scope.select_events(result, input_message_id) if input_message_id else result


def merge_resident_queue_items(resident_id: str, actor: str, message_ids: list[str]) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_queue_control_domain.merge_resident_queue_items(resident_id, actor, message_ids), resident_id, actor,
    )


def split_resident_queue_item(resident_id: str, actor: str, message_id: str) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_queue_control_domain.split_resident_queue_item(resident_id, actor, message_id), resident_id, actor,
    )


def reorder_resident_queue(resident_id: str, actor: str, **move: Any) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_queue_control_domain.reorder_resident_queue(resident_id, actor, **move), resident_id, actor,
    )


def requeue_resident_message(resident_id: str, actor: str, message_id: str) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_input_recovery.requeue(resident_id, actor, message_id), resident_id, actor,
    )


def get_resident_mailbox(resident_id: str, actor: str) -> dict[str, Any]:
    return resident_input_recovery.view(resident_id, actor)


def inject_queued_input_into_active_turn(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_queue_control_domain.inject_resident_queue_input_into_active_turn(
            resident_id, actor, message_id
        ), resident_id, actor,
    )


def cancel_resident_service_input(resident_id: str, actor: str, message_id: str) -> dict[str, Any] | None:
    """Cancel only an isolated service input owned by this authenticated caller.

    The immutable input scope is the service's receipt. Unmatched inputs return
    no receipt; their cancellation must use the ordinary ISA boundary.
    """
    scope = resident_input_scope.get(resident_id, message_id)
    if not scope or scope["actor"] != canonical_actor_id(actor):
        return None
    if not get_portal_access_store().is_active(canonical_actor_id(actor)):
        raise PermissionError("Active Portal identity required")
    return cancel_resident_input(resident_id, actor, message_id)


def cancel_resident_input(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    resident_record_domain.resolve_resident_admission_target(resident_id, actor, require_visible=True)
    if resident_input_scope.get(resident_id, message_id):
        for _attempt in range(3):
            state = _stored_resident_input_state(resident_id, message_id)
            if state["state"] not in {"queued", "running"}:
                return {"resident_id": resident_id, "message_id": message_id, "input_state": state}
            active = resident_conversation_stream.get_repository().state_snapshots([resident_id])[resident_id]["state"]
            cancel = resident_execution_domain.request_resident_execution_cancellation if active.get("active_message_id") == message_id else resident_queue_control_domain.cancel_resident_queued_input
            try:
                cancel(resident_id, actor, message_id)
                return {"resident_id": resident_id, "message_id": message_id,
                        "input_state": _stored_resident_input_state(resident_id, message_id)}
            except (KeyError, ValueError):
                refreshed = _stored_resident_input_state(resident_id, message_id)
                if refreshed == state:
                    raise
        raise RuntimeError("Resident input changed repeatedly during cancellation")
    for _attempt in range(3):
        input_state = _stored_resident_input_state(resident_id, message_id)
        state = str(input_state.get("state") or "")
        execution_owned = None
        try:
            if state == "queued":
                control = resident_execution_work.read_control(resident_id).get("state") or {}
                execution_owned = (
                    str(control.get("user_message_id") or "") == str(message_id or "")
                    and not resident_execution_work.is_execution_held(control)
                )
                cancel = cancel_resident_execution if execution_owned else cancel_resident_queue_item
                result = cancel(
                    resident_id,
                    actor,
                    message_id,
                )
            elif state == "running":
                result = cancel_resident_execution(resident_id, actor, message_id)
            else:
                result = {"resident_id": resident_id, "message_id": message_id, "already_terminal": True}
            return {
                **dict(result),
                "message_id": message_id,
                "input_state": _stored_resident_input_state(resident_id, message_id),
            }
        except (KeyError, ValueError):
            refreshed = _stored_resident_input_state(resident_id, message_id)
            if str(refreshed.get("state") or "") == state:
                if execution_owned is not None:
                    current = resident_execution_work.read_control(resident_id).get("state") or {}
                    owns_execution = (
                        str(current.get("user_message_id") or "") == str(message_id or "")
                        and not resident_execution_work.is_execution_held(current)
                    )
                    # Promotion need not change queued: the newly admitted
                    # Execution may immediately wait for quota or capacity.
                    # Retry only a changed owner, not an unchanged queue error.
                    if owns_execution != execution_owned:
                        continue
                lost = _end_lost_input(resident_id, message_id)
                if lost is None:
                    raise
                return {**lost, "input_state": _stored_resident_input_state(resident_id, message_id)}
    raise RuntimeError("Resident input state kept changing during cancellation")


def _end_lost_input(resident_id: str, message_id: str) -> dict[str, Any] | None:
    """End an input its Resident holds nowhere; None while anything still holds it.

    A queued or running input that is in no queue, is not the current work and
    never had an Execution never finishes: the queue dropped it (first turns
    before 10-10), or its run ended without recording an Execution. Its Mailbox
    row leaves with the terminal state.

    An input with an Execution ends with that run's outcome. A finished run
    leaves the work and the active message before its outcome reaches the
    input state (after the state lock is released), so in that moment the input
    looks held by nothing.
    """
    from management_portal.execution import execution_inputs
    from management_portal.execution.agent_data_store import get_agent_data_store
    from . import resident_domain_common, resident_record_domain
    from .state_store import get_lineage_data_store

    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record or family_facade.is_facade(record):
            return None
        if _stored_resident_input_state(resident_id, message_id)["state"] not in {"queued", "running"}:
            return None
        if resident_execution_work.input_location(resident_id, message_id, resident_execution_work.read(record)) is not None:
            return None
        repository = resident_conversation_stream.get_repository()
        if repository.state_snapshots([resident_id])[resident_id]["state"].get("active_message_id") == message_id:
            return None
        entry = get_lineage_data_store().mailbox_entry(resident_id, message_id)
        if entry is not None and entry["place"] != "pending":
            return None
        if execution_inputs.latest(get_agent_data_store(), resident_id, message_id) is not None:
            return None
        repository.set_input_state(
            resident_id, message_id, "cancelled", updated_at=resident_domain_common.now_iso(),
            error={"code": "lost_input", "message": "没有任何工作在处理这条消息，它不会再送达，已撤回"},
        )
    return {"resident_id": resident_id, "message_id": message_id, "cancelled": True}


def cancel_resident_queue_item(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    if resident_input_scope.get(resident_id, message_id):
        return cancel_resident_input(resident_id, actor, message_id)

    try:
        result = resident_queue_control_domain.cancel_resident_queued_input(resident_id, actor, message_id)
    except KeyError:
        result = _end_lost_input(resident_id, message_id)
        if result is None:
            raise
    return _with_conversation_controls(result, resident_id, actor)


def clear_resident_queue(resident_id: str, actor: str) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_queue_control_domain.clear_resident_queued_inputs(resident_id, actor), resident_id, actor,
    )


def cancel_resident_execution(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    continue_queue: bool = True,
) -> dict[str, Any]:
    resident_record_domain.resolve_resident_admission_target(resident_id, actor, require_visible=True)
    if resident_input_scope.get(resident_id, message_id):
        return cancel_resident_input(resident_id, actor, message_id)

    input_state = _stored_resident_input_state(resident_id, message_id)
    if str(input_state.get("state") or "") not in {"queued", "running"}:
        raise ValueError("message is not the active Resident input")
    control = dict(resident_execution_work.read_control(resident_id).get("state") or {})
    if str(control.get("user_message_id") or "") != str(message_id or ""):
        raise ValueError("message is not the active Resident input")
    if (
        str(input_state.get("state") or "") == "queued"
        and resident_execution_work.is_execution_held(control)
    ):
        return cancel_resident_queue_item(resident_id, actor, message_id)
    result = dict(
        resident_execution_domain.request_resident_execution_cancellation(
            resident_id,
            actor,
            message_id,
            continue_queue=continue_queue,
        )
    )
    return _with_conversation_controls(
        {**result, "message_id": str(message_id or "")}, resident_id, actor,
    )


def change_resident_state(
    resident_id: str,
    actor: str,
    *,
    state_domain: str,
    transition: str,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    return _with_conversation_controls(
        resident_management_domain.transition_resident_state(
            resident_id,
            actor,
            state_domain=state_domain,
            transition=transition,
            allow_quota_override=allow_quota_override,
        ), resident_id, actor,
    )


def update_resident_runtime_settings(
    resident_id: str,
    actor: str,
    payload: object = None,
) -> dict[str, Any]:
    return dict(
        resident_execution_domain.change_resident_runtime_settings(
            resident_id,
            actor,
            payload,
        )
    )
