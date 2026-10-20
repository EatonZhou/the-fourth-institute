"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "code"))
from management_portal.distribution.history import verify_frozen


@unittest.skipUnless(os.environ.get("TFI_TEST_POSTGRES_ROOT"), "Explicit disposable PostgreSQL runtime required")
class HistoryFreezeIntegration(unittest.TestCase):
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
