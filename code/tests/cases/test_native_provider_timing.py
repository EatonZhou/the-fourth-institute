"""Real local transport and installed native clients, using only fake credentials.

:module: execution.harness
:importance: P1
:design: decision_425e428410e0, decision_b1ab45c4b3ec
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import uuid

import pytest
from aiohttp import ClientSession, DummyCookieJar, WSMsgType, web

from management_portal import provider_timing as timing
from management_portal.execution.provider_timing_proxy import Proxy, ResponseObserver, Timings, application, now


@contextmanager
def serving(app):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def start():
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]

    runner, port = asyncio.run_coroutine_threadsafe(start(), loop).result(5)
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def events(protocol, identity, model='probe'):
    if protocol == 'responses':
        item = {'id': 'msg_' + identity, 'type': 'message', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': 'PROXY_OK', 'annotations': []}]}
        response = {'id': 'resp_' + identity, 'object': 'response', 'created_at': int(time.time()),
                    'status': 'in_progress', 'model': model, 'output': []}
        yield {'type': 'response.created', 'response': response}
        yield {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'status': 'in_progress', 'content': []}}
        yield {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0, 'content_index': 0,
               'part': {'type': 'output_text', 'text': '', 'annotations': []}}
        yield {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'delta': 'PROXY_OK'}
        yield {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'text': 'PROXY_OK'}
        yield {'type': 'response.output_item.done', 'output_index': 0, 'item': item}
        yield {'type': 'response.completed', 'response': {**response, 'status': 'completed', 'output': [item],
               'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5, 'input_tokens_details': {'cached_tokens': 0},
                         'output_tokens_details': {'reasoning_tokens': 0}}}}
    elif protocol == 'messages':
        yield {'type': 'message_start', 'message': {'id': 'msg_' + identity, 'type': 'message', 'role': 'assistant',
               'model': model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 3, 'output_tokens': 0}}}
        yield {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}
        yield {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'PROXY_OK'}}
        yield {'type': 'content_block_stop', 'index': 0}
        yield {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 2}}
        yield {'type': 'message_stop'}
    else:
        base = {'id': 'chatcmpl_' + identity, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model}
        yield {**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'PROXY_OK'}, 'finish_reason': None}]}
        yield {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}


def sse(event):
    return (('event: ' + event['type'] + '\r\n') if 'type' in event else '').encode() + b'data: ' + json.dumps(event).encode() + b'\r\n\r\n'


@pytest.fixture
def native_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path / 'timing'))
    records = []

    async def upstream(request):
        if request.headers.get('Upgrade') == 'websocket':
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for frame in ws:
                body = json.loads(frame.data)
                identity = uuid.uuid4().hex
                records.append({'received': now(), 'id': identity, 'body': body})
                for event in events('responses', identity):
                    await ws.send_json(event)
            return ws
        raw = await request.read()
        body = json.loads(raw)
        identity = uuid.uuid4().hex
        records.append({'received': now(), 'id': identity, 'raw': raw, 'auth': request.headers.get('Authorization') or request.headers.get('x-api-key'),
                        'internal_header': request.headers.get('x-portal-output-id')})
        if request.path.endswith('count_tokens'):
            return web.json_response({'input_tokens': 3})
        if body.get('retry') and len(records) == 1:
            return web.json_response({'error': 'retry'}, status=500)
        protocol = 'responses' if request.path.endswith('responses') else 'messages' if request.path.endswith('messages') else 'chat'
        payload = b''.join(sse(event) for event in events(protocol, identity, body.get('model', 'probe')))
        records[-1]['payload'] = payload
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        # Split CRLF and JSON in arbitrary positions; the downstream bytes must survive.
        await response.write(payload[:101])
        await asyncio.sleep(.03)
        await response.write(payload[101:])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_route('*', '/{path:.*}', upstream)
    with serving(app) as upstream_url:
        yield records, upstream_url


@pytest.fixture
def transport(native_upstream, monkeypatch):
    records, upstream_url = native_upstream
    with serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        yield records, lambda provider: timing.route_url(provider, upstream_url)


@pytest.mark.parametrize('provider,path', [('codex','responses'), ('claude','messages'), ('kimi','chat/completions'), ('opencode','chat/completions')])
def test_transport_stream_retry_and_parallel_output_identity(transport, provider, path):
    """Provider 代理保持流式字节、重试与并发请求身份"""
    records, route = transport
    url = route(provider) + '/' + path

    async def run():
        async with ClientSession() as client:
            async with client.get(timing.endpoint() + '/timing') as response:
                assert response.status == 403
            async with client.post(url.replace('/p/', '/p/tampered')) as response:
                assert response.status == 403
            async with client.post(url, json={'retry': True}) as response:
                assert response.status == 500
            async def one(index):
                payload = json.dumps({'request': index}).encode()
                async with client.post(url, data=payload, headers={'Authorization': 'Bearer fake', 'x-portal-output-id': 'local-' + str(index)}) as response:
                    first = await response.content.read(101)
                    assert len(first) == 101
                    assert not response.content.at_eof()
                    result = first + await response.read()
                    return payload, result
            return await asyncio.gather(one(1), one(2))
    results = asyncio.run(run())
    for index, (raw, payload) in enumerate(results, 1):
        record = next(r for r in records if r.get('raw') == raw)
        assert record['auth'] == 'Bearer fake' and record['internal_header'] is None
        assert payload == record['payload']
        value = timing.lookup(provider, 'local-' + str(index))
        assert value['started_at'] <= record['received'] < value['finished_at']
    assert timing.lookup(provider, 'missing') == {}


@pytest.mark.parametrize('encoding,encoded', [
    ('zstd', '28b52ffd04582902007b226d6f64656c223a2270726f6265222c22696e707574223a5b7b22726f6c65223a2275736572222c22636f6e74656e74223a22636f6d706163742070726f6265227d5d7d7e16017c'),
    ('gzip', '1f8b0800000000000203ab56cacd4f49cd51b2522a28ca4f4a55d251cacc2b282d51b28aae562acacf49054a9416a71601c593f3f34a52f3803240566e416272890244476d6c2d00df8b05d945000000'),
])
def test_compact_compressed_request_and_response_pass_through(monkeypatch, tmp_path, encoding, encoded):
    """Codex compact 的 zstd 与 gzip 请求响应原样透传

    Forward compressed bytes unchanged even without optional decoding libraries."""
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    body = bytes.fromhex(encoded)
    received = []

    async def compact(request):
        received.append((request.path, request.headers.get('Content-Encoding'),
                         request.headers.get('Content-Length'), await request.read()))
        return web.Response(body=body, headers={'Content-Encoding': encoding,
                                                'Content-Type': 'application/json'})

    upstream = web.Application(handler_args={'auto_decompress': False})
    upstream.router.add_post('/responses/compact', compact)
    with serving(upstream) as upstream_url, serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        url = timing.route_url('codex', upstream_url) + '/responses/compact'

        async def run():
            async with ClientSession(auto_decompress=False) as client:
                async with client.post(url, data=body, headers={'Content-Encoding': encoding,
                                                              'Content-Type': 'application/json'}) as response:
                    assert response.status == 200
                    assert response.headers['Content-Encoding'] == encoding
                    assert await response.read() == body
        asyncio.run(run())
    assert received == [('/responses/compact', encoding, str(len(body)), body)]


def test_websocket_warmup_and_each_response_have_separate_dispatch(transport):
    """WebSocket 预热与各模型请求分别计时"""
    records, route = transport
    async def run():
        async with ClientSession() as client, client.ws_connect(route('codex') + '/responses') as ws:
            for generate in (False, True, True):
                await ws.send_json({'type': 'response.create', 'generate': generate})
                while (await ws.receive_json())['type'] != 'response.completed':
                    pass
    asyncio.run(run())
    assert timing.lookup('codex', 'msg_' + records[0]['id']) == {}
    for record in records[1:]:
        value = timing.lookup('codex', 'msg_' + record['id'])
        assert value['started_at'] <= record['received'] <= value['finished_at']
    assert timing.lookup('codex', 'msg_' + records[1]['id'])['started_at'] < timing.lookup('codex', 'msg_' + records[2]['id'])['started_at']


def test_upload_streams_and_preserves_escaped_target(monkeypatch, tmp_path):
    """计时代理边收边转发请求体并保留 URL 转义"""
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    first_arrived = threading.Event()
    received = []

    async def upstream(request):
        first = await request.content.readexactly(5)
        first_arrived.set()
        received.append((request.raw_path, first + await request.read()))
        return web.Response(body=b'ok')

    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', upstream)
    with serving(app) as upstream_url, serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        url = timing.route_url('codex', upstream_url)

        async def run():
            from yarl import URL
            async def upload():
                yield b'first'
                assert await asyncio.to_thread(first_arrived.wait, 3), 'proxy buffered the upload'
                yield b'last'
            async with ClientSession() as client:
                async with client.post(URL(url + '/a%2Fb?x=%2F&x=a+b', encoded=True), data=upload()) as response:
                    assert response.status == 200
                    assert await response.read() == b'ok'
        asyncio.run(run())
    assert received == [('/a%2Fb?x=%2F&x=a+b', b'firstlast')]


def test_http_preserves_negotiation_headers_without_shared_cookie_state(monkeypatch, tmp_path):
    """计时代理保留协商和重复头且不共享 Cookie"""
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    requests = []

    async def upstream(request):
        requests.append(request.headers.copy())
        return web.Response(body=b'ok', headers=[('Set-Cookie', 'one=1; Path=/'),
            ('Set-Cookie', 'two=2; Path=/'), ('X-Response', 'a'), ('X-Response', 'b')])

    app = web.Application()
    app.router.add_get('/probe', upstream)
    with serving(app) as upstream_url, serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        url = timing.route_url('codex', upstream_url) + '/probe'

        async def run():
            async with ClientSession(cookie_jar=DummyCookieJar(),
                    skip_auto_headers={'Accept', 'Accept-Encoding', 'User-Agent'}) as client:
                headers = [('Accept-Encoding', 'zstd, gzip'), ('Accept', 'application/json'),
                           ('X-Repeated', 'a'), ('X-Repeated', 'b'), ('Connection', 'X-Hop'), ('X-Hop', 'omit')]
                async with client.get(url, headers=headers) as response:
                    assert await response.read() == b'ok'
                    assert response.headers.getall('Set-Cookie') == ['one=1; Path=/', 'two=2; Path=/']
                    assert response.headers.getall('X-Response') == ['a', 'b']
                async with client.get(url) as response:
                    assert await response.read() == b'ok'
        asyncio.run(run())
    assert requests[0]['Accept-Encoding'] == 'zstd, gzip'
    assert requests[0]['Accept'] == 'application/json'
    assert requests[0].getall('X-Repeated') == ['a', 'b']
    assert 'X-Hop' not in requests[0]
    assert all(name not in requests[1] for name in ('Cookie', 'Accept-Encoding', 'Accept', 'User-Agent', 'Content-Type'))


@pytest.mark.parametrize('fault', ['parser', 'cache', 'unknown_protocol', 'oversized'])
def test_observation_failure_never_interrupts_http(monkeypatch, tmp_path, fault):
    """解析异常、未知协议和观察超限不阻断响应"""
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    payload = sse({'type': 'message_start', 'message': {'id': 'observed'}})
    if fault == 'unknown_protocol':
        payload = sse({'type': 'message_start', 'message': ['unexpected', 'shape']})
    elif fault == 'oversized':
        payload = b'data: ' + b'x' * (2 * 1024 * 1024) + b'\n\n'
    else:
        def fail(*_args, **_kwargs):
            raise RuntimeError('injected observation failure')
        monkeypatch.setattr(ResponseObserver if fault == 'parser' else Timings, 'feed' if fault == 'parser' else 'put', fail)
    payload += b'data: [DONE]\n\n'

    async def upstream(_request):
        return web.Response(body=payload, content_type='text/event-stream')

    app = web.Application()
    app.router.add_get('/responses', upstream)
    with serving(app) as upstream_url, serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        url = timing.route_url('codex', upstream_url) + '/responses'

        async def run():
            async with ClientSession() as client:
                async with client.get(url) as response:
                    assert response.status == 200
                    assert await response.read() == payload
        asyncio.run(run())


def test_websocket_preserves_control_frames_and_opaque_messages(monkeypatch, tmp_path):
    """计时代理传递 WebSocket 控制帧、大消息和关闭原因"""
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    opaque = '[' + ' ' * (2 * 1024 * 1024) + ']'

    async def upstream(request):
        ws = web.WebSocketResponse(protocols=['probe'], autoping=False, max_msg_size=0)
        await ws.prepare(request)
        message = await ws.receive()
        assert message.type == WSMsgType.PING and message.data == b'alive'
        await ws.pong(message.data)
        assert await ws.receive_str() == opaque
        await ws.send_str(opaque)
        await ws.send_str('{"type":"message_start","message":[]}')
        await ws.close(code=4001, message=b'provider-finished')
        return ws

    app = web.Application()
    app.router.add_get('/responses', upstream)
    with serving(app) as upstream_url, serving(application()) as proxy:
        monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', proxy.rsplit(':', 1)[1])
        url = timing.route_url('codex', upstream_url) + '/responses'

        async def run():
            async with ClientSession() as client, client.ws_connect(url, protocols=['probe'], autoping=False, max_msg_size=0) as ws:
                assert ws.protocol == 'probe'
                await ws.ping(b'alive')
                message = await ws.receive(timeout=3)
                assert message.type == WSMsgType.PONG and message.data == b'alive'
                await ws.send_str(opaque)
                assert await ws.receive_str(timeout=3) == opaque
                assert await ws.receive_str(timeout=3) == '{"type":"message_start","message":[]}'
                close = await ws.receive(timeout=3)
                assert close.type == WSMsgType.CLOSE and close.data == 4001
                assert close.extra == 'provider-finished'
        asyncio.run(run())


@pytest.mark.parametrize('reply', ['[]', '{"started_at":false}', 'incomplete'])
def test_timing_lookup_failure_does_not_change_completed_output(monkeypatch, tmp_path, reply):
    """计时查询损坏或中断不改变已完成模型输出"""
    import io
    from http.client import IncompleteRead
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path))
    timing.signing_key()

    def lookup(*_args, **_kwargs):
        if reply == 'incomplete':
            raise IncompleteRead(b'{')
        return io.BytesIO(reply.encode())

    monkeypatch.setattr(timing._local, 'open', lookup)
    output = {'item': {'id': 'output', 'type': 'agentMessage', 'text': 'unchanged'}}
    assert timing.attach(output, 'codex') == output


def test_observation_keeps_first_completion_and_bounds_cache():
    """请求计时保留首次完成时间并限制缓存容量"""
    cache = Timings(limit=3)
    observer = ResponseObserver(cache, 'kimi', now(), 'assistant')
    for event in events('chat', 'first'):
        for byte in sse(event):
            observer.feed(bytes([byte]))
    completed = cache.get('kimi', 'assistant')
    observer.event({'id': 'chatcmpl_first', 'choices': [], 'usage': {}})
    observer.finish()
    assert cache.get('kimi', 'assistant') == completed
    for i in range(6):
        cache.put('kimi', str(i), {})
    assert len(cache.entries) == 3


def native_process(command, root, env, interact=None):
    process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        if interact:
            return interact(process)
        output, error = process.communicate(timeout=45)
        assert process.returncode == 0, error.decode(errors='replace')[-1500:]
        return output.decode()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)


@pytest.mark.parametrize('provider', ['codex', 'claude', 'kimi', 'opencode'])
def test_installed_providers_stream_without_timing_proxy(native_upstream, tmp_path, provider, installed_provider_paths):
    """四种原生 Provider 无计时代理直连并保留未知发送时间

    使用已安装的四种原生客户端直连测试服务，验证流式输出、认证与模型段的未知发送时间。
    """
    records, url = native_upstream
    runtime = installed_provider_paths['runtime_root']
    binaries = {
        'codex': installed_provider_paths['codex_binary'],
        'claude': next(runtime.glob('claude-agent-sdk-current/lib/python*/site-packages/claude_agent_sdk/_bundled/claude'), None),
        'kimi': runtime / 'kimi-code-current/bin/kimi',
        'opencode': runtime / 'opencode-current/bin/opencode',
    }
    binary = binaries[provider]
    if binary is None or not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip(f'Installed {provider} program is unavailable: {binary or runtime}')
    env = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','HOME','USER'}}
    env.update(NO_PROXY='127.0.0.1,localhost', DO_NOT_TRACK='1', DISABLE_TELEMETRY='1')
    prompt = 'Reply only PROXY_OK. Do not use tools.'
    if provider == 'claude':
        env.update(CLAUDE_CONFIG_DIR=str(tmp_path/'claude'), ANTHROPIC_API_KEY='fake', ANTHROPIC_BASE_URL=url, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        command = [str(binary), '--bare', '-p', prompt, '--output-format','stream-json','--verbose','--model','claude-sonnet-4-6','--tools','','--no-session-persistence']
        output = native_process(command, tmp_path, env)
        ids = [row['message']['id'] for line in output.splitlines() if (row:=json.loads(line)).get('type') == 'assistant']
    elif provider == 'kimi':
        home = tmp_path/'kimi'
        home.mkdir()
        env['KIMI_CODE_HOME'] = str(home)
        (home/'config.toml').write_text(f'default_model="probe"\n[providers.probe]\ntype="kimi"\nbase_url="{url}/v1"\napi_key="fake"\n[models.probe]\nprovider="probe"\nmodel="kimi-k2.7-code"\nmax_context_size=262144\n[telemetry]\nenabled=false\n')
        output = native_process([str(binary), '-p', prompt, '--output-format','stream-json'], tmp_path, env)
        ids = []
        for wire in home.glob('sessions/*/*/agents/main/wire.jsonl'):
            for line in wire.read_text().splitlines():
                event = json.loads(line).get('event', {})
                if event.get('type') == 'step.end' and event.get('messageId'):
                    ids.append(event['messageId'])
    elif provider == 'codex':
        (tmp_path/'codex').mkdir()
        env.update(CODEX_HOME=str(tmp_path/'codex'), PROBE_API_KEY='fake')
        command = [str(binary), 'app-server','--stdio','-c','model_provider="probe"', '-c','model_providers.probe.name="Probe"',
                   '-c',f'model_providers.probe.base_url="{url}/v1"', '-c','model_providers.probe.env_key="PROBE_API_KEY"', '-c','model_providers.probe.wire_api="responses"']
        def interact(process):
            received = queue.Queue()
            errors = []
            threading.Thread(target=lambda: [received.put(json.loads(line)) for line in process.stdout], daemon=True).start()
            threading.Thread(target=lambda: [errors.append(line.decode()) for line in process.stderr], daemon=True).start()
            outputs = []
            def rpc(number, method, params):
                process.stdin.write((json.dumps({'id':number,'method':method,'params':params})+'\n').encode()); process.stdin.flush()
                while True:
                    try:
                        row = received.get(timeout=30)
                    except queue.Empty:
                        pytest.fail('Native app server did not answer: ' + ''.join(errors)[-2000:])
                    outputs.append(row)
                    if row.get('id') == number:
                        assert 'error' not in row, row
                        return row['result']
            rpc(1,'initialize',{'clientInfo':{'name':'portal-timing-test','version':'1'},'capabilities':{'experimentalApi':True}})
            thread = rpc(2,'thread/start',{'cwd':str(tmp_path),'model':'gpt-5.4','modelProvider':'probe','approvalPolicy':'never','sandbox':'read-only','ephemeral':True,
                         'config': {'model_providers.probe.base_url': url + '/v1'}})['thread']['id']
            rpc(3,'turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}]})
            while True:
                row=received.get(timeout=30); outputs.append(row)
                if row.get('method') == 'turn/completed':
                    assert row['params']['turn']['status'] == 'completed'
                    return outputs
        rows = native_process(command, tmp_path, env, interact)
        output = json.dumps(rows)
        ids = [row['params']['item']['id'] for row in rows if row.get('method') == 'item/completed' and row['params']['item']['type'] == 'agentMessage']
    else:
        for name in ('CONFIG','DATA','STATE','CACHE'):
            env['XDG_'+name+'_HOME'] = str(tmp_path/name)
        env.update(OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_PROJECT_CONFIG='1',OPENCODE_DISABLE_CLAUDE_CODE='1',OPENCODE_DISABLE_MODELS_FETCH='1',OPENCODE_DISABLE_SHARE='1',OPENCODE_DB=str(tmp_path/'open.db'))
        env['OPENCODE_CONFIG_CONTENT']=json.dumps({'autoupdate':False,'share':'disabled','snapshot':False,'enabled_providers':['openrouter'],
          'provider':{'openrouter':{'options':{'baseURL':url+'/v1','apiKey':'fake'},'models':{'probe':{'name':'Probe','limit':{'context':32768,'output':128}}}}}})
        command=[str(binary),'serve','--hostname','127.0.0.1','--port','0']
        def interact(process):
            import re
            import urllib.request
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(line) for line in process.stdout],daemon=True).start()
            threading.Thread(target=lambda: process.stderr.read(),daemon=True).start()
            while not (match:=re.search(rb'listening on (http://[\d.]+:\d+)',lines.get(timeout=30))):
                pass
            base=match[1].decode()
            local=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(path,body):
                return json.load(local.open(urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=35))
            session=post('/session',{})['id']
            return post('/session/'+session+'/message',{'model':{'providerID':'openrouter','modelID':'probe'},'parts':[{'type':'text','text':prompt}]})
        result=native_process(command,tmp_path,env,interact)
        output=json.dumps(result)
        ids=[result['info']['id']]
    assert 'PROXY_OK' in output and ids
    assert records
    assert all(record.get('auth') in ('fake', 'Bearer fake') for record in records)
    assert all(not record.get('internal_header') for record in records)
    # A native completion with no dispatch event persists an unknown start.
    from dataclasses import replace
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation
    store = AgentDataStore()
    request = replace(_turn_request('resident_123456789abc'), provider=provider)
    session = _runtime_session(request, 'timing-test')
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session))
    for kind in ('reasoning', 'agentMessage'):
        params = {'threadId': session.backend_session_id, 'turnId': 'turn-test',
                  'item': {'id': kind, 'type': kind, 'text': 'PROXY_OK', 'content': ['PROXY_OK']}}
        projector.observe(provider=provider, backend_route_id='zero', backend_session_id=session.backend_session_id,
                          execution_id=request.execution_id, event={'method':'item/completed', 'params': params})
    with store.database.read() as connection:
        rows = connection.execute("SELECT started_at,finished_at FROM segments WHERE segment_type='model'").fetchall()
    assert len(rows) == 2
    assert all(row['started_at'] is None and row['finished_at'] is not None for row in rows)


def test_kimi_delayed_blocks_keep_their_own_step_timing():
    """Kimi 延迟完成块使用本步骤请求计时"""
    from management_portal.kimi_live_events import KimiLiveItems
    observed, lookups = [], []
    values = {'one': {'started_at':'2026-10-14T10:00:00Z', 'finished_at':'2026-10-14T10:00:02Z'},
              'two': {'started_at':'2026-10-14T10:00:03Z', 'finished_at':'2026-10-14T10:00:05Z'}}
    def lookup(agent, turn, step, identity):
        lookups.append((agent, turn, step, identity))
        return values[identity]
    items = KimiLiveItems('session', observed.append, request_timing=lookup)
    def event(typ, **payload):
        items.observe({'type':typ, 'session_id':'session', 'epoch':'e', 'payload':{'agentId':'main','turnId':'turn', **payload}})
    for step, identity in enumerate(values, 1):
        event('turn.step.started', step=step)
        event('thinking.delta', delta=identity + '-think')
        event('assistant.delta', delta=identity + '-answer')
        event('turn.step.completed', stepId=identity)
    event('turn.ended')
    completed = [e['params'] for e in observed if e['method'] == 'item/completed']
    assert len(completed) == 4
    assert [e['request_timing'] for e in completed] == [values['one'], values['one'], values['two'], values['two']]
    assert lookups == [('main','turn',1,'one'),('main','turn',2,'two')]
