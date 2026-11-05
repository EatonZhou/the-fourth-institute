"""Activation owns service coordinates; package availability is not readiness."""
import json
import plistlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import portal_service_units as units
from management_portal.execution import provider_updates as updates
from management_portal.execution.provider_preparation import PreparationError


@pytest.fixture(params=['win32', 'darwin'])
def native(request, monkeypatch, tmp_path):
    """Native daemons are registered components defined through the instance."""
    from scripts import instance_services, tfi_release_platform_windows as win, tfi_release_platform_macos as mac
    from tests.cases.provider_service_test_support import FakeControl, provider_instance, real_provider_links
    links = real_provider_links()
    root, _ = provider_instance(monkeypatch, tmp_path, components=('claude-code', 'kimi-code'))
    adapter = win if request.param == 'win32' else mac
    directory = tmp_path / 'definitions'
    directory.mkdir()
    monkeypatch.setattr(units, 'sys', SimpleNamespace(platform=request.param))
    monkeypatch.setattr(instance_services, 'platform_name', lambda: 'windows' if request.param == 'win32' else 'macos')
    monkeypatch.setattr(instance_services, 'definition_directory', lambda instance: directory)
    controller = FakeControl(directory, adapter.unit_path)
    monkeypatch.setattr(instance_services, 'controller', lambda instance: controller)
    monkeypatch.setattr(updates, 'STATE_ROOT', tmp_path / 'state')
    yield adapter, root, controller, directory
    assert real_provider_links() == links


def _definition(adapter, root, directory, component):
    from management_common.instance_manifest import load
    instance = load(root)
    name = instance.service_name(component) + ('' if adapter.__name__.endswith('_windows') else '.service')
    return name, adapter.unit_path(directory, name), adapter.manifest_unit_text(instance, component)


def test_native_absent_stopped_and_running_daemons(native):
    adapter, root, controller, directory = native
    for component in ['claude-code', 'kimi-code']:
        unit = f'portal-{component}.service'
        name, path, text = _definition(adapter, root, directory, component)
        assert units.provider_runtime_services(unit) == ()
        path.write_text(text)
        assert units.provider_runtime_services(unit) == ()
        controller.active.add(name)
        assert units.provider_runtime_services(unit) == (name,)
        if adapter.__name__.endswith('_windows'):
            foreign = json.loads(text); foreign['environment']['PORTAL_INSTANCE_ROOT'] = '/another'
            path.write_text(json.dumps(foreign))
        else:
            foreign = plistlib.loads(text.encode()); foreign['EnvironmentVariables']['PORTAL_INSTANCE_ROOT'] = '/another'
            path.write_bytes(plistlib.dumps(foreign))
        with pytest.raises(ValueError, match='another instance'):
            units.provider_runtime_services(unit)
        assert not [call for call in controller.calls if call[0] in {'start', 'restart'}]


def test_native_first_start_rolls_back_failed_health(native, monkeypatch):
    from management_common.instance_manifest import load
    from tests.cases.provider_service_test_support import installed_claude_sdk
    installed_claude_sdk(monkeypatch)
    adapter, root, controller, directory = native
    document = json.loads((root / 'instance.json').read_text())
    document['services']['components'].remove('claude-code')
    (root / 'instance.json').write_text(json.dumps(document))
    removed = []

    def register(target, definitions, **_):
        for name, text in definitions.items():
            adapter.unit_path(target, name).write_text(text)
        return list(definitions)

    def remove(target, names):
        removed.extend(names)
        for name in names:
            adapter.unit_path(target, name).unlink()
    monkeypatch.setattr(adapter, 'register_services', register)
    monkeypatch.setattr(adapter, 'remove_services', remove)

    def fail():
        raise TimeoutError('not ready')
    with pytest.raises(TimeoutError):
        units.start_claude_service(wait_ready=fail)
    name = removed[0]
    assert [call for call in controller.calls if call[0] in {'start', 'stop'}] == [('start', name), ('stop', name)]
    assert 'claude-code' not in load(root).document['services']['components']


def test_codex_update_restarts_account_hosts_through_instance_controller(monkeypatch, tmp_path):
    from scripts import instance_services
    from tests.cases.provider_service_test_support import provider_instance
    provider_instance(monkeypatch, tmp_path)
    monkeypatch.setattr(updates, 'sys', SimpleNamespace(platform='win32'))
    own = Mock(); own.status.return_value = {'active': 'active'}
    monkeypatch.setattr(instance_services, 'controller', lambda instance: own)
    monkeypatch.setattr(updates, '_active_executions', lambda _: 0)
    runtime = updates.CodexRuntime()
    monkeypatch.setattr(updates.CodexRuntime, 'services', property(lambda _: ('account-host',)))
    monkeypatch.setattr(runtime, 'activation_link', lambda: tmp_path/'link')
    monkeypatch.setattr(updates, '_link', lambda *a: None)
    assert updates._activate('codex', runtime, tmp_path/'new')
    own.restart.assert_called_once_with('account-host')


def test_windows_user_restart_uses_same_directory():
    from scripts.tfi_windows_user_services import WindowsServiceControl
    controller = WindowsServiceControl('/owned/units')
    controller.stop = Mock(); controller.start = Mock()
    controller.restart('owned-account')
    controller.stop.assert_called_once_with('owned-account')
    controller.start.assert_called_once_with('owned-account')


@pytest.mark.parametrize('where', ['download', 'dependency_install', 'executable_validation', 'service_activation'])
def test_preparation_phase_is_sanitized_and_retry_reuses_staged_package(monkeypatch, tmp_path, where):
    from management_portal import business_tasks
    monkeypatch.setattr(updates, 'STATE_ROOT', tmp_path/'state')
    monkeypatch.setattr(business_tasks, 'start', lambda _, work: work())
    root = tmp_path/'package'; root.mkdir(); (root/'.portal-validated').touch()
    runtime = Mock()
    monkeypatch.setattr(updates, 'runtimes', lambda: {'claude':runtime})
    monkeypatch.setattr(updates, 'runtime_available', lambda _:False)
    monkeypatch.setattr(updates, '_runtime_installed', lambda _:False)
    runtime.available.return_value=('1',); runtime.stage.return_value=root
    action = Mock(return_value=True)
    monkeypatch.setattr(updates, '_activate', action)
    error = RuntimeError('secret=https://user:password@server/')
    if where=='download': runtime.available.side_effect=error
    elif where=='dependency_install': runtime.stage.side_effect=error
    elif where=='executable_validation': runtime.stage.side_effect=PreparationError(where,error)
    else: action.side_effect=PreparationError(where,error)
    updates.prepare_runtime('claude')
    status=updates.preparation_status('claude')
    assert status['status']=='failed' and status['phase']==where
    assert 'password' not in str(status) and 'https' not in str(status)
    if where=='service_activation':
        runtime.available.reset_mock(); runtime.stage.reset_mock()
        action.side_effect=None
        updates.prepare_runtime('claude')
        runtime.available.assert_not_called(); runtime.stage.assert_not_called()
        assert json.loads((updates.STATE_ROOT/'provider-updates.json').read_text())['preparations']['claude']['status']=='ready'


def test_installed_runtime_is_activated_without_package_reinstall(monkeypatch, tmp_path):
    from management_portal import business_tasks
    runtime=Mock()
    monkeypatch.setattr(updates,'STATE_ROOT',tmp_path)
    monkeypatch.setattr(updates,'runtimes',lambda:{'claude':runtime})
    monkeypatch.setattr(updates,'runtime_available',lambda _:False)
    monkeypatch.setattr(updates,'_runtime_installed',lambda _:True)
    monkeypatch.setattr(business_tasks,'start',lambda _,work:work())
    updates.prepare_runtime('claude')
    runtime.ensure_ready.assert_called_once()
    runtime.stage.assert_not_called()
    assert json.loads((updates.STATE_ROOT/'provider-updates.json').read_text())['preparations']['claude']['status']=='ready'


def test_deferred_first_preparation_starts_daemon_and_clears_error(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, 'STATE_ROOT', tmp_path)
    runtime = Mock()
    monkeypatch.setattr(updates, 'runtimes', lambda: {'claude': runtime})
    activate = Mock(return_value=True)
    monkeypatch.setattr(updates, '_activate', activate)
    (tmp_path/'provider-updates.json').write_text(json.dumps({'pending': {'claude': str(tmp_path/'pkg')},
        'preparations': {'claude': {'status':'pending', 'message':'wait'}}}))
    assert updates.activate_pending_once()['activated'] == ['claude']
    activate.assert_called_once_with('claude', runtime, tmp_path/'pkg', prepare=True)
    result=json.loads((tmp_path/'provider-updates.json').read_text())
    assert result['preparations']['claude']['status']=='ready'
    assert 'message' not in result['preparations']['claude']


def test_kimi_ownership_checks_creation_and_home_before_stop(monkeypatch, tmp_path):
    import psutil
    from management_portal import kimi_account_runtime as kimi
    process=Mock(); process.is_running.return_value=True; process.create_time.return_value=8
    process.environ.return_value={'KIMI_CODE_HOME':str(tmp_path)}
    monkeypatch.setattr(psutil, 'Process', lambda _:process)
    assert kimi._owned_process(tmp_path, {'pid':123,'created':7}) is None
    assert kimi._owned_process(tmp_path, {'pid':123,'created':8}) is process
    process.environ.return_value={'KIMI_CODE_HOME':str(tmp_path/'other')}
    terminate=Mock(); monkeypatch.setattr(kimi,'terminate_process_tree',terminate)
    with pytest.raises(RuntimeError, match='ownership'):
        kimi._stop_server({'account_id':'one'},tmp_path,{'pid':123,'port':5,'created':8})
    terminate.assert_not_called()


def test_kimi_busy_account_defers_without_switching_runtime(monkeypatch, tmp_path):
    from management_portal import kimi_account_runtime as kimi
    monkeypatch.setattr(updates,'_active_executions', lambda _:0)
    monkeypatch.setattr(kimi,'runtime_busy', lambda:True)
    switch=Mock(); monkeypatch.setattr(updates,'_link',switch)
    assert updates._activate('kimi',updates.KimiRuntime(),tmp_path/'new') is False
    switch.assert_not_called()


def test_kimi_account_restart_rolls_back_previous_runtime(monkeypatch,tmp_path):
    old=tmp_path/'old'; old.mkdir()
    link=tmp_path/'current'; link.symlink_to(old, target_is_directory=True)
    runtime=updates.KimiRuntime()
    monkeypatch.setattr(updates,'_active_executions',lambda _:0)
    monkeypatch.setattr(runtime,'busy',lambda:False)
    monkeypatch.setattr(updates.KimiRuntime,'services',property(lambda _:()))
    monkeypatch.setattr(runtime,'activation_link',lambda:link)
    monkeypatch.setattr(runtime,'account_processes',lambda:('one',))
    restart=Mock(side_effect=[RuntimeError('secret'), None])
    monkeypatch.setattr(runtime,'restart_account_process',restart)
    with pytest.raises(PreparationError) as error:
        updates._activate('kimi',runtime,tmp_path/'new')
    assert link.resolve()==old and restart.call_count==2
    assert not error.value.rollback_failed


def test_codex_update_rejects_service_that_is_not_a_registered_account(native):
    from management_common.host_instances import add_service_component
    adapter, root, controller, directory = native
    instance = add_service_component(root, 'codex-app-server@owned')
    name, path, text = _definition(adapter, root, directory, 'codex-app-server@owned')
    path.write_text(text)
    units.verify_native_codex_services([name])
    with pytest.raises(ValueError, match='not a registered account host'):
        units.verify_native_codex_services([name.replace('owned', 'foreign')])
    path.write_text(text.replace(str(instance.root), '/another'))
    with pytest.raises(ValueError, match='another instance'):
        units.verify_native_codex_services([name])
