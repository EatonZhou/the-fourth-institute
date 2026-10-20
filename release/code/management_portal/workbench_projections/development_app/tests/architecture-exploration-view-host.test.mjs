import test from 'node:test';
import assert from 'node:assert/strict';
import { createExplorationViewHost, instructionWorkbenchFactory } from '../src/architecture/exploration-view-host.js';

function surface() {
  const document = { createElement: () => ({ ownerDocument: document, replaceChildren(...children) { this.children = children; }, children: [] }) };
  return document.createElement();
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function renderer(log, label, state = {}) {
  return { show: () => log.push(label + ':show'), hide: () => log.push(label + ':hide'),
    destroy: () => log.push(label + ':destroy'), getViewState: () => state,
    restoreViewState: value => { state = value; log.push(label + ':restore'); },
    getScene: () => ({ status: 'simulated', value: state }) };
}

test('late renderer cannot replace the newer authorized view or emit navigation', async () => {
  const root = surface(), host = createExplorationViewHost(root), pending = deferred(), log = [], events = [];
  let first;
  const oldMount = host.mount({ factory: (_root, props) => { first = props; return pending.promise; }, ports: { navigate: x => events.push(x) } });
  const oldStage = root.children[0];
  await host.mount({ factory: () => renderer(log, 'new') });
  first.ports.navigate('old');
  pending.resolve(renderer(log, 'old'));
  assert.deepEqual(await oldMount, { status: 'superseded' });
  assert.equal(first.signal.aborted, true);
  assert.notEqual(root.children[0], oldStage);
  assert.deepEqual(log, ['new:show', 'old:hide', 'old:destroy']);
  assert.deepEqual(events, []);
});

test('hide revokes old callbacks and returning restores local state before showing', async () => {
  const root = surface(), host = createExplorationViewHost(root), log = [], events = [];
  let props;
  await host.mount({ factory: (_root, p) => { props = p; return renderer(log, 'a', { filter: 'claims' }); }, ports: { change: x => events.push(x) } });
  props.ports.change('before');
  const captured = host.hide();
  props.ports.change('after');
  assert.deepEqual(events, ['before']);
  assert.deepEqual(root.children, []);
  assert.equal(props.signal.aborted, true);
  await host.mount({ factory: () => renderer(log, 'b'), initialState: captured.viewState });
  assert.deepEqual(host.capture().viewState, { filter: 'claims' });
  assert.deepEqual(log, ['a:show', 'a:hide', 'a:destroy', 'b:restore', 'b:show']);
  host.destroy();
  await assert.rejects(host.mount({ factory: () => renderer(log, 'c') }), /destroyed/);
});

test('failed current mount leaves no old content; stale failure cannot clear a newer view', async () => {
  const root = surface(), host = createExplorationViewHost(root), log = [];
  await host.mount({ factory: () => renderer(log, 'old') });
  await assert.rejects(host.mount({ factory: () => { throw new Error('content rejected'); } }), /content rejected/);
  assert.deepEqual(root.children, []);
  let reject;
  const late = host.mount({ factory: () => new Promise((_r, rej) => { reject = rej; }) });
  await host.mount({ factory: () => renderer(log, 'new') });
  const stage = root.children[0]; reject(new Error('late'));
  assert.deepEqual(await late, { status: 'superseded' });
  assert.equal(root.children[0], stage);
});

test('instruction adapter maps only approved ports without an SDK or separate navigation', async () => {
  const root = surface(), host = createExplorationViewHost(root), calls = [], subject = { kind: 'protocol:hatg-isa' };
  let received;
  await host.mount({
    factory: instructionWorkbenchFactory((_root, props) => { received = props; return renderer([], 'isa'); }),
    subject, content: { catalog: [] },
    ports: { select: x => calls.push(['select', x]), navigate: x => calls.push(['navigate', x]), return: () => calls.push(['back']) },
  });
  assert.deepEqual(Object.keys(received).sort(), ['subject','content','signal','onSelect','onNavigate','onSource','onChange','onReturn'].sort());
  assert.equal(received.subject, subject);
  const route = { kind: 'mechanism', objectId: 'worker.runtime', mechanismId: 'reconcile' };
  received.onNavigate(route); received.onReturn();
  assert.deepEqual(calls, [['navigate', route], ['back']]);
  host.destroy(); received.onSelect('late');
  assert.equal(calls.length, 2);
});

test('state capture or renderer cleanup failure cannot keep protected content visible', async () => {
  const root = surface(), host = createExplorationViewHost(root);
  let destroyed = false, signal;
  await host.mount({ factory: (_root, props) => {
    signal = props.signal;
    return { getViewState() { throw new Error('bad state'); },
      hide() { throw new Error('cleanup failed'); }, destroy() { destroyed = true; } };
  } });
  assert.throws(() => host.hide(), /cleanup failed/);
  assert.deepEqual(root.children, []);
  assert.equal(destroyed, true);
  assert.equal(signal.aborted, true);
});

