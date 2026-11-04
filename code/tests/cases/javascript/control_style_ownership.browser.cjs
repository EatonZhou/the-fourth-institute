'use strict';
// Computed styles of real shared controls must survive a host change and late
// host CSS. This catches indirect selectors (e.g. `.page button`), not just
// overrides naming component classes. No backend or business action is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const evidence = process.env.CONTROL_STYLE_EVIDENCE;
const baseline = process.env.CONTROL_STYLE_BASELINE;
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts(), 'home_report.js'];
const styles = ['portal_themes.css', 'portal_design_system.css', 'lineage_workbench.css',
  ...PORTAL_COMPONENTS_STYLES.map(n => n + '.css'), 'home_dialogue.css', 'home_report.css', 'wall.css'];
const read = name => fs.readFileSync(baseline && fs.existsSync(path.join(baseline, name))
  ? path.join(baseline, name) : path.join(repo, 'management_portal/static', name));
const report = { failures: [], samples: [], errors: [] };
const check = (actual, expected, message) => {
  try { assert.deepEqual(actual, expected, message); } catch (e) { report.failures.push({message, actual, expected}); }
};
(async () => {
  if (evidence) fs.mkdirSync(evidence, { recursive: true });
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8">' +
        styles.map(n => `<link rel="stylesheet" href="/${n}">`).join('') +
        '<style>body{margin:0;padding:16px;background:var(--bg);color:var(--text);font:14px/1.5 system-ui}' +
        '#host{height:650px;min-width:0;color:var(--text)}#chat{height:560px;min-height:0;width:100%}' +
        '#probe{display:flex;gap:8px;flex-wrap:wrap;padding:8px}' +
        '.componentAction{font:12px/20px system-ui;border:0;border-radius:4px;background:transparent;color:rgb(32,80,120);padding:2px 4px;opacity:1;cursor:pointer}' +
        '.componentAction:hover{background:rgb(220,235,245)}' +
        '.componentAction:focus-visible{outline:2px solid rgb(32,80,120);outline-offset:4px}' +
        '.componentAction:disabled{opacity:.3;cursor:not-allowed}' +
        '.componentAction[aria-pressed="true"]{background:rgb(32,80,120);color:white;outline:0}' +
        '.authoredDisabled{font:12px/20px system-ui;opacity:.7;cursor:not-allowed}' +
        '</style><main id="host"><div id="probe"><button class="portalButton is-primary">主要操作</button>' +
        '<button class="portalButton is-quiet">轻量操作</button><button class="componentAction">组件自己的操作</button>' +
        '<button class="authoredDisabled" disabled>暂不可用</button></div>' +
        '<div id="chat"></div></main><div id="frontier"></div>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routeScript;
    else if (modules.includes(name) || styles.includes(name) || name === 'frontier.css') {
      body = read(name); if (name.endsWith('.css')) type = 'text/css';
    }
    res.writeHead(body === undefined ? 404 : 200, {'Content-Type': type}); res.end(body ?? 'not found');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1000, height: 850}});
    page.setDefaultTimeout(5000);
    page.on('pageerror', e => report.errors.push(e.message));
    const origin = 'http://127.0.0.1:' + server.address().port;
    await page.route('**/*', r => r.request().url().startsWith(origin + '/') ? r.continue() : r.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalIdentity = {username: () => 'reader'};
      window.workspace = PortalChatWorkspace.createWorkspace({root: document.getElementById('chat'),
        id: 'style-contract', historyScope: () => 'style-contract', refs: false,
        contract: {schema: 'portal.chat_workspace.v3', messages: [], composer: {can_send: true, queue: {items: []}}}});
      workspace.setMessages([
        {id: 'long', role: 'assistant', author: 'Ask Me Anything', content: '注意力机制根据上下文建立信息关联。\n\n'.repeat(20)},
        {id: 'user', role: 'user', content: '明白了。'},
        {id: 'short', role: 'assistant', content: '可以继续阅读完整解释。'}]);
      window.styleProperties = ['fontSize','fontWeight','lineHeight','color','backgroundColor','borderTopWidth',
        'borderTopColor','borderRadius','paddingTop','paddingRight','paddingBottom','paddingLeft',
        'opacity','cursor','outlineStyle','outlineWidth','outlineColor','outlineOffset','boxShadow'];
      window.sample = el => Object.fromEntries(styleProperties.map(p => [p, getComputedStyle(el)[p]]));
    });
    await page.locator('[data-message-fold-toggle="long"]').waitFor({state: 'visible'});
    check(await page.locator('.authoredDisabled').evaluate(el=>getComputedStyle(el).opacity), '0.7',
      'global disabled defaults yield to an authored control');
    // Actual Chat buttons, plus two design-system variants and a minimal custom
    // component. Parent colour/font are identical; only host membership changes.
    for (const state of ['normal','disabled','pressed']) {
      const rows = await page.evaluate(state => {
        const host = document.getElementById('host');
        const controls = [...host.querySelectorAll('button')];
        const saved = controls.map(el => [el.disabled, el.getAttribute('aria-pressed')]);
        if (state === 'disabled') controls.forEach(el => el.disabled = true);
        if (state === 'pressed') controls.forEach(el => el.setAttribute('aria-pressed', 'true'));
        host.className = '';
        const expected = controls.map(sample), rows = [];
        for (const cls of ['homeDialogue','home-report','wallPage']) {
          host.className = cls;
          controls.forEach((el, i) => rows.push({host:cls,state,control:el.className,expected:expected[i],actual:sample(el)}));
        }
        host.className = '';
        controls.forEach((el,i) => {el.disabled=saved[i][0]; saved[i][1] === null ? el.removeAttribute('aria-pressed') : el.setAttribute('aria-pressed',saved[i][1]);});
        return rows;
      }, state);
      for (const r of rows) check(r.actual,r.expected,`${r.host}/${state}/${r.control}`);
      report.samples.push({state, comparisons:rows.length});
    }
    const action = page.locator('.componentAction');
    for (const state of ['hover','focus']) {
      if (state === 'hover') await action.hover();
      else { await page.mouse.move(0,0); await page.keyboard.press('Tab'); await action.focus(); }
      const expected = await action.evaluate(el => sample(el));
      for (const host of ['homeDialogue','home-report','wallPage']) {
        await page.locator('#host').evaluate((el,cls)=>el.className=cls,host);
        if (state === 'hover') await action.hover(); else await action.focus();
        check(await action.evaluate(el => sample(el)),expected,`${host}/${state}/custom component`);
      }
      await page.locator('#host').evaluate(el=>el.className='');
    }
    await page.locator('#host').evaluate(el=>el.className='homeDialogue');
    const fold = page.locator('[data-message-fold-toggle="long"]');
    await fold.click(); check(await fold.getAttribute('aria-expanded'),'true','fold click expands');
    await page.keyboard.press('Space'); check(await fold.getAttribute('aria-expanded'),'false','fold keyboard collapses');
    check(await page.locator('[data-message-fold-toggle="short"]').isVisible(),false,'short message has no fold affordance');
    // Frontier is shadow-scoped, but its generic hover must still yield to the
    // authored primary skin. Previously primary text became background-coloured.
    await page.evaluate(async () => {
      const shadow = document.getElementById('frontier').attachShadow({mode:'open'});
      const css = await fetch('/frontier.css').then(r=>r.text());
      shadow.innerHTML = '<style>'+css+'</style><div class="graphTools"><button class="primary">保存节点</button><button>刷新</button></div>';
    });
    const primary = page.locator('#frontier .primary');
    await primary.hover();
    const primaryStyle = await primary.evaluate(el=>({color:getComputedStyle(el).color,bg:getComputedStyle(el).backgroundColor}));
    check(primaryStyle.color,'rgb(255, 255, 255)','Frontier primary retains readable text on hover');
    report.samples.push({frontierPrimary:primaryStyle});
    // Render a real report failure/retry surface. Its own controls must retain
    // the report skin both standalone and in the Home container.
    await page.route('**/api/home-report**', r=>r.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'Fixture access denied'})}));
    const reportStyle = await page.evaluate(async () => {
      const wrapper = document.createElement('div'), root = document.createElement('div');
      wrapper.append(root); document.body.append(wrapper);
      const report = PortalHomeReportView.create({root});
      await report.show({kind:'lineage',id:'lineage_fixture'}).catch(()=>{});
      const button = root.querySelector('button');
      const standalone = sample(button);
      wrapper.className='homeDialogue';
      const embedded = sample(button), ownSkin = {padding:getComputedStyle(button).padding,borderRadius:getComputedStyle(button).borderRadius};
      report.destroy();wrapper.remove();return {standalone,embedded,ownSkin};
    });
    check(reportStyle.embedded,reportStyle.standalone,'report controls keep their own skin inside Home');
    check(reportStyle.ownSkin,{padding:'7px 12px',borderRadius:'7px'},'report retry retains report control styling');
    report.samples.push({reportControl:reportStyle.ownSkin});
    if (evidence) for (const [width,theme] of [[1000,'light'],[380,'light'],[380,'dark']]) {
      await page.setViewportSize({width,height:850});
      await page.evaluate(t=>document.documentElement.dataset.portalTheme=t,theme);
      await fold.scrollIntoViewIfNeeded(); await page.mouse.click(2,2);
      await page.screenshot({path:path.join(evidence,`${theme}-${width}.png`)});
    }
    check(report.errors,[],'no page errors');
  } finally {
    if (browser) await browser.close(); server.close();
    if (evidence) fs.writeFileSync(path.join(evidence,'report.json'),JSON.stringify(report,null,2));
  }
  console.log(JSON.stringify({samples:report.samples, failures:report.failures.length, firstFailures:report.failures.slice(0,3)}));
  assert.equal(report.failures.length,0,'shared component appearance changed across hosts');
})().catch(e=>{console.error(e);process.exitCode=1;});
