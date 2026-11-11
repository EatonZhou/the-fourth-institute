import subprocess
import pytest
from management_portal.execution.kimi_runtime_patch import transform, apply
@pytest.fixture
def patched():
    from management_portal.kimi_code_server import DEFAULT_BINARY
    p=DEFAULT_BINARY.resolve()
    if not p.is_file():pytest.skip('native 2.4.1 package absent')
    return transform(p.with_suffix('.portal-upstream').read_text() if p.with_suffix('.portal-upstream').is_file() else p.read_text())
def test_native_syntax_and_policy_concurrency(patched,tmp_path):
    p=tmp_path/'main.mjs';p.write_text(patched)
    subprocess.run(['node','--check',str(p)],check=True,capture_output=True)
    a=patched.index('function isToolActive(');b=patched.index('\nfunction resolveActiveToolNames',a)
    start=patched.index('SessionToolPolicyService = class ');end=patched.index('\n\t};',start)+5
    cls=patched[start:end].replace('SessionToolPolicyService =','const S =',1)
    js='const assert=require("node:assert/strict");class Disposable{};const sessionToolPolicyStateKey="s",STATE_KEY="state.json";'+patched[a:b]+cls+'''
    (async()=>{
      for(const source of ['builtin','user','mcp'])for(const name of ['Bash','FutureTool','mcp__new__future','select_tools'])
        assert.equal(isToolActiveComposed({sessionDisabledTools:['*']},name,source),false);
      assert.equal(isToolActiveComposed({sessionDisabledTools:[],profile:{}},'Bash','builtin'),true);
      let cell={disabledTools:[]};const s=Object.create(S.prototype);
      s.states={get:()=>cell,set:(k,v)=>cell=v};s.ready=Promise.resolve();s.updateQueue=Promise.resolve();
      s.store={set:async()=>{await new Promise(r=>setTimeout(r,5));}};s.changeEmitter={fireAsync:async()=>{}};
      const first=s.setDisabledTools(['*']);const second=s.setDisabledTools([]);
      await first;await assert.rejects(second,/cannot be relaxed/);assert.deepEqual(cell.disabledTools,['*']);
      await s.setDisabledTools(['*']);
    })().catch(e=>{console.error(e);process.exitCode=1;});'''
    result=subprocess.run(['node','-e',js],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
def test_unknown_version_and_integrity(tmp_path,patched):
    with pytest.raises(ValueError):transform('unknown source')
    p=tmp_path/'lib/node_modules/@moonshot-ai/kimi-code/dist/main.mjs';p.parent.mkdir(parents=True)
    from management_portal.kimi_code_server import DEFAULT_BINARY
    original=DEFAULT_BINARY.resolve();backup=original.with_suffix('.portal-upstream')
    p.write_text(backup.read_text() if backup.is_file() else original.read_text())
    p.chmod(0o755)
    apply(tmp_path);apply(tmp_path)
    assert p.read_text()==patched
    assert p.stat().st_mode & 0o777 == 0o755
    p.write_text(patched+'\n// tamper')
    with pytest.raises(ValueError,match='integrity'):apply(tmp_path)

def test_native_receipt_is_applied_and_bound(patched):
    start=patched.index('const portalMetadata =');end=patched.index('if (req.body.skills',start)
    block=patched[start:end]
    js='''const assert=require('node:assert/strict');
      const session_id='session-real',reservation={id:'prompt-real'};
      const req={body:{deny_all_tools:true,metadata:{portal_tool_policy:'forged'}}};
      const resolved={toolPolicy:{getPolicyFingerprint:()=>JSON.stringify([null,null,null,null,null,['*']])}};
    '''+block+'''
      assert.deepEqual(clientMetadata[0].portal_tool_policy,{version:1,policy:'deny_all',session_id,prompt_id:'prompt-real',effective_tools:[],source:'kimi.native.prompt'});
    '''
    r=subprocess.run(['node','-e',js],capture_output=True,text=True);assert r.returncode==0,r.stderr
