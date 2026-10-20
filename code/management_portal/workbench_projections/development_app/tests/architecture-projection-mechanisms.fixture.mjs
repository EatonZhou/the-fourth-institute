import fs from 'node:fs';
import {createHash} from 'node:crypto';
export const FIXTURE_SHA = '053a04e22722a72244bb8262e586425ea1580b7fd17a8a98a13c336764e31ea6';
const models = {
  [FIXTURE_SHA]: '116d3a72b0e185fd54e6465452a2b0c6606a708a2ffb4947004fe843d8fc7eb0',
  '7cb53b6cbb4a2adaa7b3588e9a4d0d7d19d678c99971ab0eab0290b5c70e374e': '3b3660fb56b08759f761ff49cc25ca4c201cbc55a382584d0ce3e06e1f98e9ad',
};
export const hash = x => createHash('sha256').update(x).digest('hex');
export function readProjectionFixture() {
  if (!process.env.PROJECTION_MODEL_FIXTURE) throw Error('Set PROJECTION_MODEL_FIXTURE to the fixed domains-development fixture');
  const bytes = fs.readFileSync(process.env.PROJECTION_MODEL_FIXTURE);
  const fixtureHash = hash(bytes), modelHash = models[fixtureHash];
  if (!modelHash) throw Error('projection_fixture_hash_mismatch');
  const capture = JSON.parse(bytes), entry = Object.entries(capture.facts).find(([k]) => k.endsWith('/model-' + modelHash + '.json'));
  if (!entry || hash(entry[1].text) !== modelHash) throw Error('projection_model_hash_mismatch');
  const model = JSON.parse(entry[1].text);
  const mechanisms = model.mechanisms.filter(x => ['web.m.panel-lifecycle','web.m.projection-scope','web.m.focus-revision','web.m.feedback-confirmation','web.m.semantic-validation','web.m.generation-save','web.m.ui-association','projection.mechanism.assembly','projection.mechanism.opaque-bridge','projection.mechanism.host-focus-order','projection.mechanism.region-detail-order','projection.mechanism.feedback-budget'].includes(x.id));
  const ids = new Set(mechanisms.flatMap(m => [m, ...m.steps, ...m.cases].flatMap(x => x.evidence_ids)));
  const evidence = model.evidence.filter(e => ids.has(e.id));
  for (const e of evidence) {
    const body = capture.facts[e.frozen.address]?.text;
    if (!body || hash(body) !== e.frozen.sha256 || Buffer.byteLength(body) !== e.frozen.size) throw Error('projection_source_hash_mismatch:' + e.id);
    const excerpt = body.split('\n').slice(e.start_line - 1, e.end_line).join('\n');
    if (excerpt.trimEnd() !== e.excerpt.trimEnd()) throw Error('projection_source_lines_mismatch:' + e.id);
  }
  return {model, mechanisms, evidence, capture, fixtureHash,
    content: id => ({batchKey: capture.manifest.sha256, mechanism: mechanisms.find(m => m.id === id), objects: model.objects, evidence})};
}
