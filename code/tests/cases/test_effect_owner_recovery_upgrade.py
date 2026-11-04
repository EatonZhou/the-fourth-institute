"""Existing held Events get one original-identity owner recovery probe."""
from types import SimpleNamespace
from tests.cases.test_effect_unknown_outcome_hold import (  # noqa: F401
    primitive_store, _setup, _receipt, _pending, _drain, WIRE, SOURCE, ACTOR,
    lineage_domain, runtime, routing, _asked,
)
from tests.cases.test_effect_script import _Outcome


def test_pre_upgrade_owner_hold_resumes_original_event_once(primitive_store, monkeypatch):
    script = "GetLineage"
    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=True),
        lambda: _Outcome("committed", result={"status": "relayed", "message_id": "msg_original"})], script)
    monkeypatch.setattr(routing, "kernel_for", lambda *a, **k:
        SimpleNamespace(binding=SimpleNamespace(recover_interrupted={"GetLineage": object()})))
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    [held] = _pending(lineage_id)
    original_event = held["event"]
    with runtime._runtime_transaction(lineage_id) as state:
        state["event_wires"][WIRE]["pending_events"][0].pop("owner_recovery_checked", None)
    _drain(lineage_id)
    assert calls == ["GetLineage", "GetLineage"]
    assert not _pending(lineage_id)
    _drain(lineage_id)
    assert len(calls) == 2
    assert original_event["id"]


def test_unsupported_pre_upgrade_hold_remains_held(primitive_store, monkeypatch):
    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=True)])
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    with runtime._runtime_transaction(lineage_id) as state:
        state["event_wires"][WIRE]["pending_events"][0].pop("owner_recovery_checked", None)
    _drain(lineage_id)
    [held] = _pending(lineage_id)
    assert held["recovery_blocked"] and held["owner_recovery_checked"]
    assert calls == ["GetLineage"]
