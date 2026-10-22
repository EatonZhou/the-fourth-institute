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


@pytest.mark.parametrize('ipc_already_patched', [False, True])
def test_startup_repair_supports_both_previews_and_preserves_state(installed, monkeypatch, ipc_already_patched):
    from scripts.windows import repair_startup as startup
    platform, root, target, payload, stopped = installed
    source = Path(__file__).resolve().parents[2]
    bundle = platform / 'payload'
    bundle.mkdir()
    (bundle / 'local_ipc.py').write_bytes(payload.read_bytes())
    if ipc_already_patched:
        target.write_bytes(payload.read_bytes())
        (platform / 'windows-package.json').write_text(json.dumps({'source_commit': startup.VERSIONS[1]}))
    updates = []
    for relative, _, _ in startup.UPDATES:
        original = ('original ' + relative).encode()
        destination = platform / 'code' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original)
        content = (source / relative).read_bytes()
        (bundle / Path(relative).name).write_bytes(content)
        # The real repair pins a historical bundle; this fixture supplies current
        # code and must declare that payload's digest without changing the pins.
        updates.append((relative, hashlib.sha256(original).hexdigest(), hashlib.sha256(content).hexdigest()))
    monkeypatch.setattr(startup, 'UPDATES', updates)
    state = (root / 'installation.json').read_bytes()
    assert startup.repair_install(platform, bundle) == 'repaired'
    assert startup.repair_install(platform, bundle) == 'already repaired'
    assert stopped == [platform]
    assert (root / 'installation.json').read_bytes() == state
    for relative, _, _ in updates:
        assert (platform / 'code' / relative).read_bytes() == (source / relative).read_bytes()


def test_later_payload_validation_prevents_partial_repair(installed):
    platform, _, target, payload, stopped = installed
    second = platform / 'code/management_portal/page_bootstrap.py'
    second.parent.mkdir()
    second.write_bytes(b'operator edit')
    with pytest.raises(ValueError, match='Installed code was modified'):
        repair.repair(platform, payload, extra_payloads=[('management_portal/page_bootstrap.py',
            payload, hashlib.sha256(b'expected source').hexdigest(), repair.REPAIRED_SHA256)])
    assert target.read_bytes() == b'original fixture code'
    assert second.read_bytes() == b'operator edit'
    assert not stopped


@pytest.mark.parametrize('version', [
    '28b94ae89bb7d1e1e966cde8472f9b25f5a41cca',
    '43d7e5544a564dc8625a2187bdfdd7c624797491',
])
def test_secret_repair_only_replaces_private_file_helper_and_preserves_state(installed, monkeypatch, version):
    from scripts.windows import repair_secrets as secrets
    platform, root, ipc, _, stopped = installed
    receipt = platform / 'windows-package.json'
    receipt.write_text(json.dumps({'source_commit': version}))
    target = ipc.with_name('file_permissions.py')
    original = b'original private-file helper'
    target.write_bytes(original)
    payload = Path(__file__).resolve().parents[2] / 'management_common/file_permissions.py'
    monkeypatch.setattr(secrets, 'OLD_SHA256', hashlib.sha256(original).hexdigest())
    preserved = {p: p.read_bytes() for p in (ipc, receipt, root / 'installation.json')}
    assert secrets.repair_install(platform, payload) == 'repaired'
    assert target.read_bytes() == payload.read_bytes()
    assert (platform / '.build/windows/repairs/secret-attributes/file_permissions.before.py').read_bytes() == original
    assert secrets.repair_install(platform, payload) == 'already repaired'
    assert stopped == [platform]
    assert all(path.read_bytes() == before for path, before in preserved.items())
    target.write_bytes(b'operator edit')
    with pytest.raises(ValueError, match='Installed code was modified'):
        secrets.repair_install(platform, payload)
    assert target.read_bytes() == b'operator edit'
    assert stopped == [platform]
