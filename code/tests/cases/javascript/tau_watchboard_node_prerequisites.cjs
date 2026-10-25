'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWatchboardNodePrerequisites:read}=require(process.env.TAU_WATCHBOARD_NODE_MODULE||path.join(code,'management_portal/test_framework/tau_watchboard_node_prerequisites'));
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage({viewport:{width:900,height:700}}), requests=[];
    page.on('request',r=>requests.push(r.url()));
    await page.setContent(`<style>#lineage-hatg-panel{margin:40px;width:600px}#hatgCanvas{width:600px;height:350px;overflow:hidden;position:relative}#clip{position:absolute;width:500px;height:300px;overflow:hidden}#node{position:absolute;left:20px;top:20px;width:160px;height:80px;background:#ccc}#cover{position:absolute;inset:0;z-index:2}</style><section id="lineage-hatg-panel" role="region" aria-label="HATG"><div id="hatgCanvas"><div id="clip"><button id="node" data-node-id="resident_123456789abc">Own node</button></div></div><button id="fit">Fit all HATG nodes</button></section>`);
    await page.evaluate(()=>{window.clicks=0;node.onclick=()=>window.clicks++;fit.onclick=()=>node.style.left='20px';});
    const panel=page.locator('#lineage-hatg-panel'), options={residentId:'resident_123456789abc'};
    assert((await read(panel,options)).ready);
    await page.locator('#node').evaluate(n=>n.style.left='620px');
    assert.equal((await read(panel,options)).ready,false,'visible DOM outside canvas must fail');
    assert.equal(await page.evaluate(()=>window.clicks),0);
    // A normal, separate setup action can establish preconditions; it does not
    // change the saved failed sample or execute the following selection.
    await page.getByRole('button',{name:'Fit all HATG nodes',exact:true}).click();
    const beforeSelection=await read(panel,options);assert(beforeSelection.ready);
    await page.locator('#node').click();assert.equal(await page.evaluate(()=>window.clicks),1);
    await page.locator('#clip').evaluate(n=>n.style.width='100px');
    assert.equal((await read(panel,options)).ready,false,'ancestor clipping is not isVisible');
    await page.locator('#clip').evaluate(n=>n.style.width='500px');
    await page.locator('#hatgCanvas').evaluate(n=>n.insertAdjacentHTML('beforeend','<div id="cover"></div>'));
    assert.equal((await read(panel,options)).second.center_hit,false);
    await page.locator('#cover').evaluate(n=>n.remove());
    await panel.evaluate(n=>n.hidden=true);assert.equal((await read(panel,options)).ready,false);
    await panel.evaluate(n=>n.hidden=false);
    await page.locator('#node').evaluate(n=>n.after(n.cloneNode(true)));
    assert.equal((await read(panel,options)).second.node_count,2);
    await page.locator('#node').last().evaluate(n=>n.remove());
    await page.locator('#clip').evaluate(n=>{n.style.transformOrigin='top left';n.style.transform='scale(.5)'});
    assert((await read(panel,options)).ready,'transformed native-style ancestor scale');
    await page.locator('#clip').evaluate(n=>{window.motion=n.animate([{transform:'translateX(0)'},{transform:'translateX(80px)'}],{duration:10000,iterations:Infinity})});
    assert.equal((await read(panel,options)).ready,false,'moving camera ancestor');
    await page.evaluate(()=>window.motion.cancel());
    await assert.rejects(read(panel,{residentId:'wrong'}),/Exact Resident/);
    assert.deepEqual(requests,[]);assert.equal(await page.evaluate(()=>window.clicks),1);
    console.log(JSON.stringify({status:'passed',isolated_chromium:true,fixture:'native-shaped DOM, not live Portal acceptance',checks:['two-frame original geometry','canvas and ancestor clip','occlusion','hidden and duplicate targets','scaled ancestor','active ancestor animation','no helper mutations or requests'],formal_acceptance:false}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
