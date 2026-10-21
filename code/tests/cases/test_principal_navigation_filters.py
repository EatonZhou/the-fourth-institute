"""My Projects filters in the Lineages pane apply to the Principal navigation tree.

:module: lineage.team
:importance: P1
:aspects: behavior
:design: decision_3e07c6d35cac
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "management_portal/static/principal_navigation.js"

HIERARCHY = {"projects": [
    {"project_id": "alpha", "title": "Alpha", "lineage_id": "lineage_alpha_main", "resident_id": "resident_alpha",
     "lineages": [{"lineage_id": "lineage_a1", "title": "Survey", "resident_id": "resident_a1"}]},
    {"project_id": "beta", "title": "Beta", "lineage_id": "", "resident_id": "", "lineages": []},
    {"project_id": "gamma", "title": "Gamma", "lineage_id": "lineage_gamma_main", "resident_id": "resident_gamma",
     "lineages": []},
]}


def project_ids(**options):
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(MODULE))});\n"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "const groups = window.PortalPrincipalNavigation.projectGroups(input.hierarchy, [], input.options);\n"
        "process.stdout.write(JSON.stringify(groups.map(group => group.project_id)));\n"
    )
    result = subprocess.run(
        ["node", "-e", script], input=json.dumps({"hierarchy": HIERARCHY, "options": options}),
        text=True, capture_output=True, timeout=30, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_options_keep_every_project():
    """默认导航选项保留全部项目"""
    assert project_ids() == ["alpha", "beta", "gamma"]


def test_project_filter_keeps_only_the_selected_project():
    """项目筛选只保留选定项目"""
    assert project_ids(projectFilter="beta") == ["beta"]
    assert project_ids(projectFilter="all") == ["alpha", "beta", "gamma"]


def test_non_empty_hides_projects_without_any_lineage():
    """非空筛选隐藏没有任何 Lineage 的项目"""
    # gamma has no child Lineage but its Project-level Lineage exists, so it has content.
    assert project_ids(nonEmptyOnly=True) == ["alpha", "gamma"]


def test_lineages_pane_passes_its_filters_to_my_projects():
    """Lineage 面板向项目导航传递筛选条件"""
    source = (ROOT / "management_portal/static/lineage_workbench.js").read_text(encoding="utf-8")
    start = source.index("function residentGroups()")
    body = source[start:source.index("function residentSidebarPendingText(", start)]
    assert "projectFilter: state.projectFilter" in body
    assert 'nonEmptyOnly: state.projectDisplayFilter === "non-empty"' in body
