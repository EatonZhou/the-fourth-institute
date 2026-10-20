import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "tfi_dev", Path(__file__).resolve().parents[2] / "scripts/tfi_dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "main"
        self.root.mkdir()
        dev.git(self.root, "init", "-b", "main")
        dev.git(self.root, "config", "user.email", "test@local.invalid")
        dev.git(self.root, "config", "user.name", "Test")
        (self.root / "file").write_text("base\n")
        dev.git(self.root, "add", "file")
        dev.git(self.root, "commit", "-m", "base")
        self.cfg = dev.init(self.root)
        self.work = Path(self.cfg["dev"])

    def change(self):
        (self.work / "file").write_text("changed\n")
        dev.git(self.work, "add", "file")
        dev.git(self.work, "commit", "-m", "private experiment")

    def test_publish_and_second_round(self):
        self.change()
        first = dev.publish(self.root, "public fix", ["python3", "-c", "pass"])
        self.assertEqual((self.root / "file").read_text(), "changed\n")
        self.assertFalse(first["pushed"])
        self.assertNotIn("private experiment", dev.git(self.root, "log", "--format=%s"))
        (self.work / "second").write_text("next\n")
        dev.git(self.work, "add", "second")
        dev.git(self.work, "commit", "-m", "another private change")
        dev.publish(self.root, "next fix", ["python3", "-c", "pass"])
        self.assertEqual(dev.git(self.root, "rev-parse", "HEAD^{tree}"),
                         dev.git(self.work, "rev-parse", "HEAD^{tree}"))

    def test_dirty_main_is_preserved(self):
        self.change()
        (self.root / "file").write_text("someone else's work\n")
        with self.assertRaises(ValueError):
            dev.publish(self.root, "fix", ["python3", "-c", "pass"])
        self.assertEqual((self.root / "file").read_text(), "someone else's work\n")

    def test_failed_check_never_changes_main(self):
        self.change()
        head = dev.git(self.root, "rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.publish(self.root, "fix", ["python3", "-c", "raise SystemExit(2)"])
        self.assertEqual(dev.git(self.root, "rev-parse", "HEAD"), head)

    def test_dev_push_blocked_main_allowed(self):
        remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        dev.git(self.root, "config", "remote.origin.url", str(remote))
        dev.git(self.root, "push", "origin", "main:main")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.git(self.work, "push", "origin", "dev:dev")
        self.assertEqual(dev.git(self.root, "ls-remote", "--heads", "origin", "dev"), "")


if __name__ == "__main__":
    unittest.main()
