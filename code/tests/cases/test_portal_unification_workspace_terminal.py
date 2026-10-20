"""
:module: resident.workspace-memory
:importance: P1
:aspects: functional, behavior
"""
from unittest import mock

import pytest

from management_portal import local_terminal_backend as terminal


@pytest.fixture
def session():
    process = mock.Mock(pid=1234)
    process.poll.return_value = None
    return terminal.TerminalSession(
        session_id="unification-terminal", actor="terminal-owner",
        process=process, created_at=0, last_access=0, cwd="/tmp",
    )


def test_terminal_exit_returns_final_output_before_removing_session(session, monkeypatch):
    """终端退出时先返回最后的输出，再移除会话

    :importance: P2"""
    session.last_access = terminal.time.time()
    session.process.poll.return_value = 0
    monkeypatch.setitem(terminal._SESSIONS, session.session_id, session)
    with mock.patch.object(session.process, "read", return_value=b"logout\r\n"):
        result = terminal.read_terminal(session.actor, session.session_id)
    assert result["alive"] is False
    assert result["chunk"] == "logout\r\n"
    assert "output" not in result
    assert session.session_id not in terminal._SESSIONS


def test_terminal_cursor_remains_monotonic_after_output_eviction(session, monkeypatch):
    """终端输出被截断淘汰后游标仍单调递增，落后的读取者拿到保留的全部输出

    :design: decision_f22e45dfd382"""
    monkeypatch.setattr(terminal, "MAX_OUTPUT_CHARS", 5)
    with mock.patch.object(session.process, "read", return_value=b"12345"):
        first = terminal._public_state(session)
    with mock.patch.object(session.process, "read", return_value=b"678"):
        second = terminal._public_state(session, first["cursor"])
    assert first["cursor"] == 5
    assert session.output == "45678"
    assert "output" not in second
    assert second["chunk"] == "678"
    assert second["cursor"] == 8
    with mock.patch.object(session.process, "read", return_value=b""):
        lagging = terminal._public_state(session, 1)
    assert lagging["chunk"] == "45678"
    assert lagging["cursor"] == 8


def test_terminal_preserves_utf8_codepoints_split_between_reads(session):
    """终端读取把跨次拆分的 UTF-8 字符拼回完整后再输出

    :design: decision_f22e45dfd382"""
    encoded = "汉🙂".encode("utf-8")
    with mock.patch.object(session.process, "read", return_value=encoded[:2]):
        first = terminal._public_state(session)
    with mock.patch.object(session.process, "read", return_value=encoded[2:5]):
        second = terminal._public_state(session, first["cursor"])
    with mock.patch.object(session.process, "read", return_value=encoded[5:]):
        third = terminal._public_state(session, second["cursor"])
    assert first["chunk"] == ""
    assert second["chunk"] == "汉"
    assert third["chunk"] == "🙂"
    assert third["cursor"] == 2


@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='POSIX partial-write transport')
def test_terminal_finishes_known_partial_writes_without_replaying_prefix(session, monkeypatch):
    """终端部分写入时只续写剩余字节，不重发已写入的前缀

    :design: decision_f22e45dfd382"""
    monkeypatch.setitem(terminal._SESSIONS, session.session_id, session)
    from management_common.terminal_posix import PosixTerminal
    transport = PosixTerminal.__new__(PosixTerminal)
    transport.master_fd = 42
    with mock.patch("management_common.terminal_posix.os.write", side_effect=[2, 2]) as write:
        transport.write(b"abcd")
    assert write.call_args_list == [mock.call(42, b"abcd"), mock.call(42, b"cd")]


def test_terminal_rejects_oversized_input_without_silent_truncation(session, monkeypatch):
    """超过 8192 字符的终端输入被拒绝，不会被静默截断

    :design: decision_f22e45dfd382"""
    monkeypatch.setitem(terminal._SESSIONS, session.session_id, session)
    with mock.patch.object(session.process, "write") as write:
        with pytest.raises(ValueError, match="8192"):
            terminal.write_terminal(session.actor, session.session_id, "a" * 8193)
        write.assert_not_called()


def test_terminal_write_failure_does_not_retry_and_other_actors_cannot_write(session, monkeypatch):
    """终端写入失败时不重试，其他用户不能向该终端写入

    :design: decision_f22e45dfd382"""
    monkeypatch.setitem(terminal._SESSIONS, session.session_id, session)
    with mock.patch.object(session.process, "write", side_effect=BlockingIOError()) as write:
        with pytest.raises(KeyError):
            terminal.write_terminal("another-actor", session.session_id, "command")
        write.assert_not_called()
        with pytest.raises(BlockingIOError):
            terminal.write_terminal(session.actor, session.session_id, "command")
        write.assert_called_once_with(b"command")
