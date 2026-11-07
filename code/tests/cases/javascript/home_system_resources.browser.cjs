const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 945, height: 1000}});
    await page.setContent('<main id="resources" class="agent-status" style="padding:16px"></main>');
    await page.addStyleTag({path: path.join(root, 'management_portal/static/home.css')});
    await page.evaluate(() => {
      const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
      window.PortalI18n = {t: value => value, html: tag, text: tag};
      window.PortalUI = {formatBytes: value => `${Number(value) / 1024 ** 3} GiB`};
    });
    await page.addScriptTag({path: path.join(root, 'management_portal/static/home_system_resources.js')});
    await page.evaluate(() => {
      window.renderer = window.PortalHomeSystemResources.createSystemResources({
        esc: value => {const el = document.createElement('span'); el.textContent = value ?? ''; return el.innerHTML;},
        formatDateTime: value => value || '',
      });
    });
    const healthy = {
      resource_id: 'nuc_storage', display_name: 'nuc', status: 'ok', updated_at: '2026-11-07 16:00',
      filesystems: [{filesystem: '/dev/test', mount: '/', status: 'ok', used_percent: 5, available_bytes: 100 * 1024 ** 3}],
      stages: {capacity: {status: 'ok'}, suggestions: {status: 'ok'}},
      triage: {findings: [{title: 'Diagnostic detail'}]}, suggestions: ['Transfer suggestion'],
    };
    const render = resources => page.evaluate(resources => {
      document.querySelector('#resources').innerHTML = window.renderer.diskDetailsHtml({
        status: 'ok', resources, filesystems: resources.flatMap(r => r.filesystems || []),
      });
    }, resources);
    const card = id => page.locator(`[data-disk-resource="${id}"]`);
    await render([healthy]);
    assert.match(await card('nuc_storage').innerText(), /nuc.*\nok/s);
    assert.equal(await page.locator('.agent-status-progress').count(), 0);
    assert.doesNotMatch(await page.locator('#resources').innerText(), /\/dev\/test|Diagnostic detail|Transfer suggestion|External targets/);
    for (const status of ['warn', 'critical']) {
      const alert = {...healthy, resource_id: 'alert_storage', display_name: 'Alert disk', status,
        filesystems: [{...healthy.filesystems[0], status, used_percent: 95}]};
      await render([healthy, alert]);
      assert.equal(await card('nuc_storage').locator('.agent-status-progress').count(), 0);
      assert.equal(await card('alert_storage').locator('.agent-status-progress').count(), 1);
      assert.match(await card('alert_storage').innerText(), /Diagnostic detail/);
      assert.match(await card('alert_storage').innerText(), /Transfer suggestion/);
      if (status === 'warn') {
        for (const width of [945, 625]) {
          await page.setViewportSize({width, height: 1000});
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
          const out = '/tmp/resident84-disk-alerts'; fs.mkdirSync(out, {recursive: true});
          await page.screenshot({path: `${out}/${width}.png`, fullPage: true});
        }
      }
    }
    await render([{...healthy, status: 'partial', stale: true, refresh_error: 'Connection failed',
      filesystems: [{...healthy.filesystems[0], status: 'warn'}]}]);
    assert.equal(await page.locator('.agent-status-progress').count(), 1);
    assert.match(await page.locator('#resources').innerText(), /Connection failed/);
    await render([{...healthy, status: 'error', error: 'Connection failed', filesystems: []}]);
    assert.equal(await page.locator('.agent-status-progress').count(), 0);
    assert.match(await page.locator('#resources').innerText(), /unavailable.*Connection failed/s);
    await render([healthy]);
    assert.equal(await page.locator('.agent-status-progress').count(), 0, 'recovery hides old details');
    console.log('Home disk rendering passed: healthy, alerts, recovery, stale/error and narrow layout');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
