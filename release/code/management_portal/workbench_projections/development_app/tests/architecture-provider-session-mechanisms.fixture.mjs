import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
export const sha = value => createHash('sha256').update(value).digest('hex');
export const fixturePath = (process.env.PROVIDER_SESSION_FACTS || (() => { throw new Error("Set PROVIDER_SESSION_FACTS to a local fixture"); })());
export function readProviderSessionFixture() {
  const bytes = readFileSync(fixturePath), fixture = JSON.parse(bytes);
  assert.equal(sha(bytes), '1bb21129ee61d00c4f48b80946086980fa70ff8f2796820160c1ef5d422db3bc');
  const read = descriptor => {
    const text = fixture.facts[descriptor.address]?.text;
    assert.equal(sha(text), descriptor.sha256, descriptor.address);
    assert.equal(Buffer.byteLength(text), descriptor.size);
    return text;
  };
  const root = JSON.parse(read(fixture.entry)), pair = JSON.parse(read(fixture.pair));
  const manifest = JSON.parse(read(fixture.manifest));
  assert.equal(manifest.model.sha256, 'daf11b2d9e220ca7ef37fc058e798931c6d610b8c98dd65ca326097ef05cecd7');
  assert.equal(manifest.contract.sha256, '4abe99a3c5139cce0816b527baefddaaeee3232efd7d50f9c62457383473d1ac');
  const model = JSON.parse(read(manifest.model)), contract = read(manifest.contract);
  assert.equal(model.binding.commit, 'a0dac83394748582158ee9c63a8b23c715872a1e');
  return {fixture, root, pair, manifest, model, contract, fixtureHash:sha(bytes), content: {
    objects:model.objects, relations:model.relations, evidence:model.evidence, batchKey:fixture.manifest.sha256,
    modelDescriptor:manifest.model, mechanisms:model.mechanisms,
  }};
}
