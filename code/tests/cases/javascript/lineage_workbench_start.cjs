'use strict';
// The lineage page starts as a browser loads it: every script lineage.html
// names runs in order, the workbench creates each of its module parts, and
// the first render shows the Resident list without a page error.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { STATIC, startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const workbench = fs.readFileSync(path.join(STATIC, 'lineage_workbench.js'), 'utf8');
const PARTS = [...new Set([...workbench.matchAll(/workbenchModule\("([A-Za-z]+)"\)/g)].map(match => match[1]))];

const RECOMMENDER = 'resident_cccccccccccc', MEMBER = 'resident_aaaaaaaaaaaa', LINEAGE = 'lineage_bbbbbbbbbbbb';

test('the lineage page starts with every workbench module part', async t => {
  const page = startWorkbenchPage({ answers: {
    '/api/me': { username: 'fixture', display_name: 'Fixture' },
    '/api/agent-workbench/residents': {
      ready: true,
      residents: [{ resident_id: MEMBER, title: 'Fixture Member', lineage_id: LINEAGE, owner: 'fixture' }],
      lineages: [{ lineage_id: LINEAGE, title: 'Fixture Lineage', owner: 'fixture' }],
      recommendation_resident: { resident_id: RECOMMENDER, title: 'Recommender', lineage_id: 'lineage_dddddddddddd' },
      recommendation_status: 'available',
      principal_hierarchy: { projects: [], user_residents: [] },
    },
  } });
  t.after(page.close);
  const which = () => page.document.querySelector(`#agentList .whichResidentWrap [data-resident-card="${RECOMMENDER}"]`);
  await page.until(which);
  assert.match(which().textContent, /Which Resident/, 'the Which Resident part renders the recommender card');
  assert.ok(page.requests.some(request => request.path === '/api/agent-workbench/residents'));
  assert.ok(PARTS.length, 'the workbench takes its parts from module scripts');
  assert.deepEqual(PARTS.filter(name => !page.parts[name]), [], 'the workbench creates every part it names');
  assert.deepEqual(page.errors, [], 'no script, page or console error');
});
