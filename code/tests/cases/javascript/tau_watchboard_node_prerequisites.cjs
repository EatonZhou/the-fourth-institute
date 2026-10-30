'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {LINEAGE_GRAPH_SCRIPTS}=require(path.join(code,'tests/cases/javascript/lineage_graph_scripts.cjs'));
const {HATG_PANEL_STYLES}=require(path.join(code,'tests/cases/javascript/hatg_panel_styles.cjs'));
const {readWatchboardNodePrerequisites:read}=require(process.env.TAU_WATCHBOARD_NODE_MODULE||path.join(code,'management_portal/test_framework/tau_watchboard_node_prerequisites'));
async function nativeFrameEvidence(browser){
  const page=await browser.newPage({viewport:{width:1100,height:800}}),requests=[],errors=[];
  page.on('request',r=>requests.push(r.url()));page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.setContent('<!doctype html><section id="lineage-hatg-panel" role="region" class="portalHatgPanelComponent"><div id="hatgCanvas" class="hatgCanvas" style="position:absolute;left:100px;top:100px;width:600px;height:500px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></section>');
    for(const n of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES,'lineage_function_blocks'])
      await page.addStyleTag({content:fs.readFileSync(path.join(code,'management_portal/static',n+'.css'),'utf8')});
    await page.addStyleTag({content:'html,body{height:100%;margin:0} #lineage-hatg-panel{height:100%;width:100%}'});
    for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min',...LINEAGE_GRAPH_SCRIPTS])
      await page.addScriptTag({content:fs.readFileSync(path.join(code,'management_portal/static',n+'.js'),'utf8')});
    await page.evaluate(()=>{
      window.graph=PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',scaleExtent:[.05,3.2]});
      graph.render({nodes:[{id:'resident_fixture',kind:'agent',title:'Local node',x:50,y:50}],edges:[],selectedId:'resident_fixture'});
      graph.focusNode('resident_fixture',{duration:0});
    });
    const panel=page.locator('#lineage-hatg-panel'),options={residentId:'resident_fixture'};
    const card=panel.locator('[data-node-id="resident_fixture"]');
    await page.waitForFunction(()=>document.querySelector('#lineage-hatg-panel').getAnimations({subtree:true}).every(a=>!a.pending&&a.playState!=='running'));
    const ready=await read(panel,options);assert.equal(ready.ready,true,JSON.stringify(ready));
    assert.equal(ready.frames_completed,2);assert.equal(ready.error,null);
    assert.equal(ready.first.resident_id,options.residentId);assert.equal(ready.second.resident_id,options.residentId);
    for(const frameTimeoutMs of [0,-1,NaN,Infinity,10001,'150'])
      await assert.rejects(read(panel,{...options,frameTimeoutMs}),/deadline/);
    const deadlines=[];
    for(const stallAt of [1,2]){
      await card.evaluate((node,stallAt)=>{
        const original={request:window.requestAnimationFrame,cancel:window.cancelAnimationFrame,bounds:node.getBoundingClientRect};
        const held=new Map(),issued=[],cancelled=[];let calls=0,nextId=1000000;
        const fixture=window.nodeFrameFixture={original,held,issued,cancelled,node,reads:0};
        node.getBoundingClientRect=function(){fixture.reads++;return original.bounds.call(this);};
        window.requestAnimationFrame=callback=>{
          if(++calls<stallAt)return original.request.call(window,callback);
          const id=nextId++;held.set(id,callback);issued.push(callback);return id;
        };
        window.cancelAnimationFrame=id=>{
          if(held.has(id)){held.delete(id);cancelled.push(id);}else original.cancel.call(window,id);
        };
      },stallAt);
      let watchdog;
      const pending=read(panel,{...options,frameTimeoutMs:150});
      const outcome=await Promise.race([pending,new Promise(resolve=>{watchdog=setTimeout(()=>resolve({watchdog:true}),1500);})]);
      clearTimeout(watchdog);
      const cleanup=await page.evaluate(async()=>{
        const f=window.nodeFrameFixture;
        const before={held:f.held.size,cancelled:f.cancelled.length,reads:f.reads};
        window.requestAnimationFrame=f.original.request;window.cancelAnimationFrame=f.original.cancel;
        // Deliberately dispatch even cancelled frames: a settled deadline must
        // not resume the chain or read fresh geometry into original evidence.
        for(const callback of f.issued)callback(performance.now());
        await new Promise(resolve=>f.original.request.call(window,()=>f.original.request.call(window,resolve)));
        const afterReads=f.reads;f.node.getBoundingClientRect=f.original.bounds;
        return {...before,afterReads};
      });
      await pending;
      assert(!outcome.watchdog,'Node sampling must settle while its frame is paused');
      assert.equal(outcome.ready,false);assert.equal(outcome.stable,false);
      assert.equal(outcome.error.code,'frame_timeout');assert.equal(outcome.error.phase,'animation_frames');
      assert.equal(outcome.frames_completed,stallAt-1);assert.equal(outcome.frame_timeout_ms,150);
      assert.equal(outcome.first.resident_id,options.residentId);assert.equal(outcome.first.bound_to_canvas,true);
      assert.equal(outcome.second,null,'Never invent a second sample on timeout');
      assert.deepEqual(cleanup,{held:0,cancelled:1,reads:1,afterReads:1});deadlines.push(outcome);
    }
    const sampleErrors=[];
    for(const failAt of [1,2]){
      await card.evaluate((node,failAt)=>{
        const bounds=node.getBoundingClientRect;let reads=0;
        window.restoreNodeBounds=()=>{node.getBoundingClientRect=bounds;};
        node.getBoundingClientRect=function(){
          if(++reads===failAt)throw new Error('isolated node geometry failure');
          return bounds.call(this);
        };
      },failAt);
      const failed=await read(panel,options);
      await page.evaluate(()=>window.restoreNodeBounds());
      assert.equal(failed.ready,false);assert.equal(failed.second,null);
      assert.equal(failed.error.code,'sampling_error');
      assert.equal(failed.error.phase,failAt===1?'initial_sample':'final_sample');
      assert.equal(failed.frames_completed,failAt===1?0:2);
      assert.equal(!!failed.first,failAt===2);assert.match(failed.error.stack,/isolated node geometry failure/);
      sampleErrors.push(failed);
    }
    await page.evaluate(()=>{
      window.restoreFrames=window.requestAnimationFrame;
      window.requestAnimationFrame=()=>{throw new Error('isolated frame scheduler failure');};
    });
    const schedulerError=await read(panel,options);
    await page.evaluate(()=>{window.requestAnimationFrame=window.restoreFrames;});
    assert.equal(schedulerError.ready,false);assert.equal(schedulerError.second,null);
    assert.equal(schedulerError.first.bound_to_canvas,true);
    assert.equal(schedulerError.error.phase,'animation_frames');assert.match(schedulerError.error.stack,/isolated frame scheduler failure/);
    await panel.evaluate(n=>{n.style.height='150px';n.style.overflow='hidden';});
    assert.equal((await read(panel,options)).ready,false,'Native ancestor clipping remains enforced');
    await panel.evaluate(n=>{n.style.height='';n.style.overflow='';});
    await page.evaluate(()=>graph.destroy());
    assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
    return {ready,deadlines,sampleErrors,schedulerError,production_requests:0,caller_RTT_measured:false};
  } finally {await page.close();}
}
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
    const native=await nativeFrameEvidence(browser);
    console.log(JSON.stringify({status:'passed',isolated_chromium:true,fixture:'original DOM contract cases plus actual PortalHatgGraph/native CSS frame sampling',checks:['two-frame original geometry','canvas and ancestor clip','occlusion','hidden and duplicate targets','scaled ancestor','active ancestor animation','no helper mutations or requests','bounded paused frames and cancellation','no late sampling','original geometry and scheduler errors'],native,formal_acceptance:false}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
