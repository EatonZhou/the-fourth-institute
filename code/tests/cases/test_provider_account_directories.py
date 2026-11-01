"""Host account registration and independent daemons sharing credentials."""
from pathlib import Path
import subprocess
import sys

import pytest

from management_common import provider_account_paths as paths
from management_portal.execution import account_hosting as hosting, account_registrations as store
from management_portal.hatg.contracts import InstructionError


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    from management_common import host_instances
    source = tmp_path / 'host-config/instances.json'
    monkeypatch.setattr(host_instances, 'registry_path', lambda: source)
    return source


def test_subscription_requires_explicit_directory_and_creates_selected_path(tmp_path):
    with pytest.raises(InstructionError, match='account_dir'):
        store.create_registration({'account_id': 'codex_missing', 'provider': 'codex',
            'kind': 'subscription_login'}, 'test')
    directory = tmp_path / 'chosen/nested/account'
    row = store.create_registration({'account_id': 'codex_selected', 'provider': 'codex',
        'kind': 'subscription_login', 'config': {'account_dir': str(directory)}}, 'test')
    assert directory.is_dir()
    assert row['config']['account_dir'] == str(directory)
    assert hosting.account_home('codex', 'codex_selected') == directory


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


def test_native_daemon_hosts_share_account_cwd(tmp_path):
    from management_portal import provider_daemon_host
    command = [sys.executable, provider_daemon_host.__file__, "--", sys.executable,
               "-c", "import time; print('ready', flush=True); time.sleep(30)"]
    processes = []
    try:
        for _ in range(2):
            process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            processes.append(process)
            assert process.stdout.readline().strip() == 'ready'
        assert all(p.poll() is None for p in processes)
        processes[0].terminate(); processes[0].communicate(timeout=10)
        assert processes[0].returncode == 0 and processes[1].poll() is None
    finally:
        for process in processes:
            if process.poll() is None:process.terminate()
            process.communicate(timeout=10)
    assert all(p.returncode == 0 for p in processes)


def test_failed_registration_removes_only_new_empty_directory(monkeypatch, tmp_path):
    original = store._check_relationships
    calls = 0
    def reject_second(connection, row):
        nonlocal calls
        calls += 1
        original(connection, row)
        if calls == 2:
            raise InstructionError("instruction.conflict", "synthetic concurrent conflict")
    monkeypatch.setattr(store, "_check_relationships", reject_second)
    directory = tmp_path / "selected-account"
    with pytest.raises(InstructionError):
        store.create_registration({"account_id": "codex_rollback", "provider": "codex", "kind": "subscription_login", "config": {"account_dir": str(directory)}}, "test")
    assert not directory.exists()
    assert store.registration("codex_rollback") is None

def test_kimi_process_tree_identity_is_instance_scoped(tmp_path, monkeypatch):
    from management_portal import kimi_account_runtime as kimi
    account = {'account_id': 'shared'}
    control = tmp_path / 'instance-a/runtime/provider-accounts/kimi/shared'
    monkeypatch.setattr(kimi, 'control', lambda row: control)
    first = kimi.process_tree_id(account)
    assert kimi.process_tree_id(account) == first
    control = tmp_path / 'instance-b/runtime/provider-accounts/kimi/shared'
    assert kimi.process_tree_id(account) != first
