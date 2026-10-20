"""Admission, cancellation and transport errors preserve caller-visible semantics.

:module: spec.design-governance
:importance: P2
"""
from unittest.mock import MagicMock

import pytest

from management_portal.hatg.catalog import declaration
from management_portal.society_service import family_facade, family_quota, transport
from management_portal.society_service.errors import SocietyServiceBusy, SocietyServiceUnavailable
from tests.cases.test_family_quota import quota_family, owners, team, ACTOR, _account, _record  # noqa: F401
from tests.cases.rpc_test_support import socket_rpc_authenticator


def test_required_shared_state_accepts_empty_json_string():
    """共享状态必填值接受空 JSON 字符串"""
    declaration("UpdateSharedState").validate_arguments({"lineage_id": "lineage_123456789012", "key": "x", "value": ""})
    declaration("UpdateSharedState").validate_arguments({"lineage_id": "lineage_123456789012", "key": "x", "expected_value": "", "value": "next"})


def test_false_release_flag_does_not_select_the_all_alternative():
    """PublishRelease 的 all 为假时不触发全量选项互斥"""
    declaration("PublishRelease").validate_arguments({"paths": ["SPEC.md"], "all": False, "message": "B4 validation"})


@pytest.mark.parametrize("flag", ["quota_reviewed", "scope_reviewed"])
def test_review_with_reason_requires_this_familys_steward(quota_family, flag):
    """Family 审查即使提供理由仍要求本组 steward 身份"""
    f = quota_family
    if flag == "scope_reviewed":
        f.accounts[0] = _account("one", 90)
    family_quota.relay_message(f.facade, f.message, f.member, ACTOR,
                              scope_gap="数据库" if flag == "scope_reviewed" else "")
    with pytest.raises(PermissionError, match="steward"):
        family_quota.relay_message(f.facade, f.message, f.member, ACTOR,
                                  reason="给出理由仍不能代替 steward", **{flag: True})


@pytest.mark.parametrize("stage,error", [("connect", TimeoutError("full backlog")),
                                         ("connect", ConnectionRefusedError("offline")),
                                         ("sendall", TimeoutError("partial send")),
                                         ("readline", TimeoutError("response delayed"))])
def test_socket_failure_distinguishes_unsent_requests(monkeypatch, tmp_path, socket_rpc_authenticator, stage, error):
    """套接字故障按请求是否已发送区分提交状态与重试"""
    path = tmp_path / "lineage.sock"
    socket_rpc_authenticator(path)
    connection = MagicMock()
    stream = connection.makefile.return_value
    getattr(stream if stage == "readline" else connection, stage).side_effect = error
    monkeypatch.setattr(transport.socket, "socket", lambda *args: connection)
    expected = SocietyServiceUnavailable if stage == "connect" else SocietyServiceBusy
    with pytest.raises(expected) as caught:
        transport.SocietyServiceClient(path).health()
    assert caught.value.commit_state == ("not_committed" if stage == "connect" else "unknown")
    assert caught.value.retryable is (stage == "connect")
    if stage == "connect":
        connection.sendall.assert_not_called()


def test_withdrawal_during_delivery_keeps_request_cancelled(quota_family, monkeypatch):
    """投递过程中撤回请求后双方输入保持取消状态"""
    from management_portal.society_service import outward_messages, resident_conversation_stream
    f = quota_family
    f.accounts[0] = _account("one", 90)
    deliver = outward_messages.deliver_to_resident

    def withdraw_then_deliver(*args, **kwargs):
        family_facade.withdraw_request(_record(f.facade), f.message, ACTOR, manager=False)
        return deliver(*args, **kwargs)

    monkeypatch.setattr(outward_messages, "deliver_to_resident", withdraw_then_deliver)
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert result["status"] == "withdrawn"
    assert f.message not in family_facade.relay_links(_record(f.facade))
    repo = resident_conversation_stream.get_repository()
    assert repo.input_state(f.facade, f.message)["state"] == "cancelled"
    delivered = family_quota.resident_message_contract.derived_message_identity("family-relay", f.facade, f.message, f.member)
    assert repo.input_state(f.member, delivered)["state"] == "cancelled"


def test_scope_notice_retries_after_external_failure_without_open_transaction(quota_family, monkeypatch):
    """范围通知在事务外发送且失败后使用同一消息重试

    :module: platform.core"""
    f = quota_family
    f.accounts[0] = _account("one", 90)
    notify = family_facade.tell_steward
    attempts = []

    def interrupted(**kwargs):
        connection = family_quota.get_lineage_data_store().database._current_connection()
        assert connection is None or not connection.in_transaction
        attempts.append(kwargs["message_id"])
        if len(attempts) == 1:
            raise OSError("notice service interrupted")
        return notify(**kwargs)

    monkeypatch.setattr(family_facade, "tell_steward", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        family_quota.relay_message(f.facade, f.message, f.member, ACTOR, scope_gap="数据库")
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR, scope_gap="数据库")
    assert result["status"] == "waiting_for_steward"
    assert attempts == [result["steward_message_id"], result["steward_message_id"]]
