'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(path.resolve(__dirname,'../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async()=>{
  const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.stack));page.setDefaultTimeout(10000);
  try {
    await page.goto(process.env.WORKSHOP_TEST_ORIGIN);
    const recipe=await page.evaluate(async lineageId=>{
      PortalI18n.apply('zh-CN');
      window.PortalIdentity={username:()=>'sample_admin',peek:()=>({}),userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
      window.PortalAPI={get:url=>fetch(url).then(r=>r.json())};
      window.PortalNav={personalAssistantTarget:async()=>null,loadChatWorkspace:async()=>{}};
      window.execute=async(instruction,args={},options={})=>{const data=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(data.error)throw Error(data.error.message);return data.result;};
      window.PortalInstructionClient={execute};await PortalHatgLoader.load();
      window.sourceLineageId=lineageId;
      const a=(await execute('CreateResident',{lineage_id:lineageId,title:'Anchor A',provider:'codex',x:30,y:40})).resident_id;
      const b=(await execute('CreateResident',{lineage_id:lineageId,title:'Anchor B',provider:'codex',x:70,y:65})).resident_id;
      window.anchors=[a,b];
      const {template}=await execute('CreateTemplate',{template:{kind:'structure',title:'Anchored structure',body:{
        roles:[{key:'target',from:'target',title:'目标 Resident'}],components:[{key:'event',kind:'event_source',title:'Observe anchor',config:{event_type:'resident.message_received'}},{key:'loop',kind:'loop',title:'Placed loop',config:{max_rounds:3}}],
        wires:[{key:'watch',kind:'watch',source:'event',target:'target'},{key:'route',kind:'route',source:'event',target:'loop'}],
        layout:{target:{x:40,y:40},event:{x:65,y:40},loop:{x:90,y:40}}}}});
      document.body.innerHTML='<main class="portalHatgOverlayComponent" style="display:grid;grid-template-columns:160px 1fr;height:100vh"><aside id="templateShelf" style="overflow:auto"></aside><div id="hatgCanvas" class="hatgCanvas"><div class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g data-layer="edges"></g><g data-layer="effects"></g></svg><div class="hatgEdgeLabels"></div><div class="hatgNodes"></div></div></div></main>';
      const canvas=document.querySelector('#hatgCanvas'),viewport=document.createElement('div');
      canvas.parentElement.classList.add('hatgPane');
      viewport.className='hatgViewport hatgPane';viewport.style='position:relative;min-width:0;height:100%';
      canvas.before(viewport);viewport.append(canvas);
      window.draw=async()=>{state.lineage=(await execute('GetLineage',{lineage_id:lineageId})).lineage;const graph=PortalLineageProgram.projectGraph(state.lineage.program,state.lineage.nodes.map(n=>({...n,kind:'agent'})));state.graphController.render({nodes:graph.nodes,edges:graph.edges,layoutInteractive:true,selectionEnabled:false});};
      window.state={selectedCanvasObjectIds:new Set()};
      state.graphController=PortalHatgGraph.createGraph({root:canvas,world:canvas.firstElementChild,nodeLayer:canvas.querySelector('.hatgNodes'),edgeLayer:canvas.querySelector('[data-layer="edges"]'),effectLayer:canvas.querySelector('[data-layer="effects"]'),labelLayer:canvas.querySelector('.hatgEdgeLabels'),worldWidth:1280,worldHeight:900,nodeContent:node=>`<strong>${PortalUIPrimitives.escapeHtml(node.title||node.id)}</strong>`,portsForNode:node=>PortalLineageProgram.portsForNode(node)});
      await draw();state.graphController.setTransform({x:20,y:30,k:.8},{duration:0});
      window.panelHost={hatgComponentOptions:{emit:()=>{}}};
      const deps={$id:id=>document.getElementById(id),state,panelHost,nodeById:id=>state.lineage.nodes.find(n=>n.id===id),renderGraph:()=>{},toast:(message,tone)=>{if(tone==='error')throw Error(message);},executeHatgInstruction:execute,applyProgramMutation:async work=>{const result=await work();await draw();return result;}};
      PortalHatgPanelTemplateShelf.createTemplateShelf(deps).mountTemplates();await panelHost.templateController.load();
      return template.template_id;
    },process.env.WORKSHOP_TEST_LINEAGE);
    const graph=page.locator('#hatgCanvas'),tile=page.locator(`#templateShelf [data-template-id="${recipe}"]`);
    const canvasBounds=await graph.boundingBox();assert.ok(canvasBounds.x>=160&&canvasBounds.height<=1000,JSON.stringify(canvasBounds));
    assert.equal(await graph.evaluate(n=>getComputedStyle(n).getPropertyValue('--hatg-program-route-color').trim()),'#7654c7','production pane provides the edge palette');
    const snapshot=()=>page.evaluate(()=>execute('GetLineage',{lineage_id:sourceLineageId}).then(r=>r.lineage));
    const before=await snapshot();
    const folder=process.env.TEMPLATE_PLACEMENT_SCREENSHOT_DIR||'/tmp/template-placement-evidence';fs.mkdirSync(folder,{recursive:true});
    const candidate=async index=>{
      const id=await page.evaluate(index=>anchors[index],index),node=graph.locator(`.hatgNode[data-node-id="${id}"]`),box=await node.boundingBox();
      return {id,node,box,point:{x:box.x-18,y:box.y+box.height/2-28}};
    };
    await tile.click();await page.waitForTimeout(280);
    const first=await candidate(0);await page.mouse.move(first.point.x,first.point.y);
    await page.waitForFunction(id=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===id,first.id);
    assert.equal(await first.node.evaluate(n=>n.classList.contains('templateDropTarget')),true);
    assert.ok(first.point.x<first.box.x,'pointer is outside the Resident while its visible anchor binds');
    assert.equal(await graph.locator('.templatePlacementPreview .hatgNode').count(),3);
    assert.ok(await graph.locator('.templatePlacementPreview .hatgEdge').count()>0);
    const waitVisible=selector=>page.waitForFunction(selector=>[...document.querySelectorAll(selector)].every(node=>Number(getComputedStyle(node).opacity)>.95),selector);
    const previewVisible=()=>waitVisible('.templatePlacementPreview .hatgNode');
    await previewVisible();await page.screenshot({animations:'disabled',path:path.join(folder,'pickup-target-highlight.png')});
    const wires=await graph.locator('.templatePlacementPreview .hatgEdge').evaluateAll(nodes=>nodes.map(n=>({box:n.getBoundingClientRect().toJSON(),stroke:getComputedStyle(n).stroke,d:n.getAttribute('d')})));
    assert.ok(wires.some(w=>w.stroke!=='none'&&w.box.width>10&&w.box.right<1440),JSON.stringify(wires));
    const preview=await graph.locator('.templatePlacementPreview .hatgNode[data-node-id="loop"]').boundingBox();
    await page.mouse.click(first.point.x,first.point.y);await page.waitForFunction(()=>state.lineage.program.components.some(c=>c.title==='Placed loop'));
    const after=await snapshot(),loop=after.program.components.find(c=>c.title==='Placed loop');
    assert.equal(after.nodes.length,before.nodes.length);assert.deepEqual(after.nodes.find(n=>n.id===first.id),before.nodes.find(n=>n.id===first.id));
    assert.equal(after.program.wires.find(w=>w.kind==='watch').target,first.id);
    const landed=await graph.locator(`.hatgNode[data-node-id="${loop.id}"]`).boundingBox();
    assert.ok(Math.abs(landed.x+landed.width/2-preview.x-preview.width/2)<2 && Math.abs(landed.y+landed.height/2-preview.y-preview.height/2)<2,JSON.stringify({preview,landed}));
    await waitVisible('#hatgCanvas > .hatgWorld .hatgNode');await page.screenshot({animations:'disabled',path:path.join(folder,'landed-structure.png')});
    // Repeated pickup, candidate change, viewport transform and cancellation
    // never mutate the graph or allow an old highlighted candidate to stick.
    let clean=await snapshot();
    await page.evaluate(()=>state.graphController.setTransform({x:100,y:80,k:.5},{duration:0}));
    await tile.click();await page.waitForTimeout(280);
    const second=await candidate(1);await page.mouse.move(second.point.x,second.point.y);
    await page.waitForFunction(id=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===id,second.id);
    await page.evaluate(()=>state.graphController.setTransform({x:120,y:60,k:.6},{duration:0}));
    await page.waitForFunction(()=>document.querySelector('.templatePlacementPreview')?.dataset.zoom==='0.600');
    await page.evaluate(()=>state.graphController.setTransform({x:100,y:80,k:.5},{duration:0}));
    await page.waitForFunction(()=>document.querySelector('.templatePlacementPreview')?.dataset.zoom==='0.500');
    await page.keyboard.press('Escape');assert.equal(await graph.locator('.templatePlacementPreview').count(),0);assert.deepEqual(await snapshot(),clean);
    // The native shelf drag uses the same temporary graph and visible anchor.
    const tileBox=await tile.boundingBox();await page.mouse.move(tileBox.x+tileBox.width/2,tileBox.y+tileBox.height/2);await page.mouse.down();
    await page.mouse.move(tileBox.x+tileBox.width/2+12,tileBox.y+tileBox.height/2+12,{steps:5});await page.mouse.move(second.point.x,second.point.y,{steps:12});
    await page.mouse.move(second.point.x+1,second.point.y,{steps:2});
    await page.waitForFunction(id=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===id,second.id);
    await previewVisible();await page.screenshot({animations:'disabled',path:path.join(folder,'native-drag-structure.png')});
    await page.keyboard.press('Escape');await page.mouse.up();assert.deepEqual(await snapshot(),clean);
    await page.mouse.move(tileBox.x+tileBox.width/2,tileBox.y+tileBox.height/2);await page.mouse.down();
    await page.mouse.move(tileBox.x+tileBox.width/2+12,tileBox.y+tileBox.height/2+12,{steps:5});
    await page.mouse.move(second.point.x,second.point.y,{steps:12});await page.mouse.move(second.point.x+1,second.point.y,{steps:2});
    await page.waitForFunction(id=>document.querySelector('.templatePlacementPreview')?.dataset.boundResident===id,second.id);
    await previewVisible();const nativePreview=await graph.locator('.templatePlacementPreview .hatgNode[data-node-id="loop"]').boundingBox();
    const oldIds=clean.program.components.map(c=>c.id),oldCount=oldIds.length;
    await page.mouse.up();await page.waitForFunction(count=>state.lineage.program.components.length===count+2,oldCount);
    clean=await snapshot();const nativeLoop=clean.program.components.find(c=>c.title==='Placed loop'&&!oldIds.includes(c.id));
    const nativeLanded=await graph.locator(`.hatgNode[data-node-id="${nativeLoop.id}"]`).boundingBox();
    assert.ok(Math.abs(nativePreview.x+nativePreview.width/2-nativeLanded.x-nativeLanded.width/2)<2);
    assert.equal(clean.nodes.length,before.nodes.length);
    await waitVisible('#hatgCanvas > .hatgWorld .hatgNode');await page.screenshot({animations:'disabled',path:path.join(folder,'native-landed-structure.png')});
    for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
      await page.setViewportSize({width,height:1000});
      await page.waitForTimeout(250); // Let the production viewport resize transition finish before setting the test camera.
      await page.evaluate(theme=>{PortalTheme.setMode(theme);state.graphController.setTransform({x:100,y:40,k:.35},{duration:0});},theme);
      await tile.click();await page.waitForTimeout(280);const anchor=await candidate(0);await page.mouse.move(anchor.point.x,anchor.point.y);
      await page.waitForFunction(()=>document.querySelectorAll('.templatePlacementPreview .hatgNode').length===3);await previewVisible();await page.screenshot({animations:'disabled',path:path.join(folder,`preview-${theme}-${width}.png`)});await page.keyboard.press('Escape');
    }
    await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>{PortalTheme.setMode('auto');state.graphController.setTransform({x:100,y:80,k:.5},{duration:0});});
    await tile.click();await page.waitForTimeout(280);await page.mouse.move(1200,800);
    assert.equal(await page.locator('.templateDropHint').getAttribute('data-allowed'),'false');
    await page.mouse.click(1200,800);await page.keyboard.press('Escape');assert.deepEqual(await snapshot(),clean,'invalid binding creates nothing');
    const effectId=await page.evaluate(async()=>{
      const {template}=await execute('CreateTemplate',{template:{kind:'structure',title:'Effect on canvas',body:{roles:[],components:[{key:'effect',kind:'effect',title:'Canvas effect',config:{script:'',endpoints:[]}}],wires:[]}}});
      window.dispatchEvent(new Event('portal:templates-changed'));return template.template_id;
    });
    await page.locator(`[data-template-id="${effectId}"]`).click();await page.waitForTimeout(280);await page.mouse.move(1200,800);
    await previewVisible();const effectPreview=await graph.locator('.templatePlacementPreview .hatgNode').boundingBox();
    await page.mouse.click(1200,800);await page.waitForFunction(()=>state.lineage.program.components.some(c=>c.title==='Canvas effect'));
    const withEffect=await snapshot(),effect=withEffect.program.components.find(c=>c.title==='Canvas effect');
    assert.equal(withEffect.nodes.length,clean.nodes.length);
    const effectLanded=await graph.locator(`.hatgNode[data-node-id="${effect.id}"]`).boundingBox();
    assert.ok(Math.abs(effectLanded.x+effectLanded.width/2-effectPreview.x-effectPreview.width/2)<2);
    // Family previews are generated by the existing recipe owner, not a second
    // hand-written diagram. Reading and canceling one creates no graph objects.
    const family=await page.evaluate(async()=>{state.selectedCanvasObjectIds=new Set(anchors);return (await execute('ListTemplates')).templates.find(t=>t.builtin_key==='family').template_id;});
    await page.locator(`[data-template-id="${family}"]`).click();await page.waitForTimeout(280);
    const anchor=await candidate(0);await page.mouse.move(anchor.point.x,anchor.point.y);
    await page.waitForFunction(()=>document.querySelectorAll('.templatePlacementPreview .hatgNode').length>3);
    await page.keyboard.press('Escape');assert.deepEqual(await snapshot(),withEffect);
    // The same drag into a workshop modifies only its local draft.
    await page.evaluate(async()=>{
      panelHost.templateController.destroy();state.graphController.destroy();
      document.body.innerHTML=await(await fetch('/static/template_workshop_panel.html')).text();
      window.workshop=LineageTemplateWorkshop.mount({root:document.querySelector('.templateWorkshop'),lineageId:sourceLineageId,execute});await workshop.load();
    });
    const compositionTile=page.locator(`[data-workshop-library] [data-template-id="${recipe}"]`),canvas=page.locator('.templateGraphCanvas');
    const compositionBounds=await canvas.boundingBox();
    await compositionTile.dragTo(canvas,{targetPosition:{x:compositionBounds.width/2,y:compositionBounds.height/2}});
    assert.equal(await page.evaluate(()=>workshop.template().body.components.length),2);
    assert.equal(await page.evaluate(()=>workshop.template().body.roles.length),1);
    assert.equal(await page.evaluate(()=>workshop.dirty()),true);
    assert.deepEqual(await snapshot(),withEffect,'composition does not apply the template to the live graph');
    assert.deepEqual(errors,[]);
  } catch(error) {
    await page.screenshot({path:'/tmp/template-placement-failure.png'});
    console.error(JSON.stringify({errors,diagnostic:await page.evaluate(()=>({
      preview:document.querySelector('.templatePlacementPreview')?.outerHTML.slice(0,400),
      hint:document.querySelector('.templateDropHint')?.outerHTML,
      picking:document.querySelector('#hatgCanvas')?.className,
      nodes:[...document.querySelectorAll('#hatgCanvas > .hatgWorld .hatgNode')].map(n=>({id:n.dataset.nodeId,box:n.getBoundingClientRect().toJSON()}))}))}));
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
