const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
test('packaging is a persistent development sidebar page separate from the development tabs',async()=>{
  const dom=new JSDOM('<main id="root"></main>',{url:'http://localhost/development?view=packaging',runScripts:'outside-only'});
  const w=dom.window,calls=[],visibility=[];let railOptions,railViews;
  w.PortalI18n={t:x=>x,definition:x=>x,text:(strings,...values)=>strings.reduce((s,x,i)=>s+x+(values[i]||''),'')};
  w.PortalAPI={get:async()=>({views:[{id:'wall',label:'WALL'},{id:'test',label:'Test'}]})};
  w.PortalWorkbench={createActivityRail:options=>{railOptions=options;return{setItems(){},setActiveView(){},setViews:v=>{railViews=v;},destroy(){}};}};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/development_workspace.js'),'utf8'));
  const workspace=w.PortalDevelopmentWorkspace.mount(w.document.getElementById('root'),{openDevelopment:async(root,options)=>{
    calls.push({view:root.dataset.developmentView,options});
    const controller={setVisible:value=>visibility.push([root.dataset.developmentView,value]),focusChat(){},destroy(){}};
    options.onMounted(controller);return controller;
  }});
  try {
    await workspace.selectView('packaging');
    assert.ok(railOptions.views.some(x=>x.id==='packaging'));
    assert.ok(railViews.some(x=>x.id==='packaging'));
    assert.equal(calls[0].options.region,'packaging');
    assert.equal(w.document.querySelector('[data-development-view="packaging"]').hidden,false);
    await workspace.selectView('development',true);
    assert.deepEqual(Array.from(calls.find(x=>x.view==='development').options.excludeRegions),['packaging']);
    assert.equal(w.document.querySelector('[data-development-view="packaging"]').hidden,true);
    await workspace.selectView('packaging',true);
    assert.equal(calls.filter(x=>x.view==='packaging').length,1);
    assert.equal(new URL(w.location.href).searchParams.get('view'),'packaging');
    assert.ok(visibility.some(([name,visible])=>name==='packaging'&&!visible));
  } finally {workspace.destroy();dom.window.close();}
});
