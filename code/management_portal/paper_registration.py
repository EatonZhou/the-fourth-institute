"""Adding papers: registration with an uploaded PDF and its metadata draft, and replacing or deleting a paper's PDF."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import secrets

from management_common.principal import is_user_or_resident
from management_portal.paper_assets import _start_assets_task
from management_portal.paper_detail import get_paper_detail
from management_portal.paper_pdf import _extract_pdf_text_file, _has_pdf_header
from management_portal.paper_records import _is_url, _now
from management_portal.paper_registration_metadata import (
    _ai_paper_registration_metadata, _deterministic_paper_registration_metadata,
    _heuristic_paper_registration_classification, _identifier_paper_registration_metadata,
)
from management_portal.paper_store import (
    LOCK, PAPER_STORE, _load_json, _paper_store_file, _safe_file_path, _update_paper_fields, _validate_local_pdf_source, _write_json,
)
from management_portal.paper_summaries import _start_summary_task

LOGGER = logging.getLogger(__name__)

PAPER_PDF_UPLOAD_MAX_BYTES = 512 * 1024 * 1024


def register_paper(payload: dict, actor: str, *, operation_id: str = "") -> dict:
    """Register or relate a Paper; callers retain an explicit key for retries.

    An omitted key starts a new creation operation. Literature matching still
    relates an already held Paper independently of request identity.
    """
    clean_payload = dict(payload)
    upload_id = str(clean_payload.pop("registration_upload_id", "") or "").strip()
    draft = _paper_registration_draft(upload_id, actor=actor) if upload_id else {}
    staged_pdf = _paper_registration_draft_pdf(upload_id) if upload_id else None
    if staged_pdf and not staged_pdf.is_file():
        raise FileNotFoundError("uploaded registration PDF not found")
    draft_metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    for key in (
        "title", "authors", "venue", "year", "session", "abstract", "tags", "workload",
        "platform", "architecture", "optimization", "doi", "arxiv",
    ):
        # Only omitted fields inherit extraction defaults. An explicit empty
        # value is a user correction, including while extraction is running.
        if key not in clean_payload:
            clean_payload[key] = draft_metadata.get(key)
    if staged_pdf:
        clean_payload["pdf_path"] = str(staged_pdf)
    pdf_source = str(clean_payload.get("pdf_path") or "").strip()
    _validate_local_pdf_source(pdf_source)
    from management_portal.resources.directory_registrations import directory_writer
    with LOCK:
        # An existing creation operation owns its original identity and input.
        # Only a new operation performs the library's literature deduplication.
        retry = PAPER_STORE.has_creation_operation(clean_payload, actor, operation_id)
        reused = '' if retry else PAPER_STORE.find_existing_id(clean_payload)
        paper = (PAPER_STORE.relate_existing(reused, clean_payload, actor) if reused
                 else PAPER_STORE.register(clean_payload, actor, operation_id=operation_id))
        paper_id = str(paper.get("id") or "")
        # Creation commits its intent before publishing files. Only accepted
        # upload ownership belongs to this subsequent directory transaction.
        with directory_writer('paper') as writer:
            current_pdf = _paper_store_file(paper, "pdf")
            if staged_pdf and (not current_pdf or not current_pdf.is_file()):
                found = PAPER_STORE.manifest_for_paper_id(paper_id)
                if not found:
                    raise KeyError("paper store manifest not found")
                manifest_path, manifest = found
                paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
                target = paper_dir / "paper.pdf"
                PAPER_STORE.ingest_file(str(staged_pdf), target, require_pdf=True)
                paper = PAPER_STORE.update_paper(
                    paper_id,
                    {"pdf_path": str(target), "status": "registered"},
                ) or paper
            if staged_pdf:
                from management_common.upload_retention import consumed
                consumed(staged_pdf.parent, owner_id=paper_id, connection=writer.connection)
            elif pdf_source and not _is_url(pdf_source) and current_pdf and current_pdf.is_file():
                from management_portal.resources.upload_consumption import consume_file
                consume_file(Path(pdf_source), owner_id=paper_id, connection=writer.connection)
    if not reused or staged_pdf:
        # Registering a paper the library already holds only merges its relations
        # (e.g. relating it to another Project); its summary and assets stay.
        _start_summary_task(str(paper.get("id") or ""), actor=actor)
        _start_assets_task(str(paper.get("id") or ""), actor=actor)
    return get_paper_detail(paper["id"])


def _validated_pdf_upload(payload: dict) -> tuple[bytes, str]:
    data = payload.get("data_bytes")
    filename = str(payload.get("filename") or "paper.pdf").strip()
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("pdf file required")
    if len(data) > PAPER_PDF_UPLOAD_MAX_BYTES:
        raise ValueError("pdf file is too large")
    if filename and not filename.lower().endswith(".pdf"):
        raise ValueError("uploaded file must be a PDF")
    pdf = bytes(data)
    if not _has_pdf_header(pdf):
        raise ValueError("uploaded file does not look like a PDF")
    return pdf, filename or "paper.pdf"


def _validated_paper_registration_upload_id(upload_id: object) -> str:
    value = str(upload_id or "").strip()
    if not re.fullmatch(r"paper-upload-[0-9a-f]{12}", value):
        raise ValueError("invalid paper registration upload id")
    return value


def _paper_registration_draft_dir(upload_id: object) -> Path:
    value = _validated_paper_registration_upload_id(upload_id)
    return PAPER_STORE.root / "_incoming" / "registration-drafts" / value


def _paper_registration_draft_pdf(upload_id: object) -> Path:
    return _paper_registration_draft_dir(upload_id) / "paper.pdf"


def _paper_registration_draft_state_path(upload_id: object) -> Path:
    return _paper_registration_draft_dir(upload_id) / "draft.json"


def _public_paper_registration_draft(draft: dict) -> dict:
    return {
        key: draft.get(key)
        for key in (
            "upload_id", "filename", "status", "step", "created_at", "updated_at",
            "metadata", "warnings", "error", "usage",
        )
        if draft.get(key) not in (None, "")
    }


def _paper_registration_draft(upload_id: object, *, actor: str) -> dict:
    path = _paper_registration_draft_state_path(upload_id)
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    if get_directory_cleanup_store().pending_at(path.parent):
        raise KeyError('paper registration upload has expired')
    draft = _load_json(path, {})
    if not draft:
        raise KeyError("paper registration upload not found")
    if not is_user_or_resident(actor):
        raise PermissionError("paper registration upload needs a User or Resident")
    return draft


def _set_paper_registration_draft(upload_id: object, patch: dict) -> dict:
    path = _paper_registration_draft_state_path(upload_id)
    with LOCK:
        current = _load_json(path, {})
        if not current:
            raise KeyError("paper registration upload not found")
        updated = {**current, **patch, "updated_at": _now()}
        _write_json(path, updated)
    return updated


def create_paper_registration_draft(payload: dict, actor: str) -> dict:
    from management_portal import business_tasks

    pdf, filename = _validated_pdf_upload(payload)
    upload_id = f"paper-upload-{secrets.token_hex(6)}"
    draft_dir = _paper_registration_draft_dir(upload_id)
    from datetime import datetime, timezone
    from management_common.directory_retention import RetentionStore
    created_at = _now()
    RetentionStore().declare(draft_dir, category='unused_upload', owner_id=upload_id,
                             created_at=datetime.fromtimestamp(created_at, timezone.utc))
    draft_dir.mkdir(parents=True, exist_ok=False)
    _paper_registration_draft_pdf(upload_id).write_bytes(pdf)
    draft = {
        "schema": "tfi.paper_registration_draft.v1",
        "upload_id": upload_id,
        "filename": filename,
        "actor": str(actor or ""),
        "status": "queued",
        "step": "queued",
        "created_at": created_at,
        "updated_at": _now(),
        "metadata": {},
        "warnings": [],
        "error": "",
        "usage": {},
    }
    _write_json(_paper_registration_draft_state_path(upload_id), draft)

    def fail(exc: BaseException) -> None:
        try:
            _set_paper_registration_draft(upload_id, {
                "status": "error",
                "step": "error",
                "error": str(exc),
            })
        except Exception:
            LOGGER.exception("Paper registration draft %s failed (%s) and its error was not recorded",
                             upload_id, type(exc).__name__)

    try:
        business_tasks.start(
            f"paper-registration-draft:{upload_id}",
            _run_paper_registration_draft,
            upload_id,
            on_error=fail,
        )
    except Exception as exc:
        fail(exc)
    return _public_paper_registration_draft(
        _paper_registration_draft(upload_id, actor=actor)
    )


def get_paper_registration_draft(upload_id: str, actor: str) -> dict:
    return _public_paper_registration_draft(
        _paper_registration_draft(upload_id, actor=actor)
    )


def _run_paper_registration_draft(upload_id: str) -> dict:
    draft = _load_json(_paper_registration_draft_state_path(upload_id), {})
    if not draft:
        raise KeyError("paper registration upload not found")
    pdf_path = _paper_registration_draft_pdf(upload_id)
    warnings: list[str] = []
    _set_paper_registration_draft(upload_id, {
        "status": "running",
        "step": "extracting PDF text",
        "error": "",
    })
    text_path = _paper_registration_draft_dir(upload_id) / "pdf.txt"
    try:
        pdf_text = _extract_pdf_text_file(pdf_path, cache_path=text_path)
    except Exception as exc:
        pdf_text = ""
        warnings.append(f"PDF text extraction failed: {exc}")
    metadata = _deterministic_paper_registration_metadata(
        pdf_path,
        filename=str(draft.get("filename") or "paper.pdf"),
        pdf_text=pdf_text,
    )
    identifier_metadata = _identifier_paper_registration_metadata(metadata)
    for key, value in identifier_metadata.items():
        if value not in (None, "", []):
            metadata[key] = value
    usage = {}
    _set_paper_registration_draft(upload_id, {
        "status": "running",
        "step": "extracting metadata",
        "metadata": metadata,
    })
    core_metadata_ready = all(
        str(metadata.get(key) or "").strip()
        for key in ("title", "authors", "year", "abstract")
    )
    if not pdf_text.strip():
        warnings.append("PDF contains no extractable text")
    else:
        heuristic_metadata = _heuristic_paper_registration_classification(metadata, pdf_text)
        for key, value in heuristic_metadata.items():
            if value:
                metadata[key] = value
        _set_paper_registration_draft(upload_id, {
            "status": "running",
            "step": "refining semantic classification",
            "metadata": metadata,
        })
        try:
            # Identifier services provide reliable bibliography, but not Portal's
            # semantic classification fields. Keep the trusted fields and still
            # run one bounded enrichment pass over the abstract/body excerpt.
            ai_source_text = pdf_text
            if identifier_metadata and core_metadata_ready:
                ai_source_text = (
                    "Known authoritative metadata:\n"
                    f"Title: {metadata.get('title') or ''}\n"
                    f"Authors: {metadata.get('authors') or ''}\n"
                    f"Venue: {metadata.get('venue') or ''}\n"
                    f"Year: {metadata.get('year') or ''}\n"
                    f"Abstract: {metadata.get('abstract') or ''}\n\n"
                    f"PDF body excerpt:\n{pdf_text[:20_000]}"
                )
            ai_metadata, usage = _ai_paper_registration_metadata(
                ai_source_text,
                actor=str(draft.get("actor") or ""),
                upload_id=upload_id,
            )
            for key, value in ai_metadata.items():
                if value in (None, "", []):
                    continue
                if key in {"tags", "workload", "platform", "architecture", "optimization"}:
                    metadata[key] = value
                elif identifier_metadata.get(key) in (None, "", []):
                    metadata[key] = value
        except Exception as exc:
            warnings.append(f"AI metadata extraction failed: {exc}")
    status = "done" if metadata.get("title") and (metadata.get("authors") or metadata.get("abstract")) else "partial"
    result = _set_paper_registration_draft(upload_id, {
        "status": status,
        "step": "ready",
        "metadata": metadata,
        "warnings": warnings,
        "usage": usage,
        "error": "",
    })
    return _public_paper_registration_draft(result)


def upload_paper_pdf(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    data, _filename = _validated_pdf_upload(payload)
    with LOCK:
        found = PAPER_STORE.manifest_for_paper_id(paper_id)
        if not found:
            raise KeyError("paper store manifest not found")
        manifest_path, manifest = found
        paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
        try:
            resolved_dir = paper_dir.resolve(strict=False)
            resolved_items = PAPER_STORE.items_root.resolve(strict=False)
        except Exception:
            raise ValueError("paper store path unavailable")
        if resolved_items not in resolved_dir.parents:
            raise ValueError(f"refusing to write PDF outside Paper items: {paper_dir}")
        paper_dir.mkdir(parents=True, exist_ok=True)
        target = paper_dir / "paper.pdf"
        target.write_bytes(data)
        fields = {"pdf_path": str(target)}
        if str(manifest.get("status") or "").strip() == "pdf-missing":
            fields["status"] = "registered"
        updated = _update_paper_fields(paper_id, fields)
        for rel in ("text/pdf.raw.txt", "text/pdf.clean.txt"):
            path = paper_dir / rel
            if path.exists():
                path.unlink()
    updated_id = str(updated.get("id") or paper_id)
    _start_assets_task(updated_id, actor=actor)
    return get_paper_detail(updated_id)


def delete_paper_pdf(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        found = PAPER_STORE.manifest_for_paper_id(paper_id)
        if not found:
            raise KeyError("paper store manifest not found")
        manifest_path, manifest = found
        paper_dir = PAPER_STORE.paper_dir_from_manifest(manifest, manifest_path)
        try:
            resolved_dir = paper_dir.resolve(strict=False)
            resolved_items = PAPER_STORE.items_root.resolve(strict=False)
        except Exception:
            raise ValueError("paper store path unavailable")
        if resolved_items not in resolved_dir.parents:
            raise ValueError(f"refusing to delete PDF outside Paper items: {paper_dir}")

        deleted_paths = []
        for rel in ("paper.pdf", "text/pdf.raw.txt", "text/pdf.clean.txt"):
            path = paper_dir / rel
            if path.exists():
                path.unlink()
                deleted_paths.append(str(path))
        updated = _update_paper_fields(paper_id, {"pdf_path": "", "status": "pdf-missing"})
    detail = get_paper_detail(str(updated.get("id") or paper_id))
    detail["pdf_delete"] = {
        "deleted": True,
        "deleted_paths": deleted_paths,
        "deleted_by": actor,
        "deleted_at": _now(),
    }
    return detail
