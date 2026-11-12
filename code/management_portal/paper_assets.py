"""The assets task: extracting a paper's references and figures once its PDF is in the store."""

from __future__ import annotations

from pathlib import Path
import hashlib
import secrets
import time
from typing import Any

from management_common.file_lock import lock_stream
from management_common.process_observation import process_identity, process_is_alive
from management_portal.paper_figures import extract_figures
from management_portal.paper_records import _now
from management_portal.paper_refs import extract_refs
from management_portal.paper_store import (
    PAPER_STORE_ROOT, _local_pdf_for_paper, _paper_by_id, _paper_run_record_by_job_id,
    _update_paper_run_record, _write_paper_run_record, _iter_paper_run_records,
)


PAPER_ASSETS_WORK_ROOT = PAPER_STORE_ROOT / "_work" / "assets"


def _admission_path(paper_id: str) -> Path:
    PAPER_ASSETS_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return PAPER_ASSETS_WORK_ROOT / (hashlib.sha256(paper_id.encode()).hexdigest() + ".lock")


def _latest_assets_job(paper_id: str) -> dict:
    # Finishing an older PDF's job must not make it the current PDF's job.
    return max(_iter_paper_run_records(paper_id, kind="assets"), default={},
               key=lambda job: (int(job.get("created_at_ns") or int(job.get("queued_at") or 0) * 10**9),
                                str(job.get("job_id") or "")))


def assets_job_status(paper_id: str) -> dict:
    """Observe durable state without replaying work or using its stored author."""
    from management_portal import business_tasks

    job = dict(_latest_assets_job(paper_id))
    if job.get("status") in {"queued", "running"}:
        owner = job.get("worker")
        active_here = business_tasks.is_active(f"paper-assets:{job.get('job_id')}")
        if not active_here and (not owner or not process_is_alive(owner)):
            job.update(status="interrupted", recovery_reason=(
                "worker exited" if owner else "legacy task has no recoverable worker"))
    if job:
        job["retryable"] = job.get("status") in {"interrupted", "error", "skipped"}
    return job


def _require_assets_write(paper_id: str, actor: str) -> None:
    from management_portal import permission_system

    if not actor or paper_id not in permission_system.permitted_ids(
        actor, "UpdateResource", "resource_id", [paper_id]
    ):
        raise PermissionError("Paper asset retry requires contributor permission")


def retry_assets(payload: dict, actor: str) -> dict:
    """Retry the observed job under the authenticated caller, never its old actor."""
    from management_portal import business_tasks

    paper_id = str(payload.get("paper_id") or "").strip()
    job_id = str(payload.get("job_id") or "").strip()
    if not paper_id or not job_id:
        raise ValueError("paper_id and job_id required")
    _require_assets_write(paper_id, actor)
    if not _paper_by_id(paper_id):
        raise KeyError("paper not found")
    with _admission_path(paper_id).open("a+b") as stream, lock_stream(stream):
        job = assets_job_status(paper_id)
        if job.get("job_id") != job_id:
            raise ValueError("assets job changed; reload paper detail before retrying")
        reused = not job.get("retryable") or business_tasks.is_active(f"paper-assets:{job_id}")
        if not reused:
            _launch_assets_attempt(paper_id, job, actor=actor, reauthorize=True)
        return {"paper_id": paper_id, "assets_job": assets_job_status(paper_id), "reused": reused}


def _start_assets_task(paper_id: str, *, actor: str = "") -> None:
    paper_id = str(paper_id or "").strip()
    if not paper_id:
        return
    job_id = f"assets-{int(time.time())}-{secrets.token_hex(5)}"
    job = {
        "job_id": job_id,
        "kind": "assets",
        "status": "queued",
        "actor": str(actor or ""),
        "queued_at": _now(),
        "created_at_ns": time.time_ns(),
        "step": "queued",
        "refs": None,
        "figures": None,
        "errors": [],
    }
    with _admission_path(paper_id).open("a+b") as stream, lock_stream(stream):
        _write_paper_run_record(paper_id, job)
        _launch_assets_attempt(paper_id, job, actor=actor)


def _launch_assets_attempt(paper_id: str, job: dict, *, actor: str, reauthorize: bool = False) -> None:
    from management_portal import business_tasks

    job_id = job["job_id"]
    attempt_id = secrets.token_hex(12)
    _update_paper_run_record(paper_id, {
        "job_id": job_id, "kind": "assets", "status": "queued", "step": "queued",
        "actor": actor, "worker": process_identity(), "attempt_id": attempt_id,
        "attempt": int(job.get("attempt") or 0) + 1, "attempted_at": _now(),
        "started_at": None, "finished_at": None, "error": "", "errors": [],
        "refs": None, "figures": None, "reason": "",
    })

    def fail(exc: BaseException) -> None:
        _update_attempt(paper_id, attempt_id, {
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
            attempt_id=attempt_id,
            reauthorize=reauthorize,
            on_error=fail,
        )
    except Exception as exc:
        _update_attempt(paper_id, attempt_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "step": "launch error",
            "finished_at": _now(),
            "error": f"Failed to run assets business task: {exc}",
        })
        print(f"[paper-assets] failed to launch for {paper_id}: {exc}", flush=True)


def _current_attempt(paper_id: str, job_id: str, attempt_id: str) -> bool:
    if not attempt_id:  # Direct synchronous internal extraction.
        return True
    current = _latest_assets_job(paper_id)
    return current.get("job_id") == job_id and current.get("attempt_id") == attempt_id


def _update_attempt(paper_id: str, attempt_id: str, update: dict) -> None:
    if _current_attempt(paper_id, update["job_id"], attempt_id):
        _update_paper_run_record(paper_id, update)


def _paper_asset_lock_path() -> Path:
    PAPER_ASSETS_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return PAPER_ASSETS_WORK_ROOT / "paper-assets.lock"


def _run_assets_job(paper_id: str, *, actor: str = "", job_id: str = "",
                    attempt_id: str = "", reauthorize: bool = False) -> dict:
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
            if not _current_attempt(paper_id, job_id, attempt_id):
                return {"paper_id": paper_id, "skipped": True, "reason": "superseded attempt"}
            if reauthorize:
                _require_assets_write(paper_id, actor)
            _update_attempt(paper_id, attempt_id, {
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
                _update_attempt(paper_id, attempt_id, {
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
            _update_attempt(paper_id, attempt_id, {
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
        _update_attempt(paper_id, attempt_id, {
            "job_id": job_id,
            "kind": "assets",
            "status": "error",
            "actor": str(actor or ""),
            "finished_at": _now(),
            "step": "error",
            "error": str(exc),
        })
        raise
