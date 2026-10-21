"""Home activity account reads must scale with active work, not idle Residents.

:design: decision_1b2bdf5c33d6
"""
from management_portal.society_service import active_agent_projection


def test_inactive_residents_do_not_resolve_session_accounts(monkeypatch):
    """Home 活动投影不为空闲且无排队输入的 Resident 解析会话账号

    :design-point:"""
    def no_binding_read(_record):
        raise AssertionError("idle Resident must not resolve its session account")

    monkeypatch.setattr(active_agent_projection, "_resident_account_id", no_binding_read)
    for index in range(1000):
        assert active_agent_projection._resident_runs(
            {"resident_id": f"resident_{index:012x}"},
            {"status": "idle", "queued_messages": []},
        ) == []


def test_idle_resident_with_queued_inputs_still_resolves_account(monkeypatch):
    """空闲但有排队输入的 Resident 仍解析账号并显示为排队中

    :design-point:"""
    record = {"resident_id": "resident_000000000001"}
    calls = []

    def account(value):
        calls.append(value)
        return "two"

    monkeypatch.setattr(active_agent_projection, "_resident_account_id", account)
    runs = active_agent_projection._resident_runs(
        record,
        {"status": "idle", "queued_messages": [{"message_id": "queued_input"}]},
    )
    assert calls == [record]
    assert len(runs) == 1
    assert runs[0]["status"] == "queued"
    assert runs[0]["account_id"] == "two"
