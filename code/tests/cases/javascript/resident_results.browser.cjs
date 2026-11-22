'use strict';
// Single browser, sequential cases. All HTTP is intercepted: no live Portal,
// model invocation, business creation or author message leaves this fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const portal = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(portal, 'script_groups.json'), 'utf8'));
let html = fs.readFileSync(path.join(portal, 'resident_results.html'), 'utf8');
html = html.replace(/<script data-portal-script-group="([^"]+)"><\/script>/g, (_, group) =>
  // Navigation is independently tested; exercise the actual page, language,
  // theme, identity client and authenticated request client here.
  group === 'portal-shell' ? '' : groups[group].map(url => `<script src="${url}"></script>`).join(''));
const id = 'deliverable_111111111111', other = 'deliverable_222222222222';
const hostile = '<img src=x onerror="window.resultsXss=1">';
let latest = 2, status = 'published', conflict = false, createFails = false, contactFails = false;
const calls = [], errors = [];
function detail(identifier, revision) {
  return {record: {resource_id: identifier, title: hostile, summary: 'Summary', author_id: 'resident_aaaaaaaaaaaa', project_id: 'Project & one',
    status, revision: latest, updated_at: '2026-11-22T00:00:00Z', can_edit: true, portal_url: 'javascript:alert(1)'},
  revision, latest_revision: latest,
  snapshot: {title: hostile, summary: 'Summary', body: 'Body ' + hostile, validation: 'Only fixture checks passed', limitations: 'No production check',
    documents: [{name: hostile + '.md', content: hostile, sha256: 'a'.repeat(64)}],
    references: [{address: 'article_aaaaaaaaaaaa', version: '4', sha256: 'b'.repeat(64)}]}};
}
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, locale: 'zh-CN'});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.hostname !== 'results.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        return route.fulfill({body: fs.readFileSync(path.join(portal, url.pathname.slice(1))),
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      }
      if (url.pathname.startsWith('/api/results')) {
        const body = request.method() === 'GET' ? null : request.postDataJSON();
        calls.push({method: request.method(), url: request.url(), body, headers: request.headers()});
        if (request.method() === 'GET') {
          if (url.pathname === '/api/results') {
            const offset = Number(url.searchParams.get('offset') || 0);
            return route.fulfill({json: {items: [{...detail(id, 2).record, title: offset ? 'Second page' : hostile}], total: 2, offset, limit: 1}});
          }
          if (url.pathname.endsWith('missing')) return route.fulfill({status: 404, json: {error: 'not found'}});
          return route.fulfill({json: detail(url.pathname.split('/').at(-1), Number(url.searchParams.get('revision') || latest))});
        }
        if (url.pathname.endsWith('/contact')) {
          if (contactFails) return route.fulfill({status: 503, json: {error: 'fixture contact retry'}});
          return route.fulfill({json: {accepted: true}});
        }
        if (url.pathname.endsWith('/update')) {
          if (conflict) return route.fulfill({status: 409, json: {error: 'new revision'}});
          latest++; status = body.payload.status;
          const resource = detail(id, latest).record;
          return route.fulfill({json: {resource: status === 'withdrawn' ? resource : {record: resource}, version: String(latest)}});
        }
        if (createFails) return route.fulfill({status: 503, json: {error: 'fixture create retry'}});
        latest = 1; status = body.payload.status;
        // RegisterResource returns a resource envelope. If its version is
        // opaque, the UI must GET before presenting a pinned detail view.
        return route.fulfill({json: {resource_id: other, resource: {resource_id: other, version: 'opaque-version'}}});
      }
      if (url.pathname.startsWith('/api/')) return route.fulfill({json: {}});
      return route.fulfill({body: html, contentType: 'text/html'});
    });
    const goto = query => page.goto(`http://results.fixture.invalid/results?${query}`);
    const click = name => page.getByRole('button', {name, exact: true}).click();
    const readyDetail = () => page.locator('.resultsPanel h2').waitFor();
    await goto('project_id=Project%20%26%20one&author_id=resident_filter');
    await page.locator('.resultsCard').waitFor();
    assert.equal(await page.locator('.resultsCard h2').textContent(), hostile);
    assert.equal(await page.locator('#residentResults img').count(), 0);
    assert.match(calls.at(-1).url, /author_id=resident_filter/);
    await page.getByRole('link', {name: '下一页', exact: true}).click();
    await page.getByRole('link', {name: 'Second page', exact: true}).waitFor();
    assert.match(calls.at(-1).url, /offset=1/);
    await page.getByRole('link', {name: '上一页', exact: true}).click();
    await page.locator('.resultsCard h2').filter({hasText: hostile}).waitFor();
    console.log('PASS list: encoded project/author filters, paging, untrusted title and URL');

    await goto(`id=${id}&revision=1`); await readyDetail();
    assert.equal(await page.getByRole('button', {name: '编辑为新版本'}).count(), 0);
    assert.ok(await page.getByText('发布状态不表示验证通过。以下内容由作者提供。').isVisible());
    assert.match(await page.getByRole('link', {name: '固定版本链接'}).getAttribute('href'), /revision=1/);
    await page.getByText('查看本版本原文', {exact: true}).click();
    assert.ok((await page.locator('details pre').last().textContent()).includes('"revision": 1'));
    assert.equal(calls.filter(call => call.url.endsWith('/contact')).length, 0);
    await page.getByText('联系作者', {exact: true}).click();
    await page.getByLabel('留言', {exact: true}).fill('Question about the saved result');
    contactFails = true; await click('发送给原作者'); await page.getByRole('alert').waitFor();
    const failedContact = calls.at(-1);
    contactFails = false;
    await page.getByRole('button', {name: '发送给原作者'}).evaluate(button => { button.click(); button.click(); });
    await page.getByText('已发送给原作者。', {exact: true}).waitFor();
    const contacts = calls.filter(call => call.url.endsWith('/contact'));
    assert.equal(contacts.length, 2);
    assert.equal(contacts[1].body.operation_id, failedContact.body.operation_id);
    assert.equal(contacts[1].body.revision, 1);
    assert.equal(await page.locator('#residentResults img').count(), 0);
    console.log('PASS fixed historical source, distinct validation, explicit contact, double-click suppression and retry key');

    await goto(`id=${id}`); await readyDetail();
    assert.equal(new URL(page.url()).searchParams.get('revision'), '2');
    await click('编辑为新版本');
    await page.getByLabel('正文', {exact: true}).fill('Changed body');
    await page.evaluate(() => PortalI18n.apply('en'));
    assert.equal(await page.getByLabel('Body', {exact: true}).inputValue(), 'Changed body');
    await page.evaluate(() => PortalI18n.apply('zh-CN'));
    conflict = true; await click('保存新版本'); await page.getByRole('alert').waitFor();
    assert.equal(await page.getByLabel('正文', {exact: true}).inputValue(), 'Changed body');
    assert.equal(await page.getByRole('link', {name: '查看最新版本'}).getAttribute('target'), '_blank');
    const patch = calls.at(-1);
    assert.equal(patch.method, 'POST'); assert.ok(patch.url.endsWith('/update')); assert.equal(patch.body.expected_revision, 2);
    assert.equal(patch.body.payload.body, 'Changed body');
    assert.equal(patch.body.payload.documents[0].sha256, undefined);
    assert.equal(patch.body.payload.author_id, undefined); assert.equal(patch.body.payload.project_id, undefined);
    assert.equal(patch.headers['content-type'], 'application/json');
    assert.equal(patch.headers['origin'], 'http://results.fixture.invalid');
    conflict = false; await click('保存新版本'); await readyDetail();
    assert.equal(new URL(page.url()).searchParams.get('revision'), '3');
    assert.ok(calls.at(-1).url.endsWith(`${id}?revision=3`));
    await page.getByText('撤回成果', {exact: true}).click();
    await page.getByLabel('撤回原因').fill('Superseded'); await click('确认撤回');
    await page.getByText('此成果已撤回。历史内容仅供追溯，请勿作为当前结论使用。').waitFor();
    assert.equal(calls.filter(call => call.url.endsWith('/update')).at(-1).body.expected_revision, 3);
    assert.equal(new URL(page.url()).searchParams.get('revision'), '4');
    assert.ok(calls.at(-1).url.endsWith(`${id}?revision=4`));
    console.log('PASS edit CAS, conflict preserves draft, language switch, same-origin JSON, new revision and withdrawal');

    await goto('project_id=Project%20%26%20one&author_id=do-not-impersonate');
    await click('新建成果'); await page.getByLabel('标题', {exact: true}).fill('My new result');
    createFails = true; await click('保存草稿'); await page.getByRole('alert').waitFor();
    const failedCreate = calls.at(-1);
    assert.equal(failedCreate.body.payload.author_id, undefined);
    assert.equal(failedCreate.body.payload.project_id, 'Project & one');
    assert.equal(failedCreate.body.payload.status, 'draft');
    createFails = false; await click('保存草稿'); await readyDetail();
    const creations = calls.filter(call => call.method === 'POST' && new URL(call.url).pathname === '/api/results');
    assert.equal(creations.length, 2);
    assert.equal(creations[1].body.operation_id, failedCreate.body.operation_id);
    assert.equal(new URL(page.url()).searchParams.get('id'), other);
    assert.equal(new URL(page.url()).searchParams.get('revision'), '1');
    assert.equal(calls.at(-1).method, 'GET');
    assert.ok(calls.at(-1).url.endsWith(other));
    console.log('PASS create: fixed project, actor-owned author, private draft payload, stable retry operation');

    await goto('project_id=Initial'); await page.locator('.resultsCard').waitFor();
    // Simulate a transport that cannot cancel. Its old result still must not win.
    // Delay at the actual fetch transport while retaining the real PortalAPI.
    await page.evaluate(() => {
      const original = window.fetch;
      window.fetch = (url, options) => url.includes('project_id=Slow')
        ? new Promise(resolve => { window.resolveSlowResult = () => resolve(new Response(JSON.stringify({items: [], total: 0, offset: 0, limit: 50}), {status: 200})); })
        : original(url, options);
    });
    await page.getByLabel('项目', {exact: true}).fill('Slow'); await click('筛选');
    await page.waitForFunction(() => typeof window.resolveSlowResult === 'function');
    await page.getByLabel('项目', {exact: true}).fill('Fast'); await click('筛选');
    await page.locator('.resultsCard').waitFor(); await page.evaluate(() => window.resolveSlowResult());
    await page.waitForTimeout(30);
    assert.equal(await page.getByLabel('项目', {exact: true}).inputValue(), 'Fast');
    assert.equal(await page.locator('.resultsCard').count(), 1);
    await goto('id=missing'); await page.getByRole('alert').waitFor();
    assert.equal(await page.getByRole('button', {name: '编辑为新版本'}).count(), 0);
    await goto(`id=${id}&revision=-1`); await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').textContent(), /版本必须为正整数/);
    console.log('PASS stale response suppression, inaccessible result and invalid revision');

    await page.setViewportSize({width: 390, height: 844});
    await goto(`id=${id}&revision=1`); await readyDetail();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (process.env.RESULTS_SCREENSHOT) await page.screenshot({path: process.env.RESULTS_SCREENSHOT, fullPage: true});
    assert.deepEqual(errors, []);
    console.log('PASS mobile width and no browser errors');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
