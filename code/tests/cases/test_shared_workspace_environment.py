"""Workspace path variables reach the shells of shared Provider servers (Kimi Code, Antigravity) and of DSH."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import resident_shell_environment as shell


RESIDENT = "resident_0123456789ab"
LINEAGE = "lineage_0123456789ab"


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    directory = tmp_path / "workspace-env"
    monkeypatch.setattr(shell, "shared_workspace_environment_dir", lambda: directory)
    return directory


def _identity(tmp_path, **extra):
    return {"PORTAL_RESIDENT_ID": RESIDENT, "PORTAL_LINEAGE_ID": LINEAGE,
            "PORTAL_RESIDENT_WORKSPACE": str(tmp_path / "own workspace"), **extra}


def _shell(command, *, cwd, env_dir, **environment):
    # The server's environment: the hook plus whatever the native server sets.
    base = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    base.update(BASH_ENV=str(shell.WORKSPACE_ENV_HOOK), PORTAL_SHARED_SHELL_ENV_DIR=str(env_dir), **environment)
    return subprocess.run(["bash", "-c", command], cwd=cwd, env=base, capture_output=True, text=True, check=True).stdout


def _variables(output):
    return dict(line.split("=", 1) for line in output.splitlines() if line.startswith("PORTAL_") and "_WORKSPACE" in line)


def test_shared_server_shell_sources_its_residents_current_workspace_variables(tmp_path, env_dir):
    """共享 Provider 服务器启动的每个 shell 按工作目录所属 Resident 读到当前 Workspace 变量，解绑后下一条命令即消失

    :module: execution.providers
    :design: decision_ccb99a00eebd"""
    folder = tmp_path / "agents" / LINEAGE / "residents" / RESIDENT / "workspace" / "sub dir"
    folder.mkdir(parents=True)
    bound = {"PORTAL_WORKSPACE_0123456789AB": str(tmp_path / "it's bound")}
    shell.write_shared_workspace_environment(_identity(tmp_path, **bound))
    seen = _variables(_shell("env", cwd=folder, env_dir=env_dir))
    assert seen == {"PORTAL_RESIDENT_WORKSPACE": str(tmp_path / "own workspace"), **bound}
    # A command's own shells keep the first shell's values.
    assert _variables(_shell("cd / && bash -c env", cwd=folder, env_dir=env_dir)) == seen
    shell.write_shared_workspace_environment(_identity(tmp_path))
    assert _variables(_shell("env", cwd=folder, env_dir=env_dir)) == {
        "PORTAL_RESIDENT_WORKSPACE": str(tmp_path / "own workspace")}
    # Outside any Resident folder, without a session key, nothing is sourced.
    assert _variables(_shell("env", cwd=tmp_path, env_dir=env_dir)) == {}
    assert (env_dir.stat().st_mode & 0o777) == 0o700
    assert ((env_dir / f"{RESIDENT}.sh").stat().st_mode & 0o777) == 0o600
    # A deleted Resident's file ages out on a later write.
    stale = env_dir / "resident_ba9876543210.sh"
    stale.write_text("export PORTAL_RESIDENT_WORKSPACE=/gone\n")
    old = stale.stat().st_mtime - shell.STALE_FILE_SECONDS - 60
    os.utime(stale, (old, old))
    shell.write_shared_workspace_environment(_identity(tmp_path))
    assert not stale.exists() and (env_dir / f"{RESIDENT}.sh").exists()


def test_hook_runs_in_a_top_level_shell_with_socket_stdio_like_kimis(tmp_path, env_dir, monkeypatch):
    """Kimi Code 以 socket 作 stdio、不带 SHLVL 启动 bash；服务器环境仍让它执行钩子，并从开头的 cd 找到 Resident"""
    import socket

    monkeypatch.setattr(shell, "shared_workspace_environment_dir", lambda: env_dir)
    workspace = tmp_path / "agents" / LINEAGE / "residents" / RESIDENT / "workspace"
    workspace.mkdir(parents=True)
    shell.write_shared_workspace_environment(_identity(tmp_path))
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    environment.update(shell.shared_server_environment())
    ours, theirs = socket.socketpair()
    with ours, theirs:
        # Kimi enters the session directory with a leading cd, from its server's directory.
        output = subprocess.run(["/bin/bash", "-c", f"cd '{workspace}' && env"], cwd=tmp_path, env=environment,
                                stdin=theirs, capture_output=True, text=True, check=True).stdout
    assert _variables(output) == {"PORTAL_RESIDENT_WORKSPACE": str(tmp_path / "own workspace")}


def test_antigravity_shell_finds_its_session_file_from_any_directory(tmp_path, env_dir):
    """Antigravity 的 shell 按 ANTIGRAVITY_CONVERSATION_ID 找到本会话的变量，命令在 Resident 目录之外也一样"""
    session = "af0789be-5c5d-4287-80bf-321855daae2f"
    shell.write_shared_workspace_environment(_identity(tmp_path), session_id=session)
    seen = _variables(_shell("env", cwd=tmp_path, env_dir=env_dir, ANTIGRAVITY_CONVERSATION_ID=session))
    assert seen == {"PORTAL_RESIDENT_WORKSPACE": str(tmp_path / "own workspace")}
    assert _variables(_shell("env", cwd=tmp_path, env_dir=env_dir, ANTIGRAVITY_CONVERSATION_ID="../escape")) == {}
    with pytest.raises(ValueError):
        shell.write_shared_workspace_environment(_identity(tmp_path), session_id="../escape")


def test_worker_refreshes_the_file_before_each_shared_server_turn(tmp_path, env_dir, monkeypatch):
    """Worker 在 Kimi 每轮执行前按当前绑定重写共享变量文件，独立进程的 Provider 不写"""
    from management_portal.execution import native_options

    seen = []

    class Adapter:
        def __init__(self, shared):
            self.shared_shell_server = shared

        def prepare(self, invocation):
            seen.append(invocation)
            return invocation

    for provider, shared in (("kimi", True), ("codex", False)):
        definition = SimpleNamespace(runtime_adapter=lambda shared=shared: Adapter(shared))
        monkeypatch.setitem(native_options.__dict__, "_unused", None)
        from management_portal.execution import provider_catalog
        monkeypatch.setitem(provider_catalog.AGENT_PROVIDERS, provider, definition)
        request = SimpleNamespace(provider=provider, runtime_options={
            "resident_environment": _identity(tmp_path, PORTAL_WORKSPACE_0123456789AB=f"/{provider}")})
        invocation = SimpleNamespace(request=request)
        monkeypatch.setattr(native_options, "replace", lambda obj, **changes: SimpleNamespace(**{**vars(obj), **changes}))
        native_options.prepare_invocation(invocation)
    text = (env_dir / f"{RESIDENT}.sh").read_text()
    assert "export PORTAL_WORKSPACE_0123456789AB=/kimi" in text and "/codex" not in text
    assert "PORTAL_RESIDENT_ID" not in text  # Paths only; identity never comes from the file.
    assert len(seen) == 2


def test_shared_servers_give_their_shells_the_hook():
    """Kimi 与 Antigravity 账号服务器的环境带 BASH_ENV 钩子和变量目录"""
    environment = shell.shared_server_environment()
    assert environment["BASH_ENV"] == str(shell.WORKSPACE_ENV_HOOK) and shell.WORKSPACE_ENV_HOOK.is_file()
    assert Path(environment["PORTAL_SHARED_SHELL_ENV_DIR"]).name == "workspace-env"
    from management_portal.execution.antigravity_backend import AntigravityOptions
    from management_portal.execution.native_options import ClaudeOptions, CodexOptions, KimiOptions
    assert KimiOptions.shared_shell_server and AntigravityOptions.shared_shell_server
    assert not CodexOptions.shared_shell_server and not ClaudeOptions.shared_shell_server


def test_lineage_outside_a_project_has_its_own_lineage_short_name_and_no_project_one(tmp_path):
    """不属于项目的 Lineage：Lineage 短名指向其 lineage_folder/workspace，没有项目短名，每项绑定都有 ID 变量"""
    from management_portal.society_service import resident_runtime_domain

    lineage_folder = tmp_path / "Agents" / LINEAGE
    own = lineage_folder / "residents" / RESIDENT / "workspace"
    record = {"resident_id": RESIDENT, "lineage_id": LINEAGE, "workspace_path": str(lineage_folder),
              "owned_workspace": {"workspace_id": "workspace_aaaaaaaaaaaa", "path": str(own)},
              "workspace_bindings": [{"workspace_id": "workspace_bbbbbbbbbbbb", "path": str(lineage_folder / "workspace")},
                                     {"workspace_id": "workspace_cccccccccccc", "path": str(tmp_path / "data")}]}
    environment = resident_runtime_domain.resident_cli_environment(record)
    assert workspace_variables(environment) == {
        "PORTAL_WORKSPACE_AAAAAAAAAAAA": str(own), "PORTAL_RESIDENT_WORKSPACE": str(own),
        "PORTAL_WORKSPACE_BBBBBBBBBBBB": str(lineage_folder / "workspace"),
        "PORTAL_LINEAGE_WORKSPACE": str(lineage_folder / "workspace"),
        "PORTAL_WORKSPACE_CCCCCCCCCCCC": str(tmp_path / "data"),
    }


def workspace_variables(environment):
    return shell.workspace_fields(environment)


class _Transport:
    def __init__(self, *, busy=False):
        self.calls, self.busy, self.closed = [], busy, False

    def _request(self, method, params=None, **_):
        from management_portal.native_agent_transport import NativeAgentError, NativeAgentUnavailable
        self.calls.append(method)
        if method == "health":
            if self.closed:
                raise NativeAgentUnavailable("exited")
            return {"ready": True}
        if method == "session/close":
            if self.busy:
                raise NativeAgentError("Cannot release an active session")
            self.closed = True
            return {"closed": True}
        raise AssertionError(method)


@pytest.mark.parametrize("busy", [False, True])
def test_dsh_relaunches_an_idle_process_when_the_workspace_variables_changed(tmp_path, monkeypatch, busy):
    """DSH 的绑定变化后下一轮重启空闲进程以带上新变量；运行中的一轮保留原进程；未变化时复用

    :module: execution.providers
    :design: decision_ccb99a00eebd"""
    from management_portal import dsh_client

    monkeypatch.setattr(dsh_client, "STATE", tmp_path / "state")
    monkeypatch.setattr(dsh_client, "SOCKETS", tmp_path / "sockets")
    monkeypatch.setattr(dsh_client.time, "sleep", lambda _: None)
    transport = _Transport(busy=busy)
    monkeypatch.setattr(dsh_client.DshClient, "_transport", lambda self, session_id: transport)
    launched = []

    class Launched(Exception):
        pass

    def profile(route):
        launched.append(route)
        raise Launched()

    monkeypatch.setattr(dsh_client.dsh_provider_catalog, "profile", profile)
    session = "dsh-" + "a" * 32
    params = {"provider_profile": "deepseek", "cwd": str(tmp_path),
              "resident_environment": _identity(tmp_path, PORTAL_WORKSPACE_0123456789AB="/bound")}
    root = tmp_path / "state" / session
    root.mkdir(parents=True)
    (root / "environment.sha256").write_text(dsh_client._environment_digest(params))
    assert dsh_client.DshClient()._start(session, params) is transport and "session/close" not in transport.calls
    params["resident_environment"] = _identity(tmp_path)
    if busy:
        assert dsh_client.DshClient()._start(session, params) is transport and not launched
    else:
        with pytest.raises(Launched):
            dsh_client.DshClient()._start(session, params)
        assert transport.closed and launched == ["deepseek"]
