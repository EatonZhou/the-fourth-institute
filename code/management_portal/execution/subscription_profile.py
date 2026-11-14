"""Whitelisted native subscription facts; never infer a price or plan multiplier."""
import json
import time


def tier_sample(tier, *, source, observed_at=None):
    tier = {k: v for k, v in tier.items() if k in ('id', 'name') and isinstance(v, str) and v.strip()}
    return {'status': 'observed' if tier else 'unknown', 'tier': tier, 'source': source,
            'observed_at': time.time() if tier and observed_at is None else observed_at}


def kimi_profile(payload):
    info = payload.get('userInfo') or {} if payload.get('kind') == 'ok' else {}
    level = info.get('userLevel')
    return tier_sample({'id': str(level) if type(level) is int else '',
                        'name': info.get('userLevelName')}, source='kimi.oauth/userinfo')


def reset_cards(payload, *, observed_at):
    value = payload.get('rateLimitResetCredits')
    count = value.get('availableCount') if isinstance(value, dict) else None
    if type(count) is not int or count < 0:
        return {'status': 'unknown', 'available_count': None, 'cards': None}
    rows = value.get('credits')
    keys = ('id', 'resetType', 'status', 'grantedAt', 'expiresAt', 'title', 'description')
    return {'status': 'observed', 'source': 'codex.account/rateLimits/read',
            'observed_at': observed_at, 'available_count': count,
            'cards': [{k: v for k, v in row.items() if k in keys and
                       (v is None or type(v) in (str, int, float))}
                      for row in rows if isinstance(row, dict)] if isinstance(rows, list) else None}


def read_profile(account, accounts):
    if account.provider == 'kimi':
        from management_portal.kimi_account_runtime import client_for_route
        return kimi_profile(client_for_route(account.route_id, for_control=True)._request('GET', 'oauth/userinfo'))
    if account.provider == 'codex':
        from management_portal.codex_app_server_client import client_for_socket
        route = accounts.adapters['codex'].registry.get(account.route_id)
        result = client_for_socket(route.socket_path).request('account/read', {'refreshToken': False}, timeout=15)
        plan = (result.get('account') or {}).get('planType')
        return tier_sample({'id': plan, 'name': plan}, source='codex.account/read')
    if account.provider == 'claude':
        from .account_login import login_command, login_environment
        from .account_observation import login_status
        result = login_status(login_command('claude', status=True),
                              env=login_environment('claude', account.account_id))
        data = json.loads(result.stdout)
        return tier_sample({'id': data.get('planTier'), 'name': data.get('planTier') or data.get('subscriptionType')},
                           source='claude.auth/status')
    if account.provider == 'antigravity':
        from management_portal.antigravity_accounts import catalog_snapshot
        return catalog_snapshot(account.route_id).get('subscription') or tier_sample({}, source='acp')
    return tier_sample({}, source=account.provider)
