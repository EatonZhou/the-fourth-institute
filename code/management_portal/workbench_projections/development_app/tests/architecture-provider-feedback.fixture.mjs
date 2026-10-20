import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
export const sha = value => createHash('sha256').update(value).digest('hex');
export const fixtureSha = '6fd44e5d04c3e202c66852a0cfed0badc90bff83fd58c57244885036e3b8832a';
export const batchKey = '53c38944c97f1c4fcf5bb44481b791fe2d6f73867d9930ceaeea2c83c7378f10';
export const modelSha = '5f6e45a8edc412bce9ed2588bfc4a0f75c9307eafb51f067304f89bf4ca0066d';
export function readProviderFeedbackFixture(path = process.env.PROVIDER_FEEDBACK_FACTS) {
  const raw = readFileSync(path); assert.equal(sha(raw), fixtureSha);
  const capture = JSON.parse(raw), checked = new Set();
  function text(descriptor) {
    assert.ok(descriptor?.address); const body = capture.facts[descriptor.address]?.text;
    assert.equal(typeof body, 'string', descriptor.address);
    assert.equal(sha(body), descriptor.sha256, descriptor.address);
    assert.equal(Buffer.byteLength(body), descriptor.size, descriptor.address);
    checked.add(descriptor.address); return body;
  }
  const read = descriptor => JSON.parse(text(descriptor));
  assert.equal(capture.entry.sha256, 'b9f25c616f567d13f88852a4f6954982cb7530a83b37afd3a8e891d3c0f35143');
  assert.equal(capture.pair.sha256, '8844d53c9c84b610a693e1558c49e42de3abd3baeb2a1da5d69f7a2d308c1794');
  const root = read(capture.entry), pair = read(capture.pair);
  assert.deepEqual(root.exploration, pair.exploration);
  assert.equal(root.exploration.manifest.sha256, batchKey);
  const manifest = read(root.exploration.manifest); assert.equal(manifest.model.sha256, modelSha);
  const model = read(manifest.model), contract = text(manifest.contract);
  assert.equal(capture.commit, 'a0dac83394748582158ee9c63a8b23c715872a1e');
  assert.deepEqual(manifest.binding, model.binding);
  for (const descriptor of Object.values(manifest.binding)) if (descriptor?.address) text(descriptor);
  const declared = new Map(manifest.sources.map(source => [source.address, source]));
  for (const source of manifest.sources) text(source);
  for (const evidence of model.evidence) if (evidence.frozen) assert.deepEqual(declared.get(evidence.frozen.address), evidence.frozen, evidence.id);
  function content(id, objectId, includeModes = false) {
    const mechanism = model.mechanisms.find(m => m.id === id); assert.ok(mechanism);
    const subject = model.objects.find(o => o.id === objectId); assert.ok(subject?.mechanism_ids.includes(id));
    const mechanisms = includeModes ? model.mechanisms.filter(m => subject.mechanism_ids.includes(m.id)) : [mechanism];
    const eids = new Set(mechanisms.flatMap(m => [...m.evidence_ids, ...m.steps.flatMap(s => s.evidence_ids), ...m.cases.flatMap(c => c.evidence_ids)]));
    const evidence = model.evidence.filter(e => eids.has(e.id)); assert.equal(evidence.length, eids.size);
    for (const e of evidence) text(e.frozen);
    const oids = new Set([objectId, 'worker.runtime', ...mechanisms.flatMap(m => [m.owner_id, ...m.participants.map(p => p.object_id), ...m.state_fields.map(f => f.authority_id), ...m.steps.flatMap(s => [s.from, s.to])])]);
    return {mechanism, mechanisms: includeModes ? mechanisms : undefined, objects: model.objects.filter(o => oids.has(o.id)), evidence, batchKey};
  }
  return {content, model, manifest, contract, provenance: {fixturePath: path, fixtureSha, batchKey, modelSha, entry: capture.entry, pair: capture.pair, contract: manifest.contract, commit: capture.commit, verified_source_descriptors: declared.size, verified_descriptors: checked.size}};
}
