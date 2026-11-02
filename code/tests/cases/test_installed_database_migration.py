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
    assert controls.read(facade, ACTOR)['excluded_member_ids'] == [member]
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
