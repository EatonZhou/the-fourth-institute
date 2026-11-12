"""Bound graph-cache retention without changing graph/actor semantics.

:design: decision_3ece26686464
"""
from types import SimpleNamespace

import pytest

from knowledge_board_lan import knowledge_backend as knowledge


@pytest.fixture(params=['item', 'global'])
def graph_cache(request, tmp_path, monkeypatch):
    root = 'idea_0123456789ab'
    path = tmp_path / 'root.md'
    path.write_text('Root')
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(knowledge, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(knowledge, '_ITEM_GRAPH_CACHE', {})
    monkeypatch.setattr(knowledge, '_KNOWLEDGE_GRAPH_CACHE', {})
    monkeypatch.setattr(knowledge, 'get_resource_service', lambda: object())
    record = {'kb_id': root, 'path': str(path), 'title': 'Root', 'type': 'idea'}
    monkeypatch.setattr(knowledge, 'resolve_item_record', lambda *args, **kwargs: record)

    def item_records(actor):
        calls.append(actor)
        return {root: record}

    def global_records(*, actor, **kwargs):
        calls.append(actor)
        return [{**record, '_source_path': str(path)}]

    monkeypatch.setattr(knowledge, '_item_graph_records', item_records)
    monkeypatch.setattr(knowledge.resource_views, 'knowledge_graph_records', global_records)
    if request.param == 'item':
        return (lambda actor: knowledge.build_item_graph(root, actor=actor)), knowledge._ITEM_GRAPH_CACHE, clock, calls
    return (lambda actor: knowledge.build_knowledge_graph(actor=actor)), knowledge._KNOWLEDGE_GRAPH_CACHE, clock, calls


def test_expired_graphs_are_reclaimed_on_other_key_access(graph_cache):
    build, cache, clock, calls = graph_cache
    for index in range(10):
        clock[0] += knowledge._ITEM_GRAPH_CACHE_TTL_SECONDS + 1
        result = build(f'reader{index}')
        assert len(result['nodes']) == 1
    assert len(calls) == 10
    assert len(cache) == 1, 'unvisited expired graph payloads were retained indefinitely'


def test_live_graph_cache_evicts_oldest_and_rebuilds_same_result(graph_cache, monkeypatch):
    build, cache, clock, calls = graph_cache
    # A small retention budget makes the same production eviction boundary observable.
    monkeypatch.setattr(knowledge, '_GRAPH_CACHE_MAX_ENTRIES', 3, raising=False)
    original = build('reader0')
    for index in range(1, 5):
        build(f'reader{index}')
    assert len(cache) == 3, 'distinct callers can grow a live graph cache without bound'
    assert len(calls) == 5
    assert build('reader4') == original
    assert len(calls) == 5
    assert build('reader0') == original
    assert len(calls) == 6 and calls[-1] == 'reader0'
    assert len(cache) == 3
