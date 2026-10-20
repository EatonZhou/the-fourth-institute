"""Isolated cutovers; real service controls are never invoked by these tests."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from scripts import migrate_deployment_paths as cutover

REAL_PROCESS_GUARD = cutover.process_guard

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux offline cutover capability')


@pytest.fixture
def cached_unit_cutover(tmp_path,monkeypatch):
    roots=[tmp_path/name for name in ['config','source','provider']]
    targets=[tmp_path/('new-'+p.name) for p in roots]
    for p in roots:p.mkdir()
    env=roots[0]/'database.env';env.write_text('ROOT='+str(roots[2])+'\n')
    paths=roots[0]/'portal_paths.json';paths.write_text(json.dumps({'root':str(roots[1])}))
    provider=roots[2]/'provider.env';provider.write_text('CONFIG='+str(roots[0])+'\n')
    fragment=roots[1]/'worker.service';fragment.write_text('[Service]\nEnvironmentFile='+str(env)+'\n')
    drop=roots[1]/'override.conf';drop.write_text('[Service]\nWorkingDirectory='+str(roots[2])+'\n')
    link=roots[2]/'env-link';link.symlink_to(env)
    files=[env,paths,provider,fragment,drop]
    edits=[]
    for i,p in enumerate(files):
        c=tmp_path/f'candidate-{i}';text=p.read_text()
        for a,b in zip(roots,targets):text=text.replace(str(a),str(b))
        c.write_text(text)
        edits.append({'path':str(p),'candidate':str(c),'before_sha256':cutover.digest(p.read_bytes())})
    row=unit('worker.service',FragmentPath=str(fragment),DropInPaths=str(drop),
             EnvironmentFiles=' '.join(str(p)+' (ignore_errors=no)' for p in [env,paths,provider,link]))
    inventory={('user','worker.service'):row}
    monkeypatch.setattr(cutover,'service_inventory',lambda _:copy.deepcopy(inventory))
    monkeypatch.setattr(cutover,'mount_guard',lambda _:None)
    monkeypatch.setattr(cutover,'process_guard',lambda _:None)
    spec={'moves':[{'source':str(a),'target':str(b)} for a,b in zip(roots,targets)],'edits':edits,
          'links':[{'path':str(link),'target':str(targets[0]/env.name),'before_target':str(env)}],
          'remove_links':[],'references':[],'services':[{'scope':'user','unit':'worker.service'}]}
    return spec,inventory,roots,targets,tmp_path/'backup'


def test_cached_service_inputs_survive_real_move_edit_link_roundtrip(cached_unit_cutover):
    spec,inventory,roots,targets,backup=cached_unit_cutover
    plan=cutover.seal(spec)
    journal=cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    assert journal['status']=='applied'
    assert [s['kind'] for s in journal['steps']]==['move']*3+['edit']*5+['link']
    assert all(not p.exists() for p in roots)
    assert (targets[2]/'env-link').read_bytes()==Path(spec['edits'][0]['candidate']).read_bytes()
    for e in spec['edits']:assert cutover.mapped(Path(e['path']),spec['moves']).read_bytes()==Path(e['candidate']).read_bytes()
    assert inventory[('user','worker.service')]['FragmentPath'].startswith(str(roots[1]))
    assert cutover.rollback(backup)['status']=='rolled_back'
    assert all(p.exists() for p in roots)


@pytest.mark.parametrize('stage',['move','edit','link'])
@pytest.mark.parametrize('fault',['new_consumer','active_service','input_bytes','candidate_bytes','mount','live_process'])
def test_transition_keeps_guards_live_between_each_stage(cached_unit_cutover,monkeypatch,stage,fault):
    spec,inventory,roots,targets,backup=cached_unit_cutover
    plan=cutover.seal(spec);persist=cutover.persist;fired=False;processes=[]
    def interrupt(path,data):
        nonlocal fired
        persist(path,data)
        if path.name!='journal.json' or fired or not data['steps']:return
        step=data['steps'][-1]
        if step['kind']!=stage or not step.get('done'):return
        fired=True
        if fault=='new_consumer':inventory[('system','new.service')]=unit('new.service',ExecStart=str(targets[2]))
        elif fault=='active_service':inventory[('user','worker.service')]['MainPID']='123'
        elif fault=='input_bytes':(targets[0]/'database.env').write_text('changed while stopped')
        elif fault=='candidate_bytes':Path(spec['edits'][0]['candidate']).write_text('tampered candidate')
        elif fault=='mount':
            monkeypatch.setattr(cutover,'mount_guard',lambda _: (_ for _ in ()).throw(RuntimeError('new mount refused')))
        else:
            child=subprocess.Popen(['/usr/bin/sleep','20'],cwd=targets[0]);processes.append(child)
            monkeypatch.setattr(cutover,'process_guard',REAL_PROCESS_GUARD)
    monkeypatch.setattr(cutover,'persist',interrupt)
    try:
        with pytest.raises(RuntimeError) as error:
            cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
        assert fired
        expected={'new_consumer':'new.service','active_service':'Service guard refused','input_bytes':'input content',
                  'candidate_bytes':'Candidate changed','mount':'new mount refused','live_process':'Process guard refused'}
        assert expected[fault] in str(error.value)
        if processes:assert str(processes[0].pid) in str(error.value)
        journal=json.loads((backup/'journal.json').read_text())
        assert journal['status']=='interrupted'
        with pytest.raises(RuntimeError,match='inspection'):cutover.rollback(backup)
    finally:
        for child in processes:child.terminate();child.wait(timeout=5)


def test_retired_cached_env_alias_uses_only_sealed_backing_file(cached_unit_cutover):
    spec,inventory,roots,targets,backup=cached_unit_cutover
    alias=backup.parent/'retired.env';alias.symlink_to(roots[0]/'database.env')
    spec['remove_links']=[{'path':str(alias),'before_target':os.readlink(alias)}]
    inventory[('user','worker.service')]['EnvironmentFiles']+=' '+str(alias)+' (ignore_errors=no)'
    plan=cutover.seal(spec)
    assert cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)['status']=='applied'
    assert not alias.is_symlink() and not alias.exists()
    # This is not a global old-path fallback for unrelated callers.
    with pytest.raises(RuntimeError):cutover.service_guard(spec['services'],cutover.roots_of(spec))
    assert cutover.rollback(backup)['status']=='rolled_back'
    assert alias.read_bytes()==(roots[0]/'database.env').read_bytes()


@pytest.mark.parametrize('wildcard',[False,True])
def test_individual_env_file_moves_keep_cached_unit_inputs(cached_unit_cutover,wildcard):
    spec,inventory,roots,targets,backup=cached_unit_cutover
    targets[0].mkdir()
    spec['moves']=[{'source':str(roots[0]/name),'target':str(targets[0]/name)}
                   for name in ['database.env','portal_paths.json']]+spec['moves'][1:]
    if wildcard:
        row=inventory[('user','worker.service')]
        row['EnvironmentFiles']=row['EnvironmentFiles'].replace(str(roots[0]/'database.env'),str(roots[0]/'*.env'))
    plan=cutover.seal(spec)
    assert cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)['status']=='applied'
    assert not (roots[0]/'database.env').exists()
    assert (targets[0]/'database.env').read_bytes()==Path(spec['edits'][0]['candidate']).read_bytes()
    assert cutover.rollback(backup)['status']=='rolled_back'
    assert (roots[0]/'database.env').is_file()


@pytest.mark.parametrize('fault',['expanded_env','changed_declaration','new_old_path_consumer','recreated_source'])
def test_transition_rejects_unsealed_references(cached_unit_cutover,monkeypatch,fault):
    spec,inventory,roots,targets,backup=cached_unit_cutover
    row=inventory[('user','worker.service')]
    row['EnvironmentFiles']=str(roots[0]/'*.env')+' (ignore_errors=no)'
    plan=cutover.seal(spec);persist=cutover.persist;fired=False
    def interrupt(path,data):
        nonlocal fired
        persist(path,data)
        if path.name!='journal.json' or fired or not data['steps'] or not data['steps'][0].get('done'):return
        fired=True
        if fault=='expanded_env':(targets[0]/'new.env').write_text('new input')
        elif fault=='changed_declaration':row['EnvironmentFiles']=str(targets[0]/'database.env')+' (ignore_errors=no)'
        elif fault=='new_old_path_consumer':inventory[('user','unsealed.service')]=unit('unsealed.service',EnvironmentFiles=str(roots[0]/'database.env')+' (ignore_errors=no)')
        else:roots[0].mkdir()
    monkeypatch.setattr(cutover,'persist',interrupt)
    with pytest.raises(RuntimeError) as error:cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    expected={'expanded_env':'expansion changed','changed_declaration':'declarations changed',
              'new_old_path_consumer':'directory missing','recreated_source':'source was recreated'}
    assert expected[fault] in str(error.value)


@pytest.fixture
def case(tmp_path, monkeypatch):
    source = tmp_path/'archive-source'; source.mkdir()
    secret = source/'auth.key'; secret.write_bytes(b'\x00credential\xffbytes'); secret.chmod(0o600)
    config = source/'site.json'; config.write_text('{"root":"archive"}')
    candidate = tmp_path/'candidate.json'; candidate.write_text('{"root":"tfi"}')
    (source/'current').symlink_to(source/'auth.key')
    target = tmp_path/'tfi-source'
    spec = {'moves':[{'source':str(source),'target':str(target)}],
            'edits':[{'path':str(config),'candidate':str(candidate),'before_sha256':cutover.digest(config.read_bytes())}],
            'links':[{'path':str(source/'current'),'target':str(target/'auth.key'),'before_target':str(source/'auth.key')}],
            'remove_links': [], 'references': [],
            'services':[{'scope': 'user', 'unit': 'portal-fixture.service'}]}
    monkeypatch.setattr(cutover, 'quiescent', lambda services, roots, inputs=None: None)
    plan = cutover.seal(spec)
    return source, target, candidate, plan, tmp_path/'backup'


def apply(case):
    return cutover.apply(case[3], cutover.digest(cutover.canonical(case[3])), case[4])


def test_roundtrip_preserves_credentials_inode_mode_and_no_alias(case):
    source, target, candidate, plan, backup = case
    inode = (source/'auth.key').stat().st_ino
    result = apply(case)
    assert result['status'] == 'applied' and not source.exists() and not source.is_symlink()
    assert (target/'auth.key').stat().st_ino == inode
    assert (target/'auth.key').read_bytes() == b'\x00credential\xffbytes'
    assert (target/'auth.key').stat().st_mode & 0o777 == 0o600
    assert (target/'site.json').read_bytes() == candidate.read_bytes()
    assert os.readlink(target/'current') == str(target/'auth.key')
    assert cutover.rollback(backup)['status'] == 'rolled_back'
    assert cutover.rollback(backup)['status'] == 'rolled_back'
    assert not target.exists() and (source/'site.json').read_text() == '{"root":"archive"}'


@pytest.mark.parametrize('change', ['source', 'candidate', 'target', 'plan', 'inode'])
def test_stale_plan_refuses_before_move(case, change):
    source,target,candidate,plan,backup=case
    if change == 'source': (source/'auth.key').write_bytes(b'new login')
    elif change == 'candidate': candidate.write_text('other')
    elif change == 'target': target.mkdir()
    elif change == 'inode':
        raw=(source/'auth.key').read_bytes(); (source/'auth.key').unlink(); (source/'auth.key').write_bytes(raw)
        # The root itself also has a new identity.
        source.rename(source.with_name('retained')); source.mkdir()
    else: plan['schema']='archive.invalid'
    with pytest.raises((ValueError, RuntimeError)): apply(case)
    assert source.exists() and not backup.exists()


def test_no_nested_roots_or_symlink_ancestors(case):
    source,target,_,plan,_=case
    spec=copy.deepcopy(plan['spec']); spec['moves'][0]['target']=str(source/'nested')
    with pytest.raises(ValueError,match='overlapping'):cutover.validate(spec)
    alias=source.parent/'alias'; alias.symlink_to(source)
    with pytest.raises(ValueError,match='ancestor'):cutover.absolute(str(alias/'key'))


def test_special_file_is_explicit_precondition(case):
    source,_,_,plan,_=case
    os.mkfifo(source/'ipc')
    with pytest.raises(ValueError,match='special file'):cutover.seal(plan['spec'])


def test_atomic_rename_does_not_clobber_new_target(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    with pytest.raises(FileExistsError):cutover.rename_noreplace(a,b)
    assert a.is_dir() and b.is_dir()


def test_failure_journal_keeps_exact_backups_and_refuses_blind_replay(case,monkeypatch):
    source,target,_,_,backup=case
    monkeypatch.setattr(cutover,'replace_bytes',lambda *a: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):apply(case)
    assert target.is_dir() and not source.exists()
    journal=json.loads((backup/'journal.json').read_text())
    assert journal['status']=='interrupted' and journal['steps'][0]['done']
    assert (backup/'0.before').read_text()=='{"root":"archive"}'
    with pytest.raises(RuntimeError,match='inspection'):cutover.rollback(backup)


@pytest.mark.parametrize('change',['data','backup','old_root','stored_plan'])
def test_rollback_does_not_overwrite_later_changes(case,change):
    source,target,_,_,backup=case; apply(case)
    if change=='data':(target/'auth.key').write_bytes(b'new credential')
    elif change=='backup':(backup/'0.before').write_bytes(b'changed backup')
    elif change=='old_root':source.mkdir()
    else:(backup/'plan.json').write_text('{}')
    with pytest.raises(RuntimeError):cutover.rollback(backup)
    assert target.is_dir()


def test_unsafe_backup_rejected(case):
    source,_,_,plan,_=case
    with pytest.raises(ValueError,match='Backup'):cutover.apply(plan,cutover.digest(cutover.canonical(plan)),source/'backup')


def test_actual_process_reference_is_refused(tmp_path):
    root=tmp_path/'active';root.mkdir()
    process=subprocess.Popen(['/usr/bin/sleep','20'],cwd=root)
    try:
        with pytest.raises(RuntimeError,match='Process guard refused'):cutover.process_guard([root])
    finally:process.terminate();process.wait(timeout=5)


def test_active_service_rejected_without_stop(monkeypatch,tmp_path):
    row=unit('portal-fixture.service', ActiveState='active', MainPID='123')
    monkeypatch.setattr(cutover,'service_inventory',lambda _: {('user',row['Id']):row})
    with pytest.raises(RuntimeError,match='Service guard refused'):
        cutover.service_guard([{'scope':'user','unit':row['Id']}],[tmp_path])


def test_unknown_platform_refused(monkeypatch):
    monkeypatch.setattr(cutover.sys,'platform','darwin')
    with pytest.raises(RuntimeError,match='Linux'):cutover.quiescent([],[])


def test_candidate_cannot_move_with_root(case):
    source,_,_,plan,_=case
    spec=copy.deepcopy(plan['spec']);spec['edits'][0]['candidate']=str(source/'auth.key')
    with pytest.raises(ValueError,match='outside'):cutover.validate(spec)


def test_seal_refuses_parallel_configuration_edit(case):
    source,_,_,plan,_=case
    (source/'site.json').write_text('parallel owner change')
    with pytest.raises(RuntimeError,match='preimage'):cutover.seal(plan['spec'])


def test_maintenance_lock_serializes_and_preserves_inode(tmp_path):
    path=tmp_path/'cutover.lock'
    with cutover.operation_lock(path,[]):
        inode=path.stat().st_ino
        with pytest.raises(RuntimeError,match='holds the lock'):
            with cutover.operation_lock(path,[]):pass
    with cutover.operation_lock(path,[]):assert path.stat().st_ino==inode
    alias=tmp_path/'alias';alias.symlink_to(path)
    with pytest.raises(OSError):
        with cutover.operation_lock(alias,[]):pass
    with pytest.raises(ValueError,match='outside'):
        with cutover.operation_lock(path,[tmp_path]):pass


def test_git_worktree_pointers_preserve_head_and_dirty_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(cutover,'quiescent',lambda *args:None)
    def git(root,*argv):
        return subprocess.check_output(['git','-C',str(root),*argv],text=True,
                                       env=os.environ|{'GIT_OPTIONAL_LOCKS':'0'})
    source=tmp_path/'source';source.mkdir();linked=tmp_path/'linked'
    git(source,'init','-q');git(source,'config','user.name','Cutover Fixture')
    git(source,'config','user.email','fixture@example.invalid')
    (source/'tracked').write_bytes(b'initial\n');git(source,'add','tracked');git(source,'commit','-qm','fixture')
    git(source,'worktree','add','-q','--detach',str(linked))
    (source/'tracked').write_bytes(b'parallel dirty\n');(linked/'untracked').write_bytes(b'untracked\n')
    targets=[tmp_path/'canonical-source',tmp_path/'canonical-linked']
    moves=[{'source':str(a),'target':str(b)} for a,b in zip([source,linked],targets)]
    paths=[linked/'.git',next((source/'.git/worktrees').glob('*/gitdir'))]
    edits=[]
    for i,p in enumerate(paths):
        raw=p.read_text();changed=raw.replace(str(source),str(targets[0])).replace(str(linked),str(targets[1]))
        c=tmp_path/f'candidate-{i}';c.write_text(changed)
        edits.append({'path':str(p),'candidate':str(c),'before_sha256':cutover.digest(p.read_bytes())})
    before=[(git(r,'rev-parse','HEAD'),git(r,'status','--porcelain')) for r in [source,linked]]
    plan=cutover.seal({'moves':moves,'edits':edits,'links':[],'remove_links':[], 'references':[], 'services':[]})
    backup=tmp_path/'backup'
    cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    assert [(git(r,'rev-parse','HEAD'),git(r,'status','--porcelain')) for r in targets]==before
    assert (targets[0]/'tracked').read_bytes()==b'parallel dirty\n'
    assert (targets[1]/'untracked').read_bytes()==b'untracked\n'
    assert cutover.rollback(backup)['status']=='rolled_back'


def test_config_to_real_account_and_service_consumers(tmp_path,monkeypatch):
    from management_common import portal_paths
    from scripts import portal_instance_storage as storage
    from management_portal.execution import provider_updates
    monkeypatch.setattr(provider_updates, '_PROVIDER_DEPLOYMENT', {'codex_binary': '/stale-site/codex'})
    from management_portal.execution.account_hosting import account_home, codex_socket_path
    root=tmp_path/'tfi-installation';root.mkdir()
    codex = root / 'providers/codex/bin/codex'
    codex.parent.mkdir(parents=True)
    codex.write_text('#!/bin/sh\nexit 0\n')
    codex.chmod(0o700)
    paths={k:str(root/k) for k in portal_paths.KEYS}
    config=tmp_path/'paths.json';config.write_text(json.dumps(paths))
    site=tmp_path/'site.json'
    deployment={'source_root':str(root/'source'),'release_root':str(root/'release'),
                'postgres_env_file':str(root/'config/database.env'),
                'postgres':{'prefix':str(root/'postgresql-16')},
                'network_config':str(root/'config/network.env'),
                'auth':{'session_signing_key_path':str(root/'auth/session.key')},
                'providers':{'runtime_root':str(root/'providers'),
                             'codex_binary':str(root/'providers/codex/bin/codex')}}
    site.write_text(json.dumps({'deployment':deployment}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG',str(site))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG',str(config))
    monkeypatch.delenv('PORTAL_POSTGRES_ENV_FILE',raising=False)
    monkeypatch.delenv('PORTAL_NETWORK_CONFIG',raising=False)
    monkeypatch.delenv('PORTAL_PROVIDER_EGRESS_ENV_FILE',raising=False)
    monkeypatch.setattr(portal_paths,'CONFIG_PATH',config)
    assert account_home('codex','codex_two')==root/'accounts_root/codex/codex_two'
    assert codex_socket_path('codex_two')==root/'instances_root/sockets/codex-codex_two.sock'
    source=Path(cutover.__file__).resolve().parents[1]
    for name in ['config/systemd/portal-codex-app-server@.service.in',
                 'systemd/portal-dev-execution-service.service',
                 'systemd/portal-postgresql.service']:
        rendered=storage.render_unit(source/name)
        assert '@PORTAL_' not in rendered
        assert f'PORTAL_PATHS_CONFIG={config}' in rendered
        assert str(root) in rendered
    rendered=storage.render_unit(source/'config/systemd/portal-codex-app-server@.service.in')
    assert str(root/'providers/codex/bin/codex') in rendered
    assert str(root/'accounts_root/codex/%i') in rendered


def unit(name, **kwargs):
    return dict(Id=name,LoadState='loaded',ActiveState='inactive',MainPID='0',ControlPID='0',Job='0') | kwargs


def test_system_consumer_and_timer_cannot_be_omitted(monkeypatch,tmp_path):
    system=unit('archive-reader.service',ExecStart=str(tmp_path/'wrapper'),TriggeredBy='archive-reader.timer')
    timer=unit('archive-reader.timer')
    inv={('system',v['Id']):v for v in [system,timer]}
    monkeypatch.setattr(cutover,'service_inventory',lambda _: inv)
    with pytest.raises(RuntimeError,match='missing'):
        cutover.service_guard([], [tmp_path])
    with pytest.raises(RuntimeError,match='archive-reader.timer'):
        cutover.service_guard([{'scope':'system','unit':system['Id']}], [tmp_path])
    declared=[{'scope':s,'unit':u} for s,u in inv]
    assert len(cutover.service_guard(declared,[tmp_path])['required'])==2
    timer['Job']='42 /job/42'
    with pytest.raises(RuntimeError,match='active'):cutover.service_guard(declared,[tmp_path])


def test_unit_dropin_reference_and_visibility(monkeypatch,tmp_path):
    drop=tmp_path/'override.conf';drop.write_text('[Service]\nWorkingDirectory=/opt/archive-state\n')
    row=unit('consumer.service',DropInPaths=str(drop))
    monkeypatch.setattr(cutover,'service_inventory',lambda _: {('system',row['Id']):row})
    with pytest.raises(RuntimeError,match='consumer.service'):cutover.service_guard([], [Path('/opt/archive-state')])
    drop.unlink()
    with pytest.raises(RuntimeError,match='unit input file'):cutover.service_guard([], [Path('/opt/archive-state')])


def test_unit_environment_file_content_is_a_guarded_reference(monkeypatch,tmp_path):
    env=tmp_path/'private config.env';env.write_text('ROOT=/opt/archive-state\nTOKEN=fixture-secret\n')
    encoded=str(env).replace(' ',r'\x20')
    consumer=unit('consumer.service',EnvironmentFiles=encoded+' (ignore_errors=no)',TriggeredBy='consumer.timer')
    inv={('system','consumer.service'):consumer,('system','consumer.timer'):unit('consumer.timer')}
    monkeypatch.setattr(cutover,'service_inventory',lambda _:inv)
    with pytest.raises(RuntimeError,match='consumer.timer') as error:
        cutover.service_guard([], [Path('/opt/archive-state')])
    assert 'fixture-secret' not in str(error.value)
    declared=[{'scope':s,'unit':u} for s,u in inv]
    assert len(cutover.service_guard(declared,[Path('/opt/archive-state')])['required'])==2


@pytest.mark.parametrize('optional',['yes','no'])
def test_unit_environment_missing_and_unreadable_not_confused(monkeypatch,tmp_path,optional):
    env=tmp_path/'config.env'
    row=unit('consumer.service',EnvironmentFiles=str(env)+' (ignore_errors='+optional+')')
    if optional=='yes':assert cutover.unit_reference_bytes(row,{})
    else:
        with pytest.raises(RuntimeError,match='missing'):cutover.unit_reference_bytes(row,{})
    env.write_text('ROOT=/opt/archive-state')
    read=Path.read_bytes
    def guarded(path):
        if path==env:raise PermissionError('fixture denied')
        return read(path)
    monkeypatch.setattr(Path,'read_bytes',guarded)
    with pytest.raises(RuntimeError,match='unit input file'):cutover.unit_reference_bytes(row,{})


@pytest.mark.parametrize('value',['/etc/example.env (bad=no)','relative.env (ignore_errors=yes)','/etc/*/example.env (ignore_errors=yes)'])
def test_ambiguous_environment_file_declaration_refused(value):
    with pytest.raises(RuntimeError):cutover.unit_reference_bytes(unit('consumer.service',EnvironmentFiles=value),{})


def test_both_service_managers_and_inventory_failure(monkeypatch):
    calls=[]
    def run(scope,*args):
        calls.append((scope,args[0]))
        if args[0]=='list-units':return '[{"unit":"fixture.service"}]'
        if args[0]=='list-unit-files':return '[{"unit_file":"fixture.service"}]'
        return '\n'.join(k+'='+v for k,v in unit('fixture.service',Names='fixture.service').items())
    monkeypatch.setattr(cutover,'systemctl',run)
    assert set(cutover.service_inventory([]))=={('user','fixture.service'),('system','fixture.service')}
    assert {x[0] for x in calls}=={'user','system'}
    monkeypatch.setattr(cutover,'systemctl',lambda *args: (_ for _ in ()).throw(RuntimeError('bus denied')))
    with pytest.raises(RuntimeError,match='bus denied'):cutover.service_inventory([])


BASE_MOUNTS='1 0 8:1 / / rw - ext4 /dev/test rw\n'


@pytest.mark.parametrize('line',[
    '2 1 8:2 / /opt/archive-state/session rw - ext4 /dev/other rw',
    '2 1 8:1 /history /opt/archive-state/session rw - ext4 /dev/test rw',
    '2 1 8:1 /opt/archive-state /external/bind rw - ext4 /dev/test rw',
    '2 1 8:1 /opt /external/parent-bind rw - ext4 /dev/test rw',
    '2 1 8:1 /opt/archive-state/sub /external/child-bind rw - ext4 /dev/test rw',
])
def test_nested_mount_same_device_and_external_bind_refused(line):
    rows=cutover.mount_rows(BASE_MOUNTS+line+'\n')
    assert cutover.mount_conflicts(rows,[Path('/opt/archive-state')],rows)


def test_mountinfo_escape_and_unrelated_mount():
    rows=cutover.mount_rows(BASE_MOUNTS+'2 1 8:2 / /opt/archive\\040state rw - ext4 /dev/other rw\n')
    assert rows[1]['point']=='/opt/archive state'
    assert cutover.mount_conflicts(rows,[Path('/opt/archive state')],rows)
    assert not cutover.mount_conflicts(rows,[Path('/opt/unrelated')],rows)


def test_mount_namespace_permission_never_waived(monkeypatch):
    real_read=Path.read_text;real_iter=Path.iterdir
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**k: BASE_MOUNTS if str(p).endswith('mountinfo') else real_read(p,*a,**k))
    monkeypatch.setattr(Path,'iterdir',lambda p: iter([Path('/proc/123')]) if p==Path('/proc') else real_iter(p))
    def link(p):
        if str(p)=='/proc/self/ns/mnt':return 'mnt:[1]'
        raise PermissionError('guard')
    monkeypatch.setattr(cutover.os,'readlink',link)
    with pytest.raises(RuntimeError,match='visibility incomplete'):cutover.mount_guard([Path('/opt/state')])


def test_other_namespace_bind_refused(monkeypatch):
    monkeypatch.setattr(Path,'iterdir',lambda p: iter([Path('/proc/123')]))
    monkeypatch.setattr(cutover.os,'readlink',lambda p:'mnt:[1]' if str(p).startswith('/proc/self') else 'mnt:[2]')
    monkeypatch.setattr(Path,'read_text',lambda p: BASE_MOUNTS+('2 1 8:1 /opt/state /alias rw - ext4 /dev/test rw\n' if '123' in str(p) else ''))
    with pytest.raises(RuntimeError,match='bind alias') as error:cutover.mount_guard([Path('/opt/state')])
    assert 'mnt:[2]' in str(error.value) and '123' in str(error.value)


def test_reference_cas_and_retire_alias_roundtrip(case):
    source,target,candidate,plan,backup=case
    wrapper=source.parent/'wrapper';wrapper.write_text('exec canonical-program\n')
    alias=source.parent/'archive-alias';alias.symlink_to(source)
    spec=copy.deepcopy(plan['spec']);spec['references']=[{'path':str(wrapper),'before_sha256':cutover.digest(wrapper.read_bytes())}]
    spec['remove_links']=[{'path':str(alias),'before_target':str(source)}]
    plan=cutover.seal(spec)
    cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    assert not alias.exists() and not alias.is_symlink()
    cutover.rollback(backup)
    assert alias.is_symlink() and os.readlink(alias)==str(source)
    wrapper.write_text('changed\n')
    with pytest.raises(RuntimeError,match='Reference preimage'):cutover.seal(spec)


def test_retired_alias_recreation_refuses_rollback(case):
    source,target,candidate,plan,backup=case
    alias=source.parent/'archive-alias';alias.symlink_to(source)
    spec=copy.deepcopy(plan['spec']);spec['remove_links']=[{'path':str(alias),'before_target':str(source)}]
    plan=cutover.seal(spec);cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    alias.symlink_to('/new-owner')
    with pytest.raises(RuntimeError,match='alias was recreated'):cutover.rollback(backup)
    assert target.is_dir() and os.readlink(alias)=='/new-owner'


def test_guard_reruns_for_apply_and_rollback(case,monkeypatch):
    source,target,_,plan,backup=case
    def mounted(*args):raise RuntimeError('mounted after seal')
    monkeypatch.setattr(cutover,'quiescent',mounted)
    with pytest.raises(RuntimeError,match='mounted after seal'):apply(case)
    assert source.is_dir() and not backup.exists()
    monkeypatch.setattr(cutover,'quiescent',lambda *args:None);apply(case)
    monkeypatch.setattr(cutover,'quiescent',mounted)
    with pytest.raises(RuntimeError,match='mounted after seal'):cutover.rollback(backup)
    assert target.is_dir()


def test_old_plan_and_unscoped_services_are_not_accepted(case):
    plan=copy.deepcopy(case[3]);plan['schema']='tfi.deployment-cutover.v1'
    with pytest.raises(ValueError,match='Plan digest'):cutover.apply(plan,cutover.digest(cutover.canonical(plan)),case[4])
    plan['spec']['services']=['fixture.service']
    with pytest.raises(ValueError,match='explicit'):cutover.validate(plan['spec'])


def test_root_mount_name_and_template_inventory(monkeypatch):
    calls=[]
    def command(scope,*args):
        calls.append((scope,args))
        if args[0]=='list-units':return '[{"unit":"-.mount"}]'
        if args[0]=='list-unit-files':return '[{"unit_file":"fixture@.service"}]'
        if args[0]=='cat':return '[Service]\nExecStart=/opt/archive-state/bin/run %i\n'
        assert args.index('--')<args.index('-.mount')
        assert 'fixture@.service' not in args
        return '\n'.join(k+'='+v for k,v in unit('-.mount',Names='-.mount').items())
    monkeypatch.setattr(cutover,'systemctl',command)
    inv=cutover.service_inventory([])
    assert ('system','fixture@.service') in inv and ('user','-.mount') in inv
    monkeypatch.setattr(cutover,'service_inventory',lambda _:inv)
    with pytest.raises(RuntimeError,match='fixture@.service'):cutover.service_guard([], [Path('/opt/archive-state')])


def test_alias_remove_after_caller_edit_failure_retains_alias(case,monkeypatch):
    source,_,_,plan,backup=case
    alias=source.parent/'old-alias';alias.symlink_to(source)
    spec=copy.deepcopy(plan['spec']);spec['remove_links']=[{'path':str(alias),'before_target':str(source)}]
    plan=cutover.seal(spec)
    monkeypatch.setattr(cutover,'replace_bytes',lambda *args:(_ for _ in ()).throw(OSError('failed caller edit')))
    with pytest.raises(OSError):cutover.apply(plan,cutover.digest(cutover.canonical(plan)),backup)
    assert alias.is_symlink() and os.readlink(alias)==str(source)
    assert json.loads((backup/'journal.json').read_text())['status']=='interrupted'
