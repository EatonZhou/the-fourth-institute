'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../..');
function fixture(t) {
  const dom = new JSDOM('', {runScripts: 'outside-only', pretendToBeVisual: true});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.PortalI18n = {t: s => s};
  w.PortalEvents = {watchResident: () => () => {}};
  w.PortalIdentity = {username: () => 'fixture', load: async () => {}};
  const load = (name, transform = s => s) => w.eval(transform(fs.readFileSync(path.join(root, `management_portal/static/${name}.js`), 'utf8')));
  load('resident_protocol', s => s.replace('  window.PortalResidentProtocol =', '  window.TestClasses = { ResidentStore, ResidentStateProjection };\n  window.PortalResidentProtocol ='));
  return {w, load};
}
test('single state delta is isolated, unavailable deletes, notification avoids rows, default stays complete', t => {
  const {w} = fixture(t), C = w.TestClasses.ResidentStateProjection;
  const ids = Array.from({length: 100}, (_, i) => `r${i}`);
  let copies = 0;
  const sub = new C(ids, {delivery: 'delta'}), updates = [];
  sub.listener = u => updates.push(u);
  ids.forEach(id => { sub.states[id] = {toJSON() { copies++; return {work_state: 'idle'}; }}; });
  sub.onStateUpdate({resident_id: 'r0', type: 'event', state: {work_state: 'running'}});
  assert.equal(copies, 0, 'unchanged 99 rows must not be serialized');
  assert.deepEqual(Object.keys(updates[0].states), ['r0']);
  updates[0].states.r0.work_state = 'corrupt';
  assert.equal(sub.states.r0.work_state, 'running');
  sub.onStateUpdate({resident_id: 'r0', type: 'unavailable'});
  assert.deepEqual(Object.keys(updates[1].states), []);
  assert.equal(updates[1].replace, false);
  sub.delivery = 'notification'; sub.emit({type: 'snapshot'});
  assert.equal('states' in updates[2], false); assert.equal(copies, 0);
  sub.delivery = 'snapshot'; sub.emit({type: 'snapshot'});
  assert.equal(Object.keys(updates[3].states).length, 99); assert.equal(copies, 99);
  console.log('WORK: 100-row stream update serialized unchanged rows 99 -> 0');
});
test('history merge retains old private rows, detaches new rows, joins duplicates and invalidates on events', t => {
  const {w} = fixture(t), store = new w.TestClasses.ResidentStore('r');
  const current = {message_id: 'head', body: {text: 'head'}, family_reply: {request_message_ids: ['a']}};
  const snapshot = {messages: [current], input_states: [], next_page_token: 'opaque'};
  let priorCopies = 0;
  current.toJSON = function () { priorCopies++; return {message_id: this.message_id, body: this.body, family_reply: this.family_reply}; };
  for (let i = 0; i < 20; i++) {
    const row = {message_id: `old${i}`, body: {text: 'before'}};
    store.mergeHistoryPage(snapshot, {resident_id: 'r', messages: [row], input_states: [], next_page_token: `opaque${i}`});
    row.body.text = 'outside mutation';
    assert.equal(snapshot.messages[0].body.text, 'before');
  }
  assert.equal(priorCopies, 0);
  assert.equal(snapshot.messages.at(-1), current);
  store.mergeHistoryPage(snapshot, {resident_id: 'r', messages: [{message_id: 'head', family_reply: {request_message_ids: ['b']}}]});
  assert.deepEqual(Array.from(snapshot.messages.at(-1).family_reply.request_message_ids), ['b', 'a']);
  store.snapshot = snapshot; store.cursor = 1;
  store.applyEvent({cursor: 2, event_type: 'conversation.message.appended', payload: {message: {message_id: 'live', body: {text: 'fresh'}}}}, {emit: false});
  store.mergeHistoryPage(snapshot, {resident_id: 'r', messages: [{message_id: 'live', body: {text: 'stale'}}]});
  assert.equal(snapshot.messages.filter(m => m.message_id === 'live').length, 1);
  assert.equal(snapshot.messages.at(-1).body.text, 'fresh');
  assert.throws(() => store.mergeHistoryPage(snapshot, {resident_id: 'wrong'}), /target mismatch/);
  const restored = {messages: [{message_id: 'head'}], input_states: []};
  const head = restored.messages;
  for (let i = 0; i < 20; i++) {
    store.mergeHistoryPage(restored, {resident_id: 'r', messages: [{message_id: `p${i}`}], next_page_token: `t${i}`}, new Set(), {defer: true});
    assert.equal(restored.messages, head, 'restore must not assemble the growing prefix per page');
  }
  store.finalizeHistoryMerge(restored);
  assert.equal(restored.messages.length, 21);
  assert.equal(restored.messages[0].message_id, 'p19');
  console.log('WORK: 20 pages recopy initial private row 20 -> 0; duplicate association/live precedence preserved');
  console.log('WORK: multi-page restore growing message array assembly 20 -> 1');
});
test('Mailbox deduplicates before projection and reads each Resident snapshot once', t => {
  const {w, load} = fixture(t);
  let reads = 0;
  w.PortalCompatibility = {identifier: s => s};
  w.PortalResidentProtocol = {stateSnapshot() { reads++; return {ready: true, viewer_state: {read_revision: 5, unread: true}, state: {status: 'idle'}}; }};
  load('portal_conversation_store', s => s.replace('  function publicSnapshot()', '  window.TestInbox = {state, publicSnapshot}; return;\n  function publicSnapshot()'));
  const {state, publicSnapshot} = w.TestInbox;
  const row = {key: 'resident:r', resident_id: 'r', kind: 'resident', title: 'loaded', updated_at: '2026-11-12T00:00:00Z'};
  state.entries = [row]; state.summaryEntries.set(row.key, {...row, title: 'summary'});
  const snap = publicSnapshot();
  assert.equal(reads, 1); assert.equal(snap.entries[0].title, 'loaded'); assert.equal(snap.unreadCount, 1);
  assert.equal(snap.entries[0].read_revision, 5);
  console.log('WORK: overlapping Mailbox row stateSnapshot calls 4 -> 1');
});
test('graph delta computes only affected node presentation and clears unavailable runtime', t => {
  const {w, load} = fixture(t);
  load('hatg_resident_runtime');
  const api = w.PortalHatgResidentRuntime;
  const nodes = Array.from({length: 100}, (_, i) => ({id: `r${i}`, status: 'idle'}));
  const state = {lineage: {lineage_id: 'l', nodes}, residentRuntimeById: new Map(), runtimeStatus: new Map()};
  let callback, work = 0, patched;
  const deps = Object.fromEntries(api.dependencies.map(key => [key, () => {}]));
  Object.assign(deps, {state, currentPanelActive: () => true, nodeResidentId: n => n.id,
    nodeWorkPresentation: n => {work++; return {phase: state.residentRuntimeById.get(n.id)?.resident_state?.work_phase || ''};},
    nodeIntakeState: () => 'open', nodeExecutionGate: () => ({text: ''}),
    effectiveNodeStatus: n => state.runtimeStatus.get(n.id) || n.status,
    setNodeRuntimeStatus: (id, value) => state.runtimeStatus.set(id, value),
    currentHatgComponentOptions: () => ({}), renderGraphRuntimePresentation: ids => {patched = ids;}});
  w.PortalResidentProtocol = {...w.PortalResidentProtocol, acquireStates: (_, cb, options) => {callback = cb; assert.equal(options.delivery, 'delta'); return {destroy() {}};}};
  api.createResidentRuntime(deps).bindResidentStateCollection();
  callback({type: 'event', replace: false, changedResidentIds: ['r4'], states: {r4: {work_state: 'running'}}});
  assert.equal(work, 2); assert.deepEqual(Array.from(patched), ['r4']);
  callback({type: 'unavailable', replace: false, changedResidentIds: ['r4'], states: {}});
  assert.equal(state.runtimeStatus.has('r4'), false);
  console.log('WORK: 100-node graph work presentation evaluations 200 -> 2 per single-node delta');
});

test('canvas delta patches visible chrome and hidden overview heat without full node projection', t => {
  const {w, load} = fixture(t);
  load('lineage_graph_render');
  const api = w.PortalHatgGraphRender;
  const node = {id: 'r1'}, hidden = {id: 'r2'};
  const el = w.document.createElement('div'), mini = w.document.createElement('div');
  const model = {nodes: [node], overviewNodes: [node, hidden], edges: [], overviewEdges: []};
  const state = {model, nodeMap: new Map([['r1', node]])};
  let contents = 0, edges = 0;
  const deps = Object.fromEntries(api.dependencies.map(key => [key, () => {}]));
  Object.assign(deps, {state, nodeElement: id => id === 'r1' ? el : null,
    minimapNodeElement: id => id === 'r2' ? mini : null,
    nodeContent: () => {contents++; return 'running';}, nodeStatus: () => 'running',
    nodeContextHeat: () => 'hot', isWorkspaceNode: () => false, isActiveStatus: () => true,
    validEdges: () => [], renderEdges: () => {edges++;}});
  const render = api.createRender(deps);
  assert.equal(render.patchRuntimePresentation({runtimeNodeIds: ['r1', 'r1', 'r2']}), true);
  assert.equal(contents, 1); assert.equal(el.dataset.status, 'running');
  assert.equal(el.innerHTML, 'running'); assert.equal(mini.dataset.contextHeat, 'hot');
  assert.equal(edges, 1); assert.equal(state.model, model);
});
