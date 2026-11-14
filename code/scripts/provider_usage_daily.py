"""One daily trigger: native subscription profiles, incremental usage, expiry notices."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_daily(call, *, since='2020-01-01T00:00:00Z', until=None,
              cache_root='features:provider_usage/cache-20261114',
              refresh_cache_root='features:provider_usage/refresh-cache-20261114',
              output_root='features:provider_usage/daily', workers=8):
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.execution.subscription_profile import collect_profiles
    from scripts import provider_weekly_usage
    from scripts.provider_usage_reset_reminders import notify_expiring_resets
    snapshot = None
    def shared_call(name, **arguments):
        nonlocal snapshot
        if name == 'ListProviderAccounts' and arguments == {'refresh': False}:
            if snapshot is None:
                snapshot = call(name, **arguments)
            return snapshot
        return call(name, **arguments)
    result = {'schema': 'tfi.provider-daily-run.v1', 'provider_usage_refresh_requested': False}
    try:
        profiles = collect_profiles(default_execution_accounts())
        result['tiers'] = {'status': 'partial' if any(r['status'] == 'failed' for r in profiles) else 'ok',
                           'accounts': profiles}
    except Exception as error:
        result['tiers'] = {'status': 'failed', 'error_code': type(error).__name__}
    try:
        output = io.StringIO()
        with redirect_stdout(output):
            provider_weekly_usage.main(['--portal-script', '--since', since, '--until', until or
                datetime.now(timezone.utc).isoformat(), '--native-only', '--subscription-only',
                '--write-report', '--skip-reset-reminders', '--workers', str(workers),
                '--cache-root', cache_root, '--refresh-cache-root', refresh_cache_root,
                '--output-root', output_root], portal_call=shared_call)
        result['usage'] = {'status': 'ok', **json.loads(output.getvalue())}
    except (Exception, SystemExit) as error:
        result['usage'] = {'status': 'failed', 'error_code': type(error).__name__}
    try:
        accounts = shared_call('ListProviderAccounts', refresh=False).get('accounts') or []
        result['reminders'] = notify_expiring_resets(shared_call, accounts)
    except Exception as error:
        result['reminders'] = {'status': 'failed', 'error_code': type(error).__name__}
    result['ok'] = all(result[k]['status'] not in ('failed', 'partial') for k in ('tiers', 'usage', 'reminders'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id')
    parser.add_argument('--since', default='2020-01-01T00:00:00Z')
    parser.add_argument('--until')
    parser.add_argument('--cache-root', default='features:provider_usage/cache-20261114')
    parser.add_argument('--refresh-cache-root', default='features:provider_usage/refresh-cache-20261114')
    parser.add_argument('--output-root', default='features:provider_usage/daily')
    parser.add_argument('--workers', type=int, choices=range(1, 17), default=8)
    args = vars(parser.parse_args())
    resident_id = args.pop('resident_id')
    set_output = None
    if os.environ.get('PORTAL_ISA_SOCKET'):
        from portal_script import isa, set_output
        call = isa
    else:
        from scripts.resident_hatg_instruction import execute, _resident_id
        resident_id = _resident_id(resident_id or '')
        def call(name, **arguments):
            return execute(resident_id, {'schema': 'tfi.hatg-instruction.v2', 'instruction': name,
                'arguments': arguments, 'idempotency_key': 'provider-daily:' + uuid.uuid4().hex},
                request_timeout=30, max_attempts=1)['result']
    result = run_daily(call, **args)
    if set_output:
        set_output(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
