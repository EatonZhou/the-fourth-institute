"""Cleanup keeps denied resources pending and reports its same-actor checks.

:module: operations.test-framework
"""
import pytest

from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
from management_common.resource_identity import get_resource_type_registry
from management_portal import isa_client
from management_portal.hatg.contracts import InstructionError
from management_portal.test_framework import run_cleanup, tau_fixtures


@pytest.mark.parametrize('probe', ['existing', 'denied', 'hidden', 'unavailable'])
def test_workspace_denial_keeps_same_actor_confirmation(monkeypatch, probe):
    rid = 'workspace_123456789abc'
    calls = []
    def call(actor, name, arguments, **kwargs):
        assert actor == tau_fixtures.TAU_ACTOR
        calls.append(name)
        if name == 'UnregisterResource':
            raise InstructionError('instruction.forbidden', 'admin denied',
                                   details={'reason': 'no_permit', 'a': '@admin'})
        assert name == 'ResolveResource'
        if probe == 'existing':
            return {'resource': {'resource_id': rid, 'attributes': {'status': 'active'}}}
        code = {'denied': 'instruction.forbidden', 'hidden': 'instruction.not_found',
                'unavailable': 'instruction.owner_unavailable'}[probe]
        raise InstructionError(code, 'confirmation failed',
                               details={'reason': 'no_permit'} if probe == 'hidden' else {})
    monkeypatch.setattr(isa_client, 'call', call)
    with pytest.raises(InstructionError) as raised:
        tau_fixtures._release_resource_once(rid, 'denied-workspace')
    error = raised.value
    assert error.code == 'instruction.forbidden' and error.details['a'] == '@admin'
    check = error.details['cleanup_confirmation']
    assert check['actor'] == tau_fixtures.TAU_ACTOR and check['resource_id'] == rid
    assert check['instruction'] == 'ResolveResource'
    if probe == 'existing':
        assert check['outcome'] == 'resource_exists' and check['status'] == 'active'
    else:
        assert check['outcome'] == 'unconfirmed'
        assert check['error']['code'] == code_for(probe)
    assert 'ResolveResource' in str(error) and tau_fixtures.TAU_ACTOR in str(error)
    assert calls == ['UnregisterResource', 'ResolveResource']


def code_for(probe):
    return {'denied': 'instruction.forbidden', 'hidden': 'instruction.not_found',
            'unavailable': 'instruction.owner_unavailable'}[probe]


def test_project_unreadable_receipt_is_diagnosed_not_absence(monkeypatch):
    def call(actor, name, arguments, **kwargs):
        assert actor == tau_fixtures.TAU_ACTOR
        if name == 'DeleteProject':
            raise InstructionError('instruction.forbidden', 'admin denied')
        assert name == 'GetProjectDeletion'
        raise InstructionError('instruction.not_found', 'receipt unavailable to this caller')
    monkeypatch.setattr(isa_client, 'call', call)
    with pytest.raises(InstructionError) as raised:
        tau_fixtures._release_resource_once('project-unreadable-receipt', 'denied-project')
    assert raised.value.code == 'instruction.forbidden'
    check = raised.value.details['cleanup_confirmation']
    assert check['outcome'] == 'unconfirmed'
    assert check['instruction'] == 'GetProjectDeletion'
    assert check['error']['code'] == 'instruction.not_found'


@pytest.mark.parametrize('denial', ['instruction.forbidden', 'instruction.not_found'])
def test_denied_resource_attempted_once_per_run_and_retried_next_run(monkeypatch, denial):
    store = LifecycleStore()
    run_id = 'testrun_denied_cleanup'
    scopes = [store.open(key, actor='scope-owner', run_id=run_id, case_id=key)
              for key in ('one', 'two')]
    ids = []
    for scope in scopes:
        with lifecycle_scope(scope, actor='scope-owner'):
            ids.append(get_resource_type_registry().new_id('workspace'))
        store.request_close(scope)
    calls = []
    allowed = False
    def release(rid, key):
        calls.append(rid)
        if rid == ids[0] and not allowed:
            raise InstructionError(denial, 'current actor has no admin',
                                   details={'reason': 'no_permit'})
    monkeypatch.setattr(tau_fixtures, '_release_resource', release)
    result = run_cleanup.finish_closing_scopes(run_id)
    assert result['status'] == 'error'
    assert calls.count(ids[0]) == 1
    assert store.pending(scopes[0]) == [ids[0]]
    assert ids[0] not in result['resources'] and len(result['errors']) == 1
    assert ids[1] in result['resources']
    allowed = True  # A later owner-authorized change, not an automatic grant.
    result = run_cleanup.finish_closing_scopes(run_id)
    assert result['status'] == 'complete'
    assert calls.count(ids[0]) == 2
    assert not store.pending(scopes[0])


def test_transient_cleanup_failure_still_retries_after_seal(monkeypatch):
    store = LifecycleStore()
    scope = store.open('transient', actor='scope-owner', run_id='transient-run', case_id='one')
    with lifecycle_scope(scope, actor='scope-owner'):
        rid = get_resource_type_registry().new_id('workspace')
    store.request_close(scope)
    calls = []
    def release(resource_id, key):
        calls.append(resource_id)
        if len(calls) == 1:
            raise InstructionError('instruction.owner_unavailable', 'temporary outage', retryable=True)
    monkeypatch.setattr(tau_fixtures, '_release_resource', release)
    result = run_cleanup.finish_closing_scopes('transient-run')
    assert calls == [rid, rid] and result['status'] == 'complete'
