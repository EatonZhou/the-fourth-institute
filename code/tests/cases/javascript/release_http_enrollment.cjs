const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // A named HTTP origin reproduces deployed LAN behavior, unlike localhost.
    await page.route('http://release.test/**', route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (!name) return route.fulfill({ contentType: 'text/html', body: '<main></main>' });
      if (!['portal_user_enrollment.js', 'portal_sha256.js', 'portal_ui.js'].includes(name))
        return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(root, name), 'utf8') });
    });
    await page.goto('http://release.test/');
    const values = ['', 'abc', '中文🧩', ...[55, 56, 63, 64, 65, 1000, 1000000].map(n => 'a'.repeat(n))];
    const result = await page.evaluate(async values => {
      const { secretDigest, newSecret, mountEnrollment } = await import('/portal_user_enrollment.js');
      const digests = [];
      for (const value of values) digests.push(await secretDigest(value));
      const random = [newSecret(), newSecret()];
      window.calls = [];
      window.PortalI18n = { t: s => s, html: (s, ...v) => s.reduce((a, b, i) => a + b + (v[i] ?? ''), '') };
      await import('/portal_ui.js');
      window.copyFallbackCalls = [];
      const execCommand = document.execCommand.bind(document);
      document.execCommand = (command, ...args) => {
        window.copyFallbackCalls.push(command);
        return execCommand(command, ...args);
      };
      window.PortalInstructionClient = { execute: async (name, args) => {
        window.calls.push({ name, args });
        return { invitations: [] };
      } };
      mountEnrollment(document.querySelector('main'), { invitations: true });
      document.querySelector('details').open = true;
      return { secure: isSecureContext, subtle: !!crypto.subtle, digests, random };
    }, values);
    assert.equal(result.secure, false);
    assert.equal(result.subtle, false);
    assert.deepEqual(result.digests, values.map(v => createHash('sha256').update(v).digest('hex')));
    assert.notEqual(result.random[0], result.random[1]);
    assert.match(result.random[0], /^[A-Za-z0-9_-]{43}$/);
    await page.locator('[name=name]').fill('release-invite-test');
    await page.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[role=status]').textContent === 'Invitation created.');
    const submitted = await page.evaluate(() => ({
      call: window.calls.find(c => c.name === 'CreateUserInvitation'),
      link: document.querySelector('.enrollmentSecret textarea').value,
    }));
    const secret = new URLSearchParams(new URL(submitted.link).hash.slice(1)).get('invite');
    assert.equal(submitted.call.args.secret_sha256, createHash('sha256').update(secret).digest('hex'));
    assert.equal(JSON.stringify(submitted.call).includes(secret), false, 'raw secret stays in the browser');
    assert.equal(submitted.call.args.user_id, 'release-invite-test');
    await page.locator('[data-copy]').click();
    await page.waitForFunction(() => document.querySelector('[role=status]').textContent === 'Copied.');
    assert.deepEqual(await page.evaluate(() => window.copyFallbackCalls), ['copy']);
    const native = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(root, 'portal_sha256.js')).toString('base64'));
    for (const value of values.slice(0, 4)) {
      assert.equal(await native.sha256(new TextEncoder().encode(value)), createHash('sha256').update(value).digest('hex'));
    }
    console.log('PASS LAN HTTP source enrollment UI, random secrets, SHA-256 vectors, hashed-only request');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
