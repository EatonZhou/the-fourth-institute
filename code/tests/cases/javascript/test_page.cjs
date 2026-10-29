'use strict';
// The real Test page modules in Chromium: overview, catalog, case, plan, run, runs and TAU pages.
// A fixture origin answers the Test Framework API and records every request; nothing reaches an instance.
const path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const page_root = path.join(root, 'test_framework/page');
const { chromium } = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));
const ORIGIN = 'http://portal.test';

const GROUPS = [
  { id: 'lineage', label: '6. Lineage', modules: [{ id: 'lineage.hatg', label: 'HATG 原语', design_refs: [] }, { id: 'lineage.team', label: '团队', design_refs: [] }] },
  { id: 'operations', label: '11. 运维', modules: [{ id: 'operations.test-framework', label: '测试框架', design_refs: [] }, { id: 'operations.empty', label: '空模块', design_refs: [] }] },
];
const CASES = [
  { id: 'test_aaaaaaaaaaaa', key: 'tests/cases/test_hatg.py::test_route', title: 'Route 上限', importance: 'P0', module: 'lineage.hatg', features: [], aspects: ['functional'], is_design_point: true, design_point_ref: null, lifecycle: 'active', runners: ['pytest'], detail_loaded: false },
  { id: 'test_bbbbbbbbbbbb', key: 'tau.hatg.route', title: 'Route 真实闭环', importance: 'P1', module: 'lineage.hatg', features: [], aspects: ['ui'], is_design_point: false, design_point_ref: null, lifecycle: 'active', runners: ['tau'], detail_loaded: false },
  { id: 'test_cccccccccccc', key: 'tests/cases/test_team.py::test_team', title: '团队成员', importance: 'P2', module: 'lineage.team', features: [], aspects: ['behavior'], is_design_point: false, design_point_ref: null, lifecycle: 'active', runners: [], detail_loaded: false },
  { id: 'test_dddddddddddd', key: 'tests/cases/test_framework.py::test_plan', title: '运行计划', importance: 'P1', module: 'operations.test-framework', features: [], aspects: ['functional'], is_design_point: true, design_point_ref: null, lifecycle: 'active', runners: ['pytest'], detail_loaded: false },
];
const DETAIL = {
  ...CASES[0], description: '只通过 Route 验证上限。', design_refs: ['decision_aaaaaaaaaaaa'], module_path: { module_label: 'HATG 原语' },
  implementations: [
    { id: 'impl-a', runner: 'pytest', entrypoint: 'tests/cases/test_hatg.py::test_route', source: 'tests/cases/test_hatg.py', profiles: [], risk: 'no-external-side-effects' },
    { id: 'impl-b', runner: 'pytest', entrypoint: 'tests/cases/test_hatg.py::test_route_fast', source: 'tests/cases/test_hatg.py', profiles: ['local.fixture'], risk: 'unspecified' },
  ],
};
delete DETAIL.detail_loaded;
const OVERVIEW = {
  schema: 'tfi.test-overview.v1',
  summary: { total: 4, design_points: 2, without_implementation: 1, needs_review: 0, by_importance: { P0: 1, P1: 2, P2: 1, P3: 0 } },
  catalog_errors: [{ resource_id: 'test_eeeeeeeeeeee', error: 'test_eeeeeeeeeeee：设计点必须关联 design_refs' }],
  filters: { importance: ['P0', 'P1', 'P2', 'P3'], modules: GROUPS, features: [{ id: 'core', label: '核心' }], aspects: ['functional', 'ui'] },
  modules: [
    { id: 'lineage.hatg', group_id: 'lineage', total: 2, by_importance: { P0: 1, P1: 1, P2: 0, P3: 0 }, design_points: 1, tau: 1, without_implementation: 0, live: 0 },
    { id: 'lineage.team', group_id: 'lineage', total: 1, by_importance: { P0: 0, P1: 0, P2: 1, P3: 0 }, design_points: 0, tau: 0, without_implementation: 1, live: 0 },
    { id: 'operations.test-framework', group_id: 'operations', total: 1, by_importance: { P0: 0, P1: 1, P2: 0, P3: 0 }, design_points: 1, tau: 0, without_implementation: 0, live: 0 },
    { id: 'operations.empty', group_id: 'operations', total: 0, by_importance: { P0: 0, P1: 0, P2: 0, P3: 0 }, design_points: 0, tau: 0, without_implementation: 0, live: 0 },
  ],
  capabilities: { runners: [{ id: 'pytest', label: 'pytest', available: true }, { id: 'tau', label: 'TAU · Agent 真实使用', available: true }], profiles: [{ id: 'local.fixture', label: '本地测试数据', risk: 'isolated' }] },
  recent_runs: [
    { run_id: 'testrun_1111111111111111', status: 'failed', created_by: 'resident_aaaaaaaaaaaa', created_at: '2026-10-28T10:00:00Z', started_at: '2026-10-28T10:00:01Z', finished_at: '2026-10-28T10:05:01Z', plan: { campaign: 'tau', selected: [{ case_id: 'test_bbbbbbbbbbbb', runner: { id: 'tau' } }] }, case_count: 1, title: 'Route 真实闭环', module_ids: ['lineage.hatg'] },
    { run_id: 'testrun_2222222222222222', status: 'passed', created_by: 'portal-test', created_at: '2026-10-27T10:00:00Z', started_at: '2026-10-27T10:00:01Z', finished_at: '2026-10-27T10:00:31Z', plan: { campaign: '', selected: [{ case_id: 'test_aaaaaaaaaaaa', runner: { id: 'pytest' } }] }, case_count: 1, title: 'Route 上限', module_ids: ['lineage.hatg'] },
  ],
  tau_residents: [{ module_id: 'lineage.hatg', resident_id: 'resident_tttttttttttt', lineage_id: 'lineage_tttttttttttt', backend_route_id: 'five', updated_at: '2026-10-23T02:00:00Z' }],
  access: { actor: 'portal-test', can_operate: true },
};

function filterCases(url) {
  const params = url.searchParams;
  const modules = params.getAll('module'), runners = params.getAll('runner'), search = (params.get('search') || '').toLowerCase();
  return CASES.filter(item => (!modules.length || modules.includes(item.module))
    && (!runners.length || runners.some(runner => runner === 'none' ? !item.runners.length : item.runners.includes(runner)))
    && (!params.get('importance') || params.getAll('importance').includes(item.importance))
    && (!params.get('is_design_point') || String(item.is_design_point) === params.get('is_design_point'))
    && (!search || `${item.title} ${item.key}`.toLowerCase().includes(search)));
}

function runRecord(state) {
  return {
    run_id: 'testrun_3333333333333333', status: state, created_by: 'portal-test', created_at: '2026-10-29T10:00:00Z', started_at: '2026-10-29T10:00:01Z',
    finished_at: state === 'passed' ? '2026-10-29T10:00:11Z' : '', output: '\r\nfirst <b>line</b>\n',
    plan: { campaign: '', request: { select: { case_ids: ['test_aaaaaaaaaaaa', 'test_dddddddddddd'] }, implementation: {} },
      selected: [{ case_id: 'test_aaaaaaaaaaaa', case_title: 'Route 上限', module: 'lineage.hatg', implementation: { entrypoint: 'tests/cases/test_hatg.py::test_route' }, runner: { id: 'pytest' } },
        { case_id: 'test_dddddddddddd', case_title: '运行计划', module: 'operations.test-framework', implementation: { entrypoint: 'tests/cases/test_framework.py::test_plan' }, runner: { id: 'pytest' } }], excluded: [] },
    groups: [{ group_id: 'pytest:1', label: 'pytest 1/1', status: state, results: state === 'passed'
      ? [{ case_id: 'test_aaaaaaaaaaaa', status: 'passed', group_id: 'pytest:1', item_index: 0, details: { summary: 'ok' } },
        { case_id: 'test_dddddddddddd', status: 'passed', group_id: 'pytest:1', item_index: 1, details: { summary: 'ok', evidence: ['log.txt'] } }]
      : [] }],
    results: state === 'passed' ? [{ case_id: 'test_aaaaaaaaaaaa', status: 'passed' }, { case_id: 'test_dddddddddddd', status: 'passed' }] : [],
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const requests = [];
  const posts = [];
  let runPolls = 0;
  let admissionPaused = true;
  const control = { failPage: false, slow: null };
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route(`${ORIGIN}/**`, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/development') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body><section id="wallTestView"><div class="testPage" id="testPage"></div></section></body></html>' });
      requests.push(`${request.method()} ${url.pathname}${url.search}`);
      if (request.method() === 'POST') posts.push([url.pathname, JSON.parse(request.postData() || '{}')]);
      if (url.pathname === '/test/api/catalog' && url.searchParams.get('projection') === 'overview') return json(OVERVIEW);
      if (url.pathname === '/test/api/catalog' && url.searchParams.get('projection') === 'page') {
        if (control.failPage) return json({ error: '目录暂时不可用' }, 503);
        if (url.searchParams.get('importance') === 'P1' && control.slow) await control.slow;
        const matched = filterCases(url);
        return json({ schema: 'tfi.test-catalog-page.v1', count: matched.length, offset: 0, limit: 100, cases: matched });
      }
      if (url.pathname === '/test/api/catalog/query') return json({ case_ids: filterCases(url).map(item => item.id), count: filterCases(url).length });
      if (url.pathname === '/test/api/catalog/case') return url.searchParams.get('case_id') === DETAIL.id ? json({ case: DETAIL }) : json({ error: 'missing' }, 404);
      if (url.pathname === '/test/api/source') return json({ path: 'tests/cases/test_hatg.py', content: 'line 1\nline 2\nline 3', line: 2, entrypoint: 'tests/cases/test_hatg.py::test_route' });
      if (url.pathname === '/test/api/preview') {
        const body = JSON.parse(request.postData());
        return json({ executable: true, campaign: '', request: body, selected_case_count: body.select.case_ids.length, selected_implementation_count: body.select.case_ids.length,
          selected: body.select.case_ids.map(id => ({ case_id: id, case_title: CASES.find(item => item.id === id).title, implementation: { entrypoint: CASES.find(item => item.id === id).key }, runner: { id: 'pytest', label: 'pytest' }, risk: 'isolated' })),
          excluded: [], execution: { groups: [{ group_id: 'pytest:1' }] } });
      }
      if (url.pathname === '/test/api/run' && request.method() === 'POST') return json(runRecord('queued'));
      if (url.pathname === '/test/api/run') {
        const id = url.searchParams.get('run_id');
        if (id === 'testrun_3333333333333333') { runPolls += 1; return json(runRecord(runPolls >= 2 ? 'passed' : 'running')); }
        if (id === 'testrun_4444444444444444') return json({ ...runRecord('error'), run_id: id, output: '', error: '测试执行进程在运行完成前退出。', groups: [], results: [] });
        if (id === 'testrun_5555555555555555') return json({ ...runRecord('running'), run_id: id, output: '', groups: [], results: [] });
        return json({ error: 'missing' }, 404);
      }
      if (url.pathname === '/test/api/runs/delete') return json({ deleted_run_ids: JSON.parse(request.postData()).run_ids, artifact_warnings: [] });
      if (url.pathname === '/test/api/tau/admission' && request.method() === 'POST') { admissionPaused = JSON.parse(request.postData()).paused; }
      if (url.pathname === '/test/api/tau/admission') return json({ can_manage: true, configuration: { admission_state: admissionPaused ? 'paused' : 'open', revision: 3, target_available: true, admission_paused: admissionPaused, reason: '' } });
      if (url.pathname === '/api/workbenches/native') return json({ workbench: { bindings: { main: { lineage_id: 'lineage_8794980b1bb7', residents: { facade: 'resident_ffffffffffff' } } } } });
      if (url.pathname === '/api/wall/test-binding') return json({ status: 'unbound' });
      return json({ error: `unexpected ${url.pathname}` }, 500);
    });
    await page.goto(`${ORIGIN}/development?view=test`);
    await page.evaluate(() => {
      const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
      window.PortalI18n = { t: x => String(x), text: tag, html: tag, markup: x => x, locale: () => 'zh-CN' };
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.f = { chats: [], toasts: [], confirms: [] };
      window.PortalUIPrimitives = { escapeHtml, mountIcons() {}, asyncStateHtml: state => `<div class="portalAsyncState" data-state="${state.state}"><strong>${escapeHtml(state.title)}</strong>${state.actionsHtml || ''}</div>` };
      window.PortalUI = {
        toast: (message, kind) => f.toasts.push([message, kind]),
        formatDateTime: value => new Date(value).toISOString().slice(0, 16),
        confirm: async options => { f.confirms.push(options.title); return true; },
        createDialogController: options => ({ open: () => { options.modal.hidden = false; }, close: () => { options.modal.hidden = true; options.onClose?.(); }, destroy() {} }),
      };
      window.PortalAPI = { get: async url => (await fetch(url)).json() };
      window.PortalInstructionClient = { execute: async (name, args) => name === 'ResolveResource' ? { resource: { title: `名字 ${args.address.slice(-4)}` } } : {} };
      window.PortalChatWorkspace = { panelPackage: { create: options => { f.chats.push(options.target.resident_id); return { destroy() {}, setPresented() {} }; } } };
      const states = {};
      window.PortalPageState = { create: name => ({ read: () => ({ ...(states[name] || {}) }), write: patch => { states[name] = { ...(states[name] || {}), ...patch }; } }) };
      window.PortalIdentity = { username: () => 'portal-test' };
      window.PortalDemoPlayer = { create: () => ({ open: () => true }) };
      // The development page activates the registered view on every visit, back and forward included.
      window.PortalDevelopmentWorkspace = { registerView: (id, controller) => { f.view = [id, controller]; } };
      window.addEventListener('popstate', () => void f.view[1].activate({ isVisible: () => true }));
    });
    for (const name of ['test_page_core', 'test_page_overview', 'test_page_catalog', 'test_page_case', 'test_page_run', 'test_page_plan', 'test_page_tau', 'test_live_adapter', 'test_page']) {
      await page.addScriptTag({ path: path.join(page_root, `${name}.js`) });
    }
    await page.addStyleTag({ path: path.join(page_root, 'test_page.css') });
    assert.equal(await page.evaluate(() => f.view[0]), 'test');
    await page.evaluate(() => f.view[1].activate({ isVisible: () => true }));

    // Overview: the figures, a tile per module with its gaps, recent runs, the attention list and the Test team's conversation.
    await page.waitForSelector('.testOverview');
    assert.equal(await page.$eval('.testHero h1', node => node.textContent), 'Portal 测试');
    assert.deepEqual(await page.$$eval('[data-module-tile]', nodes => nodes.map(node => [node.dataset.moduleTile, node.dataset.gap])),
      [['lineage.hatg', ''], ['lineage.team', 'important'], ['operations.test-framework', ''], ['operations.empty', 'empty']]);
    assert.equal(await page.$$eval('[data-test-section="runs"] [data-run-row]', rows => rows.length), 2);
    await page.waitForFunction(() => document.querySelector('[data-test-resident-name="resident_aaaaaaaaaaaa"]')?.textContent === '名字 aaaa');
    assert.match(await page.$eval('[data-attention="errors"]', node => node.textContent), /test_eeeeeeeeeeee/);
    await page.waitForFunction(() => f.chats.length === 1);
    assert.deepEqual(await page.evaluate(() => f.chats), ['resident_ffffffffffff'], 'the Test team facade bound to the test workbench');
    assert.equal(await page.$eval('[data-test-tab="overview"]', node => node.getAttribute('aria-current')), 'page');

    // A module tile opens the catalog filtered by that module; the address carries the filter.
    await page.click('[data-module-tile="lineage.hatg"]');
    await page.waitForSelector('.testCatalog [data-case-id]');
    assert.equal(new URL(page.url()).search, '?view=test&page=cases&module=lineage.hatg');
    assert.deepEqual(await page.$$eval('[data-case-id]', rows => rows.map(row => row.dataset.caseId)), ['test_aaaaaaaaaaaa', 'test_bbbbbbbbbbbb']);
    assert.ok(requests.some(item => item.includes('projection=page') && item.includes('module=lineage.hatg')));
    // Typing waits for a pause, then filters on the server and replaces the address.
    await page.fill('[data-catalog-search]', 'tau');
    await page.waitForFunction(() => document.querySelectorAll('[data-case-id]').length === 1);
    assert.match(page.url(), /q=tau/);
    await page.click('[data-catalog-clear]');
    await page.waitForFunction(() => document.querySelectorAll('[data-case-id]').length === 4);
    assert.equal(new URL(page.url()).search, '?view=test&page=cases');
    // A failed filter offers a retry; a slower, superseded response never replaces the newer result.
    control.failPage = true;
    await page.selectOption('[data-catalog-importance]', 'P0');
    await page.waitForSelector('[data-catalog-retry]');
    control.failPage = false;
    await page.click('[data-catalog-retry]');
    await page.waitForFunction(() => [...document.querySelectorAll('[data-case-id]')].map(row => row.dataset.caseId).join() === 'test_aaaaaaaaaaaa');
    let releaseSlow;
    control.slow = new Promise(resolve => { releaseSlow = resolve; });
    await page.selectOption('[data-catalog-importance]', 'P1');
    await page.selectOption('[data-catalog-importance]', 'P2');
    await page.waitForFunction(() => [...document.querySelectorAll('[data-case-id]')].map(row => row.dataset.caseId).join() === 'test_cccccccccccc');
    releaseSlow();
    control.slow = null;
    await page.waitForTimeout(150);
    assert.deepEqual(await page.$$eval('[data-case-id]', rows => rows.map(row => row.dataset.caseId)), ['test_cccccccccccc']);
    assert.match(page.url(), /importance=P2/);
    await page.click('[data-catalog-clear]');
    await page.waitForFunction(() => document.querySelectorAll('[data-case-id]').length === 4);
    // Two rows selected become a plan; the plan previews at once and starts only on the button.
    await page.check('[data-select-case="test_aaaaaaaaaaaa"]');
    await page.check('[data-select-case="test_dddddddddddd"]');
    assert.match(await page.$eval('[data-selection-bar]', node => node.textContent), /已选 2 条用例/);
    await page.click('[data-run-selection]');
    await page.waitForSelector('[data-plan-implementation]');
    assert.equal(new URL(page.url()).search, '?view=test&page=plan');
    assert.deepEqual(posts.filter(([name]) => name === '/test/api/preview').map(([, body]) => body.select.case_ids), [['test_aaaaaaaaaaaa', 'test_dddddddddddd']]);
    assert.equal(posts.some(([name]) => name === '/test/api/run'), false, 'previewing starts nothing');
    await page.click('[data-plan-start]');
    // The run page replaces the plan, follows the run until it ends and keeps the raw output exact.
    await page.waitForSelector('.testRunPage [data-run-result]');
    assert.equal(new URL(page.url()).search, '?view=test&run=testrun_3333333333333333');
    await page.waitForFunction(() => document.querySelector('.testHero[data-run-status]')?.dataset.runStatus === 'passed');
    assert.equal(await page.$eval('[data-run-raw-output]', node => node.textContent), '\r\nfirst <b>line</b>\n');
    assert.equal(await page.$$eval('[data-run-result]', nodes => nodes.length), 2);
    await page.goBack();
    await page.waitForFunction(() => new URL(location.href).searchParams.get('page') === 'cases' && document.querySelector('.testCatalog [data-case-id]'));

    // A case page: implementations with a default choice, the source at its line, and "运行" with the chosen implementation.
    await page.evaluate(() => PortalTestPage.navigate('/development?view=test&case=test_aaaaaaaaaaaa'));
    await page.waitForSelector('.testCasePage .testImpl');
    assert.equal(await page.$eval('.testCasePage .testHero h1', node => node.textContent), 'Route 上限');
    assert.deepEqual(await page.$$eval('[data-test-section="runs"] [data-run-row]', rows => rows.map(row => row.dataset.runRow)),
      ['testrun_3333333333333333', 'testrun_2222222222222222'], 'the runs that included this case, the one just started first');
    await page.click('[data-source-impl="impl-a"]');
    await page.waitForSelector('.testSourceSurface');
    assert.equal(await page.$eval('.testSourceCode [data-target="true"]', node => node.textContent), 'line 2');
    await page.click('[data-source-close]');
    await page.check('input[name="testImplementation"][value="impl-b"]');
    await page.click('[data-case-run]');
    await page.waitForSelector('[data-plan-implementation]');
    assert.deepEqual(posts.filter(([name]) => name === '/test/api/preview').at(-1)[1].implementation, { implementation_ids: ['impl-b'], runners: ['pytest'], profile: 'local.fixture' });

    // A run that ended without output says so and keeps its error; one still going waits for output.
    await page.evaluate(() => PortalTestPage.navigate('/development?view=test&run=testrun_4444444444444444'));
    await page.waitForFunction(() => document.querySelector('.testRunPage')?.dataset.runId === 'testrun_4444444444444444');
    assert.equal(await page.$eval('[data-run-raw-output]', node => node.textContent), '测试执行进程在运行完成前退出。');
    await page.evaluate(() => PortalTestPage.navigate('/development?view=test&run=testrun_5555555555555555'));
    await page.waitForFunction(() => document.querySelector('.testRunPage')?.dataset.runId === 'testrun_5555555555555555');
    assert.equal(await page.$eval('[data-run-raw-output]', node => node.textContent), '等待输出…');

    // A removed run says so instead of showing another one.
    await page.evaluate(() => PortalTestPage.navigate('/development?view=test&run=testrun_9999999999999999'));
    await page.waitForSelector('[data-run-feedback]');
    assert.match(await page.$eval('[data-run-feedback]', node => node.textContent), /运行报告已不存在/);

    // TAU: modules with their Residents; running waits for admission; a module's Resident takes the conversation for a while.
    await page.click('[data-test-tab="tau"]');
    await page.waitForSelector('[data-tau-module-card="lineage.hatg"]');
    await page.click('[data-tau-module="lineage.hatg"]');
    assert.match(await page.$eval('[data-tau-selection]', node => node.textContent), /已选 1 \/ 1/);
    assert.equal(await page.$eval('[data-tau-run]', node => node.disabled), true, 'paused admission blocks a run');
    await page.click('[data-tau-admission-open]');
    await page.waitForFunction(() => document.querySelector('[data-tau-run]') && !document.querySelector('[data-tau-run]').disabled);
    assert.deepEqual(posts.filter(([name]) => name === '/test/api/tau/admission').map(([, body]) => body), [{ paused: false, expected_revision: 3 }]);
    await page.click('[data-tau-chat="lineage.hatg"]');
    await page.waitForFunction(() => f.chats.at(-1) === 'resident_tttttttttttt');
    assert.equal(await page.isVisible('[data-chat-back]'), true);
    await page.click('[data-chat-back]');
    await page.waitForFunction(() => f.chats.at(-1) === 'resident_ffffffffffff');

    // Runs: problems only, then deleting a report after confirmation removes it everywhere.
    await page.click('[data-test-tab="runs"]');
    await page.waitForSelector('.testRunsPage');
    await page.click('[data-runs-filter="problem"]');
    assert.deepEqual(await page.$$eval('.testRunsPage [data-run-row]', rows => rows.map(row => row.dataset.runRow)), ['testrun_1111111111111111']);
    await page.check('[data-select-run="testrun_1111111111111111"]');
    await page.click('[data-runs-delete]');
    await page.waitForFunction(() => !document.querySelector('.testRunsPage [data-run-row="testrun_1111111111111111"]'));
    assert.deepEqual(posts.filter(([name]) => name === '/test/api/runs/delete').map(([, body]) => body), [{ run_ids: ['testrun_1111111111111111'] }]);
    assert.equal((await page.evaluate(() => f.confirms)).length, 1);

    assert.deepEqual(errors, []);
    console.log('PASS test page');
  } catch (error) {
    console.error(error);
    if (errors.length) console.error('page errors:', errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
