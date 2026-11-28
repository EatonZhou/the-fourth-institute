'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(`<form id="nodeForm">
  <div id="nodeFormError" hidden></div><p id="nodeFormRuntimeStatus" hidden></p>
  <input id="nodeTitleInput" value="Fixture" />
  <select id="nodeBackendSelect"><option value="codex">Codex</option></select>
  <select id="nodeAccountSelection"><option value="auto">Auto</option><option value="fixed">Fixed</option></select>
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
w.PortalAPI = {get:url=>{if(url.endsWith('/agent-backends')) return Promise.resolve(state.agentBackendCatalog);const id=new URL(url,'http://fixture').searchParams.get('account_id');return pending.get(id).promise;}};
for (const name of ['portal_lineage_catalogs.js','hatg_resident_dialogs.js']) {
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',name),'utf8'));
}
const deps = Object.fromEntries(w.PortalHatgResidentDialogs.dependencies.map(name=>[name,()=>{}]));
const state = {lineage:{lineage_id:'fixture'},nodeRouteChosen:true,accountSelection:{},agentBackendCatalog:{backends:[{
  backend:'codex',runtime_settings:['model','reasoning_effort','service_tier'],routes:[
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
    await ui.openNodeModal();
    assert.equal($id('nodeAccountSelection').value,'fixed','opening creation defaults to manual, even after previous auto choice');
    $id('nodeAccountSelection').value='auto';
    await ui.openNodeModal();
    assert.equal($id('nodeAccountSelection').value,'fixed');
    assert.equal($id('nodeBackendSelect').value,'');
    assert.equal($id('nodeRouteSelect').value,'');
    assert.equal($id('nodeModelSelect').value,'');
    assert.equal($id('nodeSubmitBtn').disabled,true);
    $id('nodeBackendSelect').value='codex';
    state.agentBackendCatalog.backends[0].recommended_route='two';
    ui.renderNodeProviderFields({type:'change',target:$id('nodeBackendSelect')});
    $id('nodeTitleInput').value='Fixture';
    state.nodeRouteChosen=true; // User route changes below follow the normal change listener.
    for (const [file,id] of [['hatg-panel.html','nodeAccountSelection'],['lineage.html','residentAccountSelection']]) {
      const document=new JSDOM(fs.readFileSync(path.resolve(__dirname,'../../../management_portal',file),'utf8'));
      assert.equal(document.window.document.getElementById(id).value,'fixed','fresh page uses manual account selection');
      document.window.close();
    }
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
    pending.get('codex_two').resolve({models:['model-x','model-y'],default_model:'model-x',efforts_by_model:{'model-x':['high']},
      service_tiers_by_model:{'model-x':['priority',{id:'ultrafast'}]}});
    await tick();
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'false');
    assert.equal($id('nodeModelSelect').value,'model-x');
    assert.equal($id('nodeSubmitBtn').disabled,false);
    assert.equal($id('nodeFormError').hidden,true);
    const tier = $id('nodeServiceTierInput');
    assert.deepEqual(Array.from(tier.options, option => option.text), ['Standard (default)', 'Fast', 'Ultra-fast']);
    tier.value = 'ultrafast';
    let submitted;
    state.graphController = {getPlacementGeometry: () => ({})};
    deps.societyService.createNode = async request => { submitted = request; throw Error('fixture write outcome'); };
    await ui.submitNodeForm({preventDefault(){}});
    assert.equal(submitted.settings.service_tier, 'ultrafast');
    $id('nodeModelSelect').value = 'model-y';
    ui.renderNodeProviderFields();
    assert.equal(tier.value, '');
    assert.equal(tier.disabled, true);
    state.agentBackendCatalog.creation_default = {backend:'codex', backend_route_id:'three', model:'remembered'};
    pending.set('codex_three',deferred());
    await ui.openNodeModal();
    assert.equal($id('nodeRouteSelect').value,'three');
    pending.get('codex_three').resolve({models:['first','remembered'],default_model:'first'});
    await tick();
    assert.equal($id('nodeModelSelect').value,'remembered');
    $id('nodeModelSelect').value='first';ui.renderNodeProviderFields({type:'change'});
    assert.equal($id('nodeModelSelect').value,'first');
    const backend = $id('nodeBackendSelect');
    const change = {type:'change', target:backend};
    const claude = {backend:'claude', recommended_route:'b', runtime_settings:['model'],
      routes:['a','b'].map(id => ({label:id, backend_route_id:id, models:['m'], default_model:'m'}))};
    state.agentBackendCatalog.backends.push(claude);
    backend.add(new w.Option('Claude', 'claude'));
    backend.value='claude';ui.renderNodeProviderFields(change);
    assert.equal($id('nodeRouteSelect').value,'b');
    claude.recommended_route=null;
    backend.value='codex';ui.renderNodeProviderFields(change);
    backend.value='claude';ui.renderNodeProviderFields(change);
    assert.equal($id('nodeRouteSelect').value,'');
    assert.equal($id('nodeSubmitBtn').disabled,true);
    $id('nodeRouteSelect').value='a';ui.renderNodeProviderFields({type:'change',target:$id('nodeRouteSelect')});
    ui.renderNodeProviderFields();
    assert.equal($id('nodeRouteSelect').value,'a');
    $id('nodeTitleInput').value='Manual account';
    assert.equal($id('nodeSubmitBtn').disabled,false);
    await ui.submitNodeForm({preventDefault(){}});
    assert.equal(submitted.account_id,'a');
    state.nodeSubmitBusy=true;ui.renderNodeProviderFields();
    assert.equal($id('nodeSubmitBtn').disabled,true,'catalog refresh cannot enable a second write');
    assert.equal($id('nodeForm').getAttribute('aria-busy'),'true');
    console.log('pending catalog, current-account errors, recovery and submission ownership verified');
  } finally {w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
