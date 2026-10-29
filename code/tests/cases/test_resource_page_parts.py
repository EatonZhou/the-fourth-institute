"""Paper、Article 与 Repo 页面由根脚本和它创建的模块脚本组成。

:module: workbench.preview
:importance: P1
"""
from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from management_portal import http_articles

ROOT = Path(__file__).resolve().parents[2] / "management_portal"
STATIC = ROOT / "static"

# page, its own scripts' prefix, the root script's module lookup
PAGES = (
    ("paper.html", "paper", "paperModule"),
    ("article_workspace.html", "article_workspace", "articleModule"),
    ("repo_workspace.html", "repo_workspace", "repoModule"),
)
REGISTERED = re.compile(r'window\.(\w+) = Object\.freeze\(\{ (\w+), dependencies: DEPENDENCIES \}\);')


def own_scripts(page: str, prefix: str) -> list[str]:
    html = (ROOT / page).read_text(encoding="utf-8")
    return [name for name in re.findall(r'<script src="/static/([a-z_]+\.js)"', html)
            if name == f"{prefix}.js" or name.startswith(f"{prefix}_")]


def creations(root: str, lookup: str) -> dict[str, tuple[str, set[str]]]:
    """Each part the root script creates: global -> (factory, the dependency names it passes)."""
    found = {}
    for match in re.finditer(rf'{lookup}\("(\w+)"\)\.(\w+)\(\{{\n(.*?)\n  \}}\);', root, re.S):
        names = {item.split(":")[0].strip() for item in re.split(r",\s*", match.group(3)) if item.strip()}
        found[match.group(1)] = (match.group(2), names)
    return found


@pytest.mark.parametrize("page,prefix,lookup", PAGES)
def test_root_creates_every_part_its_page_loads_with_exactly_its_dependencies(page, prefix, lookup):
    """页面先加载各模块脚本再加载根脚本，根脚本创建每个模块且只传入该模块声明的依赖"""
    scripts = own_scripts(page, prefix)
    assert scripts[-1] == f"{prefix}.js", scripts
    root = (STATIC / scripts[-1]).read_text(encoding="utf-8")
    created = creations(root, lookup)
    modules = {}
    for name in scripts[:-1]:
        source = (STATIC / name).read_text(encoding="utf-8")
        registered = REGISTERED.findall(source)
        assert len(registered) == 1, name
        dependencies = re.search(r"const DEPENDENCIES = Object\.freeze\(\[(.*?)\]\);", source, re.S)
        modules[registered[0][0]] = (registered[0][1], set(re.findall(r'"([\w$]+)"', dependencies.group(1))))
    assert set(created) == set(modules)
    for name, (factory, dependencies) in modules.items():
        assert created[name] == (factory, dependencies), name


def test_article_page_scripts_are_served_as_its_public_assets():
    """Article 页面加载的自有样式和脚本都登记为公开静态资源"""
    marker = object()
    portal = SimpleNamespace(RequestBodyTooLarge=Exception, _PUBLIC_PAGES_HTTP=SimpleNamespace(static=marker))
    public = {route.path for route in http_articles.routes(portal) if route.handler is marker}
    html = (ROOT / "article_workspace.html").read_text(encoding="utf-8")
    assets = set(re.findall(r'(?:src|href)="(/static/article_workspace[a-z_]*\.(?:css|js))"', html))
    assert len(assets) > 2
    assert public == assets
