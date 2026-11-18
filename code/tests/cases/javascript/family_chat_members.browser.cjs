'use strict';
// Real Resident Chat and Family popover; isolated preferences, no live writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const playwrightPath = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const browsers = require(fs.existsSync(playwrightPath) ? playwrightPath : 'playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  if (process.env.FAMILY_CHAT_SCREENSHOTS) fs.mkdirSync(process.env.FAMILY_CHAT_SCREENSHOTS, {recursive: true});
  const browser = await browsers[process.env.PORTAL_TEST_BROWSER || 'chromium'].launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'family.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({
        body: fs.readFileSync(path.join(root, url.pathname.slice(8))),
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
      });
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><body><section id="chat" style="height:90vh;min-width:0"></section></body></html>'});
    });
    await page.goto('http://family.fixture.invalid');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'portal_design_system', 'portal_themes']) {
      await page.addStyleTag({url: `/static/${name}.css`});
    }
    await page.addScriptTag({content: input.routeScript});
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js', 'portal_button_tooltips.js',
      'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({url: `/static/${name}`});
    }
    await page.evaluate(() => {
      window.PortalIdentity = {username: () => 'viewer'};
      const pins = new Map();
      window.pinListeners = new Set();
      window.pinWrites = [];
      window.PortalConversationStore = {
        entry: key => pins.get(key),
        subscribe: listener => { pinListeners.add(listener); return () => pinListeners.delete(listener); },
        loadPin: async () => true,
        setPinned: async (key, pinned) => {
          pinWrites.push(key); pins.set(key, {pinned});
          pinListeners.forEach(listener => listener());
          return {item: {pinned}};
        },
      };
      window.calls = []; window.toasts = []; window.failNext = false; window.denyRead = false;
      window.renderToast = PortalUI.toast;
      PortalUI.toast = message => toasts.push(message);
      let policy = {muted_member_ids: [], solo_member_id: ''};
      const members = [0, 1].map(i => ({resident_id: `member_${i}`, title: `成员 ${i} · 架构与实现`, role: 'member'}));
      const family = {family_id: 'family_fixture', title: 'Family', lineage_id: 'fixture', members,
        nodes: [{resident_id: 'router', role: 'router', title: 'Router'}, ...members]};
      window.facadeDetail = () => ({resident_id: 'facade', resident: {resident_id: 'facade', family: {role: 'facade'}},
        family_facade: {...family, chat_preferences: {...policy}}});
      window.memberDetail = (id = 'member_0', role = 'member') => ({resident: {resident_id: id, lineage_id: 'fixture', title: '成员 · 架构与实现', family: {family_id: 'family_fixture', role}}});
      window.watchers = new Set();
      window.PortalResidentProjection = {
        subscribe: (id, listener) => { assertFacade(id); watchers.add(listener); queueMicrotask(() => listener({detail: facadeDetail()})); return () => watchers.delete(listener); },
        invalidate: id => { assertFacade(id); watchers.forEach(listener => listener({detail: facadeDetail()})); },
      };
      const assertFacade = id => { if (id !== 'facade') throw new Error('Wrong preference target: ' + id); };
      window.PortalInstructionClient = {execute: async (name, args) => {
        calls.push([name, args]);
        if (name === 'GetLineage') return {lineage: {nodes: [{resident_id: 'facade', title: 'Dolty'}], function_blocks: [{id: 'family_fixture', family: {facade: 'facade', members: members.map(m => m.resident_id)}}]}};
        assertFacade(args.resident_id);
        if (name === 'GetFamilyChatPreferences' && denyRead) throw new Error('Not visible');
        if (name === 'UpdateFamilyChatPreferences') {
          if (failNext) { failNext = false; throw new Error('Save failed'); }
          if (args.control === 'mute') {
            const key = 'muted_member_ids';
            policy[key] = policy[key].filter(id => id !== args.member_id);
            if (args.enabled) policy[key].push(args.member_id);
          } else policy.solo_member_id = args.enabled ? args.member_id : '';
        }
        return {...policy};
      }};
      window.PortalAPI = {get: async () => ({accounts: []})};
      const intake = {};
      window.memberStates = {router: {work_state: 'idle'}, member_0: {work_state: 'running', queued_count: 2},
        member_1: {work_state: 'idle', family_activity: {work_state: 'running'}}};
      window.memberListeners = new Map();
      window.PortalResidentService = {projection: async () => facadeDetail(), setIntake: async args => {
        if (window.failIntake) { window.failIntake = false; throw new Error('Intake failed'); }
        intake[args.resident_id] = args.open ? 'open' : 'closed';
      }};
      window.PortalResidentRuntimeController = {bind: id => ({
        state: () => ({}), load: async () => ({detail: {resident: {backend: 'codex', ...(id === 'member_1' ? {family: {role: 'facade'}} : {})}, allows: {OpenIntake: true}}, settings: {}}), close() {}, destroy() {},
      })};
      window.PortalResidentProtocol = {...PortalResidentProtocol,
        loadSnapshot: async (id, opts) => {
          calls.push(['snapshot', id, opts]); assertFacade(id);
          if (window.slowRefresh) {
            panel.updateFamilyStatus(window.staleFamily);
            await new Promise(resolve => { window.finishRefresh = resolve; });
            panel.updateFamilyStatus(facadeDetail());
          }
        },
        acquire: (id, listener) => {
          memberListeners.set(id, listener);
          return {load: async () => ({state: {...memberStates[id], intake_state: intake[id] || 'open'}, messages: []}),
            destroy() { memberListeners.delete(id); }};
        },
      };
      window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('chat'), id: 'chat', title: 'Chat Workspace · 架构与实现', autoLoad: false, target: {resident_id: 'member_0'}});
      panel.updateFamilyStatus(memberDetail());
    });
    const popover = page.locator('.familyStatusPanel');
    const button = (id, mode, parent = popover) => parent.locator(`[data-member="${id}"][data-family-mix="${mode}"]`);
    const pressed = async (id, mode, active, parent = popover) => {
      await button(id, mode, parent).locator(`xpath=self::*[@aria-pressed='${active}'][not(@disabled)]`).waitFor();
    };
    const back = page.locator('.familyChatReturn');
    await back.waitFor({state: 'visible'});
    assert.equal(await back.textContent(), '←Dolty');
    assert.equal(await page.locator('.chatTitleLine [data-family-mix]').count(), 0, 'member header has navigation only');
    const destination = new URL(await back.getAttribute('href'), page.url());
    assert.equal(destination.searchParams.get('resident'), 'facade');
    assert.equal(destination.searchParams.get('open_chat'), '1');
    await page.evaluate(() => { window.originalSetResident = panel.setResident; panel.setResident = async target => { window.returnTarget = target; }; });
    await back.click();
    assert.equal(await page.evaluate(() => returnTarget.resident_id), 'facade');
    await page.evaluate(() => { panel.setResident = originalSetResident; });
    await page.evaluate(async () => {
      const anchor = document.createElement('button'); anchor.id = 'family-anchor'; document.body.append(anchor);
      window.menu = await PortalChatWorkspace.openFamilyStatus('facade', {anchor});
    });
    await pressed('member_0', 'mute', false);
    await page.evaluate(() => { failNext = true; });
    await button('member_0', 'mute').click();
    await page.waitForFunction(() => toasts.includes('Save failed'));
    await pressed('member_0', 'mute', false);
    await button('member_0', 'mute').click(); await pressed('member_0', 'mute', true);
    await button('member_0', 'solo').click(); await pressed('member_0', 'solo', true);
    await button('member_0', 'solo').click(); await pressed('member_0', 'solo', false);
    await page.evaluate(() => menu.close());
    await page.evaluate(() => { panel.updateFamilyStatus(memberDetail()); panel.updateFamilyStatus(facadeDetail()); });
    await page.waitForTimeout(30);
    assert.equal(await back.count(), 0, 'no stale return link on top-level facade');
    await page.evaluate(() => {
      const nested = memberDetail('member_0', 'facade'); nested.resident.family.family_id = 'nested_family';
      panel.updateFamilyStatus(nested);
    });
    await back.waitFor({state: 'visible'});
    assert.match(await back.getAttribute('href'), /resident=facade/);
    await page.evaluate(() => panel.updateFamilyStatus(memberDetail()));
    await back.waitFor({state: 'visible'});

    for (const width of [1200, 390]) {
      await page.setViewportSize({width, height: 900});
      const metrics = await back.evaluate(node => {
        const rect = node.getBoundingClientRect();
        return {width: rect.width, height: rect.height, right: rect.right, viewport: innerWidth};
      });
      assert.ok(metrics.height <= 32 && metrics.right <= metrics.viewport, JSON.stringify(metrics));
      const heading = await page.locator('.chatTitleLine').evaluate(node => {
        const a = node.querySelector('.familyChatReturn').getBoundingClientRect();
        const b = node.querySelector('.paneTitle').getBoundingClientRect();
        return {aTop: a.top, aBottom: a.bottom, bTop: b.top, bBottom: b.bottom, width: node.scrollWidth, available: node.clientWidth};
      });
      assert.ok(heading.aTop < heading.bBottom && heading.bTop < heading.aBottom, JSON.stringify(heading));
      assert.ok(heading.width <= heading.available + 1, JSON.stringify(heading));
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `member-chat-${width}.png`)});
      console.log(JSON.stringify({width, ...metrics}));
    }
    await page.evaluate(() => panel.updateFamilyStatus(facadeDetail()));
    await page.evaluate(async () => { menu = await PortalChatWorkspace.openFamilyStatus('facade', {anchor: document.getElementById('family-anchor')}); });
    await button('member_0', 'solo', popover).click(); await pressed('member_0', 'solo', true, popover);
    const proxy = page.locator('[data-family-solo="header"]');
    await proxy.waitFor({state: 'visible'});
    assert.match(await proxy.innerText(), /→ 成员 0.*Solo/);
    assert.match(await proxy.getAttribute('title'), /Family → 成员 0/);
    assert.equal(await page.locator('[data-family-solo="composer"]').count(), 0, 'Solo target and exit stay in the header');
    // Remount the facade UI: policy is read from the server, not local mode state.
    await page.evaluate(() => { panel.updateFamilyStatus(memberDetail()); panel.updateFamilyStatus(facadeDetail()); });
    await proxy.waitFor({state: 'visible'});
    await proxy.locator('button').click();
    await proxy.waitFor({state: 'hidden'});
    await page.evaluate(async () => { menu = await PortalChatWorkspace.openFamilyStatus('facade', {anchor: document.getElementById('family-anchor')}); });
    await pressed('member_0', 'mute', true, popover);
    await page.evaluate(() => menu.close());
    await page.evaluate(() => panel.workspace.setMessages([
      {id: 'reply_0', role: 'agent', content: '这是成员的回复。',
        family_reply: {resident_id: 'member_0', title: '架构与实现', message_id: 'original_reply', request_message_id: 'request_0'}},
      {id: 'reply_nested', role: 'agent', content: '嵌套成员回复。',
        family_reply: {resident_id: 'nested', title: '内部成员', via: [{resident_id: 'member_1'}], request_message_id: 'request_1'}},
      {id: 'reply_left', role: 'agent', content: '已离开的成员。',
        family_reply: {resident_id: 'former', title: '已离开', request_message_id: 'request_2'}},
    ]));
    const footer = page.locator('[data-family-reply-member="member_0"]');
    await pressed('member_0', 'mute', true, footer);
    // Hold the refresh while stale conversation data arrives. The pending
    // controls retain their nodes, full opacity and the saved color each frame.
    await page.evaluate(() => {
      window.slowRefresh = true; window.staleFamily = facadeDetail();
      window.trackedButton = document.querySelector('[data-family-reply-member="member_0"] [data-family-mix="solo"]');
      window.mixFrames = [];
      const sample = () => {
        const current = document.querySelector('[data-family-reply-member="member_0"] [data-family-mix="solo"]');
        mixFrames.push({same: current === trackedButton, pressed: current.getAttribute('aria-pressed'),
          opacity: getComputedStyle(current).opacity, color: getComputedStyle(current).backgroundColor});
        if (slowRefresh) requestAnimationFrame(sample);
      };
      trackedButton.click(); requestAnimationFrame(sample);
    });
    await page.waitForFunction(() => window.finishRefresh && mixFrames.length >= 4);
    const frames = await page.evaluate(() => mixFrames);
    assert.ok(frames.every(frame => frame.same && frame.pressed === 'true' && frame.opacity === '1'), JSON.stringify(frames));
    assert.equal(new Set(frames.map(frame => frame.color)).size, 1, 'saved color never flashes back during refresh');
    await page.evaluate(() => { slowRefresh = false; finishRefresh(); });
    await pressed('member_0', 'solo', true, footer);
    await button('member_0', 'solo', footer).click(); await pressed('member_0', 'solo', false, footer);
    assert.equal(await footer.locator('[data-family-reply-source] + [data-family-reply-controls]').count(), 1, 'M/S immediately follow attribution');
    assert.equal(await page.locator('[data-family-reply-member="former"] button').count(), 0);
    assert.equal(await page.locator('[data-family-reply-member="nested"] [data-member="member_1"]').count(), 2, 'nested replies control M/S on their direct Family member');
    await button('member_0', 'mute', footer).click(); await pressed('member_0', 'mute', false, footer);
    await page.evaluate(() => { failNext = true; });
    await button('member_0', 'solo', footer).click(); await pressed('member_0', 'solo', false, footer);
    await button('member_0', 'solo', footer).click(); await pressed('member_0', 'solo', true, footer);
    assert.equal(await button('member_0', 'solo', footer).evaluate(node => getComputedStyle(node).cursor), 'default');
    await page.evaluate(async () => { menu = await PortalChatWorkspace.openFamilyStatus('facade', {anchor: document.getElementById('family-anchor')}); });
    await pressed('member_0', 'solo', true, popover);
    assert.deepEqual(await popover.locator('th').allTextContents(), ['节点', 'backend', '周剩余', '工作状态', '静音 / 对话']);
    const work = id => popover.locator(`[data-family-status-row="${id}"] .familyStatusWork`);
    assert.equal(await work('router').innerText(), '空闲');
    assert.equal(await work('member_0').innerText(), '工作中');
    assert.equal(await work('member_1').innerText(), '工作中', 'nested facade shows its Family activity');
    assert.equal(await popover.locator('[data-family-status-row="member_0"] .familyStatusQueue').innerText(), '排队 2');
    assert.equal(await work('member_0').isVisible(), true, 'no hover or expansion is needed');
    await page.evaluate(() => memberListeners.get('member_0')({snapshot: {state: {work_state: 'idle', intake_state: 'closed'}, messages: []}}));
    assert.equal(await work('member_0').innerText(), '空闲');
    await page.evaluate(() => memberListeners.get('member_0')({snapshot: {state: {...memberStates.member_0, intake_state: 'open'}, messages: []}}));
    assert.equal(await work('member_0').innerText(), '工作中');
    assert.equal(await popover.locator('[data-family-status-row="member_0"] td:last-child [data-family-mix]').count(), 2);
    const intakeSwitch = popover.locator('[data-family-status-intake="member_0"]');
    await intakeSwitch.click();
    await page.waitForFunction(() => document.querySelector('[data-family-status-intake="member_0"]').getAttribute('aria-checked') === 'false');
    assert.equal(await intakeSwitch.innerText(), 'closed');
    assert.equal(await work('member_0').innerText(), '工作中', 'closing intake leaves the running work visible');
    await page.evaluate(() => { failIntake = true; });
    await intakeSwitch.click();
    await page.waitForFunction(() => toasts.includes('Intake failed'));
    assert.equal(await intakeSwitch.isEnabled(), true);
    await intakeSwitch.click();
    await page.waitForFunction(() => document.querySelector('[data-family-status-intake="member_0"]').getAttribute('aria-checked') === 'true');
    if (process.env.FAMILY_CHAT_SCREENSHOTS) {
      fs.mkdirSync(process.env.FAMILY_CHAT_SCREENSHOTS, {recursive: true});
      for (const width of [1200, 390]) {
        await page.setViewportSize({width, height: 900});
        await work('member_0').scrollIntoViewIfNeeded();
        await popover.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `member-menu-${width}.png`)});
      }
      await page.setViewportSize({width: 1200, height: 900});
    }
    await button('member_0', 'solo', popover).click();
    await page.evaluate(() => { panel.updateFamilyStatus(facadeDetail()); menu.close(); });
    await pressed('member_0', 'solo', false, footer);
    for (const width of [1200, 390]) {
      await page.setViewportSize({width, height: 900});
      const metrics = await footer.locator('.familyMemberMix').evaluate(node => {
        const rect = node.getBoundingClientRect();
        return {width: rect.width, height: rect.height, right: rect.right, viewport: innerWidth};
      });
      assert.ok(metrics.width <= 80 && metrics.height <= 24 && metrics.right <= width, JSON.stringify(metrics));
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `reply-controls-${width}.png`)});
    }
    if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, 'facade-no-controls.png')});
    assert.equal(new URL(page.url()).pathname, '/', 'M/S never navigates');
    assert.deepEqual(errors, []);
    // The header follows aggregate Family activity, independently of the facade.
    await page.evaluate(() => {
      menu.destroy(); panel.destroy();
      panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('chat'), id: 'familyHeader',
        title: 'Dolty', autoLoad: false, target: {resident_id: 'facade'}});
      window.renderFamilyHeader = (activity, facadeState = 'idle') => {
        const detail = facadeDetail();
        const projected = PortalChatWorkspace.projectResidentConversation({chat_workspace: {
          title: 'Dolty', target: {kind: 'resident', id: 'facade'}, messages: [],
          header: {subtitle_parts: [{identity: 'family', text: 'Family'},
            {identity: 'resident', text: 'resident_9ced3511fde1', copy_value: 'resident_9ced3511fde1'}]},
          composer: {can_send: true},
        }}, {...detail, messages: [], controls: {can_send: true},
          state: {work_state: facadeState, family_activity: {work_state: activity, running_count: activity === 'running' ? 2 : 0}}});
        panel.workspace.applyContract(projected.chat_workspace);
        panel.updateFamilyStatus(detail);
      };
      renderFamilyHeader('running');
      window.headerBadge = document.querySelector('[data-family-status-open]');
    });
    const badge = page.locator('[data-family-status-open]');
    const animation = () => badge.evaluate(node => getComputedStyle(node, '::after').animationName);
    assert.equal(await badge.innerText(), 'Family');
    assert.equal(await animation(), 'portalFamilyStatusPulse', 'members run while the facade itself stays idle');
    assert.equal(await badge.evaluate(node => node.previousElementSibling.classList.contains('paneTitle')), true);
    assert.equal(await page.locator('.chatSubline [data-workspace-identity="family"]').count(), 0);
    assert.equal((await page.locator('[data-workspace-status]').innerText()).trim(), 'resident_9ced3511fde1', 'no leftover separator or Family text');
    await badge.click();
    await popover.waitFor({state: 'visible'});
    assert.deepEqual(await popover.locator('th').allTextContents(), ['节点', 'backend', '周剩余', '工作状态', '静音 / 对话']);
    await pressed('member_0', 'solo', false, popover);
    await button('member_0', 'solo', popover).click();
    await proxy.waitFor({state: 'visible'});
    await page.keyboard.press('Escape');
    await page.evaluate(() => {
      const detail = facadeDetail();
      detail.family_facade.members = detail.family_facade.members.map(member => ({
        ...member, title: 'Family 职责模块化与扩展 · 一个很长的成员名称',
      }));
      panel.updateFamilyStatus(detail);
      panel.workspace.setTitle('Family 实现');
    });
    for (const width of [1200, 640, 390, 280]) {
      await page.setViewportSize({width, height: 600});
      await badge.evaluate(node => node.blur());
      const box = await proxy.evaluate(node => {
        const frame = node.closest('.portalWorkspaceHead').getBoundingClientRect();
        const label = node.firstElementChild.getBoundingClientRect();
        const exit = node.querySelector('button').getBoundingClientRect();
        return {height: node.getBoundingClientRect().height, labelTop: label.top, labelBottom: label.bottom,
          exitTop: exit.top, exitBottom: exit.bottom, exitLeft: exit.left, exitRight: exit.right,
          left: frame.left, right: frame.right, fits: node.scrollWidth <= node.clientWidth + 1,
          titleWidth: node.parentElement.querySelector('.paneTitle').getBoundingClientRect().width};
      });
      assert.ok(box.height <= 26 && box.fits, JSON.stringify({width, box}));
      assert.ok(box.labelTop < box.exitBottom && box.exitTop < box.labelBottom, 'Solo stays on one line');
      assert.ok(box.exitLeft >= box.left && box.exitRight <= box.right, 'exit remains fully accessible');
      assert.ok(box.titleWidth >= 40, 'reserve space for the Family name');
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.locator('.portalWorkspaceHead').screenshot({
        path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `solo-header-${width}.png`)});
    }
    await proxy.locator('button').click();
    await proxy.waitFor({state: 'hidden'});
    await badge.click();
    await popover.waitFor({state: 'visible'});
    for (const activity of ['idle', 'queued', 'running']) {
      await page.evaluate(activity => renderFamilyHeader(activity, 'running'), activity);
      assert.equal(await badge.innerText(), 'Family');
      assert.equal(await animation(), activity === 'running' ? 'portalFamilyStatusPulse' : 'none');
      assert.equal(await badge.evaluate(node => node === window.headerBadge), true, 'live updates retain the original popover anchor');
      assert.equal(await popover.isVisible(), true, 'live header updates keep the menu open');
    }
    await page.keyboard.press('Escape');
    await popover.waitFor({state: 'hidden'});
    await page.evaluate(() => document.documentElement.dataset.portalMotion = 'reduced');
    assert.equal(await animation(), 'none', 'respects the existing reduced motion preference');
    await page.evaluate(() => document.documentElement.dataset.portalMotion = 'full');
    for (const [width, theme] of [[640, 'light'], [390, 'light'], [640, 'dark']]) {
      await page.setViewportSize({width, height: 600});
      await page.evaluate(theme => document.documentElement.dataset.portalTheme = theme, theme);
      await page.mouse.move(0, 0);
      await badge.evaluate(node => node.blur());
      const metrics = await badge.evaluate(node => {
        const badge = node.getBoundingClientRect(), name = node.previousElementSibling.getBoundingClientRect();
        return {badgeTop: badge.top, badgeBottom: badge.bottom, nameTop: name.top, nameBottom: name.bottom,
          badgeLeft: badge.left, nameRight: name.right, right: badge.right, viewport: innerWidth};
      });
      assert.ok(metrics.badgeTop < metrics.nameBottom && metrics.badgeBottom > metrics.nameTop, 'Family stays on the name row');
      assert.ok(metrics.badgeLeft >= metrics.nameRight && metrics.right <= metrics.viewport, JSON.stringify(metrics));
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.locator('.portalWorkspaceHead').screenshot({
        path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `family-header-${theme}-${width}.png`)});
    }
    // Long availability reasons and account migrations must not change header height, including Solo.
    await badge.click();
    await popover.waitFor({state: 'visible'});
    await button('member_0', 'solo', popover).click();
    await proxy.waitFor({state: 'visible'});
    await page.keyboard.press('Escape');
    await page.evaluate(() => {
      PortalUI.toast = (message, ...args) => { toasts.push(message); return renderToast(message, ...args); };
      window.longReason = 'Steward：账号额度已用尽，发送的消息将排队等待额度恢复或人工放行。'.repeat(4);
      window.showHeaderNotice = () => panel.workspace.setSubtitle('', [
        {identity: 'family', text: 'Family'},
        {identity: 'resident', text: 'resident_9ced3511fde1'},
        {identity: 'family_availability', text: longReason},
      ]);
      window.restartOpens = 0;
      window.headerProgress = PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: PortalUIPrimitives.escapeHtml})
        .createSessionRestartProgress({compact: true, headerIcon: true, owner: 'viewer', onOpen: () => restartOpens++});
      document.querySelector('.chatTitleLine').append(headerProgress.element);
      window.updateHeaderProgress = status => headerProgress.update({
        config: {resident_id: 'facade'},
        restart: {restart_id: 'header-layout-regression', status, phase: 'waiting_capacity', operation: 'switch_account',
          source: {account_id: 'a_very_long_source_account_name'}, target: {account_id: 'a_very_long_target_account_name'},
          previous_session_retained: true, error: status === 'failed' ? '切换失败：'.repeat(60) : ''},
      });
    });
    for (const width of [1200, 640, 390, 280]) {
      await page.setViewportSize({width, height: 600});
      await page.evaluate(() => { renderFamilyHeader('idle'); updateHeaderProgress('completed'); });
      const baseline = await page.locator('.portalWorkspaceHead').evaluate(node => node.getBoundingClientRect().height);
      await page.evaluate(() => { showHeaderNotice(); updateHeaderProgress('accepted'); });
      const notice = page.locator('[data-workspace-notice]');
      assert.equal(await notice.innerText(), '!');
      assert.match(await notice.getAttribute('data-portal-tooltip'), /额度已用尽/);
      await page.waitForFunction(() => !document.querySelector('[data-workspace-notice]').hasAttribute('title'));
      await notice.focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => toasts.at(-1) === longReason), true, 'keyboard/touch can read the full reason');
      const toast = page.locator('[data-portal-toast-key="chat-header-notice"]');
      assert.equal(await toast.locator('.portalToastMessage').textContent(), await page.evaluate(() => longReason));
      assert.equal(await toast.isVisible(), true, 'the real toast displays its content after tooltip enhancement');
      await toast.locator('.portalToastClose').click();
      const measure = () => page.locator('.portalWorkspaceHead').evaluate(node => ({
        height: node.getBoundingClientRect().height, width: node.clientWidth, scroll: node.scrollWidth,
        children: [...node.querySelector('.chatTitleLine').children].map(child => ({
          name: child.className, width: child.getBoundingClientRect().width,
          min: getComputedStyle(child).minWidth, display: getComputedStyle(child).display,
          scroll: child.scrollWidth,
        })),
      }));
      const active = await measure();
      assert.ok(active.height <= baseline + 1 && active.scroll <= active.width + 1, JSON.stringify({width, baseline, active}));
      await page.locator('.is-header-icon [data-session-restart-progress-open]').last().click();
      assert.equal(await page.evaluate(() => restartOpens), [1200, 640, 390, 280].indexOf(width) + 1);
      await page.evaluate(() => updateHeaderProgress('failed'));
      const failed = await measure();
      assert.ok(failed.height <= baseline + 1 && failed.scroll <= failed.width + 1, JSON.stringify({width, baseline, failed}));
      if (process.env.FAMILY_CHAT_SCREENSHOTS) {
        await page.mouse.move(0, 0);
        await page.locator('.portalWorkspaceHead').screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `header-notices-${width}.png`)});
      }
    }
    await page.evaluate(() => {
      window.noticeBeforeUpdate = document.querySelector('[data-workspace-notice]');
      window.updatedNotice = '连接已中断，正在重试。\n错误详情：<network unavailable>';
      panel.workspace.setStatus(updatedNotice);
    });
    await page.waitForFunction(() => !noticeBeforeUpdate.hasAttribute('title'));
    const updatedNotice = page.locator('[data-workspace-notice]');
    assert.equal(await updatedNotice.evaluate(node => node === noticeBeforeUpdate), true, 'updates reuse the notice button');
    await updatedNotice.click();
    await updatedNotice.click();
    const updatedToast = page.locator('[data-portal-toast-key="chat-header-notice"]');
    assert.equal(await updatedToast.count(), 1, 'repeated clicks reuse the same toast');
    assert.equal(await updatedToast.locator('.portalToastMessage').textContent(),
      await page.evaluate(() => updatedNotice), 'click uses the latest full text and preserves newlines');
    assert.equal(await updatedToast.locator('.portalToastMessage *').count(), 0, 'notice details stay plain text');
    await updatedToast.locator('.portalToastClose').click();
    await page.locator('.is-header-icon [data-session-restart-dismiss]').last().click();
    assert.equal(await page.evaluate(() => headerProgress.element.hidden), true);
    await proxy.locator('button').click();
    await page.evaluate(() => { headerProgress.destroy(); renderFamilyHeader('idle'); });
    assert.equal(await page.locator('[data-workspace-notice]').count(), 0, 'recovery clears the warning');
    await page.evaluate(() => panel.workspace.setStatus('连接已中断，正在重试。'.repeat(30)));
    assert.equal(await page.locator('[data-workspace-notice]').innerText(), '!');
    await page.evaluate(() => panel.workspace.setSubtitle('', [{identity: 'resident', text: 'resident_other'}]));
    assert.equal(await page.locator('[data-workspace-notice]').count(), 0, 'switching identity clears transient status');
    assert.equal(await badge.count(), 0, 'ordinary Residents retain their original header');
    await page.evaluate(() => {
      panel.workspace.setTarget({resident_id: 'resident_other'});
      panel.workspace.setSubtitle('', [
        {identity: 'account', text: '账号 codex_long_account', copy_value: 'codex_long_account'},
        {identity: 'resident', text: 'resident_other', copy_value: 'resident_other'},
      ]);
    });
    const pin = page.locator('[data-portal-icon="pin"]');
    await pin.click();
    assert.equal(await pin.getAttribute('aria-pressed'), 'true');
    if (process.env.FAMILY_CHAT_SCREENSHOTS) {
      await page.mouse.move(0, 0);
      await pin.evaluate(node => node.blur());
      await page.locator('.portalWorkspaceHead').screenshot({
        path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, 'pinned-header.png')});
    }
    assert.equal(await page.evaluate(() => pinWrites.at(-1)), 'resident:resident_other');
    await page.evaluate(() => PortalConversationStore.setPinned('resident:resident_other', false));
    assert.equal(await pin.getAttribute('aria-pressed'), 'false', 'Mailbox changes update the same shared state');
    assert.equal(await page.locator('[aria-label="Chat Workspace 扩展"]').count(), 0);
    for (const width of [1100, 390, 280]) {
      await page.setViewportSize({width, height: 600});
      const bounds = await page.locator('.chatTitleLine').evaluate(node => {
        const title = node.querySelector('.paneTitle').getBoundingClientRect();
        const sub = node.querySelector('.chatSubline').getBoundingClientRect();
        return {title: {top: title.top, bottom: title.bottom}, sub: {top: sub.top, bottom: sub.bottom},
          overflow: node.scrollWidth > node.clientWidth + 1};
      });
      assert.equal(bounds.overflow, false, JSON.stringify({width, bounds}));
      assert.ok(bounds.title.top < bounds.sub.bottom && bounds.sub.top < bounds.title.bottom);
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.locator('.portalWorkspaceHead').screenshot({
        path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `resident-header-${width}.png`)});
    }
    await page.evaluate(() => {
      const container = document.createElement('section');
      container.dataset.residentId = 'resident_other';
      panel.detail = {watchboard_capabilities: {installed: false, can_install: true}};
      panel.options.residentFeatures = {watchboard: true};
      panel.options.onWatchboardAction = (action, id) => { window.boardAction = [action, id]; };
      panel.renderExtensions(container);
      container.querySelector('[data-watchboard-action]').click();
    });
    assert.deepEqual(await page.evaluate(() => boardAction), ['add', 'resident_other']);
    assert.deepEqual(errors, []);
    const updates = await page.evaluate(() => calls.filter(call => call[0] === 'UpdateFamilyChatPreferences'));
    assert.ok(updates.every(([, args]) => args.resident_id === 'facade' && !args.actor && !args.username));
    await page.evaluate(() => { menu.destroy(); panel.destroy(); });
    assert.equal(await page.evaluate(() => pinListeners.size), 0, 'destroy releases pin subscriptions');
    console.log('PASS: member Chat only, parent Family preferences, Menu sync, failure/retry, permissions, cleanup, narrow layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
