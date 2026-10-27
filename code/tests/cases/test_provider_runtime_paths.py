"""Configured Provider entry paths are shared by startup and activation.

:module: operations.deployment
:design: decision_e587fe1c472e
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import importlib

import pytest

from management_common.provider_runtime_paths import runtime_entry
from management_portal.execution import provider_updates as updates
from management_portal.opencode_runtime import OpenCodeRuntime


def test_configured_entries_keep_version_links_and_are_used_by_every_updater(monkeypatch, tmp_path):
    selected = tmp_path / "versions" / "selected"
    selected.mkdir(parents=True)
    settings = {}
    cases = [("codex_binary", updates.CodexRuntime()),
             ("claude_runtime_current", updates.ClaudeRuntime()),
             ("kimi_runtime_current", updates.KimiRuntime()),
             ("dsh_runtime_current", updates.DshRuntime()),
             ("opencode_runtime_current", OpenCodeRuntime())]
    for key, _ in cases:
        link = tmp_path / "entries" / key
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(selected, target_is_directory=True)
        settings[key] = str(link)
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", settings)
    monkeypatch.delenv("PORTAL_CLAUDE_RUNTIME_ROOT", raising=False)
    for key, runtime in cases:
        assert runtime.link() == Path(settings[key])
        assert runtime.activation_link() == Path(settings[key])
        assert runtime.link() != selected


@pytest.mark.parametrize("value", ["", "relative/current", "/tmp/invalid\ncurrent", None, 3])
def test_invalid_explicit_entry_does_not_fall_back(value, tmp_path):
    with pytest.raises(ValueError, match="absolute path"):
        runtime_entry({"kimi_runtime_current": value}, "kimi_runtime_current", tmp_path / "default")


def test_runtime_clients_use_the_same_explicit_entries(monkeypatch, tmp_path):
    from management_common import site_config
    from management_portal import dsh_client, kimi_code_server, opencode_provider_catalog
    settings = {key: str(tmp_path / key) for key in (
        "dsh_runtime_current", "kimi_runtime_current", "opencode_runtime_current")}
    modules = (dsh_client, kimi_code_server, opencode_provider_catalog)
    with monkeypatch.context() as patch:
        patch.setattr(site_config, "read_site_config", lambda: {"deployment": {"providers": settings}})
        patch.delenv("PORTAL_DSH_RUNTIME_ROOT", raising=False)
        patch.delenv("PORTAL_OPENCODE_RUNTIME_ROOT", raising=False)
        try:
            for module in modules:
                importlib.reload(module)
            assert dsh_client.RUNTIME == Path(settings["dsh_runtime_current"])
            suffix = "kimi.cmd" if updates.sys.platform == "win32" else "bin/kimi"
            assert kimi_code_server.DEFAULT_BINARY == Path(settings["kimi_runtime_current"]) / suffix
            assert opencode_provider_catalog.RUNTIME == Path(settings["opencode_runtime_current"])
        finally:
            patch.undo()
            for module in modules:
                importlib.reload(module)
