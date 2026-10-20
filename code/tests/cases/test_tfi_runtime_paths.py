"""Deployment parameter coverage without service or Provider activation.

:module: execution.providers
:design: decision_15aab18c0ab3
"""
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from management_common import instance_paths, portal_paths, site_config
from scripts import portal_instance_storage as storage

ROOT = Path(storage.__file__).resolve().parents[1]


@pytest.fixture
def site(tmp_path, monkeypatch):
    values = {key: str(tmp_path / key) for key in portal_paths.KEYS}
    paths = tmp_path / "paths.json"
    paths.write_text(json.dumps(values))
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", paths)
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", tmp_path)
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(paths))
    monkeypatch.setenv("PORTAL_NETWORK_CONFIG", str(tmp_path / "network.env"))
    old = tmp_path / "instances.json"
    old.write_text('{"environment":{}}')
    monkeypatch.setattr(instance_paths, "CONFIG_PATH", old)
    config = tmp_path / "site.json"
    deployment = {
        "source_root": str(tmp_path / "source"),
        "release_root": str(tmp_path / "release"),
        "postgres_env_file": str(tmp_path / "postgres.env"),
        "postgres": {"prefix": str(tmp_path / "postgres"), "base": str(tmp_path / "database")},
        "runtime_group": "operators",
        "providers": {
            "runtime_root": str(tmp_path / "providers"),
            "state_root": str(tmp_path / "updates"),
            "claude_source_root": str(tmp_path / "claude-source"),
            "codex_binary": str(tmp_path / "bin/codex"),
        },
        "auth": {"session_signing_key_path": str(tmp_path / "secret/session.key")},
        "router": {"bind": "127.0.0.2", "port": 19084,
                   "allowed_networks": ["127.0.0.0/8"], "dev_users": ["alice"]},
        "relay": {"bind": "127.0.0.2", "port": 19484, "allowed_ips": ["127.0.0.2"]},
    }
    config.write_text(json.dumps({"deployment": deployment}))
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(config))
    for key in ("PORTAL_DSH_RUNTIME_ROOT", "PORTAL_OPENCODE_RUNTIME_ROOT", "PORTAL_POSTGRES_ENV_FILE", "PORTAL_PROVIDER_EGRESS_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)
    return config, deployment, values


def save(site):
    config, deployment, _ = site
    config.write_text(json.dumps({"deployment": deployment}))


def test_renderer_uses_configured_paths_group_and_preserves_templates(site, tmp_path):
    _, deployment, values = site
    files = [p for p in (ROOT / "systemd").iterdir() if p.suffix in {".service", ".target", ".env", ".conf"} and p.name != "portal-postgresql.service"]
    files += [ROOT / "management_portal/management-portal.service", ROOT / "config/systemd/portal-codex-app-server@.service.in"]
    before = {p: p.read_bytes() for p in files}
    rendered = {p.name: storage.render_unit(p) for p in files}
    for text in rendered.values():
        assert not re.search(r"@PORTAL_[A-Z_]+@", text)
        assert str(Path.home()) not in text
        assert "/srv/agent-staging" not in text
        assert "192.168." not in text and "10.138." not in text
        assert "sg panda" not in text
    assert {p: p.read_bytes() for p in files} == before
    web = rendered["management-portal.service"]
    assert f"WorkingDirectory={deployment['source_root']}" in web
    assert "/usr/bin/sg operators -c 'exec " in web
    assert f"WorkingDirectory={deployment['release_root']}" in rendered["management-portal-release.service"]
    assert f"EnvironmentFile=-{deployment['postgres_env_file']}" in web or f"EnvironmentFile=-{deployment['postgres_env_file']}" in rendered["management-portal-routed.conf"]
    assert f"Environment=PORTAL_SITE_CONFIG={site[0]}" in web
    assert "PORTAL_ROUTER_DEV_USERS=alice" in rendered["portal-user-router.service"]
    # Router access policy now comes from its network configuration file.
    router = rendered["portal-user-router.service"]
    assert f"Environment=PORTAL_NETWORK_CONFIG={site[0].parent / 'network.env'}" in router
    assert "--bind" not in router and "--port" not in router and "--allow-network" not in router
    assert "ALLOWED_IPS=127.0.0.2" in rendered["portal-ai-socket-relay.service"]
    assert f"{deployment['providers']['runtime_root']}/kimi-code-current/bin/kimi" in rendered["portal-kimi-code.env"]
    assert "%i" in rendered["portal-codex-app-server@.service.in"]
    assert "portal-postgresql.service" in rendered["portal-dev-execution-service.service"]


def test_render_defaults_and_invalid_values(site):
    site[1].clear()
    save(site)
    text = storage.render_unit(ROOT / "management_portal/management-portal.service")
    assert "/usr/bin/sg" not in text
    assert "ExecStart=" in text
    router = storage.render_unit(ROOT / "systemd/portal-user-router.service")
    assert "--bind" not in router and "--allow-network" not in router
    assert "PORTAL_NETWORK_CONFIG=" in router
    assert "PORTAL_ROUTER_DEV_USERS=\n" in router
    for key, value in (("runtime_group", "bad;group"), ("source_root", "/tmp/a\nInjected=yes"), ("router", {"allowed_networks": ["not-a-network"]}), ("relay", {"port": True})):
        site[1].clear()
        site[1][key] = value
        save(site)
        with pytest.raises(ValueError):
            storage.render_unit(ROOT / "systemd/portal-user-router.service")


def test_renderer_rejects_unknown_placeholder(site, tmp_path):
    source = tmp_path / "unit.service"
    source.write_text("[Service]\nExecStart=@PORTAL_MISSPELLED_ROOT@/python\n")
    with pytest.raises(ValueError, match="placeholder"):
        storage.render_unit(source)


def test_installer_materializes_provider_environment_without_overwriting_source(site, tmp_path, monkeypatch):
    from scripts import portal_service_units as units
    monkeypatch.setattr(units, "DEV_ROOT", ROOT)
    monkeypatch.setattr(units, "USER_UNIT_ROOT", tmp_path / "units")
    monkeypatch.setattr(units, "DEV_WEB_UNIT_SOURCE", ROOT / "management_portal/management-portal.service")
    monkeypatch.setattr(units, "provider_units", lambda: ("portal-claude-code.service", "portal-kimi-code.service"))
    calls = []
    monkeypatch.setattr(units, "_run", lambda *args, **kwargs: calls.append(args))
    units.install_units()
    materialized = Path(site[2]["instances_root"]) / "systemd"
    for name in ("portal-kimi-code.env", "portal-claude-code.env"):
        assert (materialized / name).is_file()
        assert not re.search(r"@PORTAL_[A-Z_]+@", (materialized / name).read_text())
    assert f"EnvironmentFile={materialized}/portal-kimi-code.env" in (tmp_path / "units/portal-kimi-code.service").read_text()
    assert calls and all(call == ("systemctl", "--user", "daemon-reload") for call in calls)


def test_provider_roots_and_quota_defaults_share_site_paths(site, monkeypatch):
    from management_portal import dsh_client, kimi_code_server, opencode_provider_catalog
    from management_portal.execution import provider_updates
    from scripts import codex_daily_limit_snapshot, codex_weekly_quota_usage
    modules = [dsh_client, kimi_code_server, opencode_provider_catalog, provider_updates, codex_daily_limit_snapshot, codex_weekly_quota_usage]
    for module in modules:
        importlib.reload(module)
    runtime = Path(site[1]["providers"]["runtime_root"])
    assert dsh_client.RUNTIME == runtime / "dsh-runtime-current"
    assert opencode_provider_catalog.RUNTIME == runtime / "opencode-current"
    assert kimi_code_server.DEFAULT_BINARY == runtime / "kimi-code-current/bin/kimi"
    assert provider_updates.RUNTIMES == runtime
    assert provider_updates.STATE_ROOT == Path(site[1]["providers"]["state_root"])
    staging = Path(site[2]["staging_root"])
    assert codex_daily_limit_snapshot.DEFAULT_OUTPUT_ROOT == staging / "shared/codex_limit_usage/daily"
    assert codex_weekly_quota_usage.DEFAULT_OUTPUT_ROOT == staging / "shared/codex_weekly_usage"
    assert codex_weekly_quota_usage.DEFAULT_CODEX_ACCOUNTS_ROOT == Path(site[2]["accounts_root"]) / "codex"
    monkeypatch.setenv("PORTAL_DSH_RUNTIME_ROOT", str(runtime / "explicit"))
    importlib.reload(dsh_client)
    assert dsh_client.RUNTIME == runtime / "explicit"


def test_activation_watch_only_builds_configured_command(site, monkeypatch):
    from scripts import update_provider_runtimes as updater
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 1))
    updater._start_activation_watch()
    assert f"--property=EnvironmentFile={site[1]['postgres_env_file']}" in calls[-1]
    assert f"--setenv=PORTAL_SITE_CONFIG={site[0]}" in calls[-1]
    assert f"--setenv=PORTAL_PATHS_CONFIG={site[0].parent / 'paths.json'}" in calls[-1]
    monkeypatch.setenv("PORTAL_POSTGRES_ENV_FILE", "/explicit/db.env")
    updater._start_activation_watch()
    assert "--property=EnvironmentFile=/explicit/db.env" in calls[-1]


def test_source_root_can_be_configured_outside_cwd(site, monkeypatch):
    from scripts import portal_source_layout
    monkeypatch.chdir(site[0].parent)
    importlib.reload(portal_source_layout)
    assert portal_source_layout.SHARED_ROOT == Path(site[1]["source_root"])
    assert "portal-ai-socket-relay.service" in portal_source_layout.DEV_AUX_UNITS
    assert "portal-postgresql.service" in portal_source_layout.DEV_AUX_UNITS


def test_deployment_cli_is_importable_outside_repo(site, tmp_path):
    import os
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/portal_instance_storage.py"), "--help"],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "install-unit" in result.stdout


def test_portable_provider_defaults_and_explicit_existing_paths(site):
    from management_portal.execution import provider_updates
    from management_portal import opencode_provider_catalog
    site[1]["providers"].clear()
    save(site)
    importlib.reload(provider_updates)
    importlib.reload(opencode_provider_catalog)
    staging = Path(site[2]["staging_root"])
    assert provider_updates.RUNTIMES == staging / "provider-runtimes"
    assert provider_updates.STATE_ROOT == staging / "provider-update-state"
    assert opencode_provider_catalog.RUNTIME == provider_updates.RUNTIMES / "opencode-current"
    site[1]["providers"].update(runtime_root="existing/runtime", state_root="existing/state")
    save(site)
    importlib.reload(provider_updates)
    assert provider_updates.RUNTIMES == site[0].parent / "existing/runtime"
    assert provider_updates.STATE_ROOT == site[0].parent / "existing/state"
    assert not provider_updates.RUNTIMES.exists() and not provider_updates.STATE_ROOT.exists()
