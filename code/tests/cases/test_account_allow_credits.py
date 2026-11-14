"""Account-scoped paid consent uses isolated Scheduler storage and synthetic observations."""
import time
from types import SimpleNamespace

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.account_quota import quota_state
from management_portal.execution.account_selection import available, headroom
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionService, ExecutionServiceServer
from management_portal.execution.contracts import ExecutionRequest, SessionOptions


class Adapter:
    provider = 'codex'

    def accounts(self):
        return (ExecutionAccount('paid_test', 'codex', 'paid_test', 'codex:paid_test',
                                 quota_policy='reserve', kind='subscription_login'),
                ExecutionAccount('api_test', 'codex', 'api_test', 'codex:api_test', kind='api_profile'))

    def resolve(self, route_id, options, **kwargs):
        return next(a for a in self.accounts() if a.route_id == route_id)


@pytest.fixture
def scheduler():
    value = Scheduler(accounts=ExecutionAccounts((Adapter(),)), quota_reserve_enabled=True,
                      dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    with value.changes.connect() as connection:
        connection.execute("DELETE FROM execution_store_meta WHERE key LIKE %s", ('%:codex:paid_test',))
        connection.execute("DELETE FROM execution_store_meta WHERE key=%s", ('quota_override:resident:resident_0123456789ab',))
        connection.execute("DELETE FROM capacity_settings WHERE capacity_key=%s", ('codex:paid_test',))
        connection.execute("DELETE FROM executions")
        connection.commit()
    value.register_worker(worker_id='paid-worker', build_id='fixture', capabilities=['agent_session:codex'])
    return value


def observation(**credits):
    return {'status': 'reserved', 'exhausted': True, 'remaining_percent': 0, 'complete': True,
            'observed_at': time.time(), 'windows': [{'window': 'weekly', 'remaining_percent': 0}],
            'credits': {'has_credits': True, 'balance': 8, 'authenticated': True,
                        'observed_at': time.time(), **credits}}


def configure(scheduler, **values):
    return scheduler.configure_capacity('codex:paid_test', 8, 0, 0, actor='fixture-admin', **values)


def quota(scheduler):
    return scheduler.health()['capacity_partitions']['codex:paid_test']['quota_reserve']


def test_allow_credits_persists_and_manual_binding_close_keeps_option(scheduler):
    scheduler.update_account_quota('paid_test', observation())
    assert quota(scheduler)['automatic_paused']
    saved = configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    assert saved['allow_credits'] is True and saved['credits_supported'] is True
    restored = Scheduler(accounts=scheduler.accounts, quota_reserve_enabled=True)
    configure(restored)  # Old clients omit the new option.
    binding = 'resident:resident_0123456789ab'
    for revision, enabled in enumerate((True, False, True), 1):
        restored.configure_binding_quota_overrides([{'binding_id': binding, 'revision': revision, 'enabled': enabled}])
        state = restored.binding_quota_states({binding: 'paid_test'})[binding]
        assert state['state'] == 'open'
        assert quota(restored)['allow_credits'] is True
    assert quota(restored)['used_up'] and quota(restored)['remaining'] == 0
    assert quota(restored)['credits_active'] and not quota(restored)['automatic_paused']
    with restored.changes.connect() as connection:
        row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s', ('quota_credits:codex:paid_test',)).fetchone()
    assert 'fixture-admin' in str(row['value'])


@pytest.mark.parametrize('change', [{'authenticated': False}, {'stale': True}, {'has_credits': False},
    {'balance': 0}, {'balance': None}, {'balance': float('nan')}, {'observed_at': 0}, {'observed_at': time.time()+3600}])
def test_invalid_credit_observation_keeps_native_hold(scheduler, change):
    configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('paid_test', observation(**change))
    assert quota(scheduler)['automatic_paused'] and not quota(scheduler)['credits_active']


def test_positive_threshold_revocation_and_failed_refresh_protect(scheduler):
    scheduler.update_account_quota('paid_test', observation())
    configure(scheduler, allow_credits=True)
    assert quota(scheduler)['close_threshold']['value'] == 2
    assert quota(scheduler)['automatic_paused']
    configure(scheduler, close_threshold={'value': 0, 'unit': '%'})
    assert not quota(scheduler)['automatic_paused']
    configure(scheduler, allow_credits=False)
    assert quota(scheduler)['automatic_paused']
    configure(scheduler, allow_credits=True)
    scheduler.update_account_quota('paid_test', {'status': 'unknown', 'credits': {}})
    assert quota(scheduler)['used_up'] and quota(scheduler)['automatic_paused']


def test_scheduler_releases_original_queue_and_stops_when_credits_fail(scheduler):
    scheduler.update_account_quota('paid_test', observation())
    request = ExecutionRequest.new(actor='fixture', prompt='synthetic queued input', service_type='agent_session',
        provider='codex', operation='turn', session=SessionOptions(mode='stateless', backend_route_id='paid_test'))
    scheduler.submit(request)
    assert scheduler.claim(worker_id='paid-worker', capabilities=['agent_session:codex'])['reason'] == 'quota_reserve'
    configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    claimed = scheduler.claim(worker_id='paid-worker', capabilities=['agent_session:codex'])
    assert claimed['execution']['execution_id'] == request.execution_id
    scheduler.update_account_quota('paid_test', observation())
    assert not scheduler.snapshot(request.execution_id)['scheduler']['cancel_requested']
    scheduler.update_account_quota('paid_test', observation(balance=0))
    assert scheduler.snapshot(request.execution_id)['scheduler']['cancel_requested']


def test_expiry_and_disabled_account_still_close_gate(scheduler, monkeypatch):
    from management_portal.execution import account_registrations
    configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('paid_test', observation())
    with scheduler.changes.connect() as connection:
        states, _ = scheduler.quota.quota_states(connection, {'codex:paid_test'}, set(), now=time.time()+61)
    assert states['codex:paid_test']['automatic_paused']
    monkeypatch.setattr(account_registrations, 'registrations', lambda: [{'account_id': 'paid_test', 'enabled': False}])
    gate = scheduler.binding_quota_states({'resident:resident_0123456789ab': 'paid_test'})
    assert next(iter(gate.values()))['reason'] == 'account_disabled'


def test_invalid_threshold_rolls_back_paid_setting(scheduler):
    with pytest.raises(ValueError):
        configure(scheduler, allow_credits=True, close_threshold={'value': -1, 'unit': '%'})
    assert quota(scheduler)['allow_credits'] is False


@pytest.mark.parametrize('scope', [{}, {'account_id': 'paid_test', 'operation_id': 'op'},
    {'account_id': 'paid_test', 'operation_id': 'op', 'execution_id': 'x', 'provider_session_ref': 's'},
    {'account_id': 'paid_test', 'operation_id': 'op', 'execution_id': ''}])
def test_session_scope_validation_remains_strict(scheduler, scope):
    with pytest.raises(ValueError, match='credits_scope'):
        scheduler.configure_binding_quota_overrides([{'binding_id': 'resident:resident_0123456789ab',
            'revision': 1, 'enabled': True, 'credits_scope': scope}])


from tests.cases.test_family_quota import quota_family, ACTOR, owners, team  # noqa: E402,F401


@pytest.mark.parametrize('already_waiting', [False, True])
def test_family_member_paid_account_releases_existing_review_without_replay(quota_family, already_waiting):
    from management_portal.society_service import family_quota
    f = quota_family
    if already_waiting:
        assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR)['status'] == 'waiting_for_quota_review'
    f.accounts[0]['quota_reserve'].update(status='reserved', used_up=True, remaining=0,
                                         credits_active=True, automatic_paused=False)
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert result['status'] == 'relayed'
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR) == result


def test_paid_available_does_not_fabricate_migration_headroom(scheduler):
    configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('paid_test', observation(unlimited=True, balance=None))
    account = {'provider': 'codex', 'kind': 'subscription_login', 'enabled': True,
               'login_state': 'logged_in', 'quota_reserve': quota(scheduler)}
    assert available(account) and not available(account, reserve=5)
    assert headroom(account) is None
    assert not available({**account, 'enabled': False})
    assert not available({**account, 'login_state': 'login_expired'})


@pytest.mark.parametrize('value', [1, 'true', {}, []])
def test_boolean_validation_is_atomic(scheduler, value):
    with pytest.raises(ValueError, match='bool'):
        configure(scheduler, allow_credits=value, close_threshold={'value': 0, 'unit': '%'})
    assert quota(scheduler)['close_threshold']['value'] == 2


def test_unsupported_account_rejects_paid_optin(scheduler):
    with pytest.raises(ValueError, match='support'):
        scheduler.configure_capacity('codex:api_test', 8, 0, 0, actor='fixture', allow_credits=True)


def test_home_summary_preserves_native_option_and_marks_api_unsupported():
    from management_portal.execution.account_resources import home_account_resources
    rows = home_account_resources([
        {'account_id': 'native', 'kind': 'subscription_login', 'allow_credits': True, 'credits_supported': True},
        {'account_id': 'api', 'kind': 'api_profile', 'billing_account_id': 'billing'}], {})
    assert rows[0]['allow_credits'] and rows[0]['credits_supported']
    assert rows[1]['allow_credits'] is False and rows[1]['credits_supported'] is False


def test_isa_rpc_and_monitor_share_account_settings(scheduler, monkeypatch):
    from management_portal.execution import isa
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.hatg.catalog import declaration
    params = {'account_id': 'paid_test', 'max_concurrency': 8, 'dispatch_interval_min_seconds': 0,
              'dispatch_interval_max_seconds': 0, 'allow_credits': True, 'close_threshold_percent': 0}
    declaration('UpdateProviderAccount').validate_arguments(params)
    server = SimpleNamespace(accounts=scheduler.accounts, scheduler=scheduler)
    client = SimpleNamespace(request=lambda method, values: ExecutionServiceServer.dispatch(server, method, values))
    service = ExecutionService(client=client)
    monkeypatch.setattr(isa, '_client', lambda: service)
    result = isa.update_provider_account(SimpleNamespace(argument=lambda key: params.get(key), caller='fixture-admin'))
    assert result['settings']['allow_credits'] and result['settings']['credits_supported']
    monitor = AccountMonitor(scheduler.accounts, scheduler)
    rows = {row['account_id']: row for row in monitor.snapshot()['accounts']}
    assert rows['paid_test']['allow_credits'] and rows['paid_test']['credits_supported']
    assert rows['api_test']['allow_credits'] is False and rows['api_test']['credits_supported'] is False
    monkeypatch.setattr(isa, '_accounts_snapshot', lambda force, **kw: {'accounts': list(rows.values())})
    listed = isa.list_provider_accounts(SimpleNamespace(argument=lambda key, default=None: default, caller='fixture-admin'))
    assert next(row for row in listed['accounts'] if row['account_id']=='paid_test')['allow_credits']


def test_family_fixed_paid_account_does_not_trigger_migration(scheduler, monkeypatch):
    from management_portal.society_service import family_quota as f, resident_record_domain as records, resident_default_backend
    configure(scheduler, allow_credits=True, close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('paid_test', observation())
    account = {'account_id': 'paid_test', 'provider': 'codex', 'kind': 'subscription_login',
               'login_state': 'logged_in', 'quota_reserve': quota(scheduler)}
    record = {'resident_id': 'resident_0123456789ab', 'lineage_id': 'lineage_0123456789ab'}
    monkeypatch.setattr(f, 'auto_migration_enabled', lambda record: True)
    monkeypatch.setattr(f, 'reserve_candidate', lambda record: record)
    monkeypatch.setattr(f, 'quota_switch_policy', lambda record: True)
    monkeypatch.setattr(records, 'lifecycle_operations', lambda: SimpleNamespace(pending=lambda rid: None))
    monkeypatch.setattr(f.resident_execution_work, 'read', lambda record: {})
    monkeypatch.setattr(f, 'resident_account', lambda record, accounts: account)
    monkeypatch.setattr(f, 'role_migration', lambda record: {})
    monkeypatch.setattr(resident_default_backend, 'selection_mode', lambda record: 'fixed')
    def forbidden(*args):
        raise AssertionError('paid account must not migrate')
    monkeypatch.setattr(f, '_ensure_quota_switch', forbidden)
    assert f.ensure_role_reserve(record, [account]) is False
