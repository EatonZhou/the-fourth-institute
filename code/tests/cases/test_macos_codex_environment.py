"""Codex survives the clean launchd environment, without exposing credentials.

:module: execution.providers
:profiles: local.fixture
:risk: no-external-side-effects
"""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from scripts import portal_service_units as units, tfi_release_platform_macos as adapter


@pytest.fixture
def definition(tmp_path, monkeypatch):
    from management_common import site_config
    from management_portal.execution import account_hosting
    root = tmp_path
    binary = root / "codex"
    # npm's CLI launcher likewise finds its interpreter through /usr/bin/env.
    binary.write_text("#!/usr/bin/env tfi-fixture-python\n" +
                      (Path(__file__).parent / "fixtures/codex_env_server.py").read_text())
    binary.chmod(0o755)
    tools = root / "bundled tools"
    tools.mkdir()
    (tools / "tfi-fixture-python").symlink_to(sys.executable)
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
    monkeypatch.setattr(units, "instance_root", lambda: root)
    monkeypatch.setattr(site_config, "read_site_config", lambda: {"deployment": {"providers": {"codex_binary": str(binary)}}})
    monkeypatch.setattr(account_hosting, "account_home", lambda *args: root / "account")
    monkeypatch.setattr(account_hosting, "codex_socket_path", lambda *args: root / "codex.sock")
    monkeypatch.setattr(adapter, "unit_directory", lambda: root / "LaunchAgents")
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.defpath)
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.setenv("CODEX_HOME", "/another/account/must-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key-must-not-be-in-plist")
    monkeypatch.setenv("HTTPS_PROXY", "http://fixture-user:fixture-password@127.0.0.1:1")
    return units._native_codex_service("one")


def test_launchd_definition_carries_paths_not_credentials(definition):
    _, _, _, content, _ = definition
    env = plistlib.loads(content.encode())["EnvironmentVariables"]
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] == os.environ["HOME"]
    assert env["PORTAL_PATHS_CONFIG"] == os.environ["PORTAL_PATHS_CONFIG"]
    assert env["PORTAL_NETWORK_CONFIG"]
    assert not {"OPENAI_API_KEY", "CODEX_HOME", "HTTPS_PROXY", "PORTAL_DATABASE_URL"} & env.keys()
    assert "fixture-password" not in content


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable and socket")
def test_clean_launchd_environment_can_start_npm_entry_and_connect(definition):
    from management_portal.codex_app_server_host import connect_transport
    _, _, _, content, _ = definition
    job = plistlib.loads(content.encode())
    with tempfile.TemporaryDirectory(prefix="tfi-codex-", dir="/tmp") as short:
        # macOS has a short AF_UNIX path limit, independent of the test root.
        command = job["ProgramArguments"]
        endpoint = Path(short) / "one.sock"
        command[command.index("--socket") + 1] = str(endpoint)
        old_env = {"PATH": os.defpath, "HOME": str(Path(short) / "home")}
        broken = subprocess.run(command, cwd=job["WorkingDirectory"], env=old_env,
                                capture_output=True, text=True, timeout=10)
        assert broken.returncode != 0
        assert "tfi-fixture-python" in broken.stderr
        assert not endpoint.exists()
        # Execute the actual plist command with only launchd + declared values.
        process = subprocess.Popen(command, cwd=job["WorkingDirectory"],
            env={**old_env, **job["EnvironmentVariables"]}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert endpoint.exists(), process.communicate(timeout=1)[1].decode() if process.poll() is not None else "socket never appeared"
            connection = connect_transport(endpoint, platform="darwin")
            connection.close()
            # Both our connection and the host's own readiness probe completed
            # before testing stop cleanup (not merely a path appearing on disk).
            while not endpoint.with_suffix(".ready").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert endpoint.with_suffix(".ready").exists()
        finally:
            process.terminate()
            process.communicate(timeout=10)
        assert process.returncode == 0
        assert not endpoint.exists()


def test_upgrade_exact_legacy_job_then_retry_without_restart(definition):
    platform, directory, name, content, _ = definition
    current = plistlib.loads(content.encode())
    current.pop("EnvironmentVariables")
    legacy = plistlib.dumps(current, sort_keys=True).decode()
    platform.register_services(directory, {name: legacy})
    stopped = []
    controller = SimpleNamespace(stop=lambda job: stopped.append(job))
    assert units._register_native_codex(platform, directory, name, content, controller) == []
    assert platform.unit_path(directory, name).read_text() == content
    assert stopped == [name]
    units._register_native_codex(platform, directory, name, content, controller)
    assert stopped == [name]
    foreign = current | {"WorkingDirectory": "/other/installation"}
    path = platform.unit_path(directory, name)
    path.write_bytes(plistlib.dumps(foreign, sort_keys=True))
    with pytest.raises(ValueError, match="another launchd job"):
        units._register_native_codex(platform, directory, name, content, controller)
    assert plistlib.loads(path.read_bytes()) == foreign
    assert stopped == [name]


def test_failed_stop_preserves_legacy_definition(definition):
    platform, directory, name, content, _ = definition
    legacy = plistlib.loads(content.encode())
    legacy.pop("EnvironmentVariables")
    before = plistlib.dumps(legacy, sort_keys=True).decode()
    platform.register_services(directory, {name: before})
    def fail_stop(_):
        raise TimeoutError("still running")
    with pytest.raises(TimeoutError):
        units._register_native_codex(platform, directory, name, content, SimpleNamespace(stop=fail_stop))
    assert platform.unit_path(directory, name).read_text() == before
