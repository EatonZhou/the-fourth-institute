'use strict';
// The card menus on the real lineage page: a Lineage card offers Attach and
// Delete only when the server allows them, and deleting a Lineage asks for
// confirmation, sends DeleteLineage for it and removes its card.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const A = 'resident_aaaaaaaaaaaa', B = 'resident_bbbbbbbbbbbb';
const LA = 'lineage_aaaaaaaaaaaa', LB = 'lineage_bbbbbbbbbbbb', PROJECT = 'project_cccccccccccc';

function sidebar(deleted) {
  const lineages = [
    { lineage_id: LA, title: 'Lineage A', leader_resident_id: A, project_id: PROJECT, allows: { CreateResident: true, DeleteLineage: true } },
    { lineage_id: LB, title: 'Lineage B', leader_resident_id: B, project_id: PROJECT, allows: {} },
  ].filter(lineage => !deleted.has(lineage.lineage_id));
  return {
    ready: true, lineages,
    residents: lineages.map(lineage => ({ resident_id: lineage.leader_resident_id, title: `Leader of ${lineage.title}`, lineage_id: lineage.lineage_id })),
    principal_hierarchy: { user_residents: [], projects: [{ project_id: PROJECT, title: 'Project C',
      lineages: lineages.map(lineage => ({ lineage_id: lineage.lineage_id, title: lineage.title, resident_id: lineage.leader_resident_id })) }] },
  };
}

test('Lineage card menus follow the server permissions and delete a Lineage after confirmation', async t => {
  const deleted = new Set();
  const page = startWorkbenchPage({
    answers: { '/api/me': { username: 'fixture' }, '/api/agent-workbench/residents': () => sidebar(deleted) },
    instructions: {
      GetLineage: args => ({ lineage: { lineage_id: args.lineage_id, title: 'Lineage A', revision: 3, nodes: [{ id: A }] } }),
      DeleteLineage: args => { deleted.add(args.lineage_id); return { lineage_id: args.lineage_id }; },
    },
  });
  t.after(page.close);
  const doc = page.document;
  const menu = lineageId => doc.querySelector(`#agentList [data-agent-menu="lineage:${lineageId}"]`);
  const actions = () => [...doc.querySelectorAll('#agentFloatingMenu [data-agent-floating-action]')]
    .map(button => button.dataset.agentFloatingAction);
  await page.until(() => doc.querySelector('#agentList [data-resident-project-toggle]'));
  doc.querySelector('#agentList [data-resident-project-toggle]').click();
  await page.until(() => menu(LA) && menu(LB));

  menu(LB).click();
  assert.ok(!actions().includes('attach-resident') && !actions().includes('delete-lineage'),
    'a Lineage the server allows nothing on offers neither Attach nor Delete');
  menu(LA).click();
  await page.until(() => actions().includes('delete-lineage'));
  assert.ok(actions().includes('attach-resident'));

  doc.querySelector('#agentFloatingMenu [data-agent-floating-action="delete-lineage"]').click();
  await page.until(() => doc.querySelector('[data-portal-confirmation] [data-portal-confirm-accept]'));
  const deletions = () => page.requests.filter(request => request.body?.instruction === 'DeleteLineage');
  assert.deepEqual(deletions(), [], 'nothing is deleted before the user confirms');
  doc.querySelector('[data-portal-confirmation] [data-portal-confirm-accept]').click();
  await page.until(() => deletions().length && !menu(LA));
  assert.deepEqual(deletions().map(request => request.body.arguments), [{ lineage_id: LA }]);
  assert.ok(menu(LB), 'the other Lineage stays');
  assert.deepEqual(page.errors, []);
});
