'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { articleAutosaveFixture, tick } = require('./article_autosave_fixture.cjs');

async function startAuto(f, text = 'new') {
  f.edit(text); f.documents.scheduleAutoSave(); await f.flush();
  assert.equal(f.requests.at(-1).options, undefined);
  f.requests.at(-1).resolve({ content: f.doc.savedSource }); await tick();
}
test('idle saves debounce, persist without compiling, and manual save still compiles clean source', async () => {
  const f = articleAutosaveFixture();
  f.edit('a'); f.documents.scheduleAutoSave(); f.edit('new'); f.documents.scheduleAutoSave();
  assert.equal(f.timers.size, 1); assert.equal([...f.timers.values()][0].delay, 500);
  await f.flush(); f.requests[0].resolve({ content: 'old' }); await tick();
  assert.deepEqual(JSON.parse(f.requests[1].options.body), { path: 'main.tex', content: 'new' });
  assert.equal(f.$('articleEditorState').textContent, 'Saving…');
  f.requests[1].resolve({ path: 'main.tex', revision: 'r2' }); await tick();
  assert.equal(f.session.snapshot().dirty, false); assert.equal(f.doc.savedSource, 'new');
  assert.equal(f.$('articleEditorState').textContent, '已自动保存');
  assert.equal(f.$('articleSaveFile').disabled, false);
  assert.equal(f.builds.length, 0); assert.equal(f.timers.size, 0);
  assert.equal(await f.documents.saveOpenFile(), true); assert.equal(f.builds.length, 1);
});
test('typing during save preserves the latest draft and queues it for the next auto-save', async () => {
  const f = articleAutosaveFixture(); await startAuto(f);
  f.edit('newer'); f.documents.scheduleAutoSave();
  f.requests[1].resolve({ revision: 'r2' }); await tick();
  assert.equal(f.state.source, 'newer'); assert.equal(f.doc.source, 'newer');
  assert.equal(f.state.savedSource, 'new'); assert.equal(f.session.snapshot().draft, 'newer');
  assert.equal(f.documents.hasDirtyDocuments(), true);
  await f.flush(); f.requests[2].resolve({ content: 'new' }); await tick();
  assert.equal(JSON.parse(f.requests[3].options.body).content, 'newer');
  f.requests[3].resolve({ revision: 'r3' }); await tick();
  assert.equal(f.doc.savedSource, 'newer'); assert.equal(f.builds.length, 0);
});
test('manual save waits for auto-save before saving the newer draft and compiling', async () => {
  const f = articleAutosaveFixture(); await startAuto(f); f.edit('newer');
  const manual = f.documents.saveOpenFile(); assert.equal(f.requests.length, 2);
  f.requests[1].resolve({}); await tick();
  assert.equal(JSON.parse(f.requests[2].options.body).content, 'newer');
  assert.equal(f.builds.length, 0); f.requests[2].resolve({});
  assert.equal(await manual, true); assert.equal(f.builds.length, 1); assert.equal(f.timers.size, 0);
});
test('background tabs save their own drafts without altering the active tab', async () => {
  const f = articleAutosaveFixture(); f.edit('new'); f.documents.scheduleAutoSave();
  const other = { path: 'other.tex', editable: true, textDocument: true, source: 'other', savedSource: 'other' };
  f.state.documents.set('other.tex', other); f.state.openTabs.push('other.tex');
  f.state.openFile = 'other.tex'; f.state.source = f.state.savedSource = 'other'; f.$('articleSourceEditor').value = 'other';
  await f.flush(); f.requests[0].resolve({ content: 'old' }); await tick();
  assert.equal(JSON.parse(f.requests[1].options.body).path, 'main.tex'); f.requests[1].resolve({}); await tick();
  assert.equal(f.doc.savedSource, 'new'); assert.equal(f.state.savedSource, 'other'); assert.equal(f.builds.length, 0);
});
test('save failure keeps edits, exposes failure and offers a retry that does not compile', async () => {
  const f = articleAutosaveFixture(); await startAuto(f); f.edit('newer');
  f.requests[1].reject(new Error('offline')); await tick();
  assert.equal(f.session.snapshot().draft, 'newer'); assert.equal(f.session.snapshot().dirty, true);
  assert.match(f.$('articleEditorState').textContent, /保存失败/); assert.equal(f.timers.size, 0);
  const retry = f.errors[0][2].retry(); f.requests[2].resolve({ content: 'old' }); await tick();
  f.requests[3].resolve({}); assert.equal(await retry, true); assert.equal(f.builds.length, 0);
});
test('external edits pause background saving and leave the draft for explicit resolution', async () => {
  const f = articleAutosaveFixture(); f.edit('draft'); f.documents.scheduleAutoSave(); await f.flush();
  f.requests[0].resolve({ content: 'external', version: 'v2', size: 8 }); await tick();
  assert.equal(f.requests.length, 1); assert.equal(f.doc.source, 'draft');
  assert.match(f.errors[0][0], /其他地方更新/);
  f.documents.scheduleAutoSave(); assert.equal(f.timers.size, 0);
  const manual = f.documents.saveOpenFile(); f.requests[1].resolve({}); await manual;
  assert.equal(f.doc.externalChangeVersion, ''); assert.equal(f.builds.length, 1);
});
test('retrying an explicit save compiles only after the retry succeeds', async () => {
  const f = articleAutosaveFixture(); f.edit('new');
  const manual = f.documents.saveOpenFile(); f.requests[0].reject(new Error('offline'));
  assert.equal(await manual, false); assert.equal(f.builds.length, 0);
  const retry = f.errors[0][2].retry(); f.requests[1].resolve({});
  assert.equal(await retry, true); assert.equal(f.builds.length, 1);
});
test('compile preparation waits for the in-flight save and persists newer edits without scheduling a build', async () => {
  const f = articleAutosaveFixture(); await startAuto(f); f.edit('newer');
  const preparation = f.documents.saveAllDirtyDocuments();
  assert.equal(f.requests.length, 2); f.requests[1].resolve({}); await tick();
  assert.equal(JSON.parse(f.requests[2].options.body).content, 'newer');
  f.requests[2].resolve({}); assert.equal(await preparation, true);
  assert.equal(f.doc.savedSource, 'newer'); assert.equal(f.builds.length, 0);
});
test('article switch invalidates timers and preflight; stale save cannot alter the new article', async () => {
  const f = articleAutosaveFixture(); f.edit('new'); f.documents.scheduleAutoSave(); f.state.selectedId = 'B';
  await f.flush(); assert.equal(f.requests.length, 0);
  f.state.selectedId = 'A'; f.documents.scheduleAutoSave(); await f.flush(); f.state.selectedId = 'B';
  f.requests[0].resolve({ content: 'old' }); await tick(); assert.equal(f.requests.length, 1);
  f.state.selectedId = 'A'; await startAuto(f); f.state.selectedId = 'B'; f.state.source = 'B draft'; f.state.savedSource = 'B saved';
  f.requests.at(-1).resolve({}); await tick();
  assert.equal(f.state.source, 'B draft'); assert.equal(f.state.savedSource, 'B saved'); assert.equal(f.builds.length, 0);
});
test('undo while preflight is pending skips the write; read-only documents never auto-save', async () => {
  const f = articleAutosaveFixture(); f.edit('new'); f.documents.scheduleAutoSave(); await f.flush();
  f.edit('old'); f.requests[0].resolve({ content: 'old' }); await tick();
  assert.equal(f.requests.length, 1); assert.equal(f.errors.length, 0);
  f.doc.editable = false; f.edit('preview'); f.documents.scheduleAutoSave(); await f.flush(); assert.equal(f.requests.length, 1);
});
