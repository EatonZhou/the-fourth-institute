import test from 'node:test';
import assert from 'node:assert/strict';
import { REFS, loadFacts, semanticObject, linkedDirections, feedbackPayload, lineAnchor, planSections } from '../src/evolution/data.js';
import { EvolutionSession } from '../src/evolution/session.js';
import { initialScene } from '../src/evolution/model.js';
function fixture(){
 const frames=[0,1,2,3].map(i=>({id:`scene-${i}`,status:'simulated',duration_ms:1000}));
 const direction={id:'agent-society-city',title:'City',status:'proposed',modules:['direct'],plans:[],visual:{storyboard:frames}};
 const attached={id:'goal-grown-studio',title:'Studio',status:'proposed',modules:['direct'],plans:['plan_x'],visual:{storyboard:frames}};
 const e={schema:'tfi.portal-evolution.v2',directions:[direction,attached],plans:[{id:'plan_x',title:'Partial Plan',modules:['plan-only']},{id:'unrelated',title:'Hidden'}],retired_directions:[{id:'retired',retired_reason:'No successor'}],module_map:{ref:REFS.modules,schema:'tfi.portal-architecture.v1',revision:'map-hash',snapshot_ref:'snapshot'}};
 const map={schema:'tfi.portal-architecture.v1',modules:[{id:'direct',title:'Direct module'},{id:'plan-only',title:'Plan module'}]};
 const sources={ [REFS.evolution]:{data:e,text:'directions:\n  - id: agent-society-city\n  - id: goal-grown-studio\n',version:'evo-hash'},[REFS.modules]:{data:map,text:'modules:\n  - id: direct\n  - id: plan-only\n',version:'map-hash'},snapshot:{data:map,text:'bound',version:'map-hash'},plan_x:{text:'## 问题\nProblem\n\n## 预期体验\nExperience\n\n## 成功检验\nCheck',version:'body-hash',recordVersion:'2'}};
 const calls=[];
 const resources={async read(ref,format){calls.push([ref,format]);const s=sources[ref];if(s?.error)throw Error('offline');return {readable:!!s&&!s.denied,data:s&&!s.denied?(format==='yaml'?structuredClone(s.data):s.text):null,metadata:{content_sha256:s?.version,record:{version:{number:s?.recordVersion}}}};}};
 return {sources,resources,calls};
}
test('no-Plan directions are legal; direct modules never union Plan modules; only attached Plans read',async()=>{
 const f=fixture(),facts=await loadFacts(f.resources);assert.equal(facts.mapMatches,true);assert.equal(facts.directions.length,2);assert.equal(f.calls.some(([id])=>id==='unrelated'),false);
 assert.deepEqual(linkedDirections({kind:'module:plan-only',source:REFS.modules},facts),[]);
 assert.deepEqual(linkedDirections({kind:'module:direct',source:REFS.modules},facts),['agent-society-city','goal-grown-studio']);
 const object=semanticObject({kind:'direction',id:'agent-society-city'},facts);
 assert.deepEqual(Object.keys(object).sort(),['kind','observed_at','ref','source','status']);assert.equal(object.status,'proposed');assert.equal(semanticObject({kind:'direction',id:'retired'},facts),null);
 const payload=feedbackPayload(facts,facts.directions[0],initialScene(facts.directions[0]),'idea'),scene=JSON.parse(payload.value.scene);
 assert.equal(scene.status,'simulated');assert.equal(scene.sources.modules.version,'map-hash');assert.equal(scene.storyboard.id,'scene-0');assert.equal(scene.local.city.actor,'research');
});
test('description changes retain current identity and never read historical fallback',async()=>{
 const f=fixture();f.sources[REFS.modules].version='new-map';const facts=await loadFacts(f.resources);assert.equal(facts.mapMatches,true);assert.equal(facts.modules[0].title,'Direct module');
 assert.ok(semanticObject({kind:'module',id:'direct'},facts));assert.deepEqual(linkedDirections({kind:'module:direct',source:REFS.modules},facts),['agent-society-city','goal-grown-studio']);
 assert.equal(f.calls.some(([ref])=>ref==='snapshot'),false);assert.equal('boundModules' in facts,false);
});
test('torn YAML/text pair rereads both; repeated torn pairs fail',async()=>{
 const f=fixture();let torn=0;const normal=f.resources.read;f.resources.read=async(ref,format)=>{const r=await normal(ref,format);if(ref===REFS.evolution&&format==='text'&&torn++===0)r.metadata.content_sha256='torn';return r;};
 assert.equal((await loadFacts(f.resources)).validity,'ready');assert.equal(f.calls.filter(([r])=>r===REFS.evolution).length,4);
 f.resources.read=async(ref,format)=>{const r=await normal(ref,format);if(ref===REFS.evolution&&format==='text')r.metadata.content_sha256='torn';return r;};await assert.rejects(loadFacts(f.resources),/不同/);
});
test('model mismatch preserves narrative and never attempts even available historical map',async()=>{
 const f=fixture();f.sources[REFS.modules].data.model_version=2;
 const facts=await loadFacts(f.resources);assert.equal(facts.validity,'ready');assert.equal(facts.directions.length,2);assert.equal(facts.mapMatches,false);
 assert.equal(f.calls.some(([ref])=>ref==='snapshot'),false);assert.equal(semanticObject({kind:'module',id:'direct'},facts),null);
});
test('current-only binding works without historical fields; deleted modules and pending directions never link',async()=>{
 const f=fixture();delete f.sources[REFS.evolution].data.module_map.revision;delete f.sources[REFS.evolution].data.module_map.snapshot_ref;
 f.sources[REFS.modules].data.modules=f.sources[REFS.modules].data.modules.filter(m=>m.id!=='direct');
 const facts=await loadFacts(f.resources);assert.equal(facts.mapMatches,true);assert.equal(semanticObject({kind:'module',id:'direct'},facts),null);
 assert.deepEqual(linkedDirections({kind:'module:direct',source:REFS.modules},facts),[]);
 const g=fixture();g.sources[REFS.evolution].data.directions.forEach(d=>d.module_mapping='needs_review');
 assert.deepEqual(linkedDirections({kind:'module:direct',source:REFS.modules},await loadFacts(g.resources)),[]);
});
test('source changes freeze old scene; explicit reload resets; denial purges titles scenes drafts',async()=>{
 const f=fixture(),s=new EvolutionSession(f.resources);await s.refresh(true);s.change('agent-society-city',{type:'step',value:2});s.drafts['agent-society-city']='private draft';
 f.sources[REFS.evolution].version='new';await s.refresh();assert.equal(s.phase,'frozen');assert.equal(s.scenes['agent-society-city'].step,2);assert.equal(s.change('agent-society-city',{type:'step',value:3}),false);
 await s.refresh(true);assert.equal(s.phase,'ready');assert.equal(s.scenes['agent-society-city'].step,0);
 f.sources.plan_x.denied=true;await s.refresh();assert.equal(s.phase,'revoked');assert.equal(s.facts,null);assert.deepEqual(s.drafts,{});assert.deepEqual(s.scenes,{});
 await s.refresh(true);assert.equal(s.phase,'ready');assert.equal(s.facts.plans.length,0);
});
test('hidden response cannot revive private scene',async()=>{
 const f=fixture(),s=new EvolutionSession(f.resources);await s.refresh(true);const normal=f.resources.read;let release;const barrier=new Promise(r=>release=r);f.resources.read=async(...args)=>{await barrier;return normal(...args);};
 const pending=s.refresh();s.hide();release();await pending;assert.equal(s.phase,'hidden');assert.equal(s.writable,false);
});
test('sections use actual headings and lines; ambiguous sections and IDs stay unknown',()=>{
 const sections=planSections('## 问题\nOne\n\n## 预期体验\nTwo\n\n## 成功检验\nThree');assert.equal(sections[1].heading,'预期体验');assert.equal(sections[1].line,4);
 assert.equal(planSections('## 问题\nOne\n\n## 问题\nTwo')[0].available,false);assert.equal(lineAnchor('ref','id','- id: id\n- id: id'),null);
});
