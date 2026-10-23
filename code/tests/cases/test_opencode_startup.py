"""OpenCode local startup errors retain a safe category across Execution.

:module: execution.providers
:importance: P1
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import opencode_client as client


@pytest.mark.parametrize('failure,code', [
    ('missing', 'provider_runtime_missing'), ('launch', 'provider_runtime_launch_failed'),
    ('exit', 'provider_runtime_startup_failed'), ('timeout', 'provider_runtime_startup_timeout')])
def test_startup_failure_keeps_category_and_reaps_a_started_bridge(tmp_path, monkeypatch, failure, code):
    monkeypatch.setattr(client, 'STATE', tmp_path / 'state')
    runner = client.OpenCodeClient()
    transport = SimpleNamespace(socket_path=tmp_path / 'native.sock',
        _request=Mock(side_effect=client.NativeAgentUnavailable('not listening')))
    monkeypatch.setattr(runner, '_transport', lambda _: transport)
    binary = tmp_path / 'opencode'
    if failure != 'missing':
        binary.touch()
    for name, value in {'binary': binary, 'api_key': '', 'service': 'fixture', 'provider_config': {},
                        'config_home': tmp_path / 'config', 'version': 'test'}.items():
        monkeypatch.setattr(client.opencode_provider_catalog, name, Mock(return_value=value))
    monkeypatch.setattr(client, 'resident_process_environment', lambda _: ({}, {}))
    process = SimpleNamespace(poll=lambda: 1)
    spawn = Mock(side_effect=OSError('private path and credential') if failure == 'launch' else None,
                 return_value=process)
    monkeypatch.setattr(client, 'spawn_process', spawn)
    cleanup = Mock()
    monkeypatch.setattr(client, 'terminate_process_tree', cleanup)
    if failure == 'timeout':
        monkeypatch.setattr(client.time, 'monotonic', Mock(side_effect=[0, 181]))
    with pytest.raises(client.OpenCodeStartupError) as error:
        runner._start('fixture', {'cwd': str(tmp_path), 'provider_profile': 'fixture'})
    assert error.value.error_code == code
    assert 'credential' not in str(error.value)
    if failure in {'exit', 'timeout'}:
        cleanup.assert_called_once_with(process, force=True)
    else:
        cleanup.assert_not_called()
