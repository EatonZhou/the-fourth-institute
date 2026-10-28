'use strict';
// The activity rail on the real lineage page: Residents the user recently sent
// a command to appear newest first, and dismissing one removes only it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const A = 'resident_aaaaaaaaaaaa', B = 'resident_bbbbbbbbbbbb', LINEAGE = 'lineage_cccccccccccc';

test('recent Residents appear newest first and dismiss one at a time', async t => {
  const page = startWorkbenchPage({ answers: {
    '/api/me': { username: 'fixture' },
    '/api/agent-workbench/residents': { ready: true, principal_hierarchy: { projects: [], user_residents: [] },
      lineages: [{ lineage_id: LINEAGE, title: 'Lineage C', leader_resident_id: A }],
      residents: [{ resident_id: A, title: 'Alpha', lineage_id: LINEAGE }, { resident_id: B, title: 'Beta', lineage_id: LINEAGE }] },
  } });
  t.after(page.close);
  const doc = page.document;
  const items = () => [...doc.querySelectorAll('#activityRecentRail [data-activity-item]')];
  // The rail names a Resident from the Resident list, so wait for the list.
  await page.until(() => page.parts.PortalLineageWorkbenchRecent
    && page.parts.PortalLineageWorkbenchRecent.dependencies.state.residents?.length === 2);
  const { rememberResidentShortcutSession } = page.parts.PortalLineageWorkbenchRecent.exports;
  rememberResidentShortcutSession(A);
  rememberResidentShortcutSession(B);
  await page.until(() => items().length === 2);
  assert.deepEqual(items().map(item => item.dataset.activityItem), [`resident:${B}`, `resident:${A}`], 'the latest command comes first');
  assert.match(items()[0].getAttribute('title') || '', /Beta/);

  // Navigation can unload the previous Resident as the user moves between
  // lineages. Its persisted shortcut still owns its display name.
  const recent = page.parts.PortalLineageWorkbenchRecent;
  assert.equal(page.window.PortalConversationStore.recent().find(item => item.resident_id === A).title, 'Alpha');
  recent.dependencies.state.residents = [{ resident_id: A, title: '' }];
  recent.dependencies.state.detail = null;
  rememberResidentShortcutSession(A);
  recent.exports.renderActivityRecentRail();
  assert.match(items().find(item => item.dataset.activityItem === `resident:${A}`).title, /Alpha/);
  assert.equal(page.window.PortalConversationStore.recent().find(item => item.resident_id === A).title, 'Alpha');

  doc.querySelector(`#activityRecentRail [data-activity-dismiss="resident:${B}"]`).click();
  await page.until(() => items().length === 1);
  assert.equal(items()[0].dataset.activityItem, `resident:${A}`, 'only the dismissed Resident leaves');
  assert.deepEqual(page.errors, []);
});

test('old ID-only shortcuts restore names without navigation or changing command order', async t => {
  const residents = {
    [A]: { resident_id: A, title: 'vla', lineage_id: LINEAGE },
    [B]: { resident_id: B, title: '机箱', lineage_id: LINEAGE },
  };
  const page = startWorkbenchPage({
    answers: { '/api/me': { username: 'fixture' } },
    instructions: { GetResident: ({ resident_id }) => ({ resident: residents[resident_id] }) },
  });
  t.after(page.close);
  await page.until(() => page.parts.PortalLineageWorkbenchRecent && page.window.PortalIdentity.username());
  const store = page.window.PortalConversationStore;
  store.remember({ resident_id: A, title: A });
  store.remember({ resident_id: B });
  const before = store.recent().map(item => [item.resident_id, item.t]);
  const rail = page.parts.PortalLineageWorkbenchRecent.exports;
  rail.renderActivityRecentRail();
  await page.until(() => store.recent().every(item => item.title));
  assert.deepEqual(store.recent().map(item => [item.resident_id, item.t]), before);
  const item = id => page.document.querySelector(`#activityRecentRail [data-activity-item="resident:${id}"]`);
  const label = id => item(id)?.getAttribute('aria-label') || '';
  await page.until(() => label(A).includes('vla') && label(B).includes('机箱'));
  assert.match(label(A), /vla/);
  assert.match(label(B), /机箱/);
  assert.equal(item(A).textContent.trim(), 'vl');
  assert.equal(item(B).textContent.trim(), '机');
  store.remember({ resident_id: A, title: '', lineage_id: '' });
  assert.equal(store.recent()[0].title, 'vla', 'ID-only acknowledgements retain the cached title');
  store.dismissRecent(`resident:${B}`);
  store.updateRecentResident(residents[B]);
  assert.equal(store.recent().some(item => item.resident_id === B), false, 'late metadata cannot restore a dismissed shortcut');
  assert.deepEqual(page.errors, []);
});
