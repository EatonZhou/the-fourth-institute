'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const source=process.env.TAU_CODE_ROOT || path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(source,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readMailboxPinDom}=require(process.env.TAU_MAILBOX_PREREQUISITE_MODULE || path.join(source,'management_portal/test_framework/tau_mailbox_prerequisite_dom'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage(),residentId='resident_123456789abc';
  await page.setContent(`<section role="dialog" aria-modal="true" aria-labelledby="nodeSettingsTitle"><h2 id="nodeSettingsTitle">Menu</h2><code id="nodeResidentIdentityReadout">${residentId}</code><button id="nodeMenuPin" aria-label="Pin" aria-pressed="false"> </button></section>`);
  const menu=page.getByRole('dialog',{name:'Menu',exact:true});
  const before=await page.content();
  assert.equal((await readMailboxPinDom(menu,{residentId,pinned:false})).ready,true);
  assert.equal(await page.content(),before,'reading must not click, refresh or mutate');
  await menu.locator('#nodeMenuPin').evaluate(el=>{el.setAttribute('aria-label','Unpin');el.setAttribute('aria-pressed','true');});
  const pinned=await readMailboxPinDom(menu,{residentId,pinned:true});
  assert.equal(pinned.ready,true);assert.equal(pinned.observed_label,'Unpin');
  assert.equal((await readMailboxPinDom(menu,{residentId,pinned:false})).ready,false);
  await menu.locator('#nodeMenuPin').evaluate(el=>el.setAttribute('aria-label','取消置顶'));
  assert.equal((await readMailboxPinDom(menu,{residentId,pinned:true})).ready,true,'read actual translated name; never guess a future label');
  await menu.locator('#nodeMenuPin').evaluate(el=>el.disabled=true);
  assert.equal((await readMailboxPinDom(menu,{residentId,pinned:true})).ready,false);
  await menu.locator('#nodeMenuPin').evaluate(el=>el.disabled=false);
  assert.equal((await readMailboxPinDom(menu,{residentId:'resident_abcdef123456',pinned:true})).ready,false);
  await page.evaluate(()=>document.body.insertAdjacentHTML('beforeend','<section role="alertdialog">Confirm</section>'));
  assert.equal((await readMailboxPinDom(menu,{residentId,pinned:true})).ready,false,'another top dialog cannot be ignored');
  await page.getByRole('alertdialog').evaluate(el=>el.remove());
  await menu.evaluate(el=>el.hidden=true);
  await assert.rejects(readMailboxPinDom(menu,{residentId,pinned:true}),/one original Menu/);
  console.log('Mailbox Pin DOM: exact visible Menu/Resident, original stable control, both observed languages, pressed-state transition, disabled/foreign/obscured/hidden negatives and non-mutation passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
