"""Durable publication intent; Git preparation and activation have separate locks."""
from contextlib import contextmanager
from management_common.file_lock import lock_stream
import json
import os
from pathlib import Path
import time

QUIET_SECONDS = 5.0
MAX_SECONDS = 30.0


@contextmanager
def preparation_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'portal-publish-prepare.lock').open('a+b') as stream, lock_stream(stream):
        yield


def read(root: Path) -> dict:
    try:
        return json.loads((root / 'portal-publish-pending.json').read_text())
    except FileNotFoundError:
        return {}


def _write(root: Path, value: dict) -> None:
    target = root / 'portal-publish-pending.json'
    temporary = target.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(target)


def submit(root: Path, commit: str) -> None:
    """Caller holds preparation_lock, including the Git commit operation."""
    previous = read(root)
    now = time.time()
    first = previous.get('first_requested_at', now) if previous.get('pending') else now
    _write(root, {'commit': commit, 'first_requested_at': first,
                  'requested_at': now, 'pending': True})


def wait(root: Path) -> None:
    while True:
        value = read(root)
        if not value.get('pending'):
            return
        due = min(value['requested_at'] + QUIET_SECONDS, value['first_requested_at'] + MAX_SECONDS)
        delay = due - time.time()
        if delay <= 0:
            return
        time.sleep(min(delay, 0.2))


def claim(root: Path, fallback: str) -> str:
    """Activation owns switch_lock; new submissions form the next batch."""
    with preparation_lock(root):
        value = read(root)
        commit = value.get('commit') or fallback
        if value:
            value['pending'] = False
            _write(root, value)
        return commit
