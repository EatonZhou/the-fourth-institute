"""Lifecycle capability boundaries, without installed services or model calls."""
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


class Server:
    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.closed = False

    def serve_forever(self):
        self.started.set()
        assert self.finished.wait(5), 'test did not stop server'

    def shutdown(self):
        self.finished.set()

    def server_close(self):
        self.closed = True


def test_unknown_platform_is_rejected(monkeypatch):
    platform(monkeypatch, 'freebsd')
    with pytest.raises(NotImplementedError, match='Unsupported ExecutionService platform'):
        lifecycle.ServiceLifecycle(Server())


@pytest.mark.parametrize('value', ['linux', 'darwin', 'win32'])
def test_shutdown_handlers_and_cleanup_follow_supported_signals(monkeypatch, value):
    """停止信号关闭服务并恢复原处理器；SIGHUP 不再触发进程内换代"""
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
        assert set(callbacks) == ({2, 15, 21} if value == 'win32' else {2, 15})
        callbacks[signal.SIGBREAK if value == 'win32' else signal.SIGTERM](None, None)
        return original()

    original = owner._run
    monkeypatch.setattr(owner, '_run', run)
    assert owner.run() == 0
    assert owner.stop.is_set()
    assert server.finished.is_set() and server.closed
    assert set(restored) == set(callbacks)


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


@pytest.mark.parametrize('value', ['win32', 'darwin'])
def test_external_native_restart_uses_service_control(value):
    calls = []
    windows_states = iter(['RUNNING', 'STOPPED', 'STOPPED'])

    def command(*args, **kwargs):
        calls.append(args)
        output = ''
        if args[:2] == ('sc.exe', 'queryex'):
            output = f'STATE : 4 {next(windows_states)}\nPID : 333\n'
        return SimpleNamespace(returncode=0, stdout=output)

    controller = ServiceControl(platform=value, runner=command, uid=501)
    controller.restart('execution-test.service')
    if value == 'win32':
        assert ('sc.exe', 'stop', 'execution-test.service') in calls
        assert calls[-1] == ('sc.exe', 'start', 'execution-test.service')
    else:
        assert calls[-1] == ('launchctl', 'kickstart', '-k',
                             'gui/501/com.tfi.portal.execution-test')
