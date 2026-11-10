from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from management_portal import business_tasks, paper_store, paper_summaries as summaries, permission_system

PAPER = "paper_0123456789ab"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(paper_store.PAPER_STORE, "manifest_for_paper_id", lambda _: (bundle / "manifest.json", {}))
    monkeypatch.setattr(paper_store.PAPER_STORE, "paper_dir_from_manifest", lambda *_: bundle)
    monkeypatch.setattr(paper_store, "_notify_paper_projection", lambda *_a, **_kw: None)
    monkeypatch.setattr(summaries, "PAPER_STORE_ROOT", tmp_path)
    monkeypatch.setattr(summaries, "_paper_by_id", lambda _: {"id": PAPER})
    monkeypatch.setattr(permission_system, "permitted_ids", lambda actor, *_: {PAPER} if actor == "caller" else set())
    launch = Mock(return_value=True)
    monkeypatch.setattr(business_tasks, "start", launch)
    summaries._write_paper_run_record(PAPER, {"job_id": "failed-summary", "kind": "summary", "status": "error", "queued_at": 1, "actor": "old-caller"})
    return launch


def retry(actor="caller", job_id="failed-summary"):
    return summaries.retry_summary({"paper_id": PAPER, "job_id": job_id}, actor)


def test_concurrent_retries_create_one_job_and_preserve_original(jobs, monkeypatch):
    """Repeated explicit retries share a new job and never rerun the old input."""
    before = deepcopy(summaries._paper_run_record_by_job_id(PAPER, "failed-summary"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(r["reused"] for r in results) == [False, True]
    assert jobs.call_count == 1
    job = results[0]["summary_job"]
    assert job["actor"] == "caller" and job["retry_of"] == "failed-summary"
    assert summaries._paper_run_record_by_job_id(PAPER, "failed-summary") == before
    monkeypatch.setattr(summaries, "_ensure_paper_summary", lambda *_a, **_k: "/saved/summary.md")
    call = jobs.call_args
    options = {k: v for k, v in call.kwargs.items() if k != "on_error"}
    call.args[1](*call.args[2:], **options)
    assert retry()["summary_job"]["status"] == "done"
    assert jobs.call_count == 1


def test_denied_stale_and_active_jobs_do_not_launch(jobs):
    with pytest.raises(PermissionError):
        retry("other")
    summaries._write_paper_run_record(PAPER, {"job_id": "new-summary", "kind": "summary", "status": "running", "queued_at": 2})
    with pytest.raises(ValueError, match="changed"):
        retry()
    with pytest.raises(ValueError, match="failed"):
        retry(job_id="new-summary")
    jobs.assert_not_called()
