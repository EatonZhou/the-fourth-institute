"""RunScript host execution, admission and completion contracts (decision_8b510cbd9c58).

:module: spec.design-governance
:design: decision_8b510cbd9c58
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.society_service.script_runs import runner, runtime, service, store, worker, events
from management_portal.society_service import lineage_aggregate


@pytest.fixture
def work(tmp_path):
    return tmp_path


def execute(work, runtime, code, *, timeout=None, arguments=None):
    suffix = {'python': '.py', 'javascript': '.mjs', 'bash': '.sh'}[runtime]
    script = work / ('script' + suffix)
    script.write_text(code)
    root = work / 'run'
    (root / 'io/artifacts').mkdir(parents=True)
    (root / 'io/input.json').write_text(json.dumps({'caller_id':'resident_aaaaaaaaaaaa', 'arguments': arguments or {}}))
    config = {'runtime':runtime,'path':str(script),'cwd':str(work),
              'timeout_seconds':timeout,'argv':['hello world']}
    calls = []
    def forward(request):
        calls.append(request)
        return {'status':'committed','result':{'caller_id':'resident_aaaaaaaaaaaa'}}
    result = runner.run(config, root, forward=forward)
    return result, root, calls


@pytest.mark.parametrize('runtime,code', [
    ('python', '''import sys, os\nfrom pathlib import Path\nfrom portal_script import inputs,isa,set_output\nx=isa("GetPortalStatus")\nassert x['caller_id']==inputs()['caller_id']\nPath(os.environ['PORTAL_SCRIPT_ARTIFACTS'],'hello.html').write_text('<h1>ok</h1>')\nset_output({'arg':sys.argv[1],'value':inputs()['arguments']['n']})\nprint('日志')\n'''),
    ('javascript', '''import {isa,inputs,setOutput} from 'portal-script';\nimport fs from 'node:fs';\nconst x=await isa('GetPortalStatus');\nif(x.caller_id !== inputs().caller_id) throw Error('wrong caller');\nfs.writeFileSync(process.env.PORTAL_SCRIPT_ARTIFACTS+'/hello.html','<h1>ok</h1>');\nsetOutput({arg:process.argv[2],value:inputs().arguments.n});console.log('日志');\n'''),
    ('bash', '''portal-isa GetPortalStatus\nprintf '<h1>ok</h1>' > "$PORTAL_SCRIPT_ARTIFACTS/hello.html"\nportal-output "arg=$1" value=7\necho 日志\n'''),
])
def test_host_three_runtimes_inputs_argv_artifacts_and_isa(work, runtime, code):
    """三种脚本运行时均可读取参数、写入产物并调用 ISA

    :module: platform.core
    :design-point:
    """
    result, root, calls = execute(work, runtime, code, arguments={'n':7})
    assert result['status'] == 'succeeded', result
    assert result['outputs'] == {'arg':'hello world','value':7}
    assert result['artifacts'][0]['mime_type'] == 'text/html'
    assert calls == [{'instruction':'GetPortalStatus','arguments':{}}]
    assert '日志' in (root / 'stdout.log').read_text()


def test_host_environment_filesystem_and_network(work, monkeypatch):
    """宿主机脚本继承用户环境变量和 HOME，可读写工作目录文件并访问网络

    :module: platform.core
    :design-point:
    """
    import pwd
    sentinel=work/'.ssh';sentinel.mkdir();(sentinel/'test-only').write_text('visible')
    monkeypatch.setenv('RUNSCRIPT_USER_SETTING','inherited')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    port=listener.getsockname()[1]
    def reply():
        client,_=listener.accept()
        with client: client.sendall(b'connected')
    threading.Thread(target=reply,daemon=True).start()
    try:
        result, _, _ = execute(work,'python',f"""import os,socket
from pathlib import Path
from portal_script import set_output
assert os.environ['RUNSCRIPT_USER_SETTING']=='inherited'
assert os.environ['HOME']=={os.environ['HOME']!r}
assert {os.environ['PATH']!r} in os.environ['PATH']
assert Path('.ssh/test-only').read_text()=='visible'
with socket.create_connection(('127.0.0.1',{port}),timeout=2) as s: assert s.recv(20)==b'connected'
Path('result.txt').write_text('saved')
set_output({{'host':True}})
""")
        assert result['status']=='succeeded',result
        assert (work/'result.txt').read_text()=='saved'
    finally: listener.close()


def test_real_timeout_and_invalid_output(work):
    """脚本超过设定的超时时间后被终止并标记为超时

    :design-point:"""
    result, _, _ = execute(work,'python','import time\ntime.sleep(10)\n',timeout=1)
    assert result['status']=='timed_out'
    assert result['duration_seconds']<4


@pytest.mark.parametrize('code', ["import os;open(os.environ['PORTAL_SCRIPT_OUTPUT'],'w').write('[]')", "import os;open(os.environ['PORTAL_SCRIPT_OUTPUT'],'w').write('x'*65537)"])
def test_invalid_output_is_failed(work, code):
    """脚本输出不是 JSON 对象或超过大小上限时运行判为失败

    :design-point:"""
    result, _, _ = execute(work,'python',code)
    assert result['status']=='failed'
    assert result['error_code']=='script.invalid_output'


def test_logs_truncate_and_symlink_artifacts_fail(work):
    """超长日志被截断，产物中含符号链接时运行判为失败

    :design-point:"""
    code="import os;print('x'*11000000);os.symlink('/etc/passwd',os.environ['PORTAL_SCRIPT_ARTIFACTS']+'/leak')"
    result, root, _ = execute(work,'python',code)
    assert result['status']=='failed' and result['error_code']=='script.invalid_artifacts'
    assert result['stdout_truncated'] and (root/'stdout.log').stat().st_size==runner.MAX_LOG


@pytest.mark.parametrize('phase', ['running', 'command', 'spawn'])
def test_cancel_reaches_host_process(work, phase):
    """在运行中或进程启动前后取消脚本都能终止宿主进程并记为已取消

    :module: platform.core
    :design-point:
    """
    script=work/'sleep.py'; script.write_text("import os,time\nfrom pathlib import Path\nPath(os.environ['PORTAL_SCRIPT_ARTIFACTS'],'started').touch()\ntime.sleep(30)")
    root=work/'run';(root/'io/artifacts').mkdir(parents=True);(root/'io/input.json').write_text('{}')
    config={'runtime':'python','path':str(script),'cwd':str(work),'run_dir':str(root),'token':'fake','portal_url':'http://127.0.0.1:1'}
    private=work/'private.json';private.write_text(json.dumps(config))
    if phase == 'running':
        # Start the real runner with this test's interpreter, independently of
        # whether a Release installation exists in the disposable site.
        command=[sys.executable,'-m','management_portal.society_service.script_runs.runner',str(private)]
        ready=root/'io/artifacts/started'
    else:
        # Hold startup on either side of Popen, before runner owns its handle.
        # Cancellation must survive this window even without host CPU pressure.
        probe='''
import sys,time
from pathlib import Path
from management_portal.society_service.script_runs import runner
private,phase,root=sys.argv[1:]
root=Path(root)
owner=runner.runtime if phase=='command' else runner.subprocess
name='command' if phase=='command' else 'Popen'
original=getattr(owner,name)
def gated(*args,**kwargs):
    value=original(*args,**kwargs)
    (root/'ready').touch()
    while not (root/'release').exists(): time.sleep(.01)
    return value
setattr(owner,name,gated)
sys.argv=['runner',private]
runner.main()
'''
        command=[sys.executable,'-c',probe,str(private),phase,str(root)]
        ready=root/'ready'
    process=subprocess.Popen(command)
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.05)
        assert ready.exists(), f'runner did not reach {phase} checkpoint'
        process.terminate()
        (root/'release').touch()
        process.wait(timeout=5)
        assert json.loads((root/'result.json').read_text())['status']=='cancelled'
        assert not private.exists()
        if phase == 'command':
            assert not (root/'io/artifacts/started').exists()
    finally:
        if process.poll() is None: process.kill();process.wait()


@pytest.mark.parametrize('caller', ['sample_admin', 'resident_aaaaaaaaaaaa', 'portal'])
def test_atomic_idempotency_and_exclusion(work, monkeypatch, caller):
    """相同幂等键返回同一运行，相同互斥键在运行中返回已在运行，只启动一次

    :design-point:"""
    script=work/'s.py';script.write_text('pass')
    launched=[]
    monkeypatch.setattr(service,'launch',lambda row,config:launched.append(row))
    token=service.ACTIVATION.set({'lineage_id':'lineage_aaaaaaaaaaaa','effect_id':'effect_a','trigger_type':'time.reached'})
    try:
        first=service.start({'path':str(script),'exclusive_key':'one'},caller,idempotency_key='same',trigger_path_id='run_a')
        again=service.start({'path':str(script),'exclusive_key':'one'},caller,idempotency_key='same')
        exclusive=service.start({'path':str(script),'exclusive_key':'one'},caller,idempotency_key='other')
    finally: service.ACTIVATION.reset(token)
    assert first==again and exclusive['script_run_id']==first['script_run_id']
    assert exclusive['status']=='already_running' and len(launched)==1
    row=store.get(first['script_run_id'])
    assert row['caller_id']==caller
    assert row['trigger_path_id']=='run_a' and row['effect_id']=='effect_a'
    import hashlib
    assert row['sha256']==hashlib.sha256(script.read_bytes()).hexdigest()
    assert 'timeout_seconds' not in row


def test_host_run_parameters_have_no_default_or_maximum_timeout(work):
    """RunScript 超时参数没有默认值和上限，非正整数被拒绝，也没有内存和网络参数

    :module: platform.core
    :design-point:
    """
    from management_portal.hatg.catalog import declaration
    script=work/'.ssh/job.py';script.parent.mkdir();script.write_text('pass')
    assert 'timeout_seconds' not in service._validate({'path':str(script)})
    assert service._validate({'path':str(script),'timeout_seconds':10**9})['timeout_seconds']==10**9
    for value in (0,-1,True,'600'):
        with pytest.raises(ValueError): service._validate({'path':str(script),'timeout_seconds':value})
    names={p.name for p in declaration('RunScript').parameters}
    assert not names & {'memory_mb','network'}


def test_completed_call_summary_keeps_script_run_id():
    """Effect 脚本调用摘要保留 RunScript 返回的 script_run_id

    :design-point:"""
    from management_portal.society_service.lineage_effect_script_journal import call_summary
    # record_call keeps the business IDs of a call's result.
    summary=call_summary({'calls':{'c1':{'instruction':'RunScript','ok':True,'result':{'script_run_id':'script_run_one'},'business_ids':{'script_run_id':'script_run_one'}}}})
    assert summary[0]['script_run_id']=='script_run_one'
    assert call_summary({'calls':{'c1':summary[0]}})[0]['script_run_id']=='script_run_one'


@pytest.mark.parametrize('caller,initiated_by', [('sample_admin', ''), ('resident_aaaaaaaaaaaa', 'sample_admin'), ('portal', '')])
def test_script_events_filter_outcome_keep_run_and_identity(monkeypatch, caller, initiated_by):
    """脚本结束事件按结果筛选事件源并保留运行 ID 和调用身份，失败通知按 Effect 合并

    :design-point:"""
    record={'script_run_id':'script_run_one','status':'failed','lineage_id':'lineage_aaaaaaaaaaaa','effect_id':'effect_b','trigger_path_id':'run_original','caller_id':'sample_admin','outputs':{'n':1},'path':'/a.py','duration_seconds':2,'finished_at':service.now(),'trigger_type':'time.reached'}
    from management_common.principal import OperationIdentity
    record.update(caller_id=caller, identity=OperationIdentity.for_actor(caller, initiated_by=initiated_by).to_dict())
    sources={key:{'event_type':'script.finished','outcome':outcome,'subject_effect_ids':['effect_a','effect_b']} for key,outcome in [('any','any'),('ok','succeeded'),('bad','failed')]}
    monkeypatch.setattr(lineage_aggregate,'read_lineage_state',lambda _: {'program': {'components': [
        {'id': 'effect_b', 'kind': 'effect', 'config': {'failure_recipients': ['lineage-admin']}}
    ]}})
    monkeypatch.setattr(events.lineage_program_queries,'_compiled_program',lambda _: {'event_sources':sources})
    dispatch=[]; notices=[]
    monkeypatch.setattr(events.lineage_domain,'dispatch_lineage_program_event',lambda *args,**kwargs:dispatch.append((args,kwargs)))
    from management_portal.society_service import lineage_failure_notifications as failures
    from management_portal.society_service import effect_failure_notifications as recipients
    monkeypatch.setattr(recipients, 'get_portal_access_store', lambda: SimpleNamespace(
        user=lambda username: {'configured': username == 'lineage-admin', 'status': 'active'}))
    monkeypatch.setattr(failures.lineage_human_notifications,'create_notification',lambda *args,**kwargs:notices.append(kwargs))
    events.publish(record)
    assert {d[0][1] for d in dispatch}=={'any','bad'}
    assert all(d[0][2].payload['cause']['id']=='script_run_one' and d[0][3]==caller for d in dispatch)
    assert all(d[0][2].identity==record['identity'] for d in dispatch)
    assert all(d[0][2].payload['script_run_id']=='script_run_one' and d[0][2].payload['exit_code'] is None for d in dispatch)
    assert notices[0]['recipients']==['lineage-admin']
    events.publish({**record,'script_run_id':'script_run_two'})
    assert notices[0]['notification_id']==notices[1]['notification_id']


def test_multiple_effect_watch_compiles_and_outcome_validates():
    """事件源可同时监视多个 Effect 的脚本结束事件，并编译出结果条件

    :design-point:"""
    from management_portal.society_service import (
        lineage_program_bindings, lineage_program_compilation,
    )
    graph={'components':[{'id':'effect_aaaaaaaaaaaa','kind':'effect','config':{'script':'GetPortalStatus'}},{'id':'effect_bbbbbbbbbbbb','kind':'effect','config':{'script':'GetPortalStatus'}},{'id':'event_source_cccccccccccc','kind':'event_source','config':{'event_type':'script.finished','outcome':'failed'}}],'wires':[]}
    lineage_program_bindings.reconcile_event_source_watch_binding(graph,'event_source_cccccccccccc',{'kind':'effect','ids':['effect_aaaaaaaaaaaa','effect_bbbbbbbbbbbb']},lineage_resident_ids=[])
    plan=lineage_program_compilation.compile_program(graph,[])
    source=plan['event_sources']['event_source_cccccccccccc']
    assert source['subject_effect_ids']==['effect_aaaaaaaaaaaa','effect_bbbbbbbbbbbb']
    assert source['outcome']=='failed'


@pytest.mark.parametrize('timeout', [None, 60])
def test_per_run_bearer_is_scoped_expires_and_revokes(work, monkeypatch, timeout):
    """每次脚本运行的令牌只能用于指令调用，租约文件删除或过期后失效

    :design-point:"""
    from management_portal import agent_token_auth as auth
    lease=work/'lease.json';lease.write_text('{}')
    token='test-only-token'
    cfg={'token_hash':auth.hash_token(token),'token_lookup':auth.token_lookup(token),'enabled':True,
         'managed_by':'portal_script_run','caller_id':'sample_admin','required_scope':'instructions',
         'expires_at':time.time()+timeout if timeout is not None else None,'lease_path':str(lease)}
    class Tokens:
        def candidates(self, lookup): return [('script_run_one',cfg)]
    monkeypatch.setattr(auth,'get_agent_token_store',lambda:Tokens())
    assert auth.authenticate_bearer_token(token) is None
    assert auth.authenticate_bearer_token(token,scope='instructions')['agent_id']=='sample_admin'
    lease.unlink()
    assert auth.authenticate_bearer_token(token,scope='instructions') is None
    lease.write_text('{}');cfg['expires_at']=time.time()-1
    assert auth.authenticate_bearer_token(token,scope='instructions') is None


def test_runner_revokes_lease_before_publishing_terminal_result(work, monkeypatch):
    """脚本 runner 先撤销运行凭证再写出最终结果

    :module: platform.core
    :design-point:
    """
    script = work / 'terminal.py'
    script.write_text('pass')
    root = work / 'run'
    (root / 'io/artifacts').mkdir(parents=True)
    (root / 'io/input.json').write_text('{}')
    private = work / 'private.json'
    private.write_text(json.dumps({'runtime': 'python', 'path': str(script),
        'cwd': str(work), 'run_dir': str(root), 'token': 'test-only',
        'portal_url': 'http://127.0.0.1:1'}))
    monkeypatch.setattr(runner.sys, 'argv', ['runner', str(private)])
    publish = runner.atomic_json
    def publish_after_revocation(path, result):
        if path.name == 'result.json':
            assert not private.exists(), 'terminal publication must follow credential revocation'
        publish(path, result)
    monkeypatch.setattr(runner, 'atomic_json', publish_after_revocation)
    runner.main()
    assert json.loads((root / 'result.json').read_text())['status'] == 'succeeded'


def test_real_systemd_unit_finishes_outside_portal(work, monkeypatch):
    """脚本在独立的 systemd 单元中运行完成，不继承服务专用环境变量并清理私有文件

    :design-point:
    :profiles: live.controlled
    """
    script=work/'unit.py';script.write_text('import os,pwd\nfrom portal_script import set_output\nassert os.environ.get("PORTAL_DATABASE_URL") != "service-only-sentinel"\nassert os.environ["HOME"] == pwd.getpwuid(os.getuid()).pw_dir\nset_output({"unit":True})\n')
    root=work/'runs';private=work/'private';private.mkdir()
    monkeypatch.setattr(runtime,'RUN_ROOT',root)
    monkeypatch.setattr(runtime,'PRIVATE_ROOT',private)
    monkeypatch.setattr(service,'_issue_token',lambda *args:'test-no-service-token')
    monkeypatch.setattr(service,'revoke',lambda record: (private/(record['script_run_id']+'.json')).unlink(missing_ok=True))
    identifier='script_run_test_'+str(os.getpid())
    row={'script_run_id':identifier,'unit':'portal-script-test-'+str(os.getpid()),'arguments':{},'caller_id':'sample_admin','lineage_id':'','trigger_path_id':'','effect_id':''}
    config={'path':str(script),'cwd':str(work),'runtime':'python'}
    try:
        # Only the launch sees the service-only value: the database fixture's teardown still needs the real one.
        with mock.patch.dict(os.environ,{'PORTAL_DATABASE_URL':'service-only-sentinel'}):
            service.launch(row,config)
        deadline=time.time()+15
        result=root/identifier/'result.json'
        while not result.exists() and time.time()<deadline: time.sleep(.1)
        assert result.exists()
        value=json.loads(result.read_text())
        assert value['status']=='succeeded',value
        assert value['outputs']=={'unit':True}
        assert not (private/(identifier+'.json')).exists()
        assert not list((root/identifier).glob('source*'))
    finally:
        subprocess.run(['systemctl','--user','stop',row['unit']],capture_output=True)


@pytest.mark.parametrize('timeout,status', [(None,'lost'), (600,'lost'), (1,'timed_out')])
def test_coordinator_lease_lost_and_retention(work,monkeypatch,timeout,status):
    """协调 worker 把已确认进程树停止的运行记为丢失或超时并通知，保留期满后删除记录和目录

    :design-point:"""
    from datetime import datetime,timezone
    monkeypatch.setattr(runtime,'RUN_ROOT',work)
    revoked=[];published=[]
    monkeypatch.setattr(service,'revoke',lambda row:revoked.append(row['script_run_id']))
    monkeypatch.setattr(events,'publish',lambda row:published.append(row['script_run_id']))
    monkeypatch.setattr(service,'running_state',lambda row:'stopped')
    row={'script_run_id':'script_run_lost','unit':'test','status':'running',
         'started_at':datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat(),'notified':False}
    if timeout is not None: row['timeout_seconds']=timeout
    store.admit(row)
    one,two=worker.Worker(),worker.Worker()
    one.tick();two.tick()
    assert store.get(row['script_run_id'])['status']==status
    assert published==['script_run_lost']
    with store.edit(row['script_run_id']) as saved:
        saved['finished_at']=datetime.fromtimestamp(time.time()-61*86400,timezone.utc).isoformat()
    (work/row['script_run_id']).mkdir()
    one.next_cleanup = 0
    one.tick()
    assert store.get(row['script_run_id']) is None and not (work/row['script_run_id']).exists()


def test_admitted_output_preserves_caller_and_rejects_artifact_traversal(work,monkeypatch):
    """读取脚本运行保留原调用者，按字节偏移读输出，越界的产物路径被拒绝

    :module: platform.core
    :design-point:
    """
    monkeypatch.setattr(runtime,'RUN_ROOT',work)
    identifier='script_run_output';root=work/identifier;(root/'io/artifacts').mkdir(parents=True)
    (root/'stdout.log').write_bytes('中文'.encode())
    store.admit({'script_run_id':identifier,'caller_id':'sample_admin','lineage_id':'lineage_aaaaaaaaaaaa','artifacts':[],'status':'succeeded'})
    assert service.get(identifier,'someone',lineage_id='lineage_aaaaaaaaaaaa')['caller_id'] == 'sample_admin'
    value=service.read_output({'lineage_id':'lineage_aaaaaaaaaaaa','script_run_id':identifier,'stream':'stdout','offset':3,'limit':3},'sample_admin')
    assert value['text']=='文' and value['next_offset']==6 and value['eof']
    with pytest.raises(FileNotFoundError):service.read_output({'lineage_id':'lineage_aaaaaaaaaaaa','script_run_id':identifier,'stream':'artifact','name':'../../input.json'},'sample_admin')


def test_instruction_adapters_are_owned_by_lineage():
    """RunScript 等五条脚本运行指令由 Lineage 服务实现

    :design-point:"""
    from management_portal.hatg.catalog import declaration
    from management_portal.society_service.isa import _handlers
    handlers=_handlers()
    for name in ('RunScript','GetScriptRun','ListScriptRuns','ReadScriptRunOutput','CancelScriptRun'):
        assert name in handlers and declaration(name).owner=='lineage'


def test_run_script_is_judged_on_the_lineage_it_runs_in(monkeypatch):
    """RunScript 按运行所在的 Lineage 判定权限

    入口判定用的 Lineage 与脚本运行所属的 Lineage 相同：Effect 内取激活的 Lineage，Resident 省略 lineage_id 时取其所在 Lineage，
    User 省略时以缺少 lineage_id 拒绝，不会落到其他节点或免检；与激活不符的 lineage_id 被拒绝；记录的调用保持调用者给出的参数。

    :design-point:"""
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service import isa
    from management_portal.society_service.isa import common
    monkeypatch.setattr(common, 'lineage_of_resident', lambda resident: {'resident_aaaaaaaaaaaa': 'lineage_aaaaaaaaaaaa'}[resident])
    judged = []

    def authorize(declaration, arguments, context):
        judged.append((declaration.name, dict(arguments)))
        raise PermissionError('judged')

    kernel = InstructionKernel(isa.binding(), store=None, authorize=authorize)

    def run(caller, arguments, activation=None, instruction='RunScript'):
        token = service.ACTIVATION.set(activation or {})
        try:
            result = kernel.execute(InstructionCall(instruction, arguments), InstructionContext(caller=caller, entry='lineage'))
        finally:
            service.ACTIVATION.reset(token)
        return judged.pop()[1] if judged else result.error['message']

    assert run('resident_aaaaaaaaaaaa', {'path': '/a.py'})['lineage_id'] == 'lineage_aaaaaaaaaaaa'
    assert run('resident_aaaaaaaaaaaa', {'path': '/a.py', 'lineage_id': 'lineage_bbbbbbbbbbbb'})['lineage_id'] == 'lineage_bbbbbbbbbbbb'
    assert run('sample_admin', {'path': '/a.py'}, {'lineage_id': 'lineage_cccccccccccc'})['lineage_id'] == 'lineage_cccccccccccc'
    assert run('sample_admin', {'path': '/a.py'}) == 'RunScript 需要有效的所属 lineage_id'
    assert '不符' in run('sample_admin', {'path': '/a.py', 'lineage_id': 'lineage_bbbbbbbbbbbb'}, {'lineage_id': 'lineage_cccccccccccc'})
    assert run('sample_admin', {'lineage_id': 'lineage_bbbbbbbbbbbb', 'script_run_id': 'script_run_one'}, instruction='CancelScriptRun') == {
        'lineage_id': 'lineage_bbbbbbbbbbbb', 'script_run_id': 'script_run_one'}


def test_script_completion_payload_keeps_full_outputs_and_signed_exit():
    """脚本结束事件完整保留大输出和负数退出码

    :design-point:"""
    from management_portal.society_service.lineage_primitives import make_event, normalize_event
    payload={'script_run_id':'script_run_one','exit_code':-9,'duration_seconds':1.25,'outputs':{'data':'x'*64500}}
    event=normalize_event(make_event('script.finished',payload=payload).to_dict())
    assert event.payload==payload


def test_postgres_admission_serializes_parallel_callers(work,monkeypatch):
    """并行提交同一互斥键的脚本时 PostgreSQL 准入只放行一个运行

    :design-point:"""
    from concurrent.futures import ThreadPoolExecutor
    script=work/'once.py';script.write_text('pass')
    launched=[]
    monkeypatch.setattr(service,'launch',lambda row,config:launched.append(row))
    def start(index):
        return service.start({'path':str(script),'lineage_id':'lineage_aaaaaaaaaaaa','exclusive_key':'global'},'sample_admin',idempotency_key=str(index))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(start,range(4)))
    assert len({row['script_run_id'] for row in results})==1
    assert len(launched)==1
    assert store.get(results[0]['script_run_id'])['status']=='running'


def test_relay_http_forward_preserves_run_caller_without_exposing_token(work):
    """脚本 ISA 中继以运行令牌转发且不暴露令牌，Web 重启时重试，服务重启时只重试读指令

    :design-point:"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    observed=[]
    token='private-test-token'
    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path=='/api/instructions/execute'
            observed.append((self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body=json.dumps({'status':'committed','result':{'caller_id':'resident_aaaaaaaaaaaa'}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    target=ThreadingHTTPServer(('127.0.0.1',0),Target)
    thread=threading.Thread(target=target.serve_forever,daemon=True);thread.start()
    import tempfile
    socket_dir = tempfile.TemporaryDirectory(prefix='isa-test-', dir='/tmp')
    path=Path(socket_dir.name)/'relay.sock'
    relay=runner.Relay(path,runner.http_forward({'portal_url':f'http://127.0.0.1:{target.server_port}','token':token}))
    relay_thread=threading.Thread(target=relay.serve_forever,daemon=True);relay_thread.start()
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.connect(str(path));client.sendall(b'{"instruction":"GetPortalStatus","arguments":{}}\n')
            response=json.loads(client.makefile('rb').readline())
        assert response['result']['caller_id']=='resident_aaaaaaaaaaaa'
        assert observed==[('Bearer '+token,{'instruction':'GetPortalStatus','arguments':{}})]
        assert token not in json.dumps(response)
    finally:
        relay.shutdown();relay.server_close();target.shutdown();target.server_close();socket_dir.cleanup()
    # Portal Web restarting: the refused connection is retried and the request arrives once.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    observed.clear();late=[]
    def start_late():
        time.sleep(3);late.append(ThreadingHTTPServer(('127.0.0.1',port),Target));late[0].serve_forever()
    threading.Thread(target=start_late,daemon=True).start()
    try:
        result=runner.http_forward({'portal_url':f'http://127.0.0.1:{port}','token':token})({'instruction':'GetPortalStatus','arguments':{}})
        assert result['status']=='committed' and len(observed)==1
    finally:
        if late:late[0].shutdown();late[0].server_close()
    # The owning service restarting mid-call: reads are asked again, mutations are not.
    calls=[]
    class Restarting(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length'])))['instruction'])
            reply={'status':'committed','result':{}} if len(calls)>1 and calls[-1]=='GetPortalStatus' else {'status':'failed','error':{'code':'instruction.owner_unavailable','retryable':True}}
            body=json.dumps(reply).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Restarting)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        forward=runner.http_forward({'portal_url':f'http://127.0.0.1:{server.server_port}','token':token})
        assert forward({'instruction':'GetPortalStatus','arguments':{}})['status']=='committed' and calls==['GetPortalStatus']*2
        calls.clear()
        assert forward({'instruction':'RunScript','arguments':{}})['error']['code']=='instruction.owner_unavailable' and calls==['RunScript']
    finally:
        server.shutdown();server.server_close()
