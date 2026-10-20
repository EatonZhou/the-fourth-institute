import test from 'node:test';
import assert from 'node:assert/strict';
import { createExplorationNavigation } from '../src/architecture/exploration-navigation.js';

const composition = objectId => ({ kind: 'composition', objectId, mechanismId: null });
const mechanism = { kind: 'mechanism', objectId: 'worker.runtime', mechanismId: 'reconcile' };
const protocol = { kind: 'protocol', objectId: 'worker-lease', mechanismId: null };
const context = { mode: 'current', batchKey: 'verified-pair-A' };
function setup() {
  const allowed = new Set(['execution', 'worker', 'worker.runtime', 'worker-lease']);
  const selections = new Set(['worker.runtime', 'worker']);
  const controls = { allowState: true };
  const nav = createExplorationNavigation({
    resolveRoute: (_ctx, route) => route.objectId === null || allowed.has(route.objectId) ? route : null,
    resolveSelection: (_ctx, _route, id) => selections.has(id),
    sanitizeViewState: (_ctx, _route, state) => controls.allowState ? state ?? null : null,
  });
  return { nav, allowed, selections, controls };
}

test('first entry into another root does not inherit a visited root camera', () => {
  const { nav } = setup();
  const old = { route: composition('execution'), camera: { x: -2000, y: -1400, k: 2 } };
  const worker = nav.enter(context, composition('worker'), old);
  assert.equal(worker.camera, null);
  assert.deepEqual(nav.back(context, worker).camera, old.camera);
});

test('composition, mechanism and protocol have separate state and actual return order', () => {
  const { nav } = setup();
  const root = { route: composition('worker'), camera: { x: 20, y: 30, k: 1.3 } };
  const inside = nav.enter(context, mechanism, root);
  inside.viewState = { checkpoint: 'provider_completed' };
  const lease = nav.enter(context, protocol, inside);
  lease.viewState = { claim: 'B' };
  assert.deepEqual(nav.back(context, lease).route, mechanism);
  assert.deepEqual(nav.back(context, inside).route, root.route);
  assert.deepEqual(nav.restore(context, mechanism).viewState, inside.viewState);
  assert.deepEqual(nav.restore(context, protocol).viewState, lease.viewState);
});

test('historical reading state is rejected and old current batches are discarded', () => {
  const { nav } = setup();
  nav.save(context, { route: mechanism, viewState: { input: 'A' } });
  assert.equal(nav.restore({ ...context, batchKey: 'verified-pair-B' }, mechanism).viewState, null);
  assert.throws(() => nav.restore({ ...context, mode: 'history' }, mechanism), { code: 'missing_exploration_batch' });
  assert.equal(nav.restore(context, mechanism).viewState, null);
  assert.throws(() => nav.save(context, { route: mechanism, batchKey: 'verified-pair-B' }), { code: 'exploration_batch_mismatch' });
  assert.throws(() => nav.save(context, { route: mechanism, mode: 'history' }), { code: 'exploration_mode_mismatch' });
});

test('unavailable target cannot change the departing view or return history', () => {
  const { nav } = setup();
  const root = nav.enter(context, composition('worker'), { route: composition('execution') });
  assert.throws(() => nav.enter(context, composition('missing'), root), { code: 'exploration_route_unavailable' });
  assert.deepEqual(nav.back(context).route, composition('execution'));
  assert.equal(nav.back(context), null);
});

test('revocation prunes routes, selection, focus and domain state before restore', () => {
  const { nav, allowed, selections, controls } = setup();
  const root = { route: composition('worker'), selectionId: 'worker.runtime', returnFocusId: 'worker.runtime', viewState: { privateInput: 'local' } };
  nav.enter(context, mechanism, root);
  nav.enter(context, protocol, { route: mechanism });
  allowed.delete('worker.runtime'); selections.delete('worker.runtime'); controls.allowState = false;
  nav.revalidate(context);
  const restored = nav.back(context, { route: protocol });
  assert.deepEqual(restored.route, root.route);
  assert.equal(restored.selectionId, null);
  assert.equal(restored.returnFocusId, null);
  assert.equal(restored.viewState, null);
  assert.throws(() => nav.restore(context, mechanism), { code: 'exploration_route_unavailable' });
});

test('revoked current route still permits return; stored state is copied', () => {
  const { nav, allowed } = setup();
  const old = { route: composition('worker'), viewState: { values: [1] } };
  nav.enter(context, mechanism, old);
  old.viewState.values.push(2);
  allowed.delete('worker.runtime');
  const back = nav.back(context, { route: mechanism });
  assert.deepEqual(back.viewState, { values: [1] });
  back.viewState.values.push(3);
  assert.deepEqual(nav.restore(context, old.route).viewState, { values: [1] });
});

test('invalid routes, silent identity substitution and unsupported restore fail explicitly', () => {
  const { nav } = setup();
  assert.throws(() => nav.restore(context, { kind: 'mechanism', objectId: 'worker' }), { code: 'invalid_exploration_route' });
  assert.throws(() => nav.restore({ mode: 'current' }, composition(null)), { code: 'missing_exploration_batch' });
  const bad = createExplorationNavigation({
    resolveRoute: () => composition('worker'), resolveSelection: () => false, sanitizeViewState: () => null,
  });
  assert.throws(() => bad.restore(context, composition('execution')), { code: 'exploration_route_identity_changed' });
  assert.throws(() => createExplorationNavigation(), { code: 'exploration_authorization_callbacks_required' });
});

