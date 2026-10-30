"""Installed native clients reach a local fake upstream directly, using only fake credentials.

:module: execution.harness
:importance: P1
:design: decision_425e428410e0, decision_b1ab45c4b3ec
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid

import pytest
from aiohttp import web


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


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
def native_upstream():
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
