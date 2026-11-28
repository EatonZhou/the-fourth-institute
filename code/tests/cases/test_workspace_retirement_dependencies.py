"""Registration-only retirement requires identity evidence, never just paths."""
import pytest

from management_portal.resources import directory_registrations
from management_portal.resources.creation import Creation, pending_creation_workspace_dependency
from management_portal.resources.registry_store import get_registry_store
from tests.cases.test_workspace_redirected_retirement import workspace, resource_workspace, prepare, call
from tests.cases.test_workspace_resource_rename import WID


@pytest.mark.parametrize('inside', [True, False])
def test_pending_parent_reference_blocks_even_outside_tree(workspace, tmp_path, inside):
    prepare(workspace)
    target = (workspace.root if inside else tmp_path) / 'pending-child'
    creation = Creation('resident_111111111111', actor='alice', payload={},
                        operation_id='pending-parent', workspace_dependencies=[WID])
    with creation.prepare([(target, 'directory')]):
        with creation.db.read() as connection:
            before = connection.execute(f'SELECT * FROM {creation.queue.reservations}').fetchall()
        result = call(workspace)
        assert result.status == 'failed' and result.error['commit_state'] == 'not_committed'
        assert 'pending identity dependencies' in result.error['message']
        assert workspace.store.rows([WID])
        with creation.db.read() as connection:
            assert connection.execute(f'SELECT * FROM {creation.queue.reservations}').fetchall() == before
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_legacy_creation_is_unknown_and_positive_reference_wins(workspace, tmp_path):
    prepare(workspace)
    legacy = Creation('resident_111111111111', actor='alice', payload={}, operation_id='legacy')
    dependent = Creation('resident_222222222222', actor='alice', payload={},
                         operation_id='known', workspace_dependencies=[WID])
    with legacy.prepare([(tmp_path / 'outside-legacy', 'directory')]):
        result = call(workspace)
        assert result.status == 'failed' and 'unknown pending identity dependencies' in result.error['message']
        with dependent.prepare([(workspace.root / 'child', 'directory')]):
            with dependent.db.transaction() as connection:
                assert pending_creation_workspace_dependency(WID, connection=connection) == 'dependent'
        assert workspace.store.rows([WID])


def test_independent_child_creation_and_claims_survive_and_commit(workspace):
    prepare(workspace)
    target = workspace.root / 'independent-child'
    rid = 'knowledge_111111111111'
    creation = Creation(rid, actor='alice', payload={}, operation_id='independent',
                        workspace_dependencies=[])
    with creation.prepare([(target, 'directory')]):
        creation.publish(lambda _index, stage: (stage / 'keep.txt').write_text('keep'))
        with creation.db.read() as connection:
            before = connection.execute(f'SELECT * FROM {creation.queue.reservations}').fetchall()
        result = call(workspace).raise_for_error()
        assert result['attributes']['directory_retirement']['cleanup_ids'] == []
        assert result['attributes']['directory_retirement']['preserves_independent_claims'] is True
        with creation.db.read() as connection:
            assert connection.execute(f'SELECT * FROM {creation.queue.reservations}').fetchall() == before
        with creation.db.transaction() as connection, creation.identity_registration(connection):
            with creation.registration(connection):
                directory_registrations.register_owned('knowledge', rid, [target], connection=connection)
        assert get_registry_store().get('knowledge', rid)
    assert not workspace.store.rows([WID])
    assert (target / 'keep.txt').read_text() == 'keep'
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_unmapped_reservation_stays_unknown_even_without_pending_creations(workspace):
    prepare(workspace)
    with workspace.store.database.transaction() as connection:
        workspace.store.cleanup.reserve(workspace.root / 'foreign-child',
            operation_id='unmapped-writer', purpose='resource_creation', connection=connection)
    result = call(workspace)
    assert result.status == 'failed' and 'operation claims' in result.error['message']
    assert workspace.store.rows([WID])


def test_missing_owner_outcome_cannot_be_used_as_permission(workspace):
    prepare(workspace)
    row = workspace.store.rows([WID])[WID]
    with workspace.store.database.transaction() as connection:
        with pytest.raises(ValueError, match='unknown pending identity dependencies'):
            directory_registrations.retire_workspace_registration(row, connection=connection, actor='alice')
    assert workspace.store.rows([WID])


def test_declaration_claim_and_retirement_share_transaction_lock(workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    prepare(workspace)
    creation = Creation('resident_111111111111', actor='alice', payload={},
                        operation_id='race', workspace_dependencies=[WID])
    declared, release, retiring = Event(), Event(), Event()
    reserve = creation.queue.reserve
    def pause_after_declaration(path, **kwargs):
        declared.set()
        assert release.wait(5)
        return reserve(path, **kwargs)
    monkeypatch.setattr(creation.queue, 'reserve', pause_after_declaration)
    def create():
        with creation.prepare([(workspace.root / 'pending', 'directory')]):
            pass
    def retire():
        retiring.set()
        return call(workspace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        producer = executor.submit(create)
        try:
            assert declared.wait(5)
            consumer = executor.submit(retire)
            assert retiring.wait(5)
            with pytest.raises(TimeoutError):
                consumer.result(timeout=0.1)
        finally:
            release.set()
        producer.result(timeout=5)
        result = consumer.result(timeout=5)
    assert result.status == 'failed' and 'pending identity dependencies' in result.error['message']
    assert workspace.store.rows([WID])
    assert not workspace.store.cleanup.status(owner_id=WID)
