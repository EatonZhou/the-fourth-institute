// Real shared service/menu and Paper selector; controlled transport/runtime boundary.
// No model calls or production deletes. Test server state survives page reload.
const {chromium}=require('/opt/tfi/the-fourth-institute-dev/code/management_portal/test_framework/tau_runtime/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const source=name=>fs.readFileSync(path.resolve('management_portal/static',name),'utf8');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage();page.setDefaultTimeout(4000);
 await page.route('http://fixture/**',route=>route.fulfill({contentType:'text/html',body:'<main id="chat"><textarea id="draft">keep draft</textarea><div id="status"></div></main><div id="notice"></div><button id="delete">Delete selected</button>'}));
 const scripts=['portal_resident_service.js','portal_resident_menu_resident.js','paper_chat.js'].map(source);
 async function mount({legacy=false,noRecent=false,serverState=null}={}){
  await page.goto('http://fixture/');
  await page.evaluate(({legacy,noRecent,serverState})=>{
   window.PortalI18n={t:s=>s,text:(s,...v)=>s.map((a,i)=>a+(v[i]??'')).join('')};
   window.actor='alice';window.errors=[];window.toasts=[];window.PortalUI={confirm:async()=>true,loadCurrentUser:async()=>({ok:true,data:{username:actor,principal:{id:actor}}})};
   window.PortalUIPrimitives={randomUUID:()=>String(Math.random())};window.PortalIdentity={username:()=> actor};
   window.server=serverState||{slots:['A','B','C'].map(n=>({slot_id:n,resident_id:'rid'+n,title:'Assistant '+n,state:'bound'})),selected:'A',legacy:legacy?[{workspace_key:'paper:old',resident_id:'ridL',title:'Previous paper'}]:[]};
   window.operations={};window.calls=[];window.events=[];
   if(noRecent)localStorage.removeItem('portal.paper.research.recent:alice');
   window.addEventListener('portal:paper-research-selected',e=>events.push(e.detail));
  },{legacy,noRecent,serverState});
  for(const script of scripts)await page.addScriptTag({content:script});
  await page.evaluate(()=>{
   const transport={execute:async(name,args)=>{
    calls.push([name,args]);
    if(name==='GetResident')return {resident:{resident_id:args.resident_id,lineage_id:'lineage'}};
    if(name==='DeleteResident')return new Promise((resolve,reject)=>operations[args.resident_id]={resolve:()=>{server.slots=server.slots.filter(x=>x.resident_id!==args.resident_id);server.legacy=server.legacy.filter(x=>x.resident_id!==args.resident_id);resolve({resident_id:args.resident_id})},reject:()=>reject(Object.assign(Error('owner denied deletion'),{code:'instruction.forbidden'}))});
    if(name==='GetResidentSettings')return new Promise(resolve=>window.finishRead=()=>resolve({}));
    throw Error(name);
   }};
   window.client=PortalResidentService.createClient(transport);window.PortalResidentService={...PortalResidentService,remove:client.remove};
   window.state={username:'alice',principalId:'alice',selectedId:''};
   const deps=Object.fromEntries(PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
   Object.assign(deps,{$:id=>document.getElementById(id),state,esc:s=>s,isCollectionMode:()=>false,alertError:e=>errors.push(e.message),paperApi:{request:async(url,options={})=>{
    calls.push([url,options.method||'GET']);
    if(url==='/paper/api/research/chats'){
     const result=structuredClone({principal:actor,slots:server.slots,legacy:server.legacy,selected:server.slots.find(x=>x.slot_id===server.selected)||null,selected_legacy:server.legacy.find(x=>x.workspace_key===server.selected)||null});
     if(window.holdCatalog){window.holdCatalog=false;return new Promise(resolve=>window.finishCatalog=()=>resolve(result));}return result;
    }
    if(url==='/paper/api/research/chats/select'){
     const body=JSON.parse(options.body),selected=body.slot_id||body.workspace_key;
     const result=server.slots.find(x=>x.slot_id===body.slot_id)||server.legacy.find(x=>x.workspace_key===body.workspace_key);
     if(window.holdSelect){window.holdSelect=false;return new Promise(resolve=>window.finishSelect=()=>{server.selected=selected;resolve(result)});}server.selected=selected;return result;
    }throw Error(url);
   }}});
   window.chat=PortalPaperChat.createChat(deps);let surface=null,target={};
   window.runtime={workspace:{root:document.getElementById('chat'),draftText:()=>document.getElementById('draft').value,attachments:()=>[],setDraftText:value=>document.getElementById('draft').value=value,setStatus:text=>document.getElementById('status').textContent=text},surface:()=>surface,target:()=>target,
    openSurface:async value=>{surface=value;const key=value.workspace_key;target={resident_id:server.slots.find(x=>'research:'+x.slot_id===key)?.resident_id||server.legacy.find(x=>x.workspace_key===key)?.resident_id}},
    closeSurface:()=>{surface=null;target={}}};
   chat.paperChatDescriptor().onShow({panel:{runtime}});
   window.menuContext=null;window.menuClosed=0;window.changed=0;
   const m=Object.fromEntries(PortalResidentMenuResident.dependencies.map(k=>[k,()=>{}]));
   Object.assign(m,{menuState:{get nodeMenuContext(){return menuContext},options:{}},dialog:{close:()=>{menuClosed++}},changed:async()=>changed++,toast:(...v)=>toasts.push(v)});
   const menu=PortalResidentMenuResident.createResident(m);
   document.getElementById('delete').onclick=()=>{const rid=state.researchSlot?.resident_id||server.legacy.find(x=>x.workspace_key===state.researchLegacyKey)?.resident_id;menuContext={residentId:rid,lineageId:'lineage'};window.menuPromise=menu.removeResident({title:'Test assistant'});};
   window.refresh=()=>chat.loadMe();refresh();
  });
  await page.waitForFunction(()=>state.researchSlot?.slot_id==='A');
 }
 const options=()=>page.locator('.paperResearchSelector select option').evaluateAll(rows=>rows.map(x=>x.value));
 async function select(value){await page.locator('.paperResearchSelector select').selectOption(value);await page.waitForFunction(v=>(state.researchSlot?.slot_id||'legacy:'+state.researchLegacyKey)===v,value);await page.waitForFunction(()=>runtime.target().resident_id);}
 await mount({noRecent:true});await select('C');await select('B');
 // Old catalog/read do not delay sending the formal deletion to its owner.
 await page.evaluate(()=>{holdCatalog=true;refresh();holdSelect=true;window.read=client.runtimeSettings('ridB')});
 await page.click('#delete');await page.waitForFunction(()=>menuClosed===1);await page.waitForFunction(()=>state.researchSlot?.slot_id==='C');
 assert(!(await options()).includes('B'));await page.waitForFunction(()=>operations.ridB);
 assert.equal(await page.evaluate(()=>runtime.target().resident_id),undefined);
 assert(await page.evaluate(()=>{try{chat.paperChatDescriptor().options.resolveBeforeSend({},{});return false;}catch(_){return true;}}));
 assert.equal(await page.evaluate(()=>document.activeElement.tagName),'SELECT');
 await page.evaluate(()=>finishCatalog());assert.equal(await page.evaluate(()=>state.researchSlot.slot_id),'C');
 assert(!(await options()).includes('B'));
 await page.evaluate(()=>refresh());await page.waitForFunction(()=>calls.filter(x=>x[0]==='/paper/api/research/chats').length>=3);
 assert.equal(await page.evaluate(()=>state.researchSlot.slot_id),'C');
 await page.evaluate(()=>finishSelect());await page.waitForFunction(()=>runtime.target().resident_id==='ridC');
 await select('A');await select('C'); // Continue using another assistant while deletion waits.
 await page.screenshot({path:'/tmp/resident-delete-pending.png'});
 await page.evaluate(()=>finishRead());await page.waitForFunction(()=>operations.ridB);
 await page.evaluate(()=>{window.duplicate=client.remove({resident_id:'ridB',lineage_id:'lineage'})});
 assert.equal(await page.evaluate(()=>calls.filter(x=>x[0]==='DeleteResident').length),1);
 await page.evaluate(()=>{operations.ridB.reject();duplicate.catch(()=>{})});
 await page.waitForFunction(()=>toasts.length);await page.waitForFunction(()=>Array.from(document.querySelectorAll('select option')).some(x=>x.value==='B'));
 assert.equal(await page.evaluate(()=>state.researchSlot.slot_id),'C');assert.equal(await page.locator('#draft').inputValue(),'keep draft');
 await page.screenshot({path:'/tmp/resident-delete-failed.png'});
 // Delete a noncurrent item through the same service: focus stays unchanged.
 await page.evaluate(()=>{window.other=client.remove({resident_id:'ridA',lineage_id:'lineage'})});await page.waitForFunction(()=>operations.ridA);
 assert(!(await options()).includes('A'));assert.equal(await page.evaluate(()=>state.researchSlot.slot_id),'C');
 await page.evaluate(()=>operations.ridA.resolve());await page.evaluate(()=>other);assert(!(await options()).includes('A'));
 // Legacy is also an MRU target; not list-first A. Empty MRU never picks arbitrarily.
 await mount({legacy:true,noRecent:true});await select('legacy:paper:old');await select('B');await page.click('#delete');
 await page.waitForFunction(()=>state.researchLegacyKey==='paper:old');await page.waitForFunction(()=>runtime.target().resident_id==='ridL');
 assert(!(await options()).includes('B'));await page.evaluate(()=>operations.ridB.resolve());await page.waitForFunction(()=>changed===1);
 await page.click('#delete');await page.waitForFunction(()=>state.researchSlot?.slot_id==='A');
 assert(!(await options()).includes('legacy:paper:old'));await page.evaluate(()=>operations.ridL.resolve());await page.waitForFunction(()=>changed===2);
 await mount({noRecent:true});await page.click('#delete');await page.waitForFunction(()=>menuClosed===1);
 assert.equal(await page.evaluate(()=>state.researchSlot),null);assert.equal(await page.evaluate(()=>runtime.target().resident_id),undefined);
 // A fresh document has no optimistic tombstone: pending server item is shown.
 await mount({serverState:await page.evaluate(()=>server)});
 assert((await options()).includes('A'));
 assert.equal(await page.evaluate(()=>PortalResidentService.deletionState('ridA').deletion_pending),false);
 // A stale selected response cannot revive a target now being deleted.
 await page.evaluate(()=>{holdSelect=true});await page.locator('.paperResearchSelector select').selectOption('C');await page.waitForFunction(()=>window.finishSelect);
 await page.evaluate(()=>{window.lateDelete=client.remove({resident_id:'ridC',lineage_id:'lineage'})});await page.waitForFunction(()=>operations.ridC);
 await page.evaluate(()=>finishSelect());assert.equal(await page.evaluate(()=>state.researchSlot.slot_id),'A');
 await page.evaluate(()=>operations.ridC.resolve());await page.evaluate(()=>lateDelete);
 // A failure from the former account cannot restore its rows or steal focus.
 await page.evaluate(()=>{window.oldRemoval=client.remove({resident_id:'ridB',lineage_id:'lineage'}).catch(()=>{});actor='bob';server={slots:[{slot_id:'Z',resident_id:'ridZ',title:'Bob',state:'bound'}],selected:'Z',legacy:[]};refresh()});
 await page.waitForFunction(()=>state.researchSlot?.slot_id==='Z');await page.waitForFunction(()=>operations.ridB);
 await page.evaluate(()=>operations.ridB.reject());await page.evaluate(()=>oldRemoval);
 assert.deepEqual(await options(),['','Z']);assert.equal(await page.evaluate(()=>state.principalId),'bob');
 await page.screenshot({path:'/tmp/resident-delete-optimistic.png'});
 // Shared-menu host with no Paper mounted retains completion/error callbacks.
 const generic=await browser.newPage();await generic.setContent('<main>Unrelated host</main>');
 for(const script of scripts.slice(0,2))await generic.addScriptTag({content:script});
 await generic.evaluate(()=>{
   window.PortalI18n={t:s=>s,text:(s,...v)=>s.map((a,i)=>a+(v[i]??'')).join('')};window.PortalUI={confirm:async()=>true};window.PortalUIPrimitives={randomUUID:()=> 'generic'};
   let count=0;const c=PortalResidentService.createClient({execute:async()=>{count++;return new Promise(resolve=>window.finish=()=>resolve({resident_id:'generic'}))}});
   window.PortalResidentService={...PortalResidentService,remove:c.remove};window.completed=0;window.dismissed=0;
   const d=Object.fromEntries(PortalResidentMenuResident.dependencies.map(k=>[k,()=>{}]));
   Object.assign(d,{menuState:{nodeMenuContext:{residentId:'generic',lineageId:'lineage'},options:{}},dialog:{close:()=>dismissed++},changed:async()=>completed++});
   window.menu=PortalResidentMenuResident.createResident(d);window.call=menu.removeResident({title:'Generic'});
 });
 await generic.evaluate(()=>call);assert.equal(await generic.evaluate(()=>dismissed),1);assert.equal(await generic.evaluate(()=>completed),0);
 await generic.evaluate(()=>finish());await generic.waitForFunction(()=>completed===1);assert.equal(await generic.evaluate(()=>!!window.PortalPaperChat),false);
 await generic.evaluate(async()=>{const client=PortalResidentService.createClient({execute:async()=>{throw Object.assign(Error('projection pending'),{body:{error:{commit_state:'committed'}}})}});await client.remove({resident_id:'committed-probe',lineage_id:'lineage'}).catch(()=>{});});
 assert.equal(await generic.evaluate(()=>PortalResidentService.deletionState('committed-probe').deleted),true);
 await generic.close();
 console.log('PASS immediate close/filter and wire before old reads; MRU/legacy/empty; late catalog and select fenced; formal selection ack gates send; duplicate wire suppressed; failure restores row not focus; draft; noncurrent deletion; fresh page shows extant server item; account switch isolation');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
