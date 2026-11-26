"""Mailbox Todo resources preserve ownership, ordering and versioned writes.

:module: resources.types
:design: decision_c54e68a82d7a
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_common.portal_database import portal_database
from management_portal import mailbox_todos as todos, permission_system
from management_portal.resources.errors import ResourceVersionConflict

ACTOR = "resident_123456789abc"
OLD = "0123456789abcdef0123456789abcdef"
ID = "mailbox_todo_" + hashlib.sha256(("mailbox_todo:" + OLD).encode()).hexdigest()[:12]


@pytest.fixture
def legacy():
    db = portal_database(schema="portal_ui")
    with db.transaction() as conn:
        # The isolated seed may already contain the current empty owner table.
        conn.execute("DROP TABLE IF EXISTS mailbox_todos")
        conn.execute("""CREATE TABLE mailbox_todos (
            todo_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL,
            date TEXT NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE,
            position BIGINT NOT NULL, created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            indent INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("INSERT INTO mailbox_todos VALUES (%s,'alice','保留原文','2026-11-26',false,7,%s,'2026-11-01Z','2026-11-02Z',2)", (OLD, ACTOR))
    todos._database.cache_clear()
    return db


def test_legacy_rows_migrate_once_without_content_or_order_changes(legacy):
    with todos.database().read() as conn:
        row = dict(conn.execute("SELECT * FROM mailbox_todos").fetchone())
    assert row["todo_id"] == ID
    assert row["legacy_todo_id"] == OLD
    assert row["version"] == 1
    assert (row["text"], row["position"], row["indent"], row["created_by"]) == ("保留原文", 7, 2, ACTOR)
    todos._database.cache_clear()
    with todos.database().read() as conn:
        assert dict(conn.execute("SELECT * FROM mailbox_todos").fetchone()) == row


@pytest.fixture
def active_users(monkeypatch):
    # User registration is a separate owner; these tests exercise Todo storage
    # and authorization with an explicit, mutable role seam.
    monkeypatch.setattr(todos, "_require_active_user", lambda user: None)
    roles = set()
    monkeypatch.setattr(permission_system, "allows_roles",
                        lambda actor, requirements: (actor, tuple(requirements)) in roles)
    return roles


def new(actor="alice", user="alice", text="item", **kwargs):
    return todos.create(actor, text, user_id=user, date="2026-11-26", **kwargs)


def test_migration_collision_rolls_back_every_row_and_ddl(legacy):
    with legacy.transaction() as conn:
        conn.execute("INSERT INTO mailbox_todos SELECT %s,user_id,text,date,completed,position,created_by,created_at,updated_at,indent FROM mailbox_todos", (ID,))
    with pytest.raises(ValueError, match="collision"):
        todos.database().ensure()
    with legacy.read() as conn:
        assert {row[0] for row in conn.execute("SELECT todo_id FROM mailbox_todos")} == {OLD, ID}
        assert "version" not in dict(conn.execute("SELECT * FROM mailbox_todos LIMIT 1").fetchone())


def test_old_id_is_not_an_alias(legacy):
    todos.database().ensure()
    with pytest.raises(KeyError):
        todos.get("alice", OLD)
    assert todos.get("alice", ID)["text"] == "保留原文"


def test_fields_cas_idempotency_and_same_version_race(active_users):
    first = new(resource_id="mailbox_todo_aaaaaaaaaaaa")
    assert new(resource_id=first["resource_id"]) == first
    with pytest.raises(ValueError, match="another request"):
        new(text="changed", resource_id=first["resource_id"])
    def write(text):
        try:
            return todos.update("alice", first["resource_id"], {"text": text, "completed": True, "indent": 3}, expected_version=1)
        except ResourceVersionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["one", "two"]))
    assert sum(item is not None for item in results) == 1
    item = todos.get("alice", first["resource_id"])
    assert item["version"] == 2 and item["completed"] and item["indent"] == 3
    for bad in (None, True, 0, "2"):
        with pytest.raises(ValueError, match="expected_version"):
            todos.update("alice", first["resource_id"], {"text": "no"}, expected_version=bad)
    assert todos.get("alice", first["resource_id"]) == item


def test_owner_privacy_resident_creation_revocation_and_restrictions(active_users):
    item = new(ACTOR, "alice")
    other = new("bob", "bob")
    for identifier in (item["resource_id"], "mailbox_todo_000000000000"):
        with pytest.raises(KeyError):
            todos.get("bob", identifier)
    with pytest.raises(PermissionError):
        todos.search_resources("alice", user_id="bob")
    with pytest.raises(PermissionError):
        todos.get(ACTOR, item["resource_id"])
    active_users.add((ACTOR, (("@admin", "alice"),)))
    assert todos.get(ACTOR, item["resource_id"])["created_by"] == ACTOR
    modified = todos.update(ACTOR, item["resource_id"], {"completed": True}, expected_version=1)
    with pytest.raises(PermissionError):
        todos.delete(ACTOR, item["resource_id"])
    with pytest.raises(PermissionError):
        todos.update(ACTOR, item["resource_id"], {"order": {"direction": "up"}}, expected_version=modified["version"])
    active_users.clear()
    with pytest.raises(PermissionError):
        todos.update(ACTOR, item["resource_id"], {"completed": False}, expected_version=modified["version"])
    assert todos.get("bob", other["resource_id"]) == other


def test_atomic_reorder_updates_displaced_versions_and_rejects_foreign_anchor(active_users):
    items = [new(text=letter) for letter in ("a", "b", "c")]
    ids = [item["resource_id"] for item in items]
    changed = todos.update("alice", ids[2], {"order": {"before_id": ids[0]}}, expected_version=1)
    ordered = todos.search_resources("alice", user_id="alice")
    assert [item["resource_id"] for item in ordered["items"]] == [ids[2], ids[0], ids[1]]
    assert changed["version"] == 2
    assert todos.get("alice", ids[0])["version"] > 1
    with pytest.raises(ResourceVersionConflict):
        todos.update("alice", ids[0], {"completed": True}, expected_version=1)
    foreign = new("bob", "bob")
    before = todos.all_items()
    with pytest.raises(KeyError):
        todos.update("alice", ids[2], {"order": {"after_id": foreign["resource_id"]}}, expected_version=2)
    assert todos.all_items() == before
    with pytest.raises(ValueError, match="不得"):
        todos.update("alice", ids[2], {"text": "x", "order": {"direction": "down"}}, expected_version=2)


def test_search_has_owner_counts_order_and_pages(active_users):
    for word in ("first", "second", "third"):
        new(text=word)
    new("bob", "bob", "secret")
    items = todos.search_resources("alice", user_id="alice", limit=2)
    assert items["total"] == items["pending_count"] == 3
    assert [entry["attributes"]["text"] for entry in items["items"]] == ["first", "second"]
    tail = todos.search_resources("alice", user_id="alice", cursor=items["next_cursor"])
    assert tail["items"][0]["attributes"]["text"] == "third"
    assert tail["next_cursor"] is None
    assert todos.search_resources("alice", user_id="alice", q="secret")["total"] == 0


def test_provider_rechecks_read_and_exact_mutation_fields(active_users):
    from management_portal.resources import ResourceService
    from management_portal.resources.providers import mailbox_todo
    service = ResourceService()
    mailbox_todo.install_resolver(service)
    mailbox_todo.install_mutation_provider(service)
    item = new()
    data, mime, resolved = service.read_content(item["resource_id"], actor="alice")
    assert json.loads(data) == item and mime == "application/json"
    assert resolved["record"]["version"] == 1
    provider = service._mutation_providers.provider_for("mailbox_todo")
    with pytest.raises(ValueError):
        provider.register({"user_id": "alice", "text": "x", "created_by": "bob"}, actor="alice", resource_id="mailbox_todo_bbbbbbbbbbbb")
    with pytest.raises(ValueError):
        provider.update(item["resource_id"], {"text": "x"}, actor="alice")
    result = provider.update(item["resource_id"], {"text": "changed", "expected_version": 1}, actor="alice")
    assert result["version"] == 2
    with pytest.raises(KeyError):
        service.read_content(item["resource_id"], actor="bob")
    provider.unregister(item["resource_id"], actor="alice")
    with pytest.raises(KeyError):
        todos.get("alice", item["resource_id"])


def test_active_recipient_required_before_creation(monkeypatch):
    from management_portal import portal_access_control
    from types import SimpleNamespace
    monkeypatch.setattr(portal_access_control, "get_portal_access_store",
                        lambda: SimpleNamespace(user=lambda username: {"configured": True, "status": "disabled"}))
    with pytest.raises(ValueError, match="停用"):
        new(ACTOR, "alice")
    assert todos.all_items() == []


def test_lock_rechecks_resident_role_before_commit(active_users, monkeypatch):
    item = new()
    calls = []
    def allowed(actor, requirements):
        calls.append((actor, requirements))
        return len(calls) == 1
    monkeypatch.setattr(permission_system, "allows_roles", allowed)
    with pytest.raises(PermissionError):
        todos.update(ACTOR, item["resource_id"], {"text": "no"}, expected_version=1)
    assert len(calls) == 2
    assert todos.get("alice", item["resource_id"]) == item


def test_cross_date_reorder_and_boundary_noop(active_users):
    first = new()
    second = todos.create("alice", "yesterday", user_id="alice", date="2026-11-25")
    noop = todos.update("alice", first["resource_id"], {"order": {"direction": "up"}}, expected_version=1)
    assert noop == first
    updated = todos.update("alice", second["resource_id"], {"order": {"after_id": first["resource_id"]}}, expected_version=1)
    assert updated["date"] == first["date"] and updated["version"] == 2
    assert [row["resource_id"] for row in todos.search_resources("alice", user_id="alice")["items"]] == [first["resource_id"],second["resource_id"]]


def test_order_transaction_rolls_back_if_later_update_fails(active_users):
    items = [new(text=letter) for letter in ("a", "b", "c")]
    before = todos.all_items()
    with todos.database().transaction() as conn:
        conn.execute("""CREATE FUNCTION reject_todo_order() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.text='b' THEN RAISE EXCEPTION 'injected ordering failure'; END IF; RETURN NEW; END $$""")
        conn.execute("CREATE TRIGGER test_order_failure BEFORE UPDATE ON mailbox_todos FOR EACH ROW EXECUTE FUNCTION reject_todo_order()")
    with pytest.raises(Exception, match="injected ordering failure"):
        todos.update("alice", items[2]["resource_id"], {"order": {"before_id": items[0]["resource_id"]}}, expected_version=1)
    assert todos.all_items() == before


def test_real_par_user_admin_grant_and_revoke(monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal.permission_system.facts import Fact
    permissions = enable(monkeypatch)
    permissions.users("alice", "bob")
    permissions.place(Fact("resident", ACTOR))
    item = new(ACTOR, "alice")
    with pytest.raises(PermissionError):
        todos.get(ACTOR, item["resource_id"])
    entry = permissions.grant(ACTOR, "alice", a="@admin")
    assert todos.get(ACTOR, item["resource_id"]) == item
    updated = todos.update(ACTOR, item["resource_id"], {"completed": True}, expected_version=1)
    with permissions.store.transaction() as connection:
        permissions.store.delete(connection, where="entry_id=%s", parameters=(entry.entry_id,))
    with pytest.raises(PermissionError):
        todos.get(ACTOR, item["resource_id"])
    with pytest.raises(PermissionError):
        todos.update(ACTOR, item["resource_id"], {"completed": False}, expected_version=2)
    assert todos.get("alice", item["resource_id"]) == updated


def test_migration_preserves_order_when_legacy_positions_tie(legacy):
    second = "1" * 32
    with legacy.transaction() as conn:
        conn.execute("INSERT INTO mailbox_todos SELECT %s,user_id,'second',date,completed,position,created_by,created_at,updated_at,indent FROM mailbox_todos", (second,))
    result = todos.search_resources("alice", user_id="alice")
    assert [entry["attributes"]["text"] for entry in result["items"]] == ["保留原文", "second"]
    assert {entry["attributes"]["position"] for entry in result["items"]} == {7}


def test_real_resource_service_create_update_read_delete_and_private_query(active_users):
    from management_portal.resources.runtime import create_local_resource_service
    service = create_local_resource_service()
    # Resident may create a User's Todo without obtaining read permission.
    result = service.register("mailbox_todo", {"user_id": "alice", "text": "new"},
                              actor=ACTOR, operation_id="test-todo-create")
    identifier = result["resource_id"]
    assert result["attributes"]["created_by"] == ACTOR
    with pytest.raises(PermissionError):
        service.read_content(identifier, actor=ACTOR)
    with pytest.raises(KeyError):
        service.get(identifier, actor="bob")
    replay = service.register("mailbox_todo", {"user_id": "alice", "text": "new"},
                             actor=ACTOR, operation_id="test-todo-create")
    assert replay == result
    changed = service.update(identifier, {"completed": True, "expected_version": 1}, actor="alice")
    assert changed["version"] == 2
    assert service.get(identifier, actor="alice")["attributes"]["completed"]
    page = service.query(resource_types=["mailbox_todo"], user_id="alice", actor="alice", limit=1)
    assert page["total"] == 1 and page["pending_count"] == 0
    assert page["resources"][0]["attributes"]["version"] == 2
    for options in (
        {"resource_types": ["mailbox_todo"]},
        {"resource_types": ["mailbox_todo", "project"], "user_id": "alice"},
        {"resource_types": ["project"], "user_id": "alice"},
        {"resource_types": ["mailbox_todo"], "user_id": "alice", "projects": ["Demo"]},
    ):
        with pytest.raises(ValueError):
            service.query(actor="alice", **options)
    with pytest.raises(PermissionError):
        service.query(resource_types=["mailbox_todo"], user_id="alice", actor="bob")
    assert service.query(q=identifier, actor="bob")["total"] == 0
    indexed = service._index().store.internal_get(identifier)
    assert indexed["attributes"]["completed"]
    service.unregister(identifier, actor="alice")
    with pytest.raises(KeyError):
        service.get(identifier, actor="alice")


def test_generic_search_access_filter_runs_before_paging_and_counts(active_users):
    from management_portal.resources import ResourceService
    from management_portal.resources.providers.mailbox_todo import install_resolver
    service = ResourceService()
    install_resolver(service)
    first, second = new(text="hidden"), new(text="visible")
    class Access:
        def readable_ids(self, ids, *, actor):
            return set(ids) - {first["resource_id"]}
    service._access = Access()
    result = service.query(actor="alice", resource_types=["mailbox_todo"], user_id="alice", limit=1)
    assert result["total"] == result["pending_count"] == 1
    assert result["resources"][0]["resource_id"] == second["resource_id"]
    assert result["next_cursor"] is None


def test_isa_generic_payload_paging_and_envelope_cas(active_users, monkeypatch):
    from types import SimpleNamespace
    from management_portal.resources import isa
    from management_portal.resources.runtime import create_local_resource_service
    service = create_local_resource_service()
    monkeypatch.setattr(isa, "_service", lambda: service)
    values = {"resource_type": "mailbox_todo", "fields": {"user_id": "alice", "text": "first"}}
    invocation = SimpleNamespace(caller=ACTOR, argument=values.get, idempotency_key="todo-generic-create")
    created = isa.register_resource(invocation)
    identifier = created["resource_id"]
    assert created["resource"]["attributes"]["created_by"] == ACTOR
    new(text="second")
    values = {"resource_type": "mailbox_todo", "user_id": "alice", "limit": 1}
    invocation = SimpleNamespace(caller="alice", argument=values.get)
    first = isa.search_resources(invocation)
    assert first["total"] == first["pending_count"] == 2
    assert first["next_cursor"] == "1"
    assert "version" in first["items"][0]["attributes"]
    values["cursor"] = first["next_cursor"]
    second = isa.search_resources(invocation)
    assert second["next_cursor"] is None
    assert second["items"][0]["resource_id"] != first["items"][0]["resource_id"]
    values = {"resource_id": identifier, "changes": {"completed": True}}
    invocation = SimpleNamespace(caller="alice", argument=values.get, expected_version_for=lambda _: 1)
    updated = isa.update_resource(invocation)
    assert updated["version"] == "2"  # Existing generic ISA receipt encodes the token as text.
    assert updated["resource"]["version"] == 2
    assert updated["resource"]["attributes"]["completed"]


def test_disabled_type_and_unscoped_queries_cannot_expose_todos(active_users, monkeypatch):
    from types import SimpleNamespace
    from management_portal.resources import ResourceService
    service = ResourceService()
    item = new(text="private snippet")
    seen = []
    def query(**kwargs):
        seen.append(kwargs)
        assert "mailbox_todo" not in kwargs["resource_types"]
        return {"resources": [], "total": 0}
    monkeypatch.setattr(service, "_index", lambda: SimpleNamespace(query=query))
    assert service.query(actor="bob", q="private snippet")["total"] == 0
    assert seen
    types = service.resource_types
    monkeypatch.setattr(type(types), "enabled_types", lambda _: {"project"})
    result = service.query(actor="alice", resource_types=["mailbox_todo"], user_id="alice")
    assert result == {"resources": [], "total": 0, "pending_count": 0, "next_cursor": None}
    assert service.query(actor="bob", q=item["resource_id"])["total"] == 0


def test_todo_discovery_imports_respect_core_feature_boundary():
    import ast
    from tests.cases import test_core_layers as layers
    subjects = {"management_portal.resources." + name for name in
                ("service_index", "providers.mailbox_todo", "address", "service", "service_providers")}
    shared = "management_portal.resources.service"
    # Other named methods in service.py have a separate writer. Check the
    # approved registration method here; integration checks the whole module.
    tree = layers._modules()[shared][1]
    registration = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef) and node.name == "register_resolver")
    upward = [(source, target, line) for source, targets in layers._imports().items() if source in subjects
              for target, line in targets.items()
              if source != shared or registration.lineno <= line <= registration.end_lineno
              if layers._tier(target) > layers._tier(source)
              and (layers._short(source), layers._short(target)) not in layers.ALLOWED]
    assert not upward, upward


@pytest.mark.parametrize("configuration", ["absent", "without-query", "duplicate"])
def test_scoped_query_provider_absent_or_ambiguous_never_falls_back(active_users, monkeypatch, configuration):
    from management_portal.resources import ResourceService
    from management_portal.resources.errors import ResourceServiceUnavailable
    from management_portal.resources.providers.mailbox_todo import install_resolver
    service = ResourceService()
    def forbid_index():
        pytest.fail("A private scoped query must not fall back to the index")
    monkeypatch.setattr(service, "_index", forbid_index)
    if configuration == "absent":
        with pytest.raises(ValueError, match="no address resolver"):
            service.query(actor="alice", resource_types=["mailbox_todo"], user_id="alice")
    elif configuration == "without-query":
        service.register_resolver("mailbox_todo", lambda *_args, **_kwargs: None)
        with pytest.raises(ResourceServiceUnavailable):
            service.query(actor="alice", resource_types=["mailbox_todo"], user_id="alice")
    else:
        install_resolver(service)
        original = service._resolvers.resolver_for("mailbox_todo")
        with pytest.raises(ValueError, match="already registered"):
            install_resolver(service)
        assert service._resolvers.resolver_for("mailbox_todo") is original
        service.query(actor="alice", resource_types=["mailbox_todo"], user_id="alice")


def test_provider_discovery_rechecks_caller_and_returns_fresh_records(active_users):
    from management_portal.resources.runtime import create_local_resource_service
    service = create_local_resource_service()
    item = new()
    identifier = item["resource_id"]
    # No index adapter is needed: both discovery paths read the owner.
    role = (ACTOR, (("@admin", "alice"),))
    active_users.add(role)
    assert service.get(identifier, actor=ACTOR)["version"] == 1
    assert service.query(actor=ACTOR, resource_types=["mailbox_todo"], user_id="alice")["total"] == 1
    todos.update("alice", identifier, {"text": "fresh"}, expected_version=1)
    assert service.get(identifier, actor=ACTOR)["attributes"]["text"] == "fresh"
    active_users.remove(role)
    with pytest.raises(PermissionError):
        service.get(identifier, actor=ACTOR)
    with pytest.raises(PermissionError):
        service.query(actor=ACTOR, resource_types=["mailbox_todo"], user_id="alice")
    with pytest.raises(KeyError):
        service.get(identifier, actor="bob")
    todos.delete("alice", identifier)
    with pytest.raises(KeyError):
        service.get(identifier, actor="alice")
