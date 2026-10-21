from __future__ import annotations

from pathlib import Path
import pytest


from management_portal.notice_board_backend import NoticeBoardBackend
from management_portal.notice_board_classifier import NoticeBoardClassifier
from tests.cases.registry_support import put_registry_document


def test_classifier_hands_the_post_to_the_configured_resident_and_corrects_category(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """分类器把帖子交给配置的分类 Resident，并按其回复更正帖子分类"""
    from management_portal import notice_board_classifier as module

    board = NoticeBoardBackend()
    created = board.create_post(
        "alice",
        "希望增加 CLI 版本",
        "这是一个新的 Portal 功能建议。",
        "chat",
    )
    monkeypatch.setattr(module, "resume_and_wait", lambda **_kwargs: {"message": '```json\n{"category":"request"}\n```'})
    monkeypatch.setattr(module.application_targets, "resident", lambda application: {
        "notice_classifier": {"resident_id": "resident_classifier", "lineage_id": "lineage_classifier"}}[application])
    deliveries = []

    def deliver(text, **kwargs):
        deliveries.append({"text": text, **kwargs})
        return {"resident_id": "resident_classifier", "message_id": "msg_classifier"}

    classifier = NoticeBoardClassifier(board, deliver=deliver, poll_seconds=0.01)
    classifier.start = lambda: None

    classifier.enqueue(created["post"]["post_id"])
    assert classifier.run_once() is True
    assert len(deliveries) == 1
    request = deliveries[0]
    assert request["actor"] == "portal"
    assert request["business_ref"] == f"notice-category:{created['post']['post_id']}"
    assert "希望增加 CLI 版本" in request["text"]

    assert classifier.run_once() is True
    post = board.get_post("alice", created["post"]["post_id"])["post"]
    assert post["category"] == "request"
    assert classifier.items()[post["post_id"]]["status"] == "completed"


def test_classifier_does_not_override_a_post_edited_after_enqueue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """帖子入队后被作者编辑时，分类器不覆盖作者的分类并把任务标为过期"""
    board = NoticeBoardBackend()
    created = board.create_post("alice", "First title", "Initial body", "other")
    post_id = created["post"]["post_id"]
    classifier = NoticeBoardClassifier(
        board,
        deliver=lambda duty, text, **kwargs: {"resident_id": "resident_classifier", "message_id": "msg_classifier"},
    )
    classifier.start = lambda: None
    classifier.enqueue(post_id)
    board.update_post("alice", post_id, "Updated title", "Updated body", "chat")

    assert classifier.run_once() is True
    assert board.get_post("alice", post_id)["post"]["category"] == "chat"
    assert classifier.items()[post_id]["status"] == "stale"


def test_classifier_rejects_categories_outside_notice_board_contract() -> None:
    """分类器拒绝公告板分类之外的类别"""
    try:
        NoticeBoardClassifier._parse_category('{"category":"announcement"}')
    except ValueError as exc:
        assert "invalid category" in str(exc)
    else:
        raise AssertionError("invalid classifier category was accepted")


def test_classifier_observes_same_input_and_terminates_actual_failure(tmp_path, monkeypatch, subtests):
    """分类器继续观察同一条输入，观察超时保持已提交，输入失败或取消时终止任务"""
    from management_portal import notice_board_classifier as module
    from management_portal.resident_handoff import ResidentInputTerminated
    for failure in (TimeoutError("observation budget"), ResidentInputTerminated({"state": "failed", "error": {"message": "provider failed"}}),
                    ResidentInputTerminated({"state": "cancelled"})):
        with subtests.test(failure=type(failure).__name__, status=getattr(failure, "status", "submitted")):
            board = NoticeBoardBackend()
            classifier = NoticeBoardClassifier(board)
            classifier.start = lambda: None
            item = {"post_id": "notice_test", "status": "submitted", "resident_id": "resident_test",
                    "lineage_id": "lineage_test", "message_id": "msg_test"}
            put_registry_document(module.STATE_COLLECTION, item["post_id"], item)
            def observe(**kwargs):
                assert kwargs["message_id"] == "msg_test"
                raise failure
            monkeypatch.setattr(module, "resume_and_wait", observe)
            classifier._reconcile(item)
            actual = classifier.items()[item["post_id"]]
            assert actual["status"] == getattr(failure, "status", "submitted")
            assert actual["message_id"] == "msg_test"
