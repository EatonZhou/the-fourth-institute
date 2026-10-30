'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..'),staticDir=path.join(code,'management_portal/static');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {LINEAGE_GRAPH_SCRIPTS}=require(path.join(code,'tests/cases/javascript/lineage_graph_scripts.cjs'));
const {LINEAGE_TEMPLATES_SCRIPTS}=require(path.join(code,'tests/cases/javascript/lineage_templates_scripts.cjs'));
const {RESIDENT_MENU_SCRIPTS}=require(path.join(code,'tests/cases/javascript/resident_menu_scripts.cjs'));
const {readFunctionBlockMenuReady}=require(path.join(code,'management_portal/test_framework/tau_function_block_menu_evidence'));
const {readFunctionBlockObjectRows}=require(path.join(code,'management_portal/test_framework/tau_function_block_objects_evidence'));
const B='fbi_fixture',E='effect_fixture',V='event_source_fixture',R='resident_fixture',TITLE='Local block';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const report={formal_acceptance:false,fixture:'Native Menu, FamilyBlocks membership projection, GraphView alert markup, graph, i18n/CSS; local graph/runtime/acknowledgement/service data, no business requests'};
 try{
  const p=await browser.newPage({viewport:{width:1200,height:900}}),requests=[],errors=[];
  p.on('request',r=>requests.push(r.url()));p.on('pageerror',e=>errors.push(e.message));
  await p.setContent('<!doctype html><main><div id="hatgCanvas" class="hatgCanvas" style="height:500px;width:700px"><div id="world"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></main>');
  for(const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks','portal_resident_menu'])await p.addStyleTag({path:path.join(staticDir,n+'.css')});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','lineage_element_fields',...RESIDENT_MENU_SCRIPTS,'projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_program',...LINEAGE_TEMPLATES_SCRIPTS,...LINEAGE_GRAPH_SCRIPTS,'hatg_graph_view','hatg_family_blocks','hatg_function_block_menu'])await p.addScriptTag({path:path.join(staticDir,n+'.js')});
  await p.evaluate(({B,E,V,R,TITLE})=>{
   PortalI18n.apply('en');window.calls=[];window.ack=[];
   window.PortalSocietyService={createClient:()=>({})};
   window.PortalInstructionClient={execute:async(name,args)=>{calls.push({name,args});if(name!=='ListUsers')throw new Error('Unexpected instruction '+name);return {users:[]};}};
   const strict=m=>Object.fromEntries(m.dependencies.map(k=>[k,()=>{throw new Error('Unexpected dependency '+k);} ]));
   const block={id:B,kind:'function_block',primitive_kind:'function_block',title:TITLE,x:40,y:40,elements:[{kind:'resident',id:R},{kind:'component',id:E},{kind:'component',id:V}]};
   const components=[{id:E,kind:'effect',title:'Controlled Effect',config:{failure_recipients:[]}},{id:V,kind:'event_source',title:'Manual Event',config:{event_type:'manual.triggered'}}];
   const state={programMutationInFlight:false,lineage:{lineage_id:'lineage_fixture',revision:1,nodes:[{id:R,title:'Member'}],function_blocks:[block],program:{components,wires:[]}}};
   const common={state,esc:PortalUIPrimitives.escapeHtml,functionBlockInstanceById:id=>id===B?block:null};
   const family=PortalHatgFamilyBlocks.createFamilyBlocks({...strict(PortalHatgFamilyBlocks),...common});
   const issue={id:E,kind:'effect',title:'Controlled Effect',error:'Local fixture failure',fingerprint:'new-local-step'};
   const failures=()=>[issue],unseen=()=>[issue];
   const menu=PortalHatgFunctionBlockMenu.createFunctionBlockMenu({...strict(PortalHatgFunctionBlockMenu),...common,
    familyRoles:family.familyRoles,functionBlockElements:family.functionBlockElements,instanceInnerObjects:family.instanceInnerObjects,
    functionBlockInstanceFailures:failures,functionBlockInstanceUnacknowledgedFailures:unseen,
    acknowledgeFunctionBlockFailures:items=>ack.push(items),residentMenuHandlers:()=>({}),
    programEffectRuntime:()=>({status:'failed'}),programEventSourceRuntime:()=>({paused:false}),
    effectRecipients:()=>[],effectRecipientsState:()=> 'off',graphObjectTitle:id=>id===R?'Member':components.find(x=>x.id===id)?.title||id,
    expandedFunctionBlockIds:()=>new Set(),functionBlockInstanceTriggersHtml:()=>''});
   const view=PortalHatgGraphView.createGraphView({...strict(PortalHatgGraphView),esc:common.esc,isFunctionBlockInstance:n=>n.kind==='function_block',
    functionBlockInstanceUnacknowledgedFailures:unseen,currentTemplateController:()=>null,functionBlockSourceTitle:()=> 'Local fixture'});
   const graph=PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',nodeContent:view.graphNodeContent});
   graph.render({nodes:[block],edges:[]});graph.fit({duration:0});
   window.PortalNav={openResidentMenu:options=>PortalResidentMenu.open(options)};
   window.openLocal=(pane='block')=>menu.openFunctionBlockMenu(B,pane);
   window.refreshLocal=()=>menu.refreshFunctionBlockMenu();
  },{B,E,V,R,TITLE});
  await p.evaluate(()=>openLocal());
  const originalIds=[E,V,R],panel=p.locator('[data-function-block-menu-panel]');
  assert.ok((await panel.locator('.functionBlockElementList').innerText()).includes(R),'Resident is in the ordinary block overview elements');
  await p.locator('[data-group-tab="objects"]').click();
  const guard=await readFunctionBlockMenuReady(p,{blockId:B,title:TITLE,pane:'objects'});assert.equal(guard.ready,true);
  const original=await p.evaluate(ids=>Array.from(document.querySelectorAll('[role="dialog"]')).some(e=>e.getClientRects().length&&ids.every(id=>e.textContent.includes(id))),originalIds);
  assert.equal(original,false);assert.equal(await panel.locator('[data-block-object]').count(),2);
  assert.equal((await panel.innerText()).includes(R),false);
  const alert=p.locator(`button[data-function-block-issues="${B}"]`);
  report.baseline={original_ids:originalIds,original_all_ids_in_dialog:original,menu:guard,
   objects_snapshot:await panel.ariaSnapshot(),alert:{label:await alert.getAttribute('aria-label'),count:await alert.innerText(),visible:await alert.isVisible()},acknowledgements:await p.evaluate(()=>ack)};
  assert.equal(report.baseline.alert.visible,true);assert.deepEqual(report.baseline.acknowledgements,[]);
  if(!process.argv.includes('--probe')){
   const expected={blockId:B,title:TITLE,components:[{id:E,kind:'effect'},{id:V,kind:'event_source'}]},read=more=>readFunctionBlockObjectRows(panel,{...expected,...more});
   report.observation=await read();assert.equal(report.observation.rows_ready,true,JSON.stringify(report.observation));
   await assert.rejects(()=>read({components:[{id:R,kind:'resident'}]}),TypeError);
   assert.equal((await read({blockId:'fbi_other'})).rows_ready,false);
   assert.equal((await read({components:[{id:'effect_other',kind:'effect'},{id:V,kind:'event_source'}]})).rows_ready,false);
   const row=panel.locator(`[data-block-object="${E}"]`);
   await row.evaluate(n=>n.removeAttribute('data-object-kind'));const missing=await read();assert.equal(missing.rows_ready,false);assert.equal(missing.candidates[0].rows[0].kind,null);
   await p.evaluate(()=>refreshLocal());
   await row.evaluate(n=>n.hidden=true);assert.equal((await read()).rows_ready,false);
   await p.evaluate(()=>refreshLocal());
   await row.locator('[data-block-object-edit]').evaluate(n=>n.disabled=true);assert.equal((await read()).rows_ready,false);
   await p.evaluate(()=>refreshLocal());
   await row.locator('[data-block-object-select]').evaluate(n=>n.setAttribute('data-block-object-select','effect_other'));assert.equal((await read()).rows_ready,false);
   await p.evaluate(()=>refreshLocal());
   await row.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).rows_ready,false);
   await p.evaluate(()=>refreshLocal());
   await panel.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).rows_ready,false);await panel.last().evaluate(n=>n.remove());
   await p.locator('[data-group-tab="objects"]').evaluate(n=>n.setAttribute('aria-selected','false'));assert.equal((await read()).rows_ready,false);
   await p.locator('[data-group-tab="objects"]').evaluate(n=>n.setAttribute('aria-selected','true'));
   const before={html:await panel.innerHTML(),calls:await p.evaluate(()=>calls),ack:await p.evaluate(()=>ack)};
   assert.equal((await read()).rows_ready,true);assert.equal(await panel.innerHTML(),before.html);assert.deepEqual(await p.evaluate(()=>calls),before.calls);assert.deepEqual(await p.evaluate(()=>ack),before.ack);
   const handle=await panel.elementHandle();const failed=await readFunctionBlockObjectRows({elementHandles:async()=>[handle,{evaluate:async()=>{throw new Error('local lost row observation');},dispose:async()=>{}}]},expected);
   assert.equal(failed.rows_ready,false);assert.equal(failed.candidates.length,1);assert.equal(failed.error.phase,'sample');assert.match(failed.error.stack,/local lost row observation/);
  }
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);assert.deepEqual(await p.evaluate(()=>ack),[]);
  report.status=process.argv.includes('--probe')?'reproduced':'passed';
  if(process.env.TAU_OBJECT_ROWS_REPORT)fs.writeFileSync(process.env.TAU_OBJECT_ROWS_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,formal_acceptance:false,original_all_ids_in_dialog:original,original_alert_retained:true,acknowledgements:await p.evaluate(()=>ack)}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
