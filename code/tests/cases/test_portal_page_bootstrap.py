import json
import re
from pathlib import Path

from management_portal.page_bootstrap import inject_page_bootstrap


def test_bootstrap_precedes_resources_and_preserves_versioned_manifest():
    """页面启动脚本插在所有资源之前，清单保留带版本号的资源并去重，重复注入无变化"""
    document = b'''<!doctype html><html><head>
      <script src="/static/portal_theme.js?portal_v=123"></script>
      <link rel="stylesheet" href="/static/portal.css?portal_v=456">
      <script src="/static/app.js"></script><script src="/static/app.js"></script>
      <link rel="preload" href="/static/optional.js" as="script">
      </head><body><header data-portal-site-header></header>
      <main id="workbench" data-portal-boot-required="Workbench"></main>
      <img src="/static/example.png"></body></html>'''
    result = inject_page_bootstrap(document).decode()
    assert result.index('id="portalPageBootScript"') < result.index('src="/static/portal_theme.js')
    config = json.loads(re.search(r'\}\s*\)\((\{.*?\})\);</script>', result).group(1))
    assert config == {
        "assets": ["/static/portal_theme.js?portal_v=123", "/static/portal.css?portal_v=456", "/static/app.js"],
        "navigation": True,
        "checks": [{"id": "workbench", "label": "Workbench"}],
    }
    assert inject_page_bootstrap(result.encode()) == result.encode()


def test_bootstrap_escapes_manifest_and_does_not_instrument_unrelated_html():
    """页面启动脚本转义清单中的 script 结束符，不注入与 Portal 无关的 HTML"""
    document = b'''<html><head><script src="/static/portal_theme.js"></script></head>
      <body><div id="root" data-portal-boot-required="&lt;/script&gt;broken"></div></body></html>'''
    result = inject_page_bootstrap(document)
    assert b'\\u003c/script>' in result
    assert result.count(b'</script>') == 2
    for plain in (b'<html><head></head><body>Preview</body></html>',
                  b'<head><script src="/static/portal_theme.js"></script></head><body>Login</body>'):
        assert inject_page_bootstrap(plain) == plain


def test_declared_eager_workbenches_are_included_in_manifest():
    """Lineage、论文和文章页声明的工作台根节点列入启动检查清单"""
    root = Path(__file__).resolve().parents[2] / "management_portal"
    for name, host in (("lineage.html", "lineageWorkbenchRoot"),
                       ("paper.html", "paperWorkbench"),
                       ("article_workspace.html", "articleWorkspaceGrid")):
        result = inject_page_bootstrap((root / name).read_bytes()).decode()
        config = json.loads(re.search(r'\}\s*\)\((\{.*?\})\);</script>', result).group(1))
        assert {check["id"] for check in config["checks"]} == {host}
        assert config["navigation"] is True


def test_html_response_injects_after_versioning_without_changing_identity_or_cache_policy(monkeypatch):
    """HTML 响应在资源加版本号后注入启动脚本，身份引导顺序和缓存策略不变"""
    from management_portal import serve_management_portal as portal

    monkeypatch.setattr(portal, "_PORTAL_BUILD_ID", "boot-contract")
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: "playwright")
    monkeypatch.setattr(portal, "_portal_user_preferences", lambda username: {})
    monkeypatch.setattr(portal, "_serve_payload", lambda *args, **kwargs: (args, kwargs))
    html = b'''<html><head><script src="/static/portal_theme.js"></script>
      <script src="/static/portal_identity.js"></script></head>
      <body><header data-portal-site-header></header></body></html>'''
    args, options = portal._serve_bytes(None, 200, html, "text/html", cache_control="private, no-cache")
    result = args[2].decode()
    config = json.loads(re.search(r'\}\s*\)\((\{.*?\})\);</script>', result).group(1))
    assert config["assets"] == ["/static/portal_theme.js?portal_v=boot-contract", "/static/portal_identity.js?portal_v=boot-contract"]
    assert result.index('id="portalPageBootScript"') < result.index('__PORTAL_IDENTITY_BOOTSTRAP__')
    assert result.index('__PORTAL_IDENTITY_BOOTSTRAP__') < result.index('src="/static/portal_identity.js')
    assert options["cache_control"] == "private, no-cache"
    args, _ = portal._serve_bytes(None, 200, b'{}', "application/json")
    assert args[2] == b'{}'
