"""Real PostgreSQL / independent process ownership; all business/model sinks fake."""
import multiprocessing
from pathlib import Path
import time

import pytest

from management_portal import paper_job_ownership as ownership, paper_state_store as store


def _race_worker(job_id, ready, start, release, events):
    ready.put(True)
    start.wait(20)
    def execute(identity):
        events.put("executed")
        release.wait(20)
        ownership.worker_write(identity, {"status": "done", "step": "done"})
    ownership.run(job_id, execute)


def _stale_worker(job_id, ready, release, artifact):
    def execute(identity):
        ready.put(True)
        release.wait(20)
        with ownership.fenced_write(identity):
            Path(artifact).write_text("stale worker")
            ownership.worker_write(identity, {"status": "error", "error": "stale"})
    ownership.run(job_id, execute)


def _new_worker(job_id):
    ownership.run(job_id, lambda identity: ownership.worker_write(identity, {"status": "done"}))


def _job(identity):
    store.append("deep_read_jobs", {"job_id": identity, "paper_id": "synthetic",
                                   "status": "queued", "created_at": time.time()})


def test_two_processes_only_one_execution(isolated_portal_case):
    with isolated_portal_case():
        _job("race")
        ctx = multiprocessing.get_context("spawn")
        ready, events, start, release = ctx.Queue(), ctx.Queue(), ctx.Event(), ctx.Event()
        processes = [ctx.Process(target=_race_worker, args=("race", ready, start, release, events)) for _ in range(2)]
        for process in processes: process.start()
        try:
            ready.get(timeout=20); ready.get(timeout=20); start.set()
            assert events.get(timeout=20) == "executed"
            # The losing process returns while the owner is still executing.
            deadline = time.monotonic() + 20
            while all(process.is_alive() for process in processes) and time.monotonic() < deadline:
                time.sleep(.02)
            assert any(not process.is_alive() for process in processes)
            release.set()
            for process in processes: process.join(20); assert process.exitcode == 0
            assert events.empty()
            assert store.get("deep_read_jobs", "race")["status"] == "done"
            assert ownership.claim("race") is None
        finally:
            release.set()
            for process in processes:
                if process.is_alive(): process.terminate()
                process.join(5)


def test_expired_worker_cannot_overwrite_terminal_or_artifact(isolated_portal_case, tmp_path):
    with isolated_portal_case():
        _job("fence")
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Queue(), ctx.Event()
        artifact = tmp_path / "stale.md"
        old = ctx.Process(target=_stale_worker, args=("fence", ready, release, str(artifact)))
        old.start()
        try:
            ready.get(timeout=20)
            with store.locked("deep_read_jobs"):
                job = store.get("deep_read_jobs", "fence")
                handoff = job["resident_handoff"]
                handoff[ownership.CLAIM_KEY]["expires_at"] = 0
                store.merge("deep_read_jobs", "fence", {"resident_handoff": handoff})
            new = ctx.Process(target=_new_worker, args=("fence",)); new.start(); new.join(20)
            assert new.exitcode == 0
            release.set(); old.join(20)
            assert old.exitcode == 0
            assert not artifact.exists()
            job = store.get("deep_read_jobs", "fence")
            assert job["status"] == "done" and not job.get("error")
            assert job["resident_handoff"][ownership.CLAIM_KEY]["generation"] == 2
        finally:
            release.set()
            if old.is_alive(): old.terminate()
            old.join(5)


def test_expired_lease_never_revives(isolated_portal_case):
    with isolated_portal_case():
        _job("expired")
        lease = ownership.claim("expired")
        token = ownership._owner.set(("expired", lease))
        try:
            with store.locked("deep_read_jobs"):
                job = store.get("deep_read_jobs", "expired")
                handoff = job["resident_handoff"]; handoff[ownership.CLAIM_KEY]["expires_at"] = 0
                store.merge("deep_read_jobs", "expired", {"resident_handoff": handoff})
            with pytest.raises(ownership.OwnershipLost):
                ownership.worker_write("expired", {"status": "error"})
        finally:
            ownership._owner.reset(token)


def test_status_get_is_read_only(monkeypatch):
    from management_portal import paper_reading_jobs as jobs
    job = {"job_id": "read", "paper_id": "paper", "status": "running"}
    monkeypatch.setattr(jobs, "_latest_deep_read_job", lambda **kw: job)
    monkeypatch.setattr(jobs, "_authorize_paper_job_status", lambda *a, **kw: None)
    monkeypatch.setattr(jobs, "_launch_deep_read_business_task", lambda *a: pytest.fail("GET launched execution"))
    assert jobs.get_deep_read_status(actor="reader", job_id="read") == job


def test_unconfirmed_uses_original_submission_reads(monkeypatch):
    from management_portal import paper_projection as projection, resident_handoff
    handle = projection.receipt_from_resident_handle({"resident_id": "resident", "lineage_id": "lineage",
        "event_source_id": "source", "effect_id": "effect", "event_id": "event", "trigger_path_id": "path",
        "request_id": "requested", "idempotency_key": "original", "status": "assigned"})
    seen = []
    def resolve(**kw):
        seen.append(kw)
        return {**kw["handle"], "submission_state": "submitted", "message_id": "canonical"}
    monkeypatch.setattr(resident_handoff, "resolve_invocation_submission", resolve)
    result = projection.confirm_input(actor="actor", receipt=handle)
    assert result["input_message_id"] == "canonical"
    assert seen[0]["handle"]["trigger_path_id"] == "path"
    assert seen[0]["handle"]["idempotency_key"] == "original"


def test_unconfirmed_remains_pending_without_guessing_input(monkeypatch):
    from management_portal import paper_projection as projection, resident_handoff
    receipt = projection.receipt_from_resident_handle({"resident_id": "resident", "trigger_path_id": "path"})
    monkeypatch.setattr(resident_handoff, "resolve_invocation_submission",
                        lambda **kw: {"submission_state": "pending"})
    with pytest.raises(projection.InputConfirmationPending):
        projection.confirm_input(actor="actor", receipt=receipt)
    with pytest.raises(projection.InputConfirmationPending):
        projection.confirm_input(actor="actor", receipt={"_submission_started": True})


def test_pending_job_recovers_original_canonical_input_without_redelivery(monkeypatch, isolated_portal_case, tmp_path):
    from management_portal import paper_projection as projection, paper_reading_jobs as jobs, isa_client
    from management_portal.lineage_workflows import WorkflowPending
    with isolated_portal_case():
        _job("original")
        paper = {"id": "synthetic", "title": "Synthetic", "has_pdf": True}
        monkeypatch.setattr(jobs, "get_paper_detail", lambda *a, **kw: {"paper": paper})
        monkeypatch.setattr(jobs, "_paper_public", lambda p: p)
        monkeypatch.setattr(jobs, "_local_pdf_for_paper", lambda p: tmp_path / "test.pdf")
        monkeypatch.setattr(jobs, "_extract_pdf_text", lambda *a, **kw: "source " * 300)
        monkeypatch.setattr(jobs, "_notify_paper_projection", lambda *a, **kw: None)
        monkeypatch.setattr(jobs.PAPER_STORE, "write_run_record", lambda *a: None)
        saved, calls = [], []
        monkeypatch.setattr(jobs, "_save_deep_read_analysis", lambda *a: saved.append(a) or tmp_path / "analysis.md")
        handle = {"resident_id": "resident", "lineage_id": "lineage", "event_source_id": "source",
                  "effect_id": "effect", "trigger_path_id": "path", "event_id": "event",
                  "instruction": "TriggerEvent", "request_id": "request", "idempotency_key": "original-key"}
        def submit(**kw):
            calls.append("submit")
            kw["on_assigned"](projection.receipt_from_resident_handle({**handle, "status": "assigned"}))
            raise WorkflowPending(handle, {})
        monkeypatch.setattr(projection, "turn", submit)
        ownership.run("original", jobs._run_deep_read_job)
        job = store.get("deep_read_jobs", "original")
        assert job["status"] == "running" and job["step"] == "confirming submission" and not job.get("error")
        visible = False
        def isa(actor, instruction, arguments, **kw):
            calls.append(instruction)
            assert instruction in {"GetTriggerPath", "GetInputState"}
            if instruction == "GetTriggerPath":
                assert arguments["trigger_path_id"] == "path"
                return {"path": {"lineage_id": "lineage", "trigger_path_id": "path", "root_kind": "event",
                                  "root_node_id": "source", "occurrence_id": "event"},
                        "steps": {"items": [{"step_id": "effect-step", "kind": "effect", "node_id": "effect"},
                            *([{"step_id": "input-step", "kind": "resident", "node_id": "resident",
                                "input_message_id": "canonical"}] if visible else [])]},
                        "edges": {"items": [{"source_step_id": "effect-step", "target_step_id": "input-step"}]}}
            assert arguments["message_id"] == "canonical"
            return {"input_state": {"message_id": "canonical", "state": "running"}}
        monkeypatch.setattr(isa_client, "call", isa)
        ownership.run("original", jobs._run_deep_read_job)
        assert store.get("deep_read_jobs", "original")["step"] == "confirming submission"
        visible = True
        def resume(**kw):
            calls.append("resume")
            assert kw["message_id"] == "canonical"
            return {"message": jobs.DEEP_READ_HEADINGS, "usage": {}}
        monkeypatch.setattr(projection, "resume", resume)
        ownership.run("original", jobs._run_deep_read_job)
        job = store.get("deep_read_jobs", "original")
        assert job["status"] == "done" and job["resident_input_message_id"] == "canonical"
        assert calls.count("submit") == calls.count("resume") == len(saved) == 1


def test_heartbeat_keeps_claim_unavailable_to_other_workers(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        _job("renew")
        monkeypatch.setattr(ownership, "LEASE_SECONDS", .18)
        def execute(identity):
            time.sleep(.4)  # several lease periods, no model invocation
            assert ownership.claim(identity) is None
            ownership.worker_write(identity, {"status": "done"})
        ownership.run("renew", execute)
        assert store.get("deep_read_jobs", "renew")["status"] == "done"


def test_heartbeat_failure_is_observable_without_reviving_claim(monkeypatch, isolated_portal_case, caplog):
    import threading
    with isolated_portal_case():
        _job('heartbeat-failure')
        adapter = ownership.TableAdapter()
        original = adapter.merge
        failed = threading.Event()
        def merge(identity, patch):
            if threading.current_thread().name == 'paper-job-lease':
                failed.set()
                raise RuntimeError('private transport detail')
            return original(identity, patch)
        monkeypatch.setattr(adapter, 'merge', merge)
        monkeypatch.setattr(ownership, 'LEASE_SECONDS', .18)
        def execute(identity):
            assert failed.wait(5)
            time.sleep(.25)
            with pytest.raises(ownership.OwnershipLost):
                ownership.worker_write(identity, {'status': 'done'})
        ownership.run('heartbeat-failure', execute, adapter=adapter)
        assert store.get('deep_read_jobs', 'heartbeat-failure')['status'] == 'queued'
        assert 'heartbeat-failure' in caplog.text and 'RuntimeError' in caplog.text
        assert 'private transport detail' not in caplog.text
        assert ownership.claim('heartbeat-failure', adapter) is not None


def test_recovery_loop_launches_only_active_jobs_outside_get(monkeypatch, isolated_portal_case):
    from management_portal import paper_reading_jobs as jobs
    with isolated_portal_case():
        _job("active")
        _job("finished")
        store.merge("deep_read_jobs", "finished", {"status": "done"})
        class Stop:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, seconds): self.stopped = True
        seen = []
        monkeypatch.setattr(jobs, "_launch_deep_read_business_task", lambda job: seen.append(job["job_id"]))
        ownership.recovery_loop(Stop())
        assert seen == ["active"]
