"""Installed v65 state must upgrade atomically without losing business records.

:module: resource.service
:importance: P1
"""
import pytest

from management_common.portal_database import portal_database, _database_schema
from scripts.migrate_installed_database import convert, preservation_facts
from tests.cases.test_family_chat_preferences import family_chat, lineage, charters, future_creation_store  # noqa: F401
from tests.cases.test_workspace_resource_rename import workspace  # noqa: F401


def test_installed_v65_preview_commit_and_reentry_preserve_records(family_chat, workspace, monkeypatch):
    from management_portal.society_service.state_store import get_lineage_data_store, LineageDataStore
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.resources.config import resource_database_schema
    from management_portal.society_service import family_chat_preferences as controls, family_facade
    from tests.cases.isa_test_support import ACTOR
    from scripts import migrate_deleted_identities
    facade, member = family_chat
    controls.change(facade['resident_id'], ACTOR, member, 'exclude', True)
    message = {'message_id': 'legacy_migration_request', 'author_id': ACTOR}
    routing = family_facade.routing_snapshot(facade, message)
    registry = get_registry_store()
    registry._ensure()
    index = portal_database(schema=resource_database_schema())
    with index.transaction() as c:
        c.execute('CREATE TABLE IF NOT EXISTS resource_index_records(record_json TEXT)')
    database = get_lineage_data_store().database
    with database.transaction() as c:
        c.execute(f'ALTER TABLE "{registry.database.schema}".deleted_identities RENAME TO retired_identities')
        c.execute(f'ALTER INDEX "{registry.database.schema}".deleted_identities_pkey RENAME TO retired_identities_pkey')
        c.execute('ALTER TABLE family_chat_preferences DROP COLUMN muted_member_ids')
        for table in ('family_chat_preferences', 'family_chat_inputs'):
            c.execute(f'ALTER TABLE {table} RENAME COLUMN excluded_member_ids TO muted_member_ids')
        for table in ('lineages', 'residents'):
            c.execute(f'ALTER TABLE {table} DROP COLUMN directory_json')
        c.execute(f'ALTER TABLE {workspace.store.table} DROP COLUMN origin, DROP COLUMN owner_id, DROP COLUMN operation_id')
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v65' WHERE key='schema'")
        before = preservation_facts(c)
    # The framework deliberately holds pools on its disposable server. Service
    # exclusivity is checked by the CLI; this case exercises the owner transaction.
    monkeypatch.setattr(migrate_deleted_identities, 'require_database_alone', lambda _c: None)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with database.transaction() as c:
            preview = convert(c)
            assert preview['to_schema'] == 'tfi.lineage-data.v66'
            assert preservation_facts(c) == before
            raise Rollback()
    with database.read() as c:
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0] == 'tfi.lineage-data.v65'
        assert c.execute('SELECT to_regclass(%s)', (f'"{registry.database.schema}".retired_identities',)).fetchone()[0]
        assert preservation_facts(c) == before
    with database.transaction() as c:
        applied = convert(c)
        assert applied['preservation'] == before
        LineageDataStore._initialize(c)
    assert controls.read(facade, ACTOR)['excluded_member_ids'] == [member]
    assert family_facade.routing_snapshot(facade, message) == routing
    with database.transaction() as c:
        assert convert(c)['family']['changed'] is False
        assert preservation_facts(c) == before


def test_unrecognized_schema_refuses_before_changes(future_creation_store, workspace):
    from management_portal.society_service.state_store import get_lineage_data_store
    database = get_lineage_data_store().database
    with pytest.raises(ValueError, match='Expected an installed'):
        with database.transaction() as c:
            c.execute("UPDATE lineage_meta SET value='unknown' WHERE key='schema'")
            convert(c)
    with database.read() as c:
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0] == 'tfi.lineage-data.v66'
