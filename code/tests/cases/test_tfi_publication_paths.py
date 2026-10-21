"""Publication configuration coverage without publishing or restarting services.

:module: execution.providers
:design: decision_15aab18c0ab3
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from management_common import portal_paths, site_config

    config = tmp_path / "site.json"
    paths = tmp_path / "paths.json"
    paths.write_text(json.dumps({key: str(tmp_path / key) for key in portal_paths.KEYS}))
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", paths)
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", tmp_path)
    monkeypatch.setattr(site_config, "PORTAL_ROOT", tmp_path)
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(config))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(paths))
    monkeypatch.delenv("PORTAL_POSTGRES_ENV_FILE", raising=False)
    for key in portal_paths.KEYS:
        canonical = portal_paths.ENV_KEYS[key]
        monkeypatch.delenv(canonical, raising=False)
        monkeypatch.delenv(canonical.replace("PORTAL_", "ARCHIVE_", 1), raising=False)
    deployment = {
        "source_root": str(tmp_path / "source tree"),
        "release_root": str(tmp_path / "release tree"),
        "postgres_env_file": str(tmp_path / "postgres.env"),
        "runtime_group": "operators",
    }

    def load():
        config.write_text(json.dumps({"deployment": deployment}))
        from scripts import portal_source_layout, portal_release, publish_portal_release
        importlib.reload(portal_source_layout)
        release = importlib.reload(portal_release)
        publish = importlib.reload(publish_portal_release)
        monkeypatch.setattr(publish, "LOG_ROOT", tmp_path / "publication-logs")
        return release, publish

    return SimpleNamespace(load=load, deployment=deployment, config=config, paths=paths, anchor=tmp_path)


def test_configured_roots_preserve_release_manifest_and_validation(configured, monkeypatch):
    release, publish = configured.load()
    root = Path(configured.deployment["release_root"])
    root.mkdir()
    (root / ".git").write_text("gitdir: fixture-only\n")
    assert publish.ROOT == Path(configured.deployment["source_root"])
    assert release.RELEASE_ROOT == root
    assert release.RELEASE_BRANCH == "release"
    commit = "a" * 40
    release.write_manifest(root, commit, changed=["one.py"], message="fixture")
    manifest = release.load_release_manifest()
    assert manifest["shared_source"] == configured.deployment["source_root"]
    assert manifest["release_commit"] == commit
    calls = []
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=commit + "\n", stderr="")
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.require_released_commit(commit) == commit
    assert all(kwargs["cwd"] == root for _, kwargs in calls)
    assert calls[-1][0][-1] == "refs/heads/release"
    with pytest.raises(ValueError, match="完整"):
        release.require_released_commit("short")
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(ValueError):
        release.require_released_commit(commit)


def test_detached_command_forwards_explicit_configuration_and_exit(configured, monkeypatch, capsys):
    _, publish = configured.load()
    monkeypatch.setenv("PORTAL_DATABASE_URL", "fixture-secret-not-forwarded")
    calls = []
    argv = ["--message", "release with spaces", "--path", "a file.py", "--path", "deleted.py"]
    def run(args, **kwargs):
        calls.append((args, kwargs))
        for arg in args:
            if arg.startswith("StandardOutput=append:"):
                Path(arg.partition("append:")[2]).write_text("publication output\n")
            if arg.startswith("StandardError=append:"):
                Path(arg.partition("append:")[2]).write_text("publication error\n")
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(publish.subprocess, "run", run)
    assert publish.detached_publication(argv) == 7
    args, kwargs = calls[0]
    assert args[:4] == ["systemd-run", "--user", "--wait", "--collect"]
    assert kwargs == {"check": False}
    assert f"--working-directory={configured.deployment['source_root']}" in args
    assert f"EnvironmentFile=-{configured.deployment['postgres_env_file']}" in args
    assert f"--setenv=PORTAL_SITE_CONFIG={configured.config}" in args
    assert f"--setenv=PORTAL_PATHS_CONFIG={configured.paths}" in args
    assert args[-len(argv):] == argv
    assert str(Path(configured.deployment["source_root"]) / "scripts/publish_portal_release.py") in args
    assert not any("fixture-secret" in value or "PORTAL_DATABASE_URL=" in value for value in args)
    assert not any("sudo" == value or value.endswith("/sg") for value in args)
    output = capsys.readouterr()
    assert output.out == "publication output\n"
    assert output.err == "publication error\n"


def test_defaults_and_postgres_override_do_not_inject_absent_configuration(configured, monkeypatch):
    configured.deployment.clear()
    release, publish = configured.load()
    assert release.RELEASE_ROOT == release.SHARED_ROOT.parent / "tfi-release"
    monkeypatch.delenv("PORTAL_SITE_CONFIG")
    monkeypatch.delenv("PORTAL_PATHS_CONFIG")
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))
    assert publish.detached_publication([]) == 0
    assert f"EnvironmentFile=-{Path.home() / '.config/tfi/portal-postgres.env'}" in calls[-1]
    assert not any(value.startswith("--setenv=") for value in calls[-1])
    monkeypatch.setenv("PORTAL_POSTGRES_ENV_FILE", "explicit-postgres.env")
    assert publish.detached_publication([]) == 0
    assert f"EnvironmentFile=-{configured.anchor / 'explicit-postgres.env'}" in calls[-1]


def test_relative_explicit_configs_are_fixed_before_detaching(configured, monkeypatch):
    _, publish = configured.load()
    monkeypatch.setenv("PORTAL_SITE_CONFIG", "site.json")
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", "paths.json")
    monkeypatch.chdir(configured.anchor.parent)
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))
    publish.detached_publication([])
    assert f"--setenv=PORTAL_SITE_CONFIG={configured.config}" in calls[0]
    assert f"--setenv=PORTAL_PATHS_CONFIG={configured.paths}" in calls[0]


@pytest.mark.parametrize("name", ["PORTAL_SITE_CONFIG", "PORTAL_PATHS_CONFIG", "PORTAL_POSTGRES_ENV_FILE"])
def test_invalid_explicit_path_rejected_before_process_creation(configured, monkeypatch, name):
    _, publish = configured.load()
    monkeypatch.setenv(name, "/tmp/bad\npath")
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))
    with pytest.raises(ValueError):
        publish.detached_publication([])
    assert not calls


def test_prepare_runtime_uses_configured_source_without_changing_privileges(configured, monkeypatch):
    _, publish = configured.load()
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    publish.prepare_runtime()
    assert calls == [([str(Path(configured.deployment["source_root"]) / "scripts/prepare_portal_release_services")],
                      {"check": True, "stdout": sys.stderr})]


def test_publisher_can_show_help_outside_checkout(configured):
    _, publish = configured.load()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, publish.__file__, "--help"], cwd=configured.anchor,
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "--path" in result.stdout and "--message" in result.stdout


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
