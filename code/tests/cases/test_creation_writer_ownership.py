import hashlib
import json
from pathlib import Path
import pytest
from management_common.portal_database import portal_database
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.society_service import lineage_handoffs, lineage_handoff_execution, lineage_handoff_preparation
from tests.cases.test_lineage_backend import lineage_store
from tests.cases.test_handoff_directory_creation import draft

def sever(key):
    unsigned = key & ((1 << 64) - 1)
    db = portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        pids = [r[0] for r in conn.execute(
            "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND granted",
            (unsigned >> 32, unsigned & 0xffffffff))]
        assert len(pids) == 1, pids
        assert conn.execute('SELECT pg_terminate_backend(%s)', (pids[0],)).fetchone()[0]
    # Backend termination completes before takeover is exercised.
    import time
    for _ in range(100):
        with db.read() as conn:
            if not conn.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s', (pids[0],)).fetchone():
                return
        time.sleep(.01)
    raise AssertionError('isolated lock holder did not stop')

def test_handoff_live_writer_after_lease_disconnect(lineage_store, tmp_path, monkeypatch):
    from management_portal.society_service.errors import LineageConflictError
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    original = lineage_handoff_preparation.prepare_items
    def lose_lease(*args):
        original(*args)
        key = int.from_bytes(hashlib.sha256(f"handoff:{lid}:{handoff['id']}".encode()).digest()[:8], 'big', signed=True)
        sever(key)
        with pytest.raises(LineageConflictError):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
        with pytest.raises(LineageConflictError):
            lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
        raise RuntimeError('old live writer failed')
    monkeypatch.setattr(lineage_handoff_preparation, 'prepare_items', lose_lease)
    with pytest.raises(RuntimeError):
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert current['status'] == 'preparing'
    operation = current['items'][0]['attempts'][0]['operation_id']
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT resource_id FROM resource_creations WHERE actor=%s', (lid,)).fetchone()
    assert row and get_workspace_store().rows([row[0]])
    monkeypatch.setattr(lineage_handoff_preparation, 'prepare_items', original)
    resumed = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert resumed['status'] == 'ready'
    assert resumed['items'][0]['attempts'][0]['operation_id'] == operation
    assert resumed['items'][0]['result']['target_workspace_id'] == row[0]


def test_creation_cannot_publish_after_lease_disconnect(tmp_path):
    actor, operation = 'review-actor', 'review-operation'
    def initialize(stage, effects):
        creation_key = 'resource-create:' + hashlib.sha256((actor + '\0workspace:' + operation).encode()).hexdigest()
        key = int.from_bytes(hashlib.sha256(creation_key.encode()).digest()[:8], 'big', signed=True)
        sever(key)
        with portal_database(schema='directory_lifecycle').advisory_lock(key, blocking=False) as owned:
            assert owned
            (stage / 'old-writer').write_text('old writer is still alive')
        return {}
    with pytest.raises(RuntimeError, match='ownership'):
        WorkspaceCreationStore().materialize(path=tmp_path / 'copy', actor=actor,
            operation_id=operation, contents={}, initialize=initialize)
    assert not (tmp_path / 'copy').exists()
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT * FROM resource_creations WHERE actor=%s', (actor,)).fetchone()
    assert not row['completed'] and not get_workspace_store().rows([row['resource_id']])
    resumed, _ = WorkspaceCreationStore().materialize(path=tmp_path / 'copy', actor=actor,
        operation_id=operation, contents={}, initialize=lambda stage, effects: (stage/'fresh').write_text('new'))
    assert resumed['workspace_id'] == row['resource_id']
    assert (tmp_path/'copy/fresh').exists() and not (tmp_path/'copy/old-writer').exists()


def _creation_key(actor, operation):
    return 'resource-create:' + hashlib.sha256((actor + '\0workspace:' + operation).encode()).hexdigest()


@pytest.mark.parametrize('delete_parent', [False, True])
def test_live_creation_process_blocks_cleanup_and_takeover_until_death(tmp_path, delete_parent):
    import subprocess
    import sys
    from management_portal.resources.creation import finish_deleted_creations
    from management_portal.resources.directory_registrations import unregister_identity
    actor, operation = 'child-writer', 'child-operation'
    parent_id = 'lineage_0123456789ab' if delete_parent else ''
    target = tmp_path / 'copy'
    program = '''
import sys, os
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
def populate(stage, effects):
    with (stage/'payload').open('w') as output:
        output.write('before'); output.flush()
        print(stage, flush=True)
        sys.stdin.readline()
        output.write('after'); output.flush(); os.fsync(output.fileno())
        print('STILL_WRITING', flush=True)
        sys.stdin.readline()
    return {}
WorkspaceCreationStore().materialize(path=sys.argv[1], parent_id=sys.argv[2], actor='child-writer',
    operation_id='child-operation', contents={}, initialize=populate)
'''
    child = subprocess.Popen([sys.executable, '-c', program, str(target), parent_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stage = Path(child.stdout.readline().strip())
        assert stage.is_dir() and (stage / 'payload').read_text() == 'before'
        key = _creation_key(actor, operation)
        sever(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True))
        store = WorkspaceCreationStore()
        with pytest.raises(RuntimeError, match='ownership'):
            store.materialize(path=target, parent_id=parent_id, actor=actor, operation_id=operation,
                              contents={}, initialize=lambda *args: pytest.fail('live stage stolen'))
        with pytest.raises(RuntimeError, match='ownership'):
            store.abort(actor=actor, operation_ids=[operation])
        if delete_parent:
            db = portal_database(schema='directory_lifecycle')
            with db.transaction() as conn:
                unregister_identity(parent_id, connection=conn, actor=actor)
            assert 'ownership' in '; '.join(finish_deleted_creations())
        child.stdin.write('write\n'); child.stdin.flush()
        assert child.stdout.readline().strip() == 'STILL_WRITING'
        assert child.poll() is None and (stage/'payload').read_text() == 'beforeafter'
        with portal_database(schema='directory_lifecycle').read() as conn:
            before = conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s', (key,)).fetchone()
        assert before['artifacts'][0]['writer']['ended'] is False
        assert not before['aborted'] and not before['completed']
        print('CREATION_LIVE', json.dumps({'pid': child.pid, 'parent_deleted': delete_parent,
                                         'stage_exists': stage.exists(), 'still_writes': True}))
        child.kill(); child.wait(timeout=10)
        if delete_parent:
            assert finish_deleted_creations() == []
            assert not stage.exists() and not target.exists()
        else:
            row, _ = store.materialize(path=target, actor=actor, operation_id=operation,
                contents={}, initialize=lambda stage, effects: (stage/'fresh').write_text('recovered'))
            assert row['workspace_id'] == before['resource_id']
            assert (target/'fresh').exists() and not stage.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_handoff_actual_process_death_recovers_same_attempt(lineage_store, tmp_path, monkeypatch):
    import multiprocessing
    from management_portal.society_service.errors import LineageConflictError
    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('fixture inheritance requires fork')
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    context = multiprocessing.get_context('fork')
    parent, pipe = context.Pipe()
    original = lineage_handoff_preparation.prepare_items
    def pause(*args):
        original(*args)
        prepared = args[-1]
        path = Path(prepared[0][1]['target_path'])
        pipe.send(str(path))
        pipe.recv()
        (path/'live-writer').write_text('after PG disconnect')
        pipe.send('STILL_WRITING')
        pipe.recv()
    def execute():
        lineage_handoff_preparation.prepare_items = pause
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    child = context.Process(target=execute)
    child.start()
    try:
        assert parent.poll(20)
        path = Path(parent.recv())
        sever(int.from_bytes(hashlib.sha256(f"handoff:{lid}:{handoff['id']}".encode()).digest()[:8], 'big', signed=True))
        before = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
        operation = before['items'][0]['attempts'][0]['operation_id']
        with pytest.raises(LineageConflictError):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
        with pytest.raises(LineageConflictError):
            lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
        parent.send('write')
        assert parent.poll(10) and parent.recv() == 'STILL_WRITING'
        assert child.is_alive() and (path/'live-writer').exists()
        child.kill(); child.join(10)
        assert not child.is_alive()
        after = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
        assert after['status'] == 'ready'
        assert len(after['items'][0]['attempts']) == 1
        assert after['items'][0]['attempts'][0]['operation_id'] == operation
        assert (path/'live-writer').read_text() == 'after PG disconnect'
        print('HANDOFF_DEATH', json.dumps({'pid': child.pid, 'same_attempt': operation, 'status': after['status']}))
    finally:
        if child.is_alive():
            child.kill(); child.join(10)
        parent.close(); pipe.close()


def test_creation_registration_rolls_back_when_session_lost(tmp_path, monkeypatch):
    from management_portal.resources.workspace_store import WorkspaceStore
    actor, operation = 'registration-writer', 'registration-operation'
    key = _creation_key(actor, operation)
    original = WorkspaceStore.ensure
    def lose(*args, **kwargs):
        result = original(*args, **kwargs)
        sever(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(WorkspaceStore, 'ensure', lose)
        with pytest.raises(RuntimeError, match='ownership'):
            WorkspaceCreationStore().materialize(path=tmp_path/'copy', actor=actor, operation_id=operation,
                contents={}, initialize=lambda stage, effects: (stage/'payload').write_text('complete'))
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s', (key,)).fetchone()
    assert not row['completed'] and not get_workspace_store().rows([row['resource_id']])
    resumed, _ = WorkspaceCreationStore().materialize(path=tmp_path/'copy', actor=actor, operation_id=operation,
        contents={}, initialize=lambda *args: pytest.fail('published content must be reused'))
    assert resumed['workspace_id'] == row['resource_id']


def test_abort_rolls_back_unregistration_when_session_lost(tmp_path, monkeypatch):
    from management_portal.resources import directory_registrations
    actor, operation = 'abort-writer', 'abort-operation'
    target = tmp_path/'copy'
    store = WorkspaceCreationStore()
    row, _ = store.materialize(path=target, actor=actor, operation_id=operation,
        contents={}, initialize=lambda stage, effects: (stage/'payload').write_text('keep'))
    original = directory_registrations.unregister_tree
    key = _creation_key(actor, operation)
    def lose(*args, **kwargs):
        result = original(*args, **kwargs)
        sever(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(directory_registrations, 'unregister_tree', lose)
        with pytest.raises(RuntimeError, match='ownership'):
            store.abort(actor=actor, operation_ids=[operation])
    assert get_workspace_store().rows([row['workspace_id']])
    assert (target/'payload').read_text() == 'keep'
    store.abort(actor=actor, operation_ids=[operation])
    assert not get_workspace_store().rows([row['workspace_id']]) and not target.exists()


@pytest.mark.parametrize('boundary', ['ready', 'initializer'])
def test_handoff_session_loss_fences_delegated_creation_and_ready(lineage_store, tmp_path, monkeypatch, boundary):
    from management_portal.resources.creation import Creation
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    key = int.from_bytes(hashlib.sha256(f"handoff:{lid}:{handoff['id']}".encode()).digest()[:8], 'big', signed=True)
    with monkeypatch.context() as patch:
        if boundary == 'ready':
            original = lineage_handoff_execution.complete_attempt
            def lose(*args):
                sever(key)
                original(*args)
            patch.setattr(lineage_handoff_execution, 'complete_attempt', lose)
        else:
            original = Creation.publish
            def lose(self, initialize):
                def populate(index, stage):
                    initialize(index, stage)
                    sever(key)
                return original(self, populate)
            patch.setattr(Creation, 'publish', lose)
        with pytest.raises(RuntimeError, match='ownership'):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert current['status'] == 'preparing'
    attempt = current['items'][0]['attempts'][0]['operation_id']
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT * FROM resource_creations WHERE actor=%s', (lid,)).fetchone()
    if boundary == 'initializer':
        assert not row['completed'] and not get_workspace_store().rows([row['resource_id']])
        assert not Path(current['items'][0]['target']['path']).exists()
    resumed = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert resumed['status'] == 'ready' and resumed['items'][0]['attempts'][0]['operation_id'] == attempt


@pytest.mark.parametrize('receipt', [{}, {'ended': False, 'process': {'host': 'unknown-host'}},
                                   {'ended': False, 'process': {'pid': 99999999}}])
@pytest.mark.parametrize('completed', [False, True])
def test_unknown_creation_writer_is_retained(tmp_path, receipt, completed):
    actor, operation = 'unknown-writer', 'unknown-operation'
    store = WorkspaceCreationStore()
    def initialize(stage, effects):
        (stage/'payload').write_text('keep')
        if not completed:
            raise RuntimeError('unfinished initializer')
        return {'frozen': 'keep'}
    def materialize():
        return store.materialize(path=tmp_path/'copy', actor=actor, operation_id=operation,
            contents={}, initialize=initialize)
    if completed:
        result = materialize()
    else:
        with pytest.raises(RuntimeError, match='unfinished initializer'):
            materialize()
    db = portal_database(schema='directory_lifecycle')
    with db.transaction() as conn:
        conn.execute("UPDATE resource_creations SET artifacts=jsonb_set(artifacts, '{0,writer}', %s::jsonb) "
                     'WHERE operation_id=%s', (json.dumps(receipt), _creation_key(actor, operation)))
    with pytest.raises(RuntimeError, match='ownership'):
        store.abort(actor=actor, operation_ids=[operation])
    def replay():
        return store.materialize(path=tmp_path/'copy', actor=actor, operation_id=operation,
            contents={}, initialize=lambda *args: pytest.fail('unknown owner taken over'))
    if completed:
        assert replay() == result
        assert get_workspace_store().rows([result[0]['workspace_id']])
        assert (tmp_path/'copy/payload').read_text() == 'keep'
    else:
        assert any('ownership' in error for error in finish_instance_creations())
        with pytest.raises(RuntimeError, match='ownership'):
            replay()
        stage, = tmp_path.glob('.tfi-create-*')
        assert (stage/'payload').read_text() == 'keep'
    with db.read() as conn:
        row = conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s',
                           (_creation_key(actor, operation),)).fetchone()
    assert row['artifacts'][0]['writer'] == receipt
    assert not row['aborted'] and not row['cleanup_complete']


def test_unknown_preparing_handoff_cannot_execute_or_edit(lineage_store, tmp_path, monkeypatch):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    from management_portal.society_service import lineage_aggregate
    from management_portal.society_service.errors import LineageConflictError
    with monkeypatch.context() as patch:
        patch.setattr(lineage_handoff_execution, 'complete_attempt',
                      lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    with lineage_aggregate.lineage_scope(lid):
        previous = lineage_aggregate.read_lineage(lid, actor='sample_admin')
        current = json.loads(json.dumps(previous))
        edge = lineage_handoffs._handoff_edge(current, handoff['id'])
        for item in edge['items']:
            item['attempts'][-1]['result'].pop('_writer')
        lineage_handoffs._save_handoff_lineage(previous, current, 'sample_admin', 'legacy missing receipt', runtime=True)
    with pytest.raises(LineageConflictError):
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    with pytest.raises(LineageConflictError):
        lineage_handoffs.update_handoff(lid, handoff['id'], {'items': [{}]}, 'sample_admin')
    assert lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']['status'] == 'preparing'


# Closure regressions exercise real completed registration and runtime transactions.
from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
from management_portal.resources.creation import finish_deleted_creations, finish_scope_creations, finish_instance_creations
from management_portal.resources.directory_registrations import unregister_identity

ACTOR = 'closure-review'
OP = 'synthetic-pre-upgrade'
KEY = 'resource-create:' + hashlib.sha256((ACTOR + '\0workspace:' + OP).encode()).hexdigest()

def materialize(path, parent_id=''):
    def init(stage, effects):
        (stage / 'payload').write_text('original data')
        return {'frozen': 'original'}
    return WorkspaceCreationStore().materialize(path=path, actor=ACTOR, operation_id=OP,
        contents={'source': 'frozen'}, initialize=init, parent_id=parent_id)

def legacy_row():
    db = portal_database(schema='directory_lifecycle')
    with db.transaction() as conn:
        conn.execute("UPDATE resource_creations SET artifacts=artifacts #- '{0,writer}' WHERE operation_id=%s", (KEY,))
    with db.read() as conn:
        row = dict(conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s', (KEY,)).fetchone())
    assert row['completed'] and not row['aborted'] and 'writer' not in row['artifacts'][0]
    return row

def read_row():
    with portal_database(schema='directory_lifecycle').read() as conn:
        return dict(conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s', (KEY,)).fetchone())

@pytest.mark.parametrize('writer_state', ['missing', 'ended', 'unknown'])
def test_completed_creation_readonly_replay(tmp_path, writer_state):
    target = tmp_path / 'copy'
    result = materialize(target)
    if writer_state == 'missing':
        before = legacy_row()
    elif writer_state == 'unknown':
        with portal_database(schema='directory_lifecycle').transaction() as conn:
            conn.execute("UPDATE resource_creations SET artifacts=jsonb_set(artifacts, '{0,writer}', '{}'::jsonb) WHERE operation_id=%s", (KEY,))
        before = read_row()
    else:
        before = read_row()
    def forbidden(*args):
        pytest.fail('Completed replay must not run initializer')
    replayed = WorkspaceCreationStore().materialize(path=target, actor=ACTOR, operation_id=OP,
        contents={'source': 'frozen'}, initialize=forbidden)
    assert replayed == result
    assert (target/'payload').read_text() == 'original data'
    assert read_row()['artifacts'] == before['artifacts'], 'Readonly replay rewrote writer metadata'

@pytest.mark.parametrize('boundary', ['parent', 'scope', 'instance'])
@pytest.mark.parametrize('writer_state', ['missing', 'ended', 'unknown'])
def test_completed_creation_bookkeeping_can_finish(tmp_path, boundary, writer_state):
    target = tmp_path / 'copy'
    parent_id = 'lineage_0123456789ab'
    lifecycle = LifecycleStore()
    scope = lifecycle.open('closure-scope', actor=ACTOR, run_id='closure', case_id=boundary)
    with lifecycle_scope(scope, actor=ACTOR):
        result = materialize(target, parent_id)
    if writer_state == 'missing':
        legacy_row()
    elif writer_state == 'unknown':
        with portal_database(schema='directory_lifecycle').transaction() as conn:
            conn.execute("UPDATE resource_creations SET artifacts=jsonb_set(artifacts, '{0,writer}', '{}'::jsonb) WHERE operation_id=%s", (KEY,))
    before = read_row()
    if boundary == 'parent':
        with portal_database(schema='directory_lifecycle').transaction() as conn:
            unregister_identity(parent_id, actor=ACTOR, connection=conn)
        errors = finish_deleted_creations()
    elif boundary == 'scope':
        lifecycle.seal(scope)
        errors = finish_scope_creations(scope)
    else:
        # Only this disposable database; no service or production client exists.
        errors = finish_instance_creations()
    after = read_row()
    print('LEGACY_CLEANUP', json.dumps({'boundary': boundary, 'errors': errors,
        'cleanup_complete': after['cleanup_complete'], 'target_exists': target.exists()}))
    assert errors == [], 'Old completed intent cannot finish the normal cleanup owner'
    assert after['cleanup_complete']
    assert not after['aborted']
    assert after['artifacts'] == before['artifacts']
    assert (target/'payload').read_text() == 'original data'
    assert get_workspace_store().rows([result[0]['workspace_id']])


@pytest.mark.parametrize('boundary', ['entry', 'after_write'])
def test_handoff_disconnect_inside_runtime_transaction_cannot_commit_ready(lineage_store, tmp_path, monkeypatch, boundary):
    from management_portal.society_service import lineage_handoffs, lineage_state
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    key = int.from_bytes(hashlib.sha256(f"handoff:{lid}:{handoff['id']}".encode()).digest()[:8], 'big', signed=True)
    original = lineage_state._persist_lineage_state_transaction
    injected = []
    def persist(previous, candidate, reason, **kwargs):
        edge = lineage_handoffs._handoff_edge(candidate, handoff['id'])
        disconnect = edge['status'] == 'ready' and not injected
        if disconnect and boundary == 'entry':
            sever(key)
            injected.append(True)
        result = original(previous, candidate, reason, **kwargs)
        if disconnect and boundary == 'after_write':
            sever(key)
            injected.append(True)
        return result
    monkeypatch.setattr(lineage_state, '_persist_lineage_state_transaction', persist)
    caught = None
    try:
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    except RuntimeError as error:
        caught = str(error)
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert injected
    print('HANDOFF_SQL_BOUNDARY', json.dumps({'status': current['status'], 'error': caught}))
    assert current['status'] == 'preparing', 'Disconnected executor committed ready from inside SQL transaction'

    assert caught and 'ownership lost' in caught
    receipt = lineage_handoff_execution.owner_receipt(current)
    assert receipt['ended'] is True, 'Effects ended receipt must survive PG lease death'
    operation = current['items'][0]['attempts'][-1]['operation_id']
    resumed = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert resumed['status'] == 'ready'
    assert resumed['items'][0]['attempts'][-1]['operation_id'] == operation


@pytest.mark.parametrize('remaining', ['unproven_stage', 'aborted'])
def test_completed_unknown_writer_cannot_clean_remaining_effects(tmp_path, monkeypatch, remaining):
    from management_portal.resources import creation as owner
    target = tmp_path / 'copy'
    materialize(target)
    if remaining == 'unproven_stage':
        stage = tmp_path / 'unknown-stage'
        stage.mkdir()
        (stage/'payload').write_text('unproven')
        from management_common.directory_paths import cleanup_location
        row = read_row()
        row['artifacts'][0]['unproven_stages'] = [
            {'path': cleanup_location(stage), 'operation_id': KEY + ':unproven'}]
        with portal_database(schema='directory_lifecycle').transaction() as conn:
            conn.execute('UPDATE resource_creations SET artifacts=%s::jsonb WHERE operation_id=%s',
                         (json.dumps(row['artifacts']), KEY))
    else:
        with monkeypatch.context() as patch:
            patch.setattr(owner, '_remove_owned', lambda *args: (_ for _ in ()).throw(RuntimeError('cleanup interrupted')))
            with pytest.raises(RuntimeError, match='cleanup interrupted'):
                WorkspaceCreationStore().abort(actor=ACTOR, operation_ids=[OP])
        assert read_row()['aborted']
    with portal_database(schema='directory_lifecycle').transaction() as conn:
        conn.execute("UPDATE resource_creations SET artifacts=artifacts #- '{0,writer}' WHERE operation_id=%s", (KEY,))
    before = read_row()
    assert any('ownership' in error for error in finish_instance_creations())
    assert read_row() == before
    assert (target/'payload').read_text() == 'original data'
    if remaining == 'unproven_stage':
        assert (stage/'payload').read_text() == 'unproven'


def test_completed_replay_cannot_mutate_or_ignore_identity_fences(tmp_path):
    from management_portal.resources.creation import Creation
    target = tmp_path / 'copy'
    result = materialize(target)
    row = legacy_row()
    creation = Creation(row['resource_id'], actor=ACTOR, operation_id='workspace:' + OP, payload=row['payload'])
    with creation.prepare([(target, 'directory')]):
        creation.publish(lambda *args: pytest.fail('completed initializer ran'))
        with pytest.raises(RuntimeError, match='read-only'):
            creation.assert_owned()
        with creation.db.transaction() as conn, pytest.raises(RuntimeError, match='read-only'):
            with creation.registration(conn):
                pytest.fail('completed registration ran')
    assert read_row() == row
    with pytest.raises(ValueError, match='different arguments'):
        WorkspaceCreationStore().materialize(path=target, actor=ACTOR, operation_id=OP,
            contents={'changed': True}, initialize=lambda *a: None)
    with portal_database(schema='directory_lifecycle').transaction() as conn:
        unregister_identity(result[0]['workspace_id'], actor=ACTOR, connection=conn)
    with pytest.raises(KeyError):
        materialize(target)
    assert (target/'payload').read_text() == 'original data'


def test_handoff_ready_rejects_foreign_candidate_writer(lineage_store, tmp_path, monkeypatch):
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    complete = lineage_handoff_execution.complete_attempt
    def replace_token(edge, *args):
        complete(edge, *args)
        edge['items'][0]['attempts'][-1]['result']['_writer']['token'] = 'foreign-writer'
    monkeypatch.setattr(lineage_handoff_execution, 'complete_attempt', replace_token)
    with pytest.raises(RuntimeError, match='ownership lost'):
        lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    assert current['status'] == 'failed'
    assert lineage_handoff_execution.owner_receipt(current)['token'] != 'foreign-writer'
