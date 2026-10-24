'use strict';
// Production state subscription, HATG projection/renderer and D3 graph in Chromium.
// Only API transport and unrelated node features are fixtures; no live mutations.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '../../..'), root = path.join(repo, 'management_portal/static');
const {chromium} = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {babelParse, traverse} = require('./babel_parse.cjs');
const source = fs.readFileSync(path.join(root, 'hatg_panel.js'), 'utf8');
const names = new Set(['graphNodeContent', 'runtimeStatusFromResidentProjection',
  'applyResidentStateProjections', 'applyResidentStateCollection', 'renderGraphRuntimePresentation']);
const extracted = [];
traverse(babelParse(source, 'hatg_panel.js'), {FunctionDeclaration(p) {
  if (names.has(p.node.id?.name)) extracted.push(source.slice(p.node.start, p.node.end));
}});
const report = {checks:[], errors:[], ok:false};
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1050,height:600}});
    page.on('pageerror', e => report.errors.push(e.message));
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.pathname.startsWith('/static/')) return route.fulfill({body:fs.readFileSync(path.join(root, u.pathname.slice(8))),contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript'});
      return route.fulfill({contentType:'text/html', body:'<!doctype html><html data-portal-theme="light" data-portal-motion="full"><meta charset="utf-8"><body><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1000px;height:550px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div></body></html>'});
    });
    await page.goto('https://hatg-state.fixture.invalid');
    for (const name of ['portal_design_system','portal_themes','hatg_panel']) await page.addStyleTag({url:'/static/'+name+'.css'});
    for (const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','resident_protocol','lineage_graph','hatg_graph_model','hatg_node_status']) await page.addScriptTag({url:'/static/'+name+'.js'});
    await page.evaluate(code => {
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
      window.graphVisibleModel=()=>({nodes:state.lineage.nodes,edges:[]});
      eval(code+'\nwindow.renderer=graphNodeContent;window.resolveStatus=effectiveNodeStatus;window.applyStates=applyResidentStateCollection;');
      state.graphController=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',statusForNode:window.resolveStatus,nodeContent:window.renderer});
      const patch=state.graphController.patchRuntimePresentation;
      state.graphController.patchRuntimePresentation=(...args)=>{f.patches++;return patch(...args)};
      window.renderGraph=()=>state.graphController.render(graphVisibleModel());renderGraph();
      f.sub=PortalResidentProtocol.acquireStates([member,facade],window.applyStates,{load:false});
      f.load=()=>f.sub.load({force:true});
      f.event=(work,cursor=++f.cursor)=>{f.work={...f.work,...work};window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_events:[member,facade].map(resident_id=>({resident_id,cursor,event_type:'resident.state.changed',payload:{state:f.work}}))}}))};
    }, extracted.join('\n'));
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
