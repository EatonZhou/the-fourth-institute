import test from 'node:test';
import assert from 'node:assert/strict';
import review from '../src/architecture/exploration-source-review.js';
import { reviewedSources, currentFeedbackEligible, evidenceStatus } from '../src/architecture/exploration-current-source.js';
import { readExplorationSemanticObject } from '../src/architecture/exploration-focus.js';

const objects = Object.entries(review.objects).map(([id, item]) => ({id,
  kind:item.kind?.split(':')[0] || 'concept', identity:{resource_ref:item.address,
    focus_kind:item.kind,focus_owner_id:item.owner,local_only:item.status==='local_only'}}));
const bundle={mode:'current',sourceReview:review,model:{objects}};
const canonical=objects.find(x=>x.id==='worker');

test('only reviewed current objects enter feedback; history has no inferred migration',()=>{
  for(const object of objects) assert.equal(currentFeedbackEligible(bundle,object),review.objects[object.id].status==='current',object.id);
  assert.equal(currentFeedbackEligible({...bundle,sourceReview:null},canonical),false);
  assert.equal(currentFeedbackEligible({...bundle,mode:'history'},canonical),false);
  assert.equal(currentFeedbackEligible(bundle,{...canonical,identity:{...canonical.identity,local_only:true}}),false);
  assert.equal(review.objects['lineage-service'].status,'current_map_anchor_absent');
  assert.equal(review.objects['lineage-service'].kind,'protocol:lineage-service');
  assert.equal(review.objects['society-service'],undefined);
});

test('current evidence requires the reviewed address; probe addresses never become historical links',()=>{
  for(const [id,item] of Object.entries(review.evidence)) {
    assert.equal(evidenceStatus(bundle,{id,address:item.address}),item.status,id);
    assert.equal(evidenceStatus(bundle,{id,address:'different-source'}),'unconfirmed',id);
  }
  assert.equal(evidenceStatus(bundle,{id:'unreviewed',address:review.map}),'unconfirmed');
  assert.equal(reviewedSources('{}'),null);
});

test('an authenticated visitor read is renewed; missing source and permission errors stay distinct',async()=>{
  const calls=[];let code=null;
  const resources={read:async (ref,format)=>{calls.push({ref,format});return code
    ? {readable:false,error:{code}} : {readable:true,data:'modules:\n  - id: worker\nprotocols:\n'};}};
  assert(await readExplorationSemanticObject(bundle,canonical,resources));
  for(const failure of ['permission_denied','file_not_found']) {
    code=failure;await assert.rejects(readExplorationSemanticObject(bundle,canonical,resources),{code:failure});
  }
  assert.equal(calls.length,3);assert(calls.every(x=>x.ref===review.map&&x.format==='text'));
  code=null;
  assert.equal(await readExplorationSemanticObject(bundle,canonical,resources,()=>false),null);
});
