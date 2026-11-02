"""Creation cancellation through the real kernel and PAR engine, isolated DB only."""
import json

import pytest

from management_common.portal_database import portal_database
from management_common.lifetime_execution_lease import execution_lease
from management_portal.resources.creation import Creation, get_resource_creation
from management_portal.society_service.creation_intents import CreationIntent
from management_portal.society_service.isa import build_kernel
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from tests.cases.permission_test_support import Fact, enable

PARENT = 'lineage_aaaaaaaaaaaa'
FOREIGN = 'lineage_bbbbbbbbbbbb'
RESOURCE = 'resident_cccccccccccc'


@pytest.fixture
def world(monkeypatch):
    permissions = enable(monkeypatch)
    permissions.users('contributor', 'viewer', category='intern')
    permissions.place(Fact('project', 'Cancellation'),
        Fact('lineage', PARENT, {'project': 'Cancellation'}),
        Fact('lineage', FOREIGN, {'project': 'Cancellation'}))
    permissions.grant('contributor', PARENT, '@contributor')
    permissions.grant('contributor', FOREIGN, '@contributor')
    permissions.grant('viewer', PARENT, '@viewer')
    yield permissions
    from management_portal import permission_system
    permission_system.forget_implementation()


def allocate(tmp_path, *, complete=False):
    request = {'payload': {'lineage_id': PARENT}, 'lineage_node': {}}
    intent = CreationIntent('resident', 'original-caller', request, 'original-operation')
    creation = Creation(RESOURCE, actor='original-caller', payload=request, operation_id=intent.key)
    with intent.open(lambda: {'record': {'resident_id': RESOURCE}}):
        with creation.prepare([(tmp_path/'allocated', 'directory')]):
            creation.publish(lambda index, stage: (stage/'payload').write_text('owned contents'))
            if complete:
                with creation.db.transaction() as conn, creation.registration(conn):
                    intent.complete(conn)
    return creation


def call(name='CancelResourceCreation', actor='contributor', parent=PARENT, **arguments):
    return build_kernel().execute(InstructionCall(name, {'lineage_id': parent, 'resource_id': RESOURCE, **arguments}),
                                 InstructionContext(caller=actor, entry='web'))


def outcome(result):
    assert result.status == 'committed', result.error
    return result.result['creation']


def test_cancel_idempotent_and_original_replay_refused(world, tmp_path):
    creation = allocate(tmp_path)
    assert not outcome(call('GetResourceCreation'))['registered']
    first = outcome(call())
    assert first['cancelled'] and first['cleanup_complete'] and not first['errors']
    assert not (tmp_path/'allocated').exists()
    assert outcome(call()) == first
    intent = CreationIntent('resident', 'original-caller', creation.payload, 'original-operation')
    with pytest.raises(ValueError, match='cancelled'), intent.open(lambda: pytest.fail('reallocation')):
        pass
    with portal_database(schema='directory_lifecycle').read() as conn:
        row = conn.execute('SELECT snapshot FROM society_creation_intents WHERE operation_id=%s', (intent.key,)).fetchone()
    assert row['snapshot']['cancellation']['actor'] == 'contributor'


def test_kernel_rejects_viewer_and_forged_parent(world, tmp_path):
    allocate(tmp_path)
    denied = call(actor='viewer')
    assert denied.status == 'failed' and denied.error['code'] == 'instruction.forbidden'
    denied = call(parent=FOREIGN)
    assert denied.status == 'failed'
    assert (tmp_path/'allocated/payload').read_text() == 'owned contents'
    assert not get_resource_creation(RESOURCE)['aborted']


def test_committed_creation_refused(world, tmp_path):
    allocate(tmp_path, complete=True)
    denied = call()
    assert denied.status == 'failed'
    assert not get_resource_creation(RESOURCE)['aborted']
    assert (tmp_path/'allocated').exists()


@pytest.mark.parametrize('writer', ['active', 'unknown', 'missing'])
def test_writer_evidence_is_not_fabricated(world, tmp_path, writer):
    creation = allocate(tmp_path)
    from management_common.process_observation import process_identity
    receipt = {'ended': False, 'process': process_identity()} if writer == 'active' else {}
    row = get_resource_creation(RESOURCE)
    if writer == 'missing':
        row['artifacts'][0].pop('writer')
    else:
        row['artifacts'][0]['writer'] = receipt
    creation.db.write(lambda conn: conn.execute('UPDATE resource_creations SET artifacts=%s::jsonb WHERE resource_id=%s',
                                             (json.dumps(row['artifacts']), RESOURCE)))
    result = outcome(call())
    assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
    assert get_resource_creation(RESOURCE)['artifacts'] == row['artifacts']
    assert (tmp_path/'allocated').exists()


def test_native_admission_refuses_even_ended_receipt(world, tmp_path):
    creation = allocate(tmp_path)
    with execution_lease(creation.key, namespace=creation.db.schema, blocking=False) as held:
        assert held
        result = outcome(call())
        assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
    assert outcome(call())['cleanup_complete']


def legacy_allocation(tmp_path, monkeypatch):
    """Real kernel normal-unwind receipt, matching nested keys, real initializer."""
    from management_portal.hatg.contracts import InstructionError
    from management_portal.society_service.resident_workspace_domain import ensure_resident_workspace
    kernel = build_kernel()
    allocated = {}
    def handler(inv):
        request = {'payload': {'title': 'Pending', 'lineage_id': PARENT, 'project_id': 'Cancellation',
            'lineage_title': 'Parent', 'lineage_folder_path': str(tmp_path)},
            'lineage_node': {}, 'project_principal': False, 'personal_owner': ''}
        intent = CreationIntent('resident', inv.caller, request, inv.instruction_id)
        creation = Creation(RESOURCE, actor=inv.caller, payload=request, operation_id=intent.key)
        record = {'resident_id': RESOURCE, 'lineage_id': PARENT, 'created_by': inv.caller,
                  'owned_workspace_id': 'workspace_dddddddddddd',
                  'owned_workspace': {'workspace_id': 'workspace_dddddddddddd', 'path': str(tmp_path/'allocated/workspace')},
                  'session_state': 'pending_create', 'registration_state': 'initializing'}
        with intent.open(lambda: {'record': record, 'runtime_settings': {}}):
            with creation.prepare([(tmp_path/'allocated', 'directory')]):
                creation.publish(lambda index, stage: ensure_resident_workspace(record, folder=stage))
        allocated.update(creation=creation, intent=intent)
        raise InstructionError('instruction.conflict', 'Lineage CAS', commit_state='not_committed',
            details={'object_id': PARENT, 'expected_version': 1, 'current_version': 2})
    kernel.binding.handlers['CreateComponent'] = handler
    result = kernel.execute(InstructionCall('CreateComponent', {'lineage_id': PARENT, 'kind': 'seed',
        'title': 'Pending'}), InstructionContext(caller='contributor', entry='web'))
    assert result.status == 'failed' and result.error['code'] == 'instruction.conflict', result.error
    creation = allocated['creation']
    row = get_resource_creation(RESOURCE)
    row['artifacts'][0].pop('writer')
    creation.db.write(lambda conn: conn.execute('UPDATE resource_creations SET artifacts=%s::jsonb WHERE resource_id=%s',
        (json.dumps(row['artifacts']), RESOURCE)))
    return kernel, result.instruction_id, creation, allocated['intent']


def test_normal_synchronous_legacy_completion(world, tmp_path, monkeypatch):
    kernel, original, creation, intent = legacy_allocation(tmp_path, monkeypatch)
    result = outcome(call(originating_instruction_id=original))
    assert result['cancelled'] and result['cleanup_complete'] and not result['errors']
    assert not (tmp_path/'allocated').exists()
    with creation.db.read() as conn:
        snapshot = conn.execute('SELECT snapshot FROM society_creation_intents WHERE operation_id=%s',
            (intent.key,)).fetchone()['snapshot']
    proof = snapshot['normal_handler_completion']
    assert proof['receipt'] == kernel.store.get(original)
    assert proof['intent_key'] == intent.key and proof['creation_key'] == creation.key
    assert snapshot['cancellation']['actor'] == 'contributor'
    with pytest.raises(ValueError, match='cancelled'), intent.open(lambda: {}):
        pass
    assert outcome(call())['cleanup_complete']


@pytest.mark.parametrize('failure', ['interrupted', 'unknown', 'timeout', 'owner_unavailable',
    'executing', 'wrong_id', 'caller', 'parent', 'creation_key', 'fork', 'pending_fork', 'native',
    'active_writer', 'malformed_writer', 'active_instruction', 'intent_key', 'creation_exclusion', 'other_kind'])
def test_legacy_completion_refuses_unproven_paths(world, tmp_path, monkeypatch, failure):
    from contextlib import nullcontext
    from management_common.process_observation import process_identity
    kernel, original, creation, intent = legacy_allocation(tmp_path, monkeypatch)
    if failure in {'interrupted', 'unknown', 'timeout', 'owner_unavailable', 'executing'}:
        error = dict(kernel.store.get(original)['error'])
        if failure in {'interrupted', 'unknown'}:
            error['commit_state'] = 'unknown'
        if failure == 'interrupted':
            error['details'] = {'reason': 'interrupted'}
        if failure in {'timeout', 'owner_unavailable'}:
            error['code'] = 'instruction.' + failure
        kernel.store.finish(original, status='executing' if failure == 'executing' else 'failed',
            now=kernel.store.get(original)['updated_at'], error=error)
    elif failure in {'caller', 'parent'}:
        column = 'caller' if failure == 'caller' else 'scope_id'
        kernel.store.database.write(lambda conn: conn.execute(
            f'UPDATE instruction_executions SET {column}=%s WHERE instruction_id=%s', ('wrong', original)))
    elif failure in {'fork', 'pending_fork'}:
        if failure == 'fork':
            intent.request['payload']['initialize_from_source'] = True
            creation.db.write(lambda conn: conn.execute('UPDATE society_creation_intents SET request=%s::jsonb WHERE operation_id=%s',
                (json.dumps(intent.request), intent.key)))
            creation.db.write(lambda conn: conn.execute('UPDATE resource_creations SET payload=%s::jsonb WHERE resource_id=%s',
                (json.dumps(intent.request), RESOURCE)))
        else:
            intent.snapshot['record']['_pending_agent_data_fork'] = {}
            intent.save()
    elif failure in {'native', 'active_writer', 'malformed_writer', 'other_kind'}:
        row = get_resource_creation(RESOURCE)
        if failure == 'native':
            row['artifacts'][0]['git_dependency'] = {'kind': 'worktree'}
        elif failure == 'other_kind':
            row['artifacts'][0]['kind'] = 'adopted'
        else:
            row['artifacts'][0]['writer'] = ({'ended': False, 'process': process_identity()}
                if failure == 'active_writer' else {})
        creation.db.write(lambda conn: conn.execute('UPDATE resource_creations SET artifacts=%s::jsonb WHERE resource_id=%s',
            (json.dumps(row['artifacts']), RESOURCE)))
    elif failure == 'intent_key':
        creation.db.write(lambda conn: conn.execute('UPDATE society_creation_intents SET operation_id=%s WHERE operation_id=%s',
            ('resident:wrong', intent.key)))
    elif failure == 'creation_key':
        creation.db.write(lambda conn: conn.execute('UPDATE resource_creations SET operation_id=%s WHERE resource_id=%s',
            ('resource-create:wrong', RESOURCE)))
    reference = 'inst_lin_00000000000000000000' if failure == 'wrong_id' else original
    before = get_resource_creation(RESOURCE)['artifacts']
    exclusion = (kernel.store.active_execution(original) if failure == 'active_instruction' else
                 execution_lease(creation.key, namespace=creation.db.schema, blocking=False)
                 if failure == 'creation_exclusion' else nullcontext())
    with exclusion:
        result = outcome(call(originating_instruction_id=reference))
    assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
    assert get_resource_creation(RESOURCE)['artifacts'] == before
    assert (tmp_path/'allocated').exists()


def test_legacy_completion_preserves_foreign_inode_and_retries(world, tmp_path, monkeypatch):
    _, original, creation, intent = legacy_allocation(tmp_path, monkeypatch)
    target = tmp_path/'allocated'
    target.rename(tmp_path/'owned')
    target.mkdir()
    (target/'foreign').write_text('keep')
    result = outcome(call(originating_instruction_id=original))
    assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
    audit = outcome(call('GetResourceCreation'))['normal_handler_completion']
    assert audit['receipt']['instruction_id'] == original
    target.rename(tmp_path/'foreign')
    (tmp_path/'owned').rename(target)
    assert outcome(call())['cleanup_complete']
    assert (tmp_path/'foreign/foreign').read_text() == 'keep'
    assert outcome(call('GetResourceCreation'))['normal_handler_completion'] == audit


def test_legacy_proof_cannot_override_surviving_native_child(world, tmp_path, monkeypatch):
    import subprocess
    from management_common.process_runtime import spawn_process
    _, original, creation, _ = legacy_allocation(tmp_path, monkeypatch)
    with execution_lease(creation.key, namespace=creation.db.schema, blocking=False) as acquired:
        assert acquired
        child = spawn_process(['/bin/cat'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        result = outcome(call(originating_instruction_id=original))
        assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
        assert 'writer' not in get_resource_creation(RESOURCE)['artifacts'][0]
        assert outcome(call('GetResourceCreation'))['normal_handler_completion'] is None
    finally:
        child.communicate(timeout=5)
    assert outcome(call(originating_instruction_id=original))['cleanup_complete']


def test_foreign_inode_retained_and_pending_cleanup_retries(world, tmp_path):
    allocate(tmp_path)
    target = tmp_path/'allocated'
    target.rename(tmp_path/'original')
    target.mkdir()
    (target/'foreign').write_text('keep')
    result = outcome(call())
    assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
    assert (target/'foreign').read_text() == 'keep'
    target.rename(tmp_path/'foreign')
    (tmp_path/'original').rename(target)
    result = outcome(call())
    assert result['cancelled'] and result['cleanup_complete'] and not result['errors']
    assert (tmp_path/'foreign/foreign').read_text() == 'keep'


def test_surviving_native_child_defers_cleanup(world, tmp_path):
    import subprocess
    from management_common.process_runtime import spawn_process
    creation = allocate(tmp_path)
    with execution_lease(creation.key, namespace=creation.db.schema, blocking=False) as held:
        assert held
        child = spawn_process(['/bin/cat'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        assert child.poll() is None
        result = outcome(call())
        assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
        assert (tmp_path/'allocated/payload').read_text() == 'owned contents'
    finally:
        child.communicate(timeout=5)
    assert outcome(call())['cleanup_complete']


def test_pending_cancellation_fences_cached_intent_and_live_writer(world, tmp_path):
    request = {'payload': {'lineage_id': PARENT}, 'lineage_node': {}}
    intent = CreationIntent('resident', 'original-caller', request, 'original-operation')
    creation = Creation(RESOURCE, actor='original-caller', payload=request, operation_id=intent.key)
    with intent.open(lambda: {'record': {'resident_id': RESOURCE}}):
        with creation.prepare([(tmp_path/'allocated', 'directory')]):
            creation.publish(lambda index, stage: (stage/'payload').write_text('owned'))
            # The owner marks cancellation while this stack's writer is still
            # alive. No cleanup admission or fake ended receipt is obtained.
            from management_portal.resources.creation import cancel_resource_creation
            from management_portal.society_service.creation_intents import record_intent_cancellation
            result = cancel_resource_creation(RESOURCE, validate=lambda row, conn: None,
                record_cancellation=lambda row, conn: record_intent_cancellation(intent, 'contributor', conn))
            assert result['cancelled'] and not result['cleanup_complete'] and result['errors']
            with pytest.raises(RuntimeError, match='cancelled'):
                creation.assert_owned()
            with pytest.raises(RuntimeError, match='cancelled'):
                with creation.db.transaction() as conn, creation.registration(conn):
                    pytest.fail('Cancelled writer reached registration')
            intent.snapshot.pop('cancellation')
            with pytest.raises(ValueError, match='cancelled'):
                intent.save()
            with pytest.raises(ValueError, match='cancelled'):
                with creation.db.transaction() as conn:
                    intent.complete(conn)
            assert (tmp_path/'allocated/payload').read_text() == 'owned'
    assert outcome(call())['cleanup_complete']
