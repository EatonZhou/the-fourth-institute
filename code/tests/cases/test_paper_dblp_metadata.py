""":importance: P1"""
import io
import json
import urllib.error
import urllib.parse

import pytest

from management_portal import paper_dblp_metadata as metadata


VENUE = {"dblp_section": "conf", "dblp_conf_key": "ppopp", "abbr": "PPoPP", "rank": "A", "category": "并行计算", "source": "CCF"}
TOC = "https://dblp.org/db/conf/ppopp/ppopp2025"
RECORD = "https://dblp.org/rec/conf/ppopp/Example25"


def row(**values):
    return {key: {"value": str(value)} for key, value in values.items()}


def test_transport_uses_documented_read_only_endpoint(monkeypatch):
    """DBLP 查询用 GET 访问 sparql.dblp.org 只读端点，并多取一条检查截断"""
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 30
        return io.BytesIO(json.dumps({"results": {"bindings": [row(year=2025)]}}).encode())

    monkeypatch.setattr(metadata.urllib.request, "urlopen", open_request)
    result = metadata._query("SELECT ?year WHERE {}", 3)
    parsed = urllib.parse.urlsplit(requests[0].full_url)
    assert parsed.scheme == "https" and parsed.netloc == "sparql.dblp.org"
    assert parsed.path == "/sparql"
    assert requests[0].get_method() == "GET"
    assert "LIMIT 4" in urllib.parse.parse_qs(parsed.query)["query"][0]
    assert result == [row(year=2025)]


@pytest.mark.parametrize("body, message", [
    (b'<script id="anubis_challenge">{}</script>', "访问验证页面"),
    (b"<html>Service unavailable</html>", "无效数据"),
    (b'{"exception":"invalid query"}', "缺少结果列表"),
    (b'{"results":{"bindings":[null]}}', "缺少结果列表"),
])
def test_transport_rejects_non_results_without_retry(monkeypatch, body, message):
    """DBLP 返回验证页、HTML 或缺少结果列表时报错且不重试"""
    calls = []

    def open_request(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(body)

    monkeypatch.setattr(metadata.urllib.request, "urlopen", open_request)
    with pytest.raises(metadata.DblpMetadataError, match=message):
        metadata._query("SELECT * WHERE {}", 3)
    assert len(calls) == 1


def test_transport_rejects_truncated_results(monkeypatch):
    """DBLP 结果超过请求条数或响应超过读取上限时按不完整结果报错"""
    body = json.dumps({"results": {"bindings": [row(year=2025), row(year=2024)]}}).encode()
    monkeypatch.setattr(metadata.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    with pytest.raises(metadata.DblpMetadataError, match="不完整结果"):
        metadata._query("SELECT * WHERE {}", 1)
    monkeypatch.setattr(metadata, "MAX_RESPONSE_BYTES", 4)
    with pytest.raises(metadata.DblpMetadataError, match="读取上限"):
        metadata._query("SELECT * WHERE {}", 10)


def test_transport_preserves_http_error_and_does_not_switch_hosts(monkeypatch):
    """DBLP 查询遇 HTTP 错误时报告状态码，不重试也不切换主机"""
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError(metadata.ENDPOINT, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(metadata.urllib.request, "urlopen", fail)
    with pytest.raises(metadata.DblpMetadataError, match="HTTP 429"):
        metadata._query("SELECT * WHERE {}", 3)
    assert len(calls) == 1


def test_catalog_prefers_main_conference_and_rejects_empty_response(monkeypatch):
    """DBLP 会议年份目录优先取主会议而非 workshop，查询结果为空时报错"""
    monkeypatch.setattr(metadata, "_query", lambda *args: [
        row(toc=TOC + "w", year=2025, count=99),
        row(toc=TOC, year=2025, count=50),
        row(toc=TOC.replace("2025", "2024"), year=2024, count=45),
    ])
    entries = metadata.catalog_entries(VENUE)
    assert [(item["entry_id"], item["count"]) for item in entries] == [("2025", 50), ("2024", 45)]
    assert entries[0]["metadata_source"] == "dblp-sparql"
    monkeypatch.setattr(metadata, "_query", lambda *args: [])
    with pytest.raises(metadata.DblpMetadataError, match="未返回可识别"):
        metadata.catalog_entries(VENUE)


def test_journal_retains_volume_identity_and_aggregates_cross_year_counts(monkeypatch):
    """期刊目录按卷标识条目，跨年的同一卷论文数合并计算"""
    venue = {"dblp_section": "journals", "dblp_conf_key": "tpds"}
    monkeypatch.setattr(metadata, "_query", lambda *args: [
        row(toc="https://dblp.org/db/journals/tpds/tpds36", year=2025, volume=36, count=40),
        row(toc="https://dblp.org/db/journals/tpds/tpds36", year=2024, volume=36, count=2),
    ])
    entries = metadata.catalog_entries(venue)
    assert entries[0]["entry_id"] == "tpds36"
    assert entries[0]["issue"] == "36" and entries[0]["label"] == "Volume 36"
    assert entries[0]["year"] == 2025 and entries[0]["count"] == 42


@pytest.mark.parametrize("key", ['ppopp> } UNION { ?a ?b ?c', "../secret", "", "ppopp/other"])
def test_untrusted_venue_cannot_inject_queries(monkeypatch, key):
    """格式非法的会议标识在查询前被拒绝，无法注入 SPARQL"""
    monkeypatch.setattr(metadata, "_query", lambda *args: pytest.fail("invalid input must not reach the endpoint"))
    with pytest.raises(ValueError, match="identity"):
        metadata.catalog_entries({**VENUE, "dblp_conf_key": key})


@pytest.mark.parametrize("address", [
    "http://127.0.0.1/private", "https://other.example/db/conf/ppopp/ppopp2025",
    "https://dblp.org/db/conf/other/other2025", TOC + '> } UNION {',
])
def test_untrusted_toc_cannot_change_source_or_stream(monkeypatch, address):
    """不可信的目录链接无法改变查询来源或注入查询，在请求前被拒绝"""
    monkeypatch.setattr(metadata, "_query", lambda *args: pytest.fail("invalid input must not reach the endpoint"))
    with pytest.raises(metadata.DblpMetadataError):
        metadata.papers_for_entry(VENUE, {"html_url": address})


def test_paper_mapping_preserves_ids_doi_and_author_order(monkeypatch):
    """DBLP 论文映射保留 dblp_key、规范化 DOI，并按署名顺序排列作者"""
    calls = []

    def query(text, limit):
        calls.append(text)
        if "signatureOrdinal" in text:
            return [row(paper=RECORD, ordinal=2, name="Second Author"), row(paper=RECORD, ordinal=1, name="First Author")]
        return [row(paper=RECORD, title="Real metadata.", year=2025, doi="https://doi.org/10.1/example", document="https://doi.org/10.1/example", pages="1-8", booktitle="PPoPP")]

    monkeypatch.setattr(metadata, "_query", query)
    papers = metadata.papers_for_entry(VENUE, {"html_url": TOC + ".html"})
    assert len(calls) == 2
    assert f"<{TOC}>" in calls[0]
    assert papers[0]["dblp_key"] == "conf/ppopp/Example25"
    assert papers[0]["dblp_url"] == RECORD
    assert papers[0]["authors"] == "First Author, Second Author"
    assert papers[0]["doi"] == "10.1/example"
    assert papers[0]["title"] == "Real metadata"
    assert papers[0]["ccf_rank"] == "A" and papers[0]["ccf_source"] == "CCF"


def test_invalid_publication_is_not_silently_skipped(monkeypatch):
    """DBLP 论文缺少标题或年份时报错，不静默跳过"""
    monkeypatch.setattr(metadata, "_query", lambda *args: [row(paper=RECORD, title="", year=2025)])
    with pytest.raises(metadata.DblpMetadataError, match="标题或年份"):
        metadata.papers_for_entry(VENUE, {"html_url": TOC})
