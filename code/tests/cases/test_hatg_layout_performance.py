"""Layout instructions read only their required state, retaining current-row checks."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from queue import Queue
import time

import pytest

from management_portal import research_map_store as maps
from management_portal.society_service import lineage_aggregate, lineage_lifecycle
from tests.cases.test_lineage_state_domains import lineage_store, _create, ACTOR, FIRST


def test_lineage_layout_skips_runtime_and_second_graph_copy(lineage_store, monkeypatch, record_property):
    created = _create()
    calls = []
    original = lineage_aggregate.read_lineage_state
    def full_read(lineage_id):
        calls.append(lineage_id)
        return original(lineage_id)
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', full_read)
    # Existing readers retain the complete default; the layout call is narrow.
    assert lineage_aggregate.read_lineage(created['lineage_id'], actor=ACTOR)['lineage_id'] == created['lineage_id']
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(lineage_lifecycle, '_clone_json', lambda *args: pytest.fail('layout copied the entire graph'))
    result = lineage_lifecycle.update_lineage_layout(created['lineage_id'], {'positions':[
        {'object_id': FIRST, 'x': 71, 'y': 72},
        {'object_id': 'resident_ffffffffffff', 'x': 99, 'y': 99},
        {'object_id': FIRST, 'x': 81, 'y': 82},
    ]}, ACTOR)
    assert calls == []
    assert result['applied'] == [{'object_id':FIRST,'object_kind':'resident','x':81.0,'y':82.0}]
    assert result['missing_object_ids'] == ['resident_ffffffffffff']
    stored = lineage_aggregate.lineage_catalog_store().get(created['lineage_id'])
    assert stored['revision'] == created['revision']
    assert stored['layout_revision'] == created['layout_revision'] + 1
    assert next(row for row in stored['nodes'] if row['id']==FIRST)['x'] == 81
    record_property('layout_runtime_reads', 0)
    record_property('legacy_full_view_runtime_reads', 1)
    record_property('layout_second_whole_graph_copies', 0)


def test_lineage_layout_keeps_management_gate(lineage_store, monkeypatch):
    created = _create()
    monkeypatch.setattr(lineage_aggregate, '_actor_can_manage_lineage', lambda *args: False)
    with pytest.raises(PermissionError):
        lineage_lifecycle.update_lineage_layout(created['lineage_id'], {'positions':[
            {'object_id':FIRST,'x':90,'y':91}]}, 'not_a_manager')
    stored = lineage_aggregate.lineage_catalog_store().get(created['lineage_id'])
    assert stored['layout_revision'] == created['layout_revision']


def _map(project, count=2, prose=''):
    maps.read(project)
    result = maps.apply(project, 0, [{'op':'add_direction','key':f'd{i}','title':f'Direction {i}',
        'question':prose,'approach':prose,'expectation':prose} for i in range(count)],
        actor='alice', resident=False)
    return list(result['created'].values())


def test_research_layout_projects_ids_in_one_locked_read(monkeypatch, record_property):
    project = 'LayoutPayload'
    ids = _map(project, 100, 'q'*4000)
    db = maps.database()
    with db.read() as connection:
        legacy = maps._map_row(connection, project)
    reads = []
    transaction = db.transaction
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def fetchone(self):
            value = self.cursor.fetchone()
            reads.append(dict(value))
            return value
    class Connection:
        def __init__(self, connection): self.connection = connection
        def execute(self, query, *args, **kwargs):
            cursor = self.connection.execute(query, *args, **kwargs)
            if query.startswith('SELECT layout, layout_revision, '):
                assert query.endswith('FOR UPDATE')
                return Cursor(cursor)
            return cursor
    @contextmanager
    def observed(*args, **kwargs):
        with transaction(*args, **kwargs) as connection:
            yield Connection(connection)
    monkeypatch.setattr(db, 'transaction', observed)
    saved = maps.update_layout(project, {ids[0]:{'x':12.75,'y':-2.25}}, actor='alice')
    assert saved['layout'] == {ids[0]:{'x':12.8,'y':-2.2}}
    assert saved['layout_revision'] == 1
    assert len(reads) == 1
    assert set(reads[0]) == {'layout','layout_revision','direction_ids'}
    assert set(reads[0]['direction_ids']) == set(ids)
    before = len(json.dumps(legacy, default=str).encode())
    after = len(json.dumps(reads[0], default=str).encode())
    assert before > after*100
    record_property('legacy_materialized_row_utf8_bytes', before)
    record_property('layout_materialized_row_utf8_bytes', after)
    record_property('layout_selects_per_call', 1)


def test_research_layout_keeps_revision_null_and_atomic_validation():
    project='LayoutValidation'
    first,second=_map(project)
    initial=maps.update_layout(project,{first:{'x':1,'y':2}},actor='alice')
    # Same coordinates are still a successful layout write with its own revision.
    repeated=maps.update_layout(project,{first:{'x':1,'y':2}},actor='alice')
    assert repeated['layout_revision']==initial['layout_revision']+1
    with pytest.raises(ValueError):
        maps.update_layout(project,{first:{'x':9,'y':10},second:{'x':True,'y':2}},actor='alice')
    current=maps.read(project)
    assert current['layout']==repeated['layout'] and current['layout_revision']==repeated['layout_revision']
    dropped=maps.update_layout(project,{first:None},actor='alice')
    assert dropped['layout']=={} and dropped['layout_revision']==3
    assert maps.read(project)['revision']==1


def test_research_layout_rechecks_ids_after_waiting_for_map_lock():
    from psycopg.types.json import Jsonb
    project='LayoutConcurrentRemoval'
    direction=_map(project,1)[0]
    db=maps.database()
    started=Queue()
    def change_layout():
        with db.transaction():
            with db.read() as connection:
                connection.execute("SET LOCAL statement_timeout='5s'")
                started.put(connection.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
            return maps.update_layout(project,{direction:{'x':4,'y':5}},actor='alice')
    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.transaction() as connection:
            connection.execute('SELECT project_id FROM maps WHERE project_id=%s FOR UPDATE',(project,)).fetchone()
            future=executor.submit(change_layout)
            pid=started.get(timeout=3)
            deadline=time.monotonic()+3
            while not connection.execute('SELECT pg_blocking_pids(%s) AS blockers',(pid,)).fetchone()['blockers']:
                if time.monotonic()>deadline:raise AssertionError('layout did not wait for map row')
                time.sleep(0.01)
            connection.execute('UPDATE maps SET document=%s,revision=revision+1 WHERE project_id=%s',
                               (Jsonb(maps.empty_document()),project))
        with pytest.raises(ValueError, match='不是这张地图上的方向'):
            future.result(timeout=6)
    saved=maps.read(project)
    assert saved['layout']=={} and saved['layout_revision']==0
