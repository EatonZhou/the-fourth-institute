"""Deleting one workspace must not lock an unrelated Resident's FK target."""
from concurrent.futures import ThreadPoolExecutor

from management_portal.resources.directory_registrations import unregister_tree
from management_portal.resources.workspace_store import get_workspace_store


def test_delete_does_not_lock_unrelated_resident_workspace(tmp_path):
    store = get_workspace_store()
    one, other = 'workspace_111111111111', 'workspace_222222222222'
    first, second = tmp_path / 'one', tmp_path / 'other'
    first.mkdir()
    second.mkdir()
    store.ensure(one, str(first), owner_id='resident_111111111111')
    store.ensure(other, str(second), owner_id='resident_222222222222')

    def unrelated_foreign_key_check():
        # PostgreSQL's reference check takes KEY SHARE, as an unrelated
        # Resident save does. A different thread owns a separate transaction.
        with store.database.transaction() as connection:
            connection.execute(
                f'SELECT workspace_id FROM {store.table} WHERE workspace_id=%s FOR KEY SHARE NOWAIT',
                (other,),
            ).fetchone()

    with store.database.transaction() as connection:
        unregister_tree([first], connection=connection, actor='test', preserve_resources=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(unrelated_foreign_key_check).result(timeout=5)
