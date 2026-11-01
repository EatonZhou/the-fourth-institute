"""macOS control jobs select their instance and never respawn failed moves.

:module: operations.deployment
:design: decision_d88306d0bd17
"""
import os
from pathlib import Path
import json
import plistlib
import subprocess
import sys

import pytest

from management_common.instance_manifest import load
from scripts import instance_services, tfi_instance_manager, tfi_move_area
from scripts import tfi_release_platform_macos as macos
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def instance(tmp_path):
    instance = load(write_instance(tmp_path / 'instance'))
    instance.directory('runtime').mkdir(exist_ok=True)
    return instance


@pytest.mark.parametrize('launcher', [tfi_instance_manager.launch, tfi_move_area._launch])
def test_native_job_carries_selection_without_launchctl_inheriting_environment(instance, monkeypatch, launcher):
    """The actual child can resolve the selected root from the serialized job."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'must-not-cross-instance-boundary')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', '/some/other/instance')
    real_run = subprocess.run
    observed = []

    def bootstrap(arguments, **kwargs):
        assert arguments[:3] == ['launchctl', 'bootstrap', f'gui/{os.getuid()}']
        definition = plistlib.loads(Path(arguments[3]).read_bytes())
        assert definition['RunAtLoad'] is True and definition['KeepAlive'] is False
        assert definition['ProgramArguments'][-4:] == ['--name', instance.name, '--operation-id', 'a' * 32]
        assert 'PORTAL_DATABASE_URL' not in definition['EnvironmentVariables']
        # launchd gets the plist environment, not the environment of launchctl.
        result = real_run([sys.executable, '-c',
                           'from management_common.instance_manifest import load; print(load().root)'],
                          cwd=Path(__file__).resolve().parents[2],
                          env=definition['EnvironmentVariables'], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == str(instance.root)
        observed.append(definition)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(macos.subprocess, 'run', bootstrap)
    launcher(instance.root, {'id': 'a' * 32, 'unit': 'tfi-control-' + 'a' * 32})
    assert len(observed) == 1


def job_file(instance, *, foreign=False):
    directory = instance.directory('runtime') / 'control-jobs'
    directory.mkdir()
    path = directory / ('tfi-control-' + 'b' * 32 + '.plist')
    path.write_bytes(plistlib.dumps({'Label': path.stem, 'EnvironmentVariables': {
        'PORTAL_INSTANCE_ROOT': '/foreign' if foreign else str(instance.root)}}))
    return path


def test_retiring_definitions_removes_finished_native_job(instance, monkeypatch):
    path = job_file(instance)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='state = not running\n')

    monkeypatch.setattr(macos.subprocess, 'run', run)
    monkeypatch.setattr(instance_services, 'platform_name', lambda: 'macos')
    monkeypatch.setattr(instance_services, 'adapter', lambda: macos)
    monkeypatch.setattr(macos, 'unit_directory', lambda: instance.root / 'unused-agent-directory')
    instance_services.remove_definitions(instance)
    assert [c[1] for c in calls] == ['print', 'bootout']
    assert not path.parent.exists()


@pytest.mark.parametrize('foreign', [False, True])
def test_native_job_cleanup_preserves_live_or_foreign_jobs(instance, monkeypatch, foreign):
    path = job_file(instance, foreign=foreign)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='state = running\n pid = 123\n')

    monkeypatch.setattr(macos.subprocess, 'run', run)
    with pytest.raises((RuntimeError, ValueError), match='another instance|still running'):
        macos.remove_control_jobs(instance)
    assert path.exists() and all(args[1] != 'bootout' for args in calls)


def test_failed_bootstrap_removes_only_new_definition(instance, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(5, args[0])

    monkeypatch.setattr(macos.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        macos.launch_control_job(instance, {'unit': 'tfi-control-' + 'c' * 32},
                                 ['python', 'control'], {'PORTAL_INSTANCE_ROOT': str(instance.root)},
                                 instance.directory('runtime') / 'control.log')
    assert list((instance.directory('runtime') / 'control-jobs').iterdir()) == []


def test_cleanup_preserves_queued_job_before_process_exists(instance, monkeypatch):
    path = job_file(instance)
    (instance.directory('runtime') / 'portal-control.json').write_text(json.dumps({
        'unit': path.stem, 'status': 'queued'}))
    calls = []
    monkeypatch.setattr(macos.subprocess, 'run', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match='still running'):
        macos.remove_control_jobs(instance)
    assert path.exists() and not calls
