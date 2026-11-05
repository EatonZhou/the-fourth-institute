'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', ...(process.env.RAIL_PREVIEW_ONLY ? ['portal_translations.js'] : []), 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_button_tooltips.js', 'portal_preview_contract.js', 'portal_resource_chips.js',
  ...(process.env.RAIL_REFERENCES_ONLY ? ['portal_file_links.js'] : []),
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const output = process.env.SCREENSHOT_DIR;
if (output) fs.mkdirSync(output, { recursive: true });
(async () => {
  const server = http.createServer((request, response) => {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('')
        + '<style>html,body{height:100%;margin:0;background:var(--panel);color:var(--text)}#chat{height:100%;min-height:0}</style><main id="chat"></main>'
        + '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routeScript;
    else if (modules.includes(name) || styles.includes(name)) {
      body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
      if (name.endsWith('.css')) type = 'text/css';
    }
    response.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); response.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser; const checks = [], errors = [], geometry = [];
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1000, height: 850 }, ...(output ? { recordVideo: { dir: output, size: { width: 1000, height: 850 } } } : {}) });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const origin = 'http://127.0.0.1:' + server.address().port;
    const setup = async (page, { baseline = false } = {}) => {
      await page.goto(origin);
      if (baseline) await page.evaluate(() => {
        // Same Chat renderer/viewport width; omit only this feature for a
        // before/after measurement, never a production runtime switch.
        PortalChatWorkspaceRoundRail = { create: () => ({ requestFor: () => '', toggle: () => false, isFollowed: () => false,
          updateProgress() {}, connectionLost() {}, setSearch() {}, refresh() {}, setPresented() {}, destroy() {} }) };
        document.getElementById('chat').style.width = 'calc(100% - 46px)';
      });
      await page.evaluate(() => {
        window.__PORTAL_SITE__ = { storage_namespace: 'rail-test' };
        window.viewer = 'reader'; PortalIdentity = { username: () => viewer };
        window.scope = 'resident:rounds'; window.loads = 0; window.toasts = [];
        PortalUI.toast = (text, kind) => toasts.push({ text, kind });
        window.human = (id, text = id) => ({ id, message_id: id, role: 'user', content: text,
          created_at: '2026-11-04T12:00:00Z', origin: { sender: { kind: 'user', id: 'reader' } } });
        window.agent = (id, request) => ({ id, message_id: id, role: 'assistant', reply_to_message_id: request,
          content: ('这是有来源的解释与结果。\n\n').repeat(16) });
        window.workInput = (id, state, at = '2026-11-04T12:00:00Z', execution = 'execution-' + id) => ({ message_id: id,
          state: 'completed', work: { state, kind: 'primary', delivery: 'delivered', execution_id: execution, updated_at: at } });
        window.items = [human('h1', '比较两个方案，并解释各自限制。'), agent('a1', 'h1'),
          human('h2', '给出可以复现的验证步骤。'), agent('a2', 'h2'),
          human('h3', '检查大字体下的交互与状态。'), agent('a3', 'h3'),
          { ...human('h4', '并行处理的 Family 请求。'), relay: workInput('h4', 'running') }, agent('a4', 'h4'),
          { ...human('local', '未接收'), local_delivery: { status: 'sending' } },
          { ...human('from-agent'), origin: { sender: { kind: 'resident', id: 'resident_aaaaaaaaaaaa' } } }];
        window.contract = { messages: items, composer: { can_send: true, history_scope: scope, queue: { items: [] } },
          request_progress: { cursor: 10, relays: [workInput('h4', 'running')], input_states: [workInput('h1', 'completed'), workInput('h2', 'running'), workInput('h3', 'queued')] } };
        window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'rail',
          title: 'Chat · 轮次阅读', historyScope: () => scope, refs: false, pinMessages: true, foldLongMessages: false,
          onLoadOlder: async () => { loads++; if (window.loadOlderAction) return window.loadOlderAction(); if (window.deferOlder) await new Promise(resolve => window.finishOlder = resolve); },
          onRoundStatusRefresh: () => new Promise(resolve => { window.refreshCalls = (window.refreshCalls || 0) + 1; window.finishRefresh = resolve; }),
          onSearchMessages: async query => query === 'slow' ? await new Promise(resolve => window.slowSearch = resolve)
            : { messages: query === 'none' ? [] : [items[0], items[1], { ...human('not-loaded'), content: 'unloaded match' }] },
          contract });
        workspace.setInputText('未发送的草稿');
      });
      await page.waitForSelector(baseline ? '.messageList > [data-message-key]' : '.chatRoundNode');
    };
    await setup(page);
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const check = (name, value) => { assert.ok(value, name); checks.push(name); };
    const shot = async name => { if (output) await page.screenshot({ path: path.join(output, name + '.png') }); };
    if (process.env.RAIL_HIT_ONLY) {
      await require('./chat_round_hit.browser.cjs')({ page, context, browser, setup, output, errors });
      await context.close(); return;
    }
    const togglePreviewFlag = async (target, trigger, touch = false) => {
      const followed = await trigger.getAttribute('aria-pressed') === 'true';
      if (touch) await trigger.tap(); else await trigger.click();
      const choice = target.locator(followed ? '.chatRoundMarkerRemove' : '.chatRoundMarkerChoice[data-round-marker="flag"]');
      if (touch) await choice.tap(); else await choice.click();
    };
    if (process.env.RAIL_HISTORY_ONLY) {
      const setHistory = async (target, empty = false) => {
        await target.evaluate(empty => {
          workspace.setMessages(empty ? [] : items, { history: { can_load_older: true } });
          window.loadOlderAction = () => new Promise((resolve, reject) => { window.olderResolve = resolve; window.olderReject = reject; });
        }, empty);
        await target.waitForTimeout(100);
      };
      if (process.env.RAIL_HISTORY_BASELINE) {
        await setHistory(page, true);
        await page.locator('.chatRoundDirectory').click();
        const observed = await page.evaluate(() => ({ loads, text: document.querySelector('.chatRoundPreview').textContent,
          preview: !document.querySelector('.chatRoundPreview').hidden }));
        assert.equal(observed.loads, 0); assert.equal(observed.preview, true);
        if (output) { await page.screenshot({ path: path.join(output, 'baseline-empty-arrow.png') }); fs.writeFileSync(path.join(output, 'baseline.json'), JSON.stringify(observed, null, 2)); }
        console.log('Baseline reproduced: arrow opens empty directory without loading'); return;
      }
      if (process.env.RAIL_HISTORY_EMPTY_ONLY) {
        const emptyCheck = async (target, prefix) => {
          await setHistory(target, true);
          const directory = target.locator('.chatRoundDirectory');
          if (prefix === '390') await directory.tap(); else { await directory.focus(); await target.keyboard.press('Enter'); }
          check(prefix + ' empty directory is a compact notice without popup or request', !await target.locator('.chatRoundPreview').isVisible()
            && await target.evaluate(() => loads === 0) && await target.locator('.chatRoundHistoryNotice').textContent() === '暂无已加载轮次');
          const box = await target.locator('.chatRoundHistoryNotice').boundingBox(); geometry.push({ prefix, notice: box });
          check(prefix + ' empty notice fits', box.height < 80 && box.x >= 0 && box.x + box.width <= (prefix === '390' ? 390 : 1000));
          for (const theme of ['light', 'dark']) {
            await target.evaluate(theme => document.documentElement.dataset.portalTheme = theme, theme);
            if (output) await target.screenshot({ path: path.join(output, prefix + '-empty-' + theme + '.png') });
          }
          await setHistory(target);
          await directory.click();
          check(prefix + ' independent directory opens populated rounds only', await target.locator('.chatRoundPreviewItem').count() === 4 && await target.locator('.chatRoundHistoryNotice').textContent() === '');
          await target.locator('.chatRoundClose').focus(); await target.keyboard.press('Tab'); await target.keyboard.press('Shift+Tab');
          check(prefix + ' close focus remains compact', await target.locator('.chatRoundClose').evaluate(n => getComputedStyle(n).outlineWidth === '1px' && n.getBoundingClientRect().width <= 44));
          if (output) await target.screenshot({ path: path.join(output, prefix + '-directory-dark.png') });
          await target.keyboard.press('Escape');
          check(prefix + ' Escape restores directory focus', await directory.evaluate(n => n === document.activeElement));
          await target.evaluate(() => workspace.setMessages([], { history: { can_load_older: false } })); await target.waitForTimeout(80);
          await target.locator('.chatRoundOlder').click({ force: true });
          check(prefix + ' history end remains request-free', await target.evaluate(() => loads === 0) && await target.locator('.chatRoundHistoryNotice').textContent() === '没有更早消息');
        };
        await emptyCheck(page, 'desktop');
        const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
        const small = await mobile.newPage(); small.on('pageerror', error => errors.push(error.message)); await setup(small);
        await small.evaluate(() => document.documentElement.style.fontSize = '20px');
        await emptyCheck(small, '390'); await mobile.close();
        assert.deepEqual(errors, []);
        if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, geometry, errors, boundary: 'Empty-directory increment only; other pagination checks reuse prior history result.' }, null, 2));
        console.log('Empty-directory increment passed: ' + checks.length); return;
      }
      const arrow = page.locator('.chatRoundOlder');
      await setHistory(page);
      await page.locator('.paneBody').first().hover(); await page.mouse.wheel(0, -600);
      await page.waitForTimeout(120);
      const anchor = await page.locator('[data-message-key="h2"]').evaluate(n => n.getBoundingClientRect().top);
      await arrow.click();
      await arrow.dispatchEvent('click');
      check('arrow immediately loads one page and repeat click is coalesced', await page.evaluate(() => loads === 1));
      check('loading is compact, accessible and never opens directory', await arrow.getAttribute('aria-busy') === 'true'
        && await page.locator('.chatRoundHistoryNotice').textContent() === '正在加载…' && !await page.locator('.chatRoundPreview').isVisible());
      await page.evaluate(() => {
        items = [human('older1', '更早的一轮'), agent('older2', 'older1'), ...items];
        workspace.setMessages(items, { history: { can_load_older: true } }); olderResolve();
      });
      await page.waitForTimeout(180);
      check('prepend keeps existing reading anchor', Math.abs(await page.locator('[data-message-key="h2"]').evaluate(n => n.getBoundingClientRect().top) - anchor) < 2);
      check('load success is announced without opening a popup', await page.locator('.chatRoundHistoryNotice').textContent() === '已加载更早消息' && !await page.locator('.chatRoundPreview').isVisible());
      await arrow.focus(); await page.keyboard.press('Enter');
      await page.evaluate(() => olderReject(new Error('fixture denied'))); await page.waitForTimeout(100);
      check('failure stays readable and retryable', (await page.locator('.chatRoundHistoryNotice').textContent()).includes('fixture denied') && await arrow.getAttribute('aria-disabled') === 'false');
      if (output) await page.screenshot({ path: path.join(output, 'desktop-failure.png') });
      await arrow.click();
      await page.evaluate(() => { workspace.setMessages(items, { history: { can_load_older: false } }); olderResolve(); });
      await page.waitForTimeout(100);
      const calls = await page.evaluate(() => loads); await arrow.click({ force: true });
      check('end of history never loads or opens empty directory', await page.evaluate(() => loads) === calls
        && await page.locator('.chatRoundHistoryNotice').textContent() === '没有更早消息' && !await page.locator('.chatRoundPreview').isVisible());
      await setHistory(page, true); await page.locator('.chatRoundDirectory').click();
      check('empty directory uses a notice instead of a zero-message popup', !await page.locator('.chatRoundPreview').isVisible()
        && await page.locator('.chatRoundHistoryNotice').textContent() === '暂无已加载轮次');
      await arrow.click();
      await page.evaluate(() => { window.oldResolve = olderResolve; scope = 'resident:next'; workspace.setMessages([human('next')], { history: { can_load_older: true } }); });
      await page.waitForTimeout(100); await arrow.click();
      await page.evaluate(() => oldResolve()); await page.waitForTimeout(80);
      check('late old success cannot clear new request loading', await arrow.getAttribute('aria-busy') === 'true' && await page.locator('.chatRoundHistoryNotice').textContent() === '正在加载…');
      await page.evaluate(() => olderReject(new Error('new failure'))); await page.waitForTimeout(80);
      await arrow.click();
      await page.evaluate(() => { window.oldReject = olderReject; scope = 'resident:third'; workspace.setMessages([], { history: { can_load_older: false } }); });
      await page.waitForTimeout(100); await page.evaluate(() => oldReject(new Error('late private error'))); await page.waitForTimeout(80);
      check('late failure after conversation switch is isolated', await page.locator('.chatRoundHistoryNotice').textContent() === '');
      await page.locator('.chatRoundDirectory').click();
      if (output) await page.screenshot({ path: path.join(output, 'desktop-empty.png') });
      await setHistory(page); await page.locator('.chatRoundDirectory').click();
      await page.locator('.chatRoundClose').focus(); await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab');
      const closeGeometry = await page.locator('.chatRoundClose').evaluate(n => ({ focus: n === document.activeElement, outline: getComputedStyle(n).outlineWidth, width: n.getBoundingClientRect().width }));
      geometry.push({ closeGeometry });
      assert.deepEqual(closeGeometry, { focus: true, outline: '1px', width: 32 }); checks.push('close has compact visible keyboard focus');
      await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
      if (output) await page.screenshot({ path: path.join(output, 'desktop-empty-dark.png') });
      const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const small = await mobile.newPage(); small.on('pageerror', error => errors.push(error.message)); await setup(small);
      await small.evaluate(() => document.documentElement.style.fontSize = '20px');
      await setHistory(small, true); await small.locator('.chatRoundOlder').tap();
      check('390 touch loads directly without popup', await small.evaluate(() => loads === 1) && !await small.locator('.chatRoundPreview').isVisible());
      const box = await small.locator('.chatRoundOlder').boundingBox();
      check('touch history control has 44px target', box.width >= 44 && box.height >= 44);
      if (output) await small.screenshot({ path: path.join(output, '390-loading-light.png') });
      await small.evaluate(() => { workspace.setMessages([], { history: { can_load_older: false } }); olderResolve(); });
      await small.waitForTimeout(80); await small.locator('.chatRoundDirectory').tap();
      const previewBox = await small.locator('.chatRoundHistoryNotice').boundingBox(); geometry.push({ previewBox, arrow: box });
      check('390 20px empty notice fits without a popup', !await small.locator('.chatRoundPreview').isVisible() && previewBox.height < 80 && previewBox.x >= 0 && previewBox.x + previewBox.width <= 390);
      for (const theme of ['light', 'dark']) {
        await small.evaluate(theme => document.documentElement.dataset.portalTheme = theme, theme);
        if (output) await small.screenshot({ path: path.join(output, '390-empty-' + theme + '.png') });
      }
      await setHistory(small); await small.locator('.chatRoundDirectory').tap();
      check('independent directory keeps every loaded round', await small.locator('.chatRoundPreviewItem').count() === 4);
      if (output) await small.screenshot({ path: path.join(output, '390-directory-dark.png') });
      await small.locator('.chatRoundPreviewJump').first().tap();
      check('directory still locates exact selected message', await small.locator('[data-message-key="h1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      await small.locator('.chatRoundOlder').tap();
      await small.evaluate(() => { workspace.destroy(); olderReject(new Error('destroyed request')); });
      await small.waitForTimeout(80);
      check('destroyed workspace ignores pending page failure', await small.locator('.chatRoundHistoryNotice').count() === 0);
      await mobile.close();
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, geometry, errors, boundary: 'Native shared Chat/styles; isolated synthetic pagination. No User session, business writes or formal Run.' }, null, 2));
      console.log('History entry checks passed: ' + checks.length); return;
    }
    if (process.env.RAIL_REFERENCES_ONLY) {
      const mediaRequests = [];
      await page.route('**/api/uploads/**', route => {
        mediaRequests.push(route.request().url());
        return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9QAAAABJRU5ErkJggg==', 'base64') });
      });
      await page.evaluate(() => {
        window.openedFiles = []; window.openedImages = []; window.refReads = [];
        window.previewObservers = [];
        const NativeObserver = IntersectionObserver;
        window.IntersectionObserver = class extends NativeObserver {
          constructor(callback, options) { super(callback, options); if (options?.root?.classList?.contains('chatRoundPreviewList')) previewObservers.push(this); }
          disconnect() { this.released = true; super.disconnect(); }
        };
        PortalImageEditor.open = (src, title) => openedImages.push({ src, title });
        PortalAPI = { get: async url => {
          refReads.push(url);
          if (url.includes('reference?')) throw Object.assign(new Error('denied'), { status: 403, data: { retryable: false } });
          throw new Error('unexpected resource request');
        } };
        const media = '/api/uploads/preview-ref.png', file = 'workspace_aaaaaaaaaaaa/file/notes.md';
        items = [
          { ...human('refs', '请看图片\n[image:' + media + ']\n[image:' + media + ']\n[file:' + file + ']\ndecision_aaaaaaaaaaaa'),
            input_media: [{ type: 'image', source: media, name: '图片说明.png' }], attachments: [{ kind: 'file', path: file, name: '阅读笔记.md' }] },
          // Protocol upload metadata carries the name; the projected text only
          // carries a path. These are isolated component values, never uploads.
          { ...human('named-upload', '[file: /api/uploads/upload_aaaaaaaaaaaaaaaaaaaaaaaa] 请核对附件。'),
            content_parts: [{ type: 'file', media_type: 'file', mime_type: 'text/plain', name: '社区花园需求.txt', upload_id: 'upload_aaaaaaaaaaaaaaaaaaaaaaaa' }],
            input_media: [{ type: 'file', upload_id: 'upload_aaaaaaaaaaaaaaaaaaaaaaaa' }] },
          { ...human('named-reference', '继续检查。'),
            input_media: [{ type: 'file', filename: '社区花园需求.txt', file_reference: { upload_id: 'upload_bbbbbbbbbbbbbbbbbbbbbbbb' } }] },
          { ...human('safe-name', '[file: /api/uploads/upload_cccccccccccccccccccccccc]'),
            context_refs: { attachments: [{ type: 'file', name: '<img src=x onerror=alert(1)>.txt', upload_id: 'upload_cccccccccccccccccccccccc' }] } },
          { ...human('unnamed', '[file: /api/uploads/upload_dddddddddddddddddddddddd]'),
            input_media: [{ type: 'file', upload_id: 'upload_dddddddddddddddddddddddd' }] },
          ...Array.from({ length: 60 }, (_, i) => ({ ...human('media' + i, '只有媒体 ' + i), input_media: [{ type: 'image', source: '/api/uploads/history-' + i + '.png' }] })),
        ];
        workspace.setMessages(items, { previewUrlForPath: path => path.startsWith('/api/uploads/') ? path : '',
          onLinkPreview: request => openedFiles.push({ path: request.path, source: request.source }),
          onResourcePreview: () => { throw new Error('denied resource must never open'); } });
      });
      await settle();
      check('closed preview has no reference DOM or historical media loads', await page.locator('.chatRoundPreview img').count() === 0
        && !mediaRequests.some(url => url.includes('history-')));
      await page.locator('.chatRoundDirectory').click();
      const card = page.locator('.chatRoundPreviewItem').filter({ has: page.locator('[data-round-follow="refs"]') });
      check('native image and attachment refs deduplicated across body and metadata', await card.locator('[data-message-attachment-path]').count() === 2
        && await card.locator('img.composerImageRefThumb').count() === 1);
      check('metadata-only image references use thumbnails', await page.locator('.chatRoundPreview img.composerImageRefThumb').count() === 61);
      check('resource chip reused and removed from prose', await card.locator('.portalResourceChip').count() === 1
        && !(await card.locator('.chatRoundPreviewJump').innerText()).match(/\[image:|\[file:|decision_aaaaaaaaaaaa/));
      check('no substitute icons or duplicate bodies', await card.locator('.chatRoundAttachmentIcon,.chatRoundAttachments,.chatRoundFull').count() === 0);
      const previewCard = id => page.locator('.chatRoundPreviewItem').filter({ has: page.locator(`[data-round-follow="${id}"]`) });
      const named = previewCard('named-upload'), reference = previewCard('named-reference'), safe = previewCard('safe-name');
      assert.deepEqual(await named.evaluate(node => ({ count: node.querySelectorAll('[data-message-attachment-path]').length,
        name: node.querySelector('.composerImageRefLabel')?.textContent,
        title: node.querySelector('[data-message-attachment-path]')?.dataset.portalTooltip,
        text: node.querySelector('.chatRoundPreviewJump')?.textContent })),
      { count: 1, name: '社区花园需求.txt', title: '社区花园需求.txt', text: '请核对附件。' });
      check('body reference is enriched from protocol name before deduplication', true);
      check('metadata file_reference and filename use the same native label', await reference.locator('.composerImageRefLabel').innerText() === '社区花园需求.txt'
        && await reference.locator('[data-message-attachment-path]').getAttribute('data-message-attachment-path') === '/api/uploads/upload_bbbbbbbbbbbbbbbbbbbbbbbb');
      check('names are text and do not create injected markup', await safe.locator('.composerImageRefLabel').innerText() === '<img src=x onerror=alert(1)>.txt'
        && await safe.locator('img').count() === 0);
      check('unnamed references retain their existing fallback', await previewCard('unnamed').locator('.composerImageRefLabel').innerText() === 'file');
      check('existing attachment and image metadata supply titles', await card.locator('[data-message-attachment-kind="file"] .composerImageRefLabel').innerText() === '阅读笔记.md'
        && await card.locator('[data-message-attachment-kind="image"]').getAttribute('data-portal-tooltip') === '图片说明.png');
      check('labels require no upload or metadata reads', !mediaRequests.some(url => /upload_[a-d]{24}/.test(url)));
      await card.locator('[data-message-attachment-kind="file"]').click();
      check('file uses existing workspace open callback once', await page.evaluate(() => openedFiles.length === 1 && openedFiles[0].path === 'workspace_aaaaaaaaaaaa/file/notes.md'));
      await named.locator('[data-message-attachment-path]').click();
      check('named upload opens its canonical reference once', await page.evaluate(() => openedFiles.length === 2 && openedFiles[1].path === '/api/uploads/upload_aaaaaaaaaaaaaaaaaaaaaaaa'));
      await card.locator('[data-message-attachment-kind="image"]').click();
      check('image uses existing image editor once', await page.evaluate(() => openedImages.length === 1 && openedImages[0].src === '/api/uploads/preview-ref.png'));
      await card.locator('.portalResourceChip').click();
      await page.waitForFunction(() => refReads.length > 0 && toasts.length > 0);
      check('resource click preserves live authorization failure', await page.evaluate(() => refReads.some(url => url.includes('reference?'))));
      check('offscreen historical thumbnails remain deferred', await page.locator('.chatRoundPreview img[data-portal-deferred-image]:not([src])').count() > 0
        && mediaRequests.filter(url => url.includes('history-')).length < 60);
      await shot('references-desktop');
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => document.documentElement.style.fontSize = '20px');
      await settle();
      check('390px references fit the preview', await card.evaluate(n => n.scrollWidth <= n.clientWidth + 1)
        && await named.evaluate(n => n.scrollWidth <= n.clientWidth + 1));
      await shot('references-390');
      await page.keyboard.press('Escape');
      check('closing preview removes media DOM and disconnects observer', await page.locator('.chatRoundPreview img').count() === 0
        && await page.evaluate(() => previewObservers.length > 0 && previewObservers.every(o => o.released)));
      await page.locator('.chatRoundDirectory').click();
      await page.evaluate(() => workspace.destroy());
      check('destroy releases reopened preview observer', await page.evaluate(() => previewObservers.every(o => o.released)));
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'references-result.json'), JSON.stringify({ checks, errors, mediaRequests }, null, 2));
      console.log('Round preview reference checks passed: ' + checks.length); await context.close(); return;
    }
    if (process.env.RAIL_PREVIEW_ONLY) {
      const seed = async target => {
        await target.evaluate(() => {
          PortalI18n.apply('zh-CN');
          window.previewSource = '请逐步说明这个真实问题的上下文与来源，保留每一步推导和限制。'.repeat(8)
            + '\n第二行包含很长的地址：https://example.invalid/' + 'long-path-'.repeat(35)
            + '\n<img src=x onerror=alert(1)> 只是用户原文，不应执行。\n[image:/api/uploads/preview-test.png]';
          items = [human('short', '谢谢。'), human('long', previewSource),
            human('medium', '请解释这段文字的实际含义和上下文联系'), human('multiline', '第一行\n第二行\n第三行\n第四行'), agent('reply', 'short')];
          workspace.setMessages(items);
        }); await target.locator('.chatRoundDirectory').click();
      };
      const card = (target, key) => target.locator('.chatRoundPreviewItem').filter({ has: target.locator(`[data-round-follow="${key}"]`) });
      await page.setViewportSize({ width: 1440, height: 1000 }); await seed(page);
      const short = card(page, 'short'), long = card(page, 'long'), medium = card(page, 'medium');
      check('short prose has no ineffective expand button', await short.locator('.chatRoundExpand').count() === 0);
      check('long and multiline prose expose explicit expand', await long.getByRole('button', { name: '展开', exact: true }).count() === 1
        && await card(page, 'multiline').getByRole('button', { name: '展开', exact: true }).count() === 1);
      check('preview exposes marker chooser', await short.locator('.chatRoundFollow').getAttribute('aria-label') === '选择标记');
      await togglePreviewFlag(page, short.locator('.chatRoundFollow'));
      check('preview shows selected flag', await short.locator('.chatRoundFollow').getAttribute('aria-pressed') === 'true');
      await page.keyboard.press('Escape');
      await page.locator('[data-message-key="reply"]').click({ button: 'right' });
      const menu = page.locator('body > [data-portal-message-menu]');
      check('reply menu uses same cancel action and preserves pin', await menu.locator('[data-portal-message-action="round-follow"]').innerText() === '取消Flag'
        && await menu.locator('[data-portal-message-action="pin"]').innerText() === '置顶消息');
      await menu.locator('[data-portal-message-action="round-follow"]').click();
      await page.locator('[data-message-key="reply"]').click({ button: 'right' });
      check('message menu cancellation synchronizes to Flag', await menu.locator('[data-portal-message-action="round-follow"]').innerText() === 'Flag');
      await page.keyboard.press('Escape'); await page.locator('.chatRoundDirectory').click();
      await long.locator('.chatRoundExpand').hover(); await page.waitForTimeout(600);
      check('hover does not expand or create duplicate text', await long.locator('.chatRoundExpand').getAttribute('aria-expanded') === 'false' && await page.locator('.chatRoundFull').count() === 0);
      await long.locator('.chatRoundExpand').click();
      const expanded = await long.evaluate(n => {
        const text = n.querySelector('.chatRoundPreviewJump');
        return { copies: [...n.querySelectorAll('*')].filter(e => e.childNodes.length === 1 && e.firstChild.nodeType === Node.TEXT_NODE && e.textContent === text.textContent).length,
          clipped: text.scrollHeight > text.clientHeight + 1, label: n.querySelector('.chatRoundExpand').textContent, imgs: n.querySelectorAll('img').length };
      });
      check('expanded text occupies original location once and remains safe', expanded.copies === 1 && !expanded.clipped && expanded.label === '收起' && expanded.imgs === 0);
      check('attachment retained as a native Chat reference without placeholder leakage', await long.locator('[data-message-attachment-path="/api/uploads/preview-test.png"]').count() === 1
        && !(await long.locator('.chatRoundPreviewJump').innerText()).includes('[image:') && await page.evaluate(() => items[1].content === previewSource));
      await shot('preview-expanded-desktop');
      await togglePreviewFlag(page, long.locator('.chatRoundFollow'));
      check('flag refresh preserves inline expansion', await long.locator('.chatRoundPreviewJump').evaluate(n => n.classList.contains('is-expanded')));
      await long.locator('.chatRoundExpand').focus(); await page.keyboard.press('Enter');
      check('keyboard collapses same text', await long.locator('.chatRoundExpand').getAttribute('aria-expanded') === 'false'
        && await long.locator('.chatRoundPreviewJump').evaluate(n => n.scrollHeight > n.clientHeight));
      check('medium text fits desktop without expand', await medium.locator('.chatRoundExpand').count() === 0);
      await page.setViewportSize({ width: 240, height: 844 }); await page.waitForTimeout(100);
      check('resize adds expansion only when text becomes truncated', await medium.locator('.chatRoundExpand').count() === 1);
      await page.setViewportSize({ width: 1440, height: 1000 }); await page.waitForTimeout(100);
      check('resize removes obsolete expansion', await medium.locator('.chatRoundExpand').count() === 0);
      await page.evaluate(() => {
        const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight'); window.previewReads = 0;
        Object.defineProperty(Element.prototype, 'scrollHeight', { configurable: true, get() { if (this.matches('.chatRoundPreviewJump')) previewReads++; return descriptor.get.call(this); } });
      });
      await page.locator('.chatRoundPreviewList').evaluate(n => { n.scrollTop = 200; }); await page.waitForTimeout(150);
      check('preview scrolling does not remeasure all text', await page.evaluate(() => previewReads === 0));
      await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark'); await shot('preview-collapsed-desktop-dark');
      await page.keyboard.press('Escape'); await page.evaluate(() => PortalI18n.apply('en'));
      await page.locator('.chatRoundDirectory').click();
      check('English catalog exposes marker choices', await short.locator('.chatRoundFollow').getAttribute('aria-label') === 'Choose marker'
        && await long.locator('.chatRoundFollow').getAttribute('aria-label') === 'Choose marker · Flag');
      await page.keyboard.press('Escape'); await page.locator('[data-message-key="long"]').click({ button: 'right' });
      check('English message menu uses same Unflag', await menu.locator('[data-portal-message-action="round-follow"]').innerText() === 'Unflag');
      await page.keyboard.press('Escape');
      const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const small = await mobile.newPage(); small.on('pageerror', e => errors.push(e.message)); await setup(small);
      await small.evaluate(() => document.documentElement.style.fontSize = '20px'); await seed(small);
      const smallLong = card(small, 'long');
      await smallLong.locator('.chatRoundExpand').tap();
      check('390 font20 tap expands original prose with reachable control', await smallLong.locator('.chatRoundExpand').getAttribute('aria-expanded') === 'true'
        && await smallLong.locator('.chatRoundExpand').evaluate(n => n.getBoundingClientRect().width >= 44 && n.getBoundingClientRect().height >= 44));
      geometry.push(await small.locator('.chatRoundPreview').evaluate(n => ({ width: n.clientWidth, x: n.getBoundingClientRect().x, right: n.getBoundingClientRect().right, bottom: n.getBoundingClientRect().bottom, scrollWidth: n.scrollWidth })));
      check('long links and large text stay within viewport', geometry.at(-1).x >= 0 && geometry.at(-1).right <= 390 && geometry.at(-1).bottom <= 844 && geometry.at(-1).scrollWidth === geometry.at(-1).width);
      if (output) await small.screenshot({ path: path.join(output, 'preview-expanded-390-font20.png') });
      await smallLong.locator('.chatRoundExpand').tap();
      check('tap collapses without duplicate text', await smallLong.locator('.chatRoundExpand').getAttribute('aria-expanded') === 'false' && await small.locator('.chatRoundFull').count() === 0);
      await smallLong.locator('.chatRoundPreviewJump').tap();
      check('expanded contract preserves exact message navigation', await small.locator('[data-message-key="long"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      await small.evaluate(() => { items = Array.from({ length: 80 }, (_, i) => human('dense' + i, i === 35 ? previewSource : '短文')); workspace.setMessages(items); });
      await small.waitForTimeout(100);
      const box = await small.locator('[data-round-key="dense35"]').boundingBox(); await small.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
      await small.locator('.chatRoundDirectory').tap();
      const denseLong = card(small, 'dense35');
      await denseLong.locator('.chatRoundExpand').tap();
      check('dense preview expands only the actual long item', await small.locator('.chatRoundExpand').count() === 1 && await denseLong.locator('.chatRoundPreviewJump').evaluate(n => n.classList.contains('is-expanded')));
      if (output) await small.screenshot({ path: path.join(output, 'preview-dense-390.png') });
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'preview-result.json'), JSON.stringify({ checks, geometry, errors }, null, 2));
      console.log('Round rail preview/Flag checks passed: ' + checks.length); await mobile.close(); await context.close(); return;
    }
    if (process.env.RAIL_MARKERS_ONLY) {
      const afterLayout = target => target.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const seed = async target => {
        await target.evaluate(() => {
          const titles = ['请逐步解释注意力机制如何结合上下文理解长句，并比较不同方案的限制。', '这是正在排队的已受理请求，请保留我的原始问题。', '正在运行：说明查询、键和值如何共同决定注意力权重。', '关注这条排队请求，稍后继续检查可复现的例子。', '关注正在运行的验证工作，请同时保留原文与来源。'];
          items = titles.flatMap((text, i) => [human('m' + i, text), agent('ma' + i, 'm' + i)]);
          workspace.setMessages(items);
          workspace.setRequestProgress({ cursor: 50, input_states: titles.map((_, i) => workInput('m' + i, ['completed', 'queued', 'running', 'queued', 'running'][i])) });
        });
        await afterLayout(target);
        await target.locator('.chatRoundDirectory').click();
        for (const key of ['m3', 'm4']) await togglePreviewFlag(target, target.locator(`[data-round-follow="${key}"]`));
        await target.keyboard.press('Escape'); await afterLayout(target);
      };
      const inspect = target => target.evaluate(() => ({
        line: getComputedStyle(document.querySelector('.chatRoundLane'), '::before').content,
        divider: getComputedStyle(document.querySelector('.chatRoundRail')).borderInlineStartWidth,
        nodes: [...document.querySelectorAll('.chatRoundNode')].map(node => {
          const core = node.querySelector('.chatRoundCore'), flag = node.querySelector('.chatRoundFlag');
          const shape = getComputedStyle(core, '::after'), halo = getComputedStyle(core, '::before');
          const c = core.getBoundingClientRect(), f = flag?.getBoundingClientRect(), n = node.getBoundingClientRect();
          return { key: node.dataset.roundKey, text: node.textContent, label: node.getAttribute('aria-label'), shape: shape.display, radius: shape.borderRadius,
            border: shape.borderWidth, rotation: shape.transform, background: shape.backgroundColor, halo: halo.animationDuration,
            coreAnimation: getComputedStyle(core).animationName, flag: !!flag,
            offset: f ? [f.x + f.width / 2 - c.x - c.width / 2, f.y + f.height / 2 - c.y - c.height / 2] : null,
            width: n.width, height: n.height };
        }),
        hiddenFlags: document.querySelectorAll('.chatRoundNode:not(.is-followed) .chatRoundFlag').length,
        counts: document.querySelectorAll('.chatRoundCount,.chatRoundFlagCount,[data-running-count]').length,
      }));
      const dense = async (target, touch) => {
        await target.evaluate(() => {
          items = Array.from({ length: 80 }, (_, i) => human('dense' + i, '第 ' + i + ' 条独立用户请求：请保留这条消息的准确位置与完整上下文。'));
          workspace.setMessages(items);
          workspace.setRequestProgress({ cursor: 51, input_states: [workInput('dense35', 'queued'), workInput('dense36', 'running')] });
        }); await afterLayout(target);
        check('dense markers keep every message identity', await target.locator('.chatRoundNode').count() === 80);
        const spacing = await target.evaluate(() => {
          const view = workspace.els.messages, b = view.getBoundingClientRect(), hit = matchMedia('(pointer:coarse)').matches ? 44 : 26;
          return [...document.querySelectorAll('.chatRoundNode')].every(node => {
            const row = view.querySelector(`[data-message-key="${node.dataset.roundKey}"]`), y = row.getBoundingClientRect().top - b.top + view.scrollTop;
            return Math.abs(parseFloat(node.style.top) - (hit / 2 + y / view.scrollHeight * (document.querySelector('.chatRoundLane').clientHeight - hit))) < 1;
          });
        }); check('dense positions stay measured rather than redistributed', spacing);
        const box = await target.locator('[data-round-key="dense35"]').boundingBox();
        if (touch) await target.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
        else await target.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        check('overlapping pointer targets locate nearest message directly', await target.locator('[data-message-key="dense35"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
        await target.locator('.chatRoundDirectory').click();
        const follow = target.locator('[data-round-follow="dense35"]');
        await togglePreviewFlag(target, follow, touch);
        await afterLayout(target);
        check('dense list flags only selected identity', await target.evaluate(() => workspace.isRoundFollowed('dense35') && !workspace.isRoundFollowed('dense36')));
        if (output) await target.screenshot({ path: path.join(output, touch ? 'markers-dense-390.png' : 'markers-dense-desktop.png') });
        const jump = target.locator('.chatRoundPreviewItem').filter({ has: target.locator('[data-round-follow="dense35"]') }).locator('.chatRoundPreviewJump');
        if (touch) await jump.tap(); else await jump.click();
        check('dense list actual action locates exact message', await target.locator('[data-message-key="dense35"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
        await target.locator('[data-round-key="dense0"]').focus();
        const visited = [await target.evaluate(() => document.activeElement.dataset.roundKey)];
        for (let i = 1; i < 80; i++) { await target.keyboard.press('ArrowDown'); visited.push(await target.evaluate(() => document.activeElement.dataset.roundKey)); }
        check('all dense identities reachable individually by keyboard', new Set(visited).size === 80 && visited.every((key, i) => key === 'dense' + i));
        await target.keyboard.press('Home'); await target.keyboard.press('ArrowDown'); await target.keyboard.press('Enter');
        check('dense keyboard activation stays exact without ambiguous list', await target.locator('[data-message-key="dense1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      };
      await page.setViewportSize({ width: 1440, height: 1000 }); await seed(page);
      const desktop = await inspect(page); geometry.push({ desktop });
      check('each accepted input owns one marker without counts or hidden flags', desktop.nodes.length === 5 && desktop.counts === 0 && desktop.hiddenFlags === 0 && desktop.nodes.every(n => n.text === ''));
      check('queued is a hollow diamond', desktop.nodes[1].radius === '0px' && desktop.nodes[1].border === '1px' && desktop.nodes[1].rotation.startsWith('matrix(0.707'));
      check('flag replaces queued and running core at same center', desktop.nodes.slice(3).every(n => n.flag && n.shape === 'none' && n.offset.every(v => Math.abs(v) < .1)));
      check('ordinary and flagged running keep stable core and green halo', desktop.nodes[2].coreAnimation === 'none' && desktop.nodes[2].halo === '3.6s' && desktop.nodes[4].halo === '3.6s');
      check('corrected node line and border contract retained', desktop.line !== 'none' && desktop.divider === '0px');
      await shot('markers-desktop-light');
      await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark'); await shot('markers-desktop-dark');
      await dense(page, false);
      const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const small = await mobile.newPage(); small.on('pageerror', error => errors.push(error.message)); await setup(small);
      await small.evaluate(() => document.documentElement.style.fontSize = '20px'); await seed(small);
      const narrow = await inspect(small); geometry.push({ narrow });
      check('390 font20 preserves independent 44px targets and centered flags', narrow.nodes.every(n => n.width >= 44 && n.height >= 44 && (!n.offset || n.offset.every(v => Math.abs(v) < .1))));
      if (output) await small.screenshot({ path: path.join(output, 'markers-390-font20-light.png') });
      await small.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
      if (output) await small.screenshot({ path: path.join(output, 'markers-390-font20-dark.png') });
      await dense(small, true);
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'markers-result.json'), JSON.stringify({ checks, geometry, errors, boundary: 'Native Chat with synthetic accepted input states. No private user session, API writes or official TAU.' }, null, 2));
      console.log('Round rail marker review passed: ' + checks.length); await mobile.close(); await context.close(); return;
    }
    if (process.env.RAIL_PERF_ONLY) {
      const measurements = [];
      for (const baseline of [true, false, false, true]) {
        await setup(page, { baseline });
        await page.evaluate(() => {
          const originalRect = Element.prototype.getBoundingClientRect, originalRAF = window.requestAnimationFrame;
          window.probe = { reads: 0, railGeometryReads: 0, frames: [], longtasks: [], mutations: 0, railAdds: 0 };
          Element.prototype.getBoundingClientRect = function() {
            if (this.matches('.messageList > [data-message-key]')) probe.reads++;
            if (this.matches('.chatRoundViewport') || this.closest('.chatRoundRail')) probe.railGeometryReads++;
            return originalRect.call(this);
          };
          window.requestAnimationFrame = fn => originalRAF(time => { const start = performance.now(); fn(time); probe.frames.push(performance.now() - start); });
          new PerformanceObserver(list => probe.longtasks.push(...list.getEntries().map(e => e.duration))).observe({ entryTypes: ['longtask'] });
          new MutationObserver(records => { probe.mutations += records.length; probe.railAdds += records.filter(r => r.type === 'childList' && r.target.closest?.('.chatRoundRail')).reduce((n, r) => n + r.addedNodes.length, 0); })
            .observe(document.getElementById('chat'), { childList: true, subtree: true, attributes: true });
          items = Array.from({ length: 1000 }, (_, i) => i % 2 ? { id: 'p' + i, role: 'assistant', content: '这是一段用于连续阅读的真实组件长会话样稿。'.repeat(5) } : human('p' + i, '请说明第' + i + '轮的步骤和来源。'));
          window.mountStart = performance.now(); workspace.setMessages(items, { history: { can_load_older: true } });
        });
        await page.waitForTimeout(400); await settle();
        const mount = await page.evaluate(() => ({ reads: probe.reads, railGeometryReads: probe.railGeometryReads, callbacksMs: probe.frames.reduce((a,b) => a+b, 0), nodes: document.getElementById('chat').querySelectorAll('*').length,
          markers: document.querySelectorAll('.chatRoundNode').length, flagElements: document.querySelectorAll('.chatRoundNode .chatRoundFlag').length,
          rowWidth: document.querySelector('.messageList').clientWidth, longtasks: [...probe.longtasks] }));
        await page.evaluate(() => { probe.reads = probe.railGeometryReads = probe.mutations = probe.railAdds = 0; probe.frames = []; probe.longtasks = []; });
        await page.evaluate(async () => {
          const view = workspace.els.messages, range = view.scrollHeight - view.clientHeight;
          for (let step = 0; step < 120; step++) await new Promise(resolve => requestAnimationFrame(() => { view.scrollTop = range * ((step % 60) / 59); resolve(); }));
        }); await settle();
        const scrolling = await page.evaluate(() => ({ ...probe, frames: [...probe.frames], longtasks: [...probe.longtasks] }));
        await page.evaluate(() => { probe.reads = probe.railGeometryReads = probe.mutations = probe.railAdds = 0; probe.frames = []; probe.longtasks = []; });
        await page.waitForTimeout(1000);
        const idle = await page.evaluate(() => ({ ...probe }));
        if (!baseline) {
          check('cached scroll avoids row and rail geometry measurement and search DOM rebuild', scrolling.reads === 0 && scrolling.railGeometryReads === 0 && scrolling.railAdds === 0);
          check('long conversation keeps 500 independent markers without hidden flag elements', mount.markers === 500 && mount.flagElements === 0);
          check('idle has no rail scheduling work', idle.reads === 0 && idle.frames.length === 0 && idle.railAdds === 0);
          await page.evaluate(() => { workspace.setRoundRailPresented(false); probe.reads = 0; probe.frames = []; workspace.appendMessage(human('hidden-request', '隐藏内容')); });
          await page.waitForTimeout(100);
          check('hidden rail disconnects content measurement', await page.evaluate(() => probe.reads === 0));
        }
        measurements.push({ baseline, mount, scrolling, idle });
      }
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'performance-result.json'), JSON.stringify({ checks, measurements, errors }, null, 2));
      console.log('Round rail performance review passed: ' + checks.length); await context.close(); return;
    }
    if (process.env.RAIL_PROTOCOL_ONLY) {
      await page.evaluate(() => {
        workspace.destroy();
        PortalAPI = { get: async () => ({ accounts: [] }) };
        PortalInstructionClient = { execute: async () => ({ entries: [] }) };
        PortalResidentService = { inputHistory: async () => ({ entries: [] }), runtimeSettings: async () => ({ settings: {} }), runtimeCatalog: async () => ({}) };
        window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'protocolRail', autoLoad: false,
          target: { resident_id: 'resident_111111111111' } });
        panel.residentProjection = { resident: { resident_id: 'resident_111111111111' },
          chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: 'resident_111111111111' }, composer: { can_send: true } } };
        const request = id => ({ message_id: id, role: 'user', origin: { sender: { kind: 'user', id: 'reader' } }, content_parts: [{ type: 'text', text: '真实协议请求 ' + id }] });
        window.raw = { cursor: 20, resident: { resident_id: 'resident_111111111111' }, controls: {}, state: { work_state: 'idle' },
          family_facade: { title: '协议契约样稿', members: [] }, messages: [
            { ...request('family-running'), relay: { resident_id: 'resident_222222222222', ...workInput('member-input', 'running') } },
            { ...request('family-done'), relay: { resident_id: 'resident_222222222222', ...workInput('member-done', 'completed') } },
            { ...request('resident-running'), origin: { sender: { kind: 'resident', id: 'resident_333333333333' } }, relay: workInput('resident-running', 'running') }],
          input_states: [{ message_id: 'family-running', state: 'completed' }, { message_id: 'family-done', state: 'completed' }] };
        panel.protocol = { snapshot: () => raw, load: () => Promise.resolve(raw), destroy() {} };
        panel.applySnapshot(raw, { type: 'snapshot' });
      }); await settle();
      check('formal Family relay overrides router completion under user request ID', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      check('resident request is not promoted to a human node', await page.locator('[data-round-key="resident-running"]').count() === 0);
      // Native Portal traces: an instruction first-batch read closes normally,
      // then a ready successor closes its predecessor without another global
      // events-ready. These receipts diagnose transport disposal, not failure.
      await page.evaluate(() => {
        window.dispatchEvent(new CustomEvent('portal:connection-closed', { detail: {
          instruction: 'WatchConversationEvents', reason: 'first_batch_read', transport_id: 'read-1',
        } }));
        window.dispatchEvent(new CustomEvent('portal:connection-ready', { detail: { stream_id: 'next', predecessor_stream_id: 'old' } }));
        window.dispatchEvent(new CustomEvent('portal:connection-closed', { detail: { reason: 'handover_complete', stream_id: 'old', successor_stream_id: 'next' } }));
        window.dispatchEvent(new CustomEvent('portal:events-resubscribed', { detail: { added_resident_ids: [] } }));
      }); await settle();
      check('normal first-batch close and ready successor handover retain verified running', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      check('normal transport receipts retain completed request labels', (await page.locator('[data-round-key="family-done"]').getAttribute('aria-label')).includes('已完成'));
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('portal:connection-closed', { detail: { reason: 'leadership_released', stream_id: 'old' } })));
      await settle();
      check('stream ownership release does not invalidate the independent Resident snapshot', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      await page.evaluate(() => panel.workspace.controller.requestProgressDisconnected()); await settle();
      check('Resident protocol errors still invalidate request progress', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => panel.applySnapshot(raw, { type: 'snapshot' })); await settle();
      check('formal snapshot restores progress after Resident error', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      await page.evaluate(() => { window.dispatchEvent(new Event('portal:events-error')); panel.applySnapshot(raw, { type: 'event' }); }); await settle();
      check('cached event cannot certify disconnected progress', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => panel.applySnapshot(raw, { type: 'snapshot' })); await settle();
      check('protocol snapshot clears unknown after recovery without requiring global ready', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await settle();
      check('offline still clears running certainty', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => panel.applySnapshot(raw, { type: 'snapshot' })); await settle();
      await page.evaluate(() => panel.setPresented(false));
      check('native Resident presentation suspends running certainty', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => panel.setPresented(true)); await settle();
      check('native Resident resume verifies through protocol load', await page.locator('[data-round-key="family-running"].is-running').count() === 1);
      await shot('protocol-family-running');
      await page.evaluate(() => { raw = { ...raw, cursor: 21, messages: raw.messages.map(m => m.message_id === 'family-running' ? { ...m, relay: { ...m.relay, work: workInput('member-input', 'completed').work } } : m) }; panel.applySnapshot(raw, { type: 'snapshot' }); }); await settle();
      check('member completion stops only its user request', await page.locator('.chatRoundNode.is-running').count() === 0);
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'protocol-result.json'), JSON.stringify({ checks, errors }, null, 2));
      console.log('Round rail protocol review passed: ' + checks.length); await context.close(); return;
    }
    if (process.env.RAIL_VISUAL_ONLY) {
      const seed = async target => target.evaluate(() => {
        window.originalText = '请直观解释注意力机制：为什么每个词都需要结合上下文理解？请比较查询、键和值的作用，给出长中文例子，并保留这次截图中的细节。\n[image:/api/uploads/example-one.png]\n[image:/api/uploads/example-two.png]';
        items = [human('v1', originalText), agent('va1', 'v1'),
          human('v2', '这里需要进一步说明不同方案的限制，以及实际使用时应该如何选择。不要只列术语，请使用具体过程解释，让第一次接触的人也能看明白。'), agent('va2', 'v2'),
          human('v3', '请检查窄屏与大字体的阅读体验，保存关注之后仍可通过键盘返回原消息。')];
        workspace.setMessages(items);
        workspace.setRequestProgress({ cursor: 50, input_states: [workInput('v1', 'completed'), workInput('v2', 'running'), workInput('v3', 'queued')] });
      });
      await page.setViewportSize({ width: 1440, height: 1000 }); await seed(page); await settle();
      await page.locator('.chatRoundDirectory').click();
      const popup = page.locator('.chatRoundPreview'), first = popup.locator('.chatRoundPreviewItem').first();
      const desktop = await popup.evaluate(n => ({ width: n.clientWidth, height: n.clientHeight, itemHeights: [...n.querySelectorAll('article')].map(r => r.getBoundingClientRect().height), text: n.innerText }));
      check('compact preview width and rows', desktop.width >= 300 && desktop.width <= 340 && desktop.itemHeights.every(h => h < 115));
      check('short local time and clean attachment display', !/2026-|\[image:|\/api\/uploads/.test(desktop.text) && await first.locator('.chatRoundAttachments').innerText() === '图片 · 图片');
      check('no repeated follow text or tooltip', !(desktop.text.includes('标记本轮关注')) && await page.locator('.chatRoundNode:not([data-portal-tooltip-opt-out="1"])').count() === 0);
      await togglePreviewFlag(page, first.locator('.chatRoundFollow')); await settle();
      check('compact flag is an actual action', await page.evaluate(() => workspace.isRoundFollowed('v1')));
      await shot('preview-desktop-light');
      await first.locator('.chatRoundExpand').focus(); await page.keyboard.press('Enter');
      check('keyboard exposes full original prose inline', await first.locator('.chatRoundPreviewJump').evaluate(n => n.classList.contains('is-expanded')) && await first.locator('.chatRoundExpand').innerText() === '收起');
      await togglePreviewFlag(page, first.locator('.chatRoundFollow'));
      check('flag preserves expanded prose and source unchanged', await first.locator('.chatRoundPreviewJump').evaluate(n => n.classList.contains('is-expanded')) && await page.evaluate(() => items[0].content === originalText));
      await first.locator('.chatRoundExpand').click(); await page.mouse.move(10, 10);
      await first.locator('.chatRoundExpand').hover(); await page.waitForTimeout(570);
      check('hover does not expand prose or show black tooltip', await first.locator('.chatRoundExpand').getAttribute('aria-expanded') === 'false' && await page.locator('[role="tooltip"]:visible').count() === 0);
      await page.mouse.move(10, 10); await first.locator('.chatRoundExpand').click();
      await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark'); await shot('preview-desktop-dark');
      check('dark preview is opaque; node line remains without a divider beside the text', await popup.evaluate(n => !getComputedStyle(n).backgroundColor.includes('rgba'))
        && await page.locator('.chatRoundLane').evaluate(n => getComputedStyle(n, '::before').content !== 'none')
        && await page.locator('.chatRoundRail').evaluate(n => getComputedStyle(n).borderInlineStartWidth === '0px'));
      await first.locator('.chatRoundPreviewJump').click(); await settle();
      check('preview native click locates original message', await page.locator('[data-message-key="v1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      const smallContext = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const small = await smallContext.newPage(); small.on('pageerror', error => errors.push(error.message)); await setup(small);
      await small.evaluate(() => document.documentElement.style.fontSize = '20px'); await seed(small);
      await small.locator('.chatRoundDirectory').tap();
      const narrow = await small.locator('.chatRoundPreview').evaluate(n => ({ x: n.getBoundingClientRect().x, right: n.getBoundingClientRect().right, bottom: n.getBoundingClientRect().bottom, width: n.clientWidth,
        targets: [...n.querySelectorAll('.chatRoundFollow,.chatRoundExpand,.chatRoundPreviewHead button')].map(b => ({ w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height })) }));
      geometry.push({ desktop, narrow });
      check('390 large type keeps preview and touch targets reachable', narrow.x >= 0 && narrow.right <= 390 && narrow.bottom <= 844 && narrow.targets.every(r => r.w >= 44 && r.h >= 44));
      await togglePreviewFlag(small, small.locator('.chatRoundFollow').first(), true);
      check('touch flag action works without double tap', await small.evaluate(() => workspace.isRoundFollowed('v1')));
      if (output) await small.screenshot({ path: path.join(output, 'preview-390-font20-light.png') });
      await small.locator('.chatRoundExpand').first().focus(); await small.keyboard.press('Enter');
      check('390 keyboard full text stays scrollable', await small.locator('.chatRoundPreviewJump').first().evaluate(n => n.classList.contains('is-expanded')));
      await small.keyboard.press('Escape');
      check('escape restores rail focus without reopening preview', !(await small.locator('.chatRoundPreview').isVisible()) && await small.evaluate(() => document.activeElement.classList.contains('chatRoundDirectory')));
      await small.locator('.chatRoundDirectory').tap();
      await small.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
      if (output) await small.screenshot({ path: path.join(output, 'preview-390-font20-dark.png') });
      await small.locator('.chatRoundPreviewJump').first().tap();
      check('390 native tap navigates original message', await small.locator('[data-message-key="v1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'visual-result.json'), JSON.stringify({ checks, geometry, errors }, null, 2));
      console.log('Round rail visual review passed: ' + checks.length); await smallContext.close(); await context.close(); return;
    }
    if (process.env.RAIL_REVIEW_ONLY) {
      await page.locator('[data-workspace-history-search]').click();
      await page.locator('.portalConversationSearch input').fill('match'); await page.waitForTimeout(280); await settle();
      await page.locator('.conversationSearchResult').first().click();
      check('list navigation retains search ticks', await page.locator('.chatRoundSearchTick').count() === 2);
      await page.locator('[data-search-next]').click();
      check('next result retains ticks and finds agent', await page.locator('[data-message-key="a1"]').evaluate(n => n.classList.contains('messageQuoteTarget')) && await page.locator('.chatRoundSearchTick').count() === 2);
      await page.locator('[data-search-previous]').click();
      check('previous result finds input', await page.locator('[data-message-key="h1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      // All deferred pagination still goes through the existing history owner.
      for (const action of ['query', 'close', 'reading', 'thumb']) {
        if (!(await page.locator('.portalConversationSearch').isVisible())) await page.locator('[data-workspace-history-search]').click();
        await page.locator('.portalConversationSearch input').fill('match'); await page.waitForTimeout(260);
        await page.evaluate(() => {
          workspace.setMessages(items, { history: { can_load_older: true } });
          window.deferOlder = true;
          document.querySelectorAll('.messageQuoteTarget').forEach(node => node.classList.remove('messageQuoteTarget'));
        });
        await page.locator('[data-search-message="not-loaded"]').click();
        await page.waitForFunction(() => typeof finishOlder === 'function');
        if (action === 'query') await page.locator('.portalConversationSearch input').fill('none');
        if (action === 'close') await page.keyboard.press('Escape');
        if (action === 'reading') { await page.locator('.paneBody').hover(); await page.mouse.wheel(0, 95); }
        if (action === 'thumb') { await page.locator('.chatRoundThumb').focus(); await page.keyboard.press('ArrowDown'); }
        await page.evaluate(() => {
          workspace.setMessages([human('not-loaded', '早先请求'), ...items], { history: { can_load_older: false } });
          window.deferOlder = false; finishOlder(); delete window.finishOlder;
        }); await settle();
        check(action + ' cancels old in-flight reveal', await page.locator('[data-message-key="not-loaded"]').evaluate(n => !n.classList.contains('messageQuoteTarget')));
      }
      await page.keyboard.press('Escape');
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); }); await settle();
      check('hidden stops running certainty', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); }); await settle();
      check('resume requests verification and stays unknown', await page.evaluate(() => refreshCalls === 1) && await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => workspace.setRequestProgress({ cursor: 12, input_states: [workInput('h2', 'completed', '2026-11-04T12:02:00Z')] }));
      await page.evaluate(() => finishRefresh({ cursor: 11, input_states: [workInput('h2', 'running', '2026-11-04T12:01:00Z')] })); await settle();
      check('late recovery cannot resurrect terminal', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => { window.dispatchEvent(new Event('portal:events-error')); window.dispatchEvent(new Event('portal:events-ready')); });
      await page.evaluate(() => finishRefresh({ cursor: 13, input_states: [workInput('h3', 'running')] })); await settle();
      check('missing input state is not reused as running', !(await page.locator('[data-round-key="h2"]').getAttribute('class')).includes('is-running'));
      check('verified separate request resumes', (await page.locator('[data-round-key="h3"]').getAttribute('class')).includes('is-running'));
      await page.evaluate(() => workspace.setRequestProgress({ cursor: 14, input_states: [workInput('h2', 'running', '2026-11-04T12:00:00Z')] })); await settle();
      check('older updated_at cannot resurrect terminal with new cursor', !(await page.locator('[data-round-key="h2"]').getAttribute('class')).includes('is-running'));
      await page.evaluate(() => { items = Array.from({ length: 80 }, (_, i) => human('d' + i, '请求 ' + i)); workspace.setMessages(items); workspace.toggleRoundFollow('d0'); workspace.toggleRoundFollow('d1'); }); await settle();
      check('dense messages retain separate flags without counts', await page.locator('.chatRoundNode').count() === 80 && await page.locator('.chatRoundNode.is-followed').count() === 2 && await page.locator('.chatRoundFlagCount').count() === 0);
      const firstDense = await page.locator('.chatRoundNode').first().boundingBox();
      await page.mouse.click(firstDense.x + firstDense.width / 2, firstDense.y + firstDense.height / 2); await shot('review-dense-flags'); await page.keyboard.press('Escape');
      await page.evaluate(() => { items = [human('h1'), agent('agent-only')]; workspace.setMessages(items); }); await settle();
      await page.locator('[data-workspace-history-search]').click(); await page.locator('.portalConversationSearch input').fill('match'); await page.waitForTimeout(280);
      // Hover-independent native pointer click on a static result tick must reveal its row.
      await page.locator('.chatRoundSearchTick').last().click(); await settle();
      check('static search tick locates assistant body', await page.locator('[data-message-key="agent-only"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
      await page.locator('.chatRoundDirectory').click();
      check('directory retains only human rounds', await page.locator('.chatRoundPreviewItem').count() === 1);
      await page.keyboard.press('Escape');
      // Resize and fold use the existing viewport anchor, while the rail remaps positions.
      await page.evaluate(() => { items = [human('h1'), agent('a1', 'h1'), human('h2'), agent('a2', 'h2'), human('h3')]; workspace.setMessages(items); });
      await page.evaluate(() => workspace.revealMessage('h2', { alignStart: true }));
      const rowTop = () => page.locator('[data-message-key="h2"]').evaluate(n => n.getBoundingClientRect().top);
      const beforeTop = await rowTop();
      await page.evaluate(() => { workspace.setMessages([human('earlier'), agent('earlier-answer'), ...items]); }); await settle();
      check('prepend keeps reading anchor', Math.abs(await rowTop() - beforeTop) < 2);
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'review-result.json'), JSON.stringify({ checks, errors }, null, 2));
      console.log('Round rail integration review passed: ' + checks.length); await context.close(); return;
    }
    check('only accepted human inputs are nodes', await page.locator('.chatRoundNode').count() === 4);
    check('individual ordinary and Family running nodes', await page.locator('.chatRoundNode.is-running').count() === 2);
    check('queued is not animated', !(await page.locator('[data-round-key="h3"]').getAttribute('class')).includes('is-running'));
    const measured = await page.evaluate(() => {
      const view = workspace.els.messages, total = view.scrollHeight, bounds = view.getBoundingClientRect();
      return [...document.querySelectorAll('.chatRoundNode')].map(node => {
        const row = view.querySelector(`[data-message-key="${node.dataset.roundKey}"]`);
        return { key: node.dataset.roundKey, actual: parseFloat(node.style.top), expected: 13 + (row.getBoundingClientRect().top - bounds.top + view.scrollTop) / total * (document.querySelector('.chatRoundLane').clientHeight - 26) };
      });
    });
    check('nodes use actual loaded content layout', measured.every(point => Math.abs(point.actual - point.expected) < 1)); geometry.push({ measured });
    const node = page.locator('[data-round-key="h2"]');
    await node.click(); await settle();
    check('mouse locates exact request', await page.locator('[data-message-key="h2"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    await page.mouse.move(10, 10); await node.hover(); await page.waitForTimeout(260);
    check('hover shows enterable preview', await page.locator('.chatRoundPreview').isVisible());
    await page.locator('.chatRoundPreview').hover(); await page.waitForTimeout(280);
    check('preview survives pointer transfer', await page.locator('.chatRoundPreview').isVisible());
    await togglePreviewFlag(page, page.locator('.chatRoundPreview').getByRole('button', { name: '选择标记', exact: true }));
    await settle(); check('preview flag synchronized', (await node.getAttribute('class')).includes('is-followed'));
    await page.keyboard.press('Escape');
    check('escape closes preview', !(await page.locator('.chatRoundPreview').isVisible()));
    await page.locator('[data-message-key="a2"]').click({ button: 'right' });
    check('reply menu resolves formal request flag', await page.locator('body > [data-portal-message-menu] [data-portal-message-action="round-follow"]').innerText() === '取消Flag');
    await page.locator('body > [data-portal-message-menu] [data-portal-message-action="round-follow"]').click(); await settle();
    check('menu flag synchronized', !(await node.getAttribute('class')).includes('is-followed'));
    const pressPosition = await page.evaluate(() => workspace.els.messages.scrollTop);
    const rect = await node.boundingBox(); await page.mouse.move(rect.x + 15, rect.y + 13); await page.mouse.down(); await page.waitForTimeout(620); await page.mouse.up(); await settle();
    check('long press toggles once', await page.evaluate(() => workspace.isRoundFollowed('h2')));
    check('long press release does not navigate', (await page.evaluate(() => workspace.els.messages.scrollTop)) === pressPosition);
    await page.mouse.down(); await page.mouse.move(rect.x - 30, rect.y + 15); await page.waitForTimeout(600); await page.mouse.up();
    check('moving cancels long press', await page.evaluate(() => workspace.isRoundFollowed('h2')));
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) { if (key.includes('round-flags')) throw new Error('quota'); return original.call(this, key, value); };
      window.saveResult = workspace.toggleRoundFollow('h2'); Storage.prototype.setItem = original;
    });
    check('failed persistence rolls back and reports', await page.evaluate(() => !saveResult && workspace.isRoundFollowed('h2') && toasts.at(-1).text.includes('未保存')));
    const second = await context.newPage(); await setup(second);
    check('new document restores flags', await second.evaluate(() => workspace.isRoundFollowed('h2')));
    await second.evaluate(() => workspace.toggleRoundFollow('h2')); await page.waitForTimeout(80);
    check('cross tab flags synchronize', !(await page.evaluate(() => workspace.isRoundFollowed('h2')))); await second.close();
    await page.evaluate(() => { workspace.toggleRoundFollow('h2'); viewer = 'other'; window.dispatchEvent(new Event('portal:identity-changed')); }); await settle();
    check('personal identity isolation', !(await page.evaluate(() => workspace.isRoundFollowed('h2'))));
    await page.evaluate(() => { viewer = 'reader'; window.dispatchEvent(new Event('portal:identity-changed')); }); await settle();
    check('identity restoration', await page.evaluate(() => workspace.isRoundFollowed('h2')));
    await page.evaluate(() => workspace.setRequestProgress(contract.request_progress));
    await page.locator('[data-round-key="h1"]').focus(); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    check('keyboard node navigation', await page.locator('[data-message-key="h2"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    const position = await page.evaluate(() => workspace.els.messages.scrollTop);
    await page.evaluate(() => workspace.appendMessage(agent('stream', 'h4'))); await settle();
    check('streaming preserves reading position', Math.abs((await page.evaluate(() => workspace.els.messages.scrollTop)) - position) < 2);
    const thumb = page.locator('.chatRoundThumb'), grip = await thumb.boundingBox();
    await page.mouse.move(grip.x + 5, grip.y + grip.height / 2); await page.mouse.down(); await page.mouse.move(grip.x + 5, grip.y + grip.height / 2 + 50, { steps: 5 }); await page.mouse.up(); await settle();
    check('outer thumb drag scrolls without changing flag', await page.evaluate(p => workspace.els.messages.scrollTop > p && workspace.isRoundFollowed('h2'), position));
    await page.evaluate(() => workspace.setRequestProgress({ cursor: 11, input_states: [workInput('h2', 'completed')] })); await settle();
    check('running nodes stop independently', !(await node.getAttribute('class')).includes('is-running') && (await page.locator('[data-round-key="h4"]').getAttribute('class')).includes('is-running'));
    await page.evaluate(() => workspace.setRequestProgress(contract.request_progress)); await settle();
    check('old status cannot replace newer terminal', !(await node.getAttribute('class')).includes('is-running'));
    await page.evaluate(() => window.dispatchEvent(new Event('portal:events-error'))); await settle();
    check('disconnected is unknown not running', await page.locator('.chatRoundNode.is-running').count() === 0);
    await page.evaluate(() => window.dispatchEvent(new Event('portal:events-ready'))); await settle();
    check('reconnect alone is not status verification', await page.locator('.chatRoundNode.is-running').count() === 0);
    await page.evaluate(() => finishRefresh({ cursor: 12, relays: [workInput('h4', 'running')], input_states: [workInput('h2', 'running', '2026-11-04T12:01:00Z', 'execution-retry-h2')] })); await settle();
    check('fresh snapshot restores running', await page.locator('.chatRoundNode.is-running').count() === 2);
    const animation = await node.locator('.chatRoundCore').evaluate(n => ({ core: getComputedStyle(n).animationName, halo: getComputedStyle(n, '::before').animationDuration }));
    check('stable core and slow separate halo', animation.core === 'none' && animation.halo === '3.6s');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    check('reduced motion keeps static status', await node.locator('.chatRoundCore').evaluate(n => getComputedStyle(n, '::before').animationName === 'none')); await page.emulateMedia({ reducedMotion: 'no-preference' });
    await shot('desktop-light');
    await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark'); await shot('desktop-dark');
    await page.locator('[data-workspace-history-search]').click(); await page.locator('.portalConversationSearch input').fill('match'); await page.waitForTimeout(300); await settle();
    check('search uses exact loaded matches only', await page.locator('.chatRoundSearchTick').count() === 2);
    check('search is static', await page.locator('.chatRoundSearchTick').first().evaluate(n => getComputedStyle(n).animationName === 'none'));
    await page.locator('[data-search-next]').click();
    check('next search result locates message', await page.locator('[data-message-key="h1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    await page.locator('[data-search-next]').click();
    check('next search result can locate an assistant body', await page.locator('[data-message-key="a1"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    await page.locator('.portalConversationSearch input').fill('slow'); await page.waitForTimeout(250);
    await page.locator('.portalConversationSearch input').fill('none'); await page.waitForTimeout(250);
    await page.evaluate(() => slowSearch({ messages: items })); await settle();
    check('late search results discarded', await page.locator('.chatRoundSearchTick').count() === 0);
    await page.keyboard.press('Escape');
    await page.evaluate(() => { items = Array.from({ length: 80 }, (_, i) => human('dense' + i, '第 ' + i + ' 条独立请求')); workspace.setMessages(items); }); await settle();
    check('dense rounds keep individual measured markers', await page.locator('.chatRoundNode').count() === 80);
    const firstDense = await page.locator('.chatRoundNode').first().boundingBox();
    await page.mouse.click(firstDense.x + firstDense.width / 2, firstDense.y + firstDense.height / 2);
    check('overlapping hit areas locate one measured target', await page.locator('[data-message-key="dense0"]').evaluate(n => n.classList.contains('messageQuoteTarget')) && await page.locator('.chatRoundPreviewItem').count() === 0);
    await page.locator('.chatRoundDirectory').click(); await shot('desktop-dense');
    await page.keyboard.press('Escape');
    await page.evaluate(() => { scope = 'another'; workspace.setMessages([human('other')]); }); await settle();
    check('conversation switch drops marks and previous states', await page.locator('.chatRoundNode').count() === 1 && await page.locator('.chatRoundNode.is-followed').count() === 0);
    await page.evaluate(() => workspace.destroy()); check('destroy removes rail and observers', await page.locator('.chatRoundRail').count() === 0);
    await context.close();
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const touch = await mobile.newPage(); touch.on('pageerror', error => errors.push(error.message)); await setup(touch);
    await touch.evaluate(() => document.documentElement.style.fontSize = '20px'); await touch.waitForTimeout(100);
    await touch.locator('.chatRoundDirectory').tap();
    const box = await touch.locator('.chatRoundPreview').boundingBox();
    check('390 big font preview stays in viewport', box.x >= 0 && box.x + box.width <= 390 && box.y + box.height <= 844); geometry.push({ mobilePreview: box });
    await touch.locator('.chatRoundPreviewJump').nth(1).tap();
    check('touch directory opens exact round', await touch.locator('[data-message-key="h2"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    await touch.locator('[data-round-key="h3"]').tap();
    check('touch node navigates without longpress', await touch.locator('[data-message-key="h3"]').evaluate(n => n.classList.contains('messageQuoteTarget')));
    if (output) await touch.screenshot({ path: path.join(output, '390-font20.png') });
    await mobile.close();
    assert.deepEqual(errors, []); checks.push('no script errors');
    if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, geometry, errors, boundary: 'Real shared Chat, synthetic accepted messages/status/search; no private User session or formal TAU used.' }, null, 2));
    console.log('Round rail browser checks passed: ' + checks.length);
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
