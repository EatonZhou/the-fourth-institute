""":module: spec.design-governance"""
from types import SimpleNamespace

from management_portal.society_service.isa import common


def test_resident_account_inherits_only_for_same_provider(monkeypatch):
    """Resident 调用者的账号只在目标 Provider 相同时被继承"""
    record = {"provider": "codex", "account": "zero"}
    monkeypatch.setattr(common, "protocol", lambda: SimpleNamespace(
        get_resident=lambda resident_id, caller: record,
    ))
    invocation = SimpleNamespace(
        caller="resident_111111111111",
        entry="portal",
        context=SimpleNamespace(is_resident=True),
    )
    assert common.caller_account(invocation, "codex") == "zero"
    assert common.caller_account(invocation, "kimi") == ""
    assert common.caller_account(invocation, "dsh") == ""
