"""Installed feature facts for permission previews never initialize their owners.

:module: security.identity-access
:importance: P1
"""
import pytest

from management_common.domain_database import DomainDatabase
from management_common.postgres_database import PostgresDatabase
from management_portal import chatroom_backend, chatroom_store, home_report_store
from management_portal import notice_board_backend, notice_store, research_map_store
from management_portal.resources import deliverables, registry_store


READERS = {
    "direction": research_map_store.permission_records,
    "notice": notice_board_backend.resource_records,
    "chatroom": chatroom_backend.resource_records,
    "deliverable": deliverables.permission_records,
    "report": home_report_store.permission_records,
}


@pytest.fixture
def installed_facts():
    with research_map_store.database().transaction() as c:
        c.execute("INSERT INTO directions VALUES (%s,%s,%s,false)",
                  ("direction_0123456789ab", "Alpha", "alice"))
    with notice_store.database().transaction() as c:
        c.execute("INSERT INTO posts(post_id,author,content) VALUES (%s,%s,%s)",
                  ("notice_0123456789ab", "alice", "private text"))
    with chatroom_store.database().transaction() as c:
        c.execute("INSERT INTO rooms(chatroom_id,created_by,last_message) VALUES (%s,%s,%s)",
                  ("chatroom_0123456789ab", "alice", '{"text":"private text"}'))
        c.execute("INSERT INTO members VALUES (%s,%s)", ("chatroom_0123456789ab", "bob"))
    store = registry_store.get_registry_store()
    with store.writer("deliverable", "report") as writer:
        writer.put("deliverable", "deliverable_0123456789ab",
                   {"resource_id": "deliverable_0123456789ab", "created_by": "alice"})
        writer.put("report", "report_0123456789ab",
                   {"resource_id": "report_0123456789ab", "created_by": "alice", "revisions": ["private text"]})
    return store


def forbid_bootstrap(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("permission preview entered a write/bootstrap/cache lifecycle")

    monkeypatch.setattr(DomainDatabase, "ensure", forbidden)
    monkeypatch.setattr(PostgresDatabase, "_connection_pool", forbidden)
    monkeypatch.setattr(PostgresDatabase, "_ensure_schema", forbidden)
    monkeypatch.setattr(registry_store.RegistryStore, "_ensure", forbidden)


@pytest.mark.parametrize("kind", READERS)
def test_feature_facts_cold_read_has_no_bootstrap(kind, installed_facts, monkeypatch):
    reader = READERS[kind]
    # Capture current owner semantics before making all normal initialization
    # paths unavailable. Chatrooms intentionally read only permission metadata.
    if kind == "chatroom":
        expected = [{"resource_id": "chatroom_0123456789ab", "resource_type": "chatroom",
                     "created_by": "alice", "members": ["bob"]}]
    else:
        expected = reader()
    research_map_store._database.cache_clear()
    notice_store._database.cache_clear()
    chatroom_store._database.cache_clear()
    registry_store.reset_registry_store_for_tests()
    forbid_bootstrap(monkeypatch)
    assert reader(read_only=True) == expected


def test_feature_facts_use_current_committed_authorship(installed_facts, monkeypatch):
    assert research_map_store.permission_records(read_only=True)[0]["created_by"] == "alice"
    with research_map_store.database().transaction() as c:
        c.execute("UPDATE directions SET created_by='bob'")
    with chatroom_store.database().transaction() as c:
        c.execute("DELETE FROM members")
    forbid_bootstrap(monkeypatch)
    assert research_map_store.permission_records(read_only=True)[0]["created_by"] == "bob"
    assert chatroom_backend.resource_records(read_only=True)[0]["members"] == []


@pytest.mark.parametrize("kind", ["direction", "notice", "chatroom"])
def test_feature_missing_owner_tables_fail_without_installing(kind, monkeypatch):
    import psycopg
    from management_common.portal_database import portal_database

    db = portal_database(schema="missing_feature_owner")
    # Existing schema, deliberately missing feature tables. Preview must fail
    # visibly, not manufacture an empty installed feature.
    with db.connect():
        pass
    owner = {"direction": research_map_store, "notice": notice_store, "chatroom": chatroom_store}[kind]
    monkeypatch.setattr(owner, "database", lambda: type("Owner", (), {"database": db})())
    forbid_bootstrap(monkeypatch)
    with pytest.raises(psycopg.errors.UndefinedTable):
        READERS[kind](read_only=True)
    with db.readonly_snapshot() as c:
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()").fetchone()[0] == 0
