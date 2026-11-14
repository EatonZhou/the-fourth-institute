'use strict';

// Production rendering, styles and floating positioning, with local account data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {homeStatements} = require('./home_modules.cjs');
const runtime = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {chromium, webkit} = require(fs.existsSync(runtime) ? runtime : 'playwright');
const assets = path.resolve(__dirname, '../../../management_portal/static');
const evidence = process.env.ACCOUNT_DETAILS_EVIDENCE;
const declarations = homeStatements();
const names = ['accountConcurrencyRows', 'accountConcurrencyHtml', 'accountThresholdHtml', 'accountIntervalHtml',
  'accountSettingsPending', 'accountQuotaWindowIsStale', 'accountQuotaWindows', 'accountSummaryQuotaWindows',
  'accountQuotaWarning', 'accountSubscriptionHtml', 'accountCreditsHtml', 'renderAccountDetails',
  'ensureAccountDetails', 'openAccountDetails', 'syncAccountDetailsButtons'];
const source = names.map(name => declarations.find(item => item.node.id?.name === name).source).join('\n');
const results = [];

(async () => {
  for (const engine of [chromium, webkit]) {
    const browser = await engine.launch({headless:true});
    try {
      const page = await browser.newPage({viewport:{width:1280, height:1000}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<!doctype html><html lang="zh-CN" data-portal-theme="light"><body><button id="trigger" data-account-details="codex_three">账号详情</button></body></html>');
      for (const name of ['portal_components.css', 'home_workbench.css', 'portal_design_system.css', 'portal_themes.css', 'home.css']) {
        await page.addStyleTag({path:path.join(assets, name)});
      }
      await page.addStyleTag({content:'body {margin:0;padding:24px;background:var(--bg);color:var(--text)} #trigger {margin:8px}'});
      await page.evaluate(() => {
        const tag = (parts,...args) => parts.reduce((s,p,i) => s+p+(args[i]??''),'');
        window.PortalI18n = {t:x=>x,html:tag,text:tag,markup:x=>x};
        window.PortalCompatibility = {identifier:x=>x};
        window.PortalUIPrimitives = {elementOf:x=>x};
        window.esc = x => String(x??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
        window.formatDateTime = () => '';
        window.executionAccounts = d => d.accounts;
        window.editable = true;
        window.currentCanSetScheduler = () => editable;
        window.account = {account_id:'codex_three',provider:'codex',kind:'subscription_login',
          capabilities:{resident_details:true,scheduler_settings:true},max_concurrency:10,
          dispatch_interval_min_seconds:2,dispatch_interval_max_seconds:6,
          close_threshold:{supported:true,value:0,metric:'percent',unit:'%'},credits_supported:true,
          subscription:{tier:{name:'pro',id:'pro'}},refresh_cards:{available_count:1,cards:[]},credits:{balance:59112.19}};
        window.currentAgentStatusData = () => ({accounts:[account]});
        window.accountIntakePending = new Map();
        window.accountConcurrencyEdits = new Map();
        window.accountThresholdEdits = new Map();
        window.accountIntervalEdits = new Map();
        window.accountDetails = null;
        window.snapshot = {total:8,queued_messages:3,intake:{open:5,closed:3},work:{running:2,idle:6},open_resident_ids:['r1'],closed_resident_ids:['r2']};
        window.loadAgentStatus = () => {};
        window.loadAccountDetails = details => {details.snapshot=snapshot;renderAccountDetails();};
        window.changeAccountResidentsIntake = () => {};
        window.saveAccountConcurrency = window.saveAccountThreshold = window.saveAccountInterval = () => {};
      });
      await page.addScriptTag({path:path.join(assets, 'portal_overlay.js')});
      await page.addScriptTag({content:source});
      await page.evaluate(() => document.querySelector('#trigger').addEventListener('click', event => openAccountDetails(event.currentTarget)));
      await page.click('#trigger');

      async function check(label) {
        await page.waitForTimeout(80);
        const geometry = await page.evaluate(() => {
          const panel = document.querySelector('#account-details');
          const bounds = panel.getBoundingClientRect();
          const body = panel.querySelector('.account-details-body');
          const footer = panel.querySelector('footer').getBoundingClientRect();
          const overlaps = [...panel.querySelectorAll('form')].flatMap(form => {
            const button = form.querySelector('button');
            if (!button) return [];
            const input = form.querySelector('input[type=number]').getBoundingClientRect();
            const save = button.getBoundingClientRect();
            return [{aligned:Math.abs(input.bottom-save.bottom)<=2, separated:input.right<=save.left,inputBottom:input.bottom,saveBottom:save.bottom,inputRight:input.right,saveLeft:save.left}];
          });
          return {width:innerWidth,height:innerHeight,left:bounds.left,right:bounds.right,top:bounds.top,bottom:bounds.bottom,
            overflow:body.scrollWidth-body.clientWidth,footerBottom:footer.bottom,footerTop:footer.top,
            bodyBottom:body.getBoundingClientRect().bottom,overlaps};
        });
        assert(geometry.left>=0 && geometry.right<=geometry.width && geometry.top>=0 && geometry.bottom<=geometry.height, `${label}: outside viewport`);
        assert(geometry.overflow<=1, `${label}: horizontal overflow`);
        assert(geometry.bodyBottom<=geometry.footerTop+1 && geometry.footerBottom<=geometry.bottom+1, `${label}: footer obscured`);
        assert(geometry.overlaps.every(row=>row.aligned && row.separated), `${label}: inputs and save buttons misaligned ${JSON.stringify(geometry.overlaps)}`);
        results.push({engine:engine.name(),label,...geometry});
      }
      await check('desktop');
      assert.equal(await page.locator('[data-account-action="close"]').evaluate(el=>el===document.activeElement),true);
      if (evidence) await page.screenshot({path:path.join(evidence,`${engine.name()}-account-desktop.png`)});
      await page.locator('[name=maximum]').fill('9');
      await page.evaluate(() => renderAccountDetails());
      assert.equal(await page.locator('[name=maximum]').inputValue(),'9');
      assert.equal(await page.locator('[name=maximum]').evaluate(el=>el===document.activeElement),true,'refresh keeps focus on the maximum field');
      await page.locator('[data-account-action="threshold-help"]').click();
      await page.evaluate(() => renderAccountDetails());
      assert(await page.locator('.account-details-help').evaluate(el=>el.open),'refresh keeps explanation expanded');
      await page.evaluate(() => {document.querySelector('.account-details-body').scrollTop=140;renderAccountDetails();});
      assert.equal(await page.locator('.account-details-body').evaluate(el=>el.scrollTop),140);
      await check('expanded-help-scrolled');
      await page.evaluate(() => {document.querySelector('.account-details-help').open=false;renderAccountDetails();document.querySelector('.account-details-body').scrollTop=0;});
      for (const width of [625,375,320]) {
        await page.setViewportSize({width,height:720});
        await check(`width-${width}`);
        if (width===375 && evidence) await page.screenshot({path:path.join(evidence,`${engine.name()}-account-narrow.png`)});
      }
      await page.evaluate(() => document.documentElement.dataset.portalTheme='dark');
      await check('dark');
      if (evidence) await page.screenshot({path:path.join(evidence,`${engine.name()}-account-dark.png`)});
      await page.evaluate(() => {account.details_scope='model_api';account.execution_accounts=[{...account,account_id:'a-very-long-route-name-with-multiple-parts-for-api-settings'}];renderAccountDetails();});
      await check('long-api-route');
      await page.evaluate(() => {editable=false;renderAccountDetails();});
      assert.equal(await page.locator('form input:enabled').count(),0);
      assert.equal(await page.locator('form button').count(),0);
      await check('viewer');
      await page.keyboard.press('Escape');
      assert(await page.locator('#account-details').isHidden());
      assert(await page.locator('#trigger').evaluate(el=>el===document.activeElement));
      assert.deepEqual(errors,[]);
    } finally {await browser.close();}
  }
  if (evidence) fs.writeFileSync(path.join(evidence,'account-details-results.json'),JSON.stringify(results,null,2));
  console.log(`Account detail layout: ${results.length} cases passed in Chromium and WebKit.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
