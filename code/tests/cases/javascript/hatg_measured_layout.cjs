'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {babelParse} = require('./babel_parse.cjs');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(root, 'hatg_panel.js'), 'utf8');
const names = ['readableAutoLayoutOptions', 'AUTO_LAYOUT_METRICS', 'HATG_WORLD_SIZE'];
const parts = {};
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (names.includes(node.id?.name)) parts[node.id.name] =
    (node.type === 'VariableDeclarator' ? 'const ' : '') + source.slice(node.start, node.end) + ';';
  for (const child of Object.values(node)) {
    if (Array.isArray(child)) child.forEach(visit);
    else if (child && typeof child === 'object') visit(child);
  }
}
visit(babelParse(source, 'hatg_panel.js'));
names.forEach(name => assert.ok(parts[name], name));
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const name = new URL(route.request().url()).pathname;
      if (name.startsWith('/static/')) return route.fulfill({body:fs.readFileSync(path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'});
      return route.fulfill({contentType:'text/html',body:'<!doctype html><div class="portalHatgPanelComponent"><div id="hatgCanvas" class="hatgCanvas" style="width:900px;height:740px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>'});
    });
    await page.goto('http://measured-layout.fixture.invalid');
    for (const name of ['portal_design_system','portal_themes','hatg_panel','lineage_function_blocks']) await page.addStyleTag({url:'/static/'+name+'.css'});
    for (const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min','lineage_graph']) await page.addScriptTag({url:'/static/'+name+'.js'});
    await page.evaluate(parts => {
      window.graph = PortalHatgGraph.createGraph({root:'#hatgCanvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',scaleExtent:[.05,3.2],
        nodeContent: node => `<strong class="nodeTitle">${PortalUIPrimitives.escapeHtml(node.title)}</strong><small>${node.id} — runtime idle</small><small>Context: current input</small><small>Output port: completed</small><small>Input port: event</small>`});
      const state = {graphController:graph}, $id = id => document.getElementById(id);
      eval(parts.join('\n')+'\nwindow.layoutOptions = readableAutoLayoutOptions;');
      window.nodes = Array.from({length:9}, (_,i) => ({id:'node_'+i,kind:['agent','event_source','effect','filter'][i%4],
        title:'[TAU:testrun_3d217e86b7d069b0:lineage.hatg] '+['Resident B','Completion event with a long distinguishable title','Effect A','Final filter'][i%4],x:30+i*3,y:30+i*3}));
      window.edges = [{source:'node_0',target:'node_1'},{source:'node_1',target:'node_2'},{source:'node_1',target:'node_3'},
        {source:'node_2',target:'node_4'},{source:'node_3',target:'node_5'},{source:'node_4',target:'node_0',type:'watch'},
        {source:'node_6',target:'node_7'},{source:'node_7',target:'node_8'}].map((edge,i) => ({id:'edge_'+i,...edge}));
      graph.render({nodes,edges});
    }, names.map(name => parts[name]));
    const results = [];
    for (const density of ['comfortable','compact']) {
      const result = await page.evaluate(density => {
        const sizes = graph.getNodeSizes();
        const positions = PortalHatgGraph.computeBranchAwareLayout(nodes,edges,{...layoutOptions(nodes,density),feedbackEdgeTypes:['watch','target']});
        nodes = nodes.map(node => ({...node,...positions.get(node.id)}));
        graph.render({nodes,edges});
        graph.fit({duration:0,maxScale:1,minScale:.05});
        return {density,maximumHeight:Math.max(...Object.values(sizes).map(s=>s.height*9)),sizes};
      }, density);
      assert.ok(result.maximumHeight>94, 'fixture must exceed the old fixed card height: '+JSON.stringify(result));
      for (const reload of [false,true]) {
        if (reload) await page.evaluate(() => {nodes=JSON.parse(JSON.stringify(nodes));graph.render({nodes,edges});graph.fit({duration:0,maxScale:1,minScale:.05});});
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        const geometry = await page.evaluate(() => {
          const canvas=document.getElementById('hatgCanvas').getBoundingClientRect();
          const cards=[...document.querySelectorAll('#nodes .hatgNode')].map(el=>({id:el.dataset.nodeId,r:el.getBoundingClientRect()}));
          const overlaps=[],clipped=[];
          cards.forEach(({id,r},i)=>{
            if(r.left<canvas.left-.5||r.top<canvas.top-.5||r.right>canvas.right+.5||r.bottom>canvas.bottom+.5)clipped.push(id);
            cards.slice(i+1).forEach(other=>{if(Math.min(r.right,other.r.right)-Math.max(r.left,other.r.left)>.5&&Math.min(r.bottom,other.r.bottom)-Math.max(r.top,other.r.top)>.5)overlaps.push([id,other.id]);});
          });
          return {overlaps,clipped,sizes:graph.getNodeSizes()};
        });
        assert.deepEqual(geometry.overlaps,[],`${density}, reload=${reload}`);
        assert.deepEqual(geometry.clipped,[],`${density}, reload=${reload}`);
        assert.deepEqual(geometry.sizes,result.sizes,'world geometry is independent of fit zoom');
      }
      results.push({density,maximumHeight:result.maximumHeight});
    }
    assert.deepEqual(errors,[]);
    if(process.env.HATG_MEASURED_SCREENSHOT) await page.screenshot({path:process.env.HATG_MEASURED_SCREENSHOT});
    console.log(JSON.stringify({status:'passed',results}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
