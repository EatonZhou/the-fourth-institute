"""One Test Workbench identity and separate presentation/execution boundaries."""
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal.distribution import resource_dependencies as deps
from management_portal.distribution.features import resolve_features
from tests.cases.test_distribution_resource_dependencies import Source, OTHER

TEST_APP = 'workbench_fbfebc527201'


def source():
    value = Source()
    value.data['workbenches', 'apps'].append({'id': TEST_APP, 'builtin': 'test', 'title': 'Renamed title', 'published': 9})
    value.data['workbenches', 'bindings'].append({'app_id': TEST_APP, 'revision': 9, 'alias': 'main', 'lineage_id': OTHER})
    return value


def test_legacy_test_choice_and_real_projection_resolve_one_identity():
    old = {'test_view': True, 'projections': [{'id': TEST_APP}]}
    before = deepcopy(old)
    result = deps.resolve(old, source())
    assert result['valid'] and len(result['projections']) == 1
    projection = result['projections'][0]
    assert projection['id'] == TEST_APP and projection['builtin'] == 'test'
    assert projection['selection_feature'] == 'test_view'
    assert projection['bindings'] == [{'alias': 'main', 'lineage_id': OTHER}]
    assert projection['required_by'] == ['feature_states.test_view', 'manual.projections']
    assert set(result['features']) == {'test_view', 'test_framework'}
    assert old == before
    for snapshot in [{'test_view': True}, {'projections': [{'id': TEST_APP}]}]:
        resolution = resolve_features(snapshot, source=source())
        assert {'test_view', 'test_framework'} <= set(resolution['included_features'])
    # Canonical state wins over stale legacy true; explicit manual selection remains independent.
    deselected = {'test_view': True, 'feature_states': {'test_view': {'included': False}}}
    assert not deps.resolve(deselected, source())['projections']
    deselected['projections'] = [{'id': TEST_APP}]
    assert resolve_features(deselected, source=source())['states']['test_view']['included']


def test_test_projection_does_not_guess_identity_from_title_or_path():
    value = source()
    value.data['workbenches', 'apps'][-1]['builtin'] = 'some-other-app'
    value.data['workbenches', 'apps'][-1].update(title='Test', path='/test')
    result = deps.resolve({'test_view': True}, value)
    assert not result['valid'] and not result['projections']


def test_existing_frozen_v1_keeps_its_original_scope():
    from management_portal.distribution.store import digest
    snapshot = {'test_view': True}
    frozen = {'schema': 'tfi.release-resource-dependencies.v1',
              'input_sha256': digest({key: snapshot.get(key) for key in ('resource_types', 'document_indexes', 'lineages', 'projections')}),
              'projections': [], 'features': [], 'gaps': [], 'valid': True}
    snapshot['resource_dependencies'] = frozen
    assert deps.frozen_or_resolve(snapshot, source()) == frozen


@pytest.mark.parametrize('with_view', [False, True])
def test_test_view_routes_are_independent_from_framework_and_tau(monkeypatch, with_view):
    from management_common import distribution_features
    from management_portal import portal_features, serve_management_portal as shell
    from management_portal.workbench_projections.store import builtins
    from management_portal.test_framework import web
    selected = {'test_framework', 'tau'} | ({'test_view'} if with_view else set())
    monkeypatch.setattr(distribution_features, 'included_features', lambda: selected)
    portal_features.contributions.cache_clear()
    builtins.cache_clear()
    try:
        replies = []
        monkeypatch.setattr(shell._PORTAL_PAGES_HTTP, 'respond', lambda request, status, body: replies.append((status, body)))
        shell._PORTAL_PAGES_HTTP.development_native_views(None, urlparse('/development/native-views?view=test'), 'viewer')
        assert replies[-1][0] == (200 if with_view else 404)
        assert ('test' in builtins()) is with_view
        routes = {route.path: route for route in web.routes(shell)}
        assert ('/static/test_page.js' in routes) is with_view
        # Presentation omission never weakens the framework API's original gate.
        assert routes['/test/api/run'].group == 'test-actor-post'
        assert routes['/test/api/catalog'].group == 'test-actor-get'
        assert any(feature == 'test_framework' for feature, part in portal_features.contributions('application.targets'))
    finally:
        portal_features.contributions.cache_clear()
        builtins.cache_clear()
