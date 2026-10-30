"""The new-user guide: one public entrance (/guide) that people and agents both find."""
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from management_portal import http_user_guide as guide
from management_portal.hatg.catalog import instruction_catalog


def test_both_guides_exist_and_share_one_page():
    """各类指南共享同一页面且标题锚点携带指南前缀"""
    page = guide.render_guide_page().decode("utf-8")
    for name in guide.GUIDES:
        assert guide.guide_source(name).strip(), name
        assert f'data-guide-panel="{name}"' in page and f'href="/guide/{name}.md"' in page
    # Headings of both guides share the page, so every anchor names its guide.
    assert not [anchor for anchor in re.findall(r'<h[23] id="([^"]+)"', page)
                if not anchor.startswith(("human-", "agent-", "admin-"))]


def test_links_between_guides_stay_on_the_page():
    """指南之间的相对链接转换为页内锚点"""
    body, outline = guide._render("human", "## 加入 Portal\n\n[a](agent.md) [b](./agent.md#凭证安全) [c](#加入-portal) [d](https://x.test/agent.md)\n")
    assert 'href="#agent"' in body and 'href="#agent-凭证安全"' in body and 'href="#human-加入-portal"' in body
    assert 'href="https://x.test/agent.md"' in body
    assert outline == [("human-加入-portal", "加入 Portal")]
    assert "<script" not in guide._render("human", "<script>alert(1)</script>\n")[0]


def test_agent_guide_names_only_real_instructions():
    """Agent 指南引用的指令均存在于实际目录"""
    catalog = set(instruction_catalog())
    text = guide.guide_source("agent")
    named = {word for word in re.findall(r"`([A-Z][A-Za-z]+)`", text) if re.fullmatch(r"(?:[A-Z][a-z]+){2,}", word)}
    assert named, "the agent guide should point at concrete instructions"
    assert sorted(named - catalog) == []


@pytest.mark.parametrize("name", guide.GUIDES)
def test_guide_sources_link_only_to_public_or_relative_targets(name):
    """指南源码仅链接公开地址或相对目标"""
    for target in re.findall(r"\]\(([^)\s]+)\)", guide.guide_source(name)):
        parts = urlsplit(target)
        assert parts.scheme in {"", "https"} and not target.startswith("/share"), target


def _request(accept=""):
    return SimpleNamespace(headers={"Accept": accept, "Accept-Language": ""})


def test_agents_get_markdown_and_people_get_the_page(monkeypatch):
    """指南按请求类型提供 Markdown 或网页并支持缓存校验"""
    from management_portal import serve_management_portal as portal
    from management_portal import http_documents

    sent, headers = [], []

    def serve(request, status, data, content_type, **kwargs):
        sent.append((status, content_type, data))
        headers.append(kwargs.get("extra_headers") or {})

    monkeypatch.setattr(http_documents, "_serve_bytes", serve)
    guide.guide_page(_request("text/markdown"), urlsplit("/guide"), "")
    guide.guide_page(_request("text/html,application/xhtml+xml"), urlsplit("/guide"), "")
    guide.guide_markdown(_request(), urlsplit("/guide/agent.md"), "")
    guide.llms_txt(_request(), urlsplit("/llms.txt"), "")
    guide.client_script(_request(), urlsplit("/guide/portal_user.py"), "")
    (index_status, index_type, index), (_, page_type, _page), (_, md_type, md), (_, txt_type, txt), (_, py_type, py) = sent
    assert index_status == 200 and index_type.startswith("text/markdown") and b"/guide/agent.md" in index
    assert page_type.startswith("text/html")
    assert md_type.startswith("text/markdown") and md.decode() == guide.guide_source("agent")
    assert re.fullmatch(r'"[0-9a-f]{16}"', headers[2]["ETag"])
    # An agent that still holds the current guide gets 304 without the body.
    guide.guide_markdown(SimpleNamespace(headers={"Accept": "", "Accept-Language": "", "If-None-Match": headers[2]["ETag"]}),
                         urlsplit("/guide/agent.md"), "")
    assert sent[-1][0] == 304 and sent[-1][2] == b"" and headers[-1]["ETag"] == headers[2]["ETag"]
    assert txt_type.startswith("text/plain") and b"/api/instructions/execute" in txt
    assert py_type.startswith("text/x-python") and b"def main()" in py


def test_unauthenticated_instruction_call_points_at_the_agent_guide():
    """未认证指令调用返回 Agent 指南地址"""
    import asyncio

    from starlette.requests import Request

    from management_portal import asgi

    scope = {"type": "http", "method": "POST", "path": "/api/instructions/execute", "headers": [], "query_string": b""}
    original = asgi._instruction_principal
    asgi._instruction_principal = lambda request: ("", "")
    try:
        response = asyncio.run(asgi.instruction_execute(Request(scope)))
    finally:
        asgi._instruction_principal = original
    assert response.status_code == 401
    assert b'"guide": "/guide/agent.md"' in response.body or b'"guide":"/guide/agent.md"' in response.body
