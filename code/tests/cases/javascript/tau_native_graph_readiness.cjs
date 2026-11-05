'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
module.exports=async({page,residentId,childId,lineageId,errors})=>{
  const {readHatgViewSnapshot,waitForHatgView,fitAndWaitForHatgView}=require('../../../management_portal/test_framework/tau_hatg_view_evidence');
  await page.getByRole('button',{name:'Show HATG',exact:true}).click();
  const panel=page.getByRole('region',{name:'HATG',exact:true});
  await panel.locator(`[data-node-id="${residentId}"]`).waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.querySelector('.portalHatgPanelComponent[aria-busy="true"]'));
  const first=await readHatgViewSnapshot(panel,{lineageId});
  assert.equal(first.identity_ready,true);
  const nodeIds=[residentId,childId],edgeIds=['edge_sample_inheritance'];
  assert.deepEqual(first.candidates[0].nodes.map(n=>n.id),nodeIds);
  assert.deepEqual(first.candidates[0].edges,edgeIds);
  assert.ok(first.candidates[0].execution_gates.some(g=>g.state==='unknown'
    && /执行服务暂不可达|Execution service is unavailable/.test(g.title)),JSON.stringify(first));
  const rejected=[];
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    requireExecutionOpen:true,timeoutMs:300,stableMs:100,intervalMs:30,record:s=>rejected.push(s)}),
    e=>e.code==='tau.hatg_view_timeout');
  assert.ok(rejected.length>=2 && rejected.every(s=>!s.ready));
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    requireExecutionOpen:true,executionResidentIds:[],record:()=>{}}),/Exact node/);
  // Geometry can settle even though execution is unavailable; these are
  // distinct observations. The explicit option prevents calling this send-ready.
  const samples=[];
  const settled=await waitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    timeoutMs:1500,stableMs:120,intervalMs:30,record:s=>samples.push(s)});
  assert.ok(settled.ready && settled.stable_for_ms>=120 && samples.length>=3);
  const prompt=fs.readFileSync(path.join(__dirname,'../../../management_portal/test_framework/config/tau_runner_prompt.md'),'utf8');
  const documented=prompt.match(/```js\n(\/\/ tau-hatg-execution-scope\n[\s\S]*?)\n```/);
  assert.ok(documented,'public graph preparation must define geometry-only and ordinary-member execution scopes');
  const options=new Function('ids',documented[1]+'\nreturn hatgExecutionScope(ids);');
  assert.deepEqual(options([residentId]),{requireExecutionOpen:true,executionResidentIds:[residentId]});
  const geometryOnly=options([]);
  assert.deepEqual(geometryOnly,{requireExecutionOpen:false,executionResidentIds:[]});
  assert.ok((await waitForHatgView(panel,{lineageId,nodeIds,edgeIds,...geometryOnly,
    timeoutMs:1500,stableMs:120,intervalMs:30,record:()=>{}})).ready);
  const mismatch=[];
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds:[],edgeIds:[],
    timeoutMs:250,stableMs:100,intervalMs:30,record:s=>mismatch.push(s)}),e=>e.code==='tau.hatg_view_timeout');
  assert.ok(mismatch.length>=2 && mismatch.every(s=>!s.ready));
  assert.equal((await readHatgViewSnapshot(panel,{lineageId:'lineage_wrong'})).identity_ready,false);
  assert.equal((await readHatgViewSnapshot(panel.locator('#hatgCanvas'),{lineageId})).identity_ready,false);
  // Semantic detail follows the actual camera scale; the former preset
  // toolbar is retired. Reach overview using the native zoom controls.
  for (let i=0;i<4;i++) {
    await panel.locator('#graphZoomOutBtn').click();
    await page.waitForTimeout(260);
  }
  await page.waitForFunction(()=>document.getElementById('hatgCanvas')?.dataset.semanticZoom==='overview');
  assert.ok(await panel.locator('[data-execution-gate]').evaluateAll(nodes=>nodes.length>0 && nodes.every(n=>!n.checkVisibility())));
  const overview=await readHatgViewSnapshot(panel,{lineageId});
  assert.ok(overview.candidates[0].execution_gates.some(g=>g.state==='unknown' && g.visible===false),
    'semantic zoom hides a label, not its execution state');
  const nativeEdge=panel.locator('path.hatgEdge[data-edge-id="edge_sample_inheritance"]');
  const paint=await nativeEdge.evaluate(n=>({length:n.getTotalLength(),stroke:getComputedStyle(n).stroke,
    strokeWidth:parseFloat(getComputedStyle(n).strokeWidth),cssBoxVisible:n.checkVisibility(),
    decorative:n.ownerSVGElement.getAttribute('aria-hidden')}));
  assert.ok(paint.length>0 && paint.stroke!=='none' && paint.strokeWidth>0);
  assert.equal(paint.cssBoxVisible,true);
  assert.equal(paint.decorative,'true','native accessibility hides the SVG from readers, not from sight');
  assert.deepEqual(overview.candidates[0].edges,edgeIds,'overview hides HTML labels but the native SVG edge remains rendered');
  assert.ok(overview.candidates[0].edge_geometry.some(e=>e.tag.toLowerCase()==='path' && e.path));
  const setsExample=prompt.match(/```js\n(\/\/ tau-hatg-original-sets\n[\s\S]*?)\n```/);
  assert.ok(setsExample);
  const originalSets=new Function('snapshot',setsExample[1]+'\nreturn originalHatgSets(snapshot);');
  assert.deepEqual(originalSets(first),{nodeIds,edgeIds});
  assert.deepEqual(originalSets(overview),{nodeIds,edgeIds});
  assert.ok((await waitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    timeoutMs:1500,stableMs:120,intervalMs:30,record:()=>{}})).ready);
  // Negative visibility control on the same native path, without replacing it.
  await nativeEdge.evaluate(n=>n.style.opacity='0');
  assert.deepEqual((await readHatgViewSnapshot(panel,{lineageId})).candidates[0].edges,[]);
  await nativeEdge.evaluate(n=>n.style.opacity='1');
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    requireExecutionOpen:true,timeoutMs:300,stableMs:100,intervalMs:30,record:()=>{}}),
    e=>e.code==='tau.hatg_view_timeout');
  for(let i=0;i<5;i++)await panel.locator('#graphZoomInBtn').click();
  assert.ok((await readHatgViewSnapshot(panel,{lineageId})).candidates[0].nodes.some(n=>!n.within_canvas));
  await panel.locator('#graphFitBtn').evaluate(button=>button.addEventListener('click',()=>{window.sampleFitClicks=(window.sampleFitClicks||0)+1;}));
  const fittedSamples=[];
  const fitted=await fitAndWaitForHatgView(panel,{lineageId,nodeIds,edgeIds,
    timeoutMs:4000,stableMs:120,intervalMs:30,record:s=>fittedSamples.push(s)});
  assert.ok(fitted.ready&&fitted.candidates[0].nodes.every(n=>n.within_canvas));
  assert.equal(await page.evaluate(()=>sampleFitClicks),1);
  assert.ok(fittedSamples.some(s=>s.phase==='before-fit')&&fittedSamples.some(s=>s.phase==='after-fit'));
  await assert.rejects(fitAndWaitForHatgView(panel,{lineageId:'lineage_wrong',nodeIds,edgeIds,
    timeoutMs:300,stableMs:100,intervalMs:30,record:()=>{}}),e=>e.code==='tau.hatg_view_timeout');
  assert.equal(await page.evaluate(()=>sampleFitClicks),1,'identity mismatch must not click Fit');
  assert.deepEqual(errors,[]);
};
