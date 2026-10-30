"""An Effect call whose outcome cannot become known stops retrying and asks people (decision_4adcc1ff2f2c).

A retry with the same idempotency key is answered from the owner's stored
receipt. Once that receipt is final and still says the outcome is unknown,
retrying only reads it again: the queued occurrence is held for
ResolveEffectActivation, the wire delivers its later Events, and the
Effect's failure recipients are told.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""

from __future__ import annotations

import pytest

from management_portal.hatg import routing
from management_portal.portal_access_control import get_portal_access_store
from management_portal.society_service import (
    effect_disposition, lineage_domain, lineage_human_notifications, lineage_program_backlog,
    lineage_program_runtime as runtime, lineage_program_wire_queue, outward_messages,
)
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service import lineage_aggregate
from tests.cases.test_effect_script import _Outcome
from tests.cases.test_isa_audit_g4 import event_wire, make_lineage, source
from tests.cases.test_lineage_generic_primitives import ACTOR, primitive_store  # noqa: F401

EFFECT = "effect_aaaaaaaaaaaa"
WIRE = "wire_start"
SOURCE = "event_source_start"


def _receipt(*, replayed: bool, code: str = "instruction.failed", commit_state: str = "unknown") -> _Outcome:
    outcome = _Outcome("failed", error={
        "code": code, "message": "执行已中断，业务结果未获确认", "retryable": False, "commit_state": commit_state,
    }, instruction_id="inst_unknown_hold")
    outcome.replayed = replayed
    return outcome


ASKED: list[dict] = []


def _setup(monkeypatch, answers):
    calls: list[str] = []
    ASKED.clear()

    def post(resident_id, users, text, **kwargs):
        ASKED.append({"leader": resident_id, "recipients": users, "text": text, **kwargs})
        return {"unreached": []}

    monkeypatch.setattr(outward_messages, "post_platform_notice", post)

    def execute(call, context, instruction_id=""):
        calls.append(call["instruction"])
        answer = answers[min(len(calls), len(answers)) - 1]
        return answer() if callable(answer) else answer

    monkeypatch.setattr(routing, "execute", execute)
    get_portal_access_store().upsert_user(ACTOR, actor="portal", status="active")
    lineage = make_lineage(
        [source(), {"id": EFFECT, "kind": "effect", "config": {"script": "GetLineage", "failure_recipients": [ACTOR]}}],
        [{**event_wire({"unavailable_policy": "queue"}), "target": EFFECT}],
    )
    return lineage["lineage_id"], calls


def _pending(lineage_id: str) -> list[dict]:
    return list((runtime.read_runtime(lineage_id)["event_wires"].get(WIRE) or {}).get("pending_events") or [])


def _make_retry_due(lineage_id: str) -> None:
    with runtime._runtime_transaction(lineage_id) as state:
        for item in state["event_wires"][WIRE]["pending_events"]:
            item.pop("retry_at", None)


def _drain(lineage_id: str) -> dict:
    return lineage_program_backlog.drain_event_backlog(
        lineage_id, ACTOR, authority=lineage_domain._program_control_authority(),
    )


def _asked(lineage_id: str) -> list[dict]:
    """Requests written in the Leader's name (by message ID) and Lineage notifications."""
    written = {item["message_id"]: item for item in ASKED}
    notices = [{"text": item["title"] + "\n\n" + item["message"], **item}
               for group in lineage_human_notifications.list_actor_notifications(ACTOR, [lineage_id])
               for item in group["notifications"]]
    return [*written.values(), *notices]


def test_unknown_outcome_read_again_from_its_receipt_stops_retrying_and_asks_people(primitive_store, monkeypatch):
    """结果未知的调用重试时只读回同一张已终结回执，就停止重试、放行连线并通知收件人处置

    第一次调用结果未知，触发 Event 时随即重试一次；重试读回的仍是同一张结果未知的终结回执，这次待投递项
    改为等待处置。之后处理积压不再尝试它，也不再为它安排重试；同一连线后来的 Event 照常投递；Effect 的
    失败通知收件人收到一条请人处置的通知，重复处理不重复通知；用 ResolveEffectActivation 处置后这项待投递
    随之结束。
    """
    lineage_id, calls = _setup(monkeypatch, [
        lambda: _receipt(replayed=False),
        lambda: _receipt(replayed=True),
        lambda: _Outcome("committed", {}, instruction_id="inst_unknown_next"),
    ])
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert calls == ["GetLineage"]
    assert _pending(lineage_id)[0].get("retry_at")
    assert _drain(lineage_id)["attempted_count"] == 0
    _make_retry_due(lineage_id)
    _drain(lineage_id)
    assert calls == ["GetLineage", "GetLineage"]
    held = _pending(lineage_id)
    assert len(held) == 1 and "retry_at" not in held[0]
    hold = held[0]["recovery_blocked"]
    assert hold["requires_resolution"] is True and hold["effect_id"] == EFFECT
    activation_key = hold["activation_key"]
    assert effect_disposition.can_resolve(
        lineage_id, activation_key, get_lineage_data_store().effect_work.get(lineage_id, activation_key),
    )
    assert lineage_program_wire_queue.next_retry_seconds(lineage_id) == 0
    with get_lineage_data_store().database.read() as connection:
        from management_portal.society_service import program_work_storage
        assert lineage_id not in program_work_storage.recovery_lineage_ids(connection)

    result = _drain(lineage_id)
    assert result["attempted_count"] == 0 and result["retry_after_seconds"] == 0
    assert calls == ["GetLineage", "GetLineage"]
    [asked] = _asked(lineage_id)
    assert asked["leader"] == lineage_aggregate.read_lineage_state(lineage_id)["leader_resident_id"]
    assert asked["recipients"] == [ACTOR]
    assert asked["text"].startswith("Effect 结果未知，等待处置\n\n")
    assert EFFECT in asked["text"] and activation_key in asked["text"] and hold["trigger_path_id"] in asked["text"]

    # The wire is not held behind it: the next Event runs the Effect again.
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert calls == ["GetLineage", "GetLineage", "GetLineage"]
    assert [item["recovery_blocked"]["activation_key"] for item in _pending(lineage_id)] == [activation_key]
    assert len(_asked(lineage_id)) == 1

    effect_disposition.resolve(lineage_id, activation_key, "failed", ACTOR, note="已核对：没有执行")
    assert _pending(lineage_id) == []


def test_first_delivery_answered_from_a_final_unknown_receipt_is_held_at_once(primitive_store, monkeypatch):
    """首次投递就读回结果未知的终结回执时立即等待处置，不再重试

    执行方已经为这个幂等键保存了结果未知的终结回执（例如上次调用后、写入恢复日志前进程退出），触发 Event 时
    的第一次调用就读回它：待投递项直接等待处置并通知收件人，随后的积压处理不再调用。
    """
    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=True)])
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert calls == ["GetLineage"]
    [held] = _pending(lineage_id)
    assert held["recovery_blocked"]["requires_resolution"] is True and "retry_at" not in held
    assert len(_asked(lineage_id)) == 1


@pytest.mark.parametrize("second", [
    pytest.param(lambda: _receipt(replayed=False), id="fresh-attempt"),
    pytest.param(lambda: _receipt(replayed=True, code="instruction.projection_pending", commit_state="committed"),
                 id="projection-pending"),
])
def test_unknown_outcome_keeps_retrying_while_a_retry_can_change_it(primitive_store, monkeypatch, second):
    """重试还可能改变结果时照常按间隔重试

    重试没有读回终结回执（例如调用根本没到达执行方），或回执说明业务已提交、投影还能补齐时，
    待投递项继续按间隔重试，不进入等待处置，也不发通知。
    """
    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=False), second])
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert calls == ["GetLineage"]
    assert _pending(lineage_id)[0].get("retry_at")
    assert _drain(lineage_id)["attempted_count"] == 0
    _make_retry_due(lineage_id)
    _drain(lineage_id)
    assert calls == ["GetLineage", "GetLineage"]
    for attempts in (3, 4):
        pending = _pending(lineage_id)
        assert len(pending) == 1 and pending[0].get("retry_at") and not pending[0].get("recovery_blocked")
        _make_retry_due(lineage_id)
        _drain(lineage_id)
        assert len(calls) == attempts
    assert _asked(lineage_id) == []


def test_a_failing_resolution_check_still_finishes_the_claim_and_retries(primitive_store, monkeypatch):
    """判断是否等待处置时出错，这次认领照常结束并按间隔重试

    处理积压在认领收尾时判断这次投递要不要等待处置。判断本身出错时，待投递项不停在投递中，
    照常安排重试，下一次处理积压会再调用。
    """
    from management_portal.society_service import lineage_event_delivery

    def broken(*_):
        raise ValueError("unexpected dispatch result")

    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=False), lambda: _receipt(replayed=True)])
    monkeypatch.setattr(lineage_event_delivery, "resolution_hold", broken)
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert calls == ["GetLineage"]
    assert _pending(lineage_id)[0].get("retry_at")
    assert _drain(lineage_id)["attempted_count"] == 0
    _make_retry_due(lineage_id)
    _drain(lineage_id)
    assert calls == ["GetLineage", "GetLineage"]
    [pending] = _pending(lineage_id)
    assert pending.get("retry_at") and not pending.get("recovery_blocked")
    _make_retry_due(lineage_id)
    _drain(lineage_id)
    assert len(calls) == 3


def test_a_resolution_hold_is_found_past_results_that_are_not_records():
    """投递结果里混有非记录项时跳过它们，仍能找到等待处置的那一项"""
    from management_portal.society_service import lineage_event_delivery as delivery

    item = {"wire_id": WIRE, "status": "failed", "typed_outputs": {
        "code": delivery.RESOLUTION_REQUIRED, "message": "结果未知", "details": {"effect_id": EFFECT}}}
    hold = delivery.resolution_hold({"results": ["unexpected", None, item]}, WIRE)
    assert hold is not None and hold["requires_resolution"] is True and hold["effect_id"] == EFFECT


HOLD = {"requires_resolution": True, "activation_key": "effect:" + "a" * 40, "effect_id": EFFECT,
        "trigger_path_id": "trigger_path_aaaaaaaaaaaa", "instruction": "RunScript", "instruction_id": "inst_x",
        "receipt": "执行已中断", "error": ""}


@pytest.mark.parametrize("leader", ["resident_aaaaaaaaaaaa", ""])
def test_a_resolution_request_without_effect_recipients_asks_the_lineage_admins(monkeypatch, leader):
    """Effect 没有失败收件人时，请人处置改问 Lineage 管理员，以 Leader 署名，没有 Leader 时发 Lineage 通知

    处置是必须有人做的事，和只告知的失败通知不同：Effect 没配收件人或清单为空时，问 Lineage 管理员；
    即使 Lineage 有监听这个 Effect 失败的处理者也照发，因为这次没有失败 Event。同一次激活只占一个消息 ID，
    隔天再问也是同一个。
    """
    from management_portal.society_service import effect_failure_notifications, lineage_primitives
    from management_portal.society_service import lineage_failure_notifications as failures

    notices, written = [], []
    monkeypatch.setattr(effect_failure_notifications, "recipients", lambda *_: [])
    monkeypatch.setattr(failures.permission_system, "users_permitted", lambda *_: ["CASE-A", "bob"])
    monkeypatch.setattr(failures, "handled", lambda *_: True)
    monkeypatch.setattr(failures, "_leader", lambda _: leader)
    monkeypatch.setattr(failures.lineage_human_notifications, "create_notification", lambda *a, **k: notices.append(k))
    monkeypatch.setattr(outward_messages, "post_platform_notice", lambda resident_id, users, text, **kwargs: (
        written.append((resident_id, users, kwargs["message_id"])) or {"unreached": []}))
    for day in ("2026-10-18", "2026-10-19"):
        monkeypatch.setattr(lineage_primitives, "now_iso", lambda day=day: day + "T23:59:59Z")
        failures.ask_resolution("lineage_aaaaaaaaaaaa", WIRE, HOLD)
    if leader:
        assert notices == [] and [item[:2] for item in written] == [(leader, ["CASE-A", "bob"])] * 2
        assert written[0][2] == written[1][2]
    else:
        assert written == [] and [item["recipients"] for item in notices] == [["CASE-A", "bob"]] * 2
        assert {item["notification_id"] for item in notices} == {"effect-resolution:" + HOLD["activation_key"]}
