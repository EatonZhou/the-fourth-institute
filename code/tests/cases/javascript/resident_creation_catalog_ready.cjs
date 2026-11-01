'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(`<form id="nodeForm">
  <div id="nodeFormError" hidden></div><p id="nodeFormRuntimeStatus" hidden></p>
  <input id="nodeTitleInput" value="Fixture" />
  <select id="nodeBackendSelect"><option value="codex">Codex</option></select>
  <label data-node-provider-setting><select id="nodeRouteSelect"></select></label>
  <label data-node-provider-setting><select id="nodeModelSelect"></select></label>
  <label data-node-provider-setting><select id="nodeEffortSelect"></select></label>
  <label><select id="nodeServiceTierInput"><option value="">Standard</option></select></label>
  <button id="nodeSubmitBtn" type="submit">Create</button></form>`, {runScripts:'outside-only'});
const w = dom.window, $id = id => w.document.getElementById(id);
const pending = new Map();
const deferred = () => {let resolve, reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick = async () => {for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
w.PortalI18n = {t:String,text:(parts,...values)=>parts.reduce((out,p,i)=>out+p+(values[i]??''),'')};
w.PortalAPI = {get:url=>{const id=new URL(url,'http://fixture').searchParams.get('account_id');return pending.get(id).promise;}};
for (const name of ['portal_lineage_catalogs.js','hatg_resident_dialogs.js']) {
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',name),'utf8'));
}
const deps = Object.fromEntries(w.PortalHatgResidentDialogs.dependencies.map(name=>[name,()=>{}]));
const state = {lineage:{lineage_id:'fixture'},nodeRouteChosen:true,accountSelection:{},agentBackendCatalog:{backends:[{
  backend:'codex',runtime_settings:['model','reasoning_effort'],routes:[
    {backend_route_id:'two',account_id:'codex_two',label:'two'},
    {backend_route_id:'three',account_id:'codex_three',label:'three'},
  ],
}]}};
let creates=0;
Object.assign(deps,{$id,state,setBusy:(button,busy)=>{button.disabled=busy;},societyService:{createNode:async()=>{creates++;throw Error('fixture write outcome');}}});
const ui = w.PortalHatgResidentDialogs.createResidentDialogs(deps);
(async()=>{
  try {
    pending.set('codex_two',deferred());pending.set('codex_three',deferred());
    ui.renderNodeProviderFields();
    assert.equal($id('nodeSubmitBtn').disabled,true,'an empty pending model select must not offer Create');
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'true');
    assert.equal($id('nodeFormRuntimeStatus').hidden,false);
    await ui.submitNodeForm({preventDefault(){}});assert.equal(creates,0);
    // Switch accounts while the first catalog is still outstanding.
    $id('nodeRouteSelect').value='three';ui.renderNodeProviderFields();
    pending.get('codex_two').reject(Error('old route failed'));await tick();
    assert.equal($id('nodeFormError').hidden,true,'old route failure cannot contaminate current choice');
    pending.get('codex_three').reject(Error('current route unavailable'));await tick();
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'false');
    assert.equal($id('nodeFormRuntimeStatus').hidden,true);
    assert.match($id('nodeFormError').textContent,/current route unavailable/);
    assert.equal($id('nodeSubmitBtn').disabled,true);
    pending.set('codex_two',deferred());$id('nodeRouteSelect').value='two';ui.renderNodeProviderFields();
    pending.get('codex_two').resolve({models:['model-x'],default_model:'model-x',efforts_by_model:{'model-x':['high']}});
    await tick();
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'false');
    assert.equal($id('nodeModelSelect').value,'model-x');
    assert.equal($id('nodeSubmitBtn').disabled,false);
    assert.equal($id('nodeFormError').hidden,true);
    state.nodeSubmitBusy=true;ui.renderNodeProviderFields();
    assert.equal($id('nodeSubmitBtn').disabled,true,'catalog refresh cannot enable a second write');
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'true');
    console.log('pending catalog, current-account errors, recovery and submission ownership verified');
  } finally {w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
