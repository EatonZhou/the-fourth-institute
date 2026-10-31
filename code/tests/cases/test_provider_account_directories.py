"""Host account ownership, registration and daemon exclusivity."""
from pathlib import Path
import subprocess
import sys

import pytest

from management_common import provider_account_paths as paths
from management_portal.execution import account_hosting as hosting, account_registrations as store
from management_portal.hatg.contracts import InstructionError


def test_default_uses_os_identity_not_process_home(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace
    monkeypatch.delenv("PORTAL_ACCOUNT_HOST_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "instance-home"))
    monkeypatch.setattr(pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(tmp_path / "host-home")))
    assert paths.default_directory("codex", "codex_five") == tmp_path / "host-home/.codex-accounts/five"
    assert paths.default_directory("kimi", "kimi-code") == tmp_path / "host-home/.kimi-accounts/kimi-code"


def test_existing_directory_registered_in_place_and_retained_on_delete(tmp_path):
    directory = tmp_path / "native"
    directory.mkdir()
    native = directory / "provider-file"
    native.write_bytes(b"synthetic-native-state")
    identity = native.stat().st_ino
    row = store.create_registration({"account_id": "codex_existing", "provider": "codex",
        "kind": "subscription_login", "config": {"account_dir": str(directory)}}, "test")
    assert row["config"]["account_dir"] == str(directory)
    assert hosting.account_home("codex", "codex_existing") == directory
    with pytest.raises(InstructionError):
        store.create_registration({"account_id": "codex_duplicate", "provider": "codex",
            "kind": "subscription_login", "config": {"account_dir": str(directory)}}, "test")
    store.delete_registration("codex_existing")
    hosting.remove_account_runtime("codex", "codex_existing")
    assert native.stat().st_ino == identity
    assert list(directory.iterdir()) == [native]


def test_paths_require_canonical_absolute_non_symlink_directory(tmp_path):
    actual = tmp_path / "real"
    actual.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(actual, target_is_directory=True)
    for value in ("relative", "~/account", "", str(link), str(link / "child"), str(actual / ".." / "other")):
        with pytest.raises(ValueError):
            paths.canonical_directory(value)
    with pytest.raises(ValueError):
        hosting.registered_home({"kind": "subscription_login", "config": {}})


def test_daemon_lock_conflicts_across_processes_and_releases(tmp_path):
    directory = tmp_path / "native"
    directory.mkdir()
    script = "from pathlib import Path; from management_common.provider_account_paths import daemon_lock; " \
             "lease=daemon_lock(Path(__import__('sys').argv[1])); lease.__enter__()"
    command = [sys.executable, "-c", script, str(directory)]
    with paths.daemon_lock(directory):
        blocked = subprocess.run(command, capture_output=True, text=True)
        assert blocked.returncode != 0
        assert "already in use" in blocked.stderr
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert list(directory.iterdir()) == []


def test_launch_record_uses_registered_directory_and_control_stays_in_runtime(tmp_path):
    directory = tmp_path / "host-account"
    directory.mkdir()
    store.create_registration({"account_id": "codex_descriptor", "provider": "codex",
        "kind": "subscription_login", "config": {"account_dir": str(directory)}}, "test")
    launch = hosting.write_codex_launch("codex_descriptor")
    import json
    assert json.loads(launch.read_text())["account_dir"] == str(directory)
    assert launch.is_relative_to(paths.runtime_root())
    assert list(directory.iterdir()) == []


def test_daemon_lock_rejects_redirected_lock_file(tmp_path):
    directory = tmp_path / "native"
    directory.mkdir()
    target = tmp_path / "unrelated"
    target.write_bytes(b"keep")
    (tmp_path / ".native.portal-daemon.lock").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        with paths.daemon_lock(directory):
            pass
    assert target.read_bytes() == b"keep"


def test_native_daemon_host_imports_from_account_cwd_and_keeps_lease(tmp_path):
    from management_portal import provider_daemon_host
    command = [sys.executable, provider_daemon_host.__file__, "--account-dir", str(tmp_path), "--",
               sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"]
    process = subprocess.Popen(command, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="already in use"):
            with paths.daemon_lock(tmp_path):
                pass
    finally:
        process.terminate()
        process.communicate(timeout=10)
    assert process.returncode == 0
    with paths.daemon_lock(tmp_path):
        pass


def test_failed_registration_removes_only_new_empty_directory(monkeypatch):
    original = store._check_relationships
    calls = 0
    def reject_second(connection, row):
        nonlocal calls
        calls += 1
        original(connection, row)
        if calls == 2:
            raise InstructionError("instruction.conflict", "synthetic concurrent conflict")
    monkeypatch.setattr(store, "_check_relationships", reject_second)
    directory = paths.default_directory("codex", "codex_rollback")
    with pytest.raises(InstructionError):
        store.create_registration({"account_id": "codex_rollback", "provider": "codex", "kind": "subscription_login"}, "test")
    assert not directory.exists()
    assert store.registration("codex_rollback") is None
