"""Native definitions and Telepus lifecycle, using an isolated launchd model."""
import plistlib
import subprocess

import pytest

from management_common.instance_manifest import load
from management_common.operation_errors import OperationError
from management_portal.telepus.runtime import TelepusRuntime, COMPONENTS, WEIXIN_COMPONENTS
from scripts import instance_services, tfi_release_platform_macos as macos
from scripts.portal_service_control import ServiceControl
from tests.cases.instance_test_support import write_instance


class Launchd:
    def __init__(self):
        self.jobs = {}
        self.disabled = set()
        self.calls = []
        self.fail = False

    def __call__(self, *args, check=True):
        self.calls.append(args)
        assert args[0] == 'launchctl'
        command, target = args[1:3]
        if self.fail and command == 'bootstrap':
            raise subprocess.CalledProcessError(5, args)
        if command == 'print-disabled':
            return subprocess.CompletedProcess(args, 0, '\n'.join(
                '"' + label + '" => true' for label in self.disabled), '')
        if command == 'print':
            path = self.jobs.get(target)
            return subprocess.CompletedProcess(args, 0 if path else 113,
                f'path = {path}\nstate = running\npid = 123\n' if path else '', '')
        if command == 'enable':
            self.disabled.discard(target.split('/', 2)[2])
        elif command == 'disable':
            self.disabled.add(target.split('/', 2)[2])
        elif command == 'bootstrap':
            from pathlib import Path
            path = Path(args[3])
            content = plistlib.loads(path.read_bytes())
            assert content['Label'] not in self.disabled
            self.jobs[target + '/' + content['Label']] = str(path)
        elif command == 'bootout':
            self.jobs.pop(target, None)
        elif command != 'kickstart':
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, '', '')


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    agent_root = tmp_path / 'custom-launch-agents'
    manager = Launchd()
    control = ServiceControl(platform='macos', runner=manager, agent_root=agent_root, uid=501)
    monkeypatch.setattr(instance_services, 'platform_name', lambda: 'macos')
    monkeypatch.setattr(instance_services, 'adapter', lambda: macos)
    monkeypatch.setattr(macos, 'unit_directory', lambda: agent_root)
    monkeypatch.setattr(macos, 'service_controller', lambda: control)
    return TelepusRuntime(root=root), manager, root, agent_root


def test_weixin_registers_exact_instance_services_and_survives_repeated_enable(native):
    runtime, manager, root, directory = native
    assert not runtime.status(WEIXIN_COMPONENTS)['complete']
    assert runtime.ensure_weixin()['complete']
    instance = load(root)
    assert all(c in instance.document['services']['components'] for c in WEIXIN_COMPONENTS)
    assert COMPONENTS[1] not in instance.document['services']['components']
    files = list(directory.glob('*.plist'))
    assert len(files) == 2
    for path in files:
        definition = plistlib.loads(path.read_bytes())
        assert definition['EnvironmentVariables']['PORTAL_INSTANCE_ROOT'] == str(root)
        assert definition['RunAtLoad']
        assert definition['WorkingDirectory'] == str(instance.code_root())
        assert definition['ProgramArguments'][-1] in WEIXIN_COMPONENTS
    first_jobs = dict(manager.jobs)
    runtime.ensure_weixin()
    assert manager.jobs == first_jobs
    assert sum(c[1] == 'bootstrap' for c in manager.calls) == 2


def test_switch_disables_persistent_startup_and_reenables_without_restart(native):
    runtime, manager, root, directory = native
    assert runtime.set_enabled(True)['complete']
    assert not runtime.set_enabled(False)['enabled']
    assert len(manager.disabled) == 3 and not manager.jobs
    assert len(list(directory.glob('*.plist'))) == 3
    assert runtime.set_enabled(True)['complete']
    assert not manager.disabled


def test_failure_retains_registration_for_retry_and_does_not_report_ready(native):
    runtime, manager, root, directory = native
    manager.fail = True
    with pytest.raises(OperationError):
        runtime.ensure_weixin()
    assert not runtime.status(WEIXIN_COMPONENTS)['complete']
    manager.fail = False
    assert runtime.ensure_weixin()['complete']


def test_other_instance_definition_is_never_overwritten(native):
    runtime, manager, root, directory = native
    directory.mkdir()
    instance = load(root)
    path = macos.unit_path(directory, instance.service_prefix + '-' + WEIXIN_COMPONENTS[0] + '.service')
    original = plistlib.dumps({'EnvironmentVariables': {'PORTAL_INSTANCE_ROOT': '/another-instance'}})
    path.write_bytes(original)
    with pytest.raises(OperationError, match='another instance'):
        runtime.ensure_weixin()
    assert path.read_bytes() == original and not manager.jobs


def test_linux_persistent_switch_contract():
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, 'enabled\n', '')
    control = ServiceControl(platform='linux', runner=run)
    name = 'tfi-fixture-telepus-weixin-router.service'
    control.set_enabled(name, True)
    assert control.is_enabled(name)
    control.set_enabled(name, False)
    assert calls == [('systemctl', '--user', 'enable', '--now', name),
                     ('systemctl', '--user', 'is-enabled', name),
                     ('systemctl', '--user', 'disable', '--now', name)]


def test_unsupported_host_reports_unavailable_without_registration(native, monkeypatch):
    runtime, manager, root, directory = native
    monkeypatch.setattr(instance_services, 'platform_name', lambda: 'windows')
    assert not runtime.status()['available']
    with pytest.raises(OperationError):
        runtime.ensure_weixin()
    assert not directory.exists() and not manager.calls
