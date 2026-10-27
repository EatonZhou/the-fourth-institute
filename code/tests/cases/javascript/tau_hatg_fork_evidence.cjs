'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readHatgForkPrerequisites:read}=require(path.join(code,'management_portal/test_framework/tau_hatg_fork_evidence'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:800}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.setContent('<!doctype html><section id="lineage-hatg-panel" role="region" class="portalHatgPanelComponent"><div id="hatgCanvas" class="hatgCanvas" style="position:absolute;left:100px;top:100px;width:600px;height:500px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></section>');
  for(const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks'])
   await page.addStyleTag({content:fs.readFileSync(path.join(code,'management_portal/static',n+'.css'),'utf8')});
  await page.addStyleTag({content:'html,body{height:100%;margin:0} #lineage-hatg-panel{height:100%;width:100%}'});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_graph'])
   await page.addScriptTag({content:fs.readFileSync(path.join(code,'management_portal/static',n+'.js'),'utf8')});
  await page.evaluate(()=>{
   window.forkCalls=[];
   // Native HATG adapter's scale extent, not the generic renderer's 35% default.
   window.graph=PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',scaleExtent:[.05,3.2],onNodeQuickFork:(node,_event,mode)=>window.forkCalls.push({id:node.id,mode})});
   graph.render({nodes:[{id:'resident_fixture',kind:'agent',title:'Local source',x:50,y:50}],edges:[],selectedId:'resident_fixture'});
   graph.focusNode('resident_fixture',{scale:.05,duration:0});
  });
  const panel=page.locator('#lineage-hatg-panel'),button=panel.locator('[data-node-quick-fork]');
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('[data-node-quick-fork]')).opacity==='1');
  const small=await read(panel,{residentId:'resident_fixture'});
  assert.equal(small.zoom,'0.050');assert.equal(small.candidates[0].visible,true);
  assert.equal(small.candidates[0].enabled,true);assert.equal(small.candidates[0].center_hit,true);
  assert.equal(small.candidates[0].has_pointer_area,false);assert.equal(small.ready,false);
  assert.deepEqual(await page.evaluate(()=>window.forkCalls),[],'Readiness does not invoke Fork or change zoom');
  let subpixelClick=null;
  try {await button.click({timeout:350});}catch(error){subpixelClick={name:error.name,message:error.message};}
  assert.equal(subpixelClick?.name,'TimeoutError','The original weak predicates cannot guarantee an actionable pointer target');
  assert.match(subpixelClick.message,/outside of the viewport/);
  assert.deepEqual(await page.evaluate(()=>window.forkCalls),[],'Failed isolated pointer dispatch never invokes a mutation');
  const absent=await read(panel,{residentId:'resident_other'});
  assert.equal(absent.ready,false);assert.deepEqual(absent.candidates,[]);
  await page.evaluate(()=>graph.focusNode('resident_fixture',{duration:0}));
  const ready=await read(panel,{residentId:'resident_fixture'});assert.equal(ready.ready,true,JSON.stringify(ready));
  await panel.evaluate(n=>{n.style.height='200px';n.style.overflow='hidden';});
  const clipped=await read(panel,{residentId:'resident_fixture'});
  assert.equal(clipped.ready,false);assert.equal(clipped.candidates[0].within_clip,false);
  await panel.evaluate(n=>{n.style.height='';n.style.overflow='';});
  await button.evaluate(n=>{n.disabled=true;});
  assert.equal((await read(panel,{residentId:'resident_fixture'})).ready,false);
  await button.evaluate(n=>{n.disabled=false;n.setAttribute('aria-busy','true');});
  assert.equal((await read(panel,{residentId:'resident_fixture'})).ready,false);
  await button.evaluate(n=>{n.setAttribute('aria-busy','false');const c=n.cloneNode(true);c.id='duplicate';n.parentElement.append(c);});
  const duplicate=await read(panel,{residentId:'resident_fixture'});
  assert.equal(duplicate.candidates.length,2);assert.equal(duplicate.ready,false);
  await page.locator('#duplicate').evaluate(n=>n.remove());
  await page.evaluate(()=>{const cover=document.createElement('div');cover.id='cover';cover.style.cssText='position:fixed;inset:0;z-index:2147483647';document.body.append(cover);});
  const covered=await read(panel,{residentId:'resident_fixture'});
  assert.equal(covered.ready,false);assert.equal(covered.candidates[0].center_hit,false);
  await page.locator('#cover').evaluate(n=>n.remove());
  await panel.evaluate(n=>n.hidden=true);
  assert.equal((await read(panel,{residentId:'resident_fixture'})).ready,false);
  await panel.evaluate(n=>n.hidden=false);
  await assert.rejects(read(page.locator('#hatgCanvas'),{residentId:'resident_fixture'}),/Full native/);
  await button.click();
  assert.deepEqual(await page.evaluate(()=>window.forkCalls),[{id:'resident_fixture',mode:'full'}]);
  await page.evaluate(()=>graph.destroy());
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  console.log(JSON.stringify({status:'passed',small,subpixelClick,ready,absent,duplicate,covered,formal_acceptance:false,production_calls:0}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
