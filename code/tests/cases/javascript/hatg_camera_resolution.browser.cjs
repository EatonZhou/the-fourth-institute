'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const repo=path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(repo,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {LINEAGE_GRAPH_SCRIPTS}=require('./lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES}=require('./hatg_panel_styles.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1000,height:800}}),errors=[];
  page.setDefaultTimeout(5000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<html data-portal-theme="auto"><body><button id="plus">+</button><div class="portalHatgPanelComponent"><div class="hatgPane" style="width:900px;height:700px"><div id="canvas" class="hatgCanvas" style="width:900px;height:700px;flex:none"><div id="world" class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g id="edges"></g><g id="effects"></g></svg><div id="labels" class="hatgEdgeLabels"></div><div id="nodes" class="hatgNodes"></div></div></div></div></div></body></html>');
  for(const name of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES])await page.addStyleTag({path:path.join(repo,'management_portal/static',name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min',...LINEAGE_GRAPH_SCRIPTS])await page.addScriptTag({path:path.join(repo,'management_portal/static',name+'.js')});
  await page.evaluate(()=>{
   window.model={nodes:[{id:'a',kind:'agent',x:50,y:50,title:'测试节点',status:'running'},{id:'b',kind:'agent',x:65,y:55,title:'目标',status:'idle'}],edges:[{id:'wire',source:'a',target:'b',type:'watch',label:'Watch'}]};
   window.clicks=[];
   window.mount=()=>{
    window.graph=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',nodeLayer:'#nodes',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',zoomInButton:'#plus',nodeContent:n=>`<strong>${n.title}</strong>`,onNodeClick:n=>clicks.push(n.id)});
    graph.render(model);
   };mount();graph.focusNode('a',{scale:.4,duration:0});
  });
  async function settled(){
   await page.waitForFunction(()=>{
    const w=document.querySelector('#world'),s=getComputedStyle(w),t=graph.getTransform(),m=new DOMMatrixReadOnly(s.transform);
    return !document.querySelector('#canvas').classList.contains('isViewportMoving') && Math.abs(Number(s.zoom)-t.k)<1e-5 && Math.abs(m.a-1)<1e-5;
   });
   const sample=await page.evaluate(()=>{
    const w=document.querySelector('#world'),n=document.querySelector('[data-node-id="a"]'),t=graph.getTransform(),r=n.getBoundingClientRect();
    return {k:t.k,width:r.width,logicalWidth:n.offsetWidth,svgScale:document.querySelector('.hatgEdges').getScreenCTM().a,worldWidth:w.offsetWidth};
   });
   assert.ok(Math.abs(sample.width-sample.logicalWidth*sample.k)<sample.k,'Card magnification matches the logical camera');
   assert.ok(Math.abs(sample.svgScale-sample.worldWidth/100*sample.k)<.1,'SVG and HTML use the same screen scale');
  }
  await settled();
  for(const compositor of [false,true]){
   await page.evaluate(compositor=>graph.focusNode('a',{scale:3.2,duration:180,compositor}),compositor);await settled();
   await page.evaluate(()=>graph.focusNode('a',{scale:1.6,duration:0}));await settled();
  }
  await page.locator('#plus').click();await settled();
  await page.mouse.move(450,350);await page.mouse.wheel(0,-160);await page.waitForTimeout(180);await settled();
  await page.locator('[data-node-id="a"]').click();
  const node=await page.locator('[data-node-id="a"]').boundingBox();
  await page.mouse.move(node.x+node.width/2,node.y+node.height/2);await page.mouse.down();await page.mouse.move(node.x+node.width/2+40,node.y+node.height/2+25,{steps:4});await page.mouse.up();
  const moved=await page.locator('[data-node-id="a"]').boundingBox();
  assert.ok(Math.abs(moved.x-node.x-40)<2 && Math.abs(moved.y-node.y-25)<2,'Dragging preserves screen-space pointer distance');
  await page.evaluate(()=>graph.focusNode('a',{scale:.8,duration:1000,compositor:true}));await page.waitForTimeout(150);
  const jump=await page.evaluate(()=>{
   const n=document.querySelector('[data-node-id="a"]'),before=n.getBoundingClientRect();
   document.querySelector('#canvas').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));
   const after=n.getBoundingClientRect();return Math.max(Math.abs(after.x-before.x),Math.abs(after.y-before.y),Math.abs(after.width-before.width));
  });assert.ok(jump<1,'Interrupting focus preserves its currently visible position and scale');await settled();
  await page.evaluate(()=>graph.fit({duration:100}));await settled();
  assert.deepEqual(await page.evaluate(()=>clicks),['a']);
  assert.equal(await page.locator('canvas').count(),0);
  await page.evaluate(()=>{graph.destroy();mount();graph.focusNode('a',{scale:1,duration:0});});await settled();
  assert.deepEqual(errors,[]);console.log('HATG drawing resolution, SVG alignment, zoom, drag, animation interruption and remount passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
