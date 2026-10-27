'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const codeRoot = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWallChatPanel} = require(path.join(codeRoot, 'management_portal/test_framework/tau_detail_prerequisites'));

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    let requests = 0;
    page.on('request', () => requests++);
    await page.evaluate(html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      document.body.append(doc.getElementById('wallNativeView').content.querySelector('#wallDetail'));
      const root = document.getElementById('wallDetail');
      root.hidden = false; root.setAttribute('aria-hidden', 'false');
      // Keep the native initial selection and hidden single-tab header intact.
      const discussion = document.getElementById('wallChat');
      discussion.innerHTML = '<span data-workspace-resident-id="resident_discussion">Discussion</span>'
        + '<article class="messageItem" data-message-key="msg_original">Original discussion</article>'
        + '<div role="textbox" contenteditable="true" style="width:300px;min-height:30px"></div>';
    }, fs.readFileSync(path.join(codeRoot, 'management_portal/wall.html'), 'utf8'));
    const root = page.locator('#wallDetail');
    const sample = () => readWallChatPanel(root, {kind: 'discussion'});
    const originalDom = await page.content();
    assert.equal(await root.getByRole('tab').count(), 0, 'Native single-tab header is hidden');
    const selected = await sample();
    assert.equal(selected.tab_id, 'wallDiscussionTab');
    assert.equal(selected.tab_visible, false);
    assert.equal(selected.tablist_visible, false);
    assert.equal(selected.selected_tab, 'discussion');
    assert.equal(selected.panel_id, 'wallChat');
    assert.equal(selected.panel_visible, true);
    assert.equal(selected.resident_id, 'resident_discussion');
    assert.deepEqual(selected.message_keys, ['msg_original']);
    assert.equal(selected.composer_editable, true);
    assert.equal((await readWallChatPanel(root, {kind: 'work', messageId: 'msg_original'})).panel_visible, false);
    assert.equal(await page.content(), originalDom, 'Sampling must not unhide tabs or change selection');
    assert.equal(requests, 0, 'Sampling must not fetch or reopen history');

    // Selection alone is insufficient when the controlled panel is unavailable.
    await page.locator('#wallChat').evaluate(node => { node.hidden = true; });
    assert.equal((await sample()).panel_visible, false);
    await page.locator('#wallChat').evaluate(node => { node.hidden = false; node.setAttribute('aria-labelledby', 'wallWorkTab'); });
    assert.equal((await sample()).selected_tab, '');
    assert.equal((await sample()).panel_visible, false);
    await page.locator('#wallChat').evaluate(node => node.setAttribute('aria-labelledby', 'wallDiscussionTab'));
    await page.locator('#wallDiscussionTab').evaluate(node => node.setAttribute('aria-selected', 'false'));
    assert.equal((await sample()).panel_visible, false);
    await page.locator('#wallDiscussionTab').evaluate(node => node.setAttribute('aria-selected', 'true'));
    await root.evaluate(node => { node.hidden = true; });
    assert.equal((await sample()).panel_visible, false);
    await root.evaluate(node => { node.hidden = false; });
    await page.locator('#wallChatTabs').evaluate(node => { node.hidden = false; });
    const withTabs = await sample();
    assert.equal(withTabs.tab_visible, true);
    assert.equal(withTabs.tablist_visible, true);
    assert.equal(withTabs.panel_visible, true);
    assert.equal(requests, 0);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
