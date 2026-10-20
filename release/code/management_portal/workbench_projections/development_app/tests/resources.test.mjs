import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createResources, requireReadable } from '../src/shared/resources.js';

test('unavailable without a reason never invents a permission denial', () => {
  assert.throws(() => requireReadable({ readable: false }, 'file'), { code: 'resource_unavailable' });
  for (const code of ['file_not_found', 'resource_not_found', 'permission_denied']) {
    assert.throws(() => requireReadable({ readable: false, error: { code } }, 'file'), { code });
  }
});

test('read relies on authorized body endpoint, preserves anchors and metadata', async () => {
  const calls = [];
  const resources = createResources({ resource: async (operation, args) => {
    calls.push([operation, args]);
    if (args.address === 'private') throw Object.assign(new Error('denied'), { code: 'permission_denied' });
    return { encoding: 'base64', content: Buffer.from('{"name":"中文"}').toString('base64'), metadata: { version: 'v1' } };
  } });
  const value = await resources.read('plan_111111111111#L42', 'json');
  assert.equal(value.ref, 'plan_111111111111#L42');
  assert.deepEqual(value.data, { name: '中文' });
  assert.deepEqual(value.metadata, { version: 'v1' });
  assert.deepEqual(calls.map(value => value[1].address), ['plan_111111111111']);
  assert.deepEqual(await resources.read('private'), { readable: false, ref: 'private', data: null, error: { code: 'permission_denied', message: 'denied' }, status: undefined });
  assert.equal(calls.length, 2);
});

test('filter returns readable objects without rewriting prerequisite facts', async () => {
  const resources = createResources({ resource: async (operation, args) => operation === 'resolve_many' ? {results:args.addresses.map(address=>({address,ok:true,result:{readable:address!=='private'}}))} : {readable:args.address!=='private'} });
  const items = [{ ref: 'visible', depends_on: ['private'] }, { ref: 'private' }];
  assert.deepEqual(await resources.readable(items), [items[0]]);
  assert.deepEqual(items[0].depends_on, ['private']);
});

test('service errors are not turned into lack of permission or empty data', async () => {
  const resources = createResources({ resource: async () => { throw new Error('service unavailable'); } });
  await assert.rejects(resources.read('ref'), /service unavailable/);
});

test('read handles endpoint revocation or deletion without swallowing service and version errors', async () => {
  const ref = 'repo_111111111111/file/map.yaml#L2-L4';
  for (const format of ['text', 'yaml']) {
    for (const code of ['permission_denied', 'file_not_found', 'resource_not_found', 'service_unavailable', 'invalid_argument', 'version_conflict']) {
      const calls = [];
      const error = Object.assign(new Error(code), { code, status: code === 'service_unavailable' ? 503 : undefined });
      const resources = createResources({ resource: async (operation, args) => {
        calls.push([operation, args.address]);
        if (operation === 'resolve') return { readable: true };
        throw error;
      } });
      if (['permission_denied', 'file_not_found', 'resource_not_found'].includes(code)) {
        assert.deepEqual(await resources.read(ref, format), { readable: false, ref, data: null, error: { code, message: code }, status: undefined }, `${format}: ${code}`);
      } else {
        await assert.rejects(resources.read(ref, format), value => value === error, `${format}: ${code}`);
      }
      assert.deepEqual(calls, [['read', ref.split('#')[0]]]);
    }
  }
});
