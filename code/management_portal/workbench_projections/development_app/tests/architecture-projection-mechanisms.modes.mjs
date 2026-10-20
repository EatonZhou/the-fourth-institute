import assert from 'node:assert/strict';

// Exercises only the mode container using the actual component and fixed model.
export async function assertProjectionModes({page,capture,observations}) {
  const focus='web.m.focus-revision',feedback='web.m.feedback-confirmation';
  await page.evaluate(id=>mount(id,null,true),focus);
  const ids=await page.evaluate(()=>projectionTestData.objects.find(o=>o.id==='projection-family').mechanism_ids);
  assert.equal(ids.length,7);
  assert.deepEqual(await page.locator('[data-projection-mode]').evaluateAll(nodes=>nodes.map(n=>n.dataset.projectionMode)),ids);
  assert.equal(await page.evaluate(()=>view.getScene().mechanismId),focus);
  const savedByMode={};
  for (const [index,id] of ids.entries()) {
    await page.locator(`[data-projection-mode="${id}"]`).click();
    const choices=await page.locator('[data-projection-action=case] option').evaluateAll(nodes=>nodes.map(n=>n.value));
    await page.locator('[data-projection-action=case]').selectOption(choices.at(-1));
    for(let i=0;i<index%3+1;i++) if(await page.locator('[data-projection-action=next]').isEnabled()) await page.locator('[data-projection-action=next]').click();
    await page.locator('[data-projection-action=sources]').click();
    await page.locator('[data-projection-detail=state] summary').click();
    const scene=await page.evaluate(()=>view.getScene());
    assert.equal(scene.ownerId,await page.evaluate(id=>projectionTestData.mechanisms.find(m=>m.id===id).owner_id,id));
    assert.equal(await page.evaluate(()=>projectionTestSubject.objectId),'projection-family');
    savedByMode[id]=await page.evaluate(()=>{const s=view.getViewState();return s.views[s.activeMechanismId]});
  }
  for (const id of ids) {
    await page.locator(`[data-projection-mode="${id}"]`).click();
    const state=await page.evaluate(()=>{const s=view.getViewState();return s.views[s.activeMechanismId]});
    for (const key of ['caseId','position','showSources','openDetails']) assert.deepEqual(state[key],savedByMode[id][key]);
  }
  assert.equal(await page.evaluate(()=>calls.filter(c=>['select','navigate','return'].includes(c.key)).length),0);
  await capture('01-seven-protocol-modes');
  await page.locator(`[data-projection-mode="${feedback}"]`).click();
  const beforeSource=await page.evaluate(()=>view.getViewState());
  await page.locator('[data-projection-action=source]').first().click();
  assert((await page.locator('#source-body').textContent()).length>100);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.key==='source').length),1);
  await page.locator('#source-close').click();
  await page.evaluate(({id,saved})=>mount(id,saved,true),{id:focus,saved:beforeSource});
  assert.equal(await page.evaluate(()=>view.getScene().mechanismId),feedback);
  const restored=await page.evaluate(()=>view.getViewState());
  assert.equal(restored.activeMechanismId,feedback);
  for(const id of ids) for(const key of ['caseId','position','showSources','openDetails']) assert.deepEqual(restored.views[id][key],beforeSource.views[id][key]);

  // Reject before touching the target DOM or emitting parent callbacks.
  const rejections=await page.evaluate(()=>{
    const data=projectionTestData,subject={objectId:'projection-family'};
    const ids=data.objects.find(o=>o.id===subject.objectId).mechanism_ids;
    const content={...data,mechanism:data.mechanisms.find(m=>m.id===ids[0]),modeMechanisms:ids.map(id=>data.mechanisms.find(m=>m.id===id))};
    const options={subject,content};const results=[];
    const check=(name,modify)=>{
      const root=document.createElement('div');root.textContent='untouched';const props=structuredClone(options);modify(props);
      let error=null,callbacks=0;
      try{const v=createProjectionTestView(root,{...props,ports:{change:()=>callbacks++}});v.destroy()}catch(e){error=e.message}
      results.push({name,error,untouched:root.textContent==='untouched',callbacks});
    };
    check('unreferenced_mode',p=>p.content.modeMechanisms.push(data.mechanisms.find(m=>m.id==='web.m.panel-lifecycle')));
    check('same_owner_undeclared',p=>p.content.modeMechanisms.push({...p.content.mechanism,id:'not-declared'}));
    check('forged_owner',p=>p.content.modeMechanisms.find(m=>m.id==='projection.mechanism.assembly').owner_id='projection-family');
    check('unknown_subject',p=>p.subject={objectId:'missing',id:'projection-family'});
    check('wrong_subject',p=>p.subject={objectId:'web.projections'});
    check('duplicate_subject',p=>p.content.objects.push({...p.content.objects.find(o=>o.id==='projection-family')}));
    check('duplicate_mode',p=>p.content.modeMechanisms.push(p.content.modeMechanisms[0]));
    check('missing_route_mode',p=>p.content.modeMechanisms.shift());
    check('foreign_initial_batch',p=>p.initialState={...view.getViewState(),batchKey:'another-batch'});
    return results;
  });
  for(const r of rejections){assert(r.error,r.name);assert(r.untouched,r.name);assert.equal(r.callbacks,0,r.name);}
  assert.match(rejections.find(r=>r.name==='forged_owner').error,/unsupported_adapter/);
  observations.push({name:'02-pre-mount-rejections',rejections});
  const restoreRejections=await page.evaluate(()=>{
    const saved=view.getViewState();const results=[];
    for(const bad of [{...saved,batchKey:'other-batch'},{...saved,activeMechanismId:'web.m.panel-lifecycle'},
      {...saved,views:{...saved.views,'web.m.panel-lifecycle':saved.views[saved.activeMechanismId]}}]){
      let error=null;try{view.restoreViewState(bad)}catch(e){error=e.message}
      results.push({error,unchanged:JSON.stringify(saved)===JSON.stringify(view.getViewState())});
    }
    return results;
  });
  for(const r of restoreRejections){assert.match(r.error,/restore_mismatch/);assert(r.unchanged);}
  observations.push({name:'03-restore-rejections',restoreRejections});
  await page.setViewportSize({width:625,height:1000});await capture('04-seven-modes-narrow');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const hidden=await page.evaluate(()=>{
    window.staleMode=document.querySelector('[data-projection-mode]');window.staleNext=document.querySelector('[data-projection-action=next]');
    const state=view.getViewState(),count=calls.length;view.hide();staleMode.click();staleNext.click();
    return {unchanged:JSON.stringify(state)===JSON.stringify(view.getViewState()),noCallbacks:count===calls.length};
  });assert(hidden.unchanged);assert(hidden.noCallbacks);
  await page.evaluate(()=>view.show());
  await page.locator('[data-projection-action=return]').click();
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.key==='return').length),1);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.key==='navigate').length),0);
  const aborted=await page.evaluate(()=>{const count=calls.length;controller.abort();staleMode.click();staleNext.click();return {noCallbacks:count===calls.length,empty:!document.querySelector('#root').textContent}});
  assert(aborted.noCallbacks);assert(aborted.empty);
  observations.push({name:'05-parent-ports-and-lifecycle',hidden,aborted,returnCallbacks:1,navigateCallbacks:0,scope:'mocked parent ports only; parent stack itself is not executed'});
}
