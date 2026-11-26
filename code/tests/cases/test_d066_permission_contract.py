"""d066 permission contract through the normal owner and isolated PostgreSQL."""
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store, ReadOnlyStore
from management_portal.permission_system.scheme import repository as repo
from management_portal.permission_system.scheme import d066_migration as migration
from management_portal.permission_system.scheme.compiler import rules_digest
from tests.cases.permission_test_support import permissions
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def document():
    return json.loads((repo.BUILTIN_DIRECTORY / "default.json").read_text())


@pytest.fixture
def prepared_loan(permissions, monkeypatch):
    from types import SimpleNamespace
    from management_portal import society_service
    from management_portal.permission_system import management
    from management_portal.permission_system.facts import Fact

    issuer, recipient = "resident_121212121212", "resident_343434343434"
    permissions.place(Fact("resident", issuer), Fact("resident", recipient))
    permissions.grant(issuer, "@shared", "@admin")
    assert not permissions.holds(recipient, "@admin", "@shared")
    state = {"prepared": True}
    calls = []

    def eligible(**args):
        calls.append(args)
        return state["prepared"] and args == {
            "resident_id": recipient, "message_id": "msg-prepared-grant", "issuer": issuer}

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(is_resident_input_open_for_issuer=eligible)))
    # The legacy input-open predicate is deliberately true: it must no longer
    # be sufficient to mint a public task grant on someone else's active task.
    monkeypatch.setattr(management, "input_open", lambda *args: True)
    args = {"principal_id": recipient, "message_id": "msg-prepared-grant",
            "a": "@admin", "resource_id": "@shared"}

    def create(**changes):
        return permissions.implementation.manage("CreateTemporaryCredential", {**args, **changes},
                                                actor=issuer, enforce=False)

    return permissions, issuer, recipient, args, state, calls, create


def _approved_state(store, connection):
    return (store.entries(connection), store.execute(connection,
        "SELECT * FROM {s}.prepared_task_credentials ORDER BY issuer,recipient,message_id,a_node,r_node").fetchall(),
        store.execute(connection, "SELECT * FROM {s}.state").fetchall())


def test_approved_credentials_exact_scope_atomic_activation_and_retry(prepared_loan):
    from management_portal import permission_system
    p, issuer, recipient, args, state, calls, create = prepared_loan
    first, second = create(), create(a="@contributor")
    store = p.store
    request = dict(issuer=issuer, resident_id=recipient, message_id=args["message_id"],
                   credentials=[{"a": "@admin", "resource_id": "@shared"},
                                {"a": "@contributor", "resource_id": "@shared"}])
    with store.read() as con:
        before = _approved_state(store, con)
    with pytest.raises(RuntimeError, match="acceptance failed"), store.transaction() as con:
        assert permission_system.activate_approved_task_credentials(con, **request) == 2
        raise RuntimeError("acceptance failed")
    with store.read() as con:
        assert _approved_state(store, con) == before
    assert not p.holds(recipient, "@admin", "@shared")
    with store.transaction() as con:
        assert permission_system.activate_approved_task_credentials(con, **request) == 2
    assert p.holds(recipient, "@admin", "@shared")
    with store.transaction() as con:
        before_retry = _approved_state(store, con)
        assert permission_system.activate_approved_task_credentials(con, **request) == 0
        assert _approved_state(store, con) == before_retry
    assert create()["credential_id"] == first["credential_id"]
    assert create(a="@contributor")["deadline_at"] == second["deadline_at"]


@pytest.mark.parametrize("damage", ["extra", "missing", "issuer", "recipient", "message", "expired",
                                    "revoked", "ended", "scheme", "no_receipt", "wrong_entry", "mixed"])
def test_approved_credentials_reject_mismatched_or_dead_set_without_writes(prepared_loan, damage):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    first = create()
    create(a="@contributor")
    store = p.store
    request = dict(issuer=issuer, resident_id=recipient, message_id=args["message_id"],
                   credentials=[{"a": "@admin", "resource_id": "@shared"},
                                {"a": "@contributor", "resource_id": "@shared"}])
    if damage == "extra":
        request["credentials"].pop()
    elif damage == "missing":
        request["credentials"].append({"a": "@viewer", "resource_id": "@shared"})
    elif damage in {"issuer", "recipient", "message"}:
        request[{"issuer": "issuer", "recipient": "resident_id", "message": "message_id"}[damage]] = "wrong-" + damage
    with store.transaction() as con:
        if damage == "expired":
            store.execute(con, "UPDATE {s}.scheme_entries SET expires_at=clock_timestamp()-interval '1 second' "
                          "WHERE entry_id=%s", (first["credential_id"],))
        elif damage == "revoked":
            store.delete(con, where="entry_id=%s", parameters=(first["credential_id"],))
        elif damage == "ended":
            store.execute(con, "UPDATE {s}.prepared_task_credentials SET state='ended' WHERE credential_id=%s",
                          (first["credential_id"],))
        elif damage == "scheme":
            store.execute(con, "UPDATE {s}.active_scheme SET scheme_id='default'")
        elif damage == "no_receipt":
            store.execute(con, "DELETE FROM {s}.prepared_task_credentials WHERE credential_id=%s",
                          (first["credential_id"],))
        elif damage == "wrong_entry":
            store.execute(con, "UPDATE {s}.scheme_entries SET issued_by='other-issuer' WHERE entry_id=%s",
                          (first["credential_id"],))
        elif damage == "mixed":
            store.execute(con, "UPDATE {s}.scheme_entries SET disabled=FALSE WHERE entry_id=%s",
                          (first["credential_id"],))
            store.execute(con, "UPDATE {s}.prepared_task_credentials SET state='active' WHERE credential_id=%s",
                          (first["credential_id"],))
        before = _approved_state(store, con)
        with pytest.raises(temporary.LoanRefused):
            permission_system.activate_approved_task_credentials(con, **request)
        assert _approved_state(store, con) == before


@pytest.mark.parametrize("loss", ["delete", "deny", "expired", "resource"])
def test_approved_credentials_rechecks_issuer_in_same_transaction(prepared_loan, loss):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    from management_portal.permission_system.engine.store import new_entry_id
    p, issuer, recipient, args, state, calls, create = prepared_loan
    create()
    assert p.holds(issuer, "@admin", "@shared")  # Warm the regular engine cache before the uncommitted change.
    store = p.store
    request = dict(issuer=issuer, resident_id=recipient, message_id=args["message_id"],
                   credentials=[{"a": "@admin", "resource_id": "@shared"}])
    with store.transaction() as con:
        if loss == "delete":
            store.delete(con, where="p_node=%s AND expires_at IS NULL", parameters=(issuer,))
        elif loss == "deny":
            store.add(con, [Entry(new_entry_id(), "deny", issuer, "@admin", "@shared")])
        elif loss == "expired":
            store.execute(con, "UPDATE {s}.scheme_entries SET expires_at=clock_timestamp()-interval '1 second' "
                          "WHERE p_node=%s", (issuer,))
            store.bump(con)
        else:
            # An issuer grant narrowed to another resource no longer covers this approval.
            store.execute(con, "UPDATE {s}.scheme_entries SET r_node='original-parent' WHERE p_node=%s", (issuer,))
            store.bump(con)
        before = _approved_state(store, con)
        with pytest.raises(temporary.LoanRefused, match="签发者"):
            permission_system.activate_approved_task_credentials(con, **request)
        assert _approved_state(store, con) == before
    assert not p.holds(recipient, "@admin", "@shared")


@pytest.mark.parametrize("ending", ["revoke", "terminal"])
def test_approved_credentials_never_restore_revoked_active_grant(prepared_loan, ending):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    receipt = create()
    store = p.store
    request = dict(issuer=issuer, resident_id=recipient, message_id=args["message_id"],
                   credentials=[{"a": "@admin", "resource_id": "@shared"}])
    with store.transaction() as con:
        permission_system.activate_approved_task_credentials(con, **request)
    if ending == "revoke":
        temporary.revoke(store, receipt["credential_id"], actor=issuer)
    else:
        with store.transaction() as con:
            permission_system.end_task_credentials(con, recipient, args["message_id"])
    with store.transaction() as con:
        before = _approved_state(store, con)
        with pytest.raises(temporary.LoanRefused):
            permission_system.activate_approved_task_credentials(con, **request)
        assert _approved_state(store, con) == before
    with pytest.raises(temporary.LoanRefused):
        create()


def test_approved_credentials_current_resource_path_and_concurrent_retry(prepared_loan):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    store, scheme = p.store, p.scheme
    resource = "article_121212121212"
    with store.transaction() as con:
        store.place(con, "R", resource, scheme.trees["R"].element_path("@shared"))
    create(resource_id=resource)
    request = dict(issuer=issuer, resident_id=recipient, message_id=args["message_id"],
                   credentials=[{"a": "@admin", "resource_id": resource}])
    assert p.holds(issuer, "@admin", resource)
    with pytest.raises(RuntimeError, match="rollback path"), store.transaction() as con:
        store.place(con, "R", resource, scheme.trees["R"].element_path(scheme.trees["R"].root))
        before = _approved_state(store, con)
        with pytest.raises(temporary.LoanRefused, match="签发者"):
            permission_system.activate_approved_task_credentials(con, **request)
        assert _approved_state(store, con) == before
        raise RuntimeError("rollback path")

    def accept():
        with store.transaction() as con:
            return permission_system.activate_approved_task_credentials(con, **request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: accept(), range(2))) == [0, 1]
    assert p.holds(recipient, "@admin", resource)


@pytest.mark.parametrize("scope", [[{"a": "@admin", "resource_id": "@shared"}] * 2,
                                  [{"a": "@admin", "resource_id": "@shared", "issuer": "spoof"}],
                                  [{"a": "@admin", "resource_id": " @shared"}], None])
def test_approved_credentials_invalid_scope_rejected_before_sql(prepared_loan, monkeypatch, scope):
    from management_portal import permission_system
    from management_portal.permission_system import inline
    p, issuer, recipient, args, state, calls, create = prepared_loan
    create()

    def forbidden(*args, **kwargs):
        pytest.fail("invalid approval reached the database")

    monkeypatch.setattr(inline, "lock", forbidden)
    with p.store.transaction() as con, pytest.raises(ValueError):
        permission_system.activate_approved_task_credentials(con, issuer=issuer, resident_id=recipient,
            message_id=args["message_id"], credentials=scope)


def test_approved_credentials_deadline_after_owner_read_is_rejected(prepared_loan, monkeypatch):
    from datetime import datetime, timezone
    from management_portal import permission_system
    from management_portal.permission_system import personal_ama, temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    receipt = create()
    store = p.store
    original = ReadOnlyStore.execute
    owners_read = []
    owners = personal_ama.readonly_owners_of

    def read_owners(principals):
        value = owners(principals)
        owners_read.append(True)
        return value

    def at_deadline(self, con, sql, parameters=()):
        if "extract(epoch FROM clock_timestamp())" in sql:
            assert owners_read, "expiry must be evaluated after blocking owner reads"
            # Keep the real PG read and move only the decision clock to the fixed deadline.
            return con.execute("SELECT extract(epoch FROM %s::timestamptz) AS now",
                               (datetime.fromtimestamp(receipt["deadline_at"], timezone.utc),))
        return original(self, con, sql, parameters)

    monkeypatch.setattr(personal_ama, "readonly_owners_of", read_owners)
    monkeypatch.setattr(ReadOnlyStore, "execute", at_deadline)
    with store.transaction() as con:
        before = _approved_state(store, con)
        with pytest.raises(temporary.LoanRefused, match="过期"):
            permission_system.activate_approved_task_credentials(con, issuer=issuer, resident_id=recipient,
                message_id=args["message_id"], credentials=[{"a": "@admin", "resource_id": "@shared"}])
        assert _approved_state(store, con) == before


def test_prepared_credential_is_inactive_until_acceptance(prepared_loan):
    from management_portal import permission_system
    p, issuer, recipient, args, state, calls, create = prepared_loan
    receipt = create()
    assert not p.holds(recipient, "@admin", "@shared")
    assert receipt["state"] == "pending" and receipt["expires_at"] is None
    assert calls[-1]["issuer"] == issuer
    assert create() == receipt
    with pytest.raises(RuntimeError), p.store.transaction() as con:
        assert permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"]) == 1
        raise RuntimeError("admission transaction failed")
    assert not p.holds(recipient, "@admin", "@shared")
    with p.store.transaction() as con:
        assert permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"]) == 1
    state["prepared"] = False
    assert p.holds(recipient, "@admin", "@shared")
    replay = create()
    assert replay["credential_id"] == receipt["credential_id"]
    assert replay["deadline_at"] == receipt["deadline_at"] and replay["state"] == "active"
    with p.store.transaction() as con:
        assert permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"]) == 0


@pytest.mark.parametrize("change", [{"message_id": "another-input"}, {"principal_id": "resident_565656565656"}])
def test_prepared_credential_rejects_unproven_input_without_writes(prepared_loan, change):
    from management_portal.permission_system import temporary
    p, *_rest, create = prepared_loan
    with p.store.read() as con:
        before = p.store.entries(con)
    with pytest.raises(temporary.LoanRefused):
        create(**change)
    with p.store.read() as con:
        assert p.store.entries(con) == before


@pytest.mark.parametrize("ending", ["revoke", "end", "expired"])
def test_prepared_credential_terminal_receipt_prevents_regrant(prepared_loan, ending):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    receipt = create()
    if ending == "revoke":
        temporary.revoke(p.store, receipt["credential_id"], actor=issuer)
    elif ending == "end":
        with p.store.transaction() as con:
            permission_system.end_task_credentials(con, recipient, args["message_id"])
    else:
        with p.store.transaction() as con:
            p.store.execute(con, "UPDATE {s}.scheme_entries SET expires_at=now()-interval '1 second' "
                            "WHERE entry_id=%s", (receipt["credential_id"],))
    with pytest.raises(temporary.LoanRefused):
        create()
    with p.store.transaction() as con:
        assert permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"]) == 0
    assert not p.holds(recipient, "@admin", "@shared")


def test_prepared_credential_rechecks_grant_and_closes_release_window(prepared_loan):
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    p.deny(issuer, "@shared", "@admin")
    with pytest.raises(temporary.LoanRefused):
        create()
    with p.store.transaction() as con:
        p.store.delete(con, where="effect='deny'", parameters=())
    state["prepared"] = False
    with pytest.raises(temporary.LoanRefused):
        create()
    state["prepared"] = True
    receipt = create()
    with pytest.raises(temporary.LoanRefused):
        create(expires_in_seconds=60)
    assert create()["credential_id"] == receipt["credential_id"]


def test_prepared_credential_concurrent_retry_and_acceptance(prepared_loan, monkeypatch):
    import threading
    from management_portal import permission_system
    from management_portal.permission_system import management

    p, issuer, recipient, args, state, calls, create = prepared_loan
    checked, proceed = threading.Event(), threading.Event()
    probe = management.input_prepared

    def paused(*values):
        checked.set()
        assert proceed.wait(10)
        return probe(*values)

    monkeypatch.setattr(management, "input_prepared", paused)

    def accept():
        with p.store.transaction() as con:
            return permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"])

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(create)
        assert checked.wait(10)
        admission = pool.submit(accept)
        retry = pool.submit(create)
        assert not admission.done()
        assert not p.holds(recipient, "@admin", "@shared")
        proceed.set()
        initial, repeated = first.result(timeout=10), retry.result(timeout=10)
        assert admission.result(timeout=10) == 1
    assert initial["credential_id"] == repeated["credential_id"]
    assert p.holds(recipient, "@admin", "@shared")
    with p.store.read() as con:
        assert len(p.store.entries(con, where="message_id=%s", parameters=(args["message_id"],))) == 1


def test_prepared_credential_acceptance_never_enables_unrelated_or_tampered_rows(prepared_loan):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    from management_portal.permission_system.engine.store import new_entry_id

    p, issuer, recipient, args, state, calls, create = prepared_loan
    receipt = create()
    unrelated = Entry(new_entry_id(), "permit", recipient, "@admin", "@shared", disabled=True,
                      issued_by=issuer, issued_at=time.time(), expires_at=time.time()+3600,
                      message_id=args["message_id"])
    with p.store.transaction() as con:
        p.store.add(con, [unrelated])
        p.store.execute(con, "UPDATE {s}.scheme_entries SET a_node='@viewer' WHERE entry_id=%s",
                        (receipt["credential_id"],))
    with pytest.raises(ValueError, match="不匹配"), p.store.transaction() as con:
        permission_system.activate_prepared_task_credentials(con, recipient, args["message_id"])
    with p.store.read() as con:
        assert all(row.disabled for row in p.store.entries(con, where="message_id=%s", parameters=(args["message_id"],)))
    with pytest.raises(temporary.LoanRefused):
        create()


def test_prepared_credential_keeps_duration_and_trusted_input_contract(prepared_loan):
    from management_portal.permission_system import temporary
    p, issuer, recipient, args, state, calls, create = prepared_loan
    duration = create(message_id="", expires_in_seconds=60)
    assert duration["state"] == "active" and duration["lifetime"] == "duration"
    assert duration["expires_at"] == duration["deadline_at"]
    temporary.revoke(p.store, duration["credential_id"], actor=issuer)
    scheme, engine, _ = p.implementation.current()
    with pytest.raises(temporary.LoanRefused):
        temporary.issue(engine, scheme, issuer=issuer, recipient=recipient, a="@admin", resource="@shared",
                        message_id="trusted-input", input_open=lambda *args: False)
    trusted = temporary.issue(engine, scheme, issuer=issuer, recipient=recipient, a="@admin", resource="@shared",
                              message_id="trusted-input", input_open=lambda *args: True)
    assert trusted["state"] == "active" and trusted["expires_at"] is None
    assert p.holds(recipient, "@admin", "@shared")


@pytest.fixture
def prepared_admission(delivery_resident, monkeypatch):
    from management_portal import society_service
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service import resident_domain
    from management_portal.society_service import contracts, server
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.permission_test_support import enable
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload

    recipient = delivery_resident["resident_id"]
    sender = resident_domain.register_resident(_new_session_payload("Credential issuer"), "sample_admin")
    issuer = sender["resident_id"]
    p = enable(monkeypatch)
    p.place(Fact("resident", recipient), Fact("resident", issuer))
    p.grant(issuer, recipient, "@message")
    p.grant(issuer, "@shared", "@admin")
    client = SocietyServiceClient()
    def dispatch(self, method, params=None, **kwargs):
        assert method == "domain.call"
        operation = contracts.operation_contract(params["domain"], params["name"])
        handler, bound = server._bind_domain_call(params["domain"], params["name"],
                                                  params["args"], params["kwargs"])
        return server._invoke_domain(operation, handler, bound)
    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    monkeypatch.setattr(society_service, "get_society_service", lambda: client)
    args = {"principal_id": recipient, "message_id": "msg_abcdef123456abcdef123456abcdef12",
            "a": "@admin", "resource_id": "@shared"}
    message = {"message_id": args["message_id"], "content_parts": [{"type": "text", "text": "Exact prepared task"}]}

    def create(**changes):
        return p.implementation.manage("CreateTemporaryCredential", {**args, **changes}, actor=issuer, enforce=False)

    return p, issuer, recipient, args, message, create


def test_prepared_actual_admission_activation_and_completion(prepared_admission):
    from management_portal.permission_system import temporary
    from management_portal.society_service import resident_prepared_inputs as prepared
    from management_portal.society_service import resident_protocol, resident_conversation_stream
    from management_portal.society_service import resident_policy_facts
    from tests.cases.isa_test_support import run

    p, issuer, recipient, args, message, create = prepared_admission
    arguments = {"resident_id": recipient, "message": message}
    assert run("PrepareResidentInput", arguments, actor=issuer, key="credential-prepare").raise_for_error()["status"] == "prepared"
    receipt = create()
    assert receipt["state"] == "pending" and not p.holds(recipient, "@admin", "@shared")
    with prepared.release(recipient, issuer, message):
        result = resident_protocol.deliver_resident_input(arguments, issuer)
    assert result["message_id"] == args["message_id"]
    assert p.holds(recipient, "@admin", "@shared")
    assert not resident_policy_facts.is_resident_input_open_for_issuer(recipient, args["message_id"], issuer)
    assert create()["credential_id"] == receipt["credential_id"]
    with pytest.raises(temporary.LoanRefused):
        create(a="@viewer")
    resident_conversation_stream.get_repository().set_input_state(recipient, args["message_id"], "completed")
    assert not p.holds(recipient, "@admin", "@shared")
    with pytest.raises(temporary.LoanRefused):
        create()


def test_prepared_actual_admission_cancel_and_failed_commit(prepared_admission, monkeypatch):
    from management_portal import permission_system
    from management_portal.permission_system import temporary
    from management_portal.society_service import resident_prepared_inputs as prepared
    from management_portal.society_service import resident_protocol, resident_conversation_stream

    p, issuer, recipient, args, message, create = prepared_admission
    prepared.prepare(recipient, issuer, message)
    receipt = create()
    actual = permission_system.activate_prepared_task_credentials

    def fail_after_activation(*values):
        assert actual(*values) == 1
        raise RuntimeError("acceptance failed after enabling grant")

    with monkeypatch.context() as patch:
        patch.setattr(permission_system, "activate_prepared_task_credentials", fail_after_activation)
        with pytest.raises(RuntimeError, match="acceptance failed"), prepared.release(recipient, issuer, message):
            resident_protocol.deliver_resident_input({"resident_id": recipient, "message": message}, issuer)
    assert not p.holds(recipient, "@admin", "@shared")
    assert create() == receipt
    assert resident_conversation_stream.get_repository().input_states(recipient) == []
    assert prepared.cancel(recipient, issuer, args["message_id"])["status"] == "cancelled"
    with pytest.raises(temporary.LoanRefused):
        create()


def test_prepared_public_create_receipt_listing_and_forged_issuer(prepared_admission):
    from management_portal.permission_system import temporary
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service import resident_prepared_inputs as prepared
    from tests.cases.isa_test_support import run

    p, issuer, recipient, args, message, create = prepared_admission
    prepared.prepare(recipient, issuer, message)
    outsider = "resident_787878787878"
    p.place(Fact("resident", outsider))
    p.grant(outsider, "@shared", "@admin")
    denied = run("CreateTemporaryCredential", args, actor=outsider, key="wrong-prepared-issuer")
    assert denied.status != "committed"
    assert temporary.listing(p.store, recipient) == []
    receipt = run("CreateTemporaryCredential", args, actor=issuer, key="public-stage").raise_for_error()
    assert receipt["state"] == "pending" and receipt["expires_at"] is None
    listed = temporary.listing(p.store, recipient)
    assert len(listed) == 1 and listed[0]["state"] == "pending"
    assert not p.holds(recipient, "@admin", "@shared")
    repeated = run("CreateTemporaryCredential", args, actor=issuer, key="public-stage-owner-retry").raise_for_error()
    assert repeated == receipt
    prepared.cancel(recipient, issuer, args["message_id"])
    refused = run("CreateTemporaryCredential", args, actor=issuer, key="public-stage-after-cancel")
    assert refused.status != "committed" and not p.holds(recipient, "@admin", "@shared")


def test_readonly_categories_use_one_normalized_owner_snapshot(monkeypatch):
    from types import SimpleNamespace
    from management_portal.permission_system import categories as category_owner

    users = {
        "alice": {"status": "active", "category": "portal_admin"},
        "bob": {"status": "disabled", "category": "intern"},
        "carol": {"status": "active", "category": "member"},
        "gone": {"status": "deleted", "category": "portal_admin"},
    }
    original = copy.deepcopy(users)
    calls = []

    def read_snapshot(*, read_only=False):
        assert read_only is True, "categories called the initializing account reader"
        calls.append(True)
        return {"users": users}

    monkeypatch.setattr(category_owner, "accounts", lambda: SimpleNamespace(snapshot=read_snapshot))
    expected = {"alice": "portal_admin", "bob": "intern"}
    assert category_owner.categories().all(read_only=True) == expected
    assert category_owner.categories_from_users(users) == expected
    assert calls == [True]
    assert users == original


@pytest.fixture
def cold_fact_owners(permissions, monkeypatch):
    """Real isolated domain stores; Society transport alone is dispatched in process."""
    from management_common.portal_database import portal_database
    from management_portal import portal_features, project_registry
    from management_portal.permission_system.categories import accounts
    from management_portal.resource_index_records import resource_record
    from management_portal.resource_index_store import IndexStore
    from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.society_service import contracts, server
    from management_portal.society_service.transport import SocietyServiceClient

    permissions.users("reader", "author", category="intern")
    permissions.grant("reader", "@shared", a="@admin")
    registry = get_registry_store()
    with registry.writer(project_registry.COLLECTION) as writer:
        writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, {
            **project_registry.empty_registry(), "projects": [{"id": "FactProject"}],
        })
    lineage, resident = "lineage_111111111111", "resident_111111111111"
    workspace, family = "workspace_111111111111", "family_111111111111"
    descriptors = CollaborationDescriptorStore()
    descriptors.put({"resource_id": lineage, "project_ids": ["FactProject"],
                     "attributes": {"created_by": "author"}})
    descriptors.put({"resource_id": resident, "attributes": {"lineage_id": lineage,
                     "created_by": "author", "personal_owner_id": "author"}})
    descriptors.put({"resource_id": family, "attributes": {"lineage_id": lineage,
                     "steward_resident_id": resident}})
    # Minimal installed Society table contract consumed by this read operation;
    # no Society service/runtime is started by this fixture.
    society = portal_database(schema="lineage")
    with society.transaction() as con:
        con.execute("CREATE TABLE IF NOT EXISTS residents (resident_id TEXT PRIMARY KEY, "
                    "owned_workspace_id TEXT, deleted_at TIMESTAMPTZ)")
        con.execute("INSERT INTO residents VALUES (%s,%s,NULL)", (resident, workspace))
    index = IndexStore()
    index.publish_snapshot("cold-test", [resource_record(
        resource_id="decision_111111111111", resource_type="decision", title="Existing knowledge",
        summary="", adapter="cold-test", path="", modified_at="2026-11-26T00:00:00Z",
        attributes={"project_ids": ["FactProject"], "created_by": "author"},
    ), resource_record(resource_id=workspace, resource_type="workspace", title="Owned workspace",
        summary="", adapter="cold-test", path="", modified_at="2026-11-26T00:00:00Z", attributes={})], {})
    # Prepare installed feature storage through its ordinary owner before the
    # cold-read assertion. Preview must not call these initializing paths.
    for _feature, records in portal_features.contributions("permission.records"):
        records()

    def dispatch(self, method, params=None, **kwargs):
        assert method == "domain.call"
        assert params["domain"] == "projection"
        assert params["name"] == "list_permission_resident_workspaces"
        operation = contracts.operation_contract(params["domain"], params["name"])
        assert operation.mode == "read"
        handler, bound = server._bind_domain_call(params["domain"], params["name"],
                                                  params["args"], params["kwargs"])
        return server._invoke_domain(operation, handler, bound)

    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    from tests.cases.permission_test_support import ACTIVE_COPY
    proposed_rules = copy.deepcopy(permissions.implementation.schemes.get(ACTIVE_COPY)["rules"])
    return {"permissions": permissions, "registry": registry, "index": index,
            "accounts": accounts(), "society": society, "resident": resident,
            "workspace": workspace, "family": family, "proposed_rules": proposed_rules}


def _cold_domain_state():
    """Snapshot only this test's domain tables; never use deployment state."""
    from psycopg import sql
    from management_common.portal_database import portal_database
    from management_portal.resources.config import resource_database_schema

    result = {}
    for schema in ("permission", "portal_identity", "resource_registry", "lineage",
                   "research_map", "chatrooms", "portal_notice", resource_database_schema()):
        db = portal_database(schema=schema)
        with db.readonly_snapshot() as con:
            names = [row["tablename"] for row in con.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (db.schema,))]
            result[schema] = {name: sorted((dict(row) for row in con.execute(
                sql.SQL("SELECT * FROM {}").format(sql.Identifier(name)))), key=repr) for name in names}
    return result


def _deny_cold_initializers(patch):
    from management_common.domain_database import DomainDatabase
    from management_common.postgres_database import PostgresDatabase
    from management_portal import portal_access_control, project_registry
    from management_portal.permission_system import categories as category_owner
    from management_portal.resource_index_store import IndexStore
    from management_portal.resources.registry_store import RegistryStore, reset_registry_store_for_tests
    from management_portal.resources.service import ResourceService
    from management_portal.society_service.state_store import LineageDataStore

    def forbidden(*args, **kwargs):
        raise AssertionError("cold preview attempted initialization, cache read, or migration")

    category_owner._ACCOUNTS = None
    reset_registry_store_for_tests()
    project_registry.reset_cache_for_tests()
    for target, name in ((DomainDatabase, "ensure"), (PostgresDatabase, "_connection_pool"),
                          (RegistryStore, "_ensure"), (IndexStore, "__init__"),
                          (ResourceService, "__init__"), (LineageDataStore, "__init__"),
                          (portal_access_control.PortalAccessStore, "_current"),
                          (project_registry, "load"), (Store, "ensure"), (Store, "transaction")):
        patch.setattr(target, name, forbidden)


def _run_cold_preview(name, owner, seen, *, actor="reader"):
    from types import SimpleNamespace
    from management_portal import permission_system as api
    from management_portal.hatg.catalog.permission import DECLARATIONS
    from management_portal.permission_system.facts import collect_portal_facts
    from management_portal.resources.runtime import readonly_index_snapshot
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.permission_test_support import ACTIVE_COPY

    arguments = {"scheme_id": ACTIVE_COPY}
    if name == "GetPermissionSchemeUpdatePreview":
        rules = copy.deepcopy(owner["proposed_rules"])
        rules["title"] = "Proposed title"
        next(node for node in rules["trees"]["R"]["nodes"] if node["id"] == "@project")["parent"] = "@public"
        arguments.update(rules=rules, diff=True)

    def collect():
        facts = collect_portal_facts(read_only=True, resident_workspaces=lambda:
            SocietyServiceClient().projection.list_permission_resident_workspaces(),
            index_snapshot=readonly_index_snapshot)
        seen.update({fact.id: fact for fact in facts})
        return facts

    declaration = next(item for item in DECLARATIONS if item.name == name)
    api.authorize_instruction(declaration, arguments, SimpleNamespace(caller=actor))
    return api.manage(name, arguments, actor=actor, collect_facts=collect)


@pytest.mark.parametrize("name", ["GetPermissionSchemeActivationPreview", "GetPermissionSchemeUpdatePreview"])
def test_cold_fact_preview_reads_real_owners_and_current_changes(cold_fact_owners, monkeypatch, name):
    from management_portal import project_registry
    from management_portal.permission_system.categories import categories
    from management_portal.permission_system import personal_ama

    owner, seen = cold_fact_owners, {}
    before = _cold_domain_state()
    with monkeypatch.context() as patch:
        _deny_cold_initializers(patch)
        patch.setattr(personal_ama, "_OWNERS", {owner["resident"]: "outdated-owner"})
        result = _run_cold_preview(name, owner, seen)
        assert categories().all(read_only=True) == {"author": "intern", "reader": "intern"}
        assert personal_ama.readonly_owners_of([owner["resident"]]) == {owner["resident"]: "author"}
    assert result.get("activated") is False if "Activation" in name else result["valid"] is True
    assert _cold_domain_state() == before
    assert seen["author"].category == "intern"
    assert seen["decision_111111111111"].owners == {"project": "FactProject"}
    assert seen[owner["family"]].steward == owner["resident"]
    assert seen[owner["resident"]].dependents[0].id == owner["workspace"]
    assert owner["workspace"] not in seen  # the index must not detach its Resident owner

    owner["accounts"].set_category("author", "member", actor="test-actor")
    with owner["registry"].writer(project_registry.COLLECTION) as writer:
        current = writer.get(project_registry.COLLECTION, project_registry.DOCUMENT_ID)
        writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID,
                   {**current, "projects": [*current["projects"], {"id": "NewFactProject"}]})
    changed, seen = _cold_domain_state(), {}
    with monkeypatch.context() as patch:
        _deny_cold_initializers(patch)
        _run_cold_preview(name, owner, seen)
    assert seen["author"].category == "member" and "NewFactProject" in seen
    assert _cold_domain_state() == changed


def test_cold_fact_preview_missing_owner_table_fails_without_partial_facts(cold_fact_owners, monkeypatch):
    import psycopg

    owner, seen = cold_fact_owners, {}
    with owner["index"].database.transaction() as con:
        con.execute("ALTER TABLE resource_index_records RENAME TO cold_test_absent_index")
    try:
        before = _cold_domain_state()
        with monkeypatch.context() as patch:
            _deny_cold_initializers(patch)
            with pytest.raises(psycopg.errors.UndefinedTable):
                _run_cold_preview("GetPermissionSchemeActivationPreview", owner, seen)
        assert not seen
        assert _cold_domain_state() == before
    finally:
        with owner["index"].database.transaction() as con:
            con.execute("ALTER TABLE cold_test_absent_index RENAME TO resource_index_records")


@pytest.mark.parametrize("missing", ["resident_workspaces", "index_snapshot"])
def test_cold_fact_preview_requires_readers_before_any_owner(monkeypatch, missing):
    from management_portal.permission_system import categories
    from management_portal.permission_system.facts import collect_portal_facts

    def forbidden():
        pytest.fail("missing callback must fail before reading any domain")

    monkeypatch.setattr(categories, "accounts", forbidden)
    readers = {"resident_workspaces": forbidden, "index_snapshot": forbidden}
    readers.pop(missing)
    with pytest.raises(ValueError, match="require.*reader"):
        collect_portal_facts(read_only=True, **readers)


@pytest.mark.parametrize("name", ["GetPermissionSchemeActivationPreview", "GetPermissionSchemeUpdatePreview"])
def test_cold_ama_preview_checks_current_administrator_and_revocation(cold_fact_owners, monkeypatch, name):
    from management_portal.hatg.contracts import InstructionError
    from management_portal.permission_system.facts import Fact

    owner, seen = cold_fact_owners, {}
    owner["permissions"].place(Fact("resident", owner["resident"]))
    owner["permissions"].users("author", category="portal_admin")
    before = _cold_domain_state()
    with monkeypatch.context() as patch:
        _deny_cold_initializers(patch)
        _run_cold_preview(name, owner, seen, actor=owner["resident"])
    assert seen["author"].category == "portal_admin"
    assert _cold_domain_state() == before

    owner["permissions"].users("author", category="intern")
    revoked, seen = _cold_domain_state(), {}
    with monkeypatch.context() as patch:
        _deny_cold_initializers(patch)
        with pytest.raises(InstructionError, match="无权"):
            _run_cold_preview(name, owner, seen, actor=owner["resident"])
    assert not seen
    assert _cold_domain_state() == revoked


def snapshot(store):
    with store.read() as con:
        return {table: sorted((dict(r) for r in store.execute(con, f"SELECT * FROM {{s}}.{table}").fetchall()), key=repr)
                for table in ("schemes", "scheme_instances", "scheme_sources", "scheme_nodes", "scheme_entries",
                              "scheme_migrations", "scheme_migration_history", "state", "user_authorizations")}


def legacy(store, *, element="RelayFamilyMessage", temporary=False):
    rules = document()
    rules.update(scheme_id="custom", title="original audit title")
    rules["instructions"].pop("RelayMessage:relay", None)
    for target in migration.targets(element):
        if target != element:
            rules["instructions"].pop(target, None)
    rules["instructions"][element] = {"a": "@contributor"}
    with store.transaction() as con:
        store.execute(con, "INSERT INTO {s}.schemes(scheme_id,document,version,created_by,updated_by) VALUES ('custom',%s::jsonb,3,'alice','alice')", (json.dumps(rules),))
        store.execute(con, "INSERT INTO {s}.scheme_sources(scheme_id,source_scheme_id,rules,digest) VALUES ('copy','custom',%s::jsonb,%s)", (json.dumps(rules), repo.source_digest(rules)))
        scope = store.scoped("custom")
        scope.set_instance(con, rules_digest(rules), rules)
        scope.place(con, "A", element, ["@actions", "@admin", "@contributor"])
        scope.add(con, [Entry("allow", "permit", "alice", element, "resident_aaaaaaaaaaaa",
                             issued_by="bob" if temporary else "", issued_at=time.time() if temporary else None,
                             expires_at=time.time() + 300 if temporary else None,
                             message_id="input-1" if temporary else ""),
                        Entry("deny", "deny", "bob", element, "resident_aaaaaaaaaaaa", disabled=True)])
        for mid in (repo.ISA_NAME_MIGRATION, repo.THREE_PLAN_MIGRATION):
            store.execute(con, "INSERT INTO {s}.scheme_migrations(migration_id) VALUES (%s)", (mid,))
    return rules


def test_builtin_classifies_installed_contract_and_no_retired_aliases():
    owner = repo.Schemes(None)
    assert owner.compiled("default").unmapped() == []
    mapped = document()["instructions"]
    assert mapped["UpdateDeviceSshCredential"] == {"a": "@admin"}
    assert mapped["CheckDeviceSsh"] == {"a": "@viewer"}
    assert mapped["RelayMessage:reply"] == {"a": "@message"}
    assert not (migration.REMOVED & {key.split(":")[0] for key in mapped})
    assert "RunResourceAction" not in mapped


def test_normal_owner_migrates_rules_basis_sources_entries_and_temporary_once():
    store = Store()
    old = legacy(store, temporary=True)
    before = snapshot(store)
    owner = repo.Schemes(store)
    current = owner.get("custom")["rules"]
    assert current["title"] == old["title"]
    assert "RelayFamilyMessage" not in current["instructions"]
    assert current["instructions"]["RelayMessage:relay"] == {"a": "@contributor"}
    after = snapshot(store)
    entries = {row["entry_id"]: row for row in after["scheme_entries"]}
    for original in before["scheme_entries"]:
        row = entries[original["entry_id"]]
        assert row == {**original, "a_node": "RelayMessage:relay", "version": original["version"] + 1}
    assert store.scoped("custom").basis() == current
    assert store.scoped("custom").instance()["rules_digest"] == rules_digest(current)
    assert not owner.source("copy")["outdated"]
    assert after["user_authorizations"] == before["user_authorizations"]
    repo.Schemes(Store())
    assert snapshot(store) == after


@pytest.mark.parametrize("element,temporary", [("RunResourceAction", False), ("MoveResident", False),
                                                  ("DeliverMessage:resident_id", True), ("ListMailboxTodos", False)])
def test_unsafe_permissions_abort_every_table_without_receipt(element, temporary):
    store = Store()
    legacy(store, element=element, temporary=temporary)
    before = snapshot(store)
    with pytest.raises(repo.SchemeConflict):
        migration.migrate(store)
    assert snapshot(store) == before


def test_split_lasting_permission_preserves_both_branches_and_disabled_deny():
    store = Store()
    legacy(store, element="DeliverMessage:resident_id")
    migration.migrate(store)
    rows = snapshot(store)["scheme_entries"]
    assert len(rows) == 4
    assert {r["a_node"] for r in rows} == {"DeliverMessage", "RelayMessage:reply"}
    assert all(r["disabled"] for r in rows if r["effect"] == "deny")
    assert all(r["r_node"] == "resident_aaaaaaaaaaaa" for r in rows)


def test_concurrent_migration_serializes_and_is_idempotent():
    store = Store()
    legacy(store)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: migration.migrate(Store()), range(2)))
    assert sorted(results) == [False, True]
    assert sum(r["migration_id"] == migration.MIGRATION for r in snapshot(store)["scheme_migrations"]) == 1


def test_asymmetric_mailbox_merge_fails_without_rewriting_deny():
    store = Store()
    legacy(store, element="SetMailboxItemRead")
    before = snapshot(store)
    with pytest.raises(repo.SchemeConflict, match="无法等价合并"):
        migration.migrate(store)
    assert snapshot(store) == before


def test_three_previews_do_not_initialize_migrate_or_write(monkeypatch):
    from management_portal.permission_system import schemes
    from management_portal.permission_system.facts import Instance

    store = Store()
    owner = repo.Schemes(store)
    rules = document()
    rules["scheme_id"] = "custom"
    owner.create(rules, actor="alice")
    Instance(owner.compiled("custom"), store).generate([])
    before = snapshot(store)
    def forbidden(*args, **kwargs):
        raise AssertionError("preview attempted owner write or migration")
    readonly = ReadOnlyStore(store.database)
    monkeypatch.setattr(Store, "ensure", forbidden)
    monkeypatch.setattr(Store, "transaction", forbidden)
    monkeypatch.setattr(repo, "migrate_instruction_names", forbidden)
    monkeypatch.setattr(repo, "migrate_three_plan_permissions", forbidden)
    monkeypatch.setattr(migration, "migrate", forbidden)
    reader = repo.Schemes(readonly, migrate=False)
    context = schemes.Context(reader, True, lambda: [], lambda *_: True)
    assert schemes.manage(context, "GetPermissionSchemeCreatePreview", {"scheme_id": "copy", "source_scheme_id": "custom"}, actor="alice")["valid"]
    changed = copy.deepcopy(rules)
    changed["title"] = "proposed title"
    assert schemes.manage(context, "GetPermissionSchemeUpdatePreview", {"scheme_id": "custom", "rules": changed, "diff": True}, actor="alice")["valid"]
    assert schemes.manage(context, "GetPermissionSchemeActivationPreview", {"scheme_id": "custom"}, actor="alice")["activated"] is False
    assert snapshot(readonly) == before


def test_readonly_store_rejects_sql_writes_at_database_boundary():
    import psycopg
    store = Store()
    store.ensure()
    readonly = ReadOnlyStore(store.database)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with readonly.read() as con:
            readonly.execute(con, "DELETE FROM {s}.scheme_entries")


def test_catalog_split_modes_and_entry_ids_only():
    from management_portal.hatg.catalog.permission import DECLARATIONS
    declarations = {d.name: d for d in DECLARATIONS}
    assert "ListTemporaryCredentials" not in declarations
    for name in ("GetPermissionSchemeCreatePreview", "GetPermissionSchemeUpdatePreview", "GetPermissionSchemeActivationPreview"):
        assert not declarations[name].mutates
    for name in ("CreatePermissionScheme", "UpdatePermissionScheme", "ActivatePermissionScheme"):
        assert "dry_run" not in {p.name for p in declarations[name].parameters}
    assert "instruction" not in {p.name for p in declarations["CheckPermission"].parameters}
    assert "a" not in {p.name for p in declarations["CheckInstructionPermission"].parameters}
    assert "entry_id" not in {p.name for p in declarations["DeletePermissionEntry"].parameters}
    assert declarations["SetPermissionSystemEnabled"].owner == "lineage"


def test_operation_selection_requires_owner_derived_apply_requirements():
    from management_portal.permission_system.operation_elements import arguments
    from management_portal.permission_system.requests import Unresolved
    assert arguments("RelayMessage", {"resident_id": "resident_aaaaaaaaaaaa", "reply_to_message_id": "m"}) == {"reply": "resident_aaaaaaaaaaaa"}
    with pytest.raises(Unresolved):
        arguments("ApplyLineageChanges", {"lineage_id": "lineage_aaaaaaaaaaaa"})
    assert arguments("ApplyLineageChanges", {"_permission_operations": [
        {"element": "ApplyLineageChanges:delete_resident.resident_id", "resource_id": "resident_aaaaaaaaaaaa"}
    ]}) == {"delete_resident": {"resident_id": ["resident_aaaaaaaaaaaa"]}}


def test_pure_merge_preview_matches_write_and_preserves_edits():
    from management_portal.permission_system.facts import Instance
    from management_portal.permission_system.scheme.compiler import compile_scheme

    old = document()
    old["scheme_id"] = "custom"
    previous = compile_scheme(repo.installed(old))
    new = copy.deepcopy(old)
    new["entries"].append({"id": "new", "effect": "permit", "p": "@interns", "a": "@contributor", "r": "@shared"})
    compiled = compile_scheme(repo.installed(new))
    store = Store().scoped("custom")
    Instance(previous, store).generate([])
    with store.transaction() as con:
        store.add(con, [Entry("edit", "deny", "@interns", "@viewer", "@shared", disabled=True),
                        Entry("temporary", "permit", "@interns", "@viewer", "@shared", issued_by="bob", expires_at=time.time()+300)])
        generated = store.entries(con, where="expires_at IS NULL")
        removed = next(row for row in generated if row.entry_id != "edit")
        store.delete(con, where="entry_id=%s", parameters=(removed.entry_id,))
    before = snapshot(store)
    reader = ReadOnlyStore(store.database, scheme_id="custom")
    preview = Instance(compiled, reader).regenerate_keeping_edits([], previous, dry_run=True, changes=True)
    assert snapshot(store) == before
    applied = Instance(compiled, store).regenerate_keeping_edits([], previous, changes=True)
    assert {k:v for k,v in preview.items() if k != "preview"} == applied
    with store.read() as con:
        assert store.contents(con) == preview["preview"].entries


def test_readonly_missing_schema_never_creates_it():
    import psycopg
    from types import SimpleNamespace
    store = Store()
    reader = ReadOnlyStore(SimpleNamespace(dsn=store.database.dsn, schema="missing_d066_permission"))
    with pytest.raises(psycopg.errors.UndefinedTable):
        with reader.read() as con:
            reader.execute(con, "SELECT * FROM {s}.scheme_entries")
    with reader.read() as con:
        assert con.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (reader.schema,)).fetchone() is None


def test_domain_elements_keep_specific_grants_denies_and_delete_admin(permissions):
    from management_portal import permission_system as api
    from management_portal.permission_system.facts import Fact
    lineage, resident = "lineage_aaaaaaaaaaaa", "resident_aaaaaaaaaaaa"
    permissions.users("alice", category="intern")
    permissions.place(Fact("lineage", lineage), Fact("resident", resident, owners={"lineage": lineage}))
    permissions.grant("alice", resident, a="RelayMessage:reply")
    assert api.allows("alice", "RelayMessage", {"resident_id": resident, "reply_to_message_id": "original"})
    assert not api.allows("alice", "RelayMessage", {"resident_id": resident, "message_id": "original"})
    permissions.grant("alice", resident, a="RelayMessage:reply", effect="deny")
    assert not api.allows_elements("alice", [("RelayMessage:reply", resident)])
    permissions.grant("alice", lineage, a="ApplyLineageChanges:create_component")
    payload = {"_permission_operations": [{"element": "ApplyLineageChanges:create_component", "resource_id": lineage}]}
    assert api.allows("alice", "ApplyLineageChanges", payload)
    assert not api.allows_elements("alice", [("ApplyLineageChanges:update_component", lineage)])
    permissions.grant("alice", lineage, a="@contributor")
    requirements = [("ApplyLineageChanges:delete_resident.lineage_id", lineage),
                    ("ApplyLineageChanges:delete_resident.resident_id", resident)]
    assert not api.allows_elements("alice", requirements)
    permissions.grant("alice", resident, a="@admin")
    assert api.allows_elements("alice", requirements)
    permissions.grant("alice", resident, a="ApplyLineageChanges:delete_resident.resident_id", effect="deny")
    assert not api.allows_elements("alice", requirements)
    assert not api.allows_elements("alice", [("retired_unknown", resident)])


def test_check_modes_temp_projection_and_entry_array(permissions):
    from management_portal.permission_system.management import Management
    from management_portal.permission_system import schemes
    permissions.users("alice", category="intern")
    permissions.grant("alice", "@shared", a="@contributor")
    manager = Management(permissions.implementation)
    assert manager.check("alice", {"principal_id": "alice", "a": "@contributor", "resource_id": "@shared"})["allowed"]
    with pytest.raises(ValueError, match="CheckInstructionPermission"):
        manager.check("alice", {"principal_id": "alice", "instruction": "GetProject"})
    with pytest.raises(ValueError, match="CheckPermission"):
        manager.check_instruction("alice", {"principal_id": "alice", "a": "@admin"})
    call = manager.check_instruction("alice", {"principal_id": "alice", "instruction": "GetEffectivePermissions", "arguments": {"principal_id": "alice"}})
    assert isinstance(call["allowed"], bool) and call["requests"]
    store = permissions.store
    with store.transaction() as con:
        store.add(con, [Entry("temporary-one", "permit", "alice", "@viewer", "@shared", issued_by="bob", expires_at=time.time()+300)])
    effective = manager.effective("alice", {"principal_id": "alice"})
    assert [r["credential_id"] for r in effective["temporary"]] == ["temporary-one"]
    context = permissions.implementation._context(enforce=False)
    with pytest.raises(ValueError, match="entry_ids"):
        schemes.delete_entries(context, "alice", {"entry_id": "temporary-one"})


def test_public_preview_authorization_and_execution_are_readonly(permissions, monkeypatch):
    from types import SimpleNamespace
    from management_portal import permission_system as api
    from management_portal.hatg.catalog.permission import DECLARATIONS
    permissions.users("alice", category="intern")
    permissions.grant("alice", "@shared", a="@admin")
    before = snapshot(permissions.store)
    database = permissions.store.database
    def forbidden(*args, **kwargs):
        raise AssertionError("public preview attempted persistent initialization or mutation")
    monkeypatch.setattr(Store, "ensure", forbidden)
    monkeypatch.setattr(Store, "transaction", forbidden)
    declaration = next(d for d in DECLARATIONS if d.name == "GetPermissionSchemeActivationPreview")
    api.authorize_instruction(declaration, {"scheme_id": "default"}, SimpleNamespace(caller="alice"))
    result = api.manage(declaration.name, {"scheme_id": "default"}, actor="alice", collect_facts=lambda: [])
    assert result["activated"] is False
    assert snapshot(ReadOnlyStore(database)) == before


def test_admin_user_split_preserves_self_last_admin_and_personal_ama(permissions, monkeypatch):
    from management_portal.permission_system import administrators, personal_ama
    permissions.users("alice", "bob")
    permissions.administrator("alice")
    permissions.administrator("bob")
    assert administrators.passes("alice", "UpdateUserStatus", {"user_id": "bob", "status": "disabled"})
    assert not administrators.passes("alice", "UpdateUser", {"user_id": "bob", "changes": {"category": "member"}})
    with pytest.raises(ValueError, match="自己"):
        administrators.check_change("alice", "alice", status="disabled")
    monkeypatch.setattr(personal_ama, "owner_of", lambda resident_id: "alice")
    assert administrators.passes("resident_aaaaaaaaaaaa", "UpdateUserCategory", {"user_id": "bob", "category": "member"})


def test_lineage_preview_keeps_every_operation_readonly(permissions, monkeypatch):
    from types import SimpleNamespace
    from management_portal import permission_system as api
    from management_portal.application_tool_contracts import permission_catalog
    from management_portal.hatg.contracts import InstructionError
    from management_portal.permission_system.facts import Fact

    lineage, resident = "lineage_aaaaaaaaaaaa", "resident_aaaaaaaaaaaa"
    permissions.users("alice", "bob", category="intern")
    permissions.place(Fact("lineage", lineage), Fact("resident", resident, owners={"lineage": lineage}))
    for user in ("alice", "bob"):
        permissions.grant(user, lineage, a="@contributor")
    permissions.grant("alice", resident, a="@admin")
    payload = {"_permission_operations": [
        {"element": "ApplyLineageChanges:lineage_id", "resource_id": lineage},
        {"element": "ApplyLineageChanges:delete_resident.lineage_id", "resource_id": lineage},
        {"element": "ApplyLineageChanges:delete_resident.resident_id", "resource_id": resident},
    ]}
    database = permissions.store.database
    before = snapshot(permissions.store)
    def forbidden(*args, **kwargs):
        raise AssertionError("preview authorization initialized or mutated the permission owner")
    monkeypatch.setattr(Store, "ensure", forbidden)
    monkeypatch.setattr(Store, "transaction", forbidden)
    monkeypatch.setattr(repo, "migrate_instruction_names", forbidden)
    monkeypatch.setattr(migration, "migrate", forbidden)
    declaration = permission_catalog()["GetLineageChangesPreview"]
    assert api.allows("alice", declaration.name, payload)
    assert not api.allows("bob", declaration.name, payload)
    api.authorize_instruction(declaration, payload, SimpleNamespace(caller="alice"))
    with pytest.raises(InstructionError):
        api.authorize_instruction(declaration, payload, SimpleNamespace(caller="bob"))
    assert api.authorize_reads(declaration, [payload], SimpleNamespace(caller="alice")) == [None]
    assert snapshot(ReadOnlyStore(database)) == before


@pytest.mark.parametrize("element", ["UpdateUser", "UpdateExecutionSettings", "RunResourceAction"])
def test_custom_reclassification_requires_owner_resolution(element):
    old = document()
    for target in migration.targets(element):
        old["instructions"].pop(target, None)
    old["instructions"][element] = {"a": "@custom_stricter"}
    with pytest.raises(repo.SchemeConflict, match="owner"):
        migration.rules(old)


@pytest.mark.parametrize("mode", ["classification", "missing_classification", "entry"])
def test_duty_merge_requires_equivalent_classification_and_entries(mode):
    old = document()
    old["instructions"].pop("ApplyLineageChanges:update_duty", None)
    old["instructions"].pop("GetLineageChangesPreview:update_duty", None)
    old["instructions"]["CreateDuty"] = {"a": "@contributor"}
    if mode != "missing_classification":
        old["instructions"]["UpdateDuty"] = {"a": "@admin" if mode == "classification" else "@contributor"}
    if mode == "entry":
        old["entries"].append({"effect": "deny", "p": "alice", "a": "CreateDuty", "r": "lineage_aaaaaaaaaaaa"})
    with pytest.raises(repo.SchemeConflict):
        migration.rules(old)


def test_instruction_check_rejects_forged_owner_requirements(permissions):
    from management_portal.permission_system.management import Management
    from management_portal.hatg.contracts import InstructionError
    permissions.users("alice", category="intern")
    with pytest.raises(InstructionError):
        Management(permissions.implementation).check_instruction("alice", {
            "principal_id": "alice", "instruction": "ApplyLineageChanges", "arguments": {
                "lineage_id": "lineage_aaaaaaaaaaaa", "operations": [],
                "_permission_operations": [{"element": "ApplyLineageChanges:lineage_id", "resource_id": "@shared"}],
            },
        })


def test_instruction_check_uses_owner_port_without_replacing_reader(permissions):
    from management_portal import permission_system as api
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service.isa.definition import lineage_changes_permission_arguments
    lineage = "lineage_aaaaaaaaaaaa"
    permissions.users("reader", "subject", category="intern")
    permissions.place(Fact("lineage", lineage))
    permissions.grant("subject", lineage, a="ApplyLineageChanges:create_component")
    received = []
    def derive(name, arguments, *, actor, principal_id):
        received.append((name, arguments, actor, principal_id))
        return lineage_changes_permission_arguments(arguments)
    arguments = {"principal_id": "subject", "instruction": "ApplyLineageChanges", "arguments": {
        "lineage_id": lineage, "operations": [{"op": "create", "kind": "component", "value": {"type": "Effect"}}],
    }}
    before = snapshot(permissions.store)
    result = api.manage("CheckInstructionPermission", arguments, actor="reader", derive_arguments=derive)
    assert result["allowed"]
    assert {r["action"] for r in result["requests"]} == {"ApplyLineageChanges:create_component"}
    assert received == [("ApplyLineageChanges", arguments["arguments"], "reader", "subject")]
    assert snapshot(permissions.store) == before
    batch = copy.deepcopy(arguments)
    batch["arguments"]["operations"] *= 2
    assert not api.manage("CheckInstructionPermission", batch, actor="reader", derive_arguments=derive)["allowed"]
    permissions.grant("subject", lineage, a="ApplyLineageChanges:lineage_id")
    assert api.manage("CheckInstructionPermission", batch, actor="reader", derive_arguments=derive)["allowed"]
    permissions.deny("subject", lineage, a="ApplyLineageChanges:create_component")
    assert not api.manage("CheckInstructionPermission", arguments, actor="reader", derive_arguments=derive)["allowed"]
