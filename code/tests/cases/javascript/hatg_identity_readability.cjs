'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {LINEAGE_GRAPH_SCRIPTS} = require('./lineage_graph_scripts.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
 const browser = await chromium.launch({headless:true});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  await page.route('**/*', route=> {
   const name=new URL(route.request().url()).pathname;
   if(name.startsWith('/static/')) return route.fulfill({body:fs.readFileSync(path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'});
   return route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><div class="portalHatgPanelComponent"><section class="hatgPane" style="width:900px;height:740px"><div class="programWireHint" data-mode="ready"><span></span><span class="programWireHintCopy"><small>Ports shown</small><strong>Click an output port to connect</strong></span></div><div class="hatgViewport"><div id="canvas" class="hatgCanvas"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div></section><div class="programInspectorForm"><textarea class="programEffectScript">return await DeliverMessage({body: "hello"});</textarea><div class="programEffectDiagnostics">Diagnostics: no errors</div></div></div></body></html>'});
  });
  await page.goto('http://identity.fixture.invalid');
  for(const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks','portal_resident_menu']) await page.addStyleTag({url:'/static/'+n+'.css'});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface','portal_society_service','portal_resident_menu','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_program',...LINEAGE_GRAPH_SCRIPTS]) await page.addScriptTag({url:'/static/'+n+'.js'});
  await page.evaluate(()=> {
   const esc=PortalUIPrimitives.escapeHtml;
   window.longTitle='[TAU:testrun_8e9a430c655ffcf6:lineage.hatg] Manual Event with distinguishing suffix';
   window.graph=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',nodeContent:n=>`<strong>${esc(n.title)}</strong>`});
   graph.render({nodes:[{id:'event',kind:'event_source',title:longTitle,x:50,y:50}],edges:[],functionBlockBoundaries:[{id:'inner',title:longTitle+' Inner',member_ids:['event']},{id:'outer',title:longTitle+' Outer',member_ids:[],child_block_ids:['inner']}]});
   graph.fit({duration:0,maxScale:1});
  });
  const metrics=await page.evaluate(()=> {
   const card=document.querySelector('[data-node-id="event"] strong'), hint=document.querySelector('.programWireHint').getBoundingClientRect(), canvas=document.querySelector('#canvas').getBoundingClientRect();
   const headers=[...document.querySelectorAll('.functionBlockBoundary header')].map(h=>h.getBoundingClientRect());
   return {headers:headers.map(r=>r.toJSON()),whiteSpace:getComputedStyle(card).whiteSpace, titleFits:card.scrollWidth<=card.clientWidth+1&&card.scrollHeight<=card.clientHeight+1,hintOutside:hint.bottom<=canvas.top+.5,font:parseFloat(getComputedStyle(document.querySelector('textarea')).fontSize),diag:parseFloat(getComputedStyle(document.querySelector('.programEffectDiagnostics')).fontSize),innerAbove:headers[0].bottom<card.getBoundingClientRect().top,outerAbove:headers[1].bottom<headers[0].top};
  });
  assert.equal(metrics.whiteSpace,'normal'); assert.ok(metrics.titleFits);assert.ok(metrics.hintOutside);assert.ok(metrics.font>=12&&metrics.diag>=12);assert.ok(metrics.innerAbove&&metrics.outerAbove,JSON.stringify(metrics));
  await page.evaluate(async()=> {
   window.openBlock=()=>PortalResidentMenu.open({lineage:{lineage_id:'fixture',nodes:[]},group:{active:'block',tabs:[{key:'block',label:longTitle}],render:(_k,root)=> {root.innerHTML=`<div class="portalHatgOverlayComponent functionBlockMenuPanel"><header class="functionBlockMenuIdentity"><strong>${longTitle}</strong><code>fbi_fixture</code></header><div style="height:1200px"><label>Title<input value="${longTitle}"></label></div></div>`;}}});
   await openBlock();
   document.querySelector('#nodeMenuGroupPanel').closest('.nodeSettingsBody').scrollTop=150;
   await openBlock();
  });
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const menu=await page.evaluate(()=>{const title=document.querySelector('.functionBlockMenuIdentity strong'),body=title.closest('.nodeSettingsBody');return {scroll:body.scrollTop,top:title.getBoundingClientRect().top,bodyTop:body.getBoundingClientRect().top,fits:title.scrollWidth<=title.clientWidth+1};});
  assert.equal(menu.scroll,0);assert.ok(menu.top>=menu.bodyTop);assert.ok(menu.fits);
  assert.deepEqual(errors,[]);
  if(process.env.HATG_READABILITY_SCREENSHOT) await page.screenshot({path:process.env.HATG_READABILITY_SCREENSHOT});
  console.log(JSON.stringify({metrics,menu,status:'passed'}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
