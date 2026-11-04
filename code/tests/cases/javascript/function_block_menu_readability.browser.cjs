'use strict';
// Production menu shell, Function Block renderer and trigger rows; isolated data/services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { RESIDENT_MENU_SCRIPTS } = require('./resident_menu_scripts.cjs');
const { HATG_PANEL_STYLES } = require('./hatg_panel_styles.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const evidence = process.env.FUNCTION_BLOCK_MENU_EVIDENCE;
(async () => {
  const browser = await chromium.launch({ headless: true });
  const observations = [], errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
    page.setDefaultTimeout(5000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent('<!doctype html><html lang="zh-CN" data-portal-theme="light"><body></body></html>');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'lineage_workbench', ...HATG_PANEL_STYLES,
      'lineage_function_blocks', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) {
      await page.addStyleTag({ path: path.join(root, name + '.css') });
    }
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives',
      'portal_overlay', 'portal_ui', 'portal_detail_surface', 'lineage_element_fields', ...RESIDENT_MENU_SCRIPTS,
      'hatg_family_blocks', 'hatg_function_block_menu']) {
      await page.addScriptTag({ path: path.join(root, name + '.js') });
    }
    await page.evaluate(() => {
      PortalI18n.apply('zh-CN');
      window.calls = [];
      window.PortalSocietyService = { createClient: () => ({}) };
      window.PortalInstructionClient = { execute: async name => {
        if (name === 'ListUsers') return { users: [] };
        throw Error('Unexpected instruction ' + name);
      }};
      const components = [
        { id: 'event_source_76071b74f59c', title: '运行一次', kind: 'event_source', config: { event_type: 'manual.triggered' } },
        { id: 'event_source_9ee52af9a9b2', title: '定时', kind: 'event_source', config: { event_type: 'timer.interval' } },
      ];
      const wires = ['wire_98053cd08df1', 'wire_a401f3820772'].map(id => ({ id }));
      window.block = { id: 'fbi_123456789abc', title: '定时工作', template_id: 'template_fixture',
        elements: [...components.map(c => ({ id: c.id, kind: 'component' })), ...wires.map(w => ({ id: w.id, kind: 'wire' }))] };
      window.state = { programMutationInFlight: false, lineage: { lineage_id: 'lineage_fixture', revision: 1,
        nodes: [], program: { components, wires }, function_blocks: [block] } };
      const defaults = module => Object.fromEntries(module.dependencies.map(name => [name, () => { throw Error('Unexpected dependency ' + name); }]));
      const family = PortalHatgFamilyBlocks.createFamilyBlocks({ ...defaults(PortalHatgFamilyBlocks),
        state, esc: PortalUIPrimitives.escapeHtml, functionBlockInstanceById: () => block });
      const deps = { ...defaults(PortalHatgFunctionBlockMenu),
        state, esc: PortalUIPrimitives.escapeHtml, familyRoles: () => null,
        functionBlockInstanceById: () => block, residentMenuHandlers: () => ({}),
        functionBlockInstanceFailures: () => [], functionBlockInstanceUnacknowledgedFailures: () => [],
        functionBlockElements: family.functionBlockElements, instanceInnerObjects: family.instanceInnerObjects,
        functionBlockInstanceTriggersHtml: family.functionBlockInstanceTriggersHtml,
        graphObjectTitle: id => components.find(c => c.id === id)?.title || id,
        expandedFunctionBlockIds: () => new Set(), programEventSourceRuntime: () => ({}),
        currentTemplateController: () => ({ find: () => ({ title: '定时工作' }), openEditor: id => calls.push({ source: id }),
          saveSelection: ids => calls.push({ saveTemplate: ids }) }),
        applyProgramMutation: async callback => callback(),
        executeHatgInstruction: async (name, args) => { calls.push({ name, args });
          if (name !== 'UpdateFunctionBlock') throw Error('Unexpected mutation ' + name);
          block.title = args.title; return { lineage: state.lineage }; },
        emitManualProgramEvent: async id => { calls.push({ trigger: id }); },
        setFunctionBlockExpanded: (id, expanded) => calls.push({ expand: id, expanded }),
        toast: () => {},
      };
      window.PortalNav = { openResidentMenu: options => PortalResidentMenu.open(options) };
      window.menu = PortalHatgFunctionBlockMenu.createFunctionBlockMenu(deps);
      window.openFixture = () => menu.openFunctionBlockMenu(block.id);
      return openFixture();
    });
    if (evidence) fs.mkdirSync(evidence, { recursive: true });
    // The same data at desktop, a narrow host and a phone. No business calls.
    for (const [width, theme] of [[1280, 'light'], [734, 'light'], [390, 'light'], [734, 'dark']]) {
      await page.setViewportSize({ width, height: 960 });
      await page.evaluate(theme => document.documentElement.dataset.portalTheme = theme, theme);
      const metrics = await page.locator('.functionBlockMenuPanel').evaluate(panel => {
        const body = panel.closest('.nodeSettingsBody');
        body.scrollTop = 0;
        const r = panel.getBoundingClientRect();
        const input = panel.querySelector('[data-function-block-title]').getBoundingClientRect();
        const save = panel.querySelector('[data-function-block-rename]').getBoundingClientRect();
        const text = [...panel.querySelectorAll('.functionBlockMenuIdentity code, .functionBlockSource h3, .functionBlockSource p, .functionBlockInstanceBindings h3, dt, dd, dd code, .functionBlockTitleEditor label, .functionBlockTitleEditor input, button')];
        const fonts = text.map(e => ({ text: e.textContent.trim().slice(0, 40), size: parseFloat(getComputedStyle(e).fontSize) }));
        const overflows = [...panel.querySelectorAll('*')].filter(e => e.getClientRects().length && e.getBoundingClientRect().width > 0)
          .filter(e => e.getBoundingClientRect().left < r.left - 1 || e.getBoundingClientRect().right > r.right + 1)
          .map(e => e.className || e.tagName);
        body.scrollTop = body.scrollHeight;
        const last = panel.querySelector('[data-function-block-delete]').getBoundingClientRect();
        const br = body.getBoundingClientRect();
        const bottomReachable = last.bottom <= br.bottom + 1 && last.top >= br.top;
        body.scrollTop = 0;
        return { fonts, overflows, bottomReachable, bodyOverflow: body.scrollWidth - body.clientWidth,
          saveWidth: save.width, inputWidth: input.width, panelWidth: r.width,
          wireText: [...panel.querySelectorAll('.functionBlockElementList dd')].at(-1).textContent };
      });
      observations.push({ width, theme, ...metrics });
      assert.ok(metrics.fonts.every(x => x.size >= 13), JSON.stringify(metrics.fonts));
      assert.deepEqual(metrics.overflows, [], 'content stays inside its menu panel');
      assert.ok(metrics.bodyOverflow <= 1 && metrics.bottomReachable, 'one vertical reader, reachable actions');
      assert.ok(metrics.saveWidth < metrics.panelWidth * .65, 'save stays an action, not a full-width bar');
      assert.equal(metrics.wireText, 'wire_a401f3820772', 'untitled identifiers are shown once');
      if (evidence) await page.screenshot({ path: path.join(evidence, width + '-' + theme + '.png') });
    }
    await page.setViewportSize({ width: 1280, height: 960 });
    await page.locator('[data-function-block-title]').fill('定时工作 · 已修改');
    await page.locator('[data-function-block-rename]').click();
    await page.waitForFunction(() => document.querySelector('.functionBlockMenuIdentity strong').textContent === '定时工作 · 已修改');
    assert.deepEqual(await page.evaluate(() => calls[0]), { name: 'UpdateFunctionBlock', args: { function_block_id: 'fbi_123456789abc', title: '定时工作 · 已修改' } });
    await page.locator('[data-function-block-trigger]').click();
    await page.waitForFunction(() => calls.some(x => x.trigger));
    assert.equal(await page.evaluate(() => calls.find(x => x.trigger).trigger), 'event_source_76071b74f59c');
    await page.evaluate(() => openFixture());
    await page.locator('[data-function-block-source]').click();
    await page.waitForFunction(() => calls.some(x => x.source));
    assert.equal(await page.evaluate(() => calls.find(x => x.source).source), 'template_fixture');
    await page.evaluate(() => { state.programMutationInFlight = true; return openFixture(); });
    for (const selector of ['[data-function-block-title]', '[data-function-block-rename]', '[data-function-block-trigger]', '[data-function-block-delete]']) {
      assert.ok(await page.locator(selector).isDisabled(), selector + ' preserves mutation lock');
    }
    assert.deepEqual(errors, []);
    const report = { status: 'passed', observations, errors, behavior: 'rename, manual trigger, source navigation, mutation lock', fixture: 'production renderer/shell with isolated data; no live mutation' };
    if (evidence) fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ status: report.status, views: observations.length, errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
