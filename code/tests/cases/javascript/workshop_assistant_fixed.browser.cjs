'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},hasTouch:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(10000);
 const folder='/tmp/workshop-assistant-fixed-evidence';fs.mkdirSync(folder,{recursive:true});
 async function mount(){
  await page.evaluate(async lineageId=>{
   PortalI18n.apply('zh-CN');window.PortalIdentity={username:()=>'sample_admin',userId:async()=>'sample_admin',listUIAssociations:async()=>({items:[]})};window.PortalAPI={get:path=>fetch(path).then(r=>r.json())};
   window.execute=async(instruction,args={},options={})=>{const d=await(await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args,options})})).json();if(d.error)throw Object.assign(Error(d.error.message),d.error);return d.result;};
   await PortalTemplateWorkshopLoader.load();
   window.chatOptions={};const create=PortalWorkbench.createPanel;window.PortalWorkbench={...PortalWorkbench,createPanel(type,options){if(type==='chat')chatOptions[options.id]=options;return create(type,options);}};
   const surface=TemplateWorkshopSurface.mount;window.TemplateWorkshopSurface={mount(options){const dock=options.root.querySelector('[data-workshop-dock]');dock.style.left='820px';dock.style.top='22px';return surface(options);}};
   const factory=LineageTemplateWorkshop.mount;window.workshops={};window.LineageTemplateWorkshop={...LineageTemplateWorkshop,mount(options){const result=factory(options);workshops[options.root.closest('[data-fixed-host]').id]=result;return result;}};
   const host=document.createElement('div');host.id='fixedAssistant';host.dataset.fixedHost='';host.className='workshopPanelHost';host.style='position:fixed;inset:0;z-index:200;background:var(--panel)';document.body.append(host);
   window.fixedPanel=PortalTemplateWorkshopLoader.create({root:host,id:'fixed-assistant',target:{lineage_id:lineageId},execute});await fixedPanel.show();
   const global=document.createElement('div');global.id='globalDockSentinel';global.style='position:fixed;right:17px;bottom:19px;width:20px;height:20px';document.body.append(global);
   localStorage.setItem('mailbox-position','17,19');localStorage.setItem('ama-position','23,29');
  },process.env.WORKSHOP_TEST_LINEAGE);
 }
 const root=page.locator('#fixedAssistant'),icon=root.locator('[data-workshop-assistant]'),panel=root.locator('.workshopChatColumn');
 const anchor=async(id='fixedAssistant')=>{
  await page.waitForFunction(id=>{const root=document.getElementById(id),area=root?.querySelector('.workshopEditorContent > [data-page="body"],.dutyWorkshopContent')?.getBoundingClientRect(),icon=root?.querySelector('[data-workshop-assistant]')?.getBoundingClientRect();return area&&icon&&Math.abs(icon.left-area.left-12)<1&&Math.abs(area.bottom-icon.bottom-12)<1;},id);
 };
 const clean=async()=>{assert.equal(await page.evaluate(()=>workshops.fixedAssistant.dirty()),false);assert.equal(await root.locator('[data-workshop-history="undo"]').isDisabled(),true);assert.equal(await root.locator('[data-workshop-history="redo"]').isDisabled(),true);};
 const contained=async(id='fixedAssistant')=>{
  const host=page.locator(`#${id}`),b=await host.locator('.workshopChatColumn').boundingBox(),a=await host.locator('.workshopEditorContent > [data-page="body"],.dutyWorkshopContent').boundingBox();assert.ok(b.x>=a.x-1&&b.y>=a.y-1&&b.x+b.width<=a.x+a.width+1&&b.y+b.height<=a.y+a.height+1,JSON.stringify({a,b}));
 };
 try{
  await page.goto(process.env.WORKSHOP_TEST_ORIGIN);await mount();await anchor();await clean();
  await icon.hover();await panel.waitFor({state:'visible'});await panel.hover();await contained();
  const input=panel.locator('.composer textarea,.composer [contenteditable="true"]').first();await input.fill('Keep unsent assistant input');
  await page.mouse.move(5,5);await page.waitForTimeout(220);assert.equal(await panel.isVisible(),true);
  await input.press('Escape');assert.equal(await panel.isVisible(),false);await icon.press('Enter');await panel.waitFor({state:'visible'});assert.equal(await input.evaluate(n=>n.value??n.textContent),'Keep unsent assistant input');
  await root.locator('[data-workshop-chat]').click();
  for(const source of ['attachment','question']){
   await page.evaluate(source=>Object.values(chatOptions)[0].onInteractionChange(true,source),source);await panel.waitFor({state:'visible'});await page.mouse.click(5,5);await page.waitForTimeout(220);assert.equal(await panel.isVisible(),true);
   await page.evaluate(source=>Object.values(chatOptions)[0].onInteractionChange(false,source),source);await panel.waitFor({state:'hidden'});
  }
  await icon.hover();await page.mouse.move(5,5);await page.evaluate(()=>document.activeElement?.blur());await panel.waitFor({state:'hidden'});
  // Native touch and keyboard keep their open/close semantics without moving.
  await icon.tap();await panel.waitFor({state:'visible'});await icon.tap();await panel.waitFor({state:'hidden'});
  await icon.press('ArrowDown');await icon.press('ArrowRight');await anchor();const before=await icon.boundingBox();await page.mouse.move(before.x+18,before.y+18);await page.mouse.down();await page.mouse.move(900,50,{steps:6});await page.mouse.up();await anchor();
  await icon.press('Escape');
  // Camera and scrolling change content only; the entry belongs to the frame.
  await root.locator('[data-graph-zoom-in]').click();await root.locator('[data-graph-fit]').click();await anchor();
  for(const view of ['list','json','graph']){await root.locator(`[data-structure-view="${view}"]`).click();await anchor();await clean();}
  await page.evaluate(()=>{const area=document.querySelector('#fixedAssistant [data-page="body"]'),probe=document.createElement('div');probe.id='fixedScrollProbe';probe.style.height='2000px';area.append(probe);area.scrollTop=500;});await anchor();
  await page.evaluate(()=>document.getElementById('fixedScrollProbe').remove());
  await root.locator('[data-workshop-info]').click();assert.equal(await root.locator('.workshopInfoOverlay.open').isVisible(),true);await root.locator('[data-close-template-info]').click();await anchor();await icon.hover();await contained();await icon.press('Escape');
  await page.evaluate(()=>{const host=document.getElementById('fixedAssistant');host.style.inset='auto';host.style.left='40px';host.style.top='60px';host.style.width='600px';host.style.height='700px';});await anchor();
  await page.evaluate(()=>document.getElementById('fixedAssistant').style.display='none');await page.waitForTimeout(30);await page.evaluate(()=>document.getElementById('fixedAssistant').style.display='');await anchor();assert.equal(await icon.count(),1);
  await page.evaluate(()=>{const host=document.getElementById('fixedAssistant');host.style.position='relative';host.style.top='0';document.body.style.minHeight='2000px';document.documentElement.style.overflow='auto';window.scrollTo(0,200);});await anchor();
  await page.evaluate(()=>{window.scrollTo(0,0);document.body.style.minHeight='';document.documentElement.style.overflow='';const host=document.getElementById('fixedAssistant');host.style='position:fixed;inset:0;z-index:200;background:var(--panel)';});await anchor();
  // A sibling panel keeps independent input, expansion and anchor ownership.
  await page.evaluate(async()=>{const host=document.createElement('div');host.id='fixedSibling';host.dataset.fixedHost='';host.style='position:fixed;right:0;top:0;width:450px;height:700px;z-index:220';document.body.append(host);window.sibling=PortalTemplateWorkshopLoader.create({root:host,id:'fixed-sibling',target:{},execute});await sibling.show();});await anchor('fixedSibling');
  const sibling=page.locator('#fixedSibling');await sibling.locator('[data-workshop-assistant]').hover();await sibling.locator('.composer textarea,.composer [contenteditable="true"]').first().fill('Sibling input only');assert.equal(await input.evaluate(n=>n.value??n.textContent),'Keep unsent assistant input');await page.evaluate(()=>{sibling.destroy();document.getElementById('fixedSibling').remove();});await anchor();await clean();
  // The same presentation controller anchors an actual duty instance editor.
  await page.evaluate(async lineageId=>{
   const holder=await execute('CreateResident',{lineage_id:lineageId,title:'Fixed entry duty holder',provider:'codex'}),source=(await execute('ListTemplates')).templates.find(template=>template.builtin_key==='scheduled_work');
   const duty=await execute('ApplyTemplate',{lineage_id:lineageId,template_id:source.template_id,target:{kind:'resident',resident_ids:[holder.resident_id]},parameters:{task:'Fixed assistant entry'}});
   const factory=LineageDutyWorkshop.mount;window.LineageDutyWorkshop={...LineageDutyWorkshop,mount(options){const result=factory(options);window.fixedDutyWorkshop=result;return result;}};
   const host=document.createElement('div');host.id='fixedDuty';host.style='position:fixed;right:0;top:0;width:500px;height:700px;z-index:220';document.body.append(host);window.fixedDuty=PortalTemplateWorkshopLoader.create({root:host,id:'fixed-duty',target:{lineage_id:lineageId,function_block_id:duty.function_block_ids[0]},execute});await fixedDuty.show();
  },process.env.WORKSHOP_TEST_LINEAGE);
  await anchor('fixedDuty');await page.locator('#fixedDuty .dutyWorkshopMemory > summary').click();await anchor('fixedDuty');await page.locator('#fixedDuty [data-workshop-assistant]').hover();await contained('fixedDuty');assert.equal(await page.evaluate(()=>fixedDutyWorkshop.dirty()),false);assert.equal(await page.locator('#fixedDuty [data-workshop-history="undo"]').isDisabled(),true);await page.evaluate(()=>{fixedDuty.destroy();document.getElementById('fixedDuty').remove();});
  for(const theme of ['auto','liquid-glass','dark'])for(const width of [1440,600]){
   await page.setViewportSize({width,height:1000});await page.evaluate(theme=>PortalTheme.setMode(theme),theme);await anchor();await icon.press('Escape');await page.mouse.move(5,5);await page.waitForTimeout(200);await page.screenshot({path:`${folder}/fixed-${theme}-${width}.png`});
   await icon.hover();await panel.waitFor({state:'visible'});await contained();await panel.evaluate(async node=>{await Promise.all(node.getAnimations().map(animation=>animation.finished));});await page.screenshot({path:`${folder}/expanded-${theme}-${width}.png`});await icon.press('Escape');
  }
  await clean();assert.equal(await page.evaluate(()=>localStorage.getItem('mailbox-position')),'17,19');assert.equal(await page.evaluate(()=>localStorage.getItem('ama-position')),'23,29');assert.deepEqual(await page.locator('#globalDockSentinel').evaluate(n=>[n.style.right,n.style.bottom]),['17px','19px']);
  // A complete reload uses the fixed position, even with a legacy inline dock.
  await page.reload();await mount();await anchor();await clean();assert.equal(await icon.count(),1);assert.equal(await page.evaluate(()=>fixedPanel.target().lineage_id),process.env.WORKSHOP_TEST_LINEAGE);assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
