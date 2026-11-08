"""Persisted PDF asset tasks are observable and explicitly retryable after restart.

:design: decision_528bf9985705
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import business_tasks, paper_assets as assets, paper_store, permission_system

PAPER = "paper_0123456789ab"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    """Real run-record JSON and locks, synthetic paper/PDF and extraction adapters."""
    bundle = tmp_path / "paper"
    bundle.mkdir()
    manifest = {"paper_id": PAPER}
    monkeypatch.setattr(paper_store.PAPER_STORE, "manifest_for_paper_id", lambda _: (bundle / "manifest.json", manifest))
    monkeypatch.setattr(paper_store.PAPER_STORE, "paper_dir_from_manifest", lambda *_: bundle)
    monkeypatch.setattr(paper_store, "_notify_paper_projection", lambda *_a, **_kw: None)
    monkeypatch.setattr(assets, "PAPER_ASSETS_WORK_ROOT", tmp_path / "locks")
    paper = {"id": PAPER}
    monkeypatch.setattr(assets, "_paper_by_id", lambda _: paper)
    pdf = bundle / "paper.pdf"
    pdf.write_bytes(b"synthetic PDF; never parsed")
    monkeypatch.setattr(assets, "_local_pdf_for_paper", lambda _: pdf)
    allowed = Mock(side_effect=lambda actor, *_: {PAPER} if actor == "current-caller" else set())
    monkeypatch.setattr(permission_system, "permitted_ids", allowed)
    refs = Mock(return_value={"count": 3})
    figures = Mock(return_value={"count": 2})
    monkeypatch.setattr(assets, "extract_refs", refs)
    monkeypatch.setattr(assets, "extract_figures", figures)
    launch = Mock(return_value=True)
    monkeypatch.setattr(business_tasks, "start", launch)
    return SimpleNamespace(bundle=bundle, paper=paper, launch=launch, allowed=allowed, refs=refs, figures=figures)


def save(**changes):
    job = dict(job_id="assets-fixture", kind="assets", status="queued", queued_at=1, actor="old-author")
    job.update(changes)
    assets._write_paper_run_record(PAPER, job)
    return job


def retry():
    return assets.retry_assets({"paper_id": PAPER, "job_id": "assets-fixture", "actor": "old-author"}, "current-caller")


def run_accepted(jobs):
    call = jobs.launch.call_args
    kwargs = dict(call.kwargs)
    kwargs.pop("on_error")
    return call.args[1](*call.args[2:], **kwargs)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_detail_exposes_dead_worker_without_replaying(jobs, monkeypatch, status):
    """A real exited process identity turns durable queued/running into interrupted on GET."""
    from management_portal import paper_detail
    completed = subprocess.run([sys.executable, "-c", "import json; from management_common.process_observation import process_identity; print(json.dumps(process_identity()))"], capture_output=True, text=True, check=True)
    owner = json.loads(completed.stdout)
    save(status=status, worker=owner)
    before = list((jobs.bundle / "runs").glob("*.json"))[0].read_bytes()
    monkeypatch.setattr(paper_detail, "_paper_by_id", lambda _: jobs.paper)
    monkeypatch.setattr(paper_detail, "_paper_public", lambda _: {"has_pdf": True})
    detail = paper_detail.get_paper_detail(PAPER, sections="none")
    assert detail["assets_job"]["status"] == "interrupted"
    assert detail["assets_job"]["retryable"] is True
    assert list((jobs.bundle / "runs").glob("*.json"))[0].read_bytes() == before
    jobs.launch.assert_not_called()
    jobs.refs.assert_not_called()


def test_retry_legacy_task_uses_current_caller_and_deduplicates(jobs):
    save()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(result["reused"] for result in results) == [False, True]
    assert jobs.launch.call_count == 1
    job = assets.assets_job_status(PAPER)
    assert job["actor"] == "current-caller" and job["attempt"] == 1
    assert job["worker"]["pid"] == os.getpid()
    assert run_accepted(jobs)["errors"] == []
    jobs.refs.assert_called_once_with({"paper_id": PAPER}, "current-caller")
    jobs.figures.assert_called_once_with({"paper_id": PAPER}, "current-caller")
    assert assets.assets_job_status(PAPER)["status"] == "done"
    assert retry()["reused"] is True
    assert not (jobs.bundle / "text" / "pdf.clean.txt").exists()


def test_live_or_unknown_worker_is_not_replayed(jobs):
    save(worker={**assets.process_identity(), "host": "another-host"})
    assert retry()["reused"] is True
    jobs.launch.assert_not_called()


def test_stale_job_and_permission_denial_cannot_write(jobs):
    save()
    before = list((jobs.bundle / "runs").glob("*.json"))[0].read_bytes()
    with pytest.raises(PermissionError):
        assets.retry_assets({"paper_id": PAPER, "job_id": "assets-fixture"}, "old-author")
    with pytest.raises(ValueError, match="changed"):
        assets.retry_assets({"paper_id": PAPER, "job_id": "stale"}, "current-caller")
    assert list((jobs.bundle / "runs").glob("*.json"))[0].read_bytes() == before
    jobs.launch.assert_not_called()


def test_permission_revoked_while_waiting_stops_extraction(jobs):
    save()
    retry()
    jobs.allowed.side_effect = lambda *_: set()
    with pytest.raises(PermissionError):
        run_accepted(jobs)
    assert assets.assets_job_status(PAPER)["status"] == "error"
    jobs.refs.assert_not_called()
    jobs.figures.assert_not_called()


def test_replaced_pdf_job_fences_old_attempt_and_callback(jobs):
    save()
    retry()
    old_call = jobs.launch.call_args
    save(job_id="assets-new-pdf", queued_at=2)
    assert run_accepted(jobs)["reason"] == "superseded attempt"
    old_call.kwargs["on_error"](RuntimeError("late old failure"))
    # Even an old record's later mtime cannot select it as the current PDF job.
    assets._update_paper_run_record(PAPER, {"job_id": "assets-fixture", "status": "error"})
    assert assets.assets_job_status(PAPER)["job_id"] == "assets-new-pdf"
    jobs.refs.assert_not_called()


def test_launch_failure_is_visible_and_retryable(jobs):
    save()
    jobs.launch.side_effect = RuntimeError("cannot create thread")
    result = retry()
    assert result["assets_job"]["status"] == "error"
    assert result["assets_job"]["retryable"] is True
    assert "cannot create thread" in result["assets_job"]["error"]


def test_http_retry_checks_auth_read_and_contributor(jobs):
    from management_portal.http_paper import PaperHttp
    save()
    auth = {"actor": "current-caller"}
    read = Mock()
    http = PaperHttp(respond=lambda _r, status, body: (status, body), exception_response=None, media_file=None,
                     get_auth=lambda _: auth, authorize=lambda *_: "current-caller", read_json=lambda _: {"paper_id": PAPER, "job_id": "assets-fixture"},
                     read_multipart=None, multipart_limit=None, body_too_large=None, require_resource=read,
                     require_active_actor=None, project_ids=lambda _: [], notify_index=None)
    route = http.post_backend(assets, "retry_assets")
    assert route(None, None, None)[0] == 200
    read.assert_called_once_with(PAPER, "current-caller")
    jobs.allowed.side_effect = lambda *_: set()
    assert route(None, None, None)[0] == 403
    auth.clear()
    assert route(None, None, None)[0] == 401


def test_automatic_enqueue_records_recoverable_worker(jobs):
    assets._start_assets_task(PAPER, actor="current-caller")
    job = assets.assets_job_status(PAPER)
    assert job["status"] == "queued" and job["worker"]["pid"] == os.getpid()
    assert job["attempt_id"] and job["attempt"] == 1
    assert run_accepted(jobs)["errors"] == []


def test_dead_queued_thread_is_recoverable_after_process_exit(jobs, tmp_path):
    """A daemon waiting on the real global lock exits; a new caller can recover its disk job."""
    from management_common.file_lock import lock_stream
    child = '''
import sys
from pathlib import Path
from management_portal import paper_assets as assets, paper_store
bundle, locks = map(Path, sys.argv[1:])
paper_store.PAPER_STORE.manifest_for_paper_id = lambda _: (bundle / "manifest.json", {})
paper_store.PAPER_STORE.paper_dir_from_manifest = lambda *_: bundle
paper_store._notify_paper_projection = lambda *_a, **_kw: None
assets.PAPER_ASSETS_WORK_ROOT = locks
assets._start_assets_task("paper_0123456789ab", actor="old-author")
'''
    with assets._paper_asset_lock_path().open("a+b") as stream, lock_stream(stream):
        result = subprocess.run([sys.executable, "-c", child, str(jobs.bundle), str(tmp_path / "locks")], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
    interrupted = assets.assets_job_status(PAPER)
    assert interrupted["status"] == "interrupted"
    assert interrupted["actor"] == "old-author"
    assert assets._paper_run_record_by_job_id(PAPER, interrupted["job_id"])["status"] == "queued"
    result = assets.retry_assets({"paper_id": PAPER, "job_id": interrupted["job_id"]}, "current-caller")
    assert result["assets_job"]["attempt"] == 2
    assert run_accepted(jobs)["errors"] == []
    assert assets.assets_job_status(PAPER)["status"] == "done"


def test_failure_callback_during_thread_exit_cannot_launch_orphan_attempt(jobs, monkeypatch):
    save(status="error", attempt=1)
    monkeypatch.setattr(business_tasks, "is_active", lambda _: True)
    assert retry()["reused"] is True
    assert assets.assets_job_status(PAPER)["attempt"] == 1
    jobs.launch.assert_not_called()
