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
    package = updates.RUNTIMES / 'claude-app-server-current'
    selected = control.root / directory
    selected.mkdir()
    package.symlink_to(selected, target_is_directory=True)
    target = units.install_claude_unit(control.sdk, package)
    result = REAL_RUN([analyzer, "--user", "verify", "--man=no", str(target)],
                      text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      timeout=15)
    assert result.returncode == 0, result.stdout


@pytest.fixture
def control(monkeypatch, tmp_path):
    from scripts import instance_services, tfi_release_platform_linux as linux
    from tests.cases.provider_service_test_support import FakeControl, provider_instance, real_provider_links
    links = real_provider_links()
    # First onboarding: claude-code is not yet a registered component.
    root, runtimes = provider_instance(monkeypatch, tmp_path)
    directory = tmp_path / 'units'
    monkeypatch.setattr(instance_services, 'platform_name', lambda: 'linux')
    monkeypatch.setattr(linux, 'unit_directory', lambda: directory)
    service = FakeControl(directory)
    monkeypatch.setattr(instance_services, 'controller', lambda instance: service)
    context = {'directory': directory}
    stage = root / 'runtime'
    installed_sdk = runtimes / 'claude-agent-sdk-0.2.174'
    (installed_sdk / 'bin').mkdir(parents=True)
    (installed_sdk / 'bin/python').write_text('SDK remains unmodified')
    sdk = runtimes / 'claude-agent-sdk-current'
    sdk.symlink_to(installed_sdk, target_is_directory=True)
    monkeypatch.setattr(updates.ClaudeRuntime, 'installed', lambda self: ('0.2.174',))
    install = Mock(side_effect=AssertionError("must reuse SDK without downloading"))
    monkeypatch.setattr(updates.ClaudeRuntime, "stage", install)
    monkeypatch.setattr(updates.ClaudeRuntime, "available", install)
    commands = []
    state = SimpleNamespace(root=root, stage=stage, context=context, sdk=sdk, commands=commands,
                            service=service, install=install, on_start=lambda: None)

    def run(args, **kwargs):
        if args[0] == "uname":
            return subprocess.CompletedProcess(args, 0, stdout="x86_64" if kwargs.get("text") else b"x86_64")
        commands.append(args)
        if args[0] == "systemctl":
            assert not any(a.startswith("portal-") for a in args)
            if "--now" in args:
                # Service control goes through the instance's registered-unit guard.
                assert "DBUS_SESSION_BUS_ADDRESS" in kwargs["env"]
                service.active.add(args[-1])
                state.on_start()
        else:
            # Only the SDK's package import probe is external to this fixture.
            assert Path(args[0]).resolve() == (sdk / "bin/python").resolve()
            assert "claude_code_daemon" in " ".join(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(units.subprocess, "run", run)
    yield state
    assert real_provider_links() == links


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
        assert control.service.active, "SDK presence did not start the daemon"
        return {"backend": "claude", "protocol_version": 1, "status": "degraded"}

    monkeypatch.setattr(ClaudeCodeClient, "health", health)
    result = setup.check(inv)
    assert observed == [control.stage / "s/claude-code-daemon.sock"]
    assert result["execution_id"] == "existing-durable-owner"
    execution.submit.assert_called_once()
    unit = control.context["directory"] / "tfi-fixture-claude-code.service"
    content = unit.read_text()
    assert 'management_portal.service_runtime' in content
    assert 'PORTAL_INSTANCE_ROOT=' + str(control.root) in content
    assert [line for line in content.splitlines() if line.startswith('Environment=')] == [
        'Environment=' + json.dumps('PORTAL_INSTANCE_ROOT=' + str(control.root))]
    assert (control.sdk / 'bin/python').read_text() == 'SDK remains unmodified'
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
    assert seen == [control.stage / "s/claude-code-daemon.sock"]
    execution.submit.assert_not_called()


def test_real_socket_is_awaited_before_durable_submit(control, check, monkeypatch):
    inv, execution = check
    received = []
    # Unix sockets have a short path limit; pytest's Portal workspace is long.
    with tempfile.TemporaryDirectory(prefix="claude-ready-", dir="/tmp") as directory:
        stage = Path(directory)
        from management_portal import claude_code_client
        monkeypatch.setattr(claude_code_client, 'portal_path', lambda key: stage)
        address = stage / "s/claude-code-daemon.sock"
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
    assert (control.sdk / "bin/python").read_text() == "SDK remains unmodified"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_native_first_start_prepares_daemon_before_start(control, monkeypatch, platform):
    # A native distribution may have an SDK without a packaged daemon. First
    # onboarding must use the same package/validation owner as standard install.
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform=platform))
    install = Mock(side_effect=lambda **kwargs: units._define("claude-code"))
    monkeypatch.setattr(units, "_install_claude_backend", install)
    start = control.service.start

    def start_after_install(*names):
        install.assert_called_once_with(app_server_only=True)
        start(*names)

    monkeypatch.setattr(control.service, "start", start_after_install)
    units.start_claude_service()
    units.start_claude_service()
    install.assert_called_once_with(app_server_only=True)
    assert len([call for call in control.service.calls if call[0] == "start"]) == 1


def test_shared_kimi_first_start_is_owned_and_idempotent(control, monkeypatch):
    monkeypatch.setattr(units, '_install_claude_backend', Mock(side_effect=AssertionError('Kimi must not install Claude')))
    units.start_kimi_service()
    units.start_kimi_service()
    assert control.service.active == {'tfi-fixture-kimi-code.service'}
    assert len([cmd for cmd in control.commands if '--now' in cmd]) == 1
    assert not any(cmd[0] != 'systemctl' for cmd in control.commands)


def test_foreign_dropin_rejected_before_new_start(control):
    target = units.install_claude_backend(app_server_only=True)
    before = target.read_bytes()
    control.service.dropins = "/another/override.conf"
    control.commands.clear()
    with pytest.raises(ValueError, match="different definition or overrides"):
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
    staged_sdk = control.sdk.resolve()
    staged = Mock(return_value=staged_sdk)
    monkeypatch.setattr(updates.ClaudeRuntime, "stage", staged)
    target = units.install_claude_backend()
    staged.assert_called_once_with(("0.2.174",))
    link = updates.RUNTIMES / "claude-agent-sdk-current"
    assert link.resolve() == staged_sdk
    assert "management_portal.service_runtime" in target.read_text()


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
    document = json.loads((control.root / 'instance.json').read_text())
    document['services']['components'].append('claude-code')
    (control.root / 'instance.json').write_text(json.dumps(document))
    control.context["directory"].mkdir()
    unit = control.context["directory"] / "tfi-fixture-claude-code.service"
    unit.write_text("Environment=PORTAL_INSTANCE_ROOT=/another/instance\n")
    with pytest.raises(InstructionError) as error:
        setup.check(inv)
    assert error.value.details["reason"] == "account.service_start_failed"
    assert unit.read_text() == "Environment=PORTAL_INSTANCE_ROOT=/another/instance\n"
    assert control.commands == []
    execution.submit.assert_not_called()
