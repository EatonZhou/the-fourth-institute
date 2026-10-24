'use strict';
const assert = require('node:assert/strict');
const {watchboardGraph, createScriptOutputCapture} = require('../../../management_portal/test_framework/tau_watchboard_evidence.js');
const residentId = 'resident_1234', prefix = `watchboard.${residentId}.`;
const unrelated = {id: 'other', client_key: 'watchboard.resident_12345.status', config: {resident_id: residentId}};
const current = {id: 'effect_one', client_key: prefix + 'status'};
const graph = {program: {components: [current, unrelated], wires: [
  {id: 'owned', source: 'event_one', target: current.id},
  {id: 'unrelated', source: 'elsewhere', target: 'other', config: {resident_id: residentId}},
]}};
const original = structuredClone(graph);
const installed = watchboardGraph(graph, {residentId});
assert.deepEqual(installed.components, [{id: current.id, client_key: current.client_key}]);
assert.deepEqual(installed.wires.map(w => w.id), ['owned']);
const removed = structuredClone(graph); removed.program.components = [unrelated];
assert.deepEqual(watchboardGraph(removed, {residentId, previousComponentIds: installed.tracked_component_ids}).wires.map(w => w.id),
  ['owned'], 'dangling wires still fail removal after their component disappears');
removed.program.wires.shift();
const cleared = watchboardGraph(removed, {residentId, previousComponentIds: installed.tracked_component_ids});
assert.deepEqual(cleared.components, []); assert.deepEqual(cleared.wires, []);
assert.equal(cleared.total_component_count, 1, 'unrelated components remain');
assert.deepEqual(graph, original, 'the reader cannot mutate original evidence');
for (const incomplete of [{}, {components: [], wires: []}, {program: {components: []}}, {program: {components: [], wires: null}}])
  assert.throws(() => watchboardGraph(incomplete, {residentId}), /required/, 'missing graph is not an empty installation');
assert.throws(() => watchboardGraph({program: {components: [], wires: [{id: 'x', source: {}, target: 'y'}]}}, {residentId}), /exact/);

const identity = {lineageId: 'lineage_fixture', scriptRunId: 'script_fixture', stream: 'stdout'};
const request = offset => ({lineage_id: identity.lineageId, script_run_id: identity.scriptRunId, stream: identity.stream, offset});
const data = Buffer.from('开始 ✓\n');
const chunk = (offset, end, size = data.length) => ({offset, next_offset: end, size,
  base64: data.subarray(offset, end).toString('base64'), eof: end === size, truncated: false});
const capture = createScriptOutputCapture(identity);
assert.equal(capture.append({request: request(0), result: chunk(0, 1, 1)}).fully_read, false,
  'running EOF does not close the decoder or prove terminal completion');
const partial = capture.append({request: request(1), result: chunk(1, 4)});
assert.equal(partial.text, '开', 'UTF-8 is decoded across byte boundaries, not per chunk.text');
const final = capture.append({request: request(4), result: chunk(4, data.length), terminal: true});
assert.equal(final.text, data.toString()); assert.equal(final.fully_read, true);
assert.throws(() => capture.append({}), /already/);
const clipped = createScriptOutputCapture(identity).append({request: request(0), result: {...chunk(0, data.length), truncated: true}, terminal: true});
assert.equal(clipped.fully_read, false, 'stored truncation cannot count as complete output');
for (const invalid of [
  {request: {...request(0), script_run_id: 'unrelated'}, result: chunk(0, 1)},
  {request: request(0), result: {status: 'succeeded', stdout: 'guessed'}},
  {request: request(1), result: chunk(1, 4)},
  {request: request(0), result: {...chunk(0, 1), base64: '!invalid!'}},
  {request: request(0), result: {...chunk(0, 1), next_offset: 5}},
]) assert.throws(() => createScriptOutputCapture(identity).append(invalid));
const empty = createScriptOutputCapture(identity).append({request: request(0), result: {
  offset: 0, next_offset: 0, size: 0, base64: '', eof: true, truncated: false}, terminal: true});
assert.equal(empty.text, ''); assert.equal(empty.fully_read, true, 'genuine empty output differs from a missing field');
console.log('Watchboard exact ownership, dangling edges and original output byte capture passed.');
