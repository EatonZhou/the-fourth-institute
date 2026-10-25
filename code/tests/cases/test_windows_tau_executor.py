"""Native Windows TAU infrastructure tests, not a formal TAU Campaign.

Run with unittest on Windows. Uses disposable RunRecords, actual Windows Jobs
and (when installed) the dedicated TAU Chromium; no account, key or model call.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from management_common import process_runtime
from management_portal.business_tasks import process_is_alive
from management_portal.test_framework import native_executor as native
from management_portal.test_framework.records import RunRecords

ROOT = Path(__file__).resolve().parents[2]


def until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("Timed out waiting for disposable native test resource")


# The fixture only replaces the workload after the real launcher/lock. The
# actual durable claim and cancellation record are exercised across processes.
WORKLOAD = '''
import argparse, os, time
from pathlib import Path
from management_portal.business_tasks import process_identity
from management_portal.test_framework.records import RunRecords
from management_portal.test_framework import executor, native_executor
def run(argv):
    root = Path(argv[1]); run_id = argv[2]
    records = RunRecords(root / 'runs.json')
    record = records.claim(run_id, process_identity())
    if record is None: return 0
    with (root / 'claims.log').open('a') as stream: stream.write(str(os.getpid()) + '\\n')
    while records.get(run_id)['status'] != 'cancelling': time.sleep(.05)
    records.update(run_id, {'status':'cancelled', 'finished_at':'fixture-terminal'})
    return 0
executor.main = run
raise SystemExit(native_executor.main())
'''


class NativeEnvironment(unittest.TestCase):
    def test_private_environment_and_required_database_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); envfile = root / 'postgres.env'
            envfile.write_text('PORTAL_DATABASE_URL="postgresql://fixture-only"\n', encoding='utf-8')
            with patch.dict(os.environ, {'PORTAL_POSTGRES_ENV_FILE':str(envfile),
                    'ANTHROPIC_API_KEY':'must-not-leak', 'PORTAL_FIXTURE_TOKEN':'must-not-leak',
                    'PORTAL_DATABASE_URL':'must-not-leak', 'PORTAL_TEST_TAU_MODEL':'fixture-model'}):
                local = native.executor_environment(root, tau=False)
                tau = native.executor_environment(root, tau=True)
                self.assertNotIn('PORTAL_DATABASE_URL', local)
                self.assertEqual(tau['PORTAL_DATABASE_URL'], 'postgresql://fixture-only')
                self.assertEqual(tau['PORTAL_TEST_TAU_MODEL'], 'fixture-model')
                self.assertNotIn('ANTHROPIC_API_KEY', tau)
                self.assertNotIn('PORTAL_FIXTURE_TOKEN', tau)
                self.assertNotIn('fixture-only', ' '.join(native.executor_command('testrun_'+'a'*16,root)))
                envfile.unlink()
                with self.assertRaises(FileNotFoundError): native.executor_environment(root, tau=True)

    def test_invalid_run_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): native.run_directory(Path(tmp), '../another-run')


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows Jobs')
class NativeExecutor(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tau native 空格 ')
        self.root = Path(self.temporary.name)
        self.records = RunRecords(self.root / 'runs.json')
        self.run_id = self.records.create({'campaign':'tau', 'selected':[], 'execution':{'groups':[]}}, 'fixture')['run_id']
        self.workload = self.root / 'workload.py'
        self.workload.write_text(WORKLOAD, encoding='utf-8')
        self.processes = []

    def tearDown(self):
        import psutil
        self.records.request_cancel(self.run_id)
        for identity in self.processes:
            try:
                if process_is_alive(identity): psutil.Process(identity['pid']).kill()
            except psutil.NoSuchProcess: pass
        until(lambda: all(not process_is_alive(i) for i in self.processes))
        from management_common.file_lock import try_lock_descriptor, unlock_descriptor
        def unlocked():
            with (native.run_directory(self.root,self.run_id)/'executor.lock').open('a+b') as stream:
                if not try_lock_descriptor(stream.fileno()): return False
                unlock_descriptor(stream.fileno())
                return True
        until(unlocked)
        self.temporary.cleanup()

    def launch_fixture(self):
        command = [sys.executable, str(self.workload), '--state-root', str(self.root), self.run_id]
        environment = native.executor_environment(self.root, tau=False)
        environment['PYTHONPATH'] = str(ROOT)
        with patch.object(native, 'executor_command', return_value=command), \
                patch.object(native, 'executor_environment', return_value=environment):
            native.launch(self.run_id, self.root, tau=False)

    def started(self):
        value = until(lambda: self.records.get(self.run_id) if self.records.get(self.run_id)['status']=='running' else None)
        self.processes.append(value['executor'])
        until(lambda: (self.root/'claims.log').exists())
        return value['executor']

    def test_duplicate_launch_and_durable_cancel(self):
        self.launch_fixture(); identity = self.started()
        self.launch_fixture()
        self.assertEqual(self.records.get(self.run_id)['executor'], identity)
        self.assertEqual(len((self.root/'claims.log').read_text().splitlines()), 1)
        self.records.request_cancel(self.run_id)
        until(lambda: self.records.get(self.run_id)['status']=='cancelled')
        until(lambda: not process_is_alive(identity))

    def test_cancel_before_start_uses_real_executor(self):
        self.records.request_cancel(self.run_id)
        native.launch(self.run_id, self.root, tau=False)
        until(lambda: self.records.get(self.run_id)['status']=='cancelled')
        self.assertFalse((self.root/'claims.log').exists())

    def test_dead_executor_can_resume_same_run_and_invocation(self):
        import psutil
        self.launch_fixture(); original = self.started()
        invocation = {'message_id':'fixture-original-input', 'submission_state':'submitted'}
        self.records.update(self.run_id, {'groups':[{'group_id':'fixture', 'active_invocation':invocation}]})
        psutil.Process(original['pid']).kill()
        until(lambda: not process_is_alive(original))
        self.launch_fixture()
        new = until(lambda: self.records.get(self.run_id)['executor'] if self.records.get(self.run_id)['executor']!=original else None)
        self.processes.append(new)
        self.assertEqual(self.records.get(self.run_id)['groups'][0]['active_invocation'], invocation)
        self.assertEqual(self.records.get(self.run_id)['run_id'], self.run_id)
        self.records.request_cancel(self.run_id)
        until(lambda: self.records.get(self.run_id)['status']=='cancelled')

    def test_executor_survives_web_job_termination(self):
        script = f'''
import time
from pathlib import Path
from unittest.mock import patch
from management_portal.test_framework import native_executor as n
root=Path({str(self.root)!r})
env=n.executor_environment(root,tau=False); env['PYTHONPATH']={str(ROOT)!r}
with patch.object(n,'executor_command',return_value={[sys.executable,str(self.workload),'--state-root',str(self.root),self.run_id]!r}), patch.object(n,'executor_environment',return_value=env):
    n.launch({self.run_id!r},root,tau=False)
time.sleep(60)
'''
        web = process_runtime.spawn_process([sys.executable,'-c',script], cwd=ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            identity = self.started()
            process_runtime.terminate_process_tree(web,force=True)
            self.assertTrue(process_is_alive(identity))
            self.records.request_cancel(self.run_id)
            until(lambda: self.records.get(self.run_id)['status']=='cancelled')
        finally:
            process_runtime.terminate_process_tree(web,force=True)


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows browser ownership')
class NativeBrowser(unittest.TestCase):
    def test_chromium_lifecycle_and_recovery_after_owner_exit(self):
        import psutil
        from management_portal.test_framework import tau_browser_owner as owner
        from management_portal.test_framework.tau import browser_runtime_issue
        self.assertEqual(browser_runtime_issue(), '')
        for abrupt in (False, True):
            with self.subTest(abrupt=abrupt), tempfile.TemporaryDirectory(prefix='tau browser 空格 ') as tmp:
                directory = Path(tmp)
                lease = owner.start_browser(directory, shutil.which('node'), '1')
                try:
                    host = until(lambda: json.loads((directory/'tau-browser-host.json').read_text())
                            if (directory/'tau-browser-host.json').exists() else None)
                    self.assertEqual(host['state'], 'running')
                    # Connect using this Windows TAU runtime to an actual local
                    # Chromium, operate a page and save a screenshot.
                    script = """(async()=>{const b=await require(process.argv[1]).chromium.connect(process.argv[2]); const c=await b.newContext(); const p=await c.newPage(); await p.setContent('<h1>Windows TAU browser</h1>'); if(await p.locator('h1').innerText()!=='Windows TAU browser') throw Error('page mismatch'); await p.screenshot({path:process.argv[3]}); await c.close(); await b.close();})().catch(e=>{console.error(e);process.exit(1)});"""
                    process_runtime.run_process([shutil.which('node'),'-e',script,
                        str(ROOT/'management_portal/test_framework/tau_runtime/node_modules/playwright'),
                        host['ws_endpoint'],str(directory/'page.png')],check=True,timeout=25)
                    self.assertGreater((directory/'page.png').stat().st_size,100)
                    if abrupt:
                        psutil.Process(lease['owner']['pid']).kill()
                        until(lambda: not owner._same(lease['owner']))
                finally:
                    stopped = owner.stop_browser(directory)
                self.assertEqual(stopped['state'], 'closed')
                self.assertFalse(process_runtime.process_tree_alive(tree_id=lease['tree_id']))
                until(lambda: not owner._same(lease['owner']))
                self.assertFalse(Path(host['profile_root']).exists())


if __name__ == '__main__':
    unittest.main()
