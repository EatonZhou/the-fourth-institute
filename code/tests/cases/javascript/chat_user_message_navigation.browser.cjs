'use strict';
// Real layout and keyboard events: navigating the conversation must not recall
// input, move its caret, send anything, or be undone by streaming/layout updates.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
(async () => {
  const server = http.createServer((request, response) => {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8">' + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('') +
        '<style>html,body{height:100%;margin:0}#chat{height:100%;min-height:0}</style><main id="chat"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routeScript;
    else if (modules.includes(name) || (/^[\w-]+\.css$/.test(name))) {
      const file = path.join(repo, 'management_portal/static', name);
      if (fs.existsSync(file)) body = fs.readFileSync(file);
      if (name.endsWith('.css')) type = 'text/css';
    }
    response.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type });
    response.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 945, height: 700 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = 'http://127.0.0.1:' + server.address().port;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      PortalIdentity = { username: () => 'viewer' };
      window.scope = 'navigation'; window.loads = 0; window.sent = 0;
      window.human = (id, content = id, sender = 'viewer') => ({ id, role: 'user', content,
        origin: { sender: { kind: 'user', id: sender } }, created_at: '2026-11-04T00:00:00Z' });
      window.agent = id => ({ id, role: 'assistant', content: ('Response ' + id + '\n\n').repeat(18) });
      window.items = [human('h1'), agent('a1'), human('h2', 'Another user', 'someone'), agent('a2'),
        { id: 'resident-input', role: 'user', content: 'Resident delivery', origin: { sender: { kind: 'resident', id: 'resident_aaaaaaaaaaaa' } } },
        { id: 'notice', role: 'system', content: 'System notice' }, human('h3'), agent('a3')];
      window.workspace = PortalChatWorkspace.createWorkspace({
        root: document.getElementById('chat'), id: 'navigation', historyScope: () => scope, refs: false,
        onSend: () => { sent++; }, onLoadOlder: () => { loads++; },
        onLoadInputHistory: async () => ({ messages: [human('recalled', 'Earlier input')], next_page_token: '' }),
        contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
      });
      workspace.setMessages(items, { history: { can_load_older: true } });
      workspace.setInputText('Unsent draft');
      workspace.els.input.focus();
      window.caret = getSelection().anchorOffset;
      window.draftHtml = workspace.els.input.innerHTML;
    });
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await settle();
    const press = async (key, expected) => {
      await page.evaluate(() => document.querySelectorAll('.messageQuoteTarget').forEach(row => row.classList.remove('messageQuoteTarget')));
      await page.keyboard.press(key);
      await settle();
      assert.equal(await page.locator('.messageQuoteTarget').getAttribute('data-message-key'), expected, key);
      assert.equal(await page.evaluate(() => document.activeElement === workspace.els.input), true, 'composer keeps focus');
    };
    await press('Alt+ArrowUp', 'h3');
    await press('Alt+ArrowUp', 'h2');
    const placement = await page.evaluate(() => {
      const top = workspace.els.messages.getBoundingClientRect().top;
      const row = workspace.els.messages.querySelector('[data-message-key="h2"]').getBoundingClientRect();
      return { top, row: row.top, height: workspace.els.messages.clientHeight, scroll: workspace.els.messages.scrollTop,
        total: workspace.els.messages.scrollHeight };
    });
    assert.ok(placement.row >= placement.top && placement.row < placement.top + 30,
      'the target is visible near the top: ' + JSON.stringify(placement));
    await page.evaluate(() => document.getElementById('chat').dispatchEvent(new CustomEvent('portal:workbench-before-layout')));
    await press('Alt+ArrowDown', 'h3');
    await page.evaluate(() => document.getElementById('chat').dispatchEvent(new CustomEvent('portal:workbench-resize')));
    await settle();
    assert.ok(await page.evaluate(() => {
      const view = workspace.els.messages.getBoundingClientRect();
      const row = workspace.els.messages.querySelector('[data-message-key="h3"]').getBoundingClientRect();
      return row.top >= view.top && row.top < view.top + 30;
    }), 'a pending layout restoration cannot undo explicit navigation');
    let position = await page.evaluate(() => workspace.els.messages.scrollTop);
    await page.keyboard.press('Alt+ArrowDown');
    assert.equal(await page.evaluate(() => workspace.els.messages.scrollTop), position, 'last message is a boundary');
    await press('Alt+ArrowUp', 'h2');
    await press('Alt+ArrowUp', 'h1');
    position = await page.evaluate(() => workspace.els.messages.scrollTop);
    await page.keyboard.press('Alt+ArrowUp');
    assert.equal(await page.evaluate(() => workspace.els.messages.scrollTop), position, 'first loaded message is a boundary');
    assert.deepEqual(await page.evaluate(() => ({ html: workspace.els.input.innerHTML, caret: getSelection().anchorOffset,
      unchanged: workspace.els.input.innerHTML === draftHtml, sent, loads })),
    { html: await page.evaluate(() => draftHtml), caret: await page.evaluate(() => caret), unchanged: true, sent: 0, loads: 0 });
    // Scrolling manually discards the old message cursor and uses the viewport.
    await page.evaluate(() => {
      const view = workspace.els.messages;
      const row = view.querySelector('[data-message-key="h2"]');
      view.dispatchEvent(new WheelEvent('wheel', { deltaY: 120 }));
      view.scrollTop += row.getBoundingClientRect().top - view.getBoundingClientRect().top - 80;
      view.dispatchEvent(new Event('scroll'));
    });
    await press('Alt+ArrowDown', 'h2');
    position = await page.evaluate(() => workspace.els.messages.scrollTop);
    await page.evaluate(() => workspace.appendMessage(agent('streaming')));
    await settle();
    assert.equal(await page.evaluate(() => workspace.els.messages.scrollTop), position, 'new replies leave history reading in place');
    await page.setViewportSize({ width: 625, height: 700 });
    await settle();
    assert.equal(await page.evaluate(() => workspace.els.messages.classList.contains('is-reading-history')), true);
    if (process.env.SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'user-message-navigation-625.png') });
    }
    // IME and other modifier combinations keep their native editing meaning.
    position = await page.evaluate(() => workspace.els.messages.scrollTop);
    for (const key of ['Control+ArrowUp', 'Control+ArrowDown', 'Control+Alt+ArrowUp', 'Meta+Alt+ArrowDown']) {
      await page.keyboard.press(key);
      assert.equal(await page.evaluate(() => workspace.els.messages.scrollTop), position, key + ' does not navigate messages');
    }
    assert.equal(await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', { key: 'ArrowUp', ctrlKey: true, bubbles: true, cancelable: true });
      workspace.els.input.dispatchEvent(event); return event.defaultPrevented;
    }), false, 'Ctrl+Up is not consumed by chat navigation');
    assert.equal(await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', { key: 'ArrowUp', altKey: true, isComposing: true, bubbles: true, cancelable: true });
      workspace.els.input.dispatchEvent(event); return event.defaultPrevented;
    }), false);
    await page.keyboard.press('Alt+Shift+ArrowUp');
    assert.equal(await page.evaluate(() => workspace.els.messages.scrollTop), position);
    await page.evaluate(() => workspace.setInputText('Unsent draft'));
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.evaluate(() => workspace.inputText()), 'Earlier input', 'plain Up still recalls input');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => workspace.inputText()), 'Unsent draft', 'plain Down restores the draft');
    await page.evaluate(() => { scope = 'other'; workspace.setMessages([human('other1'), agent('other-reply'), human('other2'), agent('end')]); });
    await settle();
    await press('Alt+ArrowUp', 'other2');
    await press('Alt+ArrowUp', 'other1');
    await page.setViewportSize({ width: 945, height: 700 });
    await settle();
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'user-message-navigation-945.png') });
    await page.evaluate(() => { scope = 'empty'; workspace.setMessages([agent('only-reply')]); });
    await settle();
    await page.keyboard.press('Alt+ArrowUp');
    await page.keyboard.press('Alt+ArrowDown');
    assert.equal(await page.locator('.messageQuoteTarget').count(), 0, 'no user messages is a no-op');
    await page.evaluate(() => { scope = 'short'; workspace.setMessages([human('short1'), human('short2')]); });
    await settle();
    await press('Alt+ArrowUp', 'short2');
    await press('Alt+ArrowUp', 'short1');
    await press('Alt+ArrowDown', 'short2');
    assert.deepEqual(errors, []);
    console.log('User message navigation: keyboard, layout, streaming, scope, boundaries, IME and input history passed');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
