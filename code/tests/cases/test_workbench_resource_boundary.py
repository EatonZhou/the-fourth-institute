"""Workbench authorization must still hold when a resource result is returned."""
from types import SimpleNamespace

import pytest

from management_portal.workbench_projections import http


@pytest.mark.parametrize('operation', ['read', 'resolve', 'query', 'resolve_missing'])
def test_resource_result_is_discarded_if_workbench_access_is_revoked(monkeypatch, operation):
    from management_portal import resources

    revoked = False
    checks = []
    address = 'repo_111111111111/file/example.txt'

    def get(app_id, actor):
        checks.append((app_id, actor))
        if revoked:
            raise PermissionError('Workbench access revoked')
        return {}

    def resource_operation(*args, actor, **kwargs):
        nonlocal revoked
        assert actor == 'alice'
        revoked = True
        if operation == 'read':
            assert args == (address,)
            return b'synthetic content', 'text/plain', {}
        if operation == 'query':
            return {'items': [{'title': 'synthetic result'}]}
        assert args == (address,)
        return {'exists': operation != 'resolve_missing', 'title': 'synthetic title'}

    monkeypatch.setattr(http, 'get_service', lambda: SimpleNamespace(store=SimpleNamespace(get=get)))
    monkeypatch.setattr(resources, 'get_resource_service', lambda: SimpleNamespace(
        read_content=resource_operation, resolve=resource_operation, query=resource_operation))
    with pytest.raises(PermissionError, match='Workbench access revoked'):
        http.post('/api/workbenches/resource', {
            'id': 'workbench_111111111111',
            'operation': 'resolve' if operation == 'resolve_missing' else operation,
            'arguments': {} if operation == 'query' else {'address': address},
        }, 'alice')
    assert checks == [('workbench_111111111111', 'alice')] * 2
