import test from 'node:test';
import assert from 'node:assert/strict';
import { loadDiscoveredBatch, batchIdentity } from '../src/architecture/discovery.js';
test('direct explanation never requires historical mirror or verification', async () => {
  const exploration = { status: 'available', manifest: { address: 'entry' } };
  const value = await loadDiscoveredBatch({ exploration, immutable_discovery: { address: 'gone' },
    immutable_manifest: { address: 'old-commit' } }, { readJSON: () => { throw new Error('unexpected read'); } });
  assert.deepEqual(value.discoveryPin.exploration, exploration); assert.equal(value.snapshots, undefined); assert.equal(value.discoveryPin.snapshots, undefined);
});
test('older index locates its explanation without comparing commit, hash or binding', async () => {
  const reads = [], exploration = { status: 'available', manifest: { address: 'entry' } };
  const value = await loadDiscoveredBatch({ immutable_discovery: { address: 'pair', sha256: 'stale', size: 0 } },
    { readJSON: async ref => { reads.push(ref.address); return { schema:'tfi.portal-architecture-discovery.v1', exploration }; } });
  assert.deepEqual(reads, ['pair']); assert.deepEqual(value.discoveryPin.exploration, exploration);
});
test('older indexes supply current provenance without retaining archived entries', async () => {
  const value = await loadDiscoveredBatch({ immutable_manifest: { address: 'base' } },
    { readJSON: async () => ({ schema:'tfi.portal-architecture-evidence.v1', snapshots: [{ commit: 'old' }], current_commit: 'old' }) });
  assert.equal(value.snapshots, undefined); assert.deepEqual(value.currentSource, { commit: 'old' });
});
test('declaration changes remain visible to refresh identity', async () => {
  const first = await loadDiscoveredBatch({ exploration: { status: 'available', manifest: { address: 'one' } } }, {});
  const next = await loadDiscoveredBatch({ exploration: { status: 'available', manifest: { address: 'two' } } }, {});
  assert.notEqual(batchIdentity(first), batchIdentity(next));
});
test('source failures retain their actual cause', async () => {
  await assert.rejects(loadDiscoveredBatch({ immutable_discovery: { address: 'missing' } },
    { readJSON: async () => { throw Object.assign(new Error('missing'), { code: 'file_not_found' }); } }),
  { code: 'file_not_found' });
});

test('discovery drops archived architecture entries and retains only current provenance',async()=>{
 const reads=[];
 const value=await loadDiscoveredBatch({schema:'tfi.portal-architecture-evidence.v1',current_commit:'now',
   snapshots:[{commit:'old',files:{map:{address:'never-read'}}},{commit:'now',files:{map:{address:'current'}}}]},
   {readJSON:async ref=>{reads.push(ref);throw Error('unexpected archive read');}});
 assert.equal(value.snapshots,undefined);assert.equal(value.discoveryPin.snapshots,undefined);
 assert.equal(value.currentSource.commit,'now');assert(!JSON.stringify(value).includes('never-read'));assert.deepEqual(reads,[]);
});
