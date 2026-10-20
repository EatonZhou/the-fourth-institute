import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createEvidenceLoader, sha256 } from '../src/architecture/data.js';
import { createExplorationLoader } from '../src/architecture/exploration-data.js';
import { digest, readText } from '../src/progress/data.js';

test('HTTP without WebCrypto verifies UTF-8 evidence and rejects changed content', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
  try {
    for (const text of ['', 'abc', '架构事实\n🙂', 'x'.repeat(65)]) {
      assert.equal(await sha256(text), createHash('sha256').update(text).digest('hex'));
      assert.equal(await digest(text), createHash('sha256').update(text).digest('hex'));
      assert.equal(await digest(new TextEncoder().encode(text)), await sha256(text));
    }
    const content = JSON.stringify({ title: '不可变架构事实' });
    const expected = createHash('sha256').update(content).digest('hex');
    let actual = content;
    const loader = createEvidenceLoader({
      resources: { read: async () => ({ readable: true, data: actual }) },
      sdk: {},
    });
    // The production evidence reader must still enforce the immutable digest.
    const artifact = { address: 'workspace_example/file/facts.json', sha256: expected,
      size: new TextEncoder().encode(content).length };
    assert.equal((await loader.json(artifact)).data.title, '不可变架构事实');
    actual = content.replace('不可变', '已改变');
    await assert.rejects(loader.json(artifact), /摘要不匹配/);
    const resources = { read: async () => ({ readable: true, data: actual }) };
    assert.equal((await readText(resources, artifact.address, expected)).failure.kind, 'version');
    actual = content;
    assert.equal((await readText(resources, artifact.address, expected)).text, content);

    const model = { schema: 'tfi.portal-architecture-exploration.v1',
      objects: [{ id: 'module', title: '当前说明' }], relations: [], mechanisms: [], evidence: [] };
    const manifest = { schema: 'tfi.portal-architecture-exploration-manifest.v1',
      model: { address: 'workspace_example/file/model.json' } };
    const exploration = createExplorationLoader({ resources: {
      read: async address => ({ readable: true, data: JSON.stringify(address.endsWith('model.json') ? model : manifest) }),
    } });
    const pair = { exploration: { manifest: { address: 'workspace_example/file/manifest.json' } } };
    const before = await exploration.loadPair(pair);
    assert.equal(before.status, 'available');
    assert.equal(before.model.objects[0].title, '当前说明');
    model.objects[0].title = '更新说明';
    const after = await exploration.loadPair(pair);
    assert.notEqual(after.batchKey, before.batchKey);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor);
    else delete globalThis.crypto;
  }
});
