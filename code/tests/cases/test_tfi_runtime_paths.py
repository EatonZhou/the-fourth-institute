"""Deployment parameter coverage without service or Provider activation.

:module: execution.providers
:design: decision_15aab18c0ab3
"""
import importlib
import json
from pathlib import Path
import re
import subprocess

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


def test_renderer_uses_configured_paths_and_preserves_templates(site, tmp_path):
    _, deployment, values = site
    files = [p for p in (ROOT / "systemd").iterdir() if p.suffix in {".service", ".env"}]
    files.append(ROOT / "config/systemd/portal-codex-app-server@.service.in")
    before = {p: p.read_bytes() for p in files}
    rendered = {p.name: storage.render_unit(p) for p in files}
    for text in rendered.values():
        assert not re.search(r"@PORTAL_[A-Z_]+@", text)
        assert str(Path.home()) not in text
        assert "/srv/agent-staging" not in text
        assert "192.168." not in text and "10.138." not in text
    assert {p: p.read_bytes() for p in files} == before
    claude = rendered["portal-claude-code.service"]
    assert f"WorkingDirectory={deployment['providers']['claude_source_root']}" in claude
    assert f"EnvironmentFile=-{deployment['postgres_env_file']}" in claude
    assert f"Environment=PORTAL_SITE_CONFIG={site[0]}" in claude
    assert f"Environment=PORTAL_NETWORK_CONFIG={site[0].parent / 'network.env'}" in claude
    assert f"{deployment['providers']['runtime_root']}/claude-agent-sdk-current/bin/python" in claude
    codex = rendered["portal-codex-app-server@.service.in"]
    assert "%i" in codex and deployment["providers"]["codex_binary"] in codex


def test_render_defaults_and_invalid_values(site):
    site[1].clear()
    save(site)
    text = storage.render_unit(ROOT / "systemd/portal-claude-code.service")
    assert "ExecStart=" in text
    assert "PORTAL_NETWORK_CONFIG=" in text
    for key, value in (("source_root", "/tmp/a\nInjected=yes"), ("providers", {"runtime_root": "/tmp/a b"}),
                       ("providers", ["not-an-object"])):
        site[1].clear()
        site[1][key] = value
        save(site)
        with pytest.raises(ValueError):
            storage.render_unit(ROOT / "systemd/portal-claude-code.service")


def test_renderer_rejects_unknown_placeholder(site, tmp_path):
    source = tmp_path / "unit.service"
    source.write_text("[Service]\nExecStart=@PORTAL_MISSPELLED_ROOT@/python\n")
    with pytest.raises(ValueError, match="placeholder"):
        storage.render_unit(source)


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
