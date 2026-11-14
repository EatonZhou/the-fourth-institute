'use strict';
const path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const {chromium} = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.setContent('<main></main>');
    await page.addScriptTag({path: path.join(root, 'static/lineage_templates_resident_settings.js')});
    await page.evaluate(() => {
      window.PortalLineageCatalogs = {routeRuntimeCatalog: () => ({models: ['kimi-code/k3'], default_model: 'kimi-code/k3'})};
      const optionsHtml = (items, value) => items.map(item => {
        const v = typeof item === 'string' ? item : item.value;
        return `<option value="${v}" ${v === value ? 'selected' : ''}>${item.label || v}</option>`;
      }).join('');
      const renderer = LineageTemplatesResidentSettings.createResidentSettings({
        clone: structuredClone, esc: x => String(x || ''), t: x => x, optionsHtml,
        getContext: () => ({residents: []}), config: {residentSettingsOptions: () => ({backends: [
          {backend: 'kimi', label: 'Kimi', runtime_settings: ['model'], routes: [{backend_route_id: 'kimi-0', label: 'Kimi'}]},
        ]})},
      });
      window.choices = renderer.renderResidentSettings(document.querySelector('main'),
        [{key: 'router', from: 'new', title: 'Router'}], {}, {kind: 'familize'});
    });
    const provider = page.locator('[data-setting="provider"]');
    assert.equal(await provider.inputValue(), '');
    assert.equal(await page.locator('[data-setting="account"]').isDisabled(), true);
    assert.deepEqual(await page.evaluate(() => choices.router), {account: '', settings: {}});
    await provider.selectOption('kimi');
    await page.locator('[data-setting="account"]').selectOption('kimi-0');
    await page.locator('[data-setting="model"]').selectOption('kimi-code/k3');
    assert.deepEqual(await page.evaluate(() => choices.router), {provider: 'kimi', account: 'kimi-0', settings: {model: 'kimi-code/k3'}});
    await provider.selectOption('');
    assert.deepEqual(await page.evaluate(() => choices.router), {account: '', settings: {}});
    assert.equal(await page.locator('[data-setting="model"]').isDisabled(), true);
    console.log('Family site default, explicit Kimi and reset preserve submitted intent');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
