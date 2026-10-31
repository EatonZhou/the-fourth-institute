"""Release Provider service names and operations must not touch source accounts."""
import json
import subprocess

import pytest

from scripts import portal_service_units as units


def test_isolated_account_uses_owned_namespace_and_bus(tmp_path, monkeypatch):
    context = {"prefix": "tfi-check-codex-app-server@", "root": tmp_path,
               "directory": tmp_path / "units"}
    monkeypatch.setattr(units, "_isolated_codex_context", lambda: context)
    monkeypatch.setattr(units, "_isolated_provider_context", lambda: {"service_prefix": "tfi-check"})
    calls = []
    monkeypatch.setattr(units.subprocess, "run", lambda args, **kw:
                        calls.append((args, kw)) or subprocess.CompletedProcess(args, 0, ""))
    assert units._codex_unit("codex_two") == "tfi-check-codex-app-server@codex_two.service"
    units._run("systemctl", "--user", "start", units._codex_unit("codex_two"))
    assert calls[0][1]["env"]["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")
    with pytest.raises(ValueError, match="source service"):
        units._run("systemctl", "--user", "stop", "portal-codex-app-server@codex_two.service")
    assert len(calls) == 1


def test_owned_template_never_overwrites_source_or_conflicting_unit(tmp_path, monkeypatch):
    from scripts import portal_instance_storage
    context = {"prefix": "tfi-check-codex-app-server@", "root": tmp_path,
               "directory": tmp_path / "units"}
    monkeypatch.setattr(units, "_isolated_codex_context", lambda: context)
    monkeypatch.setattr(portal_instance_storage, "render_unit", lambda _: "[Service]\nExecStart=owned\n")
    monkeypatch.setattr(units, "_run", lambda *a, **k: None)
    path = units.install_codex_unit_template()
    assert path.name == "tfi-check-codex-app-server@.service"
    assert f"HOME={tmp_path / 'home'}" in path.read_text()
    assert units.install_codex_unit_template() == path
    path.write_text("unrelated")
    with pytest.raises(ValueError, match="different Provider"):
        units.install_codex_unit_template()
    assert path.read_text() == "unrelated"
    assert not (context["directory"] / units.CODEX_TEMPLATE).exists()


@pytest.mark.parametrize("locator", ["target.json", "paths.json", "foreign/target.json"])
def test_existing_account_template_accepts_only_equivalent_instance_locator(tmp_path, monkeypatch, locator):
    from scripts import portal_instance_storage
    context = {"prefix": "tfi-check-codex-app-server@", "root": tmp_path,
               "directory": tmp_path / "units"}
    monkeypatch.setattr(units, "_isolated_codex_context", lambda: context)
    monkeypatch.setattr(units, "_run", lambda *a, **k: None)
    active = ["paths.json"]
    monkeypatch.setattr(portal_instance_storage, "render_unit", lambda _: (
        f"[Service]\nEnvironment=PORTAL_PATHS_CONFIG={tmp_path / 'config' / active[0]}\nExecStart=owned\n"))
    path = units.install_codex_unit_template()
    original = path.read_text()
    active[0] = locator
    if locator.startswith("foreign/"):
        with pytest.raises(ValueError, match="different Provider"):
            units.install_codex_unit_template()
    else:
        assert units.install_codex_unit_template() == path
    assert path.read_text() == original


def test_context_rejects_mismatched_code_and_roots(tmp_path, monkeypatch):
    from management_common import portal_paths
    root = tmp_path / "release"
    (root / "config").mkdir(parents=True)
    config = root / "config/paths.json"
    config.write_text("{}")
    target = {"install_root": str(root), "service_prefix": "tfi-check",
              "data_roots": {"secrets_dir": str(root / "accounts")}}
    (root / "installation.json").write_text(json.dumps({"schema": "tfi.release-installation.v1", "target": target}))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(config))
    with pytest.raises(ValueError, match="ownership"):
        units._isolated_codex_context()
    monkeypatch.setattr(units, "SOURCE_ROOT", root / "code")
    monkeypatch.setattr(portal_paths, "path", lambda _: tmp_path / "source-accounts")
    with pytest.raises(ValueError, match="roots"):
        units._isolated_codex_context()
    monkeypatch.setattr(portal_paths, "path", lambda _: root / "accounts")
    assert units._isolated_codex_context()["prefix"] == "tfi-check-codex-app-server@"


@pytest.mark.parametrize("worktree", ["dev", "release", "foreign"])
def test_paired_provider_control_accepts_only_registered_worktrees(tmp_path, monkeypatch, worktree):
    """Dev and Release control the same instance without admitting a third source tree."""
    from management_common import portal_paths
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    dev, release = tmp_path / "dev/code", tmp_path / "release/code"
    target = {"install_root": str(root), "service_prefix": "tfi-check",
              "data_roots": {"secrets_dir": str(root / "accounts")},
              "development": {"dev_code": str(dev), "release_code": str(release)}}
    (root / "installation.json").write_text(json.dumps({"schema": "tfi.release-installation.v1", "target": target}))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(root / "config/paths.json"))
    monkeypatch.setattr(portal_paths, "path", lambda _: root / "accounts")
    monkeypatch.setattr(units, "SOURCE_ROOT", {"dev": dev, "release": release, "foreign": tmp_path / "other"}[worktree])
    if worktree == "foreign":
        with pytest.raises(ValueError, match="ownership"):
            units._isolated_codex_context()
    else:
        assert units._isolated_codex_context()["prefix"] == "tfi-check-codex-app-server@"
