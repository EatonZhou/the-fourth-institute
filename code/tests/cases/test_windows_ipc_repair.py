"""Repair a known Windows preview without replacing its immutable instance.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from scripts.windows import repair_ipc as repair
from scripts import tfi_windows_launcher as launcher


@pytest.fixture
def installed(tmp_path, monkeypatch):
    platform = tmp_path / 'platform'
    root = platform / 'instance/example_instance'
    root.mkdir(parents=True)
    state = {'completed': ['prepare', 'database'], 'target': {
        'install_root': str(root), 'shared_code_root': str(platform / 'code')}}
    (root / 'installation.json').write_text(json.dumps(state))
    (platform / 'windows-package.json').write_text(json.dumps({'source_commit': repair.ORIGINAL_COMMIT}))
    target = platform / 'code/management_common/local_ipc.py'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'original fixture code')
    monkeypatch.setattr(repair, 'ORIGINAL_SHA256', hashlib.sha256(target.read_bytes()).hexdigest())
    payload = Path(__file__).resolve().parents[2] / 'management_common/local_ipc.py'
    monkeypatch.setattr(launcher, 'launcher_lock', lambda path: nullcontext())
    stopped = []
    monkeypatch.setattr(launcher, 'stop_all', lambda path: stopped.append(path))
    return platform, root, target, payload, stopped


def test_repair_preserves_instance_and_original_engineering_then_is_idempotent(installed):
    platform, root, target, payload, stopped = installed
    data = root / 'data/share/user-file.txt'
    data.parent.mkdir(parents=True)
    data.write_bytes(b'instance user data')
    manifest = platform / '.build/w/engineering/manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'immutable original engineering')
    before = {p: p.read_bytes() for p in (root / 'installation.json', data, manifest)}
    assert repair.repair(platform, payload) == 'repaired'
    assert stopped == [platform]
    assert target.read_bytes() == payload.read_bytes()
    assert (platform / '.build/windows/repairs/ipc-acl/local_ipc.before.py').read_bytes() == b'original fixture code'
    assert all(p.read_bytes() == contents for p, contents in before.items())
    assert repair.repair(platform, payload) == 'already repaired'
    assert stopped == [platform]


@pytest.mark.parametrize('case', ['modified', 'wrong-version', 'incomplete', 'bad-payload', 'locked', 'stop-failed'])
def test_invalid_or_busy_installations_are_not_patched(installed, monkeypatch, case):
    platform, root, target, payload, stopped = installed
    if case == 'modified':
        target.write_bytes(b'operator changes')
    elif case == 'wrong-version':
        (platform / 'windows-package.json').write_text('{"source_commit":"different"}')
    elif case == 'incomplete':
        (root / 'installation.json').write_text('{"completed":[]}')
    elif case == 'bad-payload':
        payload = platform / 'bad.py'
        payload.write_bytes(b'corrupt patch')
    def denied(*args):
        raise RuntimeError('fixture busy')
    if case == 'locked':
        monkeypatch.setattr(launcher, 'launcher_lock', denied)
    elif case == 'stop-failed':
        monkeypatch.setattr(launcher, 'stop_all', denied)
    before = target.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        repair.repair(platform, payload)
    assert target.read_bytes() == before
    assert not stopped
