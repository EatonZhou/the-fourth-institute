const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

test('host protects pending or failed sandbox state writes until a successful retry', async () => {
  const dom = new JSDOM('',{url:'http://localhost',runScripts:'outside-only'}), w=dom.window;
  w.matchMedia=()=>({matches:false,addEventListener(){}});
  w.PortalWorkbench={registerPanelPackage(){}};
  w.PortalI18n={t:value=>value};
  const replies=[]; let resolveWrite, rejectWrite;
  const child={postMessage:reply=>replies.push(reply)};
  const record={app:{workbench_id:'workbench_test'},frame:{contentWindow:child},page:{},panelId:'packaging'};
  w.testActive=new Map([['channel',record]]);
  w.PortalProjections={request:(name,payload)=>{
    if(name.startsWith('state?'))return Promise.resolve({});
    assert.equal(name,'state');assert.equal(payload.panel,'packaging');
    return new Promise((resolve,reject)=>{resolveWrite=resolve;rejectWrite=reject;});
  }};
  const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/projection_host.js'),'utf8');
  w.eval(source.replace('const active = new Map();','const active = window.testActive;'));
  const guarded=()=>{const e=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(e);return e.defaultPrevented;};
  const send=payload=>w.dispatchEvent(new w.MessageEvent('message',{source:child,data:{type:'portal-projection-request',channel:'channel',requestId:'state-test',method:'state',payload}}));
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  try {
    assert.equal(guarded(),false);
    send({});await tick();assert.equal(guarded(),false);
    send({value:{checklist:{notes:'draft'}}});assert.equal(guarded(),true);
    rejectWrite(Error('save failed'));await tick();assert.equal(guarded(),true);
    assert.equal(replies.at(-1).error,'save failed');
    send({value:{checklist:{notes:'draft'}}});assert.equal(guarded(),true);
    resolveWrite({});await tick();assert.equal(guarded(),false);
    assert.equal(replies.at(-1).error,undefined);
  } finally {w.close();}
});
