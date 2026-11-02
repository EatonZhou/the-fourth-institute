"""Society checks the real action slot when temporary AMA delegation is used.

:module: resident.identity
:importance: P1
:design: decision_bd5217d08c09, decision_b820faf42218
"""
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.permission_system import personal_ama
from management_portal.society_service import lineage_access_policy, resident_access
from management_portal.society_service.isa import _access_scope
from tests.cases.test_personal_ama_delegation import (  # noqa: F401
    world, permissions, lend, AMA, OTHER_AMA, LINEAGE, WORKSPACE, CALLER,
)


@pytest.mark.parametrize("mode", [permission_system.PAR, permission_system.OPEN])
@pytest.mark.parametrize("grant,write", [("@viewer", False), ("@contributor", True)])
def test_handler_scope_retains_actual_read_control_and_delete_actions(world, monkeypatch, mode, grant, write):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: mode)
    monkeypatch.setattr(resident_access, "actor_can_manage_residents", lambda actor: True)
    record = {"resident_id": AMA, "personal_owner_id": "owner"}
    lend(world, a=grant)
    # The same owner predicate is used by legacy domain entry points. ISA
    # carries its exact slot through that predicate without lending identity.
    for instruction, expected in [("GetResident", True), ("GetSessionRestart", True),
                                  ("RestartSession", write), ("CancelInput", write), ("DeleteResident", False)]:
        inv = SimpleNamespace(declaration=SimpleNamespace(name=instruction),
                              call=SimpleNamespace(arguments={"resident_id": AMA}))
        assert _access_scope(lambda inv: resident_access.can_manage_resident(record, CALLER))(inv) is expected
    # Scope is reset after the handler: generic 'manage' cannot imply a
    # particular mutation, even when OPEN would otherwise allow everything.
    assert not resident_access.can_manage_resident(record, CALLER)
    assert resident_access.resident_visible_to_actor(record, CALLER)
    allowed = resident_access.resident_allows(record, CALLER)
    assert allowed["RestartSession"] is write and not allowed["DeleteResident"]
    from management_portal.society_service import resident_protocol
    monkeypatch.setattr(resident_protocol.resident_execution_provider, "provider_for", lambda record: SimpleNamespace(
        capabilities=SimpleNamespace(supports_active_turn_input=True, supports_media=True, local_tools=True)))
    assert resident_protocol._conversation_controls(SimpleNamespace(record=record), CALLER,
        {"active_message_id": "msg_working"}, allowed=True)["can_stop"] is write
    assert not resident_access.can_manage_resident({**record, "resident_id": OTHER_AMA}, CALLER, operation="read")


def test_file_and_lineage_changes_use_precise_resource_and_action(world, monkeypatch):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    monkeypatch.setattr(lineage_access_policy, "actor_can_manage_lineages", lambda actor: True)
    lineage = {"lineage_id": LINEAGE, "lineage_membership": "personal", "created_by": "owner"}
    lend(world, resource=LINEAGE)
    assert lineage_access_policy.actor_can_manage_lineage(lineage, CALLER, operation="read")
    assert not lineage_access_policy.actor_can_manage_lineage(lineage, CALLER)
    assert not lineage_access_policy.changeable_ids(CALLER, {LINEAGE: "owner"}, "DeleteLineage", "lineage_id")
    assert not lineage_access_policy.file_reaches(CALLER, WORKSPACE, write=False, owner="owner")
    lend(world, resource=WORKSPACE)
    assert lineage_access_policy.file_reaches(CALLER, WORKSPACE, write=False, owner="owner")
    assert not lineage_access_policy.file_reaches(CALLER, WORKSPACE, write=True, owner="owner")


def test_exact_resident_can_be_read_without_opening_its_private_parent(world, monkeypatch):
    monkeypatch.setattr(resident_access, "actor_can_manage_residents", lambda actor: True)
    lend(world)
    from management_portal.society_service import resident_catalog
    record = {"resident_id": AMA, "lineage_id": LINEAGE, "personal_owner_id": "owner"}
    monkeypatch.setattr(resident_access.get_portal_access_store(), "is_service_actor", lambda actor: True)
    monkeypatch.setattr(resident_catalog.get_catalog(), "get_identity_records", lambda ids: [record])
    monkeypatch.setattr(resident_catalog, "registration_ready", lambda record: True)
    monkeypatch.setattr(resident_access.resident_lineage_host_port, "actor_lineage_access_records", lambda *args: [])
    assert resident_access.visible_resident_records([AMA], CALLER, "portal") == [record]
    assert not personal_ama.reaches(CALLER, "owner", LINEAGE)
