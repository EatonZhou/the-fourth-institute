'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { RESIDENT_MENU_SCRIPTS } = require('./resident_menu_scripts.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const evidence = process.env.REPORTING_MENU_EVIDENCE;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
    page.setDefaultTimeout(5000);
    const errors = [], observations = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent('<!doctype html><html lang="zh-CN" data-portal-theme="light"><body></body></html>');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'lineage_workbench', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) {
      await page.addStyleTag({ path: path.join(root, name + '.css') });
    }
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives',
      'portal_overlay', 'portal_ui', 'portal_detail_surface', ...RESIDENT_MENU_SCRIPTS]) {
      await page.addScriptTag({ path: path.join(root, name + '.js') });
    }
    await page.evaluate(async () => {
      PortalI18n.apply('zh-CN');
      window.PortalSocietyService = { createClient: () => ({}) };
      window.PortalInstructionClient = { execute: async name => {
        if (name !== 'ListUsers') throw Error('Unexpected instruction ' + name);
        return { users: [] };
      }};
      window.shell = await PortalResidentMenu.open({ lineage: { lineage_id: 'fixture', nodes: [] },
        group: { active: 'fixture', tabs: [{ key: 'fixture', label: '设置' }], render() {} } });
      document.getElementById('nodeMenuGroupPanel').hidden = true;
      document.getElementById('nodeDisplayPanel').hidden = false;
      document.getElementById('nodeSettingsForm').hidden = true;
      // Keep the real Menu layout and real reporting renderer. Only service data is local.
      window.calls = []; window.reject = false; window.readOnly = false;
      let reporting = { initiative: 'medium', quality: 'high' }, revision = 7;
      window.PortalResidentService = {
        projection: async () => ({ allows: { UpdateResidentSettings: !readOnly } }),
        runtimeSettings: async () => ({ settings: { reporting }, settings_revision: revision }),
        updateRuntimeSettings: async (id, changes, key, expected) => {
          calls.push({ id, changes, key, expected });
          if (reject) throw Object.assign(Error('保存失败，请重试'), { status: 403 });
          if (expected !== revision) throw Error('Unexpected revision');
          reporting = { ...changes.reporting }; revision++;
        },
      };
      window.menuState = { nodeMenuContext: { residentId: 'resident_fixture', family: {} } };
      window.runtime = PortalResidentMenuRuntime.createRuntime({
        $id: id => document.getElementById(id), esc: PortalUIPrimitives.escapeHtml,
        menuState, nodeMenuNotice() {}, renderFamilySettings() {},
      });
      await runtime.loadNodeMenuRuntime();
    });
    await page.locator('.residentMenuReportingForm input').first().waitFor();
    if (evidence) fs.mkdirSync(evidence, { recursive: true });
    for (const [width, theme] of [[1280, 'light'], [734, 'light'], [390, 'light'], [734, 'dark']]) {
      await page.setViewportSize({ width, height: 960 });
      await page.evaluate(theme => { document.documentElement.dataset.portalTheme = theme; }, theme);
      const metrics = await page.locator('.residentMenuReportingForm').evaluate(form => {
        const body = form.closest('.nodeSettingsBody'), fr = form.getBoundingClientRect();
        const controls = [...form.querySelectorAll('label')].map(label => {
          const r = label.getBoundingClientRect();
          return { width: r.width, height: r.height, inside: r.left >= fr.left - 1 && r.right <= fr.right + 1,
            selected: label.querySelector('input').checked, border: getComputedStyle(label).borderColor };
        });
        const fonts = [...form.querySelectorAll('legend, strong, small, p, button')].filter(e => e.textContent.trim())
          .map(e => ({ size: parseFloat(getComputedStyle(e).fontSize), text: e.textContent.slice(0, 35) }));
        body.scrollTop = body.scrollHeight;
        const submit = form.querySelector('[type=submit]').getBoundingClientRect();
        const bottomReachable = submit.bottom <= body.getBoundingClientRect().bottom + 1;
        body.scrollTop = 0;
        return { controls, fonts, overflow: body.scrollWidth - body.clientWidth, bottomReachable,
          selected: [...form.querySelectorAll('input:checked')].map(e => [e.name, e.value]) };
      });
      observations.push({ width, theme, ...metrics });
      assert.ok(metrics.controls.every(c => c.inside && c.height >= 40), 'visible choices fit the content column');
      assert.ok(metrics.fonts.every(f => f.size >= 13), 'all explanatory text stays legible');
      assert.ok(metrics.overflow <= 1 && metrics.bottomReachable, 'no outer horizontal scroll and save stays reachable');
      assert.deepEqual(metrics.selected, [['initiative', 'medium'], ['quality', 'high']]);
      assert.notEqual(metrics.controls[0].border, metrics.controls[2].border, 'selected level has a visible border');
      if (evidence) await page.screenshot({ path: path.join(evidence, `${width}-${theme}.png`) });
    }
    await page.setViewportSize({ width: 1280, height: 960 });
    const medium = page.locator('[name=initiative][value=medium]');
    await medium.focus(); await page.keyboard.press('ArrowRight');
    assert.ok(await page.locator('[name=initiative][value=high]').isChecked(), 'native keyboard navigation');
    await page.locator('[name=quality][value=low]').check();
    await page.locator('.residentMenuReportingForm [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]').textContent.includes('已保存'));
    assert.deepEqual(await page.evaluate(() => calls[0].changes), { reporting: { initiative: 'high', quality: 'low' } });
    assert.equal(await page.evaluate(() => calls[0].expected), 7);
    await page.evaluate(() => { reject = true; });
    await page.locator('[name=quality][value=high]').check();
    await page.locator('.residentMenuReportingForm [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]').textContent.includes('保存失败'));
    assert.ok(await page.locator('[name=quality][value=high]').isChecked(), 'failure keeps edited choice');
    await page.evaluate(() => {
      readOnly = true; menuState.nodeMenuContext = { residentId: 'readonly_fixture', family: {} };
      return runtime.loadNodeMenuRuntime();
    });
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]')?.textContent === '无权限');
    for (const radio of await page.locator('.residentMenuReportingForm input').all()) assert.ok(await radio.isDisabled());
    assert.deepEqual(errors, []);
    if (evidence) fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify({ status: 'passed', observations, errors,
      behavior: ['native radio keys', 'CAS save/readback', 'failed save draft', 'read-only'],
      boundary: 'Real menu shell and reporting component; local service fixture, no user setting changed' }, null, 2));
    console.log(JSON.stringify({ status: 'passed', views: observations.length, errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
