import tempfile
from pathlib import Path
import unittest

from management_common.code_environment import environment_directory, environment_key, runtime_python


class CodeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.code = Path(self.temporary.name) / "source/code"
        (self.code / "scripts").mkdir(parents=True)
        (self.code / "scripts/bootstrap_portal").write_text("bootstrap")
        (self.code / "requirements.txt").write_text("example==1\n")

    def test_dependency_change_selects_a_new_environment(self):
        before = environment_key(self.code)
        (self.code / "business.py").write_text("business code")
        self.assertEqual(before, environment_key(self.code))
        (self.code / "requirements.txt").write_text("example==2\n")
        self.assertNotEqual(before, environment_key(self.code))

    def test_missing_exact_environment_fails_without_using_system_python(self):
        with self.assertRaisesRegex(FileNotFoundError, "Prepare the code dependency"):
            runtime_python(self.code)
        root = environment_directory(self.code)
        executable = root / "bin/python"
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.assertEqual(runtime_python(self.code), executable)
        (self.code / "requirements.txt").write_text("example==3\n")
        with self.assertRaises(FileNotFoundError):
            runtime_python(self.code)


if __name__ == "__main__":
    unittest.main()
