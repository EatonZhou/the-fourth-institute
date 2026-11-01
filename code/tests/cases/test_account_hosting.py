""":module: execution.providers"""
from pathlib import Path
import json
import shutil
import stat

import pytest

from management_common import file_permissions
from management_portal.execution import account_hosting as hosting


@pytest.fixture(autouse=True)
def isolated_installation_paths(tmp_path, monkeypatch):
    from tests.cases.instance_test_support import write_instance
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    return root


def test_account_paths_and_private_lifecycle(tmp_path, monkeypatch):
    """执行账号目录按站点与账号隔离，拒绝越界路径并完整删除

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_portal.execution.account_registrations import create_registration, delete_registration
    create_registration({"config": {"account_dir": str(tmp_path / "accounts" / "codex_private")}, "account_id": "codex_private", "provider": "codex", "kind": "subscription_login"}, "test")
    home = hosting.ensure_account_home("codex", "codex_private")
    assert home == tmp_path / "accounts/codex_private"
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert hosting.codex_socket_path("codex_private") == tmp_path / "instance/runtime/s/codex-codex_private.sock"
    for value in ("../one", "one/two", "one\n", "", "@one"):
        with pytest.raises(ValueError):
            hosting.account_home("codex", value)
    (home / "state").write_text("provider-owned")
    delete_registration("codex_private")
    hosting.remove_account_runtime("codex", "codex_private")
    assert (home / "state").read_text() == "provider-owned"


def test_secret_replace_is_private_atomic_and_silent(tmp_path, monkeypatch, capsys):
    """密钥写入与轮换保持 600 权限，失败保留原文件且不回显

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    secret_root = tmp_path / 'instance/secrets'
    secret = "synthetic-test-value"
    path = hosting.write_secret_file("example", secret)
    assert path == secret_root / "example.key"
    assert path.read_text() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    previous_inode = path.stat().st_ino
    assert hosting.write_secret_file("example", "synthetic-rotated-value") == path
    assert path.stat().st_ino != previous_inode
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    monkeypatch.setattr(file_permissions.os, "replace", lambda *_, **__: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError):
        hosting.write_secret_file("example", "synthetic-failed-value")
    assert path.read_text() == "synthetic-rotated-value"
    assert list(secret_root.iterdir()) == [path]
    assert not capsys.readouterr().out


def test_site_environment_shared_by_unit_rendering(tmp_path, monkeypatch):
    from management_common.instance_manifest import load
    from scripts.tfi_release_platform_linux import manifest_unit_text
    instance = load()
    rendered = manifest_unit_text(instance, 'resource')
    assert instance.service_environment('resource') == {'PORTAL_INSTANCE_ROOT': str(tmp_path / 'instance')}
    assert 'Environment="PORTAL_INSTANCE_ROOT=' in rendered
    assert 'HOME=' not in rendered


def test_codex_unstarted_account_has_no_host_process_operations(monkeypatch):
    from scripts import portal_service_units as units
    monkeypatch.setattr(units, '_run', lambda *a, **k: pytest.fail('Unregistered daemon must not reach service control'))
    assert units.codex_instance_state('codex_unstarted')['load'] == 'not-found'
