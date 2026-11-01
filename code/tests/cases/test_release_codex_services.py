"""Provider control only accepts registered services and registered code."""
import json
import subprocess
import pytest
from scripts import portal_service_units as units
from scripts import tfi_release_platform_linux as linux
from tests.cases.instance_test_support import write_instance


def test_account_control_uses_manifest_inventory_and_user_bus(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    document = json.loads((root / 'instance.json').read_text())
    document['services']['components'].append('codex-app-server@codex_two')
    (root / 'instance.json').write_text(json.dumps(document))
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.setattr(linux, 'unit_directory', lambda: tmp_path / 'units')
    calls = []
    monkeypatch.setattr(units.subprocess, 'run', lambda args, **kw:
                        calls.append((args, kw)) or subprocess.CompletedProcess(args, 0, ''))
    name = 'tfi-fixture-codex-app-server@codex_two.service'
    assert units._codex_unit('codex_two') == name
    units._run('systemctl', '--user', 'start', name)
    assert calls[0][1]['env']['DBUS_SESSION_BUS_ADDRESS'].endswith('/bus')
    for other in ['foreign-codex.service', 'tfi-fixture-unregistered.service']:
        with pytest.raises(ValueError, match='unregistered instance'):
            units._run('systemctl', '--user', 'stop', other)
    assert len(calls) == 1
    with pytest.raises(ValueError, match='not registered'):
        units._codex_unit('codex_other')


@pytest.mark.parametrize('worktree', ['dev', 'release', 'foreign'])
def test_provider_control_accepts_only_registered_worktrees(tmp_path, monkeypatch, worktree):
    dev, release = tmp_path / 'dev/code', tmp_path / 'release/code'
    root = write_instance(tmp_path / 'instance', code=release)
    document = json.loads((root / 'instance.json').read_text())
    document['deployments']['dev'] = {'kind': 'dev', 'code_root': str(dev), 'web_port': 28087}
    document['network']['entry_port'] = 28088
    document['services']['components'] += ['dev-web', 'gateway']
    (root / 'instance.json').write_text(json.dumps(document))
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.setattr(linux, 'unit_directory', lambda: tmp_path / 'units')
    monkeypatch.setattr(units, 'SOURCE_ROOT', {'dev': dev, 'release': release, 'foreign': tmp_path / 'other'}[worktree])
    if worktree == 'foreign':
        with pytest.raises(ValueError, match='code is not registered'):
            units._instance()
    else:
        assert units._instance().root == root
