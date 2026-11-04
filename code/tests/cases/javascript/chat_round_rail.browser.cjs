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
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_button_tooltips.js', 'portal_resource_chips.js',
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
        window.items = [human('h1', '比较两个方案，并解释各自限制。'), agent('a1', 'h1'),
          human('h2', '给出可以复现的验证步骤。'), agent('a2', 'h2'),
          human('h3', '检查大字体下的交互与状态。'), agent('a3', 'h3'),
          { ...human('h4', '并行处理的 Family 请求。'), relay: { state: 'running' } }, agent('a4', 'h4'),
          { ...human('local', '未接收'), local_delivery: { status: 'sending' } },
          { ...human('from-agent'), origin: { sender: { kind: 'resident', id: 'resident_aaaaaaaaaaaa' } } }];
        window.contract = { messages: items, composer: { can_send: true, history_scope: scope, queue: { items: [] } },
          request_progress: { cursor: 10, relays: [{ message_id: 'h4', state: 'running' }], input_states: [{ message_id: 'h1', state: 'completed' }, { message_id: 'h2', state: 'running' }, { message_id: 'h3', state: 'queued' }] } };
        window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'rail',
          title: 'Chat · 轮次阅读', historyScope: () => scope, refs: false, pinMessages: true, foldLongMessages: false,
          onLoadOlder: async () => { loads++; if (window.deferOlder) await new Promise(resolve => window.finishOlder = resolve); },
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
    if (process.env.RAIL_MARKERS_ONLY) {
      const afterLayout = target => target.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const seed = async target => {
        await target.evaluate(() => {
          const titles = ['请逐步解释注意力机制如何结合上下文理解长句，并比较不同方案的限制。', '这是正在排队的已受理请求，请保留我的原始问题。', '正在运行：说明查询、键和值如何共同决定注意力权重。', '关注这条排队请求，稍后继续检查可复现的例子。', '关注正在运行的验证工作，请同时保留原文与来源。'];
          items = titles.flatMap((text, i) => [human('m' + i, text), agent('ma' + i, 'm' + i)]);
          workspace.setMessages(items);
          workspace.setRequestProgress({ cursor: 50, input_states: titles.map((_, i) => ({ message_id: 'm' + i, state: ['completed', 'queued', 'running', 'queued', 'running'][i] })) });
        });
        await afterLayout(target);
        await target.locator('.chatRoundDirectory').click();
        for (const key of ['m3', 'm4']) await target.locator(`[data-round-follow="${key}"]`).click();
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
          workspace.setRequestProgress({ cursor: 51, input_states: [{ message_id: 'dense35', state: 'queued' }, { message_id: 'dense36', state: 'running' }] });
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
        check('overlapping pointer targets open local disambiguation', await target.locator('.chatRoundPreviewItem').count() > 1 && await target.locator('.chatRoundPreviewItem').count() < 80);
        const follow = target.locator('[data-round-follow="dense35"]');
        if (touch) await follow.tap(); else await follow.click();
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
            { ...request('family-running'), relay: { resident_id: 'resident_222222222222', message_id: 'member-input', state: 'running' } },
            { ...request('family-done'), relay: { resident_id: 'resident_222222222222', message_id: 'member-done', state: 'completed' } },
            { ...request('resident-running'), origin: { sender: { kind: 'resident', id: 'resident_333333333333' } }, relay: { state: 'running' } }],
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
      await page.evaluate(() => { raw = { ...raw, cursor: 21, messages: raw.messages.map(m => m.message_id === 'family-running' ? { ...m, relay: { ...m.relay, state: 'completed' } } : m) }; panel.applySnapshot(raw, { type: 'snapshot' }); }); await settle();
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
        workspace.setRequestProgress({ cursor: 50, input_states: [{ message_id: 'v1', state: 'completed' }, { message_id: 'v2', state: 'running' }, { message_id: 'v3', state: 'queued' }] });
      });
      await page.setViewportSize({ width: 1440, height: 1000 }); await seed(page); await settle();
      await page.locator('.chatRoundDirectory').click();
      const popup = page.locator('.chatRoundPreview'), first = popup.locator('.chatRoundPreviewItem').first();
      const desktop = await popup.evaluate(n => ({ width: n.clientWidth, height: n.clientHeight, itemHeights: [...n.querySelectorAll('article')].map(r => r.getBoundingClientRect().height), text: n.innerText }));
      check('compact preview width and rows', desktop.width >= 300 && desktop.width <= 340 && desktop.itemHeights.every(h => h < 115));
      check('short local time and clean attachment display', !/2026-|\[image:|\/api\/uploads/.test(desktop.text) && await first.locator('.chatRoundAttachments').innerText() === '图片 · 图片');
      check('no repeated follow text or tooltip', !(desktop.text.includes('标记本轮关注')) && await page.locator('.chatRoundNode:not([data-portal-tooltip-opt-out="1"])').count() === 0);
      await first.locator('.chatRoundFollow').click(); await settle();
      check('compact flag is an actual action', await page.evaluate(() => workspace.isRoundFollowed('v1')));
      await shot('preview-desktop-light');
      await first.locator('.chatRoundExpand').focus(); await page.keyboard.press('Enter');
      check('keyboard exposes full original prose', await first.locator('.chatRoundFull').isVisible() && (await first.locator('.chatRoundFull').innerText()).includes('截图中的细节'));
      await first.locator('.chatRoundFollow').click();
      check('flag preserves expanded prose and source unchanged', await first.locator('.chatRoundFull').isVisible() && await page.evaluate(() => items[0].content === originalText));
      await first.locator('.chatRoundExpand').click(); await page.mouse.move(10, 10);
      await first.locator('.chatRoundExpand').hover(); await page.waitForTimeout(570);
      check('hover exposes complete prose without black tooltip', await first.locator('.chatRoundFull').isVisible() && await page.locator('[role="tooltip"]:visible').count() === 0);
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
      await small.locator('.chatRoundFollow').first().tap();
      check('touch flag action works without double tap', await small.evaluate(() => workspace.isRoundFollowed('v1')));
      if (output) await small.screenshot({ path: path.join(output, 'preview-390-font20-light.png') });
      await small.locator('.chatRoundExpand').first().focus(); await small.keyboard.press('Enter');
      check('390 keyboard full text stays scrollable', await small.locator('.chatRoundFull').first().isVisible());
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
      await page.evaluate(() => workspace.setRequestProgress({ cursor: 12, input_states: [{ message_id: 'h2', state: 'completed', updated_at: '2026-11-04T12:02:00Z' }] }));
      await page.evaluate(() => finishRefresh({ cursor: 11, input_states: [{ message_id: 'h2', state: 'running', updated_at: '2026-11-04T12:01:00Z' }] })); await settle();
      check('late recovery cannot resurrect terminal', await page.locator('.chatRoundNode.is-running').count() === 0);
      await page.evaluate(() => { window.dispatchEvent(new Event('portal:events-error')); window.dispatchEvent(new Event('portal:events-ready')); });
      await page.evaluate(() => finishRefresh({ cursor: 13, input_states: [{ message_id: 'h3', state: 'running' }] })); await settle();
      check('missing input state is not reused as running', !(await page.locator('[data-round-key="h2"]').getAttribute('class')).includes('is-running'));
      check('verified separate request resumes', (await page.locator('[data-round-key="h3"]').getAttribute('class')).includes('is-running'));
      await page.evaluate(() => workspace.setRequestProgress({ cursor: 14, input_states: [{ message_id: 'h2', state: 'running', updated_at: '2026-11-04T12:00:00Z' }] })); await settle();
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
    await page.locator('.chatRoundPreview').getByRole('button', { name: '标记本轮关注', exact: true }).click();
    await settle(); check('preview flag synchronized', (await node.getAttribute('class')).includes('is-followed'));
    await page.keyboard.press('Escape');
    check('escape closes preview', !(await page.locator('.chatRoundPreview').isVisible()));
    await page.locator('[data-message-key="a2"]').click({ button: 'right' });
    check('reply menu resolves formal request flag', await page.locator('body > [data-portal-message-menu] [data-portal-message-action="round-follow"]').innerText() === '取消本轮关注');
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
    await page.evaluate(() => workspace.setRequestProgress({ cursor: 11, input_states: [{ message_id: 'h2', state: 'completed' }] })); await settle();
    check('running nodes stop independently', !(await node.getAttribute('class')).includes('is-running') && (await page.locator('[data-round-key="h4"]').getAttribute('class')).includes('is-running'));
    await page.evaluate(() => workspace.setRequestProgress(contract.request_progress)); await settle();
    check('old status cannot replace newer terminal', !(await node.getAttribute('class')).includes('is-running'));
    await page.evaluate(() => window.dispatchEvent(new Event('portal:events-error'))); await settle();
    check('disconnected is unknown not running', await page.locator('.chatRoundNode.is-running').count() === 0);
    await page.evaluate(() => window.dispatchEvent(new Event('portal:events-ready'))); await settle();
    check('reconnect alone is not status verification', await page.locator('.chatRoundNode.is-running').count() === 0);
    await page.evaluate(() => finishRefresh({ cursor: 12, relays: [{ message_id: 'h4', state: 'running' }], input_states: [{ message_id: 'h2', state: 'running' }] })); await settle();
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
    check('overlapping hit areas expose individual targets', await page.locator('.chatRoundPreviewItem').count() > 1); await shot('desktop-dense');
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
