"""The Resident CLI reads only canonical PORTAL_* names and keeps explicit identity and file checks."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    for key in (
        'PORTAL_RESIDENT_ID', 'PORTAL_EXECUTION_ID', 'PORTAL_EXECUTION_CONTEXT_FILE',
        'PORTAL_URL', 'ARCHIVE_PORTAL_URL',
    ):
        monkeypatch.delenv(key, raising=False)
    root = Path(os.environ.get('TFI_CLI_CANDIDATE_ROOT', Path(__file__).resolve().parents[2]))

    def load(relative, name):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    context = load('management_common/execution_context_file.py', 'tfi_context_under_test')
    cli = load('scripts/resident_hatg_instruction.py', 'tfi_cli_under_test')
    monkeypatch.setattr(cli, 'execution_context', context.execution_context)
    monkeypatch.setattr(cli, '_token', lambda _: 'test-only-token')
    return context, cli, load


def test_identity_aliases_keep_explicit_actor_and_refuse_other_flag(modules, monkeypatch):
    """CLI 以 PORTAL_RESIDENT_ID 为调用身份，相同的 --resident-id 可以重复给出，不同的身份被拒绝且不回退到参数"""
    _, cli, _ = modules
    actor = 'resident_aaaaaaaaaaaa'
    monkeypatch.setenv('PORTAL_RESIDENT_ID', actor)
    assert cli._resident_id('') == actor
    assert cli._resident_id(actor) == actor
    with pytest.raises(RuntimeError):
        cli._resident_id('resident_bbbbbbbbbbbb')


def test_context_file_is_read_only_and_precedes_execution_environment(modules, monkeypatch, tmp_path):
    """执行上下文文件只读，并优先于 PORTAL_EXECUTION_ID 决定请求的执行 ID"""
    context, cli, _ = modules
    path = tmp_path / 'context.json'
    path.write_text(json.dumps({'execution_id': 'execution-from-file'}))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setenv('PORTAL_EXECUTION_CONTEXT_FILE', str(path))
    monkeypatch.setenv('PORTAL_EXECUTION_ID', 'execution-from-env')
    assert context.execution_context() == {'execution_id': 'execution-from-file'}
    request = cli._request('resident_aaaaaaaaaaaa', {}, accept='application/json')
    assert request.get_header('X-portal-execution-id') == 'execution-from-file'
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    monkeypatch.setenv('PORTAL_EXECUTION_ID', 'conflicting-execution')
    request = cli._request('resident_aaaaaaaaaaaa', {}, accept='application/json')
    assert request.get_header('X-portal-execution-id') == 'execution-from-file'


def test_execution_environment_without_file_remains_resident_request(modules, monkeypatch):
    """没有上下文文件时，请求带 PORTAL_EXECUTION_ID 的执行 ID 和 Resident 令牌"""
    _, cli, _ = modules
    monkeypatch.setenv('PORTAL_EXECUTION_ID', 'execution-original')
    request = cli._request('resident_aaaaaaaaaaaa', {}, accept='application/json')
    assert request.get_header('X-portal-execution-id') == 'execution-original'
    assert request.get_header('Authorization') == 'Bearer test-only-token'


def test_context_validation_and_conflicting_paths_are_not_weakened(modules, monkeypatch, tmp_path):
    context, _, _ = modules
    monkeypatch.setenv('PORTAL_EXECUTION_CONTEXT_FILE', 'relative.json')
    with pytest.raises(ValueError, match='absolute'):
        context.execution_context()
    path = tmp_path / 'context.json'
    path.write_text('{"execution_id":"x","other":"y"}')
    monkeypatch.setenv('PORTAL_EXECUTION_CONTEXT_FILE', str(path))
    with pytest.raises(ValueError, match='Invalid'):
        context.execution_context()
    path.write_text('x' * 4097)
    with pytest.raises(ValueError, match='too large'):
        context.execution_context()
    monkeypatch.setenv('PORTAL_EXECUTION_CONTEXT_FILE', str(tmp_path / 'other.json'))
    with pytest.raises(FileNotFoundError):
        context.execution_context()


def test_cli_endpoint_ignores_retired_environment(modules, monkeypatch, tmp_path):
    _, cli, load = modules
    monkeypatch.setattr(cli.portal_paths, 'CONFIG_PATH', tmp_path / 'uninstalled.json')
    monkeypatch.setenv('ARCHIVE_PORTAL_URL', 'https://legacy.invalid/')
    assert load('scripts/resident_hatg_instruction.py', 'old_endpoint').DEFAULT_PORTAL_URL == 'http://127.0.0.1:18086'
    monkeypatch.setenv('PORTAL_URL', 'https://new.invalid/')
    assert load('scripts/resident_hatg_instruction.py', 'new_endpoint').DEFAULT_PORTAL_URL == 'https://new.invalid'


def test_share_path_aliases_refresh_and_saved_config_precedence(modules, monkeypatch, tmp_path):
    """共享根目录每次读取都反映 PORTAL_SHARE_ROOT 的当前值；保存了路径配置后按配置返回，读取不改写配置文件"""
    _, _, load = modules
    paths = load('management_common/portal_paths.py', 'tfi_paths_under_test')
    monkeypatch.setattr(paths, 'CONFIG_PATH', tmp_path / 'paths.json')
    monkeypatch.setenv('PORTAL_SHARE_ROOT', str(tmp_path / 'first'))
    assert paths.path('share_root') == tmp_path / 'first'
    monkeypatch.setenv('PORTAL_SHARE_ROOT', str(tmp_path / 'second'))
    assert paths.path('share_root') == tmp_path / 'second'
    saved = {key: str(tmp_path / key) for key in paths.KEYS}
    paths.CONFIG_PATH.write_text(json.dumps(saved))
    before = (paths.CONFIG_PATH.read_bytes(), paths.CONFIG_PATH.stat().st_mtime_ns)
    assert paths.path_values() == saved
    assert (paths.CONFIG_PATH.read_bytes(), paths.CONFIG_PATH.stat().st_mtime_ns) == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
