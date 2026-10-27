"""Pages load the chat workspace as one script group, listed only in script_groups.json."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from management_portal import script_groups


ROOT = Path(__file__).resolve().parents[2]
ENTRY = "/static/portal_chat_workspace.js"
CHAT_SCRIPT = re.compile(r"/static/(?:chat_workspace_[a-z_]+|portal_chat_workspace)\.js")
GROUP = script_groups.scripts("chat-workspace")


class ScriptParser(HTMLParser):
    """Script sources in document order, with their loading attributes. A
    native view lists its scripts in a JSON block, which is read in place."""

    def __init__(self):
        super().__init__()
        self.scripts = []
        self._json = None

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        attributes = dict(attrs)
        if attributes.get("src"):
            self.scripts.append((attributes["src"].partition("?")[0], "defer" in attributes, "async" in attributes))
        self._json = [] if attributes.get("type") == "application/json" else None

    def handle_data(self, data):
        if self._json is not None:
            self._json.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json is not None:
            listed = json.loads("".join(self._json))
            if isinstance(listed, dict):
                self.scripts.extend((source, False, False) for source in listed.get("scripts", []))
            self._json = None


def chat_scripts(text):
    parser = ScriptParser()
    parser.feed(text)
    return [script for script in parser.scripts if CHAT_SCRIPT.fullmatch(script[0])]


PAGES = [*sorted((ROOT / "management_portal").glob("*.html")),
         *sorted((ROOT / "management_portal/test_framework/page").glob("*.html")),
         *sorted(ROOT.glob("*_lan/*.html"))]
HOSTS = [path for path in PAGES if script_groups.MARKER in path.read_text(encoding="utf-8")]
IDS = [str(path.relative_to(ROOT)) for path in HOSTS]


def test_chat_group_lists_every_module_once_before_the_entry_script():
    """chat-workspace 脚本组列出全部 Chat 模块各一次，入口脚本 portal_chat_workspace.js 排在最后。

    :module: workbench.chat
    """
    modules = {f"/static/{path.name}" for path in (ROOT / "management_portal/static").glob("chat_workspace_*.js")}
    assert GROUP[-1] == ENTRY
    assert len(set(GROUP)) == len(GROUP)
    assert set(GROUP[:-1]) == modules


def test_pages_name_the_chat_group_instead_of_its_scripts():
    """加载对话工作区的页面只写脚本组，不逐个列出 Chat 脚本。

    :module: workbench.chat
    """
    assert {"management_portal/lineage.html", "management_portal/index.html", "management_portal/wall.html",
            "management_portal/test_framework/page/test_native_view.html"} <= set(IDS)
    for path in PAGES:
        assert not CHAT_SCRIPT.search(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("path", HOSTS, ids=IDS)
def test_expanded_hosts_load_the_chat_group_in_order_like_its_placeholder(path):
    """页面展开后按脚本组的顺序加载对话工作区的全部脚本，加载方式与占位元素相同。

    :module: workbench.chat
    """
    text = path.read_text(encoding="utf-8")
    scripts = chat_scripts(script_groups.expand_document(text))
    assert tuple(source for source, *_ in scripts) == GROUP
    placeholder = re.search(r"<script\b[^>]*data-portal-script-group[^>]*>", text)
    loading = (bool(placeholder and " defer" in placeholder[0]), False)
    assert all(tuple(script[1:]) == loading for script in scripts)


def test_expansion_keeps_the_rest_of_a_document():
    """展开只替换占位元素和资源清单中的脚本组，其余内容与缩进保持不变。

    :module: workbench.chat
    """
    text = ('<head>\n    <script src="/static/a.js"></script>\n'
            '    <script defer data-portal-script-group="chat-workspace"></script>\n'
            '<script type="application/json" id="xNativeViewAssets">'
            '{"styles": ["/static/x.css"], "scripts": ["/static/a.js", {"script-group": "chat-workspace"}, "/static/x.js"]}'
            '</script>\n</head>')
    expanded = script_groups.expand_document(text)
    lines = expanded.splitlines()
    assert lines[1] == '    <script src="/static/a.js"></script>'
    assert lines[2:2 + len(GROUP)] == [f'    <script defer src="{source}"></script>' for source in GROUP]
    assets = json.loads(re.search(r'id="xNativeViewAssets">(.*?)</script>', expanded, re.S)[1])
    assert assets == {"styles": ["/static/x.css"], "scripts": ["/static/a.js", *GROUP, "/static/x.js"]}
    assert script_groups.expand_document("<p>no groups</p>") == "<p>no groups</p>"


def test_unknown_group_is_an_error():
    """页面写了不存在的脚本组时展开报错，不静默丢掉脚本。

    :module: workbench.chat
    """
    with pytest.raises(KeyError, match="no script group 'missing'"):
        script_groups.expand_document('<script data-portal-script-group="missing"></script>')


def test_server_serves_every_script_of_the_group_publicly():
    """服务端的公开静态文件列表包含脚本组中的每个脚本。

    :module: workbench.chat
    """
    from management_portal import serve_management_portal as portal

    public = {route.path for route in portal._portal_route_definitions()
              if route.method == "GET" and route.phase == "public" and route.match == "exact"}
    assert set(GROUP) <= public
