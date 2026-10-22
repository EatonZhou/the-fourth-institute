"""Desktop readiness must reach the local instance and retain launchd errors.

:module: operations.deployment
:profiles: local.fixture
:risk: no-external-side-effects
"""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from scripts import tfi_release_install as installer


class DesktopHealthTransport(unittest.TestCase):
    def state(self, root, port):
        (root / 'runtime').mkdir()
        (root / 'runtime/worker-health.json').write_text(json.dumps({
            'ok': True, 'instance': 'tfi-fixture', 'updated_at': installer.time.time()}))
        return {'target': {'install_root': str(root), 'service_prefix': 'tfi-fixture',
                'run_id': 'fixture', 'supervisor_resident_id': '', 'ports': {'web': port},
                'bind': '127.0.0.1', 'python': sys.executable}}

    def controller(self):
        controller = Mock()
        controller.status.side_effect = lambda name: {'service': name, 'active': 'active'}
        return controller

    def test_local_readiness_ignores_environment_and_system_proxy(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'instance': 'tfi-fixture',
                    'execution_layer': {'ok': True}, 'society_service': {'ok': True}}).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
                state = self.state(Path(temporary), server.server_port)
                stack.enter_context(patch.dict(os.environ, {
                    'http_proxy': 'http://127.0.0.1:1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                    'no_proxy': '', 'NO_PROXY': ''}))
                stack.enter_context(patch('urllib.request.getproxies', return_value={'http': 'http://127.0.0.1:1'}))
                stack.enter_context(patch('urllib.request.proxy_bypass', return_value=False))
                stack.enter_context(patch.object(installer, 'service_controller', return_value=self.controller()))
                result = installer.observed_status(state)
            self.assertEqual(result['status'], 'installed')
            self.assertEqual(calls, ['/api/health', '/login'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_non_json_503_and_separate_launchd_stderr_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.state(root, 28000)
            (root / 'runtime/web.log').write_text('')
            (root / 'runtime/web.error.log').write_text(
                'old-marker\n' + 'old output\n' * 2000 + 'RuntimeError: web startup fixture\n')
            errors = [HTTPError('http://127.0.0.1/' + route, 503, 'Unavailable', {},
                                io.BytesIO(b'<html>Unavailable</html>')) for route in ('api/health', 'login')]
            with patch.object(installer, 'service_controller', return_value=self.controller()), \
                    patch.object(installer._LOCAL_HTTP, 'open', side_effect=errors):
                result = installer.observed_status(state)
            self.assertEqual(result['checks'][0]['error'], 'HTTP 503')
            error = installer.health_failure(state, result)
            self.assertIn('web.error.log', error)
            self.assertIn('RuntimeError: web startup fixture', error)
            self.assertNotIn('old-marker', error)
            saved = json.loads((root / 'runtime/startup-diagnostics.json').read_text())
            self.assertIn('web startup fixture', saved['component_error_log_tails']['web'])


if __name__ == '__main__':
    unittest.main()
