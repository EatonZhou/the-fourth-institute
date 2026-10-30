from unittest import mock


from management_portal.execution import claude_backend


def test_claude_interrupt_uses_native_identity_and_reports_daemon_result(subtests, isolated_portal_case):
    """Claude 中断按原生会话 ID 发给 daemon，并以 daemon 的回复判断是否已中断"""
    for session_key, session_id, reply, expected, called in [
        ("explicit", "native", {"interrupted": True}, True, True),
        ("", "native", {"interrupted": True}, True, True),
        ("explicit", "native", {"interrupted": False, "reason": "idle"}, False, True),
        ("explicit", "native", {}, False, True),
        ("", "", {"interrupted": True}, False, False),
    ]:
        with subtests.test(session_key=session_key, session_id=session_id, reply=reply, expected=expected, called=called), isolated_portal_case() as case_path:
            client = mock.Mock()
            client.interrupt.return_value = reply
            with mock.patch.object(claude_backend, "shared_claude_client", return_value=client):
                result = claude_backend.ClaudeBackend().interrupt(
                    backend_session_id=session_id, turn_id="turn-a", backend_route_id="",
                    runtime_options={"session_key": session_key} if session_key else {},
                )
            assert result is expected
            if called:
                client.interrupt.assert_called_once_with(session_key, session_id, turn_id="turn-a")
            else:
                client.interrupt.assert_not_called()
