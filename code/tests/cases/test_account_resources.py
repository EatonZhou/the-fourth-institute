from datetime import datetime, timedelta, timezone

from management_portal.execution.account_resources import with_account_resources
from management_portal.execution import provider_balance_adapters as native
from management_portal.execution.accounts import default_execution_accounts


def test_shared_billing_preserves_each_execution_identity():
    """共用计费账号的多个执行账号各自保留账号身份并得到独立的余额副本"""
    source = {'id': 'shared', 'status': 'ok', 'metric_kind': 'balance', 'display': '0 CNY', 'amount': 0, 'currency': 'CNY'}
    accounts = [{'account_id': name, 'billing_account_id': 'shared', 'balance': source} for name in ('adapter_a', 'adapter_b', 'shared')]
    result = with_account_resources(accounts, {})
    assert [row['account_id'] for row in result] == ['adapter_a', 'adapter_b', 'shared']
    assert all(row['balance']['amount'] == 0 and row['balance']['source_account_id'] == 'shared' for row in result)
    assert result[0]['balance'] is not result[1]['balance']
    assert 'source_account_id' not in source


def test_empty_execution_accounts_do_not_create_balance_rows():
    """没有执行账号时不生成余额行"""
    assert with_account_resources([], {}) == []


def test_stale_and_missing_balance_never_become_fresh_or_zero():
    """缺失余额保持加载中，读取失败的余额标为过期，一小时前成功读取的余额仍有效"""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    result = with_account_resources([
        {'account_id': 'missing', 'billing_account_id': 'missing', 'status': 'loading'},
        {'account_id': 'failed', 'balance': {'id': 'failed', 'status': 'unavailable', 'stale': True, 'display': '$3', 'updated_at': past}},
        {'account_id': 'old', 'balance': {'id': 'old', 'status': 'ok', 'display': '$4', 'updated_at': past}},
    ], {})
    assert result[0]['balance']['status'] == 'loading' and 'display' not in result[0]['balance']
    assert result[1]['balance']['stale'] and result[1]['balance']['display'] == '$3'
    # Only a failed read marks a balance stale; an hour-old successful read stays current.
    assert result[2]['balance']['stale'] is False and result[2]['balance']['display'] == '$4'


def test_billing_reads_a_shared_account_once_and_collects_fx(monkeypatch):
    """共用计费账号的余额只读取一次，并同时收集汇率快照"""
    from management_portal.execution import account_exchange_rates as fx
    calls = []
    monkeypatch.setattr(native, 'entries', lambda: [{'id': 'shared'}])
    monkeypatch.setattr(native, '_fetch_provider_balance', lambda entry: calls.append(entry['id']) or {
        'id': 'shared', 'status': 'ok', 'currency': 'USD', 'amount': 1})
    monkeypatch.setattr(fx, 'collect', lambda currencies, previous: {'rates': {'USD': 7}})
    billing = native.BillingObservations()
    assert billing.observe('shared') == billing.observe('shared')
    assert calls == ['shared']
    assert billing.exchange_rates_snapshot() == {'rates': {'USD': 7}}


def test_native_billing_normalization_preserves_zero_and_debt():
    """kimi、DeepSeek、OpenRouter 余额归一化保留 0 和负余额，缺失额度显示为空"""
    assert native._normalize_kimi_balance({'data': {'available_balance': 0}})['display'] == '0 CNY'
    assert native._normalize_kimi_balance({'data': {'available_balance': 62.235577}})['amount'] == 62.235577
    assert native._normalize_deepseek_balance({'balance_infos': [{'currency': 'CNY', 'total_balance': 0}]})['amount'] == 0
    assert native._normalize_openrouter_credits({'data': {}})['amount'] is None
    assert native._normalize_openrouter_credits({'data': {'total_credits': 1, 'total_usage': 2}})['display'] == '$-1.00'
    assert native._normalize_openrouter_credits({'data': {}})['display'] == ''


def test_registered_adapters_declare_billing_identity():
    """注册的模型 API 为 kimi、DeepSeek、dsh 执行账号声明计费账号，codex 账号无计费账号"""
    from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, KIMI_PRIMARY, register_apis

    register_apis(KIMI_PRIMARY, {**DEEPSEEK_PRIMARY, "routes": [{"backend": "dsh", "route_id": "deepseek"}]})
    accounts = {a.account_id: a for a in default_execution_accounts().accounts()}
    assert accounts['kimi_default'].billing_account_id == 'kimi_primary'
    assert accounts['deepseek_primary'].billing_account_id == 'deepseek_primary'
    assert accounts['dsh_deepseek'].billing_account_id == 'deepseek_primary'
    assert accounts['codex_zero'].billing_account_id == ''

def test_exchange_rate_snapshot_does_not_wait_for_network_refresh(monkeypatch):
    """读取汇率快照不等待正在进行的网络刷新，刷新完成后返回新样本"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from management_portal.execution import provider_balance_adapters as balances, account_exchange_rates
    entered, release = Event(), Event()
    monkeypatch.setattr(balances, "entries", lambda: [{"id": "billing", "provider": "deepseek"}])
    monkeypatch.setattr(balances, "_fetch_provider_balance", lambda entry: {"currency": "USD", "status": "ok"})
    def collect(currencies, previous):
        entered.set()
        assert release.wait(3)
        return {"sample": "new"}
    monkeypatch.setattr(account_exchange_rates, "collect", collect)
    observations = balances.BillingObservations()
    with ThreadPoolExecutor(max_workers=2) as pool:
        observing = pool.submit(observations.observe, "billing")
        try:
            assert entered.wait(2)
            assert pool.submit(observations.exchange_rates_snapshot).result(timeout=1) == {}
        finally:
            release.set()
        observing.result(timeout=2)
    assert observations.exchange_rates_snapshot() == {"sample": "new"}
