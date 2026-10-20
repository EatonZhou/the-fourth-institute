import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {build} from 'esbuild';
import {createPlayback, movePlayback, selectPlaybackBranch, timelineFor, sceneFor} from '../src/architecture/playback.js';

// Ignore presentation CSS in Node; exercise the exact production projection functions.
const bundle = await build({entryPoints:[new URL('../src/architecture/protocol-workbench.js', import.meta.url).pathname],
  bundle:true, write:false, format:'esm', loader:{'.css':'empty'}});
const {supportsProtocolWorkbench, protocolWorkbenchView} = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const facts = JSON.parse(readFileSync(new URL('../../../../tests/cases/fixtures/development_projection/product-architecture.json', import.meta.url))).facts;
const load = hash => {
  const entry = Object.entries(facts).find(([key]) => key.endsWith(`protocol-scenario-${hash}.json`));
  assert.ok(entry, 'frozen scene must exist');
  assert.equal(createHash('sha256').update(entry[1].text).digest('hex'), hash);
  return JSON.parse(entry[1].text);
};
const resource = load('8400ca13bfb1faccef2990f66e0e04bc8f99818b44c12e930265a09be3699d12');
const focus = load('670d1462921e73fa8b131bfcc6f7947fa2860ff02c646444ee1fc90d8de1eefb');
const at = (scenario, branch, step) => movePlayback(scenario, createPlayback(scenario, branch), timelineFor(scenario, branch).findIndex(item => item.id === step));

test('only the two exact simulated protocol/scenario pairs have a dedicated workbench', () => {
  assert.equal(supportsProtocolWorkbench(resource), true);
  assert.equal(supportsProtocolWorkbench(focus), true);
  assert.equal(supportsProtocolWorkbench({...focus, protocol_id:'resource-access'}), false);
  assert.equal(supportsProtocolWorkbench({...focus, status:'current'}), false);
  assert.equal(supportsProtocolWorkbench({...focus, scenario_id:'feedback-confirmation-budget'}), false);
  assert.equal(supportsProtocolWorkbench(null), false);
});

test('normal resource success appears only after receipt and reverses to waiting', () => {
  const final = at(resource, '$main', 'return');
  const view = protocolWorkbenchView(resource, final);
  assert.equal(view.reader, 'returned'); assert.equal(view.receipt, 'complete'); assert.equal(view.outcome, 'complete');
  const before = protocolWorkbenchView(resource, movePlayback(resource, final, -1));
  assert.equal(before.reader, 'returned'); assert.equal(before.response, false); assert.equal(before.receipt, 'waiting');
});

test('permission refusal stops at the gate; reader and successful receipt never occur', () => {
  for (const [index] of timelineFor(resource, 'forbidden').entries()) {
    const view = protocolWorkbenchView(resource, movePlayback(resource, createPlayback(resource, 'forbidden'), index));
    assert.equal(view.reader, 'not_started'); assert.notEqual(view.receipt, 'complete');
    if (view.step.id === 'resolve') { assert.equal(view.gate, 'denied'); assert.equal(view.response, false); }
    if (view.step.id === 'deny') { assert.equal(view.response, true); assert.equal(view.outcome, 'denied'); }
  }
});

test('partial receipt cannot temporarily show success at the branch pivot', () => {
  const pivot = at(resource, 'partial_body', 'return');
  const view = protocolWorkbenchView(resource, pivot);
  assert.equal(view.reader, 'returned'); assert.equal(view.response, true);
  assert.equal(view.receipt, 'incomplete'); assert.equal(view.outcome, 'pending');
  assert.match(view.step.result, /异常/);
  assert.equal(protocolWorkbenchView(resource, movePlayback(resource, pivot, 1)).outcome, 'incomplete');
  assert.equal(protocolWorkbenchView(resource, movePlayback(resource, pivot, -1)).receipt, 'waiting');
});

test('normal A then B clears old A detail immediately when B is selected', () => {
  assert.equal(protocolWorkbenchView(focus, at(focus, 'normal-arrival', 'apply-a')).displayed, 'A');
  for (const step of ['select-b', 'start-read-b']) {
    const view = protocolWorkbenchView(focus, at(focus, 'normal-arrival', step));
    assert.equal(view.selected, 'B'); assert.equal(view.displayed, null);
    assert.equal(view.packets.find(item => item.letter === 'A').current, false);
  }
  assert.equal(protocolWorkbenchView(focus, at(focus, 'normal-arrival', 'apply-b')).displayed, 'B');
});

test('late-A branch displays both in-flight reads, accepts B, and discards A; rewind restores A in flight', () => {
  const view = protocolWorkbenchView(focus, at(focus, 'a-arrives-after-b', 'start-read-b'));
  assert.equal(view.selected, 'B'); assert.equal(view.displayed, null);
  assert.deepEqual(view.packets.map(item => [item.letter,item.status]), [['A','pending'],['B','pending']]);
  const final = at(focus, 'a-arrives-after-b', 'discard-late-a');
  const end = protocolWorkbenchView(focus, final);
  assert.equal(end.displayed, 'B'); assert.equal(end.packets[0].status, 'discarded');
  const back = protocolWorkbenchView(focus, movePlayback(focus, final, -1));
  assert.equal(back.displayed, 'B'); assert.equal(back.packets[0].status, 'pending');
});

test('branch reset uses playback identity and retains exact frozen scene provenance', () => {
  const state = selectPlaybackBranch(focus, 'a-arrives-after-b');
  assert.equal(state.index, 0); assert.equal(protocolWorkbenchView(focus, state).displayed, null);
  const scene = sceneFor(focus, state);
  assert.equal(scene.status, 'simulated'); assert.equal(scene.scenario_id, focus.scenario_id);
  assert.deepEqual(scene.source_versions, focus.source_versions);
  assert.ok(scene.source_refs.every(source => /\/objects\/.+#L\d+(?:-L\d+)?$/.test(source.frozen_ref)));
});
