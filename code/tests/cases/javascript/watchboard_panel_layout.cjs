'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {findChatWorkspaceFunction} = require('./chat_workspace_modules.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_TEST_CHROMIUM ? {executablePath: process.env.PORTAL_TEST_CHROMIUM} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => route.fulfill({contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><section id="chat" class="portalChatWorkspaceRoot" style="width:600px;height:500px"></section></body></html>'}));
    await page.goto('http://watchboard.fixture.invalid');
    for (const file of ['portal_components.css', 'lineage_workbench.css', 'portal_design_system.css', 'portal_themes.css', 'watchboard_applications/wireguard.css']) await page.addStyleTag({content: fs.readFileSync(path.join(root, file), 'utf8')});
    for (const file of ['portal_text.js', 'portal_state.js', 'portal_translations.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js', 'portal_refresh_coordinator.js', 'watchboard_applications/registry.js', 'watchboard_applications/wireguard.js', 'lineage_workbench_watchboard.js']) await page.addScriptTag({content: fs.readFileSync(path.join(root, file), 'utf8')});
    await page.evaluate(shell => {
      const escapeHtml = PortalUIPrimitives.escapeHtml, iconSvg = PortalUIPrimitives.iconHtml;
      const workspaceShellHtml = eval('(' + shell + ')');
      document.getElementById('chat').innerHTML = workspaceShellHtml({title: '添加设备到 WireGuard', bodyId: 'chatBody', messageListId: 'messages', composerId: 'composer',
        bodySuffixHtml: '<div id="splitter" class="watchboardSplitter"></div><aside id="board" class="watchboard"></aside>'});
      document.getElementById('messages').innerHTML = Array.from({length: 24}, (_, i) => '<p>聊天记录 ' + i + '：设备注册说明与用户回复。</p>').join('');
      document.getElementById('composer').innerHTML = '<div><p>请选择设备系统：</p><button id="answer">Linux</button><p>下一步会引导安装。</p></div><textarea aria-label="输入消息"></textarea>';
      const dashboard = {enabled: true, resident_id: 'fixture', signature: 'loaded', view_manifest: {payload: {layout: 'wireguard-device-manager'}},
        status: {payload: {devices: Array.from({length: 18}, (_, i) => ({label: 'device-' + i, owner: 'sample'}))}}, permissions: {can_run: true}, commands: [], recent_runs: []};
      window.saved = []; window.answers = 0;
      document.getElementById('answer').onclick = () => answers++;
      const state = {detail: {watchboard: dashboard}, watchboardCollapsed: false, watchboardHiddenResidents: new Set()};
      const elements = {body: 'chatBody', watchboard: 'board', watchboardSplitter: 'splitter'};
      window.boardController = PortalLineageWorkbenchModules.createWatchboard({state,
        getChatElement: name => document.getElementById(elements[name]), getElementById: id => document.getElementById(id), escapeHtml, portalUi: PortalUI,
        applyLayout: () => {}, bindSplitter: () => {}, currentResident: () => ({title: 'WireGuard'}), loadFolder: async () => {},
        selectedResidentId: () => 'fixture', residentService: {watchboard: async () => ({watchboard: dashboard})}, submitChatPrompt: async () => {}, writeUiState: value => saved.push(value)});
      window.refreshBoard = () => boardController.render(state.detail);
      refreshBoard();
    }, findChatWorkspaceFunction('workspaceShellHtml').source);
    await page.locator('[data-wireguard-device-add]').waitFor();
    const geometry = () => page.evaluate(() => {
      const box = s => {const r = document.querySelector(s).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
      const toggle = document.querySelector('[data-watchboard-dashboard-toggle]');
      const r = toggle.getBoundingClientRect();
      return {body:box('#chatBody'), message:box('#messages'), board:box('#board'), splitter:box('#splitter'), composer:box('#composer'), toggle:box('[data-watchboard-dashboard-toggle]'), toggleHit:toggle.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
    });
    for (const {viewport, width, height} of [{viewport:1440,width:600,height:500},{viewport:390,width:370,height:500},{viewport:390,width:370,height:420},{viewport:1440,width:1000,height:650}]) {
      await page.setViewportSize({width:viewport,height:900});
      await page.locator('#chat').evaluate((el, {width,height}) => {el.style.width=width+'px'; el.style.height=height+'px';}, {width,height});
      let g = await geometry();
      if (width <= 780) {
        assert.ok(g.board.height <= Math.max(38,g.body.height*.35)+1, 'Watchboard must not consume the reading area: '+JSON.stringify(g));
        assert.ok(g.message.width >= width-2 && g.board.y >= g.message.bottom-1);
        assert.equal(g.splitter.width,0);
      } else {
        assert.ok(g.board.x >= g.message.right && g.message.width>=600 && g.board.y===g.message.y, JSON.stringify(g));
      }
      assert.ok(g.board.bottom<=g.composer.y+1 && g.toggleHit, 'composer and collapse action must remain accessible');
      await page.locator('#answer').click();
      await page.locator('#messages').evaluate(el=>{el.scrollTop=100;});
      const reading = await page.locator('#messages').evaluate(el=>el.scrollTop);
      await page.locator('#board').evaluate(el=>{el.scrollTop=el.scrollHeight;});
      assert.equal(await page.locator('#messages').evaluate(el=>el.scrollTop),reading, 'independent scrolling');
      assert.ok(await page.locator('#board').evaluate(el=>el.scrollTop>0 && el.scrollWidth<=el.clientWidth+1));
      assert.ok((await geometry()).toggleHit, 'collapse button remains visible after scrolling');
      await page.locator('[data-watchboard-dashboard-toggle]').click();
      g=await geometry();
      if(width<=780) assert.ok(g.board.height<=38 && g.message.height>=g.body.height-39,JSON.stringify(g));
      else assert.ok(g.board.width<=38 && g.message.width>=width-39,JSON.stringify(g));
      assert.ok(g.toggleHit && g.toggle.right<=g.board.right+1 && g.toggle.bottom<=g.board.bottom+1,'collapsed action must not be clipped');
      await page.evaluate(()=>refreshBoard());
      assert.ok(await page.locator('#board').evaluate(el=>el.classList.contains('collapsed')));
      await page.locator('[data-watchboard-dashboard-toggle]').click();
      assert.equal(await page.evaluate(()=>saved.at(-1).watchboardCollapsed),false);
    }
    assert.equal(await page.evaluate(()=>answers),4);
    assert.deepEqual(errors,[]);
    if(process.env.WATCHBOARD_SCREENSHOT) {
      await page.setViewportSize({width:390,height:650});
      await page.locator('#chat').evaluate(el=>{el.style.width='370px';el.style.height='600px';});
      await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT});
    }
    await page.evaluate(()=>boardController.destroy());
    console.log('PASS real WireGuard panel: desktop/sidebar, narrow/short chat, question actions, independent scrolling, collapse/expand and saved preference');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
