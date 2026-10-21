"""Canonical Provider items have one runtime source, including descendants.

:module: spec.design-governance
:design: decision_b1ab45c4b3ec
"""
from types import SimpleNamespace
import json
import threading
import socket
import hashlib
import base64
import struct

from management_portal.codex_app_server_client import CodexAppServerClient, _TurnWaiter
from management_portal.kimi_live_events import KimiLiveItems, KimiLiveStream


def frame(typ, *, agent='main', **payload):
    return {'type': typ, 'session_id': 's', 'epoch': 'e', 'payload': {'agentId': agent, 'turnId': 1, **payload}}


def test_codex_routes_early_and_nested_children_without_mixing_other_threads(tmp_path):
    """codex 把先到的子线程和嵌套孙线程事件归入对应子 agent，不混入无关线程

    :design-point:"""
    client = CodexAppServerClient(tmp_path / 'unused.sock')
    output = []
    waiter = _TurnWaiter(thread_id='root', turn_id='t', event_callback=output.append)
    client._turn_waiters['root'] = waiter
    def message(thread, body):
        client._dispatch_notification('item/completed', {'threadId': thread, 'turnId': 'child-turn', 'item': {'type': 'agentMessage', 'id': 'a', 'text': body}})
    def spawn(parent, child, call):
        client._dispatch_notification('item/completed', {'threadId': parent, 'turnId': 't' if parent == 'root' else 'child-turn', 'item': {
            'id': call, 'type': 'collabAgentToolCall', 'tool': 'spawnAgent', 'status': 'completed', 'receiverThreadIds': [child]}})
    message('foreign', 'never belongs here')
    message('child', 'before spawn acknowledgement')
    spawn('root', 'child', 'spawn-c')
    spawn('child', 'grandchild', 'spawn-g')
    message('grandchild', 'nested output')
    children = [ev for ev in output if ev['method'] == 'subagent/event']
    assert [ev['params']['traceKey'] for ev in children] == ['child', 'child', 'grandchild']
    assert children[0]['params']['parentToolUseId'] == 'spawn-c'
    assert children[-1]['params']['parentToolUseId'] == 'spawn-g'
    assert children[-1]['params']['ancestorTraceKeys'] == ['child']
    assert waiter.completed is None and not waiter.event.is_set()
    assert 'never belongs here' not in json.dumps(output)


def test_kimi_only_complete_live_blocks_and_explicit_tool_results_enter_items():
    """kimi 只把完整的实时块和显式工具结果写成条目，最后一段文本作为最终回复

    :design-point:"""
    output=[]; live=KimiLiveItems('s', output.append, prompt_id='p')
    for ev in [
        frame('turn.started'), frame('turn.step.started', step=1),
        frame('thinking.delta', delta='thought'), frame('assistant.delta', delta='working'),
        frame('tool.call.started', toolCallId='call1', name='Shell', args={'command':'ls'}),
        frame('tool.result', toolCallId='call1', output=[{'type':'text','text':'ok'},{'type':'image','url':'fixture'}], isError=False),
        frame('turn.started', agent='child'), frame('tool.call.started', agent='child', toolCallId='call2', name='Read', args={}),
        frame('tool.result', agent='child', toolCallId='call2', output='denied', isError=True),
        frame('assistant.delta', delta='answer'), frame('turn.step.completed', step=1),
        frame('turn.ended', reason='completed'),
    ]: live.observe(ev)
    main=[x['params']['item'] for x in output if x['method']=='item/completed']
    assert [x['type'] for x in main] == ['reasoning','agentMessage','toolCall','agentMessage']
    assert main[-1]['phase']=='final_answer' and live.response=='answer'
    assert main[2]['output'][1]['type']=='image'
    assert all(x['params']['turnId']=='p' for x in output if x['method']!='subagent/event')
    child=[x['params']['event']['params']['item'] for x in output if x['method']=='subagent/event']
    assert child[-1]['status']=='failed'


def test_codex_child_projection_failure_does_not_disconnect_the_parent(tmp_path):
    """codex 子线程事件投影失败时父轮次不报错

    :design-point:"""
    client = CodexAppServerClient(tmp_path / 'unused.sock')
    def reject(_event):
        raise ValueError('projection fixture')
    client._turn_waiters['root'] = _TurnWaiter(thread_id='root', turn_id='t', event_callback=reject)
    client._dispatch_notification('thread/started', {'thread': {
        'id': 'child', 'source': {'subAgent': {'thread_spawn': {'parent_thread_id': 'root'}}},
    }})
    client._dispatch_notification('item/completed', {'threadId': 'child', 'item': {
        'id': 'answer', 'type': 'agentMessage', 'text': 'child output',
    }})
    assert client._turn_waiters['root'].error is None


def test_codex_remembers_grandchild_source_before_parent_announcement(tmp_path):
    """codex 孙线程先于其父线程公告出现时暂存事件，父线程公告后再归入

    :design-point:"""
    client = CodexAppServerClient(tmp_path / 'unused.sock')
    output = []
    client._turn_waiters['root'] = _TurnWaiter(thread_id='root', turn_id='t', event_callback=output.append)
    def started(child, parent):
        client._dispatch_notification('thread/started', {'thread': {
            'id': child, 'source': {'subAgent': {'thread_spawn': {'parent_thread_id': parent}}},
        }})
    started('grandchild', 'child')
    client._dispatch_notification('item/completed', {'threadId': 'grandchild', 'item': {
        'id': 'answer', 'type': 'agentMessage', 'text': 'early grandchild',
    }})
    assert not output
    started('child', 'root')
    assert output[0]['params']['traceKey'] == 'grandchild'


def test_kimi_child_prompt_text_uses_its_own_trace():
    """kimi 子 agent 的提示词在 spawn 公告后归入自己的 trace，嵌套子 agent 带完整祖先链

    :design-point:"""
    # Kimi Code 2.1.1 starts the subagent's turn before the spawning agent
    # says subagent.spawned; agent numbers restart in every session.
    output = []
    live = KimiLiveItems('s', output.append)
    live.observe(frame('agent.created', agent='agent-0'))
    live.observe(frame('turn.started', agent='agent-0', turnId=0, prompt='inspect the tests'))
    assert output == []
    live.observe(frame('subagent.spawned', subagentId='agent-0', parentToolCallId='Agent_0_x',
                       parentAgentId='main', callerAgentId='main', taskId='agent-task'))
    # The run's running card comes first, then the steps held for the spawn.
    assert [event['method'] for event in output] == ['subagent/progress', 'subagent/event']
    first = output[1]['params']
    assert first['traceKey'] == 's:agent-0' and first['agentId'] == 'agent-0'
    assert first['parentToolUseId'] == 'Agent_0_x' and first['ancestorTraceKeys'] == []
    assert first['event']['params']['item']['content'] == 'inspect the tests'
    # A subagent of the subagent names its whole ancestry.
    live.observe(frame('turn.started', agent='agent-1', turnId=0, prompt='look deeper'))
    live.observe(frame('subagent.spawned', agent='agent-0', subagentId='agent-1', parentToolCallId='Agent_1_y',
                       parentAgentId='agent-0'))
    nested = output[-1]['params']
    assert nested['traceKey'] == 's:agent-1' and nested['parentToolUseId'] == 'Agent_1_y'
    assert nested['ancestorTraceKeys'] == ['s:agent-0']
    # A subagent whose spawn the stream never showed keeps its steps at the turn's end.
    live.observe(frame('tool.call.started', agent='agent-9', toolCallId='Read_0', name='Read', args={}))
    live.observe(frame('turn.ended', reason='completed'))
    orphan = output[-1]['params']
    assert orphan['traceKey'] == 's:agent-9' and 'ancestorTraceKeys' not in orphan and 'parentTraceKey' not in orphan


def test_kimi_retried_step_keeps_distinct_event_identities():
    """kimi 重试同一步时两次尝试的事件使用不同 ID

    :design-point:"""
    output = []
    live = KimiLiveItems('s', output.append)
    live.observe(frame('turn.started'))
    for seq, text in [(10, 'first attempt'), (14, 'retry')]:
        live.observe({**frame('turn.step.started', step=1), 'seq': seq})
        live.observe(frame('thinking.delta', delta=text))
        live.observe(frame('tool.call.started', toolCallId=str(seq), name='Read', args={}))
    items = [event['params']['item'] for event in output if event['method'] == 'item/completed']
    assert len({item['id'] for item in items}) == 2


def test_kimi_gap_or_join_never_finalizes_partial_text_but_keeps_tool_outcomes():
    """kimi 流出现缺口或中途加入时不把残缺文本当回复，工具结果照常保留

    :design-point:"""
    for joining in [False, True]:
        out=[];live=KimiLiveItems('s',out.append,joining=joining)
        if not joining:
            live.observe(frame('assistant.delta',delta='lost-prefix'))
            live.observe({'type':'portal.stream_gap'})
        live.observe(frame('assistant.delta',delta='tail'))
        live.observe(frame('tool.result',toolCallId='c',output='failure',isError=True))
        live.observe(frame('turn.step.completed',step=1))
        live.observe(frame('turn.ended',reason='completed'))
        items=[e['params']['item'] for e in out if e['method']=='item/completed']
        assert [i['type'] for i in items]==['toolCall']
        assert live.response==''
        live.observe(frame('turn.step.started',step=2))
        live.observe(frame('assistant.delta',delta='complete new step'))
        live.observe(frame('turn.ended',reason='completed'))
        assert live.response=='complete new step'


def test_kimi_websocket_handshake_subscribes_before_ready_and_keeps_fast_tools():
    """kimi WebSocket 握手在就绪前完成订阅，不丢失紧随其后的工具结果

    :design-point:"""
    # Real local TCP + WebSocket frames, no provider or production server.
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    stopped=threading.Event();received=[];failures=[]
    def send(conn, obj):
        data=json.dumps(obj).encode(); conn.sendall(bytes([0x81,len(data)])+data if len(data)<126 else bytes([0x81,126])+struct.pack('!H',len(data))+data)
    def server():
        try:
            conn,_=listener.accept();conn.settimeout(5)
            with conn:
                header=b''
                while b'\r\n\r\n' not in header:header+=conn.recv(1)
                assert header.startswith(b'GET /api/v1/ws HTTP/1.1')
                key=next(x.split(b': ',1)[1] for x in header.split(b'\r\n') if x.startswith(b'Sec-WebSocket-Key:'))
                accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                def exact(n):
                    out=b''
                    while len(out)<n:out+=conn.recv(n-len(out))
                    return out
                h=exact(2);n=h[1]&127
                if n==126:n=struct.unpack('!H',exact(2))[0]
                mask=exact(4);data=exact(n);hello=json.loads(bytes(b^mask[i%4] for i,b in enumerate(data)))
                received.append(hello)
                send(conn,{'type':'ack','id':'portal-subscribe','code':0,'payload':{'accepted_subscriptions':['s']}})
                send(conn,{**frame('tool.result',toolCallId='fast',output='ok'), 'seq':1})
                stopped.wait(5)
        except BaseException as e:failures.append(e)
    worker=threading.Thread(target=server);worker.start()
    stream=KimiLiveStream(f'http://127.0.0.1:{listener.getsockname()[1]}/api/v1','s')
    try:
        event=stream.frames.get(timeout=3)
        assert event['payload']['toolCallId']=='fast'
        assert received[0]['payload']['subscriptions']==['s']
    finally:
        stopped.set();stream.close();worker.join(5);listener.close()
    assert not failures


def test_claude_server_tool_child_uses_normal_live_tool_contract():
    """claude 子 agent 的服务端工具调用和失败结果按普通实时工具事件投影

    :design-point:"""
    from tests.cases.claude_sdk_support import load_claude_module, sdk_of
    daemon=load_claude_module('claude_code_daemon'); sdk=sdk_of(daemon)
    msg=sdk.AssistantMessage(model='fixture',parent_tool_use_id='spawn',uuid='m',content=[
        sdk.ServerToolUseBlock(id='call',name='web_search',input={'query':'fixture'}),
        sdk.ServerToolResultBlock(tool_use_id='call',content={'type':'web_search_tool_result_error','error_code':'unavailable'}),
    ])
    events=daemon._subagent_events(msg,'turn',1)
    assert len(events)==2
    assert events[0]['params']['event']['params']['item']['id']=='call'
    assert events[1]['params']['event']['params']['item']['status']=='failed'


def test_live_child_items_reach_canonical_store_without_entering_parent_context(tmp_path):
    """实时子 agent 条目写入其自身 trace 而不进入父 agent 的 context，系统输入保留结构

    :design-point:"""
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.session_manager import RuntimeSession
    from management_portal.execution.backends import BackendInvocation
    store=AgentDataStore()
    request=ExecutionRequest.new(actor="sample_admin",prompt="inspect",service_type="agent_session",provider="kimi",operation="turn",
        agent_id="resident_live_trace_test",session=SessionOptions(mode="persistent",binding_id="resident:resident_live_trace_test"))
    session=RuntimeSession(mode="persistent",binding_id=request.session.binding_id,backend_session_id="s",backend_route_id="",is_new_backend_session=False)
    projector=AgentDataProjector(store);projector.begin(request,session)
    projector.observe_invocation(request.execution_id,BackendInvocation(request=request,session=session,user_prompt="inspect",system_context="",attach_system_context=False,is_new_backend_session=False))
    before=store.session_context_ids(store.latest_provider_session_ref(request.agent_id))
    def emit(event):
        projector.observe(provider="kimi",backend_route_id="",backend_session_id="s",execution_id=request.execution_id,event=event)
    live=KimiLiveItems("s",emit)
    for ev in [frame("turn.started",agent="child"),
               frame("subagent.spawned",subagentId="child",parentToolCallId="spawn",parentAgentId="main"),
               frame("tool.call.started",agent="child",toolCallId="c",name="Read",args={"path":"fixture"}),
               frame("tool.result",agent="child",toolCallId="c",output={"text":"same text"},isError=True),
               frame("assistant.delta",agent="child",delta="same text"),frame("turn.ended",agent="child",reason="completed")]:live.observe(ev)
    child=store.subagent_id(request.agent_id,"s:child")
    assert store.agent(child)["parent_agent_id"]==request.agent_id
    rows=store.read_sequence(store.trace_sequence_id(child))["segments"]
    assert all(row["metadata"]["subagent"]=={"parent_tool_use_id":"spawn","subagent_id":"child"} for row in rows)
    assert len(rows)==2
    assert rows[0]["status"]=="failed" and rows[0]["content"]["result"]["output"]=={"text":"same text"}
    assert rows[1]["content"]=="same text"
    assert all("native_id" not in row["metadata"] for row in rows)
    assert before==store.session_context_ids(store.latest_provider_session_ref(request.agent_id))
    # Provider-rendered system blocks also use the same observer and retain structure.
    emit({"method":"item/completed","params":{"turnId":"p","item":{"id":"system-1","type":"providerInput","role":"system","content":[{"type":"text","text":"actual system"}]}}})
    main=store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
    assert any(row["segment_type"]=="input" and row["metadata"].get("role")=="system" and row["content"]==[{"type":"text","text":"actual system"}] for row in main)


def test_dsh_translation_retains_visible_reasoning_system_and_nontext_tools():
    """dsh 事件翻译保留推理、系统输入和非文本工具结果，跳过被替换的结果和中断的消息

    :design-point:"""
    # The bridge's translate() runs on the events the pinned DSH runtime writes (session format v4).
    import subprocess
    from pathlib import Path
    bridge=Path(__file__).resolve().parents[2]/"management_portal/dsh_runtime/bridge.mjs"
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[1],'utf8');
const a=src.indexOf('  function translate(event, turnId, session'),b=src.indexOf("  // DSH's write tool publishes",a);
const timing={started_at:'2026-10-14T10:00:00Z',finished_at:'2026-10-14T10:00:08Z'};
const ctx=vm.createContext({messageTimings:new Map([['s:1',timing]]),sessionId:'s',handle:{agent:{session:{id:'s',snapshotEvents:()=>[]}}},textOf:parts=>(parts??[]).filter(p=>p.type==='text').map(p=>p.text).join('\n')});
vm.runInContext(src.slice(a,b),ctx);
const out=ctx.translate({seq:1,type:'assistant/message',data:{message:{content:[{type:'reasoning',text:'thought'},{type:'text',text:'answer'}]}}},'t');
assert.equal(out.length,2);assert.equal(out[0].params.item.content[0],'thought');
assert.equal(out[0].params.request_timing,timing);assert.equal(out[1].params.request_timing,timing);
const system=ctx.translate({seq:2,type:'system/message',data:{message:{role:'system',content:[{type:'text',text:'prompt'}]}}},'t');
assert.equal(system[0].params.item.type,'providerInput');
// DSH session format v4: the tool-role message carries the call id and the result.
const toolResult={role:'tool',toolCallId:'c',isError:true,content:[{type:'image',url:'fixture'}]};
const tool=ctx.translate({seq:3,type:'tool/result',surfaceOp:'append',data:{message:toolResult}},'t');
assert.equal(tool.length,1);assert.equal(tool[0].params.item.id,'c');
assert.equal(tool[0].params.item.output[0].type,'image');assert.equal(tool[0].params.item.status,'failed');
// A pruned or rewritten earlier result replaces its node; it is no new tool result.
assert.equal(ctx.translate({seq:5,type:'tool/result',surfaceOp:{op:'replace',startSeq:3,endSeq:3},data:{message:{...toolResult,content:[{type:'text',text:'pruned'}]}}},'t').length,0);
assert.equal(ctx.translate({seq:4,type:'assistant/message',data:{interrupted:true,message:{content:[{type:'text',text:'partial'}]}}},'t').length,0);
"""
    subprocess.run(["node","-e",script,str(bridge)],check=True,capture_output=True,text=True)


def test_the_dsh_bridge_reports_its_tool_calls_in_the_common_shape():
    """DSH bridge 以通用形状报告工具调用

    bridge 对 DSH v4 会话事件的翻译交给 native_tool_events 契约检查：每个调用先开始、再以同一 id 完成。

    :module: execution.providers
    :importance: P1
    :aspects: functional, architecture-conformance
    :design: decision_276105b5d67b
    :design-point:
    """
    # The bridge's own translation of the session events the pinned DSH writes is
    # what the Harness gets; DSH's v4 format once left every tool call without a result.
    import subprocess
    from pathlib import Path
    from tests.cases import capability_contracts
    bridge=Path(__file__).resolve().parents[2]/"management_portal/dsh_runtime/bridge.mjs"
    script=r"""
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[1],'utf8');
const a=src.indexOf('  function translate(event, turnId, session'),b=src.indexOf('  // DSH\'s write tool publishes',a);
const log=[{seq:1,type:'tool/call',surfaceOp:'append',data:{callId:'c1',name:'grep',arguments:'{"pattern":"needle"}'}},
  {seq:2,type:'tool/result',surfaceOp:'append',data:{message:{role:'tool',toolCallId:'c1',isError:false,content:[{type:'text',text:'Found 1 match'}]}}}];
const ctx=vm.createContext({sessionId:'s',handle:{agent:{session:{id:'s',snapshotEvents:()=>log}}},textOf:()=>''});
vm.runInContext(src.slice(a,b),ctx);
process.stdout.write(JSON.stringify(log.flatMap(event=>ctx.translate(event,'t'))));
"""
    events=json.loads(subprocess.run(["node","-e",script,str(bridge)],check=True,capture_output=True,text=True).stdout)
    capability_contracts.check_tool_events(events)
    assert [event["params"]["item"]["tool"] for event in events]==["grep","grep"]


def test_a_file_the_dsh_write_tool_creates_gets_the_mode_of_an_ordinary_create(tmp_path):
    """DSH write 工具新建的文件按目录的普通新建权限共享

    DSH 从 0600 临时文件发布新文件；桥接插件在 operation=create 时把文件权限改成同目录普通新建文件的权限，覆盖已有文件时保留原权限。

    :module: execution.providers
    :importance: P1
    :design: decision_276105b5d67b
    :design-point:
    """
    # DSH publishes a new file from its private 0600 staging file; other Providers' files follow the directory.
    import subprocess
    from pathlib import Path
    bridge=Path(__file__).resolve().parents[2]/"management_portal/dsh_runtime/bridge.mjs"
    script=r"""
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const [bridge,cwd]=process.argv.slice(1);
const src=fs.readFileSync(bridge,'utf8');
const a=src.indexOf('  function shareCreatedFile(event, session) {'),b=src.indexOf('  // DSH runs a subagent',a);
const calls=[];
const ctx=vm.createContext({config:{cwd},process,...fs,...path});
vm.runInContext(src.slice(a,b),ctx);
const write=(id,file,operation,isError=false)=>{
  calls.push({type:'tool/call',data:{callId:id,name:'write',arguments:JSON.stringify({file_path:file})}});
  ctx.shareCreatedFile({seq:calls.length,type:'tool/result',data:{meta:{operation},message:{toolCallId:id,isError}}},{snapshotEvents:()=>calls});
};
const ordinary=fs.openSync(path.join(cwd,'ordinary'),'wx',0o666);fs.closeSync(ordinary);
const expected=fs.statSync(path.join(cwd,'ordinary')).mode&0o777;
fs.writeFileSync(path.join(cwd,'new.md'),'x',{mode:0o600});fs.chmodSync(path.join(cwd,'new.md'),0o600);
write('c1','new.md','create');
assert.equal(fs.statSync(path.join(cwd,'new.md')).mode&0o777,expected);
// An overwrite keeps the file's own mode, a private file stays private.
fs.writeFileSync(path.join(cwd,'secret'),'x',{mode:0o600});fs.chmodSync(path.join(cwd,'secret'),0o600);
write('c2',path.join(cwd,'secret'),'overwrite');
assert.equal(fs.statSync(path.join(cwd,'secret')).mode&0o777,0o600);
assert.deepEqual(fs.readdirSync(cwd).sort(),['new.md','ordinary','secret']);
"""
    work = tmp_path / "tool-created-files"
    work.mkdir()
    result = subprocess.run(["node", "-e", script, str(bridge), str(work)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_codex_joins_a_subagent_thread_its_parent_reports_and_leaves_it_when_done(tmp_path):
    """codex 在父线程报告子 agent 启动时加入其线程、完成后退订，不加入无关线程的子 agent

    :design-point:"""
    # Codex 0.162 sends a client nothing of a subagent's own thread unless it joined it.
    client = CodexAppServerClient(tmp_path / 'unused.sock')
    output, requests = [], []
    joined = threading.Event()
    def request(method, params, **_options):
        requests.append((method, params))
        if method == 'thread/resume':
            joined.set()
            return {'thread': {'id': params['threadId'], 'agentNickname': 'Boyle'}}
        return {}
    client.request = request
    client._turn_waiters['root'] = _TurnWaiter(thread_id='root', turn_id='t', event_callback=output.append)
    def activity(kind, call):
        client._dispatch_notification('item/completed', {'threadId': 'root', 'turnId': 't', 'item': {
            'type': 'subAgentActivity', 'id': call, 'kind': kind, 'agentThreadId': 'child', 'agentPath': '/root/a'}})
    activity('started', 'call_spawn')
    assert joined.wait(5)
    client._dispatch_notification('item/completed', {'threadId': 'child', 'turnId': 'c', 'item': {
        'type': 'agentMessage', 'id': 'm', 'text': 'done'}})
    activity('completed', 'subagent-completed-child')
    for _ in range(50):
        if ('thread/unsubscribe', {'threadId': 'child'}) in requests:
            break
        threading.Event().wait(0.1)
    assert requests == [('thread/resume', {'threadId': 'child', 'excludeTurns': True}),
                        ('thread/unsubscribe', {'threadId': 'child'})]
    children = [ev['params'] for ev in output if ev['method'] == 'subagent/event']
    assert [(ev['traceKey'], ev['parentToolUseId'], ev['event']['method']) for ev in children] == [
        ('child', 'call_spawn', 'thread/started'), ('child', 'call_spawn', 'item/completed')]
    assert children[0]['event']['params']['thread']['agentNickname'] == 'Boyle'
    # A subagent of a thread no turn here follows is not joined.
    activity_for_other = {'threadId': 'other', 'turnId': 'x', 'item': {
        'type': 'subAgentActivity', 'id': 'c2', 'kind': 'started', 'agentThreadId': 'child2'}}
    client._dispatch_notification('item/completed', activity_for_other)
    threading.Event().wait(0.2)
    assert all(params.get('threadId') != 'child2' for _, params in requests)


def test_kimi_live_context_reports_latest_main_request_including_cached_input():
    """kimi 实时 context 用量取主 agent 最近一次请求，并计入缓存读写的输入

    :module: platform.core
    :design-point:
    """
    output = []
    live = KimiLiveItems("s", output.append, prompt_id="p")
    live.context_window = 1000
    for usage in ({"inputOther": 10, "inputCacheRead": 40, "inputCacheCreation": 5}, {"inputOther": 7}):
        live.observe(frame("turn.step.completed", usage=usage))
    live.observe(frame("turn.step.completed", agent="child", usage={"inputOther": 900}))
    samples = [event["params"]["tokenUsage"] for event in output if event["method"] == "thread/tokenUsage/updated"]
    assert samples == [{"context": {"inputTokens": n}, "modelContextWindow": 1000} for n in (55, 7)]
