'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const source = process.env.TAU_WATCHBOARD_SOURCE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWatchboardPanel, readBusinessScrollport} = require(process.env.TAU_WATCHBOARD_DOM_HELPER
  || path.join(source, 'management_portal/test_framework/tau_watchboard_dom_evidence'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    await page.setContent(`<style>.watchboardSectionHead{text-transform:uppercase}aside{width:320px}
      #messages{width:320px;height:80px;overflow:auto}p{margin:0;height:30px}</style>
      <aside id="chatWatchboard" class="watchboard" aria-label="Resident Watchboard">
      <div class="watchboardHead"><div class="watchboardTitle">Fixture</div></div>
      <div class="watchboardBody"><section class="watchboardSection">
        <div class="watchboardSectionHead"><span>Run Controls</span><span>unknown</span></div>
        <div class="watchboardSectionBody"><button data-watchboard-run-command="status">Refresh status</button>
          <div class="watchboardMiniActions"><button data-watchboard-config-open="watchboard/config.json">Full config</button>
          <button data-watchboard-status-open>Raw status</button></div><p id="lastRun">No run records yet.</p></div>
      </section></div></aside><div id="messages">${Array.from({length: 12}, (_, i) => `<p id="m${i}">Message ${i}</p>`).join('')}</div>
      <div id="chatInput" role="textbox" contenteditable="true"><span id="draft">Draft</span></div>`);
    const board = page.locator('#chatWatchboard');
    const initial = await readWatchboardPanel(board);
    assert.equal(initial.status_text, 'unknown', 'unknown is observed, never changed to assumed idle');
    assert.equal(initial.status_visible, true); assert.equal(initial.body_visible, true);
    assert.equal(await page.locator('.watchboardSectionHead > span').nth(0).innerText(), 'RUN CONTROLS');
    assert.equal(await page.locator('.watchboardSectionHead > span').nth(1).innerText(), 'UNKNOWN');
    assert.deepEqual(initial.commands, [{command: 'status', visible: true, enabled: true}]);
    assert.deepEqual(initial.configs, [{path: 'watchboard/config.json', visible: true, enabled: true}]);
    await assert.rejects(readWatchboardPanel(page.locator('.watchboardMiniActions')), /full Watchboard/);
    await assert.rejects(readBusinessScrollport(page.locator('#chatInput'), page.locator('#draft')), /composer/);
    const noOverflow = await readBusinessScrollport(board, page.locator('#lastRun'));
    assert.equal(noOverflow.has_vertical_overflow, false); assert.equal(noOverflow.last.center_hit, true);
    const before = await readBusinessScrollport(page.locator('#messages'), page.locator('#m11'));
    assert.equal(before.has_vertical_overflow, true); assert.equal(before.last.within_container, false);
    await page.locator('#messages').hover(); await page.mouse.wheel(0, 900);
    await page.waitForFunction(() => document.getElementById('messages').scrollTop > 0);
    const after = await readBusinessScrollport(page.locator('#messages'), page.locator('#m11'));
    assert.ok(after.scrollTop > before.scrollTop); assert.equal(after.last.within_container, true);
    assert.equal(after.last.center_hit, true); assert.equal(after.has_horizontal_overflow, false);
    // Fixture changes represent collapsed and backend-error states, never a real Portal action.
    await page.locator('.watchboardBody').evaluate(node => { node.hidden = true; });
    assert.equal((await readWatchboardPanel(board)).body_visible, false);
    await page.locator('.watchboardBody').evaluate(node => { node.hidden = false; node.insertAdjacentHTML('beforeend','<p class="consoleError">Failure</p>'); });
    assert.equal((await readWatchboardPanel(board)).errors_visible, 1);
    console.log('Watchboard original DOM evidence: uppercase, exact root, unknown/error/collapse, composer rejection and native scrolling passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
