"""Preserve legacy profile grants without manufacturing Portal administration."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal.permission_system import inline, temporary
from management_portal.permission_system.engine import Engine
from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.par import PARPermissions
from management_portal.permission_system.scheme import d066_migration as migration
from management_portal.permission_system.scheme import repository as repo
from management_portal.permission_system.scheme.compiler import rules_digest
from tests.cases.test_d066_permission_contract import document, legacy, snapshot


ENTRY_ID = "entry_8236fdec67d3108a91b6bdb1"
SOURCE_MESSAGE = "msg_ed8c7e62384080bc8c4dceaa1f5f9b41"
PRINCIPAL = "resident_121212121212"
RESOURCE = "synthetic-user"


def seed(*, mode="permit", scheme_id="default", entry_id=ENTRY_ID, element="UpdateUser"):
    store = Store()
    legacy(store)  # An independent, safe migration must still complete.
    rules = document()
    rules["instructions"].pop("UpdateUserStatus", None)
    rules["instructions"].pop("UpdateUserCategory", None)
    is_temporary = mode in {"task", "duration", "expired"}
    now = time.time()
    entry = Entry(entry_id, "deny" if mode == "deny" else "permit", PRINCIPAL, element, RESOURCE,
                  disabled=mode == "disabled", version=7,
                  issued_by="synthetic-issuer" if is_temporary else "",
                  issued_at=now - 10 if is_temporary else None,
                  expires_at=(now - 1 if mode == "expired" else now + 3600) if is_temporary else None,
                  message_id="synthetic-input" if mode == "task" else "")
    scope = store.scoped(scheme_id)
    with store.transaction() as con:
        store.set_instance(con, rules_digest(rules), rules)
        scope.place(con, "A", element, ["@actions", "@admin"])
        scope.add(con, [entry])
    return store, entry


def rows(store, entry_id=ENTRY_ID):
    return [row for row in snapshot(store)["scheme_entries"] if row["entry_id"] == entry_id]


def decisions(engine):
    return [result.allowed for result in engine.decide(PRINCIPAL, [
        ("UpdateUser", RESOURCE), ("UpdateUser", "another-synthetic-user"),
        ("UpdateUserStatus", "@portal"), ("UpdateUserCategory", "@portal"),
        ("UpdateUserStatus", RESOURCE), ("UpdateUserCategory", RESOURCE),
    ])]


@pytest.mark.parametrize("mode", ["permit", "deny", "disabled", "task", "duration", "expired"])
def test_normal_cold_owner_retains_exact_row_and_audits_without_expanding_rights(mode):
    store, _entry = seed(mode=mode)
    before = rows(store)
    original = decisions(Engine(repo.Schemes(None).compiled("default").trees, store))
    assert original == [mode in {"permit", "task", "duration"}, False, False, False, False, False]

    # The production startup path must pass its normal migration barrier.
    implementation = PARPermissions()
    _scheme, engine, _instance = implementation.current()
    assert rows(store) == before  # Includes version, expiry, issuer and task binding.
    assert decisions(engine) == original
    after = snapshot(store)
    assert any(row["scheme_id"] == "custom" and row["a_node"] == "RelayMessage:relay"
               for row in after["scheme_entries"])
    assert store.basis()["instructions"]["UpdateUserStatus"] == {"a": "@admin", "r": "@shared"}
    assert store.basis()["instructions"]["UpdateUserCategory"] == {"a": "@admin", "r": "@shared"}
    assert implementation.schemes.compiled("default").unmapped() == []
    history = next(row for row in after["scheme_migration_history"]
                   if row["migration_id"] == migration.MIGRATION)
    retained = [record for record in history["records"] if record.get("disposition") == "retained_unmodified"]
    assert retained == [{"table": "scheme_entries", "row": json.loads(json.dumps(before[0], default=str)),
                         "disposition": "retained_unmodified", "source_message_id": SOURCE_MESSAGE}]
    PARPermissions().current()
    assert snapshot(store) == after  # No duplicate audit or renewed credential on cold retry.


@pytest.mark.parametrize("mode", ["permit", "deny", "disabled", "task", "duration", "expired"])
@pytest.mark.parametrize("scheme_id", ["default", "other-scheme"])
def test_other_updateuser_entries_are_retained_without_new_capabilities(mode, scheme_id):
    entry_id = "entry_another_updateuser"
    store, _entry = seed(mode=mode, scheme_id=scheme_id, entry_id=entry_id)
    before = rows(store, entry_id)
    assert migration.migrate(store)
    assert rows(store, entry_id) == before
    assert not any(row["p_node"] == PRINCIPAL and row["a_node"] in {
        "UpdateUserStatus", "UpdateUserCategory"} for row in snapshot(store)["scheme_entries"])
    assert not migration.migrate(store)
    assert rows(store, entry_id) == before


def test_other_ambiguous_instruction_still_requires_owner_resolution():
    store, _entry = seed(element="UpdateExecutionSettings")
    before = snapshot(store)
    with pytest.raises(repo.SchemeConflict, match="需 owner 明确等价能力"):
        repo.Schemes(Store())
    assert snapshot(store) == before


def test_other_conflict_rolls_back_preservation_audit_and_all_migration_changes():
    store, _entry = seed()
    with store.transaction() as con:
        store.add(con, [Entry("entry_unapproved", "permit", PRINCIPAL, "UpdateExecutionSettings", "@shared")])
    before = snapshot(store)
    with pytest.raises(repo.SchemeConflict, match="entry_unapproved"):
        repo.Schemes(Store())
    assert snapshot(store) == before


@pytest.mark.parametrize("ending", ["revoke", "task_end", "expiry"])
def test_preserved_temporary_credential_keeps_original_end_and_does_not_resurrect(monkeypatch, ending):
    store, entry = seed(mode="task")
    _scheme, engine, _instance = PARPermissions().current()
    assert decisions(engine)[0]
    if ending == "revoke":
        assert temporary.revoke(store, ENTRY_ID, actor=entry.issued_by) == {"revoked": ENTRY_ID}
    elif ending == "task_end":
        with store.transaction() as con:
            assert inline.end_with_input(con, PRINCIPAL, entry.message_id) == 1
    else:
        engine.clock = lambda: entry.expires_at + 1
        assert not decisions(engine)[0]  # Expiry denies even before physical cleanup.
        monkeypatch.setattr(temporary, "time", SimpleNamespace(time=lambda: entry.expires_at + 1))
        assert temporary.expire(store) == 1
    assert rows(store) == []
    assert not decisions(engine)[0]
    after = snapshot(store)
    PARPermissions().current()
    assert rows(store) == []
    assert snapshot(store) == after


def test_missing_legacy_row_is_not_created_and_concurrent_migration_audits_once():
    store, _entry = seed()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: migration.migrate(Store()), range(2)))
    assert sorted(results) == [False, True]
    after = snapshot(store)
    assert len([r for r in after["scheme_migration_history"] if r["migration_id"] == migration.MIGRATION]) == 1
    # Removing an old permission after migration must remain authoritative.
    with store.transaction() as con:
        store.delete(con, where="entry_id=%s", parameters=(ENTRY_ID,))
    assert not migration.migrate(Store())
    assert rows(store) == []


def test_empty_exception_target_does_not_emit_a_fictitious_preservation_record():
    store = Store()
    legacy(store)
    repo.Schemes(Store())
    after = snapshot(store)
    assert rows(store) == []
    assert not any(record.get("disposition") == "retained_unmodified"
                   for row in after["scheme_migration_history"] for record in row["records"])


def test_explicit_rule_document_grants_still_require_owner_resolution():
    rules = document()
    rules["entries"].append({"id": ENTRY_ID, "effect": "permit", "p": PRINCIPAL,
                             "a": "UpdateUser", "r": RESOURCE})
    with pytest.raises(repo.SchemeConflict, match="规则显式授权"):
        migration.rules(rules)
