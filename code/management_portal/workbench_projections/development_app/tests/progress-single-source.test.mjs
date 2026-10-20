import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadProgress, REFERENCES} from '../src/progress/data.js';
import {fixture,stage,report,sha} from './progress-fixtures.mjs';

test('the reader uses the owner index and original report identity directly', async()=>{
  const f=await fixture(), result=await loadProgress(f.resources);
  const row=result.items.find(x=>x.id==='bubble');
  assert.deepEqual(result.failures,[]);
  assert.equal(REFERENCES.index,stage);
  assert.equal(row.report_ref,report);
  assert.equal(row.ref,f.target.ref);
  assert.equal(row.key,JSON.stringify([report,'conversation','bubble']));
  assert.equal(row.hash,await sha(f.db[report]));
  assert.ok(result.watched.includes(stage));
  assert.equal(row.source_report_ref,undefined);
  assert.ok(f.calls.every(x=>!(/canonical|publication|origins|release-runtime/.test(x.ref))));
});

test('a retired publication wrapper is not followed as another data source', async()=>{
  const f=await fixture();
  f.db[stage]=JSON.stringify({schema:'tfi.portal-progress-index.v1',stage_index_ref:stage+'.copy'});
  const result=await loadProgress(f.resources);
  assert.equal(result.items.length,0);
  assert.ok(result.failures.some(x=>x.kind==='format'));
  assert.ok(!f.calls.some(x=>x.ref===stage+'.copy'));
});
