import fs from 'node:fs';
import crypto from 'node:crypto';
const digest = data => crypto.createHash('sha256').update(data).digest('hex');
// Reuse the authorized owner/leader fixture. No second copy of the fact model.
export function readLineageFixture() {
  const filename = process.env.LINEAGE_MODEL_FIXTURE;
  if (!filename) throw new Error('LINEAGE_MODEL_FIXTURE must identify the frozen development fixture or decoded model');
  const raw = fs.readFileSync(filename), parsed = JSON.parse(raw);
  let text = raw.toString();
  if (parsed.facts) {
    const ref = process.env.LINEAGE_MODEL_REF;
    if (!ref || !parsed.facts[ref]?.text) throw new Error('LINEAGE_MODEL_REF must select the immutable model in this fixture');
    text = parsed.facts[ref].text;
  }
  const model = JSON.parse(text), fixtureHash = digest(raw), modelHash = digest(text);
  return {model,fixtureHash,modelHash,content:{
    mechanism:model.mechanisms.find(m=>m.id==='mechanism.lineage.effect-journal'),
    objects:model.objects,relations:model.relations,evidence:model.evidence,batchKey:`${fixtureHash}:${modelHash}`,
  }};
}
