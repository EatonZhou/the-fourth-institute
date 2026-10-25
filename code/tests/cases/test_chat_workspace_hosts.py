"""Every page that loads the chat workspace loads all of its scripts in one order."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRY = "/static/portal_chat_workspace.js"
CHAT_SCRIPT = re.compile(r"/static/(?:chat_workspace_[a-z_]+|portal_chat_workspace)\.js")


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


def chat_scripts(path):
    parser = ScriptParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return [script for script in parser.scripts if CHAT_SCRIPT.fullmatch(script[0])]


PAGES = [*sorted((ROOT / "management_portal").glob("*.html")),
         *sorted((ROOT / "management_portal/test_framework/page").glob("*.html")),
         *sorted(ROOT.glob("*_lan/*.html"))]
HOSTS = [path for path in PAGES if any(source == ENTRY for source, *_ in chat_scripts(path))]
LINEAGE = [source for source, *_ in chat_scripts(ROOT / "management_portal/lineage.html")]


def test_lineage_page_loads_the_chat_workspace_modules_before_the_workspace():
    """lineage.html 在 portal_chat_workspace.js 之前加载对话工作区的模块脚本。

    :module: workbench.chat
    """
    assert LINEAGE and LINEAGE[-1] == ENTRY


@pytest.mark.parametrize("path", HOSTS, ids=[str(path.relative_to(ROOT)) for path in HOSTS])
def test_chat_hosts_load_the_chat_workspace_scripts_like_lineage(path):
    """加载对话工作区的页面按 lineage.html 的顺序加载它的全部脚本，加载方式与入口脚本相同。

    :module: workbench.chat
    """
    scripts = chat_scripts(path)
    assert [source for source, *_ in scripts] == LINEAGE
    entry = next(script for script in scripts if script[0] == ENTRY)
    assert all(script[1:] == entry[1:] for script in scripts)
