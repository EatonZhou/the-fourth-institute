'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const portal=path.resolve(__dirname,'../../../management_portal');
const {chromium}=require(path.join(portal,'test_framework/tau_runtime/node_modules/playwright'));
const L='lineage_111111111111',E='event_source_222222222222',T='effect_333333333333',W='wire_444444444444';
const report={scope:'Native graph, program ports and CSS; graph data, header and node text are local fixtures. No business requests.',formal_acceptance:false};
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
    await page.setContent(`<!doctype html><section id="panel" class="portalHatgPanelComponent" role="region"><div id="activeLineageIdentity"><code data-portal-resource-id="${L}">${L}</code></div><div id="hatgCanvas" class="hatgCanvas" style="position:relative;width:1050px;height:700px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></section>`);
    for(const n of ['portal_design_system','portal_themes','hatg_panel'])await page.addStyleTag({content:fs.readFileSync(path.join(portal,'static',n+'.css'),'utf8')});
    for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_program','lineage_graph'])await page.addScriptTag({content:fs.readFileSync(path.join(portal,'static',n+'.js'),'utf8')});
    await page.evaluate(({E,T,W})=>{
      window.localNodes=[{id:E,kind:'event_source',title:'Local Event',x:25,y:40,config:{event_type:'manual.triggered'}},{id:T,kind:'effect',title:'Local Effect',x:75,y:40,config:{script:'local fixture'}}];
      window.localEdges=[{id:W,source:E,target:T,type:'route',source_port:'output',target_port:'invoke',_program_wire:true}];
      window.graph=PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',
        portsForNode:PortalLineageProgram.portsForNode,nodeContent:n=>`<strong>${n.title}</strong>`});
      window.renderLocal=(draft=false)=>graph.render({nodes:localNodes,edges:localEdges,wireDraft:draft?PortalLineageProgram.portsForNode(localNodes[0]).find(p=>p.port_id==='output'):null});
      renderLocal();graph.fit({duration:0});
    },{E,T,W});
    const root=page.locator('#panel');
    await root.locator(`button[data-edge-id="${W}"]`).waitFor({state:'visible',timeout:1000});
    if(process.argv.includes('--probe')){
      // The original predicate runs after its separate edge wait. A later
      // native empty render leaves no evidence of the original source/edge.
      await page.evaluate(()=>graph.render({nodes:[],edges:[]}));
      report.original_completion=await page.evaluate(id=>{const e=document.querySelector('button[data-port-node-id="'+id+'"][data-port-id="output"]');return !e||e.getAttribute('aria-pressed')!=='true';},E);
      report.remaining_ports=await root.locator('button.hatgPort').count();
      report.remaining_edges=await root.locator('button.edgeLabelBtn').count();
      assert.equal(report.original_completion,true);assert.equal(report.remaining_ports,0);assert.equal(report.remaining_edges,0);
    }else{
      const {readEventEffectConnection}=require(path.join(portal,'test_framework/tau_event_connection_evidence.js'));
      const opts={lineageId:L,eventId:E,effectId:T,wireId:W},read=(more={})=>readEventEffectConnection(root,{...opts,...more});
      const source=root.locator(`button.hatgPort[data-port-node-id="${E}"][data-port-id="output"]`);
      const edge=root.locator(`button.edgeLabelBtn[data-edge-id="${W}"]`);
      report.ready=await read();assert.equal(report.ready.ready,true,JSON.stringify(report.ready));
      assert.equal(report.ready.candidates[0].ports.find(p=>p.node_id===E&&p.port==='output').visible,false,'Native ended-draft port is hidden, not missing');
      await page.evaluate(()=>renderLocal(true));
      report.pending=await read();assert.equal(report.pending.edge_ready,true);assert.equal(report.pending.draft_finished,false);
      await page.evaluate(()=>renderLocal());
      await source.evaluate(n=>n.removeAttribute('aria-pressed'));
      const unknown=await read();assert.equal(unknown.ready,false);assert.equal(unknown.candidates[0].ports.find(p=>p.port==='output').pressed,null);
      await page.evaluate(()=>renderLocal());
      await source.evaluate(n=>n.remove());assert.equal((await read()).ready,false);
      await page.evaluate(()=>renderLocal());
      await source.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).ready,false);
      await source.last().evaluate(n=>n.remove());
      await edge.evaluate(n=>n.setAttribute('data-target-id','effect_555555555555'));assert.equal((await read()).ready,false);
      await page.evaluate(()=>renderLocal());
      await edge.evaluate(n=>n.after(n.cloneNode(true)));assert.equal((await read()).edge_ready,false);
      await edge.last().evaluate(n=>n.remove());
      await edge.evaluate(n=>{n.hidden=true;});assert.equal((await read()).edge_ready,false);
      await edge.evaluate(n=>{n.hidden=false;});
      assert.equal((await read({wireId:'wire_555555555555'})).ready,false);
      assert.equal((await read({lineageId:'lineage_555555555555'})).ready,false);
      await root.locator('#hatgCanvas').evaluate(n=>n.setAttribute('data-wire-draft-source-id','event_source_555555555555'));
      assert.equal((await read()).draft_finished,false);
      await page.evaluate(()=>renderLocal());
      await root.evaluate(n=>{const other=n.cloneNode(true);other.id='other';n.after(other);});
      const duplicate=await readEventEffectConnection(page.locator('.portalHatgPanelComponent'),opts);
      assert.equal(duplicate.ready,false);assert.equal(duplicate.candidates.length,2);
      await page.locator('#other').evaluate(n=>n.remove());
      await page.evaluate(()=>graph.render({nodes:[],edges:[]}));
      report.empty=await read();assert.equal(report.empty.ready,false);assert.equal(report.empty.draft_finished,false);
      await page.evaluate(()=>renderLocal());
      const handle=await root.elementHandle();
      const failed=await readEventEffectConnection({elementHandles:async()=>[handle,{evaluate:async()=>{throw new Error('local observation lost');},dispose:async()=>{}}]},opts);
      assert.equal(failed.ready,false);assert.equal(failed.candidates.length,1);
      assert.equal(failed.error.phase,'sample');assert.equal(failed.error.name,'Error');assert.ok(failed.error.stack.includes('local observation lost'));report.error=failed.error;
      report.status='passed';
    }
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    if(process.env.TAU_EVENT_CONNECTION_REPORT)fs.writeFileSync(process.env.TAU_EVENT_CONNECTION_REPORT,JSON.stringify(report,null,2)+'\n');
    console.log('Native Event connection evidence keeps exact edge and draft state together; missing evidence cannot imply completion.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
