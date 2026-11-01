"""Codex survives the clean launchd environment, without exposing credentials.

A launch agent carries only the instance selection; the launcher derives the
code-owned Codex entry, its Node runtime and the registered account ID.

:module: execution.providers
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time

import pytest

from scripts import tfi_release_platform_macos as adapter

COMPONENT = "codex-app-server@one"


@pytest.fixture
def account(tmp_path, monkeypatch):
    from management_common import code_environment
    from management_common.instance_manifest import load
    from tests.cases.provider_service_test_support import provider_instance, real_provider_links
    links = real_provider_links()
    root, runtimes = provider_instance(monkeypatch, tmp_path, components=(COMPONENT,))
    # npm's CLI launcher finds its interpreter through /usr/bin/env node; the
    # stand-in "node" is this Python, provided as the code's native Node.
    tools = tmp_path / "native node"
    tools.mkdir()
    (tools / "node").symlink_to(sys.executable)
    real_native = code_environment.native_binary
    monkeypatch.setattr(code_environment, "native_binary",
                        lambda code, package: tools / "node" if package == "node" else real_native(code, package))
    binary = runtimes / "codex"
    binary.write_text("#!/usr/bin/env node\n" + (Path(__file__).parent / "fixtures/codex_env_server.py").read_text())
    binary.chmod(0o755)
    home = tmp_path / "account"
    home.mkdir()
    from management_portal.execution import account_registrations
    from management_common.portal_database import portal_database_url
    account_registrations.create_registration({"account_id": "one", "provider": "codex", "route_id": "clean_launch",
        "kind": "subscription_login", "config": {"account_dir": str(home)}}, "test")
    # The child is a real clean service process, reading only this fixture DB.
    import shlex
    (root / "secrets/postgres.env").write_text(
        "PORTAL_DATABASE_URL=" + shlex.quote(portal_database_url()) + "\n"
        + "PORTAL_DATABASE_NAMESPACE=" + shlex.quote(os.environ["PORTAL_DATABASE_NAMESPACE"]) + "\n")
    monkeypatch.setenv("PATH", os.defpath)
    monkeypatch.setenv("CODEX_HOME", "/another/account/must-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key-must-not-be-in-definitions")
    yield load(root)
    assert real_provider_links() == links


def test_launchd_definition_carries_only_the_instance(account):
    content = adapter.manifest_unit_text(account, COMPONENT)
    job = plistlib.loads(content.encode())
    assert job["EnvironmentVariables"] == {"PORTAL_INSTANCE_ROOT": str(account.root)}
    assert job["ProgramArguments"][1:] == ["-m", "management_portal.service_runtime", "--component", COMPONENT]
    assert "fixture-key" not in content and "must-not-inherit" not in content


def test_launcher_uses_code_owned_entry_and_registered_account(account):
    from management_common.code_environment import provider_entry
    from management_portal.service_runtime import component_command
    command, environment = component_command(account, COMPONENT)
    entry = provider_entry("codex_binary", account)
    assert command[command.index("--binary") + 1] == str(entry) and entry.is_relative_to(account.root.parent)
    assert command[command.index("--account-id") + 1] == "one"
    assert not (account.root / "runtime/provider-accounts").exists()
    assert not {"CODEX_HOME", "OPENAI_API_KEY"} & environment.keys()
    assert environment["PATH"].split(os.pathsep)[0].endswith("native node")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable and socket")
def test_clean_launchd_environment_can_start_npm_entry_and_connect(account):
    from management_portal.service_runtime import component_command, working_directory
    from management_portal.codex_app_server_host import connect_transport
    command, environment = component_command(account, COMPONENT)
    with tempfile.TemporaryDirectory(prefix="tfi-codex-", dir="/tmp") as short:
        # macOS has a short AF_UNIX path limit, independent of the test root.
        endpoint = Path(short) / "one.sock"
        command[command.index("--socket") + 1] = str(endpoint)
        cwd = working_directory(account, COMPONENT)
        broken = subprocess.run(command, cwd=cwd, env={**environment, "PATH": os.defpath},
                                capture_output=True, text=True, timeout=20)
        assert broken.returncode != 0 and not endpoint.exists()
        process = subprocess.Popen(command, cwd=cwd, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 20
            while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert endpoint.exists(), process.communicate(timeout=1)[1].decode() if process.poll() is not None else "socket never appeared"
            connect_transport(endpoint, platform="darwin").close()
            # The host binds Codex to a hidden startup name and renames it to the
            # endpoint once ready; the stand-in marks the name it bound.
            ready = lambda: [path for path in Path(short).iterdir() if path.suffix == ".ready"]
            while not ready() and time.monotonic() < deadline:
                time.sleep(.02)
            assert ready()
        finally:
            process.terminate()
            process.communicate(timeout=10)
        assert process.returncode == 0
        assert not endpoint.exists()
