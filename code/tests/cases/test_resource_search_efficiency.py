"""Request-local selection reuse must preserve live projection and authorization."""
from unittest.mock import Mock

from management_portal.resources import query as search
from management_portal.resources.contracts import ResourceQuery


def record(identifier='skill_0123456789ab', title='needle'):
    return {'resource_id': identifier, 'resource_type': 'skill', 'title': title,
            'summary': '', 'attributes': {'status': 'active'}, 'facets': []}


def test_selection_scores_once_and_ranking_keeps_order_total_and_offset(monkeypatch):
    rows = [record(), record('skill_1123456789ab', 'needle suffix'),
            record('skill_2123456789ab', 'absent')]
    query = ResourceQuery(q='needle', actor='reader', limit=1, offset=1)
    policy = Mock()
    policy.readable_ids.return_value = {r['resource_id'] for r in rows}
    expected = search.query_records(rows, query, access_policy=policy)
    original = search._score
    scored = Mock(wraps=original)
    monkeypatch.setattr(search, '_score', scored)
    scores = {}
    selected = search.filter_query_candidates(rows, query, scores=scores)
    assert scored.call_count == 3
    # No repeated actor-independent filter or scoring during ranking.
    monkeypatch.setattr(search, '_matches_query', Mock(side_effect=AssertionError('duplicate filter')))
    actual = search.query_records(selected, query, access_policy=policy, _filtered_scores=scores)
    assert actual == expected
    assert scored.call_count == 3


def test_projection_is_rescored_and_permission_is_not_cached(monkeypatch):
    rows = [record(), record('skill_1123456789ab', 'needle suffix')]
    query = ResourceQuery(q='needle', actor='reader')
    early = search.prefilter_query_candidates(rows, query)
    projected = [{**early[0], 'title': 'no match'},
                 {**early[1], 'title': 'needle'}]
    scores = {}
    selected = search.filter_query_candidates(projected, query, scores=scores)
    assert selected == [projected[1]]
    policy = Mock()
    policy.readable_ids.return_value = {projected[1]['resource_id']}
    result = search.query_records(selected, query, access_policy=policy, _filtered_scores=scores)
    assert result['resources'][0]['score'] == 1000
    policy.readable_ids.return_value = set()
    assert search.query_records(selected, query, access_policy=policy, _filtered_scores=scores)['total'] == 0
    assert policy.readable_ids.call_count == 2


def test_store_keeps_page_permission_revalidation(monkeypatch):
    from management_portal import resource_index_store
    page = [record()]
    checker = Mock(side_effect=[[True], [False]])
    monkeypatch.setattr(resource_index_store, 'readable_flags', checker)
    store = object.__new__(resource_index_store.IndexStore)
    assert store._with_current_capabilities(page, 'reader')[0]['capabilities']['read']
    assert not store._with_current_capabilities(page, 'reader')[0]['capabilities']['read']
    assert checker.call_count == 2
