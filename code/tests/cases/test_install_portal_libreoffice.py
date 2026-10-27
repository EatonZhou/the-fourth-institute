"""Private presentation runtime remains usable after an instance directory move.

:design: decision_61e50306cc79
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import install_portal_libreoffice as installer


class LibreOfficeInstallationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="portal-presentation-install-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "original instance"
        self.program = self.root / "usr/lib/libreoffice/program"
        self.program.mkdir(parents=True)
        (self.root / "usr/lib/x86_64-linux-gnu").mkdir()
        registry = self.root / "usr/lib/libreoffice/share/.registry"
        registry.mkdir(parents=True)
        (registry / "main.xcd").write_text("registry fixture")
        (self.program / "fundamentalrc").write_text(
            "[Bootstrap]\nBRAND_BASE_DIR=file:///usr/lib/libreoffice\n"
            "CONFIGURATION_LAYERS=xcsxcu:file:///etc/libreoffice/registry "
            "res:file:///etc/libreoffice/registry\n"
            "URE_MORE_JAVA_CLASSPATH_URLS=file:///usr/share/java/fixture.jar\n"
        )
        executable = self.program / "soffice"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$@"\n')
        executable.chmod(0o755)

    def install(self):
        run = subprocess.run

        def transport(command, **kwargs):
            if command[0] in {"apt-get", "dpkg-deb"}:
                return subprocess.CompletedProcess(command, 0)
            return run(command, capture_output=True, **kwargs)

        with patch.object(sys, "argv", ["install", "--destination", str(self.root)]), \
                patch.object(installer.subprocess, "check_output", return_value="'https://apt.test/pkg' fixture_1_all.deb 1\n"), \
                patch.object(installer.subprocess, "run", side_effect=transport):
            installer.main()

    def assert_relocated(self, root):
        result = subprocess.run(
            [str(root / "soffice"), "--headless", "a file.pptx"],
            env={**os.environ, "LD_LIBRARY_PATH": "/inherited-libraries"},
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.splitlines(), [
            f"{root}/usr/lib/libreoffice/program:{root}/usr/lib:{root}/usr/lib/x86_64-linux-gnu:/inherited-libraries",
            "--headless", "a file.pptx",
        ])
        bootstrap = (root / "usr/lib/libreoffice/program/fundamentalrc").read_text()
        self.assertIn("BRAND_BASE_DIR=${ORIGIN}/..\n", bootstrap)
        self.assertIn("xcsxcu:${ORIGIN}/../../../../etc/libreoffice/registry", bootstrap)
        self.assertIn("res:${ORIGIN}/../../../../etc/libreoffice/registry", bootstrap)
        self.assertIn("${ORIGIN}/../../../share/java/fixture.jar", bootstrap)
        self.assertNotIn("file:///", bootstrap)
        self.assertEqual((root / "etc/libreoffice/registry/main.xcd").read_text(), "registry fixture")

    def test_installed_converter_survives_instance_move(self):
        self.install()
        moved = self.root.with_name("迁移后的 instance")
        shutil.move(self.root, moved)
        self.assert_relocated(moved)

    def test_repair_migrated_absolute_paths_without_downloading(self):
        bootstrap = self.program / "fundamentalrc"
        bootstrap.write_text(bootstrap.read_text().replace("file:///", "file:///previous/instance/"))
        (self.root / "soffice").write_text('#!/bin/sh\nexec /previous/instance/soffice "$@"\n')
        with patch.object(sys, "argv", ["install", "--repair", "--destination", str(self.root)]), \
                patch.object(installer.subprocess, "check_output", side_effect=AssertionError("repair must not download")):
            installer.main()
        self.assert_relocated(self.root)
        # Reconfiguration is safe to repeat and still permits another move.
        installer.configure_runtime(self.root)
        moved = self.root.with_name("second move")
        shutil.move(self.root, moved)
        self.assert_relocated(moved)


if __name__ == "__main__":
    unittest.main()
