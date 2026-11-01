"""Atomic configuration replacement preserves native permissions and complete data.

Native ACL mocks check API calls; Windows/macOS filesystem verification is separate.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import threading

import pytest
from management_common import configuration_files as files


def test_concurrent_saves_publish_complete_configurations(tmp_path, monkeypatch):
    config = tmp_path / 'configuration/site.json'
    values = [{'theme': 'one'}, {'theme': 'two'}]
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def overlap(source, destination):
        barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(files.os, 'replace', overlap)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(files.atomic_json, config, value) for value in values]
        for future in futures:
            future.result(timeout=10)
    assert json.loads(config.read_text()) in values
    assert set(config.parent.iterdir()) == {config}


def test_fixed_temporary_name_cannot_redirect_write(tmp_path):
    config = tmp_path / 'configuration/site.json'
    victim = tmp_path / 'untouched.txt'
    victim.write_text('unrelated content')
    config.parent.mkdir()
    alias = config.with_suffix('.tmp')
    try:
        alias.symlink_to(victim)
    except OSError:
        pytest.skip('host cannot create test symlinks')
    files.atomic_json(config, {'theme': 'one'})
    assert victim.read_text() == 'unrelated content'
    assert alias.is_symlink()
    assert json.loads(config.read_text()) == {'theme': 'one'}


@pytest.mark.parametrize('phase', ['replace', 'permissions'])
def test_failed_save_keeps_previous_content_and_cleans_temporary(tmp_path, monkeypatch, phase):
    config = tmp_path / 'configuration/site.json'
    files.atomic_json(config, {'theme': 'one'})
    before = config.read_bytes()
    opened = []
    real_fdopen = os.fdopen
    def opened_stream(*args, **kwargs):
        stream = real_fdopen(*args, **kwargs)
        opened.append(stream)
        return stream
    def denied(*args):
        raise PermissionError('test operation refused')
    monkeypatch.setattr(files.os, 'fdopen', opened_stream)
    if phase == 'replace':
        monkeypatch.setattr(files.os, 'replace', denied)
    else:
        monkeypatch.setattr(files, 'preserve_config_permissions', denied)
    with pytest.raises(PermissionError):
        files.atomic_json(config, {'theme': 'two'})
    assert config.read_bytes() == before
    assert set(config.parent.iterdir()) == {config}
    assert opened and all(stream.closed for stream in opened)


@pytest.mark.skipif(os.name == 'nt', reason='Windows uses native ACL preservation')
def test_replacing_config_preserves_restricted_mode(tmp_path):
    config = tmp_path / 'configuration/site.json'
    files.atomic_json(config, {})
    config.chmod(0o600)
    files.atomic_json(config, {'theme': 'two'})
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_configuration_symlink_is_rejected(tmp_path):
    victim = tmp_path / 'untouched.json'
    victim.write_text('{}')
    config = tmp_path / 'configuration/site.json'
    config.parent.mkdir()
    try:
        config.symlink_to(victim)
    except OSError:
        pytest.skip('host cannot create test symlinks')
    with pytest.raises(ValueError, match='symbolic link'):
        files.atomic_json(config, {'changed': True})
    assert victim.read_text() == '{}'


@pytest.mark.parametrize("protected,flags", [(True, 0x80000007), (False, 0x20000007)])
def test_windows_dacl_copy_retains_inheritance_and_reports_denial(monkeypatch, protected, flags):
    """Exercise the native call contract on Linux; this does not certify NTFS."""
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    error = [122]
    applied = []

    class NativeFunction:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    def get_security(path, information, buffer, size, needed):
        assert path == "original.json"
        assert information == 7
        ctypes.cast(needed, ctypes.POINTER(wintypes.DWORD)).contents.value = 32
        return buffer is not None

    def get_control(_descriptor, control, _revision):
        ctypes.cast(control, ctypes.POINTER(wintypes.WORD)).contents.value = 0x1000 if protected else 0
        return True

    def set_security(path, information, _descriptor):
        applied.append((path, information))
        return error[0] != 5

    native = SimpleNamespace(GetFileSecurityW=NativeFunction(get_security),
                             GetSecurityDescriptorControl=NativeFunction(get_control),
                             SetFileSecurityW=NativeFunction(set_security))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: native, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "native error"), raising=False)
    files._copy_windows_dacl(Path("original.json"), Path("temporary.json"))
    assert applied == [("temporary.json", flags)]
    # A failed descriptor read must not proceed with a default/permissive ACL.
    error[0] = 5
    with pytest.raises(OSError):
        files._copy_windows_dacl(Path("original.json"), Path("temporary.json"))
    assert len(applied) == 1


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("setfacl"), reason="Linux POSIX ACL tool required")
def test_existing_acl_and_absence_of_acl_survive_replacement(tmp_path):
    config = tmp_path / "site.json"
    files.atomic_json(config, {"setting": "one"})
    config.chmod(0o640)
    subprocess.run(["setfacl", "-m", f"u:{os.getuid()}:---", str(config)], check=True, capture_output=True)
    original_acl = os.getxattr(config, "system.posix_acl_access")
    files.atomic_json(config, {"setting": "two"})
    assert os.getxattr(config, "system.posix_acl_access") == original_acl
    subprocess.run(["setfacl", "-b", str(config)], check=True, capture_output=True)
    subprocess.run(["setfacl", "-m", f"d:u:{os.getuid()}:r--", str(config.parent)], check=True, capture_output=True)
    assert "system.posix_acl_access" not in os.listxattr(config)
    files.atomic_json(config, {"setting": "two"})
    assert "system.posix_acl_access" not in os.listxattr(config)


def test_macos_acl_copy_requests_only_acl_and_propagates_failure(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    calls = []

    class CopyFile:
        result = 0

        def __call__(self, source, target, state, flags):
            calls.append((source, target, state, flags))
            return self.result

    copyfile = CopyFile()
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(copyfile=copyfile))
    monkeypatch.setattr(ctypes, "get_errno", lambda: 13)
    files._copy_macos_acl(Path("original.json"), Path("temporary.json"))
    assert calls == [(b"original.json", b"temporary.json", None, 1)]
    copyfile.result = -1
    with pytest.raises(PermissionError):
        files._copy_macos_acl(Path("original.json"), Path("temporary.json"))
