""":importance: P1"""
import json

import pytest

from management_portal import paper_ccf, paper_ccf_enrichment, paper_dblp_metadata


@pytest.mark.parametrize("section", ["conf", "journals"])
def test_ccf_challenge_does_not_erase_catalog_cache_or_retry(monkeypatch, tmp_path, section):
    """DBLP 返回访问验证页时报错且不重试，已有的年份目录缓存保持不变"""
    cache = tmp_path / "years.json"
    original = {"years": [{"year": 2025, "entry_id": "2025"}]}
    cache.write_text(json.dumps(original))
    requests = []

    def fetch(query, limit):
        requests.append(query)
        raise paper_dblp_metadata.DblpMetadataError("DBLP 返回了访问验证页面")

    monkeypatch.setattr(paper_dblp_metadata, "_query", fetch)
    monkeypatch.setattr(paper_ccf, "_ccf_cache_path", lambda *args: cache)
    venue = {"dblp_conf_key": "fixture", "dblp_section": section}

    with pytest.raises(RuntimeError, match="DBLP 返回了访问验证页面"):
        paper_ccf._dblp_entries_for_venue(venue, force=True)

    assert len(requests) == 1
    assert json.loads(cache.read_text()) == original
    assert paper_ccf._dblp_entries_for_venue(venue, allow_network=False) == original["years"]
    assert len(requests) == 1


def test_ccf_valid_catalog_still_loads_and_caches(monkeypatch, tmp_path):
    """DBLP 返回正常的会议年份目录时解析出年份和链接并写入缓存"""
    cache = tmp_path / "years.json"
    monkeypatch.setattr(paper_ccf, "_ccf_cache_path", lambda *args: cache)
    monkeypatch.setattr(paper_dblp_metadata, "_query", lambda *args, **kwargs: [{
        "toc": {"value": "https://dblp.org/db/conf/ppopp/ppopp2025"},
        "year": {"value": "2025"}, "count": {"value": "50"},
    }])

    entries = paper_ccf._dblp_entries_for_venue({"dblp_conf_key": "ppopp"}, force=True)

    assert [entry["year"] for entry in entries] == [2025]
    assert entries[0]["html_url"] == "https://dblp.org/db/conf/ppopp/ppopp2025"
    assert json.loads(cache.read_text())["years"] == entries


def test_ccf_paper_refresh_keeps_previous_results_on_source_failure(monkeypatch, tmp_path):
    """CCF 论文刷新时数据源失败，保留之前缓存的论文列表"""
    venue = {"dblp_conf_key": "ppopp", "dblp_section": "conf"}
    entry = {"entry_id": "2025", "year": 2025, "html_url": "https://dblp.org/db/conf/ppopp/ppopp2025"}
    cache = tmp_path / "papers.json"
    original = {"papers": [{"dblp_key": "conf/ppopp/Existing25", "title": "Existing paper"}], "entry": entry}
    cache.write_text(json.dumps(original))
    monkeypatch.setattr(paper_ccf, "_ccf_venue_by_key", lambda key: venue)
    monkeypatch.setattr(paper_ccf, "_ccf_cache_path", lambda *args: cache)
    monkeypatch.setattr(paper_ccf, "_dblp_entries_for_venue", lambda *args, **kwargs: [entry])

    def fail(*args):
        raise paper_dblp_metadata.DblpMetadataError("source unavailable")

    monkeypatch.setattr(paper_dblp_metadata, "papers_for_entry", fail)
    with pytest.raises(paper_dblp_metadata.DblpMetadataError, match="source unavailable"):
        paper_ccf.ccf_papers({"venue": "ppopp", "year": "2025", "refresh": "1"})
    assert json.loads(cache.read_text()) == original


def test_ccf_papers_keep_registered_order_and_serve_cache_without_network(monkeypatch, tmp_path):
    """CCF 论文列表标出已登记的论文，之后的读取直接用缓存不访问网络"""
    venue = {"dblp_conf_key": "ppopp", "dblp_section": "conf"}
    entry = {"entry_id": "2025", "year": 2025, "html_url": "https://dblp.org/db/conf/ppopp/ppopp2025"}
    cache = tmp_path / "papers.json"
    monkeypatch.setattr(paper_ccf_enrichment, "CCF_ENRICHMENT_CACHE_ROOT", tmp_path / "enrichment")
    monkeypatch.setattr(paper_ccf, "_ccf_venue_by_key", lambda key: venue)
    monkeypatch.setattr(paper_ccf, "_ccf_cache_path", lambda *args: cache)
    monkeypatch.setattr(paper_ccf, "_dblp_entries_for_venue", lambda *args, **kwargs: [entry])
    monkeypatch.setattr(paper_ccf, "resource_index_papers", lambda: [{"id": "paper-existing", "title": "Existing paper"}])
    papers = [{"dblp_key": "conf/ppopp/New25", "title": "New paper", "year": 2025}, {"dblp_key": "conf/ppopp/Existing25", "title": "Existing paper", "year": 2025}]
    monkeypatch.setattr(paper_dblp_metadata, "papers_for_entry", lambda *args: papers)
    loaded = paper_ccf.ccf_papers({"venue": "ppopp", "year": "2025", "refresh": "1"})
    assert loaded["papers"][0]["registered_paper_id"] == "paper-existing"
    assert not loaded["papers"][1]["registered"]
    monkeypatch.setattr(paper_dblp_metadata, "papers_for_entry", lambda *args: pytest.fail("cached read must not fetch"))
    assert paper_ccf.ccf_papers({"venue": "ppopp", "year": "2025"}) == loaded


@pytest.mark.parametrize("status", ["pending", "running", "done", "error", "web_pending"])
def test_ccf_catalog_projects_current_enrichment_without_restarting_work(monkeypatch, tmp_path, status):
    """CCF 论文列表读取时显示当前的补全结果，不重新抓取元数据也不提交新任务

    :importance: P2"""
    venue = {"dblp_conf_key": "ppopp", "dblp_section": "conf"}
    entry = {"entry_id": "2025", "year": 2025}
    paper = {"dblp_key": "conf/ppopp/Fixture25", "title": "Fixture paper"}
    untouched = {"dblp_key": "conf/ppopp/Untouched25", "title": "Untouched paper"}
    cache = tmp_path / "papers.json"
    original = {"papers": [{**paper, "enrichment": {"status": "done", "summary": "outdated"}}, untouched], "entry": entry}
    cache.write_text(json.dumps(original))
    monkeypatch.setattr(paper_ccf, "_ccf_venue_by_key", lambda key: venue)
    monkeypatch.setattr(paper_ccf, "_ccf_cache_path", lambda *args: cache)
    monkeypatch.setattr(paper_ccf_enrichment, "CCF_ENRICHMENT_CACHE_ROOT", tmp_path / "enrichment")
    monkeypatch.setattr(paper_ccf, "resource_index_papers", lambda: [])
    monkeypatch.setattr(paper_dblp_metadata, "papers_for_entry", lambda *args: pytest.fail("read must not fetch metadata"))
    monkeypatch.setattr(paper_ccf_enrichment, "_start_ccf_enrichment_task", lambda *args, **kwargs: pytest.fail("read must not submit work"))
    result_path = paper_ccf_enrichment._ccf_enrichment_cache_path(paper)
    result_path.parent.mkdir()
    result = {"status": status, "summary": "current output", "message_id": "msg_original"}
    result_path.write_text(json.dumps(result))

    loaded = paper_ccf.ccf_papers({"venue": "ppopp", "year": "2025"})

    assert loaded["papers"][0]["enrichment"] == result
    assert loaded["papers"][1]["enrichment"] == {}
    assert json.loads(cache.read_text()) == original
    result["summary"] = "updated through the domain, not a page button"
    result_path.write_text(json.dumps(result))
    reloaded = paper_ccf.ccf_papers({"venue": "ppopp", "year": "2025"})
    assert reloaded["papers"][0]["enrichment"] == result
    assert json.loads(result_path.read_text()) == result
