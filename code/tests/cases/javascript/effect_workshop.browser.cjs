'use strict';
const assert = require('node:assert/strict');
module.exports = async function effectWorkshop(page) {
  await page.evaluate(async () => {
    const root = document.createElement('section'); root.id='effectTest'; root.style='position:fixed;inset:0;background:white;z-index:200';
    root.innerHTML=await (await fetch('/static/template_workshop_panel.html')).text(); document.body.append(root);
    const source=(await execute('CreateTemplate',{template:{kind:'structure',title:'Description to reviewed duty',parameters:[],body:{
      roles:[{key:'target',from:'target'}],components:[{key:'work',kind:'effect',title:'Return a result',config:{script:'',endpoints:[]}}],wires:[],function_block:true}}})).template;
    window.effectWorkshop=LineageTemplateWorkshop.mount({root:root.firstElementChild,lineageId:sourceLineageId,templateId:source.template_id,execute});
    await effectWorkshop.load();
  });
  const root=page.locator('#effectTest');
  await root.locator('.hatgNode[data-node-id="work"]').click();
  await root.locator('[name="effect_description"]').fill('Return the text ready, without sending messages');
  await root.locator('[data-workshop-save]').click();
  await page.waitForFunction(()=>!effectWorkshop.dirty() && effectWorkshop.template().body.components[0].config.script_draft);
  assert.equal(await page.evaluate(()=>effectWorkshop.template().body.components[0].config.script),'');
  // The assistant delivers a file-backed candidate to this same acceptDraft API.
  await page.evaluate(async()=>{
    const candidate=structuredClone(effectWorkshop.template());
    candidate.body.components[0].config.script_draft.script='output result="ready"';
    await effectWorkshop.acceptDraft(candidate);
  });
  await root.locator('[data-workshop-save]').click();
  await page.waitForFunction(()=>!effectWorkshop.dirty());
  await root.locator('.hatgNode[data-node-id="work"]').click();
  assert.equal(await root.locator('[name="script"]').inputValue(),'output result="ready"');
  await root.locator('[data-effect-review="confirm"]').click();
  const confirm=page.getByRole('alertdialog');
  assert.match(await confirm.innerText(),/output result="ready"/);
  await confirm.getByRole('button',{name:'确认',exact:true}).click();
  await page.waitForFunction(()=>!effectWorkshop.template().body.components[0].config.script_draft);
  assert.equal(await page.evaluate(()=>effectWorkshop.template().body.components[0].config.script),'output result="ready"');
  await root.locator('[data-workshop-publish]').click();
  await page.getByRole('dialog',{name:'发布到商店',exact:true}).getByRole('button',{name:'发布',exact:true}).click();
  await page.waitForFunction(()=>effectWorkshop.template().published_store?.active);
  // A revision stays a candidate; cancelling it keeps the confirmed executable.
  await root.locator('.hatgNode[data-node-id="work"]').click();
  await root.locator('[name="effect_description"]').fill('Revise the result');
  await root.locator('[name="script"]').fill('output result="revised"');
  await root.locator('[data-workshop-save]').click();
  await page.waitForFunction(()=>!effectWorkshop.dirty() && effectWorkshop.template().body.components[0].config.script_draft);
  assert.equal(await page.evaluate(()=>effectWorkshop.template().body.components[0].config.script),'output result="ready"');
  await root.locator('.hatgNode[data-node-id="work"]').click();
  await page.waitForFunction(()=>Number(getComputedStyle(document.querySelector('#effectTest .hatgNode[data-node-id="work"]')).opacity)>.5);
  await page.screenshot({path:'/tmp/effect-workshop-browser.png'});
  await root.locator('[data-effect-review="cancel"]').click();
  await page.getByRole('alertdialog').getByRole('button',{name:'取消草稿',exact:true}).click();
  await page.waitForFunction(()=>!effectWorkshop.template().body.components[0].config.script_draft);
  assert.equal(await page.evaluate(()=>effectWorkshop.template().body.components[0].config.script),'output result="ready"');
  assert.equal(await root.locator('#workshopError').isVisible(),false);
  await page.evaluate(()=>{effectWorkshop.destroy();document.querySelector('#effectTest').remove();});
};
