"""Article saves compare the read revision within the writer's critical section."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from management_portal import article_files, article_registry
from management_portal import http_auth, http_request_body, http_responses, web_resources
from management_portal import serve_management_portal as portal


@pytest.fixture
def manuscript(tmp_path, monkeypatch):
    target = tmp_path / "main.tex"
    target.write_text("original\n", encoding="utf-8")
    target.chmod(0o640)
    monkeypatch.setattr(article_registry, "_article_target_path", lambda *_: (tmp_path, target))
    return target


def test_save_compares_revision_and_preserves_newer_file(manuscript):
    read = article_files.read_article_file("article_0123456789ab", "main.tex")
    result = article_files.write_article_file("article_0123456789ab", "main.tex", "new\n", expected_revision=read["revision"])
    assert result["revision"] == hashlib.sha256(b"new\n").hexdigest()
    assert manuscript.stat().st_mode & 0o777 == 0o640
    with pytest.raises(article_files.ArticleFileConflict) as error:
        article_files.write_article_file("article_0123456789ab", "main.tex", "stale\n", expected_revision=read["revision"])
    assert error.value.current_revision == result["revision"]
    assert manuscript.read_text() == "new\n"


@pytest.mark.parametrize("revision", [None, "", "not-a-revision", {}, "A" * 64])
def test_save_rejects_missing_or_invalid_revision(manuscript, revision):
    with pytest.raises(ValueError, match="expected_revision"):
        article_files.write_article_file("article_0123456789ab", "main.tex", "overwrite", expected_revision=revision)
    assert manuscript.read_text() == "original\n"


def test_concurrent_saves_have_one_winner(manuscript):
    revision = article_files.read_article_file("article_0123456789ab", "main.tex")["revision"]
    start = threading.Barrier(2)

    def save(content):
        start.wait(timeout=5)
        try:
            article_files.write_article_file("article_0123456789ab", "main.tex", content, expected_revision=revision)
            return content
        except article_files.ArticleFileConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["writer one", "writer two"]))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert manuscript.read_text() == winners[0]


def test_http_save_revision_contract_and_permission(manuscript, monkeypatch):
    require_resource, notify = Mock(), Mock()
    payload = {"path": "main.tex", "content": "updated"}
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(web_resources, "_require_resource", require_resource)
    monkeypatch.setattr(web_resources, "_notify_resource_index", notify)
    monkeypatch.setattr(http_request_body, "_read_request_json", lambda request: payload)
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, value: (status, value))
    route = next(route for route in portal._portal_route_definitions() if route.method == "POST" and route.path == "/api/articles/{article_id}/file")
    path = "/api/articles/article_0123456789ab/file"

    def send():
        return route.handler(SimpleNamespace(), urlparse(path), "ignored", route.template_params(path))

    assert send()[0] == 400
    payload["expected_revision"] = article_files.read_article_file("article_0123456789ab", "main.tex")["revision"]
    status, saved = send()
    assert status == 200
    payload["content"] = "outdated"
    status, conflict = send()
    assert status == 409
    assert conflict["code"] == "revision_conflict"
    assert conflict["current_revision"] == saved["revision"]
    assert manuscript.read_text() == "updated"
    assert require_resource.call_count == 3
    require_resource.assert_called_with("article_0123456789ab", "alice")
    notify.assert_called_once()


@pytest.mark.parametrize("name", ["幻灯 片.PPTX", "slides.ppt", "main.tex"])
def test_file_descriptor_declares_preview_url(tmp_path, monkeypatch, name):
    target = tmp_path / name
    target.write_bytes(b"example")
    monkeypatch.setattr(article_files, "resolve_article_raw_file", lambda *_: target)
    result = article_files.describe_article_file("article_0123456789ab", name)
    assert result["preview_url"] == (result["rawUrl"] + "&preview=pdf" if target.suffix.lower() in {".ppt", ".pptx"} else "")
    assert result["downloadUrl"] == result["rawUrl"] + "&download=1"
