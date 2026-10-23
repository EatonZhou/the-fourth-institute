"""Disposable Windows CLI contract tests with real programs and loopback model fixtures.

Runtime downloads are allowed during preparation. No real AI account or model is
used. Run as the same ordinary user as the installed EXE smoke, against its code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


class ModelFixture:
    model = 'deepseek-v4-flash'
    reply = 'WINDOWS_NATIVE_CLI_OK'

    def __init__(self):
        self.calls = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_json({'data': [{'id': owner.model, 'object': 'model'}]})

            def send_json(self, value):
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
                owner.calls.append({'path': self.path.split('?')[0], 'model': body.get('model')})
                usage = {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}
                if self.path.split('?')[0].endswith('/count_tokens'):
                    self.send_json({'input_tokens': 10})
                    return
                anthropic = '/messages' in self.path
                if not body.get('stream'):
                    self.send_json({'id': 'fixture', 'object': 'chat.completion', 'created': 0,
                        'model': owner.model, 'choices': [{'index': 0, 'finish_reason': 'stop',
                            'message': {'role': 'assistant', 'content': owner.reply}}], 'usage': usage})
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                if anthropic:
                    events = [
                        ('message_start', {'message': {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                            'model': owner.model, 'content': [], 'stop_reason': None, 'stop_sequence': None,
                            'usage': {'input_tokens': 10, 'output_tokens': 0}}}),
                        ('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                        ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': owner.reply}}),
                        ('content_block_stop', {'index': 0}),
                        ('message_delta', {'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                            'usage': {'output_tokens': 2}}), ('message_stop', {})]
                    for kind, payload in events:
                        self.wfile.write(f'event: {kind}\ndata: {json.dumps({"type": kind, **payload})}\n\n'.encode())
                else:
                    for delta, finish in [({'role': 'assistant', 'content': owner.reply}, None), ({}, 'stop')]:
                        chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0,
                            'model': owner.model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                            **({'usage': usage} if finish else {})}
                        self.wfile.write(f'data: {json.dumps(chunk)}\n\n'.encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
                self.close_connection = True
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def codex_probe(binary, root):
    from management_common.process_runtime import spawn_process, terminate_process_tree
    home = root / 'codex-home'
    home.mkdir()
    process = spawn_process([str(binary), 'app-server'], env={**os.environ, 'CODEX_HOME': str(home)},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8')
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    def request(identifier, method, params):
        process.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            value = json.loads(lines.get(timeout=max(.1, deadline - time.monotonic())))
            if value.get('id') == identifier:
                assert 'error' not in value, value
                return value['result']
        raise TimeoutError(method)
    try:
        request(1, 'initialize', {'clientInfo': {'name': 'portal_native_test', 'version': '1'}})
        result = request(2, 'model/list', {})
        assert isinstance(result.get('data'), list), 'model/list contract missing'
        return {'initialize': 'passed', 'model_list': 'passed', 'authenticated_inference': 'not_tested'}
    finally:
        terminate_process_tree(process, force=True)


def opencode_probe(binary, root, version):
    from management_portal import opencode_client as client, opencode_provider_catalog as catalog
    fixture = ModelFixture()
    route = 'native-opencode'
    entry = {'id': 'native', 'kind': 'openai_compatible_chat', 'enabled': True, 'provider': 'deepseek',
             'base_url': fixture.url + '/v1', 'default_model': fixture.model, 'models': [{'id': fixture.model}]}
    runner = client.OpenCodeClient()
    work = root / '工作目录 空格'
    work.mkdir()
    try:
        with patch.object(catalog, 'binary', return_value=binary), patch.object(catalog, '_custom_entry', return_value=entry), \
                patch.object(catalog, 'api_key', return_value='disposable-fixture'), \
                patch.object(catalog, 'config_home', return_value=root / 'opencode-config'), \
                patch.object(catalog, 'version', return_value=version), \
                patch.object(client, 'STATE', root / 'opencode-state'), patch.object(client, 'SOCKETS', root / 'sockets'):
            result = runner.run_turn(session_key='native', session_id='', prompt='Say hello.', cwd=str(work),
                provider_profile=route, model=fixture.model, effort='', is_owner=True, permission_profile='owner',
                system_prompt_append='', turn_id='windows-native-cli', tools=[], resident_environment={},
                inherit_portal_database=False, allow_subagents=False, event_callback=lambda event: None, tool_callback=None)
            try:
                assert fixture.reply in result.response, result.response
                assert fixture.calls and {x['model'] for x in fixture.calls} == {fixture.model}, fixture.calls
                assert {x['path'] for x in fixture.calls} == {'/v1/chat/completions'}, fixture.calls
            finally:
                runner.close_session('', result.session_id)
        return {'bridge_ipc': 'passed', 'chat_completions': 'passed', 'model': fixture.model}
    finally:
        fixture.close()


def claude_probe(binary, root):
    from management_common.process_runtime import run_process
    fixture = ModelFixture()
    env = {**os.environ, 'CLAUDE_CONFIG_DIR': str(root / 'claude-config'),
           'ANTHROPIC_BASE_URL': fixture.url, 'ANTHROPIC_API_KEY': 'disposable-fixture',
           'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
    for key in ('ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL'):
        env[key] = fixture.model
    try:
        result = run_process([str(binary), '-p', 'Say hello.', '--model', fixture.model, '--output-format', 'json'],
                             env=env, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, (result.returncode, result.stderr[-2000:], result.stdout[-2000:])
        assert fixture.reply in result.stdout, result.stdout[-2000:]
        assert fixture.calls and {x['model'] for x in fixture.calls} == {fixture.model}, fixture.calls
        return {'messages_gateway': 'passed', 'subscription_login': 'not_tested', 'model': fixture.model}
    finally:
        fixture.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    assert sys.platform == 'win32', 'Windows only'
    import ctypes
    assert not ctypes.windll.shell32.IsUserAnAdmin(), 'Use a standard user'
    root = args.work.resolve()
    root.mkdir(parents=True)
    sys.path.insert(0, str(args.code.resolve()))
    from management_portal.execution import provider_updates as updates
    from management_portal.opencode_runtime import OpenCodeRuntime
    updates.RUNTIMES = root / 'runtimes'
    updates.RUNTIMES.mkdir()
    receipt = {'schema': 'tfi.windows-native-cli.v1', 'source_commit': os.environ.get('TFI_PACKAGE_SHA', ''),
               'process_elevated': False, 'real_credentials': False, 'external_inference': False, 'providers': {}}
    for name, runtime in [('codex', updates.CodexRuntime()), ('claude', updates.ClaudeRuntime()),
                          ('kimi', updates.KimiRuntime()), ('opencode', OpenCodeRuntime())]:
        row = receipt['providers'][name] = {'status': 'failed', 'stage': 'download_and_validate'}
        try:
            versions = runtime.available()
            row['versions'] = versions
            staged = runtime.stage(versions)
            row['stage'] = 'native_protocol'
            if name == 'codex':
                row.update(codex_probe(updates.npm_binary(staged, 'codex'), root))
            elif name == 'opencode':
                row.update(opencode_probe(updates.npm_binary(staged, 'opencode'), root, versions[0]))
            elif name == 'claude':
                binary = updates.run_command(str(updates.venv_python(staged)), '-c',
                    'from pathlib import Path; import claude_agent_sdk as s; print(Path(s.__file__).parent/"_bundled"/"claude.exe")')
                row.update(claude_probe(Path(binary), root))
            else:
                row['version_and_web_contract'] = 'passed'
                row['model_inference'] = 'not_tested'
            row['status'] = 'passed'
        except Exception as exc:
            row['error_type'] = type(exc).__name__
            # This test user has only fixture credentials, never production ones.
            traceback.print_exc()
        print(json.dumps({name: row}), flush=True)
        args.evidence.mkdir(parents=True, exist_ok=True)
        (args.evidence / 'native-cli.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    if any(row['status'] != 'passed' for row in receipt['providers'].values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
