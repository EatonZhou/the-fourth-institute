"""Exact native session residue after release; account homes are never owned."""
from pathlib import Path
import re

from management_common.file_retention import provider_session_ended
from management_common.portal_paths import path as portal_path


def _paths(provider, session_id, config_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session_id or ''):
        return []
    paths = []
    if provider in {'kimi', 'kimi-code'}:
        from .native_session_compaction import kimi_wire_path
        wire = kimi_wire_path(portal_path('provider-state') / 'kimi-code', session_id)
        if wire:
            paths.append(wire.parents[2])
    elif provider in {'dsh', 'deepseek', 'opencode'}:
        path = portal_path('provider-state') / ('opencode' if provider == 'opencode' else 'dsh') / session_id
        if path.exists():
            paths.append(path)
    elif provider in {'claude', 'claude-code'} and config_dir:
        # Host account config directories are adopted and never enrolled.
        root = Path(config_dir).absolute()
        owned_roots = [portal_path(area) for area in ('provider-state', 'lineages', 'projects')]
        if any(root.is_relative_to(area) and root != area for area in owned_roots):
            paths.extend((root / 'projects').glob('*/' + session_id + '.jsonl'))
            paths.extend((root / 'projects').glob('*/' + session_id))
    if paths:
        from .account_registrations import registrations
        adopted = [Path(row['config']['account_dir']).expanduser().resolve()
                   for row in registrations() if row.get('config', {}).get('account_dir')]
        paths = [path for path in paths if not any(path.resolve().is_relative_to(home) for home in adopted)]
    return paths


def released(provider, session_id, *, cwd='', config_dir=''):
    paths = _paths(provider, session_id, config_dir)
    for path in paths:
        provider_session_ended(path, owner_id='provider-session:' + provider + ':' + session_id)
    return paths


def reopened(provider, session_id, *, config_dir=''):
    from management_common.directory_retention import RetentionStore
    from management_common.directory_paths import cleanup_location
    paths = _paths(provider, session_id, config_dir)
    if not paths:
        return
    store = RetentionStore()
    with store.database.transaction() as connection:
        for path in paths:
            store.queue.assert_available(path, connection=connection)
            connection.execute(f'UPDATE {store.table} SET eligible_at=NULL WHERE location=%s AND category=%s',
                               (cleanup_location(path), 'provider_session'))
