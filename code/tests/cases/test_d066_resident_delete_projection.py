"""Resident buttons ask the same operation-specific permissions as Apply."""
from __future__ import annotations

import pytest

from management_portal import permission_system
from management_portal.permission_system import temporary
from management_portal.society_service import resident_access
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

LINEAGE = "lineage_aaaaaaaaaaaa"
RESIDENT = "resident_aaaaaaaaaaaa"
OTHER = "resident_bbbbbbbbbbbb"
LINEAGE_ACTION = "ApplyLineageChanges:delete_resident.lineage_id"
RESIDENT_ACTION = "ApplyLineageChanges:delete_resident.resident_id"
RECORD = {"resident_id": RESIDENT, "lineage_id": LINEAGE}


@pytest.fixture
def world(permissions):
    permissions.users("owner", "visitor", category="intern")
    permissions.place(Fact("lineage", LINEAGE),
                      Fact("resident", RESIDENT, {"lineage": LINEAGE}),
                      Fact("resident", OTHER, {"lineage": LINEAGE}))
    return permissions


def delete_allowed(record=RECORD, actor="visitor"):
    result = resident_access.resident_allows(record, actor)
    assert "DeleteResident" not in result
    return result["ApplyLineageChanges"]


@pytest.mark.parametrize("first", ["lineage", "resident"])
def test_projection_requires_both_exact_delete_slots_and_observes_denial_and_revocation(world, first):
    assert not delete_allowed()
    # A batch/edit grant or a grant on a different Resident cannot enable deletion.
    world.grant("visitor", LINEAGE, "ApplyLineageChanges:lineage_id")
    world.grant("visitor", OTHER, RESIDENT_ACTION)
    assert not delete_allowed()
    grants = [(LINEAGE, LINEAGE_ACTION), (RESIDENT, RESIDENT_ACTION)]
    if first == "resident":
        grants.reverse()
    world.grant("visitor", *grants[0])
    assert not delete_allowed()
    last = world.grant("visitor", *grants[1])
    assert delete_allowed()
    denied = world.deny("visitor", RESIDENT, RESIDENT_ACTION)
    assert not delete_allowed()
    with world.store.transaction() as connection:
        world.store.delete(connection, where="entry_id=%s", parameters=(denied.entry_id,))
    assert delete_allowed()
    with world.store.transaction() as connection:
        world.store.delete(connection, where="entry_id=%s", parameters=(last.entry_id,))
    assert not delete_allowed()


def test_personal_ama_projection_keeps_exact_owner_delegation_boundary(world):
    record = {**RECORD, "personal_owner_id": "owner"}
    world.grant("owner", LINEAGE, "@admin")
    assert delete_allowed(record, "owner")
    world.grant("visitor", LINEAGE, "@admin")
    assert not delete_allowed(record)
    scheme, engine, _ = world.implementation.current()

    def lend(resource, action):
        return temporary.issue(engine, scheme, issuer="owner", recipient="visitor",
                               a=action, resource=resource, seconds=120)

    # Inherited parent authority and a different action on the exact child are insufficient.
    lend(LINEAGE, "@admin")
    lend(RESIDENT, "@viewer")
    assert not delete_allowed(record)
    exact = lend(RESIDENT, RESIDENT_ACTION)
    assert delete_allowed(record)
    assert not delete_allowed({**record, "resident_id": OTHER})
    temporary.revoke(world.store, exact["credential_id"], actor="owner")
    assert not delete_allowed(record)


@pytest.mark.parametrize("record,valid_actor", [
    ({"resident_id": RESIDENT}, True),
    ({"lineage_id": LINEAGE}, True),
    (RECORD, False),
])
def test_missing_target_scope_or_invalid_identity_does_not_enable_delete(monkeypatch, record, valid_actor):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    monkeypatch.setattr(resident_access, "actor_can_manage_residents", lambda actor: valid_actor)
    assert not delete_allowed(record)
