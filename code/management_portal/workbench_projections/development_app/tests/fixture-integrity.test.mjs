import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';

const sha = value => createHash('sha256').update(value).digest('hex');
const folder = new URL('./fixtures/frozen-exploration/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('manifest.json', folder)));

function verifyCapture(capture) {
  let checked = 0;
  const facts = capture.facts || {};
  function visit(value) {
    if (Array.isArray(value)) return value.forEach(visit);
    if (!value || typeof value !== 'object') return;
    const body = facts[value.address]?.text;
    if (typeof body === 'string' && typeof value.sha256 === 'string') {
      assert.equal(sha(body), value.sha256, `${value.address}: sha256`);
      if (Object.hasOwn(value, 'size')) assert.equal(Buffer.byteLength(body), value.size, `${value.address}: size`);
      checked++;
    }
    for (const child of Object.values(value)) {
      if (typeof child === 'string' && /^[\s]*[\[{]/.test(child)) {
        let document;
        try { document = JSON.parse(child); } catch { continue; }
        visit(document);
      } else visit(child);
    }
  }
  visit(capture);
  if (capture.entry) assert.ok(checked > 100, 'A captured graph must verify its internal descriptors');
}

for (const [name, descriptor] of Object.entries(manifest.fixtures)) {
  test(`frozen fixture ${name}: compressed bytes, body and descriptor graph`, () => {
    const compressed = readFileSync(new URL(descriptor.file, folder));
    assert.equal(sha(compressed), descriptor.gzip_sha256);
    const bytes = gunzipSync(compressed);
    assert.equal(bytes.length, descriptor.size);
    assert.equal(sha(bytes), descriptor.sha256);
    verifyCapture(JSON.parse(bytes));
  });
}

const browserFixtures = new URL('../../../../tests/cases/fixtures/development_projection/', import.meta.url);
for (const name of readdirSync(browserFixtures).filter(name => name.endsWith('.json'))) {
  test(`browser fixture ${name}: embedded descriptors`, () => {
    verifyCapture(JSON.parse(readFileSync(new URL(name, browserFixtures))));
  });
}
