from __future__ import annotations

from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.resources.portal import portal_route_script
from management_portal import test_framework as framework


ROOT = Path(__file__).resolve().parents[2]


class _MarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[tuple[dict[str, str | None], list[str]]] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "script":
            self._script_parts = []
            self.scripts.append((attributes, self._script_parts))

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._script_parts = None


def _frame_owner(monkeypatch: pytest.MonkeyPatch, case: SimpleNamespace) -> tuple[ModuleType, Mock]:
    catalog = Mock()
    catalog.get.return_value = case
    isolated_api = ModuleType("management_portal.test_framework.api")
    isolated_api.service = lambda: SimpleNamespace(catalog=catalog)
    specification = importlib.util.spec_from_file_location(
        "isolated_demo_frame_owner", ROOT / "management_portal/test_framework/live_adapter.py"
    )
    assert specification is not None and specification.loader is not None
    owner = importlib.util.module_from_spec(specification)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "management_portal.test_framework.api", isolated_api)
        imports.setattr(framework, "api", isolated_api, raising=False)
        specification.loader.exec_module(owner)
    return owner, catalog


def test_sandbox_frame_inlines_canonical_routes_before_dependent_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沙盒生成器内联虚拟资源路由契约，且在 ResourceChips 与 ChatWorkspace 之前初始化。

    沙盒生成器内联虚拟资源路由契约，且在 ResourceChips 与 ChatWorkspace 之前初始化。"""
    case = SimpleNamespace(
        id="local-demo-fixture",
        live={"mode": "sandbox", "target": "/test-demo/components"},
    )
    owner, catalog = _frame_owner(monkeypatch, case)
    frame = owner.demo_frame_html(case.id, "local-demo-frame-contract-session")
    catalog.get.assert_called_once_with(case.id)
    markup = _MarkupParser()
    markup.feed(frame.decode("utf-8"))
    inline_scripts = {
        str(attributes.get("data-portal-demo-src") or "").partition("?")[0]: (
            index, "".join(parts)
        )
        for index, (attributes, parts) in enumerate(markup.scripts)
    }
    route_index, route_script = inline_scripts["/static/portal_resource_routes.js"]
    assert route_script.strip() == portal_route_script().decode("utf-8").strip()
    for dependency in ("portal_resource_chips.js", "portal_chat_workspace.js"):
        dependency_index, dependency_script = inline_scripts[f"/static/{dependency}"]
        assert dependency_script.strip()
        assert route_index < dependency_index


def test_demo_caption_uses_the_existing_readable_caption_style() -> None:
    """Live 说明段绑定现有字幕样式类，避免在深色说明区继承默认文字颜色。

    Live 说明段绑定现有字幕样式类，避免在深色说明区继承默认文字颜色。"""
    page_root = ROOT / "management_portal/test_framework/page"
    markup = _MarkupParser()
    markup.feed((page_root / "test_native_view.html").read_text(encoding="utf-8"))
    captions = [
        attributes
        for tag, attributes in markup.elements
        if tag == "p" and attributes.get("id") == "testDemoCaption"
    ]
    assert len(captions) == 1
    assert "testDemoCaption" in str(captions[0].get("class") or "").split()
    styles = (page_root / "test_workbench.css").read_text(encoding="utf-8")
    assert re.search(r"\.testDemoCaption\s*\{[^}]*\bcolor\s*:", styles)
