from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO

import pytest

from management_portal.execution import account_exchange_rates as fx
from management_portal.execution.account_resources import with_account_resources


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc).timestamp()
XML = b'''<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-09-25">
 <Cube currency="USD" rate="1.1403"/><Cube currency="CNY" rate="7.6551"/>
 </Cube></Cube></gesmes:Envelope>'''


def mock_rates(monkeypatch, xml=XML):
    calls = []
    def request(url, timeout):
        calls.append((url, timeout))
        return BytesIO(xml)
    monkeypatch.setattr(fx.urllib.request, 'urlopen', request)
    return calls


def test_common_currency_comparison_preserves_original_balances(monkeypatch):
    """各币种余额按 ECB 汇率折算为 CNY 比较，原始余额与显示文本保持不变"""
    calls = mock_rates(monkeypatch)
    monkeypatch.setattr(fx.time, 'time', lambda: NOW)
    snapshot = fx.collect({'USD', 'CNY'})
    items = [
        {'id': 'usd', 'amount': 19.79, 'currency': 'USD', 'display': '$19.79'},
        {'id': 'cny', 'amount': 62.235577, 'currency': 'CNY', 'display': '62.235577 CNY'},
        {'id': 'eur', 'amount': 1, 'currency': 'EUR', 'display': '1 EUR'},
    ]
    original = deepcopy(items)
    balances = [row['balance'] for row in with_account_resources([{'account_id': item['id'], 'balance': item} for item in items], snapshot)]
    usd, cny, eur = balances
    assert cny['comparison_amount'] < usd['comparison_amount'] < 169.95
    assert usd['comparison_amount'] == pytest.approx(132.85488818731912)
    assert eur['comparison_amount'] == pytest.approx(7.6551)
    assert all(b['comparison_currency'] == 'CNY' for b in balances)
    assert usd['exchange_rate_date'] == '2026-09-25'
    assert usd['exchange_rate_source'] == fx.SOURCE
    assert [b['display'] for b in balances] == [b['display'] for b in original]
    assert items == original
    assert calls == [(fx.SOURCE, 18)]
    equivalent = fx.comparison({'amount': usd['comparison_amount'], 'currency': 'CNY'}, snapshot)
    assert equivalent['comparison_amount'] == usd['comparison_amount']


def test_cny_only_and_fresh_cache_do_not_fetch(monkeypatch):
    """只有 CNY 余额或汇率缓存仍新鲜时不请求汇率源"""
    calls = mock_rates(monkeypatch)
    assert fx.collect({'CNY', None}, now=NOW) == {}
    assert calls == []
    snapshot = fx.collect({'USD'}, now=NOW)
    assert fx.collect({'USD'}, snapshot, now=NOW + fx.REFRESH_SECONDS - 1) == snapshot
    assert len(calls) == 1
    fx.collect({'USD'}, snapshot, now=NOW + fx.REFRESH_SECONDS)
    assert len(calls) == 2


def test_failure_retains_sample_without_extending_its_validity(monkeypatch):
    """汇率拉取失败时保留旧样本并标为过期，不延长其有效期，超期后不再折算"""
    mock_rates(monkeypatch)
    snapshot = fx.collect({'USD'}, now=NOW)
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise OSError('offline')
    monkeypatch.setattr(fx.urllib.request, 'urlopen', fail)
    later = NOW + fx.REFRESH_SECONDS
    cached = fx.collect({'USD'}, snapshot, now=later)
    assert cached['date'] == snapshot['date']
    assert cached['fetched_at'] == snapshot['fetched_at']
    assert fx.comparison({'amount': 19.79, 'currency': 'USD'}, cached, now=later)['comparison_stale']
    assert fx.collect({'USD'}, cached, now=later + 299) == cached
    assert len(calls) == 1
    expired = fx.collect({'USD'}, cached, now=NOW + 8 * 86400)
    assert fx.comparison({'amount': 19.79, 'currency': 'USD'}, expired, now=NOW + 8 * 86400) == {}
    empty = fx.collect({'USD'}, now=NOW)
    assert empty['status'] == 'unavailable'
    assert fx.comparison({'amount': 19.79, 'currency': 'USD'}, empty, now=NOW) == {}


@pytest.mark.parametrize('currency', ['credits', 'UNKNOWN', None])
def test_unknown_unit_never_uses_raw_amount(monkeypatch, currency):
    """单位未知或缺失的余额不折算，也不直接用原始数额比较"""
    mock_rates(monkeypatch)
    snapshot = fx.collect({'USD'}, now=NOW)
    assert fx.comparison({'amount': 200, 'currency': currency}, snapshot, now=NOW) == {}


@pytest.mark.parametrize('rate', ['0', '-1', 'NaN', 'inf'])
def test_invalid_reference_rates_are_rejected(monkeypatch, rate):
    """汇率为 0、负数、NaN 或无穷大时整份汇率数据视为不可用"""
    mock_rates(monkeypatch, XML.replace(b'1.1403', rate.encode()))
    assert fx.collect({'USD'}, now=NOW)['status'] == 'unavailable'


@pytest.mark.parametrize('date', ['2026-09-01', '2026-09-27', 'invalid'])
def test_out_of_range_or_invalid_dates_are_rejected(monkeypatch, date):
    """汇率日期过旧、晚于当前或无法解析时汇率数据视为不可用"""
    mock_rates(monkeypatch, XML.replace(b'2026-09-25', date.encode()))
    assert fx.collect({'USD'}, now=NOW)['status'] == 'unavailable'


@pytest.mark.parametrize('amount', [float('inf'), float('nan'), True, '19.79', None])
def test_non_numeric_or_non_finite_values_are_not_compared(amount):
    """非数值、布尔、字符串或非有限的余额不参与币种折算"""
    assert fx.comparison({'amount': amount, 'currency': 'CNY'}, {}, now=NOW) == {}


@pytest.mark.parametrize('amount', [0, -1])
def test_zero_and_debt_keep_their_sign(amount):
    """余额为 0 或负数（欠费）时折算结果保留原符号"""
    assert fx.comparison({'amount': amount, 'currency': 'CNY'}, {}, now=NOW)['comparison_amount'] == amount
