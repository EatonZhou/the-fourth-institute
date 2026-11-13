const path=require('node:path'),assert=require('node:assert/strict');
const {installReaderChat}=require('./reader_shared_chat.cjs');
const focusedStyle=e=>{const s=getComputedStyle(e);return {focused:e===document.activeElement,...Object.fromEntries(['outlineStyle','outlineWidth','outlineColor','boxShadow','borderColor'].map(k=>[k,s[k]]))};};
(async()=>{const [url,name,cookie,token]=process.argv.slice(2),browser=await require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[name].launch({headless:true});try{
const toggleOnly=process.env.READER_TOGGLE_ONLY==='1',timelineOnly=process.env.READER_TIMELINE_ONLY==='1',splitOnly=process.env.READER_SPLIT_ONLY==='1';
for(const phase of ['current']){
 const ctx=await browser.newContext({viewport:{width:1280,height:900}});await ctx.addCookies([{name:cookie,value:token,url}]);const page=await ctx.newPage(),prepares=[],errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(10000);
 page.on('request',r=>{if(r.url().endsWith('/chat-workspace/api/prepare'))prepares.push(r.postDataJSON());});
 await page.goto(url);await page.waitForFunction(()=>ready);await installReaderChat(page);
 let homeFocus;
 if(!toggleOnly&&!timelineOnly&&!splitOnly){const homeInput=page.locator('#ordinaryChat [role=textbox]');await homeInput.focus();homeFocus=await homeInput.evaluate(focusedStyle);assert(homeFocus.focused);}
 await page.evaluate(()=>openDocument());await page.waitForFunction(()=>signals.length===1);
 await page.getByRole('button',{name:'打开 AI',exact:true}).click();
 await page.locator('.paperReaderChatHost [role=textbox]').waitFor();
 assert(await page.evaluate(()=>document.querySelector('.paperReaderChatHost').contains(sharedChatRoot)));
 if(splitOnly){
  const split=page.getByRole('separator',{name:'调整 AI 侧栏宽度'}),panel=page.locator('.paperReaderSidePanel');
  await split.waitFor();assert.equal(await page.locator('.paperAIClose').count(),0);
  const input=page.locator('.paperReaderChatHost [role=textbox]');await input.fill('Retain my draft');
  await page.evaluate(()=>window.savedFrame=document.querySelector('.paperReadingViewer').contentWindow);
  const width=()=>panel.evaluate(e=>e.getBoundingClientRect().width),start=await width(),count=prepares.length;
  const box=await split.boundingBox();await page.mouse.move(box.x+3,box.y+80);await page.mouse.down();await page.mouse.move(box.x-120,box.y+80,{steps:6});await page.mouse.up();
  assert(await width()>start+90);assert.equal(await input.innerText(),'Retain my draft');assert.equal(prepares.length,count);
  assert(await page.evaluate(()=>savedFrame===document.querySelector('.paperReadingViewer').contentWindow));
  await split.focus();const before=await width();await page.keyboard.press('ArrowRight');assert(await width()<before);
  const retained=await width();await page.locator('.paperReadingToolbar').getByRole('button',{name:'收起 AI',exact:true}).click();
  await page.locator('.paperReadingToolbar').getByRole('button',{name:'打开 AI',exact:true}).click();await split.waitFor();assert(Math.abs(await width()-retained)<2);
  await page.setViewportSize({width:640,height:900});await page.waitForFunction(()=>Number(document.querySelector('.paperAISplitter').getAttribute('aria-valuenow'))<=Number(document.querySelector('.paperAISplitter').getAttribute('aria-valuemax')));
  assert(await width()<=320);assert(await width()>=120);
  const next=await split.boundingBox();await page.mouse.move(next.x+3,next.y+80);await page.mouse.down();
  await page.evaluate(()=>window.dispatchEvent(new Event('blur')));assert.equal(await page.evaluate(()=>document.body.classList.contains('resizingReaderAI')),false);await page.mouse.up();
  assert.equal(await page.locator('.paperAIClose').count(),0);assert.deepEqual(errors,[]);
  await page.evaluate(()=>host.close());await ctx.close();console.log(name+': PASS real splitter pointer across PDF/keyboard/clamp/iframe and draft identity/retained width/blur cleanup; no model');continue;
 }
 if(timelineOnly){
  // Synthetic measured message rows; buttons are created by the real shared
  // RoundRail owner, never hand-built button HTML or model output.
  await page.evaluate(()=>{
   window.probeRails=[];
   for(const place of ['inside','outside']){
    const root=document.createElement('div');root.id='rail-'+place;root.style.cssText='height:240px;width:300px;background:white;z-index:100;'+(place==='inside'?'position:absolute;top:80px;left:0':'position:fixed;top:80px;left:50px');
    (place==='inside'?document.querySelector('.paperReaderChatHost'):document.body).append(root);
    const viewport=document.createElement('div');viewport.style.cssText='height:240px;overflow:auto';
    viewport.innerHTML='<div class="messageList"><div data-message-key="probe-question" style="height:180px">Synthetic question row</div></div>';root.append(viewport);
    const message={message_id:'probe-question',role:'human',content:'Synthetic question'};
    window.probeRails.push(PortalChatWorkspaceRoundRail.create({root,viewport,state:{messages:[message],messageByKey:new Map([['probe-question',message]]),messageRenderOptions:{}},scope:()=>place,keyFor:m=>m.message_id,roleFor:m=>m.role,reveal:()=>{},scrollTo:()=>{},loadOlder:()=>{},revalidate:()=>{},previewText:()=>({text:'Synthetic question'})}));
   }
  });
  const inside=page.locator('#rail-inside .chatRoundNode'),outside=page.locator('#rail-outside .chatRoundNode');
  await inside.waitFor({state:'visible'});await outside.waitFor({state:'visible'});
  const style=el=>{const s=getComputedStyle(el);return Object.fromEntries(['borderTopWidth','borderTopStyle','borderTopColor','borderRadius','paddingTop','paddingRight','paddingBottom','paddingLeft','minHeight','outlineStyle','outlineWidth','outlineOffset','backgroundColor'].map(k=>[k,s[k]]));};
  const results={};
  for(const phase of ['default','hover','focus']){
   const sample=async node=>{
    await page.evaluate(inside=>{const d=document.querySelector('.paperReadingDialog');if(inside&&!d.open)d.showModal();if(!inside&&d.open)d.close();},node===inside);
    await page.mouse.move(0,0);await page.evaluate(()=>document.activeElement?.blur());
    if(phase==='hover')await node.hover();
    if(phase==='focus'){await page.keyboard.press('Tab');await node.focus();assert(await node.evaluate(e=>e.matches(':focus-visible')));}
    return node.evaluate(style);
   };
   results[phase]={outside:await sample(outside),inside:await sample(inside)};
   assert.deepEqual(results[phase].inside,results[phase].outside,phase+' timeline must retain shared Chat styles');
  }
  assert.equal(results.default.inside.minHeight,'0px');assert.equal(results.default.inside.paddingTop,'0px');assert.equal(results.default.inside.borderTopWidth,'0px');
  console.log(name+': PASS real RoundRail node default/hover/focus parity '+JSON.stringify(results));
  await page.evaluate(()=>{probeRails.forEach(r=>r.destroy());host.close();});await ctx.close();continue;
 }
 if(toggleOnly){
  const toolbar=page.locator('.paperReadingToolbar');
  const expanded=toolbar.getByRole('button',{name:'收起 AI',exact:true});
  assert.equal(await expanded.getAttribute('aria-expanded'),'true');
  await expanded.click();assert.equal(await page.locator('.paperReaderSidePanel').isVisible(),false);assert(await page.evaluate(()=>document.getElementById('ordinaryChat').contains(sharedChatRoot)));
  const collapsed=toolbar.getByRole('button',{name:'打开 AI',exact:true});
  assert.equal(await collapsed.getAttribute('aria-expanded'),'false');
  await collapsed.click();await page.locator('.paperReaderChatHost [role=textbox]').waitFor();
  assert.equal(await toolbar.getByRole('button',{name:'收起 AI',exact:true}).getAttribute('aria-expanded'),'true');
  assert.deepEqual(prepares,[]);assert.equal(await page.evaluate(()=>sharedPanel.surface().workspace_key),'paper:paper_0123456789ab');
  await page.locator('.paperReadingToolbar').getByRole('button',{name:'收起 AI',exact:true}).click();
  assert.equal(await collapsed.getAttribute('aria-expanded'),'false');
  assert.deepEqual(errors,[]);await page.evaluate(()=>host.close());await ctx.close();
  console.log(name+': PASS toolbar open/collapse/reopen and sidebar collapse keep aria-expanded in sync; same paper key; no model');continue;
 }

 for(const width of [1280,720]){
  await page.setViewportSize({width,height:900});
  await page.screenshot({path:path.join(process.env.TMPDIR,`reader-style-${name}-${width}.png`)});
  const layout=await page.evaluate(()=>{const panel=document.querySelector('.paperReaderSidePanel'),header=document.querySelector('.paperReadingToolbar');return {overflow:document.documentElement.scrollWidth>innerWidth,panelWidth:panel.getBoundingClientRect().width,buttons:[...header.querySelectorAll('button,a')].map(e=>({height:e.getBoundingClientRect().height,font:getComputedStyle(e).fontSize,right:e.getBoundingClientRect().right})),same:document.querySelector('.paperReaderChatHost').contains(sharedChatRoot)};});
  assert.equal(layout.overflow,false);assert(layout.panelWidth>=280&&layout.panelWidth<=365,JSON.stringify(layout));assert(layout.buttons.every(b=>b.height<=32&&b.right<=width+1&&parseFloat(b.font)<=13),JSON.stringify(layout));assert(layout.same);
 }
 const input=page.locator('.paperReaderChatHost [role=textbox]');await input.fill('Current paper follow-up draft');await input.focus();
 const borrowedFocus=await input.evaluate(focusedStyle);assert(borrowedFocus.focused);
 assert.deepEqual(borrowedFocus,homeFocus,'borrowing Chat preserves its own focus styling');
 await page.locator('.paperReadingToolbar').getByRole('button',{name:'收起 AI',exact:true}).click();
 assert(await page.evaluate(()=>document.getElementById('ordinaryChat').contains(sharedChatRoot)));
 const outer=page.frames()[1];await outer.frameLocator('iframe').first().locator('.textLayer span').first().dblclick();await outer.locator('.portalAISelection').click();
 await input.waitFor();assert((await input.innerText()).startsWith('Current paper follow-up draft'));
 assert((await input.innerText()).length>'Current paper follow-up draft'.length);
 await page.getByText('确认保存回答为便签',{exact:true}).click();assert.equal(await page.getByRole('button',{name:'确认保存便签',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:/引用选区/}).click();assert.equal(await outer.locator('#pageNumber').inputValue(),'1');
 assert.deepEqual(prepares,[]);assert.deepEqual(errors,[]);
 await page.evaluate(()=>host.close());assert(await page.evaluate(()=>document.getElementById('ordinaryChat').contains(sharedChatRoot)));await ctx.close();
}
console.log(name+': PASS shared Chat root/draft/selection/borrow-return and Reader chrome; no conversation allocation or model');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
