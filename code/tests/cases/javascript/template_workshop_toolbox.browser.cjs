'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async function sharedWorkshopToolbox(page) {
  // Two production shelves, one real instruction-backed catalog, different actions.
  await page.evaluate(async () => {
    const shelf=document.createElement('div'); shelf.id='peerShelf'; shelf.hidden=true; document.body.append(shelf);
    window.peerShelf=LineageTemplates.mount({shelf,canvas:document.createElement('div'),request:execute,
      getContext:()=>({lineage_id:sourceLineageId}),resolveTarget:()=>({kind:'canvas'}),toast:()=>{}});
    await peerShelf.load();
    window.shelfSnapshot=selector=>[...document.querySelector(selector).querySelectorAll('[data-template-id]')].map(node=>({
      id:node.dataset.templateId,group:node.closest('[data-toolbox-group]').dataset.toolboxGroup,icon:node.querySelector('svg').outerHTML}));
  });
  const sameCatalog = async () => assert.equal(await page.evaluate(()=>JSON.stringify(shelfSnapshot('#peerShelf'))===JSON.stringify(shelfSnapshot('#workshopShelf'))),true);
  await sameCatalog();
  const temporary = await page.evaluate(async () => {
    const {template}=await execute('CreateTemplate',{template:{kind:'prompt',title:'Shared catalog mutation',body:{message:'Only catalog visibility'}}});
    window.dispatchEvent(new Event('portal:templates-changed')); return template.template_id;
  });
  await page.locator(`#workshopShelf [data-template-id="${temporary}"]`).waitFor();
  await sameCatalog();
  const installed=await page.evaluate(async id=>{
    const {item}=await execute('PublishTemplate',{template_id:id});
    const {template}=await execute('InstallStoreTemplate',{store_id:item.store_id});
    await execute('UpdateTemplate',{template_id:id,title:'Updated catalog entry'});
    window.dispatchEvent(new Event('portal:templates-changed')); return template.template_id;
  },temporary);
  await page.locator(`#workshopShelf [data-template-id="${installed}"]`).waitFor();
  await page.locator(`#workshopShelf [data-template-id="${temporary}"][aria-label="Updated catalog entry"]`).waitFor();
  await sameCatalog();
  // A visible but unsupported template must explain the action and preserve the draft.
  const before=await page.evaluate(()=>JSON.stringify(workshop.template()));
  await page.evaluate(()=>document.querySelector('#workshopCanvas').addEventListener('dragover',()=>{
    window.unsupportedDrop=[...document.querySelectorAll('.templateDropHint')].some(node=>node.dataset.allowed==='false');
  },{once:true,capture:true}));
  await page.locator(`#workshopShelf [data-template-id="${temporary}"]`).dragTo(page.locator('.templateGraphCanvas'));
  assert.equal(await page.evaluate(()=>JSON.stringify(workshop.template())),before);
  assert.equal(await page.evaluate(()=>window.unsupportedDrop),true);
  await page.evaluate(async ids=>{
    for (const template_id of ids) await execute('DeleteTemplate',{template_id}); window.dispatchEvent(new Event('portal:templates-changed'));
    document.querySelector('#workshopError').hidden=true;
  },[temporary,installed]);
  await page.locator(`#workshopShelf [data-template-id="${temporary}"]`).waitFor({state:'detached'});
  await sameCatalog();
  await page.evaluate(()=>{window.PortalIdentity={...PortalIdentity,username:()=>'other-account'};window.dispatchEvent(new Event('portal:identity-changed'));});
  await page.waitForFunction(()=>!document.querySelector(`#workshopShelf [data-template-id="${parameterRecipe}"]`));
  await sameCatalog();
  await page.evaluate(()=>{window.PortalIdentity={...PortalIdentity,username:()=>'sample_admin'};window.dispatchEvent(new Event('portal:identity-changed'));});
  await page.locator(`#workshopShelf [data-template-id="${await page.evaluate(()=>parameterRecipe)}"]`).waitFor();
  await sameCatalog();
  const folder=process.env.WORKSHOP_SCREENSHOT_DIR || '/tmp/workshop-toolbox-20261125';
  fs.mkdirSync(folder,{recursive:true});
  for (const theme of ['auto','liquid-glass','dark']) for (const width of [1440,600]) {
    await page.setViewportSize({width,height:1000});
    await page.evaluate(theme=>{PortalTheme.setMode(theme);
      workshop.fit();},theme);
    await page.waitForTimeout(80);
    const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      save:document.querySelector('[data-workshop-save]').getBoundingClientRect().toJSON(),
      graph:document.querySelector('.templateGraphCanvas').getBoundingClientRect().toJSON(),
      tools:document.querySelector('#workshopTools').getBoundingClientRect().toJSON()}));
    assert.ok(bounds.scroll<=width && bounds.save.right<=width && bounds.graph.width>250 && bounds.graph.height>400,JSON.stringify(bounds));
    await page.screenshot({animations:'disabled',path:path.join(folder,`${theme==='auto'?'default':theme}-${width}.png`)});
  }
  await page.setViewportSize({width:1440,height:1000});
  const media=await page.context().newCDPSession(page);
  await media.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-transparency',value:'reduce'}]});
  await page.evaluate(()=>PortalTheme.setMode('liquid-glass'));
  assert.equal(await page.locator('.workshopLibrary').evaluate(node=>getComputedStyle(node).backdropFilter),'none');
  await page.screenshot({animations:'disabled',path:path.join(folder,'liquid-glass-reduced-transparency.png')});
  await media.send('Emulation.setEmulatedMedia',{features:[]}); await media.detach();
  await page.evaluate(()=>{
    PortalTheme.setMode('auto');
    peerShelf.destroy();document.querySelector('#peerShelf').remove();workshop.fit();
  });
};
