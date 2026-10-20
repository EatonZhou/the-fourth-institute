"""Registry paths are concrete paths, not environment placeholders."""
from pathlib import Path
import unittest
from unittest.mock import patch

from management_portal import project_registry


class ProjectRegistryPathsTest(unittest.TestCase):
    def test_runtime_path_rejects_unexpanded_variables(self):
        for value in (
            "{$CO_MINDDB_PATH}/Projects/Example",
            "${CO_MINDDB_PATH}/Projects/Example",
            "${PORTAL_SHARE_ROOT}/Projects/Example",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "变量占位符"):
                project_registry.runtime_path(value)

    def test_concrete_and_share_relative_paths_keep_their_meaning(self):
        with patch.object(project_registry, "SHARE_ROOT", Path("/tmp/registry-share")):
            for value in ("/Projects/Example", "Projects/Example", "/Codes/Example"):
                with self.subTest(value=value):
                    self.assertEqual(
                        project_registry.runtime_path(value),
                        (Path("/tmp/registry-share") / value.lstrip("/")).resolve(),
                    )
            self.assertEqual(project_registry.runtime_path("/tmp/external"), Path("/tmp/external").resolve())
            self.assertIsNone(project_registry.runtime_path(""))
