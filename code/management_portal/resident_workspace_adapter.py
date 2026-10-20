from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from typing import Any

from . import portal_chat_workspace_projection
from . import discussion_residents
from .resources import get_resource_service


PORTAL_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = (portal_path("share_root") / "Projects")


def _workspace_identity(
    actor: str,
    surface: str,
    workspace_key: str,
) -> dict[str, str]:
    return {
        "resident_scope": (
            f"surface-chat:{actor}:{surface}:{workspace_key}"
        ),
        "lineage_scope": f"surface-chat:{actor}:{surface}",
    }


def _context_for(
    surface: str,
    workspace_key: str,
    message: str,
    context: dict[str, Any] | None = None,
    *,
    include_prompt_context: bool = True,
    actor: str = "",
) -> dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    if surface == "project":
        project_id = str(context.get("project_id") or "").strip()
        if not project_id:
            raise ValueError(
                "project_id is required for a project Chat Workspace"
            )
        if workspace_key != f"project:{project_id}":
            raise ValueError("workspace_key does not match project_id")
        payload = get_resource_service().action(
            project_id, "summary.read", resource_type="project", actor=actor,
        )
        payload["id"] = str(payload.pop("resource_id", project_id))
        status = 200
        title = str(
            payload.get("name") or payload.get("id") or project_id
        ).strip()
        project_path = str(payload.get("path") or "").strip()
        summary_path = str(payload.get("summary_path") or "").strip()
        summary_text = str(payload.get("text") or "").strip()
        display = {
            "title": f"{title} Chat",
            "subtitle": project_path or "Project discussion",
            "placeholder": "讨论这个项目，Ctrl/⌘ + Enter 发送",
            "empty_text": (
                "可以围绕这个项目的目标、summary、任务和后续计划展开讨论。"
            ),
            "project_id": project_id,
        }
        if not include_prompt_context:
            return display
        display.update(
            {
                "working_directory": project_path,
                "prompt_context": (
                    "# Current Project Context\n\n"
                    "This is the exact project currently selected in Portal. "
                    "It supersedes conflicting assumptions from earlier turns.\n\n"
                    f"- project_id: {payload.get('id') or project_id}\n"
                    f"- project_path: {project_path}\n"
                    f"- summary_path: {summary_path}\n"
                    f"- summary_status: HTTP {status}\n\n"
                    "Current summary.md:\n```markdown\n"
                    f"{summary_text or '(empty)'}\n```"
                ),
            }
        )
        return display
    if surface == "paper":
        from . import paper_backend

        paper_id = str(context.get("paper_id") or "").strip()
        if workspace_key.startswith("paper:") and not paper_id:
            raise ValueError(
                "paper_id is required for a paper Chat Workspace"
            )
        if paper_id and workspace_key != f"paper:{paper_id}":
            raise ValueError("workspace_key does not match paper_id")
        if not include_prompt_context:
            return paper_backend.chat_workspace_display_context(
                paper_id=paper_id,
                collection_label=str(
                    context.get("collection_label") or ""
                ),
            )
        return paper_backend.chat_workspace_prompt_context(
            paper_id=paper_id,
            query=message,
            paper_ids=[
                str(item)
                for item in context.get("paper_ids") or []
            ],
            collection_items=[
                item
                for item in context.get("collection_items") or []
                if isinstance(item, dict)
            ],
            collection_label=str(context.get("collection_label") or ""),
            collection_summary=str(
                context.get("collection_summary") or ""
            ),
            collection_mode=str(context.get("collection_mode") or ""),
            collection_query=str(context.get("collection_query") or ""),
            collection_filters=(
                context.get("collection_filters")
                if isinstance(context.get("collection_filters"), dict)
                else {}
            ),
        )
    return {
        "title": str(context.get("title") or "Chat Workspace"),
        "subtitle": str(context.get("subtitle") or surface or "Chat"),
        "placeholder": str(context.get("placeholder") or "输入消息，Ctrl/⌘ + Enter 发送"),
        "empty_text": str(
            context.get("empty_text") or "围绕这个工作区开始对话。"
        ),
        "prompt_context": str(context.get("prompt_context") or ""),
    }


def _runtime_context(
    *,
    actor: str,
    surface: str,
    workspace_key: str,
    context: dict[str, Any],
) -> str:
    prompt_context = str(context.get("prompt_context") or "").strip()
    return (
        "You are serving a TFI Portal Chat Workspace.\n"
        f"- actor: {actor}\n"
        f"- surface: {surface}\n"
        f"- workspace_key: {workspace_key}\n\n"
        "The current workspace context below is authoritative for this turn.\n\n"
        f"{prompt_context or '(no additional workspace context)'}"
    )

def _cwd(context: dict[str, Any]) -> str:
    raw = str(context.get("working_directory") or "").strip()
    if not raw:
        return str(PORTAL_ROOT)
    path = Path(raw).expanduser().resolve(strict=False)
    projects_root = PROJECTS_ROOT.resolve(strict=False)
    if path.is_dir() and (path == projects_root or projects_root in path.parents):
        return str(path)
    return str(PORTAL_ROOT)


def _ensure(actor: str, surface: str, workspace_key: str, context: dict[str, Any], *, create: bool = True) -> dict:
    identity = _workspace_identity(actor, surface, workspace_key)
    options = dict(actor=actor, **identity, title=str(context.get("title") or "Chat Workspace"),
                   backend="codex", cwd=_cwd(context),
                   metadata={"surface": surface, "workspace_key": workspace_key,
                             "project_id": str(context.get("project_id") or ""),
                             "paper_id": str(context.get("paper_id") or "")})
    return discussion_residents.resolve(surface=surface, create=create, **options)


def _project(
    actor: str,
    surface: str,
    workspace_key: str,
    context: dict[str, Any],
    *, create: bool = False,
) -> dict[str, Any]:
    identity = _ensure(actor, surface, workspace_key, context, create=create)
    resident_id = str(identity.get("resident_id") or "")
    composer = {
        "placeholder": str(context.get("placeholder") or "输入消息，Ctrl/⌘ + Enter 发送"),
        "meta": "Ctrl/⌘ + Enter 发送",
        "can_send": True,
        "can_stop": False,
        "can_attach": True,
        "refs_enabled": False,
        "history_scope": f"resident:{resident_id}" if resident_id else _workspace_identity(actor, surface, workspace_key)["resident_scope"],
    }
    contract = portal_chat_workspace_projection.contract(
        workspace_key=workspace_key,
        surface=surface,
        title=str(context.get("title") or "Chat Workspace"),
        subtitle=str(context.get("subtitle") or "Resident 会话"),
        target=portal_chat_workspace_projection.chat_target(
            target_kind="resident" if resident_id else "workspace",
            target_id=resident_id or workspace_key,
            lineage_id=str(identity.get("lineage_id") or ""),
        ),
        messages=[],
        runtime_details=[],
        runtime_live=False,
        composer=composer,
        actions={
            "runtime_settings": {
                "instruction": "UpdateResidentSettings",
                "arguments": {"resident_id": resident_id},
            }
        } if resident_id else {},
        capabilities=[
            "attachments",
            "images",
            "history",
            "refresh",
            "stop",
            "queue",
            "runtime_settings",
            "slash_commands",
        ] if resident_id else ["attachments", "images", "history"],
        extensions={},
        empty_text=str(
            context.get("empty_text") or "围绕这个工作区开始对话。"
        ),
        user_name=actor,
        assistant_name="Agent",
    )
    return {
        "resident_id": resident_id,
        "binding_state": "bound" if resident_id else "unbound",
        "lineage_id": str(identity.get("lineage_id") or ""),
        "initial_context": {
            "context_text": _runtime_context(
                actor=actor, surface=surface, workspace_key=workspace_key, context=context,
            ),
        },
        "session": {
            "workspace_key": workspace_key,
            "surface": surface,
            "resident_id": resident_id,
            "title": str(context.get("title") or "Chat Workspace"),
        },
        "chat_workspace": contract,
    }


def get_workspace(
    actor: str,
    surface: str,
    workspace_key: str,
    context: dict[str, Any] | None = None,
    *, create: bool = False,
) -> dict[str, Any]:
    clean_actor = str(actor or "").strip()
    if not clean_actor:
        raise PermissionError("Authenticated discussion owner required")
    clean_surface = str(surface or "").strip() or "default"
    clean_key = str(workspace_key or "").strip() or "main"
    supplied = context if isinstance(context, dict) else {}
    # Session reads need display metadata; send preparation owns prompt context.
    display = _context_for(
        clean_surface,
        clean_key,
        str(supplied.get("query") or ""),
        supplied,
        include_prompt_context=create, actor=clean_actor,
    )
    return _project(
        clean_actor,
        clean_surface,
        clean_key,
        {**supplied, **display}, create=create,
    )


def prepare_workspace(actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not str(payload.get("message") or "").strip():
        raise ValueError("message required before preparing a discussion")
    return get_workspace(actor, str(payload.get("surface") or ""), str(payload.get("workspace_key") or ""),
                         payload.get("context") or {}, create=True)
