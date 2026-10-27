'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const portal = path.resolve(__dirname, '../../../management_portal');
const {chromium} = require(path.join(portal, 'test_framework/tau_runtime/node_modules/playwright'));
const {readEventCategorySelection} = require(path.join(portal,'test_framework/tau_event_category_evidence.js'));
const L = 'lineage_111111111111', E = 'event_source_222222222222';
const report = {scope:'Native HATG template, program/primitive Inspector, manifest, i18n and CSS; local graph/header/runtime dependencies. No business requests.', formal_acceptance:false};

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:1000}});
    const errors=[], requests=[];
    page.on('pageerror', e=>errors.push(e.message));
    page.on('request', r=>requests.push(r.url()));
    await page.setContent('<!doctype html><section id="panel" class="portalHatgPanelComponent" role="region">'+fs.readFileSync(path.join(portal,'hatg-panel.html'),'utf8')+'</section>');
    for (const n of ['portal_design_system','portal_themes','hatg_panel'])
      await page.addStyleTag({content:fs.readFileSync(path.join(portal,'static',n+'.css'),'utf8')});
    await page.addStyleTag({content:'html,body{margin:0;height:100%}#panel{height:100vh}#agentLineageShell{height:100%;display:block}#hatgPane{height:100%;display:flex;flex-direction:column}'});
    await page.evaluate(()=>{window.__PORTAL_LANGUAGE__='en';});
    for (const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','lineage_program','lineage_element_fields','lineage_primitive_inspector','hatg_program_inspector'])
      await page.addScriptTag({content:fs.readFileSync(path.join(portal,'static',n+'.js'),'utf8')});
    const definitions=JSON.parse(fs.readFileSync(path.join(portal,'static/lineage_primitive_definitions.json'),'utf8'));
    await page.evaluate(async ({L,E,definitions})=>{
      // Only the definitions transport is replaced; use the actual native JSON.
      window.PortalAPI={get:async()=>definitions};
      await PortalLineageProgram.loadPrimitiveDefinitions();
      document.getElementById('activeLineageIdentity').innerHTML=`<code data-portal-resource-id="${L}">${L}</code>`;
      const component={id:E,kind:'event_source',title:'Local Event',config:{event_type:'resident.work_finished'}};
      const program={components:[component],wires:[]};
      const state={lineage:{lineage_id:L,revision:1,nodes:[],program},selectedProgramObjectId:E,programMutationInFlight:false,manualProgramEventsInFlight:new Set(),programEventSourceResetsInFlight:new Set()};
      const module=PortalHatgProgramInspector;
      const deps=Object.fromEntries(module.dependencies.map(n=>[n,()=>{throw new Error('Unexpected dependency '+n);} ]));
      Object.assign(deps,{$id:id=>document.getElementById(id),state,esc:PortalUIPrimitives.escapeHtml,definitionRevisions:new WeakMap(),
        displayProgramComponentById:id=>id===E?component:null,programComponentById:id=>id===E?component:null,
        displayProgramWireById:()=>null,displayedProgram:()=>program,refreshFunctionBlockMenu:()=>{},
        normalizeOptionalLimit:PortalLineageProgram.normalizeOptionalLimit,
        programComponentSwitchHtml:()=>'',programResourceAddressHtml:()=>'',programExecutionExplanationHtml:()=>'',
        programEventSourceRuntime:()=>({}),programEventSourceStatus:()=>({label:'等待',reason:''}),
        syncProgramRunAsGuidance:()=>{},manualProgramEventKey:id=>id,setBusy:(n,b)=>{n.disabled=b;},
      });
      const inspector=module.createProgramInspector(deps);
      window.renderLocalInspector=()=>{
        document.getElementById('programInspectorPanel').replaceChildren();
        inspector.renderProgramInspector({panel:document.getElementById('programInspectorPanel'),objectId:E});
      };
      renderLocalInspector();
    },{L,E,definitions});
    const root=page.locator('#panel'), panel=root.locator('#programInspectorPanel');
    const category=panel.locator('[data-primitive-type-category]');
    const specific=panel.locator('[data-primitive-type-item]');
    const read=(options={})=>readEventCategorySelection(root,{lineageId:L,eventId:E,eventType:'manual.triggered',...options});
    assert.equal((await read()).category_ready,false);
    await category.selectOption({label:'Graph and manual control'});
    const old=specific.locator('option[value="resident.work_finished"]');
    report.native={old_option:await old.evaluate(n=>({connected:n.isConnected,hidden:n.hidden,selected:n.selected})),
      category:await category.inputValue(),specific:await specific.inputValue(),snapshot:await specific.ariaSnapshot()};
    assert.deepEqual(report.native.old_option,{connected:true,hidden:true,selected:false});
    assert.equal(report.native.category,'graph_control');
    assert.equal(report.native.specific,'');
    report.category_only=await read();
    assert.equal(report.category_only.category_ready,true,JSON.stringify(report.category_only));
    assert.equal(report.category_only.type_selected,false);
    await specific.selectOption('manual.triggered');
    report.type_selected=await read();
    assert.equal(report.type_selected.category_ready,true);
    assert.equal(report.type_selected.type_selected,true);
    assert.equal((await read({eventId:'event_source_333333333333'})).category_ready,false);
    assert.equal((await read({lineageId:'lineage_333333333333'})).category_ready,false);
    assert.equal((await read({eventType:'unknown.event'})).category_ready,false);
    // Changes below affect only local test DOM. Every rejection must keep the
    // raw candidate values, never coerce a missing field into a ready state.
    await old.evaluate(n=>{n.hidden=false;});
    const unfiltered=await read();assert.equal(unfiltered.category_ready,false);
    assert.equal(unfiltered.candidates[0].inspectors[0].forms[0].primitives[0].pickers[0].types[0].options.find(o=>o.value==='resident.work_finished').hidden,false);
    await old.evaluate(n=>{n.hidden=true;});
    await category.evaluate(n=>{n.disabled=true;});assert.equal((await read()).category_ready,false);
    await category.evaluate(n=>{n.disabled=false;});
    await specific.locator('option[value="manual.triggered"]').evaluate(n=>n.after(n.cloneNode(true)));
    assert.equal((await read()).category_ready,false);
    await specific.locator('option[value="manual.triggered"]').last().evaluate(n=>n.remove());
    await category.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).category_ready,false);
    await category.last().evaluate(n=>n.remove());
    await panel.evaluate(n=>{n.removeAttribute('data-component-id');});
    const missing=await read();assert.equal(missing.category_ready,false);assert.equal(missing.candidates[0].inspectors[0].component_id,null);
    await page.evaluate(()=>renderLocalInspector());
    await category.selectOption('graph_control');
    await panel.evaluate(n=>{n.hidden=true;});assert.equal((await read()).category_ready,false);
    await panel.evaluate(n=>{n.hidden=false;});
    await panel.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).category_ready,false);
    await panel.last().evaluate(n=>n.remove());
    await root.evaluate(n=>{const clone=n.cloneNode(true);clone.id='other-panel';n.after(clone);});
    const duplicate=await readEventCategorySelection(page.locator('.portalHatgPanelComponent'),{lineageId:L,eventId:E,eventType:'manual.triggered'});
    assert.equal(duplicate.category_ready,false);assert.equal(duplicate.candidates.length,2);
    await page.locator('#other-panel').evaluate(n=>n.remove());
    await page.evaluate(()=>{window.nativeProgram=PortalLineageProgram;window.PortalLineageProgram=undefined;});
    const missingManifest=await read();assert.equal(missingManifest.category_ready,false);assert.equal(missingManifest.candidates[0].expected_category,null);
    await page.evaluate(()=>{window.PortalLineageProgram=window.nativeProgram;});
    const handle=await root.elementHandle();
    const forced=await readEventCategorySelection({elementHandles:async()=>[handle,{evaluate:async()=>{throw new Error('local sample unavailable');},dispose:async()=>{}}]},
      {lineageId:L,eventId:E,eventType:'manual.triggered'});
    assert.equal(forced.category_ready,false);assert.equal(forced.candidates.length,1);
    assert.equal(forced.error.phase,'sample');assert.equal(forced.error.name,'Error');assert.ok(forced.error.stack.includes('local sample unavailable'));
    report.error=forced.error;
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    report.status='passed';report.no_business_requests=true;
    if(process.env.TAU_EVENT_CATEGORY_REPORT) fs.writeFileSync(process.env.TAU_EVENT_CATEGORY_REPORT,JSON.stringify(report,null,2)+'\n');
    console.log('Native Event category evidence preserves filtering and exact identity; category readiness is separate from type selection and saving.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
