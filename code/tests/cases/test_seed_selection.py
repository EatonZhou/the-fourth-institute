"""安装预置案例选择与启动失败诊断。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import io
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from prepare_install import prepare
from scripts import tfi_seed_selection as selection
from scripts import tfi_release_install as installer
from scripts import tfi_windows_launcher as launcher
from scripts.windows.build import bundle_seed


class SeedSelection(unittest.TestCase):
    def test_base_archive_excludes_cases_before_extraction_and_fits_user_path(self):
        """真实基础包不含案例文件，用户路径下的准备及最终路径不触发长路径。"""
        seed = REPO / 'instance/example_instance/seed'
        original = json.loads((seed / 'manifest.json').read_text())
        _, _, excluded = selection.plan(seed, original['engineering'], include_examples=False)
        with tempfile.TemporaryDirectory(dir='/tmp' if os.name != 'nt' else None) as temporary:
            root = Path(temporary)
            if os.name != 'nt':
                # Match the user's installation prefix while using real files.
                root /= 'p' * max(1, 53 - len(str(root)) - 1)
                root.mkdir()
            archive = root / 'base-seed.zip'
            bundle_seed(seed, archive, include_examples=False)
            (root / 'windows-package.json').write_text(json.dumps({
                'base_seed_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))
            (root / 'installation-options.ini').write_text('[installation]\ninclude_examples=false\n')
            with zipfile.ZipFile(archive) as zipped:
                spec = json.loads(zipped.read('manifest.json'))
                self.assertFalse(any(name == p or name.startswith(p + '/')
                                     for name in zipped.namelist() for p in excluded))
                for row in spec['inventory']:
                    contents = zipped.read(row['path'])
                    self.assertEqual(len(contents), row['size'])
                    self.assertEqual(hashlib.sha256(contents).hexdigest(), row['sha256'])
            target = PureWindowsPath(r'C:\Users\weihaozhang\AppData\Local\TheFourthInstitute')
            for path in launcher.seed_destination_paths(target, spec):
                self.assertLess(len(str(path).encode('utf-16-le')) // 2, 260, str(path))
                self.assertLess(len(str(path.parent).encode('utf-16-le')) // 2, 248, str(path))
            launcher.unpack_seed(root, included=False)
            extracted = root / 'instance/example_instance/seed'
            self.assertFalse(any((extracted / p).exists() for p in excluded))
            if os.name != 'nt':
                self.assertIsNone(launcher.required_long_path(root))
            deep = root / ('long-directory-' * 4)
            deep.mkdir()
            (deep / 'installation-options.ini').write_text('[installation]\ninclude_examples=false\n')
            with patch.object(launcher, 'selected_seed', return_value=archive):
                self.assertIsNotNone(launcher.required_long_path(deep))

    def test_literal_parser_preserves_quotes_newlines_and_backslashes(self):
        """种子解析区分 SQL 转义和 JSON 转义，忽略正文中的语句。"""
        text = '''INSERT INTO "s"."t" ("a","b","c") VALUES ('it''s \\path',E'line\\nnext', '{"v":"x"}'::jsonb);
INSERT INTO "s"."t" ("a") VALUES ('body\nINSERT INTO "x"."y" ("z") VALUES (1);');'''
        rows = list(selection.inserts(text))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][3], {'a': "it's \\path", 'b': 'line\nnext', 'c': {'v': 'x'}})
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            list(selection.inserts('INSERT INTO other VALUES (1);'))

    def test_real_seed_excludes_owned_graph_and_retains_platform(self):
        """不选案例时保留平台团队，排除案例记录、文件及清单引用。"""
        seed = REPO / 'instance/example_instance/seed'
        manifest = json.loads((seed / 'manifest.json').read_text())['engineering']
        planned, sql, paths = selection.plan(seed, manifest, include_examples=False)
        rows = list(selection.inserts(sql))
        lineages = [row for _, _, table, row in rows if table == ('lineage', 'lineages')]
        self.assertTrue(lineages)
        self.assertFalse(any(row['project_id'] == selection.LENIA_PROJECT for row in lineages))
        self.assertTrue(any(row['project_id'] == 'AI员工' for row in lineages))
        self.assertIn('data/share_root/Codes/repo_bd47ffeac5c8', paths)
        self.assertTrue(any(p.startswith('data/staging_root/shared/projection_applications/') for p in paths))
        self.assertNotIn(selection.LENIA_PROJECT, list(selection.strings(planned['selection_snapshot'])))
        self.assertIn('article', [r['type'] for r in planned['selection_snapshot']['resource_types']])
        self.assertEqual(selection.plan(seed, manifest, include_examples=True), (manifest, None, set()))

    def test_desktop_selection_and_retry_keep_initial_choice(self):
        """桌面准备读取安装选择，已有安装记录重试不重新选择或导入。"""
        for included in (True, False):
            with self.subTest(included=included), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'installation-options.ini').write_text('[installation]\ninclude_examples=' + str(included).lower())
                bundle = root / '.build/w/engineering/manifest.json'
                bundle.parent.mkdir(parents=True)
                bundle.write_text(json.dumps({'run_id': 'fixture'}))
                flow = launcher.DefaultStartup(root)
                with patch.object(flow, 'execute') as execute:
                    flow.initialize()
                    prepare_command = execute.call_args_list[0].args[0]
                    self.assertEqual('--without-examples' in prepare_command, not included)
                    flow.root.mkdir(parents=True)
                    (flow.root / 'installation.json').write_text('{}')
                    execute.reset_mock()
                    flow.initialize()
                    self.assertEqual(execute.call_count, 1)
                    self.assertIn('install', execute.call_args.args[0])

    def test_health_error_body_survives_and_diagnostics_precede_rollback(self):
        """503 中的组件错误进入持久诊断，而非只报告 HTTP 状态。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'runtime').mkdir()
            target = {'install_root': str(root), 'service_prefix': 'tfi-fixture', 'run_id': 'fixture',
                      'supervisor_resident_id': '', 'ports': {'web': 28000}, 'bind': '127.0.0.1', 'python': sys.executable}
            state = {'target': target}
            payload = {'ok': False, 'execution_layer': {'ok': False}, 'society_service': {'ok': True}}
            http = HTTPError('http://localhost/api/health', 503, 'Unavailable', {}, io.BytesIO(json.dumps(payload).encode()))
            controller = Mock()
            controller.status.return_value = {'service': 'tfi-fixture-worker', 'active': 'inactive'}
            (root / 'runtime/worker.log').write_text('earlier output\nTraceback (most recent call last):\nRuntimeError: fixture startup failure\n')
            with patch.object(installer, 'service_controller', return_value=controller), \
                    patch.object(installer, 'urlopen', side_effect=[http, OSError('not ready')]):
                result = installer.observed_status(state)
            self.assertEqual(result['checks'][0]['payload'], payload)
            with patch.object(installer, 'unit_name', side_effect=lambda target, component: 'tfi-fixture-' + component):
                error = installer.health_failure(state, result)
            self.assertIn('execution_layer', error)
            self.assertIn('tfi-fixture-worker: inactive', error)
            self.assertIn('RuntimeError: fixture startup failure', error)
            saved = json.loads((root / 'runtime/startup-diagnostics.json').read_text())
            self.assertEqual(saved['observed'], result)
            self.assertIn('fixture startup failure', saved['component_log_tails']['worker'])

    def test_login_http_failure_keeps_status_and_running_web_log(self):
        """进程仍运行时，登录页 500 也保留 HTTP 状态及有界 web 日志。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'runtime').mkdir()
            target = {'install_root': str(root), 'service_prefix': 'tfi-fixture', 'run_id': 'fixture',
                      'supervisor_resident_id': '', 'ports': {'web': 28000}, 'bind': '127.0.0.1', 'python': sys.executable}
            state = {'target': target}
            (root / 'runtime/worker-health.json').write_text(json.dumps({
                'ok': True, 'instance': 'tfi-fixture', 'updated_at': installer.time.time()}))
            (root / 'runtime/web.log').write_text('old-start-marker\n' + 'old output\n' * 2000
                + 'Traceback (most recent call last):\nValueError: Invalid public directory: share_root\n')
            health = Mock(status=200)
            health.__enter__ = Mock(return_value=health)
            health.__exit__ = Mock(return_value=False)
            health.read.return_value = json.dumps({'ok': True, 'instance': 'tfi-fixture',
                'execution_layer': {'ok': True}, 'society_service': {'ok': True}}).encode()
            login = HTTPError('http://localhost/login', 500, 'Internal Server Error', {}, io.BytesIO(b'Internal Server Error'))
            controller = Mock()
            controller.status.side_effect = lambda name: {'service': name, 'active': 'active'}
            with patch.object(installer, 'service_controller', return_value=controller), \
                    patch.object(installer, 'urlopen', side_effect=[health, login]):
                result = installer.observed_status(state)
            self.assertEqual(result['status'], 'unhealthy')
            self.assertEqual(result['checks'][1]['http_status'], 500)
            self.assertEqual(installer.health_failures(result), ['/login: HTTP 500'])
            error = installer.health_failure(state, result)
            self.assertIn('/login: HTTP 500', error)
            self.assertIn('ValueError: Invalid public directory: share_root', error)
            self.assertNotIn('old-start-marker', error)
            saved = json.loads((root / 'runtime/startup-diagnostics.json').read_text())
            self.assertEqual(set(saved['component_log_tails']), {'web'})
            self.assertEqual(saved['observed'], result)


@unittest.skipUnless(os.getenv('TFI_TEST_POSTGRES_ROOT'), 'Requires an explicit disposable PostgreSQL binary directory')
class SeedDatabase(unittest.TestCase):
    def test_selected_seed_imports_with_all_constraints_and_no_dangling_nodes(self):
        """真实 PostgreSQL 导入无案例种子并检查关联完整性。"""
        native = Path(os.environ['TFI_TEST_POSTGRES_ROOT'])
        env = {**os.environ, 'LD_LIBRARY_PATH': str(native / 'lib')}
        with tempfile.TemporaryDirectory(prefix='tfi-case-db-') as temporary:
            root = Path(temporary)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            def run(name, *args, **kwargs):
                return subprocess.run([str(native / 'bin' / name), *map(str, args)], env=env,
                                      check=True, capture_output=True, **kwargs)
            run('initdb', '-D', root / 'pg', '-L', native / 'share', '--auth=trust', '--no-locale', '--encoding=UTF8', '--no-sync')
            run('pg_ctl', '-D', root / 'pg', '-l', root / 'pg.log', '-o', f"-F -k {root} -p {port} -h ''", 'start', '-w')
            try:
                seed = REPO / 'instance/example_instance/seed'
                manifest = json.loads((seed / 'manifest.json').read_text())['engineering']
                _, sql, _ = selection.plan(seed, manifest, include_examples=False)
                for key in (*manifest['files'], 'code_root'):
                    sql = sql.replace('${' + key + '}', str(root / key))
                run('psql', '-h', root, '-p', port, '-d', 'postgres', '-X', '-v', 'ON_ERROR_STOP=1', '-1', input=sql.encode())
                checks = """SELECT count(*) FROM lineage.lineages WHERE project_id='元胞自动机研究';
SELECT count(*) FROM lineage.lineage_nodes n LEFT JOIN lineage.lineages l USING(lineage_id) WHERE l.lineage_id IS NULL;
SELECT count(*) FROM lineage.lineage_resident_nodes n LEFT JOIN lineage.residents r ON r.resident_id=n.node_id WHERE r.resident_id IS NULL;
SELECT count(*) FROM lineage.residents r LEFT JOIN workspace.workspaces w ON r.owned_workspace_id=w.workspace_id WHERE r.owned_workspace_id IS NOT NULL AND w.workspace_id IS NULL;"""
                result = run('psql', '-h', root, '-p', port, '-d', 'postgres', '-XAt', '-v', 'ON_ERROR_STOP=1', input=checks.encode())
                self.assertEqual(result.stdout.decode().split(), ['0'] * 4)
            finally:
                run('pg_ctl', '-D', root / 'pg', 'stop', '-m', 'fast', '-w')
