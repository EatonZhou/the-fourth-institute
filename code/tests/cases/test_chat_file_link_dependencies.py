"""Native hosts must load shared owners before their synchronous consumers."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess

import pytest

from management_portal import script_groups


ROOT = Path(__file__).resolve().parents[2]


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            if attributes.get("src"):
                self.scripts.append(attributes)


def script_hosts(consumer):
    """Pages that load ``consumer``, with their scripts as the server sends them."""
    for path in sorted((ROOT / "management_portal").glob("*.html")) + sorted(ROOT.glob("*_lan/*.html")):
        parser = ScriptParser()
        parser.feed(script_groups.expand_document(path.read_text(encoding="utf-8")))
        if any(f"/static/{consumer}" in script["src"] for script in parser.scripts):
            yield pytest.param(parser.scripts, id=str(path.relative_to(ROOT)))


@pytest.mark.parametrize("scripts", list(script_hosts("portal_chat_workspace.js")))
def test_chat_hosts_load_shared_dependencies_before_chat(scripts):
    """Chat 页面在聊天脚本之前同步加载预览、文件链接和 Resident 运行依赖。"""
    by_source = {script["src"].partition("?")[0]: (index, script) for index, script in enumerate(scripts)}
    consumer_index, consumer_script = by_source["/static/portal_chat_workspace.js"]
    for dependency in (
        "portal_preview_contract.js",
        "portal_file_links.js",
        "portal_resident_service.js",
        "portal_resident_runtime_controller.js",
    ):
        dependency_index, dependency_script = by_source[f"/static/{dependency}"]
        assert dependency_index < consumer_index
        assert "async" not in dependency_script
        assert "async" not in consumer_script
        assert "defer" not in dependency_script or "defer" in consumer_script


@pytest.mark.parametrize("scripts", list(script_hosts("portal_nav.js")))
def test_navigation_hosts_load_theme_before_normalizing_preferences(scripts):
    """导航页面在 portal_nav.js 之前同步加载 portal_theme.js"""
    by_source = {script["src"].partition("?")[0]: (index, script) for index, script in enumerate(scripts)}
    theme_index, theme = by_source["/static/portal_theme.js"]
    nav_index, nav = by_source["/static/portal_nav.js"]
    assert theme_index < nav_index
    assert "async" not in theme and "async" not in nav
    assert "defer" not in theme or "defer" in nav


def test_dynamic_chat_waits_for_shared_file_links_and_preview_requests_match():
    """动态打开 Chat 前等待共享文件链接加载，文件链接与 Workspace 预览发出相同的编码请求

    Resident 菜单动态加载 Chat 时只请求一次 portal_file_links.js，加载完成前不打开组件；对含中文、空格和 ?#& 的本机路径，
    文件链接探测与预览元数据、内容读取使用同一个 link-file 地址，共享地址不授予写权限。

    :module: workbench.preview
    :design: decision_61e50306cc79
    """
    script = Path(__file__).with_name("javascript") / "chat_file_link_loader.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
