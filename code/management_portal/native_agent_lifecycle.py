"""Stop instance-owned native agents after their admitting services stop.

DSH and OpenCode intentionally outlive Worker replacement. Full instance stop
must close them explicitly on hosts without systemd's service cgroups, before
moving their directories or retiring their instance. History remains resumable.
"""
from __future__ import annotations

import importlib
import json
import re

from management_common.portal_paths import path as portal_path

PROVIDERS = ('dsh', 'opencode')


def close_all() -> dict:
    closed = []
    for provider in PROVIDERS:
        state = portal_path('provider-state') / provider
        if state.is_symlink():
            raise ValueError('Native agent state cannot be redirected')
        if not state.is_dir():
            continue
        client = None
        for folder in sorted(state.iterdir()):
            if not re.fullmatch(provider + r'-[0-9a-f]{32}', folder.name):
                continue
            if folder.is_symlink():
                raise ValueError('Native agent session cannot be redirected')
            if not folder.is_dir():
                continue
            if client is None:
                client = importlib.import_module('management_portal.' + provider + '_client').shared_client()
            # The provider owns its launch lock and waits for native process exit.
            # Errors propagate: a caller must not move files after an unproved stop.
            client.close_session('', folder.name)
            closed.append({'provider': provider, 'session_id': folder.name})
    return {'closed': closed}


if __name__ == '__main__':
    print(json.dumps(close_all()))
