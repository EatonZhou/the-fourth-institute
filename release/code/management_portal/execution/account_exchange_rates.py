"""Reference FX for account visual comparison; never changes native balances."""
from __future__ import annotations
import math
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOURCE = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
REFRESH_SECONDS = 12 * 60 * 60
MAX_AGE_DAYS = 7


def _valid_date(value, now):
    try:
        age = (datetime.fromtimestamp(now, timezone.utc).date() - datetime.fromisoformat(value).date()).days
        return 0 <= age <= MAX_AGE_DAYS
    except (TypeError, ValueError):
        return False


def collect(currencies, previous=None, *, now=None):
    now = time.time() if now is None else now
    previous = previous or {}
    if not any(currency and currency != 'CNY' for currency in currencies):
        return previous
    if _valid_date(previous.get('date'), now) and now - previous.get('fetched_at', 0) < REFRESH_SECONDS:
        return previous
    if now < previous.get('retry_after', 0):
        return previous
    try:
        with urllib.request.urlopen(SOURCE, timeout=6) as response:
            root = ET.fromstring(response.read(128 * 1024))
        date = next(e.attrib['time'] for e in root.iter() if 'time' in e.attrib)
        if not _valid_date(date, now):
            raise ValueError('Reference FX date is out of range')
        eur = {'EUR': 1.0, **{e.attrib['currency']: float(e.attrib['rate']) for e in root.iter() if 'currency' in e.attrib}}
        if any(not math.isfinite(rate) or rate <= 0 for rate in eur.values()):
            raise ValueError('Invalid reference FX rate')
        rates = {currency: eur['CNY'] / rate for currency, rate in eur.items()}
        return {'base_currency': 'CNY', 'rates': rates, 'date': date, 'source': SOURCE,
                'fetched_at': now, 'status': 'ok'}
    except Exception:
        # Preserve the original sample time; expired quotes never enable coins.
        return {**previous, 'status': 'cached' if previous.get('rates') else 'unavailable', 'retry_after': now + 300}


def comparison(balance, snapshot, *, now=None):
    now = time.time() if now is None else now
    amount, currency = balance.get('amount'), balance.get('currency')
    if type(amount) not in (int, float) or not math.isfinite(amount):
        return {}
    if currency == 'CNY':
        return {'comparison_amount': amount, 'comparison_currency': 'CNY'}
    if snapshot.get('base_currency') != 'CNY' or not _valid_date(snapshot.get('date'), now):
        return {}
    rate = (snapshot.get('rates') or {}).get(currency)
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        return {}
    converted = amount * rate
    if not math.isfinite(converted):
        return {}
    return {'comparison_amount': converted, 'comparison_currency': 'CNY',
            'exchange_rate_date': snapshot['date'], 'exchange_rate_source': snapshot.get('source'),
            'comparison_stale': snapshot.get('status') != 'ok'}
