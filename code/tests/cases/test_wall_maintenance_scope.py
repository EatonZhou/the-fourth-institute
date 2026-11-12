"""WALL 周期维护只读取活动工作与到期清理，调度认领只更新当前工程。

:module: workflow.wall
:design: decision_06defc47e960
:risk: no-external-side-effects
"""
from copy import deepcopy

import pytest

from management_portal import wall_backend as wall, wall_store


@pytest.fixture
def history():
    state = {'scheduling_enabled': True, 'projects': {
        'sample': {'id': 'sample', 'automation': {}, 'schedules': {
            'verify': {'enabled': True, 'actor': 'fixture', 'interval_minutes': 30}}}},
        'jobs': {f'done-{i}': {'id': f'done-{i}', 'project': 'sample', 'status': 'completed',
                              'message': 'historical-body' * 20} for i in range(240)},
        'resident_cleanup': {
            'due': {'project': 'sample', 'actor': 'fixture', 'retry_at': 10},
            'later': {'project': 'later', 'actor': 'fixture', 'retry_at': 900},
            'unset': {'project': 'unset', 'actor': 'fixture'}},
        'discussions': {'unused': {'resident_id': 'resident_111111111111'}}}
    for status in wall.ACTIVE:
        state['jobs'][status] = {'id': status, 'project': 'sample', 'status': status}
    wall_store.WallState().replace(state)
    return state


def test_tick_does_not_materialize_terminal_jobs_or_reading_history(history, monkeypatch, tmp_path):
    """有大量历史时维护不走整状态读取；活动恢复、到期清理和调度仍执行。"""
    monkeypatch.setattr(wall, 'WALL_LOCK_ROOT', tmp_path)
    monkeypatch.setattr(wall.time, 'time', lambda: 6000)
    # Future cleanup stays future relative to this controlled tick.
    with wall_store.database().transaction() as c:
        c.execute("UPDATE wall_resident_cleanup SET retry_at=to_timestamp(9000) WHERE cleanup_key='later'")
    calls, cleanups, actions = [], [], []
    monkeypatch.setattr(wall, '_start_job', calls.append)
    monkeypatch.setattr(wall, '_try_resident_cleanup', lambda key, actor: cleanups.append((key, actor)))
    monkeypatch.setattr(wall, 'action', lambda payload, actor: actions.append((payload, actor)))
    monkeypatch.setattr(wall, '_read_state', lambda: pytest.fail('maintenance loaded all history'))
    wall._tick()
    assert set(calls) == wall.ACTIVE
    assert set(cleanups) == {('sample', 'fixture'), ('unset', 'fixture')}
    assert actions == [({'project': 'sample', 'action': 'verify', 'backend_route_id': ''}, 'fixture')]
    wall._tick()
    assert len(actions) == 1


def test_maintenance_projection_filters_in_sql_and_keeps_job_bodies_unread(history, monkeypatch):
    """数据库先筛选状态且只取任务ID，不把历史正文、讨论或用户读状态送入Python。"""
    from contextlib import contextmanager
    db = wall_store.database()
    original = db.read
    rows, statements = [], []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            statements.append(sql)
            cursor = self.connection.execute(sql, params)
            if 'FROM wall_jobs' in sql:
                result = list(cursor)
                rows.extend(result)
                return result
            return cursor

    @contextmanager
    def read():
        with original() as c:
            yield Connection(c)

    monkeypatch.setattr(db, 'read', read)
    snapshot = wall_store.WallState().read_maintenance(wall.ACTIVE, now=100)
    assert set(snapshot['job_ids']) == wall.ACTIVE
    assert len(rows) == len(wall.ACTIVE)
    assert all(set(dict(row)) == {'id'} for row in rows)
    assert not any(any(table in sql for table in ('wall_reads', 'wall_readers', 'wall_reviews', 'wall_discussions')) for sql in statements)
    assert snapshot['cleanup'] == {('sample', 'fixture'), ('unset', 'fixture')}
    wall_store.WallState().set_scheduling_enabled(False)
    disabled = wall_store.WallState().read_maintenance(wall.ACTIVE, now=100)
    assert not disabled['scheduling_enabled'] and not disabled['projects']
    assert set(disabled['job_ids']) == wall.ACTIVE
    assert disabled['cleanup'] == snapshot['cleanup']


def test_schedule_claim_respects_latest_switch_deletion_and_due_time(history):
    """认领在原WALL事务锁内复核当前设置，重复或已撤销计划不产生第二次调度。"""
    store = wall_store.WallState()
    before = deepcopy(store.read())
    store.set_scheduling_enabled(False)
    assert not store.claim_schedule('sample', 'verify', now=6000)
    store.set_scheduling_enabled(True)
    assert store.claim_schedule('sample', 'verify', now=6000)
    assert not store.claim_schedule('sample', 'verify', now=6001)
    after = store.read()
    assert after['jobs'] == before['jobs']
    assert after['discussions'] == before['discussions']
    assert after['projects']['sample']['schedules']['verify']['last_run'] == 6000
    with wall_store.database().transaction() as c:
        c.execute("UPDATE wall_projects SET deleting=TRUE WHERE id='sample'")
    assert not store.claim_schedule('sample', 'verify', now=12000)
    assert not store.claim_schedule('missing', 'verify', now=12000)
