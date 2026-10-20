from __future__ import annotations

import re
from typing import Any

SCHEMA = "portal.chat_workspace.v3"
DEFAULT_COMPOSER_HINT = "Ctrl/⌘ + Enter 发送；右键消息可引用、转发；/portal-help 查看指令"
RUNTIME_DETAIL_SUMMARY_CHARS = 420
RUNTIME_MESSAGE_CHARS = 12_000


def _string(value: object) -> str:
    return str(value or "").strip()


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def chat_target(
    *,
    target_kind: str,
    target_id: str,
    lineage_id: str = "",
) -> dict[str, str]:
    kind = _string(target_kind)
    target = _string(target_id)
    if kind not in {"resident", "workspace", "knowledge_item"}:
        raise ValueError("unknown Chat Workspace target kind")
    if not target:
        raise ValueError("Chat Workspace target is required")
    return {
        "kind": kind,
        "id": target,
        "lineage_id": _string(lineage_id),
    }


def _messages(value: object) -> list[dict[str, Any]]:
    items = _list(value)
    identities: set[str] = set()
    messages: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Chat Workspace messages must be objects")
        identity = _string(item.get("id"))
        if not identity:
            raise ValueError("MessageIdentity missing from Chat Workspace message")
        if identity in identities:
            raise ValueError("duplicate MessageIdentity in Chat Workspace")
        identities.add(identity)
        messages.append(item)
    return messages


def runtime_detail_content(kind: str, value: object) -> str:
    text = str(value or "").strip()
    if str(kind or "").strip().lower() == "update":
        if len(text) <= RUNTIME_MESSAGE_CHARS:
            return text
        return text[:RUNTIME_MESSAGE_CHARS].rstrip() + "\n\n[content truncated for portal preview]"
    compact = re.sub(r"\s+", " ", text)
    if len(compact) <= RUNTIME_DETAIL_SUMMARY_CHARS:
        return compact
    return compact[:RUNTIME_DETAIL_SUMMARY_CHARS].rstrip() + "..."


def contract(
    *,
    workspace_key: str,
    surface: str,
    title: str = "Chat Workspace",
    subtitle: str = "",
    header: dict[str, Any] | None = None,
    target: dict[str, Any],
    messages: list[Any] | None = None,
    runtime_details: list[Any] | None = None,
    runtime_live: bool = False,
    composer: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    extensions: dict[str, Any] | None = None,
    empty_text: str = "",
    user_name: str = "",
    assistant_name: str = "Agent",
) -> dict[str, Any]:
    composer_payload = {
        "placeholder": DEFAULT_COMPOSER_HINT,
        "meta": DEFAULT_COMPOSER_HINT,
        "can_send": True,
        "requires_text": False,
        "can_stop": False,
        "can_attach": True,
        "refs_enabled": True,
        "history_scope": workspace_key or surface or "default",
        "capabilities": list(capabilities or []),
    }
    composer_payload.update(_dict(composer))
    if capabilities is not None and "capabilities" not in _dict(composer):
        composer_payload["capabilities"] = list(capabilities)
    target_payload = chat_target_from_payload(target)
    payload = {
        "schema": SCHEMA,
        "workspace_key": _string(workspace_key) or "chat_workspace",
        "surface": _string(surface),
        "title": _string(title) or "Chat Workspace",
        "subtitle": _string(subtitle),
        "target": target_payload,
        "capabilities": list(capabilities or []),
        "messages": _messages(messages),
        "runtime": {
            "live": bool(runtime_live),
            "details": [
                runtime_detail(detail) if isinstance(detail, dict) else detail
                for detail in _list(runtime_details)
            ],
        },
        "composer": composer_payload,
        "actions": _dict(actions),
        "extensions": _dict(extensions),
        "rendering": {
            "empty_text": _string(empty_text),
            "user_name": _string(user_name),
            "assistant_name": _string(assistant_name) or "Agent",
        },
    }
    if header:
        payload["header"] = _dict(header)
    return payload


def chat_target_from_payload(value: object) -> dict[str, str]:
    raw = _dict(value)
    unknown = set(raw) - {"kind", "id", "lineage_id"}
    if unknown:
        raise ValueError(
            "unsupported Chat Workspace target fields: "
            + ", ".join(sorted(unknown))
        )
    return chat_target(
        target_kind=_string(raw.get("kind")),
        target_id=_string(raw.get("id")),
        lineage_id=_string(raw.get("lineage_id")),
    )


def message(
    *,
    role: str,
    content: str,
    message_id: str,
    name: str = "",
    created_at: str = "",
    pending: bool = False,
    context_refs: dict[str, Any] | None = None,
    attachments: list[Any] | None = None,
    runtime_details: list[Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    identity = _string(message_id)
    if not identity:
        raise ValueError("MessageIdentity is required")
    item = {
        "id": identity,
        "role": _string(role) or "assistant",
        "name": _string(name),
        "content": str(content or ""),
        "created_at": _string(created_at),
        "pending": bool(pending),
    }
    if context_refs:
        item["context_refs"] = context_refs
    if attachments:
        item["attachments"] = attachments
    if runtime_details:
        item["runtime_details"] = [
            runtime_detail(detail) if isinstance(detail, dict) else detail
            for detail in runtime_details
        ]
    item.update({key: value for key, value in extra.items() if value is not None})
    return item


def runtime_detail(detail: dict[str, Any]) -> dict[str, Any]:
    projected = dict(detail)
    kind = _string(detail.get("kind"))
    content = str(detail.get("content") or "")
    if kind == "update" and detail.get("status") in {"running", "in_progress", "inprogress"}:
        content = content[-RUNTIME_MESSAGE_CHARS:]
    projected["content"] = runtime_detail_content(kind, content)
    return projected
