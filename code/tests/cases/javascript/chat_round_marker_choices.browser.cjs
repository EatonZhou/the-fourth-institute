'use strict';
// Real Chat/Overlay/rendering with isolated conversation data and personal storage.
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
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_button_tooltips.js', 'portal_preview_contract.js',
  'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];
const output = process.env.SCREENSHOT_DIR;
if (output) fs.mkdirSync(output, { recursive: true });
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body;
    if (!name) body = '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
      + styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('')
      + '<style>html,body{height:100%;margin:0;background:var(--panel);color:var(--text)}#chat{height:100%;min-height:0}</style><main id="chat"></main>'
      + '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    else if (name === 'routes.js') body = routeScript;
    else if (modules.includes(name) || styles.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': !name ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(body ?? 'unexpected');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const checks = [], errors = [], geometry = [];
  const check = (name, value) => { assert.ok(value, name); checks.push(name); };
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const setup = async (page, legacy = false) => {
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.evaluate(({ legacy }) => {
        window.__PORTAL_SITE__ = { storage_namespace: 'marker-choices-test' };
        window.viewer = 'reader'; window.scope = 'resident:markers'; window.toasts = [];
        PortalIdentity = { username: () => viewer }; PortalUI.toast = text => toasts.push(text);
        window.flagKey = () => PortalState.storage.key('portal.chat.round-flags.v1.' + JSON.stringify([viewer, scope]));
        if (legacy) localStorage.setItem(flagKey(), JSON.stringify(['one']));
        const human = (id, content) => ({ id, message_id: id, role: 'user', content,
          origin: { sender: { kind: 'user', id: 'reader' } }, created_at: '2026-11-05T09:00:00Z' });
        window.items = [human('one', '对照来源与证据，记录已经确认的结论。'),
          { id: 'answer', role: 'assistant', reply_to_message_id: 'one', content: '结论与原始证据相符。\n\n'.repeat(7) },
          human('two', '正在执行的任务：请继续验证性能与交互。'),
          { id: 'answer2', role: 'assistant', reply_to_message_id: 'two', content: '检查中。\n\n'.repeat(8) },
          human('three', '已受理的排队请求：完成后再核对另一个方案。'),
          human('four', '用章鱼记录这个多方向探索的工作。')];
        window.contract = { messages: items, composer: { can_send: true, history_scope: scope, queue: { items: [] } },
          request_progress: { cursor: 1, verified: true, input_states: ['one', 'two', 'three', 'four'].map((id, index) => ({ message_id: id,
            state: 'completed', work: { state: ['completed', 'running', 'queued', 'completed'][index], kind: 'primary', delivery: 'delivered', execution_id: id } })) } };
        window.workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'markers', title: 'Chat',
          historyScope: () => scope, refs: false, pinMessages: true, foldLongMessages: false, contract });
      }, { legacy });
      await page.waitForSelector('.chatRoundNode');
    };
    const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const marker = (page, id) => page.locator(`[data-round-key="${id}"]`);
    const choices = (page, id) => page.locator(`[data-round-follow="${id}"]`);
    const open = async (page, id, touch = false) => {
      await page.keyboard.press('Escape');
      if (touch) await page.locator('.chatRoundDirectory').tap(); else await page.locator('.chatRoundDirectory').click();
      await choices(page, id).waitFor({state:'visible'});
    };
    const choose = async (page, id, kind, touch = false) => {
      await open(page, id, touch);
      const option = choices(page, id).locator(`[data-round-marker="${kind}"]`);
      if (touch) await option.tap(); else await option.click();
      await settle(page);
    };
    const shot = async (page, name) => { if (output) await page.screenshot({ path: path.join(output, name + '.png') }); };
    const page = await context.newPage(); await setup(page, true);
    check('legacy flag renders without write-on-read', await marker(page, 'one').getAttribute('data-round-marker') === 'flag'
      && await page.evaluate(() => Array.isArray(JSON.parse(localStorage.getItem(flagKey())))));
    await open(page, 'one');
    check('four choices are already visible in each preview header', await choices(page,'one').getByRole('button').count() === 4
      && await choices(page,'one').locator('[data-round-marker="flag"]').getAttribute('aria-pressed') === 'true'
      && await page.locator('.chatRoundMarkerMenu').count() === 0);
    const header = await choices(page,'one').evaluate(n=>{
      const bounds=[...n.children].map(b=>b.getBoundingClientRect());
      const meta=n.closest('.chatRoundPreviewMeta'),time=meta.querySelector('time').getBoundingClientRect();
      const text=meta.parentElement.querySelector('.chatRoundPreviewJump').getBoundingClientRect();
      return {sameRow:bounds.every(r=>r.y===bounds[0].y),rightOfTime:bounds[0].x>time.right,aboveText:bounds.every(r=>r.bottom<=text.top)};
    });
    check('icons sit side by side at the right of time above the message', header.sameRow && header.rightOfTime && header.aboveText);
    await choices(page,'one').locator('[data-round-marker="flag"]').focus();
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('Enter'); await settle(page);
    check('keyboard selects star and migrates legacy storage', await marker(page, 'one').getAttribute('data-round-marker') === 'star'
      && await page.evaluate(() => JSON.parse(localStorage.getItem(flagKey())).version === 2));
    await choose(page, 'two', 'turtle'); await choose(page, 'three', 'flag'); await choose(page, 'four', 'octopus');
    const inspect = async p => p.evaluate(() => [...document.querySelectorAll('.chatRoundNode')].map(n => {
      const core = n.querySelector('.chatRoundCore'), icon = n.querySelector('.chatRoundFlag');
      const a = core.getBoundingClientRect(), b = icon?.getBoundingClientRect();
      return { id: n.dataset.roundKey, marker: n.dataset.roundMarker, classes: n.className,
        core: getComputedStyle(core, '::after').display, halo: getComputedStyle(core, '::before').animationDuration,
        offset: b ? [b.x + b.width / 2 - a.x - a.width / 2, b.y + b.height / 2 - a.y - a.height / 2] : null };
    }));
    const icons = await inspect(page); geometry.push({ desktop: icons });
    check('all four markers replace cores at same center', icons.every(n => n.marker && n.core === 'none' && n.offset.every(v => Math.abs(v) < .1)));
    check('running and queued retain authoritative status with custom marker', icons[1].classes.includes('is-running') && icons[1].halo === '3.6s' && icons[2].classes.includes('is-queued'));
    await open(page, 'four'); await shot(page, 'desktop-light');
    await page.keyboard.press('Escape');
    check('Escape closes preview and restores directory focus', await page.locator('.chatRoundPreview').isHidden()
      && await page.evaluate(() => document.activeElement.classList.contains('chatRoundDirectory')));
    await page.evaluate(() => document.documentElement.dataset.portalTheme = 'dark');
    await open(page, 'two'); await shot(page, 'desktop-dark');
    await choices(page,'two').locator('[aria-pressed="true"]').click(); await settle(page);
    check('cancel removes chosen icon', !(await marker(page, 'two').getAttribute('class')).includes('is-followed'));
    await page.keyboard.press('Escape');
    await page.locator('[data-message-key="two"]').click({ button: 'right' });
    await page.locator('[data-portal-message-action="round-follow"]').click(); await settle(page);
    check('message menu after cancellation creates default flag', await marker(page, 'two').getAttribute('data-round-marker') === 'flag');
    await page.keyboard.press('Escape');
    await choose(page, 'two', 'star');
    await page.keyboard.press('Escape');
    const box = await marker(page, 'two').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down(); await page.waitForTimeout(600); await page.mouse.up(); await settle(page);
    check('long press cancels any custom marker', !(await marker(page, 'two').getAttribute('class')).includes('is-followed'));
    await page.mouse.down(); await page.waitForTimeout(600); await page.mouse.up(); await settle(page);
    check('long press recreates default flag', await marker(page, 'two').getAttribute('data-round-marker') === 'flag');
    const second = await context.newPage(); await setup(second);
    check('new document restores exact icon kinds', await marker(second, 'one').getAttribute('data-round-marker') === 'star'
      && await marker(second, 'four').getAttribute('data-round-marker') === 'octopus');
    await choose(second, 'one', 'turtle');
    await page.waitForFunction(() => document.querySelector('[data-round-key="one"]').dataset.roundMarker === 'turtle');
    check('real storage event synchronizes across pages', true);
    await open(page, 'one');
    await page.evaluate(() => {
      window.savedStorage = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) { if (key.includes('round-flags')) throw new Error('quota'); return savedStorage.call(this, key, value); };
    });
    await choices(page,'one').getByRole('button', { name: '章鱼', exact: true }).click(); await settle(page);
    check('failed save keeps prior icon and reports failure', await marker(page, 'one').getAttribute('data-round-marker') === 'turtle'
      && await page.evaluate(() => toasts.at(-1).includes('未保存')));
    await page.evaluate(() => Storage.prototype.setItem = savedStorage);
    await open(page, 'one');
    await page.evaluate(() => Storage.prototype.setItem = function(key, value) { if (!key.includes('round-flags')) savedStorage.call(this, key, value); });
    await choices(page,'one').getByRole('button', { name: '星星', exact: true }).click(); await settle(page);
    check('silently dropped write fails readback and preserves saved kind', await marker(page, 'one').getAttribute('data-round-marker') === 'turtle'
      && await page.evaluate(() => JSON.parse(localStorage.getItem(flagKey())).markers.find(m => m.id === 'one').icon === 'turtle'));
    await page.evaluate(() => {
      Storage.prototype.setItem = savedStorage;
      const value = JSON.parse(localStorage.getItem(flagKey())); value.markers.find(m => m.id === 'one').icon = '<script>unknown</script>';
      localStorage.setItem(flagKey(), JSON.stringify(value)); window.dispatchEvent(new StorageEvent('storage', { key: flagKey() }));
    }); await settle(page);
    check('unknown icon falls back safely to flag', await marker(page, 'one').getAttribute('data-round-marker') === 'flag');
    await page.evaluate(() => { viewer = 'another'; window.dispatchEvent(new Event('portal:identity-changed')); }); await settle(page);
    check('different user cannot see prior markers', await page.locator('.chatRoundNode.is-followed').count() === 0);
    await page.evaluate(() => { viewer = 'reader'; scope = 'resident:other'; window.dispatchEvent(new Event('portal:identity-changed')); }); await settle(page);
    check('same user different conversation is isolated', await page.locator('.chatRoundNode.is-followed').count() === 0);
    await page.evaluate(() => { scope = 'resident:markers'; window.dispatchEvent(new Event('portal:identity-changed')); workspace.setRequestProgress(contract.request_progress); }); await settle(page);
    check('returning to conversation restores marker choices', await marker(page, 'four').getAttribute('data-round-marker') === 'octopus');
    await choose(page, 'three', 'turtle'); await open(page, 'three');
    await choices(page,'three').locator('[aria-pressed="true"]').click(); await settle(page);
    check('cancel queued marker restores hollow diamond', await marker(page, 'three').evaluate(n => {
      const s = getComputedStyle(n.querySelector('.chatRoundCore'), '::after'); return s.display !== 'none' && s.borderRadius === '0px' && s.transform !== 'none';
    }));
    await second.close();
    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const mobile = await mobileContext.newPage(); await setup(mobile);
    await mobile.evaluate(() => document.documentElement.style.fontSize = '20px');
    await choose(mobile, 'one', 'flag', true); await choose(mobile, 'two', 'star', true); await choose(mobile, 'three', 'turtle', true); await choose(mobile, 'four', 'octopus', true);
    await open(mobile, 'four', true);
    const narrow = await choices(mobile,'four').evaluate(n => {
      const r = n.getBoundingClientRect(); return { x:r.x, y:r.y, right:r.right, bottom:r.bottom, width:innerWidth, height:innerHeight,
        controls: [...n.querySelectorAll('button')].map(b => ({ width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height })) };
    }); geometry.push({ narrow });
    check('390 font20 selector fits viewport with 44px touch targets', narrow.x >= 0 && narrow.right <= narrow.width && narrow.bottom <= narrow.height
      && narrow.controls.every(b => b.width >= 44 && b.height >= 44));
    check('tap selects exact custom icon', await marker(mobile, 'four').getAttribute('data-round-marker') === 'octopus');
    await shot(mobile, '390-light');
    await mobile.evaluate(() => document.documentElement.dataset.portalTheme = 'dark'); await shot(mobile, '390-dark');
    await choices(mobile,'four').locator('[data-round-marker="octopus"]').focus();
    await mobile.keyboard.press('Home'); await mobile.keyboard.press('ArrowRight'); await mobile.keyboard.press('Enter'); await settle(mobile);
    check('390 keyboard selection shares tap behavior', await marker(mobile, 'four').getAttribute('data-round-marker') === 'star');
    await open(mobile, 'four', true); await mobile.evaluate(() => workspace.setRoundRailPresented(false));
    check('hidden rail releases inline choices', await mobile.locator('.chatRoundMarkerChoices').count() === 0);
    await page.evaluate(() => workspace.destroy());
    check('destroy releases choices and marker nodes', await page.locator('.chatRoundMarkerChoices,.chatRoundNode').count() === 0);
    check('no script errors', errors.length === 0);
    if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, errors, geometry, boundary:'native Chat with isolated data; no business API writes' }, null, 2));
    console.log('Marker choices passed: ' + checks.length);
    await mobileContext.close(); await context.close();
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
