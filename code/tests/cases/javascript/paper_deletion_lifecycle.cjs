'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const portal = path.resolve(__dirname, '../../../management_portal');
// The Paper page's own scripts, in the order paper.html loads them.
const sources = [...fs.readFileSync(path.join(portal, 'paper.html'), 'utf8').matchAll(/<script src="\/static\/(paper[a-z_]*\.js)"/g)]
  .map(match => fs.readFileSync(path.join(portal, 'static', match[1]), 'utf8'));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function fixture({confirm = true} = {}) {
  const deletion = deferred(), calls = [], errors = [];
  const state = {selectedId: 'paper_owned', detail: {paper: {id: 'paper_owned', title: 'Owned paper'}},
    deletingPaperId: '', chatHistoryLoadedFor: 'paper:paper_owned'};
  let subscribed = true, busy = false;
  const paperChatPanel = {
    surface: () => ({paper_id: state.selectedId}),
    closeSurface: async () => {calls.push('close'); subscribed = false;},
    setBusy: value => {busy = value;},
    openSurface: async target => {calls.push(`open:${target.paper_id}`); subscribed = true;},
  };
  const context = vm.createContext({state, window: {PortalUI: {confirmAction: async () => confirm}},
    activeChatPaperId: () => state.selectedId,
    activeChatWorkspaceId: () => `paper:${state.selectedId}`,
    chatSessionKey: id => `paper:${id}`,
    paperChatPanel, currentPaperChatPanel: () => paperChatPanel,
    paperApi: {request: async (url, options) => {
      calls.push(`delete:${JSON.parse(options.body).paper_id}`);
      assert.equal(subscribed, false, 'delete notifications must not find a live Chat reader');
      assert.equal(busy, true, 'sending is unavailable during the cascade');
      return deletion.promise;
    }},
    setMode: mode => {calls.push(`mode:${mode}`);},
    loadLibrary: async options => {calls.push(`library:${options.keepDetail}`);},
    renderWorkspace: () => calls.push('render'),
    alertError: error => errors.push(error),
  });
  for (const name of ['loadChatHistory', 'deleteCurrentPaper']) {
    const pattern = new RegExp(`^( +)async function ${name}\\(`, 'm');
    const source = sources.find(text => pattern.test(text)) || '';
    const match = pattern.exec(source);
    assert.ok(match);
    const end = source.indexOf(`\n${match[1]}}\n`, match.index);
    vm.runInContext(source.slice(match.index, end + match[1].length + 3), context);
  }
  return {context, state, calls, deletion, errors, subscribed: () => subscribed, busy: () => busy};
}

test('deletion releases the Chat before the cascade and refresh cannot rebind it', async () => {
  const f = fixture(), operation = f.context.deleteCurrentPaper();
  await tick();
  assert.deepEqual(f.calls, ['close', 'delete:paper_owned']);
  assert.equal(await f.context.loadChatHistory(), null);
  await f.context.deleteCurrentPaper();
  assert.deepEqual(f.calls, ['close', 'delete:paper_owned'], 'a second click cannot duplicate deletion');
  f.deletion.resolve({}); await operation;
  assert.equal(f.state.selectedId, '');
  assert.equal(f.state.deletingPaperId, '');
  assert.equal(f.subscribed(), false);
  assert.equal(f.busy(), false);
  assert.deepEqual(f.calls.slice(2), ['mode:library', 'library:false', 'render']);
});

test('a rejected deletion restores only the current Chat and preserves the deletion error', async () => {
  const f = fixture(), operation = f.context.deleteCurrentPaper();
  await tick();
  const error = new Error('delete rejected');
  f.deletion.reject(error);
  await assert.rejects(operation, value => value === error);
  assert.equal(f.state.selectedId, 'paper_owned');
  assert.equal(f.state.deletingPaperId, '');
  assert.equal(f.busy(), false);
  assert.deepEqual(f.calls, ['close', 'delete:paper_owned', 'open:paper_owned']);
});

test('cancelling confirmation leaves the original reader and selected paper intact', async () => {
  const f = fixture({confirm: false});
  await f.context.deleteCurrentPaper();
  assert.deepEqual(f.calls, []);
  assert.equal(f.subscribed(), true);
  assert.equal(f.state.selectedId, 'paper_owned');
  assert.equal(f.state.deletingPaperId, '');
});

for (const success of [true, false]) test(`navigation during deletion is preserved on ${success ? 'success' : 'failure'}`, async () => {
  const f = fixture(), operation = f.context.deleteCurrentPaper();
  await tick();
  f.state.selectedId = 'paper_other'; f.state.detail = {paper: {id: 'paper_other'}};
  await f.context.loadChatHistory();
  if (success) {f.deletion.resolve({}); await operation;}
  else {f.deletion.reject(new Error('delete rejected')); await assert.rejects(operation, /delete rejected/);}
  assert.equal(f.state.selectedId, 'paper_other');
  assert.equal(f.state.detail.paper.id, 'paper_other');
  assert.equal(f.calls.filter(value => value === 'open:paper_owned').length, 0);
  assert.equal(f.calls.filter(value => value === 'open:paper_other').length, 1);
  assert.equal(f.calls.includes('mode:library'), false);
  assert.equal(f.busy(), false);
});

test('a library refresh failure after successful deletion does not restore a removed Chat', async () => {
  const f = fixture();
  f.context.loadLibrary = async () => {throw new Error('library unavailable');};
  const operation = f.context.deleteCurrentPaper(); await tick();
  f.deletion.resolve({});
  await assert.rejects(operation, /library unavailable/);
  assert.equal(f.subscribed(), false);
  assert.equal(f.calls.some(value => value.startsWith('open:')), false);
  assert.equal(f.busy(), false);
});
