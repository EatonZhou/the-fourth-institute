const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/lineage_workbench.js'), 'utf8');
const start = source.indexOf('      function projectCardHtml(');
const end = source.indexOf('\n      function ', start + 1);
assert(start >= 0 && end > start);
const tag = (s, ...v) => s.reduce((a, b, i) => a + b + (v[i] ?? ''), '');
const context = vm.createContext({
  state: {query: '', residentProjectGroups: {}},
  window: {PortalI18n: {html: tag, text: tag, t: s => s}},
  esc: String, favoriteStar: () => '', projectFavoriteSelectionId: () => '',
  renderRegisteredResidentItem: () => '',
});
vm.runInContext(source.slice(start, end), context);
const project = {id: 'lineage:project', project_id: 'project', title: 'Project', items: [], resident_id: ''};
const missing = context.projectCardHtml({...project, accessible: false, can_initialize: true});
assert(!missing.includes('principalAccessLock'), 'missing conversation binding is not denied access');
assert(missing.includes('data-principal-open="" disabled'), 'absence must not create a usable chat target');
const denied = context.projectCardHtml({...project, accessible: false, can_initialize: false, project_readable: false});
assert(denied.includes('principalAccessLock'));
assert(!context.projectCardHtml({...project, accessible: true, resident_id: 'resident_present'}).includes('principalAccessLock'));
console.log('PASS project card distinguishes missing binding from denied access');
