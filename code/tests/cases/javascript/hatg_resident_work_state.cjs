'use strict';
// Production state subscription, HATG projection/renderer and D3 graph in Chromium.
// Only API transport and unrelated node features are fixtures; no live mutations.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '../../..'), root = path.join(repo, 'management_portal/static');
const {chromium} = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {LINEAGE_GRAPH_SCRIPTS} = require('./lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES} = require('./hatg_panel_styles.cjs');
const report = {checks:[], errors:[], ok:false};
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1050,height:600}});
    page.on('pageerror', e => report.errors.push(e.message));
    // The state subscription reports a failing listener on the console.
    page.on('console', m => { if (m.type() === 'error') report.errors.push(m.text()); });
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.pathname.startsWith('/static/')) return route.fulfill({body:fs.readFileSync(path.join(root, u.pathname.slice(8))),contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript'});
      return route.fulfill({contentType:'text/html', body:'<!doctype html><html data-portal-theme="light" data-portal-motion="full"><meta charset="utf-8"><body><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1000px;height:550px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div></body></html>'});
    });
    await page.goto('https://hatg-state.fixture.invalid');
    for (const name of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES]) await page.addStyleTag({url:'/static/'+name+'.css'});
    for (const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','resident_protocol',...LINEAGE_GRAPH_SCRIPTS,'hatg_graph_model','hatg_node_status','hatg_graph_view','hatg_resident_runtime']) await page.addScriptTag({url:'/static/'+name+'.js'});
    await page.evaluate(() => {
      PortalI18n.apply('zh-CN');
      const member='resident_fixture', facade='resident_facade';
      window.f={cursor:10,work:{work_state:'queued',work_phase:'',intake_state:'closed',active_message_id:'msg_fixture',execution_gate:{state:'closed',reason:'manual'}},calls:[],patches:0};
      window.PortalIdentity={load:async()=>{},username:()=> 'fixture'};
      window.PortalEvents={watchResident:()=>()=>{}};
      window.PortalResidentService={loadStates:async ids=>{f.calls.push({name:'ListResidentStates',ids});return {states:ids.map(resident_id=>({resident_id,cursor:f.cursor,state:f.work,viewer_state:{unread:false}}))}},loadEvents:async()=>({events:[]})};
      window.state={lineage:{lineage_id:'lineage_fixture',nodes:[{id:member,title:'rviz审查',kind:'agent',x:25,y:30},{id:facade,title:'Family',kind:'agent',x:60,y:30}]},residentRuntimeById:new Map(),runtimeStatus:new Map()};
      window.esc=PortalUIPrimitives.escapeHtml;
      // A node's Resident, run status and kind are the production lookups, and
      // its work phase and intake the production card status; a dependency
      // these cases do not give fails when it is used.
      const create=(module,factory,given)=>module[factory](Object.fromEntries(module.dependencies.map(name=>[name,Object.hasOwn(given,name)?given[name]:()=>{throw new Error('Unexpected use of '+name)}])));
      const model=PortalHatgGraphModel.createGraphModel({esc,state});
      for (const name of ['nodeResidentId','effectiveNodeStatus','setNodeRuntimeStatus','isFunctionBlockInstance','isProgramComponent','nodeIsDerived']) window[name]=model[name];
      const status=create(PortalHatgNodeStatus,'createNodeStatus',{state,esc,nodeResidentId,effectiveNodeStatus});
      for (const name of ['nodeExecutionGate','nodeIntakeState','nodeWorkPresentation']) window[name]=status[name];
      window.familyMemberTag=()=>'';
      window.familyForFacade=id=>id===facade?{roles:{members:[member]}}:null;window.familyExpanded=()=>false;
      window.compactText=s=>s;window.nodeMessageCountText=()=> '1 messages';window.nodeContextHeatBadge=()=>'';
      window.updateLineageSummary=()=>{};window.renderControls=()=>{};window.hatgComponentOptions={};
      // The context projection gives the whole and the shown graph; this fixture shows all of it.
      window.graphVisibleModel=()=>({nodes:state.lineage.nodes,edges:[],overviewNodes:state.lineage.nodes,overviewEdges:[]});
      // The card content and the running-state repaint are the production canvas view.
      const view=create(PortalHatgGraphView,'createGraphView',{state,esc,compactText,nodeResidentId,effectiveNodeStatus,
        nodeExecutionGate,nodeIntakeState,nodeWorkPresentation,isFunctionBlockInstance,isProgramComponent,nodeIsDerived,
        familyMemberTag,familyForFacade,familyExpanded,nodeMessageCountText,nodeContextHeatBadge,graphVisibleModel});
      // The production subscription of the shown Lineage's Residents applies their state.
      const residents=create(PortalHatgResidentRuntime,'createResidentRuntime',{state,nodeResidentId,effectiveNodeStatus,
        setNodeRuntimeStatus,nodeExecutionGate,nodeIntakeState,nodeWorkPresentation,updateLineageSummary,renderControls,
        renderGraphRuntimePresentation:view.renderGraphRuntimePresentation,currentPanelActive:()=>true,
        currentHatgComponentOptions:()=>hatgComponentOptions});
      state.graphController=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',statusForNode:effectiveNodeStatus,nodeContent:view.graphNodeContent});
      const patch=state.graphController.patchRuntimePresentation;
      state.graphController.patchRuntimePresentation=(...args)=>{f.patches++;return patch(...args)};
      window.renderGraph=()=>state.graphController.render(graphVisibleModel());renderGraph();
      residents.bindResidentStateCollection();
      f.sub=state.residentStateCollection;
      f.load=()=>f.sub.load({force:true});
      f.event=(work,cursor=++f.cursor)=>{f.work={...f.work,...work};window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_events:[member,facade].map(resident_id=>({resident_id,cursor,event_type:'resident.state.changed',payload:{state:f.work}}))}}))};
    });
    await page.evaluate(()=>f.load());
    const badge=page.locator('[data-node-id="resident_fixture"] .nodeStatus');
    assert.equal(await badge.textContent(),'queued');
    const before=await page.evaluate(()=>f.patches);
    await page.evaluate(()=>f.event({work_phase:'waiting_quota'}));
    assert.equal(await badge.textContent(),'等待额度','phase-only quota transition must repaint the visible card');
    assert((await page.evaluate(()=>f.patches))>before);
    assert.equal(await page.locator('[data-node-id="resident_facade"] .nodeStatus').textContent(),'等待额度');
    assert.equal(await badge.evaluate(el=>getComputedStyle(el,'::before').animationName),'none','quota wait must not use executing pulse');
    assert.equal(await page.locator('[data-node-id="resident_fixture"] .nodeIntakeStatus').textContent(),'CLOSED');
    report.checks.push('phase-only live event repaints ordinary and Family cards, manual CLOSED preserved, quota dot static');
    if(process.env.ARTIFACT_DIR) await page.screenshot({path:path.join(process.env.ARTIFACT_DIR,'waiting-quota.png')});
    // A fresh formal read may change phase without advancing the message cursor.
    await page.evaluate(()=>{f.work.work_phase='';return f.load()});
    assert.equal(await badge.textContent(),'queued');
    await page.evaluate(()=>f.event({work_state:'running',work_phase:''}));
    assert.equal(await badge.textContent(),'running');
    await page.evaluate(()=>f.event({work_state:'stopping',work_phase:''}));
    assert.equal(await badge.textContent(),'正在停止…','stopping must repaint although compatibility status remains running');
    await page.evaluate(()=>f.event({work_state:'idle',work_phase:'',active_message_id:''}));
    assert.equal(await badge.textContent(),'idle');
    assert.equal(await page.locator('[data-node-id="resident_fixture"]').getAttribute('data-status'),'idle');
    assert.equal(await page.locator('[data-node-id="resident_fixture"]').getAttribute('data-runtime-effect'),null);
    await page.evaluate(()=>f.event({work_state:'queued',work_phase:'waiting_quota'},f.cursor-1));
    assert.equal(await badge.textContent(),'idle','older work event cannot resurrect quota activity');
    report.checks.push('same-cursor formal refresh, running to stopping to confirmed idle, stale event rejected, runtime effect removed');
    await page.evaluate(()=>f.event({work_state:'queued',work_phase:'waiting_quota',active_message_id:'msg_fixture',family_activity:{work_state:'running'}}));
    assert.equal(await badge.textContent(),'running','Family aggregate running must not inherit facade-only wait label');
    await page.evaluate(()=>{PortalI18n.apply('en');f.work={...f.work,family_activity:null,work_state:'queued',work_phase:'waiting_quota'};return f.load()});
    assert.equal(await badge.textContent(),'Waiting for quota');
    report.checks.push('Family aggregate respected and English compact label');
    report.calls=await page.evaluate(()=>f.calls);
    await page.evaluate(()=>{f.sub.destroy();state.graphController.destroy()});
    assert.deepEqual(report.errors,[]);report.ok=true;
  } catch(e) {report.failure=e.stack;process.exitCode=1}
  finally {await browser.close();if(process.env.ARTIFACT_DIR)fs.writeFileSync(path.join(process.env.ARTIFACT_DIR,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))}
})();
