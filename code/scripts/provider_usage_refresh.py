"""Normalize retained Home quota evidence without changing native usage readers."""
import math

from scripts.provider_usage_native import quota_rows


CLAUDE_QUOTA_MARKERS = (b'"quotaLimits"', b'"rate_limit_info"', b'"rate_limit_event"', b'"unifiedWindows"')


def claude_quota_records(row):
    """Read explicit native limit structures; absent utilization stays unknown."""
    at = row.get('timestamp')
    if not at:
        return
    error = row.get('apiErrorParams') or {}
    infos = [row.get('quotaLimits'), error.get('rate_limit_info') if isinstance(error, dict) else None]
    if row.get('type') == 'rate_limit_event':
        infos.append(row.get('rate_limit_info'))
    if isinstance(row.get('unifiedWindows'), dict):
        infos.append({'unifiedWindows': row['unifiedWindows']})
    windows = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        windows.append(info)
        if isinstance(info.get('unifiedWindows'), dict):
            windows.extend({**value, 'rateLimitType': kind}
                           for kind, value in info['unifiedWindows'].items() if isinstance(value, dict))
    seen = set()
    for info in windows:
        kind = info.get('rateLimitType')
        if kind not in {'seven_day', 'seven_day_sonnet', 'seven_day_opus'}:
            continue
        reset = info.get('resetsAt')
        if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset) or reset <= 0:
            continue
        utilization = info.get('utilization')
        remaining = (round(100 * (1 - utilization), 6)
                     if not isinstance(utilization, bool) and isinstance(utilization, (int, float))
                     and math.isfinite(utilization) and 0 <= utilization <= 1 else None)
        key = (kind, reset, remaining, info.get('status'))
        if key in seen:
            continue
        seen.add(key)
        yield {'at': at, 'resets_at': reset, 'id': kind,
               'quota_group': 'claude' if kind == 'seven_day' else kind,
               'window_duration_mins': 10080, 'remaining_percent': remaining,
               'provider_limit_status': info.get('status'), 'evidence_source': 'native_quota',
               'reset_time_semantics': 'scheduled_endpoint_not_proven_reset_event'}


def home_quota_records(records):
    """Recognize explicit SDK weekly identifiers, never guess from time-to-reset."""
    for q in quota_rows(records):
        if not q.get('observed_at'):
            continue  # Collection completion alone does not date a provider observation.
        if q.get('window_duration_mins') is None:
            window = str(q.get('id') or '').lower()
            if window in {'seven_day', 'seven_day_sonnet', 'seven_day_opus', 'limit7d', 'weekly'} or window.endswith('/weekly limit remaining'):
                q = {**q, 'window_duration_mins': 10080,
                     'window_duration_basis': 'explicit_weekly_window_id'}
        if q.get('window_duration_mins') != 10080:
            continue
        yield {**q, 'evidence_source': 'monitor_quota',
               'reset_time_semantics': 'scheduled_endpoint_not_proven_reset_event'}
