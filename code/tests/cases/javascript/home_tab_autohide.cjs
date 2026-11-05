'use strict';
const assert=require('node:assert/strict'),path=require('node:path');

// Exercise real Home composition, persisted-layout restoration and nested
// Workbench ownership through the isolated native Home transport fixture.
module.exports=async function(page,out,viewport) {
  await page.evaluate(()=>{
    const home=PortalWorkbench.get('home');
    home.setLayoutLocked(false);
    home.applyWorkspace({name:'Grouped Home',layout:PortalWorkbench.model.stack(['agent-status','dialogue-display']),hidden:['get-me-aligned','my-pulse-publish']});
    PortalWorkbench.get('home-agent-status').applyWorkspace({name:'Accounts',layout:PortalWorkbench.model.stack(['agent-accounts'])});
  });
  const stack=page.locator('.portalWorkbenchStack[data-auto-hide-tabs="true"]').first();
  const tabs=stack.locator(':scope > .portalWorkbenchTabs');
  const tab=id=>tabs.locator(`[data-portal-workbench-tab="${id}"]`);
  const snap=()=>stack.evaluate(el=>{
    const tabs=el.querySelector(':scope > .portalWorkbenchTabs'),body=el.querySelector(':scope > .portalWorkbenchStackBody');
    const r=el.getBoundingClientRect(),t=tabs.getBoundingClientRect(),b=body.getBoundingClientRect();
    const style=getComputedStyle(tabs),close=tabs.querySelector('.portalWorkbenchTabClose'),c=close.getBoundingClientRect();
    const hit=document.elementFromPoint(c.x+c.width/2,c.y+c.height/2);
    return {stack:r.toJSON(),tabs:t.toJSON(),body:b.toJSON(),display:style.display,position:style.position,
      transform:style.transform,opacity:Number(style.opacity),revealed:el.classList.contains('is-tab-strip-revealed'),
      closeHit:close===hit||close.contains(hit),headerBottom:document.querySelector('.portalSiteHeader').getBoundingClientRect().bottom};
  });
  const leave=async()=>{const r=await stack.boundingBox();await page.mouse.move(r.x+r.width/2,r.y+110);};
  const reveal=async()=>{const r=await stack.boundingBox();await page.mouse.move(r.x+r.width-70,r.y+2);};
  const result={viewport,mobile:await page.evaluate(()=>document.documentElement.dataset.portalViewport==='mobile')};
  if(result.mobile) {
    // Touch retains the bottom switcher and the dialogue close/header path.
    await page.evaluate(()=>PortalWorkbench.get('home').focusPanel('dialogue-display'));
    await tabs.waitFor({state:'visible'});
    result.touch=await snap();
    assert.equal(result.touch.transform,'none');assert.equal(result.touch.position,'static');
    await page.screenshot({path:path.join(out,`tabs-touch-${viewport.width}.png`)});
    await tabs.locator('.portalWorkbenchTabClose[aria-label$="对话与展示"]').tap();
    assert.ok(await page.evaluate(()=>PortalWorkbench.get('home').getState().hidden.includes('dialogue-display')));
    return result;
  }
  await leave();result.hidden=await snap();
  assert.ok(result.hidden.tabs.bottom<=result.hidden.stack.top+5,'grouped header retracts to edge');
  assert.ok(Math.abs(result.hidden.body.top-result.hidden.stack.top)<=2,'no reserved header row');
  await page.screenshot({path:path.join(out,`tabs-hidden-${viewport.width}.png`)});
  const nested=page.locator('[data-portal-workbench-tab="agent-accounts"]').first();
  await nested.hover();
  assert.equal((await snap()).revealed,false,'nested header does not reveal the outer header');
  assert.equal(await nested.evaluate(el=>getComputedStyle(el.parentElement).transform),'none','nested header remains visible');
  await reveal();result.revealed=await snap();
  assert.ok(result.revealed.closeHit,'revealed close accepts a real pointer');
  assert.ok(result.revealed.tabs.top>=result.revealed.headerBottom,'tabs stay below navigation');
  assert.equal(result.revealed.body.top,result.hidden.body.top,'reveal does not move content');
  await page.screenshot({path:path.join(out,`tabs-revealed-${viewport.width}.png`)});
  await tabs.evaluate(el=>{window.homeTabDragCount=0;el.addEventListener('dragstart',()=>window.homeTabDragCount++);});
  await tab('agent-status').dragTo(tab('dialogue-display'));
  assert.equal(await page.evaluate(()=>window.homeTabDragCount),1,'native tab dragging still starts');
  assert.equal(await tabs.locator('[data-portal-workbench-tab]').count(),2,'drop retains both grouped panels');
  await tab('dialogue-display').click();await leave();
  result.afterSwitch=await snap();
  assert.ok(result.afterSwitch.tabs.bottom<=result.afterSwitch.stack.top+5,'mouse switching does not pin the header open');
  await page.setViewportSize({width:1000,height:800});await leave();
  result.resized=await snap();
  assert.ok(result.resized.tabs.bottom<=result.resized.stack.top+5,'resize retains folding');
  await reveal();assert.ok((await snap()).closeHit,'close remains reachable after resize');
  await page.screenshot({path:path.join(out,'tabs-resized-1000.png')});
  const homeAction=require('./home_dialogue_controls.cjs');
  await leave();
  await homeAction(page,'展开');await homeAction(page,'返回原布局');await leave();
  result.returned=await snap();
  assert.ok(result.returned.tabs.bottom<=result.returned.stack.top+5,'restored layout retains folding');
  // Keyboard focus keeps a revealed strip reachable after the mouse leaves.
  await page.keyboard.press('Tab');await tab('dialogue-display').focus();
  assert.ok((await snap()).closeHit,'keyboard focus reveals controls');
  await page.keyboard.press('Tab');
  await tabs.locator('.portalWorkbenchTabClose[aria-label$="对话与展示"]').focus();
  await page.keyboard.press('Enter');
  assert.ok(await page.evaluate(()=>PortalWorkbench.get('home').getState().hidden.includes('dialogue-display')));
  await page.evaluate(()=>document.activeElement.blur());await leave();
  result.single=await snap();
  assert.ok(result.single.tabs.bottom<=result.single.stack.top+5,'remaining single tab also retracts');
  await reveal();
  await tabs.locator('.portalWorkbenchTabClose[aria-label$="Agent 状态"]').click();
  assert.ok(await page.evaluate(()=>PortalWorkbench.get('home').getState().hidden.includes('agent-status')));
  return result;
};
