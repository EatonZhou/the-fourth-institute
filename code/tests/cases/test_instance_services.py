"""Manifest service ownership is checked before any process operation."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from management_common.instance_manifest import load
from scripts import instance_services as services
from scripts.instance_service_definitions import render
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def instance(tmp_path, monkeypatch):
    selected = load(write_instance(tmp_path / 'instance'))
    directory = tmp_path / 'units'
    directory.mkdir()
    for name, content in render(selected, 'linux').items():
        (directory / name).write_text(content)
    from scripts import tfi_release_platform_linux as linux

    def register(directory, units):
        calls.append(('register', tuple(sorted(units))))
        for name, content in units.items():
            (directory / name).write_text(content)
        return []

    def remove(directory, names):
        calls.append(('remove', tuple(names)))
        for name in names:
            (directory / name).unlink()
    native = SimpleNamespace(unit_directory=lambda: directory,
                             unit_path=lambda directory, name: directory / name,
                             manifest_unit_text=linux.manifest_unit_text,
                             register_services=register, remove_services=remove)
    states = {selected.service_name(c) + '.service': 'inactive' for c in selected.document['services']['components']}
    calls = []
    class Controller:
        def status(self, name):
            return {'load': 'loaded', 'active': states[name], 'pid': 1 if states[name] == 'active' else 0}
        def run(self, *arguments, **options):
            name = arguments[3]
            return SimpleNamespace(stdout=f'FragmentPath={directory / name}\nDropInPaths=\n')
        def start(self, name):
            calls.append(('start', name))
            states[name] = 'active'
        def stop(self, name):
            calls.append(('stop', name))
            states[name] = 'inactive'
    monkeypatch.setattr(services, 'platform_name', lambda: 'linux')
    monkeypatch.setattr(services, 'adapter', lambda: native)
    monkeypatch.setattr(services, 'controller', lambda instance: Controller())
    monkeypatch.setattr(services, 'wait_database', lambda instance: calls.append(('ready', 'postgres')))
    return selected, directory, states, calls


def test_start_selects_manifest_services_in_dependency_order(instance):
    selected, directory, states, calls = instance
    result = services.lifecycle('start', selected.root)
    assert result['status'] == 'started'
    assert [name for action, name in calls if action == 'start'][:2] == ['tfi-fixture-postgres.service', 'tfi-fixture-nats.service']
    assert set(name for action, name in calls if action == 'start') == set(states)
    assert calls[1] == ('ready', 'postgres')


def test_foreign_root_prevents_all_process_operations(instance):
    selected, directory, states, calls = instance
    path = directory / 'tfi-fixture-worker.service'
    path.write_text(path.read_text().replace(str(selected.root), '/another-instance'))
    with pytest.raises(ValueError, match='another instance'):
        services.lifecycle('start', selected.root)
    assert calls == []


def test_stopping_does_not_require_current_code_dependencies(instance, monkeypatch):
    selected, directory, states, calls = instance
    monkeypatch.setattr(services, 'render', lambda *args: pytest.fail('stop must not resolve new code dependencies'))
    assert services.verify_definitions(selected, exact=False) == directory


def test_stale_definition_is_rejected_before_start_but_remains_stoppable(instance):
    selected, directory, states, calls = instance
    path = directory / 'tfi-fixture-worker.service'
    path.write_text(path.read_text().replace('RestartSec=3', 'RestartSec=7'))
    with pytest.raises(ValueError, match='differs'):
        services.lifecycle('start', selected.root)
    assert calls == []
    assert services.verify_definitions(selected, exact=False) == directory


def test_start_can_leave_release_web_stopped_for_conversion(instance):
    selected, directory, states, calls = instance
    result = services.lifecycle('start', selected.root, excluded_components=('web',))
    assert result['skipped'] == ['web']
    assert states['tfi-fixture-web.service'] == 'inactive'
    assert ('start', 'tfi-fixture-worker.service') in calls


def test_database_readiness_failure_prevents_starting_consumers(instance, monkeypatch):
    selected, directory, states, calls = instance
    def unavailable(instance):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(services, 'wait_database', unavailable)
    with pytest.raises(RuntimeError, match='database unavailable'):
        services.lifecycle('start', selected.root)
    assert calls == [('start', 'tfi-fixture-postgres.service')]


def test_restart_record_does_not_accept_foreign_services(instance):
    selected, directory, states, calls = instance
    record = selected.root / services.RESTART_RECORD
    record.parent.mkdir(exist_ok=True)
    record.write_text('["foreign-codex.service"]')
    with pytest.raises(ValueError, match='unregistered services'):
        services.lifecycle('start', selected.root)
    assert calls == []


def test_only_recorded_accounts_restart_and_regain_boot_enable_state(instance, monkeypatch):
    selected, directory, states, calls = instance
    document = selected.document
    document['services']['components'] += ['codex-app-server@one', 'codex-app-server@two']
    (selected.root / 'instance.json').write_text(json.dumps(document))
    selected = load(selected.root)
    for name, content in render(selected, 'linux').items():
        (directory / name).write_text(content)
        states.setdefault(name, 'inactive')
    record = selected.root / services.RESTART_RECORD
    record.parent.mkdir(exist_ok=True)
    record.write_text(json.dumps(['tfi-fixture-codex-app-server@one.service']))
    control = services.controller(selected)
    run = control.run
    def observed(*args, **kwargs):
        if 'enable' in args:
            calls.append(('enable', args[3:]))
        return run(*args, **kwargs)
    control.run = observed
    monkeypatch.setattr(services, 'controller', lambda instance: control)
    services.lifecycle('start', selected.root)
    assert ('start', 'tfi-fixture-codex-app-server@one.service') in calls
    assert ('start', 'tfi-fixture-codex-app-server@two.service') not in calls
    assert ('enable', ('tfi-fixture-codex-app-server@one.service',)) in calls
    assert not record.exists()


@pytest.mark.parametrize('platform', ['macos', 'windows'])
def test_native_repeated_stop_retains_running_account_selection(instance, monkeypatch, platform):
    selected, directory, states, calls = instance
    document = selected.document
    document['services']['components'] += ['codex-app-server@one', 'codex-app-server@two']
    (selected.root / 'instance.json').write_text(json.dumps(document))
    selected = load(selected.root)
    monkeypatch.setattr(services, 'platform_name', lambda: platform)
    monkeypatch.setattr(services, 'verify_definitions', lambda *args, **kwargs: directory)
    states.clear()
    states.update({services.unit_name(selected, c): 'inactive' for c in document['services']['components']})
    one = services.unit_name(selected, 'codex-app-server@one')
    states[one] = 'active'
    services.lifecycle('stop', selected.root)
    services.lifecycle('stop', selected.root)
    assert services.recorded_accounts(selected.root / services.RESTART_RECORD) == {one}
    calls.clear()
    services.lifecycle('start', selected.root)
    assert ('start', one) in calls
    assert ('start', services.unit_name(selected, 'codex-app-server@two')) not in calls


def test_definitions_are_refreshed_only_for_the_same_instance(instance, monkeypatch):
    selected, directory, states, calls = instance
    worker = directory / 'tfi-fixture-worker.service'
    worker.write_text(worker.read_text().replace('RestartSec=3', 'RestartSec=7'))
    services.install_definitions(selected)
    assert services.verify_definitions(selected) == directory
    assert calls[-1][0] == 'register'
    worker.write_text(worker.read_text().replace(str(selected.root), '/another-instance'))
    with pytest.raises(ValueError, match='another instance'):
        services.install_definitions(selected)
    worker.unlink()
    # A loaded unit without this instance's definition belongs elsewhere.
    with pytest.raises(ValueError, match='loaded from elsewhere'):
        services.install_definitions(selected, ('worker',))


def test_definitions_are_removed_only_when_stopped_and_owned(instance):
    selected, directory, states, calls = instance
    states['tfi-fixture-web.service'] = 'active'
    with pytest.raises(RuntimeError, match='Stop the service'):
        services.remove_definitions(selected)
    states['tfi-fixture-web.service'] = 'inactive'
    (directory / 'tfi-fixture-nats.service').unlink()
    removed = services.remove_definitions(selected)
    assert 'tfi-fixture-nats.service' not in removed and 'tfi-fixture-web.service' in removed
    assert not list(directory.iterdir())


def test_incomplete_creation_blocks_start_but_not_stop(instance):
    selected, directory, states, calls = instance
    record = selected.root / services.CREATION_RECORD
    record.parent.mkdir(exist_ok=True)
    record.write_text(json.dumps({'phase': 'database', 'status': 'failed', 'error': 'psql exited 3'}))
    with pytest.raises(ValueError, match='creation has not completed: psql exited 3'):
        services.lifecycle('start', selected.root)
    assert calls == []
    services.lifecycle('start', selected.root, creating=True)
    assert ('start', 'tfi-fixture-web.service') in calls


def test_instance_jobs_are_owned_units(instance):
    selected, directory, states, calls = instance
    assert 'tfi-fixture-provider-activation.service' in services.owned_units(selected)
    with pytest.raises(ValueError):
        services.job_unit(selected, 'arbitrary')


def test_macos_loaded_job_must_come_from_the_instance_plist(instance, monkeypatch):
    selected, directory, states, calls = instance
    monkeypatch.setattr(services, 'platform_name', lambda: 'macos')
    monkeypatch.setattr(services, 'definition_root', lambda text, platform: str(selected.root))
    control = services.controller(selected)
    paths = {}
    control._launch_target = lambda name: 'gui/1/' + name
    control.run = lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=f"\tpath = {paths.get(args[2], directory / args[2].removeprefix('gui/1/'))}\n")
    monkeypatch.setattr(services, 'controller', lambda instance: control)
    assert services.verify_definitions(selected, exact=False) == directory
    paths['gui/1/tfi-fixture-web.service'] = '/Users/other/Library/LaunchAgents/foreign.plist'
    with pytest.raises(ValueError, match='different definition'):
        services.verify_definitions(selected, exact=False)
