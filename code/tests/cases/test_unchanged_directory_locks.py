"""Unchanged path observations must not acquire the instance-wide path lock."""
import pytest
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.resources.directory_registrations import register_owned, unregister_owned
from management_common.directory_cleanup_store import get_directory_cleanup_store


def test_unchanged_workspace_avoids_lock_but_mutations_keep_it(tmp_path, monkeypatch):
    store = get_workspace_store()
    wid = 'workspace_814b042cd123'
    path = str(tmp_path / 'work')
    store.ensure(wid, path)
    def blocked(*args, **kw): raise RuntimeError('global path lock reached')
    monkeypatch.setattr(store.cleanup, 'lock', blocked)
    assert store.ensure(wid, path) == wid
    with pytest.raises(RuntimeError, match='global path lock'):
        store.ensure(wid, str(tmp_path / 'other'), move=True)
    with pytest.raises(RuntimeError, match='global path lock'):
        store.ensure('workspace_814b042cd124', str(tmp_path / 'new'))
    with store.database.transaction() as c:
        c.execute(f'UPDATE {store.table} SET deleted_at=CURRENT_TIMESTAMP WHERE workspace_id=%s', (wid,))
    with pytest.raises(ValueError, match='Deleted Workspace'):
        store.ensure(wid, path)


def test_known_scan_avoids_lock_and_cannot_revive_deleted_owner(tmp_path, monkeypatch):
    rid = 'article_814b042cd123'
    path = tmp_path / 'owner'
    registered = register_owned('knowledge', rid, [path])
    queue = get_directory_cleanup_store()
    lock = queue.lock
    def blocked(*args, **kw): raise RuntimeError('global path lock reached')
    monkeypatch.setattr(queue, 'lock', blocked)
    assert register_owned('knowledge', rid, [path], scanning=True) == registered
    assert register_owned('knowledge', rid, [tmp_path / 'moved'], scanning=True) is None
    with pytest.raises(RuntimeError, match='global path lock'):
        register_owned('knowledge', 'article_814b042cd124', [tmp_path / 'new'], scanning=True)
    monkeypatch.setattr(queue, 'lock', lock)
    unregister_owned('knowledge', rid)
    assert register_owned('knowledge', rid, [path], scanning=True) is None
