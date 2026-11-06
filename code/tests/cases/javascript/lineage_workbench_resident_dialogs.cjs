'use strict';
// The Resident dialogs part on the real lineage page: the edit dialog shows
// the Resident's name, refuses an empty name, and saves a new name through
// UpdateResident before it closes and rereads the Resident list.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const MEMBER = 'resident_aaaaaaaaaaaa', LINEAGE = 'lineage_bbbbbbbbbbbb';

test('failed save keeps the draft and cancellation does not claim a pending write was cancelled', async t => {
  let denied = true;
  const page = startWorkbenchPage({
    answers: {
      '/api/me': {username:'fixture'},
      '/api/agent-workbench/residents': {ready:true,residents:[],lineages:[],principal_hierarchy:{projects:[],user_residents:[]}},
    },
    instructions: {
      GetResident: args => ({resident:{resident_id:args.resident_id,lineage_id:LINEAGE,title:'Fixture Member'}}),
      UpdateResident: args => {
        if (denied) throw Object.assign(new Error('fixture permission denied'),{code:'instruction.permission_denied'});
        return {resident:{resident_id:args.resident_id,lineage_id:LINEAGE,title:args.title}};
      },
    },
  });
  t.after(page.close);
  await page.until(()=>page.parts.PortalLineageWorkbenchResidentDialogs
    && page.requests.some(r=>r.path==='/api/agent-workbench/residents'));
  const {exports:dialogs} = page.parts.PortalLineageWorkbenchResidentDialogs;
  const fetch = page.window.fetch;
  let release, calls = 0;
  page.window.fetch = async (input,init={}) => {
    if (typeof init.body==='string' && JSON.parse(init.body).instruction==='UpdateResident') {
      calls++;await new Promise(resolve=>{release=resolve;});
    }
    return fetch(input,init);
  };
  const $ = id => page.document.getElementById(id);
  const submit = ()=>$('residentEditForm').dispatchEvent(new page.window.Event('submit',{cancelable:true}));
  dialogs.openResidentEditModal({resident_id:MEMBER,title:'Fixture Member'});
  $('residentEditTitleInput').value = 'Retained draft';
  submit();submit();
  await page.until(()=>calls===1);
  assert.equal(calls,1,'duplicate submit during save is suppressed');
  assert.equal($('residentEditSubmit').disabled,true);
  dialogs.closeResidentEditModal();
  assert.equal($('residentEditOverlay').getAttribute('aria-hidden'),'false','pending write cannot be falsely dismissed as cancelled');
  release();
  await page.until(()=>$('residentEditSubmit').disabled===false);
  assert.equal($('residentEditOverlay').getAttribute('aria-hidden'),'false');
  assert.equal($('residentEditTitleInput').value,'Retained draft');
  assert.match(page.document.body.textContent,/fixture permission denied/);
  denied=false;page.window.fetch=fetch;submit();
  await page.until(()=>$('residentEditOverlay').getAttribute('aria-hidden')==='true');
  const writes=()=>page.requests.filter(r=>r.body?.instruction==='UpdateResident');
  assert.deepEqual(writes().map(r=>r.body.arguments.title),['Retained draft','Retained draft']);
  dialogs.openResidentEditModal({resident_id:MEMBER,title:'Retained draft'});
  $('residentEditTitleInput').value='Cancelled edit';dialogs.closeResidentEditModal();
  assert.equal($('residentEditOverlay').getAttribute('aria-hidden'),'true');
  assert.equal(writes().length,2,'cancel without saving sends no mutation');
  assert.deepEqual(page.errors,[]);
});

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
  // Wait for the shared initial catalog load; sidebar consumers reuse it.
  await page.until(() => page.parts.PortalLineageWorkbenchResidentDialogs && residentReads() >= 1);
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
