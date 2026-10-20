"""测试框架按运行用户与安装配置解析路径，保持历史数据和稳定编号。

:module: operations.test-framework
:profiles: local.fixture
:risk: no-external-side-effects
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from management_common import portal_paths
from management_portal.test_framework import catalog_source, executor, isolation, local_runs, paths
from scripts import portal_release


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / 'installed-portal'
    root.mkdir()
    home = tmp_path / 'operator'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(portal_paths, 'PORTAL_ROOT', root)
    monkeypatch.setattr(portal_paths, 'CONFIG_PATH', tmp_path / 'portal_paths.json')
    for key in [*portal_paths.ENV_KEYS.values(), 'PORTAL_SHARE_ROOT',
                'PORTAL_TEST_FRAMEWORK_STATE_ROOT', 'PORTAL_POSTGRES_ENV_FILE', 'PORTAL_INSTANCE',
                'PORTAL_RESOURCE_SERVICE_SOCKET', 'PORTAL_SOCIETY_SERVICE_SOCKET',
                'PORTAL_EXECUTION_SERVICE_SOCKET', 'PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return root, home


def test_framework_state_uses_running_user_and_keeps_explicit_history(installation, tmp_path, monkeypatch):
    """默认状态跟随运行用户，显式历史目录优先且不搬迁或改写。"""
    root, home = installation
    expected = home / '.local/state/tfi-portal/test-framework/dev'
    assert paths.state_root() == expected
    assert not expected.exists()
    monkeypatch.setenv('PORTAL_INSTANCE', 'release/site')
    assert paths.state_root() == expected.parent / 'release-site'
    history = tmp_path / 'historical-configured-state'
    history.mkdir()
    marker = history / 'runs.json'
    original = b'{"schema":"tfi.test-run-index.v1","runs":[]}\n'
    marker.write_bytes(original)
    monkeypatch.setenv('PORTAL_TEST_FRAMEWORK_STATE_ROOT', str(history))
    monkeypatch.setenv('PORTAL_AGENT_STAGING_ROOT', str(tmp_path / 'other-staging'))
    assert paths.state_root() == history
    assert marker.read_bytes() == original
    assert not expected.exists()
    monkeypatch.setenv('PORTAL_TEST_FRAMEWORK_STATE_ROOT', '~/existing-framework')
    assert paths.state_root() == home / 'existing-framework'
    monkeypatch.setenv('PORTAL_TEST_FRAMEWORK_STATE_ROOT', 'relative-existing')
    assert paths.state_root() == Path('relative-existing')


def test_executor_default_and_explicit_postgres_file_are_only_command_paths(installation, tmp_path, monkeypatch):
    """环境文件默认跟随用户，旧配置逐字保留，构造单元不读取数据库凭据。"""
    _, home = installation
    assert executor.postgres_environment_file() == str(home / '.config/tfi/portal-postgres.env')
    configured = tmp_path / 'existing-config/postgres.env'
    configured.parent.mkdir()
    original = b'PORTAL_DATABASE_URL=fixture-not-a-database\n'
    configured.write_bytes(original)
    monkeypatch.setenv('PORTAL_POSTGRES_ENV_FILE', str(configured))
    commands = []
    monkeypatch.setattr(executor.subprocess, 'run', lambda command, **kw:
                        commands.append(command) or subprocess.CompletedProcess(command, 0, '', ''))
    executor.launch_unit('testrun_0123456789abcdef', tmp_path / 'state', tau=True)
    executor.launch_unit('testrun_abcdef0123456789', tmp_path / 'state')
    assert f'--property=EnvironmentFile={configured}' in commands[0]
    assert '--user' in commands[0]
    assert not any('EnvironmentFile' in value for value in commands[1])
    assert 'fixture-not-a-database' not in ' '.join(commands[0] + commands[1])
    assert configured.read_bytes() == original


def test_isolation_uses_data_configuration_and_release_owner(installation, tmp_path, monkeypatch):
    """隔离掩码跟随安装数据目录与 Release 权威路径，保留通用系统目录。"""
    from management_portal.execution import provider_catalog
    root, home = installation
    monkeypatch.setattr(provider_catalog, 'AGENT_PROVIDERS', {})
    repository = tmp_path / 'dev-source'
    release = tmp_path / 'configured-release'
    repository.mkdir()
    release.mkdir()
    monkeypatch.setattr(isolation, 'REPOSITORY', repository)
    monkeypatch.setattr(portal_release, 'RELEASE_ROOT', release)
    # Environment is used only without the explicit data-directory config.
    env_staging = tmp_path / 'env-staging'
    monkeypatch.setenv('PORTAL_AGENT_STAGING_ROOT', str(env_staging))
    assert isolation.run_root() == env_staging / 'shared/script_runs'
    configured = {key: str(tmp_path / ('saved-' + key)) for key in portal_paths.KEYS}
    portal_paths.CONFIG_PATH.write_text(json.dumps(configured))
    saved_config = portal_paths.CONFIG_PATH.read_bytes()
    staging = Path(configured['staging_root'])
    instances = Path(configured['instances_root'])
    assert isolation.run_root() == staging / 'shared/script_runs'
    expected_dirs = [staging / 'portal_locks', staging / 'shared/portal_locks',
                     staging / 'shared/portal_runtime', Path(configured['accounts_root']),
                     Path(configured['secrets_dir'])]
    for p in expected_dirs:
        p.mkdir(parents=True)
    expected_files = [release / '.env', repository / '.env',
                      instances / 'dev/private.env', staging / 'portal_instances/dev/old.key']
    for p in expected_files:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'never-read-credential-content')
    actual = isolation.credential_paths(home)
    assert set(expected_dirs + expected_files) <= set(actual)
    assert not any(p.is_relative_to(env_staging) for p in actual)
    assert portal_paths.CONFIG_PATH.read_bytes() == saved_config
    assert all(p.read_bytes() == b'never-read-credential-content' for p in expected_files)
    generic = isolation.isolation_args(network=False)
    assert ['--proc', '/proc'] == generic[generic.index('--proc'):generic.index('--proc') + 2]
    assert '--unshare-net' in generic


def test_local_executor_inherits_only_explicit_directory_configuration(installation, tmp_path, monkeypatch):
    """独立本地执行器获得数据目录配置，不因脱离 Web 丢失隔离路径，也不带数据库密钥。"""
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(portal_paths.CONFIG_PATH))
    expected = {'PORTAL_PATHS_CONFIG': str(portal_paths.CONFIG_PATH)}
    for key, name in portal_paths.ENV_KEYS.items():
        expected[name] = str(tmp_path / key)
        monkeypatch.setenv(name, expected[name])
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'fixture-secret-url')
    monkeypatch.setenv('PORTAL_FIXTURE_TOKEN', 'fixture-secret-token')
    environment = executor.unit_environment(tmp_path / 'old-framework', tau=False)
    assert expected.items() <= environment.items()
    assert 'PORTAL_DATABASE_URL' not in environment
    assert 'PORTAL_FIXTURE_TOKEN' not in environment
    assert environment['PORTAL_TEST_FRAMEWORK_STATE_ROOT'] == str(tmp_path / 'old-framework')


def test_isolation_fresh_install_relative_data_and_dynamic_configuration(installation, tmp_path, monkeypatch):
    """新安装默认和相对配置以安装根为基准，配置变化在下次读取生效。"""
    root, _ = installation
    assert isolation.run_root() == root / 'portal_data/staging/shared/script_runs'
    monkeypatch.setenv('PORTAL_AGENT_STAGING_ROOT', 'relative-staging')
    assert isolation.run_root() == root / 'relative-staging/shared/script_runs'
    configured = {key: 'saved/' + key for key in portal_paths.KEYS}
    portal_paths.CONFIG_PATH.write_text(json.dumps(configured))
    assert isolation.run_root() == root / 'saved/staging_root/shared/script_runs'
    configured['staging_root'] = 'second/staging'
    portal_paths.CONFIG_PATH.write_text(json.dumps(configured))
    assert isolation.run_root() == root / 'second/staging/shared/script_runs'
    assert not (root / 'portal_data').exists()


def test_local_sandbox_protects_current_user_state_and_configured_runs(installation, tmp_path, monkeypatch):
    """本地沙盒保护当前用户状态、显式框架目录与配置中的脚本产物。"""
    _, home = installation
    framework = tmp_path / 'old-framework'
    framework.mkdir()
    original = b'historical-run-artifacts'
    (framework / 'evidence').write_bytes(original)
    monkeypatch.setenv('PORTAL_TEST_FRAMEWORK_STATE_ROOT', str(framework))
    staging = tmp_path / 'custom-staging'
    monkeypatch.setenv('PORTAL_AGENT_STAGING_ROOT', str(staging))
    runner = local_runs.PytestGroup({}, [], root=tmp_path / 'source')
    assert runner.protected_roots() == [home / '.local/state/tfi-portal', framework]
    seen = []
    monkeypatch.setattr(isolation, 'credential_paths', lambda: [])
    monkeypatch.setattr(isolation, 'mask_args', lambda targets: seen.extend(targets) or [])
    group_dir = tmp_path / 'new-artifacts/group'
    group_dir.mkdir(parents=True)
    command, scratch = runner.sandbox(group_dir)
    assert staging / 'shared/script_runs' in seen
    assert (framework / 'evidence').read_bytes() == original
    assert any(protected == framework for protected, _ in scratch)
    assert '--unshare-net' in command
    explicit = local_runs.PytestGroup({}, [], state_roots=[tmp_path / 'explicit'])
    assert explicit.protected_roots() == [tmp_path / 'explicit']


def test_architecture_design_reference_uses_installation_without_changing_ids():
    """架构条目默认规范引用跟随源码安装，既有编号与显式引用保留。"""
    source = catalog_source.SourceTest(
        'tests/cases/test_architecture_example.py::test_sample', 'test_sample', 'pytest',
        'tests/cases/test_architecture_example.py', 1,
        catalog_source.Doc(title='示例', fields=(('aspects', 'architecture-conformance'),)),
    )
    case, errors = catalog_source.derive_case(source, catalog_source.Doc(), 'test_0123456789ab', None)
    assert not errors
    assert case['design_refs'] == [str(Path(catalog_source.__file__).resolve().parents[2] / 'SPEC.md')]
    assert case['id'] == 'test_0123456789ab'
    assert case['implementations'][0]['id'] == catalog_source.implementation_id(source.entrypoint)
    explicit, errors = catalog_source.derive_case(
        source, catalog_source.Doc(fields=(('design', 'decision_8273271b67b1'),)),
        'test_0123456789ab', None,
    )
    assert not errors
    assert explicit['design_refs'] == ['decision_8273271b67b1']
    assert explicit['id'] == case['id']
    assert explicit['implementations'] == case['implementations']
