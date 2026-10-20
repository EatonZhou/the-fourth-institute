import test from 'node:test';
import assert from 'node:assert/strict';
import { createEvidenceLoader, sha256 } from '../src/architecture/data.js';
import { MAP_REF, EVOLUTION_REF, REPOSITORY, sourceAnchors, graphForView, impactOf, semanticObject, directionSelection, auditMatches, RevisionGuard } from '../src/architecture/model.js';

function map() {
  return { schema: 'tfi.portal-architecture.v1', modules: [
    { id:'root',title:'Root',responsibility:'Boundary',designs:[],code_paths:[],instruction_owners:[] },
    { id:'a',title:'A',parent:'root',designs:['decision_111111111111'],code_paths:['src'],instruction_owners:['entry'] },
    { id:'b',title:'B',parent:'root',designs:['decision_222222222222'],code_paths:['src/shared.js'],instruction_owners:[] },
  ],protocols:[{id:'shared',modules:['a','b'],designs:['decision_111111111111'],description:'participation',example:'conditional'}] };
}
async function fixture({ denied = [], race = false, evolution } = {}) {
  const m = map(), evo = {schema:'tfi.portal-evolution.v1', ...(evolution || {plans:[{id:'plan_111111111111',title:'Frozen title',modules:['b']}]})};
  const mapText = 'schema: tfi.portal-architecture.v1\nmodules:\n  - id: root\n  - id: a\n  - id: b\nprotocols:\n  - id: shared\n';
  const evoText = 'schema: ' + evo.schema + '\n' + ['directions', 'plans'].map(section => section + ':\n' + (evo[section] || []).map(item =>
    '  - id: ' + item.id + '\n' + Object.entries(item).filter(([k]) => k !== 'id').map(([k,v]) => '    ' + k + ': ' + JSON.stringify(v) + '\n').join('')).join('')).join('');
  const hash = await sha256(mapText);
  const reads = [], entries = {
    [MAP_REF]:{text:mapText,data:m},[EVOLUTION_REF]:{text:evoText,data:evo},
    frozen:{text:mapText,data:m},frozenEvolution:{text:evoText,data:evo},
  };
  const snapshot={commit:'a'.repeat(40),files:{'architecture/modules.yaml':{status:'available',address:'frozen',sha256:hash},'architecture/evolution.yaml':{status:'available',address:'frozenEvolution',sha256:await sha256(evoText)}}};
  const evidence={currentSource:snapshot,audits:[],scenario_catalog:[]};
  let count=0;
  const resources={
    resolve:async ref=>({readable:!denied.includes(ref),title:'CURRENT TITLE'}),
    read:async(ref,format)=>{
      reads.push({ref,format}); const item=entries[ref]; if(!item)throw Error('body not captured');
      if(denied.includes(ref))return{readable:false,data:null};
      const text=race&&ref===MAP_REF&&++count>2?mapText+'# changed':item.text;
      return {readable:true,data:format==='yaml'?structuredClone(item.data):text,metadata:{}};
    },
  };
  return { loader:createEvidenceLoader({resources,sdk:{hatg:async()=>({instructions:[]})}}),evidence,reads,entries,snapshot,hash };
}
test('unsupported evolution tags are rejected before unversioned Plan relations are consumed',async()=>{
  for (const schema of [undefined, 'archive.portal-evolution.v1', 'archive.portal-evolution.v2', 'tfi.portal-evolution.v3']) {
    const f=await fixture({evolution:{schema,plans:[{id:'plan_111111111111',modules:['b']}]}});
    await assert.rejects(f.loader.current(f.evidence),/演化格式不受支持/);
  }
});
test('denied knowledge is removed before graph nodes, protocol evidence and Plan counts',async()=>{
  const f=await fixture({denied:['decision_111111111111','plan_111111111111']});
  const d=await f.loader.current(f.evidence);
  assert.deepEqual(d.modules.find(m=>m.id==='a').designs,[]);
  assert.deepEqual(d.protocols[0].designs,[]);assert.equal(d.plans.length,0);
  const graph=graphForView(d,{branch:'a',level:1,files:[],showPlans:true});
  assert(!graph.nodes.some(n=>n.ref==='decision_111111111111'));
  assert(graph.edges.every(e=>graph.nodes.some(n=>n.id===e.source)&&graph.nodes.some(n=>n.id===e.target)));
});
test('only current source is readable; archived maps cannot be selected',async()=>{
  const f=await fixture(),d=await f.loader.current(f.evidence);
  assert.equal(f.loader.history,undefined);
  assert.equal(d.designMeta.decision_111111111111.title,'CURRENT TITLE');
  assert.equal(d.mode,'current');
  assert(f.reads.some(r=>r.ref===MAP_REF));
  assert(!f.reads.some(r=>r.ref==='frozen'||r.ref==='frozenEvolution'));
});
test('changing current YAML reads fail closed',async()=>{
  const changing=await fixture({race:true});
  await assert.rejects(changing.loader.current(changing.evidence),/变化/);
});
test('a multi-owner file retains both associations; unregistered file stays unknown',async()=>{
  const f=await fixture(),d=await f.loader.current(f.evidence);
  const file={type:'file',data:{module_matches:[{module_id:'a'},{module_id:'b'}]}};
  const direct=impactOf(d,file);
  assert.deepEqual(direct.modules,['a','b']);assert.deepEqual(direct.protocols,['shared']);assert.deepEqual(direct.plans,['plan_111111111111']);
  assert.equal(impactOf(d,{type:'file',data:{module_matches:[]}}).unknown,true);
  const graph=graphForView(d,{branch:'a',level:1});
  assert(graph.edges.filter(e=>e.type==='participation').every(e=>e.target==='protocol:shared'));
});
test('five field current semantic object preserves the actual map line, revision is channel-only',async()=>{
  const f=await fixture(),d=await f.loader.current(f.evidence);
  const n=graphForView(d,{branch:'a',level:1}).nodes.find(n=>n.id==='module:a');
  const object=semanticObject(d,n);
  assert.deepEqual(Object.keys(object).sort(),['kind','observed_at','ref','source','status']);
  assert.equal(object.kind,'module:a');assert.equal(object.ref,MAP_REF+'#L4');
  assert.equal(sourceAnchors(d.mapText).protocols.shared,7);
});
test('audit requires every declared input hash',()=>{
  const snapshot={commit:'sha',files:{map:{sha256:'map'},spec:{sha256:'spec'}}};
  const audit={commit:'sha',inputs:{files:{map:'map',spec:'wrong'}}};
  assert.equal(auditMatches(snapshot,audit),false);
  audit.inputs.files.spec='spec';assert.equal(auditMatches(snapshot,audit),true);
});
test('late and duplicate revisions cannot override a newer read generation',()=>{
  const guard=new RevisionGuard();assert(guard.receive(1));const a=guard.start();
  assert(guard.receive(2));const b=guard.start();
  assert(!guard.valid(a));assert(guard.valid(b));assert(!guard.receive(1));assert(!guard.receive(2));
});

const directionObject = {ref:EVOLUTION_REF+'#L33',kind:'direction:change',status:'proposed',source:EVOLUTION_REF};
const directionFacts = {
  directions:[{id:'change',title:'Change',description:'A cross-module proposal'}],
  plans:[
    {id:'plan_111111111111',directions:['change'],modules:['a','b']},
    {id:'plan_222222222222',directions:['change'],modules:['b','root']},
    {id:'plan_333333333333',directions:['change'],modules:['root'],module_mapping:'needs_review'},
  ],
};
test('direction keeps its identity and shows every verified readable Plan module, deduplicated',async()=>{
  const f=await fixture({evolution:directionFacts}),d=await f.loader.current(f.evidence);
  const selection=directionSelection(d,directionObject);
  assert.deepEqual(selection.data.modules,['a','b','root']);
  assert.equal(selection.data.pendingPlans.length,1);
  const semantic=semanticObject(d,selection);
  assert.equal(semantic.kind,'direction:change');assert.equal(semantic.status,'proposed');
  assert.equal(semantic.ref,EVOLUTION_REF+'#L3');assert.equal(semantic.source,EVOLUTION_REF);
  assert.deepEqual(Object.keys(semantic).sort(),['kind','observed_at','ref','source','status']);
  const graph=graphForView(d,{branch:null,level:0,scopeModules:selection.data.modules});
  assert(selection.data.modules.every(id=>graph.nodes.some(n=>n.id==='module:'+id)));
  assert.deepEqual(impactOf(d,selection).modules,['a','b','root']);
});
test('direction range excludes denied Plan relations and does not promote pending mappings',async()=>{
  const f=await fixture({evolution:directionFacts,denied:['plan_222222222222']}),d=await f.loader.current(f.evidence);
  const selection=directionSelection(d,directionObject);
  assert.deepEqual(selection.data.modules,['a','b']);
  assert.equal(selection.data.plans.length,2);
  const none=await fixture({evolution:directionFacts,denied:['plan_111111111111','plan_222222222222']});
  const unknown=directionSelection(await none.loader.current(none.evidence),directionObject);
  assert.deepEqual(unknown.data.modules,[]);assert.equal(impactOf(d,unknown).unknown,true);
});
test('direction must resolve against the current readable evolution source and anchored identity',async()=>{
  const f=await fixture({evolution:directionFacts}),d=await f.loader.current(f.evidence);
  for(const object of [{...directionObject,source:MAP_REF},{...directionObject,ref:MAP_REF+'#L33'},{...directionObject,kind:'direction:absent'}]) assert.equal(directionSelection(d,object),null);
  const denied=await fixture({evolution:directionFacts,denied:[EVOLUTION_REF]});
  assert.equal(directionSelection(await denied.loader.current(denied.evidence),directionObject),null);
  assert.deepEqual(sourceAnchors('directions:\n  - id: change\nplans:\n  - id: change\n').directions,{change:2});
});

test('discovery pins one immutable batch; only explicit refresh switches current scenes', async () => {
  const f = await fixture();
  const { MANIFEST_REF } = await import('../src/architecture/model.js');
  const make = async (revision, scenes) => {
    const text = JSON.stringify({schema:'tfi.portal-architecture-evidence.v1',...f.evidence,contract_revision:revision,scenario_catalog:scenes});
    const digest = await sha256(text), address = 'workspace_111111111111/file/objects/manifest-'+digest+'.json';
    f.entries[address]={text};return {address,sha256:digest,size:Buffer.byteLength(text)};
  };
  const scene={protocol_id:'shared',scenario_id:'one',status:'simulated',participants:[{id:'a'},{id:'b'}],steps:[{from:'a',to:'b',source_refs:[{}]}],branches:[],source_versions:[]};
  f.entries.scene={text:JSON.stringify(scene)};
  const old=await make(1,[]), latest=await make(3,[{protocol_id:'shared',scenario_id:'one',artifact:{address:'scene',sha256:await sha256(f.entries.scene.text)}}]);
  f.entries[MANIFEST_REF]={text:JSON.stringify({immutable_manifest:old})};
  const pinned=await f.loader.manifest();assert.deepEqual(pinned.pinned,old);
  f.entries[MANIFEST_REF]={text:JSON.stringify({immutable_manifest:latest})};
  const offset=f.reads.length, current=await f.loader.current(pinned);
  assert.deepEqual(await f.loader.scenarios(current,'shared'),[]);assert.equal(current.manifest.pinned.sha256,old.sha256);
  assert(!f.reads.slice(offset).some(r=>r.ref===MANIFEST_REF||r.ref===latest.address));
  const refreshed=await f.loader.manifest();assert.deepEqual(refreshed.pinned,latest);
  assert.equal((await f.loader.scenarios(await f.loader.current(refreshed),'shared'))[0].scenario_id,'one');
  assert.equal(current.manifest.pinned.sha256,old.sha256);
});

test('discovery and immutable failures never fall back to a remembered batch', async () => {
  const denied=[], f=await fixture({denied});const {MANIFEST_REF}=await import('../src/architecture/model.js');
  const text=JSON.stringify({schema:'tfi.portal-architecture-evidence.v1',...f.evidence});
  const sha=await sha256(text), address='workspace_111111111111/file/objects/manifest-'+sha+'.json';
  const pointer={address,sha256:sha,size:Buffer.byteLength(text)};
  f.entries[address]={text};f.entries[MANIFEST_REF]={text:JSON.stringify({immutable_manifest:pointer})};
  await f.loader.manifest();
  denied.push(MANIFEST_REF);await assert.rejects(f.loader.manifest(),/暂不可用/);denied.length=0;
  denied.push(address);await assert.rejects(f.loader.manifest(),/暂不可用/);denied.length=0;
  f.entries[address].text=JSON.stringify({schema:'unsupported'});
  await assert.rejects(f.loader.manifest(),/格式不受支持/);
});
