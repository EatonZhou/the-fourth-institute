'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..'),root=code+'/management_portal/static';
const {chromium}=require(code+'/management_portal/test_framework/tau_runtime/node_modules/playwright');
const {babelParse,traverse}=require(code+'/tests/cases/javascript/babel_parse.cjs');
const {LINEAGE_GRAPH_SCRIPTS}=require(code+'/tests/cases/javascript/lineage_graph_scripts.cjs');
const {HATG_PANEL_STYLES}=require(code+'/tests/cases/javascript/hatg_panel_styles.cjs');
const source=fs.readFileSync(root+'/hatg_family_blocks.js','utf8'); let memberTag;
traverse(babelParse(source),{FunctionDeclaration(p){if(p.node.id?.name==='familyMemberTag') memberTag=source.slice(p.node.start,p.node.end)}});
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const name=new URL(route.request().url()).pathname;
  if(name.startsWith('/static/')) {const draft=process.env.FORK_DRAFT?path.join(process.env.FORK_DRAFT,name.slice(8)):''; return route.fulfill({body:fs.readFileSync(draft&&fs.existsSync(draft)?draft:path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'})}
  return route.fulfill({contentType:'text/html',body:'<!doctype html><div class="portalHatgPanelComponent" style="position:absolute;left:780px;top:80px"><div id="canvas" class="hatgCanvas" style="width:360px;height:530px"><div id="world" class="hatgWorld"><svg id="edges"></svg><svg id="effects"></svg><div id="labels"></div><div id="nodes"></div></div></div></div>'})});
 await page.goto('http://fork-native.fixture.invalid');
 for(const n of ['portal_design_system','portal_themes',...HATG_PANEL_STYLES,'lineage_function_blocks'])await page.addStyleTag({url:'/static/'+n+'.css'});
 for(const n of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','resident_protocol','projection_libraries/portal-graph/1.0.0/geometry','vendor/d3/d3.v7.9.0.min',...LINEAGE_GRAPH_SCRIPTS,'hatg_graph_view','hatg_resident_dialogs','hatg_layout'])await page.addScriptTag({url:'/static/'+n+'.js'});
 for (const scenario of ['overlap','clear','save-failure']) {
 const result=await page.evaluate(async ({memberTag,scenario})=>{
  document.documentElement.style.zoom='1.25';
  const title='[TAU:testrun_4f71ed8ceed79720:resident.conversation] Watchboard source';
  const source={id:'resident_source',kind:'agent',title,x:60,y:50,_family_member:{role:'member',title:'Portal 开发'}};
  const components=[{id:'effect_existing',kind:'effect',title:'Watchboard · run-once',config:{},x:60,y:69},{id:'event_existing',kind:'event_source',title:'Watchboard · status',config:{},x:60,y:26}];
  const state={lineage:{lineage_id:'lineage_fixture',nodes:[source],program:{components}},graphViewMode:'context',graphFocusMode:false,localVersion:0};
  const calls=[],saves=[],notices=[]; const create=(m,values)=>m[Object.keys(m).find(k=>k.startsWith('create'))](Object.fromEntries(m.dependencies.map(n=>[n,Object.hasOwn(values,n)?values[n]:()=>{throw Error('Unexpected '+n)}])));
  const esc=PortalUIPrimitives.escapeHtml, familyMemberTag=eval('('+memberTag+')');
  const view=create(PortalHatgGraphView,{state,esc,isFunctionBlockInstance:()=>false,isProgramComponent:n=>['event_source','effect'].includes(n.kind),programExecutionExplanation:()=>({state:'idle',summary:'Waiting for event'}),programEventSourceStatus:()=>({state:'listening',label:'Listening',reason:''}),programEventSourceRuntime:()=>({}),programComponentPaused:()=>false,normalizeOptionalLimit:v=>v==null?null:Number(v),effectScriptLabel:()=> 'RunScript',nodeResidentId:n=>n.id||'',nodeIntakeState:()=> 'open',nodeWorkPresentation:(n,status)=>({label:status,phase:'',title:''}),nodeExecutionGate:()=>PortalResidentProtocol.executionGateView(null),familyMemberTag,familyForFacade:()=>null,nodeIsDerived:()=>false,nodeMessageCountText:()=> '0 messages',nodeContextHeatBadge:()=>'',compactText:(s,max)=>s.length>max?s.slice(0,max-1)+'…':s});
  const graph=PortalHatgGraph.createGraph({root:'#canvas',world:'#world',edgeLayer:'#edges',effectLayer:'#effects',labelLayer:'#labels',nodeLayer:'#nodes',nodeContent:view.graphNodeContent});state.graphController=graph;
  const all=()=>[...state.lineage.nodes,...state.lineage.program.components],nodeById=id=>all().find(n=>n.id===id);
  const renderGraph=()=>graph.render({nodes:all(),edges:[]});
  const layout=create(PortalHatgLayout,{state,graphItemById:nodeById,functionBlockInstanceById:()=>null,renderGraph,saveLineageLayout:async items=>{saves.push(items.map(({id,x,y})=>({id,x,y})));if(scenario==='save-failure')throw Error('Layout save unavailable')}});
  const service=async payload=>{calls.push(payload);const child={id:'resident_f696c238a185',kind:'agent',title:payload.title,_family_member:{role:'member',title:'Portal 开发'}};const [x,y]=scenario==='clear'?[140,50]:[components[0].x,components[0].y];Object.assign(child,{x,y});return {resident_id:child.id,lineage:{...state.lineage,nodes:[source,child]}}};
  const dialogs=create(PortalHatgResidentDialogs,{state,nodeById,familyForFacade:()=>null,familyForNewResident:()=>null,flushLineageLayoutSave:async()=>{},societyService:{branchNode:service},commitLineage:l=>{state.lineage=l;renderGraph()},renderGraph,selectNode:async id=>{graph.focusNode(id,{duration:0});},separateGraphObject:layout.separateGraphObject,setSyncStatus:()=>{},toast:(...v)=>notices.push(v),bindResidentStateCollection:()=>{}});
  renderGraph();graph.focusNode(source.id,{duration:0});
  const old=all().map(({id,x,y})=>({id,x,y}));await dialogs.createQuickFork(source,'full');
  const rects=[...document.querySelectorAll('.hatgNode')].map(n=>({id:n.dataset.nodeId,rect:n.getBoundingClientRect().toJSON(),height:n.offsetHeight})); const child=rects.find(n=>n.id==='resident_f696c238a185');
  const overlaps=rects.filter(n=>n!==child&&Math.min(n.rect.right,child.rect.right)>Math.max(n.rect.left,child.rect.left)+.1&&Math.min(n.rect.bottom,child.rect.bottom)>Math.max(n.rect.top,child.rect.top)+.1).map(n=>n.id);
  graph.destroy();return {overlaps,rects,placement:calls[0]?.placement,calls:calls.length,saves,old,existing:all().filter(n=>n.id!=='resident_f696c238a185').map(({id,x,y})=>({id,x,y})),notices};
 },{memberTag,scenario});
 assert.equal(result.calls,1,'Committed Fork is never replayed');
 assert.deepEqual(result.overlaps,[],'New card clears the rendered Effect');
 assert.deepEqual(result.existing,result.old,'Existing shared objects keep their positions');
 assert.equal(result.saves.length,scenario==='clear'?0:1,'Only a colliding card needs a layout save');
 if(result.saves.length)assert.deepEqual(result.saves[0].map(n=>n.id),['resident_f696c238a185']);
 assert.equal(result.notices.length,scenario==='save-failure'?1:0);
 if(scenario==='save-failure')assert(result.notices[0][0].includes('Layout save unavailable'));
 }
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',formal_acceptance:false,isolated_chromium:true,checks:['native renderer and quick Fork handler','overlapping committed coordinate fixture corrected','existing geometry untouched','clear placement no save','failed save warns without repeated creation']}));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
