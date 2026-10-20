"""The bundled resource-ID CLI accepts canonical environment names only.

Use the standard pytest entry; these cases do not call deployed services.
Only ISA transport is replaced; the CLI's identity validator remains real.
"""
from __future__ import annotations

import builtins
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
    'PORTAL_ROOT', 'ARCHIVE_PORTAL_ROOT',
    'PORTAL_PYTHON', 'ARCHIVE_PORTAL_PYTHON',
    'PORTAL_RESIDENT_ID', 'ARCHIVE_RESIDENT_ID',
    'PORTAL_RESOURCE_ID_ACTOR', 'ARCHIVE_RESOURCE_ID_ACTOR',
    'PORTAL_RESOURCE_ID_REEXEC', 'ARCHIVE_RESOURCE_ID_REEXEC',
)


@pytest.fixture
def cli(monkeypatch, tmp_path):
    for key in ENV_NAMES:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('PORTAL_ROOT', str(REPO))
    paths = tmp_path / 'paths.json'
    paths.write_text(json.dumps({key: str(tmp_path / key) for key in
        ('share_root', 'staging_root', 'instances_root', 'accounts_root', 'secrets_dir')}))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(paths))
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, 'CONFIG_PATH', paths)
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


@pytest.mark.parametrize('mode', ['new', 'both', 'empty-new', 'alias-only'])
def test_resource_id_bootstraps_helper_from_selected_root_without_env_write(tmp_path, mode):
    root = tmp_path / 'portal source'
    common = root / 'management_common'
    common.mkdir(parents=True)
    (common / '__init__.py').write_text('')
    for name in ('product_environment.py', 'site_compatibility.py', 'site_config.py', 'portal_paths.py'):
        shutil.copyfile(REPO / 'management_common' / name, common / name)
    env = site_environ({'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    if mode in ('new', 'both'):
        env['PORTAL_ROOT'] = str(root)
        env['PORTAL_PYTHON'] = str(root / 'new-python')
    if mode in ('alias-only', 'both', 'empty-new'):
        env['ARCHIVE_PORTAL_ROOT'] = str(root if mode == 'alias-only' else tmp_path / 'wrong-root')
        env['ARCHIVE_PORTAL_PYTHON'] = str(root / 'old-python')
    if mode == 'empty-new':
        env['PORTAL_ROOT'] = ''
        env['PORTAL_PYTHON'] = ''
    probe = '''import json, os, runpy, sys
before = dict(os.environ)
tool = runpy.run_path(sys.argv[1], run_name="resource_id_probe")
tool["_configure_portal"]()
tool = tool["_configure_portal"].__globals__
helper = sys.modules.get("management_common.product_environment")
print(json.dumps({"root": str(tool["DEFAULT_PORTAL_ROOT"]), "python": str(tool["PORTAL_PYTHON"]),
                  "helper": getattr(helper, "__file__", None), "unchanged": before == dict(os.environ)}))
'''
    detached = tmp_path / 'detached' / 'resource_id.py'
    detached.parent.mkdir()
    shutil.copyfile(SCRIPT, detached)
    completed = subprocess.run([sys.executable, '-c', probe, str(detached)],
                               cwd=detached.parent if mode == 'alias-only' else root, env=env,
                               capture_output=True, text=True)
    if mode == 'alias-only':
        assert completed.returncode != 0
        assert 'set PORTAL_ROOT explicitly' in completed.stderr
        return
    assert completed.returncode == 0, completed.stderr
    found = json.loads(completed.stdout)
    assert Path(found['helper']).resolve() == (common / 'product_environment.py').resolve()
    assert found['root'] == str(root)
    assert found['python'] == ('.' if mode == 'empty-new' else str(root / 'new-python'))
    assert found['unchanged']


@pytest.mark.parametrize('env,reexec', [
    ({}, True),
    ({'ARCHIVE_RESOURCE_ID_REEXEC': '1'}, True),
    ({'PORTAL_RESOURCE_ID_REEXEC': '1'}, False),
    ({'PORTAL_RESOURCE_ID_REEXEC': '', 'ARCHIVE_RESOURCE_ID_REEXEC': '1'}, True),
    ({'PORTAL_RESOURCE_ID_REEXEC': '1', 'ARCHIVE_RESOURCE_ID_REEXEC': ''}, False),
])
def test_resource_id_reexec_reads_and_emits_canonical_flag(cli, monkeypatch, tmp_path, env, reexec):
    python = tmp_path / 'python'
    python.touch()
    monkeypatch.setattr(cli, 'PORTAL_PYTHON', python)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    original_import = builtins.__import__

    def without_psycopg(name, *args, **kwargs):
        if name == 'psycopg':
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    exec_calls = []
    monkeypatch.setattr(cli.os, 'execv', lambda file, argv: exec_calls.append((file, argv)))
    monkeypatch.setattr(cli.sys, 'argv', [str(SCRIPT), '--actor', 'user', 'list-types'])
    before = env_snapshot()
    with monkeypatch.context() as imports:
        imports.setattr(builtins, '__import__', without_psycopg)
        cli._use_portal_python()
    if reexec:
        assert exec_calls == [(str(python), [str(python), str(SCRIPT), '--actor', 'user', 'list-types'])]
        assert env_snapshot() == {**before, 'PORTAL_RESOURCE_ID_REEXEC': '1'}
    else:
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


def test_site_paths_remain_available_without_alias_validation(cli, monkeypatch, tmp_path):
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'site_name': 'Isolated site', 'deployment': {'compatibility': {
        'environment_aliases': {'PORTAL_ROOT': 'obsolete-invalid-alias-list'},
        'domain_values': {'fixture_value': 'retained'},
    }}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))
    monkeypatch.setenv('ARCHIVE_PORTAL_ROOT', str(tmp_path / 'ignored-root'))
    cli._configure_portal()
    from management_common.site_config import site_name
    from management_common.site_compatibility import compatibility_value
    assert site_name() == 'Isolated site'
    assert compatibility_value('fixture_value') == 'retained'
    assert cli.DEFAULT_PORTAL_ROOT == REPO
    assert cli.DEFAULT_PROJECTS_ROOT == tmp_path / 'share_root' / 'Projects'


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
    assert env['PORTAL_ROOT'] == str(REPO)
    assert env['PORTAL_PYTHON'] == sys.executable


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
