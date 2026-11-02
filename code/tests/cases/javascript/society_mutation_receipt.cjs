const assert=require('node:assert/strict'),{test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_society_service.js'),'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function moduleFixture(file,globalName,values,globals={}) {
 const context={PortalI18n:{t:String,text:(parts,...args)=>parts.reduce((s,p,i)=>s+p+(args[i]??''),'')},...globals};
 context.window=context;
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',file),'utf8'),context);
 const owner=context[globalName];
 return owner[Object.keys(owner).find(key=>key.startsWith('create'))](Object.fromEntries(owner.dependencies.map(name=>[
  name,Object.hasOwn(values,name)?values[name]:(()=>{throw Error('unexpected '+name);})
 ])));
}
function fixture(execute,getRevision){let key=0;const context={PortalUIPrimitives:{randomUUID:()=>String(++key)},PortalI18n:{t:String}};context.window=context;vm.runInNewContext(source,context);return context.PortalSocietyService.createClient({execute,getRevision});}

test('committed receipt returns before a blocked graph read; concurrent observers share that read',async()=>{
 const read=deferred(),calls=[];
 const client=fixture(async name=>{calls.push(name);return name==='GetLineage'?read.promise:{resident_id:'child'};});
 const receipt=await client.branchNode({lineage_id:'L',source_resident_id:'parent',expected_version:1,defer_refresh:true});
 assert.equal(receipt.resident_id,'child');assert.equal(receipt.lineage,undefined);
 let finished=false;receipt.refresh.then(()=>{finished=true;});await turn();assert.equal(finished,false);
 const observed=client.loadLineage('L');await turn();assert.equal(calls.filter(x=>x==='GetLineage').length,1);
 read.resolve({lineage:{lineage_id:'L',revision:2}});assert.equal((await receipt.refresh).lineage.revision,2);await observed;
 assert.equal(calls.filter(x=>x==='ForkResident').length,1);
});

test('slow refresh cannot raise the version of a concurrently constructed old draft',async()=>{
 const read=deferred(),calls=[];let displayed=10,readCount=0;
 const client=fixture(async(name,args,controls)=>{
  calls.push({name,args,controls});
  if(name==='GetLineage')return ++readCount===1?read.promise:{lineage:{lineage_id:'L',revision:11}};
  if(name==='ForkResident')return {resident_id:'child'};
  assert.equal(controls.expectedVersion,10);
  throw Object.assign(new Error('stale draft'),{code:'instruction.version_conflict'});
 },()=>displayed);
 const receipt=await client.branchNode({lineage_id:'L',source_resident_id:'parent',defer_refresh:true});
 // Construct a config from the old visible graph while refresh is blocked.
 const mutation=client.saveProgramComponent({lineage_id:'L',component_id:'c',component:{title:'old draft'}});
 const rejection=assert.rejects(mutation,error=>error.code==='instruction.version_conflict');
 await turn();assert.equal(calls.some(x=>x.name==='UpdateComponent'),false);
 displayed=11;read.resolve({lineage:{lineage_id:'L',revision:11}});await receipt.refresh;await rejection;
 assert.equal(calls.filter(x=>x.name==='UpdateComponent').length,1);
 assert.equal(calls.find(x=>x.name==='UpdateComponent').controls.expectedVersion,10);
});

test('a failed asynchronous refresh carries a committed receipt and never replays the write',async()=>{
 let writes=0,reads=0;
 const client=fixture(async name=>{if(name==='GetLineage'){if(++reads===1)throw Error('offline');return {lineage:{lineage_id:'L',revision:4}};}writes++;return {resident_id:'child'};});
 const receipt=await client.branchNode({lineage_id:'L',source_resident_id:'parent',expected_version:3,defer_refresh:true});
 await assert.rejects(receipt.refresh,error=>error.committedMutation.instruction==='ForkResident');
 assert.equal(writes,1);
 await client.branchNode({lineage_id:'L',source_resident_id:'parent',expected_version:3});
 assert.equal(writes,2,'only the separately requested action writes');assert.equal(reads,3);
});

test('a pre-commit observer is drained and cannot serve as the mutation refresh',async()=>{
 const old=deferred();let reads=0;
 const client=fixture(async name=>name==='GetLineage'?(++reads===1?old.promise:{lineage:{lineage_id:'L',revision:2}}):{resident_id:'child'});
 const observed=client.loadLineage('L');await turn();
 const receipt=await client.branchNode({lineage_id:'L',source_resident_id:'parent',defer_refresh:true});
 old.resolve({lineage:{lineage_id:'L',revision:1}});await observed;
 assert.equal((await receipt.refresh).lineage.revision,2);assert.equal(reads,2);
});

test('a failed recovery read does not mislabel a later unsent action as committed',async()=>{
 let writes=0;
 const client=fixture(async name=>{if(name==='GetLineage')throw Error('offline');writes++;return {resident_id:'child'};});
 const receipt=await client.branchNode({lineage_id:'L',source_resident_id:'parent',expected_version:3,defer_refresh:true});
 await assert.rejects(receipt.refresh,error=>Boolean(error.committedMutation));
 await assert.rejects(client.saveProgramComponent({lineage_id:'L',component_id:'c',expected_version:3,component:{title:'draft'}}),error=>{
  assert.equal(error.code,'lineage.refresh_required');assert.equal(error.committedMutation,undefined);
  assert.equal(error.body.error.commit_state,'not_committed');return true;
 });
 assert.equal(writes,1);
});

test('Fork releases controls at its receipt and slow refresh respects a changed selection',async()=>{
 const graph=deferred(),source={id:'parent',title:'parent'},child={id:'child',title:'child'},calls=[];
 const state={lineage:{lineage_id:'L',revision:10,nodes:[source]},selectedNodeId:'parent',graphController:{getPlacementGeometry:()=>({})}};
 const dialogs=moduleFixture('hatg_resident_dialogs.js','PortalHatgResidentDialogs',{
  state,nodeById:id=>state.lineage.nodes.find(n=>n.id===id),familyForFacade:()=>null,familyForNewResident:()=>null,
  flushLineageLayoutSave:async()=>{},societyService:{branchNode:async payload=>{calls.push(payload);return {resident_id:'child',refresh:graph.promise};}},
  setSyncStatus(){},toast(){},renderGraph(){},bindResidentStateCollection(){},
  commitLineage:lineage=>{state.lineage=lineage;},separateGraphObject:async()=>{},selectNode:async()=>{throw Error('must not steal selection');},
 });
 const receipt=await dialogs.createQuickFork(source,'full');
 assert.equal(receipt.resident_id,'child');assert.equal(state.structureMutationInFlight,false);assert.equal(calls[0].expected_version,10);
 assert.equal(state.lineage.revision,10);state.selectedNodeId='other';
 graph.resolve({resident_id:'child',lineage:{lineage_id:'L',revision:11,nodes:[source,child]}});await turn();
 assert.equal(state.lineage.revision,11);assert.equal(state.selectedNodeId,'other');assert.equal(calls.length,1);
});

test('delete captures its displayed precondition before confirmation and cannot refresh another view',async()=>{
 const graph=deferred(),confirm=deferred(),node={id:'parent',title:'parent'},calls=[];
 const state={lineage:{lineage_id:'L',revision:10,nodes:[node],edges:[]},selectedNodeId:'parent',residents:[{resident_id:'parent'}]};
 const dialogs=moduleFixture('hatg_resident_dialogs.js','PortalHatgResidentDialogs',{
  state,nodeById:()=>node,currentNode:()=>node,nodeResidentId:n=>n.id,familyForFacade:()=>null,
  societyService:{deleteNode:async payload=>{calls.push(payload);return {deleted_resident_ids:['parent'],refresh:graph.promise};}},
  clearNodeSelection(){throw Error('must not clear another view');},setSyncStatus(){},toast(){},
  commitLineage(){throw Error('must not install old graph');},
 },{PortalUI:{confirm:()=>confirm.promise}});
 const deleting=dialogs.removeSelectedNode();state.lineage={lineage_id:'other',revision:99};confirm.resolve(true);
 assert.equal(await deleting,true);assert.equal(calls[0].lineage_id,'L');assert.equal(calls[0].expected_version,10);
 graph.resolve({lineage:{lineage_id:'L',revision:11,nodes:[],edges:[]}});await turn();
 assert.equal(state.lineage.lineage_id,'other');assert.equal(calls.length,1);
});

test('an inspector draft retains its own version even when a newer graph was displayed before save',async()=>{
 const calls=[],form={dataset:{programDraftDirty:'true'},isConnected:true},component={id:'c',kind:'seed',config:{}};
 const state={lineage:{lineage_id:'L',revision:11},selectedProgramObjectId:'c'};
 const client=fixture(async(name,args,controls)=>{
  if(name==='GetLineage')return {lineage:{lineage_id:'L',revision:11}};
  calls.push({name,args,controls});throw Object.assign(Error('conflict'),{code:'instruction.version_conflict'});
 },()=>state.lineage.revision);
 const saving=moduleFixture('hatg_program_inspector_saving.js','PortalHatgProgramInspectorSaving',{
  state,definitionRevisions:new Map([[form,10]]),programComponentById:()=>component,societyService:client,
  applyProgramMutation:request=>request({defer_refresh:true}),selectProgramComponent(){},
 },{FormData:class{get(){return '';}}});
 await assert.rejects(saving.saveProgramComponentFromInspector('c',form),error=>error.code==='instruction.version_conflict');
 assert.equal(calls.length,1);assert.equal(calls[0].controls.expectedVersion,10);
 assert.equal(form.dataset.programDraftDirty,'true');
});

test('wire preparation alone waits for a saved component projection',async()=>{
 const graph=deferred(),form={dataset:{programDraftDirty:'true'},isConnected:true};
 const state={lineage:{lineage_id:'L',revision:10}};
 const port={node_id:'c',port_id:'out',direction:'out'},component={id:'c'};
 const edits=moduleFixture('hatg_program_edits.js','PortalHatgProgramEdits',{
  state,$id:id=>id==='programInspectorPanel'?{dataset:{componentId:'c'},querySelector:()=>form}:null,
  programComponentById:()=>component,displayGraphItemById:()=>component,renderGraph(){},
  saveProgramComponentFromInspector:async()=>({refresh:graph.promise}),
 },{PortalLineageProgram:{normalizePort:value=>value,portsForNode:()=>[port]}});
 let finished=false;const preparing=edits.handleProgramPortClick(port).then(()=>{finished=true;});
 await turn();assert.equal(finished,false);state.lineage.revision=11;graph.resolve({lineage:state.lineage});
 await preparing;assert.equal(state.programWireDraft,port);
});

test('a delayed inspector completion cannot clear edits made after the committed receipt',async()=>{
 const graph=deferred(),form={dataset:{programDraftDirty:'true'},isConnected:true};
 const component={id:'c',kind:'seed',config:{}},state={lineage:{lineage_id:'L',revision:10},selectedProgramObjectId:'c'};
 let selected=0;
 const saving=moduleFixture('hatg_program_inspector_saving.js','PortalHatgProgramInspectorSaving',{
  state,definitionRevisions:new Map([[form,10]]),programComponentById:()=>component,
  societyService:{saveProgramComponent:async()=>({component,refresh:graph.promise})},
  applyProgramMutation:async request=>{const receipt=await request({defer_refresh:true});form.dataset.programDraftDirty='false';return receipt;},
  selectProgramComponent:()=>{selected++;},
 },{FormData:class{get(){return '';}}});
 const receipt=await saving.saveProgramComponentFromInspector('c',form);
 assert.equal(receipt.component.id,'c');assert.equal(selected,0);
 form.dataset.programDraftDirty='true';graph.resolve({component,lineage:{lineage_id:'L',revision:11}});
 await turn();assert.equal(selected,0);assert.equal(form.dataset.programDraftDirty,'true');
});

test('Execution unavailability overrides stale canvas caches and recovers at the same revisions',()=>{
 for(const cached of ['idle','running']) {
  const node={id:'parent',status:cached,accessible:true};
  const state={lineage:{lineage_id:'L',revision:10,runtime_revision:50,nodes:[node],edges:[]},
   runtimeStatus:new Map([['parent',cached]]),residentRuntimeById:new Map([['parent',{resident_state:{display_work_state:'queued',work_phase:'waiting_quota'}}]]),localVersion:0};
  const owner=moduleFixture('hatg_lineage_state.js','PortalHatgLineageState',{
   state,clone:value=>JSON.parse(JSON.stringify(value)),nodeResidentId:n=>n.id,reconcileGraphInteractionState(){},
  });
  const model=moduleFixture('hatg_graph_model.js','PortalHatgGraphModel',{state});
  const status=moduleFixture('hatg_node_status.js','PortalHatgNodeStatus',{
   state,effectiveNodeStatus:model.effectiveNodeStatus,nodeResidentId:n=>n.id,
  },{PortalResidentProtocol:{stateView:value=>value||{}}});
  const unavailable={lineage_id:'L',revision:10,runtime_revision:0,nodes:[{...node,status:'unknown'}],edges:[],
   resident_runtime_observation:{status:'unavailable',source:'execution'}};
  owner.commitLineage(unavailable,{render:false});
  assert.equal(state.lineage.nodes[0].status,'unknown');assert.equal(state.runtimeStatus.size,0);assert.equal(state.residentRuntimeById.size,0);
  // Even a late older subscription projection cannot restore false certainty.
  state.runtimeStatus.set('parent',cached);state.residentRuntimeById.set('parent',{resident_state:{display_work_state:'queued',work_phase:'waiting_quota'}});
  assert.equal(model.effectiveNodeStatus(state.lineage.nodes[0]),'unknown');
  assert.equal(status.nodeWorkPresentation(state.lineage.nodes[0]).label,'状态未知');
  const recovered={lineage_id:'L',revision:10,runtime_revision:0,nodes:[{...node,status:'running'}],edges:[]};
  owner.commitLineage(recovered,{render:false});
  assert.equal(state.lineage.resident_runtime_observation,undefined);assert.equal(state.runtimeStatus.size,0);
  assert.equal(model.effectiveNodeStatus(state.lineage.nodes[0]),'running');assert.equal(status.nodeWorkPresentation(state.lineage.nodes[0]).label,'running');
 }
});

test('canvas busy state ends at receipt; graph converges later without clearing a newer draft or switched view',async()=>{
 const context={window:{PortalI18n:{t:String},setTimeout:()=>1,clearTimeout(){},requestAnimationFrame:fn=>fn()}};
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/hatg_program_edits.js'),'utf8'),context);
 const module=context.window.PortalHatgProgramEdits,state={lineage:{lineage_id:'L',revision:1}};
 const status={hidden:true,textContent:''},notices=[];let busy=false,commits=0;
 const values={state,$id:()=>status,toast:(...args)=>notices.push(args),
  lineageEditQueue:{capture:id=>id,isCurrent:id=>id===state.lineage.lineage_id,enqueue:(_,run)=>run()},
  programFeature:{beginMutation(){busy=true;},endMutation(){busy=false;}},
  renderProgramInspector(){},renderLineageSurface(){},commitLineage(lineage){state.lineage=lineage;commits++;}};
 const edits=module.createProgramEdits(Object.fromEntries(module.dependencies.map(name=>[name,values[name]||(()=>{throw Error('unexpected '+name);})])));
 const graph=deferred(),form={isConnected:true,dataset:{programDraftDirty:'true'}};
 const receipt=await edits.applyProgramMutation(async options=>{assert.equal(options.defer_refresh,true);return {component_id:undefined,refresh:graph.promise};},'',{draftForm:form});
 assert.equal(busy,false);assert.equal(commits,0);assert.equal(form.dataset.programDraftDirty,'false');
 assert.match(status.textContent,/修改已提交/);assert.equal(status.hidden,false);
 form.dataset.programDraftDirty='true';graph.resolve({lineage:{lineage_id:'L',revision:2}});await receipt.refresh;await turn();
 assert.equal(commits,1);assert.equal(form.dataset.programDraftDirty,'true');assert.match(status.textContent,/状态已保存/);
 const stale=deferred();await edits.applyProgramMutation(async()=>({refresh:stale.promise}),'');
 state.lineage={lineage_id:'other',revision:1};stale.resolve({lineage:{lineage_id:'L',revision:3}});await turn();
 assert.equal(commits,1);assert.equal(state.lineage.lineage_id,'other');
});
