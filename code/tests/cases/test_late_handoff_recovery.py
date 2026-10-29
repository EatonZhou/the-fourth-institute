"""Program recovery settles a handoff whose input finished long ago from the input receipt alone."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_program_runtime, lineage_program_terminal_reconciliation as reconciliation, trigger_paths,
)
from management_portal.society_service import resident_conversation_stream

LINEAGE = "lineage_aaaaaaaaaaaa"
MEMBER = "resident_aaaaaaaaaaaa"


def recover(monkeypatch, finished_at, *, facade=False):
    """Run one recovery pass over a member's pending handoff whose input completed at ``finished_at``."""
    from tests.cases import society_service_test_support as support
    support._install_isolated_portal_access(monkeypatch)
    monkeypatch.setattr(reconciliation, "_runs_no_turns", lambda resident_id: facade and resident_id == MEMBER)
    handoff = {"trigger_path_id": "trigger_path_aaaaaaaaaaaa", "step_id": "step", "resident_id": MEMBER,
               "message_id": "msg_done", "status": "queued",
               "event": {"id": "event", "type": "time.reached", "payload": {}, "occurred_at": "2026-10-06T04:40:17Z",
                         "identity": OperationIdentity.for_actor("sample_admin").to_dict()}}
    monkeypatch.setattr(lineage_program_runtime, "pending_resident_handoffs", lambda _lineage: [handoff])
    monkeypatch.setattr(trigger_paths, "resident_inputs", lambda *_args, **_kwargs: [handoff])
    receipt = {"message_id": "msg_done", "state": "completed", "reply_message_ids": ["msg_reply"],
               "updated_at": finished_at.isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(resident_conversation_stream, "get_repository",
                        lambda: SimpleNamespace(input_state=lambda _resident, _message: receipt))
    settled, terminal = [], []
    monkeypatch.setattr(lineage_program_runtime, "settle_resident_handoffs",
                        lambda lineage_id, **kwargs: settled.append(kwargs) or [])
    monkeypatch.setattr(reconciliation, "settle_program_paths", lambda *_args: [])
    lineage = {"lineage_id": LINEAGE, "nodes": [{"id": MEMBER}]}
    ports = SimpleNamespace(
        state=SimpleNamespace(read_lineage=lambda *_args, **_kwargs: lineage, ensure_manager=lambda *_args: None),
        directory=SimpleNamespace(lineage_execution_authority=lambda _lineage: nullcontext()),
        # The Context was cleared since, so the Chat no longer shows the turn.
        resident=SimpleNamespace(
            resident_input_reconciliation_state=lambda *_args: {"status": "missing", "evidence": {}},
            handle_resident_message_terminal=lambda *args, **kwargs: terminal.append(kwargs) or {},
        ),
    )
    result = reconciliation._reconcile_resident_handoffs(LINEAGE, "sample_admin", ports=ports, recovery_only=False)
    return result, settled, terminal, receipt


def test_a_long_finished_input_settles_its_handoff_without_new_events(monkeypatch):
    """输入早已完成但 Chat 已清空看不到那一轮时，恢复按输入回执结清交接，不再触发下游事件"""
    result, settled, terminal, receipt = recover(monkeypatch, datetime.now(timezone.utc) - timedelta(days=12))
    assert result["recovery_failures"] == []
    assert result["recovered_handoff_count"] == 1
    assert terminal == []
    assert [(item["status"], item["completed_at"]) for item in settled] == [("completed", receipt["updated_at"])]
    assert settled[0]["output"]["reply_message_ids"] == ["msg_reply"]


def test_a_recent_input_still_needs_its_turn_in_the_chat(monkeypatch):
    """刚完成的输入照旧要在 Chat 里找到那一轮；找不到时交接保持待处理，留给之后的恢复"""
    result, settled, terminal, _receipt = recover(monkeypatch, datetime.now(timezone.utc) - timedelta(hours=1))
    assert result["recovered_handoff_count"] == 0
    assert settled == [] and terminal == []


def test_a_facade_input_settles_from_its_receipt_once_relayed(monkeypatch):
    """Family 门面不运行自己的轮次，Router 转交后输入回执即完成；恢复当轮按回执结清交接，不等 24 小时"""
    result, settled, terminal, receipt = recover(
        monkeypatch, datetime.now(timezone.utc) - timedelta(minutes=1), facade=True)
    assert result["recovery_failures"] == []
    assert result["recovered_handoff_count"] == 1
    assert terminal == []
    assert [(item["status"], item["completed_at"]) for item in settled] == [("completed", receipt["updated_at"])]


def test_facade_detection_reads_the_family_role(monkeypatch):
    """门面判断只看 Resident 记录里的 Family 角色"""
    from management_portal.society_service import resident_record_domain
    records = {MEMBER: {"family": {"role": "facade", "family_id": "fbi_aaaaaaaaaaaa"}},
               "resident_bbbbbbbbbbbb": {"family": {"role": "member"}}}
    monkeypatch.setattr(resident_record_domain, "find_resident", records.get)
    assert reconciliation._runs_no_turns(MEMBER)
    assert not reconciliation._runs_no_turns("resident_bbbbbbbbbbbb")
    assert not reconciliation._runs_no_turns("resident_cccccccccccc")
