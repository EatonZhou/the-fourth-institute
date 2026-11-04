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
const baselineProtocol = process.env.RAIL_QUEUE_BASELINE
  ? require('node:child_process').execFileSync('git', ['show', 'HEAD:code/management_portal/static/resident_protocol.js'], { cwd: repo }) : null;
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_button_tooltips.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_workbench_context.js',
  ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const output = process.env.SCREENSHOT_DIR;
if (output) fs.mkdirSync(output, { recursive: true });
(async () => {
  const server = http.createServer((request, response) => {
    const name = new URL(request.url, 'http://fixture').pathname.slice(1);
    if (!name) {
      response.setHeader('content-type', 'text/html');
      response.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('')
        + '<style>html,body{height:100%;margin:0}#chat{height:100%;min-height:0}</style><main id="chat"></main>'
        + '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join(''));
    } else {
      response.setHeader('content-type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
      const file = path.join(repo, 'management_portal/static', name.replace(/^static\//, ''));
      if (name !== 'routes.js' && !fs.existsSync(file)) { response.statusCode = 404; response.end(); return; }
      response.end(name === 'routes.js' ? routeScript : name === 'resident_protocol.js' && baselineProtocol ? baselineProtocol : fs.readFileSync(file));
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser; const checks = [], errors = [];
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1200, height: 850 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:' + server.address().port);
    await page.evaluate(async ({ queuedOnly }) => {
      window.__PORTAL_SITE__ = { storage_namespace: 'work-fact-test' };
      PortalIdentity = { username: () => 'fixture-reader', load: async () => {} };
      PortalEvents = { watchResident: () => () => {} };
      PortalAPI = { get: async () => ({ accounts: [] }) };
      PortalInstructionClient = { execute: async () => ({ entries: [] }) };
      window.rid = 'resident_111111111111';
      window.fact = (state, execution = 'execution-one', at = 10, kind = 'primary') => ({ state, kind,
        execution_id: execution, primary_message_id: 'primary', provider_turn_id: 'turn-one', delivery: 'delivered',
        updated_at: `2026-11-04T12:00:${String(at).padStart(2, '0')}Z` });
      const request = (id, text) => ({ message_id: id, role: 'user', origin: { sender: { kind: 'user', id: 'fixture-reader' } },
        content_parts: [{ type: 'text', text }] });
      window.raw = { cursor: 10, resident: { resident_id: rid }, state: { work_state: 'running' }, controls: {},
        next_page_token: 'older', messages: [request('primary', '主输入：执行中的工作'), request('appended', '追加输入：送达成功，工作仍在执行'),
          request('missing', '只有旧送达状态，执行归属待确认'), request('queued', '已接纳、尚未开始的工作'),
          { ...request('family', 'Family 成员仍在执行'), relay: { state: 'completed', work: fact('running', 'member-execution', 10, 'appended') } },
          { ...request('compat', '正式 input.work 优先于旧送达回执'), relay: { state: 'completed' } },
          { ...request('revoked', '明确未知不借旧成员事实恢复'), relay: { state: 'completed', work: fact('running', 'old-member') } },
          request('done', '已经完成的独立执行')], input_states: [
            { message_id: 'primary', state: 'running', updated_at: '2026-11-04T11:00:00Z', work: fact('running') },
            { message_id: 'appended', state: 'completed', updated_at: '2026-11-04T11:00:00Z', work: fact('running', 'execution-one', 10, 'appended') },
            { message_id: 'missing', state: 'running', updated_at: '2026-11-04T11:00:00Z' },
            { message_id: 'queued', state: 'completed', work: { ...fact('queued', 'execution-queue'), delivery: 'pending' } },
            { message_id: 'family', state: 'completed' },
            { message_id: 'compat', state: 'completed', work: fact('running', 'member-authoritative', 10, 'appended') },
            { message_id: 'revoked', state: 'completed', work: { state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' } },
            { message_id: 'done', state: 'completed', work: fact('completed', 'execution-done') } ] };
      if (queuedOnly) {
        raw.queue = { resident_id: rid, items: [
          { message_id: 'waiting', author_id: 'fixture-reader', enqueued_at: '2026-11-04T12:01:00Z',
            content_parts: [{ type: 'text', text: '已正式受理、尚未进入执行轨迹的排队消息。' }] },
          { message_id: 'queue-unknown', author_id: 'fixture-reader', enqueued_at: '2026-11-04T12:01:01Z', content_preview: '工作状态尚未确认的已受理消息' },
          { message_id: 'agent-queue', author_id: 'resident_222222222222', enqueued_at: '2026-11-04T12:01:02Z', content_preview: 'Resident 来信不产生用户节点' }
        ] };
        raw.input_states.push({ message_id: 'waiting', state: 'queued', work: { state: 'queued', kind: 'primary', delivery: 'pending',
          primary_message_id: 'waiting', updated_at: '2026-11-04T12:01:00Z' } },
          { message_id: 'queue-unknown', state: 'queued', work: { state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' } });
        raw.local_submissions = [{ ...request('unsent', '未受理的本地草稿'), local_delivery: { state: 'sending' } }];
      }
      window.reads = 0;
      PortalResidentService = { snapshot: async () => { reads++; if (window.snapshotWait) await new Promise(resolve => window.finishSnapshot = resolve); return structuredClone(raw); },
        conversationMessages: async () => { if (window.pageWait) return await new Promise(resolve => window.finishPage = resolve); return window.historyPage; },
        inputHistory: async () => ({ entries: [] }), runtimeSettings: async () => ({ settings: {} }), runtimeCatalog: async () => ({}) };
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'workRail', autoLoad: false,
        target: { resident_id: rid } });
      panel.residentProjection = { resident: { resident_id: rid }, chat_workspace: { schema: 'portal.chat_workspace.v3',
        target: { kind: 'resident', id: rid }, composer: { can_send: true } } };
      panel.protocol = PortalResidentProtocol.acquire(rid, update => { if (update.snapshot) panel.applySnapshot(update.snapshot, update); }, { load: false });
      await panel.protocol.load();
      window.sendWork = (id, work, options = {}) => {
        const prior = raw.input_states.find(input => input.message_id === id);
        const input = { ...prior, work };
        raw.input_states = raw.input_states.map(value => value.message_id === id ? input : value);
        const cursor = options.cursor ?? ++raw.cursor;
        window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_events: [{ resident_id: rid, cursor,
          event_type: 'resident.input.state.changed', payload: { input_state: input } }] } }));
      };
      window.sendRelay = work => {
        const message = { ...raw.messages.find(item => item.message_id === 'family'), relay: { state: 'completed', work } };
        raw.messages = raw.messages.map(item => item.message_id === 'family' ? message : item);
        window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_events: [{ resident_id: rid, cursor: ++raw.cursor,
          event_type: 'conversation.message.updated', payload: { message } }] } }));
      };
    }, { queuedOnly: !!process.env.RAIL_QUEUE_ONLY });
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const node = id => page.locator(`[data-round-key="${id}"]`);
    const running = id => node(id).evaluate(n => n.classList.contains('is-running'));
    const check = (name, value) => { assert.ok(value, name); checks.push(name); };
    await page.waitForSelector('.chatRoundNode'); await settle();
    if (process.env.RAIL_QUEUE_ONLY) {
      const evidence = { checks, errors, isolated: true, baseline: !!baselineProtocol };
      const visibleQueued = await node('waiting').count();
      if (baselineProtocol) {
        assert.equal(visibleQueued, 0);
        evidence.reproduced = 'formal input.work.queued exists but queue-only accepted input has no body row or marker';
      } else {
        check('accepted queue-only input has its own body row and marker', visibleQueued === 1
          && await page.locator('.messageList > [data-message-key="waiting"]').count() === 1);
        check('queue-only authoritative work.queued draws a diamond', await node('waiting').evaluate(n => n.classList.contains('is-queued')));
        const shape = await node('waiting').locator('.chatRoundCore').evaluate(n => {
          const s = getComputedStyle(n, '::after'); return { radius: s.borderRadius, transform: s.transform, border: s.borderTopWidth };
        });
        evidence.shape = shape;
        check('real styles render the hollow rotated square, not a circle', shape.radius === '0px' && shape.border === '1px' && shape.transform.startsWith('matrix(0.707'));
        check('explicit unknown is not inferred queued from delivery state', !await node('queue-unknown').evaluate(n => n.classList.contains('is-queued')));
        check('local draft and Resident-authored queue input have no human marker', await node('unsent').count() === 0 && await node('agent-queue').count() === 0);
        await node('waiting').focus(); await page.keyboard.press('Enter'); await settle();
        check('queued marker keyboard navigation reaches the real message row', await page.locator('.messageList > [data-message-key="waiting"]').isVisible());
        await page.evaluate(() => panel.workspace.controller.toggleRoundFollow('waiting')); await settle();
        check('Flag replaces queued diamond and retains queued accessible status', await node('waiting').locator('.chatRoundCore').evaluate(n => getComputedStyle(n, '::after').display === 'none')
          && (await node('waiting').getAttribute('aria-label')).includes('排队中'));
        await page.evaluate(() => panel.workspace.controller.toggleRoundFollow('waiting')); await settle();
        if (output) await page.screenshot({ path: path.join(output, 'queued-desktop.png') });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.evaluate(() => document.documentElement.style.fontSize = '20px'); await settle();
        await node('waiting').focus(); await page.keyboard.press('Enter'); await settle();
        if (output) await page.screenshot({ path: path.join(output, 'queued-390.png') });
        check('390/20px retains one keyboard reachable queued marker', await node('waiting').count() === 1 && await node('waiting').isVisible());
        await page.evaluate(() => {
          raw.messages.push({ message_id: 'waiting', role: 'user', author_id: 'fixture-reader',
            origin: { sender: { kind: 'user', id: 'fixture-reader' } }, content_parts: raw.queue.items[0].content_parts });
          raw.queue.items = raw.queue.items.filter(x => x.message_id !== 'waiting');
          raw.cursor++;
          raw.input_states.find(x => x.message_id === 'waiting').work = { ...fact('running', 'new-queue-execution', 35), updated_at: '2026-11-04T12:02:00Z' };
          return panel.protocol.load({ force: true });
        }); await settle();
        check('queue admission to execution keeps one stable identity and switches to running', await node('waiting').count() === 1 && await running('waiting')
          && !await node('waiting').evaluate(n => n.classList.contains('is-queued')));
        await page.evaluate(() => { raw.queue.items = []; raw.cursor++; return panel.protocol.load({ force: true }); }); await settle();
        check('removed queue-only input leaves no stale marker', await node('queue-unknown').count() === 0);
      }
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'queued-result.json'), JSON.stringify(evidence, null, 2));
      console.log('Queued admission contract: ' + (baselineProtocol ? 'baseline failure reproduced' : checks.length + ' passed'));
      await page.evaluate(() => panel.destroy()); return;
    }
    if (process.env.RAIL_BOTTOM_ONLY) {
      await page.evaluate(() => {
        panel.destroy();
        window.human = (id, text) => ({ id, message_id: id, role: 'user', content: text,
          origin: { sender: { kind: 'user', id: 'fixture-reader' } } });
        window.items = [];
        for (let i = 0; i < 8; i++) items.push(human('u' + i, '已受理用户请求 ' + i),
          { id: 'a' + i, message_id: 'a' + i, role: 'assistant', content: ('答复与解释。\n\n').repeat(6) });
        items.push({ ...human('resident-tail', 'Resident 输入不是用户轮次'), origin: { sender: { kind: 'resident', id: rid } } });
        window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'bottom', refs: false,
          foldLongMessages: false, contract: { messages: items, composer: { history_scope: 'bottom', can_send: true } } });
        window.view = workspace.els.messages;
      }); await settle(); await page.waitForTimeout(120);
      const current = () => page.locator('.chatRoundNode.is-current').evaluateAll(nodes => nodes.map(n => n.dataset.roundKey));
      const toEnd = async delta => { await page.evaluate(delta => { view.scrollTop = view.scrollHeight - view.clientHeight - delta; }, delta); await settle(); };
      await toEnd(0);
      check('bottom selects the last laid-out human request, never the resident tail', JSON.stringify(await current()) === '["u7"]');
      await toEnd(0.5);
      check('fractional bottom offset keeps one final current marker', JSON.stringify(await current()) === '["u7"]');
      await page.evaluate(() => { view.scrollTop = 0; }); await settle();
      check('leaving bottom restores the normal reading anchor', JSON.stringify(await current()) === '["u0"]');
      await page.evaluate(() => {
        // Reorder actual layout independently of the semantic message array and
        // timestamps. The last visible human must still win at the bottom.
        const list = view.querySelector('.messageList');
        list.append(view.querySelector('[data-message-key="u2"]'));
      }); await settle(); await toEnd(0);
      check('layout order, not source array order, determines bottom current', JSON.stringify(await current()) === '["u2"]');
      await page.evaluate(() => {
        items = items.map(item => item.id === 'a7' ? { ...item, content: ('最后一条很长的回复，持续展开说明。\n\n').repeat(50) } : item);
        workspace.setMessages(items);
        const list = view.querySelector('.messageList');
        for (const item of items) { const row = view.querySelector(`[data-message-key="${item.id}"]`); if (row) list.append(row); }
      }); await settle(); await page.waitForTimeout(120); await toEnd(0);
      check('long final assistant reply still belongs to the final human marker', JSON.stringify(await current()) === '["u7"]');
      await page.evaluate(() => {
        const row = view.querySelector('[data-message-key="u2"]');
        view.scrollTop += row.getBoundingClientRect().top - view.getBoundingClientRect().top;
      }); await settle();
      const readingBox = await page.evaluate(() => view.getBoundingClientRect().toJSON());
      await page.mouse.move(readingBox.x + readingBox.width / 2, readingBox.y + readingBox.height / 2);
      await page.mouse.wheel(0, -150); await page.waitForTimeout(120);
      await page.evaluate(() => {
        items = items.map(item => item.id === 'a7' ? { ...item, content: item.content + ('流式新增内容。\n\n').repeat(40) } : item);
        workspace.setMessages(items);
      }); await settle(); await page.waitForTimeout(120);
      const afterStream = await page.evaluate(() => ({ top: view.scrollTop, range: view.scrollHeight - view.clientHeight,
        current: [...document.querySelectorAll('.chatRoundNode.is-current')].map(n => n.dataset.roundKey),
        users: [...view.querySelectorAll('.messageList > [data-message-key]')].filter(n => n.dataset.messageKey.startsWith('u'))
          .map(n => ({ key: n.dataset.messageKey, y: n.getBoundingClientRect().top - view.getBoundingClientRect().top + view.scrollTop })) }));
      const expectedMiddle = afterStream.users.filter(n => n.y <= afterStream.top + 24).at(-1)?.key || afterStream.users[0].key;
      check('stream growth preserves the normal reading anchor: ' + JSON.stringify(afterStream), afterStream.current.length === 1
        && afterStream.top < afterStream.range - 1 && afterStream.current[0] === expectedMiddle);
      await toEnd(0);
      check('bottom after stream growth uses refreshed layout range', JSON.stringify(await current()) === '["u7"]');
      await page.evaluate(() => {
        const original = Element.prototype.getBoundingClientRect; window.scrollReads = 0;
        Element.prototype.getBoundingClientRect = function() { if (this.matches('.messageList > [data-message-key]')) scrollReads++; return original.call(this); };
      });
      for (const delta of [100, 0, .5, 200, 0]) await toEnd(delta);
      check('bottom and ordinary scrolling add zero message geometry reads', await page.evaluate(() => scrollReads === 0));
      if (output) await page.screenshot({ path: path.join(output, 'bottom-current.png') });
      await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => document.documentElement.style.fontSize = '20px');
      await page.waitForTimeout(150); await toEnd(.5);
      check('390px large-font bottom retains exactly the final human marker', JSON.stringify(await current()) === '["u7"]');
      if (output) await page.screenshot({ path: path.join(output, 'bottom-current-390.png') });
      assert.deepEqual(errors, []);
      if (output) fs.writeFileSync(path.join(output, 'bottom-result.json'), JSON.stringify({ checks, errors }, null, 2));
      console.log(`Round bottom contract passed: ${checks.length}`);
      await page.evaluate(() => workspace.destroy()); return;
    }
    check('native protocol -> Panel -> contract retains appended work despite delivery completed', await running('appended') && await running('primary'));
    check('missing work stays unknown despite raw running/global busy', !await running('missing') && (await node('missing').getAttribute('aria-label')).includes('状态待确认'));
    check('formal queued work draws the queued marker', await node('queued').evaluate(n => n.classList.contains('is-queued')));
    check('Family relay.work is used only when the root input lacks work', await running('family') && !await running('done'));
    check('input.work running survives old relay completed without relay.work', await running('compat'));
    check('explicit input.work unknown never falls back to old relay.work running', !await running('revoked')
      && (await node('revoked').getAttribute('aria-label')).includes('状态待确认'));
    if (output) await page.screenshot({ path: path.join(output, 'work-running.png') });
    await page.evaluate(() => sendRelay(fact('completed', 'member-execution', 20))); await settle();
    check('member work completion stops its user marker independently', !await running('family') && await running('appended'));
    await page.evaluate(() => sendRelay(fact('running', 'member-execution', 10))); await settle();
    check('late relay work cannot resurrect the member execution', !await running('family'));
    await page.evaluate(() => sendRelay({ state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' })); await settle();
    check('explicit unknown relay work does not fall back to routing/delivery', !await running('family')
      && (await node('family').getAttribute('aria-label')).includes('状态待确认'));
    await page.evaluate(() => sendRelay(fact('running', 'member-retry', 30))); await settle();
    check('a proven new member execution can run', await running('family'));
    await page.evaluate(() => sendWork('appended', fact('completed', 'execution-one', 20, 'appended'))); await settle();
    check('work-only terminal event updates without changing delivery timestamp', !await running('appended') && await running('primary')
      && await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'appended').updated_at === '2026-11-04T11:00:00Z'));
    await page.evaluate(() => sendWork('appended', fact('running', 'execution-one', 10, 'appended'))); await settle();
    check('late running event cannot revive the terminal execution', !await running('appended'));
    await page.evaluate(() => sendWork('primary', fact('completed', 'execution-one', 20))); await settle();
    await page.evaluate(() => sendWork('primary', fact('running', 'execution-two', 30))); await settle();
    check('explicit new primary execution can run while old appended work stays terminal', await running('primary') && !await running('appended'));
    await page.evaluate(() => sendWork('primary', fact('completed', 'execution-one', 20))); await settle();
    check('late old execution terminal does not overwrite new execution', await running('primary'));
    await page.evaluate(() => {
      pageWait = true;
      window.paging = panel.protocol.loadOlder();
    });
    await page.waitForFunction(() => typeof finishPage === 'function');
    await page.evaluate(() => sendWork('primary', fact('completed', 'execution-two', 40))); await settle();
    await page.evaluate(async () => {
      finishPage({ resident_id: rid, messages: [], next_page_token: 'older-again', input_states: [
        { message_id: 'primary', state: 'completed', updated_at: '2026-11-04T11:00:00Z', work: fact('running', 'execution-two', 30) } ] });
      await paging;
    }); await settle();
    check('in-flight older page cannot overwrite work event with equal delivery time', !await running('primary')
      && await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'primary').work.state === 'completed'));
    await page.evaluate(() => sendWork('appended', { state: 'unknown', kind: 'appended', delivery: 'delivered', updated_at: '' })); await settle();
    check('ordered unknown explicitly revokes an execution proof', (await node('appended').getAttribute('aria-label')).includes('状态待确认'));
    await page.evaluate(() => {
      pageWait = false; historyPage = { resident_id: rid, messages: [], next_page_token: '', input_states: [
        { message_id: 'appended', state: 'completed', updated_at: '2026-11-04T11:00:00Z', work: fact('running', 'execution-one', 10, 'appended') }] };
      return panel.protocol.loadOlder();
    }); await settle();
    check('unversioned history cannot undo current unknown proof', !await running('appended')
      && await page.evaluate(() => panel.protocol.snapshot().input_states.find(x => x.message_id === 'appended').work.state === 'unknown'));
    await page.evaluate(() => sendWork('queued', { state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' })); await settle();
    check('unknown removes previously queued shape', !await node('queued').evaluate(n => n.classList.contains('is-queued')));
    await page.evaluate(() => sendWork('primary', fact('running', 'execution-two', 59))); await settle();
    check('same execution terminal stays terminal even with a later running timestamp', !await running('primary'));
    await page.evaluate(() => sendWork('missing', fact('running', 'execution-new', 45))); await settle();
    check('new authoritative work establishes previously unknown request', await running('missing'));
    await page.evaluate(() => sendWork('done', { ...fact('running', 'execution-done', 20), provider_turn_id: 'turn-two' })); await settle();
    check('a formally proven new provider turn has a distinct execution binding', await running('done'));
    await page.evaluate(() => sendWork('done', { ...fact('completed', 'execution-done', 30), provider_turn_id: 'turn-two' })); await settle();
    await page.evaluate(() => sendWork('done', { state: 'unknown', kind: 'unknown', delivery: 'unknown', updated_at: '' })); await settle();
    await page.evaluate(() => sendWork('done', { ...fact('running', 'execution-done', 59), provider_turn_id: 'turn-two' })); await settle();
    check('terminal binding remains closed after unknown and a late same-turn running fact', !await running('done'));

    const beforeConflict = await page.evaluate(() => reads);
    await page.evaluate(() => {
      snapshotWait = true;
      const conflict = panel.protocol.snapshot();
      conflict.input_states = conflict.input_states.map(input => input.message_id === 'compat'
        ? { ...input, work: fact('completed', 'member-authoritative', 25, 'appended') } : input);
      panel.applySnapshot(conflict, { type: 'history' });
    });
    await page.waitForFunction(() => typeof finishSnapshot === 'function'); await settle();
    check('same-cursor conflicting projection remains unknown pending one formal read', !await running('compat')
      && await page.evaluate(() => reads) === beforeConflict + 1);
    await page.evaluate(() => { snapshotWait = false; finishSnapshot(); finishSnapshot = null; }); await settle();
    await page.waitForFunction(() => document.querySelector('[data-round-key="compat"]').classList.contains('is-running'));
    check('canonical snapshot resolves same-cursor ambiguity without inventing a terminal', await running('compat'));

    const beforeLegacy = await page.evaluate(() => reads);
    await page.evaluate(() => {
      snapshotWait = true;
      const input = raw.input_states.find(input => input.message_id === 'compat');
      input.work = fact('completed', 'member-authoritative', 40, 'appended');
      const legacy = { ...input }; delete legacy.work;
      window.dispatchEvent(new CustomEvent('portal:change', { detail: { resident_events: [{ resident_id: rid, cursor: ++raw.cursor,
        event_type: 'resident.input.state.changed', payload: { input_state: legacy } }] } }));
    }); await settle();
    check('legacy delivery event invalidates work instead of using the old relay or input state', !await running('compat')
      && (await node('compat').getAttribute('aria-label')).includes('状态待确认'));
    await page.waitForFunction(() => typeof finishSnapshot === 'function');
    await page.evaluate(() => { snapshotWait = false; finishSnapshot(); finishSnapshot = null; }); await settle();
    await page.waitForFunction(() => panel.protocol.snapshot().input_states.find(input => input.message_id === 'compat').work.state === 'completed');
    check('legacy event reuses one coalesced snapshot recovery for current work', !await running('compat')
      && await page.evaluate(() => reads) === beforeLegacy + 1);
    await page.evaluate(() => { const stale = structuredClone(raw); stale.cursor = 1; stale.input_states = []; panel.applySnapshot(stale, { type: 'snapshot' }); }); await settle();
    check('older snapshot cursor cannot clear current work projection', await running('missing'));
    await page.evaluate(() => { panel.workspace.controller.requestProgressDisconnected(); }); await settle();
    check('genuine disconnect still invalidates work facts', await page.locator('.chatRoundNode.is-running').count() === 0);
    await page.evaluate(() => sendWork('missing', fact('running', 'execution-new', 46))); await settle();
    check('event projection alone does not certify disconnected work', !await running('missing'));
    await page.evaluate(() => panel.protocol.load({ force: true })); await settle();
    check('verified protocol snapshot restores proven work', await running('missing') && await running('family'));
    const count = await page.evaluate(() => reads); await page.waitForTimeout(150);
    check('work consumer adds no polling reads', await page.evaluate(() => reads) === count);
    if (output) await page.screenshot({ path: path.join(output, 'work-final.png') });
    assert.deepEqual(errors, []);
    if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, errors, scope: 'isolated API; real ResidentProtocol, Panel, Chat and rail' }, null, 2));
    console.log(`Round work contract passed: ${checks.length}`);
    await page.evaluate(() => panel.destroy());
  } finally { await browser?.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
