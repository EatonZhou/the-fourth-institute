"""Knowledge 页面：服务端渲染的页面外壳与它加载的静态脚本。

:module: workflow.knowledge
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

from management_portal.page_renderers import render_knowledge_html

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "management_portal" / "static"


def _body_tag(html: str) -> str:
    return re.search(r"<body\b[^>]*>", html).group(0)


def test_article_detail_page_marks_its_body_to_hide_the_link_graph():
    """嵌入的 Article 详情页在 body 上标记隐藏关系图，其他页面不标记"""
    article = render_knowledge_html(resource_category="article", hide_detail_graph=True).decode()
    assert 'data-resource-detail-hide-graph="1"' in _body_tag(article)
    assert "knowledgeInitializing" in _body_tag(article)
    knowledge = render_knowledge_html().decode()
    assert "data-resource-detail-hide-graph" not in _body_tag(knowledge)


def test_page_loads_its_parts_before_the_root_script():
    """页面先加载各部件脚本再加载根脚本，所列文件都存在，同前缀的静态脚本都被加载"""
    html = render_knowledge_html().decode()
    scripts = re.findall(r'<script src="/static/(knowledge_board[a-z_]*\.js)"></script>', html)
    assert scripts[-1] == "knowledge_board.js" and len(scripts) > 1
    assert all((STATIC / name).is_file() for name in scripts)
    assert set(scripts) == {path.name for path in STATIC.glob("knowledge_board*.js")}
    assert html.index("/static/portal_board_shell.js") < html.index("/static/knowledge_board_common.js")
    assert '<link rel="stylesheet" href="/static/knowledge_board.css" />' in html
    assert "<style" not in html and not re.search(r"<script>\s", html)


@pytest.mark.parametrize("script", [
    "knowledge_delete_refresh.cjs",
    "knowledge_detail_graph_budget.cjs",
    "knowledge_search_card.cjs",
])
def test_board_scripts_pass_their_node_checks(script):
    """Knowledge 页面脚本的删除刷新、详情关系图布局和搜索卡片导航检查通过"""
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / script)], cwd=ROOT, check=True, timeout=60)
