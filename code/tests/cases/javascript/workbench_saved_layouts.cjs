const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_workbench_storage.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const preset = id => ({id:'workspace-'+id,name:id,layout:{type:'stack',tabs:['a','b'],active:'b'},hidden:['c'],collapsed:['a']});

function device(server, user='alice', id='projection-demo-published', initial={}) {
  const state = {storageUser:user,destroyed:false};
  const saved = clone(initial);
  const hooks = {};
  let actor=user, persisted;
  const identity = {
    username:()=>actor,
    load:async()=>{await hooks.load?.();return {username:actor};},
    listUIAssociations:async namespace=>{
      const records=[...(server.get(actor)?.get(namespace)||new Map())].map(([key,value])=>({key,value:clone(value)}));
      await hooks.list?.();return {items:records};
    },
    putUIAssociation:async(namespace,key,value)=>{
      const owner=actor;await hooks.put?.();
      if(!server.has(owner))server.set(owner,new Map());
      if(!server.get(owner).has(namespace))server.get(owner).set(namespace,new Map());
      server.get(owner).get(namespace).set(key,clone(value));
    },
    removeUIAssociation:async(namespace,key)=>{
      await hooks.remove?.();server.get(actor)?.get(namespace)?.delete(key);
    },
  };
  const window={PortalIdentity:identity,PortalState:{currentTransactionId:()=>hooks.transaction||''}};
  vm.runInNewContext(source,{window});
  const service=window.PortalWorkbenchStorage.createSavedLayouts({id,savedWorkspaces:saved,shellState:state,clone,persist:()=>persisted=clone(saved)});
  return {service,state,saved,hooks,persisted:()=>persisted,switchUser:next=>{actor=next;state.storageUser=next;Object.keys(saved).forEach(k=>delete saved[k]);}};
}

test('separate devices share named layouts, preserve layout fields, and isolate accounts and workbenches',async()=>{
  const server=new Map(), a=device(server), b=device(server), otherUser=device(server,'bob'), otherPage=device(server,'alice','other');
  await a.service.save(preset('one'));
  await b.service.refresh();
  assert.deepEqual(clone(b.saved['workspace-one']),{...preset('one'),accountSynced:true});
  await otherUser.service.refresh();await otherPage.service.refresh();
  assert.deepEqual(otherUser.saved,{});assert.deepEqual(otherPage.saved,{});
  await Promise.all([a.service.save(preset('two')),b.service.save(preset('three'))]);
  await a.service.refresh();assert.equal(Object.keys(a.saved).length,3);
  await b.service.remove('workspace-one');await a.service.refresh();
  assert.equal(a.saved['workspace-one'],undefined);
  await a.service.refresh();assert.equal(server.get('alice').get('workbench.saved-layouts').size,2,'stale synced cache does not resurrect deletion');
});

test('legacy layouts migrate once; local live layout is not sent; failed migration remains retryable',async()=>{
  const server=new Map(), old=preset('old'), a=device(server,'alice','page',{'workspace-old':{...old,panelStates:{draft:'private'}}});
  a.hooks.put=()=>{throw new Error('offline');};
  await assert.rejects(a.service.refresh(),/offline/);
  assert.equal(a.saved['workspace-old'].accountSynced,undefined);
  delete a.hooks.put;await a.service.refresh();
  const row=[...server.get('alice').get('workbench.saved-layouts').values()][0];
  assert.deepEqual(row.workspace,old);
  const b=device(server,'alice','page');await b.service.refresh();assert.equal(b.saved['workspace-old'].name,'old');
  await b.service.remove('workspace-old');await a.service.refresh();assert.deepEqual(a.saved,{});
});

test('nested host layout state survives account round trips without sharing mutable objects',async()=>{
  const server=new Map(), a=device(server), b=device(server);
  const workspace={...preset('nested'),hostState:{workbenches:{overview:{layout:{type:'stack',tabs:['news']},hidden:['notice-board'],collapsed:[]}}}};
  await a.service.save(workspace);
  const expected=clone(workspace.hostState);
  workspace.hostState.workbenches.overview.hidden.length=0;
  await b.service.refresh();
  assert.deepEqual(b.saved['workspace-nested'].hostState,expected);
  b.saved['workspace-nested'].hostState.workbenches.overview.hidden.length=0;
  await b.service.refresh();
  assert.deepEqual(b.saved['workspace-nested'].hostState,expected);
});

test('failed saves/deletes/loads never claim success or erase the local list',async()=>{
  const a=device(new Map());await a.service.save(preset('kept'));
  a.hooks.put=()=>{throw new Error('offline');};
  await assert.rejects(a.service.save(preset('failed')),/offline/);assert.equal(a.saved['workspace-failed'],undefined);
  a.hooks.remove=()=>{throw new Error('offline');};
  await assert.rejects(a.service.remove('workspace-kept'),/offline/);assert.ok(a.saved['workspace-kept']);
  a.hooks.list=()=>{throw new Error('offline');};
  await assert.rejects(a.service.refresh(),/offline/);assert.ok(a.saved['workspace-kept']);
});

test('identity changes, destruction and temporary transactions fence asynchronous work',async()=>{
  const server=new Map(), seed=device(server);await seed.service.save(preset('private'));
  const a=device(server);a.hooks.list=()=>a.switchUser('bob');
  await assert.rejects(a.service.refresh(),/状态已变化/);assert.deepEqual(a.saved,{});
  const b=device(server);b.hooks.load=()=>b.switchUser('bob');
  await assert.rejects(b.service.save(preset('wrong-account')),/账户已变化/);assert.equal(server.has('bob'),false);
  const c=device(server);c.hooks.list=()=>{c.state.destroyed=true;};
  await assert.rejects(c.service.refresh(),/状态已变化/);assert.deepEqual(c.saved,{});
  const d=device(server);d.hooks.transaction='demo';
  await assert.rejects(d.service.save(preset('demo')),/临时演示/);assert.equal(server.get('alice').get('workbench.saved-layouts').size,1);
});
