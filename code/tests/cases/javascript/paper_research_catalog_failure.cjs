const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
let user='alice',catalog=async()=>{throw Error('catalog unavailable')};
const window={PortalI18n:{t:s=>s},PortalUI:{loadCurrentUser:async()=>({ok:true,data:{username:user,principal:{id:user}}})},dispatchEvent(){}};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/paper_chat.js'),'utf8'),{window,CustomEvent:class{}});
const state={};const deps=Object.fromEntries(window.PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
Object.assign(deps,{state,paperApi:{request:async(url)=>{assert.equal(url,'/paper/api/research/chats');return catalog();}}});
const chat=window.PortalPaperChat.createChat(deps);const opts=chat.paperChatDescriptor().options;
(async()=>{
 await chat.loadMe();await new Promise(resolve=>setImmediate(resolve));assert.equal(state.principalId,'alice'); // optional catalog cannot abort identity initialization
 assert.throws(()=>opts.resolveBeforeSend({},{}));await assert.rejects(()=>opts.submissionContext({}));
 catalog=async()=>({principal:'bob',slots:[]});await chat.loadMe();await new Promise(resolve=>setImmediate(resolve));
 assert.throws(()=>opts.resolveBeforeSend({},{}));
 // Old account's late result cannot enable a new account or replace selection.
 let finish;catalog=()=>new Promise(resolve=>{finish=resolve});const old=chat.loadMe();
 while(!finish) await new Promise(resolve=>setImmediate(resolve));
 user='bob';catalog=async()=>({principal:'bob',slots:[],selected:null});await chat.loadMe();
 finish({principal:'alice',slots:[{slot_id:'old'}],selected:{slot_id:'old'}});await old;
 assert.equal(state.principalId,'bob');assert.equal(state.researchSlot,null);
 // Successful retry may restore references only from the current principal.
 catalog=async()=>({principal:'bob',slots:[],selected:{slot_id:'current',resident_id:'r'}});await chat.loadMe();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(state.researchSlot.slot_id,'current');
 // A late successful startup catalog also synchronizes the displayed Chat,
 // not just the selector value. No root is replaced in this owner-level check.
 const opened=[];
 chat.paperChatDescriptor().onShow({panel:{runtime:{workspace:{},surface:()=>({workspace_key:'legacy'}),openSurface:async value=>{opened.push(value.workspace_key)}}}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(opened.at(-1),'research:current');
 // A hanging optional selector also must not gate the real init/library path.
 catalog=()=>new Promise(()=>{});
 const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/paper.js'),'utf8');
 const begin=source.indexOf('  async function init()'),end=source.indexOf('    renderChat();',begin);
 let reads=0,libraryRequests=0;
 const init=new Function('initPaperWorkbench','bindEvents','loadMe','readUiState','paperIdentityReady',
  'requestedPaperIdFromUrl','requestedSemanticSearchIdFromUrl','requestedChatWorkspaceIdFromUrl','initSemanticSearch',
  'requestedChatOpenFromUrl','applyPaneCollapse','syncModeTabs','loadCollectorJobs','state','loadLibrary','semanticSearchReady','window',
  source.slice(begin,end)+'\n};return init;')(()=>{},()=>{},chat.loadMe,()=>reads++,null,()=>'',()=>'',()=>'',()=>{},()=>false,()=>{},()=>{},async()=>{},
  {semanticSearch:{load:async()=>{}}},()=>{libraryRequests++;return Promise.resolve()},false,window);
 await init();assert.equal(reads,1);assert.equal(libraryRequests,1);
 catalog=async()=>{throw Error('catalog permission denied')};
 await init();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reads,2);assert.equal(libraryRequests,2);
 assert.throws(()=>opts.resolveBeforeSend({},{}));
 console.log('PASS catalog failure/principal mismatch do not block identity; sending fails closed; late account result ignored; retry restores current selection; real init calls library on denied or hanging catalog');
})().catch(e=>{console.error(e);process.exitCode=1});
