'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const tick = async () => { for(let i=0;i<6;i++) await new Promise(resolve=>setImmediate(resolve)); };
function fixture(t) {
  const dom = new JSDOM('<section><button data-workspace-history-search>Search</button></section>', {url:'https://chat.fixture.invalid/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window, root=w.document.querySelector('section'), timers=new Map(), observers=[];
  const Observer=w.MutationObserver;
  w.MutationObserver=class extends Observer {constructor(fn){super(fn);observers.push(this);}};
  const nativeSet=w.setTimeout.bind(w), nativeClear=w.clearTimeout.bind(w);
  let id=0;
  w.setTimeout=(fn,ms)=>ms===220?(timers.set(--id,fn),id):nativeSet(fn,ms);
  w.clearTimeout=value=>timers.delete(value)||nativeClear(value);
  w.PortalCompatibility={identifier:key=>'fixture_'+key};
  const tag=(parts,...values)=>parts.reduce((out,part,index)=>out+part+(values[index]??''),'');
  w.PortalI18n={t:value=>value,html:tag,text:tag};
  for(const file of ['portal_ui_primitives.js','portal_overlay.js','chat_workspace_search.js']) w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',file),'utf8'));
  const owner=w.PortalChatWorkspaceSearch.createSearch({escapeHtml:w.PortalUIPrimitives.escapeHtml,messageRenderKey:item=>item.id,messageRole:item=>item.role,messageTitle:()=>''});
  const handles=[], deliveries=[];
  function create(search) {
    const handle=owner.createConversationSearch({root,scope:()=> 'fixture', messages:()=>[],rendering:()=>({}),search,loadOlder:async()=>{},canLoadOlder:()=>false,reveal:async()=>{},onResults:rows=>deliveries.push(rows)});
    handles.push(handle);return handle;
  }
  t.after(async()=>{handles.forEach(handle=>handle.destroy());observers.forEach(observer=>observer.disconnect());await tick();w.close();});
  const type=(input,value)=>{input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}));};
  return {w,root,create,deliveries,timers,type,trigger:root.querySelector('button'),async pulse(){const queue=[...timers.values()];timers.clear();queue.forEach(fn=>fn());await tick();}};
}
test('destroy releases reused trigger without resurrecting the old search', async t=>{
  const f=fixture(t), old=f.create();
  old.destroy();
  const before=f.deliveries.length;
  f.trigger.click();await tick();
  assert.equal(f.deliveries.length,before,'destroyed search cannot receive trigger clicks');
  assert.equal(f.w.document.querySelectorAll('.portalConversationSearch').length,0);
  f.create();f.trigger.click();await tick();
  assert.equal(f.w.document.querySelectorAll('.portalConversationSearch').length,1,'remount opens only the live search');
});
test('destroy releases detached input and public update callbacks',async t=>{
  const f=fixture(t), handle=f.create(async()=>({messages:[]}));
  const input=f.root.querySelector('input');
  handle.destroy();const before=f.deliveries.length;
  f.type(input,'late');await f.pulse();handle.update(true);
  assert.equal(f.deliveries.length,before,'detached input/update cannot render a destroyed search');
  assert.equal(f.timers.size,0);
});
test('late response after destroy stays silent and active instance still searches',async t=>{
  const f=fixture(t);let resolve,reads=0;
  const handle=f.create(()=>{reads++;return new Promise(done=>{resolve=done;});});
  f.trigger.click();await tick();
  f.type(f.w.document.querySelector('input'),'sample');await f.pulse();
  assert.equal(reads,1);
  handle.destroy();const before=f.deliveries.length;
  resolve({messages:[{id:'late',content:'late'}]});await tick();
  assert.equal(f.deliveries.length,before);
  f.create(async()=>({messages:[{id:'live',content:'sample'}]}));
  f.trigger.click();await tick();
  f.type(f.w.document.querySelector('input'),'sample');await f.pulse();
  assert.equal(f.w.document.querySelector('[data-search-message]')?.dataset.searchMessage,'live');
});
