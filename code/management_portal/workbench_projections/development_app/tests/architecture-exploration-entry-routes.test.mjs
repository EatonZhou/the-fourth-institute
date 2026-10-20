import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveDomainEntry,resolveDomainModes} from '../src/architecture/exploration-entry-routes.js';

const model={objects:[
  {id:'leaf',child_ids:[],mechanism_ids:['other','main']},
  {id:'protocol',child_ids:[],mechanism_ids:['other','main']},
  {id:'parent',child_ids:['leaf'],mechanism_ids:['main']},
],mechanisms:[{id:'main'},{id:'other'}]};
test('explicit authored entry enters a multi-mechanism leaf without using first array entry',()=>{
 assert.deepEqual(resolveDomainEntry(model,{kind:'composition',objectId:'leaf'},{leaf:'main'}),{kind:'mechanism',objectId:'leaf',mechanismId:'main'});
 const unknown={kind:'composition',objectId:'leaf'};
 assert.equal(resolveDomainEntry(model,unknown),unknown);
});
test('protocol keeps one protocol route with its exact authored mechanism; nonleaf stays composition',()=>{
 assert.deepEqual(resolveDomainEntry(model,{kind:'protocol',objectId:'protocol'},{protocol:'main'}),{kind:'protocol',objectId:'protocol',mechanismId:'main'});
 const parent={kind:'composition',objectId:'parent'};
 assert.equal(resolveDomainEntry(model,parent,{parent:'main'}),parent);
 const chosen={kind:'mechanism',objectId:'leaf',mechanismId:'other'};
 assert.equal(resolveDomainEntry(model,chosen,{leaf:'main'}),chosen);
});
test('an entry cannot borrow a mechanism from outside the authorized object or batch',()=>{
 assert.throws(()=>resolveDomainEntry(model,{kind:'composition',objectId:'leaf'},{leaf:'unrelated'}),{code:'invalid_domain_entry'});
 assert.throws(()=>resolveDomainEntry({...model,mechanisms:[{id:'other'}]},{kind:'composition',objectId:'leaf'},{leaf:'main'}),{code:'invalid_domain_entry'});
});

test('an authored newer default preserves the explicit older-batch entry without borrowing missing content',()=>{
 const route={kind:'composition',objectId:'leaf'},entries={leaf:['new-catalog','main']};
 assert.equal(resolveDomainEntry(model,route,entries).mechanismId,'main');
 const newer={...model,objects:model.objects.map(o=>o.id==='leaf'?{...o,mechanism_ids:['other','main','new-catalog']}:o),mechanisms:[...model.mechanisms,{id:'new-catalog'}]};
 assert.equal(resolveDomainEntry(newer,route,entries).mechanismId,'new-catalog');
 assert.throws(()=>resolveDomainEntry({...newer,mechanisms:model.mechanisms},route,entries),{code:'invalid_domain_entry'});
 assert.throws(()=>resolveDomainEntry(model,route,{leaf:['new-catalog']}),{code:'invalid_domain_entry'});
 const restored={kind:'mechanism',objectId:'leaf',mechanismId:'main'};
 assert.equal(resolveDomainEntry(newer,restored,entries),restored);
});

test('authored modes use explicit object references and cannot borrow a later batch',()=>{
 const object={id:'projection-family',mechanism_ids:['focus','feedback']};
 const modes={...model,mechanisms:[{id:'focus',owner_id:object.id},{id:'feedback',owner_id:object.id},{id:'foreign',owner_id:'other'}]};
 const choices={'projection-family':['focus','feedback']};
 assert.deepEqual(resolveDomainModes(modes,object,choices),modes.mechanisms.slice(0,2));
 assert.equal(resolveDomainModes(modes,{...object,mechanism_ids:['focus']},choices),undefined);
 assert.equal(resolveDomainModes(modes,{id:'other',mechanism_ids:['foreign']},choices),undefined);
 assert.throws(()=>resolveDomainModes({...modes,mechanisms:[modes.mechanisms[0]]},object,choices),{code:'invalid_domain_mode'});
 const shared={id:'feedback',owner_id:'web.projections'};
 const authorized={...modes,mechanisms:[modes.mechanisms[0],shared]};
 const result=resolveDomainModes(authorized,object,choices);
 assert.equal(result[1],shared);
 assert.equal(result[1].owner_id,'web.projections');
 assert.equal(object.id,'projection-family');
 assert.equal(resolveDomainModes(authorized,{...object,mechanism_ids:['focus']},choices),undefined);
});
