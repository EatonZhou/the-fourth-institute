const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
// The Menu's scripts in load order; each function comes from the script that declares it.
const menu = require('./resident_menu_scripts.cjs').residentMenuSource();
const hatg = fs.readFileSync(path.join(root, 'hatg_panel.js'), 'utf8');
function owner(source, name, indent) {
  const start = source.indexOf(`\n${' '.repeat(indent)}${name === 'removeResident' || name === 'open' || name === 'removeSelectedNode' ? 'async ' : ''}function ${name}(`);
  assert(start >= 0, name);
  const end = source.indexOf(`\n${' '.repeat(indent)}}\n`, start + 1);
  assert(end > start, name);
  return source.slice(start + 1, end + indent + 2);
}
const i18n = {t: x => x, text: (parts, ...values) => String.raw({raw: parts}, ...values)};
function menuHarness() {
  const released = [], closed = [];
  const ctx = vm.createContext({
    nodeMenuContext: null, options: {},
    disposeFamilySettings() {}, $id: () => ({replaceChildren() {}}),
    window: {PortalI18n: i18n},
  });
  ctx.dialog = {close: () => { closed.push(ctx.nodeMenuContext?.residentId); ctx.disposeNodeMenu(); }};
  // The parts read the Menu's shared state from menuState; the context stands for it.
  ctx.menuState = ctx;
  vm.runInContext(['disposeNodeMenu', 'retireResidents', 'removeResident'].map(n => owner(menu, n, 4)).join('\n'), ctx);
  const show = id => {
    ctx.nodeMenuContext = {residentId: id, unobserveSession: () => released.push(id)};
    return ctx.nodeMenuContext;
  };
  return {ctx, show, released, closed};
}

test('committed deletion releases Session before a pending owner refresh and cannot close a later menu', async () => {
  const h = menuHarness();
  const original = h.show('resident_deleted');
  let finish;
  const refresh = new Promise(resolve => { finish = resolve; });
  const graph = vm.createContext({
    window: {PortalResidentMenu: {retireResidents: ids => h.ctx.retireResidents(new Set(ids))}},
    panelHost: {hatgComponentOptions: {onResidentsDeleted: () => {
      assert.deepEqual(h.released, ['resident_deleted']);
      return refresh;
    }}},
  });
  vm.runInContext(owner(hatg, 'retireDeletedResidents', 2), graph);
  h.ctx.options.onDelete = async () => { await graph.retireDeletedResidents(['resident_deleted']); return true; };
  const deletion = h.ctx.removeResident({id: 'resident_deleted'});
  assert.equal(original.busy, true);
  assert.equal(h.ctx.nodeMenuContext, null);
  h.show('resident_other');
  finish();
  await deletion;
  assert.deepEqual(h.closed, ['resident_deleted']);
  assert.equal(h.ctx.nodeMenuContext.residentId, 'resident_other');
});

test('unrelated deletion leaves the current Session subscription active', () => {
  const h = menuHarness();
  const current = h.show('resident_kept');
  h.ctx.retireResidents(new Set(['resident_deleted']));
  assert.equal(h.ctx.nodeMenuContext, current);
  assert.deepEqual(h.released, []);
});

test('cancelled or rejected deletion keeps the menu usable', async () => {
  for (const reject of [false, true]) {
    const h = menuHarness();
    const current = h.show('resident_kept');
    h.ctx.options.onDelete = async () => { if (reject) throw new Error('write failed'); return false; };
    const result = h.ctx.removeResident({id: 'resident_kept'});
    if (reject) await assert.rejects(result, /write failed/);
    else await result;
    assert.equal(h.ctx.nodeMenuContext, current);
    assert.equal(current.busy, false);
    assert.deepEqual(h.released, []);
  }
});

test('a deleted Resident cannot finish opening from an older asynchronous read', async () => {
  let resolveResident;
  const opened = [];
  const ctx = vm.createContext({
    openSequence: 0, openingResidentId: '', instance: {retireResidents() {}, open: x => opened.push(x)},
    nodeResidentId: node => node?.id || '',
    window: {PortalInstructionClient: {execute: name => name === 'GetResident'
      ? new Promise(resolve => { resolveResident = resolve; })
      : Promise.resolve(name === 'GetLineage' ? {lineage: {lineage_id: 'lineage_one'}} : {states: []})}},
  });
  vm.runInContext(owner(menu, 'open', 2) + '\n' + owner(menu, 'retireResidents', 2), ctx);
  const opening = ctx.open({residentId: 'resident_deleted'});
  ctx.retireResidents(['resident_deleted']);
  resolveResident({resident: {id: 'resident_deleted', lineage_id: 'lineage_one'}});
  assert.equal(await opening, null);
  assert.deepEqual(opened, []);
});

test('a committed deletion followed by graph refresh failure reports the committed result', async () => {
  const messages = [];
  // The production Resident dialogs part, with only these dependencies provided.
  const window = {PortalI18n: i18n};
  vm.runInContext(readHatgModule('hatg_resident_dialogs.js'), vm.createContext({window}));
  const dialogs = createHatgModule(window.PortalHatgResidentDialogs, 'createResidentDialogs', {
    state: {lineage: {lineage_id: 'lineage_one', edges: []}},
    nodeById: () => ({id: 'resident_deleted', title: 'Deleted'}), currentNode: () => null,
    familyForFacade: () => null, nodeResidentId: node => node.id,
    toast: (...args) => messages.push(args),
    societyService: {deleteNode: async () => { throw Object.assign(new Error('refresh unavailable'), {
      committedMutation: {instruction: 'DeleteResident', result: {resident_id: 'resident_deleted'}},
    }); }},
  });
  assert.equal(await dialogs.removeSelectedNode({nodeId: 'resident_deleted', skipConfirm: true}), true);
  assert.equal(messages[0][1], 'warning');
  assert.match(messages[0][0], /Resident 已删除/);
});
