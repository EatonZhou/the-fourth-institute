'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
module.exports=async function experience(page) {
  await page.evaluate(async()=>{
    window.exFactory=LineageTemplateWorkshop;window.exChatOptions=chatOptions;
    window.LineageTemplateWorkshop={...exFactory,mount(options){window.exWorkshop=exFactory.mount(options);return exWorkshop;}};
    const root=document.createElement('div');root.id='experienceCase';root.style='position:fixed;inset:0;z-index:200;background:var(--panel)';document.body.append(root);
    window.exPanel=PortalTemplateWorkshopLoader.create({root,id:'experience',target:{lineage_id:sourceLineageId},execute});await exPanel.show();window.exOwnChatOptions=chatOptions;
  });
  const root=page.locator('#experienceCase'),graph=root.locator('.templateGraphCanvas');
  const undo=root.locator('[data-workshop-history="undo"]'),redo=root.locator('[data-workshop-history="redo"]');
  const clean=async()=>assert.equal(await page.evaluate(()=>exWorkshop.dirty()),false);
  const content=()=>page.evaluate(()=>JSON.stringify(LineageTemplateWorkshopDraft.payload(exWorkshop.template())));
  const folder=process.env.WORKSHOP_SCREENSHOT_DIR || '/tmp/workshop-experience-20261125';fs.mkdirSync(folder,{recursive:true});
  try {
    await clean();assert.equal(await page.evaluate(()=>exPanel.canClose()),true);
    assert.equal(await graph.locator('.hatgNode').count(),0);
    const geometry=await root.evaluate(node=>{const box=node.getBoundingClientRect(),title=node.querySelector('.workshopBar').getBoundingClientRect(),tools=node.querySelector('.workshopTools').getBoundingClientRect(),store=node.querySelector('[data-workshop-store]').getBoundingClientRect();return {spans:title.width===box.width,order:title.bottom<=tools.top,store:store.top>=title.top&&store.bottom<=title.bottom};});
    assert.deepEqual(geometry,{spans:true,order:true,store:true});
    for(const width of [1440,600]){await page.setViewportSize({width,height:1000});await page.screenshot({animations:'disabled',path:path.join(folder,`blank-default-${width}.png`)});}
    await page.setViewportSize({width:1440,height:1000});
    const group=root.locator('[data-toolbox-collapse="duties"]');
    await group.click();assert.equal(await group.getAttribute('aria-expanded'),'false');await clean();
    await group.click();assert.equal(await group.getAttribute('aria-expanded'),'true');await clean();
    const cards=root.locator('[data-toolbox-group="duties"] .templateTile');
    assert.ok((await cards.nth(1).boundingBox()).x>(await cards.first().boundingBox()).x);
    await root.locator('[data-template-id="workshop_target"]').click();
    assert.equal(await page.evaluate(()=>exWorkshop.template().body.roles.length),1);
    await undo.click();await clean();assert.equal(await graph.locator('.hatgNode').count(),0);
    await redo.click();assert.equal(await graph.locator('.hatgNode').count(),1);
    await root.locator('[data-template-id="workshop_loop"]').click();
    const beforeMove=await content();await root.locator('[data-graph-fit]').click();
    const node=await graph.locator('[data-node-id="loop"]').boundingBox();
    await page.mouse.move(node.x+node.width/2,node.y+node.height/2);await page.mouse.down();await page.mouse.move(node.x+node.width/2+45,node.y+node.height/2+25,{steps:5});await page.mouse.up();
    assert.notEqual(await content(),beforeMove);await undo.click();assert.equal(await content(),beforeMove);await redo.click();
    const title=root.locator('[data-meta="title"]');await title.fill('History saved');
    await root.locator('[data-workshop-save]').click();await page.waitForFunction(()=>exWorkshop.template().template_id&&!exWorkshop.dirty());
    const saved=await content();await undo.click();assert.equal(await page.evaluate(()=>exWorkshop.dirty()),true);await redo.click();await clean();
    await graph.locator('[data-node-id="loop"]').click();
    await root.locator('[data-element-json]').evaluate(node=>{node.closest('details').open=true;});
    await root.locator('[data-element-json]').fill('{');assert.equal(await page.evaluate(()=>exWorkshop.dirty()),true);
    await undo.click();await clean();await redo.click();assert.equal(await root.locator('[data-element-json]').inputValue(),'{');
    await undo.click();await clean();
    await root.locator('[data-structure-view="list"]').first().click();
    await root.locator('[data-structure-list="roles"] [data-structure-row]').first().evaluate(node=>{node.open=true;});
    await root.locator('[data-structure-list="roles"] [data-row-field="title"]').fill('From list');
    await root.locator('[data-structure-view="json"]').first().click();
    await root.locator('.monaco-editor').waitFor();
    await page.evaluate(()=>{window.exCode=()=>monaco.editor.getEditors().find(editor=>document.querySelector('#experienceCase').contains(editor.getDomNode()));exCode().setValue('{');});
    await root.locator('[data-preview-status][data-state="error"]').waitFor({state:'attached'});
    assert.equal(await page.evaluate(()=>exWorkshop.dirty()),true);
    await undo.click();await page.waitForFunction(()=>{try{return !!JSON.parse(exCode().getValue());}catch{return false;}});
    await redo.click();await page.waitForFunction(()=>exCode().getValue()==='{');
    await undo.click();
    await root.locator('[data-structure-view="graph"]').first().click();
    await undo.click();assert.equal(await content(),saved);
    const baseline=await content();
    await page.evaluate(()=>exWorkshop.acceptDraft({...exWorkshop.template(),title:'Assistant local change'}));
    await undo.click();assert.equal(await content(),baseline);await clean();await redo.click();
    await undo.click();await title.fill('Branched edit');assert.equal(await redo.isDisabled(),true);
    await undo.click();await clean();
    // The assistant is a persistent component surface, independent of the draft.
    const icon=root.locator('[data-workshop-assistant]'),panel=root.locator('.workshopChatColumn');
    await icon.hover();await panel.waitFor({state:'visible'});
    await panel.hover();await page.waitForTimeout(200);assert.equal(await panel.isVisible(),true);
    const composer=panel.locator('.composer textarea,.composer [contenteditable="true"]').first();
    await composer.fill('Unsent workshop input');
    for(const theme of ['auto','liquid-glass']) for(const width of [1440,600]) {
      await page.setViewportSize({width,height:1000});await page.evaluate(theme=>PortalTheme.setMode(theme),theme);
      await page.waitForTimeout(40);
      const bounds=await panel.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width);
      await page.screenshot({animations:'disabled',path:path.join(folder,`assistant-${theme==='auto'?'default':theme}-${width}.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    const reduced=await page.context().newCDPSession(page);
    await reduced.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-transparency',value:'reduce'}]});
    assert.equal(await panel.evaluate(node=>getComputedStyle(node).backdropFilter),'none');
    await page.screenshot({animations:'disabled',path:path.join(folder,'assistant-liquid-glass-reduced-transparency.png')});
    await reduced.send('Emulation.setEmulatedMedia',{features:[]});await reduced.detach();
    await page.evaluate(()=>PortalTheme.setMode('auto'));
    await page.mouse.move(5,5);await page.waitForTimeout(200);assert.equal(await panel.isVisible(),true);
    await composer.press('Escape');assert.equal(await panel.isVisible(),false);
    await icon.press('Enter');await panel.waitFor({state:'visible'});
    assert.equal(await composer.evaluate(node=>node.value??node.textContent),'Unsent workshop input');
    await root.locator('[data-workshop-chat]').click();
    await icon.hover();await page.mouse.move(5,5);await page.waitForTimeout(220);assert.equal(await panel.isVisible(),false);
    await page.evaluate(()=>chatOptions.onInteractionChange(true,'attachment'));
    await panel.waitFor({state:'visible'});await page.mouse.move(5,5);await page.waitForTimeout(220);assert.equal(await panel.isVisible(),true);
    await page.evaluate(()=>chatOptions.onInteractionChange(false,'attachment'));
    await icon.press('Escape');
    const box=await icon.boundingBox();await icon.press('ArrowDown');
    await page.mouse.move(box.x+18,box.y+18);await page.mouse.down();await page.mouse.move(-50,-50,{steps:5});await page.mouse.up();
    await page.waitForFunction(()=>{const root=document.getElementById('experienceCase'),area=root.querySelector('.workshopEditorContent > [data-page="body"]').getBoundingClientRect(),icon=root.querySelector('[data-workshop-assistant]').getBoundingClientRect();return Math.abs(icon.left-area.left-12)<1&&Math.abs(area.bottom-icon.bottom-12)<1;});await clean();
    const destination=root.locator('[data-workshop-library] [data-template-id]').filter({hasText:'Parameterized loop'});
    await destination.focus();await destination.press('Enter');
    await page.waitForFunction(()=>exWorkshop.template().template_id===parameterRecipe);assert.equal(await undo.isDisabled(),true);await clean();
    // A second workshop has independent floating state and unsent input.
    await page.evaluate(async()=>{
      window.LineageTemplateWorkshop=exFactory;
      const host=document.createElement('div');host.id='experienceSecond';host.style='position:fixed;right:0;bottom:0;width:400px;height:650px;z-index:220;background:var(--panel)';document.body.append(host);
      window.exSecond=PortalTemplateWorkshopLoader.create({root:host,id:'experience-second',target:{},execute});await exSecond.show();
    });
    const second=page.locator('#experienceSecond');await second.locator('[data-workshop-assistant]').hover();
    const secondInput=second.locator('.composer textarea,.composer [contenteditable="true"]').first();await secondInput.fill('Second workshop only');
    assert.equal(await composer.evaluate(node=>node.value??node.textContent),'Unsent workshop input');
    await page.evaluate(()=>{exSecond.destroy();document.querySelector('#experienceSecond').remove();window.chatOptions=exOwnChatOptions;});
    await page.evaluate(async()=>{exPanel.hide();await exPanel.show();});
    assert.equal(await composer.evaluate(node=>node.value??node.textContent),'Unsent workshop input');
    await page.evaluate(target=>{
      window.exInboxNav=PortalNav;window.PortalNav={...PortalNav,personalAssistantTarget:async()=>target,loadChatWorkspace:async()=>{}};
      PortalAgentInbox.destroy();PortalAgentInbox.mount();
    },{resident_id:process.env.WORKSHOP_PERSONAL_ASSISTANT,lineage_id:process.env.WORKSHOP_PERSONAL_LINEAGE,title:'Fixture AMA'});
    const inbox=page.locator('[data-portal-agent-inbox]');
    await page.waitForFunction(()=>document.querySelector('[data-portal-agent-inbox]')?.dataset.hasAssistant==='true');
    await inbox.locator('[data-inbox-trigger="mailbox"]').hover();
    await page.evaluate(()=>chatOptions.onInteractionChange(true,'question'));
    assert.equal(await inbox.getAttribute('data-expanded'),'true');
    await inbox.locator('[data-personal-assistant-trigger]').hover();
    const ama=inbox.locator('[data-personal-assistant-chat] .composer textarea,[data-personal-assistant-chat] .composer [contenteditable="true"]').first();
    await ama.fill('Global AMA input');
    await icon.hover();assert.equal(await inbox.getAttribute('data-active-panel'),'assistant');assert.equal(await inbox.getAttribute('data-expanded'),'true');
    assert.equal(await composer.evaluate(node=>node.value??node.textContent),'Unsent workshop input');
    await page.evaluate(()=>chatOptions.onInteractionChange(false,'question'));
    await ama.hover();await panel.waitFor({state:'hidden'});assert.equal(await inbox.getAttribute('data-expanded'),'true');
    await page.evaluate(()=>{PortalAgentInbox.destroy();window.PortalNav=exInboxNav;PortalAgentInbox.mount();});
  } finally {
    await page.evaluate(()=>{window.exSecond?.destroy();document.querySelector('#experienceSecond')?.remove();exPanel.destroy();document.querySelector('#experienceCase').remove();window.LineageTemplateWorkshop=exFactory;window.chatOptions=exChatOptions;if(window.exInboxNav){PortalAgentInbox.destroy();window.PortalNav=exInboxNav;PortalAgentInbox.mount();}});
  }
  // A touch-only surface must open on the first tap, without hover assistance.
  const touchContext=await page.context().browser().newContext({hasTouch:true,viewport:{width:600,height:900}});
  try {
    const touch=await touchContext.newPage();await touch.goto(process.env.WORKSHOP_TEST_ORIGIN);
    await touch.evaluate(async()=>{
      PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};
      const host=document.createElement('section');host.id='touchCase';host.style='position:fixed;inset:0;background:var(--panel)';document.body.append(host);
      window.touchPanel=PortalTemplateWorkshopLoader.create({root:host,id:'touch',target:{},listLineages:async()=>({lineages:[]}),execute:async(instruction,args,options)=>{
        const result=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(result.error)throw Error(result.error.message);return result.result;
      }});await touchPanel.show();
    });
    const touchRoot=touch.locator('#touchCase');await touchRoot.locator('[data-workshop-assistant]').tap();
    await touchRoot.locator('.workshopChatColumn').waitFor({state:'visible'});
    await touchRoot.locator('[data-workshop-chat]').tap();assert.equal(await touchRoot.locator('.workshopChatColumn').isVisible(),false);
    assert.equal(await touch.evaluate(()=>touchPanel.snapshot().dirty),false);
  } finally {await touchContext.close();}
};
