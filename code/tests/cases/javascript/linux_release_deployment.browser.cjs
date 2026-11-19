/* Real browser acceptance for fixtures/linux_release_deployment.py.
 * No saved browser profile, production session, model call or mocked HTTP.
 * Bootstrap secrets stay in a mode-0600 file under the disposable fixture.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require(process.argv[5] || process.env.PLAYWRIGHT_MODULE || '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');

async function main() {
  const base = path.resolve(process.argv[2] || '');
  const phase = process.argv[3];
  assert(['bootstrap', 'exercise', 'restart'].includes(phase), 'Expected bootstrap, exercise or restart');
  const state = JSON.parse(fs.readFileSync(path.join(base, 'fixture.json')));
  assert.equal(state.base, base);
  assert.equal(state.root, path.join(base, 'instance'));
  assert.match(state.name, /^tau-linux-[0-9a-f]{10}$/);
  const instance = JSON.parse(fs.readFileSync(path.join(state.root, 'instance.json')));
  assert.equal(instance.name, state.name);
  const origin = `http://127.0.0.1:${instance.network.entry_port}`;
  const label = process.argv[4] || `browser-${phase}`;
  assert.match(label, /^[a-z0-9-]+$/);
  const evidence = path.join(base, 'evidence', label);
  fs.mkdirSync(evidence, {recursive: false});
  const credentials = path.join(base, 'browser-credentials.json');
  let account;
  if (phase === 'bootstrap') {
    account = {username: 'linux_tau_admin', password: `Tau!A9-${crypto.randomBytes(24).toString('hex')}`};
    fs.writeFileSync(credentials, JSON.stringify(account), {mode: 0o600, flag: 'wx'});
  } else {
    account = JSON.parse(fs.readFileSync(credentials));
  }
  const result = {phase, origin, release_commit: state.release_commit, checks: [], page_errors: []};
  const redact = value => String(value).split(account.password).join('[redacted-password]')
    .replace(/#invite=\S+/g, '#invite=[redacted]');
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => result.page_errors.push(redact(error.message)));
  async function getJson(route) {
    const response = await context.request.get(origin + route);
    assert.equal(response.status(), 200, route);
    result.checks.push({route, status: response.status()});
    return response.json();
  }
  try {
    if (phase === 'bootstrap') {
      const invitation = fs.readFileSync(path.join(state.root, 'secrets', 'first-admin-link.txt'), 'utf8');
      const link = invitation.match(/https?:\/\/\S+\/join#invite=\S+/)?.[0];
      assert(link && new URL(link).origin === origin, 'Invitation must belong to this fixture');
      await page.goto(link);
      await page.locator('input[name="password"]').waitFor({state: 'visible'});
      await page.locator('input[name="password"]').fill(account.password);
      await page.locator('input[name="confirm"]').fill(account.password);
      const accepted = page.waitForResponse(r => r.url().endsWith('/api/auth/invitations/accept'));
      await page.locator('[data-human-submit]').click();
      const response = await accepted;
      assert.equal(response.status(), 201);
      const joined = await response.json();
      assert.equal(joined.username, account.username);
      assert(joined.administrator, 'First account must be the administrator');
      await page.locator('[data-done="human"]').waitFor({state: 'visible'});
      result.checks.push({first_administrator: true, username: account.username});
      await page.screenshot({path: path.join(evidence, 'registered.png')});
    }
    await page.goto(origin + '/login');
    await page.locator('#usr').fill(account.username);
    await page.locator('#pwd').fill(account.password);
    await Promise.all([
      page.waitForURL(url => url.pathname !== '/login'),
      page.locator('button[type="submit"]').click(),
    ]);
    const me = await getJson('/api/me');
    assert.equal(me.username, account.username);
    assert.equal(me.principal.kind, 'user');
    await page.screenshot({path: path.join(evidence, 'home.png')});
    const catalog = await getJson('/api/resource-index/query?limit=20&type=decision');
    const items = catalog.items || catalog.results || catalog.resources;
    assert(Array.isArray(items) && items.length > 0, 'Example seed must contain readable Design resources');
    const address = items[0].id || items[0].resource_id;
    assert(address, 'Resource has a canonical ID');
    const resolved = await getJson('/api/resources/resolve?address=' + encodeURIComponent(address));
    assert.equal(resolved.id || resolved.resource_id, address);
    const content = await context.request.get(origin + '/api/resources/content?address=' + encodeURIComponent(address));
    assert.equal(content.status(), 200);
    const body = await content.body();
    assert(body.length > 100, 'Seed content must actually be readable');
    result.checks.push({resource: address, bytes: body.length, sha256: crypto.createHash('sha256').update(body).digest('hex')});
    const url = resolved.portal_url || `/resource/knowledge?item=${encodeURIComponent(address)}`;
    assert.equal(new URL(url, origin).origin, origin);
    assert.equal((await page.goto(new URL(url, origin).href)).status(), 200);
    await page.waitForFunction(() => document.body.innerText.trim().length > 1500
      && document.querySelectorAll('h1,h2').length >= 2);
    await page.screenshot({path: path.join(evidence, 'resource.png')});
    if (phase !== 'restart') {
      const response = await context.request.post(origin + '/api/resources/favorites', {data: {address, favorite: true}});
      assert.equal(response.status(), 200);
      fs.writeFileSync(path.join(base, 'resource-exercise.json'), JSON.stringify({address}));
    } else {
      const original = JSON.parse(fs.readFileSync(path.join(base, 'resource-exercise.json')));
      const favorites = await getJson('/api/resources/favorites');
      assert(JSON.stringify(favorites).includes(original.address), 'Favorite must survive a stop/start');
      result.checks.push({resource_state_persisted: true, address: original.address});
    }
    assert.deepEqual(result.page_errors, [], 'Browser must have no uncaught errors');
    result.passed = true;
  } catch (error) {
    // Do not include URLs, request bodies, passwords or invitation fragments.
    result.passed = false;
    result.error = {name: error.name, message: redact(error.message)};
    throw new Error(result.error.message);
  } finally {
    fs.writeFileSync(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2));
    await context.close();
    await browser.close();
  }
  console.log(JSON.stringify({phase, passed: result.passed, checks: result.checks.length}));
}
main().catch(error => { console.error(error.name + ': ' + error.message.replace(/#invite=\S+/g, '#invite=[redacted]')); process.exitCode = 1; });
