'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {HATG_PANEL_STYLES}=require('./hatg_panel_styles.cjs');
const root=path.resolve(__dirname,'../../../management_portal');
(async()=>{
 const browser=await chromium.launch({headless:true}),samples=[];
 try{
  const page=await browser.newPage();
  await page.setContent('<!doctype html><html><body style="margin:0"><div id="fixture" class="portalHatgPanelComponent"></div></body></html>');
  await page.evaluate(html=>{
   const t=document.createElement('template');t.innerHTML=html;
   const viewport=t.content.querySelector('#hatgViewport').cloneNode(false);
   viewport.style.cssText='height:500px;width:100%;position:relative';
   viewport.append(t.content.querySelector('#graphNavigationBar'));
   if (!viewport.querySelector('#graphNavigator')) viewport.append(t.content.querySelector('#graphNavigator'));
   document.querySelector('#fixture').append(viewport);
  },fs.readFileSync(path.join(root,'hatg-panel.html'),'utf8'));
  for(const name of ['portal_design_system','portal_themes','portal_components',...HATG_PANEL_STYLES])
   await page.addStyleTag({path:path.join(root,'static',name+'.css')});
  for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay'])
   await page.addScriptTag({path:path.join(root,'static',name+'.js')});
  await page.evaluate(()=>{
   PortalUIPrimitives.mountIcons(document);
   const trigger=document.querySelector('#graphMoreToggle'),panel=document.querySelector('#graphMoreMenu');
   window.menu=PortalOverlay.bindPopover({trigger,panel,placement:'bottom-end',portal:true,closeOnOutside:true,closeOnEscape:true});
   trigger.addEventListener('click',()=>menu.toggle());
   for(const id of ['autoLayoutBtn','graphLeaderJumpBtn'])document.getElementById(id).disabled=false;
   window.actions=0;document.querySelector('#autoLayoutBtn').addEventListener('click',()=>window.actions++);
  });
  for(const width of [320,944]){
   await page.setViewportSize({width,height:650});
   for(const language of ['en','zh-CN']){
    await page.evaluate(language=>{
     document.documentElement.lang=language;
     document.querySelector('#autoLayoutBtn span').textContent=language==='en'?'Spacious layout':'舒展整理';
     document.querySelector('#graphRelationFilter option').textContent=language==='en'?'All Context relationships':'全部 Context 关系';
     document.querySelector('#graphLeaderJumpBtn').textContent='Leader';
     document.querySelector('#graphFocusModeBtn').textContent=language==='en'?'Neighborhood':'邻域';
    },language);
    for(const theme of ['light','dark']){
     await page.evaluate(theme=>document.documentElement.dataset.portalTheme=theme,theme);
     await page.locator('#graphMoreToggle').click();
     const s=await page.evaluate(()=>{
      const bar=document.querySelector('#graphNavigationBar'),m=document.querySelector('#graphMoreMenu'),b=document.querySelector('#autoLayoutBtn'),search=document.querySelector('#graphNavigator');
      const style=getComputedStyle(m);
      const skin=getComputedStyle(search);
      return {parent:m.parentElement.tagName,display:style.display,font:style.fontFamily,fontSize:style.fontSize,bg:style.backgroundColor,border:style.borderRadius,rect:m.getBoundingClientRect().toJSON(),bar:bar.getBoundingClientRect().toJSON(),search:search.getBoundingClientRect().toJSON(),searchSkin:{border:skin.borderWidth,background:skin.backgroundColor,shadow:skin.boxShadow},combined:bar.contains(b)&&bar.contains(document.querySelector('#graphMoreToggle'))&&bar.contains(search),controls:[...m.querySelectorAll('button,select')].map(e=>({rect:e.getBoundingClientRect().toJSON(),fontSize:getComputedStyle(e).fontSize}))};
     });
     samples.push({width,language,theme,...s});
     const dividers=await page.locator('#graphFitBtn, .graphCanvasActions').evaluateAll(items=>items.map(el=>getComputedStyle(el).borderLeftWidth));
     assert.ok(dividers.every(width=>width==='0px'),'no internal divider lines in the shared toolbar');
     assert.equal(s.parent,'BODY','exercise the real portalled popover');
     assert.equal(s.display,'grid','portalled tools retain their scoped menu skin');
     assert.equal(s.combined,true,'zoom, arrange, dropdown and search share one frame');
     assert.equal(s.fontSize,'12px');
     assert.notEqual(s.bg,'rgba(0, 0, 0, 0)');
     assert.ok(s.rect.left>=0&&s.rect.right<=width,'menu stays in viewport');
     assert.ok(s.search.left>=s.bar.left&&s.search.right<=s.bar.right,'search stays inside the shared frame');
     assert.deepEqual(s.searchSkin,{border:'0px',background:'rgba(0, 0, 0, 0)',shadow:'none'},'search has no second box');
     for(const c of s.controls){assert.equal(c.fontSize,'12px');assert.ok(c.rect.height>=30);assert.ok(c.rect.left>=s.rect.left&&c.rect.right<=s.rect.right);}
     if(process.env.HATG_TOOLBAR_EVIDENCE){fs.mkdirSync(process.env.HATG_TOOLBAR_EVIDENCE,{recursive:true});await page.screenshot({path:path.join(process.env.HATG_TOOLBAR_EVIDENCE,`toolbar-${width}-${language}-${theme}.png`)});}
     await page.locator('#graphRelationFilter').focus();await page.keyboard.press('Escape');
     assert.equal(await page.locator('#graphMoreMenu').isVisible(),false);
     await page.waitForFunction(()=>document.querySelector('#graphMoreToggle')===document.activeElement);
     assert.equal(await page.locator('#graphMoreToggle').evaluate(e=>e===document.activeElement),true);
     await page.keyboard.press('Enter');assert.equal(await page.locator('#graphMoreMenu').isVisible(),true);
     await page.mouse.click(5,480);assert.equal(await page.locator('#graphMoreMenu').isVisible(),false);
     if(process.env.HATG_TOOLBAR_EVIDENCE)await page.screenshot({path:path.join(process.env.HATG_TOOLBAR_EVIDENCE,`closed-${width}-${language}-${theme}.png`)});
    }
   }
  }
  await page.locator('#autoLayoutBtn').click();assert.equal(await page.evaluate(()=>actions),1);
  for (const width of [320,944]) {
   await page.setViewportSize({width,height:650});
   await page.evaluate(()=>document.querySelector('#graphNodeSearchCluster').dataset.expanded='true');
   const expanded=await page.evaluate(()=>{
    const bar=document.querySelector('#graphNavigationBar').getBoundingClientRect(),search=document.querySelector('#graphNavigator').getBoundingClientRect();
    return {bar:bar.toJSON(),search:search.toJSON()};
   });
   assert.ok(expanded.search.left>=expanded.bar.left&&expanded.search.right<=expanded.bar.right&&expanded.search.top>=expanded.bar.top&&expanded.search.bottom<=expanded.bar.bottom,'expanded search stays in the same frame');
   assert.ok(expanded.search.left>=0 && expanded.search.right<=width);
   await page.locator('#graphNodeSearchInput').fill('fixture query');
   assert.equal(await page.locator('#graphNodeSearchInput').inputValue(),'fixture query');
   samples.push({width,expanded:true,...expanded});
  }
  if(process.env.HATG_TOOLBAR_EVIDENCE)fs.writeFileSync(path.join(process.env.HATG_TOOLBAR_EVIDENCE,'geometry.json'),JSON.stringify(samples,null,2));
  console.log(JSON.stringify({samples:samples.length,actions:await page.evaluate(()=>actions)}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
