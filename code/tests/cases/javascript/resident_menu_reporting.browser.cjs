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
      window.calls = []; window.reject = false; window.readOnly = false; window.failRead = false;
      let reporting = { initiative: 'medium', quality: 'high' }, revision = 7;
      window.PortalResidentService = {
        projection: async () => ({ allows: { UpdateResidentSettings: !readOnly } }),
        runtimeSettings: async () => {
          if (failRead) throw Error('read offline');
          return { settings: { reporting }, settings_revision: revision };
        },
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
    for (const [width, theme, language = 'zh-CN'] of [[1280, 'light'], [734, 'light'], [390, 'light'], [734, 'dark'], [734, 'light', 'en'], [390, 'light', 'en']]) {
      await page.setViewportSize({ width, height: 960 });
      await page.evaluate(theme => { document.documentElement.dataset.portalTheme = theme; }, theme);
      await page.evaluate(async language => {
        if (PortalI18n.locale() === language) return;
        PortalI18n.apply(language);
        menuState.nodeMenuContext = { residentId: 'resident_fixture', family: {} };
        await runtime.loadNodeMenuRuntime();
      }, language);
      await page.locator('.residentMenuReportingForm input').first().waitFor();
      if (language === 'en') {
        assert.equal(await page.locator('.residentReportingLabel').first().textContent(), 'Reporting initiative');
        assert.equal(await page.locator('.residentReportingChoice strong').first().textContent(), 'Off');
        assert.equal(await page.getByRole('button', { name: 'About reporting quality' }).count(), 1);
      }
      const metrics = await page.locator('.residentMenuReportingForm').evaluate(form => {
        const body = form.closest('.nodeSettingsBody'), fr = form.getBoundingClientRect();
        const controls = [...form.querySelectorAll('label')].map(label => {
          const r = label.getBoundingClientRect(), text = label.querySelector('strong');
          return { width: r.width, height: r.height, inside: r.left >= fr.left - 1 && r.right <= fr.right + 1,
            unbroken: text.getBoundingClientRect().height <= parseFloat(getComputedStyle(text).lineHeight) + 1,
            selected: label.querySelector('input').checked, color: getComputedStyle(label).color,
            borderless: parseFloat(getComputedStyle(label).borderWidth) === 0 && getComputedStyle(label).backgroundColor === 'rgba(0, 0, 0, 0)' };
        });
        const fonts = [...form.querySelectorAll('legend, strong, small, p, button')].filter(e => e.textContent.trim())
          .map(e => ({ size: parseFloat(getComputedStyle(e).fontSize), text: e.textContent.slice(0, 35) }));
        const noSaveButton = !form.querySelector('[type=submit]');
        const rows = [...form.querySelectorAll('.residentReportingRow')].map(row => {
          const label = row.querySelector('.residentReportingLabel').getBoundingClientRect();
          const choices = [...row.querySelectorAll('.residentReportingChoice')].map(e => e.getBoundingClientRect());
          const group = row.querySelector('.residentReportingLevels, .residentReportingFormats').getBoundingClientRect();
          return { height: row.getBoundingClientRect().height,
            rightAligned: Math.abs(choices.at(-1).right - group.right) < 1,
            choicesOnOneLine: choices.every(r => Math.abs(r.top - choices[0].top) < 1),
            labelAlongside: label.top >= choices[0].top && label.bottom <= choices[0].bottom };
        });
        return { controls, fonts, rows, noSaveButton, height: fr.height, overflow: body.scrollWidth - body.clientWidth,
          selected: [...form.querySelectorAll('input:checked')].map(e => [e.name, e.value]) };
      });
      observations.push({ width, theme, language, ...metrics });
      assert.ok(metrics.controls.every(c => c.inside && c.height >= 40), 'visible choices fit the content column');
      assert.ok(metrics.controls.every(c => c.unbroken), 'short option labels do not break inside words');
      assert.ok(metrics.fonts.every(f => f.size >= 13), 'all explanatory text stays legible');
      assert.ok(metrics.overflow <= 1, 'no outer horizontal scroll');
      assert.deepEqual(metrics.selected, [['initiative', 'medium'], ['quality', 'high']]);
      assert.ok(metrics.controls.every(c => c.borderless), 'choices have no surrounding border or card fill');
      assert.notEqual(metrics.controls[0].color, metrics.controls[2].color, 'selected level retains a visible accent');
      assert.ok(metrics.noSaveButton, 'changes save automatically without a manual save button');
      assert.ok(await page.locator('[data-reporting-retry]').isHidden(), 'retry is only shown when needed');
      assert.equal(metrics.rows.length, 2);
      assert.ok(metrics.rows.every(r => r.rightAligned), 'each option group aligns with the right edge');
      if (language !== 'en' || width >= 734) assert.ok(metrics.rows.every(r => r.choicesOnOneLine), 'each group remains a single compact option row');
      if (width >= 734) assert.ok(metrics.rows.every(r => r.labelAlongside && r.height <= 48), `normal widths show two compact settings rows: ${JSON.stringify({ width, language, rows: metrics.rows })}`);
      assert.ok(metrics.height < (width >= 734 ? 260 : 390), 'collapsed explanations do not occupy the settings card');
      assert.equal(await page.locator('.residentReportingHelp[aria-expanded=false]').count(), 2);
      if (evidence) await page.screenshot({ path: path.join(evidence, `${width}-${theme}${language === 'en' ? '-en' : ''}.png`) });
    }
    await page.evaluate(async () => {
      PortalI18n.apply('zh-CN');
      menuState.nodeMenuContext = { residentId: 'resident_fixture', family: {} };
      await runtime.loadNodeMenuRuntime();
    });
    await page.locator('.residentMenuReportingForm input').first().waitFor();
    await page.setViewportSize({ width: 390, height: 960 });
    const initiativeHelp = page.getByRole('button', { name: '汇报主动性说明' });
    await initiativeHelp.click();
    assert.ok(await page.locator('#resident-reporting-initiative-note').isVisible(), 'help expands in narrow layout');
    await initiativeHelp.focus(); await page.keyboard.press('Enter');
    assert.ok(await page.locator('#resident-reporting-initiative-note').isHidden(), 'help collapses with keyboard');
    const qualityHelp = page.getByRole('button', { name: '汇报质量说明' });
    await qualityHelp.click();
    assert.ok(await page.locator('#resident-reporting-quality-note').isVisible());
    assert.match(await page.locator('#resident-reporting-quality-note').textContent(), /独立 Resident 审查与修改直至达标/);
    assert.equal(await page.evaluate(() => calls.length), 0, 'help never submits a setting change');
    if (evidence) await page.screenshot({ path: path.join(evidence, '390-help.png') });
    await qualityHelp.click();
    await page.setViewportSize({ width: 1280, height: 960 });
    const medium = page.locator('[name=initiative][value=medium]');
    await medium.focus(); await page.keyboard.press('ArrowRight');
    assert.ok(await page.locator('[name=initiative][value=high]').isChecked(), 'native keyboard navigation');
    await page.waitForFunction(() => calls.length === 1 && document.querySelector('[data-reporting-status]').textContent.includes('已保存'));
    assert.deepEqual(await page.evaluate(() => calls[0].changes), { reporting: { initiative: 'high', quality: 'high' } });
    assert.equal(await page.evaluate(() => calls[0].expected), 7);
    await page.locator('[name=quality][value=low]').check();
    await page.waitForFunction(() => calls.length === 2 && document.querySelector('[data-reporting-status]').textContent.includes('已保存'));
    assert.deepEqual(await page.evaluate(() => calls[1].changes), { reporting: { initiative: 'high', quality: 'low' } });
    assert.equal(await page.evaluate(() => calls[1].expected), 8);
    await page.evaluate(() => { reject = true; });
    await page.locator('[name=quality][value=high]').check();
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]').textContent.includes('保存失败'));
    assert.ok(await page.locator('[name=quality][value=high]').isChecked(), 'failure keeps edited choice');
    assert.ok(await page.locator('[data-reporting-retry]').isVisible(), 'failed automatic save offers an explicit retry');
    await page.evaluate(() => { reject = false; });
    await page.locator('[data-reporting-retry]').click();
    await page.waitForFunction(() => calls.length === 4 && document.querySelector('[data-reporting-status]').textContent.includes('已保存'));
    assert.ok(await page.locator('[data-reporting-retry]').isHidden());
    await page.evaluate(() => { failRead = true; });
    await page.locator('[name=quality][value=medium]').check();
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]').textContent.includes('设置已提交'));
    assert.ok(await page.locator('[name=quality][value=medium]').isDisabled());
    await page.evaluate(() => { failRead = false; });
    await page.getByRole('button', { name: '重试读取', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]').textContent.includes('已保存'));
    assert.equal(await page.evaluate(() => calls.length), 5, 'readback retry does not write again');
    await page.evaluate(() => {
      readOnly = true; menuState.nodeMenuContext = { residentId: 'readonly_fixture', family: {} };
      return runtime.loadNodeMenuRuntime();
    });
    await page.waitForFunction(() => document.querySelector('[data-reporting-status]')?.textContent === '无权限');
    for (const radio of await page.locator('.residentMenuReportingForm input').all()) assert.ok(await radio.isDisabled());
    await page.getByRole('button', { name: '汇报质量说明' }).click();
    assert.ok(await page.locator('#resident-reporting-quality-note').isVisible(), 'read-only viewers can still read help');
    assert.deepEqual(errors, []);
    if (evidence) fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify({ status: 'passed', observations, errors,
      behavior: ['borderless right-aligned options', 'automatic save on radio change', 'two compact rows', 'narrow layout', 'expandable help without writes', 'English labels', 'native radio keys', 'CAS save/readback', 'failed save retry', 'readback retry without rewrite', 'read-only'],
      boundary: 'Real menu shell and reporting component; local service fixture, no user setting changed' }, null, 2));
    console.log(JSON.stringify({ status: 'passed', views: observations.length, errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
