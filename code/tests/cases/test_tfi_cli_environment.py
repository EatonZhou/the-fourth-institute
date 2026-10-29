"""Resident CLI environment aliases preserve explicit identity and file checks."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    for key in (
        'PORTAL_RESIDENT_ID', 'PORTAL_RESIDENT_ID',
        'PORTAL_EXECUTION_ID', 'PORTAL_EXECUTION_ID',
        'PORTAL_EXECUTION_CONTEXT_FILE', 'PORTAL_EXECUTION_CONTEXT_FILE',
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


@pytest.mark.parametrize('key', ['PORTAL_RESIDENT_ID'])
def test_identity_aliases_keep_explicit_actor_and_refuse_other_flag(modules, monkeypatch, key):
    _, cli, _ = modules
    actor = 'resident_aaaaaaaaaaaa'
    monkeypatch.setenv(key, actor)
    assert cli._resident_id('') == actor
    assert cli._resident_id(actor) == actor
    with pytest.raises(RuntimeError):
        cli._resident_id('resident_bbbbbbbbbbbb')


def test_conflicting_identity_never_falls_back_to_flag(modules, monkeypatch):
    _, cli, _ = modules
    monkeypatch.setenv('PORTAL_RESIDENT_ID', 'resident_aaaaaaaaaaaa')
    with pytest.raises(RuntimeError):
        cli._resident_id('resident_bbbbbbbbbbbb')


@pytest.mark.parametrize('key', ['PORTAL_EXECUTION_CONTEXT_FILE'])
def test_context_file_is_read_only_and_precedes_execution_environment(modules, monkeypatch, tmp_path, key):
    context, cli, _ = modules
    path = tmp_path / 'context.json'
    path.write_text(json.dumps({'execution_id': 'execution-from-file'}))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setenv(key, str(path))
    monkeypatch.setenv('PORTAL_EXECUTION_ID', 'execution-from-env')
    assert context.execution_context() == {'execution_id': 'execution-from-file'}
    request = cli._request('resident_aaaaaaaaaaaa', {}, accept='application/json')
    assert request.get_header('X-portal-execution-id') == 'execution-from-file'
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    monkeypatch.setenv('PORTAL_EXECUTION_ID', 'conflicting-execution')
    request = cli._request('resident_aaaaaaaaaaaa', {}, accept='application/json')
    assert request.get_header('X-portal-execution-id') == 'execution-from-file'


@pytest.mark.parametrize('key', ['PORTAL_EXECUTION_ID'])
def test_execution_environment_without_file_remains_resident_request(modules, monkeypatch, key):
    _, cli, _ = modules
    monkeypatch.setenv(key, 'execution-original')
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
    _, _, load = modules
    paths = load('management_common/portal_paths.py', 'tfi_paths_under_test')
    monkeypatch.setattr(paths, 'CONFIG_PATH', tmp_path / 'paths.json')
    monkeypatch.delenv('PORTAL_SHARE_ROOT', raising=False)
    monkeypatch.delenv('PORTAL_SHARE_ROOT', raising=False)
    monkeypatch.setenv('PORTAL_SHARE_ROOT', str(tmp_path / 'old'))
    assert paths.path('share_root') == tmp_path / 'old'
    monkeypatch.setenv('PORTAL_SHARE_ROOT', str(tmp_path / 'new'))
    assert paths.path('share_root') == tmp_path / 'new'
    monkeypatch.setenv('PORTAL_SHARE_ROOT', str(tmp_path / 'next'))
    assert paths.path('share_root') == tmp_path / 'next'
    saved = {key: str(tmp_path / key) for key in paths.KEYS}
    paths.CONFIG_PATH.write_text(json.dumps(saved))
    before = (paths.CONFIG_PATH.read_bytes(), paths.CONFIG_PATH.stat().st_mtime_ns)
    assert paths.path_values() == saved
    assert (paths.CONFIG_PATH.read_bytes(), paths.CONFIG_PATH.stat().st_mtime_ns) == before


def test_catalog_projects_environment_alias_precedence(modules, monkeypatch):
    _, _, load = modules
    monkeypatch.delenv('PORTAL_PROJECTS_ROOT', raising=False)
    monkeypatch.setenv('PORTAL_PROJECTS_ROOT', '/legacy-projects')
    assert str(load('management_portal/test_framework/tools/sync_catalog.py', 'old_projects').PROJECTS_ROOT) == '/legacy-projects'
    monkeypatch.setenv('PORTAL_PROJECTS_ROOT', '/configured-projects')
    assert str(load('management_portal/test_framework/tools/sync_catalog.py', 'new_projects').PROJECTS_ROOT) == '/configured-projects'


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
