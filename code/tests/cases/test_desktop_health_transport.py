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
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from management_common.instance_manifest import load
from scripts import instance_services, tfi_release_install as installer
from tests.cases.instance_test_support import write_instance


class DesktopHealthTransport(unittest.TestCase):
    def instance(self, root, port):
        manifest = write_instance(root / 'instance') / 'instance.json'
        document = json.loads(manifest.read_text())
        document['deployments']['release']['web_port'] = port
        document['network']['entry_port'] = port
        manifest.write_text(json.dumps(document))
        instance = load(manifest.parent)
        (instance.root / 'runtime').mkdir()
        (instance.root / 'runtime/worker-health.json').write_text(json.dumps({
            'ok': True, 'instance': instance.name, 'updated_at': installer.time.time()}))
        return instance

    def services(self, instance, stopped=()):
        return patch.object(instance_services, 'status', return_value={
            component: {'service': component, 'active': 'inactive' if component in stopped else 'active', 'pid': 0}
            for component in instance_services.components(instance)})

    def test_local_readiness_ignores_environment_and_system_proxy(self):
        calls = []
        names = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'instance': names[0], 'deployment': 'release',
                    'execution_layer': {'ok': True}, 'society_service': {'ok': True}}).encode())

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
                instance = self.instance(Path(temporary), server.server_port)
                names.append(instance.name)
                stack.enter_context(patch.dict(os.environ, {
                    'http_proxy': 'http://127.0.0.1:1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                    'no_proxy': '', 'NO_PROXY': ''}))
                stack.enter_context(patch('urllib.request.getproxies', return_value={'http': 'http://127.0.0.1:1'}))
                stack.enter_context(patch('urllib.request.proxy_bypass', return_value=False))
                stack.enter_context(self.services(instance))
                result = installer.observed_status(instance)
            self.assertEqual(result['status'], 'running')
            self.assertEqual(calls, ['/api/health', '/login'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_non_json_503_and_separate_launchd_stderr_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            instance = self.instance(Path(temporary), 28000)
            (instance.root / 'runtime/web.log').write_text('')
            (instance.root / 'runtime/web.error.log').write_text(
                'old-marker\n' + 'old output\n' * 2000 + 'RuntimeError: web startup fixture\n')
            errors = [HTTPError('http://127.0.0.1/' + route, 503, 'Unavailable', {},
                                io.BytesIO(b'<html>Unavailable</html>')) for route in ('api/health', 'login')]
            with self.services(instance), patch.object(installer._LOCAL_HTTP, 'open', side_effect=errors):
                result = installer.observed_status(instance)
            self.assertFalse(result['checks'][0]['ok'])
            self.assertEqual(result['checks'][0]['http_status'], 503)
            error = installer.health_failure(instance, result)
            self.assertIn('web.error.log', error)
            self.assertIn('RuntimeError: web startup fixture', error)
            self.assertNotIn('old-marker', error)

    def test_stopped_web_skips_connection_wait_without_claiming_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            instance = self.instance(Path(temporary), 28000)
            with self.services(instance, stopped={'web'}), patch.object(installer._LOCAL_HTTP, 'open') as http:
                result = installer.observed_status(instance)
            http.assert_not_called()
            self.assertEqual(result['status'], 'unhealthy')
            self.assertTrue(all(not row['ok'] and 'not running' in row['error']
                                for row in result['checks'] if row.get('route')))


if __name__ == '__main__':
    unittest.main()
