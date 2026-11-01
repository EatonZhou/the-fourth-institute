"""Account daemon registration and the complete instance share one unit definition."""
import json
from pathlib import Path
from types import SimpleNamespace

from management_common import host_instances
from management_common.instance_manifest import load
from management_portal.execution import account_hosting
from scripts import portal_service_units as units, instance_services
from scripts import tfi_release_platform_linux as linux
from scripts.instance_service_definitions import render
from tests.cases.instance_test_support import write_instance


def test_new_account_definition_is_accepted_by_instance_control(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.setattr(host_instances, 'registry_path', lambda: tmp_path / 'host/instances.json')
    monkeypatch.setattr(account_hosting, 'ensure_account_home', lambda *args: tmp_path / 'account')
    monkeypatch.setattr(account_hosting, 'write_codex_launch', lambda account: None)
    directory = tmp_path / 'units'
    directory.mkdir()
    monkeypatch.setattr(linux, 'unit_directory', lambda: directory)
    def register(directory, definitions):
        for name, text in definitions.items():
            (directory / name).write_text(text)
        return list(definitions)
    monkeypatch.setattr(linux, 'register_services', register)
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='LoadState=loaded\nActiveState=active\nMainPID=123\n')
    monkeypatch.setattr(units, '_run', run)
    from tests.cases.provider_service_test_support import FakeControl
    control = FakeControl(directory)
    monkeypatch.setattr(instance_services, 'controller', lambda instance: control)
    state = units.start_codex_instance('codex_new')
    selected = load(root)
    name = selected.service_name('codex-app-server@codex_new') + '.service'
    assert state['pid'] == 123
    assert (directory / name).read_text() == render(selected, 'linux')[name]
    instance_services.verify_definitions(selected, selected_components=('codex-app-server@codex_new',))
    before = render(selected, 'linux')[name]
    host_instances.add_service_component(root, 'codex-app-server@codex_another')
    assert render(load(root), 'linux')[name] == before
    assert ('systemctl', '--user', 'enable', '--now', name) in calls
    assert not list(directory.glob('*@.service'))
