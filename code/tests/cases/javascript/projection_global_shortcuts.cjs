'use strict';
// Exercise the production registry/host/SDK across opaque iframe boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const modules = ['portal_text.js', 'portal_state.js', 'portal_i18n.js', 'portal_ui.js', 'portal_ui_primitives.js',
  'portal_command_registry.js', 'portal_editor_open_service.js', 'portal_workbench_context.js', 'portal_workbench.js',
  'portal_projections.js', 'projection_host.js'];
const html = '<input id="draft" value="preserved"><button id="local">Local</button>' +
  '<script>window.localKeys=[];addEventListener("keydown",e=>localKeys.push(e.key));' +
  'addEventListener("message",e=>{if(e.data?.type==="portal-projection-keybindings")window.lastBindings=e.data.bindings});</script>';
let packaged = false;
const app = () => ({ workbench_id: 'workbench_111111111111', revision: 1, published_revision: 1,
  title: 'Shortcut fixture', bindings: {}, ...(packaged ? {
    application_build: { status: 'ready', artifact_hash: 'a'.repeat(64), entries: { main: 'main.html' },
      files: { 'main.html': { content_type: 'text/html' } } },
    manifest: { schema: 'tfi.projection.v2', sidebar: [{ id: 'main', title: 'Main', source: 'package:main' }], context: [] },
  } : { html }) });
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  res.setHeader('Cache-Control', 'no-store');
  if (url.pathname === '/') {
    res.setHeader('Content-Type', 'text/html');
    res.end('<main id="root"></main>' + modules.map(name => `<script src="/static/${name}"></script>`).join(''));
  } else if (url.pathname.startsWith('/static/')) {
    const name = path.basename(url.pathname);
    res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
    res.end(fs.readFileSync(path.join(root, name)));
  } else if (url.pathname === '/api/workbenches/detail') {
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(app()));
  } else if (url.pathname.startsWith('/api/workbenches/assets/')) {
    res.setHeader('Content-Type', 'text/html'); res.end(html);
  } else { res.writeHead(404); res.end('{}'); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    for (packaged of [false, true]) {
      const page = await browser.newPage();
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.evaluate(async () => {
        window.PortalIdentity = { username: () => 'shortcut-fixture' };
        window.hits = []; window.enabled = true;
        PortalCommandRegistry.root.register({ id: 'search', keybindings: ['Ctrl+K', 'Meta+K'],
          allowInInput: true, enabled: () => enabled, execute: () => hits.push('search') });
        PortalCommandRegistry.root.register({ id: 'non-input', keybindings: ['Ctrl+J'], execute: () => hits.push('non-input') });
        const local = PortalCommandRegistry.createScope({ id: 'page-only' });
        local.register({ id: 'local', keybindings: ['Ctrl+L'], execute: () => hits.push('wrong-scope') });
        window.controller = await PortalProjectionHost.open(document.getElementById('root'), 'workbench_111111111111');
      });
      await page.waitForFunction(() => document.querySelector('#root iframe'));
      let frame;
      for (let i = 0; i < 100; i++) {
        for (const candidate of page.frames()) if (await candidate.locator('#draft').count()) frame = candidate;
        if (frame) break;
        await page.waitForTimeout(20);
      }
      assert(frame, 'application iframe loaded');
      const press = async (selector, key, count) => {
        await frame.locator(selector).focus(); await page.keyboard.press(key);
        await page.waitForFunction(n => hits.length === n, count);
      };
      await press('#draft', 'Control+k', 1);
      await press('#draft', 'Meta+k', 2);
      assert.equal(await frame.locator('#draft').inputValue(), 'preserved');
      assert.deepEqual(await frame.evaluate(() => localKeys.filter(k => k.toLowerCase() === 'k')), [], 'global key owns precedence');
      await press('#local', 'Control+j', 3);
      await frame.locator('#draft').focus();
      await page.keyboard.press('Control+j'); await page.keyboard.press('Control+l');
      await page.keyboard.press('End'); await page.keyboard.type('x');
      assert.equal(await frame.locator('#draft').inputValue(), 'preservedx');
      // Composition/repeats and unregistered combinations must not be consumed.
      const unconsumed = await frame.evaluate(() => [
        { key: 'k', ctrlKey: true, isComposing: true }, { key: 'k', ctrlKey: true, repeat: true },
        { key: 'k', ctrlKey: true, shiftKey: true }, { key: 'k', ctrlKey: true, altKey: true },
      ].map(init => { const e = new KeyboardEvent('keydown', { ...init, bubbles: true, cancelable: true });
        document.querySelector('#draft').dispatchEvent(e); return !e.defaultPrevented; }));
      assert(unconsumed.every(Boolean));
      await page.evaluate(() => {
        enabled = false; PortalCommandRegistry.root.setContext({});
        PortalCommandRegistry.root.register({ id: 'dynamic', keybindings: ['Ctrl+Y'], allowInInput: true, execute: () => hits.push('dynamic') });
      });
      await frame.waitForFunction(() => window.lastBindings?.some(command => command.keybindings.includes('Ctrl+Y')));
      await press('#draft', 'Control+y', 4); // Confirms updated bindings reached the frame.
      await page.keyboard.press('Control+k');
      // Host revalidates against live registry, not the frame's copied declarations.
      const shortcut = await frame.evaluate(() => {
        const script = document.querySelector('script[data-channel]');
        window.savedShortcut = { type: 'portal-projection-keybinding', channel: script.dataset.channel,
          editable: true, key: { key: 'k', code: 'KeyK', ctrlKey: true } };
        const host = script.dataset.hostDepth === '2' ? parent.parent : parent;
        host.postMessage({ ...savedShortcut, channel: 'wrong-channel' }, '*');
        host.postMessage(savedShortcut, '*'); // Disabled in the authoritative host.
        return savedShortcut;
      });
      await page.waitForTimeout(50);
      assert.equal(await page.evaluate(() => hits.length), 4);
      await page.evaluate(data => {
        enabled = true;
        window.dispatchEvent(new MessageEvent('message', { source: window,
          data }));
      }, shortcut);
      await page.waitForTimeout(80);
      assert.deepEqual(await page.evaluate(() => hits), ['search', 'search', 'non-input', 'dynamic']);
      await page.evaluate(() => controller.destroy());
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ packaged, passed: true, checks: 'Ctrl/Meta, input/local precedence, dynamic registry, IME/repeat, channel/source isolation' }));
      await page.close();
    }
  } finally { if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
