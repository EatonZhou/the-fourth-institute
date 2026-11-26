from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from typing import Any

from . import portal_chat_workspace_projection
from .society_service import get_society_service
from . import discussion_residents, project_resource_discussions
from .resources import get_resource_service


RESIDENT_IMAGE_ROOT = portal_path("uploads") / "resident_chat_images"
def _public_item(
    record: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    project_ids = record.get("project_ids")
    project = str(record.get("project") or "")
    if not project and isinstance(project_ids, list) and project_ids:
        project = str(project_ids[0] or "")
    return {
        "resource_id": str(record.get("resource_id") or ""),
        "path": str(path),
        "filename": path.name,
        "project": project,
        "project_ids": project_resource_discussions.project_ids(record),
        "type": str(
            record.get("type") or record.get("resource_type") or ""
        ),
        "created_at": str(record.get("created_at") or ""),
        "title": str(record.get("title") or record.get("name") or path.stem),
        "mtime": int(path.stat().st_mtime),
    }


class KnowledgeItemChatBackend:
    """Resolve a resource's private discussion identity; sending is the unified Chat flow."""

    def __init__(self, *, resource_service: Any = None):
        self._resources = resource_service or get_resource_service()

    @staticmethod
    def _scope(actor: str, resource_id: str) -> dict[str, str]:
        return {
            "resident_scope": (
                f"resource-chat:{actor}:{resource_id}"
            ),
            "lineage_scope": f"resource-chat:{actor}",
        }

    def _item_snapshot(
        self,
        actor: str,
        resource_id: str,
    ) -> tuple[str, Path, dict[str, Any]]:
        clean_id = str(resource_id or "").strip()
        if not clean_id:
            raise ValueError("kb_id required")
        # A Repo root identifies a directory, not a document with byte content.
        # Resolve it through ResourceService so every read/prepare still
        # checks the caller's resource access before exposing its metadata.
        repo_root = clean_id.startswith("repo_") and "/" not in clean_id
        if repo_root:
            resolved = self._resources.resolve(clean_id, actor=str(actor or ""))
            if resolved.get("resource_type") != "repo":
                raise ValueError("knowledge resource type mismatch")
            if not resolved.get("exists"):
                raise FileNotFoundError(clean_id)
            path = Path(str(resolved.get("checkout_path") or ""))
            if not path.is_absolute():
                raise ValueError("repo resolution requires an absolute checkout path")
        else:
            _data, _content_type, resolved = self._resources.read_content(
                clean_id,
                actor=str(actor or ""),
            )
            path = Path(str(resolved.get("resolved_path") or ""))
        record = resolved.get("record")
        if not isinstance(record, dict):
            raise TypeError("knowledge resource resolution has no record")
        if str(record.get("resource_id") or "") != clean_id:
            raise ValueError("knowledge resource ID mismatch")
        return clean_id, path, _public_item(record, path)

    def _ensure(
        self,
        actor: str,
        resource_id: str,
        item: dict[str, Any],
        *, create: bool = True,
    ) -> dict[str, Any]:
        return discussion_residents.resolve(
            actor=actor, surface="resource", create=create,
            resource_type=str(item.get("type") or ""),
            **self._scope(actor, resource_id),
            title=str(item.get("title") or "Knowledge item"), backend="codex",
            metadata={"surface": "knowledge-detail", "workspace_key": f"knowledge-item:{resource_id}",
                      "resource_id": resource_id, "resource_type": str(item.get("type") or "knowledge"),
                      "resource_path": str(item.get("path") or "")},
        )


    def _state(
        self,
        actor: str,
        resource_id: str,
        item: dict[str, Any],
        *, create: bool = False,
        project_id: str = "",
        require_project_selection: bool = False,
    ) -> dict[str, Any]:
        project_context = project_resource_discussions.context(
            self._resources, actor, item, selected=project_id,
            require_selection=require_project_selection)
        identity = self._ensure(actor, resource_id, item, create=create)
        resident_id = str(identity.get("resident_id") or "")
        composer = {
            "placeholder": "围绕这个条目继续讨论",
            "meta": "Ctrl/⌘ + Enter 发送" if resident_id else "模型：尚未创建对话，可先点击设置选择",
            "can_send": True,
            "can_stop": False,
            "can_attach": True,
            "refs_enabled": True,
            "history_scope": f"resident:{resident_id}" if resident_id else self._scope(actor, resource_id)["resident_scope"],
        }
        contract = portal_chat_workspace_projection.contract(
            workspace_key=f"knowledge-item:{resource_id}",
            surface="knowledge-detail",
            title="Chat Workspace",
            subtitle=str(
                item.get("title") or item.get("filename") or ""
            ),
            target=portal_chat_workspace_projection.chat_target(
                target_kind="resident" if resident_id else "knowledge_item",
                target_id=resident_id or resource_id,
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
                "refs",
                "history",
                "stop",
                "refresh",
                "queue",
                "runtime_settings",
                "slash_commands",
            ] if resident_id else ["attachments", "images", "refs", "history"],
            extensions={
                "knowledge": {
                    "resource_id": resource_id,
                    "path": str(item.get("path") or ""),
                }
            },
            empty_text=(
                "可以围绕这个条目的含义、修改建议、关联条目或后续问题展开讨论。"
            ),
            user_name=actor,
            assistant_name="Agent",
        )
        return {
            "resident_id": resident_id,
            "binding_state": "bound" if resident_id else "unbound",
            "lineage_id": str(identity.get("lineage_id") or ""),
            "project_context": {key: value for key, value in project_context.items() if key != "context_text"},
            "initial_context": {
                "context_text": (
                    f"当前 Resident 讨论资源 {resource_id}。\n"
                    f"标题：{item.get('title') or ''}\n"
                    f"资源路径：{item.get('path') or ''}\n"
                    f"{project_context['context_text']}"
                ),
            },
            "username": actor,
            "item": item,
            "chat_workspace": contract,
        }

    def get_state(self, actor: str, kb_id: str, *, project_id: str = "") -> dict[str, Any]:
        clean_id, _path, item = self._item_snapshot(actor, kb_id)
        return self._state(actor, clean_id, item, project_id=project_id)

    def prepare(self, actor: str, kb_id: str, message: str, *, purpose: str = "send", project_id: str = "") -> dict[str, Any]:
        if purpose not in {"send", "settings"}:
            raise ValueError("unsupported discussion preparation purpose")
        if purpose == "send" and not str(message or "").strip():
            raise ValueError("message required before preparing a discussion")
        clean_id, _path, item = self._item_snapshot(actor, kb_id)
        return self._state(actor, clean_id, item, create=True, project_id=project_id,
                           require_project_selection=purpose == "send")

    def resolve_attachment_image(
        self,
        raw_path: str,
        actor: str,
    ) -> tuple[Path, str]:
        path = Path(str(raw_path or "")).expanduser().resolve(strict=False)
        if not str(raw_path or "").strip():
            raise ValueError("path required")
        root = RESIDENT_IMAGE_ROOT.resolve(strict=False)
        try:
            file_id = path.relative_to(root).as_posix()
        except ValueError:
            raise PermissionError("attachment path is outside Resident storage") from None
        return get_society_service().workspace.resolve_workspace_pasted_image(
            file_id,
            actor,
        )


knowledge_item_chat_backend = KnowledgeItemChatBackend()
