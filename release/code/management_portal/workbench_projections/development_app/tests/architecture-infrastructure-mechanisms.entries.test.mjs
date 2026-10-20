import {readFileSync} from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {infrastructureObjectEntries, resolveInfrastructureEntry} from '../src/architecture/infrastructure-mechanisms.entries.js';

const fixture = JSON.parse(readFileSync(new URL('./architecture-infrastructure-mechanisms.fixture.json', import.meta.url)));
const content = () => structuredClone(fixture);

test('authored entry table covers the fixed infrastructure objects with exact identities', () => {
  const owned = fixture.objects.filter(o => o.mechanism_ids.some(id => id.startsWith('infra.mechanism.')));
  assert.deepEqual(Object.keys(infrastructureObjectEntries).sort(), owned.map(o => o.id).sort());
  for (const object of owned) {
    const entry = infrastructureObjectEntries[object.id], result = resolveInfrastructureEntry(object.id, fixture);
    assert.ok(object.mechanism_ids.includes(entry.defaultMechanismId));
    if (object.child_ids.length && object.kind !== 'protocol') {
      assert.deepEqual(result.route, {kind: 'composition', objectId: object.id, mechanismId: null});
      assert.equal(result.factory, null);
    } else {
      assert.equal(result.route.mechanismId, entry.defaultMechanismId);
      assert.equal(result.route.objectId, object.id);
      assert.equal(result.route.kind, object.kind === 'protocol' ? 'protocol' : 'mechanism');
      assert.equal(result.factory, 'createInfrastructureWorkbench');
    }
  }
});

test('protocol defaults are explicit and unaffected by array order or object titles', () => {
  const c = content(); c.mechanisms.reverse(); c.objects.reverse();
  for (const o of c.objects) o.title = '同名对象';
  const expected = {'signed-rpc': 'rpc-envelope', 'change-refresh': 'change-refresh', 'deployment-control': 'deployment-handoff',
    'release-events': 'release-cursor', 'application-target': 'target-freeze'};
  for (const [id, suffix] of Object.entries(expected)) assert.equal(resolveInfrastructureEntry(id, c).route.mechanismId, `infra.mechanism.${suffix}`);
  assert.equal(resolveInfrastructureEntry('delivery.services.release', c).route.mechanismId, 'infra.mechanism.release-publication');
});

test('unavailable or mismatched identities never redirect to another mechanism', () => {
  for (const id of ['delivery.backup', 'signed-rpc.extra', 'constructor', 'resources']) assert.throws(() => resolveInfrastructureEntry(id, fixture), /entry_unsupported_object/);
  const absent = content(); absent.objects = absent.objects.filter(o => o.id !== 'signed-rpc');
  assert.throws(() => resolveInfrastructureEntry('signed-rpc', absent), /entry_object_unavailable/);
  const changed = content(); changed.mechanisms.find(m => m.id === 'infra.mechanism.rpc-envelope').adapter = 'portal.infrastructure.release-publication.v1';
  assert.throws(() => resolveInfrastructureEntry('signed-rpc', changed), /entry_mechanism_mismatch/);
  const missing = content(); missing.mechanisms = missing.mechanisms.filter(m => m.id !== 'infra.mechanism.rpc-envelope');
  assert.throws(() => resolveInfrastructureEntry('signed-rpc', missing), /entry_mechanism_unavailable/);
});

test('nonleaf composition does not depend on an authorized mechanism and leaf changes retain composition', () => {
  const c = content(); c.mechanisms = [];
  assert.equal(resolveInfrastructureEntry('delivery', c).route.kind, 'composition');
  assert.throws(() => resolveInfrastructureEntry('delivery', c, {mechanismId: 'infra.mechanism.release-publication'}), /entry_mechanism_unavailable/);
  c.objects.find(o => o.id === 'delivery.services.release').child_ids = ['delivery.services.release.real-child'];
  assert.equal(resolveInfrastructureEntry('delivery.services.release', c).route.kind, 'composition');
});

test('unreviewed added mechanism associations do not become a default or an internal mode', () => {
  const c = content(), o = c.objects.find(o => o.id === 'signed-rpc');
  o.mechanism_ids.unshift('infra.mechanism.release-publication');
  const entry = resolveInfrastructureEntry(o.id, c);
  assert.equal(entry.route.mechanismId, 'infra.mechanism.rpc-envelope');
  assert.deepEqual(entry.modes.map(m => m.mechanismId), ['infra.mechanism.rpc-envelope']);
  assert.throws(() => resolveInfrastructureEntry(o.id, c, {mechanismId: 'infra.mechanism.release-publication'}), /entry_mechanism_unavailable/);
  o.mechanism_ids = ['infra.mechanism.release-publication'];
  assert.throws(() => resolveInfrastructureEntry(o.id, c), /entry_mechanism_unavailable/);
});
