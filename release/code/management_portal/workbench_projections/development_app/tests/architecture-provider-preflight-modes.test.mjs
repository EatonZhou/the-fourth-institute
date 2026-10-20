import test from 'node:test';
import assert from 'node:assert/strict';
import {readProviderFeedbackFixture} from './architecture-provider-feedback.fixture.mjs';
import {readPreflightModesFixture} from './architecture-provider-preflight.fixture.mjs';
import {getProviderPreflightModes,getProviderPreflightEntry,PREFLIGHT_ID} from '../src/architecture/provider-preflight.model.js';
const old=readProviderFeedbackFixture(),current=readPreflightModesFixture();
const added=['provider.runtime-catalog','provider.settings-tuple'];
test('preflight mechanism and every referenced source stay equal across the two frozen batches',()=>{
  const a=old.content(PREFLIGHT_ID,'provider-capabilities'),b=current.content('provider-capabilities');
  assert.deepEqual(a.mechanism,b.mechanism);
  for(const e of a.evidence)assert.deepEqual(b.evidence.find(x=>x.id===e.id),e,e.id);
});
test('new same-object members delegate only with exact identity, adapter and complete supplied evidence',()=>{
  for(const id of ['provider-capabilities','execution.providers.catalog','execution.providers']){
    const c=current.content(id),entry=getProviderPreflightEntry(id);
    for(const mid of added){
      const mode=getProviderPreflightModes(c,id).find(m=>m.mechanismId===mid);assert.ok(mode,mid);
      assert.equal(mode.adapter,mid+'.v1');assert.equal(mode.factory,undefined);
      assert.deepEqual(mode.route,{kind:entry.kind==='protocol'?'protocol':'mechanism',objectId:id,mechanismId:mid});
      const mismatch=structuredClone(c);mismatch.mechanisms.find(m=>m.id===mid).adapter+='-wrong';
      assert.ok(!getProviderPreflightModes(mismatch,id).some(m=>m.mechanismId===mid));
      const missing=structuredClone(c),mechanism=missing.mechanisms.find(m=>m.id===mid);
      missing.evidence=missing.evidence.filter(e=>e.id!==mechanism.evidence_ids[0]);
      assert.ok(!getProviderPreflightModes(missing,id).some(m=>m.mechanismId===mid));
      const undeclared=structuredClone(c);undeclared.objects.find(o=>o.id===id).mechanism_ids=undeclared.objects.find(o=>o.id===id).mechanism_ids.filter(m=>m!==mid);
      assert.ok(!getProviderPreflightModes(undeclared,id).some(m=>m.mechanismId===mid));
    }
    assert.ok(!getProviderPreflightModes(old.content(PREFLIGHT_ID,id,true),id).some(m=>added.includes(m.mechanismId)));
    if(id==='execution.providers'){
      assert.equal(entry.kind,'composition');assert.equal(entry.defaultMechanismId,null);
      for(const mode of getProviderPreflightModes(c,id))assert.equal(mode.route.kind,'mechanism');
    }
  }
});
