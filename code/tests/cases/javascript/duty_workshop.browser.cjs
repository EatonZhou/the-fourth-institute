'use strict';
const assert = require('node:assert/strict');
module.exports = async function dutyWorkshop(page) {
  await page.evaluate(async () => {
    cards.destroy();
    const html = await (await fetch('/static/template_workshop_panel.html')).text();
    const root = document.createElement('section'); root.id='dutyTest'; root.style='position:fixed;inset:0;background:white;z-index:200'; root.innerHTML=html;
    document.body.append(root); window.dutyRoot=root.firstElementChild;
    const resident=await execute('CreateResident',{lineage_id:sourceLineageId,title:'Duty holder',provider:'codex'});
    window.dutyHolder=resident.resident_id;
    const result=await execute('CreateDuty',{lineage_id:sourceLineageId,holder_id:dutyHolder,description:'Test isolated duty',prompt:'Original',memory_level:'session'});
    window.dutyId=result.function_block.id;
    window.dutyWorkshop=LineageDutyWorkshop.mount({root:dutyRoot,lineageId:sourceLineageId,functionBlockId:dutyId,execute});
    await dutyWorkshop.load();
    window.dutyBefore=(await execute('GetLineage',{lineage_id:sourceLineageId})).lineage;
    window.dutyChat=LineageTemplateWorkshopChat.mount({root:dutyRoot,workshop:dutyWorkshop,execute});
  });
  const root=page.locator('#dutyTest');
  assert.equal(await page.evaluate(()=>dutyWorkshop.dirty()),false);
  await root.locator('.dutyWorkshopMemory summary').click();
  await root.locator('[data-duty-meta="prompt"]').fill('Only the duty memory changes');
  await root.locator('[data-duty-save]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await page.evaluate(async()=>JSON.stringify((await execute('GetLineage',{lineage_id:sourceLineageId})).lineage)===JSON.stringify(dutyBefore)),true);
  assert.equal(await page.evaluate(()=>dutyWorkshop.dirty()),true);
  await root.locator('[data-duty-save]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'保存',exact:true}).click();
  await page.waitForFunction(()=>!dutyWorkshop.dirty());
  assert.equal(await page.evaluate(async()=>{
    const graph=(await execute('GetLineage',{lineage_id:sourceLineageId})).lineage;
    return graph.revision===dutyBefore.revision+1 && graph.function_blocks.find(b=>b.id===dutyId).duty.memory.prompt==='Only the duty memory changes' && JSON.stringify(graph.nodes)===JSON.stringify(dutyBefore.nodes);
  }),true);
  // A grey external Resident cannot be deleted by the graph keyboard shortcut.
  await root.locator(`.hatgNode`).click();
  await root.locator('.hatgCanvas').focus(); await page.keyboard.press('Delete');
  assert.equal(await page.evaluate(()=>dutyWorkshop.dirty()),false);
  // Build a component through the same toolbox and semantic inspector as templates.
  await root.locator('[data-template-id="workshop_loop"]').click();
  await root.locator('[data-duty-element-title]').fill('Bounded loop');
  await root.locator('[data-duty-save]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'保存',exact:true}).click();
  await page.waitForFunction(()=>!dutyWorkshop.dirty());
  assert.equal(await page.evaluate(async()=>{
    const graph=(await execute('GetLineage',{lineage_id:sourceLineageId})).lineage;
    const component=graph.program.components.find(c=>c.title==='Bounded loop');
    return Boolean(component && graph.function_blocks.find(b=>b.id===dutyId).elements.some(e=>e.id===component.id));
  }),true);
  await root.locator('[data-duty-meta="title"]').fill('Keep this conflict draft');
  await page.evaluate(()=>execute('UpdateDuty',{lineage_id:sourceLineageId,function_block_id:dutyId,description:'Concurrent editor'}));
  await root.locator('[data-duty-save]').click();
  await root.locator('#workshopError').waitFor({state:'visible'});
  assert.equal(await root.locator('[data-duty-meta="title"]').inputValue(),'Keep this conflict draft');
  assert.equal(await page.evaluate(()=>dutyWorkshop.dirty()),true);
  await root.locator('[data-duty-reload]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'重新载入',exact:true}).click();
  await page.waitForFunction(()=>!dutyWorkshop.dirty());
  const accepted=await page.evaluate(async()=>{
    const graph=(await execute('GetLineage',{lineage_id:sourceLineageId})).lineage;
    const value={lineage_id:sourceLineageId,function_block_id:dutyId,revision:graph.revision,operations:[],duty_changes:{prompt:'Assistant draft'}};
    let mismatch=false;
    try {await dutyWorkshop.acceptDraft({...value,function_block_id:'fbi_other'});} catch (_) {mismatch=true;}
    await dutyWorkshop.acceptDraft(value);
    const current=(await execute('GetLineage',{lineage_id:sourceLineageId})).lineage;
    const context=dutyChat.context().context_text;
    return {mismatch,unchanged:JSON.stringify(current)===JSON.stringify(graph),dirty:dutyWorkshop.dirty(),context:context.includes(dutyId)&&context.includes(`revision=${graph.revision}`)};
  });
  assert.deepEqual(accepted,{mismatch:true,unchanged:true,dirty:true,context:true});
  await root.locator('[data-duty-save]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'保存',exact:true}).click();
  await page.waitForFunction(()=>!dutyWorkshop.dirty());
  await page.waitForFunction(()=>{
    const node=[...document.querySelectorAll('#dutyTest .hatgNode')].find(item=>item.textContent.includes('Bounded loop'));
    if(!node)return false;
    const rect=node.getBoundingClientRect();
    return rect.width>0 && rect.height>0 && rect.left>=0 && rect.top>=0 && rect.right<innerWidth && rect.bottom<innerHeight && Number(getComputedStyle(node).opacity)>.1;
  });
  await page.screenshot({path:'/tmp/duty-workshop-browser.png'});
  await page.evaluate(()=>{dutyChat.destroy();dutyWorkshop.destroy();document.querySelector('#dutyTest').remove();});
  await page.evaluate(async()=>{
    const host=document.createElement('section');host.id='dutySwitch';host.style='position:fixed;inset:0;z-index:200;background:var(--panel)';document.body.append(host);
    window.dutySwitch=PortalTemplateWorkshopLoader.create({root:host,id:'duty-switch',target:{lineage_id:sourceLineageId,function_block_id:dutyId},execute,listLineages:async()=>({lineages:[]})});
    await dutySwitch.show();
  });
  const switchRoot=page.locator('#dutySwitch');
  await switchRoot.locator('[data-duty-meta="title"]').fill('Unsaved duty name');
  const destination=switchRoot.locator('[data-workshop-library] [data-template-id]').first();
  const targetId=await destination.getAttribute('data-template-id');
  await destination.click();
  await page.getByRole('alertdialog').getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await switchRoot.locator('[data-duty-meta="title"]').inputValue(),'Unsaved duty name');
  await destination.click();
  await page.getByRole('alertdialog').getByRole('button',{name:'关闭',exact:true}).click();
  await page.waitForFunction(id=>dutySwitch.target().template_id===id,targetId);
  await switchRoot.locator('[data-workshop-save]').waitFor();
  assert.equal(await page.evaluate(()=>dutySwitch.snapshot().dirty),false);
  await page.evaluate(()=>{dutySwitch.destroy();document.querySelector('#dutySwitch').remove();});
};
