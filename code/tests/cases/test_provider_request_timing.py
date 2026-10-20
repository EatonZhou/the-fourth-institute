"""Exercise Portal transport boundaries against a local server, with no real credentials."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import subprocess
import threading
import time

import pytest

from management_portal import model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry, AIProviderHTTPError


@pytest.mark.parametrize('protocol,endpoint', [('openai_compatible_chat', 'chat/completions'), ('typesafe_system_one', 'decide')])
@pytest.mark.parametrize('status', [200, 403])
@pytest.mark.parametrize('observer_fails', [False, True])
def test_direct_transport_records_dispatch_before_server_receives_request(tmp_path, protocol, endpoint, status, observer_fails):
    """直连模型 API 在服务端收到请求前记下发出时间，观察回调失败也不影响请求"""
    received = []
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(datetime.now(timezone.utc))
            requests.append((self.path, self.headers.get('Authorization'),
                             json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            time.sleep(0.03)
            body = json.dumps({'choices': [{'message': {'content': 'answer'}}]}).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        secret = tmp_path / 'test-key'
        secret.write_text('test-only-not-a-credential')
        model_api_store.create_api({'id': 'timing', 'enabled': True, 'provider': 'openai',
            'kind': protocol, 'base_url': f'http://127.0.0.1:{server.server_port}',
            'api_key_file': str(secret)}, 'test')
        observations = []
        def observe(value):
            observations.append(value)
            if observer_fails:
                raise RuntimeError('optional observation unavailable')
        def request():
            return AIProviderRegistry().request('timing', endpoint=endpoint, payload={'model': 'test'},
                                                timeout_seconds=5, observe_transport=observe)
        if status == 403:
            with pytest.raises(AIProviderHTTPError, match='HTTP 403'):
                request()
        else:
            assert request()['choices'][0]['message']['content'] == 'answer'
        assert requests == [('/' + endpoint, 'Bearer test-only-not-a-credential', {'model': 'test'})]
        timing = observations[0]
        start, finish = (datetime.fromisoformat(timing[key]) for key in ('started_at','finished_at'))
        assert start <= received[0] < finish <= datetime.now(timezone.utc)
        assert (finish - start).total_seconds() >= 0.03
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required by the DSH runtime')
def test_dsh_transport_intercepts_actual_fetch_and_separates_child_requests():
    """dsh 拦截实际 fetch 按会话区分子请求记发出时间，压缩与无关请求不计入"""
    fixture = Path(__file__).parent / 'fixtures/dsh_request_timing.mjs'
    subprocess.run(['node', str(fixture)], check=True, capture_output=True, text=True, timeout=20)
