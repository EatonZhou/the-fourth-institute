'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { articleAutosaveFixture, tick } = require('./article_autosave_fixture.cjs');
const body = r => JSON.parse(r.options.body);
async function startAuto(f, text = 'new') { f.edit(text); f.documents.scheduleAutoSave(); await f.flush(); }
test('auto-save sends the version directly, without a preflight read or compile', async () => {
  const f = articleAutosaveFixture();
  f.edit('a'); f.documents.scheduleAutoSave(); f.edit('new'); f.documents.scheduleAutoSave();
  assert.equal(f.timers.size, 1); await f.flush();
  assert.deepEqual(body(f.requests[0]), {path:'main.tex',content:'new',expected_revision:'r1'});
  assert.equal(f.requests[0].options.method,'POST');
  f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(f.doc.savedSource,'new'); assert.equal(f.documents.hasDirtyDocuments(),false);
  assert.equal(f.builds.length,0); assert.equal(await f.documents.saveOpenFile(),true);
  assert.equal(f.builds.length,1);
});
test('one document preserves input during save and chains the next revision', async () => {
  const f = articleAutosaveFixture(); await startAuto(f); f.edit('newer');
  f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(f.doc.source,'newer'); assert.equal(f.doc.savedSource,'new');
  assert.equal(f.documents.hasDirtyDocuments(),true); await f.flush();
  assert.deepEqual(body(f.requests[1]),{path:'main.tex',content:'newer',expected_revision:'r2'});
  f.requests[1].resolve({revision:'r3'}); await tick();
  assert.equal(f.doc.savedSource,'newer');
});
test('manual save and compile wait for the earlier write and latest input', async () => {
  const f=articleAutosaveFixture(); await startAuto(f); f.edit('newer');
  const manual=f.documents.saveOpenFile(); assert.equal(f.requests.length,1);
  f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(body(f.requests[1]).content,'newer'); assert.equal(f.builds.length,0);
  f.requests[1].resolve({revision:'r3'}); assert.equal(await manual,true); assert.equal(f.builds.length,1);
});
test('background documents save independently of the active editor',async()=>{
  const f=articleAutosaveFixture(); f.edit('new'); f.documents.scheduleAutoSave();
  const other={path:'other.tex',editable:true,textDocument:true,source:'other',savedSource:'other'};
  f.state.documents.set(other.path,other); f.state.openTabs.push(other.path); f.state.openFile=other.path; f.$('articleSourceEditor').value='other';
  await f.flush(); assert.equal(body(f.requests[0]).path,'main.tex');
  f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(f.doc.savedSource,'new'); assert.equal(other.savedSource,'other');
});
test('conflicting writes pause auto-save and explicit comparison uses the observed revision',async()=>{
  const f=articleAutosaveFixture(); await startAuto(f,'draft');
  const conflict=Object.assign(new Error('文件已在其他地方更新'),{status:409});
  f.requests[0].reject(conflict); await tick();
  assert.equal(f.doc.source,'draft'); assert.equal(f.doc.revision,'r1'); assert.equal(f.doc.savedSource,'old');
  f.documents.scheduleAutoSave(); assert.equal(f.timers.size,0);
  const retry=f.documents.saveOpenFile();
  assert.equal(f.requests[1].url,'/api/articles/A/file?path=main.tex');
  f.requests[1].resolve({content:'remote',revision:'r2'}); await tick();
  assert.deepEqual({...f.comparisons[0]},{path:'main.tex',local:'draft',remote:'remote'});
  assert.equal(body(f.requests[2]).expected_revision,'r2');
  assert.equal(f.doc.revision,'r1','observed revision is not acknowledged until the write succeeds');
  f.requests[2].resolve({revision:'r3'}); assert.equal(await retry,true);
  assert.equal(f.doc.savedSource,'draft'); assert.equal(f.doc.revision,'r3');
  assert.equal(f.builds.length,1);
});
test('offline and unconfirmed responses retain edits',async()=>{
  const f=articleAutosaveFixture(); await startAuto(f); f.edit('newer'); f.requests[0].reject(new Error('offline')); await tick();
  assert.equal(f.doc.source,'newer'); assert.equal(f.doc.savedSource,'old'); assert.equal(f.timers.size,0);
  const retry=f.errors[0][2].retry(); f.requests[1].resolve({});
  assert.equal(await retry,false); assert.equal(f.doc.savedSource,'old');
});
test('compile preparation saves all dirty drafts without requesting another build',async()=>{
  const f=articleAutosaveFixture(); await startAuto(f); f.edit('newer'); const save=f.documents.saveAllDirtyDocuments();
  f.requests[0].resolve({revision:'r2'}); await tick(); f.requests[1].resolve({revision:'r3'});
  assert.equal(await save,true); assert.equal(f.doc.savedSource,'newer'); assert.equal(f.builds.length,0);
});
test('Article switch and undo while saving cannot acknowledge a different draft',async()=>{
  const f=articleAutosaveFixture(); await startAuto(f); f.edit('old');
  f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(f.doc.source,'old'); assert.equal(f.doc.savedSource,'new'); assert.equal(f.documents.hasDirtyDocuments(),true);
  f.state.selectedId='B'; await f.flush(); assert.equal(f.requests.length,1);
  f.doc.editable=false; f.documents.scheduleAutoSave(); await f.flush(); assert.equal(f.requests.length,1);
});

test('cancel conflict comparison preserves local draft and the original base',async()=>{
  const f=articleAutosaveFixture(); f.edit('draft'); f.doc.externalChangeVersion='changed'; f.approve(false);
  const save=f.documents.saveOpenFile(); f.requests[0].resolve({content:'remote',revision:'r2'});
  assert.equal(await save,false); assert.equal(f.requests.length,1);
  assert.equal(f.doc.revision,'r1'); assert.equal(f.doc.source,'draft'); assert.equal(f.doc.savedSource,'old');
  assert.equal(f.builds.length,0);
});
test('a second concurrent write remains a conflict; no automatic overwrite or revision adoption',async()=>{
  const f=articleAutosaveFixture(); f.edit('draft'); f.doc.externalChangeVersion='changed';
  const save=f.documents.saveOpenFile(); f.requests[0].resolve({content:'remote',revision:'r2'}); await tick();
  f.requests[1].reject(Object.assign(new Error('raced'),{status:409,current_revision:'r3'}));
  assert.equal(await save,false); assert.equal(f.requests.length,2); assert.equal(f.doc.revision,'r1');
  assert.equal(f.doc.source,'draft'); assert.equal(f.doc.savedSource,'old'); assert.equal(f.builds.length,0);
});
test('compile barrier drains typing arriving during its own write',async()=>{
  const f=articleAutosaveFixture(); f.edit('draft'); const save=f.documents.saveAllDirtyDocuments();
  f.edit('newer'); f.requests[0].resolve({revision:'r2'}); await tick();
  assert.equal(body(f.requests[1]).content,'newer'); f.requests[1].resolve({revision:'r3'});
  assert.equal(await save,true); assert.equal(f.doc.savedSource,'newer');
});
