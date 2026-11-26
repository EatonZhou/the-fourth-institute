"""WALL candidate catalog for Saved Searches."""
from __future__ import annotations

import re


SURFACES = {"wall"}


def _surface(value: object) -> str:
    surface = str(value or "").strip().lower()
    if surface not in SURFACES:
        raise ValueError("unsupported semantic search surface")
    return surface


def _compact_text(value: object) -> str:
    """Whitespace-normalized text, in full: the search layer sets no size limit."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _wall_catalog(project: str, actor: str) -> list[dict]:
    from management_portal import wall_backend

    payload = wall_backend.board(project, actor)
    if payload.get("loading"):
        raise ValueError("WALL 正在读取工程文档，请等待墙面加载后刷新语义搜索。")
    return [
        {
            "item_id": block["id"],
            "project": project,
            "title": block.get("title", ""),
            "type": block.get("kind", ""),
            "status": block.get("status", ""),
            "importance": block.get("importance"),
            "module_path": block.get("module_path", []),
            "domain": block.get("domain", ""),
            "source_id": block.get("source_id", ""),
            "source_title": block.get("source_title", ""),
            "preview": _compact_text(block.get("preview")),
            "content": _compact_text(block.get("content")),
        }
        for block in payload.get("blocks", [])
    ]


def search_catalog(job: dict) -> list[dict]:
    """The items a board search ranks: what its creator can see on the job's board."""
    _surface(job.get("surface"))
    return _wall_catalog(str(job.get("project") or ""), str(job.get("created_by") or ""))
