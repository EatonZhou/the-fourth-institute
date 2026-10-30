"""Public branding consumes the site reader without leaking deployment settings."""
import html
import pytest


@pytest.fixture(autouse=True)
def isolated_public_roots(tmp_path, monkeypatch):
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, "path_values", lambda: {key: str(tmp_path / key) for key in portal_paths.KEYS})

import re
import sys
from types import ModuleType

from management_portal.page_bootstrap import inject_page_bootstrap


def test_site_name_on_public_page_is_safe_and_read_per_response(monkeypatch):
    source = ModuleType("management_common.site_config")
    names = iter(['Research "A" <Lab> & </script><script>bad()</script> {p0}', "Other Institute"])
    source.site_name = lambda: next(names)
    source.read_site_config = lambda: {}
    source.browser_storage_namespace = lambda: "installation-fixture"
    monkeypatch.setitem(sys.modules, source.__name__, source)
    document = b'''<html><head><title data-portal-site-title='["Login"]'>Login</title>
      <script src="/static/portal_i18n.js"></script></head><body>
      <a href="/" data-portal-site-name>Portal</a><p>User body: TFI Portal</p>
      <a title="a > b" href="/user">Unrelated link</a></body></html>'''
    result = inject_page_bootstrap(document).decode()
    assert 'id="portalSiteScript"' in result
    assert result.index('id="portalSiteScript"') < result.index('src="/static/portal_i18n.js')
    assert '\\u003c/script\\u003e' in result
    assert result.count('</script>') == 2
    assert '<p>User body: TFI Portal</p>' in result
    assert '<a title="a > b" href="/user">Unrelated link</a>' in result
    title = html.unescape(re.search(r'<title[^>]*>(.*?)</title>', result).group(1))
    assert title == 'Login — Research "A" <Lab> & </script><script>bad()</script> {p0}'
    assert inject_page_bootstrap(result.encode()) == result.encode()
    assert 'Login — Other Institute' in inject_page_bootstrap(document).decode()


def test_plain_preview_does_not_consume_site_configuration(monkeypatch):
    source = ModuleType("management_common.site_config")
    source.site_name = lambda: (_ for _ in ()).throw(AssertionError("unexpected configuration read"))
    monkeypatch.setitem(sys.modules, source.__name__, source)
    document = b'<html><head><title>User preview</title></head><body>Portal</body></html>'
    assert inject_page_bootstrap(document) == document


def test_real_reader_default_and_configured_name_reach_public_and_signed_in_shells(tmp_path, monkeypatch):
    from pathlib import Path
    from management_portal import serve_management_portal as portal
    from management_portal import http_auth, http_documents, http_responses, user_preferences

    config = tmp_path / "site.json"
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(config))
    monkeypatch.setattr(http_auth, "_get_session_username", lambda handler: handler)
    monkeypatch.setattr(user_preferences, "_portal_user_preferences", lambda username: {"appearance": {"language": "en"}})
    monkeypatch.setattr(http_responses, "_serve_payload", lambda *args, **kwargs: args[2])
    root = Path(portal.__file__).parent
    for actor, page in (("", "login.html"), ("", "join.html"), ("someone", "index.html")):
        source = (root / page).read_bytes()
        default = http_documents._serve_bytes(actor, 200, source, "text/html").decode()
        assert '"site_name": "TFI Portal"' in default
        assert 'id="portalSiteScript"' in default
        config.write_text('{"site_name":"Research & Lab", "private_test_key":"must-not-be-public"}')
        configured = http_documents._serve_bytes(actor, 200, source, "text/html").decode()
        assert '"site_name": "Research \\u0026 Lab"' in configured
        assert "must-not-be-public" not in configured
        assert "Research &amp; Lab" in configured
        assert "TFI Portal" not in configured
        if actor:
            assert "__PORTAL_LANGUAGE__" in configured
        config.unlink()


def test_guide_site_name_is_literal_markdown_text(tmp_path, monkeypatch):
    from management_portal import http_user_guide as guide
    name = 'Lab [click](https://invalid.example) **Bold** <Lab> &lt;Raw&gt;'
    monkeypatch.setattr(guide, "site_name", lambda: name)
    monkeypatch.setattr(guide, "GUIDE_ROOT", tmp_path)
    (tmp_path / "human.md").write_text('__PORTAL_SITE_NAME__ 是工作台。')
    rendered, _ = guide._render("human", guide.guide_source("human"))
    assert '<a ' not in rendered
    assert '<strong>' not in rendered
    assert name in html.unescape(re.sub(r'<[^>]*>', '', rendered))
