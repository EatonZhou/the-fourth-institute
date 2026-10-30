'use strict';
// Native form, UI controller, translations and ISA client; isolated transport only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const portal = path.resolve(__dirname, '../../../management_portal');
const { chromium } = require(path.join(portal, 'test_framework/tau_runtime/node_modules/playwright'));
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const { HATG_PANEL_STYLES } = require('./hatg_panel_styles.cjs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [width, language] of [[1280, 'zh-CN'], [390, 'en']]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const requests = [], errors = [];
      page.on('request', request => requests.push(request.url()));
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => route.abort());
      await page.setContent('<!doctype html><section class="portalHatgPanelComponent">'
        + fs.readFileSync(path.join(portal, 'hatg-panel.html'), 'utf8') + '</section>');
      for (const name of ['portal_design_system', 'portal_themes', ...PORTAL_COMPONENTS_STYLES, ...HATG_PANEL_STYLES]) {
        await page.addStyleTag({ path: path.join(portal, 'static', name + '.css') });
      }
      await page.evaluate(lang => { window.__PORTAL_LANGUAGE__ = lang; }, language);
      for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n',
        'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_detail_surface',
        'portal_society_service', 'hatg_lineage_dialogs']) {
        await page.addScriptTag({ path: path.join(portal, 'static', name + '.js') });
      }
      await page.evaluate(language => {
        PortalI18n.apply(language);
        window.calls = [];
        window.userReads = 0;
        window.PortalLineageCatalogs = { users() { userReads++; throw new Error('Unexpected User directory read'); } };
        const modal = document.getElementById('lineageModal');
        const controller = PortalUI.createDialogController({ modal, openClass: 'portalDialogOpen',
          bodyClass: 'agentLineageModalOpen', onOpen: () => { modal.hidden = false; },
          onClose: () => { modal.hidden = true; } });
        const state = { projects: [{ id: 'project_fixture', name: 'Fixture project' }], lineages: [], localVersion: 0,
          lineage: { lineage_id: 'lineage_123456789abc', project_id: 'project_fixture', revision: 4,
            title: 'Existing graph', nodes: [{ id: 'resident_123456789abc', title: 'Leader' }],
            leader_resident_id: 'resident_123456789abc', run_as_principal: { kind: 'user', id: 'legacy-user' } } };
        const societyService = PortalSocietyService.createClient({ execute: async (name, args, controls) => {
          calls.push({ name, args, controls });
          if (name === 'CreateLineage') return { lineage_id: 'lineage_123456789abc' };
          if (name === 'UpdateLineage') { Object.assign(state.lineage, args.changes); return {}; }
          if (name === 'GetLineage') return { lineage: structuredClone(state.lineage) };
          throw new Error('Unexpected instruction ' + name);
        } });
        const owner = PortalHatgLineageDialogs;
        const dependencies = Object.fromEntries(owner.dependencies.map(name => [name, () => {
          throw new Error('Unexpected dependency ' + name);
        }]));
        Object.assign(dependencies, { $id: id => document.getElementById(id), state, societyService,
          clone: structuredClone, esc: PortalUIPrimitives.escapeHtml,
          openModal: () => controller.open(), closeModal: () => controller.close(),
          setBusy: (button, busy) => { button.disabled = busy; }, toast() {},
          selectLineage: async () => {}, openNodeModal() {}, updateLineageSummary() {}, renderLineageSurface() {} });
        const dialogs = owner.createLineageDialogs(dependencies);
        document.getElementById('lineageForm').addEventListener('submit', dialogs.submitLineageForm);
        window.openCreate = () => dialogs.openCreateLineageModal('project_fixture');
        window.openEdit = () => dialogs.openEditLineageModal();
      }, language);

      await page.evaluate(() => openCreate());
      const form = page.locator('#lineageForm');
      assert.equal(await page.locator('#lineageRunAsInput').count(), 0);
      assert.equal(await page.locator('#lineageLeaderField').isVisible(), false);
      await page.locator('#lineageNameInput').fill('Created in browser');
      await page.locator('#lineageFamilizeInput').uncheck();
      if (process.env.LINEAGE_LEADER_EVIDENCE_DIR) {
        fs.mkdirSync(process.env.LINEAGE_LEADER_EVIDENCE_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.LINEAGE_LEADER_EVIDENCE_DIR, `create-${width}.png`) });
      }
      await page.locator('#lineageSubmitBtn').click();
      await page.waitForFunction(() => document.getElementById('lineageModal').hidden);
      const create = await page.evaluate(() => calls.find(call => call.name === 'CreateLineage'));
      assert.equal(create.args.title, 'Created in browser');
      assert.equal(create.args.familize, false);
      assert.equal(Object.hasOwn(create.args, 'definition'), false);
      assert.equal(Object.hasOwn(create.args, 'run_as_principal'), false);

      await page.evaluate(() => openEdit());
      assert.equal(await page.locator('#lineageLeaderField').isVisible(), true);
      const hint = await page.locator('#lineageLeaderField small').textContent();
      assert.match(hint, language === 'en' ? /identity and permissions/ : /身份和权限/);
      assert.equal((await form.textContent()).includes('legacy-user'), false);
      await page.locator('#lineageNameInput').fill('Edited in browser');
      await page.locator('#lineageSubmitBtn').click();
      await page.waitForFunction(() => document.getElementById('lineageModal').hidden);
      const update = await page.evaluate(() => calls.find(call => call.name === 'UpdateLineage'));
      assert.equal(update.args.changes.title, 'Edited in browser');
      assert.equal(update.args.changes.leader_resident_id, 'resident_123456789abc');
      assert.equal(update.controls.expectedVersion, 4);
      assert.equal(Object.hasOwn(update.args.changes, 'run_as_principal'), false);
      assert.equal(await page.evaluate(() => userReads), 0);
      assert.deepEqual(requests, []);
      assert.deepEqual(errors, []);
      console.log(`PASS: native create/edit form at ${width}px (${language}), no identity selector/User fetch/run-as payload`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
