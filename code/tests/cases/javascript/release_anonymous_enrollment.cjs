const assert = require('node:assert/strict');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const errors = [], responses = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', response => {
      if (new URL(response.url()).pathname === '/static/portal_sha256.js')
        responses.push({status: response.status(), type: response.headers()['content-type']});
    });
    await page.goto(`${process.argv[2]}/join#invite=fixture-invitation`);
    await page.locator('[data-step="human"]').waitFor({state: 'visible', timeout: 10000});
    assert.deepEqual(errors, []);
    assert.equal(responses.length, 1);
    assert.equal(responses[0].status, 200);
    assert.match(responses[0].type, /javascript/);
    assert.equal(await page.evaluate(() => location.hash), '');
    await page.locator('[name="password"]').fill('Fixture-only-password-123!');
    await page.locator('[name="confirm"]').fill('Fixture-only-password-123!');
    await page.locator('[data-step="human"] button[type="submit"]').click();
    await page.locator('[data-done="human"]').waitFor({state: 'visible', timeout: 10000});
    assert.equal(await page.locator('[data-done="human"] [data-done-admin]').isVisible(), true);
    assert.deepEqual(errors, []);
    await page.goto(`${process.argv[2]}/login`);
    assert.deepEqual(errors, []);
    assert.equal(await page.evaluate(() => Boolean(window.PortalOverlay)), true,
      JSON.stringify({url: page.url(), title: await page.title(), scripts: await page.locator('script[src]').evaluateAll(items => items.map(item => item.src))}));
    console.log('PASS real public routes, module MIME, first-admin invitation UI and submission');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
