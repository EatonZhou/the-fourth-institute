from pathlib import Path

import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_portal import resource_identity_backend as repos
from management_portal.resources.registry_store import get_registry_store
from tests.cases.test_resource_identity_backend import _make_repo


def test_repo_scan_records_created_and_delete_is_durable(tmp_path):
    root = tmp_path / 'repo'
    _make_repo(root, 'https://example.invalid/protocol.git')
    record = repos.scan_and_sync_repo_registry([root], apply=True)['created'][0]
    assert record['checkouts'][0]['origin'] == 'created'
    assert record['checkouts'][0]['owner_id'] == record['id']
    repos.unregister_repo(record['id'])
    assert get_registry_store().get('repo', record['id']) is None
    assert root.exists()
    queue = get_directory_cleanup_store()
    assert queue.pending_at(root)
    assert repos.scan_and_sync_repo_registry([root], apply=True)['created_count'] == 0
    queue.run_pending()
    assert not root.exists()


def test_repo_explicit_adoption_survives_scan_and_unregister(tmp_path):
    root = tmp_path / 'adopted'
    _make_repo(root, 'https://example.invalid/adopted.git')
    record = repos.register_repo_checkout(root)
    assert record['checkouts'][0]['origin'] == 'adopted'
    repos.scan_and_sync_repo_registry([root], apply=True)
    assert repos.get_repo_record(record['id'])['checkouts'][0]['origin'] == 'adopted'
    repos.unregister_repo(record['id'])
    assert root.exists()
    assert not get_directory_cleanup_store().pending_at(root)
    assert repos.scan_and_sync_repo_registry([root], apply=True)['created_count'] == 0


def test_pending_checkout_does_not_hide_sibling_from_scan(tmp_path):
    pending, sibling = tmp_path / 'pending', tmp_path / 'sibling'
    _make_repo(pending, 'https://example.invalid/pending.git')
    record = repos.scan_and_sync_repo_registry([pending], apply=True)['created'][0]
    repos.unregister_repo(record['id'])
    _make_repo(sibling, 'https://example.invalid/sibling.git')
    report = repos.scan_and_sync_repo_registry([tmp_path], apply=True)
    assert len(report['created']) == 1
    assert report['created'][0]['canonical_checkout'] == str(sibling)


def test_repo_enqueue_failure_rolls_back_object_transaction(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    _make_repo(root, 'https://example.invalid/rollback.git')
    record = repos.scan_and_sync_repo_registry([root], apply=True)['created'][0]
    queue = get_directory_cleanup_store()
    def fail(*args, **kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(queue, 'enqueue', fail)
    with pytest.raises(RuntimeError, match='queue unavailable'):
        repos.unregister_repo(record['id'])
    assert get_registry_store().get('repo', record['id']) is not None


def test_created_outer_removes_nested_adopted_registration(tmp_path):
    from management_portal.resources.directory_registrations import register_owned, unregister_owned, is_deleted
    outer = tmp_path / 'package'
    nested = outer / 'nested'
    nested.mkdir(parents=True)
    (nested / 'data').write_text('adopted inside created')
    register_owned('knowledge', 'experiment_111111111111', [outer])
    register_owned('knowledge', 'skill_111111111111', [nested], origin='adopted')
    unregister_owned('knowledge', 'experiment_111111111111')
    assert is_deleted('skill_111111111111')
    assert get_registry_store().get('knowledge', 'skill_111111111111') is None
    get_directory_cleanup_store().run_pending()
    assert not outer.exists()


def test_repo_each_checkout_keeps_its_origin(tmp_path):
    first, second = tmp_path / 'adopted', tmp_path / 'created'
    for path in (first, second):
        _make_repo(path, 'https://example.invalid/mixed.git')
    record = repos.register_repo_checkout(first)
    repos.scan_and_sync_repo_registry([second], apply=True)
    record = repos.get_repo_record(record['id'])
    assert {Path(item['path']).name: item['origin'] for item in record['checkouts']} == {'adopted': 'adopted', 'created': 'created'}
    repos.unregister_repo(record['id'])
    get_directory_cleanup_store().run_pending()
    assert first.exists() and not second.exists()


def test_offline_migration_is_conservative_idempotent_and_transactional(tmp_path):
    from scripts.directory_protocol_resource_migration import migrate
    store = get_registry_store()
    old = {'id': 'repo_111111111111', 'checkouts': [{'path': str(tmp_path / 'legacy')}],
           'canonical_checkout': str(tmp_path / 'legacy')}
    with store.writer('repo') as writer:
        writer.put('repo', old['id'], old)
    kwargs = {'registry_schema': store.database.schema,
              'inventory': [{'collection': 'knowledge', 'resource_id': 'skill_111111111111',
                             'paths': [str(tmp_path / 'skill')]}]}
    with pytest.raises(RuntimeError):
        with store.database.transaction() as connection:
            migrate(connection, **kwargs)
            raise RuntimeError('rollback migration')
    assert store.get('repo', old['id']) == old
    with store.database.transaction() as connection:
        changed = migrate(connection, **kwargs)
        assert set(changed) == {old['id'], 'skill_111111111111'}
    assert store.get('repo', old['id'])['checkouts'][0]['origin'] == 'adopted'
    with store.database.transaction() as connection:
        assert migrate(connection, **kwargs) == []


def test_paper_created_delete_and_adopted_survival(tmp_path):
    from management_portal.paper_store import PaperStore
    from management_portal.resources.directory_registrations import register_owned
    store = PaperStore(tmp_path / 'papers')
    paper = store.register({'title': 'Created', 'id': 'paper_111111111111'}, '')
    path = Path(paper['_store_root'])
    result = store.delete_paper(paper['id'])
    assert result['cleanup_ids'] and path.exists()
    assert store.paper_by_id(paper['id']) is None
    assert not store.all_papers()
    get_directory_cleanup_store().run_pending()
    assert not path.exists()
    adopted = tmp_path / 'papers' / 'items' / '2026' / 'adopted'
    register_owned('paper', 'paper_222222222222', [adopted], origin='adopted')
    store.write_manifest({'title': 'Adopted', 'id': 'paper_222222222222'}, adopted)
    assert store.delete_paper('paper_222222222222')['cleanup_ids'] == []
    assert adopted.exists()
    assert not store.all_papers()


@pytest.mark.parametrize('origin', ['created', 'adopted'])
def test_article_uses_roots_and_respects_origin(tmp_path, origin):
    from management_portal import article_registration, article_registry
    path = tmp_path / 'article'
    path.mkdir()
    (path / 'main.md').write_text('# Directory protocol manuscript\n\nText')
    if origin == 'created':
        record = article_registration.scan_and_sync_article_registry([path], apply=True)['created'][0]
    else:
        record = article_registration.register_article(path)
    assert record['roots'][0]['origin'] == origin
    assert record['roots'][0]['owner_id'] == record['id']
    article_registration.unregister_article(record['id'])
    with pytest.raises(KeyError):
        article_registry.get_article_record(record['id'])
    assert article_registration.scan_and_sync_article_registry([path], apply=True)['created_count'] == 0
    get_directory_cleanup_store().run_pending()
    assert path.exists() == (origin == 'adopted')


def test_file_owner_cleanup_failure_does_not_restore_registration(tmp_path, monkeypatch):
    from management_common import directory_cleanup_store as cleanup
    from management_portal.resources.directory_registrations import register_owned, unregister_owned, is_deleted
    root = tmp_path / 'package'
    root.mkdir()
    register_owned('knowledge', 'skill_111111111111', [root])
    unregister_owned('knowledge', 'skill_111111111111')
    def occupied(path):
        raise PermissionError('open file')
    monkeypatch.setattr(cleanup, 'remove_path', occupied)
    outcome, = get_directory_cleanup_store().run_pending()
    assert outcome['status'] == 'retry'
    assert get_registry_store().get('knowledge', 'skill_111111111111') is None
    assert is_deleted('skill_111111111111')
    assert register_owned('knowledge', 'skill_111111111111', [root], scanning=True) is None
    with pytest.raises(cleanup.DirectoryPendingCleanup):
        register_owned('knowledge', 'skill_222222222222', [root], origin='adopted')


def test_legacy_inventory_reports_bytes_without_deleting_or_following_links(tmp_path):
    from scripts.directory_protocol_resource_migration import inventory_legacy_cleanup_paths
    root = tmp_path / 'papers'
    legacy = root / '_trash'
    legacy.mkdir(parents=True)
    (legacy / 'data').write_bytes(b'abc')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep').write_bytes(b'never read or delete')
    (legacy / 'link').symlink_to(outside, target_is_directory=True)
    report, = inventory_legacy_cleanup_paths(paper_roots=[root])
    assert report['bytes'] == 3 + (legacy / 'link').lstat().st_size
    assert report['entries'] == 2 and not report['errors']
    assert (legacy / 'data').exists() and (outside / 'keep').exists()


@pytest.mark.parametrize('kind', ['skill', 'paper'])
def test_retired_owner_is_not_preserved_as_a_scan_parse_failure(tmp_path, monkeypatch, kind):
    from management_portal.resources.directory_registrations import register_owned, unregister_owned
    from management_portal.resource_index_knowledge import SkillPackageAdapter
    from management_portal.paper_resource_index import PaperManifestAdapter
    root = tmp_path / kind
    root.mkdir()
    resource_id = kind + '_111111111111'
    collection = 'paper' if kind == 'paper' else 'knowledge'
    register_owned(collection, resource_id, [root], origin='adopted')
    unregister_owned(collection, resource_id)
    adapter = PaperManifestAdapter() if kind == 'paper' else SkillPackageAdapter()
    monkeypatch.setattr(adapter, 'owns_path', lambda path: True)
    assert not adapter.protects_missing_source({'resource_id': resource_id}, root)


def test_paper_provider_reports_cleanup_receipt(monkeypatch):
    from unittest.mock import Mock
    from management_portal import paper_resources, paper_store
    from management_portal.resources.providers import paper
    service = Mock()
    paper.install_mutation_provider(service)
    handler = service.register_mutation_provider.call_args.kwargs['unregister']
    monkeypatch.setattr(paper_store, '_paper_by_id', Mock(side_effect=[{'id': 'paper_111111111111'}, None]))
    monkeypatch.setattr(paper_resources, 'delete_paper', lambda *a: {
        'store': {'cleanup_ids': ['cleanup_test']}, 'projection_cleanup_errors': [],
    })
    assert handler('paper_111111111111')['cleanup_ids'] == ['cleanup_test']
