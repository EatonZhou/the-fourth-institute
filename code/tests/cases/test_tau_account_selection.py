"""TAU rotation resolves public account names before changing an assignment.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from types import SimpleNamespace

import pytest

from management_portal.execution.account_adapters import CodexRoutes
from management_portal.test_framework.service import TestFrameworkService as FrameworkService


def test_rotation_uses_the_same_account_identity_as_campaign_planning(monkeypatch):
    """An accepted account alias cannot prevent subsequent Campaign dispatch.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    rotations = []
    monkeypatch.setattr(CodexRoutes, 'get', lambda self, name: SimpleNamespace(account_id='six'))
    service = FrameworkService.__new__(FrameworkService)
    service.tau_residents = SimpleNamespace(rotate=lambda module, **options: rotations.append((module, options)))
    service.rotate_tau_resident('workflow.repository', actor='controlled-user', backend_route_id='codex_six')
    assert rotations == [('workflow.repository', {'changed_by': 'controlled-user', 'backend_route_id': 'six'})]


def test_invalid_rotation_account_does_not_release_the_existing_resident(monkeypatch):
    """Resolve an invalid account before the destructive slot replacement.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    def missing(self, name):
        raise KeyError(name)
    monkeypatch.setattr(CodexRoutes, 'get', missing)
    service = FrameworkService.__new__(FrameworkService)
    service.tau_residents = SimpleNamespace(rotate=lambda *args, **kwargs: pytest.fail('existing slot released'))
    with pytest.raises(KeyError):
        service.rotate_tau_resident('workflow.repository', actor='controlled-user', backend_route_id='missing')
