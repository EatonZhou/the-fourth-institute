'use strict';
// Chromium's native composition through CDP, not synthetic CompositionEvents.
// Real workspace/upload lifecycle; only the upload transport is held locally.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const routes = execFileSync('python3', ['-c',
  'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
{ cwd: repo, encoding: 'utf8' });

(async () => {
  const server = http.createServer((request, response) => {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><main id="chat"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routes;
    else if (modules.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    response.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type });
    response.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    const cdp = await page.context().newCDPSession(page);
    for (const scenario of ['progress', 'completion', 'failure', 'refresh', 'refresh-without-attachment']) {
      await page.evaluate(async scenario => {
        window.workspace?.destroy();
        document.getElementById('chat').replaceChildren();
        window.sent = [];
        window.workspace = PortalChatWorkspace.createWorkspace({
          root: document.getElementById('chat'), id: 'ime-' + scenario, refs: false,
          historyScope: () => 'ime-' + scenario,
          onSend: payload => sent.push(payload.text),
          uploadAttachment: (file, context) => new Promise((resolve, reject) => {
            window.upload = { resolve, reject, id: context.item.id };
          }),
          contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
        });
        if (scenario !== 'refresh-without-attachment') {
          window.adding = workspace.addFiles([new File(['fixture'], 'test.txt', { type: 'text/plain' })]).catch(() => {});
        }
        workspace.els.input.focus();
        const range = document.createRange();
        range.selectNodeContents(workspace.els.input);
        range.collapse(false);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
      }, scenario);
      const compose = text => cdp.send('Input.imeSetComposition', { text, selectionStart: text.length, selectionEnd: text.length });
      await compose('k');
      await page.evaluate(() => {
        window.activeRange = getSelection().getRangeAt(0);
        window.inputMutations = [];
        window.inputObserver = new MutationObserver(records => inputMutations.push(...records));
        inputObserver.observe(workspace.els.input, { subtree: true, childList: true, characterData: true,
          attributes: true, attributeFilter: ['contenteditable'] });
      });
      await page.evaluate(async scenario => {
        if (scenario === 'progress') workspace.updateAttachment(upload.id, { progress: 30 });
        else if (scenario === 'completion') { upload.resolve({ upload_id: 'upload_fixture', filename: 'uploaded.txt' }); await adding; }
        else if (scenario === 'failure') { upload.reject(new Error('fixture upload failed')); await adding; }
        else workspace.setComposer({ ...workspace.state.composer, can_stop: true, meta: 'Working' });
      }, scenario);
      const untouched = await page.evaluate(() => {
        inputObserver.disconnect();
        return { mutations: inputMutations.length, sameRange: getSelection().getRangeAt(0) === activeRange };
      });
      assert.deepEqual(untouched, { mutations: 0, sameRange: true },
        scenario + ': background work must leave the native composition DOM and selection untouched');
      await compose('kon');
      await page.evaluate(scenario => {
        if (scenario === 'progress') workspace.updateAttachment(upload.id, { progress: 60 });
        else workspace.setComposer({ ...workspace.state.composer, meta: 'Still working' });
      }, scenario);
      await compose('kong');
      await cdp.send('Input.insertText', { text: '空格' });
      const result = await page.evaluate(() => ({ text: workspace.payload().text.trim(), sent }));
      assert.deepEqual(result, { text: '空格', sent: [] }, scenario + ': updates must not commit an unfinished syllable or send it');
      if (scenario === 'completion') {
        await page.waitForFunction(() => workspace.els.input.textContent.includes('uploaded.txt'));
      }
      console.log('PASS native composition during ' + scenario);
    }
    assert.deepEqual(errors, []);
    await page.evaluate(() => workspace.destroy());
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
