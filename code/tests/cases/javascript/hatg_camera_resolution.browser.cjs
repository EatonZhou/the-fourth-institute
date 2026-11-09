'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const repo=path.resolve(__dirname,'../../..');
const playwright=require(path.join(repo,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const browserName=process.argv.includes('--webkit')?'webkit':'chromium';
const {LINEAGE_GRAPH_SCRIPTS}=require('./lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES}=require('./hatg_panel_styles.cjs');
(async()=>{
 const browser=await playwright[browserName].launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1000,height:800},deviceScaleFactor:2}),errors=[];
  page.setDefaultTimeout(5000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<html data-portal-theme="auto"><body><button id="plus">+</button><div class="portalHatgPanelComponent"><div class="hatgPane" style="width:900px;height:700px"><div id="canvas" class="hatgCanvas" style="width:900px;height:700px;flex:none"><div id="world" class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g id="edges"></g><g id="effects"></g></svg><div id="labels" class="hatgEdgeLabels"></div><div id="nodes" class="hatgNodes"></div></div></div></div></div></body></html>');
  for(const name of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES])await page.addStyleTag({path:path.join(repo,'management_portal/static',name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min',...LINEAGE_GRAPH_SCRIPTS])await page.addScriptTag({path:name==='lineage_graph_camera'&&process.env.HATG_CAMERA_SOURCE?process.env.HATG_CAMERA_SOURCE:path.join(repo,'management_portal/static',name+'.js')});
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
   // offsetWidth is integer-rounded; WebKit also quantizes zoomed border widths.
   assert.ok(Math.abs(sample.width-sample.logicalWidth*sample.k)<Math.max(1,sample.k),`Card magnification matches the logical camera: ${JSON.stringify(sample)}`);
   assert.ok(Math.abs(sample.svgScale-sample.worldWidth/100*sample.k)<.1,`SVG and HTML use the same screen scale: ${JSON.stringify(sample)}`);
  }
  await settled();
  // Synthetic Safari GestureEvents exercise routing, not physical trackpad input.
  await page.evaluate(()=>{
   window.pinch=(type,scale,target=document.querySelector('#canvas'))=>{
    const r=document.querySelector('#canvas').getBoundingClientRect();
    const event=new Event(type,{bubbles:true,cancelable:true});
    Object.defineProperties(event,{scale:{value:scale},clientX:{value:r.left+320},clientY:{value:r.top+240}});
    return target.dispatchEvent(event);
   };
   graph.focusNode('a',{scale:1,duration:0});
  });await settled();
  const native=await page.evaluate(()=>{
   const initial=graph.getTransform(),viewportScale=visualViewport.scale;
   const accepted=[pinch('gesturestart',1),pinch('gesturechange',2)];
   const middle=graph.getTransform(),paint=Number(getComputedStyle(document.querySelector('#world')).zoom);
   const wheel=new WheelEvent('wheel',{deltaY:-120,ctrlKey:true,bubbles:true,cancelable:true});
   const wheelAccepted=document.querySelector('#canvas').dispatchEvent(wheel),afterWheel=graph.getTransform();
   accepted.push(pinch('gesturechange',3.2),pinch('gestureend',3.2));
   return {initial,middle,paint,wheelAccepted,afterWheel,accepted,viewportScale,after:graph.getTransform()};
  });await settled();
  assert.deepEqual(native.accepted,[false,false,false,false],'Only canvas native gestures cancel page magnification');
  assert.equal(native.middle.k,2);assert.equal(native.paint,1,'Native pinch defers layout until gesture end');
  assert.equal(native.wheelAccepted,false);assert.deepEqual(native.afterWheel,native.middle,'Do not zoom twice for companion wheel events');
  assert.equal(native.after.k,3.2);
  for(const [axis,point] of [['x',320],['y',240]])assert.ok(Math.abs((point-native.initial[axis])/native.initial.k-(point-native.after[axis])/native.after.k)<1e-6,'Keep the world point under the pinch anchor');
  const outside=await page.evaluate(()=>{
   const before=graph.getTransform();
   const accepted=['gesturestart','gesturechange','gestureend'].map(type=>pinch(type,2,document.querySelector('#plus')));
   return {before,after:graph.getTransform(),accepted,viewportScale:visualViewport.scale};
  });
  assert.deepEqual(outside.accepted,[true,true,true]);assert.deepEqual(outside.after,outside.before);
  assert.equal(outside.viewportScale,native.viewportScale);
  const limits=await page.evaluate(()=>{
   pinch('gesturestart',1);pinch('gesturechange',.001);pinch('gestureend',.001);const low=graph.getTransform().k;
   pinch('gesturestart',1);pinch('gesturechange',100);pinch('gestureend',100);return {low,high:graph.getTransform().k};
  });await settled();assert.deepEqual(limits,{low:.35,high:3.2});
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
  const removed=await page.evaluate(()=>{
   pinch('gesturestart',1);graph.destroy();
   const accepted=['gesturestart','gesturechange','gestureend'].map(type=>pinch(type,2));
   const moving=document.querySelector('#canvas').classList.contains('isViewportMoving');
   mount();graph.focusNode('a',{scale:1,duration:0});
   pinch('gesturestart',1);pinch('gesturechange',2);pinch('gestureend',2);
   return {accepted,moving,k:graph.getTransform().k};
  });await settled();assert.deepEqual(removed,{accepted:[true,true,true],moving:false,k:2});
  assert.deepEqual(errors,[]);console.log(`${browserName}: HATG drawing resolution, SVG alignment, wheel/native pinch routing, drag, animation interruption and remount passed`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
