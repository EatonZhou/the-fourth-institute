"""Provider bootstrap uses the same platform link adapter as updates."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare_agent_runtimes.py"


def test_initial_runtime_link_uses_provider_adapter(tmp_path, monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    link = tmp_path / "current runtime"
    package = tmp_path / "DSH package"
    package.mkdir()
    calls = []
    monkeypatch.setitem(module["_ensure_runtime_link"].__globals__, "_link",
                        lambda current, target: calls.append((current, target)))
    definition = SimpleNamespace(label="DSH", updater=lambda: SimpleNamespace(
        link=lambda: link, link_target=lambda root: root))

    module["_ensure_runtime_link"](definition, package)
    assert calls == [(link, package)]

    link.mkdir()
    with pytest.raises(RuntimeError, match="non-link"):
        module["_ensure_runtime_link"](definition, package)


def test_windows_npm_launcher_is_explicit_cmd(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    globals_ = module["npm_executable"].__globals__
    monkeypatch.setitem(globals_, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(globals_, "shutil", SimpleNamespace(which=lambda name: (
        r"C:\\Program Files\\nodejs\\npm.cmd" if name == "npm.cmd" else None)))
    assert module["npm_executable"]() == r"C:\\Program Files\\nodejs\\npm.cmd"
