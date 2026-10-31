const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

test('projection context forwards live read-only theme candidates without changing appearance',async()=>{
 const dom=new JSDOM('',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window,replies=[];
 w.matchMedia=()=>({matches:false,addEventListener(){}});w.PortalWorkbench={registerPanelPackage(){}};
 w.PortalI18n={t:value=>value};
 let options=[{id:'future',label:'未来主题'}],changes=0;
 w.PortalTheme={options:()=>options.map(v=>({...v})),setMode:()=>changes++};
 const child={postMessage:reply=>replies.push(reply)};
 w.testActive=new Map([['channel',{app:{workbench_id:'workbench_test'},frame:{contentWindow:child},panelId:'packaging'}]]);
 const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/projection_host.js'),'utf8');
 w.eval(source.replace('const active = new Map();','const active = window.testActive;'));
 const read=async()=>{w.dispatchEvent(new w.MessageEvent('message',{source:child,data:{type:'portal-projection-request',channel:'channel',requestId:'theme-test',method:'context'}}));await new Promise(resolve=>setImmediate(resolve));return replies.at(-1).result;};
 try{
   assert.deepEqual(JSON.parse(JSON.stringify((await read()).themes)),options);
   options=[{id:'future',label:'Future theme'}];
   assert.deepEqual(JSON.parse(JSON.stringify((await read()).themes)),options);
   assert.equal(changes,0);delete w.PortalTheme;assert.equal((await read()).themes,null);
 }finally{w.close();}
});

test('region native chat resolves its own facade and sends saved region context', async () => {
  const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost/development?region=packaging',runScripts:'outside-only'});
  const w=dom.window, shown=[],requests=[],visibility=[]; let definitions;
  w.matchMedia=()=>({matches:false,addEventListener(){}});
  const main={resident_id:'resident_111111111111',lineage_id:'lineage_111111111111'};
  const packaging={resident_id:'resident_222222222222',lineage_id:main.lineage_id};
  w.PortalI18n={t:x=>x,locale:()=> 'zh'};
  w.PortalProjections={request:async(name,payload)=>{
    requests.push({name,payload});
    if(name==='facade')return payload.panel==='packaging'?packaging:main;
    if(name.startsWith('state?'))return {checklist:{version:'v1',lineages:[{id:main.lineage_id,history:true}]}};
    throw new Error(name);
  }};
  w.PortalWorkbench={registerPanelPackage(){},model:{stack:(ids,active)=>({ids,active}),split:(...args)=>args},
    create:options=>{definitions=options;return {
      getPanel:id=>options.initialEditors.some(x=>x.id===id)?{ready:Promise.resolve(),isVisible:()=>true}:null,
      getLayout:()=>({type:'split',children:[{id:'content',type:'stack',children:[{panelId:'packaging'},{panelId:'chat-packaging'}]},{id:'conversation',type:'stack',children:[{panelId:'facade'}]}]}),
      focusPanel:()=>true,showPanel:(id,placement)=>{shown.push({id,placement});return true;},setVisible:value=>visibility.push(value),destroy(){},contextStore:{get:()=>null,revision:0}};},
    createActivityRail:()=>({setItems(){},setActiveView(){},destroy(){}})};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/projection_host.js'),'utf8'));
  const app={workbench_id:'workbench_111111111111',title:'开发',builtin:'development',url:'/development',bindings:{main:{...main,residents:{facade:main.resident_id}},packaging:{...packaging,residents:{facade:packaging.resident_id}}},manifest:{schema:'tfi.projection.v2',chat:{binding:'main',role:'facade'},sidebar:[{id:'architecture',title:'架构',source:'a.html'},{id:'packaging',title:'打包发布',source:'b.html',chat:{binding:'packaging',role:'facade'}}],context:[]}};
  try {
    const mounted=await w.PortalProjectionHost.mount(w.document.getElementById('root'),app);
    assert.equal(definitions.initialEditors.find(x=>x.id==='facade').options.target.resident_id,main.resident_id);
    const region=definitions.initialEditors.find(x=>x.id==='chat-packaging');
    assert.equal(region.type,'chat');assert.equal(region.options.target.resident_id,packaging.resident_id);
    const context=await region.options.submissionContext(packaging);
    assert.equal(JSON.parse(context.context_text).saved_state.checklist.version,'v1');
    await assert.rejects(region.options.submissionContext(main),/绑定已变化/);
    assert.deepEqual(JSON.parse(JSON.stringify(shown)),[{id:'chat-packaging',placement:{stackId:'conversation',zone:'center'}}]);
    assert.ok(requests.some(x=>x.payload?.panel==='packaging'));
    mounted.setVisible(false); mounted.setVisible(true);
    assert.deepEqual(visibility,[false,true]);
    mounted.destroy();
    requests.length=0;
    const isolated=await w.PortalProjectionHost.mount(w.document.getElementById('root'),app,{region:'packaging',externalRail:true});
    assert.deepEqual(Array.from(definitions.initialEditors,x=>x.id),['packaging','chat-packaging']);
    assert.match(definitions.id,/-packaging$/);
    assert.ok(requests.every(x=>x.name!=='facade'||x.payload.panel==='packaging'));
    isolated.destroy();
    const mainView=await w.PortalProjectionHost.mount(w.document.getElementById('root'),app,{excludeRegions:['packaging'],externalRail:true});
    assert.deepEqual(Array.from(definitions.initialEditors,x=>x.id),['architecture','facade']);
    mainView.destroy();
  } finally {dom.window.close();}
});
