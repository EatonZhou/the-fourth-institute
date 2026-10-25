'use strict';
// The Resident dialogs part on the real lineage page: the edit dialog shows
// the Resident's name, refuses an empty name, and saves a new name through
// UpdateResident before it closes and rereads the Resident list.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const MEMBER = 'resident_aaaaaaaaaaaa', LINEAGE = 'lineage_bbbbbbbbbbbb';

test('the edit dialog saves a Resident name and refuses an empty one', async t => {
  const page = startWorkbenchPage({
    answers: {
      '/api/me': { username: 'fixture' },
      '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { projects: [], user_residents: [] } },
    },
    instructions: {
      GetResident: args => ({ resident: { resident_id: args.resident_id, lineage_id: LINEAGE, title: 'Fixture Member' } }),
      UpdateResident: args => ({ resident: { resident_id: args.resident_id, lineage_id: args.lineage_id, title: args.title } }),
    },
  });
  t.after(page.close);
  const $ = id => page.document.getElementById(id);
  const updates = () => page.requests.filter(request => request.body?.instruction === 'UpdateResident');
  const residentReads = () => page.requests.filter(request => request.path === '/api/agent-workbench/residents').length;
  const submit = () => $('residentEditForm').dispatchEvent(new page.window.Event('submit', { cancelable: true }));
  // The first load and the sidebar's own first refresh both read the list.
  await page.until(() => page.parts.PortalLineageWorkbenchResidentDialogs && residentReads() >= 2);
  const { openResidentEditModal } = page.parts.PortalLineageWorkbenchResidentDialogs.exports;

  openResidentEditModal({ resident_id: MEMBER, title: 'Fixture Member' });
  assert.equal($('residentEditOverlay').getAttribute('aria-hidden'), 'false');
  assert.match($('residentEditTitle').textContent, / · Fixture Member$/);
  assert.equal($('residentEditTitleInput').value, 'Fixture Member');

  $('residentEditTitleInput').value = '   ';
  submit();
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.deepEqual(updates(), [], 'an empty name is not sent');
  assert.equal($('residentEditOverlay').getAttribute('aria-hidden'), 'false', 'the dialog stays open');

  const reads = residentReads();
  $('residentEditTitleInput').value = ' Renamed ';
  submit();
  await page.until(() => $('residentEditOverlay').getAttribute('aria-hidden') === 'true');
  assert.deepEqual(updates().map(request => ({ ...request.body.arguments })),
    [{ lineage_id: LINEAGE, resident_id: MEMBER, title: 'Renamed' }]);
  await page.until(() => residentReads() > reads);
  assert.equal($('residentEditSubmit').disabled, false);
  assert.deepEqual(page.errors, []);
});
