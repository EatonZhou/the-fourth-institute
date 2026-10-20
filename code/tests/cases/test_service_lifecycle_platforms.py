"""Lifecycle capability boundaries, without installed services or model calls."""
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution import service_lifecycle as lifecycle
from scripts.portal_service_control import ServiceControl


def platform(monkeypatch, value):
    # Do not change pytest's own sys.platform or import native Windows modules.
    monkeypatch.setattr(lifecycle, 'sys', SimpleNamespace(platform=value, executable=sys.executable))


def forbidden(*args, **kwargs):
    raise AssertionError('Platform attempted FD handover or process creation')


class Server:
    def __init__(self):
        self.socket = Mock()
        self.socket.fileno.return_value = 13
        self.preserve_socket = False
        self.started = threading.Event()
        self.finished = threading.Event()
        self.closed = False
        self.timing_proxy = None

    def serve_forever(self):
        self.started.set()
        assert self.finished.wait(5), 'test did not stop server'

    def shutdown(self):
        self.finished.set()

    def server_close(self):
        self.closed = True


@pytest.mark.parametrize('value', ['win32', 'darwin'])
def test_native_platform_refuses_in_process_handover_before_side_effects(monkeypatch, value):
    platform(monkeypatch, value)
    server = Server()
    owner = lifecycle.ServiceLifecycle(server)
    monkeypatch.setattr(lifecycle.socket, 'socketpair', forbidden)
    monkeypatch.setattr(lifecycle.process_runtime, 'spawn_process', forbidden)
    monkeypatch.setattr(lifecycle, 'notify', forbidden)
    with pytest.raises(NotImplementedError, match='ServiceControl.restart'):
        owner.replace()
    assert not server.preserve_socket
    assert not server.closed
    assert not owner.stop.is_set()
    assert owner.relay is None


@pytest.mark.parametrize('value', ['win32', 'darwin'])
def test_native_platform_rejects_inherited_fds_but_accepts_fresh_start(monkeypatch, value):
    platform(monkeypatch, value)
    monkeypatch.delenv(lifecycle.HANDOVER_ENV, raising=False)
    assert lifecycle.inherited_descriptors() == {}
    monkeypatch.setenv(lifecycle.HANDOVER_ENV, json.dumps({'rpc': 13, 'control': 14}))
    with pytest.raises(NotImplementedError, match='inherited descriptors'):
        lifecycle.inherited_descriptors()
    with pytest.raises(NotImplementedError, match='inherited descriptors'):
        lifecycle.ServiceLifecycle(Server(), {'rpc': 13})


@pytest.mark.parametrize('value', ['win32', 'darwin'])
def test_native_notify_ignores_stale_systemd_environment(monkeypatch, value):
    platform(monkeypatch, value)
    monkeypatch.setenv('NOTIFY_SOCKET', '@not-a-native-service-manager')
    monkeypatch.setattr(lifecycle.socket, 'socket', forbidden)
    lifecycle.notify('MAINPID=123')


@pytest.mark.parametrize('entry', ['read', 'init', 'notify'])
def test_unknown_platform_rejected_even_without_inherited_fds(monkeypatch, entry):
    platform(monkeypatch, 'freebsd')
    monkeypatch.delenv(lifecycle.HANDOVER_ENV, raising=False)
    with pytest.raises(NotImplementedError, match='Unsupported ExecutionService platform'):
        {'read': lifecycle.inherited_descriptors,
         'init': lambda: lifecycle.ServiceLifecycle(Server()),
         'notify': lambda: lifecycle.notify('READY=1')}[entry]()


@pytest.mark.parametrize('value', ['linux', 'darwin', 'win32'])
def test_shutdown_handlers_and_cleanup_follow_supported_signals(monkeypatch, value):
    platform(monkeypatch, value)
    runtime = lifecycle.process_runtime
    callbacks = {}
    restored = []
    signal = SimpleNamespace(SIGINT=2, SIGTERM=15)
    if value == 'win32':
        signal.SIGBREAK = 21
    else:
        signal.SIGHUP = 1

    def install(signum, callback):
        if callback == 'previous':
            restored.append(signum)
        else:
            callbacks[signum] = callback
        return 'previous'

    signal.signal = install
    monkeypatch.setattr(runtime, 'signal', signal)
    monkeypatch.setattr(runtime, '_backend', lambda: None)
    server = Server()
    owner = lifecycle.ServiceLifecycle(server)

    def run():
        assert set(callbacks) == ({2, 15, 1} if value == 'linux'
                                 else {2, 15, 21} if value == 'win32' else {2, 15})
        callbacks[signal.SIGBREAK if value == 'win32' else signal.SIGTERM](None, None)
        return original()

    original = owner._run
    monkeypatch.setattr(owner, '_run', run)
    assert owner.run() == 0
    assert owner.stop.is_set()
    assert server.finished.is_set() and server.closed
    assert set(restored) == set(callbacks)


@pytest.mark.parametrize('value', ['linux', 'darwin', 'win32'])
def test_failed_reload_keeps_current_generation_until_explicit_stop(monkeypatch, value):
    platform(monkeypatch, value)
    server = Server()
    owner = lifecycle.ServiceLifecycle(server)
    failed = threading.Event()
    previous = owner.replace

    def replace():
        try:
            if value == 'linux':
                raise RuntimeError('successor rejected')
            previous()
        finally:
            failed.set()

    monkeypatch.setattr(owner, 'replace', replace)
    monkeypatch.setattr(lifecycle.socket, 'socketpair', forbidden)
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(owner._run()), daemon=True)
    thread.start()
    try:
        assert server.started.wait(2)
        owner.reload.set()
        assert failed.wait(2)
        assert thread.is_alive() and not server.closed
        assert not server.preserve_socket and owner.relay is None
    finally:
        owner.stop.set()
        thread.join(3)
    assert not thread.is_alive()
    assert outcome == [0] and server.closed


@pytest.mark.parametrize('value', ['linux', 'darwin', 'win32'])
def test_start_failure_closes_server_and_restores_handlers(monkeypatch, value):
    platform(monkeypatch, value)
    server = Server()
    server.serve_forever = Mock(side_effect=RuntimeError('startup failed'))
    owner = lifecycle.ServiceLifecycle(server)
    restore = Mock()
    monkeypatch.setattr(lifecycle.process_runtime, 'install_shutdown_handlers', lambda *a, **k: 'saved')
    monkeypatch.setattr(lifecycle.process_runtime, 'restore_shutdown_handlers', restore)
    with pytest.raises(RuntimeError, match='startup failed'):
        owner.run()
    assert server.closed
    restore.assert_called_once_with('saved')


@pytest.mark.parametrize('ready', [True, False])
def test_linux_handover_preserves_listener_protocol_and_failure_cleanup(monkeypatch, ready):
    platform(monkeypatch, 'linux')
    inherited = {'rpc': 13, 'control': 14, 'proxy': {'http': 15}}
    monkeypatch.setenv(lifecycle.HANDOVER_ENV, json.dumps(inherited))
    assert lifecycle.inherited_descriptors() == inherited
    server = Server()
    server.timing_proxy = Mock()
    server.timing_proxy.handover_descriptors.return_value = inherited['proxy']
    owner = lifecycle.ServiceLifecycle(server, command=['fake-successor'])
    parent, child = Mock(), Mock()
    child.fileno.return_value = 14
    parent.recv.return_value = b'R' if ready else b''
    monkeypatch.setattr(lifecycle.socket, 'socketpair', lambda: (parent, child))
    successor = SimpleNamespace(pid=111)
    spawn, cleanup, notify, relay = Mock(return_value=successor), Mock(), Mock(), Mock()
    monkeypatch.setattr(lifecycle.process_runtime, 'spawn_process', spawn)
    monkeypatch.setattr(lifecycle.process_runtime, 'terminate_process_tree', cleanup)
    monkeypatch.setattr(lifecycle, 'notify', notify)
    monkeypatch.setattr(lifecycle, 'TimingRelay', relay)
    if ready:
        owner.replace()
        assert server.preserve_socket
        notify.assert_called_once()
        assert 'MAINPID=111' in notify.call_args.args[0]
        cleanup.assert_not_called()
        relay.assert_called_once_with(parent)
        server.timing_proxy.retire.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match='before readiness'):
            owner.replace()
        assert not server.preserve_socket and owner.relay is None
        cleanup.assert_called_once_with(successor, timeout=5)
        server.timing_proxy.cancel_handover.assert_called_once()
        parent.close.assert_called_once()
        notify.assert_not_called()
        relay.assert_not_called()
    kwargs = spawn.call_args.kwargs
    assert spawn.call_args.args == (['fake-successor'],)
    assert kwargs['pass_fds'] == [13, 14, 15]
    assert kwargs['kill_on_owner_exit'] is False
    assert json.loads(kwargs['env'][lifecycle.HANDOVER_ENV]) == inherited


@pytest.mark.parametrize('value', ['win32', 'darwin'])
def test_external_native_restart_uses_service_control_not_hot_reload(value):
    calls = []
    windows_states = iter(['RUNNING', 'STOPPED', 'STOPPED'])

    def command(*args, **kwargs):
        calls.append(args)
        output = ''
        if args[:2] == ('sc.exe', 'queryex'):
            output = f'STATE : 4 {next(windows_states)}\nPID : 333\n'
        return SimpleNamespace(returncode=0, stdout=output)

    controller = ServiceControl(platform=value, runner=command, uid=501)
    with pytest.raises(NotImplementedError, match='hot reload'):
        controller.reload('execution-test.service')
    assert calls == []
    controller.restart('execution-test.service')
    if value == 'win32':
        assert ('sc.exe', 'stop', 'execution-test.service') in calls
        assert calls[-1] == ('sc.exe', 'start', 'execution-test.service')
    else:
        assert calls[-1] == ('launchctl', 'kickstart', '-k',
                             'gui/501/com.tfi.portal.execution-test')
