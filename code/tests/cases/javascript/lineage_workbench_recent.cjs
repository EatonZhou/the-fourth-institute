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

  doc.querySelector(`#activityRecentRail [data-activity-dismiss="resident:${B}"]`).click();
  await page.until(() => items().length === 1);
  assert.equal(items()[0].dataset.activityItem, `resident:${A}`, 'only the dismissed Resident leaves');
  assert.deepEqual(page.errors, []);
});
