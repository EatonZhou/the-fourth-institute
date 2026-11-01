"""The bundled resource-ID CLI accepts canonical environment names only.

Use the standard pytest entry; these cases do not call deployed services.
Only ISA transport is replaced; the CLI's identity validator remains real.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'core_skills/tfi-unified-id/scripts/resource_id.py'
RESIDENT = 'resident_123456789abc'
OTHER = 'resident_abcdef123456'
ENV_NAMES = (
    'PORTAL_RESIDENT_ID', 'ARCHIVE_RESIDENT_ID',
    'PORTAL_RESOURCE_ID_ACTOR', 'ARCHIVE_RESOURCE_ID_ACTOR',
)


@pytest.fixture
def cli(monkeypatch, tmp_path):
    for key in ENV_NAMES:
        monkeypatch.delenv(key, raising=False)
    from tests.cases.instance_test_support import write_instance
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(write_instance(tmp_path / 'instance')))
    monkeypatch.syspath_prepend(str(REPO))
    spec = importlib.util.spec_from_file_location('resource_id_env_case', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._configure_portal()
    return module


@pytest.fixture
def calls(cli, monkeypatch):
    from scripts import resident_hatg_instruction as resident_cli
    import management_portal

    found = {'resident': [], 'human': []}
    catalog = {
        'registry': {
            'code_pattern': cli.CODE_PATTERN, 'type_pattern': cli.TYPE_PATTERN,
            'id_exceptions': {'user': {'format': 'username'}},
        },
        'types': [{'type': 'repo'}, {'type': 'user'}],
    }

    def execute(identity, request):
        found['resident'].append((identity, request))
        return {'result': catalog}

    def human_call(actor, name, arguments, *, entry):
        found['human'].append((actor, name, arguments, entry))
        return catalog

    human = ModuleType('management_portal.isa_client')
    human.call = human_call
    monkeypatch.setattr(resident_cli, 'execute', execute)
    monkeypatch.setitem(sys.modules, human.__name__, human)
    monkeypatch.setattr(management_portal, 'isa_client', human, raising=False)
    return found


def env_snapshot():
    return {key: os.environ[key] for key in ENV_NAMES if key in os.environ}


@pytest.mark.parametrize('env,requested', [
    ({'PORTAL_RESIDENT_ID': RESIDENT}, None),
    ({'PORTAL_RESIDENT_ID': RESIDENT, 'ARCHIVE_RESIDENT_ID': OTHER}, None),
    ({'PORTAL_RESIDENT_ID': '  ' + RESIDENT + ' ', 'ARCHIVE_RESIDENT_ID': OTHER}, RESIDENT),
    ({}, RESIDENT),
])
def test_resource_id_canonical_identity_keeps_resident_transport(cli, calls, monkeypatch, env, requested):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    before = env_snapshot()
    kwargs = {} if requested is None else {'resident_id': requested}
    result = cli.load_registry(actor='human-must-not-be-used', **kwargs)
    assert result['types'] == {'repo': {'type': 'repo'}}
    assert calls['resident'] == [(RESIDENT, {'instruction': 'ListResourceTypes', 'arguments': {}})]
    assert calls['human'] == []
    assert env_snapshot() == before


@pytest.mark.parametrize('env,requested', [
    ({'PORTAL_RESIDENT_ID': OTHER}, RESIDENT),
    ({'PORTAL_RESIDENT_ID': RESIDENT}, OTHER),
    ({'PORTAL_RESIDENT_ID': ''}, None),
    ({'PORTAL_RESIDENT_ID': '  '}, RESIDENT),
    ({'PORTAL_RESIDENT_ID': '', 'ARCHIVE_RESIDENT_ID': RESIDENT}, None),
    ({'PORTAL_RESIDENT_ID': RESIDENT}, ''),
    ({'PORTAL_RESIDENT_ID': RESIDENT}, '  '),
    ({'PORTAL_RESIDENT_ID': 'invalid-identity'}, None),
    ({}, '  '),
    ({}, ''),
    ({}, 'invalid-identity'),
])
def test_resource_id_invalid_identity_never_falls_back_to_human(cli, calls, monkeypatch, env, requested):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    before = env_snapshot()
    kwargs = {} if requested is None else {'resident_id': requested}
    with pytest.raises((ValueError, RuntimeError)) as error:
        cli.load_registry(actor='human-must-not-be-used', **kwargs)
    assert calls == {'resident': [], 'human': []}
    assert env_snapshot() == before
    assert RESIDENT not in str(error.value) and OTHER not in str(error.value)
    assert 'invalid-identity' not in str(error.value)


@pytest.mark.parametrize('env,actor,expected', [
    ({}, 'explicit-user', 'explicit-user'),
    ({'PORTAL_RESOURCE_ID_ACTOR': 'canonical-user'}, '', 'canonical-user'),
    ({'ARCHIVE_RESOURCE_ID_ACTOR': 'ignored-user'}, '', None),
    ({'ARCHIVE_RESIDENT_ID': RESIDENT}, '', None),
    ({'ARCHIVE_RESIDENT_ID': RESIDENT}, 'explicit-user', 'explicit-user'),
    ({'PORTAL_RESOURCE_ID_ACTOR': 'new-user', 'ARCHIVE_RESOURCE_ID_ACTOR': 'old-user'}, '', 'new-user'),
    ({'PORTAL_RESOURCE_ID_ACTOR': '', 'ARCHIVE_RESOURCE_ID_ACTOR': 'old-user'}, '', None),
    ({'PORTAL_RESOURCE_ID_ACTOR': 'new-user'}, 'explicit-user', 'explicit-user'),
])
def test_resource_id_human_path_requires_absent_resident_identity(cli, calls, monkeypatch, env, actor, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    before = env_snapshot()
    if expected is None:
        with pytest.raises(RuntimeError):
            cli.load_registry(actor=actor)
        assert calls['human'] == []
    else:
        cli.load_registry(actor=actor)
        assert calls['human'] == [(expected, 'ListResourceTypes', {}, 'cli')]
    assert calls['resident'] == []
    assert env_snapshot() == before


def test_resource_id_needs_the_code_directory_it_is_installed_in(tmp_path):
    """工具只从自己所在的代码目录取得 Portal 模块；脱离代码目录的副本拒绝运行，且不改动环境。"""
    detached = tmp_path / 'detached' / 'resource_id.py'
    detached.parent.mkdir()
    shutil.copyfile(SCRIPT, detached)
    probe = '''import os, runpy, sys
before = dict(os.environ)
tool = runpy.run_path(sys.argv[1], run_name="resource_id_probe")
try:
    tool["_configure_portal"]()
except RuntimeError as error:
    print(error)
assert before == dict(os.environ)
'''
    env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'PORTAL_INSTANCE_ROOT': os.environ['PORTAL_INSTANCE_ROOT']}
    completed = subprocess.run([sys.executable, '-c', probe, str(detached)], cwd=tmp_path, env=env,
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert 'installed with its Portal code directory' in completed.stdout


def test_resource_id_reexecs_into_the_code_environment_only_when_needed(cli, monkeypatch, tmp_path):
    python = tmp_path / 'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(cli, 'PORTAL_PYTHON', python)
    exec_calls = []
    monkeypatch.setattr(cli.os, 'execv', lambda file, argv: exec_calls.append((file, argv)))
    monkeypatch.setattr(cli.sys, 'argv', [str(SCRIPT), '--actor', 'user', 'list-types'])
    before = env_snapshot()
    cli._use_portal_python()
    assert exec_calls == [(str(python), [str(python), str(SCRIPT), '--actor', 'user', 'list-types'])]
    exec_calls.clear()
    monkeypatch.setattr(cli.sys, 'prefix', str(python.parent.parent))
    cli._use_portal_python()
    assert exec_calls == []
    assert env_snapshot() == before


@pytest.mark.parametrize('resident_arguments,expected', [([], 0), (['--resident-id', ''], 2)])
def test_resource_id_original_cli_validation_and_explicit_empty_identity(cli, calls, monkeypatch, capsys,
                                                                      resident_arguments, expected):
    monkeypatch.setattr(cli, '_use_portal_python', lambda: None)
    monkeypatch.setattr(cli.sys, 'argv', [str(SCRIPT), '--actor', 'human-user', *resident_arguments,
                                         'validate', 'repo_123456789abc'])
    assert cli.main() == expected
    found = json.loads(capsys.readouterr().out)
    assert ('error' in found) == (expected != 0)
    if expected:
        assert calls == {'resident': [], 'human': []}
    else:
        assert calls['human'] == [('human-user', 'ListResourceTypes', {}, 'cli')]


def test_standalone_help_without_checkout_or_dependencies(tmp_path):
    script = tmp_path / 'standalone path' / 'resource_id.py'
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    env = {'PATH': os.defpath}
    completed = subprocess.run([sys.executable, '-S', str(script), '--help'],
                               cwd=script.parent, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert 'TFI' in completed.stdout
    assert str(REPO) not in completed.stdout + completed.stderr


def test_default_roots_come_from_instance_areas_and_code(cli, tmp_path):
    instance = tmp_path / 'instance'
    assert cli.DEFAULT_PORTAL_ROOT == REPO
    assert cli.DEFAULT_PROJECTS_ROOT == instance / 'data' / 'projects'
    assert cli.DEFAULT_PAPER_ROOT == instance / 'data' / 'papers'
    assert cli.DEFAULT_SKILL_ROOTS == (REPO / 'core_skills', instance / 'data' / 'skills')


def test_scan_fixture_clears_only_canonical_resident(monkeypatch):
    from tests.cases.test_unified_id_script import _script_env
    from management_portal.execution import service_settings
    monkeypatch.setattr(service_settings, 'ExecutionSettings',
                        lambda: SimpleNamespace(update_permission_system=lambda *args: None))
    monkeypatch.setenv('PORTAL_RESIDENT_ID', RESIDENT)
    monkeypatch.setenv('ARCHIVE_RESIDENT_ID', OTHER)
    env = _script_env()
    assert 'PORTAL_RESIDENT_ID' not in env
    assert env['ARCHIVE_RESIDENT_ID'] == OTHER
