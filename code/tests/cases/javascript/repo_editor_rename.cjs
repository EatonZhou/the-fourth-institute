const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const window = {PortalI18n: {t: (s, values={}) => s.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? '')}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/repo_workspace_dialogs.js'), 'utf8'), {window});

function setup(failAt) {
  const calls = [], queries = [], toasts = [], closed = [];
  const state = {selectedId:'repo_123456789abc', editorNew:false, editorOriginalPath:'原文.txt', editorExpectedHash:'old-hash'};
  const elements = {editorPath:{value:'移动后.txt'}, editorSource:{value:'retained draft'}, editorSave:{}, editorStatus:{textContent:''}};
  const dependencies = Object.fromEntries(window.PortalRepoWorkspaceDialogs.dependencies.map(key => [key, () => {}]));
  Object.assign(dependencies, {
    state, elements, dialogs:{editor:{close: value => closed.push(value)}}, dirname: () => '',
    setBusy: () => {}, writeQuery: value => queries.push(value), showToast: (...args) => toasts.push(args),
    postJson: async (url, body) => {
      calls.push({url, body});
      if (url.endsWith('/' + failAt)) throw Error(failAt === 'move' ? 'version conflict' : 'write denied');
      return {};
    },
  });
  return {owner:window.PortalRepoWorkspaceDialogs.createDialogs(dependencies), calls, state, elements, queries, toasts, closed};
}
(async () => {
  const stale = setup('move');
  await stale.owner.saveEditor();
  assert.equal(stale.calls.length, 1);
  assert.equal(stale.calls[0].body.expected_hash, 'old-hash');
  assert.equal(stale.state.editorOriginalPath, '原文.txt');
  assert.equal(stale.elements.editorSource.value, 'retained draft');
  assert.equal(stale.closed.length, 0);
  assert.equal(stale.elements.editorStatus.textContent, 'version conflict');

  const partial = setup('file');
  await partial.owner.saveEditor();
  assert.equal(partial.calls.length, 2);
  assert.equal(partial.state.editorOriginalPath, '移动后.txt');
  assert.equal(partial.queries[0].file, '移动后.txt');
  assert(partial.elements.editorStatus.textContent.includes('移动后.txt'));
  assert(partial.elements.editorStatus.textContent.includes('重新打开文件核对内容'));
  assert.equal(partial.elements.editorSource.value, 'retained draft');
  assert.equal(partial.closed.length, 0);
  await partial.owner.saveEditor();
  assert.equal(partial.calls.filter(x => x.url.endsWith('/move')).length, 1);
  assert.equal(partial.calls.at(-1).body.path, '移动后.txt');

  const good = setup('none');
  await good.owner.saveEditor();
  assert.equal(good.closed.length, 1);
  assert.deepEqual(good.calls.map(x => x.url.split('/').at(-1)), ['move', 'file']);
  assert.equal(good.calls[1].body.expected_hash, 'old-hash');
  console.log('Repo editor: stale rename, partial-save recovery, explicit retry and successful save verified');
})().catch(error => {console.error(error);process.exitCode=1;});
