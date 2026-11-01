'use strict';
const assert=require('node:assert/strict');
module.exports=async({page,residentId,lineageId,errors})=>{
  const {readHatgViewSnapshot,waitForHatgView}=require('../../../management_portal/test_framework/tau_hatg_view_evidence');
  await page.getByRole('button',{name:'Show HATG',exact:true}).click();
  const panel=page.getByRole('region',{name:'HATG',exact:true});
  await panel.locator('[data-node-id]').waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.querySelector('.portalHatgPanelComponent[aria-busy="true"]'));
  const first=await readHatgViewSnapshot(panel,{lineageId});
  assert.equal(first.identity_ready,true);
  assert.deepEqual(first.candidates[0].nodes.map(n=>n.id),[residentId]);
  assert.ok(first.candidates[0].execution_gates.some(g=>g.state==='unknown'
    && /执行服务暂不可达|Execution service is unavailable/.test(g.title)),JSON.stringify(first));
  const rejected=[];
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds:[residentId],edgeIds:[],
    requireExecutionOpen:true,timeoutMs:300,stableMs:100,intervalMs:30,record:s=>rejected.push(s)}),
    e=>e.code==='tau.hatg_view_timeout');
  assert.ok(rejected.length>=2 && rejected.every(s=>!s.ready));
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds:[residentId],edgeIds:[],
    requireExecutionOpen:true,executionResidentIds:[],record:()=>{}}),/Exact node/);
  // Geometry can settle even though execution is unavailable; these are
  // distinct observations. The explicit option prevents calling this send-ready.
  const samples=[];
  const settled=await waitForHatgView(panel,{lineageId,nodeIds:[residentId],edgeIds:[],
    timeoutMs:1500,stableMs:120,intervalMs:30,record:s=>samples.push(s)});
  assert.ok(settled.ready && settled.stable_for_ms>=120 && samples.length>=3);
  const mismatch=[];
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds:[],edgeIds:[],
    timeoutMs:250,stableMs:100,intervalMs:30,record:s=>mismatch.push(s)}),e=>e.code==='tau.hatg_view_timeout');
  assert.ok(mismatch.length>=2 && mismatch.every(s=>!s.ready));
  assert.equal((await readHatgViewSnapshot(panel,{lineageId:'lineage_wrong'})).identity_ready,false);
  assert.equal((await readHatgViewSnapshot(panel.locator('#hatgCanvas'),{lineageId})).identity_ready,false);
  await panel.locator('button[data-semantic-zoom="overview"]').click();
  await page.waitForFunction(()=>document.getElementById('hatgCanvas')?.dataset.semanticZoom==='overview');
  assert.equal(await panel.locator('[data-execution-gate]').isVisible(),false);
  const overview=await readHatgViewSnapshot(panel,{lineageId});
  assert.ok(overview.candidates[0].execution_gates.some(g=>g.state==='unknown' && g.visible===false),
    'semantic zoom hides a label, not its execution state');
  await assert.rejects(waitForHatgView(panel,{lineageId,nodeIds:[residentId],edgeIds:[],
    requireExecutionOpen:true,timeoutMs:300,stableMs:100,intervalMs:30,record:()=>{}}),
    e=>e.code==='tau.hatg_view_timeout');
  assert.deepEqual(errors,[]);
};
