'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { url } = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('requestfailed', request => errors.push(request.url()));
    for (const version of [1, 2]) {
      await page.goto(url);
      await page.waitForFunction(() => window.PortalPageBoot?.status === 'ready');
      assert.equal(await page.locator('#workspace').innerText(), `ready version ${version}`);
      assert.equal(await page.locator('#portalPageBootError').count(), 0);
    }
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
