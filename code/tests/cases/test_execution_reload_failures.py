"""Local control RPC replacement preserves the accepted turn and its provenance."""
import time
from types import SimpleNamespace

import pytest

from management_portal.execution.contracts import ExecutionRequest, ExecutionRecoveryDeferred
from management_portal.execution.transport import (
    ExecutionClaimOwnershipLost, ExecutionResponseUnknown, ExecutionServiceUnavailable,
)
from management_portal.execution.worker import ExecutionWorker, _failed_result
from management_portal.society_service import resident_record_domain as records
from management_portal.society_service import resident_execution_domain as residents


@pytest.mark.parametrize('fault,state', [
    (ExecutionServiceUnavailable, 'not_committed'), (ExecutionResponseUnknown, 'unknown'),
])
def test_local_rpc_error_survives_result_projection(fault, state):
    result = _failed_result('same-execution', fault('connection reset'))
    payload = records.runtime_error_payload(result.error, code=result.error_code, details=result.error_details)
    assert payload['details'] == {'source': 'execution_rpc', 'commit_state': state}
    assert records.runtime_failure_summary(result.error, payload).startswith('本地执行服务')
    assert records.runtime_failure_summary('provider network connection reset') == '模型后端连接中断'


@pytest.mark.parametrize('raw', [
    'ExecutionService request failed at /local/execution-service.sock: ConnectionResetError',
    'ExecutionService closed the connection without a response',
    'ExecutionService request failed at /local/execution-service.sock: TimeoutError',
])
def test_saved_local_rpc_failure_does_not_claim_provider_failure(raw):
    assert records.runtime_error_payload(raw)['code'] == 'execution_rpc'
    assert records.runtime_failure_summary(raw, {'code': 'network', 'message': raw}).startswith('本地执行服务')


@pytest.mark.parametrize('fault', [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_resident_defers_observation_without_failing_bound_input(monkeypatch, fault):
    calls = []
    def claim(_resident, _continuation, claimed):
        claimed.job = {'execution_id': 'original-execution', 'user_message_id': 'original-input'}
        return True
    def run(*_args):
        raise fault('connection reset')
    monkeypatch.setattr(residents, '_claim_job', claim)
    monkeypatch.setattr(residents, '_run_claimed_job', run)
    monkeypatch.setattr(residents, '_settle_failed_job', lambda *_: pytest.fail('must retain binding'))
    monkeypatch.setattr(residents, 'finish_resident_continuation', lambda r: calls.append(r))
    residents._run_resident_execution_scoped('resident-test', 'continuation-test')
    assert calls == ['resident-test']


@pytest.mark.parametrize('deferred', [False, True])
@pytest.mark.parametrize('fault', [ExecutionServiceUnavailable, ExecutionResponseUnknown])
@pytest.mark.parametrize('settlement', ['requeued', 'cancelled', 'fenced', 'expired', 'invalid'])
def test_worker_release_survives_rpc_replacement_without_replay(deferred, fault, settlement):
    invocations, writes = [], []

    class Scheduler:
        def start(self, *args):
            return {}
        def wait_for_control(self, *args, **kwargs):
            time.sleep(.001)
            return {'handover_requested': True}
        def prepare_handover(self, *args):
            return {'ready': True, 'interrupt_required': False}
        def heartbeat(self, *args):
            return {}
        def requeue(self, *args, **kwargs):
            writes.append((args, kwargs))
            if settlement == 'invalid':
                raise ValueError('invalid checkpoint')
            if settlement == 'expired' or len(writes) == 1:
                raise fault('local socket replaced')
            if settlement == 'fenced':
                raise ExecutionClaimOwnershipLost('committed reply lost; new owner')
            return {'status': settlement}
        def finish(self, *args):
            pytest.fail('planned handover must not publish a terminal result')

    class Runtime:
        def execute(self, request, context):
            invocations.append(request.execution_id)
            deadline = time.monotonic() + 2
            while not context.handover_requested():
                assert time.monotonic() < deadline
                time.sleep(.001)
            if deferred:
                raise ExecutionRecoveryDeferred('kept original provider turn')
            return SimpleNamespace(output={}, backend_session_id='original-session', backend_route_id='original-route', usage={})
        def interrupt(self, *_):
            pytest.fail('handover keeps the accepted Provider turn running')

    request = ExecutionRequest.new(actor='sample_admin', prompt='accepted input', provider='codex', service_type='agent_session', operation='turn')
    worker = ExecutionWorker(build_id='old-build', worker_id='old-worker', scheduler=Scheduler(), runtime=Runtime())
    claim = {'claim_token': 'original-claim', 'execution': {'execution_id': request.execution_id, 'request': request.to_payload(), 'checkpoint': {'resumable': True, 'turn_id': 'same-turn'}}}
    if settlement == 'invalid':
        with pytest.raises(ValueError, match='invalid checkpoint'):
            worker.execute_claim(claim)
    else:
        result = worker.execute_claim(claim, lease_seconds=.05 if settlement == 'expired' else 5)
        assert result['status'] == ('claim_lost' if settlement in {'fenced', 'expired'} else settlement)
    assert invocations == [request.execution_id]
    assert all(args[:2] == (request.execution_id, 'original-claim') for args, _ in writes)
    assert all(item == writes[0] for item in writes)
