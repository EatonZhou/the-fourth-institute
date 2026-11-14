'use strict';

// Production notification rendering, navigation and dialog controller. Mailbox
// writes/navigation are intercepted locally; no real notification is read/sent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {babelParse} = require('./babel_parse.cjs');
const assets = path.resolve(__dirname, '../../../management_portal/static');
const runtime = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {chromium, webkit} = require(fs.existsSync(runtime) ? runtime : 'playwright');
const text = fs.readFileSync(path.join(assets, 'portal_agent_inbox.js'), 'utf8');
const declarations = babelParse(text).program.body[0].expression.callee.body.body;
const names = ['store', 'routedEntry', 'isNotification', 'navigationResidentId', 'verifyNavigationTarget',
  'navigateToEntry', 'ensureNotificationDetail', 'activateEntry'];
const source = names.map(name => {
  const node = declarations.find(node => node.type === 'FunctionDeclaration' && node.id.name === name);
  assert.ok(node, name);
  return text.slice(node.start, node.end);
}).join('\n');
const evidence = process.env.NOTIFICATION_DETAIL_EVIDENCE;
const report = [];
const overlay = '[data-agent-notification-detail]';
const surface = '.portalAgentNotificationSurface';

async function geometry(page) {
  return page.evaluate(() => {
    const card = document.querySelector('.portalAgentNotificationSurface');
    const body = card.querySelector('.portalDetailBody');
    const footer = card.querySelector('footer');
    const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}; };
    return {card:rect(card),body:rect(body),footer:rect(footer),scrollWidth:card.scrollWidth,clientWidth:card.clientWidth,
      titleFont:getComputedStyle(card.querySelector('h2')).fontSize,
      metaFont:getComputedStyle(card.querySelector('[data-agent-notification-meta]')).fontSize,
      bodyFont:getComputedStyle(body.firstElementChild).fontSize,
      outline:getComputedStyle(card).outlineStyle};
  });
}

(async () => {
  for (const engine of [chromium, webkit]) {
    const browser = await engine.launch({headless:true});
    try {
      const page = await browser.newPage({viewport:{width:1440,height:900}});
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => {errors.push(error.message); console.error(error.message);});
      await page.setContent('<!doctype html><html lang="zh-CN" data-portal-theme="light"><body><button id="opener">打开通知</button><main>Portal · 社群</main></body></html>');
      // Shared component styles precede the design system on real Portal pages.
      for (const name of ['portal_components.css','portal_components_agent_inbox.css','portal_design_system.css','portal_themes.css']) {
        await page.addStyleTag({path:path.join(assets,name)});
      }
      await page.addStyleTag({content:'body{margin:0;padding:24px;background:var(--bg);color:var(--text);font:14px sans-serif}main{padding:40px}'});
      await page.evaluate(() => {
        const tag = (parts,...args) => parts.reduce((s,p,i) => s+p+(args[i]??''),'');
        window.PortalI18n = {t:x=>x,html:tag,text:tag,markup:x=>x,locale:()=> 'zh-CN'};
        window.PortalCompatibility = {identifier:x=>x};
        window.calls = [];
        window.PortalConversationStore = {markRead:async key => {
          calls.push(['read',key]);
          if (window.failRead) throw Error('fixture receipt failed');
        }};
        window.addEventListener('portal:agent-conversation-open', event => {
          calls.push(['navigate',event.detail.entry.url]); event.detail.handled = true;
        });
        window.entry = {key:'notification:fixture',section:'notification',kind:'lineage-notification',
          title:'Lineage 自动执行失败',url:'/society?lineage=lineage_111111111111',notification:{
            severity:'error',lineage_id:'lineage_111111111111',lineage_title:'HW-Design-Workflow',created_by:'portal',
            created_at:'2026-11-14T09:33:46.672000Z',
            message:'TFI Portal resource type registry is unavailable\n\n失败发生时间：2026-11-14T09:33:46.672000Z\n节点：event_source_2e2a5e1bf324\nTrigger Path：trigger_path_80718f80f170'}};
      });
      for (const name of ['portal_ui_primitives.js','portal_overlay.js','portal_ui.js']) {
        await page.addScriptTag({path:path.join(assets,name)});
      }
      await page.addScriptTag({content:`let notificationDetail = null;
        const state = {listenerController:new AbortController(),navigationKeys:new Set()};
        const residentDiscovery = null;
        function updateChrome() {}
        ${source}
        document.querySelector('#opener').onclick = () => activateEntry(window.entry);`});
      const open = async () => {
        await page.locator('#opener').focus();
        await page.locator('#opener').click();
        await page.waitForFunction(() => document.activeElement?.matches('.portalDetailHeader [data-agent-notification-close]'));
      };
      await open();
      assert.equal(await page.locator('[data-agent-notification-message]').textContent(), await page.evaluate(() => entry.notification.message));
      assert.deepEqual(await page.evaluate(() => calls), [['read','notification:fixture']]);
      assert.equal(await page.locator('[data-agent-notification-severity]').textContent(), '错误');
      assert.equal(await page.locator('time').getAttribute('datetime'), '2026-11-14T09:33:46.672000Z');
      for (const [name,width,height,theme] of [['desktop',1440,900,'light'],['narrow',375,720,'light'],['dark',320,620,'dark']]) {
        await page.setViewportSize({width,height});
        await page.evaluate(theme => document.documentElement.dataset.portalTheme=theme,theme);
        const g = await geometry(page);
        assert.ok(g.card.width<=560.5 && g.card.x>=11 && g.card.right<=width-11, JSON.stringify(g));
        assert.ok(g.card.y>=11 && g.card.bottom<=height-11 && g.footer.bottom<=g.card.bottom, JSON.stringify(g));
        assert.ok(g.scrollWidth<=g.clientWidth+1, JSON.stringify(g));
        assert.equal(g.titleFont,'18px'); assert.equal(g.metaFont,'12px'); assert.equal(g.bodyFont,'14px');
        assert.equal(g.outline,'none','the whole card does not receive a focus outline');
        if (evidence) {
          fs.mkdirSync(evidence,{recursive:true});
          await page.screenshot({path:path.join(evidence,`${engine.name()}-notification-${name}.png`)});
        }
        report.push({engine:engine.name(),name,...g});
      }
      await page.keyboard.press('Shift+Tab');
      assert.equal(await page.locator('[data-agent-notification-source]').evaluate(el=>el===document.activeElement),true,'focus stays in dialog');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator(overlay).isVisible(),false);
      assert.equal(await page.locator('#opener').evaluate(el=>el===document.activeElement),true);
      await open();
      await page.locator('[data-agent-notification-source]').click();
      await page.waitForFunction(()=>calls.some(c=>c[0]==='navigate'));
      assert.equal(await page.locator(overlay).isVisible(),false);
      assert.equal(await page.evaluate(()=>calls.filter(c=>c[0]==='navigate').length),1);
      // Untrusted/long values remain plain text; absent navigation hides its action.
      await page.evaluate(() => {
        entry.url=''; entry.title='很长的通知标题'.repeat(12);
        entry.notification.lineage_title='LongUnbrokenSource'.repeat(12);
        entry.notification.message='<img src=x onerror="window.injected=true">\n'+'LongIdentifier'.repeat(60)+'\n'+'内容\n'.repeat(100);
        entry.notification.created_by=''; entry.notification.severity='unknown';
        failRead=true;
      });
      await open();
      assert.equal(await page.locator('[data-agent-notification-source]').isVisible(),false);
      assert.equal(await page.locator('[data-agent-notification-sender]').isVisible(),false);
      assert.equal(await page.locator('[data-agent-notification-severity]').textContent(),'信息');
      assert.equal(await page.locator('[data-agent-notification-message] img').count(),0);
      // Long titles/sources may occupy space, but the footer and close remain reachable.
      const long = await geometry(page);
      assert.ok(long.card.bottom<=page.viewportSize().height-11 && long.scrollWidth<=long.clientWidth+1,JSON.stringify(long));
      await page.locator('.portalDetailBody').evaluate(el=>el.scrollTop=el.scrollHeight);
      await page.locator('footer [data-agent-notification-close]').click();
      assert.equal(await page.locator(overlay).isVisible(),false);
      assert.deepEqual(errors,[]);
      report.push({engine:engine.name(),name:'focus-receipt-navigation-long-content',passed:true});
    } finally {await browser.close();}
  }
  if (evidence) fs.writeFileSync(path.join(evidence,'notification-detail-results.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:report.length,cases:report.map(x=>`${x.engine}:${x.name}`)}));
})().catch(error=>{console.error(error);process.exitCode=1;});
