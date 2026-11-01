"""测试框架的目录来自所选实例的清单，保持稳定编号。

:module: operations.test-framework
:profiles: local.fixture
:risk: no-external-side-effects
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from management_portal.test_framework import catalog_source, executor, isolation, local_runs, paths
from scripts import portal_release
from tests.cases.instance_test_support import select_instance


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.delenv('PORTAL_DEPLOYMENT', raising=False)
    monkeypatch.chdir(tmp_path)
    return select_instance(monkeypatch, tmp_path / 'selected').root


def test_framework_state_belongs_to_the_selected_instance_and_deployment(instance, tmp_path, monkeypatch):
    """运行记录与并发槽位在所选实例的 runtime 目录下，随部署分开，读取时不创建目录。"""
    expected = instance / 'runtime/test-framework/dev'
    assert paths.state_root() == expected
    assert paths.slot_root() == instance / 'runtime/test-slots'
    assert not (instance / 'runtime').exists()
    monkeypatch.setenv('PORTAL_DEPLOYMENT', 'release/site')
    assert paths.state_root() == expected.parent / 'release-site'
    other = select_instance(monkeypatch, tmp_path / 'other').root
    assert paths.state_root() == other / 'runtime/test-framework/release-site'
    assert paths.slot_root() == other / 'runtime/test-slots'


def test_executor_database_file_is_the_instance_secret_and_only_a_command_path(instance, tmp_path, monkeypatch):
    """环境文件取自实例的 secrets 目录；构造单元只写文件位置，不读取数据库凭据。"""
    configured = instance / 'secrets/postgres.env'
    assert executor.postgres_environment_file() == str(configured)
    configured.parent.mkdir()
    original = b'PORTAL_DATABASE_URL=fixture-not-a-database\n'
    configured.write_bytes(original)
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


def test_isolation_masks_the_instance_secrets_runtime_and_release_files(instance, tmp_path, monkeypatch):
    """隔离掩码覆盖所选实例的 secrets 与 runtime 目录和 Release 的环境文件，保留通用系统目录。"""
    from management_portal.execution import provider_catalog
    home = tmp_path / 'operator'
    home.mkdir()
    monkeypatch.setattr(provider_catalog, 'AGENT_PROVIDERS', {})
    repository = tmp_path / 'dev-source'
    release = tmp_path / 'configured-release'
    repository.mkdir()
    release.mkdir()
    monkeypatch.setattr(isolation, 'REPOSITORY', repository)
    monkeypatch.setattr(portal_release, 'RELEASE_ROOT', release)
    assert isolation.run_root() == instance / 'runtime/script_runs'
    expected_dirs = [instance / 'secrets', instance / 'runtime']
    for p in expected_dirs:
        p.mkdir(parents=True)
    expected_files = [release / '.env', repository / '.env']
    for p in expected_files:
        p.write_bytes(b'never-read-credential-content')
    actual = isolation.credential_paths(home)
    assert set(expected_dirs + expected_files) <= set(actual)
    assert all(p.read_bytes() == b'never-read-credential-content' for p in expected_files)
    generic = isolation.isolation_args(network=False)
    assert ['--proc', '/proc'] == generic[generic.index('--proc'):generic.index('--proc') + 2]
    assert '--unshare-net' in generic


def test_local_executor_inherits_only_the_instance_selection(instance, tmp_path, monkeypatch):
    """独立本地执行器只获得实例与部署的选择，不带数据库密钥，目录由清单给出。"""
    monkeypatch.setenv('PORTAL_DEPLOYMENT', 'release')
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'fixture-secret-url')
    monkeypatch.setenv('PORTAL_FIXTURE_TOKEN', 'fixture-secret-token')
    environment = executor.unit_environment(tau=False)
    assert environment['PORTAL_INSTANCE_ROOT'] == str(instance)
    assert environment['PORTAL_DEPLOYMENT'] == 'release'
    assert 'PORTAL_DATABASE_URL' not in environment
    assert 'PORTAL_FIXTURE_TOKEN' not in environment
    assert not any(key.endswith(('_ROOT', '_DIR')) and key != 'PORTAL_INSTANCE_ROOT' for key in environment)


def test_local_sandbox_protects_instance_state_and_runs(instance, tmp_path, monkeypatch):
    """本地沙盒保护所选实例的框架状态、并发槽位与脚本产物。"""
    framework = paths.state_root()
    framework.mkdir(parents=True)
    original = b'historical-run-artifacts'
    (framework / 'evidence').write_bytes(original)
    runner = local_runs.PytestGroup({}, [], root=tmp_path / 'source')
    assert runner.protected_roots() == [framework, paths.slot_root()]
    seen = []
    monkeypatch.setattr(isolation, 'credential_paths', lambda: [])
    monkeypatch.setattr(isolation, 'mask_args', lambda targets: seen.extend(targets) or [])
    group_dir = tmp_path / 'new-artifacts/group'
    group_dir.mkdir(parents=True)
    command, scratch = runner.sandbox(group_dir)
    assert instance / 'runtime/script_runs' in seen
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
