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
    const page = await browser.newPage({hasTouch:true, viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => route.fulfill({contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh-CN" data-portal-theme="auto"><meta charset="utf-8"><body><section id="chat" class="portalChatWorkspaceRoot" style="width:600px;height:500px"></section></body></html>'}));
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
      window.saved = []; window.answers = 0; window.selected = 'fixture';
      document.getElementById('answer').onclick = () => answers++;
      const state = {detail: {watchboard: dashboard}, watchboardCollapsed: false, watchboardHiddenResidents: new Set()};
      const elements = {body: 'chatBody', watchboard: 'board', watchboardSplitter: 'splitter'};
      window.boardController = PortalLineageWorkbenchModules.createWatchboard({state,
        getChatElement: name => document.getElementById(elements[name]), getElementById: id => document.getElementById(id), escapeHtml, portalUi: PortalUI,
        applyLayout: () => {}, bindSplitter: () => {}, currentResident: () => ({title: 'WireGuard'}), loadFolder: async () => {},
        selectedResidentId: () => selected, residentService: {watchboard: async () => ({watchboard: dashboard})}, submitChatPrompt: async () => {}, writeUiState: value => saved.push(value)});
      window.refreshBoard = () => boardController.render(state.detail);
      window.boardState = state;
      refreshBoard();
    }, findChatWorkspaceFunction('workspaceShellHtml').source);
    await page.locator('[data-wireguard-device-add]').waitFor();
    const geometry = () => page.evaluate(() => {
      const box = s => {const r = document.querySelector(s).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
      const toggle = document.querySelector('#board.peeking [data-watchboard-dashboard-toggle], #board:not(.collapsed) [data-watchboard-dashboard-toggle]') || document.querySelector('.watchboardEdgeTrigger');
      const r = toggle.getBoundingClientRect();
      return {body:box('#chatBody'), message:box('#messages'), board:box('#board'), splitter:box('#splitter'), composer:box('#composer'), toggle:box(toggle.matches('.watchboardEdgeTrigger') ? '.watchboardEdgeTrigger' : '[data-watchboard-dashboard-toggle]'), toggleHit:toggle.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
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
      assert.equal(g.board.width,0, 'collapsed panel has no rail');
      assert.ok(g.message.width>=g.body.width-1 && g.message.height>=g.body.height-1, 'collapsed panel consumes no layout space: '+JSON.stringify(g));
      assert.ok(g.toggleHit && g.toggle.width<=30 && g.toggle.height<=38, 'small reachable edge tab: '+JSON.stringify(g));
      if(process.env.WATCHBOARD_SCREENSHOT && width===1000) await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-closed.png'});
      await page.evaluate(()=>refreshBoard());
      assert.equal(await page.locator('#board').isVisible(),false);
      const edge=page.locator('.watchboardEdgeTrigger');
      await edge.hover();
      await page.locator('#board.peeking').waitFor();
      g=await geometry();
      assert.ok(g.message.width>=g.body.width-1 && g.message.height>=g.body.height-1, 'hover must not shift messages');
      assert.ok(g.board.right<=g.body.right && g.board.x>=g.body.x && g.board.bottom<=g.composer.y+1, 'drawer stays in message area');
      if(process.env.WATCHBOARD_SCREENSHOT && width===1000) await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-open.png'});
      await page.locator('.watchboardTitle').hover();
      await page.waitForTimeout(200);
      assert.equal(await page.locator('#board').isVisible(),true, 'moving from edge to panel keeps it open');
      await page.locator('#board').evaluate(el=>{el.scrollTop=el.scrollHeight;});
      assert.equal(await page.locator('#messages').evaluate(el=>el.scrollTop),reading);
      await page.locator('#answer').hover();
      await page.waitForFunction(()=>!document.querySelector('#board').classList.contains('peeking'));
      assert.equal(await page.evaluate(()=>saved.at(-1).watchboardCollapsed),true, 'hover does not change preference');
      await edge.click();
      await page.locator('#answer').hover();
      await page.waitForTimeout(200);
      assert.equal(await page.locator('#board').isVisible(),true, 'click keeps drawer open');
      await page.locator('#answer').click();
      assert.equal(await page.locator('#board').isVisible(),false, 'outside click dismisses without blocking the answer');
      await edge.hover();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#board').isVisible(),false);
      assert.equal(await edge.evaluate(el=>el===document.activeElement),true, 'Escape restores focus to edge');
      await page.keyboard.press('ArrowLeft');
      assert.equal(await page.locator('#board').isVisible(),true, 'keyboard reveals drawer');
      await page.locator('[data-watchboard-dashboard-toggle]').click();
      assert.equal(await page.evaluate(()=>saved.at(-1).watchboardCollapsed),false);
    }
    assert.equal(await page.evaluate(()=>answers),8);
    // Touch does not depend on hover; closing and reopening preserves app scroll.
    await page.locator('[data-watchboard-dashboard-toggle]').click();
    await page.locator('.watchboardEdgeTrigger').dispatchEvent('pointerenter',{pointerType:'touch'});
    assert.equal(await page.locator('#board').isVisible(),false);
    await page.locator('.watchboardEdgeTrigger').tap();
    assert.equal(await page.locator('#board').isVisible(),true);
    await page.locator('#board').evaluate(el=>{el.scrollTop=200;});
    await page.locator('.watchboardEdgeTrigger').tap();
    assert.equal(await page.locator('#board').isVisible(),false);
    await page.locator('.watchboardEdgeTrigger').tap();
    assert.equal(await page.locator('#board').evaluate(el=>el.scrollTop),200);
    await page.locator('[data-wireguard-device-add]').click();
    await page.locator('#watchboardModalOverlay').waitFor();
    await page.waitForTimeout(200);
    assert.equal(await page.locator('#board').isVisible(),true, 'application modal keeps its originating drawer open');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#watchboardModalOverlay').count(),0, 'Escape closes modal first');
    assert.equal(await page.locator('#board').isVisible(),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#board').isVisible(),false, 'next Escape closes drawer');
    await page.locator('.watchboardEdgeTrigger').tap();
    // Switching to a Resident without Watchboard clears transient state and tab.
    await page.evaluate(()=>{selected='other';boardState.detail={};refreshBoard();});
    assert.equal(await page.locator('.watchboardEdgeTrigger').isVisible(),false);
    assert.equal(await page.locator('#board').isVisible(),false);
    await page.evaluate(()=>{selected='fixture';boardState.detail={watchboard:{enabled:true,resident_id:'fixture',signature:'new',status:{},commands:[]}};refreshBoard();});
    assert.equal(await page.locator('#board').isVisible(),false);
    await page.locator('.watchboardEdgeTrigger').hover();
    assert.equal(await page.locator('#board').isVisible(),true, 'generic app shares edge behavior');
    assert.deepEqual(errors,[]);
    if(process.env.WATCHBOARD_SCREENSHOT) {
      await page.setViewportSize({width:390,height:650});
      await page.locator('#chat').evaluate(el=>{el.style.width='370px';el.style.height='600px';});
      await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-narrow.png'});
    }
    await page.evaluate(()=>boardController.destroy());
    assert.equal(await page.locator('.watchboardEdgeTrigger').count(),0);
    console.log('PASS Watchboard edge: no rail/layout shift, hover, click/touch, keyboard/Escape, outside click, independent scroll, pinned layout and owner cleanup');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
