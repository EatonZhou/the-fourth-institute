const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function browserWindow(language='zh-CN') {
  const window = {__PORTAL_LANGUAGE__:language, PortalCompatibility:{identifier:s=>s}, addEventListener:()=>{}};
  const document = {readyState:'loading', documentElement:{lang:language}, addEventListener:()=>{}};
  const context = vm.createContext({window, document, navigator:{languages:[language]}, localStorage:{getItem:()=>null}, console});
  for (const file of ['portal_text.js','portal_translations.js','portal_i18n.js','repo_workspace_dialogs.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../../management_portal/static', file), 'utf8'), context);
  }
  return window;
}
const window = browserWindow();

function setup(failAt, browser=window) {
  const calls = [], queries = [], toasts = [], closed = [];
  const state = {selectedId:'repo_123456789abc', editorNew:false, editorOriginalPath:'原文.txt', editorExpectedHash:'old-hash'};
  const elements = {editorPath:{value:'移动后.txt'}, editorSource:{value:'retained draft'}, editorSave:{}, editorStatus:{textContent:''}};
  const dependencies = Object.fromEntries(browser.PortalRepoWorkspaceDialogs.dependencies.map(key => [key, () => {}]));
  Object.assign(dependencies, {
    state, elements, dialogs:{editor:{close: value => closed.push(value)}}, dirname: () => '',
    setBusy: () => {}, writeQuery: value => queries.push(value), showToast: (...args) => toasts.push(args),
    postJson: async (url, body) => {
      calls.push({url, body});
      if (url.endsWith('/' + failAt)) throw Error(failAt === 'move' ? 'version conflict' : 'write denied');
      return {};
    },
  });
  return {owner:browser.PortalRepoWorkspaceDialogs.createDialogs(dependencies), calls, state, elements, queries, toasts, closed};
}

function setupOpen(read) {
  const state = {selectedId:'repo_123456789abc', navigationSequence:1, tree:{path:''}};
  const fields = ['editorEyebrow','editorTitle','editorPath','editorSource','editorStatus','editorPreview'];
  const elements = Object.fromEntries(fields.map(key => [key, {value:'', textContent:''}]));
  elements.editorTabs = [];
  const opened = [], toasts = [], reads = [];
  const dependencies = Object.fromEntries(window.PortalRepoWorkspaceDialogs.dependencies.map(key => [key, () => {}]));
  Object.assign(dependencies, {state, elements, queryState:()=>({}), workspaceCapability:()=>true,
    dialogs:{editor:{open:value=>opened.push(value),close:()=>{}}},
    getJson:async (url, options)=>{reads.push({url, options}); return read();},
    showToast:(...args)=>toasts.push(args),
  });
  return {owner:window.PortalRepoWorkspaceDialogs.createDialogs(dependencies),state,elements,opened,toasts,reads};
}
(async () => {
  const old = {path:'协作.txt', name:'协作.txt', content:'old baseline', hash:'old-hash', editable:true};
  let current = {...old};
  const reopened = setupOpen(()=>({...current}));
  await reopened.owner.openEditor(old);
  reopened.elements.editorSource.value = 'B unsaved draft';
  await reopened.owner.closeEditor(); // User has confirmed discarding B's draft.
  current = {...old, content:'saved by A', hash:'new-hash'};
  await reopened.owner.openEditor(old); // Preview metadata is still the old read.
  assert.equal(reopened.elements.editorSource.value, 'saved by A');
  assert.equal(reopened.state.editorExpectedHash, 'new-hash');
  assert.equal(reopened.state.file.content, 'saved by A');
  assert(reopened.reads[1].url.endsWith('path='+encodeURIComponent('协作.txt')));

  const denied = setupOpen(()=>{throw Error('read denied');});
  await denied.owner.openEditor(old);
  assert.equal(denied.opened.length, 0, 'a failed refresh cannot open stale writable content');
  assert.equal(denied.toasts[0][0], 'read denied');
  const binary = setupOpen(()=>({...old, editable:false}));
  await binary.owner.openEditor(old);
  assert.equal(binary.opened.length, 0, 'fresh server editability is authoritative');
  let finish;
  const navigation = setupOpen(()=>new Promise(resolve=>{finish=resolve;}));
  const opening = navigation.owner.openEditor(old);
  navigation.state.navigationSequence++;
  finish(current);
  await opening;
  assert.equal(navigation.opened.length, 0, 'late file read cannot reopen an old navigation');

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
  assert(!partial.elements.editorStatus.textContent.includes('{p0}'));
  assert(!partial.elements.editorStatus.textContent.includes('{path}'));
  await partial.owner.saveEditor();
  assert.equal(partial.calls.filter(x => x.url.endsWith('/move')).length, 1);
  assert.equal(partial.calls.at(-1).body.path, '移动后.txt');

  const english = setup('file', browserWindow('en'));
  english.elements.editorPath.value = '目录/{p1}-<name>.txt';
  await english.owner.saveEditor();
  assert(english.elements.editorStatus.textContent.startsWith('The file moved to 目录/{p1}-<name>.txt,'));
  assert(english.elements.editorStatus.textContent.includes('Your draft is retained'));
  assert.equal(english.elements.editorSource.value, 'retained draft');

  const good = setup('none');
  await good.owner.saveEditor();
  assert.equal(good.closed.length, 1);
  assert.deepEqual(good.calls.map(x => x.url.split('/').at(-1)), ['move', 'file']);
  assert.equal(good.calls[1].body.expected_hash, 'old-hash');
  console.log('Repo editor: current reopen baseline, read refusal/navigation, stale rename, partial-save recovery and successful save verified');
})().catch(error => {console.error(error);process.exitCode=1;});
