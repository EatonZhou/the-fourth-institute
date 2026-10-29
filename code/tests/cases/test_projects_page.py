"""The Projects page: its list, a Project and the header menu, and how Portal reaches it.

:module: platform.core
:design: decision_46c37026e8ea
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from management_portal import http_projects, portal_features
from management_portal.resources import portal as resource_portal


def test_projects_page_lists_opens_and_changes_projects():
    """项目列表按状态分组、筛选与改状态；项目页显示团队与资源并提供编辑、改名和删除

    列表按状态分组且已完成的组默认折叠，组内最近变化在前；标签与搜索筛选；改状态是一次 UpdateProject，被拒绝时恢复原值。
    项目页读出团队的实时状态与工作摘要、按类型的资源入口与最近变化；概况编辑后以 UpdateProject 保存；
    改名先 dry_run，有正在执行的 Resident 时不能确认；删除要输入项目 ID。真实页面模块，指令由夹具应答。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "projects_page.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_research_map_page_steers_directions():
    """研究地图显示方向、团队与时间线；人的姿态、收束、派团队和组建团队都落到地图修订或发给 Resident 的消息

    放弃的方向默认隐藏；候选可在地图上接纳为探索或丢弃（丢弃即标为放弃并记一条决定）；改姿态与收束各是一次带决定记录的修订；
    编辑只提交改动的字段；交给已有团队时先记到地图，再以用户的名义把方向发给该团队的 Leader；组建新团队发给 Project Resident；
    修订号冲突时重读地图并提示；拖动保存位置；回放按修订逐个显示、期间不可编辑；他人的修改到达时重读。真实页面模块，指令由夹具应答。

    :risk: no-external-side-effects
    :profiles: local.fixture
    :design: decision_9d15ae2053c4
    """
    script = Path(__file__).with_name("javascript") / "project_map.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_projects_feature_serves_the_page_and_owns_project_links(monkeypatch):
    """Projects 功能提供页面、导航入口和 Project 的打开地址；没有安装时 Project 打开按它筛选的知识"""
    served = []
    shell = type("Shell", (), {"_serve_authenticated_document": staticmethod(lambda *args: served.append(args) or "page")})
    routes = http_projects.routes(shell)
    assert {(route.method, route.path) for route in routes} == {("GET", "/projects"), ("GET", "/projects/")}
    assert routes[0].handler("request", None, "alice") == "page"
    assert served[0][2].startswith(b"<!doctype html>")
    assert ("projects", http_projects.NAVIGATION) in portal_features.contributions("web.navigation")

    assert resource_portal.portal_url_for_resource("A & B", "project") == "/projects?project=A%20%26%20B"
    try:
        monkeypatch.setattr(portal_features, "contributions", lambda hook: ())
        resource_portal.portal_routes.cache_clear()
        assert resource_portal.portal_url_for_resource("A & B", "project") == "/resource/knowledge?project=A%20%26%20B"
    finally:
        monkeypatch.undo()
        resource_portal.portal_routes.cache_clear()
