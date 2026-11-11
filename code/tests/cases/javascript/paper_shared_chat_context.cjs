const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const window = {PortalI18n: {t: x => x},PortalUI:{loadCurrentUser:async()=>({ok:true,data:{username:'actor-a',principal:{id:'actor-a'}}})},dispatchEvent(){}};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/paper_chat.js'), 'utf8'), {window,CustomEvent:class{}});
const state = {selectedId:'paper_0123456789ab', principalId:'actor-a'};
let collection = false, duringRequest = () => {}, requests = [];
let resident = 'resident-a';
let surface = {paper_id:state.selectedId, workspace_key:'paper:'+state.selectedId};
const deps = Object.fromEntries(window.PortalPaperChat.dependencies.map(key => [key, () => {}]));
Object.assign(deps, {state, isCollectionMode:()=>collection, paperApi:{request:async (_url, options)=>{
  if(_url==='/paper/api/research/chats') return {principal:'actor-a',slots:[],selected:null,legacy:[]};
  if(!options)return {url:_url};
  requests.push(JSON.parse(options.body)); duringRequest();
  return {resident_id:resident, initial_context:{context_text:'trusted single-paper context'}};
}}});
const chat = window.PortalPaperChat.createChat(deps);
const descriptor = chat.paperChatDescriptor();
descriptor.onShow({panel:{runtime:{surface:()=>surface,target:()=>({resident_id:resident})}}});
const submit = () => descriptor.options.submissionContext({resident_id:'resident-a'});
(async()=>{
  await chat.loadMe();await new Promise(resolve=>setImmediate(resolve));
  state.researchLegacyKey=surface.workspace_key; // explicit legacy selection; no implicit new Resident
  const resolved=await descriptor.options.resolveResident(surface);
  assert(resolved.url.includes('paper_id='+surface.paper_id));
  // A saved single-paper workspace can be opened while collection/history UI
  // state is active. That state must never be spread into its prepare body.
  state.requestedChatWorkspaceId=surface.workspace_key; collection=true;
  await descriptor.options.resolveBeforeSend(surface,{text:'First question'});
  assert.deepEqual(requests.pop().context,{paper_id:surface.paper_id});
  assert.equal((await descriptor.options.submissionContext({resident_id:resident})).context_text,'trusted single-paper context');
  requests=[]; collection=false;state.requestedChatWorkspaceId='';
  assert.equal((await submit()).context_text, 'trusted single-paper context');
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].context, {paper_id:'paper_0123456789ab'});
  assert.equal(requests[0].workspace_key, surface.workspace_key);
  duringRequest=()=>{state.selectedId='paper_222222222222';};
  await assert.rejects(submit(), /讨论已变化/);
  state.selectedId=surface.paper_id;
  duringRequest=()=>{state.principalId='actor-b';};
  await assert.rejects(submit(), /讨论已变化/);
  state.principalId='actor-a';
  duringRequest=()=>{resident='resident-b';};
  await assert.rejects(submit(), /讨论已变化/);
  resident='resident-a';duringRequest=()=>{};
  collection=true;state.requestedChatWorkspaceId='collection:history'; const count=requests.length;
  surface={paper_id:'',workspace_key:'collection:history'};
  assert.equal(await submit(),null);assert.equal(requests.length,count);
  await descriptor.options.resolveBeforeSend(surface,{text:'Collection question'});
  assert.equal(requests.at(-1).context.collection_mode,'history');
  console.log('PASS: Resident-only target uses Paper surface; late paper/account/Resident rejected; collection unchanged');
})().catch(error=>{console.error(error);process.exitCode=1;});
