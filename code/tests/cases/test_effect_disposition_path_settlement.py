"""Manual Effect disposition also settles its exact queued Trigger Path delivery."""
from __future__ import annotations

from copy import deepcopy

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import effect_disposition, lineage_domain, trigger_paths
from management_portal.society_service import family_recovery, lineage_failure_notifications
from management_portal.society_service import lineage_effect_script_journal as journal
from management_portal.society_service import lineage_program_wire_queue
from management_portal.society_service import trigger_path_storage as storage
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_effect_unknown_outcome_hold import SOURCE, WIRE, _drain, _pending, _receipt, _setup
from tests.cases.test_lineage_generic_primitives import ACTOR, primitive_store  # noqa: F401


def _hold(monkeypatch):
    lineage_id, calls = _setup(monkeypatch, [lambda: _receipt(replayed=True)])
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    pending = _pending(lineage_id)
    assert len(pending) == 1 and pending[0]["recovery_blocked"]["requires_resolution"]
    delivery = pending[0]
    key = delivery["recovery_blocked"]["activation_key"]
    path_id = delivery["event"]["payload"]["trigger_path_id"]
    return lineage_id, key, path_id, delivery, calls


def _operator(path):
    return next(step for step in path["steps"] if step["kind"] == "operator" and step["node_id"] == WIRE)


def _step(path, identity):
    return next(step for step in path["steps"] if step["step_id"] == identity)


def _paths(lineage_id):
    with get_lineage_data_store().database.read() as connection:
        return {row["trigger_path_id"] for row in connection.execute(
            "SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s", (lineage_id,),
        ).fetchall()}


def _legacy_disposition(lineage_id, key, status):
    """Create the old persisted state through real owners, without calling resolve."""
    existing = journal.load(lineage_id, key)
    disposition = {
        "actor": ACTOR, "status": status, "note": "original historical inspection",
        "occurred_at": "2026-10-20T01:02:03Z",
    }
    with get_lineage_data_store().database.transaction():
        journal.finish(lineage_id, key, status=status, outputs={}, disposition=disposition)
        lineage_program_wire_queue.settle_effect_delivery(lineage_id, existing)
    journal.cleanup(lineage_id, key)
    terminal = journal.load(lineage_id, key)
    assert terminal["disposition"] == disposition
    assert "source_wire_id" not in terminal and "event_id" not in terminal
    assert _pending(lineage_id) == []
    return terminal


def _admit(path, *, activation, node=WIRE, source=None, wire=WIRE, status="waiting"):
    original = _operator(path)
    if source is None:
        source = next(edge["source_step_id"] for edge in path["edges"]
                      if edge["target_step_id"] == original["step_id"])
    with get_lineage_data_store().database.transaction() as connection:
        identity = storage.admit_step(
            connection, trigger_path_id=path["trigger_path_id"], node_id=node, kind="operator",
            activation_key=activation, created_at=path["occurred_at"], predecessors=[(source, wire)],
        )
        storage.transition_step(connection, identity, status=status, at=path["occurred_at"])
    return identity


@pytest.mark.parametrize("status,terminal", [("failed", "failed"), ("succeeded", "completed")])
def test_disposition_settles_delivery_and_path(primitive_store, monkeypatch, status, terminal):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    before = trigger_paths.get(lineage_id, path_id)
    assert before["status"] == "active" and _operator(before)["status"] == "waiting"
    paths_before = _paths(lineage_id)

    effect = effect_disposition.resolve(lineage_id, key, status, ACTOR, note="isolated outcome inspection")

    assert effect["status"] == status
    assert _pending(lineage_id) == []
    assert calls == ["GetLineage"]
    after = trigger_paths.get(lineage_id, path_id)
    assert _operator(after)["status"] == terminal
    assert after["status"] == terminal and after["finished_at"]
    assert _operator(after)["finished_at"]
    assert _paths(lineage_id) == paths_before

    repeated = effect_disposition.resolve(lineage_id, key, status, ACTOR, note="must not replace prior note")
    assert repeated == effect
    assert trigger_paths.get(lineage_id, path_id) == after
    assert _drain(lineage_id)["attempted_count"] == 0
    assert calls == ["GetLineage"]


@pytest.mark.parametrize("status,terminal", [("failed", "failed"), ("succeeded", "completed")])
def test_old_terminal_effect_repairs_waiting_delivery_without_rewriting_history(
    primitive_store, monkeypatch, status, terminal,
):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    historical = _legacy_disposition(lineage_id, key, status)
    before = trigger_paths.get(lineage_id, path_id)
    assert before["status"] == "active" and _operator(before)["status"] == "waiting"
    effect_step = next(step for step in before["steps"] if step["kind"] == "effect")

    repaired = effect_disposition.resolve(lineage_id, key, status, ACTOR, note="repair only")

    assert repaired == historical
    after = trigger_paths.get(lineage_id, path_id)
    assert after["status"] == terminal and _operator(after)["status"] == terminal
    assert _step(after, effect_step["step_id"]) == effect_step
    assert _pending(lineage_id) == []
    assert effect_disposition.resolve(lineage_id, key, status, ACTOR) == historical
    assert trigger_paths.get(lineage_id, path_id) == after
    assert _drain(lineage_id)["attempted_count"] == 0
    assert calls == ["GetLineage"]


def test_disposition_leaves_later_event_and_same_path_unrelated_operators_untouched(primitive_store, monkeypatch):
    lineage_id, key, path_id, delivery, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    target_id = _operator(path)["step_id"]
    sibling_source = _admit(path, activation="other-source", node="wire_other_source", status="completed")
    unrelated = [
        _admit(path, activation="wrong-source", source=sibling_source),
        _admit(path, activation="wrong-node", node="wire_other"),
        _admit(path, activation="wrong-wire", wire="wire_other"),
    ]
    before = trigger_paths.get(lineage_id, path_id)
    snapshots = {identity: _step(before, identity) for identity in [sibling_source, *unrelated]}

    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    later = next(item for item in _pending(lineage_id) if item["event"]["id"] != delivery["event"]["id"])
    later_path_id = later["event"]["payload"]["trigger_path_id"]
    assert later_path_id != path_id and calls == ["GetLineage", "GetLineage"]
    later_path = trigger_paths.get(lineage_id, later_path_id)
    paths_before = _paths(lineage_id)

    effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    after = trigger_paths.get(lineage_id, path_id)
    assert _step(after, target_id)["status"] == "failed"
    assert after["status"] == "active"
    for identity, snapshot in snapshots.items():
        assert _step(after, identity) == snapshot
    assert _pending(lineage_id) == [later]
    assert trigger_paths.get(lineage_id, later_path_id) == later_path
    assert _paths(lineage_id) == paths_before
    assert _drain(lineage_id)["attempted_count"] == 0
    assert calls == ["GetLineage", "GetLineage"]


@pytest.mark.parametrize("legacy", [False, True], ids=["new-disposition", "historical-disposition"])
def test_ambiguous_delivery_candidates_reject_without_partial_settlement(primitive_store, monkeypatch, legacy):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    _admit(path, activation="ambiguous-second-delivery")
    if legacy:
        _legacy_disposition(lineage_id, key, "failed")
    original_effect = deepcopy(journal.load(lineage_id, key))
    original_path = trigger_paths.get(lineage_id, path_id)
    original_pending = deepcopy(_pending(lineage_id))

    with pytest.raises(InstructionError) as error:
        effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    assert error.value.code == "instruction.conflict"
    assert journal.load(lineage_id, key) == original_effect
    assert trigger_paths.get(lineage_id, path_id) == original_path
    assert _pending(lineage_id) == original_pending
    assert calls == ["GetLineage"]


@pytest.mark.parametrize("operator_status", ["completed", "failed", "cancelled"])
def test_terminal_operator_is_preserved_while_effect_and_path_finish(primitive_store, monkeypatch, operator_status):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    identity = _operator(path)["step_id"]
    with get_lineage_data_store().database.transaction() as connection:
        storage.transition_step(connection, identity, status=operator_status, at=path["occurred_at"],
                                result={"historical_delivery_receipt": "preserve"})
    original_operator = _step(trigger_paths.get(lineage_id, path_id), identity)

    effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    after = trigger_paths.get(lineage_id, path_id)
    assert _step(after, identity) == original_operator
    assert after["status"] == "failed"
    assert _pending(lineage_id) == [] and calls == ["GetLineage"]


def test_conflicting_historical_disposition_does_not_repair_or_rewrite(primitive_store, monkeypatch):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    historical = _legacy_disposition(lineage_id, key, "failed")
    before = trigger_paths.get(lineage_id, path_id)

    with pytest.raises(InstructionError) as error:
        effect_disposition.resolve(lineage_id, key, "succeeded", ACTOR)

    assert error.value.code == "instruction.conflict"
    assert journal.load(lineage_id, key) == historical
    assert trigger_paths.get(lineage_id, path_id) == before
    assert _pending(lineage_id) == [] and calls == ["GetLineage"]


@pytest.mark.parametrize("operator_status", ["pending", "running"])
def test_nonwaiting_active_operator_rejects_without_partial_disposition(primitive_store, monkeypatch, operator_status):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    with get_lineage_data_store().database.transaction() as connection:
        storage.transition_step(connection, _operator(path)["step_id"], status=operator_status,
                                at=path["occurred_at"])
    before = trigger_paths.get(lineage_id, path_id)
    original_effect = deepcopy(journal.load(lineage_id, key))
    original_pending = deepcopy(_pending(lineage_id))

    with pytest.raises(InstructionError) as error:
        effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    assert error.value.code == "instruction.conflict"
    assert trigger_paths.get(lineage_id, path_id) == before
    assert journal.load(lineage_id, key) == original_effect
    assert _pending(lineage_id) == original_pending
    assert calls == ["GetLineage"]


@pytest.mark.parametrize("legacy", [False, True], ids=["new-disposition", "historical-disposition"])
@pytest.mark.parametrize("other_failure", [False, True], ids=["manual-only", "also-real-failure"])
def test_manual_disposition_skips_failure_alerts_but_other_failure_still_notifies(
    primitive_store, monkeypatch, legacy, other_failure,
):
    lineage_id, key, path_id, _, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    notices, routing_failures = [], []
    monkeypatch.setattr(lineage_failure_notifications, "notify", lambda *args, **kwargs: notices.append((args, kwargs)))
    monkeypatch.setattr(family_recovery, "routing_failed", lambda *args, **kwargs: routing_failures.append((args, kwargs)))
    if other_failure:
        _admit(path, activation="independent-real-failure", node="wire_other_failure", status="failed")
    if legacy:
        _legacy_disposition(lineage_id, key, "failed")

    effect = effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    after = trigger_paths.get(lineage_id, path_id)
    assert after["status"] == "failed"
    assert _step(after, _operator(path)["step_id"])["result"]["disposition"] == effect["disposition"]
    assert len(notices) == len(routing_failures) == int(other_failure)
    if other_failure:
        args, kwargs = notices[0]
        assert args[:2] == (lineage_id, path_id)
        assert kwargs["subject"] == "operator:wire_other_failure"
        assert routing_failures[0][1]["node_id"] == "wire_other_failure"
    effect_disposition.resolve(lineage_id, key, "failed", ACTOR)
    assert len(notices) == len(routing_failures) == int(other_failure)
    assert calls == ["GetLineage"]


def test_filter_predecessor_disposition_settles_canonical_event_delivery(primitive_store, monkeypatch):
    """Keep a real queued delivery and admit the legal operator-predecessor topology.

    The ordinary filtered packet route does not queue this synthetic unknown
    receipt on its target wire. Only the Effect's persisted predecessor edge is
    adjusted in this isolated-PG fixture; the original queue/operator is intact.
    """
    lineage_id, key, path_id, delivery, calls = _hold(monkeypatch)
    path = trigger_paths.get(lineage_id, path_id)
    original_operator = _operator(path)
    effect = next(step for step in path["steps"] if step["kind"] == "effect")
    filter_id = "filter_disposition"
    filter_identity = trigger_paths.operator_started(lineage_id, filter_id, delivery["event"])
    trigger_paths.operator_settled(filter_identity, status="completed")
    with get_lineage_data_store().database.transaction() as connection:
        changed = connection.execute(
            "UPDATE trigger_path_step_edges SET source_step_id=%s "
            "WHERE trigger_path_id=%s AND target_step_id=%s AND wire_id=%s",
            (filter_identity, path_id, effect["step_id"], WIRE),
        )
        assert changed.rowcount == 1
    before = trigger_paths.get(lineage_id, path_id)
    filter_step = next(step for step in before["steps"] if step["node_id"] == filter_id)
    root = next(step for step in before["steps"] if step["kind"] == "event")
    operator = _operator(before)
    assert operator == original_operator and _pending(lineage_id) == [delivery]
    assert any(edge == {"source_step_id": filter_step["step_id"], "target_step_id": effect["step_id"],
                        "wire_id": WIRE} for edge in before["edges"])
    assert any(edge == {"source_step_id": root["step_id"], "target_step_id": operator["step_id"],
                        "wire_id": WIRE} for edge in before["edges"])
    assert filter_step["step_id"] != root["step_id"]

    terminal = effect_disposition.resolve(lineage_id, key, "failed", ACTOR)

    assert terminal["status"] == "failed" and _pending(lineage_id) == []
    after = trigger_paths.get(lineage_id, path_id)
    assert _step(after, operator["step_id"])["status"] == "failed"
    assert after["status"] == "failed"
    assert calls == ["GetLineage"]
