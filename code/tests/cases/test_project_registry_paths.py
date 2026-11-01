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

    def test_relative_paths_are_not_guessed(self):
        for value in ("Projects/Example", "Example", "~/Example"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                project_registry.runtime_path(value)

    def test_absolute_paths_are_not_rewritten(self):
        self.assertEqual(project_registry.runtime_path("/Projects/Example"), Path("/Projects/Example"))
        self.assertEqual(project_registry.runtime_path("/tmp/external"), Path("/tmp/external"))
        self.assertIsNone(project_registry.runtime_path(""))

    def test_provider_requires_registered_location(self):
        from management_portal.resources.providers import project
        with self.assertRaisesRegex(ValueError, "path missing"):
            project._resolved_project_root({"id": "Example"})
