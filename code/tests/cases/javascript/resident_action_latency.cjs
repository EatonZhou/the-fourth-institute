'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {babelParse} = require('./babel_parse.cjs');
const base = path.resolve(__dirname, '../../../management_portal/static');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const traces = [];
function moduleOwner(file, name, values, globals = {}) {
  const w = {PortalI18n: {t: String, text: (parts,...args) => parts.reduce((s,p,i)=>s+p+(args[i]??''),'')}, ...globals}; w.window=w;
  vm.runInNewContext(fs.readFileSync(path.join(base,file),'utf8'),w);
  const owner=w[name];return owner[Object.keys(owner).find(k=>k.startsWith('create'))](Object.fromEntries(owner.dependencies.map(k=>[k, k in values?values[k]:(()=>{throw Error('unexpected port '+k);})])));
}
function service(execute) {
  const w={PortalI18n:{t:String},PortalUIPrimitives:{randomUUID:()=> 'isolated-key'}};w.window=w;
  vm.runInNewContext(fs.readFileSync(path.join(base,'portal_society_service.js'),'utf8'),w);
  return w.PortalSocietyService.createClient({execute,getRevision:()=>7});
}
async function deletion() {
  const events=[],read=deferred(),node={id:'R',title:'Fixture'};
  const client=service(async (name,args,controls)=>{events.push({name,args,controls});return name==='GetLineage'?read.promise:{deleted_resident_ids:['R']};});
  const state={lineage:{lineage_id:'L',revision:7,nodes:[node],edges:[]},residents:[{resident_id:'R'}],selectedNodeId:'R'};
  const ui=moduleOwner('hatg_resident_dialogs.js','PortalHatgResidentDialogs',{
    state,societyService:client,nodeById:()=>node,currentNode:()=>node,familyForFacade:()=>null,nodeResidentId:n=>n.id,
    clearNodeSelection:()=>events.push({name:'clear-selection'}),setSyncStatus:()=>{},toast:()=>{},
    commitLineage:()=>events.push({name:'render-lineage'}),loadResults:async()=>events.push({name:'read-results'}),
  });
  assert.equal(await ui.removeSelectedNode({nodeId:'R',skipConfirm:true}),true);
  await tick();assert.equal(events.some(e=>e.name==='render-lineage'),false);
  assert.equal(state.residents.length,0,'committed delete updates sidebar before graph read completes');
  assert.equal(events.filter(e=>e.name==='DeleteResident').length,1);
  assert.equal(events[0].controls.expectedVersion,7);assert.equal(events[0].controls.idempotencyKey,'resident-delete:isolated-key');
  read.resolve({lineage:{lineage_id:'L',revision:8,nodes:[],edges:[]}});await tick();
  assert.equal(events.filter(e=>e.name==='GetLineage').length,1);
  assert.equal(events.filter(e=>e.name==='render-lineage').length,1);traces.push({scenario:'delete-real-dialog',events});
}
async function fork() {
  const events=[],read=deferred(),layout=deferred();
  const client=service(async(name,args,controls)=>{events.push({name,args,controls});return name==='GetLineage'?read.promise:{resident_id:'NEW'};});
  const ui=moduleOwner('lineage_workbench_resident_dialogs.js','PortalLineageWorkbenchResidentDialogs',{
    state:{},societyService:client,beginResidentSidebarMutation:()=>events.push({name:'busy-start'}),endResidentSidebarMutation:()=>events.push({name:'busy-end'}),
    currentLineageWorkbench:()=>({getPanel:()=>({applyLineageMutation:async()=>{events.push({name:'apply-layout'});await layout.promise;}})}),
    rememberResidentShortcutSession:()=>{},selectResident:async()=>{events.push({name:'open-chat'});return true;},
    renderAgentList:()=>{},renderFavoriteList:()=>{},
  },{PortalUI:{toast:message=>events.push({name:'error',message})}});
  const action=(async()=>{const response=await client.branchNode({source_resident_id:'R',lineage_id:'L'});return ui.completeResidentFork(response,'L');})();await tick();
  assert.deepEqual(events.map(e=>e.name),['ForkResident','GetLineage']);
  read.resolve({lineage:{lineage_id:'L',revision:8,nodes:[{id:'NEW'}]}});await tick();
  assert.equal(events.some(e=>e.name==='open-chat'),false,'current Fork awaits explicit layout application');
  layout.resolve();await action;
  assert.equal(events.filter(e=>e.name==='ForkResident').length,1);assert.equal(events.filter(e=>e.name==='GetLineage').length,1);
  assert.ok(events.findIndex(e=>e.name==='open-chat')>events.findIndex(e=>e.name==='apply-layout'));
  assert.equal(events.at(-1).name,'open-chat');traces.push({scenario:'fork-service-and-public-completion-controller',events});
}
async function opening() {
  const events=[],detail=deferred(),graph=deferred();let ready=false;
  const state={chatTarget:null};const panel={instanceId:()=> 'chat',isReadyFor:()=>ready,target:()=>({lineage_id:'L'})};
  const c={state,residentSelectionRequest:0,window:{PortalState:{}},
    closeChatroomView(){},residentChatTarget:(id,lineage_id)=>({id,lineage_id,key:id}),
    currentAgentChatPanel:()=>panel,currentLineageWorkbench:()=>({getPanel:()=>panel,focusPanel(){}}),
    resolveResidentNavigationTarget:async target=>{events.push('resolve-known-route');return target;},
    activeChatTargetId:()=>state.chatTarget?.id,lineageHatgTargetMatches:()=>false,
    syncLineageHatgPanel:async()=>{events.push('graph-start');await graph.promise;events.push('graph-done');},
    syncChatTargetLocation(){},rememberChatWorkspaceBeforeSwitch:()=>false,commitChatTarget:t=>{state.chatTarget=t;},
    beginChatWorkspaceNavigation(){},resetChatRenderState(){},writeLastSelection(){},syncResidentNavigationSelection(){},syncSelectedResidentSurface(){},
    loadDetail:async()=>{events.push('detail-start');await detail.promise;ready=true;events.push('detail-done');},
    loadFolder:async()=>events.push('folder'),publishConversationSelection:()=>events.push('publish'),
  };
  const source=fs.readFileSync(path.join(base,'lineage_workbench_selection.js'),'utf8');
  const iife=babelParse(source,'selection.js').program.body[0].expression.callee;
  const factory=iife.body.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='createSelection');
  const fn=factory.body.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='selectResident');
  vm.createContext(c);vm.runInContext(source.slice(fn.start,fn.end),c);
  const action=c.selectResident('R',{lineageId:'L',forceView:true,revealInGraph:true});await tick();
  assert.ok(events.includes('detail-start')&&events.includes('graph-start'),'Chat/detail and graph already start concurrently');
  detail.resolve();assert.equal(await action,true);assert.equal(events.includes('graph-done'),false,'open does not await slow graph');
  graph.resolve();await tick();traces.push({scenario:'open-real-selection-controller',events});
}
async function failures() {
  const events=[];let failRead=true;
  const c=service(async(name)=>{events.push(name);if(name==='GetLineage'&&failRead)throw Error('readback failed');return {resident_id:'NEW'};});
  await assert.rejects(c.branchNode({lineage_id:'L',source_resident_id:'R'}),e=>e.committedMutation?.instruction==='ForkResident');
  assert.equal(events.filter(n=>n==='ForkResident').length,1);
  await assert.rejects(c.deleteNode({lineage_id:'L',resident_id:'R'}),e=>e.code==='lineage.refresh_required'&&!e.committedMutation);
  assert.equal(events.includes('DeleteResident'),false,'failed reconciliation cannot submit another stale write');
  const denied=service(async name=>{events.push('denied:'+name);throw Object.assign(Error('denied'),{status:403});});
  await assert.rejects(denied.deleteNode({lineage_id:'L',resident_id:'R'}),e=>e.status===403&&!e.committedMutation);
  assert.equal(events.includes('denied:GetLineage'),false);traces.push({scenario:'readback-failure-and-acl',events});
}
(async()=>{await deletion();await fork();await opening();await failures();})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{
  if(process.env.RESIDENT_ACTION_TRACE)fs.writeFileSync(process.env.RESIDENT_ACTION_TRACE,JSON.stringify(traces,null,2));
  console.log(JSON.stringify(traces));
});
