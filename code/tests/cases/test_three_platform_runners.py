"""Process-only runner tests. Native tests use temporary children, never Portal services.

Run with unittest: bypasses the repository pytest database fixture intentionally.
Windows/macOS cases simulate contracts; they are not native platform certification.
"""
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from management_common import process_runtime
from management_portal import business_tasks
from management_portal.test_framework import local_runs, runner_adapters
from management_portal.workbench_projections import application_build


class PlatformContracts(unittest.TestCase):
    def test_build_never_runs_host_npm_cmd_without_isolation(self):
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform), patch.object(sys, 'platform', platform), \
                    patch.object(subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(application_build.ProjectionError, '隔离沙箱'):
                    application_build._command(['npm.cmd', 'run', 'build'], cwd=Path.cwd())
                popen.assert_not_called()

    def test_isolated_lane_has_explicit_capability_diagnostic(self):
        group = local_runs.PytestGroup({'group_id': 'case'}, [], state_roots=[])
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform), patch.object(sys, 'platform', platform):
                with self.assertRaisesRegex(RuntimeError, 'Linux.*bwrap'):
                    group.sandbox(Path.cwd())

    def test_windows_environment_and_utf8_contract(self):
        group = local_runs.PytestGroup({'group_id': 'case', 'lane': 'live'}, [])
        seed = {'SystemRoot': 'C:\\Windows', 'COMSPEC': 'C:\\Windows\\System32\\cmd.exe',
                'PATHEXT': '.COM;.EXE;.BAT;.CMD', 'TEMP': 'C:\\Temp', 'SECRET_TOKEN': 'excluded'}
        with patch.dict(os.environ, seed, clear=True):
            env = group.environment({})
        for name in ('SystemRoot', 'COMSPEC', 'PATHEXT', 'TEMP'):
            self.assertEqual(env[name], seed[name])
        self.assertNotIn('SECRET_TOKEN', env)
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        self.assertEqual(env['PYTHONUTF8'], '1')

    def test_business_identity_native_and_legacy(self):
        owner = business_tasks.process_identity()
        self.assertTrue(business_tasks.process_is_alive(owner))
        self.assertTrue(business_tasks.process_is_alive({**owner, 'host': 'another-host'}))
        if sys.platform == 'linux':
            self.assertTrue(business_tasks.process_is_alive({**owner, 'pid_namespace': 'unknown-namespace'}))
            self.assertFalse(business_tasks.process_is_alive({**owner, 'boot_id': 'old-boot'}))

    def test_business_identity_portable_reuse_and_denied(self):
        import psutil
        for platform in ('win32', 'darwin'):
            process = Mock()
            process.create_time.return_value = 123.456
            with self.subTest(platform=platform), patch.object(sys, 'platform', platform), \
                    patch.object(psutil, 'Process', return_value=process), \
                    patch.object(process_runtime, 'process_alive', return_value=True):
                owner = business_tasks.process_identity()
                self.assertEqual(owner['identity_kind'], 'process-create-time-v1')
                self.assertTrue(business_tasks.process_is_alive(owner))
                process.create_time.return_value = 124.0
                self.assertFalse(business_tasks.process_is_alive(owner))
                process.create_time.side_effect = psutil.AccessDenied(os.getpid())
                self.assertTrue(business_tasks.process_is_alive(owner))

    def test_missing_metadata_dependency_is_explicit(self):
        with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, {'psutil': None}):
            with self.assertRaisesRegex(RuntimeError, 'psutil'):
                business_tasks.process_identity()
        with patch.dict(sys.modules, {'psutil': None}):
            with self.assertRaisesRegex(RuntimeError, 'psutil'):
                runner_adapters._process_snapshot()

    def test_metadata_failure_preserves_fixtures_and_failure_receipt(self):
        adapter = object.__new__(runner_adapters.TauAdapter)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(runner_adapters, '_process_snapshot', side_effect=RuntimeError('metadata unavailable')), \
                patch('management_portal.test_framework.tau_fixtures.cleanup') as cleanup:
            directory = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, 'metadata unavailable'):
                adapter.cleanup(run_dir=directory, emit=lambda _: None)
            cleanup.assert_not_called()
            receipt = json.loads((directory / 'framework-helper-cleanup.json').read_text())
            self.assertFalse(receipt['complete'])
            self.assertIn('metadata unavailable', receipt['error'])


class HelperContracts(unittest.TestCase):
    def snapshot(self, directory):
        meta = runner_adapters.HelperProcess
        return {10: meta(1, ('shell', str(directory)), 1.0),
                20: meta(10, ('python', str(directory)), 2.0),
                30: meta(20, ('node', str(directory / 'host.js')), 3.0),
                40: meta(30, ('chromium',), 4.0),
                50: meta(1, ('node', str(directory) + '-other'), 5.0)}

    def test_protect_ancestors_and_sibling_prefix(self):
        directory = Path('/tmp/owned-case')
        with patch.object(os, 'getpid', return_value=20), patch.object(os, 'getppid', return_value=10), \
                patch.object(runner_adapters, '_process_snapshot', return_value=self.snapshot(directory)):
            self.assertEqual(runner_adapters._artifact_helper_targets(directory), {30, 40})

    def test_orphan_browser_option_path_is_owned(self):
        directory = Path('/tmp/owned-case')
        snapshot = {30: runner_adapters.HelperProcess(1, ('chrome', '--user-data-dir=' + str(directory / 'browser')), 3.0),
                    40: runner_adapters.HelperProcess(1, ('chrome', '--user-data-dir=' + str(directory) + '-other'), 4.0)}
        self.assertEqual(runner_adapters._artifact_helper_targets(directory, snapshot), {30})

    def test_cleanup_uses_owner_for_verified_roots_only(self):
        import psutil
        directory = Path('/tmp/owned-case')
        process = Mock()
        process.create_time.return_value = 3.0
        with patch.object(os, 'getpid', return_value=20), patch.object(os, 'getppid', return_value=10), \
                patch.object(runner_adapters, '_process_snapshot', return_value=self.snapshot(directory)), \
                patch.object(psutil, 'Process', return_value=process), \
                patch.object(process_runtime, 'terminate_process_tree') as terminate:
            self.assertEqual(runner_adapters._terminate_artifact_helpers(directory), [30, 40])
            terminate.assert_called_once_with(30, timeout=2)

    def test_reused_pid_never_terminated(self):
        import psutil
        directory = Path('/tmp/owned-case')
        process = Mock()
        process.create_time.return_value = 999.0
        with patch.object(os, 'getpid', return_value=20), patch.object(os, 'getppid', return_value=10), \
                patch.object(runner_adapters, '_process_snapshot', return_value=self.snapshot(directory)), \
                patch.object(psutil, 'Process', return_value=process), \
                patch.object(process_runtime, 'terminate_process_tree') as terminate:
            with self.assertRaisesRegex(RuntimeError, '身份'):
                runner_adapters._terminate_artifact_helpers(directory)
            terminate.assert_not_called()

    def test_windows_unmanaged_helpers_fail_closed(self):
        directory = Path('/tmp/owned-case')
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(os, 'getpid', return_value=20), patch.object(os, 'getppid', return_value=10), \
                patch.object(runner_adapters, '_process_snapshot', return_value=self.snapshot(directory)), \
                patch.object(process_runtime, 'terminate_process_tree') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'Job'):
                runner_adapters._terminate_artifact_helpers(directory)
            terminate.assert_not_called()


class LocalProcessTests(unittest.TestCase):
    def run_group(self, directory, code, *, stop='', consume=None, output=None):
        group = local_runs.PytestGroup({'group_id': 'pytest-test', 'lane': 'live'}, [], root=directory)
        group.command = lambda *_: ([sys.executable, '-c', code], group.environment({}))
        group.finish = Mock()
        if consume:
            group._consume = consume
        emitted = []
        ctx = SimpleNamespace(run_dir=directory, patch=lambda _: None,
                              output=output or emitted.append, stop_reason=lambda: stop)
        with patch.object(local_runs, 'POLL_SECONDS', 0.02):
            group._run_process([], ctx, 1)
        return group, emitted

    def test_live_group_utf8_and_owner_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(process_runtime, 'spawn_process', wraps=process_runtime.spawn_process) as spawn, \
                patch.object(process_runtime, 'terminate_process_tree', wraps=process_runtime.terminate_process_tree) as terminate:
            group, emitted = self.run_group(Path(tmp), "print('中文 terminal 🐈')")
            self.assertIn('中文 terminal 🐈', ''.join(emitted))
            self.assertEqual(spawn.call_args.kwargs['encoding'], 'utf-8')
            terminate.assert_called()
            group.finish.assert_called_once()

    def test_stop_and_callback_failure_cleanup(self):
        for mode in ('cancel', 'consume', 'output'):
            children = []
            real_spawn = process_runtime.spawn_process

            def spawn_child(*args, **kwargs):
                child = real_spawn(*args, **kwargs)
                children.append(child)
                return child

            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(process_runtime, 'spawn_process', side_effect=spawn_child) as spawn:
                def fail(*_):
                    raise RuntimeError('injected callback failure')
                kwargs = {'stop': 'cancelled'} if mode == 'cancel' else {mode: fail}
                if mode == 'cancel':
                    group, _ = self.run_group(Path(tmp), 'import time;time.sleep(30)', **kwargs)
                    self.assertEqual(group.finish.call_args.args[-1], 'cancelled')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'injected callback failure'):
                        self.run_group(Path(tmp), "import time;print('ready',flush=True);time.sleep(30)", **kwargs)
                # Capture the managed handle without a second launch.
                self.assertEqual(spawn.call_count, 1)
                self.assertIsNotNone(children[0].poll())
                self.assertFalse(process_runtime.process_alive(children[0].pid))
                self.assertTrue(children[0].stdout.closed)

    def test_windows_mac_managed_launch_contract_with_spaced_python_path(self):
        for platform, executable in (('win32', r'C:\Program Files\Python\python.exe'),
                                     ('darwin', '/Applications/Python 3/bin/python3')):
            child = Mock()
            child.stdout = io.StringIO('中文\n')
            child.poll.return_value = 0
            child.wait.return_value = 0
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(sys, 'platform', platform), patch.object(sys, 'executable', executable), \
                    patch.object(process_runtime, 'spawn_process', return_value=child) as spawn, \
                    patch.object(process_runtime, 'terminate_process_tree') as terminate:
                group, emitted = self.run_group(Path(tmp), "print('中文')")
                self.assertEqual(spawn.call_args.args[0][0], executable)
                self.assertNotIn('shell', spawn.call_args.kwargs)
                self.assertNotIn('start_new_session', spawn.call_args.kwargs)
                self.assertEqual(emitted, ['中文\n'])
                terminate.assert_called_once_with(child, force=True)
                self.assertTrue(child.stdout.closed)

    def test_launch_failure_closes_log(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(process_runtime, 'spawn_process', side_effect=OSError('missing executable')):
            with self.assertRaisesRegex(OSError, 'missing executable'):
                self.run_group(Path(tmp), 'pass')
            # A Windows reopen/delete is possible only after the log is released.
            (Path(tmp) / 'pytest-test' / 'output.log').unlink()

    def test_live_leaked_descendant_reaped_after_leader_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / 'child.pid'
            code = ("import subprocess,sys,pathlib;child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                    f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid));print('leader done')")
            self.run_group(root, code)
            self.assertFalse(process_runtime.process_alive(int(pidfile.read_text())))


@unittest.skipUnless(sys.platform == 'linux', 'Linux native checks only')
class LinuxBuildTests(unittest.TestCase):
    def test_real_sandbox_never_executes_on_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = application_build._sandbox_command(
                Path(tmp), ['/usr/bin/node', '-e', "process.stdout.write(process.cwd()+'|'+process.env.HOME)"])
            self.assertEqual(result, b'/app|/home/builder')

    def test_command_stdin_nonzero_and_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertEqual(application_build._command(
                [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'],
                cwd=directory, input='文本'.encode()), '文本'.encode())
            with self.assertRaisesRegex(application_build.ProjectionError, '快照命令失败'):
                application_build._command([sys.executable, '-c', 'raise SystemExit(3)'], cwd=directory)
            with self.assertRaises(application_build.ProjectionError):
                application_build._command([sys.executable, '-c', "print('x'*1000000)"], cwd=directory, limit=64)

    def test_command_timeout_and_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            code = "import subprocess,sys;child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);print(child.pid)"
            pid = int(application_build._command([sys.executable, '-c', code], cwd=directory))
            self.assertFalse(process_runtime.process_alive(pid))
            with self.assertRaisesRegex(application_build.ProjectionError, '超时'):
                application_build._command([sys.executable, '-c', 'import time;time.sleep(30)'], cwd=directory, timeout=0.1)

    def test_real_tau_helper_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            child = process_runtime.spawn_process([sys.executable, '-c', 'import time;time.sleep(30)', str(directory / 'helper.py')])
            try:
                self.assertIn(child.pid, runner_adapters._terminate_artifact_helpers(directory))
                child.wait(timeout=5)
                self.assertFalse(process_runtime.process_alive(child.pid))
            finally:
                process_runtime.terminate_process_tree(child, force=True)


if __name__ == '__main__':
    unittest.main()
