"""站点目录不扩大预览边界，项目目录和投影绑定随安装配置解析。"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("TFI_PATH_CANDIDATE_ROOT", Path(__file__).resolve().parents[2]))


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preview_functions(tmp_path, deployment):
    source = ROOT / "management_portal/society_service/workspace_domain.py"
    tree = ast.parse(source.read_text())
    names = {"_link_preview_roots", "_preview_root"}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    from management_common.portal_paths import resolve_directory
    namespace = {"Path": Path, "resolve_directory": resolve_directory,
                 "portal_path": lambda key: tmp_path / key,
                 "read_site_config": lambda: {"deployment": deployment}}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_preview_roots_follow_configuration_without_implicit_home_access(tmp_path):
    deployment = {"workspace": {"additional_preview_roots": [str(tmp_path / "configured")]}}
    functions = preview_functions(tmp_path, deployment)
    root = functions["_preview_root"]
    assert root(tmp_path / "share_root/file") == tmp_path / "share_root"
    assert root(tmp_path / "configured/file") == tmp_path / "configured"
    assert root(tmp_path / "outside/file") is None
    deployment["workspace"]["additional_preview_roots"] = []
    assert root(tmp_path / "configured/file") is None


@pytest.mark.parametrize("value", ["/", "", "../outside", "x/../outside", "a\npath", None, 7])
def test_preview_configuration_rejects_broad_or_invalid_roots(tmp_path, value):
    functions = preview_functions(tmp_path, {"workspace": {"additional_preview_roots": [value]}})
    with pytest.raises(ValueError):
        functions["_link_preview_roots"]()


def test_catalog_projects_follow_share_and_explicit_relative_override(tmp_path, monkeypatch):
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, "path", lambda key: tmp_path / "share")
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", tmp_path / "install")
    for key in ["PORTAL_PROJECTS_ROOT", "PORTAL_PROJECTS_ROOT"]:
        monkeypatch.delenv(key, raising=False)
    relative = "management_portal/test_framework/tools/sync_catalog.py"
    assert load(relative, "tfi_catalog_default").PROJECTS_ROOT == tmp_path / "share/Projects"
    monkeypatch.setenv("PORTAL_PROJECTS_ROOT", "configured/projects")
    monkeypatch.chdir(tmp_path)
    assert load(relative, "tfi_catalog_override").PROJECTS_ROOT == tmp_path / "install/configured/projects"


def test_packaging_and_main_bindings_are_separate_configured_targets(tmp_path, monkeypatch):
    config = {"repo_id": "repo_111111111111", "lineage_id": "lineage_222222222222",
              "facade_resident_id": "resident_333333333333", "project_ids": ["demo"],
              "packaging_lineage_id": "lineage_444444444444", "packaging_facade_resident_id": "resident_555555555555",
              "system_lineage_id": "lineage_666666666666"}
    path = tmp_path / "site.json"
    path.write_text(json.dumps({"deployment": {"development_projection": config}}))
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(path))
    module = load("management_portal/workbench_projections/development_projection.py", "management_portal.workbench_projections.tfi_development_projection")
    result = module.application_definition("1" * 40)
    assert result["bindings"]["main"]["lineage_id"] == config["lineage_id"]
    assert result["bindings"]["packaging"] == {"lineage_id": config["packaging_lineage_id"],
        "residents": {"facade": config["packaging_facade_resident_id"]}, "events": {}}
    assert result["project_ids"] == ["demo"]
    assert result["bindings"]["packaging_system"] == {"lineage_id": config["system_lineage_id"], "residents": {}, "events": {}}
    assert result["manifest"]["sidebar"][-1]["chat"] == {"binding": "packaging", "role": "facade"}
    config.pop("packaging_facade_resident_id")
    path.write_text(json.dumps({"deployment": {"development_projection": config}}))
    with pytest.raises(ValueError, match="packaging_facade_resident_id"):
        module.application_definition("1" * 40)


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
