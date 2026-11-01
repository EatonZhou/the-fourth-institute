"""Isolation for tests that stage Provider programs or define Provider services.

The selected instance runs the real code, so code_environment would place
Provider programs beside that code. Tests redirect the one owner of that
location (code_environment.provider_directory) and the updater's import-time
copy, and replace the OS service controller, so no real program link, unit
directory or service manager is touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from management_common import code_environment
from tests.cases.instance_test_support import write_instance


def real_provider_links():
    """Read-only snapshot of the real code's Provider entries."""
    directory = Path(__file__).resolve().parents[3] / 'dependencies/providers'
    if not directory.is_dir():
        return {}
    return {path.name: os.readlink(path) for path in directory.iterdir() if path.is_symlink()}


def provider_instance(monkeypatch, tmp_path, *, components=()):
    """A manifest-only instance running this code, with private Provider programs."""
    from management_portal.execution import provider_updates as updates
    root = write_instance(tmp_path / 'instance')
    document = json.loads((root / 'instance.json').read_text())
    document['services']['components'].extend(components)
    (root / 'instance.json').write_text(json.dumps(document))
    for name in ('runtime', 'secrets'):
        (root / name).mkdir(exist_ok=True)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    runtimes = tmp_path / 'dependencies/providers'
    runtimes.mkdir(parents=True)
    monkeypatch.setattr(code_environment, 'provider_directory', lambda instance=None: runtimes)
    monkeypatch.setattr(updates, 'RUNTIMES', runtimes)
    monkeypatch.setattr(updates, 'STATE_ROOT', root / 'data/provider-state')
    return root, runtimes


class FakeControl:
    """An OS service controller over a definition directory, without a service manager."""

    def __init__(self, directory, unit_path=lambda directory, name: directory / name):
        self.directory = Path(directory)
        self.unit_path = unit_path
        self.active = set()
        self.calls = []
        self.dropins = ''

    def status(self, name):
        defined = self.unit_path(self.directory, name).exists()
        active = name in self.active
        return {'service': name, 'load': 'loaded' if defined else 'not-found',
                'active': 'active' if active else 'inactive', 'sub': 'running' if active else 'dead',
                'pid': 1 if active else 0}

    @staticmethod
    def _launch_target(name):
        return 'gui/0/' + name

    def run(self, *args, check=True):
        # systemctl show FragmentPath,DropInPaths, or launchctl print, for definition verification.
        self.calls.append(args)
        if args[:2] == ('launchctl', 'print'):
            name = args[2].removeprefix('gui/0/')
            return subprocess.CompletedProcess(args, 0, stdout=f"\tpath = {self.unit_path(self.directory, name)}\n",
                                               stderr='')
        name = args[3]
        return subprocess.CompletedProcess(args, 0, stdout=(
            f"FragmentPath={self.unit_path(self.directory, name)}\nDropInPaths={self.dropins}\n"), stderr='')

    def start(self, *names):
        self.calls.append(('start', *names))
        self.active.update(names)

    def stop(self, *names):
        self.calls.append(('stop', *names))
        self.active.difference_update(names)

    def restart(self, name):
        self.calls.append(('restart', name))
        self.active.add(name)
