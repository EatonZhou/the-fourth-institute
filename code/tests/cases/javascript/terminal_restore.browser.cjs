'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 620 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.setContent('<section id="panel"></section>');
    await page.addStyleTag({ path: path.join(root, 'vendor/xterm/xterm.css') });
    await page.addStyleTag({ content: '.terminalXterm {height:420px} body {font:16px sans-serif} .paneHead {padding:16px}' });
    await page.evaluate(() => {
      const tag = (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '');
      window.PortalI18n = { t: s => s, html: tag, text: tag };
      window.PortalUIPrimitives = { escapeHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;') };
    });
    for (const file of ['vendor/xterm/xterm.js', 'vendor/xterm/addon-fit.js', 'portal_terminal.js', 'portal_workspace_surface.js']) {
      await page.addScriptTag({ path: path.join(root, file) });
    }
    await page.evaluate(() => {
      window.requests = []; window.snapshot = null;
      const NativeTerminal = window.Terminal;
      window.Terminal = class extends NativeTerminal {
        constructor(...args) { super(...args); window.nativeTerminal = this; }
      };
      window.screenText = () => Array.from({ length: nativeTerminal.buffer.active.length },
        (_, i) => nativeTerminal.buffer.active.getLine(i)?.translateToString() || '').join('\n');
      window.panel = PortalWorkspaceSurface.panelPackages.terminal.create({ root: document.querySelector('#panel'),
        apiPost: async (url, payload) => {
          requests.push({ url, ...payload });
          const chunk = 'Connected to a new shell\r\n$ ';
          return { session_id: 'replacement', alive: true, chunk, cursor: Array.from(chunk).length };
        }, apiGet: async () => ({ session_id: 'replacement', alive: true, cursor: Array.from('Connected to a new shell\r\n$ ').length, chunk: '' }),
        emit: () => { snapshot = panel.snapshot(); },
      });
      panel.restore({ target: { key: 'host', cwd: '/tmp' }, sessionId: 'expired-before-opening' });
      panel.show();
    });
    await page.waitForFunction(() => window.snapshot?.sessionId === 'replacement');
    await page.waitForFunction(() => screenText().includes('Connected to a new shell'), null, { timeout: 3000 }).catch(async error => {
      throw new Error(JSON.stringify({ error: error.message, screen: await page.evaluate(() => screenText()),
        size: await page.evaluate(() => [nativeTerminal.cols, nativeTerminal.rows]), errors }));
    });
    await page.evaluate(() => { panel.hide(); panel.show(); });
    await page.waitForTimeout(150);
    const requests = await page.evaluate(() => requests);
    assert.equal(requests.filter(r => r.url.endsWith('/start')).length, 1);
    assert.equal(requests[0].session_id, 'expired-before-opening');
    assert(!await page.evaluate(() => screenText().includes('会话已结束')));
    if (process.env.SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'restored-terminal.png') });
    }
    assert.deepEqual(errors, []);
    await page.evaluate(() => panel.destroy());
    console.log('Restored Workbench panel negotiates once, displays xterm prompt, and survives hide/show.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
