"""Only disposable PG and synthetic artifacts: no model, production GET or writes."""
import json
import multiprocessing
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import paper_job_ownership as owner, paper_state_store as store


@pytest.fixture(autouse=True)
def restore_figure_boundaries(monkeypatch):
    from management_portal import paper_store, paper_figures
    for module, name in ((paper_store, "_paper_by_id"), (paper_figures, "_figures_json_path"),
                         (paper_figures, "_notify_paper_projection")):
        monkeypatch.setattr(module, name, getattr(module, name))


def adapter_for(kind, root):
    if kind in {"exp_jobs", "review_jobs"}:
        return owner.TableAdapter(kind), "job"
    from management_portal import paper_store, paper_figures
    from management_portal.paper_figure_ownership import FigureAdapter
    paper = {"id": "paper", "created_by": "test", "_store_root": root}
    paper_store._paper_by_id = lambda identity: paper
    paper_figures._figures_json_path = lambda value: Path(root) / "figures.json"
    paper_figures._notify_paper_projection = lambda *args, **kwargs: None
    adapter = FigureAdapter("paper", "fig", "gen" if kind == "figure_generation" else "")
    return adapter, adapter.identity


def _race(kind, root, ready, start, release, events):
    adapter, identity = adapter_for(kind, root)
    ready.put(True); start.wait(20)
    def work(identity):
        events.put("executed")
        release.wait(20)
        owner.worker_write(identity, {"status": "done"})
    owner.run(identity, work, adapter=adapter)


def _late(kind, root, ready, release, artifact):
    adapter, identity = adapter_for(kind, root)
    def work(identity):
        ready.put(True); release.wait(20)
        with owner.fenced_write(identity):
            Path(artifact).write_text("old")
            owner.worker_write(identity, {"status": "error", "error": "old"})
    owner.run(identity, work, adapter=adapter)


def _finish(kind, root):
    adapter, identity = adapter_for(kind, root)
    owner.run(identity, lambda identity: owner.worker_write(identity, {"status": "done"}), adapter=adapter)


def seed(kind, root):
    if kind in {"exp_jobs", "review_jobs"}:
        store.append(kind, {"job_id": "job", "paper_id": "paper", "status": "queued", "created_at": time.time()})
    else:
        (Path(root) / "figures.json").write_text(json.dumps([{"figure_id": "fig", "explanation_status": "queued",
            "generated": [{"generated_id": "gen", "status": "queued"}]}]))


@pytest.mark.parametrize("kind", ["exp_jobs", "review_jobs", "figure_explanation", "figure_generation"])
def test_two_processes_claim_only_once(kind, isolated_portal_case):
    with isolated_portal_case() as root:
        seed(kind, root)
        ctx = multiprocessing.get_context("spawn")
        ready, events, start, release = ctx.Queue(), ctx.Queue(), ctx.Event(), ctx.Event()
        processes = [ctx.Process(target=_race, args=(kind, str(root), ready, start, release, events)) for _ in range(2)]
        for process in processes: process.start()
        try:
            ready.get(timeout=20); ready.get(timeout=20); start.set()
            assert events.get(timeout=20) == "executed"
            deadline = time.monotonic() + 15
            while all(p.is_alive() for p in processes) and time.monotonic() < deadline: time.sleep(.02)
            assert any(not p.is_alive() for p in processes)
            release.set()
            for p in processes: p.join(20); assert p.exitcode == 0
            assert events.empty()
        finally:
            release.set()
            for p in processes:
                if p.is_alive(): p.terminate()
                p.join(5)


@pytest.mark.parametrize("kind", ["exp_jobs", "review_jobs", "figure_explanation", "figure_generation"])
def test_expired_writer_cannot_publish_or_overwrite_done(kind, isolated_portal_case):
    with isolated_portal_case() as root:
        seed(kind, root); artifact = root / "old.txt"
        ctx = multiprocessing.get_context("spawn"); ready, release = ctx.Queue(), ctx.Event()
        old = ctx.Process(target=_late, args=(kind, str(root), ready, release, str(artifact)))
        old.start()
        try:
            ready.get(timeout=20)
            adapter, identity = adapter_for(kind, str(root))
            with adapter.locked():
                handoff = adapter.get(identity)["resident_handoff"]
                handoff[owner.CLAIM_KEY]["expires_at"] = 0
                adapter.merge(identity, {"resident_handoff": handoff})
            new = ctx.Process(target=_finish, args=(kind, str(root))); new.start(); new.join(20)
            assert new.exitcode == 0
            release.set(); old.join(20); assert old.exitcode == 0
            assert not artifact.exists()
            with adapter.locked():
                row = adapter.get(identity)
                assert row["status"] == "done" and not row.get("error")
                assert row["resident_handoff"][owner.CLAIM_KEY]["generation"] == 2
        finally:
            release.set()
            if old.is_alive(): old.terminate()
            old.join(5)


@pytest.mark.parametrize("kind,stage", [("exp_jobs", "paper:exp"), ("review_jobs", "paper:review"),
                                       ("figure_explanation", "figure"), ("figure_generation", "figure")])
def test_unconfirmed_stage_resumes_original_and_reuses_generated_checkpoint(kind, stage, isolated_portal_case, monkeypatch):
    from management_portal import paper_projection as projection
    from management_portal.lineage_workflows import WorkflowPending
    with isolated_portal_case() as root:
        seed(kind, root); adapter, identity = adapter_for(kind, str(root)); calls = []
        handle = {"resident_id": "resident", "lineage_id": "lineage", "event_source_id": "source",
                  "effect_id": "effect", "event_id": "event", "trigger_path_id": "path"}
        def turn(**kw):
            calls.append("submit")
            kw["on_assigned"](projection.receipt_from_resident_handle({**handle, "status": "assigned"}))
            raise WorkflowPending(handle, {})
        monkeypatch.setattr(projection, "turn", turn)
        def run(identity):
            try: owner.stage_turn(stage, actor="test", prompt="synthetic", resident_scope=stage)
            except WorkflowPending: owner.worker_write(identity, owner.settle_exception(WorkflowPending(handle, {})))
        owner.run(identity, run, adapter=adapter)
        monkeypatch.setattr(projection, "confirm_input", lambda **kw: {**kw["receipt"], "input_message_id": "canonical"})
        def resume(**kw):
            calls.append("resume"); assert kw["message_id"] == "canonical"
            return {"message": "synthetic result", "usage": {}, "invocation": kw["receipt"]}
        monkeypatch.setattr(projection, "resume", resume)
        def recover(identity):
            assert owner.stage_turn(stage, actor="test")["message"] == "synthetic result"
            assert owner.stage_turn(stage, actor="test")["message"] == "synthetic result"
            owner.worker_write(identity, {"status": "done"})
        owner.run(identity, recover, adapter=adapter)
        assert calls == ["submit", "resume"]


def test_exp_and_review_get_do_not_settle_or_launch(monkeypatch):
    from management_portal import paper_reading_jobs, paper_reviews
    job = {"job_id": "job", "paper_id": "paper", "status": "running", "created_at": 1, "updated_at": 1}
    before = dict(job)
    monkeypatch.setattr(paper_reading_jobs, "_latest_exp_job", lambda **kw: job)
    monkeypatch.setattr(paper_reading_jobs, "_authorize_paper_job_status", lambda *a, **kw: None)
    assert paper_reading_jobs.get_exp_status(actor="test", job_id="job")["status"] == "running"
    monkeypatch.setattr(paper_reviews, "_review_job_by_id", lambda identity: job)
    monkeypatch.setattr(paper_reviews, "is_user_or_resident", lambda actor: True)
    assert paper_reviews.get_review_status(actor="test", job_id="job")["job"]["status"] == "running"
    assert job == before


def test_figure_detail_get_does_not_recover(monkeypatch):
    from management_portal import paper_detail, paper_assets, paper_figure_jobs
    paper = {"id": "paper", "has_pdf": False, "has_analysis": False, "has_summary": False}
    monkeypatch.setattr(paper_detail, "_paper_by_id", lambda identity: paper)
    monkeypatch.setattr(paper_detail, "_load_figures", lambda paper: [{"figure_id": "fig", "explanation_status": "running"}])
    monkeypatch.setattr(paper_assets, "assets_job_status", lambda identity: {})
    monkeypatch.setattr(paper_figure_jobs, "_resume_figure_tasks", lambda *a: pytest.fail("GET launched Figure"))
    result = paper_detail.get_paper_detail("paper", sections=["figures"])
    assert result["figures"][0]["explanation_status"] == "running"


def test_validated_save_failure_stays_save_only_and_legacy_never_resubmits(isolated_portal_case, monkeypatch):
    from management_portal import paper_projection
    with isolated_portal_case() as root:
        seed("exp_jobs", root)
        def work(identity):
            owner.checkpoint("validated_exp", {"content": "generated", "usage": {}})
            patch = owner.settle_exception(OSError("synthetic storage failure"))
            assert patch["status"] == "running" and patch["step"] == "saving result"
            assert not owner.public_state(patch)["can_retry"]
            owner.worker_write(identity, patch)
        owner.run("job", work, adapter=owner.TableAdapter("exp_jobs"))
        store.append("exp_jobs", {"job_id": "legacy", "paper_id": "paper", "status": "running", "created_at": 1})
        monkeypatch.setattr(paper_projection, "turn", lambda **kw: pytest.fail("Legacy input was redelivered"))
        def recover(identity):
            with pytest.raises(paper_projection.InputConfirmationPending):
                owner.stage_turn("paper:exp", actor="test")
        owner.run("legacy", recover, adapter=owner.TableAdapter("exp_jobs"))


@pytest.mark.parametrize("expire", [False, True])
def test_native_image_only_promotes_from_staging_under_live_fence(expire, isolated_portal_case, monkeypatch):
    from PIL import Image
    from management_portal import paper_figure_jobs as jobs, paper_figures
    with isolated_portal_case() as root:
        seed("figure_generation", root)
        adapter, identity = adapter_for("figure_generation", str(root))
        source = root / "source.png"; Image.new("RGB", (2, 2)).save(source)
        monkeypatch.setattr(jobs, "_figures_dir", lambda paper: root / "figures")
        monkeypatch.setattr(jobs, "_paper_root", lambda paper: root)
        monkeypatch.setattr(jobs, "_figure_image_path", lambda *a: source)
        monkeypatch.setattr(jobs, "_paper_store_file", lambda *a: None)
        def native(prompt, *a, **kw):
            destination = Path(next(line for line in prompt.splitlines() if line.endswith(".png")))
            assert destination.name.startswith(".gen-")
            Image.new("RGB", (3, 3)).save(destination)
            if expire:
                with adapter.locked():
                    metadata = adapter.get(identity)["resident_handoff"]
                    metadata[owner.CLAIM_KEY]["expires_at"] = 0
                    adapter.merge(identity, {"resident_handoff": metadata})
            return "synthetic image", {}
        monkeypatch.setattr(jobs, "_run_figure_resident", native)
        results = []
        def work(identity):
            figure = paper_figures._load_figures({"id": "paper"})[0]
            relative, note, usage = jobs._generate_figure_image({"id": "paper"}, figure, "synthetic", "gen")
            assert owner._owner.get()[1]["token"] in relative
            results.append(relative)
            owner.worker_write(identity, {"status": "done", "image_rel_path": relative})
        owner.run(identity, work, adapter=adapter)
        assert bool(results) == (not expire)
        assert len(list((root / "figures" / "generated").glob("gen-*.png"))) == (0 if expire else 1)


def test_losing_figure_claim_does_not_rewrite_metadata(isolated_portal_case):
    with isolated_portal_case() as root:
        seed("figure_explanation", root)
        adapter, identity = adapter_for("figure_explanation", str(root))
        assert owner.claim(identity, adapter)
        path = root / "figures.json"; content, modified = path.read_bytes(), path.stat().st_mtime_ns
        assert owner.claim(identity, adapter) is None
        assert path.read_bytes() == content and path.stat().st_mtime_ns == modified
