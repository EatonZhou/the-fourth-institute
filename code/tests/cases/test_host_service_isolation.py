"""Disposable test instances cannot install or enable real host user services.

:module: operations.test-framework
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


def test_real_account_host_install_is_refused_before_writing(tmp_path, monkeypatch):
    from scripts import portal_service_units as units
    from scripts import instance_services, tfi_release_platform_linux as linux
    from types import SimpleNamespace
    import sys
    from tests.cases.instance_test_support import write_instance
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    path = Path.home() / '.config/systemd/user/tfi-fixture-codex-app-server@sample-codex.service'
    # Read-only manager discovery precedes the guarded registration. Model
    # those facts without requiring an actual host user-manager connection.
    monkeypatch.setattr(linux, 'unit_directory', lambda: path.parent)
    monkeypatch.setattr(linux, 'runtime_python', lambda _: Path(sys.executable))
    monkeypatch.setattr(instance_services, 'controller', lambda _: SimpleNamespace(
        status=lambda _: {'load': 'not-found'}))
    assert not path.exists()
    with pytest.raises(RuntimeError, match='cannot install host'):
        units._define('codex-app-server@sample-codex')
    assert not path.exists()


def test_unmocked_systemctl_mutation_never_spawns():
    with pytest.raises(RuntimeError, match='cannot modify host'):
        subprocess.run(['/usr/bin/systemctl', '--user', 'enable', '--now', 'tfi-fixture-test.service'])


def test_service_tests_can_use_isolated_directory_and_explicit_runner(tmp_path, monkeypatch):
    from scripts import tfi_release_platform_linux as linux
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs:
                        calls.append(args) or subprocess.CompletedProcess(args, 0))
    target = tmp_path / 'units'
    linux.register_services(target, {'tfi-fixture-test.service': 'synthetic unit'})
    assert (target / 'tfi-fixture-test.service').read_text() == 'synthetic unit'
    assert calls == [['systemctl', '--user', 'daemon-reload']]
