'use strict';
// Real shared Workspace/Markdown/CSS and clipboard, with synthetic messages only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
const code = '  echo "<tag> & 中文"\n\tprintf "%s\\n" "value"\n' + 'x'.repeat(220);
const fence = '```';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://copy.fixture/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><body><main id="chat"></main></body></html>' }));
    await page.goto('https://copy.fixture/');
    for (const style of ['portal_design_system', ...PORTAL_COMPONENTS_STYLES, 'portal_themes']) {
      await page.addStyleTag({ path: path.join(root, style + '.css') });
    }
    await page.addStyleTag({ content: 'body{margin:0;padding:16px;background:var(--panel);color:var(--text)}#chat{height:650px;max-width:1000px;margin:auto}' });
    await page.addScriptTag({ content: routeScript });
    for (const script of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_translations.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js', 'markdown.js',
      'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({ path: path.join(root, script) });
    }
    await page.evaluate(({ code, fence }) => {
      window.PortalIdentity = { username: () => 'fixture' };
      window.workspace = window.PortalChatWorkspace.createWorkspace({ root: document.querySelector('#chat'), id: 'copy-test', refs: false, can_attach: false, foldLongMessages: false });
      window.messages = [{ id: 'code', role: 'assistant', content: '运行下面的命令：\n\n' + fence + 'sh\n' + code + '\n' + fence + '\n\n' + fence + '\nsecond block\n' + fence }];
      workspace.setMessages(messages);
    }, { code, fence });
    const buttons = page.locator('[data-portal-code-copy]');
    assert.equal(await buttons.count(), 2);
    await buttons.first().click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), code);
    assert.match(await buttons.first().innerText(), /已复制|Copied/);
    await buttons.nth(1).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'second block');
    assert.equal(await page.evaluate(() => window.PortalChatWorkspaceMessages.messageCopyText(document.querySelector('.messageItem'), '').includes('复制')), false);
    assert.equal(await page.evaluate(() => window.PortalChatWorkspaceMessages.messageCopyText(document.querySelector('.messageItem'), '').includes('Copied')), false);
    // Horizontal scrolling must not hide the copy control, including narrow/dark layouts.
    for (const width of [1000, 390]) {
      await page.setViewportSize({ width, height: 760 });
      await page.evaluate(width => {
        document.documentElement.dataset.portalTheme = width === 390 ? 'dark' : 'light';
        document.querySelector('.portalCodeBlock pre').scrollLeft = 400;
      }, width);
      const block = await page.locator('.portalCodeBlock').first().boundingBox();
      const button = await buttons.first().boundingBox();
      assert.ok(button.x >= block.x && button.x + button.width <= block.x + block.width + 1);
      assert.ok(button.x + button.width <= width);
      if (process.env.CODE_COPY_EVIDENCE) await page.screenshot({ path: path.join(process.env.CODE_COPY_EVIDENCE, `code-copy-${width}.png`) });
    }
    // Existing helper's fallback and explicit failure must not report false success.
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied'); } } });
      document.execCommand = () => { window.fallbackText = document.activeElement.value; return true; };
    });
    await buttons.first().click();
    assert.equal(await page.evaluate(() => window.fallbackText), code);
    await page.evaluate(() => { document.execCommand = () => false; });
    await buttons.first().click();
    await page.waitForFunction(() => /^(复制|Copy)$/.test(document.querySelector('[data-portal-code-copy]').textContent));
    // New streaming/replaced bodies and historical prepend use the same delegated handler.
    await page.evaluate(({ fence }) => {
      document.execCommand = () => { window.fallbackText = document.activeElement.value; return true; };
      workspace.setMessages([{ id: 'older', role: 'user', content: fence + '\nolder\n' + fence }, { ...messages[0], content: fence + '\nstreaming' }]);
    }, { fence });
    await page.locator('[data-portal-code-copy]').last().click();
    assert.equal(await page.evaluate(() => window.fallbackText), 'streaming');
    await page.evaluate(() => workspace.destroy());
    assert.deepEqual(errors, []);
    console.log('PASS: exact code, keyboard, plaintext exclusion, wide/narrow layout, clipboard fallback/failure, streaming/history, cleanup');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
