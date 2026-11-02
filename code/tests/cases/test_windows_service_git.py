"""A clean Windows service gets its own verified Git, without a global install."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from management_portal import service_runtime


class ServiceGitTests(unittest.TestCase):
    def test_selected_code_git_is_in_service_search_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / 'selected/code'
            binary = root / 'selected/dependencies/native/git/cmd/git.exe'
            instance = SimpleNamespace(
                root=root, directory=lambda name: root / name,
                service_environment=lambda component: {},
                service_code=lambda component: code, code_root=lambda: code,
                internal_web_origin=lambda: 'http://127.0.0.1:1',
                document={'network': {'nats_port': 14300}})
            def native(selected, package):
                self.assertEqual(selected, code)
                return binary if package == 'git' else root / 'node/node.exe'
            windows = SimpleNamespace(name='nt', environ={'PATH': 'system-only'}, pathsep=';')
            with patch.object(service_runtime, 'os', windows), \
                    patch.object(service_runtime.code_environment, 'native_binary', side_effect=native), \
                    patch('management_common.portal_network.saved_proxy_environment', return_value={}):
                env = service_runtime.clean_environment(instance, component='resource')
            self.assertEqual(env['PATH'].split(';'), [str(binary.parent), str(root / 'node'), 'system-only'])
            self.assertEqual(windows.environ['PATH'], 'system-only')


if __name__ == '__main__':
    unittest.main()
