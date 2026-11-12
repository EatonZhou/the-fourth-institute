from types import SimpleNamespace
import pytest
from management_common import ssh_runtime


def test_windows_password_ssh_uses_paired_git_tools(tmp_path, monkeypatch):
    root = tmp_path / 'Git with spaces'
    for name in ('cmd/git.exe', 'usr/bin/ssh.exe', 'usr/bin/sh.exe'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(ssh_runtime, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(ssh_runtime, 'shutil', SimpleNamespace(which=lambda _: str(root / 'cmd/git.exe')))
    assert ssh_runtime.ssh_executable(needs_askpass=True) == (root / 'usr/bin/ssh.exe').as_posix()
    assert ssh_runtime.ssh_executable(needs_askpass=False) == 'ssh'
    (root / 'usr/bin/sh.exe').unlink()
    with pytest.raises(OSError, match='Git for Windows'):
        ssh_runtime.ssh_executable(needs_askpass=True)


def test_posix_ssh_uses_native_client(monkeypatch):
    monkeypatch.setattr(ssh_runtime, 'sys', SimpleNamespace(platform='linux'))
    assert ssh_runtime.ssh_executable(needs_askpass=True) == 'ssh'
