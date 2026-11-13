"""Bounded Paper reads and paper-scoped cleanup, using synthetic data only.

:design: decision_528bf9985705
"""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from management_portal import paper_assets, paper_detail, paper_resources, paper_state_store, paper_store


@pytest.mark.parametrize("limit", [0, 20, 120_000])
def test_text_preview_bounds_the_read_and_preserves_unicode(tmp_path, monkeypatch, limit):
    path = tmp_path / "analysis.md"
    raw = ("论文🙂\r\n" * 40_000).encode() + b"\xffend"
    path.write_bytes(raw)
    expected = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")[:limit]
    original_open = Path.open
    sizes = []

    class MeasuredReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    @contextmanager
    def measured_open(target, *args, **kwargs):
        with original_open(target, *args, **kwargs) as stream:
            yield MeasuredReader(stream) if target == path else stream

    monkeypatch.setattr(Path, "open", measured_open)
    assert paper_store._read_text_preview(path, limit=limit) == expected
    assert sizes and all(0 <= size <= limit for size in sizes), sizes


def test_text_preview_preserves_missing_and_invalid_utf8_behavior(tmp_path):
    assert paper_store._read_text_preview(None) == ""
    assert paper_store._read_text_preview(tmp_path / "missing") == ""
    path = tmp_path / "summary.md"
    path.write_bytes(b"a\xffb")
    assert paper_store._read_text_preview(path, limit=2) == "a\ufffd"


def test_detail_loads_figures_only_after_recovery_and_only_when_requested(monkeypatch):
    paper = {"id": "paper_0123456789ab", "has_pdf": False,
             "has_analysis": False, "has_summary": False}
    monkeypatch.setattr(paper_detail, "_paper_by_id", lambda _: paper)
    monkeypatch.setattr(paper_assets, "assets_job_status", lambda _: {})
    state = {"recovered": False}
    observed = []

    def recover(_paper):
        state["recovered"] = True

    def figures(_paper):
        observed.append(state["recovered"])
        return [{"figure_id": "figure_0123456789ab", "index": 1,
                 "explanation_status": "error" if state["recovered"] else "running"}]

    monkeypatch.setattr(paper_detail, "_recover_figure_tasks", recover)
    monkeypatch.setattr(paper_detail, "_load_figures", figures)
    monkeypatch.setattr(paper_detail, "_figure_public", lambda item, _paper_id: item)
    result = paper_detail.get_paper_detail(paper["id"], sections="none")
    assert result["loaded_sections"] == [] and observed == [] and not state["recovered"]
    result = paper_detail.get_paper_detail(paper["id"], sections="figures")
    assert result["figures"][0]["explanation_status"] == "running"
    assert observed == [False], "a detail read must not recover, settle or discard current figure data"
    assert not state['recovered']


def test_delete_queries_only_target_reviews_and_preserves_other_paper(monkeypatch, isolated_portal_case):
    from management_portal.workbench_projections import resource_lifecycle

    with isolated_portal_case():
        target, other = "paper_0123456789ab", "paper_abcdef012345"
        own_review, other_review = "review_0123456789ab", "review_abcdef012345"
        for paper_id, review_id in [(target, own_review), (other, other_review)]:
            paper_state_store.append("reviews", {"review_id": review_id, "paper_id": paper_id,
                                                   "raw_response": "synthetic review " * 1000})
        calls = []
        records = paper_state_store.records

        def measured_records(kind, *, paper_id=None):
            calls.append((kind, paper_id))
            return records(kind, paper_id=paper_id)

        monkeypatch.setattr(paper_state_store, "records", measured_records)
        monkeypatch.setattr(paper_resources, "_paper_by_id", lambda _: {"id": target, "title": "Synthetic"})
        deletion = Mock(return_value={"paper_id": target})
        releases = Mock(return_value=[])
        monkeypatch.setattr(paper_resources.PAPER_STORE, "delete_paper", deletion)
        monkeypatch.setattr(resource_lifecycle, "finish_resource_releases", releases)
        monkeypatch.setattr(paper_resources, "_announce_review_changes", lambda: None)
        result = paper_resources.delete_paper({"paper_id": target}, "synthetic-actor")
        assert result["deleted"] is True
        deletion.assert_called_once_with(target, actor="synthetic-actor", projection_ids=[own_review])
        releases.assert_called_once_with([target, own_review])
        assert paper_state_store.get("reviews", own_review) is None
        assert paper_state_store.get("reviews", other_review)["paper_id"] == other
        assert calls == [("reviews", target)], "deleting one Paper must not load the entire review library"
