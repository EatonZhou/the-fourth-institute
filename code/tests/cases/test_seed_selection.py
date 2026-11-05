"""安装预置案例选择与启动失败诊断。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from prepare_install import prepare
from scripts import tfi_seed_selection as selection
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
                    self.assertEqual(len(contents), row['size'], row['path'])
                    self.assertEqual(hashlib.sha256(contents).hexdigest(), row['sha256'], row['path'])
            # The program's extraction and the default instance root's area copies.
            program = PureWindowsPath(r'C:\Users\weihaozhang\AppData\Local\TheFourthInstitute')
            instance = PureWindowsPath(r'C:\Users\weihaozhang\AppData\Local\TFI\instances\example_instance')
            paths = [program / '.build/w/engineering.pending' / row['path'] for row in spec['inventory']]
            paths += [instance / row['path'] for row in spec['inventory'] if row['path'].startswith('data/')]
            for path in paths:
                self.assertLess(len(str(path).encode('utf-16-le')) // 2, 260, str(path))
                self.assertLess(len(str(path.parent).encode('utf-16-le')) // 2, 248, str(path))
            # The launcher's check reads the selected engineering archive's inventory.
            with zipfile.ZipFile(launcher.selected_engineering(root, False), 'w') as zipped:
                zipped.writestr('manifest.json', json.dumps({'inventory': spec['inventory']}))
            if os.name != 'nt':
                self.assertIsNone(launcher.required_long_path(root, root=root / 'instances/example_instance'))
            deep = root / ('long-directory-' * 4)
            self.assertIsNotNone(launcher.required_long_path(root, root=deep / ('instance-' * 12)))

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
        # The optional case is one project folder (with its Lineages, repo and articles) plus its built application.
        self.assertIn('data/projects/' + selection.LENIA_PROJECT, paths)
        self.assertTrue(any(p.startswith('data/features/projection_applications/') for p in paths))
        self.assertTrue(all(p.split('/')[1] in {'projects', 'features'} for p in paths if p.startswith('data/')))
        self.assertNotIn(selection.LENIA_PROJECT, list(selection.strings(planned['selection_snapshot'])))
        self.assertIn('article', [r['type'] for r in planned['selection_snapshot']['resource_types']])
        self.assertEqual(selection.plan(seed, manifest, include_examples=True), (manifest, None, set()))

    def test_desktop_creates_from_the_engineering_selected_at_installation(self):
        """桌面创建实例时读取安装时的案例选择，使用对应的已准备工程。"""
        for included in (True, False):
            with self.subTest(included=included), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'installation-options.ini').write_text('[installation]\ninclude_examples=' + str(included).lower())
                launcher.selected_engineering(root, included).write_bytes(b'archive')
                bundle = root / '.build/w/engineering/manifest.json'
                flow = launcher.DefaultStartup(root)
                with patch.object(flow, 'registered', return_value=False), \
                        patch.object(launcher, 'unpack_engineering', return_value=bundle) as unpack, \
                        patch.object(flow, 'execute') as execute:
                    flow.initialize()
                self.assertEqual(unpack.call_args.kwargs['included'], included)
                self.assertEqual(unpack.call_args.kwargs['archive'], launcher.selected_engineering(root, included))
                command = execute.call_args.args[0]
                self.assertEqual(command[2], 'create')
                self.assertEqual(Path(command[command.index('--engineering') + 1]), bundle)



class SeedDatabase(unittest.TestCase):
    def test_selected_seed_imports_with_all_constraints_and_no_dangling_nodes(self):
        """真实 PostgreSQL 导入无案例种子并检查关联完整性。"""
        import psycopg
        from tests.cases.postgres_test_database import start_disposable_cluster
        from scripts.tfi_release_install import map_directories

        seed = REPO / 'instance/example_instance/seed'
        manifest = json.loads((seed / 'manifest.json').read_text())['engineering']
        _, sql, _ = selection.plan(seed, manifest, include_examples=False)
        sql = map_directories(sql, {
            entry['placeholder']: '/isolated-seed/' + entry['root']
            for entry in manifest['directory_mappings']
        })
        # Always exercise the real PostgreSQL import using the shared disposable
        # test-cluster helper. No native-root environment opt-in or service access.
        with psycopg.connect(start_disposable_cluster()) as connection:
            connection.execute(sql, prepare=False)
            checks = """SELECT count(*) FROM lineage.lineages WHERE project_id='元胞自动机研究';
SELECT count(*) FROM lineage.lineage_nodes n LEFT JOIN lineage.lineages l USING(lineage_id) WHERE l.lineage_id IS NULL;
SELECT count(*) FROM lineage.lineage_resident_nodes n LEFT JOIN lineage.residents r ON r.resident_id=n.node_id WHERE r.resident_id IS NULL;
SELECT count(*) FROM lineage.residents r LEFT JOIN workspace.workspaces w ON r.owned_workspace_id=w.workspace_id WHERE r.owned_workspace_id IS NOT NULL AND w.workspace_id IS NULL;"""
            for check in checks.split(';'):
                if check.strip():
                    with self.subTest(query=check.strip()):
                        self.assertEqual(connection.execute(check).fetchone()[0], 0)
            connection.rollback()
