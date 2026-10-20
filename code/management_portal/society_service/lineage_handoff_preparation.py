"""External Workspace effects for a Handoff attempt.

The caller owns Handoff state transitions. This module never writes Lineage
state and must be called outside the Lineage state lock.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import lineage_handoff_backend
from .lineage_values import _text

EXPLORATIONS_ROOT = (portal_path("staging_root") / "shared" / "portal_explorations")

def draft_items(
    *,
    lineage_id: str,
    handoff_id: str,
    source_id: str,
    target_title: object,
    payload: dict[str, Any],
    actor: str,
    lineage_folder: Path,
    source_workspace: Callable[[str, str, str, dict[str, Any]], dict[str, Any]],
    root_path: Callable[[object], str],
) -> list[dict[str, Any]]:
    raw_items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if len(raw_items) > 24:
        raise ValueError("Workspace Handoff 最多支持 24 个交接项")
    if not raw_items:
        # A handoff without items hands the source's Workspace over directly.
        raw_items = [{}]
    items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, start=1):
        source_selector = raw_item.get("source") if isinstance(raw_item.get("source"), dict) else {}
        source_root = source_workspace(lineage_id, source_id, actor, source_selector)
        transfer = raw_item.get("transfer") if isinstance(raw_item.get("transfer"), dict) else {}
        mode = _text(transfer.get("mode"), 24).lower() or "direct"
        method = _text(transfer.get("method"), 40).lower() or (
            "direct" if mode == "direct" else "filesystem_copy"
        )
        item_id = _text(raw_item.get("item_id"), 80).lower() or f"item_{index}"
        target = raw_item.get("target") if isinstance(raw_item.get("target"), dict) else {}
        target_path = root_path(target.get("path"))
        if mode == "direct":
            target_path = str(source_root.get("path") or "")
        elif not target_path:
            if method == "git_worktree":
                target_path = str((
                    EXPLORATIONS_ROOT / handoff_id / item_id / "worktree"
                ).resolve(strict=False))
            else:
                target_path = str((
                    lineage_folder / "handoff-workspaces" / handoff_id / item_id
                ).resolve(strict=False))
        items.append({
            "item_id": item_id,
            "label": _text(raw_item.get("label"), 160) or str(source_root.get("label") or f"Workspace {index}"),
            "source": {
                "workspace_id": str(source_root.get("workspace_id") or ""),
                "path": str(source_root.get("path") or ""),
                "scope": source_selector.get("scope") or ".",
            },
            "transfer": {
                "mode": mode, "method": method,
                "parameters": transfer.get("parameters") if isinstance(transfer.get("parameters"), dict) else {},
            },
            "target": {
                "path": target_path, "scope": target.get("scope") or ".",
                "label": _text(target.get("label"), 160) or f"{target_title} · Workspace",
            },
            "attempts": [], "result": {},
        })
    return items


@dataclass(frozen=True)
class HandoffWorkspaceAccess:
    workspaces: Callable[[str, str, str], dict[str, Any]]
    bind: Callable[..., dict[str, Any]]
    allowed_target_roots: Callable[[str], list[Path]]
    root_path: Callable[[object], str]
    now: Callable[[], str]
    max_files: int
    max_bytes: int


def prepare_items(
    lineage_id: str,
    handoff: dict[str, Any],
    actor: str,
    access: HandoffWorkspaceAccess,
    prepared: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Materialize first, then bind each result to the target Resident.

    `prepared` is owned by the caller so partial results can be cleaned up
    if any materializer or binding raises.
    """
    for item in handoff.get("items") or []:
        if not isinstance(item, dict):
            continue
        result = lineage_handoff_backend.materialize_handoff_item(
            item,
            allowed_target_roots=access.allowed_target_roots(lineage_id),
            checked_at=access.now(),
            max_files=access.max_files,
            max_bytes=access.max_bytes,
        )
        prepared.append((item, result))
    for item, result in prepared:
        transfer = item.get("transfer") if isinstance(item.get("transfer"), dict) else {}
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        source_workspace_id = str(source.get("workspace_id") or "")
        target_id = str(handoff.get("target") or "")
        target_workspaces = access.workspaces(lineage_id, target_id, actor)
        existing_root = next(
            (
                root for root in target_workspaces.get("workspaces") or []
                if access.root_path(root.get("path")) == access.root_path(result.get("target_path"))
            ),
            None,
        )
        mounted = (
            access.bind(
                lineage_id, target_id, actor,
                str(result.get("target_path") or ""),
                workspace_id=(source_workspace_id if str(transfer.get("mode") or "") == "direct" else ""),
            )
            if str(transfer.get("mode") or "") == "direct" or not existing_root
            else {"workspace": target_workspaces}
        )
        bound_root = next(
            (
                root for root in (mounted.get("workspace") or {}).get("workspaces") or []
                if access.root_path(root.get("path")) == access.root_path(result.get("target_path"))
            ),
            None,
        )
        if not bound_root:
            raise RuntimeError("目标 Workspace 已准备，但没有形成可验证的 Workspace Binding")
        target_workspace_id = str(bound_root.get("workspace_id") or "")
        if not target_workspace_id:
            raise RuntimeError("目标 Workspace 已绑定，但没有登记规范 Workspace")
        if (str(transfer.get("mode") or "") == "direct" and source_workspace_id
                and target_workspace_id != source_workspace_id):
            raise RuntimeError(
                "direct Handoff 必须把同一个规范 Workspace 绑定给目标 Agent，"
                "不能生成新的 workspace_id"
            )
        result["target_workspace_id"] = target_workspace_id


def cleanup_items(prepared: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    for item, result in reversed(prepared):
        try:
            lineage_handoff_backend.cleanup_materialized_item(item, result)
        except (OSError, ValueError):
            pass
