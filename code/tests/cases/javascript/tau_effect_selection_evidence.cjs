'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const code = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const L = 'lineage_111111111111', E = 'effect_222222222222';
const report = {scope: 'Native graph and CanvasSelection; node text, header and dependency callbacks are local fixtures; no business requests', formal_acceptance: false};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1100, height: 800}});
  const requests = [], errors = [];
  page.on('request', r => requests.push(r.url()));
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.setContent(`<!doctype html><section id="panel" class="portalHatgPanelComponent" role="region"><main id="agentLineageShell"><div id="hatgPane"><div id="activeLineageIdentity"><code data-portal-resource-id="${L}">${L}</code></div><div id="hatgCanvas" class="hatgCanvas" style="position:relative;width:900px;height:600px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div></main></section>`);
    for (const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks'])
      await page.addStyleTag({content: fs.readFileSync(path.join(code,'management_portal/static',n+'.css'),'utf8')});
    await page.addStyleTag({content: 'html,body{margin:0} #panel,#agentLineageShell,#hatgPane{display:block;width:100%;height:100%}'});
    for (const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_program','lineage_graph','hatg_canvas_selection'])
      await page.addScriptTag({content: fs.readFileSync(path.join(code,'management_portal/static',n+'.js'),'utf8')});
    await page.evaluate(({L,E}) => {
      const component = {id:E,kind:'effect',title:'Same Effect',x:50,y:45,config:{script:'fail "local fixture"'}};
      window.state = {lineage:{lineage_id:L}, selectedProgramObjectId:'', selectedNodeId:'', selectedSeedId:'', selectedEdgeId:''};
      window.callbacks = [];
      const module = PortalHatgCanvasSelection;
      const deps = Object.fromEntries(module.dependencies.map(name => [name, () => { throw new Error('Unexpected dependency '+name); }]));
      Object.assign(deps, {state, displayProgramComponentById:id=>id===E?component:null,
        displayProgramWireById:()=>null, functionBlockInstanceById:()=>null,
        isPacketOperatorComponent:()=>false, programFeature:{clearSelection:()=>false},
        publishLocation:()=>{},renderControls:()=>{},renderProgramInspector:()=>{},
        renderGraphSelection:()=>graph.patchSelection({selectedNodeId:state.selectedProgramObjectId,selectedNodeIds:[]}),
        revealSelectedGraphNode:()=>{},
      });
      const selection = module.createCanvasSelection(deps);
      window.graph = PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',
        nodeContent:()=>'<strong>Same Effect</strong>',
        nodeDoubleClickDelay:260, // Native HATG adapter's single/double click arbitration.
        onNodeDoubleClick:()=>{},onNodeClick:node=>{callbacks.push(node.id);return selection.selectProgramComponent(node);}});
      window.nodes = [component];
      graph.render({nodes,edges:[]});graph.focusNode(E,{duration:0});
    }, {L,E});
    await page.waitForFunction(()=>document.querySelector('#panel').getAnimations({subtree:true}).every(a=>!a.pending&&a.playState!=='running'));
    await page.clock.install({time:new Date('2026-01-01T00:00:00Z')});
    await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
    const root=page.locator('#panel'),card=root.locator(`button.hatgNode[data-node-id="${E}"]`);
    const name=(await card.ariaSnapshot()).split('\n')[0];
    await card.click();
    const immediate=await card.getAttribute('aria-pressed');
    assert.equal(immediate,'false');assert.deepEqual(await page.evaluate(()=>callbacks),[]);
    report.baseline={native_card:name,immediate_pressed:immediate,immediate_assertion:'SELECTION_NOT_READY',caller_rtt:false};
    if (process.argv.includes('--probe')) {
      await page.clock.runFor(300);
      assert.equal(await card.getAttribute('aria-pressed'),'true');
      assert.deepEqual(await page.evaluate(()=>callbacks),[E]);
      report.baseline.after_native_callback_pressed='true';
    } else {
      const {readHatgEffectSelection:read}=require(path.join(code,'management_portal/test_framework/tau_effect_selection_evidence'));
      const opts={lineageId:L,effectId:E};
      const pending=await read(root,opts);assert.equal(pending.ready,false);assert.equal(pending.candidates[0].cards[0].pressed,'false');
      await page.clock.runFor(300);
      const ready=await read(root,opts);assert.equal(ready.ready,true,JSON.stringify(ready));
      assert.deepEqual(await page.evaluate(()=>callbacks),[E]);
      report.pending=pending;report.ready=ready;
      await page.evaluate(()=>{nodes.push({...nodes[0],id:'effect_333333333333',x:72});graph.render({nodes,edges:[],selectedNodeId:state.selectedProgramObjectId});});
      await page.clock.runFor(300);
      const sameTitle=root.locator('button.hatgNode').filter({hasText:'Same Effect'});
      assert.equal(await sameTitle.count(),2);
      assert.equal((await read(root,opts)).ready,true);
      assert.equal((await read(root,{...opts,effectId:'effect_333333333333'})).ready,false);
      await card.evaluate(n=>n.removeAttribute('aria-pressed'));
      const missing=await read(root,opts);assert.equal(missing.ready,false);assert.equal(missing.candidates[0].cards[0].pressed,null);
      await card.evaluate(n=>n.setAttribute('aria-pressed','true'));
      await card.evaluate(n=>n.hidden=true);assert.equal((await read(root,opts)).ready,false);await card.evaluate(n=>n.hidden=false);
      await card.evaluate(n=>n.disabled=true);assert.equal((await read(root,opts)).ready,false);await card.evaluate(n=>n.disabled=false);
      await card.evaluate(n=>n.dataset.nodeKind='event_source');assert.equal((await read(root,opts)).ready,false);await card.evaluate(n=>n.dataset.nodeKind='effect');
      await card.evaluate(n=>n.parentNode.append(n.cloneNode(true)));assert.equal((await read(root,opts)).ready,false);
      await root.locator(`button.hatgNode[data-node-id="${E}"]`).last().evaluate(n=>n.remove());
      await root.locator('#activeLineageIdentity code').evaluate(n=>n.textContent='lineage_wrong');
      assert.equal((await read(root,opts)).ready,false);
      await root.locator('#activeLineageIdentity code').evaluate((n,L)=>n.textContent=L,L);
      await root.evaluate(n=>n.setAttribute('aria-busy','true'));assert.equal((await read(root,opts)).ready,false);await root.evaluate(n=>n.removeAttribute('aria-busy'));
      await root.evaluate(n=>n.parentNode.append(n.cloneNode(true)));
      const duplicate=await read(page.locator('.portalHatgPanelComponent'),opts);assert.equal(duplicate.ready,false);assert.equal(duplicate.candidates.length,2);
      await page.locator('.portalHatgPanelComponent').last().evaluate(n=>n.remove());
      assert.equal((await read(page.locator('#missing'),opts)).ready,false);
      for(const options of [{lineageId:L},{effectId:E},{...opts,effectId:'resident_222222222222'}]) await assert.rejects(read(root,options),TypeError);
      const failure=await read({elementHandles:async()=>[{evaluate:async()=>{throw new Error('fixture detached');},dispose:async()=>{}}]},opts);
      assert.equal(failure.ready,false);assert.equal(failure.error.phase,'sample');assert.match(failure.error.stack,/fixture detached/);
      assert.deepEqual(await page.evaluate(()=>callbacks),[E]);
      report.status='passed';
    }
    assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
    if(process.env.TAU_EFFECT_SELECTION_REPORT) fs.writeFileSync(process.env.TAU_EFFECT_SELECTION_REPORT,JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({status:'passed',probe:process.argv.includes('--probe'),scope:report.scope,baseline:report.baseline}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
