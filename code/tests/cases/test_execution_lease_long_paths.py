"""Native lease admission must work below long Windows instance directories."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from management_common import lifetime_execution_lease as lease
from management_common.filesystem_paths import filesystem_path, logical_path


class LongLeasePaths(unittest.TestCase):
    def test_long_registry_preserves_conflicts_pins_and_cleanup(self):
        with tempfile.TemporaryDirectory(dir=filesystem_path(tempfile.gettempdir())) as temporary:
            root = logical_path(temporary) / ('instance-' + 'x' * 90) / 'runtime'
            with patch.object(lease, 'instance_runtime_root', return_value=root):
                with lease.execution_lease('resource', namespace='long-path') as acquired:
                    self.assertTrue(acquired)
                    with lease.execution_lease('resource', namespace='long-path', blocking=False) as competing:
                        self.assertFalse(competing)
                    with lease.inherit_execution_leases() as (_, record, paths):
                        self.assertGreater(len(str(paths[0])), 260)
                        self.assertTrue(paths[0].exists())
                        record(None)
                    with lease.execution_lease('different', namespace='long-path', blocking=False) as independent:
                        self.assertTrue(independent)
                with lease.execution_lease('resource', namespace='long-path', blocking=False) as next_holder:
                    self.assertTrue(next_holder)
                self.assertEqual(list(filesystem_path(root).rglob('*.lease')), [])


if __name__ == '__main__':
    unittest.main()
