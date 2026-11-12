"""Shared Paper job ownership through existing transactional/file adapters.

The database clock and the same transaction lock as job writes arbitrate leases.
Every worker write, including artifact publication, must hold fenced_write().
Deep Read keeps its existing default adapter and wire behavior. No new DDL.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import secrets
import threading

from . import paper_state_store as store

LEASE_SECONDS = 90
CLAIM_KEY = "_execution_lease"
_owner = ContextVar("paper_deep_read_owner", default=None)


class OwnershipLost(RuntimeError):
    pass


class TableAdapter:
    """Adapter contract: shared lock+clock, normalized status/handoff, atomic merge.

    get/merge are called only inside locked(), and must use that transaction.
    The reserved execution envelope survives every business usage replacement.
    """
    def __init__(self, kind="deep_read_jobs"):
        self.kind = kind
        self.column = {"deep_read_jobs": "resident_handoff", "exp_jobs": "usage",
                       "review_jobs": "process_owner"}[kind]

    def locked(self):
        return store.locked(self.kind)

    def get(self, identity):
        row = store.get(self.kind, identity)
        if row and self.column != "resident_handoff":
            row = {**row, "resident_handoff": dict((row.get(self.column) or {}).get("_job_execution") or {})}
        return row

    def merge(self, identity, patch):
        patch = dict(patch)
        if self.column != "resident_handoff":
            current = self.get(identity) or {}
            envelope = patch.pop("resident_handoff", current.get("resident_handoff") or {})
            patch[self.column] = {**dict(patch.get(self.column, current.get(self.column)) or {}),
                                  "_job_execution": envelope}
        return store.merge(self.kind, identity, patch)


def current_identity():
    owner = _owner.get()
    if not owner:
        raise OwnershipLost("Task has no execution owner")
    return owner[0]


def metadata():
    owner = _owner.get()
    adapter = owner[2] if len(owner) > 2 else TableAdapter()
    return (adapter.get(owner[0]) or {}).get("resident_handoff") or {}


def checkpoint(key, value):
    worker_write(current_identity(), {"resident_handoff": {key: value}})


def stage_turn(stage, *, actor, on_receipt=None, **options):
    """Observe the same stage input; a durable intent forbids blind redelivery."""
    from .paper_projection import turn, resume, input_receipt, confirm_input, InputConfirmationPending
    from .lineage_workflows import WorkflowPending
    record = dict(metadata().get("stages", {}).get(stage) or {})
    if not record and metadata().get("legacy_unconfirmed"):
        raise InputConfirmationPending("Legacy task has no confirmed stage locator; no input resubmitted")
    if record.get("result"):
        return record["result"]
    def save(**patch):
        nonlocal record
        record = {**record, **patch}
        checkpoint("stages", {**metadata().get("stages", {}), stage: record})
    def remember(handle):
        save(receipt=input_receipt(handle))
        if on_receipt:
            on_receipt(input_receipt(handle))
    receipt = record.get("receipt") or {}
    if record.get("intent"):
        if not receipt:
            raise InputConfirmationPending("Stage submission locator unavailable; no input resubmitted")
        receipt = confirm_input(actor=actor, receipt=receipt)
        remember(receipt)
        result = resume(actor=actor, resident_id=receipt["resident_id"], lineage_id=receipt["lineage_id"],
                        message_id=receipt["input_message_id"], receipt=receipt, on_submitted=remember)
    else:
        save(intent=True)
        try:
            result = turn(actor=actor, **options, on_assigned=remember, on_submitted=remember)
        except WorkflowPending as exc:
            from .paper_projection import receipt_from_resident_handle
            remember(receipt_from_resident_handle(exc.handle))
            raise
    save(result=result, phase="generated")
    return result


def settle_exception(exc):
    """A stable machine projection; render location belongs to the Paper UI."""
    from .paper_projection import InputConfirmationPending
    from .lineage_workflows import WorkflowPending
    if isinstance(exc, OwnershipLost):
        raise exc
    saved = metadata() if is_worker() else {}
    if any(saved.get(key) for key in ("validated_exp", "validated_review", "validated_explanation", "validated_image")):
        return {"status": "running", "step": "saving result", "error": str(exc)[:2000]}
    if isinstance(exc, (InputConfirmationPending, WorkflowPending, TimeoutError)):
        return {"status": "running", "step": "confirming submission", "error": ""}
    return {"status": "error", "step": "error", "error": str(exc)[:2000]}


def public_state(job):
    """Add presentation keys, never render a page or expose cached private input."""
    if not job:
        return job
    result = {k: v for k, v in job.items() if k != "_job_execution"}
    for column in ("usage", "process_owner"):
        if isinstance(result.get(column), dict):
            result[column] = {k: v for k, v in result[column].items() if k != "_job_execution"}
    status, step = job.get("status"), job.get("step")
    result["can_retry"] = status == "error"
    result["status_label_key"] = ("正在确认任务状态" if status == "running" and step == "confirming submission" else
                                  "结果已生成，正在保存/核对" if status == "running" and step in {"saving result", "writing exp"} else
                                  "Task failed. See Details." if status == "error" else "")
    result["diagnostics"] = {"error": str(job.get("error") or ""), "step": step or ""}
    return result


def is_worker():
    return _owner.get() is not None


def _now(connection):
    return float(connection.execute("SELECT extract(epoch FROM clock_timestamp())").fetchone()[0])


def claim(job_id, adapter=None):
    adapter = adapter or TableAdapter()
    with adapter.locked() as connection:
        job = adapter.get(job_id)
        if not job or job.get("status") not in {"queued", "running"}:
            return None
        handoff = dict(job.get("resident_handoff") or {})
        previous = handoff.get(CLAIM_KEY) or {}
        if adapter.kind != "deep_read_jobs" and not previous and job.get("status") == "running":
            handoff.setdefault("legacy_unconfirmed", True)
        now = _now(connection)
        if float(previous.get("expires_at") or 0) > now:
            return None
        lease = {"token": secrets.token_hex(16), "generation": int(previous.get("generation") or 0) + 1,
                 "expires_at": now + LEASE_SECONDS}
        adapter.merge(job_id, {"resident_handoff": {**handoff, CLAIM_KEY: lease}})
        return lease


@contextmanager
def fenced_write(job_id):
    """Lock, validate the live fence, and serialize its complete side effect.

    This also prevents a takeover during artifact publication. Administrative
    adoption/provenance routes remain explicit, outside worker ownership.
    """
    owner = _owner.get()
    if owner is None or owner[0] != job_id:
        raise OwnershipLost("Deep Read worker has no execution claim")
    adapter = owner[2] if len(owner) > 2 else TableAdapter()
    with adapter.locked() as connection:
        job = adapter.get(job_id) or {}
        handoff = dict(job.get("resident_handoff") or {})
        lease = handoff.get(CLAIM_KEY) or {}
        if (lease.get("token") != owner[1]["token"] or float(lease.get("expires_at") or 0) <= _now(connection)
                or job.get("status") not in {"queued", "running"}):
            raise OwnershipLost("Deep Read execution claim expired or superseded")
        yield job


def worker_write(job_id, patch):
    """Merge a fenced patch while preserving the adapter's execution envelope."""
    owner = _owner.get()
    adapter = owner[2] if owner and len(owner) > 2 else TableAdapter()
    with fenced_write(job_id) as job:
        patch = dict(patch)
        if "resident_handoff" in patch:
            patch["resident_handoff"] = {**(job.get("resident_handoff") or {}), **patch["resident_handoff"]}
        return adapter.merge(job_id, patch)


def run(job_id, target, *, adapter=None):
    adapter = adapter or TableAdapter()
    lease = claim(job_id, adapter)
    if lease is None:
        return False
    stop = threading.Event()
    token = _owner.set((job_id, lease, adapter))

    def heartbeat():
        context = _owner.set((job_id, lease, adapter))
        try:
            while not stop.wait(LEASE_SECONDS / 3):
                try:
                    with fenced_write(job_id) as job:
                        handoff = dict(job.get("resident_handoff") or {})
                        with adapter.locked() as connection:
                            handoff[CLAIM_KEY] = {**lease, "expires_at": _now(connection) + LEASE_SECONDS}
                            adapter.merge(job_id, {"resident_handoff": handoff})
                except Exception:
                    # An expired/unreachable claim is never revived. The worker
                    # will fail its next write; a new owner observes the input.
                    return
        finally:
            _owner.reset(context)

    thread = threading.Thread(target=heartbeat, name="paper-job-lease", daemon=True)
    thread.start()
    try:
        target(job_id)
    except OwnershipLost:
        pass
    finally:
        stop.set()
        thread.join(timeout=2)
        try:
            with fenced_write(job_id) as job:
                handoff = dict(job.get("resident_handoff") or {})
                handoff[CLAIM_KEY] = {**lease, "expires_at": 0}
                adapter.merge(job_id, {"resident_handoff": handoff})
        except OwnershipLost:
            pass
        finally:
            _owner.reset(token)
    return True


def recovery_loop(stop):
    """Server lifecycle recovery; readers never launch or repair jobs."""
    from .paper_reading_jobs import _launch_deep_read_business_task
    while not stop.is_set():
        try:
            with store.database().read() as connection:
                jobs = [store.decode("deep_read_jobs", row) for row in connection.execute(
                    "SELECT * FROM deep_read_jobs WHERE status IN ('queued','running') ORDER BY created_at,job_id"
                ).fetchall()]
            for job in jobs:
                _launch_deep_read_business_task(job)
            from .paper_reading_jobs import _start_exp_task
            from .paper_reviews import _start_review_task
            for kind, launch in (("exp_jobs", _start_exp_task), ("review_jobs", _start_review_task)):
                with store.database().read() as connection:
                    pending = [store.decode(kind, row) for row in connection.execute(
                        f"SELECT * FROM {kind} WHERE status IN ('queued','running') ORDER BY created_at,job_id"
                    ).fetchall()]
                for job in pending:
                    launch(job)
            from .paper_store import _all_papers
            from .paper_figure_jobs import _resume_figure_tasks
            for paper in _all_papers():
                _resume_figure_tasks(paper)
        except Exception as exc:
            print(f"[paper-job-recovery] {type(exc).__name__}: {exc}", flush=True)
        stop.wait(15)


def web_lifespan():
    stop = threading.Event()
    thread = threading.Thread(target=recovery_loop, args=(stop,), name="paper-job-recovery", daemon=True)
    thread.start()
    def close():
        stop.set()
        thread.join(timeout=2)
    return close
