'use strict';
// The real workbench directory (portal_workbench_catalog.js) in Chromium: heading figures,
// groups, cards for managers and readers, teams arriving later, filters, stars and English.
const path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const { chromium } = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));

const ORIGIN = 'http://portal.test';
const bound = (...ids) => Object.fromEntries(ids.map((id, index) => [index ? `extra${index}` : 'main', { lineage_id: id }]));
const WORKBENCHES = [
  { workbench_id: 'workbench_aaaaaaaaaaa1', title: 'Alpha Board', description: 'Daily Alpha numbers', project_ids: ['Alpha'],
    revision: 4, published_revision: 3, updated_at: '2026-10-20T11:00:00Z', url: '/workbenches/view?id=workbench_aaaaaaaaaaa1',
    bindings: bound('lineage_aaaaaaaaaaaa'), allows: { UpdateResource: true } },
  { workbench_id: 'workbench_aaaaaaaaaaa2', title: 'Alpha Draft', description: '', project_ids: ['Alpha', 'Beta'],
    revision: 2, published_revision: 0, updated_at: '2026-10-19T09:00:00Z', url: '/workbenches?panel=settings&id=workbench_aaaaaaaaaaa2',
    bindings: bound('lineage_aaaaaaaaaaaa', 'lineage_bbbbbbbbbbbb'), allows: { UpdateResource: true } },
  { workbench_id: 'workbench_bbbbbbbbbbb1', title: 'Beta Report', description: 'Weekly Beta report', project_ids: ['Beta'],
    revision: 5, published_revision: 5, updated_at: '2026-10-20T12:00:00Z', url: '/workbenches/view?id=workbench_bbbbbbbbbbb1',
    bindings: bound('lineage_bbbbbbbbbbbb'), allows: { UpdateResource: false } },
  { workbench_id: 'workbench_ccccccccccc1', title: 'Loose Tool', description: 'Unfiled', project_ids: [],
    revision: 1, published_revision: 1, updated_at: '2026-10-01T00:00:00Z', url: '/workbenches/view?id=workbench_ccccccccccc1',
    bindings: {}, allows: { UpdateResource: true } },
  { workbench_id: 'workbench_ddddddddddd1', title: 'Paper', description: 'Read papers', project_ids: ['Alpha'], builtin: 'paper', native: true,
    navigation: 'top', revision: 7, published_revision: 7, updated_at: '2026-10-18T00:00:00Z', url: '/paper',
    bindings: bound('lineage_cccccccccccc'), allows: { UpdateResource: true } },
];
const TEAMS = {
  lineage_aaaaaaaaaaaa: { title: 'Vision', member_count: 4, running: 2, queued: 1, avatar: null },
  lineage_bbbbbbbbbbbb: { title: 'Ops', member_count: 2, running: 0, queued: 0, avatar: null },
  lineage_cccccccccccc: { unavailable: true },
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route(`${ORIGIN}/**`, route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body></body></html>' }));
    await page.goto(`${ORIGIN}/workbenches`);
    await page.evaluate(() => {
      const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
      window.PortalI18n = { t: (x, values = {}) => String(x).replace(/\{p(\d+)\}/g, (m, i) => values['p' + i] ?? m), text: tag, html: tag, locale: () => 'zh-CN' };
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = { escapeHtml, mountIcons() {} };
      window.f = { toasts: [], writes: [], favorites: [], refreshed: [], failFavorite: false };
      window.PortalUI = { toast: (message, kind) => f.toasts.push([message, kind]), formatDateTime: value => new Date(value).toISOString().slice(0, 16) };
      window.PortalPageState = { create: () => ({ read: () => ({}), write: patch => f.writes.push(patch), destroy() {} }) };
      window.PortalIdentity = { username: () => 'fixture' };
    });
    await page.addScriptTag({ path: path.join(root, 'static', 'portal_workbench_catalog.js') });
    await page.addStyleTag({ path: path.join(root, 'static', 'portal_workbench_catalog.css') });
    const mount = (workbenches, extra = {}) => page.evaluate(({ workbenches, extra }) => {
      window.directory?.destroy();
      document.body.innerHTML = '<main id="root"></main>';
      window.directory = PortalWorkbenchCatalog.mount(document.getElementById('root'), {
        workbenches, favorites: ['workbench_bbbbbbbbbbb1'], ...extra,
        onRefresh: state => f.refreshed.push(state),
        onFavorite: async (id, favorite) => {
          f.favorites.push([id, favorite]);
          if (f.failFavorite) throw new Error('refused');
          return null;
        },
      });
    }, { workbenches, extra });
    const text = selector => page.$eval(selector, node => node.textContent.replace(/\s+/g, ' ').trim());
    const cards = selector => page.$$eval(`${selector} .workbenchCard`, nodes => nodes.map(node => node.dataset.workbenchId));
    await mount(WORKBENCHES);

    // Heading figures; teams are still being read.
    assert.equal(await text('h1'), 'Workbenches', 'the heading is the navigation entry, 工作台 in Chinese');
    assert.equal(await text('[data-workbench-stats]'), '5 个工作台 3 个团队 … 个 Resident 正在工作');
    // Starred first, then Projects by their latest change, then workbenches without a Project, built-in last.
    assert.deepEqual(await page.$$eval('[data-workbench-group]', nodes => nodes.map(node => node.dataset.workbenchGroup)),
      [':favorite', 'Alpha', ':unassigned', ':builtin']);
    assert.deepEqual(await cards('[data-workbench-group="Alpha"]'), ['workbench_aaaaaaaaaaa1', 'workbench_aaaaaaaaaaa2']);
    assert.equal(await page.$eval('[data-workbench-group="Alpha"] .workbenchGroupLink', node => node.getAttribute('href')), '/projects?project=Alpha');
    assert.equal(await page.$('[data-workbench-group=":favorite"] .workbenchGroupLink'), null);
    assert.equal(await text('[data-workbench-id="workbench_aaaaaaaaaaa1"] .workbenchCardTeam'), '正在读取团队…');
    assert.equal(await text('[data-workbench-id="workbench_ccccccccccc1"] .workbenchCardTeam'), '未绑定团队');

    // A published page opens; a draft continues in its editor.
    const card = id => `[data-workbench-id="${id}"]`;
    assert.equal(await page.$eval(`${card('workbench_aaaaaaaaaaa1')} [data-workbench-open]`, node => node.getAttribute('href')), '/workbenches/view?id=workbench_aaaaaaaaaaa1');
    assert.equal(await page.$eval(`${card('workbench_aaaaaaaaaaa2')} [data-workbench-open]`, node => [node.getAttribute('href'), node.getAttribute('aria-label')].join(' | ')),
      '/workbenches?panel=settings&id=workbench_aaaaaaaaaaa2 | 继续编辑 · Alpha Draft');
    assert.equal(await page.$eval(card('workbench_aaaaaaaaaaa2'), node => node.classList.contains('is-draft')), true);
    // Those who may change a page see its versions, unpublished changes, time and settings; readers see none.
    const marks = await page.$$eval(`${card('workbench_aaaaaaaaaaa1')} .workbenchCardMarks > span`, nodes => nodes.map(node => node.textContent));
    assert.deepEqual(marks.slice(0, 2), ['已发布 v3', '有未发布修改']);
    assert.match(marks[2], /^\d+ 天前更新$/);
    assert.equal(await text(`${card('workbench_aaaaaaaaaaa2')} .workbenchState`), '草稿 v2');
    assert.equal(await page.$(`${card('workbench_aaaaaaaaaaa1')} [data-workbench-settings]`) !== null, true);
    assert.equal(await page.$(`${card('workbench_bbbbbbbbbbb1')} .workbenchCardMarks`), null);
    assert.equal(await page.$(`${card('workbench_bbbbbbbbbbb1')} [data-workbench-settings]`), null);
    assert.equal(await page.$(`${card('workbench_ddddddddddd1')} .workbenchState`), null, 'built-in entries carry no publication state');
    // Chips name the Projects besides the group's own and the top navigation.
    assert.deepEqual(await page.$$eval(`${card('workbench_aaaaaaaaaaa2')} .workbenchChip`, nodes => nodes.map(node => [node.textContent, node.getAttribute('href')])),
      [['Beta', '/projects?project=Beta']]);
    assert.deepEqual(await page.$$eval(`${card('workbench_ddddddddddd1')} .workbenchChip`, nodes => nodes.map(node => node.textContent)), ['Alpha', '顶部导航']);

    // Teams arrive: names, members, work, other teams; the focused card keeps its focus.
    await page.focus(`${card('workbench_aaaaaaaaaaa1')} [data-workbench-open]`);
    await page.evaluate(teams => directory.setTeams(teams), TEAMS);
    assert.equal(await page.evaluate(() => document.activeElement.closest('[data-workbench-id]')?.dataset.workbenchId), 'workbench_aaaaaaaaaaa1');
    assert.equal(await text('[data-workbench-stats]'), '5 个工作台 3 个团队 2 个 Resident 正在工作');
    assert.equal(await text(`${card('workbench_aaaaaaaaaaa1')} .workbenchCardTeam`), 'Vision 4 位成员 2 在工作 · 1 排队');
    assert.equal(await page.$(`${card('workbench_aaaaaaaaaaa1')} .workbenchActivity.is-running .workbenchPulse`) !== null, true);
    assert.equal(await page.$eval(card('workbench_aaaaaaaaaaa1'), node => node.style.getPropertyValue('--heat')), '1.00');
    assert.equal(await page.$eval(`${card('workbench_aaaaaaaaaaa2')} .workbenchTeamMore`, node => [node.textContent, node.title].join(' | ')), '+1 个团队 | 另绑定团队：Ops');
    assert.equal(await text(`${card('workbench_bbbbbbbbbbb1')} .workbenchActivity`), '空闲');
    assert.match(await text(`${card('workbench_ddddddddddd1')} .workbenchCardTeam`), /团队信息暂不可读取/);
    assert.equal(await page.$(`${card('workbench_ddddddddddd1')} .workbenchActivity`), null);

    // Search covers bound team names; the figures count what is shown.
    await page.fill('[data-workbench-query]', 'ops');
    assert.deepEqual(await cards(''), ['workbench_bbbbbbbbbbb1', 'workbench_aaaaaaaaaaa2']);
    assert.equal(await text('[data-workbench-stats]'), '2 / 5 个工作台 2 个团队 2 个 Resident 正在工作');
    await page.fill('[data-workbench-query]', 'nothing-matches');
    assert.equal(await text('.workbenchCatalogEmpty h2'), '没有匹配的工作台');
    await page.click('[data-workbench-reset]');
    assert.equal(await page.$eval('[data-workbench-query]', node => [node.value, document.activeElement === node].join()), ',true');
    assert.equal((await cards('')).length, 5);

    // The Project filter lives in the address; a workbench of several Projects joins the named one's group.
    await page.selectOption('[data-workbench-filter="project"]', 'Beta');
    assert.equal(await page.evaluate(() => location.search), '?project=Beta');
    assert.deepEqual(await page.$$eval('[data-workbench-group]', nodes => nodes.map(node => node.dataset.workbenchGroup)), [':favorite', 'Beta']);
    assert.deepEqual(await page.$$eval(`${card('workbench_aaaaaaaaaaa2')} .workbenchChip`, nodes => nodes.map(node => node.textContent)), ['Alpha']);
    await page.selectOption('[data-workbench-filter="project"]', ':builtin');
    assert.deepEqual(await cards(''), ['workbench_ddddddddddd1']);
    await page.selectOption('[data-workbench-filter="project"]', '');
    assert.equal(await page.evaluate(() => location.search), '');
    await page.selectOption('[data-workbench-filter="publish"]', 'draft');
    assert.deepEqual(await cards(''), ['workbench_aaaaaaaaaaa2']);
    await page.selectOption('[data-workbench-filter="publish"]', '');

    // Folding a group is remembered; refresh hands the state back.
    await page.click('[data-workbench-group="Alpha"] [data-workbench-group-toggle]');
    assert.equal(await page.isVisible('[data-workbench-group="Alpha"] .workbenchGrid'), false);
    assert.equal(await page.$eval('[data-workbench-group="Alpha"] [data-workbench-group-toggle]', node => node.getAttribute('aria-expanded')), 'false');
    assert.deepEqual(await page.evaluate(() => f.writes.at(-1)), { query: '', publish: '', collapsed: ['Alpha'] });
    await page.click('[data-workbench-refresh]');
    assert.deepEqual(await page.evaluate(() => f.refreshed), [{ query: '', publish: '', project: '', collapsed: ['Alpha'] }]);
    await page.click('[data-workbench-group="Alpha"] [data-workbench-group-toggle]');

    // A star moves the card to the starred group and keeps the focus; a refused star puts it back.
    await page.click(`${card('workbench_ccccccccccc1')} [data-workbench-favorite]`);
    assert.deepEqual(await cards('[data-workbench-group=":favorite"]'), ['workbench_bbbbbbbbbbb1', 'workbench_ccccccccccc1']);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.workbenchFavorite), 'workbench_ccccccccccc1');
    await page.evaluate(() => { f.failFavorite = true; });
    await page.click(`${card('workbench_aaaaaaaaaaa1')} [data-workbench-favorite]`);
    await page.waitForFunction(() => f.toasts.length === 1);
    assert.deepEqual(await cards('[data-workbench-group=":favorite"]'), ['workbench_bbbbbbbbbbb1', 'workbench_ccccccccccc1']);
    assert.deepEqual(await page.evaluate(() => f.favorites), [['workbench_ccccccccccc1', true], ['workbench_aaaaaaaaaaa1', true]]);

    // An address naming a Project shows only its workbenches; readers get no publication filter.
    await page.evaluate(() => history.replaceState(null, '', '/workbenches?project=Alpha'));
    await mount(WORKBENCHES);
    assert.deepEqual(await cards(''), ['workbench_aaaaaaaaaaa1', 'workbench_aaaaaaaaaaa2']);
    assert.equal(await page.$eval('[data-workbench-filter="project"]', node => node.value), 'Alpha');
    await mount([WORKBENCHES[2]], { initialState: { project: '' } });
    assert.equal(await page.$('[data-workbench-filter="publish"]'), null);

    // In English the directory carries no untranslated Chinese.
    const english = await browser.newPage();
    english.on('pageerror', error => errors.push(error.message));
    await english.route(`${ORIGIN}/**`, route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body></body></html>' }));
    await english.goto(`${ORIGIN}/workbenches`);
    await english.evaluate(() => { window.__PORTAL_LANGUAGE__ = 'en'; window.PortalCompatibility = { identifier: name => `portal-${name}` }; });
    for (const name of ['portal_text', 'portal_translations', 'portal_i18n']) {
      await english.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    await english.evaluate(() => {
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = { escapeHtml, mountIcons() {} };
      window.PortalUI = { toast() {}, formatDateTime: value => new Date(value).toISOString().slice(0, 10) };
      window.PortalPageState = { create: () => ({ read: () => ({}), write() {}, destroy() {} }) };
    });
    await english.addScriptTag({ path: path.join(root, 'static', 'portal_workbench_catalog.js') });
    const chinese = () => english.evaluate(() => {
      const found = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) if (/[一-鿿]/.test(walker.currentNode.nodeValue)) found.push(walker.currentNode.nodeValue.trim());
      for (const node of document.body.querySelectorAll('[title],[aria-label],[placeholder],option')) {
        for (const name of ['title', 'aria-label', 'placeholder']) if (/[一-鿿]/.test(node.getAttribute(name) || '')) found.push(`${name}=${node.getAttribute(name)}`);
      }
      return found;
    });
    await english.evaluate(({ workbenches, teams }) => {
      document.body.innerHTML = '<main id="root"></main>';
      const directory = PortalWorkbenchCatalog.mount(document.getElementById('root'), {
        workbenches, favorites: ['workbench_bbbbbbbbbbb1'], onRefresh() {}, onFavorite: async () => null });
      directory.setTeams(teams);
    }, { workbenches: WORKBENCHES, teams: TEAMS });
    assert.match(await english.textContent('.workbenchListStats'), /5\s*workbenches/i);
    assert.deepEqual(await chinese(), []);
    await english.evaluate(() => {
      document.body.innerHTML = '<main id="root"></main>';
      PortalWorkbenchCatalog.mount(document.getElementById('root'), { workbenches: [] });
    });
    assert.deepEqual(await chinese(), []);
    assert.deepEqual(errors, []);
    console.log('PASS workbench catalog');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
