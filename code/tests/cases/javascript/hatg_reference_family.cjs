const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
const parent = {lineage_id: 'project', revision: 2, nodes: [
  {id: 'leader'},
  {id: 'facade', hierarchy_reference: true, accessible: true, home_lineage_id: 'child'},
  {id: 'ordinary', hierarchy_reference: true, accessible: true, home_lineage_id: 'other'},
  {id: 'denied', hierarchy_reference: true, accessible: false, home_lineage_id: 'secret'},
], function_blocks: []};

function fixture() {
  const calls = [];
  const context = {structuredClone}; context.window = context;
  for (const file of ['portal_society_service.js', 'hatg_lineage_state.js', 'hatg_resident_runtime.js']) {
    vm.runInNewContext(readHatgModule(file), context);
  }
  let role = 'facade', fail = false, home = 'child';
  const client = context.PortalSocietyService.createClient({execute: async (name, args) => {
    calls.push({name, args});
    if (name === 'GetLineage') return {lineage: structuredClone(parent)};
    assert.equal(name, 'GetResident');
    assert.notEqual(args.resident_id, 'denied');
    if (fail) throw Error('unavailable');
    return {resident: {resident_id: args.resident_id, lineage_id: args.resident_id === 'facade' ? home : 'other',
      family: args.resident_id === 'facade' ? {role, family_id: 'family'} : null}};
  }});
  const state = {lineage: parent};
  const lineage = createHatgModule(context.PortalHatgLineageState, 'createLineageState', {clone: structuredClone, state});
  return {client, calls, lineage, context, state, role: value => {role = value;},
    fail: () => {fail = true;}, move: () => {home = 'moved';}};
}

test('initial project read resolves facade identity once without importing child blocks', async () => {
  const h = fixture();
  const first = h.client.loadLineage('project');
  assert.equal(first, h.client.loadLineage('project'));
  const graph = await first;
  assert.equal(graph.nodes[1]._family_reference.family_id, 'family');
  assert.equal(graph.nodes[1]._family_reference.facade, 'facade');
  assert.equal(graph.nodes[2]._family_reference, null);
  assert.equal(graph.nodes[3]._family_reference, undefined);
  assert.equal(graph.function_blocks.length, 0);
  assert.equal(parent.nodes[1]._family_reference, undefined);
  assert.equal(h.calls.filter(call => call.name === 'GetResident').length, 2);
});

test('Family removal at unchanged parent revision invalidates its presentation', async () => {
  const h = fixture();
  const previous = await h.client.loadLineage('project');
  h.role('member');
  const incoming = await h.client.loadLineage('project');
  const merged = h.lineage.mergeLineageStateDomains(previous, incoming);
  assert.equal(previous.revision, incoming.revision);
  assert.equal(merged.nodes[1]._family_reference, null);
  h.role('facade');
  const restored = h.lineage.mergeLineageStateDomains(merged, await h.client.loadLineage('project'));
  assert.equal(restored.nodes[1]._family_reference.family_id, 'family');
});

test('failed or moved identity cannot fabricate a Family or prevent parent access', async () => {
  const failed = fixture(); failed.fail();
  const graph = await failed.client.loadLineage('project');
  assert.equal(graph.nodes.length, 4);
  assert.equal(graph.nodes[1]._family_reference, null);
  const moved = fixture(); moved.move();
  assert.equal((await moved.client.loadLineage('project')).nodes[1]._family_reference, null);
});

test('child definition signals refresh the parent, denied and unrelated children do not', () => {
  const h = fixture();
  let matches;
  h.context.PortalRefreshCoordinator = {
    portalSubscription: options => {matches ||= options.matches; return () => {};},
    create: () => ({start() {}, stop() {}, destroy() {}, invalidate() {}}),
  };
  const runtime = createHatgModule(h.context.PortalHatgResidentRuntime, 'createResidentRuntime', {
    state: h.state, currentPanelActive: () => true, nodeResidentId: node => node.id,
  });
  runtime.bindLineageProjectionSubscription();
  assert.equal(matches({changed: {lineage: ['child']}}), true);
  assert.equal(matches({changed: {lineage: ['secret']}}), false);
  assert.equal(matches({changed: {lineage: ['elsewhere']}}), false);
});
