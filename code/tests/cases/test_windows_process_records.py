"""Windows supervision record concurrency.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import tfi_windows_user_services as services


class RecordRetries(unittest.TestCase):
    def error(self, code):
        error = PermissionError(13, "busy")
        error.winerror = code
        return error

    def test_transient_sharing_and_delete_pending_are_retried(self):
        operation = Mock(side_effect=[self.error(32), self.error(5), self.error(33), "complete"])
        with patch.object(services.time, "sleep") as sleep:
            self.assertEqual(services._record_operation(Path("nats.process.json"), operation), "complete")
        self.assertEqual(sleep.call_count, 3)

    def test_persistent_denial_is_bounded_and_never_starts_a_duplicate(self):
        controller = services.WindowsServiceControl(Path("unused"))
        with patch.object(controller, "definition", return_value={"root": "fixture", "component": "nats"}), \
                patch.object(services, "_open_record", side_effect=self.error(5)), \
                patch.object(services.time, "monotonic", side_effect=[0, 0.1, 2.1]), \
                patch.object(services.time, "sleep"), patch.object(services.subprocess, "Popen") as spawn:
            with self.assertRaises(PermissionError) as caught:
                controller.start("tfi-fixture-nats")
        self.assertIn("nats.process.json", caught.exception.filename)
        spawn.assert_not_called()

    def test_unrelated_errors_are_not_retried_or_reported_as_absent(self):
        for error in (PermissionError(13, "ACL"), self.error(1314), ValueError("bad record")):
            with self.subTest(error=error), patch.object(services.time, "sleep") as sleep:
                with self.assertRaises(type(error)):
                    services._record_operation(Path("record"), Mock(side_effect=error))
                sleep.assert_not_called()

    def test_recovered_status_preserves_live_supervisor_identity(self):
        controller = services.WindowsServiceControl(Path("unused"))
        record = json.dumps({"pid": 15, "created": 10, "child_pid": 16, "child_created": 11})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nats.process.json"
            path.write_text(record)
            original = services._open_record
            calls = 0
            def open_record(path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise self.error(32)
                return original(path)
            with patch.object(controller, "definition", return_value={}), \
                    patch.object(services, "state_path", return_value=path), \
                    patch.object(services, "_open_record", side_effect=open_record), \
                    patch.object(services.time, "sleep"), \
                    patch.object(services, "process_stamp", side_effect=lambda pid: {15: 10, 16: 11}[pid]):
                self.assertEqual(controller.status("tfi-fixture-nats")["active"], "active")

    def test_failed_replace_keeps_old_identity_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nats.process.json"
            services.write_record(path, "old")
            with patch.object(Path, "replace", side_effect=self.error(5)), \
                    patch.object(services.time, "monotonic", side_effect=[0, 3, 4]):
                with self.assertRaises(PermissionError):
                    services.write_record(path, "new")
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(list(Path(tmp).iterdir()), [path])


@unittest.skipUnless(os.name == "nt", "requires real Windows file sharing")
class NativeRecordSharing(unittest.TestCase):
    def test_reader_allows_atomic_replacement_and_unlink(self):
        with tempfile.TemporaryDirectory(prefix="TFI 中文 ") as tmp:
            path = Path(tmp) / "nats.process.json"
            services.write_record(path, "old")
            # Reproduce the old CRT reader's conflict, then verify the new reader.
            with path.open(encoding="utf-8"), self.assertRaises(PermissionError):
                path.unlink()
            with services._open_record(path) as reader:
                services.write_record(path, "new")
                self.assertEqual(reader.read(), "old")
            with services._open_record(path) as reader:
                services.remove_record(path)
                self.assertEqual(reader.read(), "new")

    def test_temporary_exclusive_handle_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nats.process.json"
            services.write_record(path, "complete")
            api = services._record_api()
            handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            release = threading.Timer(0.15, lambda: api.CloseHandle(handle))
            release.start()
            try:
                self.assertEqual(services.read_record(path), "complete")
            finally:
                release.join()

    def test_concurrent_process_publication_polling_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="TFI 中文 ") as tmp:
            path = Path(tmp) / "nats.process.json"
            writer = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts import tfi_windows_user_services as s
p = Path(sys.argv[2])
for i in range(150):
    s.write_record(p, json.dumps({'pid': 123, 'created': i, 'text': '案例' * 100}))
    time.sleep(.001)
    if i % 5 == 0:
        s.remove_record(p)
s.write_record(p, json.dumps({'done': True}))
"""
            with subprocess.Popen([sys.executable, "-c", writer, str(Path(services.__file__).parents[1]), str(path)]) as child:
                def poll():
                    reads = 0
                    while child.poll() is None:
                        try:
                            value = json.loads(services.read_record(path))
                            self.assertTrue(value.get("done") or value["pid"] == 123)
                            reads += 1
                        except FileNotFoundError:
                            pass
                        time.sleep(.001)
                    return reads
                with ThreadPoolExecutor(max_workers=3) as pool:
                    counts = list(pool.map(lambda _: poll(), range(3)))
                self.assertEqual(child.wait(timeout=10), 0)
            self.assertGreater(sum(counts), 0)
            self.assertEqual(json.loads(services.read_record(path)), {"done": True})


if __name__ == "__main__":
    unittest.main()
