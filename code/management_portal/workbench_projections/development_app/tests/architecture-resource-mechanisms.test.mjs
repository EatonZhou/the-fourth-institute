import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createResourceSimulation, workNotes} from '../src/architecture/resource-mechanisms.model.js';
const filename = process.env.RESOURCE_MODEL_FIXTURE;
if (!filename) throw new Error('RESOURCE_MODEL_FIXTURE must point to the authorized frozen exploration model');
const model = JSON.parse(fs.readFileSync(filename, 'utf8'));
const content = (owner='storage.workspace.atomic-files') => ({
  object:model.objects.find(x=>x.id===owner), mechanism:model.mechanisms.find(x=>x.owner_id===owner),
  evidence:model.evidence, work_items:model.work_items.filter(x=>x.object_ids?.includes(owner)),
  binding:model.binding, batchKey:'model7622-security',
});
test('resolved historical discrepancy uses resolution and its frozen source',()=>{
 const notes=workNotes(content());const note=notes.find(x=>x.id==='resources.work.text-fsync-design');
 assert.equal(note.resolved,true);assert.match(note.description,/规范已修订/);
 assert(note.evidence_ids.includes('reconciled.decision_0e043d5f81be'));
 const s=createResourceSimulation(content());s.selectCase('resources.mechanism.storage.workspace.atomic-files.case.text_replace-normal');s.seek(s.getFrames().length-1);
 assert.equal(s.getScene().state.file_synced,true);assert.equal(s.getScene().state.directory_sync,'not_requested');assert.doesNotMatch(s.getObservation(),/不符|工作项|尚待/);
});
test('restore belongs to one exact batch and case, not to caller-supplied simulated state',()=>{
 const c=content('storage.workspace.locks'),s=createResourceSimulation(c);s.seek(3);
 const saved=s.getViewState();s.seek(2);s.restore({...saved,state:{held:false}});
 assert.equal(s.getScene().state.held,true);
 assert.equal(s.getScene().state.content_replaced,true);
 assert.throws(()=>s.restore({...saved,batchKey:'other'}),/batch/);
 assert.throws(()=>s.restore({...saved,cursor:999}),/cursor/);
});
test('presentation cannot grant adapter support or an unreviewed scenario',()=>{
 const c=content();c.mechanism={...c.mechanism,adapter:'other'};
 assert.throws(()=>createResourceSimulation(c),/adapter/);
 const s=createResourceSimulation(content());
 assert(s.getCases().every(x=>x.inputs.operation!=='json_create'));
 assert.equal(s.getScene().state.target_exists,true);
 assert.throws(()=>s.selectCase('resources.mechanism.storage.workspace.atomic-files.case.create'),/case/);
});
test('missing resolution evidence or owner is refused before drawing labels',()=>{
 const c=content();c.evidence=c.evidence.filter(x=>x.id!=='reconciled.decision_0e043d5f81be');
 assert.throws(()=>createResourceSimulation(c),/evidence/);
 const b=content();b.object={...b.object,id:'different'};
 assert.throws(()=>createResourceSimulation(b),/owner/);
});
test('consumer snapshot does not follow later mutations of caller content',()=>{
 const c=content(),s=createResourceSimulation(c);const old=s.getContent().object.title;
 c.object.title='external mutation';assert.equal(s.getContent().object.title,old);
});
