"""Activation owns service coordinates; package availability is not readiness."""
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import portal_service_units as units
from management_portal.execution import provider_updates as updates
from management_portal.execution.provider_preparation import PreparationError


@pytest.fixture(params=['win32', 'darwin'])
def native(request, monkeypatch, tmp_path):
    from scripts import tfi_release_platform_windows as win, tfi_release_platform_macos as mac
    from management_common import instance_config
    adapter = win if request.param == 'win32' else mac
    target = dict(service_prefix='tfi-owned', install_root=str(tmp_path),
                  shared_code_root=str(units.SOURCE_ROOT), data_roots={}, python='/python', windows_service_mode='user')
    monkeypatch.setattr(units, 'sys', SimpleNamespace(platform=request.param))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(tmp_path/'config/target.json'))
    # An installed instance: its state file exists and records this source tree.
    (tmp_path/instance_config.STATE_FILE).write_text('{}')
    monkeypatch.setattr(instance_config, 'read_installation',
                        lambda _: {'schema': instance_config.STATE_SCHEMA, 'target': target})
    monkeypatch.setattr(adapter, 'unit_directory', lambda: tmp_path)
    controller = Mock()
    controller.status.return_value = {'active': 'inactive'}
    controller.run.return_value = SimpleNamespace(returncode=1)
    monkeypatch.setattr(adapter, 'service_controller', lambda: controller)
    if request.param == 'win32':
        monkeypatch.setattr(adapter, 'verify_service', lambda *a: None)
    return adapter, target, controller, tmp_path


def test_native_absent_stopped_and_running_daemons(native):
    adapter, target, controller, directory = native
    for component in ['claude', 'kimi']:
        unit = f'portal-{component}-code.service'
        name = adapter.unit_name(target, component)
        assert units.provider_runtime_services(unit) == ()
        path = adapter.unit_path(directory, name)
        path.write_text(adapter.unit_text(target, component, (component,)))
        controller.status.return_value = {'active': 'inactive'}
        assert units.provider_runtime_services(unit) == ()
        controller.status.return_value = {'active': 'active'}
        assert units.provider_runtime_services(unit) == (name,)
        path.write_text('{}')
        with pytest.raises(ValueError):
            units.provider_runtime_services(unit)
        controller.restart.assert_not_called()


def test_native_first_start_rolls_back_failed_health(native, monkeypatch):
    adapter, target, controller, directory = native
    monkeypatch.setattr(updates, 'STATE_ROOT', directory)
    name = adapter.unit_name(target, 'claude')
    monkeypatch.setattr(adapter, 'register_services', Mock(return_value=[name]))
    remove = Mock()
    monkeypatch.setattr(adapter, 'remove_services', remove)
    def fail():
        raise TimeoutError('not ready')
    with pytest.raises(TimeoutError):
        units.start_claude_service(wait_ready=fail)
    controller.start.assert_called_once_with(name)
    controller.stop.assert_called_once_with(name)
    remove.assert_called_once_with(directory, [name])


def test_macos_foreign_loaded_job_rejected(native):
    adapter, target, controller, directory = native
    if units.sys.platform != 'darwin':
        return
    name = adapter.unit_name(target, 'claude')
    adapter.unit_path(directory, name).write_text(adapter.unit_text(target, 'claude', ('claude',)))
    controller.run.return_value = SimpleNamespace(returncode=0, stdout='path = /foreign/job.plist\n')
    with pytest.raises(ValueError, match='foreign loaded'):
        units.provider_runtime_services('portal-claude-code.service')


def test_codex_update_uses_account_controller_not_daemon_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, 'sys', SimpleNamespace(platform='win32'))
    own = Mock(); own.status.return_value = {'active': 'active'}
    monkeypatch.setattr(units, '_native_codex_controller', lambda: own)
    monkeypatch.setattr(updates, '_service_control', lambda: pytest.fail('daemon controller used for account host'))
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


def test_scm_installer_registers_optional_daemons_without_starting(monkeypatch):
    from scripts import tfi_release_install as install
    seen = []
    monkeypatch.setattr(install, 'unit_directory', lambda: Path('/units'))
    monkeypatch.setattr(install, 'unit_text', lambda _, c: c)
    monkeypatch.setattr(install, 'unit_name', lambda _, c: c)
    monkeypatch.setattr(install, 'platform_adapter', lambda: SimpleNamespace(register_services=lambda d,u: seen.append(u)))
    install.register_services({'windows_service_mode': 'scm'})
    assert set(seen[0]) == set(install.COMPONENTS) | {'claude','kimi'}
    assert install.registered_components({'windows_service_mode':'user'}) == install.COMPONENTS


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


def test_native_lifecycle_stops_and_restores_only_previously_running_services(tmp_path):
    from scripts import tfi_release_provider_services as services
    controller=Mock()
    state={'running':True, 'unused':False}
    controller.status.side_effect=lambda n: {'active':'active' if state[n] else 'inactive','pid':1 if state[n] else 0}
    controller.stop.side_effect=lambda n: state.update({n:False})
    plan=services._native_plan({'install_root':str(tmp_path)},'windows',['running','unused'],{})
    services.stop_services(plan,controller)
    services.start_services(plan,controller)
    controller.start.assert_called_once_with('running')


def test_codex_update_rejects_active_service_without_owned_definition(monkeypatch, tmp_path):
    from management_common import instance_config
    from scripts import tfi_release_provider_services as owners, tfi_release_platform_windows as win
    monkeypatch.setattr(units, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(tmp_path/'config/target.json'))
    monkeypatch.setattr(instance_config,'read_installation',lambda _: {'target':{}})
    monkeypatch.setattr(win,'unit_directory',lambda:tmp_path)
    monkeypatch.setattr(owners,'owned_services',lambda *a:{'names':['owned']})
    units.verify_native_codex_services(['owned'])
    with pytest.raises(ValueError,match='owned service'):
        units.verify_native_codex_services(['foreign'])


def test_windows_owned_daemon_discovery_rejects_other_instance(native, monkeypatch):
    if units.sys.platform != 'win32':
        return
    from scripts import tfi_release_provider_services as owners
    adapter,target,_,directory=native
    monkeypatch.setattr(owners,'sys',units.sys)
    name=adapter.unit_name(target,'kimi')
    path=adapter.unit_path(directory,name)
    path.write_text(adapter.unit_text(target,'kimi',()))
    assert owners._native_daemons(target,directory,adapter)==[name]
    definition=json.loads(path.read_text());definition['root']=str(directory/'foreign')
    path.write_text(json.dumps(definition))
    with pytest.raises(ValueError,match='another instance'):
        owners._native_daemons(target,directory,adapter)
