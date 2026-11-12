"""Durable Deep Read ownership, using the job's existing JSONB handoff column.

The database clock and the same transaction lock as job writes arbitrate leases.
Every worker write, including artifact publication, must hold fenced_write().
No DDL, process-local election, or browser request is involved in recovery.
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


def is_worker():
    return _owner.get() is not None


def _now(connection):
    return float(connection.execute("SELECT extract(epoch FROM clock_timestamp())").fetchone()[0])


def claim(job_id):
    with store.locked("deep_read_jobs") as connection:
        job = store.get("deep_read_jobs", job_id)
        if not job or job.get("status") not in {"queued", "running"}:
            return None
        handoff = dict(job.get("resident_handoff") or {})
        previous = handoff.get(CLAIM_KEY) or {}
        now = _now(connection)
        if float(previous.get("expires_at") or 0) > now:
            return None
        lease = {"token": secrets.token_hex(16), "generation": int(previous.get("generation") or 0) + 1,
                 "expires_at": now + LEASE_SECONDS}
        store.merge("deep_read_jobs", job_id, {"resident_handoff": {**handoff, CLAIM_KEY: lease}})
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
    with store.locked("deep_read_jobs") as connection:
        job = store.get("deep_read_jobs", job_id) or {}
        handoff = dict(job.get("resident_handoff") or {})
        lease = handoff.get(CLAIM_KEY) or {}
        if (lease.get("token") != owner[1]["token"] or float(lease.get("expires_at") or 0) <= _now(connection)
                or job.get("status") not in {"queued", "running"}):
            raise OwnershipLost("Deep Read execution claim expired or superseded")
        yield job


def worker_write(job_id, patch):
    """Called only by the claimed worker, preserving lease and submission intent."""
    with fenced_write(job_id) as job:
        patch = dict(patch)
        if "resident_handoff" in patch:
            patch["resident_handoff"] = {**(job.get("resident_handoff") or {}), **patch["resident_handoff"]}
        return store.merge("deep_read_jobs", job_id, patch)


def run(job_id, target):
    lease = claim(job_id)
    if lease is None:
        return False
    stop = threading.Event()
    token = _owner.set((job_id, lease))

    def heartbeat():
        context = _owner.set((job_id, lease))
        try:
            while not stop.wait(LEASE_SECONDS / 3):
                try:
                    with fenced_write(job_id) as job:
                        handoff = dict(job.get("resident_handoff") or {})
                        with store.locked("deep_read_jobs") as connection:
                            handoff[CLAIM_KEY] = {**lease, "expires_at": _now(connection) + LEASE_SECONDS}
                            store.merge("deep_read_jobs", job_id, {"resident_handoff": handoff})
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
                store.merge("deep_read_jobs", job_id, {"resident_handoff": handoff})
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
