"""Completed organization work reconciles the original failure, without new work.

:module: lineage.family
:design: decision_a64b3ea2c3c5
"""
import json
import uuid

import pytest

from management_portal.society_service import (
    family_facade,
    family_recovery,
    family_supervision,
    resident_access,
    resident_conversation_stream,
)
from management_portal.society_service.resident_input_queue_domain import build_message as _message
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_family_reassign import family, delivery_resident, future_creation_store  # noqa: F401


def prepared(family, *, legacy=False, state="failed", source_state="completed", wrong_root=False, author=None):
    repo = resident_conversation_stream.get_repository()
    facade_id, steward = family["facade"], family["steward"]
    root = _message("user", "创建 README 成员；不启动撰写", "sample_admin")
    repo.append_message(facade_id, root)
    repo.set_input_state(facade_id, root["message_id"], state, error={"message": "旧 Router 拒绝 Steward"} if state == "failed" else None)
    record = _find_resident(facade_id)
    mid = "msg_" + uuid.uuid4().hex if wrong_root else root["message_id"]
    facts = dict(facade=facade_id, message_id=mid, lineage_id=record["lineage_id"], family_id=record["family"]["family_id"])
    source = _message("user", "复核原请求\n" + json.dumps(facts), author or facade_id)
    source["business_ref"] = "duty/family-route_review" if legacy else f"family-review/{facade_id}/message/{mid}"
    repo.append_message(steward, source)
    reply = _message("agent", "已创建并入册；没有启动撰写。", steward, reply_to_message_id=source["message_id"])
    repo.append_message(steward, reply)
    repo.set_input_state(steward, source["message_id"], source_state)
    return root["message_id"], source["message_id"], reply["message_id"]


def archive(family, root, source, actor="sample_admin"):
    return run("ResolveFamilyRequest", dict(resident_id=family["facade"], message_id=root,
               steward_message_id=source, reason="复核已完成原组织目标，复用真实创建结果"), actor=actor)


@pytest.mark.parametrize("legacy", [False, True])
def test_archive_preserves_original_failure_and_projects_real_result_once(family, legacy):
    root, source, reply = prepared(family, legacy=legacy)
    facade_id = family["facade"]
    repo = resident_conversation_stream.get_repository()
    before = repo.message(facade_id, root)
    failed = repo.input_state(facade_id, root)
    result = archive(family, root, source).raise_for_error()
    assert result["reply_message_ids"] == [reply]
    assert archive(family, root, source).raise_for_error() == result
    assert repo.message(facade_id, root) == before
    assert repo.input_state(facade_id, root) == failed
    projected = run("GetInputState", dict(resident_id=facade_id, message_id=root), actor="sample_admin").raise_for_error()["input_state"]
    assert projected["state"] == "completed" and projected["reply_message_ids"] == [reply]
    shown = run("GetMessage", dict(resident_id=facade_id, message_id=reply), actor="sample_admin").raise_for_error()["message"]
    assert shown["message_id"] == reply
    assert "已创建并入册" in str(shown)
    record = _find_resident(facade_id)
    assert family_supervision._observe(record, dict(request_message_id=root, resident_id=facade_id, message_id=root))[0] == "resolved"
    assert family_facade.relay_links(record)[root]["archive"]["failed_receipt"] == failed


@pytest.mark.parametrize("options", [dict(state="cancelled"), dict(state="queued"), dict(source_state="running"),
                                      dict(wrong_root=True), dict(legacy=True, wrong_root=True), dict(author="sample_admin")])
def test_archive_rejects_unfinished_cancelled_or_unrelated_results(family, options):
    root, source, _ = prepared(family, **options)
    assert archive(family, root, source).error
    assert root not in family_facade.relay_links(_find_resident(family["facade"]))


def test_archive_rejects_outsider_and_existing_work(family, monkeypatch):
    from management_portal.society_service import resident_directory_domain
    root, source, _ = prepared(family)
    monkeypatch.setattr(resident_access, "can_manage", lambda *args: False)
    with pytest.raises(PermissionError):
        family_recovery.archive_result(family["facade"], root, source, family["wrong"], reason="非管理者")
    # The own steward remains authorized without borrowing the request author.
    family_recovery.archive_result(family["facade"], root, source, family["steward"], reason="核验组织成果")
    _, other, _ = prepared(family)
    with pytest.raises(ValueError):
        family_recovery.archive_result(family["facade"], root, other, family["steward"], reason="不能覆盖关联")


def test_relay_target_includes_only_own_steward_and_roster(family):
    record = _find_resident(family["facade"])
    assert family_facade.is_relay_target(record, family["steward"])
    assert family_facade.relay_delegates((family["facade"], "unused"), family["steward"])
    assert not family_facade.is_relay_target(record, "resident_ffffffffffff")
    assert not family_facade.is_relay_target(record, "")


def test_shipped_role_guides_upgrade_but_custom_edits_survive():
    """只迁移精确匹配的旧默认，保留使用者编辑。"""
    from management_portal.society_service import family_tasks
    for key, old in [("route", family_tasks._OLD_ROUTE_GUIDE), ("route_review", family_tasks._OLD_ROUTE_REVIEW_GUIDE)]:
        assert family_tasks.saved_fields(key, {"guide": old, "prompt": "custom"}) == {"prompt": "custom"}
        assert family_tasks.saved_fields(key, {"guide": "custom", "prompt": "custom"}) == {"guide": "custom", "prompt": "custom"}


def test_empty_roster_arrival_keeps_original_event_when_steward_exists(family, monkeypatch):
    """空业务名册不吞掉组织请求事件，也不复制原输入。"""
    from management_portal.society_service import lineage_message_events
    root, _, _ = prepared(family, state="queued")
    repo = resident_conversation_stream.get_repository()
    monkeypatch.setattr(family_facade, "family_members", lambda _: [])
    record = _find_resident(family["facade"])
    detail = lineage_message_events.arrival_details(repo, family["facade"], root, family["steward"], record)
    assert detail["message_id"] == root
    assert detail["author_id"] == "sample_admin"
