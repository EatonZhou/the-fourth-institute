'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

test('shortcut A-B-C repeats restore while earlier background reads are pending', async t => {
  const ids = ['resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb', 'resident_cccccccccccc'];
  const residents = ids.map((resident_id, i) => ({ resident_id, title: `Shortcut ${i}`, lineage_id: `lineage_${String(i + 1).repeat(12)}` }));
  const page = startWorkbenchPage({ answers: {
    '/api/me': { username: 'fixture' },
    '/api/agent-workbench/residents': { ready: true, principal_hierarchy: { projects: [], user_residents: [] },
      lineages: residents.map(r => ({ lineage_id: r.lineage_id, title: r.title, leader_resident_id: r.resident_id })), residents },
    '/api/agent-workbench/residents/projection': url => {
      const resident = residents.find(r => r.resident_id === url.searchParams.get('id'));
      return { resident_id: resident.resident_id, resident,
        chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: resident.resident_id }, composer: { can_send: true } } };
    },
  }, instructions: {
    GetLineage: ({ lineage_id }) => ({ lineage: { lineage_id, residents: residents.filter(r => r.lineage_id === lineage_id), components: [], wires: [] } }),
    GetResident: ({ resident_id }) => ({ resident: residents.find(r => r.resident_id === resident_id) }),
  } });
  t.after(page.close);
  await page.until(() => page.parts.PortalLineageWorkbenchRecent?.dependencies.state.residents?.length === 3);
  let gated = false;
  const pending = [], calls = [];
  const fetch = page.window.fetch;
  page.window.fetch = async (input, init = {}) => {
    const body = init.body && JSON.parse(init.body);
    if (body?.instruction !== 'GetConversation') return fetch(input, init);
    const id = body.arguments.resident_id;
    calls.push(id);
    const snapshot = { resident: { resident_id: id }, cursor: 1, state: { work_state: 'idle' },
      controls: { can_send: true }, messages: [{ message_id: `${id}-body`, role: 'user', content_parts: [{ type: 'text', text: `Body for ${id}` }] }],
      input_states: [], queue: { items: [] }, activities: [], next_page_token: '' };
    if (gated) await new Promise(resolve => pending.push({ id, resolve }));
    return new Response(JSON.stringify({ status: 'committed', result: snapshot }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  const recent = page.parts.PortalLineageWorkbenchRecent;
  ids.forEach(id => recent.exports.rememberResidentShortcutSession(id));
  const click = id => page.document.querySelector(`#activityRecentRail [data-activity-item="resident:${id}"]`).click();
  const visible = id => page.document.querySelector(`[data-message-key="${id}-body"]`);
  for (const id of ids) {
    click(id);
    await page.until(() => visible(id));
  }
  assert.equal(calls.length, 3, 'one authoritative cold read per target');
  gated = true;
  const restored = [];
  for (const id of [...ids, ...ids, ids[0]]) {
    const before = calls.length;
    const start = Date.now();
    click(id);
    await page.until(() => visible(id), 1200);
    restored.push({ id, ms: Date.now() - start });
    await page.until(() => calls.length === before + 1);
    assert.equal(ids.filter(other => other !== id && visible(other)).length, 0, 'only the selected conversation is displayed');
  }
  assert.equal(pending.length, 7, 'each revisit starts one refresh without blocking display');
  // Finishing abandoned requests cannot replace the selected conversation.
  pending.slice(0, -1).forEach(p => p.resolve());
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(visible(ids[0]));
  assert.equal(ids.filter(other => other !== ids[0] && visible(other)).length, 0);
  pending.at(-1).resolve();
  assert.deepEqual(page.errors, []);
  t.diagnostic(JSON.stringify({ restored, requests: calls.length, heldRefreshes: pending.length }));
});
