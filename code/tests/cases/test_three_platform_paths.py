"""Portable data roots and atomic configuration, without live site access.

:module: platform.core
:design: decision_15aab18c0ab3
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import shutil
import subprocess
import sys
import threading

import pytest

from management_common import instance_paths, portal_paths


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", tmp_path / "installation")
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", tmp_path / "settings" / "paths.json")
    legacy = tmp_path / "legacy" / "paths.json"
    monkeypatch.setattr(portal_paths, "compatibility_value", lambda key, default=None: str(legacy) if key == 'paths_config' else default)
    for key in (*portal_paths.ENV_KEYS.values(), "PORTAL_PATHS_CONFIG", "PORTAL_SHARE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    return {key: "portal_data/" + key for key in portal_paths.KEYS}


def test_concurrent_saves_publish_complete_configurations(isolated_paths, monkeypatch):
    first = isolated_paths
    second = {key: "second/" + key for key in portal_paths.KEYS}
    # Both writers reach the actual rename with complete files before either
    # can publish. A shared .tmp path loses a writer's content or its rename.
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def overlap(source, destination):
        barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(portal_paths.os, "replace", overlap)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(portal_paths.write_config, values) for values in (first, second)]
        for future in futures:
            future.result(timeout=10)
    assert portal_paths.read_config() in (first, second)
    assert list(portal_paths.CONFIG_PATH.parent.glob("*.tmp")) == []


def test_fixed_temporary_name_cannot_redirect_write(isolated_paths, tmp_path):
    victim = tmp_path / "untouched.txt"
    victim.write_text("unrelated content", encoding="utf-8")
    portal_paths.CONFIG_PATH.parent.mkdir()
    alias = portal_paths.CONFIG_PATH.with_suffix(".tmp")
    try:
        alias.symlink_to(victim)
    except OSError:
        pytest.skip("host cannot create test symlinks")
    portal_paths.write_config(isolated_paths)
    assert victim.read_text(encoding="utf-8") == "unrelated content"
    assert alias.is_symlink()
    assert portal_paths.read_config() == isolated_paths


def test_failed_replace_keeps_old_config_and_removes_temporary(isolated_paths, monkeypatch):
    portal_paths.write_config(isolated_paths)
    before = portal_paths.CONFIG_PATH.read_bytes()

    def fail(*_args):
        raise PermissionError("test replacement refused")

    monkeypatch.setattr(portal_paths.os, "replace", fail)
    with pytest.raises(PermissionError):
        portal_paths.write_config({key: "changed/" + key for key in portal_paths.KEYS})
    assert portal_paths.CONFIG_PATH.read_bytes() == before
    assert list(portal_paths.CONFIG_PATH.parent.glob("*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows uses native ACL preservation")
def test_replacing_config_preserves_restricted_mode(isolated_paths):
    portal_paths.write_config(isolated_paths)
    portal_paths.CONFIG_PATH.chmod(0o600)
    portal_paths.write_config(isolated_paths)
    assert stat.S_IMODE(portal_paths.CONFIG_PATH.stat().st_mode) == 0o600


@pytest.mark.parametrize("value,expected", [
    (r"C:\\data\\共享资料", r"C:\data\共享资料"),
    ("D:/data/share", r"D:\data\share"),
    (r"\\server\share\资料", r"\\server\share\资料"),
    (r"\\?\C:\long\data", r"\\?\C:\long\data"),
    (r"data\accounts", r"C:\Portal\data\accounts"),
    ("data/accounts", r"C:\Portal\data\accounts"),
])
def test_windows_paths_are_anchored_without_process_drive(value, expected):
    actual = portal_paths._anchor_directory(PureWindowsPath(value), PureWindowsPath(r"C:\Portal"))
    assert actual == PureWindowsPath(expected)


@pytest.mark.parametrize("value", ["C:", r"D:data", r"C:..\data", r"\data", "/data"])
def test_windows_partial_roots_are_rejected(value):
    with pytest.raises(ValueError, match="fully qualified"):
        portal_paths._anchor_directory(PureWindowsPath(value), PureWindowsPath(r"C:\Portal"))


def test_native_paths_preserve_unicode_explicit_roots_and_home(isolated_paths, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    # expanduser uses the platform account environment.
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    assert portal_paths.resolve_directory("~/资料") == tmp_path / "user" / "资料"
    assert portal_paths.resolve_directory("data/空间 名称") == portal_paths.PORTAL_ROOT / "data" / "空间 名称"
    assert portal_paths.resolve_directory(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_configuration_filename_is_expanded_and_anchored_at_import(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", "settings/local.json")
    spec = importlib.util.spec_from_file_location("isolated_portal_paths", portal_paths.__file__)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.CONFIG_PATH == isolated.PORTAL_ROOT / "settings" / "local.json"


def test_legacy_read_is_not_a_migration_and_new_config_wins(isolated_paths):
    legacy = Path(portal_paths.compatibility_value('paths_config'))
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(isolated_paths), encoding="utf-8")
    before = legacy.read_bytes()
    assert portal_paths.read_config() == isolated_paths
    assert not portal_paths.CONFIG_PATH.exists()
    newer = {key: "new/" + key for key in portal_paths.KEYS}
    portal_paths.write_config(newer)
    assert portal_paths.read_config() == newer
    assert legacy.read_bytes() == before


def test_explicit_missing_or_invalid_config_does_not_fall_back(isolated_paths, monkeypatch):
    legacy = Path(portal_paths.compatibility_value('paths_config'))
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(isolated_paths), encoding="utf-8")
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(portal_paths.CONFIG_PATH))
    assert portal_paths.read_config() == {}
    portal_paths.CONFIG_PATH.parent.mkdir()
    portal_paths.CONFIG_PATH.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid Portal paths"):
        portal_paths.read_config()


def test_instance_state_resolves_aliases_and_preserves_nested_source(isolated_paths, tmp_path):
    values = {**isolated_paths, "instances_root": str(tmp_path / "runtime")}
    portal_paths.write_config(values)
    source = tmp_path / "runtime" / "release" / "source"
    source.mkdir(parents=True)
    assert not instance_paths.is_instance_state(source / "script.py", source)
    assert instance_paths.is_instance_state(source / "portal_instances" / "state.json", source)
    assert instance_paths.is_instance_state(tmp_path / "runtime" / "dev" / "state.json", source)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create test symlinks")
    assert not instance_paths.is_instance_state(alias / "script.py", alias)


def test_instance_root_equal_to_source_does_not_hide_source_files(isolated_paths, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    portal_paths.write_config({**isolated_paths, "instances_root": str(source)})
    assert not instance_paths.is_instance_state(source / "script.py", source)
    assert instance_paths.is_instance_state(source / "portal_instances" / "state.json", source)
    nested_state = source / "portal_data" / "instances"
    portal_paths.write_config({**isolated_paths, "instances_root": str(nested_state)})
    assert instance_paths.is_instance_state(nested_state / "dev" / "state.json", source)


@pytest.mark.parametrize("value", ["", " ", "bad\npath", "bad\rpath", "bad\0path"])
def test_invalid_values_never_change_configuration(isolated_paths, value):
    portal_paths.write_config(isolated_paths)
    before = portal_paths.CONFIG_PATH.read_bytes()
    with pytest.raises(ValueError):
        portal_paths.write_config({**isolated_paths, "share_root": value})
    assert portal_paths.CONFIG_PATH.read_bytes() == before


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
    portal_paths._copy_windows_dacl(Path("original.json"), Path("temporary.json"))
    assert applied == [("temporary.json", flags)]
    # A failed descriptor read must not proceed with a default/permissive ACL.
    error[0] = 5
    with pytest.raises(OSError):
        portal_paths._copy_windows_dacl(Path("original.json"), Path("temporary.json"))
    assert len(applied) == 1


def test_permission_copy_failure_never_publishes_or_leaves_content(isolated_paths, monkeypatch):
    portal_paths.write_config(isolated_paths)
    original = portal_paths.CONFIG_PATH.read_bytes()

    def denied(*_args):
        raise PermissionError("permission metadata unavailable")

    monkeypatch.setattr(portal_paths, "_preserve_config_permissions", denied)
    with pytest.raises(PermissionError):
        portal_paths.write_config({key: "changed/" + key for key in portal_paths.KEYS})
    assert portal_paths.CONFIG_PATH.read_bytes() == original
    assert list(portal_paths.CONFIG_PATH.parent.glob("*.tmp")) == []


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("setfacl"), reason="Linux POSIX ACL tool required")
def test_existing_acl_and_absence_of_acl_survive_replacement(isolated_paths):
    portal_paths.write_config(isolated_paths)
    config = portal_paths.CONFIG_PATH
    config.chmod(0o640)
    subprocess.run(["setfacl", "-m", f"u:{os.getuid()}:---", str(config)], check=True, capture_output=True)
    original_acl = os.getxattr(config, "system.posix_acl_access")
    portal_paths.write_config(isolated_paths)
    assert os.getxattr(config, "system.posix_acl_access") == original_acl
    subprocess.run(["setfacl", "-b", str(config)], check=True, capture_output=True)
    subprocess.run(["setfacl", "-m", f"d:u:{os.getuid()}:r--", str(config.parent)], check=True, capture_output=True)
    assert "system.posix_acl_access" not in os.listxattr(config)
    portal_paths.write_config(isolated_paths)
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
    portal_paths._copy_macos_acl(Path("original.json"), Path("temporary.json"))
    assert calls == [(b"original.json", b"temporary.json", None, 1)]
    copyfile.result = -1
    with pytest.raises(PermissionError):
        portal_paths._copy_macos_acl(Path("original.json"), Path("temporary.json"))


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
