"""Open access scopes do not initialize the permission engine.

:module: security.identity-access
:design: decision_b820faf42218
"""
from types import SimpleNamespace

from management_portal import permission_system
from management_portal.permission_system import personal_ama
from management_portal.society_service import resident_access_scope


def test_open_scoped_actions_keep_fallback_without_loading_par(monkeypatch):
    monkeypatch.setattr(permission_system, 'configured_mode', lambda: permission_system.OPEN)
    def no_engine():
        raise AssertionError('Open action scopes must not construct PAR')
    monkeypatch.setattr(permission_system, 'active', no_engine)
    resource_id = 'lineage_aaaaaaaaaaaa'
    invocation = SimpleNamespace(
        declaration=SimpleNamespace(name='GetLineage'),
        call=SimpleNamespace(arguments={'lineage_id': resource_id}),
    )
    assert resident_access_scope.action_for(resource_id, '@viewer') == '@viewer'
    with resident_access_scope.instruction_scope(invocation):
        assert resident_access_scope.action_for(resource_id, '@viewer') == '@viewer'
        assert resident_access_scope.action_for_parameter('GetLineage', 'lineage_id') == 'GetLineage'
        assert not personal_ama.reaches('alice', 'bob', resource_id)
        assert personal_ama.hidden('alice', {resource_id: 'bob'}) == {resource_id}
    assert resident_access_scope.action_for(resource_id, '@viewer') == '@viewer'
