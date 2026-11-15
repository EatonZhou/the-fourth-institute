import json
from unittest.mock import Mock

import pytest

from scripts.provider_usage_daily import run_daily


@pytest.mark.parametrize('failed', [False, True, 'official'])
def test_single_snapshot_no_quota_refresh_and_independent_reminders(monkeypatch, failed):
    from management_portal.execution import subscription_profile
    from scripts import provider_weekly_usage, provider_usage_reset_reminders
    from scripts import provider_usage_announcements
    from scripts import provider_usage_official_news
    monkeypatch.setattr(provider_usage_official_news, 'check_official_news', lambda: {'status': 'failed' if failed == 'official' else 'available'})
    monkeypatch.setattr(provider_usage_announcements, 'collect_announcements', lambda: {'status': 'available', 'path': 'events.json'})
    order = []
    monkeypatch.setattr(subscription_profile, 'collect_profiles', lambda accounts: order.append('tier') or [])
    def usage(argv, *, portal_call):
        order.append('usage')
        assert '--skip-reset-reminders' in argv and '--rebuild' not in argv
        assert argv[argv.index('--provider-events')+1] == 'events.json'
        portal_call('ListProviderAccounts', refresh=False)
        if failed is True:
            raise SystemExit(2)
        print(json.dumps({'accounts': [], 'incremental': {'raw_files_read': 0}}))
    monkeypatch.setattr(provider_weekly_usage, 'main', usage)
    def reminder(call, accounts):
        order.append('reminder')
        return {'status': 'empty'}
    monkeypatch.setattr(provider_usage_reset_reminders, 'notify_expiring_resets', reminder)
    call = Mock(return_value={'accounts': []})
    result = run_daily(call)
    assert order == ['tier', 'usage', 'reminder']
    call.assert_called_once_with('ListProviderAccounts', refresh=False)
    assert result['ok'] == (failed is False)
