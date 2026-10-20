"""Runtime owners preserve identity while migrating product environment names.

:module: execution.providers
:aspects: functional
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from management_portal.execution.backends import CodexBackend
from management_portal.society_service import resident_execution_work, resident_runtime_domain
from management_portal.society_service.script_runs import service as scripts
from scripts import verify_dsh_backend


RID = 'resident_111111111111'
LID = 'lineage_111111111111'
EID = 'execution-' + '1' * 24


@pytest.fixture(autouse=True)
def clean_identity_environment(monkeypatch):
    for prefix in ('PORTAL_', 'ARCHIVE_'):
        for suffix in ('RESIDENT_ID', 'LINEAGE_ID', 'EXECUTION_ID',
                       'RESIDENT_EXECUTION_ID', 'EXECUTION_CONTEXT_FILE'):
            monkeypatch.delenv(prefix + suffix, raising=False)


@pytest.mark.parametrize('prefixes', [('PORTAL_',)])
def test_self_stop_recognizes_exact_execution_with_either_environment(monkeypatch, prefixes):
    """新旧身份命名均保留自停止保护，其他执行仍不命中。"""
    for prefix in prefixes:
        monkeypatch.setenv(prefix + 'RESIDENT_ID', RID)
        monkeypatch.setenv(prefix + 'RESIDENT_EXECUTION_ID', EID)
    record = {'resident_id': RID, 'lineage_id': LID}
    assert resident_execution_work.is_self_stop_request(record, {'execution_id': EID})
    assert not resident_execution_work.is_self_stop_request(record, {'execution_id': 'other'})
    assert not resident_execution_work.is_self_stop_request({'resident_id': 'resident_222222222222'}, {'execution_id': EID})


@pytest.mark.parametrize('suffix', ['RESIDENT_ID', 'RESIDENT_EXECUTION_ID'])
def test_self_stop_uses_canonical_identity_and_ignores_old_fields(monkeypatch, suffix):
    monkeypatch.setenv('PORTAL_RESIDENT_ID', RID)
    monkeypatch.setenv('PORTAL_RESIDENT_EXECUTION_ID', EID)
    monkeypatch.setenv('ARCHIVE_' + suffix, 'other-identity')
    assert resident_execution_work.is_self_stop_request({'resident_id': RID}, {'execution_id': EID})
    monkeypatch.delenv('PORTAL_' + suffix)
    assert not resident_execution_work.is_self_stop_request({'resident_id': RID}, {'execution_id': EID})


def test_runtime_identity_is_record_owned_and_request_local(monkeypatch):
    """身份由当前记录确定，双注入不会读取调用者环境或污染下一请求。"""
    monkeypatch.setenv('PORTAL_RESIDENT_ID', 'inherited-wrong-owner')
    monkeypatch.setenv('PORTAL_RESIDENT_ID', 'different-inherited-owner')
    before = dict(os.environ)
    record = {'resident_id': RID, 'lineage_id': LID, 'backend': 'codex'}
    result = resident_runtime_domain._resident_cli_environment(record)
    assert result == {
        'PORTAL_RESIDENT_ID': RID,
        'PORTAL_LINEAGE_ID': LID,
    }
    result['PORTAL_RESIDENT_ID'] = 'locally-mutated'
    next_record = {**record, 'resident_id': 'resident_222222222222'}
    next_env = resident_runtime_domain._resident_cli_environment(next_record)
    assert next_env['PORTAL_RESIDENT_ID'] == next_record['resident_id']
    block = resident_runtime_domain._resident_runtime_identity_block(record)
    assert f'resident_id: {RID}' in block and f'lineage_id: {LID}' in block
    assert f'--resident-id {RID}' in block
    assert dict(os.environ) == before


@pytest.mark.parametrize('owner', [False, True])
def test_codex_turn_mirrors_context_and_drops_both_stale_execution_ids(monkeypatch, tmp_path, owner):
    """两种执行 ID 均清除，权威上下文路径双注入，数据库授权规则不变。"""
    monkeypatch.setenv('PORTAL_EXECUTION_STATE_ROOT', str(tmp_path/'execution'))
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only/not-a-real-endpoint')
    environment = {
        'PORTAL_EXECUTION_ID': 'stale',
        'PORTAL_EXECUTION_CONTEXT_FILE': '/stale',
        'LANG': 'C.UTF-8', 'PORTAL_RESIDENT_ID': RID,
    }
    request = SimpleNamespace(runtime_options={'config_overrides': {'shell_environment_policy.set': environment}},
        session=SimpleNamespace(binding_id='provider:fixture-session'), execution_id=EID,
        agent_id=RID, permission_profile='owner' if owner else 'sandbox',
        metadata={'business_type': 'resident', 'resident_id': RID, 'filesystem_scope': {'mode': 'A'}})
    original = copy.deepcopy(request.runtime_options)
    config = CodexBackend._runtime_thread_config(request)
    result = config['shell_environment_policy.set']
    expected_path = str(CodexBackend._execution_context_path(request))
    assert result['PORTAL_EXECUTION_CONTEXT_FILE'] == expected_path
    assert 'PORTAL_EXECUTION_ID' not in result
    assert result['LANG'] == 'C.UTF-8'
    assert result['PORTAL_RESIDENT_ID'] == RID
    assert ('PORTAL_DATABASE_URL' in result) is owner
    assert request.runtime_options == original


@pytest.mark.parametrize('values,expected', [
    ({}, 'http://127.0.0.1:18086'),
    ({'ARCHIVE_PORTAL_URL': 'http://legacy.invalid/'}, 'http://127.0.0.1:18086'),
    ({'PORTAL_URL': 'http://canonical.invalid/'}, 'http://canonical.invalid'),
    ({'PORTAL_URL': 'http://canonical.invalid/', 'ARCHIVE_PORTAL_URL': 'http://legacy.invalid/'}, 'http://canonical.invalid'),
    ({'PORTAL_URL': '', 'ARCHIVE_PORTAL_URL': 'http://legacy.invalid/'}, ''),
])
@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_script_run_persists_selected_url_without_starting_a_process(monkeypatch, tmp_path, values, expected, platform):
    """真实启动配置写入采用新 URL 优先；平台启动与凭据签发使用隔离替身。"""
    for key in ('PORTAL_URL', 'ARCHIVE_PORTAL_URL'):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(scripts.runtime, 'RUN_ROOT', tmp_path/'runs')
    monkeypatch.setattr(scripts.runtime, 'PRIVATE_ROOT', tmp_path/'private')
    monkeypatch.setattr(scripts.runtime, 'REPOSITORY', tmp_path)
    monkeypatch.setattr(scripts.sys, 'platform', platform)
    monkeypatch.setattr(scripts, '_issue_token', lambda *args: 'synthetic-test-token')
    launches = []
    def fake_run(*args, **kwargs):
        launches.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(scripts.process_runtime, 'run_process', fake_run)
    monkeypatch.setattr(scripts, 'launch_job', fake_run)
    record = {'script_run_id': 'script_run_fixture', 'arguments': {'keep': 'literal'},
              'caller_id': RID, 'lineage_id': LID, 'trigger_path_id': '', 'effect_id': '', 'unit': 'fixture-unit'}
    config = {'timeout_seconds': 11, 'argv': ['unchanged']}
    scripts.launch(record, config)
    private = tmp_path/'private/script_run_fixture.json'
    written = json.loads(private.read_text())
    assert written['portal_url'] == expected
    assert written['argv'] == config['argv'] and written['token'] == 'synthetic-test-token'
    assert private.stat().st_mode & 0o777 == 0o600
    assert config == {'timeout_seconds': 11, 'argv': ['unchanged']}
    assert len(launches) == 1
    if platform == 'darwin':
        assert launches[0][1]['inherit_environment'] is False
    else:
        assert '--property=RuntimeMaxSec=21' in launches[0][0][0]


def test_dsh_verification_injects_both_names_before_any_model_operation(monkeypatch, tmp_path):
    """验收脚本的真实参数装配双注入身份，在首次模型调用前终止。"""
    captured = []
    class NoModelOperation(Exception):
        pass
    class Client:
        def run_turn(self, **kwargs):
            captured.append(kwargs)
            raise NoModelOperation()
    monkeypatch.setattr(verify_dsh_backend, 'DshClient', Client)
    monkeypatch.setattr(sys, 'argv', ['verify_dsh_backend.py', '--cwd', str(tmp_path),
                                    '--resident-id', RID, '--lineage-id', LID])
    with pytest.raises(NoModelOperation):
        verify_dsh_backend.main()
    assert captured[0]['resident_environment'] == {
        'PORTAL_RESIDENT_ID': RID,
        'PORTAL_LINEAGE_ID': LID,
    }


def test_dsh_verification_help_imports_from_outside_repository(tmp_path):
    """仓库外 CLI --help 可导入共享 helper，且不会启动模型。"""
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(Path(verify_dsh_backend.__file__).resolve()), '--help'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '--resident-id' in result.stdout and '--lineage-id' in result.stdout


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
