const assert = require('node:assert/strict');
const vm = require('node:vm');
const test = require('node:test');
const { readHatgModule, createHatgModule } = require('./hatg_modules.cjs');

function fixture() {
  const inner = { id: 'inner-block', elements: [],
    family: { facade: 'inner', members: ['member'] } };
  const outer = { id: 'outer-block', elements: [],
    family: { facade: 'outer', members: ['inner'] } };
  const state = { lineage: { function_blocks: [inner] }, graphViewMode: 'context',
    graphFocusMode: false, selectedNodeId: 'inner', expandedFamilyFacadeIds: new Set(['inner']) };
  const context = vm.createContext({ Set, Map });
  context.window = context;
  vm.runInContext(readHatgModule('hatg_family_blocks.js'), context);
  const blocks = createHatgModule(context.PortalHatgFamilyBlocks, 'createFamilyBlocks', {
    state, graphSelectedId: () => state.selectedNodeId,
  });
  return { state, blocks, inner, outer };
}

test('wrapping the selected expanded Family in a folded parent leaves the visible parent readable', () => {
  const { state, blocks, inner, outer } = fixture();
  assert.equal(blocks.familySpotlight().instance, inner);
  state.lineage.function_blocks.push(outer);
  assert.ok(blocks.foldedFamilyObjects().residents.has('inner'));
  assert.equal(blocks.familySpotlight(), null);
  // A refreshed projection with the same saved selection must stay readable.
  state.lineage = JSON.parse(JSON.stringify(state.lineage));
  assert.equal(blocks.familySpotlight(), null);
  assert.equal(state.selectedNodeId, 'inner');
});

test('opening the parent restores the selected visible inner Family spotlight', () => {
  const { state, blocks, inner, outer } = fixture();
  state.lineage.function_blocks.push(outer);
  state.expandedFamilyFacadeIds.add('outer');
  assert.equal(blocks.familySpotlight().instance, inner);
  state.selectedNodeId = 'outer';
  assert.equal(blocks.familySpotlight().instance, outer);
  state.graphFocusMode = true;
  assert.equal(blocks.familySpotlight(), null);
});

test('folding any ancestor suppresses a nested spotlight without changing expansion preferences', () => {
  const { state, blocks, outer } = fixture();
  state.lineage.function_blocks.push(outer, { id: 'top-block', elements: [],
    family: { facade: 'top', members: ['outer'] } });
  state.expandedFamilyFacadeIds.add('outer');
  assert.equal(blocks.familySpotlight(), null);
  assert.deepEqual([...state.expandedFamilyFacadeIds], ['inner', 'outer']);
  state.expandedFamilyFacadeIds.add('top');
  assert.equal(blocks.familySpotlight().roles.facade, 'inner');
});
