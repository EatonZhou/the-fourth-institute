"""Facade quota controls identify exact live owners without executing work.

:module: resident.conversation
:design: decision_44cf587c1721, decision_ccbb1cac24aa
"""
from types import SimpleNamespace
import json
import pytest

from management_portal.society_service import family_quota_projection as projection
from management_portal.society_service import resident_protocol
from tests.cases.resident_execution_test_support import resident_record, input_delivery  # noqa: F401


@pytest.fixture
def view(monkeypatch):
    records = {name: {'resident_id': name, 'lineage_id': 'lineage', 'title': name + ' name',
                     'family': {'role': 'facade' if name in {'facade', 'inner'} else name if name in {'router', 'steward'} else 'member'}}
               for name in ('facade', 'inner', 'worker', 'other', 'router', 'steward')}
    states, links, tasks, reviews, gates = {}, {}, [], {}, {}
    hidden, denied, queried = set(), set(), []
    repository = SimpleNamespace(
        input_states=lambda rid, states: [dict(value, message_id=mid) for (target, mid), value in entries.items()
                                         if target == rid and value.get('state') in states],
        input_states_by_id=lambda pairs: {key: states[key] for key in pairs if key in states},
    )
    entries = states
    repository.input_statuses = repository.input_states
    repository.input_statuses_by_id = repository.input_states_by_id
    monkeypatch.setattr(projection.resident_conversation_stream, 'get_repository', lambda: repository)
    monkeypatch.setattr(projection.resident_mailbox, '_groups_of', lambda *args: {})
    monkeypatch.setattr(projection.resident_record_domain, 'find_resident', records.get)
    monkeypatch.setattr(projection.resident_record_domain, 'resident_intake_state', lambda record: 'open')
    monkeypatch.setattr(projection.family_facade, 'relay_links', lambda record: links.get(record['resident_id'], {}))
    monkeypatch.setattr(projection.family_task_dispatches, 'request_tasks', lambda record, mids: [task for task in tasks
                        if task['facade'] == record['resident_id'] and task['facts']['message_id'] in mids])
    monkeypatch.setattr(projection, 'get_lineage_data_store', lambda: SimpleNamespace(family_quota_reviews=SimpleNamespace(by_keys=lambda ids: reviews)))
    monkeypatch.setattr(projection.resident_access, 'resident_visible_to_actor', lambda record, actor: record['resident_id'] not in hidden)
    monkeypatch.setattr(projection.resident_access, 'resident_allows', lambda record, actor: {'RestartSession': record['resident_id'] not in denied})
    monkeypatch.setattr(projection.family_quota, 'auto_migration_enabled', lambda record: False)
    def project(snapshots, *, resident_records):
        queried.append(set(snapshots))
        return {rid: {'state': {'execution_gate': gates.get(rid, {'state': 'closed', 'reason': 'quota_used_up'})}}
                for rid in snapshots}
    monkeypatch.setattr(projection.resident_execution_gate, 'project_states', project)
    def link(source, mid, target, dest, state='queued'):
        links.setdefault(source, {})[mid] = {'resident_id': target, 'message_id': dest}
        states[(source, mid)] = {'state': 'completed'}
        states[(target, dest)] = {'state': state}
    def task(target, message, original, state='queued'):
        states[('facade', original)] = {'state': 'queued'}
        states[(target, message)] = {'state': state}
        tasks.append({'facade': 'facade', 'target': target, 'message_id': message, 'facts': {'message_id': original}})
    return SimpleNamespace(**locals(), read=lambda: projection.quota_switch(records['facade'], 'actor')['targets'])


def test_exact_linked_owners_multiple_requests_and_permissions(view):
    view.link('facade', 'a', 'worker', 'm1')
    view.link('facade', 'b', 'worker', 'm2')
    view.link('facade', 'c', 'other', 'm3')
    view.denied.add('other')
    result = {row['target_resident_id']: row for row in view.read()}
    assert set(result) == {'worker', 'other'}
    assert result['worker']['request_message_ids'] == ['a', 'b']
    assert result['worker']['target_title'] == 'worker name'
    assert result['worker']['target_role_label'] == '成员'
    assert result['worker']['can_manage'] is True
    assert result['other']['can_manage'] is False
    assert result['worker']['reason'] == 'auto_switch_disabled'
    assert view.queried == [{'worker', 'other'}]


@pytest.mark.parametrize('terminal', ['completed', 'cancelled'])
def test_finished_worker_is_not_a_blocker(view, terminal):
    view.link('facade', 'a', 'worker', 'm', terminal)
    assert view.read() == []


def test_finished_history_does_not_load_group_or_reply_details(view, monkeypatch):
    for i in range(500):
        view.link('facade', f'old-{i}', 'worker', f'finished-{i}', 'completed')
    view.link('facade', 'current', 'worker', 'unfinished')
    grouped = []
    original = projection.resident_mailbox.InputProjection
    def selected(pairs, repository):
        grouped.extend(pairs)
        return original(pairs, repository)
    monkeypatch.setattr(projection.resident_mailbox, 'InputProjection', selected)
    result = view.read()
    assert result[0]['request_message_ids'] == ['current']
    assert grouped == [('worker', 'unfinished')]


def test_status_reads_do_not_hydrate_replies_or_work(input_delivery, monkeypatch):
    repository = projection.resident_conversation_stream.get_repository()
    rid = input_delivery['resident_id']
    repository.set_input_state(rid, 'status-read', 'completed')
    def forbidden(*args):
        raise AssertionError('status-only reads must not hydrate replies/work')
    monkeypatch.setattr(repository.inputs, '_fill_replies_many', forbidden)
    monkeypatch.setattr(repository.inputs, '_fill_work_many', forbidden)
    assert repository.input_statuses_by_id([(rid, 'status-read')])[(rid, 'status-read')]['state'] == 'completed'
    assert any(row['message_id'] == 'status-read' for row in repository.input_statuses(rid, states=('completed',)))


def test_cancelled_source_hidden_member_and_nonquota_do_not_offer_switch(view):
    view.link('facade', 'a', 'worker', 'm')
    view.states[('facade', 'a')] = {'state': 'cancelled'}
    view.link('facade', 'b', 'other', 'o')
    view.hidden.add('other')
    assert view.read() == []
    view.hidden.clear()
    for gate in ({'state': 'unknown', 'reason': 'quota_unknown'}, {'state': 'closed', 'reason': 'manual'}, {'state': 'open'}):
        view.gates['other'] = gate
        assert view.read() == []


def test_role_tasks_need_matching_original_and_accepted_unfinished_input(view):
    view.task('router', 'r1', 'a')
    view.task('steward', 's1', 'b')
    view.task('other', 'not-accepted', 'c')
    del view.states[('other', 'not-accepted')]
    view.task('worker', 'done', 'd', 'completed')
    result = {row['target_resident_id']: row for row in view.read()}
    assert set(result) == {'router', 'steward'}
    assert result['router']['target_role'] == 'router'
    assert result['router']['request_message_ids'] == ['a']
    view.states[('facade', 'a')] = {'state': 'cancelled'}
    assert [row['target_resident_id'] for row in view.read()] == ['steward']


def test_nested_facade_uses_real_worker_and_preserves_outer_request(view):
    view.link('facade', 'a', 'inner', 'inner-message', 'completed')
    view.link('inner', 'inner-message', 'worker', 'work')
    assert view.read()[0]['target_resident_id'] == 'worker'
    assert view.read()[0]['request_message_ids'] == ['a']
    assert all('inner' not in batch and 'facade' not in batch for batch in view.queried)
    view.links['inner']['inner-message'] = {'resident_id': 'facade', 'message_id': 'a'}
    assert view.read() == []  # malformed cycle cannot invent a target


def test_admission_review_only_current_selection_and_no_random_role(view):
    view.states[('facade', 'a')] = {'state': 'queued'}
    assert view.read() == []
    view.reviews['facade'] = {'requests': {'a': {'status': 'delivering', 'member_id': 'worker'}}}
    assert view.read()[0]['target_resident_id'] == 'worker'
    view.states[('facade', 'a')] = {'state': 'cancelled'}
    assert view.read() == []


def test_protocol_exposes_facade_targets_and_suppresses_closed_or_forbidden(view, monkeypatch):
    view.link('facade', 'a', 'worker', 'm')
    monkeypatch.setattr(resident_protocol, '_send_problem', lambda record, actor, allowed: (allowed, ''))
    monkeypatch.setattr(resident_protocol.resident_execution_provider, 'provider_for', lambda record: SimpleNamespace(
        capabilities=SimpleNamespace(supports_active_turn_input=True, supports_media=True, local_tools=True)))
    target = SimpleNamespace(record=view.records['facade'])
    read = lambda state, allowed: resident_protocol._conversation_controls(target, 'actor', state, allowed=allowed)['quota_switch']
    assert read({}, True)['targets'][0]['target_resident_id'] == 'worker'
    assert read({'intake_state': 'closed'}, True) == {'targets': []}
    assert read({}, False) == {'targets': []}


def test_task_owner_query_is_scoped_to_facade_and_original(input_delivery):
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.society_service import resident_record_domain
    record = resident_record_domain.find_resident(input_delivery['resident_id'])
    store = get_lineage_data_store()
    with store.database.read() as connection:
        before = connection.execute('SELECT COUNT(*) AS n FROM family_task_dispatches').fetchone()['n']
    def insert(connection):
        for i, (fid, original) in enumerate([(record['resident_id'], 'a'), (record['resident_id'], 'b'), ('elsewhere', 'a')]):
            connection.execute('INSERT INTO family_task_dispatches VALUES (%s,%s,%s)',
                               (record['lineage_id'], 'test-task-' + str(i), json.dumps({'request': {
                                   'facade': fid, 'target': 'router', 'facts': {'message_id': original}}})))
    store.database.write(insert)
    rows = projection.family_task_dispatches.request_tasks(record, ['a'])
    assert [row['message_id'] for row in rows] == ['test-task-0']
    assert projection.family_task_dispatches.request_tasks(record, []) == []
    with store.database.read() as connection:
        assert connection.execute('SELECT COUNT(*) AS n FROM family_task_dispatches').fetchone()['n'] == before + 3
