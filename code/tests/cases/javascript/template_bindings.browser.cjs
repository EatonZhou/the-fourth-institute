'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.stack));page.setDefaultTimeout(15000);
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
  const fid=await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',peek:()=>({}),userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};window.PortalAPI={get:url=>fetch(url).then(r=>r.json())};
   window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Error(d.error.message);return d.result;};window.PortalInstructionClient={execute};
   await PortalTemplateWorkshopLoader.load();window.lid=lineageId;
   window.anchors=[];for(const [title,x,y] of [['Existing A',30,40],['Existing B',60,60]])anchors.push((await execute('CreateResident',{lineage_id:lineageId,title,provider:'codex',x,y})).resident_id);
   document.body.innerHTML='<main class="portalHatgOverlayComponent hatgPane" style="display:grid;grid-template-columns:160px 1fr;height:100vh"><aside id="templateShelf" style="overflow:auto"></aside><div class="hatgViewport hatgPane" style="position:relative;min-width:0;height:100%"><div id="hatgCanvas" class="hatgCanvas"><div class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g data-layer="edges"></g><g data-layer="effects"></g></svg><div class="hatgEdgeLabels"></div><div class="hatgNodes"></div></div></div></div></main>';
   const canvas=document.querySelector('#hatgCanvas');window.state={selectedCanvasObjectIds:new Set(anchors)};
   window.draw=async()=>{state.lineage=(await execute('GetLineage',{lineage_id:lineageId})).lineage;const g=PortalLineageProgram.projectGraph(state.lineage.program,state.lineage.nodes.map(n=>({...n,kind:'agent'})));state.graphController.render({nodes:g.nodes,edges:g.edges,layoutInteractive:true,selectionEnabled:false});};
   state.graphController=PortalHatgGraph.createGraph({root:canvas,world:canvas.firstElementChild,nodeLayer:canvas.querySelector('.hatgNodes'),edgeLayer:canvas.querySelector('[data-layer="edges"]'),effectLayer:canvas.querySelector('[data-layer="effects"]'),labelLayer:canvas.querySelector('.hatgEdgeLabels'),worldWidth:1280,worldHeight:900,nodeContent:node=>`<strong>${PortalUIPrimitives.escapeHtml(node.title||node.id)}</strong>`,portsForNode:node=>PortalLineageProgram.portsForNode(node)});
   await draw();state.graphController.setTransform({x:70,y:40,k:.6},{duration:0});window.panelHost={hatgComponentOptions:{emit:()=>{}}};
   PortalHatgPanelTemplateShelf.createTemplateShelf({$id:id=>document.getElementById(id),state,panelHost,nodeById:id=>state.lineage.nodes.find(n=>n.id===id),renderGraph:()=>{},toast:(m,tone)=>{if(tone==='error')throw Error(m);},executeHatgInstruction:execute,applyProgramMutation:async work=>{const result=await work();await draw();return result;}}).mountTemplates();await panelHost.templateController.load();
   return (await execute('ListTemplates')).templates.find(t=>t.builtin_key==='family').template_id;
  },process.env.WORKSHOP_TEST_LINEAGE);
  const graph=page.locator('#hatgCanvas'),tile=page.locator(`#templateShelf [data-template-id="${fid}"]`),folder='/tmp/template-bindings-evidence';fs.mkdirSync(folder,{recursive:true});
  const before=await page.evaluate(()=>execute('GetLineage',{lineage_id:lid}).then(r=>r.lineage));
  const a=await page.evaluate(()=>anchors[0]),box=await graph.locator(`[data-node-id="${a}"]`).boundingBox(),point={x:box.x+box.width/2-28,y:box.y+box.height/2-28};
  await tile.click();await page.waitForTimeout(280);await page.mouse.move(point.x,point.y);
  const preview=graph.locator('.templatePlacementPreview');await page.waitForFunction(a=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===a,a);
  assert.equal(await preview.locator('.templateBinding[data-binding-key="members"]').count(),1);
  assert.equal(await preview.locator('.hatgNode:has([data-binding-key="members"])').evaluate(node=>getComputedStyle(node).borderRadius),'50%');
  assert.equal(await preview.locator('.hatgNode:not(:has(.templateBinding))').count(),3);
  const expected=await preview.locator('[data-node-id="router_resident"]').boundingBox();
  await page.waitForTimeout(300);await page.screenshot({path:`${folder}/family-collection-anchor-highlight.png`});
  await page.keyboard.press('Escape');assert.deepEqual(await page.evaluate(()=>execute('GetLineage',{lineage_id:lid}).then(r=>r.lineage)),before);
  await tile.click();await page.waitForTimeout(280);await page.mouse.move(point.x,point.y);await page.waitForFunction(a=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===a,a);
  await page.mouse.click(point.x,point.y);await page.locator('.templateSurface-apply').waitFor();await page.locator('.templateSurface-apply button[type="submit"]').click();
  await page.waitForFunction(()=>state.lineage.function_blocks.some(b=>b.family));
  const family=await page.evaluate(()=>state.lineage.function_blocks.find(b=>b.family).family);
  assert.deepEqual([...family.members].sort(),await page.evaluate(()=>[...anchors].sort()));
  const geometry=await page.evaluate(async fid=>{
   const body=(await execute('GetTemplate',{template_id:fid})).template.editor.graph.body,g=LineageTemplateGraph.project(body);
   const ids=new Set(g.nodes.map(n=>n.id)),edges=new Set(g.edges.map(e=>e.id));
   const valid=g.edges.every(e=>ids.has(e.source)&&ids.has(e.target)&&(!e.watch_subject_wire_id||edges.has(e.watch_subject_wire_id)));
   return {valid,unique:new Set(g.edges.map(e=>JSON.stringify([e.type,e.source,e.target]))).size===g.edges.length};
  },fid);assert.deepEqual(geometry,{valid:true,unique:true});
  const landed=await graph.locator(`[data-node-id="${family.router}"]`).boundingBox();
  assert.ok(Math.abs(expected.x+expected.width/2-landed.x-landed.width/2)<2 && Math.abs(expected.y+expected.height/2-landed.y-landed.height/2)<2,JSON.stringify({expected,landed}));
  await page.waitForTimeout(300);await page.screenshot({path:`${folder}/family-landed-same-anchor.png`});
  // Independent workshop views share the projection but no draft/menu state.
  await page.evaluate(async fid=>{panelHost.templateController.destroy();state.graphController.destroy();document.body.innerHTML='<div id="left" style="width:50vw;height:100vh;float:left"></div><div id="right" style="width:50vw;height:100vh;float:right"></div>';const html=await(await fetch('/static/template_workshop_panel.html')).text();for(const id of ['left','right'])document.getElementById(id).innerHTML=html;window.leftWorkshop=LineageTemplateWorkshop.mount({root:document.querySelector('#left .templateWorkshop'),lineageId:lid,templateId:fid,execute});window.rightWorkshop=LineageTemplateWorkshop.mount({root:document.querySelector('#right .templateWorkshop'),lineageId:lid,templateId:fid,execute});await leftWorkshop.load();await rightWorkshop.load();},fid);
  const left=page.locator('#left'),right=page.locator('#right');await left.locator('[data-node-id="member_resident"]').click();assert.match(await left.locator('[data-graph-inspector]').innerText(),/many/);assert.equal(await left.locator('.templateResidentOverlay.open').count(),0);
  assert.equal(await page.evaluate(()=>leftWorkshop.dirty()||rightWorkshop.dirty()),false);
  await right.locator('[data-template-resident-menu="router_resident"]').focus();await page.keyboard.press('Enter');await right.locator('.templateResidentOverlay.open').waitFor();
  assert.equal(await left.locator('.templateResidentOverlay.open').count(),0);await right.locator('[data-menu-close]').click();
  await right.locator('[data-template-resident-menu="steward_resident"]').click();await right.locator('[data-template-menu-tab="memory"]').click();assert.match(await right.locator('.templateResidentOverlay.open').innerText(),/角色记忆/);await right.locator('[data-template-menu-tab="duties"]').click();
  await right.locator('.residentMenuDuty:has([data-template-duty-graph="recovery"]) summary').click();await right.locator('[data-template-duty-graph="recovery"]').click();assert.equal(await right.locator('[data-recipe-back]').isVisible(),true);assert.equal(await page.evaluate(()=>rightWorkshop.dirty()),false);await right.locator('[data-recipe-back]').click();
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){await page.setViewportSize({width,height:1000});await page.evaluate(({theme,width})=>{PortalTheme.setMode(theme);document.getElementById('right').style.display=width===600?'none':'';document.getElementById('left').style.width=width===600?'100vw':'50vw';}, {theme,width});await page.waitForTimeout(150);await left.locator('[data-graph-fit]').click();if(width===1440)await right.locator('[data-graph-fit]').click();await page.waitForTimeout(300);await page.screenshot({path:`${folder}/family-panels-${theme}-${width}.png`});}
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
