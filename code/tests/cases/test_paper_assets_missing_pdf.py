"""Automatic extraction must distinguish absent input from processing failure."""

from unittest.mock import Mock

import pytest

from management_portal import paper_assets


@pytest.fixture
def assets_job(monkeypatch, tmp_path):
    paper = {"id": "paper_0123456789ab", "pdf_path": ""}
    updates = []
    monkeypatch.setattr(paper_assets, "_paper_by_id", lambda _: paper)
    monkeypatch.setattr(paper_assets, "_paper_run_record_by_job_id", lambda *_: {"status": "queued"})
    monkeypatch.setattr(paper_assets, "_update_paper_run_record", lambda _, value: updates.append(value))
    monkeypatch.setattr(paper_assets, "_paper_asset_lock_path", lambda: tmp_path / "assets.lock")
    refs = Mock(side_effect=AssertionError("must not extract without input"))
    figures = Mock(side_effect=AssertionError("must not extract without input"))
    monkeypatch.setattr(paper_assets, "extract_refs", refs)
    monkeypatch.setattr(paper_assets, "extract_figures", figures)
    return paper, updates, refs, figures


@pytest.mark.parametrize("missing_path", ["", "/share/Papers/not-present-audit-fixture.pdf"])
def test_actual_missing_pdf_resolver_skips_automatic_extraction(assets_job, missing_path):
    """论文没有 PDF 或 PDF 文件不存在时，跳过参考文献和图片的自动提取"""
    paper, updates, refs, figures = assets_job
    paper["pdf_path"] = missing_path
    result = paper_assets._run_assets_job(paper["id"], actor="fixture", job_id="assets-fixture")
    assert result["skipped"] is True
    assert updates[-1]["status"] == "skipped"
    assert updates[-1]["refs"] is None and updates[-1]["figures"] is None
    assert updates[-1]["errors"] == []
    refs.assert_not_called()
    figures.assert_not_called()


@pytest.mark.parametrize("error", [PermissionError("PDF access denied"), RuntimeError("invalid PDF bytes")])
def test_input_access_or_format_error_remains_failed(assets_job, monkeypatch, error):
    """PDF 无权读取或格式错误时资源提取任务记为失败，不提取参考文献和图片"""
    paper, updates, refs, figures = assets_job
    monkeypatch.setattr(paper_assets, "_local_pdf_for_paper", Mock(side_effect=error))
    with pytest.raises(type(error), match=str(error)):
        paper_assets._run_assets_job(paper["id"], actor="fixture", job_id="assets-fixture")
    assert updates[-1]["status"] == "error"
    assert updates[-1]["error"] == str(error)
    refs.assert_not_called()
    figures.assert_not_called()
