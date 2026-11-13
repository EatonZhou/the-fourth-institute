// Characterize the pinned source, not a replacement implementation.
// Usage: node .../zotero_reader_save_feasibility.cjs /path/to/pinned/reader
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const root=path.resolve(process.argv[2]);
assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),'04795bfbafd55600e042d62e05f9a4147458ae53');
const requireUpstream=createRequire(path.join(root,'package.json'));
const {parse}=requireUpstream('acorn');
const source=fs.readFileSync(path.join(root,'src/common/annotation-manager.js'),'utf8');
const tree=parse(source,{ecmaVersion:'latest',sourceType:'module'});
const klass=tree.body.find(n=>n.type==='ClassDeclaration'&&n.id.name==='AnnotationManager');
const method=klass.body.body.find(n=>n.key.name==='_triggerSaving');
let timer;
const trigger=vm.runInNewContext(`(async function() ${source.slice(method.value.body.start,method.value.body.end)})`,{
  DEBOUNCE_TIME:1000,DEBOUNCE_MAX_TIME:10000,Date,
  clearTimeout:()=>{},setTimeout:callback=>{timer=callback;return 1;},
});
function state(){return {_unsavedAnnotations:new Map([['A',{id:'A',comment:'edited'}]]),
  _highVolatilityAnnotationIDs:new Set(),_hasAnnotationsUnknownToClient:()=>false,
  _onDelete:()=>{},_onSave:async()=>{},_triggerSaving:trigger};}
(async()=>{
  const pending=state();pending._lastChangeTime=pending._lastSaveTime=Date.now();
  await trigger.call(pending);
  assert.equal(typeof timer,'function');assert.equal(pending._unsavedAnnotations.size,1);
  // A resolved trigger call is not a durable-save barrier when debounced.
  const failure=state();failure._onSave=async()=>{throw Error('offline');};
  await assert.rejects(trigger.call(failure),/offline/);
  assert.equal(failure._unsavedAnnotations.size,0);
  assert.equal(failure._savingInProgress,true);
  failure._unsavedAnnotations.set('B',{id:'B'});
  let calls=0;failure._onSave=async()=>{calls++;};await trigger.call(failure);
  assert.equal(calls,0); // subsequent edits cannot restart the worker
  const deletion=state();deletion._unsavedAnnotations=new Map([['A',null]]);
  let acknowledged=false,ack;
  deletion._onDelete=()=>new Promise(resolve=>{ack=()=>{acknowledged=true;resolve();};});
  await trigger.call(deletion);
  assert.equal(acknowledged,false);assert.equal(deletion._savingInProgress,false);ack();
  console.log('Confirmed pinned-source blockers: debounce has no barrier; save rejection drops batch and wedges worker; delete acknowledgment is not awaited');
})().catch(error=>{console.error(error);process.exitCode=1;});
