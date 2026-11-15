"""Kimi account hosts survive application owners and start for quota reads.

:module: execution.providers
:importance: P1
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import kimi_account_runtime as runtime
from management_portal.kimi_code_client import KimiCodeUnavailable


@pytest.fixture
def host(tmp_path, monkeypatch):
    account = {'account_id': 'sample', 'route_id': 'sample-route', 'kind': 'subscription_login',
               'version': 1, 'config': {}}
    binary = tmp_path / 'kimi'
    binary.touch()
    monkeypatch.setenv('PORTAL_KIMI_CODE_BINARY', str(binary))
    monkeypatch.setattr(runtime, 'account_for_route', lambda route: account)
    monkeypatch.setattr(runtime, 'home', lambda row: tmp_path)
    monkeypatch.setattr(runtime, 'control', lambda row: tmp_path)
    monkeypatch.setattr(runtime, '_owned_process', lambda target, state: object() if state.get('pid') else None)
    client = SimpleNamespace(_request=Mock(return_value={'items': []}))
    monkeypatch.setattr(runtime, '_client', lambda *args: client)
    monkeypatch.setattr(runtime, '_subscription_tools', lambda row, native: native)
    from scripts import portal_service_units
    def launch(account_id, *, action):
        assert (account_id, action) == ('sample', 'start')
        runtime.record_service(account, binary, 12345)
        return {'port': 12345}
    service = Mock(side_effect=launch)
    monkeypatch.setattr(portal_service_units, 'kimi_account_service', service)
    state = {'pid': 123, 'port': 12345, 'version': [1, None],
             'runtime': str(binary), 'host_contract': 1}
    def marker(**changes):
        runtime.atomic_write_private_bytes(tmp_path / 'server.json', json.dumps({**state, **changes}).encode())
    return SimpleNamespace(account=account, service=service, client=client, marker=marker, root=tmp_path)


def test_quota_control_starts_missing_service_without_model_session(host):
    assert runtime.client_for_route('sample-route', for_control=True) is host.client
    host.service.assert_called_once_with('sample', action='start')
    host.client._request.assert_called_once_with('GET', 'sessions', timeout=1)


def test_two_callers_share_one_started_service(host):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = list(pool.map(lambda _: runtime.client_for_route('sample-route', for_control=True), range(2)))
    assert clients == [host.client, host.client]
    assert host.service.call_count == 1


def test_existing_supervised_host_is_reused(host):
    host.marker(supervised=True)
    assert runtime.client_for_route('sample-route', for_control=True) is host.client
    host.service.assert_not_called()


def test_live_legacy_turn_defers_migration(host, monkeypatch):
    host.marker()
    host.client._request.return_value = {'items': [{'id': 'existing', 'busy': True}]}
    stop = Mock()
    monkeypatch.setattr(runtime, '_stop_server', stop)
    assert runtime.client_for_route('sample-route', for_control=True) is host.client
    stop.assert_not_called()
    host.service.assert_not_called()


def test_unresponsive_existing_host_is_never_replaced(host, monkeypatch):
    host.marker()
    host.client._request.side_effect = KimiCodeUnavailable('probe failed')
    stop = Mock()
    monkeypatch.setattr(runtime, '_stop_server', stop)
    with pytest.raises(KimiCodeUnavailable):
        runtime.client_for_route('sample-route', for_control=True)
    stop.assert_not_called()
    host.service.assert_not_called()


def test_idle_legacy_host_is_stopped_before_supervisor_start(host, monkeypatch):
    host.marker()
    order = []
    monkeypatch.setattr(runtime, '_stop_server', lambda *args: order.append('stop'))
    launch = host.service.side_effect
    def start(*args, **kwargs):
        order.append('start')
        return launch(*args, **kwargs)
    host.service.side_effect = start
    runtime.client_for_route('sample-route', for_control=True)
    assert order == ['stop', 'start']


def test_supervised_stop_uses_service_manager_not_process_kill(host, monkeypatch):
    import psutil
    process = Mock()
    process.children.return_value = []
    process.is_running.return_value = False
    monkeypatch.setattr(runtime, '_owned_process', lambda *args: process)
    monkeypatch.setattr(runtime, '_server_busy', lambda client: False)
    monkeypatch.setattr(psutil, 'wait_procs', lambda *args, **kwargs: ([], []))
    kill = Mock()
    monkeypatch.setattr(runtime, 'terminate_process_tree', kill)
    host.service.side_effect = None
    runtime._stop_server(host.account, host.root, {'pid': 123, 'port': 12345, 'supervised': True})
    host.service.assert_called_once_with('sample', action='stop')
    kill.assert_not_called()


def test_account_service_definition_and_command_are_independent(tmp_path, monkeypatch):
    from management_common import host_instances
    from management_common.service_components import is_builtin
    from management_portal import service_runtime
    from scripts.instance_service_definitions import dependencies
    from scripts.tfi_release_platform_linux import manifest_unit_text
    from tests.cases.provider_service_test_support import provider_instance
    root, _ = provider_instance(monkeypatch, tmp_path)
    component = 'kimi-account@sample'
    instance = host_instances.add_service_component(root, component, listener_bind='127.0.0.1')
    monkeypatch.setattr(runtime, 'account_for_route', lambda _: {'config': {'account_dir': str(tmp_path / 'home')}})
    monkeypatch.setattr('management_common.provider_account_paths.registered_home', lambda _: tmp_path / 'home')
    command, env = service_runtime.component_command(instance, component)
    assert is_builtin(component) and not is_builtin('kimi-account@../foreign')
    assert command[1:3] == ['-m', 'management_portal.kimi_code_server']
    assert command[-1] == '--supervised'
    assert command[command.index('--port') + 1] == str(instance.listener(component)['port'])
    assert env['KIMI_CODE_HOME'] == str(tmp_path / 'home')
    assert set(dependencies(instance, component)) <= {'postgres'}
    text = manifest_unit_text(instance, component)
    assert 'Restart=always\n' in text
    assert 'PartOf=' not in text and 'execution.service' not in text and 'society.service' not in text
    import plistlib
    from scripts.tfi_release_platform_macos import manifest_unit_text as macos_unit_text
    assert plistlib.loads(macos_unit_text(instance, component).encode())['KeepAlive'] is True


def test_service_lifecycle_uses_registered_owner_and_stable_listener(tmp_path, monkeypatch):
    from management_common.instance_manifest import load
    from scripts import instance_services, portal_service_units as units, tfi_release_platform_linux as linux
    from tests.cases.provider_service_test_support import FakeControl, provider_instance
    root, _ = provider_instance(monkeypatch, tmp_path)
    directory = tmp_path / 'units'
    control = FakeControl(directory)
    monkeypatch.setattr(instance_services, 'definition_directory', lambda _: directory)
    monkeypatch.setattr(instance_services, 'controller', lambda _: control)
    def register(directory, definitions):
        directory.mkdir(exist_ok=True)
        for name, definition in definitions.items():
            (directory / name).write_text(definition)
        return list(definitions)
    monkeypatch.setattr(linux, 'register_services', register)
    def run(*args):
        assert args[:4] == ('systemctl', '--user', 'enable', '--now')
        control.start(args[-1])
    monkeypatch.setattr(units, '_run', run)
    state = units.kimi_account_service('sample', action='start')
    assert state['active'] == 'active'
    assert units.kimi_account_service('sample', action='start')['port'] == state['port']
    instance = load(root)
    name = instance_services.unit_name(instance, 'kimi-account@sample')
    assert set(control.active) == {name}
    units.kimi_account_service('sample', action='stop')
    assert not control.active
    assert 'kimi-account@sample' in load(root).document['services']['components']
