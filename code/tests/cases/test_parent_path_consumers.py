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


def test_preview_presentation_roots_are_instance_areas(tmp_path, monkeypatch):
    from management_portal.society_service import workspace_domain
    monkeypatch.setattr(workspace_domain, 'portal_path', lambda key: tmp_path / key)
    assert workspace_domain._preview_root(tmp_path / 'projects/file') == tmp_path / 'projects'
    assert workspace_domain._preview_root(tmp_path / 'outside/file') is None


def test_catalog_projects_follow_the_projects_area(tmp_path, monkeypatch):
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, 'path', lambda key: tmp_path / key)
    relative = 'management_portal/test_framework/tools/sync_catalog.py'
    assert load(relative, 'tfi_catalog_default').PROJECTS_ROOT == tmp_path / 'projects'


def test_packaging_and_main_bindings_are_separate_configured_targets(tmp_path, monkeypatch):
    config = {"repo_id": "repo_111111111111", "lineage_id": "lineage_222222222222",
              "facade_resident_id": "resident_333333333333", "project_ids": ["demo"],
              "packaging_lineage_id": "lineage_444444444444", "packaging_facade_resident_id": "resident_555555555555",
              "system_lineage_id": "lineage_666666666666"}
    from tests.cases.instance_test_support import write_instance
    root = write_instance(tmp_path / 'instance', site={'deployment': {'project_ids': ['demo'], 'development_projection': config}})
    path = root / 'site.json'
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    module = load("management_portal/workbench_projections/development_projection.py", "management_portal.workbench_projections.tfi_development_projection")
    result = module.application_definition("1" * 40)
    assert result["bindings"]["main"]["lineage_id"] == config["lineage_id"]
    assert result["bindings"]["packaging"] == {"lineage_id": config["packaging_lineage_id"],
        "residents": {"facade": config["packaging_facade_resident_id"]}, "events": {}}
    assert result["project_ids"] == ["demo"]
    assert result["application"]["directory"].startswith("code/management_portal/")
    assert result["bindings"]["packaging_system"] == {"lineage_id": config["system_lineage_id"], "residents": {}, "events": {}}
    assert result["manifest"]["sidebar"][-1]["chat"] == {"binding": "packaging", "role": "facade"}
    config.pop("packaging_facade_resident_id")
    path.write_text(json.dumps({"deployment": {"development_projection": config}}))
    with pytest.raises(ValueError, match="packaging_facade_resident_id"):
        module.application_definition("1" * 40)
