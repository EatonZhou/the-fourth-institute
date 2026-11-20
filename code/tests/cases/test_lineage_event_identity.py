"""Lineage Event and Packet identity encoding.

:module: lineage.program
:design: decision_4adcc1ff2f2c
"""
import pytest

from management_common.principal import OperationIdentity, Principal
from management_portal.society_service import lineage_primitives, lineage_recovery_policy


def test_event_identity_reads_legacy_and_compact_forms():
    identity = OperationIdentity.for_actor("alice")
    legacy = identity.to_dict()
    compact = {"principal": legacy["principal"]}
    event = {"id": "event_saved", "type": "state.condition_met",
             "occurred_at": "2026-11-20T02:47:40Z", "payload": {"key": "phase"}}
    long_event = {**event, "identity": legacy}
    short_event = {**event, "identity": compact}
    assert lineage_primitives.normalize_event(long_event) == lineage_primitives.normalize_event(short_event)
    assert lineage_primitives.normalize_event(short_event).identity == legacy
    assert lineage_primitives.normalize_event(
        lineage_primitives.LineageEvent(event["id"], event["type"], identity=compact),
    ).identity == legacy
    assert lineage_recovery_policy.permanent_error(short_event) == ""
    packet = lineage_primitives.normalize_packet({"events": [long_event, short_event]})
    assert len(packet.events) == 1


def test_event_reader_preserves_explicit_identity_differences():
    identity = OperationIdentity(Principal("user", "alice"), Principal("user", "bob"),
                                 Principal("service", "ai-socket-relay"))
    long = identity.to_dict()
    compact = {key: value for key, value in long.items() if key != "schema"}
    assert lineage_primitives.make_event("state.condition_met", identity=compact).identity == long


@pytest.mark.parametrize("fields", [
    {"unexpected": True},
    {"producer": None},
    {"producer": {"kind": "user", "id": "bob"}},
    {"initiated_by": None},
    {"schema": "tfi.operation-identity.v2"},
    {"principal": None},
    {"principal": {}},
], ids=["unknown", "null-producer", "user-producer", "null-initiator", "unknown-schema",
        "null-principal", "empty-principal"])
def test_event_reader_rejects_invalid_identity_fields(fields):
    identity = {"principal": {"kind": "user", "id": "alice"}, **fields}
    with pytest.raises(ValueError):
        lineage_primitives.make_event("state.condition_met", identity=identity)


def test_event_reader_keeps_unverified_identity_absent():
    event = lineage_primitives.make_event("state.condition_met")
    assert event.identity == {}
    assert "no verified identity" in lineage_recovery_policy.permanent_error(event.to_dict())
