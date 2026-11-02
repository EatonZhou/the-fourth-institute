'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {LINEAGE_GRAPH_SCRIPTS}=require('./lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES}=require('./hatg_panel_styles.cjs');
const root=path.resolve(__dirname,'../../../management_portal/static');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
   const name=new URL(route.request().url()).pathname;
   if(name.startsWith('/static/'))return route.fulfill({body:fs.readFileSync(path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'});
   return route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><div class="portalHatgPanelComponent"><div class="hatgPane" style="width:1000px;height:800px"><div id="canvas" class="hatgCanvas" style="width:1000px;height:760px;flex:none"><div id="world" class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g id="edges"></g><g id="effects"></g></svg><div id="labels" class="hatgEdgeLabels"></div><div id="nodes" class="hatgNodes"></div></div></div></div></div></body></html>'});
  });
  await page.goto('http://edge-label.fixture.invalid');
  await page.evaluate(()=>{document.documentElement.dataset.portalTheme='light';document.documentElement.dataset.portalMotion='full';});
  for(const n of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES,'lineage_function_blocks'])await page.addStyleTag({url:'/static/'+n+'.css'});
  for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min',...LINEAGE_GRAPH_SCRIPTS])await page.addScriptTag({url:'/static/'+n+'.js'});
  await page.evaluate(()=>{
   window.graph=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',scaleExtent:[.05,3.2],nodeContent:node=>'<strong>'+PortalUIPrimitives.escapeHtml(node.title)+'</strong>',onEdgeClick:edge=>{window.clickedEdgeId=edge.id;}});
   graph.render({nodes:[{id:'a',x:20,y:20},{id:'b',x:80,y:80},{id:'c',x:20,y:80},{id:'d',x:80,y:20}].map(n=>({...n,kind:'agent',title:n.id})),edges:[{id:'one',source:'a',target:'b',type:'route',label:'Route'},{id:'two',source:'c',target:'d',type:'route',label:'Route'}]});
  });
  const verifyPathAttachment=async stage=>{
   const canvas=await page.locator('#canvas').boundingBox();assert.ok(canvas&&canvas.width>100&&canvas.height>100,stage+': canvas must have real area');
   if(await page.locator('.edgeLabelBtn:visible').count()===0){
    assert.ok(await page.locator('#canvas').evaluate(node=>node.dataset.semanticZoom==='overview'||node.dataset.lod==='compact'),stage+': labels are hidden only by native overview mode');
    return;
   }
   const distances=await page.locator('.edgeLabelBtn').evaluateAll(labels=>labels.map(label=>{
    const edge=[...document.querySelectorAll('path.hatgEdge')].find(edge=>edge.dataset.edgeId===label.dataset.edgeId);
    if(getComputedStyle(edge).stroke==='none')throw new Error('The production route must be visibly painted');
    const bounds=label.getBoundingClientRect(),center={x:bounds.x+bounds.width/2,y:bounds.y+bounds.height/2};
    const length=edge.getTotalLength(),matrix=edge.getScreenCTM();let nearest=Infinity;
    for(let i=0;i<=800;i++){
     const point=edge.getPointAtLength(length*i/800).matrixTransform(matrix);
     nearest=Math.min(nearest,Math.hypot(center.x-point.x,center.y-point.y));
    }
    const leader=[...document.querySelectorAll('line.hatgEdgeLabelLeader')].find(line=>line.dataset.edgeId===label.dataset.edgeId);
    let anchorDistance=Infinity,labelDistance=Infinity;
    if(leader&&getComputedStyle(leader).stroke!=='none'){
     const transform=leader.getScreenCTM();
     const start=new DOMPoint(leader.x1.baseVal.value,leader.y1.baseVal.value).matrixTransform(transform);
     const end=new DOMPoint(leader.x2.baseVal.value,leader.y2.baseVal.value).matrixTransform(transform);
     labelDistance=Math.hypot(center.x-end.x,center.y-end.y);
     for(let i=0;i<=800;i++){
      const point=edge.getPointAtLength(length*i/800).matrixTransform(matrix);
      anchorDistance=Math.min(anchorDistance,Math.hypot(start.x-point.x,start.y-point.y));
     }
    }
    return {id:label.dataset.edgeId,distance:nearest,linked:anchorDistance<3&&labelDistance<3};
   }));
   if(!distances.every(row=>row.distance<3||row.linked)&&process.env.TAU_EDGE_LABEL_SCREENSHOT)
    await page.screenshot({path:process.env.TAU_EDGE_LABEL_SCREENSHOT});
   assert.ok(distances.every(row=>row.distance<3||row.linked),stage+': labels follow or explicitly link to their own actual curves '+JSON.stringify(distances));
  };
  const verify=async stage=>{
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const b=await page.locator('.edgeLabelBtn').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom};}));
   assert.equal(b.length,2);
   assert.ok(b[0].right<=b[1].left || b[1].right<=b[0].left || b[0].bottom<=b[1].top || b[1].bottom<=b[0].top,stage+JSON.stringify(b));
   await verifyPathAttachment(stage);
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
  await page.evaluate(()=>graph.setTransform({...graph.getTransform(),k:1}));await verify('resized operate view');
  // The original TAU failure: an intervening Event card detours a Route,
  // while collision handling previously moved its label independently.
  await page.locator('#canvas').evaluate(n=>Object.assign(n.style,{width:'1000px',height:'760px'}));
  await page.evaluate(()=>graph.render({
   nodes:[{id:'source',x:15,y:45},{id:'blocker',x:50,y:45},{id:'target',x:85,y:45}]
    .map(n=>({...n,kind:'event_source',title:n.id})),
   edges:[{id:'detour-route',source:'source',target:'target',type:'route',label:'Route'}],
  }));
  await page.evaluate(()=>graph.fit({duration:0}));
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('path.hatgEdge').getAttribute('data-routing'),'obstacle');
  await verifyPathAttachment('detour fit');
  await page.evaluate(()=>graph.refresh());await verifyPathAttachment('detour refreshed');
  if(process.env.TAU_EDGE_LABEL_SCREENSHOT)await page.screenshot({path:process.env.TAU_EDGE_LABEL_SCREENSHOT});
  // Two non-overlapping cards can leave less room than their Route label.
  // Endpoint cards are excluded from path detours, but must still exclude labels.
  await page.locator('#canvas').evaluate(n=>Object.assign(n.style,{width:'1000px',height:'760px'}));
  await page.evaluate(()=>{
   const canvas=document.querySelector('#canvas');
   const width=Number(canvas.dataset.worldWidth);
   const cardWidth=document.querySelector('.hatgNode').offsetWidth;
   graph.render({nodes:[{id:'near-a',x:40,y:50},{id:'near-b',x:40+(cardWidth+10)/width*100,y:50}]
     .map(n=>({...n,kind:'agent',title:n.id})),
     edges:[{id:'near-route',source:'near-a',target:'near-b',type:'route',label:'Route'}]});
  });
  const verifyCards=async stage=>{
   await page.waitForFunction(()=>{
    const nodes=[...document.querySelectorAll('.hatgNode')];
    return nodes.length===2&&nodes.every(node=>['near-a','near-b'].includes(node.dataset.nodeId));
   });
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const overlaps=await page.evaluate(()=>{
    const label=document.querySelector('.edgeLabelBtn').getBoundingClientRect();
    return [...document.querySelectorAll('.hatgNode')].filter(node=>{
     const card=node.getBoundingClientRect();
     return label.left<card.right && label.right>card.left && label.top<card.bottom && label.bottom>card.top;
    }).map(node=>node.dataset.nodeId);
   });
   assert.deepEqual(overlaps,[],stage+': Route label must not be covered by endpoint cards');
   await verifyPathAttachment(stage);
  };
  await verifyCards('close endpoints');
  await page.evaluate(()=>graph.refresh());await verifyCards('close endpoints refreshed');
  const nearCard=await page.locator('.hatgNode[data-node-id="near-a"]').boundingBox();
  const nearX=nearCard.x+nearCard.width/2,nearY=nearCard.y+nearCard.height/2;
  await page.mouse.move(nearX,nearY);await page.mouse.down();
  await page.mouse.move(nearX-8,nearY+8,{steps:3});
  assert.equal(await page.locator('line.hatgEdgeLabelLeader').count(),0,'a moving curve does not retain a stale leader');
  await page.mouse.move(nearX,nearY,{steps:3});await page.mouse.up();
  await verifyCards('close endpoints dragged');
  if(process.env.TAU_EDGE_LABEL_SCREENSHOT)await page.screenshot({path:process.env.TAU_EDGE_LABEL_SCREENSHOT});
  await page.evaluate(()=>graph.fit({duration:0}));await verifyCards('close endpoints fit');
  await page.evaluate(()=>graph.render({nodes:[{id:'near-a',kind:'agent',title:'a',x:15,y:20},
    {id:'near-b',kind:'agent',title:'b',x:85,y:80}],edges:[{id:'near-route',source:'near-a',target:'near-b',type:'route',label:'Route'}]}));
  await page.evaluate(()=>graph.refresh());
  await verifyPathAttachment('separated endpoints');
  assert.equal(await page.locator('line.hatgEdgeLabelLeader').count(),0,'obsolete leaders disappear when the curve can hold its label');
  // Screenshot regression: a continuation label fits between staggered cards,
  // but counting clearance on both cards and labels sent it above both cards.
  await page.evaluate(()=>graph.render({nodes:[
   {id:'inherit-source',kind:'agent',title:'HATG ISA检查与润色 · 分支',x:30,y:50},
   {id:'inherit-target',kind:'agent',title:'Projects页面重新设计',x:50,y:45},
  ],edges:[{id:'continuation',source:'inherit-source',target:'inherit-target',type:'inheritance',label:'从消息继续'}]}));
  const inheritanceFixture=await page.evaluate(()=>{
   const width=Number(document.querySelector('#canvas').dataset.worldWidth);
   const height=Number(document.querySelector('#canvas').dataset.worldHeight);
   const cardWidth=document.querySelector('.hatgNode').offsetWidth;
   const labelWidth=document.querySelector('.edgeLabelBtn').offsetWidth;
   // The real label fits with at least eight world pixels on each side.
   const gap=labelWidth+20;
   return {nodes:[
    {id:'inherit-source',kind:'agent',title:'HATG ISA检查与润色 · 分支',x:30,y:50},
    {id:'inherit-target',kind:'agent',title:'Projects页面重新设计',x:30+(cardWidth+gap)/width*100,y:50-43/height*100},
   ],edges:[{id:'continuation',source:'inherit-source',target:'inherit-target',type:'inheritance',label:'从消息继续'}]};
  });
  await page.evaluate(model=>{graph.render(model);graph.fit({duration:0,maxScale:1.8});},inheritanceFixture);
  const verifyContinuation=async stage=>{
   await page.waitForFunction(()=>{
    const nodes=[...document.querySelectorAll('.hatgNode')];
    return nodes.length===2&&nodes.every(node=>node.dataset.nodeId.startsWith('inherit-'));
   });
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const geometry=await page.evaluate(()=>{
    const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
    return {cards:[...document.querySelectorAll('.hatgNode')].map(rect),label:rect(document.querySelector('.edgeLabelBtn')),
     leaders:document.querySelectorAll('line.hatgEdgeLabelLeader').length,transform:graph.getTransform()};
   });
   console.log(stage,JSON.stringify(geometry));
   if(stage==='continuation fit'&&process.env.TAU_EDGE_LABEL_SCREENSHOT)await page.screenshot({path:process.env.TAU_EDGE_LABEL_SCREENSHOT.replace(/\.png$/, '-continuation.png')});
   assert.equal(geometry.leaders,0,stage+': a continuation label that fits in the gap stays on its own curve');
   for(const card of geometry.cards)assert.ok(geometry.label.right<=card.left||geometry.label.left>=card.right
    ||geometry.label.bottom<=card.top||geometry.label.top>=card.bottom,stage+': label must clear both cards');
   await verifyPathAttachment(stage);
  };
  await verifyContinuation('continuation fit');
  await page.evaluate(()=>graph.refresh());await verifyContinuation('continuation refresh');
  await page.evaluate(()=>graph.setTransform({x:0,y:0,k:1},{duration:0}));await verifyContinuation('continuation zoom');
  const inheritanceSource=await page.locator('.hatgNode[data-node-id="inherit-source"]').boundingBox();
  const inheritanceX=inheritanceSource.x+inheritanceSource.width/2,inheritanceY=inheritanceSource.y+inheritanceSource.height/2;
  await page.mouse.move(inheritanceX,inheritanceY);await page.mouse.down();
  await page.mouse.move(inheritanceX-12,inheritanceY+8,{steps:3});
  await page.mouse.move(inheritanceX,inheritanceY,{steps:3});await page.mouse.up();
  await verifyContinuation('continuation drag');
  await page.locator('.edgeLabelBtn[data-edge-id="continuation"]').click();
  assert.equal(await page.evaluate(()=>window.clickedEdgeId),'continuation','the attached label still opens its own relationship');
  // A Route leaving a right-side output must approach the left-side input
  // from outside the card, including when its target is behind the source.
  const verifyReturnRoute=async stage=>{
   await page.waitForFunction(()=>{
    const nodes=[...document.querySelectorAll('.hatgNode')];
    return nodes.length===3&&nodes.every(node=>node.dataset.nodeId.startsWith('return-'));
   });
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const crossings=await page.evaluate(()=>{
    const edge=document.querySelector('path.hatgEdge[data-edge-id="return-route"]');
    const length=edge.getTotalLength(),matrix=edge.getScreenCTM();
    return [...document.querySelectorAll('.hatgNode')].filter(node=>{
     const r=node.getBoundingClientRect();
     for(let i=1;i<1200;i++){
      const p=edge.getPointAtLength(length*i/1200).matrixTransform(matrix);
      if(p.x>r.left+1&&p.x<r.right-1&&p.y>r.top+1&&p.y<r.bottom-1)return true;
     }
     return false;
    }).map(node=>node.dataset.nodeId);
   });
   assert.deepEqual(crossings,[],stage+': Route must avoid endpoint and intervening card interiors');
   await verifyPathAttachment(stage);
  };
  for(const [name,source,target] of [
   ['backward',{x:85,y:45},{x:15,y:45}],
   ['backward above',{x:85,y:65},{x:15,y:25}],
   ['backward below',{x:85,y:25},{x:15,y:65}],
   ['vertical',{x:50,y:15},{x:50,y:85}],
  ]){
   await page.evaluate(({source,target})=>graph.render({nodes:[
    {id:'return-source',kind:'reducer',title:'Join',...source,ports:[{port_id:'output',type:'route',direction:'out'}]},
    {id:'return-target',kind:'agent',title:'Resident title must remain readable',...target,ports:[{port_id:'invoke',type:'invoke',direction:'in'}]},
    {id:'return-blocker',kind:'filter',title:'Filter',x:50,y:45},
   ],edges:[{id:'return-route',source:'return-source',target:'return-target',type:'route',wire_type:'route',
    _program_wire:true,source_port:'output',target_port:'invoke',label:'Route'}]}),{source,target});
   await page.evaluate(()=>graph.fit({duration:0}));await verifyReturnRoute(name+' fit');
   await page.evaluate(()=>graph.refresh());await verifyReturnRoute(name+' refresh');
   await page.locator('#canvas').evaluate(n=>Object.assign(n.style,{width:'700px',height:'620px'}));
   await verifyReturnRoute(name+' resize');
   await page.locator('#canvas').evaluate(n=>Object.assign(n.style,{width:'1000px',height:'760px'}));
  }
  if(process.env.TAU_EDGE_LABEL_SCREENSHOT)await page.screenshot({path:process.env.TAU_EDGE_LABEL_SCREENSHOT});
  assert.deepEqual(errors,[]);console.log('Route labels and paths avoid cards through geometry changes');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
