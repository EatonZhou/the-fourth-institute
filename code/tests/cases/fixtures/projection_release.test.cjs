const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');

test('release bridge limits operations and carries trusted panel identity',async()=>{
  const dom=new JSDOM('',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
  w.matchMedia=()=>({matches:false,addEventListener(){}});w.PortalWorkbench={registerPanelPackage(){}};
  w.PortalI18n={t:v=>v};const replies=[],calls=[];
  const child={postMessage:reply=>replies.push(reply)};
  const record={app:{workbench_id:'workbench_actual'},frame:{contentWindow:child},page:{},panelId:'packaging'};
  w.testActive=new Map([['channel',record]]);
  w.PortalProjections={request:async(name,payload)=>{calls.push({name,payload});return {run:{run_id:'run_actual',state:'queued'}};}};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/projection_host.js'),'utf8').replace('const active = new Map();','const active = window.testActive;'));
  const send=async payload=>{w.dispatchEvent(new w.MessageEvent('message',{source:child,data:{type:'portal-projection-request',channel:'channel',requestId:'release',method:'release',payload}}));await new Promise(resolve=>setImmediate(resolve));return replies.at(-1);};
  try{
    const response=await send({operation:'runs.start',id:'forged',panel:'forged',arguments:{run_id:'run_actual'}});
    assert.equal(response.result.run.state,'queued');assert.equal(calls.length,1);
    assert.equal(calls[0].name,'release');assert.equal(calls[0].payload.id,'workbench_actual');assert.equal(calls[0].payload.panel,'packaging');
    await send({operation:'targets',arguments:{snapshot:{target_platforms:['windows']}}});
    assert.equal(calls[1].payload.operation,'targets');
    assert.equal((await send({operation:'http',arguments:{url:'http://internal/'}})).code,'projection.release_operation');
    record.panelId='architecture';assert.equal((await send({operation:'runs.list'})).code,'projection.release_context');
    record.panelId='packaging';record.preview=true;assert.equal((await send({operation:'runs.list'})).code,'projection.release_context');
    assert.equal(calls.length,2);
  }finally{w.close();}
});

test('release entry and assets stay scoped to the published packaging surface',async()=>{
  const dom=new JSDOM('',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
  w.matchMedia=()=>({matches:false,addEventListener(){}});w.PortalWorkbench={registerPanelPackage(){}};
  w.PortalI18n={t:v=>v};
  const requests=[],fetches=[];
  w.PortalProjections={request:async name=>{requests.push(name);throw Error('stop before mount');}};
  w.fetch=async url=>{fetches.push(String(url));return {ok:true,text:async()=>'',json:async()=>({})};};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/projection_host.js'),'utf8')
    .replace('Object.freeze({ open, mount, createPanel: createContentPanel })','Object.freeze({ open, mount, createPanel: createContentPanel, applicationAssets })'));
  try{
    await assert.rejects(w.PortalProjectionHost.open(w.document.body,'workbench_actual',{region:'packaging'}),/stop before mount/);
    assert.equal(requests[0],'detail?id=workbench_actual&published=true&panel=packaging');
    const app={workbench_id:'workbench_actual',revision:3,published_revision:2,release_scope:'packaging',application_build:{status:'ready',artifact_hash:'a'.repeat(64),entries:{packaging:'packaging.html'},files:{'packaging.html':{content_type:'text/html'},'app.js':{content_type:'text/javascript'}}}};
    const published=w.PortalProjectionHost.applicationAssets(app,'packaging',false);
    await published.read('app.js');await published.libraries();
    assert.match(fetches[0],/\/release-assets\/workbench_actual\/2\//);
    assert.equal(fetches[1],'/api/workbenches/release-libraries/workbench_actual/2');
    await assert.rejects(published.read('../other.html'),/本修订/);
    await w.PortalProjectionHost.applicationAssets(app,'packaging',true).read('app.js');
    assert.match(fetches[2],/\/assets\/workbench_actual\/3\//);
    delete app.release_scope;
    await w.PortalProjectionHost.applicationAssets(app,'packaging',false).libraries();
    assert.equal(fetches[3],'/api/workbenches/libraries/workbench_actual/2');
  }finally{w.close();}
});
