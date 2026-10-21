"""Docker build transport contracts; no daemon, image pulls or live installs.

Linux native stream tests exercise only disposable local Python children.
Platform parameter cases simulate callers, not native Windows/macOS acceptance.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from management_portal.workbench_projections import application_build as build
from management_common import process_runtime

IMAGE = 'sha256:' + 'a' * 64


class DockerContracts(unittest.TestCase):
    def run_container(self, platform='darwin', failure=None, image=None, state=None, network=False, mounts=(),
                      writable_cache=None):
        calls = []
        owned = {}

        def cli(argv, **kwargs):
            calls.append((argv, kwargs))
            if 'image' in argv:
                if failure == 'image':
                    return subprocess.CompletedProcess(argv, 1, b'', b'image missing or access denied')
                data = image if image is not None else {'Id': IMAGE, 'Os': 'linux', 'Config': {'Volumes': None}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'')
            if 'create' in argv:
                owned.update(Id='b' * 64, Name='/' + argv[argv.index('--name') + 1],
                             Config={'Labels': dict(argv[n+1].split('=', 1) for n,v in enumerate(argv) if v == '--label')})
                if failure in ('create', 'create-unknown'):
                    raise subprocess.TimeoutExpired(argv, 1)
                if failure == 'mount':
                    return subprocess.CompletedProcess(argv, 1, b'', b'Mounts denied')
                return subprocess.CompletedProcess(argv, 0, b'container-id\n', b'')
            if 'start' in argv:
                if failure == 'start':
                    raise subprocess.TimeoutExpired(argv, 1)
                if failure == 'output':
                    raise build.ProjectionError('projection.application_size', 'output limit')
                return subprocess.CompletedProcess(argv, 0, '构建 output'.encode(), b'')
            if 'inspect' in argv:
                if '{{json .}}' in argv:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(owned).encode(), b'')
                data = state if state is not None else {'Running': False, 'ExitCode': 0, 'Error': ''}
                return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'')
            if 'rm' in argv:
                return subprocess.CompletedProcess(argv, 1 if failure in ('remove', 'create-unknown') else 0, b'', b'daemon unavailable')
            if 'ls' in argv:
                if failure == 'create-unknown':
                    return subprocess.CompletedProcess(argv, 0, b'', b'')
                return subprocess.CompletedProcess(argv, 0, ('b' * 64).encode(), b'')
            raise AssertionError(argv)

        settings = {'docker_executable': '/native/docker', 'docker_endpoint': 'unix:///local/docker.sock', 'container_image': IMAGE}
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, 'platform', platform), \
                patch.object(build, '_container_settings', return_value=settings), \
                patch.object(build, '_docker_cli', side_effect=cli):
            root = Path(tmp)
            command = ['/usr/bin/npm', '--ignore-scripts', 'ci'] if network else ['/usr/bin/npm', 'run', 'build']
            try:
                result = build._container_command_owned(root, command, network=network, mounts=mounts,
                                                  writable_cache=writable_cache)
            except Exception as error:
                return calls, error
        return calls, result

    def test_both_platforms_isolated_and_removed(self):
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform):
                calls, result = self.run_container(platform)
                self.assertEqual(result, '构建 output'.encode())
                create = next(a for a, _ in calls if 'create' in a)
                for flag in ('--read-only', '--cap-drop=ALL', '--no-healthcheck', '--pull=never', '--network=none',
                             '--pids-limit=128', '--memory=1g', '--memory-swap=1g', '--cpus=2', '--security-opt=no-new-privileges'):
                    self.assertIn(flag, create)
                self.assertIn(IMAGE, create)
                self.assertIn('/usr/bin/prlimit', create)
                self.assertIn('/usr/bin/timeout', create)
                self.assertIn('--kill-after=15s', create)
                self.assertIn(str(build.BUILD_TIMEOUT) + 's', create)
                self.assertNotIn('/usr/bin/bwrap', create)
                self.assertEqual(create.count('--mount'), 1)
                self.assertEqual(calls[-1][0][-3:-1], ['rm', '--force'])
                self.assertNotIn('pull', [x for a, _ in calls for x in a])
                for _, kwargs in calls:
                    self.assertNotIn('DOCKER_HOST', kwargs['env'])
                    self.assertNotIn('HTTP_PROXY', kwargs['env'])

    def test_cleanup_after_unknown_create_timeout_and_output_overflow(self):
        for failure in ('create', 'start', 'output'):
            with self.subTest(failure=failure):
                calls, error = self.run_container(failure=failure)
                self.assertIsInstance(error, (subprocess.TimeoutExpired, build.ProjectionError))
                self.assertIn('rm', calls[-1][0])

    def test_cleanup_failure_never_returns_success(self):
        calls, error = self.run_container(failure='remove')
        self.assertIsInstance(error, build.ProjectionError)
        self.assertIn('清理', str(error))

    def test_unanswered_create_cannot_claim_absence_from_one_empty_listing(self):
        _, error = self.run_container(failure='create-unknown')
        self.assertIsInstance(error, build.ProjectionError)
        self.assertEqual(error.code, 'projection.application_cleanup')
        self.assertIn('portal-app-build-', str(error))

    def test_missing_image_and_denied_mount_never_fall_back(self):
        for failure in ('image', 'mount'):
            with self.subTest(failure=failure), patch.object(build, '_command') as host:
                calls, error = self.run_container(failure=failure)
                self.assertIsInstance(error, build.ProjectionError)
                self.assertFalse(any('start' in a for a, _ in calls))
                self.assertFalse(any('pull' in a for a, _ in calls))
                host.assert_not_called()
                if failure == 'mount':
                    self.assertIn('rm', calls[-1][0])

    def test_cache_mounts_are_explicit_and_readonly_for_offline_build(self):
        with tempfile.TemporaryDirectory(prefix='cache with spaces ') as tmp:
            cache = Path(tmp)
            calls, result = self.run_container(mounts=((cache, '/npm-cache/_cacache'),))
            self.assertIsInstance(result, bytes)
            create = next(a for a, _ in calls if 'create' in a)
            self.assertIn(f'type=bind,src={cache.resolve()},dst=/npm-cache/_cacache,readonly', create)
            self.assertEqual(create.count('--mount'), 2)
            calls, result = self.run_container(network=True, writable_cache=cache)
            self.assertIsInstance(result, bytes)
            create = next(a for a, _ in calls if 'create' in a)
            self.assertIn(f'type=bind,src={cache.resolve()},dst=/npm-cache', create)
            calls, error = self.run_container(mounts=((cache, '/root'),))
            self.assertIsInstance(error, build.ProjectionError)
            self.assertEqual(calls, [])

    def test_actual_container_exit_status_is_authoritative(self):
        _, error = self.run_container(state={'Running': False, 'ExitCode': 137, 'Error': ''})
        self.assertIsInstance(error, build.ProjectionError)
        self.assertIn('137', str(error))

    def test_image_volumes_and_nonlinux_images_rejected_before_creation(self):
        for image in ({'Id': IMAGE, 'Os': 'windows', 'Config': {}},
                      {'Id': IMAGE, 'Os': 'linux', 'Config': {'Volumes': {'/unsafe': {}}}}):
            calls, error = self.run_container(image=image)
            self.assertIsInstance(error, build.ProjectionError)
            self.assertFalse(any('create' in a for a, _ in calls))

    def test_script_free_dependency_network_uses_bridge(self):
        calls, result = self.run_container(network=True)
        self.assertEqual(result, '构建 output'.encode())
        create = next(a for a, _ in calls if 'create' in a)
        self.assertIn('--network=bridge', create)
        self.assertNotIn('--network=host', create)

    def test_source_build_cannot_request_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build, '_container_settings', return_value={}), \
                patch.object(build, '_docker_cli') as cli:
            with self.assertRaisesRegex(build.ProjectionError, '禁用源码脚本'):
                build._container_command_owned(Path(tmp), ['/usr/bin/npm', 'run', 'build'], network=True)
            cli.assert_not_called()

    def test_environment_and_client_config_never_inherit_host_credentials(self):
        seed = {'DOCKER_CONFIG': '/host/secrets', 'DOCKER_HOST': 'tcp://remote',
                'DOCKER_CONTEXT': 'remote', 'HTTP_PROXY': 'http://credential-proxy', 'NPM_TOKEN': 'excluded'}
        with patch.dict(os.environ, seed):
            calls, result = self.run_container()
        self.assertIsInstance(result, bytes)
        for argv, kwargs in calls:
            self.assertTrue(set(seed).isdisjoint(set(kwargs['env']) - {'DOCKER_CONFIG'}))
            self.assertNotEqual(kwargs['env']['DOCKER_CONFIG'], seed['DOCKER_CONFIG'])
            self.assertEqual(argv[argv.index('--config') + 1], kwargs['env']['DOCKER_CONFIG'])
        create = next(a for a, _ in calls if 'create' in a)
        self.assertEqual(create[create.index(IMAGE) + 1], '-i')

    def test_deployment_settings_require_local_engine_and_pinned_image(self):
        for settings in ({'container_image': 'node:latest'},
                         {'container_image': IMAGE, 'docker_endpoint': 'tcp://remote:2375'},
                         {'container_image': IMAGE, 'docker_executable': 'docker.cmd'}):
            settings = {'docker_executable': sys.executable, **settings}
            with patch.object(build, 'read_site_config', return_value={'deployment': {'application_build': settings}}):
                with self.assertRaises(build.ProjectionError):
                    build._container_settings()

    def test_local_engine_settings_and_explicit_spaced_executable(self):
        for platform, endpoint in (('win32', 'npipe:////./pipe/docker_engine'),
                                   ('darwin', 'unix:///private/run/docker.sock')):
            executable = str(Path('/native path') / 'docker.exe')
            settings = {'docker_executable': executable, 'container_image': 'portal/build@' + IMAGE,
                        'docker_endpoint': endpoint}
            with self.subTest(platform=platform), patch.object(sys, 'platform', platform), \
                    patch.object(build, 'read_site_config', return_value={'deployment': {'application_build': settings}}):
                self.assertEqual(build._container_settings(), settings)

    def test_git_snapshot_uses_readonly_project_inside_container(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, 'platform', 'win32'), \
                patch.object(build, '_container_command', return_value=b'git output') as run:
            root = Path(tmp)
            result = build._command(['/usr/bin/git', '-c', 'safe.directory=' + str(root), 'ls-tree', 'HEAD'], cwd=root)
            self.assertEqual(result, b'git output')
            self.assertIn('safe.directory=/app', run.call_args.args[1])
            self.assertTrue(run.call_args.kwargs['readonly_project'])

    def test_snapshot_and_build_boundary_translate_transport_errors(self):
        for platform in ('darwin', 'win32'):
            for failure in (FileNotFoundError('docker'), subprocess.TimeoutExpired('docker', 1)):
                for git in (True, False):
                    with self.subTest(platform=platform, failure=type(failure).__name__, git=git), \
                            patch.object(sys, 'platform', platform), \
                            patch.object(build, '_container_command', side_effect=failure):
                        with self.assertRaises(build.ProjectionError) as raised:
                            if git:
                                build._command(['/usr/bin/git', 'status'], cwd=Path.cwd())
                            else:
                                build._sandbox_command(Path.cwd(), ['/usr/bin/node', '--version'])
                        code = 'timeout' if isinstance(failure, subprocess.TimeoutExpired) else 'toolchain'
                        self.assertEqual(raised.exception.code, 'projection.application_' + code)


class BoundedDockerCli(unittest.TestCase):
    def test_utf8_bytes_stdin_and_leaked_child_cleanup(self):
        code = ('import subprocess,sys;child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
                'print(child.pid,flush=True);sys.stdout.buffer.write(sys.stdin.buffer.read())')
        result = build._docker_cli([sys.executable, '-c', code], env=os.environ.copy(), timeout=3, limit=1000, input='中文'.encode())
        pid, data = result.stdout.split(b'\n', 1)
        self.assertEqual(data, '中文'.encode())
        self.assertFalse(process_runtime.process_alive(int(pid)))

    def test_output_limit_and_timeout_cleanup(self):
        for code, timeout, error in (("import sys;sys.stdout.write('x'*100000);sys.stdout.flush()", 3, build.ProjectionError),
                                     ('import time;time.sleep(30)', 0.05, subprocess.TimeoutExpired)):
            with self.assertRaises(error):
                build._docker_cli([sys.executable, '-c', code], env=os.environ.copy(), timeout=timeout, limit=100)


if __name__ == '__main__':
    unittest.main()
