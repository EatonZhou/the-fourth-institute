from types import SimpleNamespace

import pytest

from management_common.projection_receipts import CommittedProjectionError
from management_portal import resident_handoff
from management_portal.test_framework import tau_runtime
from management_portal.test_framework.tau_residents import TauResidentAssignments


@pytest.mark.parametrize('outcome', ['ready', 'timeout', 'cancelled'])
def test_tau_recovers_committed_creation_without_changing_slot(tmp_path, monkeypatch, outcome):
    assignments = TauResidentAssignments(tmp_path / 'assignments.json')
    module = 'resource.service'
    identity = {'resident_id': 'resident_aaaaaaaaaaaa', 'lineage_id': 'lineage_bbbbbbbbbbbb'}
    failure = CommittedProjectionError([{'committed_revision': 1, 'projection': 'resident.resource_bundle'}])
    clock = SimpleNamespace(now=0.0, cancelled=False)
    calls = []

    def create(**options):
        calls.append(options)
        if len(calls) == 1 or outcome == 'timeout':
            raise failure
        return identity

    def sleep(seconds):
        clock.now += seconds
        clock.cancelled = outcome == 'cancelled'

    monkeypatch.setattr(tau_runtime, 'resolve_application_lineage', lambda **_: identity['lineage_id'])
    monkeypatch.setattr(resident_handoff, 'ensure_invocation_resident', create)
    monkeypatch.setattr(tau_runtime.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(tau_runtime.time, 'sleep', sleep)
    invoke = lambda: tau_runtime.ensure_module_resident(assignments, module, backend_route_id='two',
                                                       cancellation_requested=lambda: clock.cancelled)
    if outcome == 'ready':
        assert invoke()['resident_id'] == identity['resident_id']
        assert len(calls) == 2
        assert calls[0] == calls[1]
        assert calls[0]['backend_route_id'] == 'two'
    else:
        with pytest.raises(CommittedProjectionError if outcome == 'timeout' else InterruptedError) as error:
            invoke()
        if outcome == 'timeout':
            assert error.value is failure
            assert clock.now == 30
        else:
            assert len(calls) == 1
        assert not assignments.assignment(module)['resident_id']
