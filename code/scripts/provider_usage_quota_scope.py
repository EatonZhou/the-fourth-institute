"""Model scope of named extra quota limits in Codex rate-limit snapshots.

A snapshot's ``rate_limits.by_limit_id`` can carry, besides the main limit
(``limit_id``), separate limits such as ``codex_bengalfox`` whose ``limit_name``
is the model they cover ("GPT-5.3-Codex-Spark" -> ``gpt-5.3-codex-spark``).
Kept apart from the native readers so that a change here does not invalidate the
normalized usage index.
"""


def named_limit_models(payload):
    """{(account_id, quota_group): [model]} for extra limits that name their model."""
    records = payload if isinstance(payload, list) else payload.get('observations', [payload])
    result = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        sample = record.get('sample') or record
        for account in sample.get('accounts') or [sample]:
            windows = account.get('rate_limits') if isinstance(account, dict) else None
            if not isinstance(windows, dict):
                continue
            main = windows.get('limit_id') or ''
            for group, limit in (windows.get('by_limit_id') or {}).items():
                name = str(limit.get('limit_name') or '').strip() if isinstance(limit, dict) else ''
                if group != main and name:
                    result[(account.get('account_id') or record.get('account_id'), group)] = [name.lower()]
    return result


def account_plans(payload):
    """{account_id: plan_type} from ``rate_limits.plan_type`` (e.g. "plus", "pro")."""
    records = payload if isinstance(payload, list) else payload.get('observations', [payload])
    result = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        sample = record.get('sample') or record
        for account in sample.get('accounts') or [sample]:
            windows = account.get('rate_limits') if isinstance(account, dict) else None
            if isinstance(windows, dict) and windows.get('plan_type'):
                result[account.get('account_id') or record.get('account_id')] = str(windows['plan_type'])
    return result


def scope_quota_rows(rows, payload):
    """Attach the named model list to rows of extra limits and the account's plan type."""
    scopes, plans = named_limit_models(payload), account_plans(payload)
    for row in rows:
        extra = {}
        models = scopes.get((row.get('account_id'), row.get('quota_group')))
        if models and not row.get('models'):
            extra['models'] = models
        plan = plans.get(row.get('account_id'))
        if plan and not row.get('plan_type'):
            extra['plan_type'] = plan
        yield {**row, **extra} if extra else row
