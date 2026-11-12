'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const tick=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function fixture(t){
  const dom=new JSDOM('<main></main>',{url:'https://avatar.fixture.invalid/',runScripts:'outside-only'});
  const w=dom.window,timers=new Map(),reads=[];let sequence=0;
  t.after(()=>w.close());
  const tag=(parts,...values)=>parts.reduce((out,part,index)=>out+part+(values[index]??''),'');
  w.PortalI18n={t:text=>text,html:tag};
  w.PortalUIPrimitives={escapeHtml:value=>String(value??'')};
  // This regression concerns read/cache/event ordering, not the avatar renderer.
  w.PortalResidentAvatar={markup:avatar=>`<i data-avatar="${avatar.token}"></i>`};
  w.setTimeout=(fn,delay)=>{timers.set(++sequence,{fn,delay});return sequence;};
  w.clearTimeout=id=>timers.delete(id);
  w.PortalAPI={get:url=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});reads.push({url,resolve,reject});return promise;}};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_user_avatar.js'),'utf8'));
  const add=()=>{const host=w.document.querySelector('main');host.insertAdjacentHTML('beforeend',w.PortalUserAvatar.messageAvatarHtml('fixture','Fixture'));return host.lastElementChild;};
  const change=token=>w.dispatchEvent(new w.CustomEvent('portal:user-avatar-changed',{detail:{username:'fixture',avatar:{kind:'color',token}}}));
  return {w,reads,timers,add,change,bind:()=>w.PortalUserAvatar.bind(w.document)};
}
test('new avatar event supersedes a pending older read',async t=>{
  const f=fixture(t),node=f.add();f.bind();f.bind();
  assert.equal(f.reads.length,1,'pending requests are shared');
  f.change('new');await tick();
  f.reads[0].resolve({avatar:{kind:'color',token:'old'}});await tick();
  assert.equal(node.querySelector('i')?.dataset.avatar,'new','late read cannot revert an accepted update');
  f.bind();await tick();
  assert.equal(node.querySelector('i')?.dataset.avatar,'new');
  assert.equal(f.reads.length,1,'update event already provides authoritative avatar');
  assert.match(f.w.PortalUserAvatar.messageAvatarHtml('fixture','Fixture'),/data-avatar="new"/);
});
test('stale failure after avatar update does not schedule retry',async t=>{
  const f=fixture(t),node=f.add();f.bind();
  f.change('new');await tick();
  f.reads[0].reject(new Error('old request failed'));await tick();
  assert.equal(node.querySelector('i')?.dataset.avatar,'new');
  assert.equal(f.timers.size,0,'obsolete request failure cannot create retry work');
});
test('live reads still deduplicate and current failure retains recovery',async t=>{
  const f=fixture(t),a=f.add(),b=f.add();f.bind();f.bind();
  assert.equal(f.reads.length,1);
  f.reads[0].reject(new Error('temporary'));await tick();
  assert.equal(f.timers.size,1);
  const timer=[...f.timers.values()][0];f.timers.clear();timer.fn();await tick();
  assert.equal(f.reads.length,2);
  f.reads[1].resolve({avatar:{kind:'color',token:'recovered'}});await tick();
  for(const node of [a,b])assert.equal(node.querySelector('i')?.dataset.avatar,'recovered');
  assert.equal(f.timers.size,0);
});
test('cache eviction alone does not obsolete a connected node pending read',async t=>{
  const f=fixture(t),node=f.add();f.bind();
  const host=f.w.document.querySelector('main');
  for(let i=0;i<201;i++)host.insertAdjacentHTML('beforeend',f.w.PortalUserAvatar.messageAvatarHtml('other_'+i,'Other'));
  f.bind();
  assert.equal(f.reads.length,202);
  f.reads[0].resolve({avatar:{kind:'color',token:'valid'}});await tick();
  assert.equal(node.querySelector('i')?.dataset.avatar,'valid','bounded cache eviction is not a newer update');
});
