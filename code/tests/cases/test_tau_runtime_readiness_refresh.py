"""Provisioning TAU after Web startup updates readiness without losing runs."""
from types import SimpleNamespace
from management_portal.test_framework import api
from management_portal.test_framework.registry import RunnerRegistry


def test_runtime_readiness_recovers_and_revokes_without_recreating_service(monkeypatch):
    service = SimpleNamespace(registry=RunnerRegistry(), runs=object())
    original_runs = service.runs
    monkeypatch.setattr(api, '_SERVICE', service)
    reasons = iter(['', 'browser was removed'])
    monkeypatch.setattr(api.TauConfig, 'from_environment', lambda: SimpleNamespace(unavailable_reason=lambda: next(reasons)))
    assert api.service(refresh_runtime=True) is service
    assert service.registry.runner('tau').available
    assert api.service(refresh_runtime=True) is service
    assert not service.registry.runner('tau').available
    assert service.registry.runner('tau').reason == 'browser was removed'
    assert service.runs is original_runs
