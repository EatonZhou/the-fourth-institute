'use strict';
// The real research map page and its direction drawer in Chromium.
// Instructions are answered by fixtures and recorded; nothing reaches an instance.
const path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../management_portal');
const { chromium } = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));

const at = '2026-10-28T09:00:00Z';
const direction = (id, title, stance, extra = {}) => ({
  direction_id: id, parent_id: '', title, question: '', approach: '', stance, teams: [], expectation: '', conclusion: null,
  proposed_by: 'weihao', proposal_reason: '', created_at: at, created_by: 'weihao', updated_at: at, updated_by: 'weihao', ...extra,
});
const MAP = {
  project_id: 'Alpha', revision: 7, goal: 'Robots that learn new tasks in a day', layout: {}, layout_revision: 0,
  updated_at: at, updated_by: 'weihao',
  directions: [
    direction('direction_aaaaaaaaaaaa', 'Grasping', 'focus', { question: 'Can one policy grasp any object?', teams: ['lineage_bbbbbbbbbbbb'] }),
    direction('direction_bbbbbbbbbbbb', 'Tactile sensing', 'explore', { parent_id: 'direction_aaaaaaaaaaaa' }),
    direction('direction_cccccccccccc', 'Sim2Real', 'candidate', { proposed_by: 'resident_aaaaaaaaaaaa', proposal_reason: 'Three papers this week' }),
    direction('direction_dddddddddddd', 'Old idea', 'dropped'),
  ],
  relations: [{ from: 'direction_bbbbbbbbbbbb', to: 'direction_cccccccccccc', kind: 'builds_on' }],
  notes: [{ note_id: 'note_1', direction_id: 'direction_aaaaaaaaaaaa', kind: 'progress', text: 'Baseline grasps 80%', refs: [], noted_at: at, noted_by: 'resident_cccccccccccc', revision: 6 }],
  outputs: { direction_aaaaaaaaaaaa: [{ resource_id: 'decision_aaaaaaaaaaaa', resource_type: 'decision', title: 'Grasp design', url: '/resource/knowledge?item=decision_aaaaaaaaaaaa', modified_at: at }] },
};
const PROJECT = {
  project: { resource_id: 'Alpha', status: 'active', description: 'Alpha study', avatar: null },
  team: { project_resident_id: 'resident_aaaaaaaaaaaa', project_lineage_id: 'lineage_aaaaaaaaaaaa', resident_count: 9, running_resident_count: 1,
    lineages: [{ lineage_id: 'lineage_bbbbbbbbbbbb', title: 'Vision', leader_resident_id: 'resident_cccccccccccc', member_count: 4, running_count: 1 }] },
};
const HISTORY = [
  { revision: 1, changed_at: '2026-10-20T09:00:00Z', changed_by: 'weihao', changes: [{ op: 'add_direction', direction_id: 'direction_aaaaaaaaaaaa', title: 'Grasping', stance: 'explore' }],
    directions: [{ direction_id: 'direction_aaaaaaaaaaaa', parent_id: '', title: 'Grasping', stance: 'explore', teams: [] }] },
  { revision: 2, changed_at: '2026-10-21T09:00:00Z', changed_by: 'resident_aaaaaaaaaaaa', changes: [{ op: 'add_direction', direction_id: 'direction_cccccccccccc', title: 'Sim2Real', stance: 'candidate' }],
    directions: [{ direction_id: 'direction_aaaaaaaaaaaa', parent_id: '', title: 'Grasping', stance: 'explore', teams: [] },
      { direction_id: 'direction_cccccccccccc', parent_id: '', title: 'Sim2Real', stance: 'candidate', teams: [] }] },
  { revision: 3, changed_at: '2026-10-22T09:00:00Z', changed_by: 'weihao', changes: [
    { op: 'update_direction', direction_id: 'direction_aaaaaaaaaaaa', fields: { stance: 'focus' } },
    { op: 'add_note', direction_id: 'direction_aaaaaaaaaaaa', kind: 'decision', text: '设为主攻' }],
    directions: [{ direction_id: 'direction_aaaaaaaaaaaa', parent_id: '', title: 'Grasping', stance: 'focus', teams: [] },
      { direction_id: 'direction_cccccccccccc', parent_id: '', title: 'Sim2Real', stance: 'candidate', teams: [] }] },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('about:blank');
    await page.evaluate(({ map, project, history }) => {
      const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
      window.PortalI18n = { t: x => String(x), text: tag, html: tag, locale: () => 'zh-CN' };
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = {
        escapeHtml, mountIcons() {}, randomUUID: () => 'fixture',
        asyncStateHtml: state => `<div class="portalAsyncState" data-state="${state.state}">${escapeHtml(state.title)}</div>`,
      };
      window.f = { calls: [], toasts: [], answers: [], conflict: false };
      window.PortalUI = {
        toast: (message, kind) => f.toasts.push([message, kind]),
        formatDateTime: value => String(value).slice(0, 10),
        prompt: async () => f.answers.shift() ?? null,
        confirm: async () => true,
      };
      window.PortalResourceRoutes = { routes: [{ resource_type: 'decision', detail_kind: 'knowledge' }] };
      window.PortalInstructionClient = { execute: async (name, args) => {
        f.calls.push([name, JSON.parse(JSON.stringify(args))]);
        if (name === 'GetResearchMap') return JSON.parse(JSON.stringify(map));
        if (name === 'GetProject') return JSON.parse(JSON.stringify(project));
        if (name === 'ListResidentStates') return { states: [{ resident_id: 'resident_cccccccccccc', state: { work_state: 'running' } }] };
        if (name === 'GetResidentSummaries') return { summaries: [{ resident_id: 'resident_cccccccccccc', summary: { current_focus: 'Tuning the gripper', recent_results: [] } }] };
        if (name === 'ListResearchMapChanges') return { changes: JSON.parse(JSON.stringify(history)) };
        if (name === 'UpdateResearchMapLayout') return { layout: args.positions, layout_revision: 1 };
        if (name === 'ApplyResearchMapChanges') {
          if (f.conflict) { f.conflict = false; throw Object.assign(new Error('changed'), { code: 'instruction.version_conflict' }); }
          return { revision: 8, created: args.changes.some(change => change.key) ? { [args.changes[0].key]: 'direction_eeeeeeeeeeee' } : {}, applied: args.changes };
        }
        return {};
      } };
    }, { map: MAP, project: PROJECT, history: HISTORY });
    for (const name of ['project_common', 'project_map_layout', 'project_map_drawer', 'project_map']) {
      await page.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    await page.addStyleTag({ path: path.join(root, 'static', 'project.css') });
    await page.addStyleTag({ path: path.join(root, 'static', 'project_map.css') });
    await page.evaluate(async () => {
      document.body.innerHTML = '<main id="map" style="height:900px"></main>';
      f.selected = [];
      window.map = PortalProjectMap.create({ root: document.getElementById('map'), onSelectionChange: item => f.selected.push(item?.direction_id || '') });
      await map.load('Alpha');
    });
    const applied = () => page.evaluate(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').map(([, args]) => args));
    const lastApplied = async () => (await applied()).at(-1);
    const nodes = () => page.$$eval('[data-node]', items => items.map(item => [item.dataset.node, item.dataset.stance]));

    // The map: dropped directions hidden until asked; the team's Leader is live on its direction.
    assert.deepEqual(await nodes(), [
      ['direction_aaaaaaaaaaaa', 'focus'], ['direction_bbbbbbbbbbbb', 'explore'], ['direction_cccccccccccc', 'candidate']]);
    assert.equal(await page.$eval('.mapGoal p', node => node.textContent), 'Robots that learn new tasks in a day');
    assert.equal(await page.$eval('.mapRevision', node => node.textContent.startsWith('修订 7')), true);
    await page.waitForSelector('[data-node="direction_aaaaaaaaaaaa"].is-running');
    assert.equal(await page.$eval('[data-node="direction_aaaaaaaaaaaa"] .mapNodeCount', node => node.textContent), '1');
    assert.equal(await page.$eval('[data-node="direction_aaaaaaaaaaaa"] .mapNodeLine', node => node.textContent), 'Baseline grasps 80%');
    assert.equal(await page.$$eval('.mapRelation', items => items.length), 1);
    await page.check('[data-map-dropped]');
    assert.equal((await nodes()).length, 4);
    await page.uncheck('[data-map-dropped]');

    // A candidate on the map: accepting makes it explore with a decision; discarding drops it with one.
    await page.click('[data-accept="direction_cccccccccccc"]');
    await page.waitForFunction(() => f.calls.some(([name]) => name === 'ApplyResearchMapChanges'));
    assert.deepEqual(await lastApplied(), { project_id: 'Alpha', expected_revision: 7, changes: [
      { op: 'update_direction', direction_id: 'direction_cccccccccccc', fields: { stance: 'explore' } },
      { op: 'add_note', direction_id: 'direction_cccccccccccc', kind: 'decision', text: '接纳为探索' }] });
    await page.click('[data-node="direction_cccccccccccc"] [data-discard]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').length === 2);
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'update_direction', direction_id: 'direction_cccccccccccc', fields: { stance: 'dropped' } },
      { op: 'add_note', direction_id: 'direction_cccccccccccc', kind: 'decision', text: '丢弃了这个候选方向' }]);
    assert.equal(await page.isVisible('[data-map-drawer]'), false, 'buttons on a node do not open it');

    // Opening a direction: its question, team with the Leader's focus, and a timeline of notes and outputs.
    await page.click('[data-node="direction_aaaaaaaaaaaa"] .mapNodeTitle');
    await page.waitForSelector('.mapDrawer');
    assert.deepEqual(await page.evaluate(() => f.selected), ['direction_aaaaaaaaaaaa']);
    assert.equal(await page.$eval('.mapDrawer h3', node => node.textContent), 'Grasping');
    assert.equal(await page.$eval('.mapTeamFocus', node => node.textContent), 'Tuning the gripper');
    assert.deepEqual(await page.$$eval('.mapTimelineItem', items => items.map(item => item.dataset.kind)).then(kinds => kinds.sort()), ['output', 'progress']);

    // The people's stance is one decision on the map.
    await page.click('.mapDrawer [data-stance="watch"]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').length === 3);
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'update_direction', direction_id: 'direction_aaaaaaaaaaaa', fields: { stance: 'watch' } },
      { op: 'add_note', direction_id: 'direction_aaaaaaaaaaaa', kind: 'decision', text: '设为观望' }]);
    await page.evaluate(() => f.answers.push('One policy grasps 9 of 10 objects'));
    await page.click('.mapDrawer [data-conclude]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').length === 4);
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'update_direction', direction_id: 'direction_aaaaaaaaaaaa', fields: { stance: 'concluded', conclusion: 'One policy grasps 9 of 10 objects' } },
      { op: 'add_note', direction_id: 'direction_aaaaaaaaaaaa', kind: 'decision', text: '收束：One policy grasps 9 of 10 objects' }]);

    // Editing the direction sends only what changed; a note is one add_note.
    await page.click('.mapDrawer [data-edit]');
    await page.fill('.mapEditForm textarea[name="approach"]', 'Large-scale teleoperation data');
    await page.click('.mapEditForm button[type="submit"]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').length === 5);
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'update_direction', direction_id: 'direction_aaaaaaaaaaaa', fields: { approach: 'Large-scale teleoperation data' } }]);
    await page.fill('.mapNoteForm input[name="text"]', 'Check the new gripper first');
    await page.click('.mapNoteForm button[type="submit"]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').length === 6);
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'add_note', direction_id: 'direction_aaaaaaaaaaaa', kind: 'note', text: 'Check the new gripper first' }]);

    // Handing a direction to a team: the map names it and its Leader hears it from the user.
    await page.evaluate(() => map.select('direction_bbbbbbbbbbbb'));
    await page.selectOption('.mapDrawer [data-team-pick]', 'lineage_bbbbbbbbbbbb');
    await page.waitForFunction(() => f.calls.some(([name]) => name === 'DeliverMessage'));
    assert.deepEqual((await lastApplied()).changes, [
      { op: 'update_direction', direction_id: 'direction_bbbbbbbbbbbb', fields: { teams: ['lineage_bbbbbbbbbbbb'] } },
      { op: 'add_note', direction_id: 'direction_bbbbbbbbbbbb', kind: 'decision', text: '交给团队「Vision」' }]);
    const delivered = await page.evaluate(() => f.calls.filter(([name]) => name === 'DeliverMessage').map(([, args]) => args));
    assert.equal(delivered[0].resident_id, 'resident_cccccccccccc');
    assert.equal(delivered[0].business_ref, 'direction_bbbbbbbbbbbb');
    assert.match(delivered[0].message.content_parts[0].text, /研究方向《Tactile sensing》（direction_bbbbbbbbbbbb）.*\/projects\?direction=direction_bbbbbbbbbbbb/);
    assert.match(delivered[0].message.content_parts[0].text, /links\.related/);

    // A new team is the Project Resident's job.
    await page.click('.mapDrawer [data-form-team]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'DeliverMessage').length === 2);
    const formed = await page.evaluate(() => f.calls.filter(([name]) => name === 'DeliverMessage').at(-1)[1]);
    assert.equal(formed.resident_id, 'resident_aaaaaaaaaaaa');
    assert.match(formed.message.content_parts[0].text, /CreateLineage 在项目 Alpha/);

    // Someone else changed the map first: read it again and say so.
    const reads = await page.evaluate(() => f.calls.filter(([name]) => name === 'GetResearchMap').length);
    await page.evaluate(() => { f.conflict = true; });
    await page.fill('.mapNoteForm input[name="text"]', 'Late note');
    await page.click('.mapNoteForm button[type="submit"]');
    await page.waitForFunction(() => f.toasts.some(([, kind]) => kind === 'info'));
    assert.equal(await page.evaluate(() => f.calls.filter(([name]) => name === 'GetResearchMap').length), reads + 1);

    // A direction dragged keeps its place.
    await page.click('.mapDrawer [data-close]');
    const box = await page.$eval('[data-node="direction_cccccccccccc"] .mapNodeTitle', node => { const rect = node.getBoundingClientRect(); return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }; });
    await page.mouse.move(box.x, box.y);
    await page.mouse.down();
    await page.mouse.move(box.x + 60, box.y + 40, { steps: 4 });
    await page.mouse.up();
    await page.waitForFunction(() => f.calls.some(([name]) => name === 'UpdateResearchMapLayout'));
    const moved = await page.evaluate(() => f.calls.find(([name]) => name === 'UpdateResearchMapLayout')[1]);
    assert.deepEqual(Object.keys(moved.positions), ['direction_cccccccccccc']);

    // Replay: every revision on a timeline; the map shows each in turn and edits nothing.
    await page.click('[data-map-replay]');
    await page.waitForSelector('.mapReplay');
    assert.equal(await page.$$eval('.mapTick', ticks => ticks.length), 3);
    assert.equal(await page.$$eval('.mapTick.is-decision', ticks => ticks.length), 1);
    assert.deepEqual(await nodes(), [['direction_aaaaaaaaaaaa', 'focus'], ['direction_cccccccccccc', 'candidate']]);
    await page.click('[data-replay-index="0"]');
    assert.deepEqual(await nodes(), [['direction_aaaaaaaaaaaa', 'explore']]);
    assert.equal(await page.$eval('.mapReplayInfo p', node => node.textContent), '新增「Grasping」（探索）');
    assert.equal(await page.$('[data-map-add]'), null, 'nothing is added while replaying');
    await page.click('[data-map-replay]');
    assert.equal(await page.$('.mapReplay'), null);
    assert.equal((await nodes()).length, 3);

    // A change published by someone else reloads the map.
    const before = await page.evaluate(() => f.calls.filter(([name]) => name === 'GetResearchMap').length);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('portal:change', { detail: { projection_changes: { 'research-map': { Alpha: {} } } } })));
    await page.waitForFunction(count => f.calls.filter(([name]) => name === 'GetResearchMap').length === count + 1, before);

    // An empty map invites the first direction.
    await page.evaluate(async () => {
      const execute = PortalInstructionClient.execute;
      PortalInstructionClient.execute = async (name, args) => name === 'GetResearchMap'
        ? { project_id: args.project_id, revision: 0, goal: '', directions: [], relations: [], layout: {}, layout_revision: 0, notes: [], outputs: {} }
        : execute(name, args);
      document.body.innerHTML = '<main id="empty" style="height:600px"></main>';
      window.empty = PortalProjectMap.create({ root: document.getElementById('empty') });
      await empty.load('Beta');
      f.answers.push({ value: 'World models', choice: 'focus' });
    });
    await page.click('.mapEmpty [data-map-add]');
    await page.waitForFunction(() => f.calls.filter(([name]) => name === 'ApplyResearchMapChanges').at(-1)[1].project_id === 'Beta');
    assert.deepEqual(await lastApplied(), { project_id: 'Beta', expected_revision: 0, changes: [
      { op: 'add_direction', key: 'new', title: 'World models', stance: 'focus' }] });

    // In English the map, the drawer and the replay carry no untranslated Chinese.
    const english = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    english.on('pageerror', error => errors.push(error.message));
    await english.goto('about:blank');
    await english.evaluate(() => { window.__PORTAL_LANGUAGE__ = 'en'; window.PortalCompatibility = { identifier: name => `portal-${name}` }; });
    for (const name of ['portal_text', 'portal_translations', 'portal_i18n']) {
      await english.addScriptTag({ path: path.join(root, 'static', name + '.js') });
    }
    await english.evaluate(({ map, project, history }) => {
      const escapeHtml = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      window.PortalUIPrimitives = { escapeHtml, mountIcons() {}, randomUUID: () => 'fixture', asyncStateHtml: state => `<div>${escapeHtml(state.title)}</div>` };
      window.PortalUI = { toast() {}, formatDateTime: value => String(value).slice(0, 10), prompt: async () => null, confirm: async () => true };
      window.PortalResourceRoutes = { routes: [{ resource_type: 'decision', detail_kind: 'knowledge' }] };
      const answers = { GetResearchMap: map, GetProject: project, ListResearchMapChanges: { changes: history },
        ListResidentStates: { states: [{ resident_id: 'resident_cccccccccccc', state: { work_state: 'running' } }] },
        GetResidentSummaries: { summaries: [] } };
      window.PortalInstructionClient = { execute: async name => JSON.parse(JSON.stringify(answers[name] || {})) };
    }, { map: MAP, project: PROJECT,
      history: HISTORY.map(entry => ({ ...entry, changes: entry.changes.map(change => change.op === 'add_note' ? { ...change, text: 'decided' } : change) })) });
    for (const name of ['project_common', 'project_map_layout', 'project_map_drawer', 'project_map']) {
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
      document.body.innerHTML = '<main id="map" style="height:900px"></main>';
      window.map = PortalProjectMap.create({ root: document.getElementById('map') });
      await map.load('Alpha');
      map.select('direction_aaaaaaaaaaaa');
    });
    await english.waitForSelector('.mapDrawer .mapTeamLive');
    await english.check('[data-map-dropped]');
    assert.deepEqual(await chinese(), []);
    await english.evaluate(() => map.select('direction_cccccccccccc'));
    assert.deepEqual(await chinese(), []);
    await english.click('[data-map-replay]');
    await english.waitForSelector('.mapReplay');
    assert.deepEqual(await chinese(), []);
    assert.deepEqual(errors, []);
    console.log('PASS project map');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
