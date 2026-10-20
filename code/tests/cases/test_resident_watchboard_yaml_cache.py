""":module: workbench.panels"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from management_portal.society_service import resident_watchboard_domain


class ResidentWatchboardYamlCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "agent.yaml"
        with resident_watchboard_domain._WATCHBOARD_YAML_CACHE_LOCK:
            resident_watchboard_domain._WATCHBOARD_YAML_CACHE.clear()

    def tearDown(self) -> None:
        with resident_watchboard_domain._WATCHBOARD_YAML_CACHE_LOCK:
            resident_watchboard_domain._WATCHBOARD_YAML_CACHE.clear()
        self.temp_dir.cleanup()

    def test_unchanged_yaml_is_parsed_once(self) -> None:
        """未变化的 Watchboard YAML 只解析一次"""
        self.path.write_text("name: first\n", encoding="utf-8")

        with mock.patch.object(resident_watchboard_domain.yaml, "safe_load", wraps=resident_watchboard_domain.yaml.safe_load) as safe_load:
            first, first_error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)
            second, second_error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)

        self.assertEqual(first, {"name": "first"})
        self.assertEqual(second, first)
        self.assertEqual(first_error, "")
        self.assertEqual(second_error, "")
        self.assertEqual(safe_load.call_count, 1)

    def test_file_change_invalidates_cached_yaml(self) -> None:
        """Watchboard YAML 文件变化后缓存失效，读到新内容"""
        self.path.write_text("name: first\n", encoding="utf-8")
        resident_watchboard_domain._read_watchboard_yaml_cached(self.path)
        self.path.write_text("name: updated-value\n", encoding="utf-8")

        payload, error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)

        self.assertEqual(payload, {"name": "updated-value"})
        self.assertEqual(error, "")

    def test_parse_error_is_cached_until_file_changes(self) -> None:
        """Watchboard YAML 的解析错误被缓存，文件修复后读到新内容"""
        self.path.write_text("name: [\n", encoding="utf-8")

        with mock.patch.object(resident_watchboard_domain.yaml, "safe_load", wraps=resident_watchboard_domain.yaml.safe_load) as safe_load:
            first, first_error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)
            second, second_error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)
            self.assertEqual(safe_load.call_count, 1)

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertTrue(first_error)
        self.assertEqual(second_error, first_error)

        self.path.write_text("name: repaired\n", encoding="utf-8")
        repaired, repaired_error = resident_watchboard_domain._read_watchboard_yaml_cached(self.path)
        self.assertEqual(repaired, {"name": "repaired"})
        self.assertEqual(repaired_error, "")


if __name__ == "__main__":
    unittest.main()
