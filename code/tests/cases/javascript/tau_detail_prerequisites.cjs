'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const codeRoot = process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(codeRoot, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readWallChatPanel, probeWatchboardStructure} = require(process.env.TFI_DETAIL_PREREQUISITES_MODULE
  || path.join(codeRoot, 'management_portal/test_framework/tau_detail_prerequisites'));

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage(); let requests = 0;
    page.on('request', () => { requests++; });
    // Use the actual detail template. No Portal code, network, actor or mutation.
    await page.evaluate(html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      document.body.append(doc.getElementById('wallNativeView').content.querySelector('#wallDetail'));
      const root = document.getElementById('wallDetail'); root.hidden = false; root.setAttribute('aria-hidden', 'false');
      const style = document.createElement('style');
      style.textContent = '[role=textbox]{min-height:30px;width:300px} article{min-height:24px} aside{width:320px}';
      document.head.append(style);
      document.getElementById('wallChatTabs').hidden = false;
      document.getElementById('wallWorkTab').setAttribute('aria-selected','true');
      document.getElementById('wallDiscussionTab').setAttribute('aria-selected','false');
      document.getElementById('wallChat').hidden = true;
      const work = document.getElementById('wallWorkChat'); work.hidden = false;
      work.innerHTML = '<span data-workspace-resident-id="resident_worker">Worker</span><div class="messageList">'
        + '<article class="messageItem" data-message-key="msg_original" data-message-instance="msg_original">Original input</article>'
        + '<article class="messageItem" data-message-key="__runtime__">Runtime</article>'
        + '<div data-message-key="__scroll_anchor__"></div><span data-portal-message-key="msg_actions">Action</span></div>'
        + '<div role="textbox" contenteditable="true"></div>';
      document.getElementById('wallChat').innerHTML = '<span data-workspace-resident-id="resident_discussion">Discussion</span>'
        + '<article class="messageItem" data-message-key="msg_other">Other input</article><div role="textbox" contenteditable="true"></div>';
    }, fs.readFileSync(path.join(codeRoot, 'management_portal/wall.html'), 'utf8'));
    const root = page.locator('#wallDetail');
    const original = await readWallChatPanel(root, {kind:'work',messageId:'msg_original'});
    assert.equal(original.resident_id, 'resident_worker'); assert.equal(original.message_id, 'msg_original');
    assert.deepEqual(original.message_keys, ['msg_original']); assert.equal(original.selected_tab, 'work');
    assert.equal(original.panel_visible, true); assert.equal(original.composer_editable, true);
    assert.equal((await readWallChatPanel(root, {kind:'work',messageId:'msg_other'})).message_id, '');
    await assert.rejects(readWallChatPanel(root, {kind:'work'}), /Original job/);
    await assert.rejects(readWallChatPanel(page.locator('#wallWorkChat'), {kind:'work',messageId:'msg_original'}), /full WALL/);
    await page.locator('#wallWorkChat').evaluate(el => { el.hidden = true; });
    const hidden = await readWallChatPanel(root, {kind:'work',messageId:'msg_original'});
    assert.equal(hidden.panel_visible, false); assert.equal(hidden.message_id, ''); assert.equal(original.message_id, 'msg_original');
    await page.locator('#wallWorkChat').evaluate(el => { el.hidden = false; el.querySelector('[role=textbox]').setAttribute('contenteditable','false'); });
    assert.equal((await readWallChatPanel(root, {kind:'work',messageId:'msg_original'})).composer_editable, false);
    await page.locator('#wallWorkChat').evaluate(el => el.insertAdjacentHTML('beforeend','<span data-workspace-resident-id="resident_wrong">Wrong</span>'));
    await assert.rejects(readWallChatPanel(root, {kind:'work',messageId:'msg_original'}), /Ambiguous/);
    await page.evaluate(() => {
      document.getElementById('wallWorkChat').hidden = true; document.getElementById('wallWorkTab').setAttribute('aria-selected','false');
      document.getElementById('wallDiscussionTab').setAttribute('aria-selected','true');
      const chat = document.getElementById('wallChat'); chat.hidden = false;
      chat.innerHTML = '<div class="portalWorkspaceEmpty">No conversation</div><div role="textbox" contenteditable="true"></div>';
    });
    const empty = await readWallChatPanel(root, {kind:'discussion'});
    assert.equal(empty.resident_id, ''); assert.equal(empty.explicit_empty, true); assert.equal(empty.composer_editable, true);
    await root.evaluate(el => el.setAttribute('aria-hidden','true'));
    assert.equal((await readWallChatPanel(root, {kind:'discussion'})).panel_visible, false);

    const board = page.locator('#fixtureWatchboard');
    assert.equal((await probeWatchboardStructure(board)).ready, false);
    await page.evaluate(() => document.body.insertAdjacentHTML('beforeend','<aside id="fixtureWatchboard" class="watchboard" aria-label="Resident Watchboard"></aside>'));
    const shell = await probeWatchboardStructure(board); assert.equal(shell.ready, false);
    await board.evaluate(el => el.innerHTML = '<div class="watchboardHead">Board</div>');
    assert.equal((await probeWatchboardStructure(board)).ready, false);
    await board.evaluate(el => el.insertAdjacentHTML('beforeend','<div class="watchboardBody"><button data-watchboard-status-open="">Status</button></div>'));
    assert.equal((await probeWatchboardStructure(board)).ready, true); assert.equal(shell.ready, false);
    assert.equal(await board.locator('[data-watchboard-status-open]').getAttribute('data-watchboard-status-open'), '');
    await board.locator('.watchboardBody').evaluate(el => { el.hidden = true; });
    assert.equal((await probeWatchboardStructure(board)).ready, false);
    await assert.rejects(probeWatchboardStructure(board.locator('.watchboardBody')), /full Watchboard/);
    assert.equal(requests, 0);
    console.log(JSON.stringify({status:'passed',browser:'Chromium',scopes:['native WALL template','exact Work message key and instance','selected pane isolation','hidden/read-only/ambiguous rejection','explicit empty discussion','Watchboard absent/partial/full/collapsed structure','immutable samples','passive zero requests'],production_calls:0,business_acceptance:false}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
