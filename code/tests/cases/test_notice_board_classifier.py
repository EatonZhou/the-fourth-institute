from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest


from management_portal.notice_board_backend import NoticeBoardBackend
from management_portal.notice_board_classifier import NoticeBoardClassifier
from tests.cases.registry_support import put_registry_document


@pytest.fixture
def classifier_clock(monkeypatch):
    from management_portal import notice_board_classifier as module
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        time=lambda: clock.value, monotonic=lambda: clock.value))
    monkeypatch.setattr(module, "_now_iso", lambda: f"observation-{clock.value}")
    return clock


def install_observer(monkeypatch, observer):
    from management_portal import notice_board_classifier as module
    monkeypatch.setattr(module, "observe_invocation", observer)


def seed_job(status="submitted", **extra):
    from management_portal import notice_board_classifier as module
    item = {"post_id": "notice_test", "status": status,
            "resident_id": "resident_test", "lineage_id": "lineage_test",
            "message_id": "msg_test", "last_error": "", "updated_at": "original",
            "created_at": "2026-01-01", **extra}
    put_registry_document(module.STATE_COLLECTION, item["post_id"], item)
    return item


def snapshot():
    from management_portal import notice_board_classifier as module
    return module.get_registry_store().snapshot(module.STATE_COLLECTION)


def test_waiting_input_has_no_error_or_registry_write(monkeypatch, classifier_clock):
    """等待中的原输入不改写任务、时间戳或集合版本，也不提交新输入。"""
    seed_job()
    classifier = NoticeBoardClassifier(SimpleNamespace())
    before = snapshot()
    observed = []
    def observe(**kwargs):
        observed.append(kwargs)
        return {"input_state": {"state": "running"}, "result": None}
    install_observer(monkeypatch, observe)
    classifier._reconcile(classifier.items()["notice_test"])
    assert snapshot() == before
    assert observed == [{"actor": "portal", "resident_id": "resident_test",
                         "message_id": "msg_test", "lineage_id": "lineage_test"}]


def test_repeated_error_is_stable_but_changed_error_and_recovery_are_saved(monkeypatch, classifier_clock):
    """相同观察错误不刷新版本；错误变化和恢复等待都保留一次可见变化。"""
    item = seed_job()
    classifier = NoticeBoardClassifier(SimpleNamespace())
    problem = ["transport unavailable"]
    def observe(**kwargs):
        raise OSError(problem[0])
    install_observer(monkeypatch, observe)
    classifier._reconcile(item)
    failed = snapshot()
    classifier_clock.value += 5
    classifier._reconcile(item)
    assert snapshot() == failed
    problem[0] = "permission denied"
    classifier._reconcile(item)
    assert snapshot()[0] > failed[0]
    assert classifier.items()[item["post_id"]]["last_error"] == problem[0]
    install_observer(monkeypatch, lambda **kwargs: {"input_state": {"state": "running"}, "result": None})
    classifier._reconcile(item)
    assert classifier.items()[item["post_id"]]["last_error"] == ""


@pytest.mark.parametrize("outcome", ["pending", "transport"])
def test_workflow_observation_does_not_persist_poll_deadlines(monkeypatch, classifier_clock, outcome):
    """未确认流程保留原路径，重复等待或同一错误不重写重试时间、不重触发。"""
    from management_portal import notice_board_classifier as module
    handle = {"event_id": "event_original", "trigger_path_id": "trigger_original"}
    item = seed_job("workflow_pending", workflow_handle=handle, retry_after=90)
    classifier = NoticeBoardClassifier(SimpleNamespace())
    def observe(original, **kwargs):
        assert original["event_id"] == "event_original"
        assert kwargs == {"actor": "portal", "entry": "system"}
        if outcome == "pending":
            raise module.WorkflowPending(handle, {})
        raise OSError("transport unavailable")
    monkeypatch.setattr(module, "WorkflowBindings", lambda: SimpleNamespace(observe=observe))
    classifier._observe_workflow(item)
    first = snapshot()
    classifier_clock.value += 5
    classifier._observe_workflow(classifier.items()[item["post_id"]])
    assert snapshot() == first
    assert classifier.items()[item["post_id"]]["retry_after"] == 90


def test_same_state_update_does_not_advance_timestamp_or_revision(classifier_clock):
    """只有语义状态变化才能保存，新时间戳自身不构成业务变化。"""
    seed_job()
    worker = NoticeBoardClassifier(SimpleNamespace())
    before = snapshot()
    worker._update_item("notice_test", status="submitted", last_error="")
    assert snapshot() == before


def test_poll_cadence_allows_other_jobs_and_resumes_original_after_restart(monkeypatch, classifier_clock):
    """每条任务按节拍观察，其间可推进其他任务，进程重建只观察原输入。"""
    first = seed_job()
    second = seed_job(post_id="notice_second", message_id="msg_second", created_at="2026-01-02")
    seen = []
    def observe(**kwargs):
        seen.append(kwargs["message_id"])
        return {"input_state": {"state": "running"}, "result": None}
    install_observer(monkeypatch, observe)
    worker = NoticeBoardClassifier(SimpleNamespace(), poll_seconds=5)
    assert worker.run_once()
    assert worker.run_once()
    assert seen == [first["message_id"], second["message_id"]]
    assert worker.run_once() is False
    classifier_clock.value += 5
    assert worker.run_once()
    assert seen[-1] == first["message_id"]
    restarted = NoticeBoardClassifier(SimpleNamespace(), poll_seconds=5)
    assert restarted.run_once()
    assert seen[-1] == first["message_id"]


def test_worker_waits_for_poll_interval_after_immediate_observation_error(monkeypatch, classifier_clock):
    """持续观察错误不产生0.2秒忙循环，停止信号仍可结束等待。"""
    seed_job()
    worker = NoticeBoardClassifier(SimpleNamespace(), poll_seconds=5)
    seen = []
    def observe(**kwargs):
        seen.append(classifier_clock.value)
        raise OSError("unchanged")
    install_observer(monkeypatch, observe)
    waits = []
    class Wake:
        def clear(self):
            pass
        def wait(self, delay):
            waits.append(delay)
            classifier_clock.value += delay
            if len(waits) == 2:
                worker._stop.set()
    worker._wake = Wake()
    worker._run()
    assert waits == [5, 5]
    assert seen == [100, 105]


def test_completed_classification_clears_previous_observation_error(monkeypatch):
    """一次原输入恢复完成后分类可见，旧观察错误不残留在完成状态。"""
    item = seed_job(last_error="old transport failure", source_fingerprint="original")
    applied = []
    worker = NoticeBoardClassifier(SimpleNamespace(
        apply_automatic_category=lambda *args, **kwargs: applied.append((args, kwargs))))
    install_observer(monkeypatch, lambda **kwargs: {"result": {"message": '{"category":"request"}'}})
    worker._reconcile(item)
    current = worker.items()[item["post_id"]]
    assert current["status"] == "completed"
    assert current["last_error"] == ""
    assert applied == [((item["post_id"],), {"expected_fingerprint": "original", "category": "request"})]


def test_workflow_terminal_failure_keeps_newly_resolved_provenance(monkeypatch):
    """观察到原流程确定失败时保存新的失败回执，保留原事件且不再提交。"""
    from management_portal import notice_board_classifier as module
    original = {"event_id": "event_original", "trigger_path_id": "path_original"}
    item = seed_job("workflow_pending", workflow_handle=original)
    failure = module.WorkflowFailed(original, {"instruction_id": "inst_original", "submission_reason": "denied"})
    def observe(*args, **kwargs):
        raise failure
    monkeypatch.setattr(module, "WorkflowBindings", lambda: SimpleNamespace(observe=observe))
    worker = NoticeBoardClassifier(SimpleNamespace())
    worker._observe_workflow(item)
    current = worker.items()[item["post_id"]]
    assert current["status"] == "failed"
    assert current["workflow_handle"] == failure.handle
    assert current["last_error"] == str(failure)


def test_workflow_acceptance_continues_same_input_without_submit(monkeypatch, classifier_clock):
    """等待流程取得原输入后只观察该输入，不重新投递。"""
    from management_portal import notice_board_classifier as module
    item = seed_job("workflow_pending", workflow_handle={"event_id": "event_original"})
    receipt = {"resident_id": "resident_test", "lineage_id": "lineage_test", "message_id": "msg_original",
               "event_id": "event_original", "trigger_path_id": "path_original"}
    monkeypatch.setattr(module, "WorkflowBindings", lambda: SimpleNamespace(observe=lambda *args, **kwargs: receipt))
    def no_submit(*args, **kwargs):
        raise AssertionError("must not submit again")
    worker = NoticeBoardClassifier(SimpleNamespace(), deliver=no_submit, poll_seconds=5)
    observed = []
    def observe(**kwargs):
        observed.append(kwargs["message_id"])
        return {"result": None}
    install_observer(monkeypatch, observe)
    assert worker.run_once()
    assert worker.items()[item["post_id"]]["message_id"] == "msg_original"
    classifier_clock.value += 5
    assert worker.run_once()
    assert observed == ["msg_original"]


def test_classifier_hands_the_post_to_the_configured_resident_and_corrects_category(
    tmp_path: Path,
    monkeypatch,
    classifier_clock,
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
    install_observer(monkeypatch, lambda **_kwargs: {"result": {"message": '```json\n{"category":"request"}\n```'}})
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

    classifier_clock.value += classifier.poll_seconds
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
            install_observer(monkeypatch, observe)
            classifier._reconcile(item)
            actual = classifier.items()[item["post_id"]]
            assert actual["status"] == getattr(failure, "status", "submitted")
            assert actual["message_id"] == "msg_test"
