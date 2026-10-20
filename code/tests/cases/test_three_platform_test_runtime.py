"""Isolated pytest transport and persistent browser ownership contracts.

Uses disposable native children and a fake Docker daemon/Playwright server;
never starts a browser, Portal request, TAU Campaign or database fixture.
Run via unittest. Windows contracts are simulated, not native certification.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from management_common import process_runtime
from management_portal.test_framework import local_runs as local, tau_browser_owner as owner, runner_adapters
from management_portal.workbench_projections import application_build as build

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'sha256:' + 'a' * 64


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('deadline waiting for disposable test resource')


class ContainerBoundary(unittest.TestCase):
    def test_snapshot_whitelist_and_private_configuration(self):
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'repo'; root.mkdir()
                artifacts = Path(tmp) / 'artifacts'; artifacts.mkdir()
                for name in ('tracked.py', 'new_test.py', 'untracked.secret', '.env.private', 'secret.key'):
                    (root / name).write_text('test fixture')
                (artifacts / 'pytest-args.txt').write_text('new_test.py::test_behavior\n')
                command = [sys.executable, '-m', 'pytest', '@' + str(artifacts / 'pytest-args.txt')]
                listing = subprocess.CompletedProcess([], 0, b'tracked.py\0.env.private\0secret.key\0')
                settings = {'docker_executable': '/native/docker', 'docker_endpoint': 'unix:///local', 'container_image': IMAGE}
                with patch.object(build, '_container_settings', return_value=settings), \
                     patch.object(process_runtime, 'run_process', return_value=listing), \
                     patch.object(local, 'sys', SimpleNamespace(**{**vars(sys), 'platform': platform})), patch.object(shutil, 'which', return_value='/native/git'), \
                     patch.dict(os.environ, {'SECRET_TOKEN': 'not passed'}):
                    driver = local.ContainerPytest(root, artifacts, command, [])
                try:
                    copied = sorted(p.name for p in Path(driver.plan['source']).iterdir())
                    self.assertEqual(copied, ['new_test.py', 'tracked.py'])
                    self.assertEqual(driver.plan['argv'], ['python3', '-m', 'pytest', '@/artifacts/pytest-args.txt'])
                    self.assertNotIn('SECRET_TOKEN', driver.environment)
                    self.assertNotEqual(driver.environment['HOME'], str(Path.home()))
                    self.assertNotIn('DOCKER_HOST', driver.environment)
                finally:
                    driver.close(None)
                self.assertFalse(driver.directory.exists())

    def worker(self, failure='', image=None, exitcode=0):
        calls = []
        stop_reader = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / 'plan'; work.mkdir(); artifacts = root / 'artifacts'; artifacts.mkdir()
            plan = {'name': 'portal-pytest-own', 'prefix': ['docker'], 'environment': {}, 'image': IMAGE,
                    'source': str(root/'source'), 'artifacts': str(artifacts), 'argv': ['python3', '-m', 'pytest'], 'user': '1000'}
            plan_file = work / 'plan.json'; plan_file.write_text(json.dumps(plan))
            def control(plan, args, **kwargs):
                calls.append(args)
                if args[:2] == ['image', 'inspect']:
                    if failure == 'image':
                        return subprocess.CompletedProcess(args, 1, b'', b'not loaded')
                    data = image or {'Id': IMAGE, 'Os': 'linux', 'Config': {'Volumes': None}}
                elif args[0] == 'create':
                    if failure == 'create':
                        raise subprocess.TimeoutExpired(args, 30)
                    return subprocess.CompletedProcess(args, 0, b'owned\n', b'')
                elif args[:2] == ['container', 'ls']:
                    return subprocess.CompletedProcess(args, 0, b'' if failure == 'create' else b'owned\n', b'')
                elif args[0] == 'inspect' and '.Config.Labels' in args[2]:
                    data = {'portal.test.owner': 'foreign' if failure == 'foreign' else plan['name'], 'image.label': 'keep'}
                elif args[0] == 'inspect':
                    data = {'Running': False, 'ExitCode': exitcode, 'Error': ''}
                elif args[0] == 'rm':
                    return subprocess.CompletedProcess(args, 1 if failure == 'remove' else 0, b'', b'')
                else:
                    raise AssertionError(args)
                return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b'')
            child = Mock(); child.poll.return_value = 0
            try:
                with patch.object(local, '_docker_control', side_effect=control), \
                     patch.object(local, '_watch_container_parent', side_effect=lambda stopped: stop_reader.wait(30)), \
                     patch.object(process_runtime, 'spawn_process', return_value=child) as spawn, \
                     patch.object(process_runtime, 'terminate_process_tree'):
                    try:
                        result = local._container_worker(plan_file)
                    except Exception as error:
                        result = error
                receipt = json.loads((artifacts/'container-cleanup.json').read_text())
                return calls, result, receipt, spawn.call_args
            finally:
                stop_reader.set()

    def test_fixed_image_network_credentials_mounts_and_actual_exit(self):
        calls, result, receipt, spawn = self.worker(exitcode=7)
        self.assertEqual(result, 7)
        self.assertTrue(receipt['complete'])
        create = next(a for a in calls if a[0] == 'create')
        for flag in ('--pull=never', '--network=none', '--read-only', '--cap-drop=ALL', '--init', '--no-healthcheck', '--security-opt=no-new-privileges'):
            self.assertIn(flag, create)
        self.assertEqual(create.count('--mount'), 2)
        self.assertIn('-i', create)
        self.assertIn('PORTAL_TEST_RESULTS_FILE=/artifacts/results.jsonl', create)
        self.assertEqual(spawn.args[0], ['docker', 'start', '--attach', 'portal-pytest-own'])
        self.assertFalse(any('pull' == part for args in calls for part in args))

    def test_fail_closed_missing_image_bad_digest_volumes_and_unknown_create(self):
        for failure, image in [('image', None), ('', {'Id': IMAGE, 'Os': 'windows'}),
                               ('', {'Id': IMAGE, 'Os': 'linux', 'Config': {'Volumes': {'/secret': {}}}}),
                               ('', {'Id': 'sha256:'+'b'*64, 'Os': 'linux'}), ('create', None)]:
            with self.subTest(failure=failure, image=image):
                calls, result, receipt, spawn = self.worker(failure, image)
                self.assertIsInstance(result, Exception)
                self.assertIsNone(spawn)
                if failure == 'create':
                    self.assertFalse(receipt['complete'])
                    self.assertTrue(receipt['uncertain_create'])
                else:
                    self.assertFalse(any(a[0] == 'create' for a in calls))

    def test_foreign_container_or_failed_removal_cannot_claim_cleanup(self):
        for failure in ('foreign', 'remove'):
            calls, result, receipt, _ = self.worker(failure)
            self.assertIsInstance(result, RuntimeError)
            self.assertFalse(receipt['complete'])
            if failure == 'foreign':
                self.assertFalse(any(a[0] == 'rm' for a in calls))


FAKE_DOCKER = '''#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
args=sys.argv[1:]
while args and args[0] in ('--host','--config'): args=args[2:]
state=Path(__file__).with_name('fake-container.json')
image='sha256:'+'a'*64
if args[:2]==['image','inspect']: print(json.dumps({'Id':image,'Os':'linux','Config':{'Volumes':None}}))
elif args[0]=='create':
 name=args[args.index('--name')+1];state.write_text(json.dumps({'portal.test.owner':name}));print('owned')
elif args[:2]==['container','ls']: print('owned' if state.exists() else '')
elif args[0]=='inspect': print(state.read_text() if 'Labels' in args[2] else json.dumps({'Running':False,'ExitCode':0,'Error':''}))
elif args[0]=='start':
 print('fixture running',flush=True)
 while state.exists(): time.sleep(.05)
elif args[0]=='rm': state.unlink(missing_ok=True)
else: raise SystemExit(9)
'''


@unittest.skipUnless(sys.platform == 'linux', 'native disposable POSIX process evidence')
class GuardianLifecycle(unittest.TestCase):
    def test_completed_guardian_exits_with_lifetime_pipe_still_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / 'plan'; work.mkdir()
            artifacts = root / 'artifacts'; artifacts.mkdir()
            docker = root / 'docker.py'
            docker.write_text(FAKE_DOCKER.replace('while state.exists(): time.sleep(.05)', 'pass'))
            plan = {'name': 'portal-pytest-completed', 'prefix': [sys.executable, str(docker)],
                    'environment': dict(os.environ), 'image': IMAGE, 'source': str(root/'source'),
                    'artifacts': str(artifacts), 'argv': ['python3', '-m', 'pytest'], 'user': '0'}
            plan_file = work / 'plan.json'; plan_file.write_text(json.dumps(plan))
            process = process_runtime.spawn_process(
                [sys.executable, '-m', local.__name__, '--container-worker', str(plan_file)],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                # communicate() would close stdin and hide the shutdown deadlock.
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertTrue(json.loads((artifacts/'container-cleanup.json').read_text())['complete'])
                self.assertFalse((root/'fake-container.json').exists())
            finally:
                process.stdin.close()
                process_runtime.terminate_process_tree(process, force=True)
                process.stderr.close()

    def test_pipe_eof_cancel_and_abrupt_executor_exit_reap_daemon_resource(self):
        for abrupt in (False, True):
            with self.subTest(abrupt=abrupt), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); work=root/'plan'; work.mkdir(); artifacts=root/'artifacts'; artifacts.mkdir()
                docker=root/'docker.py'; docker.write_text(FAKE_DOCKER)
                plan={'name':'portal-pytest-test', 'prefix':[sys.executable,str(docker)], 'environment':dict(os.environ),
                      'image':IMAGE,'source':str(root/'source'),'artifacts':str(artifacts),'argv':['python3','-m','pytest'],'user':'0'}
                plan_file=work/'plan.json';plan_file.write_text(json.dumps(plan))
                worker=[sys.executable,'-m',local.__name__,'--container-worker',str(plan_file)]
                if abrupt:
                    script='from management_common import process_runtime as p; import subprocess,time; child=p.spawn_process('+repr(worker)+',stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,kill_on_owner_exit=False); print(child.pid,flush=True); time.sleep(60)'
                    process=process_runtime.spawn_process([sys.executable,'-c',script],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    guardian=int(process.stdout.readline())
                else:
                    process=process_runtime.spawn_process(worker,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                    guardian=process.pid
                try:
                    until(lambda: (root/'fake-container.json').exists())
                    if abrupt: process_runtime.terminate_process_tree(process,force=True)
                    else: process.stdin.close()
                    until(lambda: (artifacts/'container-cleanup.json').exists())
                    self.assertTrue(json.loads((artifacts/'container-cleanup.json').read_text())['complete'])
                    self.assertFalse((root/'fake-container.json').exists())
                    until(lambda: not process_runtime.process_alive(guardian))
                    self.assertFalse(work.exists())
                finally:
                    process_runtime.terminate_process_tree(process,force=True)
                    if process.stdin and not process.stdin.closed: process.stdin.close()
                    if process.stdout: process.stdout.close()
                    if process.stderr: process.stderr.close()


class BrowserOwnership(unittest.TestCase):
    def lease(self, directory):
        token='a'*32
        value={'token':token,'tree_id':owner._tree_id(directory,token),'evidence_dir':str(directory),
               'state':'owned','host':{'pid':777,'created_at':1}}
        (directory/owner.LEASE).write_text(json.dumps(value))
        return value

    def test_windows_cleanup_uses_named_job_never_reused_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);lease=self.lease(directory)
            with patch.object(sys,'platform','win32'), patch.object(process_runtime,'terminate_process_tree') as stop, \
                 patch.object(process_runtime,'process_tree_alive',return_value=False):
                owner.stop_tree(directory,lease['token'])
            stop.assert_called_once_with(None,timeout=3,tree_id=lease['tree_id'])

    def test_reused_posix_pid_and_foreign_receipt_are_not_signalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);lease=self.lease(directory)
            with patch.object(owner,'_same',return_value=False), patch.object(sys,'platform','darwin'), \
                 patch.object(process_runtime,'process_tree_alive',return_value=True), \
                 patch.object(process_runtime,'terminate_process_tree') as stop:
                with self.assertRaisesRegex(RuntimeError,'identity changed'): owner.stop_tree(directory,lease['token'])
                lease['evidence_dir']='/foreign';(directory/owner.LEASE).write_text(json.dumps(lease))
                with self.assertRaisesRegex(RuntimeError,'ownership mismatch'): owner.stop_tree(directory,lease['token'])
                stop.assert_not_called()


PLAYWRIGHT_STUB = r'''
const Module=require('module'), cp=require('child_process'), EventEmitter=require('events');
const original=Module._load;
Module._load=function(name,...args){
 if(String(name).endsWith('/playwright')) return {chromium:{
  connect: async endpoint=>({endpoint}),
  launchServer:async()=>{
   if(process.env.TEMP !== process.env.TMPDIR || process.env.TMP !== process.env.TMPDIR
       || require('os').tmpdir() !== process.env.TMPDIR) throw new Error('browser temporary directory escapes owned profile');
   if(process.env.FIXTURE_LAUNCH_FAIL) throw new Error('fixture launch failure');
   const child=cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
   require('fs').writeFileSync(process.env.FIXTURE_CHILD,String(child.pid));
   const server=new EventEmitter(); server.wsEndpoint=()=> 'ws://fixture';
   server.close=async()=>{ if(process.env.FIXTURE_HANG_CLOSE) return new Promise(()=>{}); child.kill(); };
   return server;
  }
 }};
 return original.call(this,name,...args);
};
'''


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('node'), 'native Node host with fake Playwright')
class BrowserLifecycle(unittest.TestCase):
    def scenario(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evidence=root/'case';evidence.mkdir()
            stub=root/'playwright-stub.cjs';stub.write_text(PLAYWRIGHT_STUB)
            env={'NODE_OPTIONS':'--require='+str(stub),'FIXTURE_CHILD':str(root/'child.pid')}
            if mode=='failure':env['FIXTURE_LAUNCH_FAIL']='1'
            if mode=='owner_exit':env['FIXTURE_HANG_CLOSE']='1'
            with patch.dict(os.environ,env):
                lease=owner.start_browser(evidence,shutil.which('node'),'1')
            try:
                if mode=='failure':
                    until(lambda: owner.read_lease(evidence)['state']=='closed')
                    self.assertFalse((root/'child.pid').exists())
                    self.assertEqual(json.loads((evidence/'tau-browser-host.json').read_text())['state'],'failed')
                    return
                child=int(until(lambda: (root/'child.pid').read_text() if (root/'child.pid').exists() else ''))
                host=until(lambda: json.loads((evidence/'tau-browser-host.json').read_text()) if (evidence/'tau-browser-host.json').exists() else None)
                self.assertEqual(host['state'],'running')
                self.assertTrue(Path(host['profile_root']).is_dir())
                self.assertTrue(process_runtime.process_alive(child))
                self.assertTrue(process_runtime.process_alive(lease['host']['pid']))
                second=owner.start_browser(evidence,shutil.which('node'),'1')
                self.assertEqual(lease['token'],second['token']);self.assertTrue(second['reused'])
                if mode=='owner_exit':
                    process_runtime.terminate_process_tree(lease['owner']['pid'],force=True)
                    until(lambda:not process_runtime.process_alive(child))
                    owner.stop_browser(evidence)
                else:
                    owner.cleanup_browsers(evidence)
                self.assertFalse(process_runtime.process_alive(child))
                self.assertFalse(process_runtime.process_alive(lease['host']['pid']))
                self.assertEqual(owner.read_lease(evidence)['state'],'closed')
                self.assertFalse(Path(host['profile_root']).exists())
                print('EVIDENCE '+json.dumps({'scenario':'browser_'+mode,'host_pid':lease['host']['pid'],
                      'descendant_pid':child,'host_alive':False,'descendant_alive':False,'lease_state':'closed'}))
            finally:
                try: owner.stop_browser(evidence)
                except Exception: pass

    def test_normal_close_and_reuse(self): self.scenario('normal')
    def test_launch_failure_closes_owner(self): self.scenario('failure')
    def test_owner_abrupt_exit_cleans_host_and_descendant(self): self.scenario('owner_exit')


@unittest.skipUnless(sys.platform == 'linux', 'native fake-daemon transport')
class ContainerRunTests(unittest.TestCase):
    def test_group_cancel_timeout_and_callback_failure_cleanup(self):
        from types import SimpleNamespace
        for mode in ('cancelled', 'timeout', 'consume', 'output'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); artifacts=root/'run';artifacts.mkdir(); repo=root/'repo';repo.mkdir()
                docker=root/'docker';docker.write_text(FAKE_DOCKER.replace('#!/usr/bin/env python3','#!'+sys.executable));docker.chmod(0o700)
                (repo/'test_example.py').write_text('def test_example(): pass\n')
                group=local.PytestGroup({'group_id':'pytest-test','lane':'isolated'},[],root=ROOT,state_roots=[])
                group.finish=Mock()
                def command(_, directory):
                    (directory/'pytest-args.txt').write_text('test_example.py::test_example\n')
                    return [sys.executable,'-m','pytest','@'+str(directory/'pytest-args.txt')],group.environment({})
                group.command=command
                if mode=='consume':group._consume=Mock(side_effect=ValueError('consume failure'))
                def output(_):
                    if mode=='output':raise ValueError('output failure')
                ctx=SimpleNamespace(run_dir=artifacts,patch=lambda _:None,output=output,
                                    stop_reason=lambda: mode if mode in ('cancelled','timeout') else '')
                settings={'docker_executable':str(docker),'docker_endpoint':'unix:///fixture','container_image':IMAGE}
                original=local.ContainerPytest
                def make(root, directory, argv, protected):
                    with patch.object(process_runtime,'run_process',return_value=subprocess.CompletedProcess([],0,b'test_example.py\0')):
                        return original(repo,directory,argv,protected)
                with patch.object(build,'_container_settings',return_value=settings),patch.object(local,'ContainerPytest',side_effect=make), \
                     patch.object(local,'sys',SimpleNamespace(**{**vars(sys),'platform':'darwin'})),patch.object(local,'POLL_SECONDS',.02):
                    if mode in ('consume','output'):
                        with self.assertRaisesRegex(ValueError,mode+' failure'):group._run_process([],ctx,1)
                    else:
                        group._run_process([],ctx,1)
                        self.assertEqual(group.finish.call_args.args[-1],mode)
                receipt=json.loads((artifacts/'pytest-test'/'container-cleanup.json').read_text())
                self.assertTrue(receipt['complete'])
                self.assertFalse((root/'fake-container.json').exists())
                print('EVIDENCE '+json.dumps({'scenario':'group_'+mode,'complete':receipt['complete'],'daemon_resource_present':False}))

    def test_darwin_launch_failure_removes_private_snapshot(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=local.PytestGroup({'group_id':'x','lane':'isolated'},[],root=root,state_roots=[])
            driver=Mock(command=['missing'],environment={},plan={'name':'fixture'})
            ctx=SimpleNamespace(run_dir=root,patch=lambda _:None)
            with patch.object(sys,'platform','darwin'),patch.object(local,'ContainerPytest',return_value=driver), \
                 patch.object(process_runtime,'spawn_process',side_effect=OSError('launch denied')):
                with self.assertRaisesRegex(OSError,'launch denied'):group._run_process([],ctx,1)
            driver.close.assert_called_once_with(None)


@unittest.skipUnless(sys.platform=='linux' and shutil.which('node'),'native Node bootstrap with fake Playwright')
class BrowserBootstrap(unittest.TestCase):
    def test_js_launch_reconnect_and_stop_use_the_persistent_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evidence=root/'case';evidence.mkdir()
            stub=root/'stub.cjs';stub.write_text(PLAYWRIGHT_STUB)
            script=root/'bootstrap.cjs'
            script.write_text('''const fs=require('fs'),vm=require('vm'),{createRequire}=require('module');
const source=process.argv[2], evidence=process.argv[3], python=process.argv[4];
const context={require:createRequire(source),module:{exports:{}},__dirname:require('path').dirname(source),process,setTimeout,clearTimeout,console};
vm.runInNewContext(fs.readFileSync(source,'utf8').split('function writeReceipt')[0]+'\\nmodule.exports={hostedBrowser,stopHost};',context);
(async()=>{
 const api=context.module.exports,first=await api.hostedBrowser(evidence,true,python);
 try { const second=await api.hostedBrowser(evidence,true,python);
  if(first.reused || !second.reused || first.host.token!==second.host.token)throw new Error('lease reuse broken');
  console.log(JSON.stringify({first_reused:first.reused,second_reused:second.reused,tree_id:second.host.tree_id}));
 } finally { await api.stopHost(first.host); }
})().catch(error=>{console.error(error);process.exitCode=1;});''')
            env={**os.environ,'NODE_OPTIONS':'--require='+str(stub),'FIXTURE_CHILD':str(root/'child.pid')}
            result=process_runtime.run_process([shutil.which('node'),str(script),str(ROOT/'management_portal/test_framework/tau_browser.js'),str(evidence),sys.executable],
                                              env=env,capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual(owner.read_lease(evidence)['state'],'closed')
            print('EVIDENCE js_bootstrap '+result.stdout.decode().strip())


class BrowserOwnerFailures(unittest.TestCase):
    def test_startup_timeout_and_spawn_failure_reap_only_the_owned_tree(self):
        for failure in ('timeout','spawn'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                evidence=Path(tmp);token='b'*32
                lease={'token':token,'tree_id':owner._tree_id(evidence,token),'evidence_dir':str(evidence),'state':'starting'}
                (evidence/owner.LEASE).write_text(json.dumps(lease))
                child=Mock(pid=123);child.poll.return_value=None
                stdin=Mock();stdin.buffer.readline.return_value=b'start\n'
                with patch.object(sys,'stdin',stdin),patch.object(owner,'_identity',return_value={'pid':123,'created_at':1}), \
                     patch.object(owner.time,'monotonic',side_effect=[0,61]), \
                     patch.object(process_runtime,'install_shutdown_handlers'),patch.object(process_runtime,'restore_shutdown_handlers'), \
                     patch.object(process_runtime,'spawn_process',return_value=child,side_effect=OSError('denied') if failure=='spawn' else None) as spawn, \
                     patch.object(process_runtime,'terminate_process_tree') as stop:
                    with self.assertRaises((RuntimeError,OSError)):owner.own(evidence,token,'native-node','1')
                self.assertEqual(spawn.call_args.kwargs['tree_id'],lease['tree_id'])
                self.assertNotIn('kill_on_owner_exit',spawn.call_args.kwargs)  # runtime's owning default
                if failure=='timeout':stop.assert_called_once_with(child,timeout=3)
                else:stop.assert_not_called()
                self.assertEqual(owner.read_lease(evidence)['state'],'closed')

    def test_cleanup_timeout_is_not_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp);token='c'*32
            lease={'token':token,'tree_id':owner._tree_id(evidence,token),'evidence_dir':str(evidence),'state':'owned',
                   'owner':{'pid':123,'created_at':1}}
            (evidence/owner.LEASE).write_text(json.dumps(lease))
            with patch.object(owner,'_same',return_value=True):
                with self.assertRaisesRegex(RuntimeError,'did not confirm cleanup'):owner.stop_browser(evidence,timeout=.01)
            self.assertEqual(owner.read_lease(evidence)['state'],'owned')

    def test_crashed_launch_client_lock_does_not_create_a_second_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Path(tmp);token='d'*32
            lease={'token':token,'tree_id':owner._tree_id(evidence,token),'evidence_dir':str(evidence),'state':'owned',
                   'owner':{'pid':123,'created_at':1}}
            (evidence/owner.LEASE).write_text(json.dumps(lease));(evidence/'tau-browser-launch.lock').write_text('old interrupted client')
            with patch.object(owner,'_same',return_value=True),patch.object(process_runtime,'spawn_process') as spawn:
                self.assertTrue(owner.start_browser(evidence,'node','1')['reused'])
            spawn.assert_not_called()


class ArtifactBoundary(unittest.TestCase):
    def test_result_symlink_is_never_read_as_host_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group_dir=root/'group';group_dir.mkdir()
            secret=root/'host-secret';secret.write_text('sensitive host data')
            result=group_dir/'results.jsonl';result.symlink_to(secret)
            group=local.PytestGroup({'group_id':'case'},[],root=root)
            with self.assertRaisesRegex(RuntimeError,'结果路径'):group._consume(result,0,None,group_dir)
            self.assertEqual(secret.read_text(),'sensitive host data')

    def test_writable_artifact_cannot_forge_guardian_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private=root/'private';private.mkdir();artifacts=root/'artifacts';artifacts.mkdir()
            (artifacts/'container-cleanup.json').write_text(json.dumps({'complete':True}))
            driver=object.__new__(local.ContainerPytest);driver.directory=private;driver.closed=False
            driver.plan={'artifacts':str(artifacts),'name':'owned'}
            child=Mock();child.stdin=None;child.wait.return_value=1
            with patch.object(local,'_remove_container',side_effect=RuntimeError('unknown create')) as remove:
                with self.assertRaisesRegex(RuntimeError,'unknown create'):driver.close(child)
            remove.assert_called_once_with(driver.plan,uncertain_create=True)
            self.assertFalse(driver.closed)
            self.assertTrue(private.exists())
