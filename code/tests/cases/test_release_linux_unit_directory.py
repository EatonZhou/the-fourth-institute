"""The operator service manager, not a Resident's isolated HOME, owns units."""
from subprocess import CompletedProcess
import pytest
from scripts import tfi_release_platform_linux as linux


def test_unit_directory_uses_manager_search_path_under_isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path / 'resident-home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'resident-config'))
    config = tmp_path / 'operator config' / 'systemd/user'
    def run(command, **kwargs):
        assert command == ['systemctl', '--user', 'show', '--property=UnitPath', '--value']
        return CompletedProcess(command, 0,
            f'"{config}.control" /run/user/1000/systemd/transient "{config}" /usr/lib/systemd/user\n', '')
    monkeypatch.setattr(linux.subprocess, 'run', run)
    assert linux.unit_directory() == config


def test_unit_directory_does_not_guess_when_manager_path_is_unavailable(monkeypatch):
    monkeypatch.setattr(linux.subprocess, 'run', lambda *a, **k: CompletedProcess(a, 0, '', ''))
    with pytest.raises(RuntimeError, match='unit directory'):
        linux.unit_directory()
