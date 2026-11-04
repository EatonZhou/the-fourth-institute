"""TAU prerequisites use actual permissions and scoped execution facts."""
from copy import deepcopy
from unittest.mock import Mock
import json

import pytest

from management_portal.test_framework import tau_fixture_access as access
from management_portal.test_framework.tau_execution_prerequisites import (
    prepare_execution_prerequisite, read_execution_prerequisite,
)
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_permission_management import attempt
from tests.cases.test_tau_runtime import configured  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, resident  # noqa: F401

W = 'workspace_0123456789ab'
OTHER = 'workspace_abcdefabcdef'
R = 'resident_0123456789ab'


def test_negative_access_fixture_uses_existing_protected_route(permissions, monkeypatch):
    from management_portal.http_admin import AdminHttp
    from management_common import portal_network
    from management_portal.permission_system.categories import accounts
    permissions.users('test-actor')
    responses = []
    handler = AdminHttp(respond=lambda request, status, body: responses.append((status, body)),
        session_actor=lambda _: 'test-actor', access_payload=None, read_json=None,
        is_admin=lambda actor: accounts().user(actor).get('category') == 'portal_admin')
    monkeypatch.setattr(portal_network, 'read_settings', lambda: pytest.fail('private network read before denial'))
    route = next(r for r in handler.routes() if r.method == 'GET' and r.path == '/api/admin/network')
    route.handler(object(), None, 'test-actor')
    assert responses == [(403, {'error': 'Portal administrator account required'})]


def test_runtime_open_intake_is_not_session_execution_consent(configured):
    from management_portal.test_framework import tau_runtime
    from management_portal.test_framework.tau import CaseNotSent
    cfg = configured
    cfg.state.options['source']['account_id'] = cfg.assignment['backend_route_id']
    cfg.state.runtime['service_tier'] = cfg.assignment['runtime_options']['service_tier']
    cfg.state.conversation['execution_gate'] = {'state': 'waiting_quota', 'reason': 'quota_reserved',
        'account_id': cfg.assignment['backend_route_id']}
    with pytest.raises(CaseNotSent, match='执行前提'):
        tau_runtime.prepare_configuration(cfg.assignment, cancelled=lambda: False, emit=lambda _: None)
    cfg.restart.assert_not_called()


def test_workspace_binding_does_not_grant_mutation_and_fixture_loans_are_exact(permissions, tmp_path):
    permissions.users('controller', 'test-actor')
    permissions.place(Fact('workspace', W), Fact('workspace', OTHER))
    permissions.grant('controller', W, '@admin')
    recipient = lambda name, args, **kw: attempt(name, 'test-actor', args)
    issuer = lambda name, args, **kw: attempt(name, 'controller', args)
    assert not access.prepare_workspace_access(tmp_path, W, check=recipient)['ready']
    assert access.prepare_workspace_access(tmp_path, W, check=recipient, issue=issuer)['ready']
    assert not permissions.holds('test-actor', '@admin', W)
    assert not permissions.holds('test-actor', 'UpdateWorkspaceFile', OTHER)
    assert permissions.holds('test-actor', 'UpdateWorkspaceFile', W)
    assert permissions.holds('test-actor', 'UnregisterResource', W)
    assert len(access.release_workspace_access(tmp_path, instruction=recipient)) == 2
    assert not permissions.holds('test-actor', 'UpdateWorkspaceFile', W)
    assert access.release_workspace_access(tmp_path, instruction=recipient) == []


def test_permission_denial_and_ambiguous_reply_keep_original_grant_request(permissions, tmp_path):
    permissions.users('controller', 'test-actor')
    permissions.place(Fact('workspace', W))
    recipient = lambda name, args, **kw: attempt(name, 'test-actor', args)
    issuer = lambda name, args, **kw: attempt(name, 'controller', args)
    with pytest.raises(PermissionError):
        access.prepare_workspace_access(tmp_path, W, check=recipient, issue=issuer)
    assert not permissions.holds('test-actor', 'UpdateWorkspaceFile', W)
    permissions.grant('controller', W, '@admin')
    keys = []
    def disconnected(name, args, **kw):
        keys.append(kw['key'])
        raise ConnectionError('reply unknown')
    for _ in range(2):
        with pytest.raises(ConnectionError):
            access.prepare_workspace_access(tmp_path, W, check=recipient, issue=disconnected)
    assert keys[0] == keys[1]
    with pytest.raises(RuntimeError, match='no receipt'):
        access.release_workspace_access(tmp_path, instruction=recipient)


def test_committed_permission_with_lost_reply_must_recover_receipt(permissions, tmp_path):
    permissions.users('controller', 'test-actor')
    permissions.place(Fact('workspace', W))
    permissions.grant('controller', W, '@admin')
    recipient = lambda name, args, **kw: attempt(name, 'test-actor', args)
    receipts, keys = {}, []
    def issuer(name, args, **kw):
        key = kw['key']; keys.append(key)
        if key not in receipts:
            receipts[key] = attempt(name, 'controller', args)
            raise ConnectionError('committed reply lost')
        return receipts[key]
    with pytest.raises(ConnectionError):
        access.prepare_workspace_access(tmp_path, W, check=recipient, issue=issuer)
    pending = access.prepare_workspace_access(tmp_path, W, check=recipient)
    assert not pending['ready'] and pending['reason'] == 'workspace_permission_receipt_pending'
    with pytest.raises(ConnectionError):
        access.prepare_workspace_access(tmp_path, W, check=recipient, issue=issuer)
    assert keys[0] == keys[1]
    assert access.prepare_workspace_access(tmp_path, W, check=recipient, issue=issuer)['ready']
    assert len(access.release_workspace_access(tmp_path, instruction=recipient)) == 2


def test_workspace_fixture_prepares_owned_allocation_before_background_reuse(tmp_path, monkeypatch):
    from management_common.resource_identity import get_resource_type_registry
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.test_framework import tau_fixture_paths, tau_workspace_fixture
    content = tau_fixture_paths.prepare_content(tmp_path)
    directory = content / 'engineering'; directory.mkdir()
    store = LifecycleStore()
    scope = store.open(str(tmp_path.resolve()), actor='test-actor', run_id='fixture-test', case_id='workspace')
    workspaces = get_workspace_store()
    keys = []
    def bind(name, arguments, *, key):
        assert name == 'BindWorkspace' and arguments == {'resident_id': R, 'path': str(directory)}
        keys.append(key)
        with lifecycle_scope(scope, actor='test-actor'):
            wid = workspaces.for_path(str(directory), new_id=lambda: get_resource_type_registry().new_id('workspace'))
        if len(keys) == 1:
            raise ConnectionError('binding reply unknown')
        return {'binding': {'path': str(directory), 'workspace_id': wid}}
    with pytest.raises(ConnectionError):
        tau_workspace_fixture.prepare_workspace(tmp_path, directory, R, instruction=bind)
    receipt = tau_workspace_fixture.prepare_workspace(tmp_path, directory, R, instruction=bind)
    assert len(store.pending(scope)) == 1 and store.owns(scope, receipt['workspace_id'])
    assert keys[0] == keys[1]
    # A later background worker reuses the registered path without allocating.
    assert workspaces.for_path(str(directory), new_id=lambda: pytest.fail('duplicate allocation')) == receipt['workspace_id']
    assert tau_workspace_fixture.prepare_workspace(tmp_path, directory, R, instruction=bind) == receipt
    with pytest.raises(ValueError, match='outside'):
        tau_workspace_fixture.prepare_workspace(tmp_path, tmp_path, R, instruction=bind)


def test_workspace_fixture_uses_real_bind_workspace_receipt(lineage, tmp_path):
    from management_portal.test_framework import tau_fixture_paths, tau_workspace_fixture
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from tests.cases.isa_test_support import ACTOR
    from management_portal import isa_client
    rid = resident(lineage, 'Fixture directory owner')
    directory = tau_fixture_paths.prepare_content(tmp_path) / 'project'; directory.mkdir()
    store = LifecycleStore()
    scope = store.open(str(tmp_path.resolve()), actor=ACTOR, run_id='fixture-isa', case_id='workspace')
    with lifecycle_scope(scope, actor=ACTOR):
        binding = tau_workspace_fixture.prepare_workspace(tmp_path, directory, rid,
            instruction=lambda name, args, **kw: isa_client.call(ACTOR, name, args, **kw))
    assert binding['path'] == str(directory)
    assert store.owns(scope, binding['workspace_id'])


def test_cleanup_keeps_files_when_permission_revocation_is_denied(tmp_path, monkeypatch):
    from management_portal.test_framework import tau_fixtures
    from management_portal import isa_client, portal_features
    receipt = {'credential_id': 'permission_loan', 'principal_id': 'test-actor'}
    (tmp_path / 'fixture-access.json').write_text(json.dumps({
        'evidence_dir': str(tmp_path.resolve()), 'grants': [{'key': 'original', 'receipt': receipt}]}))
    sample = tmp_path / 'fixtures'; sample.mkdir()
    monkeypatch.setattr(portal_features, 'contributions', lambda _: [])
    def call(actor, name, args, **kw):
        if name == 'ListTemporaryCredentials':
            return {'credentials': [receipt]}
        raise PermissionError('still held')
    monkeypatch.setattr(isa_client, 'call', call)
    with pytest.raises(PermissionError, match='still held'):
        tau_fixtures.cleanup(tmp_path)
    assert sample.exists()


@pytest.fixture
def execution():
    state = {'intake_state': 'open', 'execution_gate': {
        'state': 'open', 'account_id': 'codex_fixture', 'override_enabled': True}}
    init = {'resident_id': R, 'status': 'not_required', 'initialization_id': ''}
    account = {'account_id': 'codex_fixture', 'quota_protection_enabled': True,
               'quota_reserve': {'status': 'reserved', 'automatic_paused': True, 'used_up': True}}
    def call(name, args):
        return deepcopy({
            'GetResident': {'resident': {'resident_id': R, 'account': 'codex_fixture'}},
            'GetResidentInitialization': init,
            'ListResidentStates': {'states': [{'resident_id': R, 'state': state}]},
            'ListProviderAccounts': {'accounts': [account]},
        }[name])
    return call, state, init, account


def test_current_session_credits_never_authorize_independent_automatic_context(execution):
    call, state, init, account = execution
    assert read_execution_prerequisite(call, R)['ready']
    result = read_execution_prerequisite(call, R, new_context=True)
    assert not result['ready'] and result['reason'] == 'independent_context_quota_unavailable'
    account['quota_reserve'] = {'status': 'available', 'automatic_paused': False}
    assert read_execution_prerequisite(call, R, new_context=True)['ready']


@pytest.mark.parametrize('amount,fresh,expected', [
    (0.0, True, False), (-0.5, True, False), (None, True, False),
    (float('nan'), True, False), (True, True, False), (8.0, False, False),
    (8.0, True, True),
])
def test_api_balance_prerequisite_refreshes_original_account_before_recovery(
        execution, amount, fresh, expected):
    """API 执行门开放不替代原计费账号的新鲜可用余额。"""
    call, state, init, account = execution
    account['quota_reserve']['fresh'] = fresh
    account['balance'] = {'metric_kind': 'balance', 'status': 'ok', 'amount': amount}
    calls = []
    def api(name, args):
        calls.append((name, args))
        result = call(name, args)
        if name == 'GetResident':
            result['resident']['provider'] = 'provider_api'
        return result
    result = read_execution_prerequisite(api, R)
    assert result['ready'] is expected
    assert ('ListProviderAccounts', {'account_id': 'codex_fixture', 'refresh': True}) in calls
    if not expected:
        assert result['reason'] == 'api_balance_unavailable'
        init.update(status='running', initialization_id='original-api-initialization')
        interact = Mock()
        assert not prepare_execution_prerequisite(api, R, interact=interact)['ready']
        interact.assert_not_called()


@pytest.mark.parametrize('available,fresh,expected', [
    (True, True, True), (False, True, False), (None, True, False), (True, False, False),
])
def test_api_availability_only_accounts_keep_their_registered_billing_contract(
        execution, available, fresh, expected):
    """只提供可用性观察的 API 不被要求伪造货币余额或 Codex 额度。"""
    call, state, init, account = execution
    account['quota_reserve']['fresh'] = fresh
    account['balance'] = {'metric_kind': 'availability', 'status': 'ok', 'is_available': available}
    def api(name, args):
        result = call(name, args)
        if name == 'GetResident':
            result['resident']['provider'] = 'provider_api'
        return result
    result = read_execution_prerequisite(api, R)
    assert result['ready'] is expected
    assert result['api_availability']['metric_kind'] == 'availability'
    assert 'native_quota' not in result


def test_no_credits_run_does_not_reuse_paid_session_override(execution):
    call, state, init, account = execution
    assert read_execution_prerequisite(call, R)['ready']
    calls = []
    def observe(name, args):
        calls.append((name, args))
        return call(name, args)
    result = prepare_execution_prerequisite(observe, R, require_native_quota=True)
    assert not result['ready'] and result['reason'] == 'native_quota_unavailable'
    assert state['execution_gate']['override_enabled'] is True
    assert ('ListProviderAccounts', {'account_id': 'codex_fixture', 'refresh': True}) in calls
    assert {name for name, _ in calls} <= {'GetResident', 'GetResidentInitialization',
                                          'ListResidentStates', 'ListProviderAccounts'}


@pytest.mark.parametrize('remaining,fresh,used_up,gate_open,expected', [
    (30.0, True, False, True, True),
    (1.0, True, False, True, True),
    (0.0, True, True, True, False),
    (30.0, False, False, True, False),
    (None, True, False, True, False),
    (True, True, False, True, False),
    (30.0, True, False, False, False),
])
def test_no_credits_run_requires_native_quota_and_current_gate(
        execution, remaining, fresh, used_up, gate_open, expected):
    call, state, init, account = execution
    account['quota_reserve'].update(remaining=remaining, fresh=fresh, used_up=used_up)
    if not gate_open:
        state['execution_gate'].update(state='closed', reason='binding_unavailable')
    result = read_execution_prerequisite(call, R, require_native_quota=True)
    assert result['ready'] is expected
    if remaining == 1.0 and not isinstance(remaining, bool):
        # Existing native Session work may use its remaining native reserve;
        # independent automatic work still obeys its separate admission.
        assert not read_execution_prerequisite(call, R, new_context=True)['ready']


def test_no_credits_policy_survives_interaction_reread(execution):
    call, state, init, account = execution
    account['quota_reserve'].update(remaining=30.0, fresh=True, used_up=False)
    init.update(status='running', initialization_id='original-attempt')
    def finish_initialization(_request):
        init['status'] = 'completed'
        account['quota_reserve'].update(remaining=0.0, used_up=True)
        return {'ready': True, 'confirmed': True}
    callback = Mock(side_effect=finish_initialization)
    result = prepare_execution_prerequisite(call, R, require_native_quota=True, interact=callback)
    assert callback.call_count == 1
    assert not result['ready'] and result['reason'] == 'native_quota_unavailable'


def test_no_credits_policy_blocks_paid_initialization_before_interaction(execution):
    call, state, init, account = execution
    init.update(status='running', initialization_id='held-attempt')
    callback = Mock(return_value={'ready': True, 'confirmed': True})
    result = prepare_execution_prerequisite(call, R, require_native_quota=True, interact=callback)
    assert not result['ready'] and result['reason'] == 'native_quota_unavailable'
    assert result['recovery'] is None
    callback.assert_not_called()


@pytest.mark.parametrize('native_only', [False, True])
def test_family_facade_points_to_real_executor_checks_without_session_recovery(execution, native_only):
    call, state, init, account = execution
    account['quota_reserve'].update(remaining=70.0, fresh=True, used_up=False)
    calls = []
    def facade(name, args):
        calls.append(name)
        result = call(name, args)
        if name == 'GetResident':
            result['resident'].update(lineage_id='lineage_0123456789ab',
                family={'role': 'facade', 'family_id': 'fbi_0123456789ab'})
        return result
    state['execution_gate'] = {'state': 'open'}
    callback = Mock()
    result = prepare_execution_prerequisite(facade, R, require_native_quota=native_only, interact=callback)
    assert not result['ready'] and result['reason'] == 'family_facade_not_executing'
    assert result['recovery'] == {'instruction': 'GetLineage',
        'arguments': {'lineage_id': 'lineage_0123456789ab'}, 'scope': 'family_execution_members'}
    assert calls == ['GetResident']
    callback.assert_not_called()


def test_fork_initialization_and_human_interaction_require_authoritative_reread(execution):
    call, state, init, account = execution
    init.update(status='running', initialization_id='exact-attempt')
    callback = Mock(return_value={'ready': True, 'confirmed': True})
    result = prepare_execution_prerequisite(call, R, interact=callback)
    assert not result['ready']
    request = callback.call_args.args[0]['recovery']
    assert request['instruction'] == 'RetryResidentInitialization'
    assert request['arguments']['expected_initialization_id'] == 'exact-attempt'
    assert 'credits_consent' not in request['arguments']
    def actual_interaction(request):
        init['status'] = 'completed'
    assert prepare_execution_prerequisite(call, R, interact=actual_interaction)['ready']


def test_open_intake_does_not_imply_an_execution_gate(execution):
    call, state, init, account = execution
    state['execution_gate'] = {'state': 'closed', 'account_id': 'codex_fixture', 'reason': 'quota_used_up'}
    assert not read_execution_prerequisite(call, R)['ready']
    state['execution_gate'] = {}
    assert not read_execution_prerequisite(call, R)['ready']


def test_tau_configuration_does_not_restart_or_send_during_fork_initialization(configured):
    from management_portal.test_framework import tau_runtime
    from management_portal.test_framework.tau import CaseNotSent
    configured.state.options['initialization'] = {'status': 'running', 'initialization_id': 'held'}
    configured.state.options['source']['account_id'] = configured.assignment['backend_route_id']
    configured.state.runtime['service_tier'] = 'priority'
    assert not tau_runtime.resident_status(configured.assignment)['ready']
    assert tau_runtime.apply_configuration(configured.assignment)['status'] == 'initializing'
    with pytest.raises(CaseNotSent, match='RetryResidentInitialization'):
        tau_runtime.prepare_configuration(configured.assignment, cancelled=lambda: False, emit=lambda _: None)
    configured.restart.assert_not_called()
