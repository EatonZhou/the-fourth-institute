"""Creation owns pre-registration files through run termination.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
from management_portal.resources import creation as module
from management_portal.resources.creation import Creation
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.test_framework.run_cleanup import finish_run
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace
from tests.cases.test_resident_folder_creation_contract import future_creation_store


def open_scope():
    store = LifecycleStore()
    return store, store.open('failed-request', actor='alice', run_id='run-failed', case_id='create')


def attempt(target):
    return Creation('paper_121212121212', actor='alice', payload={}, operation_id='failed-request')


@pytest.mark.parametrize('adopted', [False, True])
def test_instance_retirement_finishes_unregistered_external_creation(workspace, adopted):
    from scripts.directory_protocol_retire import cleanup_external_created
    target = workspace.root.with_name('unregistered-external')
    if adopted:
        target.mkdir()
        (target / 'host-data').write_text('keep')
    creation = attempt(target)
    with creation.prepare([(target, 'adopted' if adopted else 'directory')]):
        if not adopted:
            creation.publish(lambda _, stage: (stage / 'data').write_text('ours'))
    cleanup_external_created()
    assert target.exists() is adopted


def test_instance_retirement_preserves_replaced_creation_and_refuses_completion(workspace):
    from scripts.directory_protocol_retire import cleanup_external_created
    target = workspace.root.with_name('replaced-external')
    creation = attempt(target)
    with creation.prepare([(target, 'directory')]):
        creation.publish(lambda _, stage: (stage / 'data').write_text('ours'))
    target.rename(target.with_name('saved-original'))
    target.mkdir()
    (target / 'host-data').write_text('keep')
    with pytest.raises(RuntimeError, match='Creation cleanup remains pending'):
        cleanup_external_created()
    assert (target / 'host-data').read_text() == 'keep'


@pytest.mark.parametrize('replacement', ['empty', 'identical'])
def test_workspace_creation_does_not_adopt_replaced_staging(tmp_path, monkeypatch, replacement):
    target = tmp_path / 'new-created'
    creator = WorkspaceCreationStore()
    with monkeypatch.context() as patch:
        patch.setattr(module, 'rename_directory_no_replace', lambda *_: (_ for _ in ()).throw(SystemExit('crash before publish')))
        with pytest.raises(SystemExit):
            creator.create(path=target, owner_id='', actor='alice', operation_id='replace-stage')
    stage, = target.parent.glob('.tfi-create-*')
    if replacement == 'identical':
        (stage / '.tfi-directory-creation').write_text('copied operation marker')
    saved = stage.with_name('original-stage')
    stage.rename(saved)
    stage.mkdir()
    if replacement == 'identical':
        (stage / '.tfi-directory-creation').write_bytes((saved / '.tfi-directory-creation').read_bytes())
    unrelated_inode = stage.stat().st_ino
    with pytest.raises(FileExistsError):
        creator.create(path=target, owner_id='', actor='alice', operation_id='replace-stage')
    assert stage.stat().st_ino == unrelated_inode
    assert not target.exists()


def test_closed_run_cleans_uncommitted_creation(tmp_path):
    lifecycle, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            creation.publish(lambda _, stage: (stage / 'test-data').write_text('owned test artifact'))
        assert lifecycle.pending(scope) == []
    result = finish_run('run-failed')
    assert result['status'] == 'complete', result
    assert not target.exists(), 'run marked complete while its creation artifact and reservation remain'
    with creation.db.read() as conn:
        assert not conn.execute(f'SELECT 1 FROM {creation.queue.reservations}').fetchone()
        row = conn.execute(f'SELECT aborted,cleanup_complete FROM {creation.table}').fetchone()
        assert row['aborted'] and row['cleanup_complete']
    assert lifecycle.scope(scope)['state'] == 'complete'
    assert finish_run('run-failed')['status'] == 'complete'
    with pytest.raises(ValueError, match='aborted'):
        with attempt(target).prepare([(target, 'directory')]):
            pytest.fail('aborted operation resumed without its original scope')
    assert not target.exists()


@pytest.mark.parametrize('phase', ['intent', 'stage', 'published', 'partial'])
def test_finish_run_cleans_all_creation_phases(tmp_path, monkeypatch, phase):
    lifecycle, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory'), (tmp_path / 'second', 'file')]):
            if phase != 'intent':
                def initialize(index, stage):
                    if phase == 'stage' or (phase == 'partial' and index == 1):
                        raise RuntimeError('initializer stopped')
                    if index == 0:
                        (stage / 'data').write_text('ours')
                    else:
                        stage.write_text('ours')
                if phase in {'stage', 'partial'}:
                    with pytest.raises(RuntimeError):
                        creation.publish(initialize)
                else:
                    creation.publish(initialize)
        assert not lifecycle.pending(scope)
    result = finish_run('run-failed')
    assert result['status'] == 'complete', result
    assert not target.exists()
    assert not (tmp_path / 'second').exists()
    assert not list(tmp_path.glob('.tfi-create-*'))


@pytest.mark.parametrize('replace_path', ['target', 'stage', 'unknown'])
def test_unknown_or_replaced_creation_stays_reserved_and_incomplete(tmp_path, monkeypatch, replace_path):
    lifecycle, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            with monkeypatch.context() as patch:
                if replace_path == 'unknown':
                    save = Creation._save
                    def crash(self, connection):
                        if self.artifacts[0].get('identity'):
                            raise RuntimeError('before inode commit')
                        return save(self, connection)
                    patch.setattr(Creation, '_save', crash)
                elif replace_path == 'stage':
                    patch.setattr(module, 'rename_directory_no_replace', lambda *_: (_ for _ in ()).throw(RuntimeError('before publish')))
                if replace_path == 'target':
                    creation.publish(lambda *_: None)
                else:
                    with pytest.raises(RuntimeError):
                        creation.publish(lambda *_: None)
    occupied = target if replace_path == 'target' else next(tmp_path.glob('.tfi-create-*'))
    if replace_path != 'unknown':
        occupied.rename(tmp_path / 'saved')
        occupied.mkdir()
    (occupied / 'foreign').write_text('must stay')
    assert finish_run('run-failed')['status'] == 'incomplete'
    assert (occupied / 'foreign').read_text() == 'must stay'
    assert lifecycle.scope(scope)['state'] == 'closing'
    with creation.db.transaction() as conn:
        with pytest.raises(FileExistsError):
            creation.queue.assert_available(occupied, connection=conn)


def test_cleanup_failure_retries_without_releasing_reservation(tmp_path, monkeypatch):
    _, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            creation.publish(lambda *_: None)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'remove_path', lambda *a, **k: (_ for _ in ()).throw(PermissionError('busy')))
        assert finish_run('run-failed')['status'] == 'incomplete'
    with creation.db.transaction() as conn:
        with pytest.raises(FileExistsError):
            creation.queue.assert_available(target, connection=conn)
    assert target.exists()
    assert finish_run('run-failed')['status'] == 'complete'
    assert not target.exists()


def test_finish_run_fences_inflight_registration_and_delayed_retry(tmp_path):
    lifecycle, scope = open_scope()
    prepared, proceed = Event(), Event()
    target = tmp_path / 'unfinished'
    def worker():
        with lifecycle_scope(scope, actor='alice'):
            creation = attempt(target)
            with creation.prepare([(target, 'directory')]):
                creation.publish(lambda *_: None)
                prepared.set()
                assert proceed.wait(10)
                with pytest.raises(ValueError, match='closed'):
                    with creation.db.transaction() as conn, creation.registration(conn):
                        pytest.fail('registration passed the closing fence')
            with pytest.raises(ValueError, match='closed'):
                with attempt(target).prepare([(target, 'directory')]):
                    pytest.fail('nested retry passed the closing fence')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        try:
            assert prepared.wait(10)
            assert finish_run('run-failed')['status'] == 'error'
            assert lifecycle.scope(scope)['state'] == 'closing'
        finally:
            proceed.set()
        future.result(timeout=10)
    assert finish_run('run-failed')['status'] == 'complete'
    assert not target.exists()


def test_failed_adoption_does_not_delete_foreign_directory(tmp_path, monkeypatch):
    _, scope = open_scope()
    target = tmp_path / 'adopted'
    target.mkdir()
    (target / 'keep').write_text('foreign')
    creator = WorkspaceCreationStore()
    monkeypatch.setattr(creator.workspaces, 'ensure', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('rollback')))
    with lifecycle_scope(scope, actor='alice'), pytest.raises(RuntimeError):
        creator.create(path=target, owner_id='', actor='alice', operation_id='adopt', origin='adopted')
    assert finish_run('run-failed')['status'] == 'complete'
    assert (target / 'keep').read_text() == 'foreign'


def test_completed_creation_with_unknown_stage_cannot_complete_run(workspace, monkeypatch):
    from management_portal.test_framework import tau_fixtures
    lifecycle, scope = open_scope()
    target = workspace.root.with_name('new-owned')
    def create():
        return WorkspaceCreationStore().create(path=target, owner_id='', actor='alice', operation_id='stage-gap')
    save = Creation._save
    def crash(self, conn):
        if self.artifacts[0].get('identity'):
            raise RuntimeError('crash before inode commit')
        save(self, conn)
    with lifecycle_scope(scope, actor='alice'):
        with monkeypatch.context() as patch:
            patch.setattr(Creation, '_save', crash)
            with pytest.raises(RuntimeError):
                create()
        unknown, = target.parent.glob('.tfi-create-*')
        (unknown / 'keep').write_text('unknown')
        row = create()
        assert lifecycle.pending(scope) == [row['workspace_id']]
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, _: workspace.service.unregister(rid, actor='alice'))
    result = finish_run('run-failed')
    assert result['status'] == 'incomplete', result
    assert not lifecycle.pending(scope)
    assert (unknown / 'keep').read_text() == 'unknown'
    assert lifecycle.scope(scope)['state'] == 'closing'
    # An explicit external resolution can remove the unknown directory. The
    # owner then releases its reservation without ever adopting that inode.
    (unknown / 'keep').unlink()
    unknown.rmdir()
    assert finish_run('run-failed')['status'] == 'complete'
    assert not target.exists()
    assert workspace.root.exists()


def test_society_intent_files_are_closed_by_creation_owner(future_creation_store, monkeypatch):
    from management_portal.society_service import lineage_lifecycle, lineage_state
    lifecycle = LifecycleStore()
    scope = lifecycle.open('society', actor='sample_admin', run_id='society-run', case_id='create')
    with lifecycle_scope(scope, actor='sample_admin'):
        with monkeypatch.context() as patch:
            patch.setattr(lineage_state, '_persist_lineage_state', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('commit interrupted')))
            with pytest.raises(RuntimeError, match='interrupted'):
                lineage_lifecycle.create_lineage({'title': 'Pending'}, 'sample_admin', operation_id='society-pending')
    from management_common.portal_database import portal_database
    from management_common.directory_paths import cleanup_path
    db = portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        creation = conn.execute('SELECT * FROM resource_creations WHERE scope_id=%s', (scope,)).fetchone()
        intent = conn.execute('SELECT * FROM society_creation_intents WHERE scope_id=%s', (scope,)).fetchone()
    target = cleanup_path(creation['artifacts'][0]['path'])
    assert intent and target.is_dir() and not lifecycle.pending(scope)
    result = finish_run('society-run')
    assert result['status'] == 'complete', result
    assert not target.exists()
    with pytest.raises(ValueError, match='another resource lifecycle'):
        lineage_lifecycle.create_lineage({'title': 'Pending'}, 'sample_admin', operation_id='society-pending')
    assert not target.exists()


def test_concurrent_finish_run_converges(tmp_path, monkeypatch):
    from threading import Barrier
    _, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            creation.publish(lambda *_: None)
    barrier = Barrier(2)
    def finish(_):
        barrier.wait(timeout=10)
        return finish_run('run-failed')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, range(2)))
    assert all(result['status'] == 'complete' for result in results), results
    assert not target.exists()


def test_cleanup_rechecks_owned_inode_at_deletion_boundary(tmp_path, monkeypatch):
    _, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            creation.publish(lambda *_: None)
    remove = module.remove_path
    def replace_then_remove(path, **kwargs):
        path.rename(tmp_path / 'saved-original')
        path.mkdir()
        (path / 'foreign').write_text('foreign')
        return remove(path, **kwargs)
    monkeypatch.setattr(module, 'remove_path', replace_then_remove)
    result = finish_run('run-failed')
    assert result['status'] == 'incomplete', result
    assert (target / 'foreign').read_text() == 'foreign'


def test_cleanup_transaction_failure_retains_claim_and_retries(tmp_path, monkeypatch):
    _, scope = open_scope()
    target = tmp_path / 'unfinished'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'directory')]):
            creation.publish(lambda *_: None)
    with monkeypatch.context() as patch:
        patch.setattr(creation.queue, 'release', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('commit interrupted')))
        result = finish_run('run-failed')
        assert result['status'] == 'incomplete', result
    assert not target.exists()
    with creation.db.read() as conn:
        row = conn.execute(f'SELECT aborted,cleanup_complete FROM {creation.table}').fetchone()
        assert row['aborted'] and not row['cleanup_complete']
        assert conn.execute(f'SELECT 1 FROM {creation.queue.reservations}').fetchone()
    assert finish_run('run-failed')['status'] == 'complete'


def test_initializer_retry_recovers_after_owned_cleanup_before_intent_commit(tmp_path, monkeypatch):
    target = tmp_path / 'target'
    with attempt(target).prepare([(target, 'directory')]) as creation:
        with pytest.raises(RuntimeError, match='initializer'):
            creation.publish(lambda *_: (_ for _ in ()).throw(RuntimeError('initializer stopped')))
    stage, = tmp_path.glob('.tfi-create-*')
    with monkeypatch.context() as patch:
        patch.setattr(Creation, '_save', lambda *a: (_ for _ in ()).throw(RuntimeError('intent commit interrupted')))
        with attempt(target).prepare([(target, 'directory')]) as creation:
            with pytest.raises(RuntimeError, match='intent commit'):
                creation.publish(lambda *_: None)
    assert not stage.exists()
    with attempt(target).prepare([(target, 'directory')]) as creation:
        creation.publish(lambda _, stage: (stage / 'data').write_text('complete'))
    assert (target / 'data').read_text() == 'complete'
