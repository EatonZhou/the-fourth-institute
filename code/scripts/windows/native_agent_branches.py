"""Real Codex/Claude programs and Portal adapters against disposable model servers.

Runs on Windows (ordinary user) and POSIX; never uses a real provider credential.
The fixture replaces the upstream model, not the CLI, transport or turn adapter.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


class Model:
    def __init__(self):
        self.calls = []
        self.mode = 'text'
        self.entered = threading.Event()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.json({'data': []})

            def json(self, value, status=200):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
                if self.path.split('?')[0].endswith('/count_tokens'):
                    self.json({'input_tokens': 10})
                    return
                owner.calls.append({'path': self.path.split('?')[0], 'body': body})
                mode = owner.mode
                if mode != 'unauthorized':
                    owner.mode = 'text'
                if mode == 'wait':
                    owner.entered.set()
                    owner.release.wait(45)
                if mode == 'unauthorized':
                    self.json({'type': 'error', 'error': {'type': 'authentication_error',
                              'message': 'Disposable fixture rejects this credential'}}, 401)
                    return
                messages = '/messages' in self.path
                number = len(owner.calls)
                text = f'BRANCH_REPLY_{number}'
                tool = 'mcp__portal__portal_receipt' if messages else 'portal_receipt'
                if messages:
                    block = ({'type': 'tool_use', 'id': f'tool_{number}', 'name': tool, 'input': {}}
                             if mode == 'tool' else {'type': 'text', 'text': text})
                    stop = 'tool_use' if mode == 'tool' else 'end_turn'
                    message = {'id': f'msg_{number}', 'type': 'message', 'role': 'assistant',
                               'model': body.get('model'), 'content': [block], 'stop_reason': stop,
                               'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 2}}
                    if not body.get('stream'):
                        self.json(message)
                        return
                    events = [
                        ('message_start', {'message': {**message, 'content': [], 'stop_reason': None}}),
                        ('content_block_start', {'index': 0, 'content_block': (
                            block if mode == 'tool' else {'type': 'text', 'text': ''})}),
                        ('content_block_delta', {'index': 0, 'delta': (
                            {'type': 'input_json_delta', 'partial_json': '{}'} if mode == 'tool'
                            else {'type': 'text_delta', 'text': text})}),
                        ('content_block_stop', {'index': 0}),
                        ('message_delta', {'delta': {'stop_reason': stop}, 'usage': {'output_tokens': 2}}),
                        ('message_stop', {}),
                    ]
                else:
                    item = ({'id': f'fc_{number}', 'call_id': f'call_{number}', 'type': 'function_call',
                             'name': tool, 'arguments': '{}'} if mode == 'tool' else
                            {'id': f'msg_{number}', 'type': 'message', 'role': 'assistant',
                             'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text}]})
                    events = [('response.created', {'response': {'id': f'resp_{number}'}}),
                              ('response.output_item.done', {'item': item}),
                              ('response.completed', {'response': {'id': f'resp_{number}', 'status': 'completed',
                               'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}})]
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    for kind, payload in events:
                        self.wfile.write(f'event: {kind}\ndata: {json.dumps({"type": kind, **payload})}\n\n'.encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Stop deliberately abandons an in-flight fixture response.
                self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def codex(binary, root, record):
    from management_common.local_ipc import rendezvous_path
    from management_portal import codex_app_server_host as host
    from management_portal import codex_app_server_client as wire
    from management_common import portal_network
    model = Model()
    home = root / 'codex-home'
    home.mkdir()
    (home / 'config.toml').write_text(
        'model = "gpt-test"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "Fixture"\n'
        f'base_url = "{model.url}/v1"\nwire_api = "responses"\n'
        '[features]\napps = false\nplugins = false\nmemories = false\n', encoding='utf-8')
    endpoint = root / 'codex-endpoint'
    config = {'web_search': 'disabled', 'project_doc_max_bytes': 0,
              'features': {'apps': False, 'plugins': False, 'memories': False}}
    params = {'cwd': str(root), 'config': config, 'model': 'gpt-test',
              'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
              'dynamicTools': [{'name': 'portal_receipt', 'description': 'Get the fixture receipt.',
                               'inputSchema': {'type': 'object', 'properties': {}}}]}
    tool_calls = []

    def tool(call):
        tool_calls.append(call)
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': 'TOOL_RECEIPT'}]}

    @contextmanager
    def service():
        stop, failures = threading.Event(), []
        def serve():
            try:
                host.run_host(endpoint, binary, home, stop, platform='win32', startup_timeout=30)
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = wire.CodexAppServerClient(endpoint, request_timeout_seconds=10)
        try:
            deadline = time.monotonic() + 35
            while not rendezvous_path(endpoint).exists():
                assert thread.is_alive() and not failures, failures
                assert time.monotonic() < deadline, 'Codex host readiness timed out'
                time.sleep(.05)
            yield client
        finally:
            client.close()
            stop.set()
            thread.join(35)
            assert not thread.is_alive() and not failures, failures

    def turn(client, thread, prompt, **kwargs):
        return client.run_turn(thread, prompt, cwd=str(root), model='gpt-test', config=config,
                               dynamic_tool_handler=tool, **kwargs)
    try:
        with patch.object(wire, 'connect_transport', lambda path: host.connect_transport(path, platform='win32')), \
                patch.object(portal_network, 'saved_proxy_environment', return_value={}):
            with service() as client:
                thread = client.start_thread(params)['thread']['id']
                assert 'BRANCH_REPLY' in turn(client, thread, 'FIRST_INPUT').response
                assert 'BRANCH_REPLY' in turn(client, thread, 'SECOND_INPUT').response
                assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
                record('two_turns_and_history')
                model.mode = 'tool'
                assert 'BRANCH_REPLY' in turn(client, thread, 'CALL_TOOL').response
                assert len(tool_calls) == 1 and 'TOOL_RECEIPT' in json.dumps(model.calls[-1]['body'])
                record('portal_tool_roundtrip')
                fork = client.fork_thread({**params, 'threadId': thread})['thread']['id']
                assert fork != thread
                assert 'BRANCH_REPLY' in turn(client, fork, 'FORK_INPUT').response
                assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
                record('fork_preserves_history')
                cancel = threading.Event()
                model.mode = 'wait'
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(turn, client, thread, 'STOP_INPUT', cancellation_requested=cancel.is_set)
                    assert model.entered.wait(20), 'No upstream request to stop'
                    cancel.set()
                    try:
                        pending.result(timeout=25)
                    except InterruptedError:
                        pass
                    else:
                        raise AssertionError('Stopped turn reported success')
                    finally:
                        model.release.set()
                assert 'BRANCH_REPLY' in turn(client, thread, 'AFTER_STOP').response
                record('stop_and_next_turn')
            with service() as client:
                assert 'BRANCH_REPLY' in turn(client, thread, 'AFTER_SERVICE_RESTART').response
                assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
                record('service_restart_same_thread')
            assert {call['path'] for call in model.calls} == {'/v1/responses'}
            record('windows_authenticated_websocket')
    finally:
        model.close()


async def claude(root, record):
    from management_portal import claude_code_daemon as module
    from management_portal.claude_runtime import native_cli_path
    assert Path(native_cli_path()).is_file()
    record('active_sdk_native_cli_resolution')
    model = Model()
    profile = {'id': 'fixture', 'kind': 'anthropic_messages', 'enabled': True,
               'default_model': 'fixture-model', 'models': [{'id': 'fixture-model'}],
               'protocols': {'anthropic_messages': {'base_url': model.url}}}
    env = {**dict.fromkeys(module.GATEWAY_MODEL_VARIABLES, 'fixture-model'),
           'ANTHROPIC_BASE_URL': model.url, 'ANTHROPIC_API_KEY': 'disposable-fixture',
           'ANTHROPIC_AUTH_TOKEN': '', 'CLAUDE_CODE_OAUTH_TOKEN': '',
           'CLAUDE_CODE_MAX_RETRIES': '0'}
    params = {'session_key': 'fixture', 'session_id': '', 'config_dir': str(root / 'claude-config'),
              'cwd': str(root), 'provider_profile': 'fixture', 'model': 'fixture-model',
              'is_owner': False, 'permission_mode': 'dontAsk', 'allow_subagents': False,
              'tools': [{'name': 'portal_receipt', 'description': 'Get fixture receipt.',
                         'inputSchema': {'type': 'object', 'properties': {}}}]}
    tool_calls = []
    async def dispatch(name, arguments):
        tool_calls.append(name)
        return {'content': [{'type': 'text', 'text': 'TOOL_RECEIPT'}], 'isError': False}
    params['_tool_dispatch'] = dispatch
    async def emit(event):
        pass
    async def turn(daemon, name, **overrides):
        return await asyncio.wait_for(daemon.run_turn(
            {**params, 'turn_id': name, 'prompt': name, **overrides}, emit), 90)
    daemon = module.ClaudeCodeDaemon()
    try:
        with patch.object(module, '_provider_environment', return_value=(env, profile)), \
                patch.object(module, '_provider_profile', return_value=profile):
            first = await turn(daemon, 'FIRST_INPUT')
            session = first['session_id']
            params['session_id'] = session
            second = await turn(daemon, 'SECOND_INPUT')
            assert second['session_id'] == session and 'BRANCH_REPLY' in second['response'], (first, second)
            assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
            record('two_turns_and_history')
            count = len(model.calls)
            assert await turn(daemon, 'SECOND_INPUT') == second
            assert len(model.calls) == count
            record('same_turn_retry_without_replay')
            model.mode = 'tool'
            result = await turn(daemon, 'CALL_TOOL')
            assert 'BRANCH_REPLY' in result['response'] and tool_calls == ['portal_receipt']
            assert 'TOOL_RECEIPT' in json.dumps(model.calls[-1]['body'])
            record('portal_tool_roundtrip')
            model.mode = 'wait'
            running = asyncio.create_task(turn(daemon, 'STOP_INPUT'))
            assert await asyncio.to_thread(model.entered.wait, 20), 'No upstream request to stop'
            await daemon.interrupt({**params, 'turn_id': 'STOP_INPUT'})
            try:
                await asyncio.wait_for(running, 25)
            except module.ClaudeTurnInterrupted:
                pass
            else:
                raise AssertionError('Stopped turn reported success')
            finally:
                model.release.set()
            assert 'BRANCH_REPLY' in (await turn(daemon, 'AFTER_STOP'))['response']
            record('stop_and_next_turn')
            await daemon.close()
            daemon = module.ClaudeCodeDaemon()
            resumed = await turn(daemon, 'AFTER_DAEMON_RESTART')
            assert resumed['session_id'] == session and 'BRANCH_REPLY' in resumed['response']
            assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
            record('daemon_restart_same_session')
            fork_cwd = root / 'fork-workspace'
            fork_cwd.mkdir()
            fork = await daemon.fork_session({**params, 'source_session_id': session,
                                              'cwd': str(fork_cwd), 'title': 'Fixture fork'})
            assert fork['session_id'] != session
            forked = await turn(daemon, 'FORK_INPUT', session_key='fork', session_id=fork['session_id'],
                                cwd=str(fork_cwd))
            assert 'BRANCH_REPLY' in forked['response']
            assert 'FIRST_INPUT' in json.dumps(model.calls[-1]['body'])
            record('fork_preserves_history_in_new_workspace')
            model.mode = 'unauthorized'
            try:
                await turn(daemon, 'BAD_CREDENTIAL')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Authentication failure reported success')
            model.mode = 'text'
            assert 'BRANCH_REPLY' in (await turn(daemon, 'AFTER_AUTH_FAILURE'))['response']
            record('authentication_failure_and_retry')
            assert {call['path'] for call in model.calls} == {'/v1/messages'}
    finally:
        model.release.set()
        await daemon.close()
        model.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--provider', choices=['codex', 'claude'], required=True)
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.code.resolve()))
    root = args.work.resolve()
    root.mkdir(parents=True)
    result = {'provider': args.provider, 'platform': sys.platform, 'real_cli': True,
              'upstream': 'loopback_fixture', 'cases': [], 'ok': False}
    def save():
        (root / 'branches.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    def record(name):
        result['cases'].append(name)
        save()
        print(name, 'passed', flush=True)
    try:
        if args.provider == 'codex':
            codex(args.binary, root, record)
        else:
            asyncio.run(claude(root, record))
        result['ok'] = True
    except BaseException as exc:
        result['error_type'] = type(exc).__name__
        traceback.print_exc()
    finally:
        save()
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
