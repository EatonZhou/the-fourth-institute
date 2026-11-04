"""Desktop reopening handles an absent PID rejected by OpenProcess.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import os
from unittest.mock import Mock

import pytest

from scripts import tfi_windows_user_services as services


@pytest.fixture
def denied(monkeypatch):
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    monkeypatch.setattr(services, "process_stamp", Mock(side_effect=error))
    return error


def test_absent_pid_denied_by_openprocess_is_not_a_service(monkeypatch, denied):
    monkeypatch.setattr(services, "snapshot_contains_process", Mock(return_value=False))
    assert services.matches_process(4372, 134382152143096691) is False


def test_existing_protected_pid_never_grants_ownership(monkeypatch, denied):
    monkeypatch.setattr(services, "snapshot_contains_process", Mock(return_value=True))
    with pytest.raises(PermissionError) as caught:
        services.matches_process(4372, 134382152143096691)
    assert caught.value is denied


def test_snapshot_failure_keeps_identity_unknown(monkeypatch, denied):
    monkeypatch.setattr(services, "snapshot_contains_process", Mock(side_effect=PermissionError(13, 'snapshot')))
    with pytest.raises(PermissionError) as caught:
        services.matches_process(4372, 134382152143096691)
    assert caught.value is denied


def test_other_observation_errors_propagate(monkeypatch):
    error = PermissionError(13, "unrelated")
    error.winerror = 1314
    monkeypatch.setattr(services, "process_stamp", Mock(side_effect=error))
    snapshot = Mock()
    monkeypatch.setattr(services, "snapshot_contains_process", snapshot)
    with pytest.raises(PermissionError):
        services.matches_process(4372, 1)
    snapshot.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='real Windows stdlib snapshot')
def test_native_snapshot_distinguishes_self_and_absent_pid():
    assert services.snapshot_contains_process(os.getpid()) is True
    assert services.snapshot_contains_process(0xfffffffc) is False
    stamp = services.process_stamp(os.getpid())
    assert services.matches_process(os.getpid(), stamp) is True
    assert services.matches_process(os.getpid(), stamp - 1) is False
