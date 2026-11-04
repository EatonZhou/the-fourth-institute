"""Reopening a desktop install must not trust or act on stale Windows PIDs.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
from unittest.mock import Mock

import psutil
import pytest

from scripts import tfi_windows_user_services as services


@pytest.fixture
def denied(monkeypatch):
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    monkeypatch.setattr(services, "process_stamp", Mock(side_effect=error))
    return error


def test_absent_pid_denied_by_openprocess_is_not_a_service(monkeypatch, denied):
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(4372)))
    assert services.matches_process(4372, 134382152143096691) is False


def test_protected_recycled_pid_with_different_creation_is_not_owned(monkeypatch, denied):
    monkeypatch.setattr(psutil, "Process", Mock(return_value=Mock(create_time=lambda: 1793768000.0)))
    assert services.matches_process(4372, 134382152143096691) is False


@pytest.mark.parametrize("offset", [0, 0.0005])
def test_approximate_snapshot_match_never_grants_ownership(monkeypatch, denied, offset):
    stamp = 134382152143096691
    monkeypatch.setattr(psutil, "Process", Mock(return_value=Mock(
        create_time=lambda: stamp / 10_000_000 - 11_644_473_600 + offset)))
    with pytest.raises(PermissionError) as caught:
        services.matches_process(4372, stamp)
    assert caught.value is denied


def test_snapshot_access_denial_keeps_identity_unknown(monkeypatch, denied):
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.AccessDenied(4372)))
    with pytest.raises(PermissionError) as caught:
        services.matches_process(4372, 134382152143096691)
    assert caught.value is denied


def test_other_observation_errors_propagate(monkeypatch):
    error = PermissionError(13, "unrelated")
    error.winerror = 1314
    monkeypatch.setattr(services, "process_stamp", Mock(side_effect=error))
    snapshot = Mock()
    monkeypatch.setattr(psutil, "Process", snapshot)
    with pytest.raises(PermissionError):
        services.matches_process(4372, 1)
    snapshot.assert_not_called()
