'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { observeBrowser } = require('../../../management_portal/test_framework/tau_browser_observer');

(async () => {
  const evidenceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tau-click-observer-'));
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end(request.url.startsWith('/home') ? '<h1>Destination</h1>' : `
      <a id="blocked" href="/home?secret=private-query"><span>private-label</span></a>
      <a id="navigate" href="/home?secret=private-query"><span>private-navigation</span></a>
      <main id="overlay" style="width:100px;height:100px">private-overlay</main>
      <a id="replace" href="/home?secret=private-query"><span>private-replaced-node</span></a>
      <script>
        window.clicks = 0;
        document.querySelector('#blocked').addEventListener('click', event => {
          event.preventDefault(); event.stopImmediatePropagation(); window.clicks += 1;
        });
        document.querySelector('#replace').addEventListener('pointerdown', event => {
          const link = event.currentTarget;
          link.replaceWith(link.cloneNode(true));
          window.replaced = true;
        });
      </script>`);
  });
  let browser, observer;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    observer = await observeBrowser(context, { evidenceDir });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.name));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);
    await observer.action(page, 'blocked-return', async () => {
      await page.locator('#blocked span').click();
      await page.waitForFunction(() => window.clicks === 1);
    });
    assert.equal(page.url(), `${origin}/`, 'passive observer does not force navigation');
    await observer.action(page, 'overlay-hit', () => page.locator('#overlay').click());
    await observer.action(page, 'target-replaced-between-down-up', async () => {
      const box = await page.locator('#replace span').boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.waitForFunction(() => window.replaced === true);
      await page.mouse.up();
    });
    assert.equal(page.url(), `${origin}/`, 'replacement behavior is not repaired by observation');
    await observer.action(page, 'normal-return', async () => {
      await page.locator('#navigate span').click();
      await page.waitForURL('**/home?secret=private-query');
      await page.locator('h1').waitFor();
    });
    await observer.flush();
    observer.stop();
    const output = fs.readFileSync(observer.file, 'utf8');
    const rows = output.trim().split('\n').map(line => JSON.parse(line));
    const clicks = rows.filter(row => row.kind === 'click_observed');
    const captures = clicks.filter(row => row.stage === 'capture');
    assert.equal(captures.length, 3, 'removed down target does not manufacture a click receipt');
    const blocked = captures[0];
    assert.equal(blocked.target_tag, 'span');
    assert.equal(blocked.control_tag, 'a');
    assert.equal(blocked.link_path, '/home');
    assert.equal(blocked.default_prevented, false);
    assert.equal(blocked.is_trusted, true);
    assert.ok(blocked.document_confirmed && blocked.action_id);
    assert.ok(blocked.target_indices.length);
    const finalBlocked = clicks.filter(row => row.click_id === blocked.click_id && row.stage !== 'capture');
    assert.equal(finalBlocked.length, 1);
    assert.equal(finalBlocked[0].default_prevented, true, 'late preventDefault survives stopped propagation');
    assert.equal(finalBlocked[0].document_id, blocked.document_id);
    assert.equal(captures[1].target_tag, 'main');
    assert.equal(captures[1].control_tag, null);
    const pointers = rows.filter(row => row.kind === 'pointer_observed');
    const pointerCaptures = pointers.filter(row => row.stage === 'capture');
    assert.equal(pointerCaptures.length, 8, 'each native down/up has an independent receipt');
    const firstDown = pointerCaptures[0], firstUp = pointerCaptures[1];
    assert.equal(firstDown.event_type, 'pointerdown');
    assert.equal(firstUp.event_type, 'pointerup');
    assert.equal(firstDown.pointer_id, firstUp.pointer_id);
    assert.equal(firstDown.pointer_type, 'mouse');
    assert.equal(firstDown.target_node_id, firstUp.target_node_id);
    assert.equal(firstDown.target_node_id, blocked.target_node_id);
    assert.ok(firstDown.is_trusted && firstDown.action_id && firstDown.document_confirmed);
    const replacedDown = pointerCaptures[4], replacedUp = pointerCaptures[5];
    assert.notEqual(replacedDown.target_node_id, replacedUp.target_node_id, 'identical markup is not identical node identity');
    assert.equal(replacedDown.hit_node_id, replacedDown.target_node_id);
    assert.equal(replacedDown.target_connected, true);
    const detached = pointers.find(row => row.pointer_event_id === replacedDown.pointer_event_id && row.stage === 'after_dispatch');
    assert.equal(detached.target_connected, false, 'original node removal is observed at dispatch completion');
    assert.equal(replacedDown.document_id, replacedUp.document_id);
    assert.equal(replacedDown.action_id, replacedUp.action_id);
    assert.ok(!captures.some(row => row.action_id === replacedDown.action_id), 'missing native click stays missing');
    const finalNavigation = clicks.filter(row => row.click_id === captures[2].click_id && row.stage !== 'capture');
    assert.equal(finalNavigation.length, 1, 'navigation preserves its original dispatch result');
    assert.equal(finalNavigation[0].default_prevented, false);
    assert.deepEqual(errors, []);
    for (const secret of ['private-query', 'private-label', 'private-navigation', 'private-overlay', 'private-replaced-node']) {
      assert.equal(output.includes(secret), false, 'click observation remains content-free');
    }
    console.log('Native down/up/click identities and detached target lifecycle observed without changing navigation or exposing content');
  } finally {
    observer?.stop();
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(evidenceDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
