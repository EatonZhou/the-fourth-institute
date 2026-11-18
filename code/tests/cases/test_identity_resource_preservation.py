"""Identity deletion releases scratch data, retaining independent resources."""
from pathlib import Path

import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_retention import RetentionStore
from management_portal.resources import directory_registrations as directories
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

ARTICLE = 'article_111111111111'
RESIDENT = 'resident_222222222222'
WORKSPACE = 'workspace_333333333333'


@pytest.mark.parametrize('preserve', [True, False])
def test_identity_preserves_article_and_project_cascade_still_deletes(tmp_path, preserve):
    root = tmp_path / 'resident'
    content = root / 'workspace' / 'article'
    content.mkdir(parents=True)
    (content / 'main.md').write_text('saved manuscript')
    scratch = content.parent / 'scratch'
    scratch.write_text('temporary')
    external = tmp_path / 'variant'
    external.mkdir()
    (external / 'main.md').write_text('variant')
    store = get_registry_store()
    with store.writer('article') as writer:
        writer.put('article', ARTICLE, {'id': ARTICLE, 'created_by': RESIDENT,
                   'project_ids': ['example'], 'canonical_root': str(content),
                   'roots': [{'path': str(content)}, {'path': str(external)}]})
    workspaces = get_workspace_store()
    workspaces.ensure(WORKSPACE, str(content.parent), owner_id=RESIDENT)
    retention = RetentionStore()
    retention.declare(root, category='object_data', owner_id=RESIDENT)
    with store.database.transaction() as connection:
        result = directories.unregister_tree([root], connection=connection, actor='test',
                  owner_ids=[RESIDENT], preserve_resources=preserve)
        directories.unregister_identity(RESIDENT, connection=connection, actor='test',
                                       preserved_paths=result['preserved_paths'])
        for path in result['cleanup_roots']:
            retention.enqueue_cleanup(path, owner_id=RESIDENT, operation_id='delete-fixture',
                                      actor='test', connection=connection)
    get_directory_cleanup_store().run_pending()
    assert not workspaces.rows([WORKSPACE])
    assert not scratch.exists()
    if preserve:
        assert store.get('article', ARTICLE)['created_by'] == RESIDENT
        assert not directories.is_deleted(ARTICLE)
        assert (content / 'main.md').read_text() == 'saved manuscript'
        assert (external / 'main.md').read_text() == 'variant'
        assert not get_directory_cleanup_store().pending_at(content)
    else:
        assert store.get('article', ARTICLE) is None
        assert directories.is_deleted(ARTICLE)
        assert not content.exists() and not external.exists()


def test_preserving_cleanup_refuses_redirected_ancestor(tmp_path):
    from management_common.directory_paths import deletion_roots
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'identity'
    root.mkdir()
    (root / 'redirect').symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        deletion_roots(root, [root / 'redirect' / 'article'])
    assert outside.is_dir()


def test_preserving_cleanup_handles_missing_root_and_equal_resource_root(tmp_path):
    from management_common.directory_paths import deletion_roots
    root = tmp_path / 'identity'
    assert deletion_roots(root, [root / 'article']) == []
    root.mkdir()
    (root / 'main.md').write_text('keep')
    assert deletion_roots(root, [root]) == []
    assert (root / 'main.md').read_text() == 'keep'


def test_preservation_rolls_back_and_keeps_other_users_acl(permissions, tmp_path):
    from management_portal import permission_system
    reader = 'resident_444444444444'
    lineage = 'lineage_555555555555'
    permissions.place(Fact('lineage', lineage),
                      Fact('resident', RESIDENT, {'lineage': lineage}),
                      Fact('resident', reader, {'lineage': lineage}),
                      Fact('article', ARTICLE, creator=RESIDENT))
    permissions.grant(reader, ARTICLE, '@contributor')
    root = tmp_path / 'author'
    content = root / 'article'
    content.mkdir(parents=True)
    (content / 'main.md').write_text('keep')
    store = get_registry_store()
    with store.writer('article') as writer:
        writer.put('article', ARTICLE, {'id': ARTICLE, 'created_by': RESIDENT,
                   'roots': [{'path': str(content)}]})
    before = store.get('article', ARTICLE)
    with pytest.raises(RuntimeError, match='rollback'):
        with store.database.transaction() as connection:
            result = directories.unregister_tree([root], connection=connection, actor='test',
                      owner_ids=[RESIDENT], preserve_resources=True)
            directories.unregister_identity(RESIDENT, connection=connection,
                                           preserved_paths=result['preserved_paths'])
            raise RuntimeError('rollback')
    assert not directories.is_deleted(RESIDENT)
    assert not get_directory_cleanup_store().status(owner_id=RESIDENT)
    with store.database.transaction() as connection:
        result = directories.unregister_tree([root], connection=connection, actor='test',
                  owner_ids=[RESIDENT], preserve_resources=True)
        directories.unregister_identity(RESIDENT, connection=connection,
                                       preserved_paths=result['preserved_paths'])
    assert directories.is_deleted(RESIDENT)
    assert store.get('article', ARTICLE) == before
    assert permissions.holds(reader, '@contributor', ARTICLE)
    # This isolated registry test does not delete Society's authenticated
    # principal. Verify its permission-tree identity and creator grants are
    # removed; admission of a deleted Resident belongs to Society's tests.
    with permissions.store.transaction() as connection:
        assert not permissions.store.paths('P', [RESIDENT], connection)
        assert not permissions.store.entries(connection, where='p_node = %s', parameters=(RESIDENT,))
    assert (content / 'main.md').read_text() == 'keep'


def test_registered_file_owner_and_workspace_inside_it_survive(tmp_path):
    root = tmp_path / 'author'
    package = root / 'knowledge'
    package.mkdir(parents=True)
    document = package / 'entry.md'
    document.write_text('knowledge')
    attached = package / 'attachments'
    attached.mkdir()
    directories.register_owned('knowledge', 'decision_111111111111', [document, attached])
    workspace = get_workspace_store()
    workspace.ensure(WORKSPACE, str(attached), owner_id=WORKSPACE)
    store = get_registry_store()
    with store.database.transaction() as connection:
        result = directories.unregister_tree([root], connection=connection, actor='test',
                  owner_ids=[RESIDENT], preserve_resources=True)
    assert workspace.rows([WORKSPACE])
    assert store.get('knowledge', 'decision_111111111111')
    assert all(not document.is_relative_to(path) and not attached.is_relative_to(path)
               for path in result['cleanup_roots'])
