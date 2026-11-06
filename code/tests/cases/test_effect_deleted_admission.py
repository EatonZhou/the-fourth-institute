"""A claimed graph snapshot cannot restart an Effect after graph deletion."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from management_portal.society_service import lineage_effect_script_journal as journal
from management_portal.society_service import lineage_primitives, lineage_program_effect_execution as execution
from management_portal.society_service.state_store import get_lineage_data_store
from management_common.principal import OperationIdentity

EFFECT = 'effect_aaaaaaaaaaaa'
LID = 'lineage_deleted_effect_admission'


def definition(present):
    return {'schema': 'tfi.lineage.v1', 'schema_version': 1, 'lineage_id': LID,
            'project_id': '', 'leader_resident_id': '', 'lineage_membership': '', 'principal_hierarchy': {},
            'title': 'Deleted Effect admission', 'nodes': [], 'edges': [],
            'program': {'components': [{'id': EFFECT, 'kind': 'effect', 'config': {'script': 'Ping'}}] if present else []}}


def write(present):
    get_lineage_data_store().put_lineage_definition(LID, definition(present),
        {'schema': 'tfi.lineage-runtime.v1', 'lineage_id': LID})


def run(monkeypatch):
    from management_portal.society_service import lineage_instruction_context
    from types import SimpleNamespace
    monkeypatch.setattr(lineage_instruction_context, 'get_portal_access_store', lambda: SimpleNamespace(
        is_configured=lambda _: True, is_active=lambda _: True))
    event = lineage_primitives.LineageEvent(id='event_old_snapshot', type='manual.triggered', payload={},
        identity=OperationIdentity.for_actor('CASE-A').to_dict())
    invocation = lineage_primitives.EffectInvocation(id='invocation_old_snapshot', activation=event, arguments={})
    return execution._EffectRun(definition(True), {'effects': {EFFECT: {'effect_id': EFFECT, 'script': 'Ping'}},
        'event_sources': {}}, EFFECT, invocation, 'CASE-A', host=object())


@pytest.mark.parametrize('existing', [False, True])
def test_late_delivery_is_discarded_without_instruction_or_failure_notice(monkeypatch, existing):
    write(True)
    task = run(monkeypatch)
    if existing:
        task._begin()
        journal.cancel_removed_effects(LID, {EFFECT}, reason='Effect node deleted', occurred_at='2026-11-06T01:53:22Z')
    write(False)
    from management_portal.hatg import routing
    monkeypatch.setattr(routing, 'execute', lambda *a, **k: pytest.fail('deleted Effect sent ISA'))
    monkeypatch.setattr(execution, '_dispatch_effect_watchers', lambda *a, **k: pytest.fail('cancellation emitted failure'))
    assert task.execute()['status'] == 'discarded'
    assert not get_lineage_data_store().effect_work.list(LID)


def test_begin_waits_for_deletion_commit_before_checking_membership(monkeypatch):
    write(True)
    task = run(monkeypatch)
    store = get_lineage_data_store()
    entered = threading.Event()
    original = store.lock_lineage_definition
    owner = threading.get_ident()
    def lock(identifier):
        if threading.get_ident() != owner:
            entered.set()
        return original(identifier)
    monkeypatch.setattr(store, 'lock_lineage_definition', lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.database.transaction():
            original(LID)
            write(False)
            future = pool.submit(task._begin)
            assert entered.wait(5)
            assert not future.done()
        future.result(timeout=5)
    assert task.journal['status'] == 'cancelled'
    assert not store.effect_work.list(LID)
