"""Three-Plan permission retirement through the real isolated PostgreSQL owner."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.scheme import repository as repo
from management_portal.permission_system.scheme.compiler import rules_digest


RETIRED = {
    "PublishRelease", "InstallProviderRuntime", "SetColabSessionRunning", "GetResearchMapDigest",
    "CheckArticleRestore", "RestoreArticle", "GetKnowledgeTags", "UpdateKnowledgeTags",
}


def legacy(store):
    rules = json.loads((repo.BUILTIN_DIRECTORY / "default.json").read_text())
    rules["scheme_id"] = "custom"
    rules["title"] = "GetKnowledgeTags is historical prose"
    rules["instructions"].pop("UpdatePostMarks", None)
    for name in RETIRED:
        rules["instructions"][name] = {"a": "@admin", "r": "@shared"}
    rules["instructions"]["RestoreArticle:source_address"] = {"a": "@viewer"}
    for name in ("SetPostPinned", "SetPostFeatured"):
        rules["instructions"][name] = {"a": "@admin", "r": "@shared"}
    scope = store.scoped("custom")
    with store.transaction() as con:
        store.execute(con, "INSERT INTO {s}.schemes(scheme_id,document,version,created_by,updated_by)"
                      " VALUES ('custom',%s::jsonb,5,'alice','alice')", (json.dumps(rules),))
        store.execute(con, "INSERT INTO {s}.scheme_sources(scheme_id,source_scheme_id,rules,digest)"
                      " VALUES ('copy','custom',%s::jsonb,%s)", (json.dumps(rules), repo.source_digest(rules)))
        scope.set_instance(con, rules_digest(rules), rules)
        for name in (*RETIRED, "RestoreArticle:source_address", "SetPostPinned", "SetPostFeatured"):
            scope.place(con, "A", name, ["@actions", "@admin"])
        scope.add(con, [
            Entry("pin", "permit", "alice", "SetPostPinned", "@shared"),
            Entry("feature", "permit", "alice", "SetPostFeatured", "@shared"),
            Entry("deny-pin", "deny", "bob", "SetPostPinned", "@shared"),
            Entry("deny-feature", "deny", "bob", "SetPostFeatured", "@shared"),
            Entry("retired-loan", "permit", "alice", "RestoreArticle:source_address", "Article",
                  issued_by="bob", issued_at=time.time(), expires_at=time.time()+300, message_id="input-1"),
            Entry("unrelated", "permit", "carol", "@viewer", "@shared"),
        ])
        store.record_user_authorization(con, resident_id="resident_aaaaaaaaaaaa", message_id="original",
            a="RestoreArticle:source_address", resource_id="Article", authorization_id="authorization-1",
            credential_id="retired-loan", issuer="bob", source_digest="proof", evidence="original authorization",
            source_resident_id="resident_aaaaaaaaaaaa", bound_input_message_id="input-1", execution_id="run-1",
            lifetime_seconds=None)
        # This fixture is already at the previous release's normal-form names.
        store.execute(con, "INSERT INTO {s}.scheme_migrations(migration_id) VALUES (%s)", (repo.ISA_NAME_MIGRATION,))
    return rules


def snapshot(store):
    with store.read() as con:
        return {name: sorted([dict(r) for r in store.execute(con, f"SELECT * FROM {{s}}.{name}").fetchall()], key=repr)
                for name in ("schemes", "scheme_instances", "scheme_sources", "scheme_nodes", "scheme_entries",
                             "user_authorizations", "scheme_migrations", "state")}


def test_owner_retires_old_actions_merges_equal_marks_and_keeps_history():
    store = Store()
    old = legacy(store)
    before = snapshot(store)
    owner = repo.Schemes(store)
    current = owner.get("custom")
    assert current["version"] == 6
    assert current["rules"]["title"] == old["title"]
    assert current["rules"]["instructions"]["UpdatePostMarks"] == {"a": "@admin", "r": "@shared"}
    assert not ({k.split(":")[0] for k in current["rules"]["instructions"]} & RETIRED)
    assert owner.compiled("custom").unmapped() == []
    after = snapshot(store)
    assert after["user_authorizations"] == before["user_authorizations"]
    entries = {r["entry_id"]: r for r in after["scheme_entries"]}
    assert set(entries) == {"pin", "deny-pin", "unrelated"}
    assert entries["pin"]["a_node"] == entries["deny-pin"]["a_node"] == "UpdatePostMarks"
    assert entries["deny-pin"]["effect"] == "deny" and entries["pin"]["version"] == 2
    assert store.scoped("custom").basis() == current["rules"]
    assert store.scoped("custom").instance()["rules_digest"] == rules_digest(current["rules"])
    assert not owner.source("copy")["outdated"]
    with store.read() as con:
        archive = store.execute(con, "SELECT records FROM {s}.scheme_migration_history WHERE migration_id=%s",
                                (repo.THREE_PLAN_MIGRATION,)).fetchone()["records"]
    assert any(row["table"] == "scheme_entries" and row["row"]["entry_id"] == "retired-loan" for row in archive)
    repo.Schemes(Store())
    assert snapshot(store) == after


@pytest.mark.parametrize("collision", ["classification", "single_entry", "target", "credential"])
def test_non_equivalent_merge_is_closed_and_atomic(collision):
    store = Store()
    rules = legacy(store)
    with store.transaction() as con:
        if collision == "classification":
            rules["instructions"]["SetPostFeatured"]["a"] = "@viewer"
            store.execute(con, "UPDATE {s}.schemes SET document=%s::jsonb", (json.dumps(rules),))
        elif collision == "single_entry":
            store.execute(con, "DELETE FROM {s}.scheme_entries WHERE entry_id='feature'")
        elif collision == "target":
            store.scoped("custom").place(con, "A", "UpdatePostMarks", ["@actions", "@viewer"])
        else:
            store.scoped("custom").add(con, [Entry("single-loan", "permit", "carol", "SetPostPinned", "@shared",
                issued_by="alice", issued_at=time.time(), expires_at=time.time()+300, message_id="input-2")])
    before = snapshot(store)
    with pytest.raises(repo.SchemeConflict):
        repo.Schemes(store)
    assert snapshot(store) == before


def test_migration_failure_rolls_back_then_concurrent_owners_apply_once(monkeypatch):
    store = Store()
    legacy(store)
    before = snapshot(store)
    with monkeypatch.context() as patch:
        patch.setattr(store, "bump", lambda con: (_ for _ in ()).throw(RuntimeError("rollback")))
        with pytest.raises(RuntimeError, match="rollback"):
            repo.Schemes(store)
    assert snapshot(store) == before
    barrier = threading.Barrier(3)
    def run(_):
        barrier.wait(timeout=10)
        return repo.Schemes(Store()).get("custom")["version"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(run, range(3))) == [6, 6, 6]
    assert len(snapshot(store)["scheme_migrations"]) == 2


def test_stale_basis_stays_stale_and_offline_has_no_migration():
    store = Store()
    legacy(store)
    with store.transaction() as con:
        store.execute(con, "UPDATE {s}.scheme_instances SET rules_digest='stale'")
    repo.Schemes(store)
    assert store.scoped("custom").instance()["rules_digest"] == "stale"
    assert repo.Schemes(None).compiled("default").unmapped() == []


def test_domain_role_checks_use_current_actor_deny_and_revocation(monkeypatch):
    from management_portal import permission_system
    from management_portal.permission_system.par import PARPermissions

    store = Store()
    owner = PARPermissions(store)
    monkeypatch.setattr(permission_system, "active", lambda: owner)
    monkeypatch.setattr(permission_system, "_off", lambda: False)
    with store.transaction() as con:
        store.add(con, [Entry("shared-admin", "permit", "alice", "@admin", "@shared"),
                        Entry("shared-reader", "permit", "bob", "@viewer", "@shared"),
                        Entry("resident-admin", "permit", "resident_aaaaaaaaaaaa", "@admin", "@shared")])
        store.bump(con)
    assert permission_system.allows_roles("alice", [("@admin", "@shared"), ("@viewer", "@shared")])
    assert permission_system.allows_roles("bob", [("@viewer", "@shared")])
    assert not permission_system.allows_roles("bob", [("@admin", "@shared")])
    assert not permission_system.allows_roles("alice", [("@missing_role", "@shared")])
    assert not permission_system.allows_roles("", [("@viewer", "@shared")])
    assert not permission_system.allows_roles("alice", [])
    with store.transaction() as con:
        store.add(con, [Entry("explicit-deny", "deny", "alice", "@admin", "@shared")])
        store.delete(con, where="entry_id='shared-reader'")
        store.bump(con)
    assert not permission_system.allows_roles("alice", [("@admin", "@shared")])
    assert not permission_system.allows_roles("bob", [("@viewer", "@shared")])


def test_compound_resource_type_cas_reaches_provider(monkeypatch):
    from types import SimpleNamespace
    from management_portal.resources import isa

    seen = []
    resource_id = "knowledge_tags_7d4e9a2c6f10"
    def update(identity, changes, *, actor, **kwargs):
        seen.append((identity, changes, actor))
        return {"resource_id": identity, "resource_type": "knowledge_tags", "version": "new-version"}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(update=update))
    arguments = {"resource_id": resource_id, "changes": {"content": "[]"}}
    inv = SimpleNamespace(argument=arguments.get, expected_version_for=lambda identity: "old-version", caller="alice")
    result = isa.update_resource(inv)
    assert seen == [(resource_id, {"content": "[]", "expected_version": "old-version"}, "alice")]
    assert result["version"] == "new-version"


def test_shipped_seed_basis_uses_normal_owner_migrations():
    path = Path(__file__).resolve().parents[3] / "instance/example_instance/seed/database/distribution.sql"
    with path.open() as stream:
        statement = next(line for line in stream if line.startswith('INSERT INTO "permission"."scheme_instances"'))
    store = Store()
    with store.transaction() as con:
        con.execute(statement.replace('INSERT INTO "permission".', f'INSERT INTO "{store.schema}".', 1))
    generated = store.instance()["generated_at"]
    repo.Schemes(store)
    rules = store.basis()
    assert not ({k.split(":")[0] for k in rules["instructions"]} & (RETIRED | {"SetPostPinned", "SetPostFeatured"}))
    assert rules["instructions"]["UpdatePostMarks"] == {"a": "@admin", "r": "@shared"}
    assert store.instance()["rules_digest"] == rules_digest(rules)
    assert store.instance()["generated_at"] == generated
    after = snapshot(store)
    repo.Schemes(Store())
    assert snapshot(store) == after
