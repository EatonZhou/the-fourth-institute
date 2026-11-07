'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {findChatWorkspaceFunction} = require('./chat_workspace_modules.cjs');
const {PORTAL_COMPONENTS_STYLES} = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const restoreSource = findChatWorkspaceFunction('applyLineageUiState', {
  'lineage_workbench.js': fs.readFileSync(path.join(root, 'lineage_workbench.js'), 'utf8'),
}).source;
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_TEST_CHROMIUM ? {executablePath: process.env.PORTAL_TEST_CHROMIUM} : {})});
  try {
    const page = await browser.newPage({hasTouch:true, viewport: {width: 1440, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => route.fulfill({contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh-CN" data-portal-theme="auto"><meta charset="utf-8"><body><section id="chat" class="portalChatWorkspaceRoot" style="width:600px;height:500px"></section></body></html>'}));
    async function loadFixture() {
    await page.goto('http://watchboard.fixture.invalid');
    for (const file of [...PORTAL_COMPONENTS_STYLES.map(name => `${name}.css`), 'lineage_workbench.css', 'portal_design_system.css', 'portal_themes.css', 'watchboard_applications/wireguard.css']) await page.addStyleTag({content: fs.readFileSync(path.join(root, file), 'utf8')});
    for (const file of ['portal_text.js', 'portal_state.js', 'portal_translations.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_overlay.js', 'portal_ui.js', 'portal_refresh_coordinator.js', 'watchboard_applications/registry.js', 'watchboard_applications/wireguard.js', 'watchboard_applications/telepus.js', 'lineage_workbench_state.js', 'lineage_workbench_watchboard.js']) await page.addScriptTag({content: fs.readFileSync(path.join(root, file), 'utf8')});
    await page.evaluate(({shell, restoreSource}) => {
      const escapeHtml = PortalUIPrimitives.escapeHtml, iconSvg = PortalUIPrimitives.iconHtml;
      const workspaceShellHtml = eval('(' + shell + ')');
      window.mountBoard = () => {
      window.boardController?.destroy();
      document.getElementById('chat').innerHTML = workspaceShellHtml({title: '添加设备到 WireGuard', bodyId: 'chatBody', messageListId: 'messages', composerId: 'composer',
        bodySuffixHtml: '<div id="splitter" class="watchboardSplitter"></div><aside id="board" class="watchboard"></aside>'});
      document.getElementById('messages').innerHTML = Array.from({length: 24}, (_, i) => '<p>聊天记录 ' + i + '：设备注册说明与用户回复。</p>').join('');
      document.getElementById('composer').innerHTML = '<div><p>请选择设备系统：</p><button id="answer">Linux</button><p>下一步会引导安装。</p></div><textarea aria-label="输入消息"></textarea>';
      const dashboard = {enabled: true, resident_id: 'fixture', signature: 'loaded', view_manifest: {payload: {layout: 'wireguard-device-manager'}},
        status: {payload: {devices: Array.from({length: 18}, (_, i) => ({label: 'device-' + i, owner: 'sample'}))}}, permissions: {can_run: true}, commands: [], recent_runs: []};
      window.saved ||= []; window.answers ||= 0; window.selected = 'fixture';
      document.getElementById('answer').onclick = () => answers++;
      const state = PortalLineageWorkbenchState.createState();
      state.detail = {watchboard: dashboard};
      // Run the actual restore function, with unrelated page collaborators inert.
      const readLineageUiState = () => JSON.parse(localStorage.getItem('fixture-layout') || '{}');
      const restoreWhichResidentState = () => {}, currentAgentPreviewPanelComponent = () => null;
      const normalizedProjectDisplayFilter = value => value;
      window.PortalNavigation = {normalizeLineageView: value => value};
      eval('(' + restoreSource + ')')();
      const elements = {body: 'chatBody', watchboard: 'board', watchboardSplitter: 'splitter'};
      window.boardController = PortalLineageWorkbenchModules.createWatchboard({state,
        getChatElement: name => document.getElementById(elements[name]), getElementById: id => document.getElementById(id), escapeHtml, portalUi: PortalUI,
        applyLayout: () => {}, bindSplitter: () => {}, currentResident: () => ({title: state.detail.watchboard?.telegram_users ? 'Telepus' : 'WireGuard'}), loadFolder: async () => {},
        selectedResidentId: () => selected, residentService: {watchboard: async () => ({watchboard: dashboard})}, submitChatPrompt: async () => {}, writeUiState: value => {saved.push(value); localStorage.setItem('fixture-layout', JSON.stringify(value));}});
      window.refreshBoard = () => boardController.render(state.detail);
      window.boardState = state;
      refreshBoard();
      };
      mountBoard();
    }, {shell: findChatWorkspaceFunction('workspaceShellHtml').source, restoreSource});
    }
    await loadFixture();
    await page.locator('[data-wireguard-device-add]').waitFor({state:'attached'});
    const geometry = () => page.evaluate(() => {
      const box = s => {const r = document.querySelector(s).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
      const toggle = document.querySelector('#board.peeking [data-watchboard-dashboard-toggle], #board:not(.collapsed) [data-watchboard-dashboard-toggle]') || document.querySelector('.watchboardEdgeTrigger');
      const r = toggle.getBoundingClientRect();
      return {body:box('#chatBody'), message:box('#messages'), board:box('#board'), splitter:box('#splitter'), composer:box('#composer'), toggle:box(toggle.matches('.watchboardEdgeTrigger') ? '.watchboardEdgeTrigger' : '[data-watchboard-dashboard-toggle]'), toggleHit:toggle.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
    });
    // First visit and both legacy saved values, in wide/narrow/phone/short panels.
    const observations = [];
    for (const {viewport, width, height} of [{viewport:1440,width:600,height:500},{viewport:390,width:370,height:500},{viewport:390,width:370,height:420},{viewport:1440,width:1000,height:650}]) {
      await page.setViewportSize({width:viewport,height:900});
      await page.locator('#chat').evaluate((el, {width,height}) => {el.style.width=width+'px'; el.style.height=height+'px';}, {width,height});
      for (const preference of [null, true, false]) {
        await page.mouse.move(0,0);
        await page.evaluate(preference => {
          if (preference === null) localStorage.removeItem('fixture-layout');
          else localStorage.setItem('fixture-layout',JSON.stringify({watchboardCollapsed:preference}));
          mountBoard();
        }, preference);
        let g = await geometry();
        const docked = width > 780 && preference === false;
        assert.equal(await page.evaluate(()=>boardState.watchboardCollapsed),preference !== false);
        if (docked) {
          assert.ok(g.board.x >= g.message.right && g.message.width>=600 && g.board.y===g.message.y, JSON.stringify(g));
          await page.locator('[data-watchboard-dashboard-toggle]').click();
          assert.equal(await page.evaluate(()=>saved.at(-1).watchboardCollapsed),true);
        }
        g=await geometry();
        assert.equal(g.board.width,0, 'first visit or restored narrow panel has no rail/bottom section');
        assert.equal(g.splitter.width,0);
        assert.ok(g.message.width>=g.body.width-1 && g.message.height>=g.body.height-1, JSON.stringify(g));
        assert.ok(g.toggleHit && g.toggle.width<=30 && g.toggle.height<=38, 'reachable edge tab');
        observations.push({width,height,preference,closed:g});
        const before = await page.evaluate(()=>localStorage.getItem('fixture-layout'));
        const edge=page.locator('.watchboardEdgeTrigger');
        await edge.hover();
        g=await geometry();
        assert.ok(g.message.width>=g.body.width-1 && g.message.height>=g.body.height-1, 'hover must not shift messages');
        assert.ok(g.board.right<=g.body.right && g.board.x>=g.body.x && g.board.bottom<=g.composer.y+1, 'drawer stays above composer');
        await page.locator('#messages').evaluate(el=>{el.scrollTop=100;});
        const reading = await page.locator('#messages').evaluate(el=>el.scrollTop);
        await page.locator('#board').evaluate(el=>{el.scrollTop=el.scrollHeight;});
        assert.equal(await page.locator('#messages').evaluate(el=>el.scrollTop),reading, 'independent scrolling');
        assert.ok(await page.locator('#board').evaluate(el=>el.scrollTop>0 && el.scrollWidth<=el.clientWidth+1));
        assert.ok((await geometry()).toggleHit, 'header action remains visible after scrolling');
        await page.locator('.watchboardTitle').hover();
        await page.waitForTimeout(200);
        assert.equal(await page.locator('#board').isVisible(),true);
        await page.locator('#answer').hover();
        await page.waitForFunction(()=>!document.querySelector('#board').classList.contains('peeking'));
        assert.equal(await page.evaluate(()=>localStorage.getItem('fixture-layout')),before, 'temporary compact mode does not rewrite preference');
        await edge.click();
        await page.locator('#answer').hover();
        await page.waitForTimeout(200);
        assert.equal(await page.locator('#board').isVisible(),true, 'click holds drawer');
        await page.locator('#answer').click();
        assert.equal(await page.locator('#board').isVisible(),false, 'outside click dismisses without blocking answer');
        await edge.hover();
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#board').isVisible(),false);
        assert.equal(await edge.evaluate(el=>el===document.activeElement),true);
        await page.keyboard.press('ArrowLeft');
        assert.equal(await page.locator('#board').isVisible(),true);
        await page.locator('[data-watchboard-dashboard-toggle]').click();
        if (width<=780) {
          assert.equal(await page.locator('#board').isVisible(),false, 'compact header closes instead of bottom docking');
          assert.equal(await page.evaluate(()=>localStorage.getItem('fixture-layout')),before);
        } else {
          assert.equal(await page.evaluate(()=>saved.at(-1).watchboardCollapsed),false);
          assert.ok((await geometry()).board.x >= (await geometry()).message.right, 'wide header docks on right');
        }
      }
    }
    assert.equal(await page.evaluate(()=>answers),12);
    // Resize a restored dock through the breakpoint without rerendering manually.
    // Effective CSS width also changes this way under browser zoom/split resizing.
    await page.locator('#chat').evaluate(el=>{el.style.width='780px';});
    await page.waitForFunction(()=>document.querySelector('#board').classList.contains('collapsed'));
    assert.equal((await geometry()).board.width,0);
    assert.equal(await page.evaluate(()=>boardState.watchboardCollapsed),false);
    await page.locator('#chat').evaluate(el=>{el.style.width='781px';});
    await page.waitForFunction(()=>!document.querySelector('#board').classList.contains('collapsed'));
    assert.ok((await geometry()).board.x >= (await geometry()).message.right);
    // Full document reload uses the saved false but a narrow frame stays closed.
    await loadFixture();
    assert.equal(await page.evaluate(()=>boardState.watchboardCollapsed),false);
    assert.equal((await geometry()).board.width,0);
    await page.locator('#chat').evaluate(el=>{el.style.width='1000px';el.style.height='650px';});
    await page.waitForFunction(()=>!document.querySelector('#board').classList.contains('collapsed'));
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
    // The actual Telepus application shares the frame; synthetic contacts only.
    await page.evaluate(()=>{
      boardState.detail={watchboard:{enabled:true,resident_id:'fixture',signature:'telepus',
        telegram_users:{enabled:true,permissions:{can_manage:true},users:Array.from({length:12},(_,i)=>({name:'Sample contact '+i,telegram_user_id:'fixture-'+i,display_id:'12…'+i}))}}};
      refreshBoard();
    });
    await page.locator('[data-telepus-add-user]').waitFor({state:'attached'});
    for (const theme of ['auto','dark']) {
      await page.evaluate(theme=>document.documentElement.dataset.portalTheme=theme,theme);
      await page.setViewportSize({width:900,height:760});
      await page.locator('#chat').evaluate(el=>{el.style.width='600px';el.style.height='650px';});
      await page.mouse.move(0,0);
      await page.locator('#answer').click();
      if(process.env.WATCHBOARD_SCREENSHOT) await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-telepus-'+theme+'-closed.png'});
      await page.locator('.watchboardEdgeTrigger').hover();
      assert.equal(await page.locator('.watchboardCompactItem').count(),12);
      const g=await geometry();
      assert.equal(g.message.height,g.body.height);
      assert.ok(g.board.bottom<=g.composer.y+1);
      if(process.env.WATCHBOARD_SCREENSHOT) await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-telepus-'+theme+'-open.png'});
    }
    await page.setViewportSize({width:390,height:740});
    await page.locator('#chat').evaluate(el=>{el.style.width='370px';el.style.height='650px';});
    const phone=await geometry();
    assert.ok(phone.board.x>=phone.body.x && phone.board.right<=phone.body.right);
    if(process.env.WATCHBOARD_SCREENSHOT) {
      await page.screenshot({path:process.env.WATCHBOARD_SCREENSHOT+'-telepus-phone.png'});
      fs.writeFileSync(process.env.WATCHBOARD_SCREENSHOT+'-geometry.json',JSON.stringify(observations,null,2));
    }
    assert.deepEqual(errors,[]);
    await page.evaluate(()=>boardController.destroy());
    assert.equal(await page.locator('.watchboardEdgeTrigger').count(),0);
    console.log('PASS Watchboard edge: no rail/layout shift, hover, click/touch, keyboard/Escape, outside click, independent scroll, 12 default/restored layouts, document reload, responsive docking, Telepus and owner cleanup');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
