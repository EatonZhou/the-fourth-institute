'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root=path.resolve(__dirname,'../../../management_portal/static');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
   const name=new URL(route.request().url()).pathname;
   if(name.startsWith('/static/'))return route.fulfill({body:fs.readFileSync(path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'});
   return route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1000px;height:760px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div></body></html>'});
  });
  await page.goto('http://edge-label.fixture.invalid');
  for(const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks'])await page.addStyleTag({url:'/static/'+n+'.css'});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_graph'])await page.addScriptTag({url:'/static/'+n+'.js'});
  await page.evaluate(()=>{
   window.graph=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',scaleExtent:[.05,3.2]});
   graph.render({nodes:[{id:'a',x:20,y:20},{id:'b',x:80,y:80},{id:'c',x:20,y:80},{id:'d',x:80,y:20}].map(n=>({...n,kind:'agent',title:n.id})),edges:[{id:'one',source:'a',target:'b',type:'packet',label:'Route'},{id:'two',source:'c',target:'d',type:'packet',label:'Route'}]});
  });
  const verify=async stage=>{
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const b=await page.locator('.edgeLabelBtn').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom};}));
   assert.equal(b.length,2);
   assert.ok(b[0].right<=b[1].left || b[1].right<=b[0].left || b[0].bottom<=b[1].top || b[1].bottom<=b[0].top,stage+JSON.stringify(b));
  };
  await verify('initial');
  await page.evaluate(()=>graph.refresh());await verify('geometry refresh');
  await page.evaluate(()=>graph.fit({duration:0}));await verify('fit');
  const card=await page.locator('.hatgNode[data-node-id="a"]').boundingBox();
  assert.ok(card,'source card is available for a real drag');
  const x=card.x+card.width/2,y=card.y+card.height/2;
  await page.mouse.move(x,y);await page.mouse.down();
  await page.mouse.move(x+18,y+18,{steps:3});await page.mouse.move(x,y,{steps:3});
  await page.mouse.up();await verify('drag completion');
  await page.locator('#canvas').evaluate(n=>Object.assign(n.style,{width:'440px',height:'420px'}));await verify('resize');
  assert.deepEqual(errors,[]);console.log('Crossing Route labels remain separate through refresh, fit, drag and resize');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
