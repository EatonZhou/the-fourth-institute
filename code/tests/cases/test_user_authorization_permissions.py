"""Current-input grants are atomic, bounded and cannot be resurrected by replay.

:module: security.identity-access
:importance: P1
:design: decision_b820faf42218, decision_22227d0ad075
"""
import pytest

from management_portal import permission_system
from management_portal.permission_system import temporary
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401

RESIDENT = "resident_abcdef123456"
MESSAGE = "msg_user_authorization"
RESOURCES = ["decision_000000000001", "decision_000000000002"]


@pytest.fixture
def grants(permissions):
    permissions.users("lender", category="intern")
    permissions.place(Fact("project", "AuthorizationFixture"), Fact("resident", RESIDENT),
                      *(Fact("decision", r, {"project": "AuthorizationFixture"}) for r in RESOURCES))
    for resource in RESOURCES:
        permissions.grant("lender", resource)
    return permissions


def accept(grants, resources=RESOURCES, **changes):
    args = dict(issuer="lender", resident_id=RESIDENT, message_id=MESSAGE,
                bound_input_message_id=MESSAGE, execution_id="execution_current", source_resident_id=RESIDENT,
                credentials=[{"a": "@contributor", "resource_id": r} for r in resources],
                evidence="请使用我授权的协作权限，然后继续处理图片", source_digest="original-source-digest")
    args.update(changes)
    with grants.store.transaction() as connection:
        return permission_system.accept_user_authorization(connection, **args)


def test_accepted_grants_are_immediate_and_replay_does_not_renew(grants):
    assert not grants.holds(RESIDENT, "@contributor", RESOURCES[0])
    first = accept(grants, RESOURCES[:1])
    assert grants.holds(RESIDENT, "@contributor", RESOURCES[0])
    again = accept(grants, RESOURCES[:1])
    assert again == {**first, "replayed": True}
    expanded = accept(grants)
    assert expanded["authorization_id"] == first["authorization_id"]
    assert expanded["credentials"][0] == first["credentials"][0]
    assert all(c["issued_by"] == "lender" and c["message_id"] == MESSAGE for c in expanded["credentials"])
    with grants.store.read() as connection:
        receipts = grants.store.user_authorizations(connection, RESIDENT, MESSAGE)
    assert len(receipts) == 2
    assert all(r["evidence"] and r["source_digest"] == "original-source-digest" for r in receipts)


@pytest.mark.parametrize("end", ["revoke", "disabled", "expiry", "completion", "regenerate"])
def test_replay_cannot_resurrect_or_expand_ended_authorization(grants, end):
    first = accept(grants, RESOURCES[:1])
    credential = first["credentials"][0]["credential_id"]
    store = grants.store
    with store.transaction() as connection:
        if end == "completion":
            permission_system.end_task_credentials(connection, RESIDENT, MESSAGE)
        elif end == "revoke" or end == "regenerate":
            store.delete(connection, where="entry_id=%s", parameters=(credential,))
        elif end == "disabled":
            store.execute(connection, "UPDATE {s}.scheme_entries SET disabled=TRUE WHERE entry_id=%s", (credential,))
        else:
            store.execute(connection, "UPDATE {s}.scheme_entries SET expires_at=now()-interval '1 second' WHERE entry_id=%s", (credential,))
    with pytest.raises(temporary.LoanRefused, match="已失效"):
        accept(grants)
    with store.read() as connection:
        assert len(store.user_authorizations(connection, RESIDENT, MESSAGE)) == 1


def test_source_changes_and_scheme_changes_refuse_replay(grants):
    accept(grants)
    with pytest.raises(temporary.LoanRefused, match="来源或权限方案"):
        accept(grants, source_digest="modified-message")
    with grants.store.transaction() as connection:
        grants.store.execute(connection, "UPDATE {s}.user_authorizations SET scheme_id='different' WHERE message_id=%s", (MESSAGE,))
    with pytest.raises(temporary.LoanRefused, match="来源或权限方案"):
        accept(grants)


def test_unheld_right_rolls_back_entire_credential_batch_and_receipt(grants):
    grants.deny("lender", RESOURCES[1], "@contributor")
    with pytest.raises(temporary.LoanRefused):
        accept(grants)
    with grants.store.read() as connection:
        assert grants.store.user_authorizations(connection, RESIDENT, MESSAGE) == []
        assert grants.store.entries(connection, where="message_id=%s", parameters=(MESSAGE,)) == []


@pytest.mark.parametrize("requests", [[], [{"a": "@viewer", "resource_id": RESOURCES[0], "issuer": "forged"}],
    [{"a": "@viewer", "resource_id": RESOURCES[0]}] * 2])
def test_invalid_batches_never_issue(grants, requests):
    with pytest.raises(ValueError):
        accept(grants, credentials=requests)
    with grants.store.read() as connection:
        assert grants.store.user_authorizations(connection, RESIDENT, MESSAGE) == []


def test_explicit_duration_survives_task_but_cannot_be_changed_by_replay(grants):
    result = accept(grants, expires_in_seconds=120)
    assert all("message_id" not in c and c["expires_at"] - c["issued_at"] == 120 for c in result["credentials"])
    with grants.store.transaction() as connection:
        permission_system.end_task_credentials(connection, RESIDENT, MESSAGE)
    assert all(grants.holds(RESIDENT, "@contributor", r) for r in RESOURCES)
    with pytest.raises(temporary.LoanRefused):
        accept(grants, expires_in_seconds=240)


def test_consumed_source_is_bound_to_primary_task_and_cannot_cross_tasks(grants):
    result = accept(grants, bound_input_message_id="msg_primary")
    assert all(c["message_id"] == "msg_primary" for c in result["credentials"])
    with pytest.raises(temporary.LoanRefused):
        accept(grants, bound_input_message_id="msg_next", execution_id="execution_next")
    with grants.store.transaction() as connection:
        permission_system.end_task_credentials(connection, RESIDENT, "msg_primary")
    assert all(not grants.holds(RESIDENT, "@contributor", r) for r in RESOURCES)


def test_disabled_user_cannot_issue(grants):
    from management_portal.permission_system.categories import accounts
    accounts().upsert_user("lender", actor="portal", status="disabled")
    with pytest.raises(PermissionError, match="停用"):
        accept(grants)


def test_failed_audit_rolls_back_credentials(grants, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(type(grants.store), "record_user_authorization", fail)
    with pytest.raises(RuntimeError, match="audit write"):
        accept(grants)
    with grants.store.read() as connection:
        assert grants.store.entries(connection, where="message_id=%s", parameters=(MESSAGE,)) == []


def test_issuer_permission_change_cannot_commit_between_check_and_signature(grants, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    checked, release, changed = Event(), Event(), Event()
    original = temporary.issue
    def gated(*args, **kwargs):
        checked.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(temporary, "issue", gated)

    def remove_right():
        with grants.store.transaction() as connection:
            grants.store.execute(connection, "DELETE FROM {s}.scheme_entries WHERE scheme_id=%s AND p_node='lender' AND expires_at IS NULL", (grants.store.scheme_id,))
            changed.set()
            grants.store.bump(connection)

    with ThreadPoolExecutor(max_workers=2) as pool:
        issuance = pool.submit(accept, grants, RESOURCES[:1])
        try:
            assert checked.wait(5)
            removal = pool.submit(remove_right)
            assert changed.wait(5)
            assert not removal.done()
        finally:
            release.set()
        result = issuance.result(timeout=5)
        removal.result(timeout=5)
    # Issuance linearized first; later issuer loss follows existing loan semantics.
    assert result["credentials"]
    with pytest.raises(temporary.LoanRefused):
        accept(grants, RESOURCES[1:])
