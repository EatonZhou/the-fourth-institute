"""The assets task: extracting a paper's references and figures once its PDF is in the store."""

from __future__ import annotations

from pathlib import Path
import secrets
import time
from typing import Any

from management_common.file_lock import lock_stream
from management_portal.paper_figures import extract_figures
from management_portal.paper_records import _now
from management_portal.paper_refs import extract_refs
from management_portal.paper_store import (
    PAPER_STORE_ROOT, _local_pdf_for_paper, _paper_by_id, _paper_run_record_by_job_id,
    _update_paper_run_record, _write_paper_run_record,
)


PAPER_ASSETS_WORK_ROOT = PAPER_STORE_ROOT / "_work" / "assets"


def _start_assets_task(paper_id: str, *, actor: str = "") -> None:
    from management_portal import business_tasks

    paper_id = str(paper_id or "").strip()
    if not paper_id:
        return
    job_id = f"assets-{int(time.time())}-{secrets.token_hex(5)}"
    _write_paper_run_record(paper_id, {
        "job_id": job_id,
        "kind": "assets",
        "status": "queued",
        "actor": str(actor or ""),
        "queued_at": _now(),
        "step": "queued",
        "refs": None,
        "figures": None,
        "errors": [],
    })
    def fail(exc: BaseException) -> None:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "step": "error",
            "finished_at": _now(),
            "error": str(exc),
        })

    try:
        business_tasks.start(
            f"paper-assets:{job_id}",
            _run_assets_job,
            paper_id,
            actor=actor,
            job_id=job_id,
            on_error=fail,
        )
    except Exception as exc:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "step": "launch error",
            "finished_at": _now(),
            "error": f"Failed to run assets business task: {exc}",
        })
        print(f"[paper-assets] failed to launch for {paper_id}: {exc}", flush=True)


def _paper_asset_lock_path() -> Path:
    PAPER_ASSETS_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return PAPER_ASSETS_WORK_ROOT / "paper-assets.lock"


def _run_assets_job(paper_id: str, *, actor: str = "", job_id: str = "") -> dict:
    paper_id = str(paper_id or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    job_id = str(job_id or f"manual-assets-{secrets.token_hex(4)}").strip()
    existing = _paper_run_record_by_job_id(paper_id, job_id)
    if not existing:
        _write_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "queued",
            "actor": str(actor or ""),
            "queued_at": _now(),
            "step": "queued",
            "errors": [],
        })
    lock_path = _paper_asset_lock_path()
    try:
        with lock_path.open("a+b") as lock_file, lock_stream(lock_file):
            _update_paper_run_record(paper_id, {
                "job_id": job_id,
                "kind": "assets",
                "status": "running",
                "actor": str(actor or ""),
                "started_at": _now(),
                "step": "extracting refs and figures",
                "error": "",
                "errors": [],
            })
            paper = _paper_by_id(paper_id)
            if not paper:
                raise KeyError(f"paper not found: {paper_id}")
            try:
                pdf_path = _local_pdf_for_paper(paper)
            except FileNotFoundError:
                pdf_path = None
            if not pdf_path or not pdf_path.is_file():
                print(f"[paper-assets] skip {paper_id}: local PDF unavailable", flush=True)
                result = {"paper_id": paper_id, "skipped": True, "reason": "local PDF unavailable"}
                _update_paper_run_record(paper_id, {
                    "job_id": job_id,
                    "kind": "assets",
                    "status": "skipped",
                    "actor": str(actor or ""),
                    "finished_at": _now(),
                    "reason": result["reason"],
                    "refs": None,
                    "figures": None,
                    "errors": [],
                })
                return result
            result: dict[str, Any] = {"paper_id": paper_id, "skipped": False, "refs": None, "figures": None, "errors": []}
            try:
                refs = extract_refs({"paper_id": paper_id}, actor)
                result["refs"] = int(refs.get("count") or 0)
            except Exception as exc:
                message = f"refs failed: {exc}"
                print(f"[paper-assets] {paper_id} {message}", flush=True)
                result["errors"].append(message)
            try:
                figures = extract_figures({"paper_id": paper_id}, actor)
                result["figures"] = int(figures.get("count") or 0)
            except Exception as exc:
                message = f"figures failed: {exc}"
                print(f"[paper-assets] {paper_id} {message}", flush=True)
                result["errors"].append(message)
            final_status = "error" if result.get("errors") else "done"
            _update_paper_run_record(paper_id, {
                "job_id": job_id,
                "kind": "assets",
                "status": final_status,
                "actor": str(actor or ""),
                "finished_at": _now(),
                "step": final_status,
                "refs": result.get("refs"),
                "figures": result.get("figures"),
                "errors": result.get("errors") or [],
            })
            print(
                f"[paper-assets] done {paper_id}: refs={result.get('refs')} figures={result.get('figures')} errors={len(result.get('errors') or [])}",
                flush=True,
            )
            return result
    except Exception as exc:
        _update_paper_run_record(paper_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "actor": str(actor or ""),
            "finished_at": _now(),
            "step": "error",
            "error": str(exc),
        })
        raise
