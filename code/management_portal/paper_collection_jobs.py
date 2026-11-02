"""Paper Collect jobs: starting, listing, recovering and deleting the paper:collector requests."""

from __future__ import annotations

from pathlib import Path
import secrets
import sys

from management_common.format_tags import schema_matches
from management_common.database_time import instant
from management_common.directory_retention import RetentionStore
from management_portal import paper_state_store
from management_portal.paper_records import _now
from management_portal.paper_resident import _extract_json_object
from management_portal.paper_store import LOCK, PAPER_STORE_ROOT, _load_json, _notify_paper_projection

# paper_collector.run(job_id, backend=sys.modules[__name__]) also reads these names from this module.
from management_portal.paper_search import resource_index_papers
from management_portal.paper_store import WORKSPACE_ROOT


PAPER_COLLECTOR_WORK_ROOT = PAPER_STORE_ROOT / "_work" / "collector"


# Legacy or unconfirmed jobs cannot be resumed from a canonical input receipt.
COLLECTOR_STALE_SECONDS = 18 * 60 * 60
_TERMINAL_STATUSES = {"done", "error", "cancelled", "deleted"}


def _all_paper_collection_jobs() -> list[dict]:
    return paper_state_store.records("collector_jobs")


def _set_paper_collection_job(job_id: str, patch: dict) -> dict:
    with LOCK, paper_state_store.locked("collector_jobs") as connection:
        current = _paper_collection_job_by_id(job_id)
        # Late progress/result callbacks cannot resurrect a deleted or ended job.
        # Only deletion and the writer's explicit release can change it further.
        if current and (current.get("deleted") or current.get("status") in _TERMINAL_STATUSES):
            if not patch.get("deleted") and "process_owner" not in patch:
                return _reconcile_paper_collection_retention(current, connection=connection)
            if not patch.get("deleted"):
                patch = {"process_owner": patch["process_owner"]}
        found = paper_state_store.merge("collector_jobs", job_id, {**patch, "updated_at": _now()})
        found = _reconcile_paper_collection_retention(found, connection=connection)
    _notify_paper_projection(job_id, fields=["collector"])
    return found


def _reconcile_paper_collection_retention(job: dict, *, connection) -> dict:
    """Finish only terminal work with no remaining writer, using its stored clock."""
    from management_portal import business_tasks

    if not job.get("deleted") and job.get("status") not in _TERMINAL_STATUSES:
        return job
    if business_tasks.is_active(f"paper-collection:{job['job_id']}"):
        return job
    if job.get("process_owner"):
        if business_tasks.process_is_alive(job["process_owner"]):
            return job
        # The terminal update may precede a deleted writer's last file access.
        # Its death is confirmed now; persist that conservative upper bound,
        # rather than pretending the earlier delete timestamp ended the work.
        job = paper_state_store.merge("collector_jobs", job['job_id'], {
            "process_owner": {}, "updated_at": _now(),
        })
    ended_at = job.get("updated_at")
    if not ended_at or ended_at <= 0:
        raise ValueError(f"Collector {job['job_id']} has no confirmed terminal time")
    RetentionStore().finish(job['job_id'], at=instant(ended_at), connection=connection)
    return job


def _finish_paper_collection_job(job_id: str) -> None:
    """The writer acknowledges its last file access, including active deletion.

    The job clock and retention deadline commit together. A dead process that
    misses this acknowledgement is reconciled from its terminal job on listing.
    """
    with LOCK, paper_state_store.locked("collector_jobs") as connection:
        found = _paper_collection_job_by_id(job_id)
        if found is None:
            raise KeyError("paper collection job not found")
        if not found.get('deleted') and found.get('status') not in _TERMINAL_STATUSES:
            # A failed terminal-state commit is not an end receipt. The task
            # host must persist its outcome before retention can release files.
            return
        if not (found.get("process_owner") == {} and (found.get("updated_at") or 0) > 0
                and (found.get("deleted") or found.get("status") in _TERMINAL_STATUSES)):
            found = _set_paper_collection_job(job_id, {"process_owner": {}, "updated_at": _now()})
        RetentionStore().finish(
            job_id, at=instant(found['updated_at']), connection=connection)


def _paper_collection_job_by_id(job_id: str) -> dict | None:
    return paper_state_store.get("collector_jobs", str(job_id or "").strip())


def reconcile_collection_retention() -> dict:
    """Recover ended writers without waiting for a user to open the job list.

    This does not resume inputs or infer that an unfinished job has ended.
    Each terminal job owns its reconciliation; one invalid historical receipt
    must not prevent unrelated jobs from recording their release.
    """
    report = {'checked': 0, 'errors': []}
    for job in _all_paper_collection_jobs():
        if not job.get('deleted') and job.get('status') not in _TERMINAL_STATUSES:
            continue
        try:
            with LOCK, paper_state_store.locked('collector_jobs') as connection:
                current = _paper_collection_job_by_id(job['job_id'])
                if current is not None:
                    _reconcile_paper_collection_retention(current, connection=connection)
                    report['checked'] += 1
        except Exception as error:
            report['errors'].append(f"{job['job_id']}: {error}")
    return report


def _paper_collection_result_items(job: dict | None) -> list[dict]:
    if not job or job.get("deleted"):
        return []
    raw_results = job.get("results") if isinstance(job.get("results"), list) else []
    if not raw_results:
        result_path = str(job.get("result_path") or job.get("stdout_path") or "").strip()
        path = Path(result_path) if result_path else None
        parsed = {}
        if path and path.is_file():
            parsed = _load_json(path, {})
            if not isinstance(parsed, dict):
                try:
                    parsed = _extract_json_object(path.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError):  # unreadable, or no JSON object in the output
                    parsed = {}
        raw_results = parsed.get("registered") if isinstance(parsed.get("registered"), list) else []
    results = []
    seen = set()
    for idx, item in enumerate(raw_results):
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        results.append({
            "paper_id": paper_id,
            "rank": idx + 1,
            "title": str(item.get("title") or "").strip(),
        })
    return results


def _paper_collection_result_map(job_id: str) -> dict[str, dict]:
    job = _paper_collection_job_by_id(job_id)
    result_map = {}
    for item in _paper_collection_result_items(job):
        paper_id = str(item.get("paper_id") or "").strip()
        if paper_id and paper_id not in result_map:
            result_map[paper_id] = item
    return result_map


def _paper_collection_filter_summary(job_id: str) -> dict | None:
    job = _paper_collection_job_by_id(job_id)
    if not job or job.get("deleted"):
        return None
    results = _paper_collection_result_items(job)
    return {
        "job_id": str(job.get("job_id") or ""),
        "direction": str(job.get("direction") or ""),
        "status": str(job.get("status") or ""),
        "result_count": len(results),
        "registered_count": int(job["registered_count"] if job.get("registered_count") is not None else len(results)),
        "summary": str(job.get("summary") or ""),
        "error": str(job.get("error") or ""),
        "updated_at": int(job.get("updated_at") or 0),
    }


def _recover_paper_collection_job(job: dict, actor: str) -> dict:
    from management_portal import business_tasks
    from management_portal.paper_collector import require_actor
    from management_portal.paper_projection import input_receipt

    if (not actor or job.get("created_by") != actor or job.get("deleted")
            or job.get("status") not in {"queued", "running"}
            or not schema_matches(job.get("collector_contract"), "tfi.paper.collector.v2")):
        return job
    try:
        require_actor(actor)
    except PermissionError:
        return job
    job_id = str(job.get("job_id") or "")
    with LOCK, paper_state_store.locked("collector_jobs"):
        current = paper_state_store.get("collector_jobs", job_id)
        if current is None:
            return job
        if (current.get("deleted") or current.get("status") not in {"queued", "running"}
                or current.get("created_by") != actor
                or not schema_matches(current.get("collector_contract"), "tfi.paper.collector.v2")
                or "existing_paper_ids" not in current
                or business_tasks.is_active(f"paper-collection:{job_id}")):
            return current
        try:
            binding = input_receipt(current.get("invocation") or {})
        except ValueError:
            return current
        if (current["invocation"].get("input_identity_status") == "unconfirmed"
                or not all(binding.get(key) for key in ("resident_id", "lineage_id", "input_message_id"))):
            return current
        owner = current.get("process_owner") or {}
        here = business_tasks.process_identity()
        if owner and owner != here and business_tasks.process_is_alive(owner):
            return current
        # Claim observation under the durable store lock. Never manufacture the
        # original caller's credentials or replay a missing/unconfirmed input.
        current = paper_state_store.merge("collector_jobs", job_id, {"process_owner": here})
    return _start_paper_collection_task(current)


def _is_stale_paper_collection_job(job: dict) -> bool:
    from management_portal import business_tasks

    if job.get("deleted") or str(job.get("status") or "") not in {"queued", "running"}:
        return False
    if business_tasks.is_active(f"paper-collection:{job.get('job_id') or ''}"):
        return False
    if job.get("process_owner") and business_tasks.process_is_alive(job["process_owner"]):
        return False
    touched = int(job.get("updated_at") or job.get("created_at") or 0)
    return touched > 0 and _now() - touched > COLLECTOR_STALE_SECONDS


def list_paper_collection_jobs(actor: str = "") -> dict:
    actor = str(actor or "").strip()
    with LOCK, paper_state_store.locked("collector_jobs") as connection:
        jobs = list(_all_paper_collection_jobs())
        # Include hidden deleted jobs: their writer may have exited before its
        # finally block. Retention.finish is idempotent and never extends TTL.
        jobs = [_reconcile_paper_collection_retention(job, connection=connection) for job in jobs]
    jobs = [job for job in jobs if not job.get("deleted")]
    jobs = [_recover_paper_collection_job(job, actor) for job in jobs]
    jobs = [
        _mark_stale_paper_collection_job(str(job.get("job_id") or ""))
        if _is_stale_paper_collection_job(job) else job
        for job in jobs
    ]
    jobs = [job for job in jobs if not job.get("deleted")]
    jobs = sorted(jobs, key=lambda item: int(item.get("updated_at") or item.get("created_at") or 0), reverse=True)
    return {"jobs": [_public_paper_collection_job(job) for job in jobs[:30]]}


def _mark_stale_paper_collection_job(job_id: str) -> dict:
    with LOCK, paper_state_store.locked("collector_jobs"):
        current = _paper_collection_job_by_id(job_id)
        if current is None:
            raise KeyError("paper collection job not found")
        if not _is_stale_paper_collection_job(current):
            return current
        return _set_paper_collection_job(job_id, {
            "status": "error", "step": "stale", "progress": 100,
            "error": "Collector 任务已中断（执行进程重启或长时间没有更新）；请重新收集。",
        })


def start_paper_collection(payload: dict, actor: str = "") -> dict:
    from management_portal import business_tasks
    from management_portal.paper_collector import CONTRACT, MAX_DIRECTION_CHARS, require_actor

    actor = require_actor(actor)
    direction = str(payload.get("direction") or payload.get("query") or "").strip()
    if not direction:
        raise ValueError("collection direction required")
    if len(direction) > MAX_DIRECTION_CHARS:
        raise ValueError(f"collection direction exceeds {MAX_DIRECTION_CHARS} characters; no input was truncated")
    try:
        max_papers = int(payload.get("max_papers") or 50)
    except (TypeError, ValueError, OverflowError):
        max_papers = 50
    max_papers = max(1, min(50, max_papers))
    job_id = f"collector-{secrets.token_hex(6)}"
    job = {
        "job_id": job_id,
        "direction": direction,
        "collector_contract": CONTRACT,
        "requested_message_id": f"msg_{secrets.token_hex(16)}",
        "process_owner": business_tasks.process_identity(),
        "max_papers": max_papers,
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "created_by": str(actor or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "registered_count": 0,
        "error": "",
        "summary": "",
    }
    with LOCK:
        paper_state_store.append("collector_jobs", job)
    return _public_paper_collection_job(_start_paper_collection_task(job))


def delete_paper_collection_job(payload: dict, actor: str = "") -> dict:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("paper collection job id required")
    with LOCK, paper_state_store.locked("collector_jobs"):
        job = _paper_collection_job_by_id(job_id)
        if not job or job.get("deleted"):
            raise KeyError("paper collection job not found")
        _set_paper_collection_job(job_id, {"deleted": True, "status": "deleted", "step": "deleted", "progress": 100})
    return {"ok": True, "job_id": job_id}


def _start_paper_collection_task(job: dict) -> dict:
    from contextvars import copy_context
    from management_portal import business_tasks
    from management_portal.paper_collector import require_actor

    if not schema_matches(job.get("collector_contract"), "tfi.paper.collector.v2"):
        raise RuntimeError("Collector job does not satisfy the current execution contract")
    require_actor(str(job.get("created_by") or ""))

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        current = _paper_collection_job_by_id(job_id) or {}
        if not current.get("deleted") and current.get("status") not in _TERMINAL_STATUSES:
            _set_paper_collection_job(job_id, {
                "status": "error",
                "step": "error",
                "progress": 100,
                "error": str(exc)[:2000],
            })
        # Admission/target failures can precede the collector's file-writing
        # scope. The task host still confirms that this writer has stopped.
        _finish_paper_collection_job(job_id)

    try:
        business_tasks.start(
            f"paper-collection:{job_id}",
            copy_context().run,
            _run_paper_collection_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_paper_collection_job(
            job_id,
            {
                "status": "error",
                "step": "launch error",
                "progress": 100,
                "error": f"Failed to run PaperCollector business task: {exc}",
                "process_owner": {},
            },
        )
    return _paper_collection_job_by_id(job_id) or job


def _public_paper_collection_job(job: dict) -> dict:
    from management_portal.paper_projection import input_receipt

    result_count = len(_paper_collection_result_items(job))
    registered_count = int(job["registered_count"] if job.get("registered_count") is not None else result_count)
    invocation = job.get("invocation")
    return {
        "job_id": str(job.get("job_id") or ""),
        "direction": str(job.get("direction") or ""),
        "max_papers": int(job.get("max_papers") or 0),
        "status": str(job.get("status") or "queued"),
        "step": str(job.get("step") or ""),
        "progress": int(job.get("progress") or 0),
        "created_by": str(job.get("created_by") or ""),
        "created_at": int(job.get("created_at") or 0),
        "updated_at": int(job.get("updated_at") or 0),
        "registered_count": registered_count,
        "result_count": result_count,
        "invocation": input_receipt(invocation) if isinstance(invocation, dict) and invocation else None,
        "summary": str(job.get("summary") or ""),
        "error": str(job.get("error") or ""),
        "stdout_path": str(job.get("stdout_path") or ""),
        "stderr_path": str(job.get("stderr_path") or ""),
        "result_path": str(job.get("result_path") or ""),
    }


def _run_paper_collection_job(job_id: str) -> None:
    from management_portal.paper_collector import run

    run(str(job_id or "").strip(), backend=sys.modules[__name__])
