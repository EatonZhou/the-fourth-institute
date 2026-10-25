'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const source = process.env.TAU_SESSION_SOURCE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readSessionDeleteDialog, readSessionGraphReady} = require(process.env.TAU_SESSION_DOM_HELPER
  || path.join(source, 'management_portal/test_framework/tau_session_dom_evidence'));
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const residentId = 'resident_fixture123';
    const title = 'Child [TAU:fixture:resident.session]';
    await page.setContent(`<section id="graph"><div data-node-id="${residentId}">Child</div></section>
      <div class="graphNodeSearchResultList"><button data-graph-search-node="${residentId}">Child</button>
      <div class="graphSearchEmpty">Searching current Lineage nodes…</div></div>
      <div role="alertdialog" aria-labelledby="confirmation"><h2 id="confirmation">Delete HATG node?</h2>
      <p>Delete "${title}" from HATG? This marks the Resident as deleted and removes Sessions, graph references,
      and owned workspaces; conversation records and run traces are preserved.</p>
      <button>Cancel</button><button>Delete node</button></div>`);
    const panel = page.locator('#graph'), results = page.locator('.graphNodeSearchResultList');
    const dialog = page.getByRole('alertdialog');
    assert.equal((await readSessionGraphReady(panel,results,{residentId})).ready,false,'local card does not complete asynchronous search');
    await page.locator('.graphSearchEmpty').evaluate(el=>el.remove());
    assert.equal((await readSessionGraphReady(panel,results,{residentId})).ready,true);
    await panel.evaluate(el=>el.setAttribute('aria-busy','true'));
    assert.equal((await readSessionGraphReady(panel,results,{residentId})).ready,false);
    await panel.evaluate(el=>el.removeAttribute('aria-busy'));
    assert.equal((await readSessionGraphReady(panel,results,{residentId:'resident_wrong'})).ready,false);
    await assert.rejects(readSessionGraphReady(panel,panel,{residentId}),/search results/);
    const before = await page.content();
    assert.equal((await readSessionDeleteDialog(dialog,{title})).ready,true);
    assert.equal(await page.content(),before,'helper never mutates the observed DOM');
    assert.equal((await readSessionDeleteDialog(dialog,{title:'Another child'})).ready,false);
    await page.locator('#confirmation').evaluate(el=>el.textContent='Delete Resident?');
    assert.equal((await readSessionDeleteDialog(dialog,{title})).ready,false,'wrong menu dialog cannot be substituted');
    await page.locator('#confirmation').evaluate(el=>el.textContent='Delete HATG node?');
    await dialog.getByRole('button',{name:'Delete node',exact:true}).evaluate(el=>el.disabled=true);
    assert.equal((await readSessionDeleteDialog(dialog,{title})).ready,false);
    await dialog.getByRole('button',{name:'Delete node',exact:true}).evaluate(el=>el.disabled=false);
    await page.locator('#confirmation').evaluate(el=>el.textContent='删除 HATG 节点？');
    await dialog.locator('p').evaluate((el,title)=>el.textContent=`从 HATG 中删除「${title}」？这会将 Resident 标记为已删除，并移除 Session、图引用与自有工作区；对话记录与运行轨迹保留。`,title);
    await dialog.locator('button').last().evaluate(el=>el.textContent='删除节点');
    assert.equal((await readSessionDeleteDialog(dialog,{title})).ready,true);
    await dialog.evaluate(el=>el.insertAdjacentHTML('afterend','<div role="alertdialog">Other dialog</div>'));
    assert.equal((await readSessionDeleteDialog(dialog.first(),{title})).ready,false,'ambiguous top-level confirmation is rejected');
    console.log('Session original DOM: asynchronous search, exact graph target, native deletion name/paragraph/button, disabled/ambiguous/foreign roots and read-only checks passed');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
