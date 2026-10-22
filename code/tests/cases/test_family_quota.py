"""Low weekly quota is reviewed before delivery, and steward safety needs no model.

:module: lineage.hatg
"""
from __future__ import annotations
from tests.cases.family_test_support import set_role

from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    family_facade, family_quota, lineage_domain, resident_account_policy as accounts_policy,
    resident_backend, resident_conversation_stream, resident_domain, resident_execution_work,
    resident_intake_domain, resident_lifecycle_domain, resident_restart_domain,
    resident_workspace_domain, lineage_human_notifications,
    resident_record_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_resident_mailbox_requeue import _record


def _account(route, weekly, *, short=90, provider="codex", fresh=True, duration=False):
    return {"account_id": f"{provider}_{route}", "provider": provider, "backend_route_id": route,
            "quota_reserve": {"status": "available", "fresh": fresh, "remaining": min(weekly, short),
                              "windows": [{"window": "primary" if duration else "seven_day",
                                           "window_duration_mins": 10080 if duration else None,
                                           "remaining_percent": weekly, "resets_at": 123456},
                                          {"window": "five_hour", "remaining_percent": short}]}}


@pytest.mark.parametrize("weekly,short,expected", [(9, 90, 9), (60, 1, 60), (10, 80, 10)])
def test_weekly_headroom_uses_the_weekly_window_and_supports_codex_durations(weekly, short, expected):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    assert accounts_policy.weekly_remaining(_account("one", weekly, short=short)) == expected
    assert accounts_policy.weekly_remaining(_account("one", weekly, short=short, duration=True)) == expected
    assert accounts_policy.weekly_remaining(_account("one", weekly, fresh=False)) is None


def test_reserve_targets_require_weekly_above_ten_or_a_funded_metered_agent():
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    candidates = [_account("one", 9), _account("two", 10), _account("three", 50, fresh=False),
                  _account("four", 45), _account("claude_one", 80, provider="claude")]
    assert accounts_policy.fallback_account(candidates, "codex", "one", [], weekly_reserve=True)["backend_route_id"] == "four"
    assert accounts_policy.fallback_account(candidates, "codex", "one", ["codex_four"], weekly_reserve=True)["provider"] == "claude"
    metered = {"provider": "claude", "account_id": "deepseek_primary", "backend_route_id": "deepseek_primary",
               "quota_reserve": {"status": "available", "fresh": True, "remaining": 15,
                                 "remaining_balance": 15, "currency": "CNY"}}
    assert accounts_policy.sufficient_weekly_reserve(metered)
    metered["quota_reserve"].pop("currency")
    assert not accounts_policy.sufficient_weekly_reserve(metered)


@pytest.fixture
def quota_family(team, monkeypatch):
    monkeypatch.setattr(resident_record_domain, "_resolve_context_refs", lambda *args, **kwargs: {})
    facade_id = team.source["resident_id"]
    member_id = team.target["resident_id"]
    steward = lineage_domain.create_lineage_resident(team.lineage_id, {
        "title": "Quota steward", "backend": "codex", "mode": "new_session",
    }, ACTOR)["resident"]
    steward_id = steward["resident_id"]
    # A second Resident the steward can arrange a request to, made before the Family exists.
    spare_id = lineage_domain.create_lineage_resident(team.lineage_id, {
        "title": "数据库迁移", "backend": "codex", "mode": "new_session",
    }, ACTOR)["resident"]["resident_id"]
    resident_intake_domain._commit_resident_intake_state(_record(steward_id), "closed")
    set_role(facade_id, family_id="fbi_test", actor=ACTOR)
    set_role(steward_id, role="steward", family_id="fbi_test", actor=ACTOR)
    resident_intake_domain._commit_resident_intake_state(_record(facade_id), "open")
    monkeypatch.setattr(family_facade, "family_steward_id", lambda _: steward_id)
    monkeypatch.setattr(family_facade, "family_member_ids", lambda _: {member_id})
    monkeypatch.setattr(resident_backend, "session_record", lambda _: {})
    for rid, route in ((member_id, "one"), (steward_id, "one"), (spare_id, "two")):
        record = _record(rid); record["backend_route_id"] = route
        resident_workspace_domain._save_resident_record(record, commit_configuration=True)
    accounts = [_account("one", 9), _account("two", 90)]
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": accounts}))
    message_id = "msg_1234567890abcdef"
    resident_domain.deliver_resident_message_content(facade_id, ACTOR, "持续检查权限并修复所有缺陷", message_id=message_id)
    return SimpleNamespace(facade=facade_id, member=member_id, steward=steward_id, spare=spare_id,
                           message=message_id, accounts=accounts)


def test_missing_bound_account_wait_does_not_erase_quota_incident_or_choose_another_account(quota_family):
    """账号缺失保留原绑定和额度事故，恢复账号仍受原额度保护。"""
    f = quota_family
    record = _record(f.steward)
    store = family_quota.get_lineage_data_store().family_quota_migrations
    incident = {"epoch": "existing-window", "attempts": 1, "tried_accounts": ["codex_two"],
                "status": "unavailable", "reason": "quota incident"}
    with store.transaction(record["lineage_id"], f.steward) as state:
        state.update(incident)
    assert family_quota.ensure_role_reserve(record, [f.accounts[1]])
    held = family_quota.role_migration(record)
    assert held == {**incident, "account_wait": held["account_wait"]}
    assert "当前绑定" in held["account_wait"]
    assert _record(f.steward)["backend_route_id"] == "one"
    assert family_quota.role_availability(_record(f.steward))["status"] == "unavailable"
    # Recovering a route with stale quota must not release its old incident.
    assert family_quota.ensure_role_reserve(record, [_account("one", 90, fresh=False)])
    assert family_quota.role_migration(record) == incident
    assert not family_quota.ensure_role_reserve(record, [_account("one", 90)])
    assert family_quota.role_migration(record) == {}


def test_low_weekly_request_waits_for_the_steward_then_relays_original_content_once(quota_family):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    repo = resident_conversation_stream.get_repository()
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert result["status"] == "waiting_for_quota_review"
    assert resident_execution_work.read(_record(f.member)) == {}
    assert repo.input_state(f.facade, f.message)["state"] == "queued"
    projected = family_facade.project_messages(_record(f.facade), [repo.message(f.facade, f.message)],
                                               input_states={f.message: "queued"})
    assert projected[0]["relay"]["state"] == "quota_review"
    review_id = result["steward_message_id"]
    assert repo.message(f.steward, review_id)["author_id"] == f.facade
    again = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert again == result
    assert len(resident_execution_work.normalized_queue(resident_execution_work.read(_record(f.steward)))) == 0
    result = family_quota.relay_message(f.facade, f.message, f.member, f.steward,
                                      quota_reviewed=True, reason="任务较短，现有额度足够")
    assert result["status"] == "relayed"
    original = repo.message(f.facade, f.message)
    delivered = repo.message(f.member, result["message_id"])
    assert delivered["content_parts"] == original["content_parts"]
    assert delivered["author_id"] == ACTOR
    assert repo.input_state(f.facade, f.message)["state"] == "completed"
    assert family_quota.relay_message(f.facade, f.message, f.member, f.steward,
                                     quota_reviewed=True, reason="已放行") == result
    assert len(resident_execution_work.normalized_queue(resident_execution_work.read(_record(f.member)))) == 0


@pytest.mark.parametrize("weekly,short", [(10, 90), (70, 1)])
def test_member_at_ten_percent_or_low_short_window_does_not_require_weekly_review(quota_family, weekly, short):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    f.accounts[0] = _account("one", weekly, short=short)
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR)["status"] == "relayed"
    assert resident_execution_work.read(_record(f.steward)) == {}


def test_quota_review_cannot_be_approved_by_another_actor_or_relay_cancelled_input(quota_family):
    """Family 周额度预防与监督

    Family 周额度预防与监督；额度复核期间被撤回的请求，steward 放行时回报 withdrawn，不投给成员。

    :design: decision_44cf587c1721
    """
    f = quota_family
    family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    with pytest.raises(ValueError, match="判断理由"):
        family_quota.relay_message(f.facade, f.message, f.member, ACTOR, quota_reviewed=True)
    resident_conversation_stream.get_repository().set_input_state(f.facade, f.message, "cancelled")
    # A request its sender withdrew meanwhile is not let through; the steward hears it was withdrawn.
    assert family_quota.relay_message(
        f.facade, f.message, f.member, f.steward, quota_reviewed=True, reason="continue",
    ) == {"status": "withdrawn", "member_id": f.member}
    assert resident_execution_work.read(_record(f.member)) == {}


def _text_of(message):
    return "".join(part.get("text", "") for part in message["content_parts"])


def test_a_request_no_member_owns_waits_for_the_steward_who_picks_its_member(quota_family, monkeypatch):
    """职责空缺的请求等待 steward 安排成员

    No member's duties cover it: the facade holds it until the steward arranges a member (CASE-A 10-11)."""
    f = quota_family
    f.accounts[0] = _account("one", 90)
    newcomer = f.spare  # the member the steward made for it
    monkeypatch.setattr(family_facade, "family_member_ids", lambda _: {f.member, newcomer})
    repo = resident_conversation_stream.get_repository()

    held = family_quota.relay_message(f.facade, f.message, f.member, ACTOR,
                                      reason="没有人负责数据库", scope_gap="数据库迁移")
    assert held["status"] == "waiting_for_steward" and held["member_id"] == f.member
    assert resident_execution_work.read(_record(f.member)) == {}
    assert repo.input_state(f.facade, f.message)["state"] == "queued"
    notice = repo.message(f.steward, held["steward_message_id"])
    assert notice["author_id"] == f.facade
    assert "职责缺口" in _text_of(notice) and "数据库迁移" in _text_of(notice)
    assert "family-work-guide.md" in _text_of(notice) and "持续检查权限" not in _text_of(notice)
    projected = family_facade.project_messages(_record(f.facade), [repo.message(f.facade, f.message)],
                                               input_states={f.message: "queued"})
    assert projected[0]["relay"] == {"state": "scope_review"}
    # A replayed Router relay, or its sender relaying by hand, leaves it waiting.
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR, scope_gap="数据库迁移") == held
    assert family_quota.relay_message(f.facade, f.message, newcomer, ACTOR)["status"] == "waiting_for_steward"
    with pytest.raises(ValueError, match="判断理由"):
        family_quota.relay_message(f.facade, f.message, newcomer, f.steward, scope_reviewed=True)

    done = family_quota.relay_message(f.facade, f.message, newcomer, f.steward, scope_reviewed=True,
                                      reason="持续的数据库工作，新建成员")
    assert done["status"] == "relayed" and done["member_id"] == newcomer
    delivered = repo.message(newcomer, done["message_id"])
    assert delivered["content_parts"] == repo.message(f.facade, f.message)["content_parts"]
    assert delivered["author_id"] == ACTOR
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR, scope_gap="数据库迁移") == {
        "status": "arranged", "member_id": newcomer, "message_id": done["message_id"]}
    assert resident_execution_work.read(_record(f.member)) == {}


def test_only_a_request_waiting_for_its_member_can_be_arranged(quota_family, monkeypatch):
    """仅职责审核中的请求允许 steward 安排成员"""
    f = quota_family
    f.accounts[0] = _account("one", 90)
    with pytest.raises(ValueError, match="职责空缺"):
        family_quota.relay_message(f.facade, f.message, f.member, f.steward, scope_reviewed=True, reason="我来转交")
    # Without a steward nobody could arrange it, so the closest member takes it.
    monkeypatch.setattr(family_facade, "family_steward_id", lambda _: "")
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR, scope_gap="数据库迁移")["status"] == "relayed"


@pytest.mark.parametrize("typed_question", [False, True])
def test_answer_and_organization_requests_can_reach_own_steward(quota_family, typed_question):
    """owner 引用 steward 经门面发出的消息回复时，回复直接交给 steward

    Steward 经门面发出的普通消息或问题卡片收到引用回答时，quoted_member 与转交授权指向 Steward；原回答及作者完整保留，问题卡片在门面校验，经过验证的原样转交才能在 Steward 处接受；错误单成员选择在尚无副本被接受时允许向 Steward 重试；Steward 处理后改派业务成员，保留原任务文本、作者和转交关联，原问题回答标记留在门面；不做额度复核，没有对应引用或合法转交关联的输入不能使用该问题卡片。

    decision_15f7df737ed5: the steward asks the owner through the facade, and the owner's quoting answer reaches it."""
    from management_portal.society_service import outward_messages
    from management_common.async_questions import question_answer_id, question_message_parts
    from management_portal.society_service.resident_questions import prepare_question_answer
    from tests.cases.isa_test_support import run

    f = quota_family
    repo = resident_conversation_stream.get_repository()
    asked = "msg_00000000000005a1"
    repo.append_message(f.facade, {
        "message_id": asked, "role": "agent", "author_id": f.facade, "created_at": "2026-10-13T01:00:00Z",
        "content_parts": question_message_parts("以哪一份记录为准？", [{"question_index": 0, "title": "以哪一份记录为准？"}], f.facade, asked)
                         if typed_question else [{"type": "text", "text": "两位成员对接口版本的记录矛盾，以哪一份为准？"}],
        "recipients": [{"kind": "user", "id": ACTOR}],
        "origin": outward_messages.message_origin(f.facade, initiated_by=f.steward),
    }, activated_at="2026-10-13T01:00:00Z")
    request = {"content_parts": [{"type": "text", "text": "以 B 的记录为准"}], "quote": {"message_id": asked}}
    if typed_question:
        request["message_id"] = question_answer_id(f.facade, asked)
        request["content_parts"].append({"type": "async_question_answer", "source_message_id": asked,
                                         "answers": [{"question_index": 0, "answer": "以 B 的记录为准"}]})
    answer = run("DeliverMessage", {"resident_id": f.facade, "message": request},
        actor=ACTOR, key="answer-steward").raise_for_error()["message_id"]
    facade = _record(f.facade)
    assert family_facade.quoted_member(facade, repo.message(f.facade, answer)) == f.steward
    assert family_facade.relay_delegates((f.facade, answer), f.steward)
    monkeypatch = pytest.MonkeyPatch()
    try:
        # The waiting label follows the same explicit quote as routing,
        # including a Family with just one business member.
        monkeypatch.setattr(family_facade, "family_members", lambda _: [f.member])
        waiting = family_facade.project_messages(facade, [repo.message(f.facade, answer)],
                                                input_states={answer: "queued"})
        assert waiting[0]["relay"]["resident_id"] == f.steward
    finally:
        monkeypatch.undo()
    # Organization work can reach the own Steward without pretending to answer it.
    assert not family_facade.answers_steward(facade, repo.message(f.facade, f.message))
    assert family_facade.relay_delegates((f.facade, f.message), f.steward)
    if typed_question:
        # Access to the facade alone does not let an unrelated delivery use
        # its question set: only the validated unchanged Family relay can.
        with pytest.raises(ValueError, match="问题不存在"):
            prepare_question_answer(f.steward, repo.message(f.facade, answer), repo)
        # Reproduce the old single-member selection: failed admission leaves
        # a delivery review, but never a member copy. Retrying to the Steward
        # must be possible without duplicating or replacing the original.
        f.accounts[0] = _account("one", 70)
        with pytest.raises(ValueError, match="问题不存在"):
            family_quota.relay_message(f.facade, answer, f.member, ACTOR)
        assert repo.message(f.member, answer) is None
        assert answer not in family_facade.relay_links(facade)
    with pytest.raises(ValueError, match="不属于"):
        family_quota.relay_message(f.facade, f.message, "resident_ffffffffffff", ACTOR)

    # The steward's own week is low (9%), but it keeps its own reserve and is not asked to review itself.
    done = family_quota.relay_message(f.facade, answer, f.steward, ACTOR)
    assert done["status"] == "relayed" and done["member_id"] == f.steward
    delivered = repo.message(f.steward, done["message_id"])
    assert _text_of(delivered).endswith("以 B 的记录为准") and delivered["author_id"] == ACTOR
    assert delivered["content_parts"] == repo.message(f.facade, answer)["content_parts"]
    projected = family_facade.project_messages(facade, [repo.message(f.facade, answer)],
                                               input_states={answer: "completed"})
    assert projected[0]["relay"]["resident_id"] == f.steward
    if typed_question:
        from management_portal.society_service import family_reassign

        # Once the Steward has handled the answer, its task can pass to a
        # business member without pretending that member owns the question.
        original_parts = list(repo.message(f.facade, answer)["content_parts"])
        repo.set_input_state(f.steward, done["message_id"], "completed")
        moved = family_reassign.reassign_request(f.facade, answer, f.member, ACTOR, reason="目标已确认，继续设计方案")
        copy = repo.message(f.member, moved["message_id"])
        assert copy["content_parts"] == [original_parts[0]]
        assert copy["author_id"] == ACTOR
        assert repo.message(f.facade, answer)["content_parts"] == original_parts
        assert family_facade.relay_links(facade)[answer]["resident_id"] == f.member


@pytest.mark.parametrize("weekly,should_migrate", [(9, True), (10, True), (11, False)])
def test_platform_migrates_steward_before_execution_at_or_below_ten(quota_family, monkeypatch, weekly, should_migrate):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    f.accounts[0] = _account("one", weekly)
    resident_intake_domain._commit_resident_intake_state(_record(f.steward), "open")
    calls = []
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *args, **kwargs: calls.append((args, kwargs)))
    before = _record(f.steward)
    assert family_quota.ensure_role_reserve(_record(f.steward), f.accounts) == should_migrate
    assert _record(f.steward) == before
    if should_migrate:
        migration = family_quota.role_migration(before)
        assert migration["attempts"] == 1 and migration["pending_restart"] is None
        assert "family_quota_migration" not in before
        assert calls[0][0][2]["target_account"] == "two"
        assert calls[0][0][2]["context_strategy"] == "compact"
        assert calls[0][1]["portal_reason"] == "steward_weekly_reserve"
        assert family_quota.ensure_role_reserve(_record(f.steward), f.accounts)
        assert len(calls) == 1
    else:
        assert calls == []


@pytest.mark.parametrize("human_initiator", [True, False])
def test_steward_without_a_safe_target_retains_work_and_notifies_once(quota_family, monkeypatch, human_initiator):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    from management_common.principal import message_origin
    from management_portal.society_service.family_owners import register_familizer
    register_familizer(f.facade, 'family-creator')
    job = {"input_message": {"origin": message_origin(f.facade, initiated_by=ACTOR if human_initiator else f.member)}}
    monkeypatch.setattr(resident_execution_work, "read", lambda _: job)
    f.accounts[:] = [_account("one", 9), _account("two", 10)]
    resident_intake_domain._commit_resident_intake_state(_record(f.steward), "open")
    notices = []
    monkeypatch.setattr(lineage_human_notifications, "create_notification", lambda *args, **kwargs: notices.append(kwargs))
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *args, **kwargs: pytest.fail("unsafe target"))
    assert family_quota.ensure_role_reserve(_record(f.steward), f.accounts)
    record = _record(f.steward)
    with family_quota.get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], record['resident_id']) as migration:
        migration["checked_at"] = 0
    assert family_quota.ensure_role_reserve(_record(f.steward), f.accounts)
    assert len(notices) == 1
    assert notices[0]["recipients"] == ["family-creator"]


def test_blocked_steward_recovery_notifies_only_the_failed_inputs_human(quota_family, monkeypatch):
    """steward 恢复受阻仅通知失败输入的人类发起者

    :module: platform.core"""
    from management_common.principal import message_origin
    from management_portal.society_service import resident_work_recovery

    f = quota_family
    message_id = "msg_000000000000000000000000000005a2"
    resident_conversation_stream.get_repository().append_message(f.steward, {
        "message_id": message_id, "role": "user", "author_id": f.facade,
        "origin": message_origin(f.facade, initiated_by="requesting-user"),
        "created_at": "2026-10-13T01:00:00Z", "content_parts": [{"type": "text", "text": "Review"}],
    }, activated_at="2026-10-13T01:00:00Z")
    notices = []
    monkeypatch.setattr(lineage_human_notifications, "create_notification", lambda *args, **kwargs: notices.append(kwargs))
    resident_work_recovery._notify_steward_blocked(_record(f.steward), message_id, "No backend")
    assert notices == []  # No recorded creator: never substitute the latest sender.


def test_proactive_migration_intent_survives_interruption(quota_family, monkeypatch):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    resident_intake_domain._commit_resident_intake_state(_record(f.steward), "open")
    calls = []
    def restart(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            raise RuntimeError("service interrupted")
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", restart)
    with pytest.raises(RuntimeError):
        family_quota.ensure_role_reserve(_record(f.steward), f.accounts)
    record = _record(f.steward)
    with family_quota.get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], record['resident_id']) as migration:
        migration["checked_at"] = 0
    assert family_quota.ensure_role_reserve(_record(f.steward), f.accounts)
    assert calls[0] == calls[1]
    assert family_quota.role_migration(_record(f.steward))["attempts"] == 1
    assert "family_quota_migration" not in _record(f.steward)


@pytest.mark.parametrize("boundary", ["family_paused", "executing", "stopped"])
def test_steward_reserve_preserves_manual_boundaries_and_an_admitted_turn(quota_family, monkeypatch, boundary):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    f = quota_family
    if boundary == "family_paused":
        resident_intake_domain._commit_resident_intake_state(_record(f.facade), "closed")
    else:
        resident_intake_domain._commit_resident_intake_state(_record(f.steward), "open")
        monkeypatch.setattr(resident_execution_work, "read", lambda _: {
             "execution_id": "execution_running" if boundary == "executing" else "",
            "stop_requested_at": "2026-10-07T00:00:00Z" if boundary == "stopped" else "",
         "submitted_at": "2026-10-14T00:00:00Z"})
        monkeypatch.setattr(resident_execution_work, "is_active", lambda _: True)
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *args, **kwargs: pytest.fail("must preserve boundary"))
    assert not family_quota.ensure_role_reserve(_record(f.steward), f.accounts)


@pytest.mark.parametrize("remaining,short,fresh,expected", [
    (80, 9, True, 9), (10, 80, True, 10), (0, 80, False, None),
])
def test_observed_reserve_uses_fresh_limiting_window(remaining, short, fresh, expected):
    """执行前迁移使用新鲜的最短额度窗口。"""
    assert accounts_policy.reserve_remaining(_account("one", remaining, short=short, fresh=fresh)) == expected


def test_invalid_and_stale_observations_are_not_exhaustion():
    """未知与非法窗口不当成零，明确耗尽的计费账号仍触发。"""
    for value in (None, float("nan"), float("inf"), True, -1, 101):
        account = _account("one", 50)
        for window in account["quota_reserve"]["windows"]:
            window["remaining_percent"] = value
        assert accounts_policy.reserve_remaining(account) is None
    assert accounts_policy.reserve_remaining({"quota_reserve": {"fresh": True, "remaining": 0}}) == 0
    assert accounts_policy.reserve_remaining({"quota_reserve": {"fresh": False, "remaining": 0}}) is None


def test_role_intake_reopens_even_when_account_service_is_down(quota_family, monkeypatch):
    f = quota_family
    def unavailable():
        raise RuntimeError("account service unavailable")
    monkeypatch.setattr(execution_port, "service", unavailable)
    family_quota.reconcile_role_reserves([_record(f.steward)])
    assert _record(f.steward)["intake_open"] is True
    assert not (_record(f.steward).get("quota_reserve_override") or {}).get("enabled")
    assert not family_quota.role_migration(_record(f.steward)).get("owner_notified")


def test_wait_migration_preserves_a_steward_input_that_has_not_entered_execution(monkeypatch):
    """Family 周额度预防与监督

    :design: decision_44cf587c1721
    """
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.society_service import resident_state

    record, operations, _saved = _restart_domain(monkeypatch)
    pending = {'execution_id': "job_pending", "user_message_id": "msg_pending", "actor": "alice",
               resident_state.INTAKE_FIELD: resident_state.INTAKE_OPEN, "activation_policy": "enqueue_and_trigger"}
    monkeypatch.setattr(resident_execution_work, "read", lambda _: pending)
    monkeypatch.setattr(resident_execution_work, "is_active", lambda j: bool(j.get('execution_id')))
    monkeypatch.setattr(resident_execution_work, "write", lambda _r, j: pending.update(j))
    monkeypatch.setattr(resident_conversation_stream.get_repository(), "set_state", lambda *args, **kwargs: None)
    result = resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_account": "one", "active_execution_policy": "wait", "context_strategy": "compact",
    })
    assert result["status"] == "accepted"
    assert result["preserved_held_input"] == {'execution_id': "job_pending", "message_id": "msg_pending"}
    resident_restart_domain._resume_resident_session_restart(operations[-1]["step"])
    assert record["session_restart"]["status"] == "completed"
    assert (pending['execution_id'], pending["user_message_id"], pending["actor"]) == ("job_pending", "msg_pending", "alice")


@pytest.mark.parametrize("weekly", [9, 70])
def test_selected_member_effect_keeps_admission_and_original_request(quota_family, weekly):
    """Router 选择成员后仍保留配额准入和原请求内容

    :module: platform.core"""
    from management_portal.society_service import family_definition, lineage_effect_script

    f = quota_family
    f.accounts[0] = _account("one", weekly)
    # Closing intake holds the delivered request in the member queue.
    resident_intake_domain._commit_resident_intake_state(_record(f.member), "closed")
    calls, results = [], []

    def execute(call):
        calls.append(call.instruction)
        assert call.instruction == "RelayFamilyMessage"
        result = family_quota.relay_message(call.arguments["resident_id"], call.arguments["message_id"],
                                           call.arguments["member_id"], ACTOR)
        results.append(result)
        return lineage_effect_script.CallResult(True, result)

    inputs = {"arguments": {"members": [f.member], "steward": f.steward, "threshold": 60},
              "event": {"payload": {"input_business_ref": f"family-route/{f.facade}/message/{f.message}"}},
              "packet": {"message": {"data": {"target": f.member, "confidence": 100, "scope": "in"}}}}
    script = lineage_effect_script.parse(family_definition.FAMILY_ROUTE_RELAY_SCRIPT)
    outcome = lineage_effect_script.Interpreter(script, inputs, execute).run()
    replay = lineage_effect_script.Interpreter(script, inputs, execute).run()
    assert outcome.status == replay.status == "succeeded" and calls == ["RelayFamilyMessage"] * 2
    assert replay.outputs == outcome.outputs
    repo = resident_conversation_stream.get_repository()
    if weekly < 10:
        assert results[0]["status"] == "waiting_for_quota_review"
        assert repo.input_state(f.facade, f.message)["state"] == "queued"
    else:
        assert results[0]["status"] == "relayed"
        link = family_facade.relay_links(_record(f.facade))[f.message]
        delivered = repo.message(f.member, link["message_id"])
        assert delivered["author_id"] == ACTOR
        assert delivered["content_parts"] == repo.message(f.facade, f.message)["content_parts"]
        assert repo.input_state(f.member, link["message_id"])["state"] == "queued"


def test_organization_request_reaches_steward_once_without_business_quota_review(quota_family):
    """本 Steward 接组织原文，不需要引用或把自己加入业务名册。"""
    f = quota_family
    repo = resident_conversation_stream.get_repository()
    original = repo.message(f.facade, f.message)
    done = family_quota.relay_message(f.facade, f.message, f.steward, ACTOR)
    assert done["status"] == "relayed"
    assert family_quota.relay_message(f.facade, f.message, f.steward, ACTOR) == done
    delivered = repo.message(f.steward, done["message_id"])
    assert delivered["content_parts"] == original["content_parts"]
    assert delivered["author_id"] == original["author_id"]
