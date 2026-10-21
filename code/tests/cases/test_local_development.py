"""共享 Dev 工作树、按文件快速发布与 Release 推送。

:module: operations.deployment
:design: decision_08bb2d9141f9
:profiles: local.fixture
:risk: no-external-side-effects
"""
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
        """Release 快进到 Dev 的普通提交，并可继续发布下一轮。"""
        self.change()
        first = dev.publish(self.root, "public fix", ["python3", "-c", "pass"], push=False)
        self.assertEqual((self.root / "file").read_text(), "changed\n")
        self.assertFalse(first["pushed"])
        self.assertIn("private experiment", dev.git(self.root, "log", "--format=%s"))
        (self.work / "second").write_text("next\n")
        dev.git(self.work, "add", "second")
        dev.git(self.work, "commit", "-m", "another private change")
        dev.publish(self.root, "next fix", ["python3", "-c", "pass"], push=False)
        self.assertEqual(dev.git(self.root, "rev-parse", "HEAD^{tree}"),
                         dev.git(self.work, "rev-parse", "HEAD^{tree}"))

    def test_dirty_main_is_preserved(self):
        """主分支的他人在途改动使发布停止且字节保持不变。"""
        self.change()
        (self.root / "file").write_text("someone else's work\n")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.publish(self.root, "fix", ["python3", "-c", "pass"], push=False)
        self.assertEqual((self.root / "file").read_text(), "someone else's work\n")

    def test_failed_check_never_changes_main(self):
        """验证命令失败时不更改主分支提交。"""
        self.change()
        head = dev.git(self.root, "rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.publish(self.root, "fix", ["python3", "-c", "raise SystemExit(2)"])
        self.assertEqual(dev.git(self.root, "rev-parse", "HEAD"), head)

    def test_selected_file_can_include_other_work_and_leave_unrelated_dirty(self):
        """发布同文件全部修改及暂存内容，不等待其他文件完成。"""
        (self.work / "file").write_text("my fix\nother resident draft\n")
        (self.work / "unrelated").write_text("still editing\n")
        (self.work / "staged").write_text("already staged\n")
        dev.git(self.work, "add", "staged")
        result = dev.publish(self.root, "selected fix", push=False, paths=("file",))
        self.assertEqual((self.root / "file").read_text(), "my fix\nother resident draft\n")
        self.assertTrue((self.root / "staged").exists())
        self.assertFalse((self.root / "unrelated").exists())
        self.assertEqual((self.work / "unrelated").read_text(), "still editing\n")
        self.assertEqual(result["commit"], dev.git(self.work, "rev-parse", "HEAD"))

    def test_unrelated_release_work_is_not_a_clean_tree_gate(self):
        self.change()
        (self.root / "local-note").write_text("keep me")
        dev.publish(self.root, "fix", push=False)
        self.assertEqual((self.root / "local-note").read_text(), "keep me")

    def test_release_divergence_does_not_overwrite_either_tree(self):
        self.change()
        (self.root / "foreign").write_text("release-only")
        dev.git(self.root, "add", "foreign")
        dev.git(self.root, "commit", "-m", "unexpected release edit")
        old = dev.git(self.root, "rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.publish(self.root, "fix", push=False)
        self.assertEqual(dev.git(self.root, "rev-parse", "HEAD"), old)
        self.assertEqual((self.work / "file").read_text(), "changed\n")

    def test_dev_push_blocked_main_allowed(self):
        """本地远端只接收 main，拒绝推送 dev。"""
        remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        dev.git(self.root, "config", "remote.origin.url", str(remote))
        dev.git(self.root, "push", "origin", "main:main")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.git(self.work, "push", "origin", "dev:dev")
        self.change()
        dev.git(self.root, "merge", "--ff-only", "dev")
        with self.assertRaises(subprocess.CalledProcessError):
            dev.git(self.work, "push", "origin", "main:main")
        self.assertEqual(dev.git(self.root, "ls-remote", "--heads", "origin", "dev"), "")


if __name__ == "__main__":
    unittest.main()
