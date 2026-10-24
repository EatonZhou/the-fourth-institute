'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {babelParse, traverse} = require('./babel_parse.cjs');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(root, 'hatg_panel.js'), 'utf8');
const names = new Set(['separateGraphObject', 'separateGraphObjects', 'setFunctionBlockExpanded', 'applyProgramMutation', 'createLineageEditQueue']);
const parts = [];
traverse(babelParse(source), {FunctionDeclaration(p) {
  if (names.has(p.node.id?.name)) parts.push(source.slice(p.node.start, p.node.end));
}});
assert.equal(parts.length, names.size);
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1364, height: 844}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, name.slice(8))), contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><div class="portalHatgPanelComponent"><div id="canvas" class="hatgCanvas" style="width:1300px;height:760px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>'});
    });
    await page.goto('http://object-separation.fixture.invalid');
    for (const n of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks']) await page.addStyleTag({url: '/static/'+n+'.css'});
    for (const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_graph']) await page.addScriptTag({url:'/static/'+n+'.js'});
    await page.evaluate(parts => {
      window.saved = []; window.failSave = false; window.requestCount = 0;
      const title = '[TAU:testrun_3bb414d982c23e92:lineage.hatg] ';
      const inner = {id:'inner', title:title+'Inner renamed', x:50, y:50, member_ids:['event','resident']};
      const outer = {id:'outer', title:title+'Outer', x:50, y:50, member_ids:['event','resident','extra'], child_block_ids:['inner']};
      window.state = {lineage:{lineage_id:'fixture'}, graphViewMode:'context', graphFocusMode:false};
      window.nodes = [{id:'event',kind:'event_source',title:title+'Manual',x:35,y:50},
        {id:'resident',kind:'agent',title:title+'Member',x:65,y:63},
        {id:'extra',kind:'event_source',title:title+'Outer extra',x:32,y:35}];
      const blocks = [inner, outer]; const expanded = new Set(['inner','outer']);
      const graphItemById = id => nodes.find(n=>n.id===id) || blocks.find(n=>n.id===id);
      const functionBlockInstanceById = id => blocks.find(n=>n.id===id);
      const functionBlockElements = block => block.member_ids.map(id=>({id}));
      window.graph = PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',
        nodeContent:n=>`<small>${n.kind}</small><strong>${PortalUIPrimitives.escapeHtml(n.title)}</strong><small>Listening · idle</small><small>0 messages</small>`});
      state.graphController = graph;
      const graphVisibleModel = () => ({nodes,edges:[],functionBlockBoundaries:blocks});
      const renderGraph = () => graph.render(graphVisibleModel());
      const renderLineageSurface = renderGraph;
      const saveLineageLayout = items => lineageEditQueue.enqueue(intent, async()=> {
        if (failSave) throw Error('layout unavailable');
        saved.push(items.map(({id,x,y})=>({id,x,y})));
      }, {layout:true});
      const functionBlockPreferenceKey = ()=>'fixture.blocks';
      const expandedFunctionBlockIds = () => expanded;
      const programFeature = {beginMutation(){},endMutation(){}};
      const renderProgramInspector = ()=>{},toast = ()=>{};
      const commitLineage = () => renderGraph();
      const flushHatgViewPreferenceSave = () => Promise.resolve();
      eval(parts.join('\n')+'\nwindow.makeQueue = createLineageEditQueue;window.separate = separateGraphObject;window.expand = setFunctionBlockExpanded;window.mutate = applyProgramMutation;');
      const lineageEditQueue = window.makeQueue(), intent = lineageEditQueue.activate('fixture');
      window.resetQueue = () => lineageEditQueue.activate('fixture');
      window.render = renderGraph;
      window.bounds = () => {
        const header=document.querySelector('[data-function-block-boundary="inner"] header').getBoundingClientRect();
        const card=document.querySelector('[data-node-id="extra"]').getBoundingClientRect();
        const intersects=(a,b)=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>.1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>.1;
        return {overlap:intersects(header,card),header:header.toJSON(),card:card.toJSON()};
      };
      renderGraph();graph.fit({duration:0,maxScale:1});
    }, parts);
    const before = await page.evaluate(()=>bounds());
    assert.equal(before.overlap, true, 'fixture reproduces a sibling obscuring the expanded title');
    const original = await page.evaluate(()=>nodes.map(({id,x,y})=>({id,x,y})));
    await page.evaluate(()=>expand('inner',true));
    await page.waitForFunction(()=>saved.length===1);
    const after = await page.evaluate(()=>({bounds:bounds(),nodes:nodes.map(({id,x,y})=>({id,x,y})),saved}));
    assert.equal(after.bounds.overlap,false);
    assert.deepEqual(after.nodes.find(n=>n.id==='extra'),original.find(n=>n.id==='extra'),'sibling does not move');
    const oldDistance=original[1].x-original[0].x, newDistance=after.nodes[1].x-after.nodes[0].x;
    assert.equal(newDistance,oldDistance,'members translate together');
    assert.ok(after.saved[0].some(n=>n.id==='inner'),'collapsed anchor is saved too');
    await page.evaluate(()=>{nodes=JSON.parse(JSON.stringify(nodes));render();graph.fit({duration:0,maxScale:1});});
    assert.equal((await page.evaluate(()=>bounds())).overlap,false,'persisted reload remains clear');
    await page.evaluate(()=>{expand('inner',true);});
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    assert.equal(await page.evaluate(()=>saved.length),1,'re-expansion is stable');
    // A saved long title grows a component into a nearby card. The actual
    // mutation owner must wait for queued placement without a queue deadlock.
    const resized=await page.evaluate(async()=>{
      nodes=[{id:'edited',kind:'event_source',title:'Short',x:50,y:35},{id:'other',kind:'agent',title:'Nearby Resident',x:50,y:48}];
      render();
      const before={...nodes[1]};
      await mutate(async()=>{requestCount++;nodes[0].title='Long marked title '.repeat(8);return {component_id:'edited',lineage:state.lineage};});
      const rects=[...document.querySelectorAll('.hatgNode')].map(n=>n.getBoundingClientRect().toJSON());
      return {rects,before,after:nodes[1],requests:requestCount,saves:saved.length};
    });
    const [a,b]=resized.rects;
    assert.ok(a.bottom<=b.top || b.bottom<=a.top || a.right<=b.left || b.right<=a.left);
    assert.deepEqual(resized.after,resized.before);assert.equal(resized.requests,1);assert.equal(resized.saves,2);
    const failure=await page.evaluate(async()=>{
      failSave=true; nodes[0].x=nodes[1].x;nodes[0].y=nodes[1].y;render();
      const result=await mutate(async()=>{requestCount++;return {component_id:'edited',lineage:state.lineage};});
      return {id:result.component_id,requests:requestCount};
    });
    assert.deepEqual(failure,{id:'edited',requests:2},'failed placement never replays committed definition');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',before:before.overlap,after:after.bounds.overlap,saves:resized.saves}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
