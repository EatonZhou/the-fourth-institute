from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import pytest

@pytest.fixture
def paths(tmp_path, monkeypatch):
    root = Path(os.environ.get('TFI_PATH_CANDIDATE_ROOT', Path(__file__).resolve().parents[2]))
    spec = importlib.util.spec_from_file_location('tfi_paths_candidate', root / 'management_common/portal_paths.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'PORTAL_ROOT', tmp_path / 'source')
    monkeypatch.setattr(module, 'CONFIG_PATH', tmp_path / '.config/tfi/portal_paths.json')
    module.site_paths = tmp_path / 'deployment/paths.json'
    for key in [*module.ENV_KEYS.values(), 'PORTAL_PATHS_CONFIG']:
        monkeypatch.delenv(key, raising=False)
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'deployment': {'compatibility': {
        'domain_values': {'paths_config': str(module.site_paths)},
    }}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))
    yield module

def configured_paths(paths, tmp_path):
    values = {key: str(tmp_path / 'kept' / key) for key in paths.KEYS}
    paths.site_paths.parent.mkdir(parents=True)
    paths.site_paths.write_text(json.dumps(values))
    return values

def test_legacy_read_and_new_write_leave_original_bytes_and_mtime(paths, tmp_path, monkeypatch):
    """实例声明优先于默认配置，显式覆盖才切换；原文件保持原字节。"""
    original = configured_paths(paths, tmp_path)
    before = (paths.site_paths.read_bytes(), paths.site_paths.stat().st_mtime_ns)
    assert paths.path_values() == original
    assert not paths.CONFIG_PATH.exists()
    replacement = {key: str(tmp_path / 'chosen' / key) for key in paths.KEYS}
    paths.write_config(replacement)
    assert paths.read_config() == original
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(paths.CONFIG_PATH))
    assert paths.read_config() == replacement
    assert (paths.site_paths.read_bytes(), paths.site_paths.stat().st_mtime_ns) == before

def test_invalid_new_configuration_does_not_fall_back(paths, tmp_path, monkeypatch):
    """当前规范配置损坏时拒绝读取，不退回旧兼容文件。"""
    valid = configured_paths(paths, tmp_path)
    paths.CONFIG_PATH.parent.mkdir(parents=True)
    paths.CONFIG_PATH.write_text('{}')
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(paths.CONFIG_PATH))
    with pytest.raises(ValueError, match='Invalid'):
        paths.read_config()

def test_explicit_missing_override_never_loads_legacy_site(paths, tmp_path, monkeypatch):
    """显式环境路径不存在时不退回本站另一个配置文件。"""
    configured_paths(paths, tmp_path)
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(paths.CONFIG_PATH))
    assert paths.read_config() == {}

def test_fresh_install_paths_do_not_infer_lab_or_copied_instance_config(paths, tmp_path, monkeypatch):
    (paths.PORTAL_ROOT / 'config').mkdir(parents=True)
    old_instance = paths.PORTAL_ROOT / 'portal_instances'
    old_instance.mkdir()
    (paths.PORTAL_ROOT / 'config/portal_instances.json').write_text(json.dumps({'root': str(old_instance)}))
    monkeypatch.chdir(tmp_path)
    assert paths.path('share_root') == paths.PORTAL_ROOT / 'portal_data/share'
    assert paths.path('instances_root') == paths.PORTAL_ROOT / 'portal_data/instances'
    assert paths.path('staging_root') == paths.PORTAL_ROOT / 'portal_data/staging'
