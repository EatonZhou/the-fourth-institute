const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const tick=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};

function duty(id,position,description,elements=[]){
 return {id,title:`块 ${id}`,elements,duty:{holder_id:'resident_a',description,category:'common',position,parameters:{}}};
}

function fixture(){
 const dom=new JSDOM('<div id="nodeDutiesRows"></div>',{runScripts:'outside-only'}),w=dom.window;
 const calls=[],changes=[],tabs=[];
 let lineage={lineage_id:'lineage_1',nodes:[{id:'resident_a',title:'A'}],
  program:{components:[{id:'event_1',kind:'event_source',title:'目标完成一轮'}],wires:[{id:'wire_1',kind:'route',source:'event_1',target:'resident_a',config:{invoke_profiles:{agent:{arguments:{prompt:{concat:[{value:'检查目标'},{from:'event.payload.prompt'}],separator:'\n\n'}}}}}}]},
  function_blocks:[duty('fbi_b',1,'第二条'),duty('fbi_a',0,'第一条',[{id:'event_1',kind:'component'},{id:'wire_1',kind:'wire'}]),
   {id:'fbi_c',title:'别人',elements:[],duty:{holder_id:'resident_b',description:'别人的',category:'custom',position:0,parameters:{}}}]};
 const tag=(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'');
 w.PortalI18n={t:String,html:tag,text:tag};
 w.PortalUI={confirm:async()=>true};
 w.PortalInstructionClient={execute:async(name,args)=>{
  calls.push([name,args]);
  if(name==='GetLineage')return {lineage:JSON.parse(JSON.stringify(lineage))};
  if(name==='GetResidentSettings')return {quota_switch:{auto_switch_on_quota:true}};
  if(name==='OrderDuties'){args.function_block_ids.forEach((id,i)=>{lineage.function_blocks.find(b=>b.id===id).duty.position=i;});return {function_block_ids:args.function_block_ids};}
  if(name==='DeleteFunctionBlock'){lineage={...lineage,function_blocks:lineage.function_blocks.filter(b=>b.id!==args.function_block_id)};return {};}
  if(name==='UpdateDuty'){const b=lineage.function_blocks.find(b=>b.id===args.function_block_id);b.duty={...b.duty,description:args.description,memory:{level:args.memory_level,prompt:args.prompt}};return {function_block:b};}
  if(name==='CreateDuty'){if(args.prompt==='失败')throw Error('不行');const b={id:'fbi_new',title:args.description,elements:[],duty:{holder_id:args.holder_id,description:args.description,category:'custom',position:9,parameters:{},memory:{level:args.memory_level,prompt:args.prompt}}};lineage.function_blocks.push(b);return {function_block:b};}
  if(name==='PreviewPromptInjection')return {parts:[
   {id:'core_rules',label:'Portal 核心规则',timing:'session_start_and_after_compaction',content:'规则正文'},
   {id:'runtime_identity',label:'运行身份',timing:'every_turn',content:'resident_id: resident_a'},
   {id:'duty_memory',label:'职责清单与说明',timing:'session_start_and_after_compaction',content:'# 你的职责\n1. 第一条'},
   {id:'duty_memory_turn',label:'职责约束（每轮）',timing:'every_turn',content:'职责约束：\n- x'}]};
  throw Error(name);
 }};
 w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_menu_duties.js'),'utf8'));
 const state={lineage:{lineage_id:'lineage_1'},nodeMenuContext:{residentId:'resident_a'}};
 const part=w.PortalResidentMenuDuties.createDuties({$id:id=>w.document.getElementById(id),esc:String,menuState:state,
  changed:async detail=>{changes.push(detail);},switchNodeSettingsTab:async tab=>{tabs.push(tab);},toast(){}});
 const root=w.document.getElementById('nodeDutiesRows');
 const items=()=>[...root.querySelectorAll('[data-duty-id]')].map(item=>item.dataset.dutyId);
 const click=async el=>{el.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));await tick();};
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();};
 return {w,dom,calls,changes,tabs,root,items,click,submit,part,state,load:()=>part.loadNodeDuties()};
}

test('lists only the Resident\'s duties in order, with what each one connects',async()=>{
 const f=fixture();await f.load();
 assert.deepEqual(f.items(),['fbi_a','fbi_b']);
 assert.match(f.root.textContent,/第一条/);assert.doesNotMatch(f.root.textContent,/别人的/);
 const first=f.root.querySelector('[data-duty-id="fbi_a"]');
 assert.match(first.textContent,/目标完成一轮/);assert.match(first.textContent,/目标完成一轮 → A/);
 assert.equal(first.querySelector('[data-duty-move="-1"]').disabled,true);
 const system=f.root.querySelector('.residentMenuSystemDuties');
 assert.equal(system.open,false,'system duties start folded');
 assert.match(system.textContent,/额度不足时自动切换账号/);assert.match(system.textContent,/已开启/);
 await f.click(system.querySelector('[data-duty-open-settings]'));assert.deepEqual(f.tabs,['display']);
 f.dom.window.close();
});

test('moving and removing a duty go through OrderDuties and DeleteFunctionBlock',async()=>{
 const f=fixture();await f.load();
 const plain=value=>JSON.parse(JSON.stringify(value));
 await f.click(f.root.querySelector('[data-duty-id="fbi_a"] [data-duty-move="1"]'));
 assert.deepEqual(plain(f.calls.find(c=>c[0]==='OrderDuties')[1]),{lineage_id:'lineage_1',holder_id:'resident_a',function_block_ids:['fbi_b','fbi_a']});
 assert.deepEqual(f.items(),['fbi_b','fbi_a']);
 await f.click(f.root.querySelector('[data-duty-id="fbi_a"] [data-duty-remove]'));
 assert.deepEqual(plain(f.calls.find(c=>c[0]==='DeleteFunctionBlock')[1]),{lineage_id:'lineage_1',function_block_id:'fbi_a'});
 assert.deepEqual(f.items(),['fbi_b']);
 assert.deepEqual(f.changes.map(c=>c.type),['duties','duties']);
 f.dom.window.close();
});

test('a duty\'s prompt is edited in place and a prompt duty is added with its level',async()=>{
 const f=fixture();await f.load();
 const plain=value=>JSON.parse(JSON.stringify(value));
 const levels=id=>[...f.root.querySelector(`[data-duty-id="${id}"] select`).options].map(o=>o.value);
 await f.click(f.root.querySelector('[data-duty-id="fbi_a"] [data-duty-edit-open]'));
 assert.deepEqual(levels('fbi_a'),['trigger','turn','session','on_demand'],'its wire triggers the holder');
 const form=f.root.querySelector('[data-duty-id="fbi_a"] form[data-duty-edit]');
 form.elements.prompt.value='  先读记录  ';form.elements.memory_level.value='session';
 await f.submit(form);
 assert.deepEqual(plain(f.calls.find(c=>c[0]==='UpdateDuty')[1]),{lineage_id:'lineage_1',function_block_id:'fbi_a',description:'第一条',prompt:'先读记录',memory_level:'session'});
 const first=f.root.querySelector('[data-duty-id="fbi_a"]');
 assert.equal(first.querySelector('details').open,true);assert.equal(first.querySelector('form'),null);
 assert.match(first.textContent,/提示词 · 会话开始和压缩后/);assert.match(first.textContent,/先读记录/);
 await f.click(f.root.querySelector('[data-duty-id="fbi_b"] [data-duty-edit-open]'));
 assert.deepEqual(levels('fbi_b'),['turn','session','on_demand'],'no wire, no trigger-time prompt');

 const create=f.root.querySelector('form[data-duty-create]');
 create.elements.description.value='失败的';create.elements.prompt.value='失败';create.elements.memory_level.value='turn';
 await f.submit(create);
 const kept=f.root.querySelector('form[data-duty-create]');
 assert.equal(kept.elements.prompt.value,'失败','a failed add keeps what was typed');
 assert.equal(kept.closest('details').open,true);
 kept.elements.prompt.value='回复用中文';
 await f.submit(kept);
 assert.deepEqual(plain(f.calls.filter(c=>c[0]==='CreateDuty').at(-1)[1]),{lineage_id:'lineage_1',holder_id:'resident_a',description:'失败的',prompt:'回复用中文',memory_level:'turn'});
 assert.deepEqual(f.items(),['fbi_a','fbi_b','fbi_new']);
 f.dom.window.close();
});

test('the Memory tab lists what enters the context by level and opens duties',async()=>{
 const f=fixture();
 f.dom.window.document.body.insertAdjacentHTML('beforeend','<div id="nodeMemoryRows"></div>');
 f.w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_menu_memory.js'),'utf8'));
 const focused=[],tabs=[];
 const memory=f.w.PortalResidentMenuMemory.createMemory({$id:id=>f.w.document.getElementById(id),esc:String,menuState:f.state,
  focusNodeDuty:async id=>{focused.push(id);},switchNodeSettingsTab:async tab=>{tabs.push(tab);}});
 const lineage=JSON.parse(JSON.stringify((await f.w.PortalInstructionClient.execute('GetLineage',{})).lineage));
 f.calls.length=0;
 const block=lineage.function_blocks.find(b=>b.id==='fbi_a');block.duty.memory={level:'trigger',prompt:'先读记录'};
 lineage.function_blocks.find(b=>b.id==='fbi_b').duty.memory={level:'on_demand',prompt:'长流程'};
 const execute=f.w.PortalInstructionClient.execute;
 f.w.PortalInstructionClient.execute=async(name,args)=>name==='GetLineage'?{lineage}:execute(name,args);
 await memory.loadNodeMemory();
 const level=name=>f.w.document.querySelector(`[data-memory-level="${name}"]`);
 assert.match(level('trigger').textContent,/目标完成一轮 触发/);assert.match(level('trigger').textContent,/先读记录/);
 assert.match(level('trigger').textContent,/职责“第一条”/);
 assert.match(level('trigger').textContent,/检查目标\n\n（触发事件带来的内容）/);
 assert.match(level('turn').textContent,/运行身份/);assert.doesNotMatch(level('turn').textContent,/职责约束/);
 assert.match(level('session').textContent,/Portal 核心规则/);assert.match(level('session').textContent,/职责清单（平台生成/);
 assert.match(level('on_demand').textContent,/长流程/);
 await f.click(level('on_demand').querySelector('[data-memory-duty]'));assert.deepEqual(focused,['fbi_b']);
 await f.click(level('session').querySelector('[data-memory-harness]'));assert.deepEqual(tabs,['harness']);
 assert.deepEqual(f.calls.map(c=>c[0]),['PreviewPromptInjection']);
 f.dom.window.close();
});
