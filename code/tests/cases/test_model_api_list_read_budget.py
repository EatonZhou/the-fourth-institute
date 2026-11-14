"""Model API list route reads are bounded; no live Provider is involved.

:module: execution.providers
"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import model_api_store as store
from management_portal.execution import isa


@pytest.mark.parametrize('count', [0, 1, 32])
def test_api_list_reads_routes_once_and_preserves_public_rows(monkeypatch, count):
    rows = [dict.fromkeys(store.API_FIELDS) | {
        'api_id': f'api_{i}', 'version': i + 1, 'provider': 'deepseek',
        'kind': 'openai_compatible_chat', 'enabled': i % 2 == 0,
    } for i in range(count)]
    bindings = {'api_0': [{'backend': 'dsh', 'route_id': 'deepseek'}]}
    reads, queries = [], []
    @contextmanager
    def read():
        reads.append(1)
        def execute(sql, params=()):
            queries.append(sql)
            if 'FROM apis' in sql:
                return deepcopy(rows)
            if 'ANY' in sql:
                return [{'api_id': identifier, **binding}
                        for identifier in params[0] for binding in bindings.get(identifier, [])]
            return deepcopy(bindings.get(params[0], []))
        yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(store, 'database', lambda: SimpleNamespace(read=read))
    # Original single-item projection remains the comparison contract.
    expected = [isa._public_api(store._decode(row), row['version']) for row in rows]
    reads.clear(); queries.clear()
    result = isa.list_model_apis(None)['apis']
    assert result == expected
    assert len(reads) == (2 if count else 1)
    assert sum('FROM routes' in sql for sql in queries) == (1 if count else 0)
    if count:
        bindings.clear()
        second = isa.list_model_apis(None)['apis']
        assert all('routes' not in item for item in second)  # no cross-request cache
        assert result[0]['routes'] == [{'backend': 'dsh', 'route_id': 'deepseek'}]


def test_route_batch_errors_are_not_hidden(monkeypatch):
    @contextmanager
    def broken_read():
        raise RuntimeError('synthetic read failure')
        yield
    monkeypatch.setattr(store, 'database', lambda: SimpleNamespace(read=broken_read))
    assert store.route_bindings_many([]) == {}
    with pytest.raises(RuntimeError, match='synthetic read failure'):
        store.route_bindings_many(['one'])


def test_route_batch_postgres_matches_single_reads_and_current_bindings():
    first = {'id': 'batch_deepseek', 'provider': 'deepseek', 'kind': 'openai_compatible_chat',
             'routes': [{'backend': 'dsh', 'route_id': 'deepseek'}]}
    second = {'id': 'batch_empty', 'provider': 'deepseek', 'kind': 'openai_compatible_chat', 'enabled': False}
    assert store.create_api(first, 'fixture')
    assert store.create_api(second, 'fixture')
    ids = [first['id'], second['id'], 'missing', first['id']]
    actual = store.route_bindings_many(ids)
    assert actual == {key: store.route_bindings(key) for key in set(ids)}
    assert actual[first['id']] == first['routes']
    assert actual[second['id']] == actual['missing'] == []
    store.update_api(first['id'], {'routes': []}, 'fixture')
    store.update_api(second['id'], {'routes': first['routes']}, 'fixture')
    updated = store.route_bindings_many(ids)
    assert updated[first['id']] == [] and updated[second['id']] == first['routes']
    store.delete_api(second['id'])
    assert store.route_bindings_many([second['id']]) == {second['id']: []}
