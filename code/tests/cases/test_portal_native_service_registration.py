from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path

import pytest

from scripts import portal_service_units as units


@pytest.mark.parametrize("platform", ["windows", "macos"])
def test_native_definition_preserves_paths_with_spaces(monkeypatch, tmp_path, platform):
    from scripts import tfi_release_platform_macos, tfi_release_platform_windows

    adapter = tfi_release_platform_windows if platform == "windows" else tfi_release_platform_macos
    root = tmp_path / "Portal instances"
    source = tmp_path / "Portal source"
    values = {"PORTAL_SOURCE_ROOT": str(source), "PORTAL_INSTANCES_ROOT": str(root),
              "PORTAL_RELEASE_ROOT": str(tmp_path / "Portal release"),
              "PORTAL_POSTGRES_ENV_FILE": str(tmp_path / "postgres config.env")}
    env = {"PORTAL_SITE_CONFIG": str(tmp_path / "site config.json")}
    result = units._native_definition("portal-dev-resource-service.service", platform,
                                      adapter=adapter, directory=tmp_path, values=values,
                                      site_env=env, account="example\\portal-user")
    if platform == "windows":
        definition = json.loads(result)
        assert definition["command"][0] == str(root / "dev/runtime-venv/Scripts/python.exe")
        assert definition["environment"]["PORTAL_SITE_CONFIG"] == env["PORTAL_SITE_CONFIG"]
        assert definition["env_files"]
        assert definition["cwd"] == str(source)
    else:
        definition = plistlib.loads(result.encode("utf-8"))
        args = definition["ProgramArguments"]
        assert str(root / "dev/runtime-venv/bin/python") in args
        assert definition["EnvironmentVariables"]["PORTAL_SITE_CONFIG"] == env["PORTAL_SITE_CONFIG"]
        assert definition["WorkingDirectory"] == str(source)


def test_installer_commands_have_finite_timeout_and_owned_tree(monkeypatch, tmp_path):
    from scripts import tfi_release_install as installer

    calls = []
    monkeypatch.setattr(installer, "clean_environment", lambda target, **kwargs: {})

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(installer, "run_utf8_capture", fake_run)
    with pytest.raises(TimeoutError, match="process tree was stopped"):
        installer.run_checked(["tool"], target={"install_root": str(tmp_path)})
    assert calls[0]["timeout"] == 1800


@pytest.mark.parametrize("platform", ["windows", "macos"])
def test_native_pool_generates_every_managed_service(monkeypatch, tmp_path, platform):
    from management_common.site_config import read_site_config
    from scripts.portal_instance_storage import _deployment_values, site_environment
    from scripts import tfi_release_platform_macos, tfi_release_platform_windows

    adapter = tfi_release_platform_windows if platform == "windows" else tfi_release_platform_macos
    deployment = read_site_config().get("deployment", {})
    values = _deployment_values(deployment)
    values["PORTAL_INSTANCES_ROOT"] = str(tmp_path / "instance state")
    monkeypatch.setattr(units.shutil, "which", lambda binary: str(tmp_path / "nats-server"))
    for instance, layout in units.INSTANCE_LAYOUT.items():
        names = (*layout["services"], layout["web"], "portal-user-router.service")
        if instance == "dev":
            names += ("portal-dev-source-reloader.service",)
        for name in names:
            definition = units._native_definition(name, platform, adapter=adapter,
                                                  directory=tmp_path, values=values,
                                                  site_env=site_environment(deployment),
                                                  account="example\\portal-user")
            if platform == "windows":
                assert json.loads(definition)["command"]
            else:
                assert plistlib.loads(definition.encode())["ProgramArguments"]


def test_management_start_prepares_native_service_pool(monkeypatch):
    from scripts import portal_services

    calls = []
    monkeypatch.setattr(portal_services, "_controller",
                        lambda: type("Controller", (), {"platform": "windows"})())
    monkeypatch.setattr(units, "install_native_units",
                        lambda instance, platform: calls.append((instance, platform)))
    with pytest.raises(RuntimeError, match="standalone|Standalone|独立"):
        portal_services._prepare_units("release")
    assert calls == []


def test_native_environment_file_is_loaded_at_service_start(tmp_path):
    from scripts.portal_native_service_child import load_environment_files

    path = tmp_path / "private settings.env"
    path.write_text("# comment\nexport PORTAL_DATABASE_URL='postgresql://host/db name'\n"
                    "PORTAL_DATA_PATH='C:\\Program Files\\Portal'\n", encoding="utf-8")
    result = load_environment_files([path], {"UNCHANGED": "yes"})
    assert result["PORTAL_DATABASE_URL"] == "postgresql://host/db name"
    assert result["PORTAL_DATA_PATH"] == r"C:\Program Files\Portal"
    assert result["UNCHANGED"] == "yes"
