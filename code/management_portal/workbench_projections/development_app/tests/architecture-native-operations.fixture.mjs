import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export const fixtureSha = '1bb21129ee61d00c4f48b80946086980fa70ff8f2796820160c1ef5d422db3bc';
export const batchKey = '0c67fa9f8d391ce1b2bf5a42f21fa5b4e000d8a3bcddcea9d13250cc3cd5f2fd';
export const modelSha = 'daf11b2d9e220ca7ef37fc058e798931c6d610b8c98dd65ca326097ef05cecd7';
export function readNativeOperationsFixture(path = process.env.NATIVE_OPERATIONS_FACTS) {
  const raw = readFileSync(path); assert.equal(sha(raw), fixtureSha);
  const capture = JSON.parse(raw), facts = capture.facts;
  const read = (prefix, hash, textOnly = false) => {
    const address = Object.keys(facts).find(a => a.endsWith(`/${prefix}-${hash}.json`)); assert.ok(address);
    const text = facts[address].text; assert.equal(sha(text), hash);
    const declared = capture.sources.find(source => source.address === address); assert.equal(Buffer.byteLength(text), declared.size);
    return textOnly ? text : JSON.parse(text);
  };
  const root = read('discovery-candidate', '1c0bbf911b3a0559f78beae22d01f643c2d117fd0667befda2dbabcf294aad76');
  const pair = read('discovery-pair', 'ba11d85123441bc9411207bac97fec92a8f0f3ac2429d896347cbcc90b062810');
  const manifest = read('manifest', batchKey), model = read('model', modelSha);
  const contract = read('contract', '4abe99a3c5139cce0816b527baefddaaeee3232efd7d50f9c62457383473d1ac', true);
  assert.equal(capture.commit, 'a0dac83394748582158ee9c63a8b23c715872a1e');
  // Verify the selected discovery chain and this domain's dependencies, not unrelated historical snapshots.
  const seen = new Set();
  function verify(node) {
    if (!node || typeof node !== 'object') return;
    if (typeof node.address === 'string' && /^[a-f0-9]{64}$/.test(node.sha256 || '') && Number.isInteger(node.size)) {
      if (!seen.has(node.address)) {
        seen.add(node.address); const text = facts[node.address]?.text; assert.equal(typeof text, 'string', node.address);
        assert.equal(sha(text), node.sha256, node.address); assert.equal(Buffer.byteLength(text), node.size, node.address);
      }
    }
    Object.values(node).forEach(verify);
  }
  assert.equal(root.exploration.manifest.sha256, batchKey); assert.equal(pair.exploration.manifest.sha256, batchKey);
  assert.equal(manifest.model.sha256, modelSha); assert.deepEqual(manifest.binding, model.binding);
  [root.immutable_discovery, root.exploration, pair.base_manifest, pair.exploration, manifest.binding, manifest.model, manifest.contract].forEach(verify);
  function content(id, objectId = 'provider-native', includeModes = true) {
    const mechanism = model.mechanisms.find(m => m.id === id); assert.ok(mechanism);
    const subject = model.objects.find(o => o.id === objectId); assert.ok(subject?.mechanism_ids.includes(id));
    const mechanisms = includeModes ? model.mechanisms.filter(m => subject.mechanism_ids.includes(m.id)) : [mechanism];
    const eids = new Set(mechanisms.flatMap(m => [...m.evidence_ids, ...m.steps.flatMap(s => s.evidence_ids), ...m.cases.flatMap(c => c.evidence_ids)]));
    const evidence = model.evidence.filter(e => eids.has(e.id)); assert.equal(evidence.length, eids.size);
    for (const e of evidence) verify(e.frozen);
    const oids = new Set([objectId, 'worker.runtime', ...mechanisms.flatMap(m => [m.owner_id, ...m.participants.map(p => p.object_id), ...m.state_fields.map(f => f.authority_id), ...m.steps.flatMap(s => [s.from, s.to])])]);
    return {mechanism, mechanisms: includeModes ? mechanisms : undefined, objects: model.objects.filter(o => oids.has(o.id)), evidence, batchKey};
  }
  return {content, model, provenance: {fixturePath: path, fixtureSha, batchKey, modelSha, commit: capture.commit, root: capture.entry, pair: capture.pair, binding: model.binding}, contract};
}
