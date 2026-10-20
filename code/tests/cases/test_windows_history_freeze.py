"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import os
import json
from pathlib import Path, PureWindowsPath
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "code"))
from management_portal.distribution.history import verify_frozen


class WorkspacePathNormalization(unittest.TestCase):
    def test_windows_paths_match_imported_paths_and_reject_roots(self):
        """运行时路径与导入路径一致，盘符和 UNC 共享根不能登记为工作区。"""
        from management_portal.resources import workspace_store
        for raw in (r"C:\Users\安装 用户\workspace", r"\\server\share\workspace",
                    "C:\\", "\\\\server\\share\\", "relative\\workspace"):
            resolved = PureWindowsPath(raw)
            with self.subTest(path=raw), patch.object(workspace_store, "Path") as path:
                expanded = path.return_value.expanduser.return_value
                expanded.is_absolute.return_value = resolved.is_absolute()
                expanded.resolve.return_value = resolved
                if not resolved.is_absolute() or resolved.parent == resolved:
                    with self.assertRaises(ValueError):
                        workspace_store.canonical_path(raw)
                else:
                    self.assertEqual(workspace_store.canonical_path(raw), resolved.as_posix())
        with self.assertRaises(ValueError):
            workspace_store.canonical_path("/")


@unittest.skipUnless(os.environ.get("TFI_TEST_POSTGRES_ROOT"), "Explicit disposable PostgreSQL runtime required")
class HistoryFreezeIntegration(unittest.TestCase):
    def test_complete_public_seed_imports_with_windows_and_posix_paths(self):
        """完整公开 SQL 在 Windows 中文路径及 POSIX 路径下均可事务导入。"""
        import psycopg
        from scripts import tfi_release_install as installer
        from scripts import tfi_release_platform_windows as windows

        seed = Path(os.environ.get("TFI_EXAMPLE_SEED", REPO / "instance/example_instance/seed"))
        manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))["engineering"]
        original = (seed / manifest["database"]["path"]).read_text(encoding="utf-8")
        for name, prefix in (("seed_windows", "C:/Users/安装 用户/TheFourthInstitute"),
                             ("seed_posix", "/opt/tfi")):
            with self.subTest(platform=name):
                self.connection.execute(f"CREATE DATABASE {name}")
                if name == "seed_windows":
                    root = PureWindowsPath(prefix)
                    roots = {key: str(root / "instance/example_instance/data" / key) for key in installer.KEYS}
                    with patch.object(installer, "Path", PureWindowsPath), \
                            patch.object(installer, "code_root", return_value=root / "code"), \
                            patch.object(installer, "platform_adapter", return_value=windows):
                        mappings = installer.directory_replacements(manifest, {"data_roots": roots})
                else:
                    mappings = {"${" + key + "}": prefix + "/data/" + key for key in installer.KEYS}
                    mappings["${code_root}"] = prefix + "/code"
                sql = self.root / (name + ".sql")
                sql.write_text(installer.map_directories(original, mappings), encoding="utf-8",
                               newline="\r\n" if name == "seed_windows" else "\n")
                result = subprocess.run(
                    [str(self.bin / "psql"), "-h", str(self.root), "-p", str(self.port),
                     "-U", self.connection.info.user, "-d", name, "-X", "--single-transaction",
                     "--set", "ON_ERROR_STOP=1", "--file", str(sql)],
                    env=self.env, capture_output=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                with psycopg.connect(host=str(self.root), port=self.port, dbname=name,
                                      user=self.connection.info.user) as connection:
                    total, mapped = connection.execute(
                        "SELECT count(*), count(*) FILTER (WHERE starts_with(path,%s)) "
                        "FROM workspace.workspaces", (prefix,)).fetchone()
                    self.assertGreater(total, 0)
                    self.assertEqual(total, mapped)
                    self.assertTrue(verify_frozen(connection)["passed"])

    def test_workspace_schema_migrates_and_checks_absolute_paths(self):
        """旧约束迁移保留工作区；支持盘符与 UNC，拒绝相对路径和各类根目录。"""
        import psycopg
        from management_portal.resources.workspace_store import WorkspaceStore

        connection = self.connection
        connection.execute("CREATE SCHEMA workspace_path_test")
        connection.execute("""CREATE TABLE workspace_path_test.workspaces (
            workspace_id text PRIMARY KEY, path text NOT NULL CHECK (left(path,1)='/' AND path<>'/'),
            title text NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            deleted_at timestamptz, deleted_by text)""")
        insert = ("INSERT INTO workspace_path_test.workspaces "
                  "(workspace_id,path,title,created_at,updated_at) VALUES (%s,%s,'test',now(),now())")
        connection.execute(insert, ("workspace_000000000000", "/existing/workspace"))
        store = WorkspaceStore.__new__(WorkspaceStore)
        store.database = SimpleNamespace(schema="workspace_path_test")
        store.table = '"workspace_path_test".workspaces'
        with connection.transaction():
            store._initialize(connection)
        valid = ["/srv/工作区", "C:/Users/安装 用户/workspace", r"C:\Users\用户\workspace",
                 "//server/share/workspace", r"\\server\share\workspace"]
        for index, path in enumerate(valid, 1):
            connection.execute(insert, (f"workspace_{index:012x}", path))
        for path in ("", "relative/path", "C:relative", "/", "C:/", "C:\\",
                     "//server/share", "//server/share/", "\\\\server\\share\\"):
            with self.subTest(path=path), self.assertRaises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(insert, ("workspace_ffffffffffff", path))
        statements = []
        def execute(sql, *args):
            statements.append(sql)
            return connection.execute(sql, *args)
        with connection.transaction():
            store._initialize(SimpleNamespace(execute=execute))
        self.assertFalse(any(sql.startswith("ALTER TABLE") for sql in statements))
        self.assertEqual(connection.execute("SELECT count(*) FROM workspace_path_test.workspaces").fetchone()[0],
                         len(valid) + 1)

    def test_windows_unicode_directory_mappings_roundtrip_through_postgres(self):
        """Windows 中文及空格路径在 SQL、JSON 数据导入中保持原值。"""
        from scripts import tfi_release_install as installer
        from scripts import tfi_release_platform_windows as windows

        root = PureWindowsPath("C:/Users/安装 用户/TheFourthInstitute")
        roots = {key: str(root / "instance/example_instance/data" / key) for key in installer.KEYS}
        tokens = {**roots, "code_root": str(root / "code")}
        manifest = {"directory_mappings": [
            {"root": key, "placeholder": "${" + key + "}", "path": "."} for key in tokens]}
        with patch.object(installer, "Path", PureWindowsPath), \
                patch.object(installer, "code_root", return_value=root / "code"), \
                patch.object(installer, "platform_adapter", return_value=windows):
            mapped = installer.directory_replacements(manifest, {"data_roots": roots})
        document = {key: "${" + key + "}/项目资料" for key in tokens}
        sql = installer.map_directories(
            "SELECT '" + json.dumps(document, ensure_ascii=False) + "'::jsonb", mapped)
        with self.connection.transaction():
            self.connection.execute("SET TRANSACTION READ ONLY")
            actual = self.connection.execute(sql).fetchone()[0]
        expected = {key: PureWindowsPath(path).as_posix() + "/项目资料" for key, path in tokens.items()}
        self.assertEqual(actual, expected)
        self.assertNotIn("\\", sql)

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.temporary = tempfile.TemporaryDirectory(prefix="tfi-win-freeze-")
        cls.root = Path(cls.temporary.name)
        native = Path(os.environ["TFI_TEST_POSTGRES_ROOT"])
        cls.bin = native / "bin"
        cls.env = {**os.environ, "LD_LIBRARY_PATH": str(native / "lib")}
        cls.data = cls.root / "pg"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        subprocess.run([str(cls.bin / "initdb"), "-D", str(cls.data), "-L", str(native / "share"),
                        "--auth=trust", "--no-locale", "--encoding=UTF8", "--no-sync"],
                       env=cls.env, check=True, capture_output=True)
        subprocess.run([str(cls.bin / "pg_ctl"), "-D", str(cls.data), "-l", str(cls.root / "pg.log"),
                        "-o", f"-F -k {cls.root} -p {cls.port} -h ''", "start", "-w"],
                       env=cls.env, check=True, capture_output=True)
        cls.connection = psycopg.connect(host=str(cls.root), port=cls.port, dbname="postgres", autocommit=True)
        cls.connection.execute("""
            CREATE SCHEMA lineage;
            CREATE TABLE lineage.resident_execution_work(id int);
            CREATE TABLE lineage.conversation_input_states(state text);
            CREATE TABLE lineage.conversation_states(state_json jsonb, activity_json jsonb);
            CREATE TABLE lineage.event_source_progress(source_id text, checkpoint_json jsonb);
            CREATE TABLE lineage.lineage_nodes(node_id text,node_type text);
            INSERT INTO lineage.conversation_input_states VALUES ('cancelled');
            INSERT INTO lineage.conversation_states VALUES
              ('{"working":false,"active_message_id":"","queue":{"items":[]}}','[]');
            INSERT INTO lineage.lineage_nodes VALUES ('source','event_source');
            INSERT INTO lineage.event_source_progress VALUES ('source','{"paused":true}');
        """)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        subprocess.run([str(cls.bin / "pg_ctl"), "-D", str(cls.data), "stop", "-m", "fast", "-w"],
                       env=cls.env, check=True, capture_output=True)
        cls.temporary.cleanup()

    def test_gate_reads_frozen_rows_and_detects_consumable_work_without_rewriting(self):
        """安装与实例回归：gate reads frozen rows and detects consumable work without rewriting。"""
        connection = self.connection
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            self.assertTrue(verify_frozen(connection)["passed"])
        connection.execute("INSERT INTO lineage.resident_execution_work VALUES (1)")
        connection.execute("UPDATE lineage.event_source_progress SET checkpoint_json='{}'")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            result = verify_frozen(connection)
        self.assertFalse(result["passed"])
        self.assertEqual(result["violations"]["lineage.resident_execution_work"], 1)
        self.assertEqual(result["violations"]["unpaused_event_sources"], 1)
        self.assertEqual(connection.execute("SELECT count(*) FROM lineage.resident_execution_work").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
