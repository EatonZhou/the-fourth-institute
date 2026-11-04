const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const tick=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
function fixture(family=false,canEdit=true){
 const dom=new JSDOM('<div id="nodeMenuReporting"></div><div id="nodeMenuRuntime"></div>',{runScripts:'outside-only'}),w=dom.window;
 const calls=[],state={nodeMenuContext:{residentId:family?'facade':'resident',node:{},family:family?{}:null}};
 let reporting={initiative:'medium',quality:'low'},revision=7,writeError=null,readError=false;
 const tag=(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'');
 w.PortalI18n={t:String,html:tag,text:tag};w.PortalUIPrimitives={randomUUID:()=>String(calls.length)};
 w.PortalChatWorkspace={residentRuntimeSettingRows:()=>[]};
 w.PortalResidentRuntimeController={bind:()=>({load:async()=>{}})};
 w.PortalResidentProjection={invalidate(){}};
 w.PortalResidentService={
  projection:async()=>({allows:{UpdateResidentSettings:canEdit}}),
  runtimeSettings:async id=>{calls.push({read:id});if(readError)throw Error('read offline');return {settings:{reporting:{...reporting}},settings_revision:revision};},
  updateRuntimeSettings:async(id,changes,key,expected)=>{calls.push({id,changes,key,expected});if(writeError)throw writeError;
   assert.equal(expected,revision);reporting={...changes.reporting};revision++;return {settings_revision:revision};},
 };
 w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_menu_runtime.js'),'utf8'));
 const runtime=w.PortalResidentMenuRuntime.createRuntime({$id:id=>w.document.getElementById(id),esc:String,menuState:state,nodeMenuNotice(){},renderFamilySettings(){calls.push({family:true});}});
 return {w,dom,calls,state,runtime,setServer:(value,version)=>{reporting=value;revision=version;},setWriteError:e=>{writeError=e;},setReadError:v=>{readError=v;},get reporting(){return reporting;},async load(){await runtime.loadNodeMenuRuntime();await tick();},get form(){return w.document.querySelector('form');},async save(){this.form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();}};
}
for(const family of [false,true]) test(`${family?'Family facade':'Resident'} consumes reporting and saves independent choices with CAS/readback`,async()=>{
 const f=fixture(family);await f.load();
 assert.equal(f.form.elements.initiative.value,'medium');assert.equal(f.form.elements.quality.value,'low');
 assert.deepEqual([...f.form.elements.initiative.options].map(o=>o.value),['off','low','medium','high']);
 assert.deepEqual([...f.form.elements.quality.options].map(o=>o.value),['low','medium','high']);
 f.form.elements.initiative.value='off';f.form.elements.quality.value='high';await f.save();
 const write=f.calls.find(c=>c.changes);assert.equal(write.id,f.state.nodeMenuContext.residentId);assert.equal(write.expected,7);
 assert.deepEqual(JSON.parse(JSON.stringify(write.changes)),{reporting:{initiative:'off',quality:'high'}});
 assert.equal(f.calls.filter(c=>c.read).length,2);assert.equal(f.form.elements.quality.disabled,false);
 assert.match(f.form.textContent,/用户明确要求的汇报仍会执行/);
 assert.match(f.form.textContent,/高主动性也按实际价值判断/);
 assert.match(f.form.textContent,/独立 Resident 审查与修改直至达标/);
 f.form.elements.initiative.value='high';await f.save();assert.equal(f.calls.filter(c=>c.changes).at(-1).expected,8);
 f.dom.window.close();
});
test('permission rejection preserves draft and does not claim saved',async()=>{
 const f=fixture();await f.load();f.setWriteError(Object.assign(Error('permission denied'),{status:403}));
 f.form.elements.quality.value='high';await f.save();
 assert.equal(f.form.elements.quality.value,'high');assert.equal(f.form.elements.quality.disabled,false);
 assert.equal(f.form.querySelector('[data-reporting-status]').textContent,'permission denied');
 assert.equal(f.calls.filter(c=>c.changes).length,1);assert.equal(f.calls.filter(c=>c.read).length,1);f.dom.window.close();
});
test('read-only view, conflict and switching menus retain the correct target',async()=>{
 const readOnly=fixture(true,false);await readOnly.load();assert.equal(readOnly.form.elements.quality.disabled,true);await readOnly.save();assert.equal(readOnly.calls.some(c=>c.changes),false);readOnly.dom.window.close();
 const f=fixture();await f.load();f.setWriteError(Object.assign(Error('settings conflict'),{status:409}));f.form.elements.initiative.value='low';await f.save();
 assert.equal(f.form.elements.initiative.value,'low');assert.match(f.form.textContent,/settings conflict/);
 const prior=f.form;f.state.nodeMenuContext={residentId:'other',node:{}};await f.load();assert.notEqual(f.form,prior);assert.equal(f.calls.at(-1).read,'other');f.dom.window.close();
});
test('committed write with failed read retries only the read',async()=>{
 const f=fixture();await f.load();f.setReadError(true);f.form.elements.quality.value='medium';await f.save();
 assert.equal(f.calls.filter(c=>c.changes).length,1);assert.equal(f.form.elements.quality.disabled,true);assert.match(f.form.textContent,/设置已提交/);
 f.setReadError(false);await f.save();assert.equal(f.calls.filter(c=>c.changes).length,1);assert.equal(f.form.elements.quality.disabled,false);assert.equal(f.form.elements.quality.value,'medium');f.dom.window.close();
});
test('resident service forwards reporting through the existing ISA revision contract',async()=>{
 const {window:w}=new JSDOM('',{runScripts:'outside-only'}),calls=[];
 w.PortalAPI={get:async()=>({}),post:async()=>({})};w.PortalUIPrimitives={randomUUID:()=> 'key'};
 w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_service.js'),'utf8'));
 const client=w.PortalResidentService.createClient({execute:async(name,args,controls)=>{calls.push({name,args,controls});return {};}});
 await client.runtimeSettings('facade');
 await client.updateRuntimeSettings('facade',{reporting:{initiative:'off',quality:'high'}},'report-key','sha256:old');
 assert.equal(calls[0].name,'GetResidentSettings');
 assert.equal(calls[1].name,'UpdateResidentSettings');
 assert.equal(calls[1].args.resident_id,'facade');assert.equal(calls[1].args.expected_revision,'sha256:old');
 assert.equal(calls[1].controls.idempotencyKey,'report-key');
 assert.deepEqual(calls[1].args.changes,{reporting:{initiative:'off',quality:'high'}});w.close();
});
test('CAS conflict recovery preserves draft, reads latest and retries only after explicit choice',async()=>{
 const f=fixture(true);await f.load();
 f.form.elements.initiative.value='off';f.form.elements.quality.value='high';
 f.setServer({initiative:'low',quality:'medium'},8);f.setWriteError(Object.assign(Error('conflict'),{status:409}));await f.save();
 assert.equal(f.calls.filter(c=>c.changes).length,1);assert.equal(f.state.nodeMenuContext.reportingRevision,7);
 assert.equal(f.form.elements.initiative.value,'off');assert.equal(f.form.elements.quality.value,'high');
 const conflict=f.form.querySelector('[data-reporting-conflict]');assert.match(conflict.textContent,/当前已保存：主动性 低；质量 中/);
 await f.save();assert.equal(f.calls.filter(c=>c.changes).length,1,'ordinary save cannot silently raise the old precondition');
 f.setWriteError(null);[...conflict.querySelectorAll('button')].find(b=>b.textContent.includes('保留当前编辑')).click();await tick();
 const writes=f.calls.filter(c=>c.changes);assert.equal(writes.length,2);assert.equal(writes[1].expected,8);assert.notEqual(writes[0].key,writes[1].key);
 assert.equal(f.reporting.initiative,'off');assert.equal(f.reporting.quality,'high');f.w.close();
});
test('CAS conflict recovery retries failed reads and can explicitly load current values without writing',async()=>{
 const f=fixture();await f.load();f.form.elements.quality.value='high';
 f.setServer({initiative:'high',quality:'medium'},9);f.setWriteError(Object.assign(Error('conflict'),{status:409}));f.setReadError(true);await f.save();
 assert.equal(f.form.elements.quality.value,'high');assert.equal(f.state.nodeMenuContext.reportingRevision,7);
 let conflict=f.form.querySelector('[data-reporting-conflict]');assert.match(conflict.textContent,/重新读取最新设置/);
 f.setReadError(false);conflict.querySelector('button').click();await tick();
 [...conflict.querySelectorAll('button')].find(b=>b.textContent.includes('加载最新设置')).click();
 assert.equal(f.form.elements.quality.value,'medium');assert.equal(f.form.elements.initiative.value,'high');
 assert.equal(f.state.nodeMenuContext.reportingRevision,9);assert.equal(f.calls.filter(c=>c.changes).length,1);
 assert.equal(f.form.querySelector('[type=submit]').disabled,false);assert.equal(conflict.hidden,true);f.w.close();
});
