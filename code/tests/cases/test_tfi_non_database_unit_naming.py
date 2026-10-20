"""Relay unit rename and resumable activation without live systemd operations.

:module: operations.deployment
:design: decision_15aab18c0ab3
"""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import portal_service_units as units
from scripts import portal_source_layout as layout
from scripts import portal_instance_storage as storage

OLD = 'archive-ai-socket-relay.service'
NEW = 'portal-ai-socket-relay.service'
ROOT = Path(units.__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    from management_common import portal_paths, instance_paths
    config = tmp_path/'site.json'
    config.write_text(json.dumps({'deployment':{'source_root':str(ROOT),'compatibility':{'domain_values':{'ai_socket_relay_unit':OLD},'environment_aliases':{'PORTAL_AI_RELAY_PORT':['PORTAL_AI_RELAY_PORT']}},'relay':{'bind':'127.0.0.2','port':19484,'allowed_ips':['127.0.0.2']}}}))
    paths=tmp_path/'paths.json'
    paths.write_text(json.dumps({key:str(tmp_path/key) for key in portal_paths.KEYS}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG',str(config))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG',str(paths))
    monkeypatch.setenv('PORTAL_NETWORK_CONFIG',str(tmp_path/'network.env'))
    monkeypatch.setattr(portal_paths,'CONFIG_PATH',paths)
    for canonical in portal_paths.ENV_KEYS.values():
        monkeypatch.delenv(canonical,raising=False)
        monkeypatch.delenv(canonical.replace('PORTAL_','ARCHIVE_',1),raising=False)
    monkeypatch.setattr(instance_paths,'CONFIG_PATH',tmp_path/'instances.json')
    monkeypatch.setattr(units,'DEV_ROOT',ROOT)
    monkeypatch.setattr(units,'DEV_WEB_UNIT_SOURCE',ROOT/'management_portal/management-portal.service')
    monkeypatch.setattr(units,'USER_UNIT_ROOT',tmp_path/'units')
    units.USER_UNIT_ROOT.mkdir()
    monkeypatch.setattr(units, 'LEGACY_AI_SOCKET_RELAY_UNIT', OLD)
    return tmp_path


def test_normal_installer_installs_new_identity_without_touching_running_legacy(deployment, monkeypatch):
    legacy=units.USER_UNIT_ROOT/OLD
    legacy.write_text('existing legacy definition')
    calls=[]
    monkeypatch.setattr(units,'_run',lambda *args,**kwargs:calls.append(args))
    result=units.install_dev_units()
    installed={item['unit'] for item in result['installed']}
    assert NEW in installed and OLD not in installed
    assert (units.USER_UNIT_ROOT/NEW).is_file()
    assert legacy.read_text()=='existing legacy definition'
    assert calls==[('systemctl','--user','daemon-reload')]
    assert 'portal-postgresql.service' in layout.DEV_AUX_UNITS
    assert '@PORTAL_' not in (units.USER_UNIT_ROOT/NEW).read_text()
    assert 'PORTAL_AI_RELAY_PORT=19484' in (units.USER_UNIT_ROOT/NEW).read_text()


class Systemd:
    def __init__(self, root):
        self.root=root
        self.calls=[]
        self.fail_start=False
        self.reactivate=False
        self.invalid_show=False
        self.dropins=''
        self.fragment=None
        self.states={OLD:('loaded','inactive','dead',0,'enabled'),NEW:('not-found','inactive','dead',0,'')}

    def __call__(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        assert args[:2]==('systemctl','--user')
        action=args[2]
        if action=='daemon-reload':
            if not (self.root/OLD).exists() and self.states[OLD][3]==0:
                self.states[OLD]=('not-found','inactive','dead',0,'')
            return SimpleNamespace(returncode=0,stdout='')
        unit=args[-1] if action!='show' else args[3]
        assert unit in {OLD,NEW}, 'Migration must never control another unit'
        if action=='show':
            if self.invalid_show:
                return SimpleNamespace(returncode=1,stdout='')
            load,active,sub,pid,enabled=self.states[unit]
            if unit==NEW and (self.root/NEW).exists() and load=='not-found':
                load='loaded'
            fragment=self.fragment if unit==OLD and self.fragment else str(self.root/unit) if load=='loaded' else ''
            fields={'Id':unit,'LoadState':load,'ActiveState':active,'SubState':sub,'MainPID':str(pid),
                    'UnitFileState':enabled,'FragmentPath':fragment,'DropInPaths':self.dropins if unit==OLD else ''}
            return SimpleNamespace(returncode=4 if load=='not-found' else 0,stdout='\n'.join(f'{k}={v}' for k,v in fields.items()))
        if action=='disable':
            assert args==('systemctl','--user','disable',OLD)
            load,active,sub,pid,_=self.states[OLD]
            self.states[OLD]=(load,active,sub,pid,'disabled')
        elif action=='enable':
            assert args==('systemctl','--user','enable','--now',NEW)
            if self.fail_start:
                raise subprocess.CalledProcessError(1,args,output='isolated start failure')
            if self.states[NEW][3]==0:
                self.states[NEW]=('loaded','active','running',42,'enabled')
            if self.reactivate:
                self.states[OLD]=('loaded','active','running',99,'enabled')
        elif action=='reset-failed':
            assert unit==OLD
        else:
            raise AssertionError(f'Unexpected or unsafe operation: {args}')
        return SimpleNamespace(returncode=0,stdout='')


@pytest.fixture
def systemd(deployment, monkeypatch):
    legacy=units.USER_UNIT_ROOT/OLD
    legacy.write_text('legacy preserved until successful replacement')
    fake=Systemd(units.USER_UNIT_ROOT)
    monkeypatch.setattr(units,'_run',fake)
    return fake


def test_preparation_is_repeatable_without_starting_either_service(systemd):
    before=(units.USER_UNIT_ROOT/OLD).read_bytes()
    units.install_ai_socket_relay_unit()
    first=(units.USER_UNIT_ROOT/NEW).read_bytes()
    units.install_ai_socket_relay_unit()
    assert (units.USER_UNIT_ROOT/NEW).read_bytes()==first
    assert (units.USER_UNIT_ROOT/OLD).read_bytes()==before
    assert all(args[2]=='daemon-reload' for args,_ in systemd.calls)


@pytest.mark.parametrize('active,sub,pid',[('active','running',99),('activating','auto-restart',0),('deactivating','stop',99),('inactive','dead',99)])
def test_active_or_transitional_legacy_blocks_without_stop_or_mutation(systemd,active,sub,pid):
    systemd.states[OLD]=('loaded',active,sub,pid,'enabled')
    before=(units.USER_UNIT_ROOT/OLD).read_bytes()
    result=units.migrate_ai_socket_relay(apply=True)
    assert result['status']=='blocked' and result['reason']=='legacy_not_stopped'
    assert (units.USER_UNIT_ROOT/OLD).read_bytes()==before
    assert not (units.USER_UNIT_ROOT/NEW).exists()
    assert all(args[2]=='show' for args,_ in systemd.calls)


def test_plan_and_success_are_idempotent_and_keep_new_pid(systemd):
    planned=units.migrate_ai_socket_relay()
    assert planned['status']=='planned'
    assert not (units.USER_UNIT_ROOT/NEW).exists()
    assert all(args[2]=='show' for args,_ in systemd.calls)
    result=units.migrate_ai_socket_relay(apply=True)
    assert result['status']=='completed'
    assert result['new']['pid']==42
    assert not (units.USER_UNIT_ROOT/OLD).exists()
    assert (units.USER_UNIT_ROOT/NEW).exists()
    again=units.migrate_ai_socket_relay(apply=True)
    assert again['status']=='completed' and again['new']['pid']==42
    assert all(args[2] not in {'stop','restart','kill'} for args,_ in systemd.calls)


def test_failed_start_preserves_old_definition_and_retry_finishes(systemd):
    systemd.fail_start=True
    with pytest.raises(subprocess.CalledProcessError):
        units.migrate_ai_socket_relay(apply=True)
    assert (units.USER_UNIT_ROOT/OLD).exists()
    assert (units.USER_UNIT_ROOT/NEW).exists()
    assert systemd.states[OLD][4]=='disabled'
    systemd.fail_start=False
    assert units.migrate_ai_socket_relay(apply=True)['status']=='completed'


def test_legacy_dropins_and_foreign_fragments_require_owner_review(systemd):
    systemd.dropins='/tmp/legacy-overrides.conf'
    assert units.migrate_ai_socket_relay(apply=True)['reason']=='legacy_overrides'
    systemd.dropins=''
    systemd.fragment='/etc/systemd/user/'+OLD
    assert units.migrate_ai_socket_relay(apply=True)['reason']=='foreign_fragment'
    assert all(args[2]=='show' for args,_ in systemd.calls)


def test_failed_observation_never_assumes_stopped(systemd):
    systemd.invalid_show=True
    with pytest.raises(RuntimeError,match='state'):
        units.migrate_ai_socket_relay(apply=True)
    assert all(args[2]=='show' for args,_ in systemd.calls)
    assert (units.USER_UNIT_ROOT/OLD).exists()


def test_external_reactivation_blocks_cleanup_after_new_start(systemd):
    systemd.reactivate=True
    result=units.migrate_ai_socket_relay(apply=True)
    assert result['status']=='blocked' and result['reason']=='legacy_not_stopped'
    assert (units.USER_UNIT_ROOT/OLD).exists()
    assert all(args[2] not in {'stop','restart','kill'} for args,_ in systemd.calls)


def test_unlink_legacy_symlink_does_not_delete_its_source(systemd,deployment):
    target=units.USER_UNIT_ROOT/OLD
    target.unlink()
    source=deployment/'legacy-source.service'
    source.write_text('keep audit source')
    target.symlink_to(source)
    assert units.migrate_ai_socket_relay(apply=True)['status']=='completed'
    assert source.read_text()=='keep audit source'
    assert not target.is_symlink()


def test_effective_legacy_enablement_blocks_definition_cleanup(systemd,monkeypatch):
    def remain_enabled(*args,**kwargs):
        result=systemd(*args,**kwargs)
        if args[2]=='disable':
            systemd.states[OLD]=('loaded','inactive','dead',0,'enabled')
        return result
    monkeypatch.setattr(units,'_run',remain_enabled)
    result=units.migrate_ai_socket_relay(apply=True)
    assert result['reason']=='legacy_still_enabled'
    assert (units.USER_UNIT_ROOT/OLD).exists()


def test_successful_start_command_without_running_process_keeps_old_file(systemd,monkeypatch):
    def still_inactive(*args,**kwargs):
        result=systemd(*args,**kwargs)
        if args[2]=='enable':
            systemd.states[NEW]=('loaded','inactive','dead',0,'enabled')
        return result
    monkeypatch.setattr(units,'_run',still_inactive)
    with pytest.raises(RuntimeError,match='running'):
        units.migrate_ai_socket_relay(apply=True)
    assert (units.USER_UNIT_ROOT/OLD).exists()


@pytest.mark.parametrize('flag,status',[(None,'planned'),('--prepare','prepared'),('--apply','completed')])
def test_control_entrypoint_uses_explicit_actions_and_json_receipt(systemd,monkeypatch,capsys,flag,status):
    monkeypatch.setattr(sys,'argv',['portal_service_units.py','relay-migration']+([flag] if flag else []))
    assert units.main()==0
    assert json.loads(capsys.readouterr().out)['status']==status


def test_control_entrypoint_reports_active_legacy_without_stopping(systemd,monkeypatch,capsys):
    systemd.states[OLD]=('loaded','active','running',99,'enabled')
    monkeypatch.setattr(sys,'argv',['portal_service_units.py','relay-migration','--apply'])
    assert units.main()==1
    result=json.loads(capsys.readouterr().out)
    assert result['reason']=='legacy_not_stopped'
    assert all(args[2]=='show' for args,_ in systemd.calls)


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
