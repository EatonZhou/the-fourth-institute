'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');
const {readFamilyFixtureScope: read} = require('../../../management_portal/test_framework/tau_family_fixture_scope.js');

(async () => {
  // Python supplies graphs from the native, in-memory Family ownership owner.
  // No API, browser, database, or real creation/ApplyTemplate is used.
  const {before, collected, adopted, expected} = JSON.parse(fs.readFileSync(0, 'utf8'));
  const objects = [...before.nodes, ...before.program.components];
  const byId = id => objects.find(object => object.id === id);
  const selected = new Set([expected.memberId, ...expected.effectIds, expected.eventId]);
  let application = null;
  const context = vm.createContext({window: {}});
  vm.runInContext(readHatgModule('hatg_canvas_selection.js'), context);
  const selection = createHatgModule(context.window.PortalHatgCanvasSelection, 'createCanvasSelection', {
    state: {selectedCanvasObjectIds: selected},
    functionBlockInstanceById: () => null,
    programComponentById: id => before.program.components.find(component => component.id === id),
    displayProgramWireById: () => null,
    isProgramComponent: object => ['effect', 'event_source'].includes(object.kind),
    isFunctionBlockInstance: () => false,
    nodeById: id => before.nodes.find(node => node.id === id),
    currentTemplateController: () => ({applyFamily: target => {application = structuredClone(target);}}),
  });
  await selection.openFamilize();
  assert.deepEqual(application, {kind: 'selection', resident_ids: [expected.memberId], element_ids: [expected.memberId]});
  assert.equal(selected.size, 4, 'the canvas can retain all selections while Familize takes only Residents');
  const external = read({lineage: collected}, expected);
  assert.equal(external.scope_ready, false);
  assert.equal(external.objects.every(row => row.candidates.length === 1 && row.owners.length === 0), true);
  const good = read({lineage: adopted}, expected);
  assert.equal(good.scope_ready, true, JSON.stringify(good));
  const unchanged = JSON.stringify(adopted);
  const rejected = (change, problem) => {
    const graph = structuredClone(adopted); change(graph);
    const evidence = read({lineage: graph}, expected);
    assert.equal(evidence.scope_ready, false);
    assert.ok(evidence.problems.some(item => item.includes(problem)), JSON.stringify(evidence));
    return evidence;
  };
  rejected(g => {g.lineage_id = 'lineage_other';}, 'lineage_id');
  rejected(g => {g.function_blocks.push(structuredClone(g.function_blocks[0]));}, 'family.candidates');
  rejected(g => {g.function_blocks[0].family.facade = 'resident_other';}, 'family.facade');
  rejected(g => {delete g.function_blocks[0].family.members;}, 'family.members');
  rejected(g => {delete g.function_blocks[0].elements;}, 'ownership.complete');
  rejected(g => {delete g.program.components;}, 'program.components');
  rejected(g => {g.program.components.push(structuredClone(g.program.components[0]));}, '.candidates');
  rejected(g => {g.program.components.find(c => c.id === expected.eventId).config.event_type = 'timer.tick';}, '.event_type');
  rejected(g => {delete g.program.components.find(c => c.id === expected.eventId).config.event_type;}, '.event_type');
  rejected(g => {g.function_blocks.push({id: 'fbi_other', elements: [structuredClone(g.function_blocks[0].elements.find(e => e.id === expected.eventId))]});}, '.ownership');
  rejected(g => {g.function_blocks[0].elements.find(e => e.id === expected.effectIds[0]).key = 'router';}, '.ownership');
  assert.equal(read({lineage: adopted}, {...expected, effectIds: [expected.effectIds[0], expected.effectIds[0]]}).scope_ready, false);
  assert.equal(read({}, expected).scope_ready, false);
  assert.equal(JSON.stringify(adopted), unchanged, 'reader is passive');
  adopted.program.components[0].title = 'later change';
  assert.notEqual(good.objects[0].candidates[0].title, 'later change', 'saved evidence cannot be backfilled by mutation');
  console.log(JSON.stringify({native_selection: application, disconnected: external, adopted: good, passed: true}));
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
