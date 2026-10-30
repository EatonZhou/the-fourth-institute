"""Notes on a paper, stored with the paper and as Markdown in its package."""

from __future__ import annotations

from pathlib import Path
import secrets

from management_portal import paper_state_store
from management_portal.paper_records import _now, _slugify
from management_portal.paper_store import (
    LOCK, _all_papers, _delete_bundle_record, _paper_by_id, _paper_store_file,
)


def create_note(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    body = str(payload.get("body") or payload.get("content") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    if not body:
        raise ValueError("note body required")
    with LOCK:
        papers = _all_papers()
        if not any(str(p.get("id") or "") == paper_id for p in papers):
            raise KeyError("paper not found")
        note = {
            "note_id": f"note-{secrets.token_hex(6)}",
            "paper_id": paper_id,
            "body": body,
            "created_by": actor,
            "created_at": _now(),
        }
        paper_state_store.append("notes", note)
        _write_note_markdown(note)
    return note


def _write_note_markdown(note: dict) -> None:
    path = _note_markdown_path(note)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([
        "---",
        f"note_id: {note.get('note_id') or ''}",
        f"paper_id: {note.get('paper_id') or ''}",
        f"created_by: {note.get('created_by') or ''}",
        f"created_at: {note.get('created_at') or ''}",
        "---",
        "",
        str(note.get("body") or ""),
        "",
    ])
    path.write_text(body, encoding="utf-8")


def _note_markdown_path(note: dict) -> Path | None:
    paper = _paper_by_id(str(note.get("paper_id") or ""))
    notes_dir = _paper_store_file(paper or {}, "notes_dir") if paper else None
    if not notes_dir:
        return None
    note_id = _slugify(str(note.get("note_id") or f"note-{secrets.token_hex(4)}"))
    return notes_dir / f"{note_id}.md"


def delete_note(payload: dict, actor: str) -> dict:
    note_id = str(payload.get("note_id") or "").strip()
    if not note_id:
        raise ValueError("note_id required")
    with LOCK:
        target = paper_state_store.get("notes", note_id)
        if not target:
            raise KeyError("note not found")
        _delete_bundle_record("notes", note_id, _note_markdown_path(target))
    return {"deleted": True, "note_id": note_id, "paper_id": str(target.get("paper_id") or ""), "deleted_by": actor, "deleted_at": _now()}
