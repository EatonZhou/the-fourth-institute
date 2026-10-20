import test from 'node:test';
import assert from 'node:assert/strict';
import { createExplorationSession } from '../src/architecture/exploration-session.js';

test('repeated visible snapshots do not rescan; explicit observation still authorizes', async () => {
  const r = rig(); await r.session.load('fixed');
  let checks = 0; const original = r.loader.revalidate;
  r.loader.revalidate = async (...args) => { checks++; return original(...args); };
  const before = r.mounts.length;
  await r.session.show(); await r.session.show();
  assert.equal(checks, 0); assert.equal(r.mounts.length, before);
  await r.session.revalidate(); assert.equal(checks, 1);
  r.controls.deny = true;
  await assert.rejects(r.session.revalidate(), /denied/);
  assert.equal(checks, 2); assert.equal(r.visible(), null);
});

test('repeated visible during navigation cannot overwrite the in-flight target', async () => {
  const r = rig(); await r.session.load('fixed');
  let release, checks = 0; const original = r.loader.revalidate;
  r.loader.revalidate = async (...args) => { checks++; return original(...args); };
  r.controls.pending = new Promise(resolve => { release = resolve; });
  const moving = r.session.navigate(route('runtime'));
  await r.session.show(); await r.session.show();
  assert.equal(checks, 1);
  release(); await moving;
  assert.deepEqual(r.visible(), route('runtime')); assert.equal(r.mounts.length, 2);
});

test('a real hidden-to-visible transition reauthorizes and cannot restore a revoked frame', async () => {
  const r = rig(); await r.session.load('fixed');
  let checks = 0; const original = r.loader.revalidate;
  r.loader.revalidate = async (...args) => { checks++; return original(...args); };
  r.setState({ private: 'must not restore after revocation' });
  r.session.hide(); r.controls.deny = true;
  await assert.rejects(r.session.show(), /denied/);
  assert.equal(checks, 1); assert.equal(r.visible(), null);
  assert.equal(r.session.snapshot().status, 'error');
  assert.equal(r.session.snapshot().route, undefined);
});
import { resolveDomainEntry } from '../src/architecture/exploration-entry-routes.js';

const route = (objectId, kind = 'composition', mechanismId = null) => ({ objectId, kind, mechanismId });
function rig({ resolveEntry, catalogBundle, focusView } = {}) {
  const events = [], mounts = [], focuses = [], opened = [], sourceErrors = [], controls = { deny: false, pending: null, candidates: new Map() };
  let visible = null, state = null, pins = [];
  const model = { objects: ['execution', 'runtime', 'session', 'protocol'].map(id => ({ id,
    kind: id === 'protocol' ? 'protocol' : 'module', mechanism_ids: ['flow', 'other-flow'],
    child_ids: id === 'execution' ? ['runtime'] : [],
    identity: { local_only: id === 'runtime', focus_kind: 'module:' + id } })),
    mechanisms: [{ id: 'flow' }, { id: 'other-flow' }], relations: [{ id: 'call' }], evidence: [{ id: 'source' }] };
  const loader = {
    async loadDelivery(pin, { mode }) { pins.push(pin); if (controls.candidates.has(pin)) await controls.candidates.get(pin); return { status: 'available', batchKey: pin, mode, model, binding: { commit: pin } }; },
    async revalidate() { if (controls.pending) await controls.pending; if (controls.deny) throw Object.assign(Error('denied'), { reason: 'permission_denied' }); },
    async source() { await this.revalidate(); return { ref: 'fixed#L1-L2' }; },
    async catalogSource() { throw Error('unexpected catalog source'); },
  };
  const host = {
    capture() { return { viewState: state, scene: visible }; },
    hide() { visible = null; }, destroy() { visible = null; },
    async mount(value) { mounts.push(value); visible = value.content.route; state = value.initialState; return { status: 'mounted' }; },
  };
  const session = createExplorationSession({ loader, host, resolveEntry, focusView,
    async resolveView(bundle, requested) { return { factory() {}, content: { route: requested, batch: bundle.batchKey }, catalogBundle }; },
    async focusObject(bundle, target, valid) { if (valid()) focuses.push({ batch: bundle.batchKey, id: target.id }); },
    openSource(value) { opened.push(value); }, onSourceError(error) { sourceErrors.push(error); }, onChange(value) { events.push(value); } });
  return { session, loader, controls, focuses, mounts, events, opened, sourceErrors, pins, setState: value => { state = value; }, visible: () => visible };
}

test('background permission checks and repeated show preserve the live frame and do not notify loading', async () => {
  const r = rig(); await r.session.load('fixed');
  await r.session.navigate(route('runtime'));
  r.setState({ camera: { x: 12, y: 3, k: 2 }, selection: 'session' });
  const before = r.session.snapshot(), count = r.mounts.length, events = r.events.length;
  let release; r.controls.pending = new Promise(resolve => { release = resolve; });
  const first = r.session.revalidate(), duplicate = r.session.revalidate();
  assert.equal(first, duplicate); assert.deepEqual(r.session.snapshot(), before);
  release(); await first; r.controls.pending = null; await r.session.show();
  assert.equal(r.mounts.length, count); assert.equal(r.events.length, events);
  assert.deepEqual(r.session.snapshot(), before);
  await r.session.back(); assert.deepEqual(r.visible(), route('execution'));
});

test('observation concurrent with navigation cannot restore the old route or cancel its target', async () => {
  const r = rig(); await r.session.load('fixed');
  let release; r.controls.pending = new Promise(resolve => { release = resolve; });
  const observation = r.session.revalidate();
  const navigation = r.session.navigate(route('runtime'));
  const during = r.session.revalidate();
  release(); await Promise.all([observation, navigation, during]);
  assert.deepEqual(r.visible(), route('runtime')); assert.equal(r.session.snapshot().status, 'ready');
  assert.equal(r.mounts.length, 2);
  r.controls.pending = null; await r.session.back(); assert.deepEqual(r.visible(), route('execution'));
});

test('quiet permission revocation clears the frame and cannot resurrect it after hide', async () => {
  const r = rig(); await r.session.load('fixed'); r.controls.deny = true;
  await assert.rejects(r.session.revalidate(), /denied/);
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().status, 'error');
  assert.equal(r.session.snapshot().route, undefined);
  r.controls.deny = false; await r.session.show();
  let release; r.controls.pending = new Promise(resolve => { release = resolve; });
  const pending = r.session.revalidate(); r.session.hide(); release(); await pending;
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().status, 'hidden');
});

test('a pending model denial still clears a later route in the same batch', async () => {
  const r = rig(); await r.session.load('fixed');
  let reject; const original = r.loader.revalidate;
  r.loader.revalidate = async () => { r.loader.revalidate = original; await new Promise((_, fail) => { reject = fail; }); };
  const checking = r.session.revalidate();
  await r.session.navigate(route('runtime')); assert.deepEqual(r.visible(), route('runtime'));
  reject(Object.assign(Error('denied'), { reason: 'permission_denied' }));
  await assert.rejects(checking, /denied/); assert.equal(r.visible(), null); assert.equal(r.session.snapshot().status, 'error');
});

test('quiet revalidation checks the active catalog supplement as well as its model', async () => {
  const catalog = { status: 'available', batchKey: 'catalog' }, calls = [];
  const r = rig({ catalogBundle: catalog }); await r.session.load('fixed');
  // The shared gate blocks both checks; revocation between the model and
  // catalog decisions must still remove the catalog view.
  let denyCatalog = false;
  const session = createExplorationSession({
    loader: { async revalidate(value) { calls.push(value.batchKey); if (value === catalog && denyCatalog) throw Object.assign(Error('catalog denied'), { reason: 'permission_denied' }); } },
    host: { capture: () => ({}), hide() { calls.push('hide'); }, mount: async () => ({ status: 'mounted' }) },
    resolveView: async () => ({ factory() {}, catalogBundle: catalog }),
  });
  await session.install({ ...r.session.getBundle() }); calls.length = 0; denyCatalog = true;
  await assert.rejects(session.revalidate(), /catalog denied/);
  assert.deepEqual(calls, ['fixed', 'catalog', 'hide']); assert.equal(session.snapshot().status, 'error');
});

test('object mode switches restore each mode but return directly to the original graph', async () => {
  for (const [objectId, kind] of [['session', 'mechanism'], ['protocol', 'protocol']]) {
    const r = rig(); await r.session.load('one');
    const camera = { x: -320, y: 56, k: 1.5 }; r.setState({ camera, selected: objectId });
    await r.session.navigate(route(objectId, kind, 'flow')); r.setState({ prefix: ['first'] });
    await r.mounts.at(-1).ports.navigate(route(objectId, kind, 'other-flow'));
    assert.equal(r.mounts.at(-1).initialState, null); r.setState({ prefix: ['second'] });
    await r.mounts.at(-1).ports.navigate(route(objectId, kind, 'flow'));
    assert.deepEqual(r.mounts.at(-1).initialState, { prefix: ['first'] });
    await r.mounts.at(-1).ports.navigate(route(objectId, kind, 'other-flow'));
    assert.deepEqual(r.mounts.at(-1).initialState, { prefix: ['second'] });
    await r.session.back(); assert.deepEqual(r.visible(), route('execution'));
    assert.deepEqual(r.mounts.at(-1).initialState, { camera, selected: objectId });
    assert.deepEqual(r.focuses, []);
  }
});

test('mode navigation to a different object keeps its return frame; unavailable mode preserves the current view', async () => {
  const r = rig(); await r.session.load('one');
  await r.session.navigate(route('session', 'mechanism', 'flow')); r.setState({ prefix: ['saved'] });
  await assert.rejects(r.mounts.at(-1).ports.navigate(route('session', 'mechanism', 'absent')), /exploration_route_unavailable/);
  assert.deepEqual(r.visible(), route('session', 'mechanism', 'flow'));
  await r.mounts.at(-1).ports.navigate(route('protocol', 'protocol', 'other-flow'));
  await r.session.back(); assert.deepEqual(r.visible(), route('session', 'mechanism', 'flow'));
  assert.deepEqual(r.mounts.at(-1).initialState, { prefix: ['saved'] });
});

test('mode switching reauthorizes and cannot restore revoked object state', async () => {
  const r = rig(); await r.session.load('one');
  await r.session.navigate(route('session', 'mechanism', 'flow')); r.setState({ private: 'input' });
  r.controls.deny = true;
  await assert.rejects(r.mounts.at(-1).ports.navigate(route('session', 'mechanism', 'other-flow')), /denied/);
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().route, undefined);
});

test('composition, mechanism and back use one stack; new roots do not inherit a camera', async () => {
  const r = rig(); await r.session.load('one');
  r.setState({ camera: { x: -500, y: -400, k: 2 }, selected: 'runtime' });
  await r.session.navigate(route('runtime')); assert.equal(r.mounts.at(-1).initialState, null);
  r.setState({ camera: { x: 0, y: 0, k: 1 } });
  await r.session.navigate(route('runtime', 'mechanism', 'flow'));
  await r.session.back(); assert.equal(r.visible().kind, 'composition'); assert.equal(r.visible().objectId, 'runtime');
  await r.session.back(); assert.deepEqual(r.mounts.at(-1).initialState.camera, { x: -500, y: -400, k: 2 });
});

test('history, concept and relation selection never publish a current object', async () => {
  const r = rig(); await r.session.load('one');
  await r.session.select({ objectId: 'runtime' }); await r.session.select({ relationId: 'call' });
  await r.session.navigate(route('protocol', 'protocol'));
  assert.equal(r.focuses.length, 0);
  await r.session.select({ objectId: 'session' }); assert.equal(r.focuses.length, 1);
  await r.session.load('one', { mode: 'history' }); await r.session.select({ objectId: 'session' });
  assert.equal(r.focuses.length, 1);
});

test('authorization completes before remount; revocation clears the current view and return memory', async () => {
  const r = rig(); await r.session.load('one'); r.setState({ secret: 'old detail' }); r.controls.deny = true;
  await assert.rejects(r.session.navigate(route('runtime')), /denied/);
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().route, undefined);
  r.controls.deny = false; await r.session.show(); assert.equal(r.mounts.at(-1).initialState, null);
});

test('hiding during an authorization read prevents a late repaint or focus', async () => {
  const r = rig(); await r.session.load('one');
  let release; r.controls.pending = new Promise(resolve => { release = resolve; });
  const pending = r.session.navigate(route('runtime')); r.session.hide(); release(); await pending;
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().status, 'hidden');
  assert.equal(r.mounts.length, 1);
});

test('a new batch never restores old simulation state; unknown identities cannot redirect', async () => {
  const r = rig(); await r.session.load('one'); r.setState({ step: 8 });
  await r.session.load('two'); assert.equal(r.mounts.at(-1).initialState, null);
  await assert.rejects(r.session.navigate(route('invented')), /exploration_route_unavailable/);
  assert.equal(r.visible().objectId, 'execution');
});

test('source access is exact and revocation clears protected content', async () => {
  const r = rig(); await r.session.load('one'); await r.session.source('source');
  assert.equal(r.opened[0].ref, 'fixed#L1-L2'); r.controls.deny = true;
  await assert.rejects(r.session.source('source'), /denied/); assert.equal(r.visible(), null);
  assert.equal(r.opened.length, 1);
});

test('a rejected source body preserves the verified route, return stack and live scene', async () => {
  const r = rig(); await r.session.load('one'); await r.session.navigate(route('runtime'));
  r.setState({ cursor: 3, camera: { x: 12, y: 34, k: 1.2 } });
  const before = r.session.snapshot(), mounts = r.mounts.length;
  const source = r.loader.source;
  r.loader.source = async () => { throw Object.assign(Error('source_hash_mismatch'), { reason: 'source_hash_mismatch' }); };
  await assert.rejects(r.session.source('source'), /source_hash_mismatch/);
  assert.deepEqual(r.session.snapshot(), before); assert.equal(r.mounts.length, mounts);
  assert.equal(r.visible().objectId, 'runtime'); assert.equal(r.opened.length, 0);
  r.loader.source = source; await r.session.source('source'); assert.equal(r.opened.length, 1);
  await r.session.back(); assert.equal(r.visible().objectId, 'execution');
});

test('late source failure cannot overwrite navigation or a newer source in the same view', async () => {
  for (const navigate of [false, true]) {
    const r = rig(); await r.session.load('one');
    let reject; r.loader.source = () => new Promise((_resolve, fail) => { reject = fail; });
    const old = r.session.requestSource('source');
    if (navigate) await r.session.navigate(route('runtime'));
    r.loader.source = async () => ({ ref: 'new-source' });
    await r.session.requestSource('source');
    const before = r.session.snapshot(); reject(Error('late_source_denied')); await old;
    assert.deepEqual(r.opened, [{ ref: 'new-source' }]); assert.deepEqual(r.sourceErrors, []);
    assert.deepEqual(r.session.snapshot(), before);
  }
});

test('rejected candidate retains the reauthorized old batch and exact view state with a stale notice', async () => {
  const r = rig(); await r.session.load('one'); await r.session.navigate(route('runtime', 'mechanism', 'flow'));
  const saved = { prefix: ['start', 'recover'], camera: { x: -80, y: -32, k: 1.2 } }; r.setState(saved);
  r.controls.candidates.set('bad', Promise.reject(Object.assign(Error('bad binding'), { reason: 'binding_mismatch' })));
  await assert.rejects(r.session.load('bad'), /bad binding/);
  assert.equal(r.session.getBundle().batchKey, 'one'); assert.equal(r.visible().kind, 'mechanism');
  assert.deepEqual(r.mounts.at(-1).initialState, saved);
  assert.equal(r.session.snapshot().refreshError, 'binding_mismatch'); assert.equal(r.session.snapshot().status, 'ready');
  await r.session.navigate(route('execution')); assert.equal(r.session.snapshot().refreshError, 'binding_mismatch');
  await r.session.load('two'); assert.equal(r.session.snapshot().refreshError, null);
});

test('rejected candidate cannot restore an old batch whose permission was revoked', async () => {
  const r = rig(); await r.session.load('one'); r.setState({ private: true }); r.controls.deny = true;
  r.controls.candidates.set('bad', Promise.reject(Error('invalid candidate')));
  await assert.rejects(r.session.load('bad'), /invalid candidate/);
  assert.equal(r.visible(), null); assert.equal(r.session.snapshot().route, undefined);
  assert.equal(r.session.snapshot().error, 'permission_denied');
});

test('late candidate rejection cannot roll back a later installed batch', async () => {
  const r = rig(); await r.session.load('one'); let reject;
  r.controls.candidates.set('late', new Promise((_resolve, no) => { reject = no; }));
  const late = r.session.load('late'); await r.session.load('newer'); reject(Error('late rejection'));
  await assert.rejects(late, /late rejection/);
  assert.equal(r.session.getBundle().batchKey, 'newer'); assert.equal(r.mounts.at(-1).content.batch, 'newer');
  assert.equal(r.session.snapshot().refreshError, null);
});

test('authored defaults enter a protocol and multi-mechanism leaf on the same exact return stack', async () => {
  const entries = { session: 'other-flow', protocol: 'flow', execution: 'flow' };
  const r = rig({ resolveEntry: (bundle, requested) => resolveDomainEntry(bundle.model, requested, entries) });
  await r.session.load('one');
  assert.deepEqual(r.visible(), route('execution')); // Non-leaf remains composition.
  const camera = { x: -700, y: -210, k: 1.4 }; r.setState({ camera });
  await r.session.select({ objectId: 'session' });
  assert.deepEqual(r.visible(), route('execution')); // Single click only selects.
  await r.session.navigate(route('session'));
  assert.deepEqual(r.visible(), route('session', 'mechanism', 'other-flow'));
  r.setState({ caseId: 'resume', step: 4 });
  await r.session.navigate(route('protocol', 'protocol'));
  assert.deepEqual(r.visible(), route('protocol', 'protocol', 'flow'));
  entries.session = 'flow'; // Later entry policy cannot redirect an existing frame.
  await r.session.back();
  assert.deepEqual(r.visible(), route('session', 'mechanism', 'other-flow'));
  assert.deepEqual(r.mounts.at(-1).initialState, { caseId: 'resume', step: 4 });
  r.session.hide(); await r.session.show();
  assert.deepEqual(r.visible(), route('session', 'mechanism', 'other-flow'));
  await r.session.back();
  assert.deepEqual(r.visible(), route('execution'));
  assert.deepEqual(r.mounts.at(-1).initialState.camera, camera);
});

test('authored defaults resolve initial historical entry without publishing a current identity', async () => {
  const r = rig({ resolveEntry: (bundle, requested) => resolveDomainEntry(bundle.model, requested, { protocol: 'other-flow' }) });
  await r.session.load('one', { mode: 'history', route: route('protocol', 'protocol') });
  assert.deepEqual(r.visible(), route('protocol', 'protocol', 'other-flow'));
  await r.session.select({ objectId: 'protocol' }); assert.equal(r.focuses.length, 0);
});

test('authored defaults outside the authorized object reject before replacing the readable view', async () => {
  const r = rig({ resolveEntry: (bundle, requested) => resolveDomainEntry(bundle.model, requested, { session: 'not-in-this-batch' }) });
  await r.session.load('one'); const count = r.mounts.length;
  await assert.rejects(r.session.navigate(route('session')), /对象的默认机制不属于本批已授权内容/);
  assert.equal(r.mounts.length, count); assert.deepEqual(r.visible(), route('execution'));
});

test('historical catalog owner navigation returns to the saved directory without current focus', async () => {
  const r = rig(); await r.session.load('one', { mode: 'history', route: route('protocol', 'protocol') });
  const directory = { instruction: 'Example', query: 'owner', cursor: 2 }; r.setState(directory);
  await r.mounts.at(-1).ports.navigate(route('execution'));
  assert.deepEqual(r.visible(), route('execution'));
  await r.session.select({ objectId: 'execution' }); assert.deepEqual(r.focuses, []);
  await r.session.back(); assert.deepEqual(r.visible(), route('protocol', 'protocol'));
  assert.deepEqual(r.mounts.at(-1).initialState, directory);
  r.controls.deny = true;
  await assert.rejects(r.mounts.at(-1).ports.navigate(route('execution')), /denied/);
  assert.deepEqual(r.focuses, []); assert.equal(r.visible(), null);
});

test('capability routes use one authorized stack and never publish a fabricated member focus', async () => {
 const r=rig();await r.session.load('capability-batch',{route:{kind:'composition',objectId:null}});
 const model=r.session.getBundle().model;model.objects.find(o=>o.id==='execution').parent_id=null;
 for(const id of ['worker','providers'])model.objects.push({id,kind:'module',parent_id:null,child_ids:[],mechanism_ids:[],identity:{local_only:false}});
 const capability={kind:'capability',objectId:null,mechanismId:null,viewId:'model-execution'};
 r.setState({camera:{x:30,y:10,k:1},selected:{viewId:'model-execution'}});
 await r.session.navigate(capability);assert.deepEqual(r.visible(),capability);
 await r.mounts.at(-1).ports.select({viewId:'model-execution',memberIds:['execution','worker','providers']});assert.equal(r.session.snapshot().selectionId,null);assert.deepEqual(r.focuses,[]);
 r.setState({memberIds:['execution','worker','providers'],camera:{x:15,y:0,k:1}});
 await r.session.navigate(route('execution'));await r.session.back();assert.deepEqual(r.visible(),capability);assert.equal(r.mounts.at(-1).initialState.camera.x,15);
 await r.session.back();assert.equal(r.visible().objectId,null);assert.equal(r.visible().kind,'composition');assert.equal(r.mounts.at(-1).initialState.camera.x,30);
 await assert.rejects(r.session.navigate({...capability,viewId:'unknown'}),/unavailable/);
 model.objects=model.objects.filter(o=>o.id!=='providers');await assert.rejects(r.session.navigate(capability),/unavailable/);
});

async function capabilitySession(focusView) {
 const r=rig({focusView});await r.session.load('capability-batch',{route:{kind:'composition',objectId:null}});
 const model=r.session.getBundle().model;model.objects.find(o=>o.id==='execution').parent_id=null;
 for(const id of ['worker','providers'])model.objects.push({id,kind:'module',parent_id:null,child_ids:[],mechanism_ids:[],identity:{local_only:false}});
 return r;
}
test('capability focus failure rejects publication while preserving a freshly authorized graph',async()=>{
 let fail=true;
 const r=await capabilitySession(async()=>{if(fail)throw Error('map unavailable');});
 await r.session.select({objectId:'execution'});assert.equal(r.focuses.at(-1).id,'execution');
 await r.session.select({viewId:'model-execution'});assert.equal(r.session.snapshot().status,'ready');assert.equal(r.session.snapshot().focusError,'map unavailable');assert(r.visible());
 const capability={kind:'capability',objectId:null,mechanismId:null,viewId:'model-execution'};
 await r.session.navigate(capability);assert.deepEqual(r.visible(),capability);
 fail=false;await r.session.select({viewId:'model-execution'});assert.equal(r.session.snapshot().focusError,null);
});
test('core revocation during failed capability focus still closes the graph',async()=>{
 let r;r=await capabilitySession(async()=>{r.controls.deny=true;throw Error('focus denied');});
 await assert.rejects(r.session.select({viewId:'model-execution'}),/denied/);assert.equal(r.visible(),null);assert.equal(r.session.snapshot().status,'error');
});
test('a late rejected capability focus cannot annotate a newer selected module',async()=>{
 let reject;const r=await capabilitySession(()=>new Promise((_resolve,fail)=>{reject=fail;}));
 const pending=r.session.select({viewId:'model-execution'});await new Promise(resolve=>setImmediate(resolve));
 await r.session.select({objectId:'execution'});reject(Error('late failure'));await pending;
 assert.equal(r.session.snapshot().focusError,null);assert.equal(r.session.snapshot().selectionId,'execution');assert.equal(r.session.snapshot().status,'ready');
});
