'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser}=require(path.join(code,'management_portal/test_framework/tau_browser_observer'));
const {observeFiniteInstructionResponses}=require(path.join(code,'management_portal/test_framework/tau_finite_instruction_evidence'));
const {readWorkbenchTabClose}=require(path.join(code,'management_portal/test_framework/tau_tab_close_evidence'));
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const {matchesWatchboardRemoval:matches,readWorkbenchPanelBinding:binding}=require(process.env.TAU_WATCHBOARD_ACTION_MODULE||path.join(code,'management_portal/test_framework/tau_watchboard_action_contract'));
const fixture=JSON.parse(fs.readFileSync(process.env.TAU_WATCHBOARD_PLAN||path.join(__dirname,'watchboard_removal_plan.json'),'utf8'));
const {plan,resident_id:residentId}=fixture;
const expected={lineageId:plan.lineage_id,residentId,componentIds:plan.remove.filter(o=>o.kind==='component').map(o=>o.id),wireIds:plan.remove.filter(o=>o.kind==='wire').map(o=>o.id)};
const args={lineage_id:plan.lineage_id,near_resident_id:residentId,operations:plan.remove};
assert(matches(args,expected));
assert(!args.operations.some(o=>expected.componentIds.includes(o.component_id)),'The original component_id predicate misses this real planner output');
for(const changed of [
  {...args,lineage_id:'lineage_other'}, {...args,near_resident_id:'resident_other'},
  {...args,operations:args.operations.slice(1)}, {...args,operations:[...args.operations,args.operations[0]]},
  {...args,operations:args.operations.map(o=>({...o,component_id:o.id,id:undefined}))},
  {...args,operations:args.operations.map(o=>({...o,op:'create'}))},
  {...args,operations:[...args.operations.slice(1),{op:'delete',kind:'wire',id:'wire_unrelated'}]},
]) assert(!matches(changed,expected));
assert.throws(()=>matches(args,{...expected,componentIds:[]}),/Exact original/);

(async()=>{
  let browser,context,primary,finite;
  const received=[],dir=fs.mkdtempSync(path.join(os.tmpdir(),'tau-watchboard-contract-'));
  const server=http.createServer((req,res)=>{
    if(req.method!=='POST'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><main id="fixture"><section id="lineage-chat-panel"><button id="remove">Remove</button></section><section id="other-panel">Other panel</section></main>');return;}
    let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{const body=JSON.parse(raw);received.push(body);
      res.writeHead(200,{'Content-Type':'application/json'});
      res.end(JSON.stringify({instruction:body.instruction,instruction_id:'inst_lin_fixture',status:'committed',replayed:false,
        result:body.instruction==='ApplyLineageChanges'?{created_ids:[],updated_ids:[],deleted_ids:plan.remove.map(o=>o.id)}:{lineage:{lineage_id:plan.lineage_id,program:{components:[],wires:[]}}}}));});
  });
  try{
    await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});context=await browser.newContext({viewport:{width:1100,height:800}});
    primary=await observeBrowser(context,{evidenceDir:dir});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(origin);
    await page.addStyleTag({content:fs.readFileSync(path.join(code,'management_portal/static/portal_workbench.css'),'utf8')});
    await page.addStyleTag({content:'#fixture{width:900px;height:400px;margin:100px 40px} :root{--portal-workbench-single-tab-opacity:0;--portal-workbench-single-tab-revealed-opacity:1}'});
    await page.evaluate(()=>{window.PortalIdentity={username:()=> 'fixture'};});
    for(const name of ['portal_text.js','portal_ui.js','portal_ui_primitives.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(path.join(code,'management_portal/static')),'portal_resident_service.js'])
      await page.addScriptTag({content:fs.readFileSync(path.join(code,'management_portal/static',name),'utf8')});
    await page.evaluate(({plan,residentId})=>{
      window.PortalWorkbench.registerPanelPackage({type:'local-test',version:1,presentation:{title:'Local',icon:'panel'},sizing:{minWidth:180,minHeight:140},instances:'multiple',capabilities:[],behavior:{canHide:true,canCollapse:true,canMove:true,closable:true},context:{channels:['*']},persistence:{schemaVersion:1},create:({panelId})=>document.getElementById(panelId==='chat'?'lineage-chat-panel':'other-panel')});
      const {stack,split}=window.PortalWorkbench.model;
      window.testWorkbench=window.PortalWorkbench.create({id:'fixture',root:document.getElementById('fixture'),initialEditors:[{id:'chat',type:'local-test',title:'Current Resident'},{id:'other',type:'local-test',title:'Other Resident'}],defaultLayout:split('horizontal',[stack(['chat']),stack(['other'])]),store:{read:()=>({}),replace:v=>v}});
      const client=window.PortalResidentService.createClient({api:{get:async()=>({watchboard:{installation:plan}})},execute:(instruction,args,controls)=>fetch('/api/instructions/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,idempotency_key:controls?.idempotencyKey,expected_version:controls?.expectedVersion})}).then(r=>r.json()).then(r=>r.result)});
      document.getElementById('remove').onclick=()=>{window.originalRemoval=client.updateWatchboard('remove',{resident_id:residentId});};
    },{plan,residentId});
    const panel=page.locator('#lineage-chat-panel');const first=await binding(panel,{domId:'lineage-chat-panel'});
    assert(first.ready);assert.equal(first.descriptor_id,'chat');assert.notEqual(first.descriptor_id,first.dom_id);
    assert.equal(await page.locator('[data-portal-workbench-tab="lineage-chat-panel"]').count(),0,'Original selector cannot find the real tab');
    await assert.rejects(binding(panel,{domId:'chat'}),/Wrong original/);
    const other=await binding(page.locator('#other-panel'),{domId:'other-panel'});assert.notEqual(first.stack_id,other.stack_id);assert.equal(other.descriptor_id,'other');
    await panel.evaluate(n=>n.closest('[data-portal-workbench-panel-frame]').hidden=true);
    assert.equal((await binding(panel,{domId:'lineage-chat-panel'})).ready,false);
    await panel.evaluate(n=>n.closest('[data-portal-workbench-panel-frame]').hidden=false);
    const events=[];
    finite=observeFiniteInstructionResponses(page,{origin,targets:[{instruction:'ApplyLineageChanges',arguments:{lineage_id:plan.lineage_id,near_resident_id:residentId}}],requestId:r=>primary.requestId(r),record:row=>events.push(row)});
    // Capture the exact finite response before inspecting operations, so even a
    // future semantic mismatch cannot erase the committed original receipt.
    const original=page.waitForResponse(r=>{const b=r.request().postDataJSON();return r.url()===origin+'/api/instructions/execute'&&b?.instruction==='ApplyLineageChanges'&&b.arguments?.lineage_id===plan.lineage_id&&b.arguments.near_resident_id===residentId;});
    await page.getByRole('button',{name:'Remove',exact:true}).click();const response=await original;
    const captured=await finite.original(response);assert.equal(captured.body_status,'read');assert.equal(captured.body.status,'committed');assert.equal(captured.body.replayed,false);
    assert.equal(captured.request_id,primary.requestId(response.request()));assert(matches(response.request().postDataJSON().arguments,expected));
    assert.deepEqual(new Set(captured.body.result.deleted_ids),new Set(plan.remove.map(o=>o.id)));await page.evaluate(()=>window.originalRemoval);
    assert.equal(received.filter(b=>b.instruction==='ApplyLineageChanges').length,1);assert.equal(events.filter(e=>e.phase==='request_observed').length,1);
    const stack=panel.locator('xpath=ancestor::section[contains(@class,"portalWorkbenchStack")]');
    const tab=stack.locator(first.tab_selector);await tab.focus();
    await page.waitForFunction(()=>[...document.querySelectorAll('.portalWorkbenchTabs')].every(n=>n.getAnimations({subtree:true}).every(a=>a.playState!=='running'&&!a.pending)));
    assert((await readWorkbenchTabClose(tab,{panelId:first.descriptor_id})).ready);
    await tab.locator('xpath=..').locator(':scope > button.portalWorkbenchTabClose').click();
    assert.equal(await panel.isVisible(),false);assert.equal(await tab.count(),0);assert((await binding(page.locator('#other-panel'),{domId:'other-panel'})).ready);
    await page.evaluate(()=>window.testWorkbench.destroy());assert.deepEqual(errors,[]);
    await finite.close();finite=null;await context.close();context=null;await primary.flush();primary.stop();primary=null;
    console.log(JSON.stringify({status:'passed',isolated_chromium:true,checks:['real installation_plan remove shape through native service','receipt retained before semantic assertions','exact lineage/resident/components/incident-wires','no accepted mutation replay','native DOM root/descriptor distinction','foreign stack and hidden panel guards','original native tab close preserves other panel'],production_calls:0,formal_acceptance:false}));
  }finally{
    try{if(finite)await finite.close({drainMs:1000});}finally{if(context)await context.close();primary?.stop();if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true});}
  }
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
