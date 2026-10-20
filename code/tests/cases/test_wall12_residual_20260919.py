import json
import socket
import subprocess


from management_portal import agent_status_backend, local_terminal_backend, resource_identity_backend




def test_repo_registration_in_temporary_registry_is_idempotent_without_commit(tmp_path, monkeypatch):
    """重复登记同一个本地 Git 检出得到同一 repo 记录，只运行本地 git 命令"""
    original_run = subprocess.run

    def local_git_only(arguments, *args, **kwargs):
        assert arguments[0] == "git"
        assert not set(arguments).intersection({"clone", "fetch", "push", "pull", "commit", "ls-remote"})
        return original_run(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", local_git_only)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    resource_identity_backend.initialize_git_checkout(checkout)
    first = resource_identity_backend.register_repo_checkout(checkout)
    second = resource_identity_backend.register_repo_checkout(checkout)
    stored = resource_identity_backend.get_repo_record(first["id"])
    assert first["id"].startswith("repo_")
    assert second["id"] == first["id"] == stored["id"]
    assert len(resource_identity_backend.load_repo_records()) == 1
    assert stored["checkouts"][0]["path"] == str(checkout)


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
