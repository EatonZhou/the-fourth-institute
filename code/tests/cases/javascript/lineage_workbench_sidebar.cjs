'use strict';
// The Resident sidebar on the real lineage page: both the Agent list and the
// favourites list show Which Resident and the personal assistant's facade, never
// the personal AMA itself; the facade follows its Lineage's title and leaves both
// lists when it is no longer reachable.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const AMA = 'resident_111111111111', FACADE = 'resident_222222222222', RECOMMENDER = 'resident_333333333333';
const LINEAGE = 'lineage_aaaaaaaaaaaa', FAMILY = 'family_444444444444';

test('both lists show Which Resident and the assistant facade, follow its title and drop it when unreachable', async t => {
  const assistant = { title: '我的私人助手', available: true, denied: false, resident_id: FACADE };
  const page = startWorkbenchPage({
    answers: {
      '/api/me': { username: 'fixture' },
      '/api/agent-workbench/residents': { ready: true, lineages: [],
        residents: [{ resident_id: AMA, title: 'Ask Me Anything', lineage_id: LINEAGE }],
        recommendation_resident: { resident_id: RECOMMENDER, title: 'Recommender', lineage_id: 'lineage_bbbbbbbbbbbb' },
        recommendation_status: 'available', principal_hierarchy: { projects: [], user_residents: [] } },
      '/api/ui-state/associations': at => {
        const namespace = at.searchParams.get('namespace');
        if (namespace === 'lineage-workbench.personal-ama') return { items: [{ key: 'primary', value: { resident_id: AMA } }] };
        if (namespace !== 'lineage-workbench.personal-assistant' || !assistant.available) return { items: [] };
        return { items: [{ key: 'primary', value: { resident_id: assistant.resident_id, lineage_id: LINEAGE,
          family_id: FAMILY, presentation: { kind: 'personal-assistant' } } }] };
      },
      '/api/agent-workbench/residents/navigation-target': at => ({ resident_id: at.searchParams.get('id'), lineage_id: LINEAGE }),
    },
    instructions: {
      GetLineage: args => {
        assert.equal(args.lineage_id, LINEAGE);
        if (assistant.denied) throw Object.assign(new Error('permission denied'), { code: 'permission.denied' });
        return { lineage: { lineage_id: LINEAGE, title: assistant.title } };
      },
    },
  });
  t.after(page.close);
  const doc = page.document;
  const lists = () => ['agentList', 'favoriteAgentList'].map(id => doc.getElementById(id));
  const facade = list => list.querySelector('.personalAssistantResident');
  const shows = title => lists().every(list => facade(list)?.querySelector('.agentTitle')?.textContent === title);
  await page.until(() => shows(assistant.title));
  for (const list of lists()) {
    assert.deepEqual([...list.firstElementChild.children].slice(0, 2).map(node => node.className),
      ['whichResidentWrap', 'personalAssistantResident'], 'Which Resident leads the list, the facade follows');
    assert.equal(facade(list).querySelector('[data-resident-select]').dataset.residentSelect, FACADE);
    assert.equal(list.querySelector(`[data-resident-select="${AMA}"]`), null, 'the personal AMA is reached through its facade');
  }

  // A rename of the facade's Lineage reaches both lists.
  assistant.title = '改名后的个人空间';
  page.window.dispatchEvent(new page.window.CustomEvent('portal:change', { detail: { changed: { lineage: [LINEAGE] } } }));
  await page.until(() => shows(assistant.title));

  const sidebar = page.parts.PortalLineageWorkbenchSidebar;
  assert.equal(sidebar.dependencies.state.personalAssistant.family_id, FAMILY, 'the facade keeps its Family');
  const { refreshPersonalAssistantShortcut } = sidebar.exports;
  for (const unreachable of [{ available: false }, { available: true, denied: true }, { denied: false, resident_id: 'resident_invalid' }]) {
    Object.assign(assistant, unreachable);
    await refreshPersonalAssistantShortcut();
    assert.ok(lists().every(list => !facade(list) && list.querySelector('.whichResidentWrap')),
      `the facade leaves both lists: ${JSON.stringify(unreachable)}`);
  }
  assert.deepEqual(page.errors, []);
});

test('a Project without its conversation is not shown as denied, and only a reachable one opens', async t => {
  // Each Project has its own Lineage, so the default non-empty filter lists it.
  const project = (digit, fields) => ({ project_id: `project_${digit.repeat(12)}`, lineage_id: `lineage_${digit.repeat(12)}`,
    title: `Project ${digit}`, lineages: [], ...fields });
  const page = startWorkbenchPage({ answers: {
    '/api/me': { username: 'fixture' },
    '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { user_residents: [], projects: [
      project('1', { accessible: false, can_initialize: true, resident_id: '' }),
      project('2', { accessible: false, can_initialize: false, project_readable: false }),
      project('3', { accessible: true, resident_id: 'resident_555555555555' }),
    ] } },
  } });
  t.after(page.close);
  const card = digit => page.document.querySelector(`#agentList [data-resident-project-group="lineage:project_${digit.repeat(12)}"]`);
  await page.until(() => card('1') && card('2') && card('3'));
  const locked = digit => Boolean(card(digit).querySelector('.principalAccessLock'));
  const opens = digit => !card(digit).querySelector('[data-principal-open]').disabled;
  assert.equal(locked('1'), false, 'a missing conversation binding is not denied access');
  assert.equal(opens('1'), false, 'absence does not create a chat target');
  assert.equal(locked('2'), true, 'a Project the user may not read is locked');
  assert.equal(card('2').querySelector('[data-agent-menu]').disabled, true);
  assert.equal(locked('3'), false);
  assert.equal(opens('3'), true);
  assert.deepEqual(page.errors, []);
});
