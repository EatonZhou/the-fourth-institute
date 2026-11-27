import pytest

from management_common import portal_release_events as facts
from management_portal.society_service import lineage_domain, lineage_release_events as events
from management_portal.society_service import (
    lineage_program, lineage_program_compilation, lineage_program_contracts as contracts,
)
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR, WORK_A, _worker  # noqa: F401
from tests.cases import society_service_test_support as support


def publication(start=100):
    return {'ok': True, 'status': 'completed', 'commit': 'a' * 40, 'active_commit': 'a' * 40,
            'started_at': start, 'completed_at': start + 1, 'parent_commit': 'b' * 40, 'origin': 'direct'}


def test_only_completed_active_publications_are_facts():
    """只有成功完成并已生效的发布才记为发布事实"""
    for changes in ({'ok': False}, {'status': 'failed'}, {'active_commit': 'b' * 40}):
        with pytest.raises(ValueError):
            facts.record({**publication(), **changes})
    assert facts.latest_sequence() == 0


def test_append_is_idempotent_but_same_commit_republication_is_distinct():
    """同一次发布重复记录是幂等的，同一提交的再次发布记为新的发布事实"""
    one = facts.record(publication())
    assert facts.record(publication()) == one
    two = facts.record(publication(200))
    assert two > one
    assert [r['sequence'] for r in facts.after(0)] == [one, two]
    records = facts.after(0)
    assert records[0]['publication_id'] != records[1]['publication_id']
    assert records[0]['previous_commit'] == 'b' * 40
    assert records[0]['source'] == 'direct'
    assert facts.after(two) == []


def test_reconciliation_preserves_each_publication_and_recovers_cursor(primitive_store, monkeypatch):
    """发布事件对账为每次发布各发一个事件，重建读取器后从持久化游标继续"""
    support.install_registered_lineage_members(monkeypatch)
    facts.record(publication(50))
    current = support.create_lineage_with_resident_members({
        'title': 'release facts', 'nodes': [_worker(WORK_A)],
        'program': {'components': [{'id': 'event_source_release', 'kind': 'event_source',
                                   'config': {'event_type': events.EVENT}}], 'wires': []}}, ACTOR)
    lineage_id = current['lineage_id']
    def reconcile():
        return lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    initial = reconcile()
    assert initial['events'] == [] and initial['next_wake_at']
    facts.record(publication(100))
    facts.record(publication(200))
    emitted = [item['event'] for item in reconcile()['events']]
    assert len(emitted) == 2
    assert [item['payload']['commit'] for item in emitted] == ['a' * 40] * 2
    assert emitted[0]['payload']['previous_commit'] == 'b' * 40
    assert emitted[0]['payload']['source'] == 'direct'
    assert emitted[0]['payload']['publication_id']
    assert emitted[0]['id'] != emitted[1]['id']
    # Reconstructing readers does not lose the persisted subscriber cursor.
    facts._domain.cache_clear()
    assert reconcile()['events'] == []
    facts.record(publication(300))
    assert len(reconcile()['events']) == 1


def test_failed_acceptance_retries_identical_event(primitive_store, monkeypatch):
    """发布事件接收失败后重试时，事件 ID 和游标与首次相同"""
    support.install_registered_lineage_members(monkeypatch)
    current = support.create_lineage_with_resident_members({
        'title': 'release retry', 'nodes': [_worker(WORK_A)],
        'program': {'components': [{'id': 'event_source_release', 'kind': 'event_source',
                                   'config': {'event_type': events.EVENT}}], 'wires': []}}, ACTOR)
    lineage_id = current['lineage_id']
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    facts.record(publication())
    plan = {'event_sources': {'event_source_release': {'event_type': events.EVENT}}}
    batch = events.prefetch(lineage_id, plan)['event_source_release']
    first = events.pending('event_source_release', plan['event_sources']['event_source_release'], batch, lambda _: 'c')
    retried = events.pending('event_source_release', plan['event_sources']['event_source_release'],
                             events.prefetch(lineage_id, plan)['event_source_release'], lambda _: 'c')
    assert first[0]['event'].id == retried[0]['event'].id
    assert first[0]['cursor'] == retried[0]['cursor']


def test_coalesced_backlog_picks_latest_beyond_one_page_and_keeps_cursor(primitive_store, monkeypatch):
    """coalesce_latest 事件源在积压超过一页时只发出最新发布，并保存游标"""
    support.install_registered_lineage_members(monkeypatch)
    current = support.create_lineage_with_resident_members({
        'title': 'release backlog', 'nodes': [_worker(WORK_A)],
        'program': {'components': [{'id': 'event_source_release', 'kind': 'event_source',
            'config': {'event_type': events.EVENT, 'misfire_policy': 'coalesce_latest'}}],
            'wires': []}}, ACTOR)
    lineage_id = current['lineage_id']
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)['events'] == []
    for start in range(101):
        latest = facts.record(publication(start))
    emitted = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)['events']
    assert len(emitted) == 1
    assert emitted[0]['event']['payload']['sequence'] == latest
    facts._domain.cache_clear()
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)['events'] == []
    newest = facts.record(publication(300))
    emitted = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)['events']
    assert [item['event']['payload']['sequence'] for item in emitted] == [newest]


def test_publication_policy_changes_fence_delivery_without_resetting_progress():
    """修改发布事件源的 misfire_policy 会更换投递指纹，不重置事件源进度"""
    def definition(policy=None):
        config = {'event_type': events.EVENT}
        if policy is not None:
            config['misfire_policy'] = policy
        return {'nodes': [], 'program': lineage_program.normalize_program({
            'components': [{'id': 'event_source_release', 'kind': 'event_source', 'config': config}],
            'wires': []}, lineage_nodes=[])}
    before = definition()
    assert 'misfire_policy' not in before['program']['components'][0]['config']
    for policy in ['all', 'coalesce_latest']:
        after = definition(policy)
        assert contracts._program_execution_contract_change(before, after)['event_source_ids'] == set()
        old_plan = lineage_program_compilation.compile_program(before['program'], [])
        new_plan = lineage_program_compilation.compile_program(after['program'], [])
        old = old_plan['event_sources']['event_source_release']
        new = new_plan['event_sources']['event_source_release']
        assert new['misfire_policy'] == policy
        assert contracts._program_event_source_contract_fingerprint(old) != contracts._program_event_source_contract_fingerprint(new)
    with pytest.raises(ValueError, match='misfire_policy'):
        definition('skip')


@pytest.fixture
def validation_lineage(primitive_store, monkeypatch):
    from management_common import provider_validation_timeouts as timeouts
    support.install_registered_lineage_members(monkeypatch)
    current = support.create_lineage_with_resident_members({
        'title': 'provider acceptance', 'nodes': [_worker(WORK_A)],
        'program': {'components': [{'id': 'event_source_timeout', 'kind': 'event_source',
                                   'config': {'event_type': timeouts.EVENT}}], 'wires': []}}, ACTOR)
    return current['lineage_id']


def test_validation_deadline_survives_worker_loss_and_late_completion(validation_lineage, monkeypatch):
    import os
    import select
    import subprocess
    import sys

    from management_common import provider_validation_timeouts as timeouts
    from management_portal.society_service import changes

    lineage = validation_lineage

    def reconcile():
        return lineage_domain.reconcile_lineage_system_events(lineage, ACTOR)

    if os.name != 'posix':
        pytest.skip('requires POSIX process suspension')
    assert not reconcile()['next_wake_at']
    calls = []
    monkeypatch.setattr(changes, 'lineage_changed', lambda key, *, runtime: calls.append((key, runtime)))
    attempts = {name: lineage_domain.register_provider_validation('kimi', name, 90)
                for name in ('paused', 'exited', 'finished', 'late')}
    assert calls == [(lineage, True)] * 4
    initial = reconcile()
    assert initial['next_wake_at'] and not initial['events']
    timeouts.finish_validation(attempts['finished'])

    # Pause a separate worker after it records its stage, and let a second one exit abruptly.
    worker = """
import os, signal, sys
from management_common.provider_validation_timeouts import set_validation_stage
set_validation_stage(sys.argv[1], 'candidate')
print('ready', flush=True)
if sys.argv[2] == 'paused':
    os.kill(os.getpid(), signal.SIGSTOP)
os._exit(17)
"""
    paused = subprocess.Popen([sys.executable, '-c', worker, attempts['paused'], 'paused'],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([paused.stdout], [], [], 10)[0] and paused.stdout.readline().strip() == 'ready'
        assert os.WIFSTOPPED(os.waitpid(paused.pid, os.WUNTRACED)[1])
        exited = subprocess.run([sys.executable, '-c', worker, attempts['exited'], 'exited'],
                                capture_output=True, timeout=10)
        assert exited.returncode == 17 and exited.stdout.strip() == b'ready'
        timeouts.set_validation_stage(attempts['late'], 'baseline')
        # Advance only the isolated database deadlines. Recreate the observer after expiry.
        with timeouts.domain().transaction("append") as connection:
            connection.execute("UPDATE pending_checks SET due_at = clock_timestamp() - interval '1 second' "
                               "WHERE attempt_id = ANY(%s::uuid[])", (list(attempts.values()),))
        timeouts.set_validation_stage(attempts['late'], 'candidate')
        timeouts.finish_validation(attempts['late'])
        timeouts._domain.cache_clear()
        result = reconcile()
        payloads = [item['event']['payload'] for item in result['events']]
        assert sorted((p['candidate'], p['stage'], p['limit_seconds']) for p in payloads) == [
            ('exited', 'candidate', 90), ('late', 'baseline', 90), ('paused', 'candidate', 90)]
        assert not result['next_wake_at'] and not reconcile()['events']
        assert paused.poll() is None
    finally:
        paused.kill()
        paused.wait(timeout=5)

def test_external_scheduler_recovers_and_fires_registered_deadline(validation_lineage):
    from threading import Event
    from management_portal.society_service.coordinator import LineageCoordinator

    alarm = Event()
    payloads = []
    lineage_domain.register_provider_validation('codex', 'recovery-test', 1)

    def reconcile(lineage_id):
        result = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
        for item in result['events']:
            payloads.append(item['event']['payload'])
            alarm.set()
        return result

    def recover():
        result = reconcile(validation_lineage)
        return {'wake_lineages': {validation_lineage: result['next_wake_at']}}

    coordinator = LineageCoordinator(resume_residents=dict, resume_resident=lambda _: {},
                                     reconcile_all=recover, reconcile_lineage=reconcile)
    coordinator.start()
    try:
        assert alarm.wait(10), coordinator.snapshot().get('last_error')
        assert [p['candidate'] for p in payloads] == ['recovery-test']
    finally:
        coordinator.stop()
