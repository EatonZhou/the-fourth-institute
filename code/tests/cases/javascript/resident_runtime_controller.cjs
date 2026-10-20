const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const repo=require('node:path').resolve(__dirname,'../../..');const src=repo+'/management_portal/static/portal_resident_runtime_controller.js';
assert(fs.existsSync(src),'four views need a shared resident_id runtime workflow owner');
const listeners={},window={addEventListener:(n,f)=>listeners[n]=f,PortalI18n:{t:s=>s},PortalUI:{toast(){}},PortalUIPrimitives:{randomUUID:()=>String(++serial)}};let serial=0;
vm.runInNewContext(fs.readFileSync(src,'utf8'),{window,console,JSON,Map,Set,Promise,Error});
const calls=[],settings={a:{model:'m',prompt_context:{history:{enabled:true}}},b:{model:'m'}},permissions={a:{edit:true,restart:true},b:{edit:true,restart:true}},watch={};let pending,hold;let runtime,restart;
const detail=id=>({resident:{resident_id:id,backend:'codex'},allows:{RestartSession:permissions[id].restart},execution_account:{account_id:'acct'},chat_workspace:{actions:permissions[id].edit?{runtime_settings:{instruction:'UpdateResidentSettings'}}:{}}});
const service={projection:async({residentId:id})=>{calls.push(['projection',id]);if(hold)await hold;return detail(id)},runtimeSettings:async id=>{calls.push(['read',id]);return {settings:settings[id]}},runtimeCatalog:async()=>({models:['m'],default_model:'m'}),updateRuntimeSettings:async(id,changes)=>{calls.push(['save',id,changes]);settings[id]={...settings[id],...changes};return {settings:settings[id]}},sessionRestartOptions:async id=>({resident_id:id,accounts:{},source:{provider:'codex'}}),observeSessionRestart:(id,fn)=>{watch[id]=fn;return()=>delete watch[id]},restartSession:async values=>{calls.push(['restart',values]);return {status:'accepted'}},cancelSessionRestart:async values=>calls.push(['cancel',values])};
const dialogs={settings:options=>({open:(...a)=>runtime={args:a,options},close:()=>options.onClose(),destroy(){}}),restart:options=>({open:(...a)=>restart={args:a,options},close:()=>options.onClose(),destroy(){}})};
const ctrl=window.PortalResidentRuntimeController.create({service,dialogs});
async function runtimeSubmissionRegressions() {
  const cases = [
    {name: 'runtime refresh keeps restart usable', event: {resident_changes: {a: ['runtime']}}},
    {name: 'snapshot refresh keeps restart usable', event: {snapshot_required: true}},
    {name: 'identity notification with unchanged route keeps restart usable', event: {resident_changes: {a: ['identity']}}},
    {name: 'refresh during validation keeps restart usable', duringRead: true},
    {name: 'runtime refresh keeps settings usable', kind: 'settings', event: {resident_changes: {a: ['runtime']}}},
    {name: 'changed execution account rejects restart', change: d => {d.execution_account.account_id = 'other';}, rejected: true},
    {name: 'changed provider binding rejects restart', change: d => {d.provider_session.account_id = 'other';}, rejected: true},
    {name: 'changed Backend rejects restart', change: d => {d.resident.backend = 'claude';}, rejected: true},
    {name: 'revoked restart permission rejects restart', change: d => {d.allows.RestartSession = false;}, rejected: true},
    {name: 'revoked settings permission rejects save', kind: 'settings', change: d => {d.allows.UpdateResidentSettings = false;}, rejected: true},
    {name: 'login identity change rejects restart', identityChanged: true, rejected: true},
    {name: 'login identity change during validation rejects restart', identityDuringRead: true, rejected: true},
    {name: 'closed dialog rejects restart', close: true, rejected: true},
    {name: 'active migration rejects restart', migration: true, rejected: true},
    {name: 'validation read failure rejects restart', readFailure: true, rejected: true},
    {name: 'double submit shares one restart request', duplicate: true},
  ];
  const failures = [];
  for (const scenario of cases) {
    const events = new EventTarget();
    const testWindow = {
      addEventListener: events.addEventListener.bind(events),
      PortalI18n: {t: value => value},
      PortalUIPrimitives: {randomUUID: () => 'settings-request'},
    };
    vm.runInNewContext(fs.readFileSync(src, 'utf8'), {window: testWindow});
    const current = {
      resident: {resident_id: 'a', backend: 'codex'},
      execution_account: {account_id: 'codex_four'},
      provider_session: {provider: 'codex', account_id: 'four'},
      allows: {RestartSession: true, UpdateResidentSettings: true},
      chat_workspace: {actions: {runtime_settings: {instruction: 'UpdateResidentSettings'}}},
    };
    const emit = (type, detail) => {const event = new Event(type); event.detail = detail; events.dispatchEvent(event);};
    const submitted = [];
    let callback, observer, dialogOptions, readHook;
    const testService = {
      projection: async () => {const hook = readHook; readHook = null; await hook?.(); return structuredClone(current);},
      runtimeSettings: async () => ({settings: {model: 'm'}}),
      runtimeCatalog: async () => ({models: ['m', 'm2']}),
      sessionRestartOptions: async () => ({source: {provider: 'codex', account_id: 'four', has_session: true}}),
      observeSessionRestart: (_id, fn) => {observer = fn; return () => {};},
      restartSession: async payload => {submitted.push(payload); return {status: 'accepted'};},
      updateRuntimeSettings: async (id, changes) => {submitted.push({resident_id: id, changes}); return {settings: changes};},
    };
    const dialog = options => {
      dialogOptions = options;
      return {open: (_config, submit) => {callback = submit;}, close: () => options.onClose(), destroy() {}};
    };
    const testController = testWindow.PortalResidentRuntimeController.create({service: testService, dialogs: {settings: dialog, restart: dialog}});
    const handle = testController.bind('a');
    try {
      await (scenario.kind === 'settings' ? handle.openSettings() : handle.openRestart());
      scenario.change?.(current);
      if (scenario.event) {emit('portal:change', scenario.event); await handle.load();}
      if (scenario.identityChanged) emit('portal:identity-changed');
      if (scenario.close) dialogOptions.onClose();
      if (scenario.migration) observer({restart: {status: 'accepted'}});
      if (scenario.duringRead) readHook = () => emit('portal:change', {resident_changes: {a: ['runtime']}});
      if (scenario.identityDuringRead) readHook = () => emit('portal:identity-changed');
      if (scenario.readFailure) readHook = () => {throw new Error('projection unavailable');};
      const values = scenario.kind === 'settings' ? {model: 'm2'} : {target_provider: 'codex', target_account: 'codex_three', active_execution_policy: 'wait'};
      if (scenario.rejected) {
        await assert.rejects(() => callback(values));
        assert.equal(submitted.length, 0, scenario.name);
      } else {
        const first = callback(values);
        await (scenario.duplicate ? Promise.all([first, callback(values)]) : first);
        assert.equal(submitted.length, 1, scenario.name);
        assert.equal(submitted[0].resident_id, 'a');
        if (scenario.kind === 'settings') assert.equal(submitted[0].changes.model, 'm2');
        else {
          assert.equal(submitted[0].target_account, 'codex_three');
          assert.equal(submitted[0].active_execution_policy, 'wait');
        }
      }
      console.log(`PASS ${scenario.name}`);
    } catch (error) {
      failures.push(`${scenario.name}: ${error.message}`);
    } finally {
      handle.destroy();
    }
  }
  assert.deepEqual(failures, [], 'runtime submission regressions');
}
if (process.argv.includes('--save-races')) {
(async () => {
  const a = ctrl.bind('a');
  let updates = [], fail = true;
  service.updateRuntimeSettings = async (id, changes, key) => {
    updates.push({id, changes, key});
    if (fail) throw new TypeError('lost response');
    settings[id] = {...settings[id], ...changes};
    return {settings: settings[id]};
  };
  await a.openSettings();
  await assert.rejects(() => runtime.args[1]({model: 'm2'}));
  await assert.rejects(() => runtime.args[1]({model: 'm3'}), /变更/);
  assert.equal(updates.length, 1, 'unknown result cannot be replaced by a different mutation');
  fail = false;
  await runtime.args[1]({model: 'm2'});
  assert.equal(updates.length, 2);
  assert.equal(updates[0].key, updates[1].key);
  assert.equal(updates[0].id, updates[1].id);
  await a.openSettings();
  service.updateRuntimeSettings = async (id, changes, key) => {
    updates.push({id, changes, key}); settings[id] = {...settings[id], ...changes};
    throw new TypeError('committed but receipt lost');
  };
  await assert.rejects(() => runtime.args[1]({model: 'm3'}));
  ctrl.invalidate('a'); await a.load();
  await a.openSettings();
  service.updateRuntimeSettings = async (id, changes, key) => {
    updates.push({id, changes, key}); settings[id] = {...settings[id], ...changes};
    return {settings: settings[id]};
  };
  await runtime.args[1]({model: 'm4'});
  assert.notEqual(updates.at(-1).key, updates.at(-2).key, 'readback confirms commit and releases old identity');
  await a.openSettings();
  const staleSave = runtime.args[1];
  service.projection = async () => ({...detail('a'), execution_account: {account_id: 'different'}});
  const before = updates.length;
  await assert.rejects(() => staleSave({model: 'm5'}), /变更/);
  assert.equal(updates.length, before, 'changed account must not receive stale settings');
  a.destroy();
  console.log('unknown save identity, conflicting retry, committed readback and changed account guards passed');
})().catch(e => {console.error(e); process.exitCode=1});
} else {
(async()=>{
let viewA,viewB;const a=ctrl.bind('a',{onState:s=>viewA=s}),b=ctrl.bind('a',{onState:s=>viewB=s});
await Promise.all([a.load(),b.load()]);assert.equal(calls.filter(c=>c[0]==='read').length,1);
await a.openSettings();await runtime.args[1]({model:'m',prompt_context:{history:{enabled:false}}});assert.equal(calls.filter(c=>c[0]==='save').length,1);assert(!calls.find(c=>c[0]==='save')[2].model);assert.equal(viewB.settings.prompt_context.history.enabled,false);
await b.openSettings();const old=runtime.args[1];b.destroy();await assert.rejects(()=>old({model:'m2'}));
permissions.b={edit:false,restart:true};const c=ctrl.bind('b');await c.openSettings();assert(restart);assert.equal(restart.args[0].choose_runtime_settings,true);assert(!calls.some(c=>c[0]==='read'&&c[1]==='b'));
permissions.b.restart=false;ctrl.invalidate('b');await c.load();assert.equal(c.state().canRestart,false);await assert.rejects(()=>c.openSettings());
await a.openSettings();const save=runtime.args[1];watch.a({restart:{status:'accepted'}});await assert.rejects(()=>save({model:'m2'}));watch.a({restart:{status:'completed'}});
await a.load();await a.openSettings();const stale=runtime.args[1];listeners['portal:identity-changed']();await assert.rejects(()=>stale({model:'m2'}));
const d=ctrl.bind('a');hold=new Promise(r=>pending=r);const opening=d.openSettings();d.destroy();pending();hold=null;await opening;assert(!runtime || runtime.args[2].residentId==='a');
a.destroy();c.destroy();console.log('shared cache/delta/object settings/cross-entry/permission/restart-only/migration/stale close passed');
await runtimeSubmissionRegressions();
})().catch(e=>{console.error(e);process.exitCode=1});

}
