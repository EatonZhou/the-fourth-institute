'use strict';
// The real Project list, Project page and header-menu dialogs in Chromium.
// Instructions are answered by fixtures and recorded; nothing reaches an instance.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const { chromium } = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));

const PROJECTS = [
  { project_id: 'Alpha', status: 'active', project_type: 'academic', description: 'Alpha study', ddl: '2026-10-25', tags: ['robot'],
    avatar: null, project_resident_id: 'resident_aaaaaaaaaaaa', lineage_count: 3, resident_count: 9, running_resident_count: 2,
    last_changed_at: '2026-10-20T09:00:00Z' },
  { project_id: 'Beta', status: 'active', project_type: 'company', description: 'Beta product', ddl: '2026-10-19', tags: ['os'],
    avatar: null, project_resident_id: 'resident_bbbbbbbbbbbb', lineage_count: 1, resident_count: 2, running_resident_count: 0,
    last_changed_at: '2026-10-20T11:00:00Z' },
  { project_id: 'Gamma', status: 'paused', project_type: 'other', description: '', ddl: '', tags: [],
    avatar: null, project_resident_id: '', lineage_count: 0, resident_count: 0, running_resident_count: 0, last_changed_at: '' },
  { project_id: 'Omega', status: 'done', project_type: 'academic', description: 'Finished', ddl: '2026-01-01', tags: ['robot'],
    avatar: null, project_resident_id: '', lineage_count: 0, resident_count: 0, running_resident_count: 0, last_changed_at: '2026-01-02T00:00:00Z' },
];
const ALPHA = {
  project: { resource_id: 'Alpha', status: 'active', project_type: 'academic', description: 'Alpha study', ddl: '2026-10-25', tags: ['robot'], avatar: null },
  summary: { exists: true, text: '# Goal\n\nShip it' },
  team: { project_resident_id: 'resident_aaaaaaaaaaaa', project_lineage_id: 'lineage_aaaaaaaaaaaa', resident_count: 9, running_resident_count: 2,
    lineages: [{ lineage_id: 'lineage_bbbbbbbbbbbb', title: 'Vision', leader_resident_id: 'resident_cccccccccccc', member_count: 4, running_count: 1 }] },
  resource_counts: { decision: 5, paper: 7 },
  recent_changes: [{ resource_id: 'decision_aaaaaaaaaaaa', resource_type: 'decision', title: 'A design', url: '/resource/knowledge?item=decision_aaaaaaaaaaaa', modified_at: '2026-10-20T10:00:00Z' }],
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('about:blank');
    await page.evaluate(({ projects, alpha }) => {
      const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
      window.PortalI18n = { t: (x, values = {}) => String(x).replace(/\{p(\d+)\}/g, (m, i) => values['p' + i] ?? m), text: tag, html: tag, locale: () => 'zh-CN' };
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = {
        escapeHtml, mountIcons() {}, randomUUID: () => 'fixture',
        asyncStateHtml: state => `<div class="portalAsyncState" data-state="${state.state}"><strong>${escapeHtml(state.title)}</strong>${state.actionsHtml || ''}</div>`,
      };
      window.f = { calls: [], fail: new Set(), dryRun: { busy_resident_ids: [], blockers: [] }, toasts: [] };
      window.PortalUI = {
        toast: (message, kind) => f.toasts.push([message, kind]),
        formatDateTime: value => new Date(value).toISOString().slice(0, 10),
        copyText: async text => { f.copied = text; },
        createDialogController: options => ({
          open: () => { options.modal.hidden = false; options.onOpen?.(); },
          close: () => { if (options.beforeClose?.() === false) return; options.modal.hidden = true; options.onClose?.(); },
          destroy() {},
        }),
      };
      const pageStates = {};
      window.PortalPageState = { create: name => ({ read: () => ({ ...(pageStates[name] || {}) }), write: patch => { pageStates[name] = { ...(pageStates[name] || {}), ...patch }; } }) };
      window.PortalIdentity = { username: () => 'fixture' };
      window.PortalResourceRoutes = { routes: [{ resource_type: 'decision', detail_kind: 'knowledge' }, { resource_type: 'paper', detail_kind: 'paper' }] };
      window.PortalAvatarEditor = { loadAssets: async () => {}, read: async () => null, mount: () => ({ destroy() {} }), remember() {} };
      window.MD = { render: text => `<p>${escapeHtml(text)}</p>` };
      window.PortalInstructionClient = { execute: async (name, args) => {
        f.calls.push([name, JSON.parse(JSON.stringify(args))]);
        if (f.fail.has(name)) throw new Error(`${name} refused`);
        if (name === 'ListProjects') await f.gate;
        if (name === 'ListProjects') return { projects: JSON.parse(JSON.stringify(projects)), status_vocab: ['not_started', 'active', 'paused', 'done', 'archived'] };
        if (name === 'GetProject' && args.include_resources !== false) await f.countGate;
        if (name === 'GetProject') return JSON.parse(JSON.stringify(alpha));
        if (name === 'GetResearchMap') return { project_id: args.project_id, revision: 4, updated_at: '2026-10-20T10:00:00Z', relations: [], layout: {}, notes: [], outputs: {},
          directions: [{ direction_id: 'direction_aaaaaaaaaaaa', parent_id: '', title: 'Grasping', stance: 'focus', teams: [] },
            { direction_id: 'direction_bbbbbbbbbbbb', parent_id: '', title: 'Sim2Real', stance: 'candidate', teams: [] },
            { direction_id: 'direction_cccccccccccc', parent_id: '', title: 'Old', stance: 'dropped', teams: [] }] };
        if (name === 'ListResidentStates') return { states: [
          { resident_id: 'resident_aaaaaaaaaaaa', state: { work_state: 'running' } },
          { resident_id: 'resident_cccccccccccc', state: { work_state: 'idle', queued_count: 2 } }] };
        if (name === 'GetResidentSummaries') return { summaries: [
          { resident_id: 'resident_cccccccccccc', title: 'Vision Lead', summary: { current_focus: 'Calibrating cameras', recent_results: ['Baseline 3/3 done'] } }] };
        if (name === 'RenameProject' && args.dry_run) return { previous_path: '/p/Alpha', path: '/p/' + args.new_project_id, database: { rows: 4, tables: { a: 4 } }, resident_ids: ['r'], relations: {}, external_references: [], ...f.dryRun };
        return {};
      } };
      window.PortalProjectsFixture = { now: Date.parse('2026-10-20T12:00:00Z') };
    }, { projects: PROJECTS, alpha: ALPHA });
    for (const name of ['project_common', 'project_list', 'project_settings', 'project_map_layout', 'project_home']) {
      await page.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    await page.addStyleTag({ path: path.join(root, 'static', 'project.css') });

    // The list says it is reading while ListProjects is slow, then groups by status,
    // newest change first, the finished group folded.
    await page.evaluate(() => {
      document.body.innerHTML = '<main id="list"></main>';
      f.opened = [];
      f.gate = new Promise(resolve => { f.release = resolve; });
      window.list = PortalProjectList.create({ root: document.getElementById('list'), onOpen: id => f.opened.push(id) });
      f.loading = list.load();
    });
    assert.equal(await page.$eval('#list .portalAsyncState', node => node.dataset.state), 'loading');
    await page.evaluate(async () => { f.release(); await f.loading; });
    const groupIds = await page.$$eval('[data-project-group]', nodes => nodes.map(node => node.dataset.projectGroup));
    assert.deepEqual(groupIds, ['active', 'waiting', 'closed']);
    assert.deepEqual(await page.$$eval('[data-project-group="active"] .projectCard', cards => cards.map(card => card.dataset.projectId)), ['Beta', 'Alpha']);
    assert.equal(await page.isVisible('[data-project-group="closed"] .projectGrid'), false, 'finished projects start folded');
    assert.match(await page.$eval('[data-project-id="Alpha"] .projectSignal.is-running', node => node.textContent), /2 个正在工作/);
    assert.equal(await page.$eval('[data-project-id="Omega"] .projectDeadline', () => 'shown').catch(() => 'none'), 'none', 'a finished project has no deadline badge');
    await page.click('[data-project-group-toggle="closed"]');
    assert.equal(await page.isVisible('[data-project-group="closed"] .projectGrid'), true);

    // Filters: a tag chip filters, the search box narrows further.
    await page.click('[data-project-id="Alpha"] [data-project-tag="robot"]');
    assert.deepEqual(await page.$$eval('.projectCard', cards => cards.map(card => card.dataset.projectId)), ['Alpha', 'Omega']);
    await page.fill('[data-project-query]', 'finished');
    assert.deepEqual(await page.$$eval('.projectCard', cards => cards.map(card => card.dataset.projectId)), ['Omega']);
    await page.fill('[data-project-query]', 'nothing-matches');
    assert.equal(await page.$eval('[data-project-groups] .portalAsyncState', node => node.dataset.state), 'empty');
    await page.fill('[data-project-query]', '');
    await page.click('[data-project-id="Alpha"] [data-project-tag="robot"]');

    // Opening a card goes through the page; changing a status is one UpdateProject, reverted when refused.
    await page.click('[data-project-id="Beta"] .projectCardLink');
    assert.deepEqual(await page.evaluate(() => f.opened), ['Beta']);
    await page.selectOption('[data-project-status="Gamma"]', 'active');
    await page.waitForFunction(() => f.calls.some(([name]) => name === 'UpdateProject'));
    assert.deepEqual(await page.evaluate(() => f.calls.find(([name]) => name === 'UpdateProject')[1]), { project_id: 'Gamma', changes: { status: 'active' } });
    await page.waitForFunction(() => [...document.querySelectorAll('[data-project-group="active"] .projectCard')].some(card => card.dataset.projectId === 'Gamma'));
    await page.evaluate(() => f.fail.add('UpdateProject'));
    await page.selectOption('[data-project-status="Beta"]', 'paused');
    await page.waitForFunction(() => f.toasts.length === 1);
    assert.equal(await page.$eval('[data-project-status="Beta"]', select => select.value), 'active', 'a refused change is undone');
    await page.evaluate(() => f.fail.clear());

    // A Project: hero, team with live states and summaries, resources, recent changes.
    await page.evaluate(async () => {
      document.body.innerHTML = '<main id="home"></main>';
      f.calls.length = 0;
      f.countGate = new Promise(resolve => { f.releaseCount = resolve; });
      window.home = PortalProjectHome.create({ root: document.getElementById('home'), onOpenMap: id => { f.mapOpened = id; },
        onRenamed: id => { f.renamed = id; }, onDeleted: id => { f.deleted = id; } });
      await home.load('Alpha');
    });
    // The registry and team show at once; the resources fill in once GetProject has counted them.
    assert.equal(await page.$eval('.projectHero h1', node => node.textContent), 'Alpha');
    assert.equal(await page.$eval('#projectResources .projectEmpty', node => node.textContent), '正在统计资源…');
    assert.deepEqual(await page.evaluate(() => f.calls.filter(([name]) => ['GetProject', 'ListProjects'].includes(name)).map(([name, args]) => [name, args])),
      [['GetProject', { project_id: 'Alpha' }], ['GetProject', { project_id: 'Alpha', include_resources: false }]]);
    await page.evaluate(() => f.releaseCount());
    await page.waitForSelector('.projectResourceTile');
    assert.equal(await page.$eval('#projectSummary .projectSummaryBody', node => node.textContent), '# Goal\n\nShip it');
    await page.waitForFunction(() => document.querySelector('.projectTeamCard [data-team-focus]')?.textContent === 'Calibrating cameras');
    assert.equal(await page.$eval('.projectTeamCard [data-team-result]', node => node.textContent), 'Baseline 3/3 done');
    assert.equal(await page.$eval('.projectTeamCard [data-team-state]', node => node.dataset.state), 'queued');
    assert.equal(await page.$eval('.projectLead [data-team-state]', node => node.dataset.state), 'running');
    assert.equal(await page.$eval('.projectTeamCard', node => node.getAttribute('href')), '/lineage?lineage=lineage_bbbbbbbbbbbb');
    const tiles = await page.$$eval('.projectResourceTile', nodes => nodes.map(node => [node.textContent.trim(), node.getAttribute('href')]));
    assert.deepEqual(tiles, [['5Design', '/resource/knowledge?type=decision&project=Alpha'], ['7论文', null]]);
    assert.equal(await page.$eval('.projectRecent a', node => node.getAttribute('href')), '/resource/knowledge?item=decision_aaaaaaaaaaaa');
    // The research map card: the map without outputs, dropped directions left out, opened in the page.
    assert.deepEqual(await page.evaluate(() => f.calls.find(([name]) => name === 'GetResearchMap')[1]), { project_id: 'Alpha', include_outputs: false });
    assert.equal(await page.$$eval('.projectMapPreview circle[data-stance]', dots => dots.length), 2);
    assert.match(await page.$eval('.projectMapCardText p', node => node.textContent), /^2 个方向 · 修订 4/);
    assert.equal(await page.$eval('.projectMapCardBody', node => node.getAttribute('href')), '/projects?project=Alpha&view=map');
    await page.click('.projectMapCardBody');
    assert.equal(await page.evaluate(() => f.mapOpened), 'Alpha');
    const reads = await page.evaluate(() => f.calls.filter(([name]) => name === 'ListResidentStates')[0][1]);
    assert.deepEqual(reads, { resident_ids: ['resident_aaaaaaaaaaaa', 'resident_cccccccccccc'], include_viewer_state: false });

    // summary.md: edit, mark unsaved, save as one UpdateProject.
    await page.click('[data-summary-edit]');
    await page.fill('[data-summary-editor]', '# Goal\n\nShip it soon');
    assert.equal(await page.evaluate(() => home.hasUnsavedChanges()), true);
    await page.click('[data-summary-save]');
    await page.waitForSelector('#projectSummary .projectSummaryBody');
    assert.deepEqual(await page.evaluate(() => f.calls.filter(([name]) => name === 'UpdateProject').at(-1)[1]),
      { project_id: 'Alpha', changes: { summary: '# Goal\n\nShip it soon' } });
    assert.equal(await page.evaluate(() => home.hasUnsavedChanges()), false);

    // Rename: a dry run first; busy Residents keep it from going on; a clear check confirms.
    assert.equal(await page.isVisible('[data-home-menu-list]'), false, 'the header menu starts closed');
    await page.click('[data-home-menu]');
    assert.equal(await page.isVisible('[data-home-menu-list]'), true);
    await page.click('[data-home-action="rename"]');
    await page.fill('input[name="new_project_id"]', 'Alpha2');
    await page.evaluate(() => { f.dryRun = { busy_resident_ids: ['resident_x'], blockers: [] }; });
    await page.click('[data-settings-submit]');
    await page.waitForSelector('.projectDryRun');
    assert.equal(await page.$eval('[data-settings-submit]', button => button.textContent), '检查', 'busy Residents block the rename');
    await page.evaluate(() => { f.dryRun = { busy_resident_ids: [], blockers: [] }; });
    await page.click('[data-settings-submit]');
    await page.waitForFunction(() => document.querySelector('[data-settings-submit]')?.textContent === '确认改名');
    await page.click('[data-settings-submit]');
    await page.waitForFunction(() => f.renamed === 'Alpha2');
    assert.deepEqual(await page.evaluate(() => f.calls.filter(([name]) => name === 'RenameProject').map(([, args]) => args)), [
      { project_id: 'Alpha', new_project_id: 'Alpha2', dry_run: true },
      { project_id: 'Alpha', new_project_id: 'Alpha2', dry_run: true },
      { project_id: 'Alpha', new_project_id: 'Alpha2' }]);

    // Delete: only after typing the exact Project ID.
    await page.click('[data-home-menu]');
    await page.click('[data-home-action="delete"]');
    assert.equal(await page.$eval('[data-settings-submit]', button => button.disabled), true);
    await page.fill('input[name="confirm_id"]', 'Alph');
    assert.equal(await page.$eval('[data-settings-submit]', button => button.disabled), true);
    await page.fill('input[name="confirm_id"]', 'Alpha');
    await page.click('[data-settings-submit]');
    await page.waitForFunction(() => f.deleted === 'Alpha');
    assert.deepEqual(await page.evaluate(() => f.calls.filter(([name]) => name === 'DeleteProject').map(([, args]) => args)), [{ project_id: 'Alpha' }]);

    // Deadlines.
    const deadlines = await page.evaluate(() => {
      const now = new Date(2026, 9, 20);
      return ['2026-10-20', '2026-10-21', '2026-10-30', '2026-10-19', 'soon', ''].map(value => PortalProjectCommon.formatDeadline(value, now));
    });
    assert.deepEqual(deadlines, [
      { label: '今天截止', tone: 'warning' }, { label: '还剩 1 天', tone: 'warning' }, { label: '还剩 10 天', tone: '' },
      { label: '已逾期 1 天', tone: 'danger' }, { label: '日期无效', tone: 'danger' }, { label: '', tone: '' }]);

    // In English the list and the Project page carry no untranslated Chinese.
    const english = await browser.newPage();
    english.on('pageerror', error => errors.push(error.message));
    await english.goto('about:blank');
    await english.evaluate(() => { window.__PORTAL_LANGUAGE__ = 'en'; window.PortalCompatibility = { identifier: name => `portal-${name}` }; });
    for (const name of ['portal_text', 'portal_translations', 'portal_i18n']) {
      await english.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    await english.evaluate(({ projects, alpha }) => {
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = { escapeHtml, mountIcons() {}, randomUUID: () => 'fixture', asyncStateHtml: state => `<div>${escapeHtml(state.title)}</div>` };
      window.PortalUI = { toast() {}, formatDateTime: value => String(value).slice(0, 10), copyText: async () => {}, createDialogController: () => ({ open() {}, close() {}, destroy() {} }) };
      const pageStates = {};
      window.PortalPageState = { create: name => ({ read: () => ({ ...(pageStates[name] || {}) }), write: patch => { pageStates[name] = { ...(pageStates[name] || {}), ...patch }; } }) };
      window.PortalIdentity = { username: () => 'fixture' };
      window.PortalResourceRoutes = { routes: [{ resource_type: 'decision', detail_kind: 'knowledge' }, { resource_type: 'paper', detail_kind: 'paper' }] };
      window.PortalAvatarEditor = { loadAssets: async () => {}, read: async () => null, mount: () => ({ destroy() {} }), remember() {} };
      window.MD = { render: text => `<p>${escapeHtml(text)}</p>` };
      const answers = {
        ListProjects: { projects, status_vocab: ['not_started', 'active', 'paused', 'done', 'archived'] }, GetProject: alpha,
        ListResidentStates: { states: [{ resident_id: 'resident_aaaaaaaaaaaa', state: { work_state: 'running' } }] },
        GetResidentSummaries: { summaries: [] },
        GetResearchMap: { project_id: 'Alpha', revision: 2, updated_at: '2026-10-20T10:00:00Z', relations: [], layout: {}, notes: [], outputs: {},
          directions: [{ direction_id: 'direction_aaaaaaaaaaaa', parent_id: '', title: 'Grasping', stance: 'focus', teams: [] }] },
      };
      window.PortalInstructionClient = { execute: async name => JSON.parse(JSON.stringify(answers[name] || {})) };
    }, { projects: PROJECTS, alpha: ALPHA });
    for (const name of ['project_common', 'project_list', 'project_settings', 'project_map_layout', 'project_home']) {
      await english.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    const chinese = () => english.evaluate(() => {
      const found = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) if (/[一-鿿]/.test(walker.currentNode.nodeValue)) found.push(walker.currentNode.nodeValue.trim());
      for (const node of document.body.querySelectorAll('[title],[aria-label],[placeholder]')) {
        for (const name of ['title', 'aria-label', 'placeholder']) if (/[一-鿿]/.test(node.getAttribute(name) || '')) found.push(`${name}=${node.getAttribute(name)}`);
      }
      return found;
    });
    await english.evaluate(async () => {
      document.body.innerHTML = '<main id="list"></main><main id="home"></main>';
      await PortalProjectList.create({ root: document.getElementById('list') }).load();
      await PortalProjectHome.create({ root: document.getElementById('home') }).load('Alpha', { statuses: ['not_started', 'active', 'paused', 'done', 'archived'] });
    });
    assert.equal(await english.$$eval('#list .projectCard', cards => cards.length), 4);
    assert.match(await english.textContent('#home'), /Research map[\s\S]*Vision/);
    assert.deepEqual(await chinese(), []);
    assert.deepEqual(errors, []);
    console.log('PASS projects page');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
