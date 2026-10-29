"""First Claude onboarding owns its daemon, independently of a reusable SDK.

:module: execution.providers
"""
import json
import socket
import subprocess
import threading
import tempfile
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_common import portal_paths
from management_portal.execution import account_setup as setup, provider_updates as updates
from management_portal.hatg.contracts import InstructionError
from scripts import portal_service_units as units

REAL_RUN = subprocess.run


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux service contract")
@pytest.mark.parametrize("directory", ["package", "package with space %"])
def test_generated_claude_unit_is_accepted_by_systemd(control, directory):
    import shutil
    analyzer = shutil.which("systemd-analyze")
    if not analyzer:
        pytest.skip("systemd-analyze is not installed")
    interpreter = control.sdk / "bin/python"
    interpreter.unlink()
    interpreter.symlink_to(sys.executable)
    package = control.root / directory
    package.mkdir()
    target = units.install_claude_unit(control.sdk, package)
    result = REAL_RUN([analyzer, "--user", "verify", "--man=no", str(target)],
                      text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      timeout=15)
    assert result.returncode == 0, result.stdout


@pytest.fixture
def control(monkeypatch, tmp_path):
    root = tmp_path / "instance"
    root.mkdir()
    stage = tmp_path / "staging"
    stage.mkdir()
    context = {"root": root, "directory": tmp_path / "units", "service_prefix": "tfi-new"}
    target = {"install_root": str(root), "service_prefix": "tfi-new", "run_id": "fixture",
              "data_roots": {"staging_root": str(stage)}, "database_name": "new_database",
              "ports": {"web": 7000, "postgres": 7001, "nats": 7002}}
    (root / "installation.json").write_text(json.dumps({
        "schema": "tfi.release-installation.v1", "target": target}))
    monkeypatch.setattr(units, "_isolated_provider_context", lambda: context)
    monkeypatch.setattr(portal_paths, "path", lambda key: stage if key == "staging_root" else root / key)
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path / "runtimes")
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path / "state")
    sdk = tmp_path / "external-sdk"
    (sdk / "bin").mkdir(parents=True)
    (sdk / "bin/python").write_text("external SDK remains unmodified")
    monkeypatch.setattr(updates.ClaudeRuntime, "link", lambda self: sdk)
    monkeypatch.setattr(updates.ClaudeRuntime, "installed", lambda self: ("0.2.174",))
    install = Mock(side_effect=AssertionError("must reuse SDK without downloading"))
    monkeypatch.setattr(updates.ClaudeRuntime, "stage", install)
    monkeypatch.setattr(updates.ClaudeRuntime, "available", install)
    commands = []
    active = []
    state = SimpleNamespace(root=root, stage=stage, context=context, sdk=sdk, commands=commands,
                            active=active, dropins="", install=install, on_start=lambda: None)

    def run(args, **kwargs):
        if args[0] == "uname":
            return subprocess.CompletedProcess(args, 0, stdout="x86_64" if kwargs.get("text") else b"x86_64")
        commands.append(args)
        if args[0] == "systemctl":
            assert not any(a.startswith("portal-") for a in args)
            assert "DBUS_SESSION_BUS_ADDRESS" in kwargs["env"]
            if "show" in args:
                name = args[3]
                out = f"FragmentPath={context['directory'] / name}\nDropInPaths={state.dropins}\n"
                out += f"ActiveState={'active' if active else 'inactive'}\n"
            else:
                out = ""
                if "--now" in args or "start" in args:
                    active.append(True)
                    state.on_start()
        else:
            # Only the SDK's package import probe is external to this fixture.
            assert args[0] == str(sdk / "bin/python")
            assert "claude_code_daemon" in " ".join(args)
            out = ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(units.subprocess, "run", run)
    return state


@pytest.fixture
def check(monkeypatch, control):
    row = {"account_id": "first", "provider": "claude", "kind": "subscription_login",
           "route_id": "first", "config": {}, "version": 1, "enabled": True}
    monkeypatch.setattr(setup, "account", lambda _: row)
    monkeypatch.setattr(setup, "signature", lambda _: {"registration_version": 1})
    monkeypatch.setattr(updates, "runtime_available", lambda _: True)
    monkeypatch.setattr(setup, "catalog", lambda _: {"default_model": "sonnet"})
    definition = Mock()
    definition.operations = {"turn"}
    definition.runtime_adapter.return_value.defaults.return_value = {}
    monkeypatch.setitem(setup.AGENT_PROVIDERS, "claude", definition)
    client = Mock()
    client.submit.return_value.execution_id = "existing-durable-owner"
    monkeypatch.setattr(setup, "ExecutionClient", lambda: client)
    inv = SimpleNamespace(caller="fixture", argument={"account_id": "first", "model": "sonnet"}.get)
    return inv, client


def test_check_installs_only_own_daemon_before_execution(control, check, monkeypatch):
    from management_portal.claude_code_client import ClaudeCodeClient
    inv, execution = check
    observed = []

    def health(client):
        observed.append(client.socket_path)
        assert control.active, "SDK presence did not start the daemon"
        return {"backend": "claude", "protocol_version": 1, "status": "degraded"}

    monkeypatch.setattr(ClaudeCodeClient, "health", health)
    result = setup.check(inv)
    assert observed == [control.stage / "portal_locks/claude-code-daemon.sock"]
    assert result["execution_id"] == "existing-durable-owner"
    execution.submit.assert_called_once()
    unit = control.context["directory"] / "tfi-new-claude-code.service"
    content = unit.read_text()
    assert str(control.sdk / "bin/python") in content
    assert str(control.root / "config/target.json") in content
    assert "new_database" in content
    assert str(observed[0]) in content
    assert not (updates.RUNTIMES / "claude-agent-sdk-current").exists()
    setup.check(inv)
    assert len([cmd for cmd in control.commands if cmd[0] != "systemctl"]) == 1
    assert len([cmd for cmd in control.commands if "--now" in cmd]) == 1


def test_check_never_queues_on_wrong_socket_protocol(control, check, monkeypatch):
    from management_portal.claude_code_client import ClaudeCodeClient
    inv, execution = check
    monkeypatch.setenv("CLAUDE_CODE_SOCKET", "/old/healthy.sock")
    seen = []
    monkeypatch.setattr(ClaudeCodeClient, "health", lambda client: seen.append(client.socket_path) or
                        {"backend": "kimi", "protocol_version": 1})
    ticks = iter([0, 31, 32, 33])
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(ticks))
    with pytest.raises(InstructionError) as error:
        setup.check(inv)
    assert error.value.details["reason"] == "account.service_not_ready"
    assert seen == [control.stage / "portal_locks/claude-code-daemon.sock"]
    execution.submit.assert_not_called()


def test_real_socket_is_awaited_before_durable_submit(control, check, monkeypatch):
    inv, execution = check
    received = []
    # Unix sockets have a short path limit; pytest's Portal workspace is long.
    with tempfile.TemporaryDirectory(prefix="claude-ready-", dir="/tmp") as directory:
        stage = Path(directory)
        monkeypatch.setattr(portal_paths, "path", lambda key: stage)
        address = stage / "portal_locks/claude-code-daemon.sock"
        address.parent.mkdir()

        def serve():
            time.sleep(0.03)
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(address))
                server.listen(1)
                server.settimeout(3)
                with server.accept()[0] as connection:
                    request = json.loads(connection.makefile("rb").readline())
                    received.append(request["method"])
                    reply = {"id": request["id"], "type": "result", "result": {
                        "backend": "claude", "protocol_version": 1, "status": "degraded"}}
                    connection.sendall(json.dumps(reply).encode() + b"\n")

        thread = threading.Thread(target=serve)
        control.on_start = thread.start
        try:
            setup.check(inv)
        finally:
            if thread.ident:
                thread.join(timeout=4)
        assert received == ["health"]
        execution.submit.assert_called_once()


def test_missing_socket_times_out_without_execution(control, check, monkeypatch):
    inv, execution = check
    original = setup._started_claude_catalog
    monkeypatch.setattr(setup, "_started_claude_catalog", lambda row: original(row, timeout=0.02))
    with pytest.raises(InstructionError) as error:
        setup.check(inv)
    assert error.value.details["reason"] == "account.service_not_ready"
    assert error.value.retryable
    execution.submit.assert_not_called()


def test_concurrent_first_start_installs_and_starts_once(control):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: units.start_claude_service(), range(2)))
    assert len([cmd for cmd in control.commands if cmd[0] != "systemctl"]) == 1
    assert len([cmd for cmd in control.commands if "--now" in cmd]) == 1
    assert (control.sdk / "bin/python").read_text() == "external SDK remains unmodified"


def test_foreign_dropin_rejected_before_new_start(control):
    target = units.install_claude_backend(app_server_only=True)
    before = target.read_bytes()
    control.dropins = "/another/override.conf"
    control.commands.clear()
    with pytest.raises(ValueError, match="foreign definition"):
        units.start_claude_service()
    assert not any("--now" in command for command in control.commands)
    assert target.read_bytes() == before


def test_same_package_version_cannot_be_overwritten(control):
    target = units.install_claude_backend(app_server_only=True)
    current = updates.RUNTIMES / "claude-app-server-current"
    module = current / "management_portal/claude_code_daemon.py"
    module.write_text("immutable earlier build\n")
    before = target.read_bytes()
    with pytest.raises(ValueError, match="immutable Claude"):
        units.install_claude_backend(app_server_only=True)
    assert target.read_bytes() == before
    assert module.read_text() == "immutable earlier build\n"


def test_missing_sdk_only_mode_does_not_download(control, monkeypatch):
    monkeypatch.setattr(updates.ClaudeRuntime, "installed", lambda self: ("0",))
    with pytest.raises(RuntimeError, match="SDK runtime is missing"):
        units.install_claude_backend(app_server_only=True)
    control.install.assert_not_called()
    assert not control.commands


def test_standard_install_stages_only_missing_sdk(control, monkeypatch):
    monkeypatch.setattr(updates.ClaudeRuntime, "installed", lambda self: ("0",))
    monkeypatch.setattr(updates.ClaudeRuntime, "available", lambda self: ("0.2.174",))
    staged = Mock(return_value=control.sdk)
    monkeypatch.setattr(updates.ClaudeRuntime, "stage", staged)
    target = units.install_claude_backend()
    staged.assert_called_once_with(("0.2.174",), root=None)
    link = updates.RUNTIMES / "claude-agent-sdk-current"
    assert link.resolve() == control.sdk
    assert str(link / "bin/python") in target.read_text()


def test_start_failure_is_retryable_and_does_not_queue(control, check, monkeypatch):
    inv, execution = check
    original = units._run

    def fail(*args, **kwargs):
        if "--now" in args:
            raise subprocess.CalledProcessError(1, args, output="private service details")
        return original(*args, **kwargs)

    monkeypatch.setattr(units, "_run", fail)
    with pytest.raises(InstructionError) as error:
        setup.check(inv)
    assert error.value.details["reason"] == "account.service_start_failed"
    assert error.value.retryable
    assert "private service details" not in str(error.value)
    execution.submit.assert_not_called()


@pytest.mark.parametrize("first_unit_failure", [False, True])
def test_real_import_package_is_repeatable_after_install_failure(control, monkeypatch, first_unit_failure):
    from scripts import claude_app_server_package

    def build(source, destination):
        module = destination / "management_portal"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        version = (source / "management_portal/claude_app_server_version.txt").read_text().strip()
        (module / "claude_code_daemon.py").write_text(
            f"APP_SERVER_VERSION = {version!r}\nAPP_SERVER_PROTOCOL_VERSION = 1\n")

    monkeypatch.setattr(claude_app_server_package, "build_package", build)
    python = control.sdk / "bin/python"
    python.unlink()
    python.symlink_to(sys.executable)
    control_run = units.subprocess.run

    def run(args, **kwargs):
        return control_run(args, **kwargs) if args[0] == "systemctl" else REAL_RUN(args, **kwargs)

    monkeypatch.setattr(units.subprocess, "run", run)
    install = units.install_claude_unit
    if first_unit_failure:
        monkeypatch.setattr(units, "install_claude_unit", Mock(side_effect=OSError("fixture unit failure")))
        with pytest.raises(OSError, match="fixture unit failure"):
            units.install_claude_backend(app_server_only=True)
        monkeypatch.setattr(units, "install_claude_unit", install)
    else:
        units.install_claude_backend(app_server_only=True)
    units.install_claude_backend(app_server_only=True)
    package = updates.RUNTIMES / "claude-app-server-current"
    assert not list(package.rglob("*.pyc"))
    assert not list(package.rglob("__pycache__"))


def test_foreign_unit_rejected_without_start_or_submission(control, check):
    inv, execution = check
    control.context["directory"].mkdir()
    unit = control.context["directory"] / "tfi-new-claude-code.service"
    unit.write_text("Environment=HOME=/another/home\n")
    with pytest.raises(InstructionError) as error:
        setup.check(inv)
    assert error.value.details["reason"] == "account.service_start_failed"
    assert unit.read_text() == "Environment=HOME=/another/home\n"
    assert control.commands == []
    execution.submit.assert_not_called()
