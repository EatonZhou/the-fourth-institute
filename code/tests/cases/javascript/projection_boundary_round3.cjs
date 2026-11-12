'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; }

for (const region of [false, true]) {
  test(`panel disposal cancels nested ${region ? 'region' : 'main'} facade mount before registration`, async () => {
    const dom = new JSDOM('<main></main>', {url:'https://fixture.invalid/',runScripts:'outside-only'});
    const w = dom.window, response = deferred(); let reads=0, registrations=0;
    w.PortalProjections = { request() { reads++; return response.promise; } };
    w.PortalI18n = {t:String}; w.matchMedia=()=>({addEventListener(){}});
    w.PortalWorkbench = {registerPanelPackage(){}, model:{stack:()=>({}),split:()=>({})}, create(){ registrations++; throw Error('obsolete registration'); }};
    w.eval(fs.readFileSync(path.join(root,'projection_host.js'),'utf8'));
    const app={workbench_id:'workbench_111111111111',title:'nested', bindings:{main:{lineage_id:'lineage_fixture',residents:{facade:'resident_fixture'}}},
      manifest:{...(region?{}:{chat:{binding:'main'}}),sidebar:[{id:'a',title:'A',source:'a.html',...(region?{chat:{binding:'main'}}:{})}]},panels:{'a.html':'<p>fixture</p>'}};
    try {
      const panel=w.PortalProjectionHost.createPanel({root:w.document.querySelector('main'),app});
      const result=panel.ready.catch(e=>e);
      assert.equal(reads,1);
      panel.destroy(); panel.destroy();
      response.resolve({resident_id:'resident_fixture',lineage_id:'lineage_fixture'});
      const error=await result;
      assert.equal(registrations,0,'destroyed outer panel must never register a nested workbench');
      assert.equal(error.code,'projection.cancelled');
      assert.equal(w.document.querySelectorAll('.projectionWorkbenchShell').length,0);
    } finally {dom.window.close();}
  });
}

function directory() {
  const dom=new JSDOM('',{url:'https://fixture.invalid/audit',runScripts:'outside-only'}), w=dom.window;
  let identity={username:'alice',principal:{kind:'user',id:'user_a'}};
  const requests=[], histories=[], notices=[], packages=[];
  w.PortalIdentity={peek:()=>identity}; w.PortalI18n={t:String};
  w.PortalUI={toast:(text)=>notices.push(text)};
  w.PortalWorkbench={registerPanelPackage(value){packages.push(value);}};
  w.fetch=(url,options)=>{const r=deferred();requests.push({...r,url,options});return r.promise;};
  w.eval(fs.readFileSync(path.join(root,'portal_projections.js'),'utf8'));
  const wb={root:{dataset:{portalWorkbenchId:'fixture'}},setEditorTypes:items=>histories.push(JSON.parse(JSON.stringify(items)))};
  const reply=(i,title)=>requests[i].resolve({ok:true,json:async()=>({workbenches:title?[{workbench_id:'workbench_111111111111',title,published_revision:1}]:[]})});
  return {w,wb,requests,histories,notices,packages,reply,
    change(value){identity=value;w.dispatchEvent(new w.CustomEvent('portal:identity-changed',{detail:{identity}}));},
    close:()=>dom.window.close()};
}

for (const waiting of [false, true]) {
  test(`lifetime nested workspace ${waiting ? 'waiting ready' : 'mounted'} releases once and hide preserves it`, async()=>{
    const dom=new JSDOM('<main></main>',{url:'https://fixture.invalid/',runScripts:'outside-only'}),w=dom.window;
    const ready=deferred();let created=0,destroyed=0;const visibility=[];
    w.PortalProjections={request(){throw Error('no facade requested');}};
    w.PortalI18n={t:String};w.matchMedia=()=>({addEventListener(){}});
    w.PortalWorkbench={registerPanelPackage(){},model:{stack:()=>({})},create(){created++;return {
      getPanel:()=>({isVisible:()=>waiting,ready:ready.promise}),setVisible:v=>visibility.push(v),destroy:()=>destroyed++
    };}};
    w.eval(fs.readFileSync(path.join(root,'projection_host.js'),'utf8'));
    try {
      const panel=w.PortalProjectionHost.createPanel({root:w.document.querySelector('main'),app:{workbench_id:'workbench_111111111111',title:'nested',bindings:{},manifest:{sidebar:[{id:'a',title:'A',source:'a.html'}]},panels:{'a.html':'fixture'}}});
      const result=panel.ready.catch(e=>e);panel.hide();await tick();
      assert.equal(created,1);assert.equal(destroyed,0,'hide must preserve nested mount');
      if (!waiting) {await result;panel.show();assert.equal(visibility.at(-1),true);}
      panel.destroy();panel.destroy();
      assert.equal(destroyed,1,'destroy releases a waiting or mounted workspace immediately, once');
      ready.resolve();const error=await result;
      if(waiting)assert.equal(error.code,'projection.cancelled');
      assert.equal(destroyed,1);
      assert.equal(w.document.querySelectorAll('.projectionWorkbenchShell').length,0);
    }finally{dom.window.close();}
  });
}

test('identity directory metadata clears immediately and late old result cannot repopulate', async()=>{
  const h=directory(), type='projection:workbench_111111111111';
  try {
    const first=h.w.PortalProjections.lineage(h.wb,[{type:'native'}],'lineage_fixture');h.reply(0,'Alice only');await first;
    assert.equal(h.w.PortalProjections.resource(type).title,'Alice only');
    assert.equal(h.w.PortalProjections.descriptor({instanceId:'fixture',type}).title,'Alice only');
    h.w.dispatchEvent(new h.w.Event('portal:workbenches-changed')); assert.equal(h.requests.length,2);
    h.change({username:'bob',principal:{kind:'user',id:'user_b'}});
    assert.notEqual(h.w.PortalProjections.resource(type).title,'Alice only','identity invalidation clears old metadata immediately');
    assert.equal(h.requests.length,3,'new identity performs one fresh directory read');
    assert.deepEqual(h.histories.at(-1),[{type:'native'}]);
    assert.equal(h.packages[0].presentation.title,'投影工作台','global package metadata contains no actor-specific title');
    h.reply(2,''); await tick(); h.reply(1,'Late Alice');await tick();
    assert.equal(h.w.PortalProjections.resource(type).title,'投影工作台');
    assert.deepEqual(h.histories.at(-1),[{type:'native'}]);
    h.change({username:'bob',principal:{kind:'user',id:'user_b'},preferences:{theme:'dark'}});
    assert.equal(h.requests.length,3,'same actor preference event does not repeat reads');
    assert(h.requests.every(r=>r.options.method===undefined&&r.options.credentials==='same-origin'));
  } finally {h.close();}
});

test('identity directory failure can retry; previous actor failure stays silent',async()=>{
  const h=directory();
  try {
    const old=h.w.PortalProjections.lineage(h.wb,[],'lineage_fixture');
    h.change({username:'resident_a',principal:{kind:'resident',id:'resident_a'}});
    assert.equal(h.requests.length,2);
    h.requests[0].reject(Error('old actor failure'));await old;assert.equal(h.notices.length,0);
    h.requests[1].reject(Error('current failure'));await tick();assert.equal(h.notices.length,1);
    const retry=h.w.PortalProjections.lineage(h.wb,[],'lineage_fixture');assert.equal(h.requests.length,3);
    h.reply(2,'Current');await retry;
    assert.equal(h.w.PortalProjections.resource('projection:workbench_111111111111').title,'Current');
    delete h.wb.root.dataset.portalWorkbenchId;
    h.change({}); assert.equal(h.requests.length,3,'destroyed workbench is not refreshed on logout');
    assert.equal(h.w.PortalProjections.resource('projection:workbench_111111111111').title,'投影工作台');
  } finally {h.close();}
});
