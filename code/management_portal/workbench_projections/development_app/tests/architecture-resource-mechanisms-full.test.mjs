import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {RESOURCE_MECHANISMS} from '../src/architecture/resource-mechanisms.entries.js';
import {createResourceSimulation} from '../src/architecture/resource-mechanisms.model.js';

const filename=process.env.RESOURCE_MODEL_FIXTURE;
if(!filename)throw Error('Set RESOURCE_MODEL_FIXTURE to the single authorized model');
const model=JSON.parse(fs.readFileSync(filename,'utf8'));
const mid={replace:'resources.mechanism.storage.workspace.atomic-files',create:'resources.review.mechanism.atomic-create-only',queue:'resources.review.mechanism.index-refresh-coalescing',publish:'resources.review.mechanism.index-snapshot-publication',protocol:'resources.mechanism.file-storage'};
const content=(id,entry)=>{
 const mechanism=model.mechanisms.find(m=>m.id===id), owner=entry||mechanism.owner_id;
 return {entryObjectId:owner,mechanism,mechanisms:model.mechanisms.filter(m=>model.objects.find(o=>o.id===owner).mechanism_ids.includes(m.id)),objects:model.objects,evidence:model.evidence,work_items:model.work_items,batchKey:'model36af-test',modelDescriptor:{address:'frozen-model',sha256:'36afcdc8abd5e05981f64cfa5e88fe10bb495cd7520351f9c63f9d8ea9ec24cd'}};
};
const last=(id,suffix,entry)=>{const s=createResourceSimulation(content(id,entry));s.selectCase(id+'.case.'+suffix);s.seek(s.getFrames().length-1);return s;};

test('production accepts the parent authorized payload and old-target cases',()=>{
 const s=createResourceSimulation(content(mid.replace));
 assert.equal(s.getScene().state.target_exists,true);
 assert.equal(s.getScene().state.target_version,'file:v1');
 assert.equal(last(mid.replace,'text_replace-file-fsync-failed').getScene().state.target_version,'file:v1');
 assert.equal(last(mid.replace,'directory-fsync-failed').getScene().state.target_version,'file:v2');
});
test('create-only leaves existing data but cleans up its own failed new target',()=>{
 assert.equal(last(mid.create,'exists').getScene().state.target_version,'file:v1');
 assert.equal(last(mid.create,'directory_fsync').getScene().state.target_version,'absent');
 const s=createResourceSimulation(content(mid.create));s.selectCase(mid.create+'.case.none');
 const i=s.getFrames().findIndex(f=>f.step?.id.endsWith('.open'));s.seek(i);
 assert.equal(s.getScene().state.read_visible_version,'empty');
});
test('arrivals during running remain in the next pending slot',()=>{
 const s=createResourceSimulation(content(mid.queue));s.selectCase(mid.queue+'.case.during-run');
 s.seek(s.getFrames().findIndex(f=>f.step?.id.endsWith('.next-arrival')));
 assert.deepEqual(s.getScene().state.dispatch_paths,['a.md']);
 assert.deepEqual(s.getScene().state.pending_paths,['b.md']);
});
test('flush failure preserves readable new memory and old durable snapshot without completion',()=>{
 const s=last(mid.publish,'persist_error').getScene().state;
 assert.equal(s.phase,'flush_failed');assert.equal(s.memory_version,'index:v2');assert.equal(s.durable_version,'index:v1');
 assert.deepEqual(s.captured_reader_records,['A@1','B@1']);assert.equal(s.notification,'not_sent');assert.equal(s.completion_signal,false);
});
test('file protocol rejects stale version in lock and releases it without replacement',()=>{
 const s=last(mid.protocol,'conflict').getScene().state;
 assert.equal(s.target,'old_complete');assert.equal(s.lock_held,false);assert.notEqual(s.result,'committed');
});
test('formal protocol reference may mount another owner while retaining entry identity',()=>{
 const s=createResourceSimulation(content(mid.create,'file-storage'));
 assert.equal(s.getScene().entry_object_id,'file-storage');assert.equal(s.getScene().object_id,'storage.workspace.atomic-files');
 assert.throws(()=>createResourceSimulation({...content(mid.create),entryObjectId:'resource-access'}),/entry|owner/);
});
test('exact ID, frozen sources and restoring the same mechanism are required',()=>{
 const c=content(mid.replace);c.mechanism={...c.mechanism,id:'synthetic-copy'};
 assert.throws(()=>createResourceSimulation(c),/mechanism|adapter/);
 const bad=content(mid.replace);bad.evidence=bad.evidence.filter(e=>e.id!==bad.mechanism.evidence_ids[0]);
 assert.throws(()=>createResourceSimulation(bad),/evidence/);
 const s=last(mid.create,'exists'),saved=s.getViewState();
 assert.throws(()=>s.restore({...saved,batchKey:'other'}),/batch/);
 assert.throws(()=>s.restore({...saved,mechanismId:mid.replace}),/batch|mechanism/);
});

test('the upgraded graph consumes the formal teaching schema, rejecting legacy matrix content',()=>{
 const id='resources.mechanism.resources.permissions.coverage', c=content(id), s=createResourceSimulation(c);
 s.selectCase('combined_private');s.seek(s.getFrames().length-1);
 assert.equal(s.getScene().state.new_grant_count,0);
 assert.deepEqual(s.getScene().state.persistent_grants_before,s.getScene().state.persistent_grants_after);
 for(const name of ['missing_parent','cycle','revision_missing','revision_mismatch']){
  s.selectCase(name);s.seek(s.getFrames().length-1);assert.notEqual(s.getScene().state.effective_level,'viewer');
 }
 assert.throws(()=>createResourceSimulation({...c,mechanism:{...c.mechanism,adapter:'tfi.resources.permission_matrix.v1'}}),/adapter/);
});

test('every registered mechanism can replay its frozen paths with enforced guards',()=>{
 for(const id of Object.keys(RESOURCE_MECHANISMS)){
  const s=createResourceSimulation(content(id));
  for(const c of s.getCases()){s.selectCase(c.id);s.seek(s.getFrames().length-1);assert.equal(s.getScene().case_id,c.id);}
 }
});
