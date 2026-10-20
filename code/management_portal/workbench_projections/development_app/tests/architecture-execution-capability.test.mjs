import test from 'node:test';
import assert from 'node:assert/strict';
import {executionCapability as capability, projectExecutionReading, readingConnections, hasExecutionCapability} from '../src/architecture/exploration-reading.js';
import {layoutExploration} from '../src/architecture/exploration-layout.js';
const obj=id=>({id,title:id,kind:'module',parent_id:null,child_ids:[],mechanism_ids:[],host_ids:[],evidence_ids:[]});
function sample(){
 const pairs=[['worker','execution','lease'],['execution','worker','session'],['worker','providers','native'],['providers','worker','native'],['providers','execution','model'],['lineage','execution','request'],['providers','postgres','database'],['worker','resources','resource'],['resources','execution',null],['execution','model-services','model'],['worker','model-services','model']];
 return {overview:true,root:null,children:['lineage','execution','worker','providers','resources','postgres','model-services'].map(obj),boundaryEndpoints:[],boundaryRelations:[],protocols:['lease','session','native','model','request','database','resource'].map(id=>({...obj(id),kind:'protocol'})),internalRelations:pairs.map(([from,to,protocol_id],i)=>({id:'r'+i,from,to,protocol_id,kind:'summary',summary:{relations:[{id:'raw'+i,from,to,protocol_id,kind:'calls',contract:{operation:'operation'+i},evidence_ids:['source'+i]}]}}))};
}
test('capability aggregation partitions all original directed contracts without mutating model identities',()=>{
 const p=sample(),before=structuredClone(p),overall=projectExecutionReading(p),inside=projectExecutionReading(p,capability.id);
 assert.deepEqual(p,before);assert.equal(overall.children.length,p.children.length-2);
 const group=overall.children.find(o=>o.viewId);assert.deepEqual(group.viewMembers,p.children.filter(o=>capability.memberIds.includes(o.id)));
 assert.equal(group.parent_id,undefined);assert.equal(group.identity,undefined);assert.equal(group.child_ids,undefined);
 assert.deepEqual([...overall.internalRelations,...group.containedRelations].map(r=>r.id).sort(),p.internalRelations.map(r=>r.id).sort());
 for(const r of overall.internalRelations)assert.deepEqual(r.summary,p.internalRelations.find(x=>x.id===r.id).summary);
 assert(!overall.internalRelations.some(r=>r.from===r.to));assert.equal(group.containedRelations.length,5);
 assert.deepEqual(inside.children,p.children);assert.deepEqual(inside.internalRelations,p.internalRelations);
 const connections=readingConnections(inside);assert(connections.some(r=>r.from==='worker'&&r.to==='providers'));assert(connections.some(r=>r.from==='providers'&&r.to==='worker'));
 for(const w of [380,765,1200]){const graph=layoutExploration(overall,{width:w});assert.equal(graph.nodes.length,overall.children.length);assert(graph.edges.some(e=>e.to==='postgres'));assert(graph.edges.some(e=>e.to==='resources'));}
});
test('missing member or invalid capability cannot manufacture a view; true compositions remain unchanged',()=>{
 const p=sample();p.children=p.children.filter(o=>o.id!=='worker');assert.equal(hasExecutionCapability(p.children),false);assert.equal(projectExecutionReading(p),p);assert.throws(()=>projectExecutionReading(p,capability.id));
 const full=sample();assert.throws(()=>projectExecutionReading(full,'unregistered'));
 const scoped={...full,overview:false,root:full.children[1]};assert.equal(projectExecutionReading(scoped),scoped);
 full.children[2].parent_id='execution';assert.equal(hasExecutionCapability(full.children),false);
});

test('execution corridors retain three directed lines with labels on the path, inside desktop and narrow bounds',()=>{
 const p=projectExecutionReading(sample(),capability.id);
 for(const width of [380,739,1200]) {
  const graph=layoutExploration(p,{width});
  for(const id of capability.memberIds) {const n=graph.nodes.find(o=>o.id===id);assert(n.x>=0&&n.x+n.width<=width);}
  for(const [from,to] of [['worker','execution'],['worker','providers'],['providers','worker']]) {
   const edge=graph.edges.find(e=>e.from===from&&e.to===to),label=edge.labelBox;
   assert(edge.paths.length>0,from+'>'+to);assert.equal(edge.presentation,'path');
   assert(label.x>=0&&label.x+label.width<=graph.width);
   assert(edge.rawPath.some((a,i)=>{const b=edge.rawPath[i+1];return b&&(a.x===b.x ? a.x>=label.x&&a.x<=label.x+label.width&&Math.min(a.y,b.y)<=label.y&&Math.max(a.y,b.y)>=label.y+label.height : a.y>=label.y&&a.y<=label.y+label.height&&Math.min(a.x,b.x)<=label.x&&Math.max(a.x,b.x)>=label.x+label.width)}),'caption is on its actual path');
  }
 }
});
