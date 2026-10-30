from __future__ import annotations

import copy
from typing import Any
from urllib.parse import quote

from management_portal.execution import slash_commands

from . import portal_chat_workspace_projection


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _backend_label(backend: object, provider: object = "") -> str:
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS, provider_name

    backend_id = provider_name(backend)
    if backend_id == "provider_api":
        return str(provider or "Model API").strip() or "Model API"
    definition = AGENT_PROVIDERS.get(backend_id)
    return definition.label if definition else (backend_id or "未知")


def project_session(value: object, actor: str = "") -> dict[str, Any]:
    """Add Portal Chat presentation to a neutral Resident session projection."""

    source = _dict(value)
    result = copy.deepcopy(source)
    resident = _dict(source.get("resident"))
    lineage = _dict(source.get("lineage"))
    capabilities = _dict(source.get("backend_capabilities"))
    allows = _dict(source.get("allows"))
    provider_session = _dict(source.get("provider_session"))
    account = _dict(source.get("execution_account"))
    family = _dict(source.get("family_facade"))
    resident_id = str(source.get("resident_id") or resident.get("resident_id") or "").strip()
    backend = str(resident.get("backend") or capabilities.get("backend") or "codex").strip()
    backend_label = _backend_label(backend, provider_session.get("provider"))
    account_id = str(account.get("account_id") or "").strip()
    resident_title = str(resident.get("title") or resident_id or "Resident Agent").strip()
    lineage_title = str(lineage.get("title") or resident.get("lineage_title") or "").strip()
    title_parts = []
    for part in (lineage_title, resident_title):
        if part and part.casefold() not in {item.casefold() for item in title_parts}:
            title_parts.append(part)
    subtitle_parts = [
        *(
            [{
                "text": f"账号 {account_id}",
                "identity": "account",
                "copy_value": account_id,
                "copy_label": "Execution Account",
            }]
            if account_id and not family
            else []
        ),
        {
            "text": resident_id,
            "identity": "resident",
            "copy_value": resident_id,
            "copy_label": "resident_id",
        },
    ]
    can_send = bool(actor and allows.get("DeliverMessage"))
    supports_media = bool(capabilities.get("image_input"))
    runtime_setting_names = capabilities.get("runtime_settings") or []
    supports_restart = bool(capabilities.get("session_restart"))
    composer = {
        "can_send": can_send,
        "can_stop": False,
        "can_attach": supports_media,
        "refs_enabled": True,
        "attachment_upload_context": {"resident_id": resident_id},
        "runtime_backend": backend,
        "runtime_settings": copy.deepcopy(_dict(source.get("runtime_settings"))),
        "slash_commands": slash_commands.slash_command_catalog(backend=backend),
    }
    actions: dict[str, Any] = {}
    if can_send:
        actions["send"] = {
            "instruction": "DeliverMessage",
            "arguments": {"resident_id": resident_id},
        }
        if runtime_setting_names:
            actions["runtime_settings"] = {
                "instruction": "UpdateResidentSettings",
                "arguments": {"resident_id": resident_id},
            }
    if allows.get("RestartSession") and supports_restart:
        actions["session_restart"] = {
            "instruction": "RestartSession",
            "options_url": (
                "/api/agent-workbench/residents/session-restart-options"
                f"?id={quote(resident_id)}"
            ),
            "arguments": {"resident_id": resident_id},
        }
    chat_capabilities = [
        *(["attachments", "images"] if supports_media else []),
        "refs",
        "history",
        "stop",
        "queue",
        "identity_toggle",
        "refresh",
        "message_actions",
        "forward",
        *(["runtime_settings"] if runtime_setting_names else []),
        *(["session_restart"] if supports_restart else []),
    ]
    empty_text = (
        "该派生 Resident 暂无可展示消息。"
        if resident.get("source_resident_id")
        else f"没有找到可展示的 {backend_label} session 历史。"
    )
    assistant_name = backend_label
    if family:
        # A Family facade never runs (decision_44cf587c1721): it has no session
        # and no runtime of its own, and its replies are the Family's.
        assistant_name = str(family.get("title") or resident_title)
        members = "、".join(
            str(item.get("title") or item.get("resident_id") or "")
            for item in family.get("members") or [] if isinstance(item, dict)
        )
        empty_text = (
            f"这是 Family「{assistant_name}」的门面。发到这里的消息由 Family Router 交给最合适的成员处理，"
            "成员的回复显示在这里。还没有经过门面的对话；直接发给成员的对话在各成员自己的对话里。"
            + (f"成员：{members}。" if members else "")
        )
        composer["slash_commands"] = []
        for name in ("runtime_settings", "session_restart"):
            actions.pop(name, None)
        chat_capabilities = [item for item in chat_capabilities if item not in {"runtime_settings", "session_restart"}]
        # The header says whose Chat this is: the Family's, through its facade.
        member_count = len([item for item in family.get("members") or [] if isinstance(item, dict)])
        subtitle_parts.insert(0, {
            "text": f"Family 门面（{member_count} 位成员）" if member_count else "Family 门面",
            "identity": "family",
        })
    contract = portal_chat_workspace_projection.contract(
        workspace_key="resident-chat",
        surface="resident-chat",
        title=f"Chat Workspace - {' - '.join(title_parts)}",
        subtitle=" · ".join(str(part["text"]) for part in subtitle_parts),
        header={"subtitle_parts": subtitle_parts},
        target=portal_chat_workspace_projection.chat_target(
            target_kind="resident",
            target_id=resident_id,
            lineage_id=str(resident.get("lineage_id") or ""),
        ),
        messages=[],
        composer=composer,
        actions=actions,
        capabilities=chat_capabilities,
        empty_text=empty_text,
        user_name=str(actor or "User"),
        assistant_name=assistant_name,
    )
    contract.pop("messages", None)
    contract["messages_ref"] = {
        "instruction": "GetConversation",
        "resident_id": resident_id,
    }
    result["chat_workspace"] = contract
    return result
