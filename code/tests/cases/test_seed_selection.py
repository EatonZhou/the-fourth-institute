"""安装预置案例选择与启动失败诊断。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from prepare_install import prepare
from scripts import tfi_seed_selection as selection
from scripts import tfi_release_install as installer
from scripts import tfi_windows_launcher as launcher


class SeedSelection(unittest.TestCase):
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
                bundle = root / '.build/example_instance-windows/engineering/manifest.json'
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
            with patch.object(installer, 'service_controller', return_value=controller), \
                    patch.object(installer, 'urlopen', side_effect=[http, OSError('not ready')]):
                result = installer.observed_status(state)
            self.assertEqual(result['checks'][0]['payload'], payload)
            error = installer.health_failure(state, result)
            self.assertIn('execution_layer', error)
            self.assertIn('tfi-fixture-worker: inactive', error)
            saved = json.loads((root / 'runtime/startup-diagnostics.json').read_text())
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
