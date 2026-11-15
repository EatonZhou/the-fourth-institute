"""Native scheduled endpoints, always UTC epoch seconds, never reset events."""
from datetime import datetime, timezone
import math


def reset_epoch_seconds(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) and 0 < value < 253402300800 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed = datetime.strptime(value, '%b %d, %Y, %H:%M UTC').replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    try:
        return reset_epoch_seconds(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def normalize_reset_windows(sample, now):
    """Expired native endpoints remain evidence; they cannot freshen a window."""
    def normalize(window):
        reset = reset_epoch_seconds(window.get('resets_at'))
        return {**window, 'resets_at': reset,
                'reset_time_semantics': 'scheduled_endpoint_not_proven_reset_event',
                'reset_time_precision_seconds': window.get('reset_time_precision_seconds') or
                    (60 if window.get('source') == 'claude_cli_usage' else 1),
                'stale': bool(window.get('stale')) or (reset is not None and reset <= now)}
    windows = [normalize(w) for w in sample.get('rate_limits') or [] if isinstance(w, dict)]
    quota = dict(sample.get('quota') or {})
    guarded = [normalize(w) for w in quota.get('windows') or [] if isinstance(w, dict)]
    if guarded:
        quota['windows'] = guarded
    result = dict(sample)
    if 'rate_limits' in sample:
        result['rate_limits'] = windows
    if quota:
        result['quota'] = quota
    # Prefer the account admission windows, so an expired extra model pool
    # cannot invalidate a fresh main pool. Unscoped providers use all windows.
    if any(w['resets_at'] is not None and w['resets_at'] <= now for w in (guarded or windows)):
        result.update(status='unavailable', error_code='quota_reset_not_advanced',
                      error='额度窗口已到期，等待原生新读数')
    return result
