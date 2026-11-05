"""Installed v65 state must upgrade atomically without losing business records.

:module: resource.service
:importance: P1
"""
import os
import uuid

import pytest

from management_common.portal_database import portal_database, _database_schema
from scripts.migrate_installed_database import convert, preservation_facts
from tests.cases.test_family_chat_preferences import family_chat, lineage, charters, future_creation_store  # noqa: F401
from tests.cases.test_workspace_resource_rename import workspace  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_storage_namespace(isolate_portal_databases, monkeypatch, tmp_path):
    # Installed-upgrade probes deliberately commit historical table shapes.
    # A failed preview must not leave those shapes in the next case's schema.
    monkeypatch.setenv('PORTAL_DATABASE_NAMESPACE',
                       os.environ['PORTAL_DATABASE_NAMESPACE'][:24] + '_' + uuid.uuid4().hex[:12])
    # The session's default accounts belong to the previous namespace. Register
    # the same explicit synthetic site in this case's fresh schema as well.
    from tests.cases.account_registration_test_support import register_site_defaults
    register_site_defaults(monkeypatch, tmp_path / 'migration-accounts')


def test_preservation_digest_ignores_retired_columns_and_accepts_their_absence(workspace):
    from scripts.migrate_directory_protocol import migrate_workspaces
    database = workspace.store.database
    with database.transaction() as connection:
        connection.execute(f"ALTER TABLE {workspace.store.table} ADD COLUMN origin TEXT DEFAULT 'adopted'")
        before = preservation_facts(connection)
        migrate_workspaces(connection, workspace_schema=database.schema, lineage_schema=_database_schema('lineage'))
        assert preservation_facts(connection) == before
        row = connection.execute(f'SELECT * FROM {workspace.store.table} LIMIT 1').fetchone()
        assert 'origin' not in row


def test_installed_v65_preview_commit_and_reentry_preserve_records(family_chat, workspace, monkeypatch):
    from management_portal.society_service.state_store import get_lineage_data_store, LineageDataStore
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.resources.config import resource_database_schema
    from management_portal.society_service import family_chat_preferences as controls, family_facade
    from tests.cases.isa_test_support import ACTOR
    from scripts import migrate_deleted_identities
    facade, member = family_chat
    # Build the historical stored policy directly: Exclude is no longer a
    # public control, but its durable value must survive an installed upgrade.
    with get_lineage_data_store().database.transaction() as c:
        c.execute('INSERT INTO family_chat_preferences(facade_id,actor,excluded_member_ids) VALUES (%s,%s,%s)',
                  (facade['resident_id'], ACTOR, [member]))
    message = {'message_id': 'legacy_migration_request', 'author_id': ACTOR}
    routing = family_facade.routing_snapshot(facade, message)
    registry = get_registry_store()
    registry._ensure()
    from management_common.instance_manifest import current_instance
    from scripts import installed_article_migration as articles
    instance = current_instance()
    article_id, project_id = 'article_123456789abc', '旧项目'
    source = instance.root / 'data/share/Articles' / article_id
    source.mkdir(parents=True)
    (source / 'main.tex').write_bytes(b'old manuscript\n')
    (source / '空目录').mkdir()
    project_root = instance.area('projects') / project_id
    project_root.mkdir(parents=True)
    target = project_root / 'articles' / article_id
    with registry.writer('project_registry', 'article') as writer:
        projects = writer.get('project_registry', 'projects') or {'projects': []}
        projects['projects'].append({'id': project_id, 'path': '/Projects/' + project_id})
        writer.put('project_registry', 'projects', projects)
        writer.put('article', article_id, {'id': article_id, 'project_ids': [project_id],
            'roots': [{'path': str(source), 'present': True}], 'canonical_root': str(source),
            'primary_file': 'main.tex', 'description': str(source)})
    index = portal_database(schema=resource_database_schema())
    with index.transaction() as c:
        c.execute('CREATE TABLE IF NOT EXISTS resource_index_records(record_json TEXT)')
    database = get_lineage_data_store().database
    with database.transaction() as c:
        # Registry format labels are descriptive; only the structures used by
        # this controlled migration establish its compatibility. Unrelated
        # installed features may own additional tables in the same schema.
        c.execute(f"UPDATE \"{registry.database.schema}\".registry_meta SET value='fixture.descriptive' WHERE key='schema'")
        c.execute(f'CREATE TABLE "{registry.database.schema}".fixture_unrelated (id TEXT PRIMARY KEY)')
        c.execute(f'ALTER TABLE "{registry.database.schema}".deleted_identities RENAME TO retired_identities')
        c.execute(f'ALTER INDEX "{registry.database.schema}".deleted_identities_pkey RENAME TO retired_identities_pkey')
        c.execute('ALTER TABLE family_chat_preferences DROP COLUMN muted_member_ids')
        for table in ('family_chat_preferences', 'family_chat_inputs'):
            c.execute(f'ALTER TABLE {table} RENAME COLUMN excluded_member_ids TO muted_member_ids')
        for table in ('lineages', 'residents'):
            c.execute(f'ALTER TABLE {table} DROP COLUMN directory_json')
        c.execute(f'ALTER TABLE {workspace.store.table} DROP COLUMN IF EXISTS origin, DROP COLUMN owner_id, DROP COLUMN operation_id')
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v65' WHERE key='schema'")
        before = preservation_facts(c)
    # The framework deliberately holds pools on its disposable server. Service
    # exclusivity is checked by the CLI; this case exercises the owner transaction.
    monkeypatch.setattr(migrate_deleted_identities, 'require_database_alone', lambda _c: None)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with database.transaction() as c:
            preview = convert(c, relocate_legacy_articles=True)
            assert preview['to_schema'] == 'tfi.lineage-data.v66'
            assert preservation_facts(c) == before
            assert not target.exists()
            raise Rollback()
    with database.read() as c:
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0] == 'tfi.lineage-data.v65'
        assert c.execute('SELECT to_regclass(%s)', (f'"{registry.database.schema}".retired_identities',)).fetchone()[0]
        assert preservation_facts(c) == before
    assert registry.get('article', article_id)['canonical_root'] == str(source)
    proposal = preview['legacy_articles']
    with pytest.raises(ValueError, match='missing'):
        articles.verify_targets(proposal)
    articles.prepare_copies(proposal)
    assert articles.tree_manifest(source) == articles.tree_manifest(target)
    # A source edit after preview is not silently accepted at database apply.
    (source / 'main.tex').write_bytes(b'changed after preview')
    with pytest.raises(ValueError, match='differs from source'):
        with database.transaction() as c:
            convert(c, relocate_legacy_articles=True, expected_article_plan=proposal, require_article_targets=True)
    (source / 'main.tex').write_bytes(b'old manuscript\n')
    with database.transaction() as c:
        applied = convert(c, relocate_legacy_articles=True, expected_article_plan=proposal, require_article_targets=True)
        assert applied['preservation'] == before
        LineageDataStore._initialize(c)
    assert controls.read(facade, ACTOR)['muted_member_ids'] == [member]
    with database.read() as c:
        assert c.execute('SELECT excluded_member_ids FROM family_chat_preferences WHERE facade_id=%s AND actor=%s',
                         (facade['resident_id'], ACTOR)).fetchone()[0] == [member]
        assert c.execute(f'SELECT value FROM "{registry.database.schema}".registry_meta WHERE key=%s',
                         ('schema',)).fetchone()[0] == 'fixture.descriptive'
        assert c.execute('SELECT to_regclass(%s)',
                         (f'"{registry.database.schema}".fixture_unrelated',)).fetchone()[0]
    assert family_facade.routing_snapshot(facade, message) == routing
    record = registry.get('article', article_id)
    assert record['canonical_root'] == str(target)
    assert record['roots'][0]['path'] == record['canonical_root']
    assert record['description'] == str(source)
    assert (source / 'main.tex').read_bytes() == (target / 'main.tex').read_bytes()
    with database.transaction() as c:
        row = c.execute(f'SELECT document_json FROM "{registry.database.schema}".registry_documents '
                        "WHERE collection='article' AND document_id=%s", (article_id,)).fetchone()
        assert articles.stored_json(row[0])['canonical_root'] == 'projects:' + project_id + '/articles/' + article_id
        reentry = convert(c, relocate_legacy_articles=True)
        assert reentry['family']['changed'] is False
        assert reentry['legacy_articles']['articles'] == []
        assert preservation_facts(c) == before
    # Reconcile an already committed Windows metadata backfill that expanded
    # project references. This must neither repeat Article copies nor touch IDs.
    with database.transaction() as c:
        row = c.execute(f'SELECT document_json FROM "{registry.database.schema}".registry_documents '
                        "WHERE collection='project_registry' AND document_id='projects'").fetchone()
        document = articles.stored_json(row[0])
        project = next(p for p in document['projects'] if p['id'] == project_id)
        project['path'] = str(project_root)
        import json
        c.execute(f'UPDATE "{registry.database.schema}".registry_documents SET document_json=%s '
                  "WHERE collection='project_registry' AND document_id='projects'", (json.dumps(document),))
    with database.transaction() as c:
        repaired = convert(c)
        assert repaired['from_schema'] == 'tfi.lineage-data.v66'
        assert repaired['family']['changed'] is False
        assert repaired['preservation'] == before
        row = c.execute(f'SELECT document_json FROM "{registry.database.schema}".registry_documents '
                        "WHERE collection='project_registry' AND document_id='projects'").fetchone()
        assert next(p for p in articles.stored_json(row[0])['projects'] if p['id'] == project_id)['path'] == 'projects:' + project_id


@pytest.mark.parametrize('damage', ['missing_document_field', 'missing_identity_index'])
def test_installed_conversion_rolls_back_required_registry_damage(lineage, workspace, monkeypatch, damage):
    """Actual migration dependencies fail locally and never leave half-renamed identities."""
    from psycopg.errors import UndefinedColumn, UndefinedTable
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.society_service.state_store import get_lineage_data_store
    from scripts import migrate_deleted_identities

    registry = get_registry_store()
    registry._ensure()
    schema = registry.database.schema
    database = get_lineage_data_store().database
    with database.transaction() as c:
        c.execute(f'ALTER TABLE "{schema}".deleted_identities RENAME TO retired_identities')
        c.execute(f'ALTER INDEX "{schema}".deleted_identities_pkey RENAME TO retired_identities_pkey')
        if damage == 'missing_document_field':
            c.execute(f'ALTER TABLE "{schema}".registry_documents RENAME COLUMN document_json TO saved_json')
        else:
            c.execute(f'ALTER TABLE "{schema}".retired_identities DROP CONSTRAINT retired_identities_pkey')
    monkeypatch.setattr(migrate_deleted_identities, 'require_database_alone', lambda _c: None)
    error = UndefinedColumn if damage == 'missing_document_field' else UndefinedTable
    with pytest.raises(error):
        with database.transaction() as c:
            convert(c)
    with database.read() as c:
        assert c.execute('SELECT to_regclass(%s)', (f'"{schema}".retired_identities',)).fetchone()[0]
        assert c.execute('SELECT to_regclass(%s)', (f'"{schema}".deleted_identities',)).fetchone()[0] is None
        if damage == 'missing_document_field':
            assert c.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s '
                             "AND table_name='registry_documents' AND column_name='document_json'", (schema,)).fetchone() is None


def test_unrecognized_schema_refuses_before_changes(future_creation_store, workspace):
    from management_portal.society_service.state_store import get_lineage_data_store
    database = get_lineage_data_store().database
    with pytest.raises(ValueError, match='Expected an installed'):
        with database.transaction() as c:
            c.execute("UPDATE lineage_meta SET value='unknown' WHERE key='schema'")
            convert(c)
    with database.read() as c:
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0] == 'tfi.lineage-data.v66'


def test_article_copy_refuses_conflict_and_links(tmp_path):
    from scripts.installed_article_migration import tree_manifest, prepare_copies
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'main.tex').write_bytes(b'original')
    proposal = {'articles': [{'resource_id': 'article_123456789abc', 'source': str(source),
        'target': str(target), 'manifest': tree_manifest(source)}]}
    target.mkdir()
    (target / 'main.tex').write_bytes(b'different')
    with pytest.raises(ValueError, match='no longer matches'):
        prepare_copies(proposal)
    assert (target / 'main.tex').read_bytes() == b'different'
    assert (source / 'main.tex').read_bytes() == b'original'
    (source / 'escape').symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='links or reparse'):
        tree_manifest(source)


def test_metadata_backfill_keeps_area_references_without_posix_encoder(future_creation_store, monkeypatch):
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_common import json_columns
    from scripts.directory_protocol_lifecycle_migration import migrate, validate_project_locations
    from scripts.installed_article_migration import stored_json
    registry = get_registry_store()
    with registry.writer('project_registry') as writer:
        writer.put('project_registry', 'projects', {'projects': [{'id': '中文', 'path': 'projects:中文'}]})
    # Model the Windows serialization boundary: it does not encode C:/... paths.
    # An offline metadata change must not need runtime expansion/re-encoding.
    monkeypatch.setattr(json_columns, 'stored_record', lambda value: value)
    with get_lineage_data_store().database.transaction() as c:
        migrate(c)
        validate_project_locations(c)
        row = c.execute(f'SELECT document_json FROM "{registry.database.schema}".registry_documents '
                        "WHERE collection='project_registry' AND document_id='projects'").fetchone()
        assert stored_json(row[0])['projects'] == [{'id': '中文', 'path': 'projects:中文',
            'directory': {'owner_id': '中文'}}]
