"""Durable Handoff copies use the Workspace directory protocol.

:module: resident.fork-handoff
:design: decision_4aa13bc21e8e, decision_7ef392b5f60d
"""
from pathlib import Path

import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.portal_database import portal_database
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import lineage_handoff_backend as backend
from management_portal.society_service import lineage_handoffs, lineage_lifecycle, lineage_state
from management_portal.society_service import lineage_resident_workspaces
from tests.cases.test_lineage_backend import lineage_store, sample_lineage_payload


def finish_binding_projections():
    from management_portal.society_service import resident_record_domain, resident_lifecycle_steps_domain
    operations = resident_record_domain.lifecycle_operations()
    for operation in operations.ready():
        operations.run(operation, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)


def copy_item(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file').write_text('source content')
    return {'source': {'path': str(source)},
            'transfer': {'mode': 'copy', 'method': 'filesystem_copy'},
            'target': {'path': str(tmp_path / 'target')}}


def materialize(item, tmp_path):
    return backend.materialize_handoff_item(item, actor='alice', operation_id='copy-operation',
                                           allowed_target_roots=[tmp_path], checked_at='now')


def test_copy_has_durable_self_owned_identity_and_retry(tmp_path):
    item = copy_item(tmp_path)
    first = materialize(item, tmp_path)
    wid = first['target_workspace_id']
    row = get_workspace_store().rows([wid])[wid]
    assert row['owner_id'] == wid
    assert row['operation_id']
    (Path(row['path']) / 'file').write_text('target edited')
    assert materialize(item, tmp_path) == first
    assert (Path(row['path']) / 'file').read_text() == 'target edited'
    with portal_database(schema='directory_lifecycle').read() as conn:
        creation = conn.execute('SELECT * FROM resource_creations WHERE resource_id=%s', (wid,)).fetchone()
    assert creation['completed']


def test_copy_recovers_after_publication_before_registration(tmp_path, monkeypatch):
    from management_portal.resources.workspace_store import WorkspaceStore
    item = copy_item(tmp_path)
    original = WorkspaceStore.ensure
    monkeypatch.setattr(WorkspaceStore, 'ensure', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('registration crash')))
    with pytest.raises(RuntimeError, match='registration crash'):
        materialize(item, tmp_path)
    assert (tmp_path / 'target/file').read_text() == 'source content'
    monkeypatch.setattr(WorkspaceStore, 'ensure', original)
    result = materialize(item, tmp_path)
    assert get_workspace_store().rows([result['target_workspace_id']])


def test_copy_failure_never_removes_replacement_target(tmp_path, monkeypatch):
    item = copy_item(tmp_path)
    def fail(*args, **kwargs):
        target = tmp_path / 'target'
        target.mkdir()
        (target / 'user-file').write_text('keep')
        raise RuntimeError('copy interrupted')
    monkeypatch.setattr(backend.shutil, 'copytree', fail)
    with pytest.raises(RuntimeError, match='copy interrupted'):
        materialize(item, tmp_path)
    assert (tmp_path / 'target/user-file').read_text() == 'keep'


def draft(lineage_store, tmp_path, method='filesystem_copy'):
    payload = sample_lineage_payload()
    payload['edges'] = []
    lineage = lineage_lifecycle.create_lineage(payload, 'sample_admin')
    lid = lineage['lineage_id']
    source, target = [node['id'] for node in payload['nodes']]
    view = lineage_resident_workspaces.get_node_workspaces(lid, source, 'sample_admin')
    root = next(root for root in view['workspaces'] if root['is_default_cwd'])
    Path(root['path']).mkdir(parents=True, exist_ok=True)
    (Path(root['path']) / 'handoff-fixture').write_text('copy me')
    if method == 'git_worktree':
        from tests.cases.test_lineage_handoff_backend import _git
        repo = Path(root['path'])
        _git(repo, 'init', '-b', 'main')
        _git(repo, 'config', 'user.name', 'Fixture')
        _git(repo, 'config', 'user.email', 'fixture@example.invalid')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-m', 'fixture')
    handoff = lineage_handoffs.create_handoff(lid, {
        'source_resident_id': source, 'target_resident_id': target,
        'items': [{'transfer': {'mode': 'copy', 'method': method,
                                'parameters': {'branch': 'handoff-copy'} if method == 'git_worktree' else {}}}],
    }, 'sample_admin')['handoff']
    return lid, handoff, target, root


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_official_copy_binding_and_standalone_unregistration(lineage_store, tmp_path, method):
    from management_portal.resources.directory_registrations import unregister_tree
    lid, handoff, target, source = draft(lineage_store, tmp_path, method)
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    copied = result['items'][0]['result']
    wid = copied['target_workspace_id']
    store = get_workspace_store()
    row = store.rows([wid])[wid]
    assert row['owner_id'] == wid
    assert wid != source['workspace_id']
    assert Path(row['path']).is_relative_to(lineage_state._lineage_dir(lid) / 'handoff-workspaces')
    roots = lineage_resident_workspaces.get_node_workspaces(lid, target, 'sample_admin')['workspaces']
    assert wid in [root['workspace_id'] for root in roots]
    with store.database.transaction() as conn:
        unregister_tree(workspace_ids=[wid], connection=conn, actor='sample_admin')
    get_directory_cleanup_store().run_pending()
    assert not Path(row['path']).exists()
    assert Path(source['path']).is_dir()


def test_preparing_crash_resumes_same_attempt(lineage_store, tmp_path, monkeypatch):
    from management_portal.society_service import lineage_handoff_execution
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    original = lineage_handoff_execution.complete_attempt
    monkeypatch.setattr(lineage_handoff_execution, 'complete_attempt',
                        lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    operation = current['items'][0]['attempts'][0]['operation_id']
    with portal_database(schema='directory_lifecycle').read() as conn:
        wid = conn.execute('SELECT resource_id FROM resource_creations WHERE actor=%s', (lid,)).fetchone()[0]
    monkeypatch.setattr(lineage_handoff_execution, 'complete_attempt', original)
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['status'] == 'ready'
    assert len(result['items'][0]['attempts']) == 1
    assert result['items'][0]['attempts'][0]['operation_id'] == operation
    assert result['items'][0]['result']['target_workspace_id'] == wid


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_lineage_deletion_removes_copy(lineage_store, tmp_path, method):
    lid, handoff, target, source = draft(lineage_store, tmp_path, method)
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    copied = result['items'][0]['result']
    lineage_lifecycle.delete_lineage(lid, 'sample_admin')
    get_directory_cleanup_store().run_pending()
    assert not Path(copied['target_path']).exists()
    assert not get_workspace_store().rows([copied['target_workspace_id']])


def test_partial_copy_is_rebuilt_on_retry(tmp_path, monkeypatch):
    from management_common.directory_paths import cleanup_path
    item = copy_item(tmp_path)
    original = backend.shutil.copytree
    def fail(source, stage, **kwargs):
        (stage / 'partial').write_text('interrupted')
        raise RuntimeError('partial copy')
    monkeypatch.setattr(backend.shutil, 'copytree', fail)
    with pytest.raises(RuntimeError, match='partial copy'):
        materialize(item, tmp_path)
    with portal_database(schema='directory_lifecycle').read() as conn:
        artifact = conn.execute('SELECT artifacts FROM resource_creations').fetchone()[0][0]
    stage = cleanup_path(artifact['stage'])
    assert stage.is_dir() and not (tmp_path / 'target').exists()
    monkeypatch.setattr(backend.shutil, 'copytree', original)
    result = materialize(item, tmp_path)
    assert not stage.exists()
    assert not (Path(result['target_path']) / 'partial').exists()


def test_retry_never_removes_foreign_stage(tmp_path, monkeypatch):
    from management_common.directory_paths import cleanup_path
    item = copy_item(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(backend.shutil, 'copytree',
                      lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('partial copy')))
        with pytest.raises(RuntimeError, match='partial copy'):
            materialize(item, tmp_path)
    with portal_database(schema='directory_lifecycle').read() as conn:
        artifact = conn.execute('SELECT artifacts FROM resource_creations').fetchone()[0][0]
    stage = cleanup_path(artifact['stage'])
    stage.rename(stage.with_name('original-stage'))
    stage.mkdir()
    (stage / 'user-file').write_text('keep')
    with pytest.raises(FileExistsError):
        materialize(item, tmp_path)
    assert (stage / 'user-file').read_text() == 'keep'


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_binding_failure_cleans_identity_and_retries_new_attempt(lineage_store, tmp_path, monkeypatch, method):
    lid, handoff, target, source = draft(lineage_store, tmp_path, method)
    bind = lineage_resident_workspaces.add_node_reference_workspace
    def fail_after_binding(*args, **kwargs):
        bind(*args, **kwargs)
        raise RuntimeError('binding crash')
    with monkeypatch.context() as patch:
        patch.setattr(lineage_resident_workspaces, 'add_node_reference_workspace', fail_after_binding)
        with pytest.raises(RuntimeError, match='binding crash'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    with portal_database(schema='directory_lifecycle').read() as conn:
        old = conn.execute("SELECT * FROM resource_creations WHERE actor=%s", (lid,)).fetchone()
    wid = old['resource_id']
    assert old['aborted'] and old['cleanup_complete']
    assert not get_workspace_store().rows([wid])
    assert not Path(handoff['items'][0]['target']['path']).exists()
    roots = lineage_resident_workspaces.get_node_workspaces(lid, target, 'sample_admin')['workspaces']
    assert wid not in [root['workspace_id'] for root in roots]
    assert Path(source['path']).is_dir()
    finish_binding_projections()
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] != wid
    assert [a['status'] for a in result['items'][0]['attempts']] == ['failed', 'ready']


def test_draft_resolves_moved_source_by_identity(lineage_store, tmp_path):
    from management_common.directory_move_store import DirectoryMoveStore
    from management_portal.directory_move_owner import relocate_nested, repair_git_worktrees
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    old = Path(source['path'])
    moved = old.with_name('moved-workspace')
    store = get_workspace_store()
    DirectoryMoveStore().move(old, moved, owner_id=source['workspace_id'], actor='sample_admin',
                              terminate=lambda _: None, database=store.database,
                              update=relocate_nested, dependencies=repair_git_worktrees)
    old.mkdir()
    (old / 'handoff-fixture').write_text('unrelated replacement')
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert (Path(result['items'][0]['result']['target_path']) / 'handoff-fixture').read_text() == 'copy me'


def test_ordinary_binding_registers_owner_and_direct_keeps_identity(lineage_store, tmp_path):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    external = tmp_path / 'existing'
    external.mkdir()
    lineage_resident_workspaces.add_node_reference_workspace(lid, target, 'sample_admin', str(external))
    roots = lineage_resident_workspaces.get_node_workspaces(lid, target, 'sample_admin')['workspaces']
    wid = next(root['workspace_id'] for root in roots if root['path'] == str(external))
    assert get_workspace_store().rows([wid])[wid]['owner_id'] == wid
    lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] == source['workspace_id']


@pytest.mark.parametrize('boundary', ['populate', 'publish', 'register'])
def test_git_copy_recovers_and_repairs_worktree_links(tmp_path, monkeypatch, boundary):
    from tests.cases.test_lineage_handoff_backend import _git
    from management_portal.resources import creation
    from management_portal.resources.workspace_store import WorkspaceStore
    from management_common.directory_move_store import DirectoryMoveStore
    from management_portal.directory_move_owner import relocate_nested, repair_git_worktrees
    item = copy_item(tmp_path)
    repo = Path(item['source']['path'])
    _git(repo, 'init', '-b', 'main')
    _git(repo, 'config', 'user.name', 'Fixture')
    _git(repo, 'config', 'user.email', 'fixture@example.invalid')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'fixture')
    (repo / 'file').write_text('uncommitted source')
    item['transfer'] = {'mode': 'copy', 'method': 'git_worktree',
                        'parameters': {'branch': 'handoff-copy', 'include_worktree_changes': True}}
    def crash(*a, **kw):
        raise RuntimeError('git crash')
    with monkeypatch.context() as patch:
        if boundary == 'populate':
            patch.setattr(backend, '_manifest', crash)
        elif boundary == 'publish':
            patch.setattr(creation, 'rename_directory_no_replace', crash)
        else:
            patch.setattr(WorkspaceStore, 'ensure', crash)
        with pytest.raises(RuntimeError, match='git crash'):
            materialize(item, tmp_path)
    result = materialize(item, tmp_path)
    target = Path(result['target_path'])
    assert _git(target, 'branch', '--show-current') == 'handoff-copy'
    assert (target / 'file').read_text() == 'uncommitted source'
    assert str(target) in _git(repo, 'worktree', 'list', '--porcelain')
    assert materialize(item, tmp_path) == result
    store = get_workspace_store()
    moved = target.with_name('moved-copy')
    DirectoryMoveStore().move(target, moved, owner_id=result['target_workspace_id'],
                              actor='alice', terminate=lambda _: None, database=store.database,
                              update=relocate_nested, dependencies=repair_git_worktrees)
    assert _git(moved, 'branch', '--show-current') == 'handoff-copy'
    assert materialize(item, tmp_path)['target_path'] == str(moved)


def test_live_preparer_lease_still_rejects_parallel_execution(lineage_store, tmp_path):
    import hashlib
    from management_portal.society_service.errors import LineageConflictError
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    key = int.from_bytes(hashlib.sha256(f"handoff:{lid}:{handoff['id']}".encode()).digest()[:8], 'big', signed=True)
    with portal_database(schema='directory_lifecycle').advisory_lock(key):
        with pytest.raises(LineageConflictError, match='正在准备'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')


def test_failed_creation_does_not_block_lineage_directory_cleanup(lineage_store, tmp_path, monkeypatch):
    from management_portal import directory_maintenance
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    folder = lineage_state._lineage_dir(lid)
    with monkeypatch.context() as patch:
        patch.setattr(backend.shutil, 'copytree',
                      lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('partial copy')))
        with pytest.raises(RuntimeError, match='partial copy'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    lineage_lifecycle.delete_lineage(lid, 'sample_admin')
    directory_maintenance.run(test_roots=[])
    assert not folder.exists()
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT aborted,cleanup_complete FROM resource_creations WHERE actor=%s', (lid,)).fetchone()
    assert row['aborted'] and row['cleanup_complete']
    with pytest.raises((KeyError, ValueError)):
        backend.materialize_handoff_item(handoff['items'][0], actor=lid, parent_id=lid,
            operation_id=f"handoff:{handoff['id']}:{handoff['items'][0]['item_id']}",
            allowed_target_roots=[folder / 'handoff-workspaces'], checked_at='retry')
    assert not folder.exists()


def test_second_item_failure_cleans_all_attempt_copies(lineage_store, tmp_path, monkeypatch):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    lineage_handoffs.update_handoff(lid, handoff['id'], {
        'items': [{'transfer': {'mode': 'copy'}}, {'transfer': {'mode': 'copy'}}],
    }, 'sample_admin')
    original = backend._manifest
    calls = []
    def fail_second(root, **kw):
        calls.append(root)
        if len(calls) == 2:
            raise RuntimeError('second item failed')
        return original(root, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(backend, '_manifest', fail_second)
        with pytest.raises(RuntimeError, match='second item failed'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    with portal_database(schema='directory_lifecycle').read() as conn:
        rows = conn.execute('SELECT resource_id,completed FROM resource_creations WHERE actor=%s', (lid,)).fetchall()
    assert len(rows) == 2 and sum(row['completed'] for row in rows) == 1
    assert not get_workspace_store().rows([row['resource_id'] for row in rows])
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert not {item['result']['target_workspace_id'] for item in result['items']} & {row['resource_id'] for row in rows}
    assert all(len(item['attempts']) == 2 for item in result['items'])


def test_failed_items_can_be_edited_after_cleanup(lineage_store, tmp_path, monkeypatch):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(backend, '_manifest', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('copy failed')))
        with pytest.raises(RuntimeError, match='copy failed'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    changed = lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')['handoff']
    assert changed['items'][0]['transfer']['mode'] == 'direct'
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] == source['workspace_id']


@pytest.mark.parametrize('failure', ['partial', 'bound'])
def test_failed_cleanup_is_durable_blocks_reuse_and_recovers(lineage_store, tmp_path, monkeypatch, failure):
    from management_portal.resources import creation
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    from management_portal import directory_maintenance
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    path = Path(handoff['items'][0]['target']['path'])
    bind = lineage_resident_workspaces.add_node_reference_workspace
    def fail_binding(*args, **kwargs):
        bind(*args, **kwargs)
        raise RuntimeError('binding failed')
    with monkeypatch.context() as patch:
        if failure == 'partial':
            patch.setattr(backend, '_manifest', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('partial failed')))
        else:
            patch.setattr(lineage_resident_workspaces, 'add_node_reference_workspace', fail_binding)
        patch.setattr(creation, '_remove_owned', lambda *a: (_ for _ in ()).throw(OSError('disk busy')))
        with pytest.raises(RuntimeError, match='cleanup pending.*disk busy'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
        with portal_database(schema='directory_lifecycle').read() as conn:
            row = conn.execute('SELECT * FROM resource_creations WHERE actor=%s', (lid,)).fetchone()
        assert row['aborted'] and not row['cleanup_complete']
        assert not get_workspace_store().rows([row['resource_id']])
        for action in [lambda: lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin'),
                       lambda: lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')]:
            with pytest.raises(RuntimeError, match='cleanup pending'):
                action()
        current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
        assert current['status'] == 'failed' and len(current['items'][0]['attempts']) == 1
        assert 'disk busy' in current['error']
        with pytest.raises(FileExistsError, match='reserved'):
            WorkspaceCreationStore().create(path=path, actor='other', owner_id='', operation_id='unrelated')
    # Scheduled recovery uses the persisted owner, without replaying Handoff.
    directory_maintenance.run(test_roots=[])
    with portal_database(schema='directory_lifecycle').read() as conn:
        assert conn.execute('SELECT cleanup_complete FROM resource_creations WHERE resource_id=%s',
                            (row['resource_id'],)).fetchone()[0]
    assert not path.exists()
    finish_binding_projections()
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] != row['resource_id']
    assert Path(source['path']).is_dir()


@pytest.mark.parametrize('location', ['stage', 'target'])
def test_failed_cleanup_never_removes_foreign_directory(lineage_store, tmp_path, monkeypatch, location):
    from management_common.directory_paths import cleanup_path
    from management_portal.resources.creation import finish_deleted_creations
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    foreign = None
    def replace_and_fail(*args, **kwargs):
        nonlocal foreign
        with portal_database(schema='directory_lifecycle').read() as conn:
            artifact = conn.execute('SELECT artifacts FROM resource_creations WHERE actor=%s', (lid,)).fetchone()[0][0]
        foreign = cleanup_path(artifact['stage' if location == 'stage' else 'path'])
        foreign.rename(foreign.with_name('owned-original'))
        foreign.mkdir()
        (foreign / 'foreign').write_text('keep')
        raise RuntimeError('copy failed')
    with monkeypatch.context() as patch:
        if location == 'stage':
            patch.setattr(backend, '_manifest', replace_and_fail)
        else:
            patch.setattr(lineage_resident_workspaces, 'add_node_reference_workspace', replace_and_fail)
        with pytest.raises(RuntimeError, match='cleanup pending'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    assert finish_deleted_creations()
    get_directory_cleanup_store().run_pending()
    assert (foreign / 'foreign').read_text() == 'keep'
    with pytest.raises(FileExistsError, match='reserved'):
        WorkspaceCreationStore().create(path=Path(handoff['items'][0]['target']['path']), actor='other',
                                        operation_id='reuse', owner_id='')
    with pytest.raises(RuntimeError, match='cleanup pending'):
        lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')


def test_direct_failure_never_unregisters_source(lineage_store, tmp_path, monkeypatch):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
    bind = lineage_resident_workspaces.add_node_reference_workspace
    def fail(*args, **kwargs):
        bind(*args, **kwargs)
        raise RuntimeError('direct bind failed')
    with monkeypatch.context() as patch:
        patch.setattr(lineage_resident_workspaces, 'add_node_reference_workspace', fail)
        with pytest.raises(RuntimeError, match='direct bind failed'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    assert get_workspace_store().rows([source['workspace_id']])
    assert (Path(source['path']) / 'handoff-fixture').read_text() == 'copy me'
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] == source['workspace_id']


@pytest.mark.parametrize('boundary', ['populate', 'publish', 'register'])
def test_git_failed_attempt_cleans_dependencies(lineage_store, tmp_path, monkeypatch, boundary):
    from management_portal.resources import creation
    from management_portal.resources.workspace_store import WorkspaceStore
    from tests.cases.test_lineage_handoff_backend import _git
    lid, handoff, target, source = draft(lineage_store, tmp_path, 'git_worktree')
    def crash(*a, **kw):
        raise RuntimeError('git failed')
    repo = Path(source['path'])
    with monkeypatch.context() as patch:
        if boundary == 'populate':
            patch.setattr(backend, '_manifest', crash)
        elif boundary == 'publish':
            patch.setattr(creation, 'rename_directory_no_replace', crash)
        else:
            patch.setattr(WorkspaceStore, 'ensure', crash)
        with pytest.raises(RuntimeError, match='git failed'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    assert _git(repo, 'worktree', 'list', '--porcelain').count('worktree ') == 1
    assert 'handoff-copy' not in _git(repo, 'branch', '--list')
    assert not _git(repo, 'for-each-ref', 'refs/tfi-creations/')
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['status'] == 'ready'


def test_lost_preparing_can_be_edited_after_termination(lineage_store, tmp_path, monkeypatch):
    from management_portal.society_service import lineage_handoff_execution
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(lineage_handoff_execution, 'complete_attempt',
                      lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    changed = lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')['handoff']
    assert changed['status'] == 'draft'
    assert not Path(handoff['items'][0]['target']['path']).exists()


def test_failed_edit_same_item_uses_new_creation_identity(lineage_store, tmp_path, monkeypatch):
    from management_portal.society_service.lineage_handoff_preparation import operation_id
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(backend, '_manifest', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('copy failed')))
        with pytest.raises(RuntimeError, match='copy failed'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    failed = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    with portal_database(schema='directory_lifecycle').read() as conn:
        wid = conn.execute('SELECT resource_id FROM resource_creations WHERE actor=%s', (lid,)).fetchone()[0]
    with pytest.raises(ValueError, match='aborted'):
        backend.materialize_handoff_item(failed['items'][0], actor=lid, parent_id=lid,
            operation_id=operation_id(failed, failed['items'][0]),
            allowed_target_roots=[lineage_state._lineage_dir(lid) / 'handoff-workspaces'], checked_at='retry')
    lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{'transfer': {'mode': 'copy'}}]}, 'sample_admin')
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert result['items'][0]['result']['target_workspace_id'] != wid
    assert not get_workspace_store().rows([wid])


def test_git_failure_preserves_foreign_branch_change(lineage_store, tmp_path, monkeypatch):
    from tests.cases.test_lineage_handoff_backend import _git
    from management_portal.resources.creation import finish_deleted_creations
    lid, handoff, target, source = draft(lineage_store, tmp_path, 'git_worktree')
    repo = Path(source['path'])
    original = _git(repo, 'rev-parse', 'HEAD')
    def changed_branch(*a, **kw):
        copy = Path(handoff['items'][0]['target']['path'])
        _git(copy, 'commit', '--allow-empty', '-m', 'external change')
        raise RuntimeError('binding failed')
    with monkeypatch.context() as patch:
        patch.setattr(lineage_resident_workspaces, 'add_node_reference_workspace', changed_branch)
        with pytest.raises(RuntimeError, match='cleanup pending'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    changed = _git(repo, 'rev-parse', 'handoff-copy')
    assert changed != original
    assert finish_deleted_creations()
    assert _git(repo, 'rev-parse', 'handoff-copy') == changed
    # Once the external change is deliberately resolved, the persisted owner
    # can finish its own branch and worktree cleanup idempotently.
    _git(repo, 'update-ref', 'refs/heads/handoff-copy', original, changed)
    assert not finish_deleted_creations()
    assert not finish_deleted_creations()
    assert 'handoff-copy' not in _git(repo, 'branch', '--list')
    assert _git(repo, 'worktree', 'list', '--porcelain').count('worktree ') == 1


def test_active_preparer_blocks_item_edit(lineage_store, tmp_path):
    from management_portal.society_service.errors import LineageConflictError
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    with lineage_handoffs._handoff_lease(lid, handoff['id']):
        with pytest.raises(LineageConflictError, match='正在准备'):
            lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
        assert lineage_handoffs.update_handoff(lid, handoff['id'], {'label': 'still editable'},
                                               'sample_admin')['handoff']['label'] == 'still editable'


def test_git_creation_owner_preserves_command_timeout_contract(lineage_store, tmp_path, monkeypatch):
    from management_portal.resources import creation_git
    lid, handoff, target, source = draft(lineage_store, tmp_path, 'git_worktree')
    monkeypatch.setenv(backend.HANDOFF_COMMAND_TIMEOUT_ENV, '47')
    run = creation_git.run_process
    seen = []
    def observe(command, **kwargs):
        seen.append((command, kwargs['timeout']))
        return run(command, **kwargs)
    monkeypatch.setattr(creation_git, 'run_process', observe)
    assert lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']['status'] == 'ready'
    assert any('update-ref' in command for command, _ in seen)
    assert all(timeout == 47 for _, timeout in seen)


@pytest.mark.parametrize('command', ['worktree', 'update-ref'])
def test_git_creation_timeout_is_reported_and_cleaned(lineage_store, tmp_path, monkeypatch, command):
    import subprocess
    from management_portal.resources import creation_git
    lid, handoff, target, source = draft(lineage_store, tmp_path, 'git_worktree')
    monkeypatch.setenv(backend.HANDOFF_COMMAND_TIMEOUT_ENV, '47')
    run = creation_git._git
    crashed = False
    def timeout(repo, *args, **kwargs):
        nonlocal crashed
        if not crashed and args[0] == command:
            crashed = True
            raise subprocess.TimeoutExpired(['git', '-C', str(repo), *args], kwargs['timeout'])
        return run(repo, *args, **kwargs)
    monkeypatch.setattr(creation_git, '_git', timeout)
    with pytest.raises(backend.HandoffPreparationError, match='47 秒'):
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT * FROM resource_creations WHERE actor=%s', (lid,)).fetchone()
    assert row['aborted'] and row['cleanup_complete']
