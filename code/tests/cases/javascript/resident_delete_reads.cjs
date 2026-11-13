const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
function fixture() {
  const calls = [], listeners = new Map();
  const f = {calls, get: null, execute: null};
  const dom = new JSDOM('');
  const context = {
    URLSearchParams, queueMicrotask, setTimeout, clearTimeout, console,
    CustomEvent: dom.window.CustomEvent,
    dispatchEvent: event => { listeners.get(event.type)?.(event); return true; },
    PortalUIPrimitives: {randomUUID}, PortalI18n: {t: String},
    addEventListener: (name, listener) => listeners.set(name, listener),
    PortalAPI: {get: url => {
      calls.push({name: 'GET', url});
      return f.get ? f.get(url) : Promise.resolve({resident_id: new URL(url, 'http://test').searchParams.get('id')});
    }},
    PortalInstructionClient: {execute: (name, args) => {
      calls.push({name, args});
      if (f.execute) return f.execute(name, args);
      return Promise.resolve(name === 'GetLineage' ? {lineage: {lineage_id: 'lineage_test', revision: 1}} : {});
    }},
  };
  context.window = context;
  dom.window.close();
  vm.createContext(context);
  for (const file of ['portal_resident_service.js', 'portal_society_service.js', 'resident_projection.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context);
  }
  f.context = context;
  f.service = context.PortalResidentService;
  f.change = id => listeners.get('portal:change')({detail: {resident_changes: {[id]: ['identity']}}});
  return f;
}

test('delete gates new reads without waiting for an unbounded existing projection read', async () => {
  const f = fixture(), read = deferred(), write = deferred();
  f.get = url => url.includes('resident_one') ? read.promise : Promise.resolve({resident_id: 'resident_two'});
  f.execute = () => write.promise;
  const first = f.service.projection({residentId: 'resident_one'});
  const deletion = f.service.remove({resident_id: 'resident_one', lineage_id: 'lineage_test'});
  const otherClient = f.service.createClient({api: f.context.PortalAPI});
  const queued = otherClient.projection({residentId: 'resident_one'}).catch(error => error);
  const history = f.service.inputHistory('resident_one').catch(error => error);
  await otherClient.projection({residentId: 'resident_two'});
  assert.equal(f.calls.filter(c => c.name === 'DeleteResident').length, 1,
    'deletion can commit while an earlier network request is still outstanding');
  read.resolve({resident_id: 'resident_one'});
  await first; await flush();
  assert.equal(f.calls.filter(c => c.name === 'DeleteResident').length, 1);
  assert.equal(f.calls.filter(c => c.name === 'ListInputHistory').length, 0);
  write.resolve({resident_id: 'resident_one'});
  await deletion;
  for (const error of await Promise.all([queued, history])) {
    assert.equal(error.code, 'resident.deleted');
    assert.equal(error.status, undefined, 'local lifecycle result must not pretend to be an HTTP response');
  }
  await assert.rejects(f.service.projection({residentId: 'resident_one'}), {code: 'resident.deleted'});
  assert.equal(f.calls.filter(c => c.name === 'GET' && c.url.includes('resident_one')).length, 1);
});

test('a rejected deletion restores waiting reads and preserves the write error', async () => {
  const f = fixture(), write = deferred();
  f.execute = (name) => name === 'DeleteResident' ? write.promise : Promise.resolve({items: []});
  const failure = Object.assign(new Error('write forbidden'), {code: 'instruction.forbidden'});
  const deletion = f.service.remove({resident_id: 'resident_one', lineage_id: 'lineage_test'}).catch(error => error);
  const read = f.service.projection({residentId: 'resident_one'});
  const history = f.service.inputHistory('resident_one');
  await flush();
  assert.deepEqual(f.calls.map(c => c.name), ['DeleteResident']);
  write.reject(failure);
  assert.equal(await deletion, failure);
  await Promise.all([read, history]);
  assert.equal(f.calls.filter(c => c.name === 'GET').length, 1);
  assert.equal(f.calls.filter(c => c.name === 'ListInputHistory').length, 1);
});

test('graph deletion gates projection invalidation before the committed callback or refresh', async () => {
  const f = fixture(), write = deferred();
  const updates = [];
  const stop = f.context.PortalResidentProjection.subscribe('resident_one', value => updates.push(value));
  await flush();
  assert.equal(f.calls.filter(c => c.name === 'GET').length, 1);
  f.execute = (name) => name === 'DeleteResident' ? write.promise : Promise.reject(new Error('refresh failed'));
  let deleted = false;
  const graph = f.context.PortalSocietyService.createClient({onResidentsDeleted: () => { deleted = true; stop(); }});
  const deletion = graph.deleteNode({lineage_id: 'lineage_test', resident_id: 'resident_one'}).catch(error => error);
  f.change('resident_one');
  await flush();
  assert.equal(deleted, false);
  assert.equal(f.calls.filter(c => c.name === 'GET').length, 1, 'invalidation must not race the write');
  write.resolve({resident_id: 'resident_one'});
  const error = await deletion;
  assert.equal(error.committedMutation.instruction, 'DeleteResident');
  assert.equal(deleted, true);
  await assert.rejects(f.service.inputHistory('resident_one'), {code: 'resident.deleted'});
  assert.equal(f.calls.filter(c => c.name === 'GET').length, 1);
});

test('HATG deletion distinguishes committed, uncommitted and unknown write outcomes', async () => {
  for (const commitState of ['committed', 'not_committed', 'unknown', undefined]) {
    const f = fixture(), notices = [];
    f.context.PortalI18n.text = (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '');
    f.context.PortalUI = {confirm: async () => true};
    vm.runInContext(fs.readFileSync(path.join(root, 'hatg_resident_dialogs.js'), 'utf8'), f.context);
    const owner = f.context.PortalHatgResidentDialogs;
    const node = {id:'resident_one', title:'Temporary resident'};
    const state = {lineage:{lineage_id:'lineage_test', edges:[]}, residents:[node]};
    let writes = 0;
    const dependencies = Object.fromEntries(owner.dependencies.map(name => [name, () => {}]));
    Object.assign(dependencies, {state, currentNode:() => node, nodeResidentId:n => n.id,
      societyService:{deleteNode:async () => {
        writes++;
        throw Object.assign(new Error('owner result'), {body:{error:{commit_state:commitState}}});
      }}, toast:(message,tone) => notices.push({message,tone})});
    const result = await owner.createResidentDialogs(dependencies).removeSelectedNode();
    assert.equal(writes, 1, 'the dialog must not resubmit an uncertain deletion');
    assert.equal(result, commitState === 'committed');
    assert.equal(notices.length, 1);
    assert.equal(notices[0].tone, commitState === 'not_committed' ? 'error' : 'warning');
    if (commitState !== 'not_committed') assert.ok(!notices[0].message.includes('HATG 未改变'));
    if (commitState === 'unknown' || commitState === undefined) {
      assert.ok(notices[0].message.includes('待确认'));
      assert.ok(notices[0].message.includes('勿重复提交'));
    }
    assert.equal(state.residents.length, 1, 'unknown projections are not optimistically rewritten');
  }
});
