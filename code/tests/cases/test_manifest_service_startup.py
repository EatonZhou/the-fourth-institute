"""Manifest-owned service definitions and launch configuration."""
import json
import plistlib
from pathlib import Path
import subprocess

import pytest

from management_common import code_environment
from management_portal import service_runtime as instance_runtime
from management_common.instance_manifest import load
from scripts import tfi_release_platform_linux as linux, tfi_release_platform_macos as macos, tfi_release_platform_windows as windows
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance with spaces')
    value = json.loads((root / 'instance.json').read_text())
    value['deployments']['dev'] = {'kind': 'dev', 'code_root': str(tmp_path / 'dev/code'), 'web_port': 28087}
    value['network']['entry_port'] = 28088
    value['services']['components'] += ['dev-web', 'gateway']
    (root / 'instance.json').write_text(json.dumps(value))
    monkeypatch.setattr(code_environment, 'runtime_python', lambda code: code.parent / 'dependencies/exact/bin/python')
    monkeypatch.setattr(code_environment, 'native_binary', lambda code, package: code.parent / 'dependencies/native' / package)
    for platform in (linux, macos, windows):
        monkeypatch.setattr(platform, 'runtime_python', code_environment.runtime_python)
    return load(root)


@pytest.mark.parametrize('component,deployment', [('web', 'release'), ('dev-web', 'dev'), ('society', None)])
def test_unit_identity_code_and_environment_come_only_from_manifest(instance, component, deployment):
    code = instance.service_code(component)
    environment = {'PORTAL_INSTANCE_ROOT': str(instance.root)}
    if deployment:
        environment['PORTAL_DEPLOYMENT'] = deployment
    text = linux.manifest_unit_text(instance, component)
    assert str(code.parent / 'dependencies/exact/bin/python') in text
    assert 'HOME=' not in text
    assert [json.loads(line.partition('=')[2]) for line in text.splitlines() if line.startswith('Environment=')] == [f'{k}={v}' for k, v in environment.items()]
    job = plistlib.loads(macos.manifest_unit_text(instance, component).encode())
    assert job['EnvironmentVariables'] == environment
    assert job['WorkingDirectory'] == str(code)
    definition = json.loads(windows.manifest_unit_text(instance, component))
    assert definition['environment'] == environment
    assert definition['code'] == str(code)
    assert definition['name'] == instance.service_name(component)


def test_launch_uses_selected_code_python_and_manifest_port(instance):
    for component, deployment in [('web', 'release'), ('dev-web', 'dev')]:
        command, env = instance_runtime.component_command(instance, component)
        assert command[0] == str(instance.code_root(deployment).parent / 'dependencies/exact/bin/python')
        assert command[-1] == str(instance.deployments[deployment]['web_port'])
        assert env['PORTAL_DEPLOYMENT'] == deployment
        assert env['PORTAL_INSTANCE_ROOT'] == str(instance.root)


def test_service_environment_does_not_inherit_another_instance(instance, monkeypatch):
    monkeypatch.setenv('HOME', '/host/user')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', '/another/instance')
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'foreign-database')
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_SOCKET', '/foreign/socket')
    monkeypatch.setenv('XDG_CONFIG_HOME', '/foreign/config')
    env = instance_runtime.clean_environment(instance, component='society')
    assert env['HOME'] == '/host/user'
    assert env['PORTAL_INSTANCE_ROOT'] == str(instance.root)
    assert 'PORTAL_DATABASE_URL' not in env
    assert 'PORTAL_RESOURCE_SERVICE_SOCKET' not in env
    assert 'XDG_CONFIG_HOME' not in env
    assert env['PORTAL_SOCIETY_SERVICE_MODE'] == 'local'
    assert env['PORTAL_ISA_ROUTING'] == 'socket'


def test_postgres_command_uses_fixed_database_and_manifest_dependency(instance):
    command, env = instance_runtime.component_command(instance, 'postgres')
    assert command[0] == str(Path(instance.document['dependencies']['postgres']['bin']) / 'postgres')
    assert command[command.index('-D') + 1] == str(instance.root / 'database')
    assert command[command.index('-p') + 1] == str(instance.document['network']['postgres_port'])
    assert env['LD_LIBRARY_PATH'] == instance.document['dependencies']['postgres']['lib']


def test_private_environment_is_parsed_without_shell_execution(instance):
    secrets = instance.directory('secrets')
    secrets.mkdir()
    (secrets / 'postgres.env').write_text("export PORTAL_DATABASE_URL='literal $(do-not-execute)'\n")
    assert instance_runtime.secret_environment(instance, 'postgres.env') == {'PORTAL_DATABASE_URL': 'literal $(do-not-execute)'}
    (secrets / 'postgres.env').write_text('not-an-assignment')
    with pytest.raises(ValueError, match='Invalid secret environment entry'):
        instance_runtime.secret_environment(instance, 'postgres.env')


def test_missing_python_does_not_fall_back_to_installer_setting(instance, monkeypatch):
    def missing(code):
        raise FileNotFoundError('prepare selected code')
    monkeypatch.setattr(linux, 'runtime_python', missing)
    with pytest.raises(FileNotFoundError, match='prepare selected code'):
        linux.manifest_unit_text(instance, 'web')


def test_unregistered_component_cannot_be_generated(instance):
    with pytest.raises(ValueError, match='not registered'):
        linux.manifest_unit_text(instance, 'foreign')


def test_shared_sockets_ignore_web_deployment(instance, monkeypatch):
    from management_common.rpc_auth import rpc_signing_key_file
    from management_portal.resources.config import resource_service_socket
    from management_portal.society_service.config import society_service_socket
    from management_portal.execution.config import execution_service_socket
    from management_portal.execution.transport import ExecutionServiceClient
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(instance.root))
    for key in ('PORTAL_RESOURCE_SERVICE_SOCKET', 'PORTAL_SOCIETY_SERVICE_SOCKET',
                'PORTAL_EXECUTION_SERVICE_SOCKET', 'PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE'):
        monkeypatch.delenv(key, raising=False)
    for deployment in ('dev', 'release'):
        monkeypatch.setenv('PORTAL_DEPLOYMENT', deployment)
        assert resource_service_socket() == instance.root / 'runtime/s/resource-service.sock'
        assert society_service_socket() == instance.root / 'runtime/s/society-service.sock'
        assert execution_service_socket() == instance.root / 'runtime/s/execution-service.sock'
        assert rpc_signing_key_file() == instance.root / 'runtime/s/resource-rpc-signing.key'
        assert ExecutionServiceClient().socket_paths == (execution_service_socket(),)


def test_provider_launchers_use_code_owned_programs(instance, monkeypatch):
    from management_common.instance_manifest import validate
    document = json.loads(json.dumps(instance.document))
    document['services']['components'] += ['claude-code', 'codex-app-server@test_account']
    instance = validate(instance.root, document)
    providers = code_environment.provider_directory(instance)
    sdk = providers / 'claude-agent-sdk-current/bin/python'
    package = providers / 'claude-app-server-current/management_portal/claude_code_daemon.py'
    binary = providers / 'codex'
    for path in (sdk, package, binary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    launch = instance.root / 'runtime/provider-accounts/codex/test_account/launch.json'
    launch.parent.mkdir(parents=True)
    launch.write_text('{}')
    command, _ = instance_runtime.component_command(instance, 'claude-code')
    assert command == [str(sdk), '-m', 'management_portal.claude_code_daemon']
    command, _ = instance_runtime.component_command(instance, 'codex-app-server@test_account')
    assert command[-1] == str(launch)
    assert command[command.index('--binary') + 1] == str(binary)
    assert command[command.index('--socket') + 1] == str(instance.root / 'runtime/s/codex-test_account.sock')
    sdk.unlink()
    with pytest.raises(FileNotFoundError, match='Claude SDK'):
        instance_runtime.component_command(instance, 'claude-code')


def test_saved_proxy_policy_is_loaded_privately_at_startup(instance, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://unrelated-host-proxy')
    private = instance.root / 'secrets'
    private.mkdir()
    (private / 'network.env').write_text('HTTPS_PROXY=""\nHTTP_PROXY=""\nALL_PROXY=""\nNO_PROXY="localhost"\n')
    environment = instance_runtime.clean_environment(instance, component='worker')
    assert environment['HTTPS_PROXY'] == ''
    assert environment['NO_PROXY'] == 'localhost'
    assert 'network.env' not in linux.manifest_unit_text(instance, 'worker')


def test_windows_supervisor_rejects_stale_or_foreign_definition(instance):
    from scripts.tfi_windows_user_services import validate_definition
    value = json.loads(windows.manifest_unit_text(instance, 'dev-web'))
    assert validate_definition(value, value['name']) == value
    value['code'] = str(instance.code_root('release'))
    with pytest.raises(ValueError, match='does not match'):
        validate_definition(value, value['name'])


def test_resident_isa_origin_uses_shared_code_web_while_release_is_stopped(instance):
    environment = instance_runtime.clean_environment(instance, component='worker')
    assert environment['PORTAL_URL'] == f"http://127.0.0.1:{instance.deployments['dev']['web_port']}"
    assert environment['PORTAL_URL'] != f"http://127.0.0.1:{instance.document['network']['entry_port']}"


def test_host_catalog_lists_registered_instances_without_code_tree_scanning(instance, monkeypatch):
    from scripts import tfi_instance_manager as manager
    monkeypatch.setattr(manager.host_instances, 'read_registry', lambda: {'outside-code': str(instance.root)})
    monkeypatch.setattr(manager, 'inspect_instance', lambda name, current: {'name': name, 'current': current})
    assert manager.catalog(str(instance.root)) == {'instances': [{'name': 'outside-code', 'current': str(instance.root)}]}


def test_service_inventory_renders_every_registered_component(instance):
    from scripts.instance_service_definitions import render
    definitions = render(instance, 'linux')
    assert len(definitions) == len(instance.document['services']['components'])
    assert 'tfi-fixture-gateway.service' in definitions
    assert '--component' in definitions['tfi-fixture-gateway.service']


def test_functional_program_uses_code_environment_and_area_locations(instance, monkeypatch):
    from management_common.instance_manifest import validate
    from scripts.instance_service_definitions import missing_launchers
    document = json.loads(json.dumps(instance.document))
    document['services']['components'].append('example-worker')
    document['services']['programs'] = {'example-worker': {
        'entrypoint': 'lineages:example/script/main.py', 'arguments': ['serve', '--name', 'two words'],
        'working_directory': 'lineages:example/script'}}
    instance = validate(instance.root, document)
    entry = instance.resolve('lineages:example/script/main.py')
    entry.parent.mkdir(parents=True)
    entry.write_text('pass')
    monkeypatch.setenv('PYTHONPATH', '/foreign/code')
    command, env = instance_runtime.component_command(instance, 'example-worker')
    assert command == [str(code_environment.runtime_python(instance.code_root())), str(entry), 'serve', '--name', 'two words']
    assert env['PYTHONPATH'].split(__import__('os').pathsep) == [str(instance.code_root()), str(entry.parent)]
    assert instance_runtime.working_directory(instance, 'example-worker') == entry.parent
    assert not missing_launchers(instance)
    assert 'PYTHONPATH=' not in linux.manifest_unit_text(instance, 'example-worker')
    entry.unlink()
    with pytest.raises(FileNotFoundError, match='Registered service program'):
        instance_runtime.component_command(instance, 'example-worker')


@pytest.mark.parametrize('entrypoint', ['lineages:../escape.py', 'lineages:/absolute.py', '/relative/../escape'])
def test_functional_program_rejects_ambiguous_locations(instance, entrypoint):
    from management_common.instance_manifest import validate
    document = json.loads(json.dumps(instance.document))
    document['services']['components'].append('example-worker')
    document['services']['programs'] = {'example-worker': {
        'entrypoint': entrypoint, 'arguments': [], 'working_directory': 'lineages:'}}
    with pytest.raises(ValueError):
        validate(instance.root, document)


def test_functional_listeners_share_one_allocation_and_client_location(instance, monkeypatch):
    from management_common.instance_manifest import validate
    from management_common.host_instances import ports
    document = json.loads(json.dumps(instance.document))
    document['services']['components'] += ['kimi-code', 'knowledge-archive-agent', 'ai-socket-relay']
    document['network']['listeners'] = {
        'kimi-code': {'bind': '127.0.0.1', 'port': 29561},
        'knowledge-archive-agent': {'bind': '::1', 'port': 28767},
        'ai-socket-relay': {'bind': '0.0.0.0', 'port': 28484},
    }
    selected = validate(instance.root, document)
    (selected.root / 'instance.json').write_text(json.dumps(document))
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(selected.root))
    from management_portal.kimi_code_client import default_base_url
    from management_portal.knowledge_archive_backend import KnowledgeArchiveBackendClient
    from management_portal.ai_socket_relay import _build_arg_parser
    assert default_base_url() == 'http://127.0.0.1:29561/api/v1'
    assert KnowledgeArchiveBackendClient().base_url == 'http://[::1]:28767'
    assert _build_arg_parser().parse_args([]).port == 28484
    assert {29561, 28767, 28484} <= ports(selected)
    document['network']['listeners']['kimi-code']['port'] = document['network']['postgres_port']
    with pytest.raises(ValueError, match='distinct'):
        validate(instance.root, document)


def test_service_entry_does_not_import_installation_configuration(instance):
    import os
    import sys
    document = json.loads((instance.root / 'instance.json').read_text())
    for deployment in document['deployments'].values():
        deployment['code_root'] = str(Path(__file__).resolve().parents[2])
    (instance.root / 'instance.json').write_text(json.dumps(document))
    environment = {**os.environ, 'PORTAL_INSTANCE_ROOT': str(instance.root)}
    probe = '''
import sys
import management_portal.service_runtime as runtime
runtime.launch = lambda root, component: print(str(root), component)
sys.argv = ['instance_runtime', '--component', 'society']
runtime.main()
assert 'scripts.tfi_release_install' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', probe], env=environment,
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(instance.root) + ' society'
    linux_command = next(line for line in linux.manifest_unit_text(instance, 'society').splitlines()
                         if line.startswith('ExecStart='))
    assert 'management_portal.service_runtime' in linux_command
    assert 'tfi_release_install' not in linux_command
    mac_command = plistlib.loads(macos.manifest_unit_text(instance, 'society').encode())['ProgramArguments']
    assert mac_command[1:] == ['-m', 'management_portal.service_runtime', '--component', 'society']
    scm = json.loads(windows.manifest_unit_text(instance, 'society', mode='scm', account='example'))
    assert 'tfi_release_windows_service.py' in scm['binary']
    assert 'tfi_release_install' not in scm['binary']


def test_linux_definition_passes_native_parser_with_spaces_and_unicode(tmp_path, monkeypatch):
    import shutil
    import sys
    analyzer = shutil.which('systemd-analyze')
    if not sys.platform.startswith('linux') or not analyzer:
        pytest.skip('Requires the native systemd parser')
    code = tmp_path / '代码 space %' / 'code'
    code.mkdir(parents=True)
    root = write_instance(tmp_path / '实例 space %', code=code)
    selected = load(root)
    monkeypatch.setattr(linux, 'runtime_python', lambda code: Path(sys.executable))
    unit = tmp_path / (selected.service_name('postgres') + '.service')
    text = linux.manifest_unit_text(selected, 'postgres')
    unit.write_text(text)
    result = subprocess.run([analyzer, '--user', 'verify', '--man=no', str(unit)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'WorkingDirectory=' + str(code).replace('%', '%%') in text
    assert '实例 space %%' in text and '\\u' not in text
