""":module: execution.providers"""
from pathlib import Path
import json
import shutil
import stat

import pytest

from management_common import instance_paths, file_permissions
from management_portal.execution import account_hosting as hosting
from scripts import portal_instance_storage as storage


@pytest.fixture(autouse=True)
def isolated_installation_paths(tmp_path, monkeypatch):
    # A missing explicit test config must never fall back to the user's legacy
    # installation config, including when an individual test replaces CONFIG_PATH.
    config = tmp_path / 'site-paths.json'
    config.write_text(json.dumps({key: str(tmp_path / {"share_root": "share", "staging_root": "staging", "instances_root": "instance", "secrets_dir": "secrets"}.get(key, key)) for key in instance_paths.portal_paths.KEYS}))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(config))
    monkeypatch.setattr(instance_paths.portal_paths, 'CONFIG_PATH', config)


def test_account_paths_and_private_lifecycle(tmp_path, monkeypatch):
    """执行账号目录按站点与账号隔离，拒绝越界路径并完整删除

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_portal.execution.account_registrations import create_registration, delete_registration
    monkeypatch.setenv("PORTAL_ACCOUNT_HOST_HOME", str(tmp_path / "host-home"))
    create_registration({"account_id": "codex_private", "provider": "codex", "kind": "subscription_login"}, "test")
    home = hosting.ensure_account_home("codex", "codex_private")
    assert home == tmp_path / "host-home/.codex-accounts/private"
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert hosting.codex_socket_path("codex_private") == tmp_path / "instance/sockets/codex-codex_private.sock"
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
    secret_root = tmp_path / 'secrets'
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(secret_root))
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
    """Dev 与 Release 单元从已保存的路径配置注入账号和密钥目录

    :design: decision_850e6248d680, decision_7956d7cb4bde
    :importance: P1
    """
    config = tmp_path / "portal_paths.json"
    monkeypatch.setattr(instance_paths.portal_paths, "CONFIG_PATH", config)
    instance_paths.portal_paths.write_config({
        key: str(tmp_path / name)
        for key, name in zip(instance_paths.portal_paths.KEYS,
                             ("share", "staging", "instance", "secrets"))
    })
    for name in ("PORTAL_INSTANCES_ROOT", "PORTAL_SECRETS_DIR", "PORTAL_PROVIDER_EGRESS_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    template = tmp_path / "unit"
    template.write_text("[Service]\nExecStart=@PORTAL_INSTANCES_ROOT@/bin/python\n")
    rendered = storage.render_unit(template)
    assert f"Environment=PORTAL_SECRETS_DIR={tmp_path}/secrets" in rendered
    assert set(instance_paths.portal_paths.KEYS) == {"share_root", "staging_root", "instances_root", "secrets_dir"}


def test_codex_template_and_instance_control(tmp_path, monkeypatch):
    """Codex 模板保留账号目录、socket、代理与内存约束，生命周期只操作指定实例

    :design: decision_7956d7cb4bde, decision_850e6248d680
    :importance: P1
    """
    from types import SimpleNamespace
    from scripts import portal_service_units as units
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path / "instance"))
    from management_portal.execution.account_registrations import create_registration
    create_registration({"account_id": "codex_test", "provider": "codex", "kind": "subscription_login"}, "test")
    monkeypatch.setenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", str(tmp_path / "proxy.env"))
    monkeypatch.setattr(units, "USER_UNIT_ROOT", tmp_path / "units")
    monkeypatch.setattr(shutil, "which", lambda _: "/opt/bin/codex")
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\nUnitFileState=enabled\n")
    monkeypatch.setattr(units, "_run", run)
    target = units.install_codex_unit_template()
    content = target.read_text()
    for part in ("--account-config " + str(tmp_path / "instance/runtime/provider-accounts/codex/%i/launch.json"),
                 str(tmp_path / "instance/sockets/codex-%i.sock"),
                 "EnvironmentFile=" + str(tmp_path / "proxy.env"),
                 "MemoryHigh=32G", "MemoryMax=36G", "MemorySwapMax=1G", "Restart=always"):
        assert part in content
    assert "@PORTAL_" not in content
    assert units.start_codex_instance("codex_test")["pid"] == 123
    units.stop_codex_instance("codex_test")
    units.remove_codex_instance("codex_test")
    assert ("systemctl", "--user", "enable", "--now", "portal-codex-app-server@codex_test.service") in calls
    assert ("systemctl", "--user", "stop", "portal-codex-app-server@codex_test.service") in calls
    assert ("systemctl", "--user", "disable", "--now", "portal-codex-app-server@codex_test.service") in calls
    monkeypatch.setenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")
    assert str(tmp_path / "proxy.env") not in storage.render_unit(units.DEV_ROOT / "config/systemd/portal-codex-app-server@.service.in")
