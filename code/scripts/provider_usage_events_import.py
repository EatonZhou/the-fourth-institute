"""Provider-wide quota events from an explicit export (e.g. https://divin.cc/api/events).

The report never fetches this itself; pass a saved copy with ``--provider-events``.
Only completed events with a completion time are used:

- ``usage_reset`` with ``global: true`` -> ``global_reset``: every paid account's
  weekly window restarted, so windows ending then were not used up.
- ``policy`` -> ``policy``: a limit change (e.g. doubled limits) from that time on.

Reset cards are not global: each account decides whether and when to use one.

``ignored`` lists event IDs that do not apply even though the export tags them for
this tool. Example: ``m-codex-2026-10-29-limits-doubled`` doubled Pro limits but
its announcement says Codex usage stays unchanged, and measured Codex cycles
before and after it hold the same capacity.
"""
from datetime import datetime

PROVIDER_TOOLS = {'codex': 'codex', 'claude': 'claude-code'}


def _moment(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def provider_events(payload, provider, ignored=()):
    tool = PROVIDER_TOOLS.get(provider)
    ignored = set(ignored)
    result = []
    for event in (payload or {}).get('events') or []:
        if not isinstance(event, dict) or event.get('tool') != tool or event.get('stage') != 'completed':
            continue
        if str(event.get('id') or '') in ignored:
            continue
        if not event.get('completedAt'):
            continue
        if event.get('type') == 'usage_reset' and event.get('global') is True:
            kind = 'global_reset'
        elif event.get('type') == 'policy':
            kind = 'policy'
        else:
            continue
        try:
            at = _moment(event['completedAt'])
        except (TypeError, ValueError):
            continue
        result.append({'kind': kind, 'at': at, 'id': str(event.get('id') or ''),
                       'title': str(event.get('title') or '')})
    return sorted(result, key=lambda e: e['at'])
