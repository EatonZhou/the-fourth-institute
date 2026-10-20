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

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "code"))
from management_portal.distribution.history import verify_frozen


@unittest.skipUnless(os.environ.get("TFI_TEST_POSTGRES_ROOT"), "Explicit disposable PostgreSQL runtime required")
class HistoryFreezeIntegration(unittest.TestCase):
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
                        "--auth=trust", "--no-locale", "--encoding=UTF8"], env=cls.env, check=True, capture_output=True)
        subprocess.run([str(cls.bin / "pg_ctl"), "-D", str(cls.data), "-l", str(cls.root / "pg.log"),
                        "-o", f"-k {cls.root} -p {cls.port} -h ''", "start", "-w"],
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
