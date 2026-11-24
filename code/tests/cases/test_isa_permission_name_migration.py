"""Persisted permission identity migration, on the isolated PostgreSQL owner lifecycle."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from psycopg.errors import UniqueViolation

from management_portal.hatg.instruction_renames import INSTRUCTION_RENAMES
from management_portal.permission_system.engine import Engine
from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.scheme import repository
from management_portal.permission_system.scheme.compiler import compile_scheme, declared_decisions, rules_digest


def rename(value, mapping=INSTRUCTION_RENAMES):
    name, sep, tail = value.partition(":")
    return mapping.get(name, name) + sep + tail


def legacy_rules(scheme_id="custom"):
    document = json.loads((repository.BUILTIN_DIRECTORY / "default.json").read_text())
    inverse = {new: old for old, new in INSTRUCTION_RENAMES.items()}
    document["instructions"] = {rename(key, inverse): value for key, value in document["instructions"].items()}
    document["scheme_id"] = scheme_id
    document["title"] = "PreviewArticleRestore is literal prose"
    # A custom classification must survive, rather than becoming @unmapped
    # or silently taking the built-in level during release regeneration.
    document["instructions"]["PreviewArticleRestore:article_id"] = {"a": "@admin"}
    return document


def seed(store):
    document = legacy_rules()
    inverse = {new: old for old, new in INSTRUCTION_RENAMES.items()}
    declarations = {inverse.get(key, key): value for key, value in declared_decisions().items()}
    compiled = compile_scheme(document, instructions=declarations)
    now = time.time()
    entries = [
        Entry("manual-permit", "permit", "alice", "PreviewArticleRestore:article_id", "Report"),
        Entry("manual-deny", "deny", "bob", "PreviewArticleRestore:article_id", "Report"),
        Entry("bob-base", "permit", "bob", "@admin", "Report"),
        Entry("disabled", "permit", "carol", "PinPost", "Report", disabled=True),
        Entry("loan", "permit", "resident_aaaaaaaaaaaa", "PinPost", "Report", version=4,
              issued_by="alice", issued_at=now, expires_at=now + 3600, message_id="input-1"),
    ]
    with store.transaction() as connection:
        store.execute(connection, "INSERT INTO {s}.schemes(scheme_id,document,version,created_by,updated_by)"
                      " VALUES ('custom',%s::jsonb,7,'alice','alice')", (json.dumps(document),))
        store.execute(connection, "INSERT INTO {s}.scheme_sources(scheme_id,source_scheme_id,rules,digest)"
                      " VALUES ('copy','custom',%s::jsonb,%s)", (json.dumps(document), repository.source_digest(document)))
        custom = store.scoped("custom")
        custom.set_instance(connection, rules_digest(document), document)
        for tree in compiled.trees.values():
            for node in tree.nodes.values():
                if len(node.path) > 1:
                    custom.place(connection, tree.name, node.id, node.path[:-1])
        custom.add(connection, entries)
        store.record_user_authorization(connection, resident_id="resident_aaaaaaaaaaaa", message_id="message-1",
            a="PinPost", resource_id="Report", authorization_id="authorization-1", credential_id="loan",
            issuer="alice", source_digest="original-message-digest", evidence="PinPost was authorized",
            source_resident_id="resident_aaaaaaaaaaaa", bound_input_message_id="input-1",
            execution_id="execution-1", lifetime_seconds=None)
    return document, compiled


def snapshot(store):
    with store.read() as connection:
        return {table: sorted([dict(row) for row in store.execute(connection, f"SELECT * FROM {{s}}.{table}").fetchall()],
                              key=repr)
                for table in ("schemes", "scheme_instances", "scheme_sources", "scheme_nodes", "scheme_entries",
                              "user_authorizations", "scheme_migrations", "state")}


def test_owner_load_migrates_all_rules_and_preserves_decisions():
    store = Store()
    document, old = seed(store)
    before = snapshot(store)
    requests = [("PreviewArticleRestore:article_id", "Report"), ("PinPost", "Report")]
    principals = ["alice", "bob", "carol", "resident_aaaaaaaaaaaa", "nobody"]
    engine = Engine(old.trees, store.scoped("custom"))
    decisions = {p: [d.allowed for d in engine.decide(p, requests)] for p in principals}
    schemes = repository.Schemes(store)  # production owner entry, before any compiled saved-rule filtering
    current = schemes.compiled("custom")
    assert current.unmapped() == []
    found = schemes.get("custom")
    assert found["version"] == 8 and found["rules"]["title"] == document["title"]
    assert found["rules"]["instructions"] == {rename(k): v for k, v in document["instructions"].items()}
    assert {key.split(":")[0] for key in found["rules"]["instructions"]}.isdisjoint(INSTRUCTION_RENAMES)
    assert set(INSTRUCTION_RENAMES.values()) <= {key.split(":")[0] for key in found["rules"]["instructions"]}
    current_engine = Engine(current.trees, store.scoped("custom"))
    assert {p: [d.allowed for d in current_engine.decide(p, [(rename(a), r) for a, r in requests])]
            for p in principals} == decisions
    assert decisions["alice"][0] and not decisions["bob"][0] and decisions["resident_aaaaaaaaaaaa"][1]
    after = snapshot(store)
    assert [{**r, "a_node": rename(r["a_node"])} for r in before["scheme_entries"]] == after["scheme_entries"]
    assert [{**r, "a_node": rename(r["a_node"])} for r in before["user_authorizations"]] == after["user_authorizations"]
    for row in after["scheme_nodes"]:
        if row["tree"] == "A":
            assert all(rename(part) == part for part in [row["id"], row["parent"], *row["path"]])
    source = schemes.source("copy")
    assert not source["outdated"] and source["digest"] == repository.source_digest(found["rules"])
    assert store.scoped("custom").basis() == found["rules"]
    assert store.scoped("custom").instance()["rules_digest"] == current.rules_digest
    assert not repository.migrate_instruction_names(Store())
    repository.Schemes(Store())
    assert snapshot(store) == after  # includes every timestamp/version and the one migration receipt


def test_old_seed_instance_uses_owner_migration_without_rewriting_export():
    # Consume the actual shipped SQL row, only into the isolated test schema.
    path = Path(__file__).resolve().parents[3] / "instance/example_instance/seed/database/distribution.sql"
    statement = next(line for line in path.open() if line.startswith('INSERT INTO "permission"."scheme_instances"'))
    store = Store()
    with store.transaction() as connection:
        connection.execute(statement.replace('INSERT INTO "permission".', f'INSERT INTO "{store.schema}".', 1))
    before = store.basis()
    generated = store.instance()["generated_at"]
    repository.Schemes(store)
    after = store.basis()
    assert after["instructions"] == {rename(k): v for k, v in before["instructions"].items()}
    assert store.instance()["rules_digest"] == rules_digest(after)
    assert store.instance()["generated_at"] == generated
    assert not repository.migrate_instruction_names(Store())


def test_migration_failure_rolls_back_and_retry_completes(monkeypatch):
    store = Store()
    seed(store)
    before = snapshot(store)
    with monkeypatch.context() as patch:
        patch.setattr(store, "bump", lambda connection: (_ for _ in ()).throw(RuntimeError("injected rollback")))
        with pytest.raises(RuntimeError, match="injected rollback"):
            repository.Schemes(store)
    assert snapshot(store) == before
    repository.Schemes(store)
    assert len(snapshot(store)["scheme_migrations"]) == 1


def test_concurrent_owner_load_commits_once():
    store = Store()
    seed(store)
    before = snapshot(store)["state"][0]["version"]
    barrier = threading.Barrier(4)
    def open_owner(_):
        barrier.wait(timeout=10)
        return repository.Schemes(Store()).get("custom")["version"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(open_owner, range(4))) == [8] * 4
    after = snapshot(store)
    assert len(after["scheme_migrations"]) == 1 and after["state"][0]["version"] == before + 1


@pytest.mark.parametrize("collision", ["rules", "node", "entry", "receipt"])
def test_ambiguous_existing_target_aborts_entire_migration(collision):
    store = Store()
    document, _ = seed(store)
    with store.transaction() as connection:
        if collision == "rules":
            document["instructions"]["SetPostPinned"] = {"a": "@viewer"}
            store.execute(connection, "UPDATE {s}.schemes SET document=%s::jsonb", (json.dumps(document),))
        elif collision == "node":
            store.scoped("custom").place(connection, "A", "SetPostPinned", ["@actions", "@viewer"])
        elif collision == "entry":
            store.scoped("custom").add(connection, [Entry("conflict", "permit", "alice", "CheckArticleRestore:article_id", "Report")])
        else:
            store.execute(connection, "INSERT INTO {s}.user_authorizations SELECT resident_id,message_id,'SetPostPinned',"
                          " r_node,authorization_id,scheme_id,credential_id,issuer,source_digest,evidence,source_resident_id,"
                          " bound_input_message_id,execution_id,lifetime_seconds,accepted_at FROM {s}.user_authorizations")
    before = snapshot(store)
    expected = repository.SchemeConflict if collision == "rules" else UniqueViolation
    with pytest.raises(expected):
        repository.Schemes(store)
    assert snapshot(store) == before


def test_stale_basis_digest_and_source_difference_stay_stale():
    store = Store()
    document, _ = seed(store)
    with store.transaction() as connection:
        store.execute(connection, "UPDATE {s}.scheme_instances SET rules_digest='old-stale-digest'")
        document["instructions"]["PreviewArticleRestore:article_id"]["a"] = "@viewer"
        store.execute(connection, "UPDATE {s}.scheme_sources SET rules=%s::jsonb,digest=%s",
                      (json.dumps(document), repository.source_digest(document)))
    schemes = repository.Schemes(store)
    assert store.scoped("custom").instance()["rules_digest"] == "old-stale-digest"
    assert schemes.source("copy")["outdated"]


def test_builtin_and_owned_handlers_use_only_current_names():
    from management_portal.hatg.catalog.permission import DECLARATIONS as permissions
    from management_portal.hatg.catalog.resource import DECLARATIONS as resources
    from management_portal.resources import article_restore_preview
    from management_portal.permission_system.management import ACTIONS

    for document in repository.builtin_documents().values():
        assert compile_scheme(document).unmapped() == []
    names = {item.name for item in (*permissions, *resources)}
    assert {"CreateTemporaryCredential", "UpdateTemporaryCredential", "CheckArticleRestore"} <= names
    assert names.isdisjoint(INSTRUCTION_RENAMES)
    assert {"CreateTemporaryCredential", "UpdateTemporaryCredential"} <= ACTIONS.keys()
    assert ACTIONS.keys().isdisjoint(INSTRUCTION_RENAMES)
    assert article_restore_preview.ACTION == "CheckArticleRestore"
