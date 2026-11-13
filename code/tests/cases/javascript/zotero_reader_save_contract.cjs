const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2]+'/src/common/annotation-manager.js','utf8');
const body=source.slice(source.indexOf('class AnnotationManager'),source.lastIndexOf('export default'));
let timer;
const Manager=vm.runInNewContext(body+';AnnotationManager',{DEBOUNCE_TIME:1000,DEBOUNCE_MAX_TIME:10000,Date,setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{}});
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function state(){return Object.assign(Object.create(Manager.prototype),{_unsavedAnnotations:new Map(),_highVolatilityAnnotationIDs:new Set(),_onSave:async()=>{},_onDelete:async()=>{},_lastChangeTime:Date.now(),_lastSaveTime:Date.now()});}
const add=(m,id,value={id})=>m._unsavedAnnotations.set(id,value);
(async()=>{
 let m=state(),gate=defer(),calls=0;add(m,'A');m._onSave=()=>{calls++;return gate.promise;};m._triggerSaving();assert(timer);const first=m.flush();assert.equal(first,m.flush());await new Promise(r=>setImmediate(r));assert.equal(calls,1);add(m,'B');gate.resolve();await first;assert.equal(calls,2);assert.equal(m._unsavedAnnotations.size,0);
 for(const deletion of [false,true]) for(const sync of [false,true]) {
  m=state();add(m,'A',deletion?null:{id:'A',image:'pixels'});
  const fail=()=>{throw Error('offline');};m[deletion?'_onDelete':'_onSave']=sync?fail:async()=>fail();
  await assert.rejects(m.flush(),/offline/);assert(m._unsavedAnnotations.has('A'));assert.equal(m._savePromise,null);
  m._onSave=async()=>{};m._onDelete=async()=>{};await m.flush();assert.equal(m._unsavedAnnotations.size,0);
 }
 m=state();add(m,'A',{id:'A',image:'pixels'});gate=defer();m._onSave=async()=>{await gate.promise;throw Error('offline');};let saving=m.flush();await new Promise(r=>setImmediate(r));add(m,'A',{id:'A',comment:'new'});m._highVolatilityAnnotationIDs.add('A');gate.resolve();await assert.rejects(saving);assert.equal(m._unsavedAnnotations.get('A').comment,'new');assert.equal(m._unsavedAnnotations.get('A').image,'pixels');
 m=state();add(m,'A');gate=defer();m._onSave=async()=>{await gate.promise;throw Error('offline');};saving=m.flush();await new Promise(r=>setImmediate(r));add(m,'A',null);gate.resolve();await assert.rejects(saving);assert.equal(m._unsavedAnnotations.get('A'),null);
 m=state();add(m,'D',null);add(m,'S');let deletes=0;m._onDelete=async()=>{deletes++;};m._onSave=async()=>{throw Error('partial');};await assert.rejects(m.flush());m._onSave=async()=>{};await m.flush();assert.equal(deletes,2);
 assert.equal(m._highVolatilityAnnotationIDs.size,0);
 m=state();add(m,'I',{id:'I',image:'pending'});m._highVolatilityAnnotationIDs.add('I');m._onSave=async()=>{throw Error('image failure');};await assert.rejects(m.flush());let retried;m._onSave=async values=>{retried=values[0];};await m.flush();assert.equal(retried.image,'pending');assert.equal(retried.onlyTextOrComment,undefined);
 m=state();add(m,'D',null);gate=defer();m._onDelete=()=>gate.promise;saving=m.flush();let done=false;saving.then(()=>done=true);await Promise.resolve();assert.equal(done,false);gate.resolve();await saving;
 m=state();add(m,'A');m._lastChangeTime=0;let reported=0;m._onSave=async()=>{throw Error('auto');};m._onSaveError=()=>reported++;m._triggerSaving();await new Promise(r=>setImmediate(r));assert.equal(reported,1);m._triggerSaving();assert.equal(reported,1);m._onSave=async()=>{};await m.flush();
 console.log('PASS save contract: immediate barrier, concurrent flush, single writer/drain, delete ack, sync/async failure retry, newer edits/tombstones/image, partial replay, automatic error/pause');
})().catch(e=>{console.error(e);process.exitCode=1;});
