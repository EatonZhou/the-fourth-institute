"""
:module: workbench.panels
:importance: P1
:design: decision_27253e25f016
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from email.message import Message
from pathlib import Path

import pytest

from management_portal import localization
from management_portal import serve_management_portal as portal


def headers(**values):
    result = Message()
    for name, value in values.items():
        result[name.replace("_", "-")] = value
    return result


@pytest.mark.parametrize(("accept", "expected"), [
    ("en-US,en;q=0.8,zh;q=0.5", "en"),
    ("en;q=0.2,zh-TW;q=0.9", "zh-CN"),
    ("en;q=0,zh;q=0.5", "zh-CN"),
    ("fr,zh;q=bad,en;q=0.4", "en"),
])
def test_request_language_negotiation_preserves_quality_and_supported_language_order(accept, expected):
    """按 Accept-Language 权重协商界面语言，忽略不支持和格式错误的条目"""
    assert localization.request_locale(headers(Accept_Language=accept)) == expected


def test_explicit_language_wins_over_cookie_and_browser_without_changing_account_security():
    """显式语言优先于 cookie 和浏览器语言，修改语言偏好只改外观中的语言字段"""
    request = headers(X_Portal_Language="en", Cookie="portal_language=zh-CN", Accept_Language="zh-CN")
    assert localization.request_locale(request) == "en"
    assert localization.request_locale(headers(Cookie="portal_language=en", Accept_Language="zh-CN")) == "en"
    assert localization.request_locale(headers(Accept_Language="en"), language="zh-CN") == "zh-CN"
    assert localization.request_locale(headers()) == "en"
    preferences = portal._merge_portal_user_preferences(
        {"appearance": {"motion": "reduced", "language": "zh-CN"}, "onboarding": {"welcome_dismissed": True}},
        {"appearance": {"language": "en"}},
    )
    assert preferences["appearance"] == {"motion": "reduced", "language": "en"}
    assert preferences["onboarding"]["welcome_dismissed"] is True
    assert portal._normalize_portal_user_preferences({"appearance": {"language": "<script>"}})["appearance"]["language"] == "auto"


def test_http_error_localization_changes_only_display_fields_and_preserves_placeholders(monkeypatch):
    """HTTP 错误本地化只翻译显示字段并保留占位符，错误码和详情不变"""
    monkeypatch.setattr(localization, "catalog", lambda: {
        "Access denied": {"zh-CN": "无访问权限", "en": "Access denied"},
        "Resource {p0} is unavailable": {"zh-CN": "资源 {p0} 暂不可用", "en": "Resource {p0} is unavailable"},
    })
    localization.message_patterns.cache_clear()
    original = {"error": "Resource {p1}</script> is unavailable", "message": "Access denied", "code": "resource.unavailable", "details": {"title": "Access denied"}, "retryable": True, "commit_state": "committed"}
    result = localization.localize_error(original, "zh-CN")
    assert result["error"] == "资源 {p1}</script> 暂不可用"
    assert result["message"] == "无访问权限"
    assert {key: result[key] for key in ("code", "details", "retryable", "commit_state")} == {key: original[key] for key in ("code", "details", "retryable", "commit_state")}
    assert original["message"] == "Access denied"
    nested = localization.localize_error({"error": {"message": "Access denied", "code": "instruction.forbidden", "details": {"message": "Access denied"}}}, "zh-CN")
    assert nested["error"]["message"] == "无访问权限"
    assert nested["error"]["details"]["message"] == "Access denied"
    localization.message_patterns.cache_clear()


def test_asgi_localizes_chunked_errors_and_leaves_successful_resource_content_untouched(monkeypatch):
    """ASGI 中间件翻译分块返回的错误响应并修正长度，成功响应内容保持原样"""
    monkeypatch.setattr(localization, "catalog", lambda: {"Access denied": {"zh-CN": "无访问权限", "en": "Access denied"}})
    localization.message_patterns.cache_clear()
    async def exercise(status):
        events = []
        async def application(scope, receive, send):
            body = json.dumps({"error": "Access denied", "code": "operation.forbidden", "details": {"message": "Access denied"}}).encode()
            await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"vary", b"Origin")]})
            await send({"type": "http.response.body", "body": body[:12], "more_body": True})
            await send({"type": "http.response.body", "body": body[12:]})
        async def receive():
            return {"type": "http.request"}
        async def send(event):
            events.append(event)
        await localization.PortalLocaleMiddleware(application)({"type": "http", "headers": [(b"x-portal-language", b"zh-CN")]}, receive, send)
        return events
    failure = asyncio.run(exercise(403))
    body = json.loads(failure[1]["body"])
    assert body["error"] == "无访问权限"
    assert body["details"]["message"] == "Access denied"
    assert dict(failure[0]["headers"])[b"content-length"] == str(len(failure[1]["body"])).encode()
    assert dict(failure[0]["headers"])[b"vary"].startswith(b"Origin,")
    success = asyncio.run(exercise(200))
    assert len(success) == 3
    assert json.loads(b"".join(event.get("body", b"") for event in success))["error"] == "Access denied"
    localization.message_patterns.cache_clear()


def test_feature_catalogs_join_the_core_catalog_only_when_present(monkeypatch, tmp_path):
    """功能的翻译片段存在时并入核心目录，缺失的功能不提供片段；两处译文不同时浏览器目录构建报错"""
    core, fragments = tmp_path / "portal_translations.json", tmp_path / "translations"
    fragments.mkdir()
    core.write_text(json.dumps({"保存": {"en": "Save", "zh-CN": "保存"}}, ensure_ascii=False))
    (fragments / "wall.json").write_text(json.dumps({"登记工程": {"en": "Register project", "zh-CN": "登记工程"}}, ensure_ascii=False))
    (fragments / "unknown.json").write_text(json.dumps({"别处": {"en": "Elsewhere", "zh-CN": "别处"}}, ensure_ascii=False))
    monkeypatch.setattr(localization, "CATALOG_PATH", core)
    monkeypatch.setattr(localization, "FRAGMENTS_DIRECTORY", fragments)
    assert localization.catalog_paths() == [core, fragments / "wall.json"]
    merged, conflicts = localization.merge_catalogs(localization.catalog_paths())
    assert merged == {"保存": {"en": "Save", "zh-CN": "保存"}, "登记工程": {"en": "Register project", "zh-CN": "登记工程"}}
    assert conflicts == []

    (fragments / "paper.json").write_text(json.dumps({"保存": {"en": "Keep", "zh-CN": "保存"}}, ensure_ascii=False))
    merged, conflicts = localization.merge_catalogs(localization.catalog_paths())
    assert merged["保存"]["en"] == "Save"
    assert conflicts == [(fragments / "paper.json", "保存")]
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_portal_translations.py"
    spec = importlib.util.spec_from_file_location("build_portal_translations_under_test", script)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, "catalog_paths", localization.catalog_paths)
    with pytest.raises(ValueError, match="translates '保存' differently"):
        build.build()


def test_original_mode_preserves_mixed_copy_preferences_and_http_bytes():
    """original 语言模式不翻译任何文案，HTTP 响应字节原样输出"""
    assert localization.normalize_language("original") == "original"
    assert localization.request_locale(headers(X_Portal_Language="original", Cookie="portal_language=en", Accept_Language="en")) == "original"
    assert localization.request_locale(headers(Cookie="portal_language=original", Accept_Language="en")) == "original"
    preferences = portal._merge_portal_user_preferences({"appearance": {"motion": "reduced"}}, {"appearance": {"language": "original"}})
    assert preferences["appearance"] == {"motion": "reduced", "language": "original"}
    for message in ("Access denied", "无访问权限", "Resource {p1} is unavailable"):
        assert localization.translate_message(message, "original") == message
    original = [
        {"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json")]},
        {"type": "http.response.body", "body": b'{"error": "Access denied"}', "more_body": False},
    ]
    async def exercise():
        received = []
        async def app(scope, receive, send):
            for event in original:
                await send(event)
        async def receive():
            return {"type": "http.request"}
        async def send(event):
            received.append(event)
        await localization.PortalLocaleMiddleware(app)({"type": "http", "headers": [(b"x-portal-language", b"original")]}, receive, send)
        return received
    assert asyncio.run(exercise()) == original
