""":module: spec.design-governance"""
from __future__ import annotations

import pytest
import yaml

from management_portal import yaml_files
from management_portal.yaml_files import load_yaml_file


def test_parse_is_reused_until_the_content_changes(tmp_path, monkeypatch):
    """YAML 文件内容不变时复用解析结果，同大小原地改写后重新解析"""
    path = tmp_path / "registry.yaml"
    path.write_text("apis:\n  - id: one\n", encoding="utf-8")
    parses = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml_files.yaml, "safe_load", lambda text: parses.append(text) or real(text))

    assert load_yaml_file(path) == {"apis": [{"id": "one"}]}
    assert load_yaml_file(path) == {"apis": [{"id": "one"}]}
    assert len(parses) == 1

    # Same size, rewritten in place within the timestamp granularity.
    path.write_text("apis:\n  - id: two\n", encoding="utf-8")
    assert load_yaml_file(path) == {"apis": [{"id": "two"}]}
    assert len(parses) == 2


def test_callers_get_independent_copies(tmp_path):
    """每次读取 YAML 文件得到独立副本，修改返回值不影响后续读取"""
    path = tmp_path / "registry.yaml"
    path.write_text("apis:\n  - id: one\n", encoding="utf-8")
    first = load_yaml_file(path)
    first["apis"][0]["id"] = "changed"
    assert load_yaml_file(path) == {"apis": [{"id": "one"}]}


def test_read_and_parse_errors_are_unchanged(tmp_path):
    """YAML 文件缺失或格式错误时原样抛出读取或解析异常"""
    with pytest.raises(FileNotFoundError):
        load_yaml_file(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("apis: [unclosed\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_yaml_file(broken)
