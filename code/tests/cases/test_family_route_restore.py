"""Explicit failed facade recovery preserves identity and ordinary delivery admission.

:module: lineage.team
:design: decision_b77fde81e7a0
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_facade, family_quota, family_recovery, resident_conversation_stream
from management_portal.society_service.resident_record_domain import find_resident
from management_portal.society_service.domain_ports import execution_port
from tests.cases.isa_test_support import run
from tests.cases.test_family_recovery import _send, _record
from tests.cases.test_family_reassign import family, delivery_resident, future_creation_store, _REAL_VALIDATE_RELAY  # noqa: F401
from tests.cases.test_family_quota import quota_family, team, owners, ACTOR  # noqa: F401


@pytest.fixture
def failed_route(family, monkeypatch):
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": []}))
    # Recovery creates its source outside the reassign fixture's synthetic marks.
    # Exercise actual source authorization, including withdrawal after recovery.
    monkeypatch.setattr(family_facade, "validate_relay", _REAL_VALIDATE_RELAY)
    root = _send(family, "显示阶段进展数量，保留原消息")
    repo = resident_conversation_stream.get_repository()
    repo.set_input_state(family["facade"], root, "failed", error={"message": "缺少 message.data.target"})
    return root, repo


def _recover(family, root, **extra):
    return run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": root,
        "member_id": family["right"], "recover": True, "reason": "Router 复核已核定目标", **extra})


@pytest.mark.parametrize('envelope', ['current', 'legacy', 'empty'])
def test_failed_route_restores_original_once_with_failure_audit(family, failed_route, monkeypatch, envelope):
    from management_portal.society_service import family_chat_storage
    capture = family_chat_storage.capture
    if envelope != 'current':
        def older_owner(*args):
            policy = capture(*args)
            if envelope == 'empty':
                assert policy == {'muted_member_ids': [], 'solo_member_id': ''}
                return {}
            return {'excluded_member_ids': policy['muted_member_ids'], 'solo_member_id': policy['solo_member_id']}
        monkeypatch.setattr(family_chat_storage, 'capture', older_owner)
    root, repo = failed_route
    before = repo.message(family["facade"], root)
    failure = repo.input_state(family["facade"], root)
    first = _recover(family, root).raise_for_error()
    assert first["status"] == "relayed"
    assert first["recovery"]["failed_receipt"] == failure
    assert first["recovery"]["actor"] == "sample_admin"
    assert repo.message(family["facade"], root) == before
    delivered = repo.message(family["right"], first["message_id"])
    assert delivered["author_id"] == before["author_id"]
    assert delivered["content_parts"] == before["content_parts"]
    assert family_facade.relay_links(_record(family))[root]["message_id"] == first["message_id"]
    second = _recover(family, root).raise_for_error()
    assert second["message_id"] == first["message_id"]
    assert len(family_recovery.route_restorations(_record(family))[root]) == 1
    assert family_recovery.arrival(_record(family), root) is None
    assert sum(m["message_id"] == first["message_id"] for m in repo.input_states(family["right"])) == 1
    assert _recover(family, root, member_id=family["wrong"]).status == "failed"
    reassigned = run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": root,
        "member_id": family["wrong"], "reassign": True, "reason": "后续正常改派"}).raise_for_error()
    assert reassigned["status"] == "reassigned"
    assert run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": root,
        "member_id": family["wrong"]}).raise_for_error()["message_id"] == reassigned["message_id"]


@pytest.mark.parametrize('key', ['excluded_member_ids', 'muted_member_ids'])
def test_routing_envelope_compatibility_keeps_exclusion_guard(family, failed_route, monkeypatch, key):
    from management_portal.society_service import family_chat_storage
    root, repo = failed_route
    monkeypatch.setattr(family_chat_storage, 'capture', lambda *_: {key: [family['right']], 'solo_member_id': ''})
    assert _recover(family, root).status == 'failed'
    assert repo.input_state(family['facade'], root)['state'] == 'failed'
    assert not family_facade.relay_links(_record(family)).get(root)


def test_invalid_routing_envelope_is_not_treated_as_empty(family, failed_route, monkeypatch):
    from management_portal.society_service import family_chat_storage
    root, repo = failed_route
    monkeypatch.setattr(family_chat_storage, 'capture', lambda *_: {'muted_member_ids': 'invalid'})
    assert _recover(family, root).status == 'failed'
    assert repo.input_state(family['facade'], root)['state'] == 'failed'


@pytest.mark.parametrize("state", ["queued", "running", "completed", "cancelled"])
def test_recovery_never_restarts_other_states(family, monkeypatch, state):
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": []}))
    root = _send(family, "不可重复执行")
    repo = resident_conversation_stream.get_repository()
    repo.set_input_state(family["facade"], root, state)
    result = _recover(family, root)
    if state == "cancelled":
        assert result.raise_for_error()["status"] == "withdrawn"
    else:
        assert result.status == "failed"
    assert repo.input_state(family["facade"], root)["state"] == state
    assert not family_recovery.route_restorations(_record(family))
    assert root not in family_facade.relay_links(_record(family))


def test_saved_recovery_intent_survives_interruption_and_ignores_old_failure(family, failed_route, monkeypatch):
    root, repo = failed_route
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted after reopen")
    with monkeypatch.context() as patch:
        patch.setattr(family_quota, "_relay_message", interrupted)
        with pytest.raises(RuntimeError):
            family_quota.relay_message(family["facade"], root, family["right"], "sample_admin", recover=True, reason="核定恢复")
    assert repo.input_state(family["facade"], root)["state"] == "queued"
    stale_route = run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": root,
        "member_id": family["wrong"]})
    assert stale_route.status == "failed"
    family_recovery.routing_failed({"occurred_at": repo.message(family["facade"], root)["created_at"],
        "payload": {"resident_id": family["facade"], "message_id": root}}, "旧 Router 失败")
    assert family_recovery.request_status(_record(family), root)["state"] == "routing"
    assert _recover(family, root).raise_for_error()["status"] == "relayed"


def test_recovery_requires_authority_and_reason_before_reopening(family, failed_route, monkeypatch):
    root, repo = failed_route
    monkeypatch.setattr(family_recovery, "can_recover", lambda *_: False)
    monkeypatch.setattr(family_facade, "is_own_steward", lambda *_: False)
    assert _recover(family, root).status == "failed"
    assert repo.input_state(family["facade"], root)["state"] == "failed"
    monkeypatch.setattr(family_recovery, "can_recover", lambda *_: True)
    assert _recover(family, root, reason="").status == "failed"
    assert _recover(family, root, reassign=True).status == "failed"
    assert _recover(family, root, notice=True).status == "failed"
    assert not family_recovery.route_restorations(_record(family))


def test_recovered_request_still_waits_for_quota_review(quota_family):
    f = quota_family
    repo = resident_conversation_stream.get_repository()
    repo.set_input_state(f.facade, f.message, "failed", error={"message": "Router failed"})
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR, recover=True, reason="核定恢复")
    assert result["status"] == "waiting_for_quota_review"
    assert not family_facade.relay_links(find_resident(f.facade)).get(f.message)
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR, recover=True,
                                     reason="重复恢复")["steward_message_id"] == result["steward_message_id"]
    approved = family_quota.relay_message(f.facade, f.message, f.member, f.steward,
                                         quota_reviewed=True, reason="轻量任务允许")
    assert approved["status"] == "relayed"
    assert family_recovery.route_restorations(find_resident(f.facade))[f.message][-1]["status"] == "relayed"


def test_withdrawal_after_saved_recovery_intent_wins(family, failed_route, monkeypatch):
    root, repo = failed_route
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(family_quota, "_relay_message", interrupted)
        with pytest.raises(RuntimeError):
            family_quota.relay_message(family["facade"], root, family["right"], "sample_admin", recover=True, reason="恢复")
    repo.set_input_state(family["facade"], root, "cancelled")
    assert _recover(family, root).raise_for_error()["status"] == "withdrawn"
    assert root not in family_facade.relay_links(_record(family))


def test_own_steward_can_recover_without_being_original_author(family, failed_route, monkeypatch):
    root, repo = failed_route
    monkeypatch.setattr(family_recovery, "can_recover", lambda *_: False)
    monkeypatch.setattr(family_facade, "is_own_steward", lambda facade, actor: actor == family["steward"])
    restored = family_quota.relay_message(family["facade"], root, family["right"], family["steward"],
                                         recover=True, reason="本层 Steward 复核目标")
    assert restored["status"] == "relayed"
    assert repo.message(family["right"], restored["message_id"])["author_id"] == "sample_admin"
    assert restored["recovery"]["actor"] == family["steward"]


def test_shipped_recovery_guides_upgrade_but_custom_guides_remain():
    from management_portal.society_service import family_tasks
    for trigger, previous in (("route_review", family_tasks._PRE_DIRECT_RECOVERY_ROUTE_REVIEW_GUIDE),
                              ("recovery", family_tasks._PRE_DIRECT_RECOVERY_GUIDE)):
        assert family_tasks.saved_fields(trigger, {"guide": previous}) == {}
        assert "recover=true" in family_tasks.TASKS[trigger][3]
        assert family_tasks.saved_fields(trigger, {"guide": "custom"}) == {"guide": "custom"}
