'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    await page.route('**/*', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><main id="chat"></main>' });
      if (/^\/static\/[\w.-]+\.js$/.test(pathname)) {
        const file = path.join(root, path.basename(pathname));
        if (fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      }
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not in fixture"}' });
    });
    await page.goto('http://attachments.test/');
    await page.addScriptTag({ content: routeScript });
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
      'portal_ui_primitives.js', 'portal_ui.js', 'markdown.js', 'portal_overlay.js', 'portal_preview_contract.js',
      'portal_file_links.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
      'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({ path: path.join(root, name) });
    }
    for (const name of ['portal_design_system', 'portal_components', 'portal_components_markdown',
      'portal_components_chat_frame', 'portal_components_messages', 'portal_components_composer']) {
      await page.addStyleTag({ path: path.join(root, name + '.css') });
    }
    await page.addStyleTag({ content: '#chat {height:800px} body {margin:0}' });
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'viewer' };
      window.previews = [];
      window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'files',
        refs: false, foldLongMessages: false, historyScope: () => 'attachment-test',
        onLinkPreview: request => previews.push(request),
      });
      window.media = ['paper.tex', 'figures.csv', 'notes.md'].map((name, i) => ({ type: 'file', kind: 'file', name,
        upload_id: 'upload_' + String(i + 1).repeat(24), source: '/uploads/upload_' + String(i + 1).repeat(24) + '/' + name,
        url: '/api/uploads/upload_' + String(i + 1).repeat(24),
      }));
      window.message = { id: 'uploaded', role: 'user', author_id: 'viewer',
        content: 'Here are the updated files.\n\n' + media.map(f => `[file: ${f.source}]`).join('\n\n') +
          '\n<attached_files>\n' + media.map(f => 'file: ' + f.url).join('\n') + '\n</attached_files>',
        content_parts: media, input_media: media, context_refs: { attachments: media }, attachments: media,
      };
      workspace.setMessages([message]);
    });
    const chips = page.locator('.messageContent [data-message-attachment-path]');
    assert.equal(await chips.count(), 3, 'all projections describe just three attachments');
    assert.deepEqual(await chips.locator('.composerImageRefLabel').allTextContents(), ['paper.tex', 'figures.csv', 'notes.md']);
    const bounds = await chips.evaluateAll(nodes => nodes.map(n => { const r = n.getBoundingClientRect(); return { x: r.x, y: r.y }; }));
    assert.ok(bounds.every(b => Math.abs(b.y - bounds[0].y) < 2), 'adjacent file cards share a row instead of an L');
    assert.ok(bounds[0].x < bounds[1].x && bounds[1].x < bounds[2].x);
    await chips.first().click();
    assert.equal(await page.evaluate(() => previews.length), 1, 'file remains clickable');
    // Simulate a snapshot reload of exactly the same message.
    await page.evaluate(() => workspace.setMessages([JSON.parse(JSON.stringify(message))]));
    assert.equal(await chips.count(), 3);
    await page.setViewportSize({ width: 330, height: 850 });
    assert.equal(await chips.count(), 3);
    assert.ok(await page.locator('.messageBubble').evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'narrow chat wraps without overflow');
    const narrow = await chips.evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().y));
    assert.ok(narrow[2] > narrow[0], 'cards wrap in a narrow column');
    await page.evaluate(() => {
      const extra = { ...media[0], upload_id: 'upload_' + '4'.repeat(24), url: '/api/uploads/upload_' + '4'.repeat(24), source: '/uploads/other/paper.tex' };
      workspace.setMessages([{ id: 'ranges', role: 'user', content: 'Before [file: ' + media[0].url + '] After\n\n' +
        '[引用: /repo/main.tex#L1-L2] [引用: /repo/main.tex#L4-L6]', content_parts: [media[0], extra], input_media: [extra] }]);
    });
    assert.equal(await page.locator('[data-message-key="ranges"] [data-message-attachment-path]').count(), 4,
      'two same-named uploads and two distinct selections are not merged');
    assert.match(await page.locator('.messageContent').textContent(), /Before.*paper.tex.*After/s);
    // An attachment supplied only as metadata still gets one named card.
    await page.evaluate(() => workspace.setMessages([{ id: 'only', role: 'user', content: 'Metadata only', input_media: media }]));
    assert.equal(await page.locator('.messageAttachmentTray [data-message-attachment-path]').count(), 3);
    assert.deepEqual(await page.locator('.messageAttachmentTray .composerImageRefLabel').allTextContents(), ['paper.tex', 'figures.csv', 'notes.md']);
    await page.evaluate(() => workspace.destroy());
    assert.deepEqual(errors, []);
    console.log('PASS attachment identity, names, inline order, reload and responsive layout');
  } finally { await browser.close(); }
})();
