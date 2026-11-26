"""Exact native session residue after release; account homes are never owned."""
from pathlib import Path
import re

from management_common.file_retention import provider_session_ended


def _paths(provider, session_id, config_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session_id or ''):
        return []
    from .provider_catalog import AGENT_PROVIDERS
    definition = AGENT_PROVIDERS.get(provider)
    residue = getattr(definition.backend_class(), 'session_residue', None) if definition and definition.adapter else None
    paths = residue(session_id, config_dir) if residue else []
    if paths:
        from .account_registrations import registrations
        host_homes = [Path(row['config']['account_dir']).expanduser().resolve()
                   for row in registrations() if row.get('config', {}).get('account_dir')]
        paths = [path for path in paths if not any(path.resolve().is_relative_to(home) for home in host_homes)]
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
