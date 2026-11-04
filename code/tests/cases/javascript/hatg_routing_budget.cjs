// Routing reads each card's bounds once per generation; labels retain exact
// collision semantics across sparse, crowded and negative-coordinate graphs.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../../../management_portal/static');
const geometry=require(path.join(root,'projection_libraries/portal-graph/1.0.0/geometry.js'));
function run(source, spacing=21){
 const ctx={window:{}};vm.runInNewContext(source,ctx);
 let reads=0;
 const nodes=Array.from({length:240},(_,i)=>({id:'n'+i,x:(i%20)*spacing-170,y:Math.floor(i/20)*(spacing+2)-70}));
 const edges=nodes.slice(1).map((n,i)=>({id:'e'+i,source:nodes[i].id,target:n.id,type:'route'}));
 const state={model:{nodes,edges},nodeMap:new Map(nodes.map(n=>[n.id,n])),edgeById:new Map(edges.map(e=>[e.id,e]))};
 const api=ctx.window.PortalHatgGraphEdgeRoutes.createEdgeRoutes({
  clamp:(v,a,b)=>Math.max(a,Math.min(b,v)),number:(v,f=0)=>Number.isFinite(Number(v))?Number(v):f,
  edgeLabelElement:()=>({offsetWidth:92,offsetHeight:24}),graphGeometry:geometry,
  nodeHalfExtents:()=>({x:7,y:5}),nodePosition:n=>{reads++;return {x:n.x,y:n.y};},
  portPosition:()=>null,portsForNode:()=>[],state,worldSize:{width:1280,height:900},
 });
 const capture=()=>JSON.parse(JSON.stringify(edges.map(e=>api.edgeGeometry(e))));
 api.refreshParallelEdgeLanes(edges);api.resolveEdgeLabelCollisions(edges);
 const initial=capture(),initialReads=reads;
 // Cached lookup and unrelated consumer reads cannot repeat routing.
 capture();assert.equal(reads,initialReads);
 // Move an obstacle and invalidate through the same boundary used by drag.
 nodes[1].x=nodes[0].x+10;nodes[1].y=nodes[0].y;
 api.forgetEdgeGeometry(edges[0].id);
 const moved=JSON.parse(JSON.stringify(api.edgeGeometry(edges[0])));
 api.invalidateEdgeGeometry();
 assert.deepEqual(JSON.parse(JSON.stringify(api.edgeGeometry(edges[0]))),moved,'drag invalidation sees new obstacle bounds');
 api.resolveEdgeLabelCollisions(edges);
 const refreshed=capture();
 return {initial,refreshed,initialReads};
}
const source=fs.readFileSync(path.join(root,'lineage_graph_edge_routes.js'),'utf8');
const result=run(source);
const crowded=run(source,8);
assert.ok(result.initialReads<240*20,'geometry generation must not read all card bounds per edge: '+result.initialReads);
// An optional historical source provides a precise differential oracle for
// development, without embedding a second routing implementation in production.
if(process.env.HATG_ROUTING_REFERENCE){
 const old=run(fs.readFileSync(process.env.HATG_ROUTING_REFERENCE,'utf8'));
 assert.deepEqual(result.initial,old.initial);
 assert.deepEqual(result.refreshed,old.refreshed);
 const oldCrowded=run(fs.readFileSync(process.env.HATG_ROUTING_REFERENCE,'utf8'),8);
 assert.deepEqual(crowded.initial,oldCrowded.initial);
 assert.deepEqual(crowded.refreshed,oldCrowded.refreshed);
 console.log(JSON.stringify({beforeReads:old.initialReads,afterReads:result.initialReads,geometryEquivalent:true}));
}else console.log(JSON.stringify({nodeReads:result.initialReads,budgetPassed:true}));
