"""Society/User callers delete identities, not the durable content they authored.

Registry and retention owners remain real; no live instance is used.
"""
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_retention import RetentionStore
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources.registry_store import get_registry_store
from management_portal.society_service import resident_catalog, resident_workspace_deletion
from management_portal.society_service import resident_record_domain
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload, _delete_member,
)

ARTICLE = 'article_123456789abc'


def manuscript(root, creator):
    root.mkdir(parents=True)
    path = root / 'main.tex'
    path.write_text('Durable project content', encoding='utf-8')
    document = {'id': ARTICLE, 'title': 'Project manuscript', 'created_by': creator,
                'project_ids': ['project_test'],
                'roots': [{'path': str(root), 'owner_id': ARTICLE}]}
    registry = get_registry_store()
    with registry.writer('article') as writer:
        writer.put('article', ARTICLE, document)
    RetentionStore().declare(root, category='object_data', owner_id=ARTICLE)
    return path, registry.get('article', ARTICLE)


def assert_preserved(path, before):
    assert get_registry_store().get('article', ARTICLE) == before
    assert path.read_text(encoding='utf-8') == 'Durable project content'
    assert not get_directory_cleanup_store().status(owner_id=ARTICLE)


def test_society_member_delete_preserves_project_manuscript(future_creation_store, tmp_path):
    resident = resident_domain.register_resident(_new_session_payload('Author'), 'sample_admin')
    rid = resident['resident_id']
    path, before = manuscript(tmp_path / 'project-content' / ARTICLE, rid)
    _delete_member(rid)
    assert resident_catalog.get_catalog().get_historical(rid)['deleted_at']
    # Run the real cleanup worker to test bytes, not only a surviving registry row.
    get_directory_cleanup_store().run_pending()
    assert_preserved(path, before)


def test_user_identity_delete_releases_private_artifacts_not_authored_content(tmp_path):
    access = PortalAccessStore()
    access.upsert_user('content_author', actor='sample_admin')
    before_user = access.user('content_author')
    private = tmp_path / 'private-runtime'
    private.mkdir()
    (private / 'cache').write_text('private')
    RetentionStore().declare(private, category='object_data', owner_id='content_author')
    path, before = manuscript(tmp_path / 'project-content' / ARTICLE, 'content_author')
    deleted = access.delete_user('content_author', actor='sample_admin')
    assert deleted['status'] == 'deleted'
    assert deleted['session_version'] > before_user['session_version']
    assert not access.is_active('content_author')
    get_directory_cleanup_store().run_pending()
    assert not private.exists()
    assert_preserved(path, before)


def test_explicit_project_tree_cleanup_still_unregisters_its_manuscript(tmp_path):
    # Counterexample at the shared directory cleanup boundary. This does not
    # pretend to be a full DeleteProject or deletion of the manuscript author.
    project = tmp_path / 'project-content'
    path, _ = manuscript(project / ARTICLE, 'content_author')
    registry = get_registry_store()
    with registry.database.transaction() as connection:
        resident_workspace_deletion.remove_deleted_folder(
            project, connection=connection, owner_id='project_test', actor='sample_admin')
    assert registry.get('article', ARTICLE) is None
    get_directory_cleanup_store().run_pending()
    assert not path.exists()


def test_member_delete_preserves_nested_multiroot_article_and_releases_private_files(future_creation_store, tmp_path):
    resident = resident_domain.register_resident(_new_session_payload('Nested author'), 'sample_admin')
    rid = resident['resident_id']
    folder = resident_record_domain.resident_folder_path(resident)
    private = folder / 'private-cache'
    private.write_text('private runtime')
    path, before = manuscript(folder / 'workspace' / ARTICLE, rid)
    other = tmp_path / 'second-root'
    other.mkdir()
    (other / 'evidence').write_text('second root')
    before['roots'].append({'path': str(other), 'owner_id': ARTICLE})
    registry = get_registry_store()
    with registry.writer('article') as writer:
        writer.put('article', ARTICLE, before)
    before = registry.get('article', ARTICLE)
    _delete_member(rid)
    get_directory_cleanup_store().run_pending()
    assert_preserved(path, before)
    assert (other / 'evidence').read_text() == 'second root'
    assert not private.exists()
    assert resident_catalog.get_catalog().get_historical(rid)['deleted_at']


def test_user_delete_does_not_erase_registered_content_inside_old_identity_retention(tmp_path):
    access = PortalAccessStore()
    access.upsert_user('nested_author', actor='sample_admin')
    root = tmp_path / 'old-user-owned-tree'
    path, before = manuscript(root / ARTICLE, 'nested_author')
    private = root / 'private-cache'
    private.write_text('private runtime')
    # Historical retention may cover the article's ancestor with a User owner.
    RetentionStore().declare(root, category='object_data', owner_id='nested_author')
    access.delete_user('nested_author', actor='sample_admin')
    get_directory_cleanup_store().run_pending()
    assert_preserved(path, before)
    assert not private.exists()
    assert not access.is_active('nested_author')
