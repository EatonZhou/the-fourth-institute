from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from management_portal import http_overview


def test_daily_news_follows_registered_lineage_folder(monkeypatch, tmp_path):
    from management_portal.society_service import lineage_storage, state_store
    folder = tmp_path / "projects" / "research" / "agents" / "lineage_7e2464bbdb60"
    monkeypatch.setenv("PORTAL_DAILY_NEWS_ROOT", str(tmp_path / "wrong"))
    calls = []
    def projection(identity):
        calls.append(identity)
        return {"project_id": "research"}

    def locate(identity, project):
        assert identity == "lineage_7e2464bbdb60"
        assert project == "research"
        return folder

    monkeypatch.setattr(http_overview.web_clients, "lineage_domain", SimpleNamespace(
        get_lineage_resource_projection=projection))
    monkeypatch.setattr(lineage_storage, "lineage_folder_for_project", locate)
    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: pytest.fail("Web opened Lineage store"))
    assert http_overview._daily_news_root("lineage_7e2464bbdb60") == folder / "memory" / "reports"
    assert calls == ["lineage_7e2464bbdb60"]


def test_daily_news_uses_latest_valid_report_until_today_exists(
    tmp_path,
    monkeypatch,
) -> None:
    """今日新闻未生成时使用最近有效报告并标记过期"""
    today = datetime.now().astimezone().date()
    previous_date = today - timedelta(days=2)
    previous_path = tmp_path / f"{previous_date.isoformat()}__daily_news.md"
    previous_path.write_text("# Previous report\n", encoding="utf-8")
    monkeypatch.setattr(http_overview, "_daily_news_root", lambda _lineage_id: tmp_path)

    fallback = http_overview._daily_news_payload()

    assert fallback["status"] == "ok"
    assert fallback["date"] == previous_date.isoformat()
    assert fallback["requested_date"] == today.isoformat()
    assert fallback["stale"] is True
    assert fallback["content"] == "# Previous report\n"

    current_path = tmp_path / f"{today.isoformat()}__daily_news.md"
    current_path.write_text("# Current report\n", encoding="utf-8")

    current = http_overview._daily_news_payload()

    assert current["status"] == "ok"
    assert current["date"] == today.isoformat()
    assert current["stale"] is False
    assert current["content"] == "# Current report\n"


@pytest.mark.parametrize("failure", [OSError("read failed"), UnicodeError("invalid UTF-8")])
def test_daily_news_current_failure_does_not_fall_back(monkeypatch, tmp_path, failure):
    from pathlib import Path

    today = datetime.now().astimezone().date()
    current = tmp_path / f"{today.isoformat()}__daily_news.md"
    current.write_text("# Today", encoding="utf-8")
    (tmp_path / f"{(today - timedelta(days=1)).isoformat()}__daily_news.md").write_text("# Old")
    monkeypatch.setattr(http_overview, "_daily_news_root", lambda _lineage_id: tmp_path)
    read = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == current:
            raise failure
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    result = http_overview._daily_news_payload()
    assert result["status"] == "error"
    assert result["content"] == ""
    assert result["error"] == str(failure)
    assert result["path"] == str(current)


def test_daily_news_owner_unavailable_is_panel_error(monkeypatch):
    from management_portal.society_service import SocietyServiceUnavailable

    def unavailable(_identity):
        raise SocietyServiceUnavailable("Society temporarily unavailable")

    monkeypatch.setattr(http_overview.web_clients, "lineage_domain", SimpleNamespace(
        get_lineage_resource_projection=unavailable))
    result = http_overview._daily_news_payload()
    assert result["status"] == "error"
    assert result["path"] == ""
    assert result["content"] == ""
    assert result["error"] == "Society temporarily unavailable"


@pytest.mark.parametrize("actor", ["fixture-user", "resident_111111111111", None])
def test_daily_news_requires_authenticated_page_principal(monkeypatch, actor):
    from urllib.parse import urlparse

    request = SimpleNamespace(headers={"Authorization": "fixture-only"})
    monkeypatch.setattr(http_overview.http_auth, "_get_page_api_actor", lambda request: actor)
    monkeypatch.setattr(http_overview.http_responses, "_serve_json", lambda request, status, data: (status, data))
    monkeypatch.setattr(http_overview.http_responses, "_serve_http_error", lambda request, status, error: (status, error))
    monkeypatch.setattr(http_overview, "_daily_news_payload", lambda edition="embodied": {"status": "ok", "content": "# News"})
    route = next(r for r in http_overview.routes() if r.path == "/api/daily-news")
    assert route.phase == "dual-auth"
    status, data = route.handler(request, urlparse(route.path), None)
    assert status == (200 if actor else 401)
    if actor:
        assert data["content"] == "# News"


def test_daily_news_x_edition_reads_x_lineage_reports(monkeypatch, tmp_path):
    from urllib.parse import urlparse

    today = datetime.now().astimezone().date().isoformat()
    report = tmp_path / f"{today}__daily_news.md"
    report.write_text("# X daily news\n", encoding="utf-8")
    calls = []

    def locate(lineage_id):
        calls.append(lineage_id)
        return tmp_path

    request = SimpleNamespace(headers={"Authorization": "fixture-only"})
    monkeypatch.setattr(http_overview.http_auth, "_get_page_api_actor", lambda request: "fixture-user")
    monkeypatch.setattr(http_overview.http_responses, "_serve_json", lambda request, status, data: (status, data))
    monkeypatch.setattr(http_overview, "_daily_news_root", locate)
    route = next(r for r in http_overview.routes() if r.path == "/api/daily-news")

    status, data = route.handler(request, urlparse("/api/daily-news?edition=x"), None)

    assert status == 200
    assert calls == ["lineage_c625a4bee56b"]
    assert data["status"] == "ok"
    assert data["edition"] == "x"
    assert data["editions"] == [
        {"id": "embodied", "title": "具身智能"},
        {"id": "x", "title": "X 资讯"},
    ]
    assert data["content"] == "# X daily news\n"


def test_daily_news_rejects_unknown_edition(monkeypatch):
    from urllib.parse import urlparse

    request = SimpleNamespace(headers={"Authorization": "fixture-only"})
    monkeypatch.setattr(http_overview.http_auth, "_get_page_api_actor", lambda request: "fixture-user")
    monkeypatch.setattr(http_overview.http_responses, "_serve_json", lambda request, status, data: (status, data))
    monkeypatch.setattr(http_overview, "_daily_news_payload", lambda edition="embodied": pytest.fail("invalid edition reached report reader"))
    route = next(r for r in http_overview.routes() if r.path == "/api/daily-news")

    status, data = route.handler(request, urlparse("/api/daily-news?edition=unknown"), None)

    assert status == 400
    assert data["status"] == "error"
    assert data["error"] == "unknown Daily News edition: unknown"
    assert [edition["id"] for edition in data["editions"]] == ["embodied", "x"]
