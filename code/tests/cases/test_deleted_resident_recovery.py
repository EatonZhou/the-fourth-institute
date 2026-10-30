"""Program recovery stops retrying work that a deleted Resident can never finish."""
from contextlib import nullcontext
from types import SimpleNamespace

from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_program_resident_inputs, lineage_program_terminal_reconciliation as reconciliation,
    lineage_recovery_policy, resident_catalog, trigger_paths,
)
from management_portal.society_service import resident_conversation_stream

LINEAGE = "lineage_aaaaaaaaaaaa"
GONE = "resident_aaaaaaaaaaaa"
LIVE = "resident_bbbbbbbbbbbb"


def catalog(monkeypatch, records):
    def get(resident_id, *, include_deleted=False):
        if resident_id not in records:
            raise KeyError(resident_id)
        return records[resident_id]
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(get=get))


def event(actor):
    return {"id": "event", "type": "manual.triggered", "payload": {}, "occurred_at": "2026-10-17T00:00:00Z",
            "identity": OperationIdentity.for_actor(actor).to_dict()}


def test_an_event_of_a_deleted_resident_needs_an_explicit_reissue(monkeypatch):
    """已删除 Resident 发起的待恢复 Event 判为永久失败，在世或未登记的身份照常恢复"""
    catalog(monkeypatch, {GONE: {"resident_id": GONE, "deleted_at": "2026-09-29T01:03:30Z"},
                          LIVE: {"resident_id": LIVE, "deleted_at": ""}})
    assert "deleted Resident" in lineage_recovery_policy.permanent_error(event(GONE))
    assert lineage_recovery_policy.permanent_error(event(LIVE)) == ""
    assert lineage_recovery_policy.permanent_error(event("resident_cccccccccccc")) == ""
    assert lineage_recovery_policy.permanent_error(event("sample_admin")) == ""


def test_a_handoff_to_a_deleted_resident_without_its_input_settles_cancelled(monkeypatch):
    """交给已删除 Resident 的输入随会话一起消失时，恢复把交接按取消结算，不再反复失败"""
    from tests.cases import society_service_test_support as support
    support._install_isolated_portal_access(monkeypatch)
    catalog(monkeypatch, {GONE: {"resident_id": GONE, "deleted_at": "2026-10-02T21:13:10Z"}})
    handoff = {"trigger_path_id": "trigger_path_aaaaaaaaaaaa", "step_id": "step", "resident_id": GONE,
               "message_id": "msg_gone", "event": event("sample_admin"), "status": "queued"}
    monkeypatch.setattr(lineage_program_resident_inputs, "pending_resident_handoffs", lambda _lineage: [handoff])
    monkeypatch.setattr(trigger_paths, "resident_inputs", lambda *_args, **_kwargs: [handoff])

    def missing(resident_id, message_id):
        raise KeyError(message_id)
    monkeypatch.setattr(resident_conversation_stream, "get_repository",
                        lambda: SimpleNamespace(input_state=missing))
    settled = []
    monkeypatch.setattr(lineage_program_resident_inputs, "settle_resident_handoffs",
                        lambda lineage_id, **kwargs: settled.append(kwargs) or [])
    monkeypatch.setattr(reconciliation, "settle_program_paths", lambda *_args: None)
    lineage = {"lineage_id": LINEAGE, "nodes": []}
    ports = SimpleNamespace(
        state=SimpleNamespace(read_lineage=lambda *_args, **_kwargs: lineage, ensure_manager=lambda *_args: None),
        directory=SimpleNamespace(lineage_execution_authority=lambda _lineage: nullcontext()),
    )
    result = reconciliation._reconcile_resident_handoffs(LINEAGE, "sample_admin", ports=ports, recovery_only=False)
    assert result["recovery_failures"] == []
    assert result["recovered_handoff_count"] == 1
    assert [item["status"] for item in settled] == ["cancelled"]
    assert settled[0]["output"]["reply_message_ids"] == []
