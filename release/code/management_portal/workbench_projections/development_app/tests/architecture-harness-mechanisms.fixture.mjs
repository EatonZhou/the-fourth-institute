import fs from 'node:fs';
import {createHash} from 'node:crypto';
const fixturePath = (process.env.HARNESS_DISCOVERY_FIXTURE || (() => { throw new Error("Set HARNESS_DISCOVERY_FIXTURE to a local fixture"); })());
export const fixtureSha256 = '1bb21129ee61d00c4f48b80946086980fa70ff8f2796820160c1ef5d422db3bc';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = fs.readFileSync(fixturePath);
if (hash(bytes) !== fixtureSha256) throw Error('Harness fixture batch mismatch');
const fixture = JSON.parse(bytes);
function body(descriptor, json = true) {
  const text = fixture.facts[descriptor.address]?.text;
  if (!text || hash(text) !== descriptor.sha256) throw Error(`Frozen source hash mismatch: ${descriptor.address}`);
  return json ? JSON.parse(text) : text;
}
const manifest = body(fixture.manifest);
export const model = body(manifest.model);
const contract = body(manifest.contract, false);
body(fixture.entry); body(fixture.pair); body(manifest.binding.base_manifest);
if (!manifest.model.sha256.startsWith('daf1') || !manifest.contract.sha256.startsWith('4abe')
  || manifest.binding.commit !== fixture.commit || !fixture.commit.startsWith('a0dac')) throw Error('Wrong frozen pair');
const harness = model.mechanisms.filter(m => m.id.startsWith('harness.'));
const evidenceIds = new Set(harness.flatMap(m => [...m.evidence_ids, ...m.steps.flatMap(s => s.evidence_ids), ...m.cases.flatMap(c => c.evidence_ids)]));
const evidence = model.evidence.filter(e => evidenceIds.has(e.id));
for (const item of evidence) body(item.frozen, false);
export const sourceHashes = {fixturePath, fixtureSha256, commit: fixture.commit, entry: fixture.entry, pair: fixture.pair,
  manifest: fixture.manifest, model: manifest.model, contract: manifest.contract, baseManifest: manifest.binding.base_manifest,
  evidence: evidence.map(e => ({id:e.id, address:e.address, startLine:e.start_line, endLine:e.end_line, frozen:e.frozen})),
  contractTitle: contract.split('\n')[0]};
export function contentFor(id) {
  const mechanism = harness.find(m => m.id === id);
  if (!mechanism) throw Error('Unknown harness mechanism');
  return structuredClone({mechanism, objects:model.objects, relations:model.relations, evidence,
    mechanisms:harness, batchKey:fixtureSha256, modelDescriptor:manifest.model});
}
