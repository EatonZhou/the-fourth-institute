"""An owner's temporary loan opens only its named private resource and actions."""
from __future__ import annotations

import pytest

from management_portal import permission_system
from management_portal.permission_system import personal_ama, temporary
from management_portal.permission_system.engine.model import PERMIT, Entry
from management_portal.permission_system.engine.store import new_entry_id
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

LINEAGE = "lineage_aaaaaaaaaaaa"
AMA = "resident_aaaaaaaaaaaa"
OTHER_AMA = "resident_bbbbbbbbbbbb"
WORKSPACE = "workspace_aaaaaaaaaaaa"
CALLER = "resident_cccccccccccc"
OTHER_CALLER = "resident_dddddddddddd"
OWNER_AMA = "resident_eeeeeeeeeeee"
RECIPIENT_AMA = "resident_ffffffffffff"


@pytest.fixture
def world(permissions, monkeypatch):
    representatives = {OWNER_AMA: "owner", RECIPIENT_AMA: "recipient"}
    monkeypatch.setattr(personal_ama, "owner_of", lambda p: representatives.get(p, ""))
    monkeypatch.setattr(personal_ama, "owners_of", lambda ps: {p: representatives[p] for p in ps if p in representatives})
    permissions.users("owner", "recipient", "stranger", "admin", category="intern")
    permissions.place(
        Fact("lineage", LINEAGE, {"personal_space": "personal_space:owner", "user": "owner"}),
        Fact("resident", AMA, {"lineage": LINEAGE}),
        Fact("resident", OTHER_AMA, {"lineage": LINEAGE}),
        Fact("workspace", WORKSPACE, {"resident": AMA}),
        Fact("project", "Team"),
        *(Fact("resident", p, {"project": "Team"}) for p in (CALLER, OTHER_CALLER, OWNER_AMA, RECIPIENT_AMA)),
    )
    # The test controls grants independently of how a deployed scheme places AMA objects.
    permissions.grant("owner", LINEAGE, "@admin")
    return permissions


def lend(world, *, resource=AMA, a="@viewer", issuer="owner", recipient=CALLER, **kwargs):
    scheme, engine, _instance = world.implementation.current()
    return temporary.issue(engine, scheme, issuer=issuer, recipient=recipient, a=a, resource=resource, **kwargs)


@pytest.mark.parametrize("mode", [permission_system.PAR, permission_system.OPEN])
@pytest.mark.parametrize("a,can_write", [("@viewer", False), ("@contributor", True)])
def test_owner_loan_requires_enabled_scheme_and_is_exact_and_action_limited(world, monkeypatch, mode, a, can_write):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: mode)
    targets = {AMA: "owner", OTHER_AMA: "owner", LINEAGE: "owner", WORKSPACE: "owner"}
    assert personal_ama.hidden(CALLER, targets) == set(targets)
    loan = lend(world, a=a, seconds=120)
    assert loan["issued_by"] == "owner" and loan["principal_id"] == CALLER
    if mode == permission_system.OPEN:
        # Disabling PAR does not turn its saved loans into private owner access.
        monkeypatch.setattr(permission_system, "active", lambda: pytest.fail("Open loaded PAR"))
        assert personal_ama.hidden(CALLER, targets) == set(targets)
        assert not personal_ama.reaches(CALLER, "owner", AMA, action="GetResident")
        assert not personal_ama.reaches(CALLER, "owner", AMA, action="GetConversation")
        assert not personal_ama.reaches(CALLER, "owner", AMA, action="@contributor")
        assert personal_ama.reaches("owner", "owner", AMA)
        assert personal_ama.reaches(OWNER_AMA, "owner", AMA)
    else:
        assert personal_ama.hidden(CALLER, targets) == set(targets) - {AMA}
        assert personal_ama.reaches(CALLER, "owner", AMA, action="GetResident")
        assert personal_ama.reaches(CALLER, "owner", AMA, action="GetConversation")
        assert personal_ama.reaches(CALLER, "owner", AMA, action="@contributor") is can_write
    assert not personal_ama.reaches(CALLER, "owner", AMA, action="@admin")
    assert not personal_ama.reaches(CALLER, "owner")
    assert not personal_ama.reaches(OTHER_CALLER, "owner", AMA)
    assert not personal_ama.reaches(CALLER, "stranger", AMA)
    assert not personal_ama.acts_for(CALLER, "owner")


def test_parent_loan_cannot_open_children_even_in_one_batch(world):
    lend(world, resource=LINEAGE)
    targets = {LINEAGE: "owner", AMA: "owner", OTHER_AMA: "owner", WORKSPACE: "owner"}
    assert personal_ama.hidden(CALLER, targets) == {AMA, OTHER_AMA, WORKSPACE}
    # PAR normally inherits the parent's loan, but the personal boundary requires the exact child.
    assert world.holds(CALLER, "@viewer", AMA)
    assert not personal_ama.reaches(CALLER, "owner", AMA)


def test_persistent_admin_and_nonowner_loans_do_not_open_private_resource(world):
    world.grant(CALLER, AMA, "@admin")
    world.grant("admin", AMA, "@admin")
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    lend(world, issuer="admin")
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    with pytest.raises(temporary.LoanRefused):
        lend(world, issuer="stranger")
    lend(world, a="@contributor")
    assert personal_ama.reaches(CALLER, "owner", AMA)
    lend(world, issuer=CALLER, recipient=OTHER_CALLER)
    assert world.holds(OTHER_CALLER, "@viewer", AMA)
    assert not personal_ama.reaches(OTHER_CALLER, "owner", AMA)


def test_cached_grants_expire_revoke_and_disable_immediately(world):
    _scheme, engine, _instance = world.implementation.current()
    clock = [engine.clock()]
    engine.clock = lambda: clock[0]
    timed = lend(world, seconds=60)
    assert personal_ama.reaches(CALLER, "owner", AMA)
    clock[0] = timed["expires_at"]
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    revoked = lend(world, seconds=60)
    assert personal_ama.reaches(CALLER, "owner", AMA)
    temporary.revoke(engine.store, revoked["credential_id"], actor="owner")
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    disabled = lend(world, seconds=60)
    assert personal_ama.reaches(CALLER, "owner", AMA)
    with engine.store.transaction() as connection:
        engine.store.execute(connection, "UPDATE {s}.scheme_entries SET disabled=true WHERE entry_id=%s",
                             (disabled["credential_id"],))
        engine.store.bump(connection)
    assert not personal_ama.reaches(CALLER, "owner", AMA)


def test_input_end_revokes_message_loan_in_the_same_transaction(world):
    _scheme, engine, _instance = world.implementation.current()
    loan = lend(world, message_id="msg_delegation", input_open=lambda recipient, message: True)
    assert loan["message_id"] == "msg_delegation"
    assert personal_ama.reaches(CALLER, "owner", AMA)
    with engine.store.transaction() as connection:
        permission_system.end_task_credentials(connection, CALLER, "msg_delegation")
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    with engine.store.transaction() as connection, pytest.raises(temporary.LoanRefused):
        temporary.message_credentials(engine, issuer="owner", recipient=CALLER, message_id="msg_delegation",
                                      requests=[{"a": "@viewer", "resource_id": AMA}],
                                      input_open=lambda recipient, message: True, connection=connection)


def test_recipient_denies_still_apply_and_only_requested_action_is_lent(world):
    lend(world, a="@contributor")
    assert personal_ama.reaches(CALLER, "owner", AMA, action="@contributor")
    denied = world.deny(CALLER, LINEAGE, "@contributor")
    assert not personal_ama.reaches(CALLER, "owner", AMA, action="@contributor")
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    with world.store.transaction() as connection:
        world.store.delete(connection, where="entry_id=%s", parameters=(denied.entry_id,))
    # A read-only loan cannot piggyback on an unrelated permanent admin grant.
    temporary.revoke(world.store, temporary.listing(world.store, CALLER)[0]["credential_id"], actor=CALLER)
    lend(world)
    world.grant(CALLER, AMA, "@admin")
    assert personal_ama.reaches(CALLER, "owner", AMA)
    assert not personal_ama.reaches(CALLER, "owner", AMA, action="@admin")


def test_owner_and_recipient_ama_follow_existing_same_party_contract(world):
    loan = lend(world, issuer=OWNER_AMA, recipient="recipient")
    assert loan["issued_by"] == OWNER_AMA
    assert personal_ama.reaches("recipient", "owner", AMA)
    assert personal_ama.reaches(RECIPIENT_AMA, "owner", AMA)
    assert not personal_ama.reaches(CALLER, "owner", AMA)
    temporary.revoke(world.store, loan["credential_id"], actor="owner")
    assert not personal_ama.reaches(RECIPIENT_AMA, "owner", AMA)


def test_group_addressed_temporary_entry_cannot_open_ama(world):
    _scheme, engine, _instance = world.implementation.current()
    path, _grants, _paths = engine.grants(CALLER)
    with engine.store.transaction() as connection:
        engine.store.add(connection, [Entry(new_entry_id(), PERMIT, path[0], "@viewer", AMA,
                                          issued_by="owner", issued_at=engine.clock(), expires_at=engine.clock()+120)])
    assert world.holds(CALLER, "@viewer", AMA)
    assert not personal_ama.reaches(CALLER, "owner", AMA)


def test_empty_or_owner_only_checks_do_not_load_the_permission_engine(monkeypatch):
    monkeypatch.setattr(personal_ama, "owner_of", lambda principal: "")
    monkeypatch.setattr(permission_system, "active", lambda: pytest.fail("unnecessary engine access"))
    assert personal_ama.hidden("owner", {AMA: "owner", "shared": ""}) == set()
    assert personal_ama.reaches("owner", "owner", AMA)
    assert personal_ama.reaches("portal", "owner", AMA)
    assert not personal_ama.reaches(CALLER, "owner")
