"""Cleanup retries resolve old ambiguous RPC errors through execution records.

:module: operations.test-framework
"""
import pytest
from management_portal import isa_client
from management_portal.test_framework import tau_fixtures


@pytest.mark.parametrize('execution_status', ['failed', 'executing'])
def test_cleanup_renews_only_a_confirmed_failed_execution(monkeypatch, execution_status):
    rid = 'resident_0123456789ab'
    keys = []
    def release(identity, key):
        assert identity == rid
        keys.append(key)
        if len(keys) == 1:
            raise RuntimeError('legacy owner RPC failure')
    def call(actor, instruction, arguments):
        assert instruction == 'GetInstructionExecution'
        assert arguments == {'instruction': 'UnregisterResource', 'idempotency_key': keys[0]}
        return {'instruction_id': 'old-execution', 'status': execution_status}
    monkeypatch.setattr(tau_fixtures, '_release_resource_once', release)
    monkeypatch.setattr(isa_client, 'call', call)
    with pytest.raises(RuntimeError, match='legacy owner RPC failure'):
        tau_fixtures._release_resource(rid, 'historical-failure')
    tau_fixtures._release_resource(rid, 'historical-failure')
    assert (keys[0] != keys[1]) is (execution_status == 'failed')
