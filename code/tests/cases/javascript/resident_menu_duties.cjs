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
  program:{components:[{id:'event_1',kind:'event_source',title:'目标完成一轮'}],wires:[{id:'wire_1',kind:'watch',source:'event_1',target:'resident_a'}]},
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
  throw Error(name);
 }};
 w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_menu_duties.js'),'utf8'));
 const state={lineage:{lineage_id:'lineage_1'},nodeMenuContext:{residentId:'resident_a'}};
 const part=w.PortalResidentMenuDuties.createDuties({$id:id=>w.document.getElementById(id),esc:String,menuState:state,
  changed:async detail=>{changes.push(detail);},switchNodeSettingsTab:async tab=>{tabs.push(tab);},toast(){}});
 const root=w.document.getElementById('nodeDutiesRows');
 const items=()=>[...root.querySelectorAll('[data-duty-id]')].map(item=>item.dataset.dutyId);
 const click=async el=>{el.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));await tick();};
 return {w,dom,calls,changes,tabs,root,items,click,load:()=>part.loadNodeDuties()};
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
