from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from management_portal import local_terminal_backend


def _observed_cwd(actor, session_id):
    code = "import os; print('CWD_BEGIN' + os.getcwd() + 'CWD_END', flush=True)"
    argv = [sys.executable, '-c', code]
    command = subprocess.list2cmdline(argv) if sys.platform == 'win32' else shlex.join(argv)
    result = local_terminal_backend.write_terminal(actor, session_id, command + '\r')
    data = result['chunk']
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if 'CWD_BEGIN' in data:
            # Use complete output line, excluding the echoed Python command.
            for line in data.splitlines():
                if line.startswith('CWD_BEGIN') and line.endswith('CWD_END'):
                    return Path(line[len('CWD_BEGIN'):-len('CWD_END')])
        result = local_terminal_backend.read_terminal(actor, session_id, result['cursor'])
        data += result['chunk']
        time.sleep(.01)
    raise AssertionError('shell did not report its working directory')


def test_terminal_uses_and_preserves_requested_cwd(tmp_path: Path) -> None:
    """终端在请求的工作目录启动，重启时改用新指定的目录"""
    first_cwd = tmp_path / "primary"
    second_cwd = tmp_path / "next-primary"
    first_cwd.mkdir()
    second_cwd.mkdir()
    actor = "workspace-cwd-test"
    # A new shell is OpenTerminal, which the Portal 管理员 category may run.
    from tests.cases.permission_test_support import portal_admin

    portal_admin(actor)
    started = local_terminal_backend.start_terminal(actor, cwd=str(first_cwd))
    session_id = started["session_id"]
    try:
        assert started["cwd"] == str(first_cwd.resolve())
        assert _observed_cwd(actor, session_id) == first_cwd.resolve()

        restarted = local_terminal_backend.control_terminal(
            actor,
            session_id,
            "restart",
            cwd=str(second_cwd),
        )
        session_id = restarted["session_id"]
        assert restarted["cwd"] == str(second_cwd.resolve())
        assert _observed_cwd(actor, session_id) == second_cwd.resolve()
    finally:
        local_terminal_backend.control_terminal(actor, session_id, "close")


def test_terminal_rejects_invalid_cwd(subtests, isolated_portal_case):
    """终端拒绝相对路径或不存在的工作目录"""
    for cwd in ["relative/path", "/path/that/does/not/exist"]:
        with subtests.test(cwd=cwd), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="terminal cwd"):
                local_terminal_backend.start_terminal("workspace-cwd-invalid", cwd=cwd)


def test_device_terminal_uses_registered_ssh_and_in_memory_password() -> None:
    """设备终端按登记的 SSH 地址连接，密码只在内存中传递而不进入命令行"""
    session = mock.Mock()
    session.session_id = "term_device"
    session.process.poll.return_value = None
    with (
        mock.patch(
            "management_portal.device_identity_backend.effective_ssh_access",
            return_value={
                "host": "lab-host",
                "user": "operator",
                "port": 2222,
                "config_path": "/tmp/config",
                "identity_file": "/tmp/id_ed25519",
                "via_resource_id": "device_0123456789ab",
                "credential_saved": True,
            },
        ),
        mock.patch(
            "management_portal.device_identity_backend.read_device_ssh_password",
            return_value="secret-password",
        ),
        mock.patch.object(
            local_terminal_backend,
            "_spawn_process",
            return_value=session,
        ) as spawn,
        mock.patch.object(
            local_terminal_backend,
            "_public_state",
            return_value={"session_id": "term_device", "cursor": 0},
        ),
    ):
        result = local_terminal_backend.start_device_terminal(
            "sample_admin",
            "device_abcdef012345",
            cols=120,
            rows=40,
        )
    local_terminal_backend._SESSIONS.pop("term_device", None)

    argv = spawn.call_args.args[1]
    assert argv[-2:] == ["--", "operator@lab-host"]
    assert ["-p", "2222"] == argv[argv.index("-p"):argv.index("-p") + 2]
    assert spawn.call_args.kwargs["initial_password"] == "secret-password"
    assert "secret-password" not in argv
    assert result["via_resource_id"] == "device_0123456789ab"


def test_two_local_terminal_sessions_isolate_interrupt_and_restart(tmp_path, record_property):
    """中断或重启一个本地终端会话不影响另一会话，重启得到新的会话与进程"""
    actor = "isolated-terminal-owner"
    # Restarting opens a new shell: OpenTerminal, which the Portal 管理员 category may run.
    from tests.cases.permission_test_support import portal_admin

    portal_admin(actor)
    first = local_terminal_backend.start_terminal(actor, cwd=str(tmp_path))
    second = local_terminal_backend.start_terminal(actor, cwd=str(tmp_path))
    active_ids = {first["session_id"], second["session_id"]}
    try:
        second_process = local_terminal_backend._SESSIONS[second["session_id"]].process
        local_terminal_backend.control_terminal(actor, first["session_id"], "interrupt")
        assert second_process.poll() is None
        restarted = local_terminal_backend.control_terminal(actor, first["session_id"], "restart", cwd=str(tmp_path))
        active_ids.discard(first["session_id"])
        active_ids.add(restarted["session_id"])
        assert restarted["session_id"] != first["session_id"]
        assert restarted["pid"] != first["pid"]
        assert local_terminal_backend._SESSIONS[second["session_id"]].process is second_process
        assert second_process.poll() is None
        for session_id in active_ids:
            result = local_terminal_backend.read_terminal(actor, session_id, cursor=0)
            assert result["session_id"] == session_id
            assert result["cursor"] >= 0
        record_property("evidence", json.dumps({"real_local_ptys": 2, "unrelated_process_survives": True, "new_session_and_pid_on_restart": True, "remote_ssh_used": False}))
    finally:
        for session_id in active_ids:
            local_terminal_backend.control_terminal(actor, session_id, "close")
