import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
export const sha=value=>createHash('sha256').update(value).digest('hex');
export const fixturePath=(process.env.PROVIDER_CATALOG_FACTS || (() => { throw new Error("Set PROVIDER_CATALOG_FACTS to a local fixture"); })());
export function readCatalogFixture(){
  const bytes=readFileSync(fixturePath),fixture=JSON.parse(bytes);
  assert.equal(sha(bytes),'fb5f52c59c251d204a750213104c670761636e5a82e1a67a47954811cb476b74');
  const read=descriptor=>{const value=fixture.facts[descriptor.address]?.text;assert.equal(sha(value),descriptor.sha256);assert.equal(Buffer.byteLength(value),descriptor.size);return value;};
  const root=JSON.parse(read(fixture.entry)),manifest=JSON.parse(read(fixture.manifest));
  assert.equal(fixture.manifest.sha256,'8d1968564339972de4de5bb323096d9d9762c0a79367586e0a1e6001bf6aa926');
  assert.equal(manifest.model.sha256,'8fc5b0a7252879de2e3e65bce69836750ea1560c1d60a397d3c175d9abe8f70e');
  const model=JSON.parse(read(manifest.model));read(manifest.contract);
  assert.equal(model.binding.commit,'a0dac83394748582158ee9c63a8b23c715872a1e');
  assert.equal(model.binding.base_manifest.sha256,'43e2c68c22125fa9e390d3970bb3ba890e7304a4d3fbd6c1bb39465138b1faa2');
  return {fixture,fixtureHash:sha(bytes),root,manifest,model,content:{objects:model.objects,relations:model.relations,evidence:model.evidence,
    mechanisms:model.mechanisms,batchKey:fixture.manifest.sha256,modelDescriptor:manifest.model}};
}
