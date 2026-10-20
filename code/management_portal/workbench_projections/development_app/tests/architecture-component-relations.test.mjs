import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import { createEvidenceLoader, sha256 } from '../src/architecture/data.js';
import { projectComponentRelations } from '../src/architecture/component-relations.js';
import { REPOSITORY, MAP_REF, EVOLUTION_REF } from '../src/architecture/model.js';
import { validateScenario, timelineFor, MAIN_BRANCH } from '../src/architecture/playback.js';

globalThis.crypto ??= webcrypto;
const COMPONENT_RELATIONS_ENTRY = {
 address:'workspace_13209cb6666b/file/architecture-evidence/component-relations/manifest-v2.json',
 sha256:'62d43a98775869588e269ba7f285981c9b7fac2ef776949f137527a302e97d22', size:2824,
};
const entryText = await readFile(new URL('./fixtures/architecture-component-relations-v2-manifest.json', import.meta.url), 'utf8');
const bodyText = await readFile(new URL('./fixtures/architecture-component-relations-v2-body.json', import.meta.url), 'utf8');
const entry = JSON.parse(entryText);
const body = JSON.parse(bodyText);
const sceneTexts = await Promise.all(['resident', 'native'].map(name => readFile(new URL(`./fixtures/architecture-component-relations-v2-${name}-scene.json`, import.meta.url), 'utf8')));
const scenes = sceneTexts.map(value => JSON.parse(value));
const codeRef = id => {
  const code = body.code_evidence.find(item => item.id === id);
  return `${REPOSITORY}/commit/${code.commit}/file/${code.path}`;
};

function setup() {
  const data = { mode: 'current', modelVersion: 2, mapRef: MAP_REF, evolutionRef: EVOLUTION_REF,
    mapHash: entry.binding.map_sha256, evolutionHash: entry.binding.evolution_sha256,
    manifest: { discoveryPin: {component_relations:{status:'available',manifest:COMPONENT_RELATIONS_ENTRY}}, pinned: structuredClone(entry.base_manifest), consumer_contract: structuredClone(entry.consumer_contract), current_commit: entry.binding.commit, scenario_catalog: [] },
    modules: ['execution', 'worker', 'resources', 'lineage', 'providers'].map(id => ({ id, parent: null })).concat(
      ...body.components.map(item => ({ id: item.module_id, parent: item.parent, title: item.title, deployed_in: item.deployed_in }))),
    protocols: [...new Set(body.relations.flatMap(item => [item.protocol_id, ...item.related_protocol_ids]).filter(Boolean))].map(id => ({ id })),
    anchors: { modules: { ...Object.fromEntries(body.components.map(item => [item.module_id, item.source.line])), lineage: 300, resources: 200, providers: 100 },
      protocols: { 'worker-lease': 900, 'provider-capabilities': 901, 'database-schema': 902 } },
    scenarios: [],
  };
  const denied = new Set(); const failures = new Map(); const resolves = []; const reads = [];
  const text = new Map([[COMPONENT_RELATIONS_ENTRY.address, entryText], [entry.artifact.address, bodyText]]);
  scenes.forEach((scene, index) => text.set(body.scenario_catalog.find(item => item.scenario_id === scene.scenario_id).artifact.address, sceneTexts[index]));
  const hooks = new Map(); const controller = new AbortController();
  const resources = {
    resolve: async ref => {
      resolves.push(ref);
      if (failures.has(ref)) throw failures.get(ref);
      if (hooks.has(ref)) await hooks.get(ref)();
      return { readable: !denied.has(ref) };
    },
    read: async (ref, format) => {
      reads.push({ ref, format });
      if (failures.has(ref)) throw failures.get(ref);
      return { readable: text.has(ref) && !denied.has(ref), data: text.get(ref) ?? null };
    },
  };
  const loader = createEvidenceLoader({ resources, sdk: {}, signal: controller.signal });
  return { data, denied, failures, resolves, reads, text, hooks, controller, loader };
}

test('sanitized fixtures preserve exact descriptor bindings, including final LF', async () => {
  for (const [artifact, text] of [[COMPONENT_RELATIONS_ENTRY, entryText], [entry.artifact, bodyText]]) {
    assert.equal(Buffer.byteLength(text), artifact.size);
    assert.equal(await sha256(text), artifact.sha256);
  }
  assert.equal(body.relations.length, 13);
  for (const [index, scene] of scenes.entries()) {
    const artifact = body.scenario_catalog.find(item => item.scenario_id === scene.scenario_id).artifact;
    assert.equal(Buffer.byteLength(sceneTexts[index]), artifact.size); assert.equal(await sha256(sceneTexts[index]), artifact.sha256);
  }
});

test('thirteen real relations load without mutating map modules, protocols or base scenes', async () => {
  const f = setup(); const before = structuredClone(f.data);
  const bundle = await f.loader.componentRelations(f.data);
  assert.equal(bundle.status, 'available');
  assert.deepEqual(bundle.relations.map(item => item.id), body.relations.map(item => item.id));
  assert.deepEqual(f.data, before);
  assert.equal(bundle.scenario_reuse, undefined);
  assert.equal(bundle.missing, undefined); // no hidden subjects/counts copied from the raw package
  assert.equal(bundle.components, undefined);
  assert.ok(bundle.relations.every(item => item.codeEvidence.length && item.source_basis.length));
});

test('Worker uses direct children, null-ID boundary roles and shared AgentData without reparenting', async () => {
  const f = setup(); const bundle = await f.loader.componentRelations(f.data);
  const result = projectComponentRelations(f.data, bundle, 'worker');
  assert.equal(result.status, 'available');
  assert.deepEqual(result.componentRelations.map(item => item.id), [
    'worker-lease-control', 'runtime-session-adapter-call', 'session-harness-governance', 'runtime-agentdata-content', 'codex-native-session',
  ]);
  const endpoints = result.componentRelations.flatMap(item => item.endpoints);
  for (const endpoint of endpoints.filter(item => item.moduleId === 'worker')) {
    assert.equal(endpoint.componentId, null); assert.equal(endpoint.display, 'root_boundary_role');
  }
  const scheduler = endpoints.find(item => item.moduleId === 'execution.scheduling');
  assert.equal(scheduler.display, 'external_root_boundary'); assert.equal(scheduler.displayModuleId, 'execution');
  assert.equal(scheduler.componentId, 'execution.scheduling');
  const agentData = endpoints.find(item => item.moduleId === 'execution.agent-data');
  assert.equal(agentData.display, 'shared_component_boundary'); assert.equal(agentData.displayModuleId, 'execution.agent-data');
  assert.equal(f.data.modules.find(item => item.id === agentData.moduleId).parent, 'execution');
  assert.equal(endpoints.find(item => item.moduleId === 'execution.providers').display, 'direct_component');
  assert.equal(endpoints.find(item => item.moduleId === 'worker.harness').display, 'direct_component');
  assert.deepEqual(result.contextModuleIds, ['execution', 'execution.agent-data', 'providers']);
  const provider = endpoints.find(item => item.moduleId === 'providers');
  assert.equal(provider.componentId, null); assert.equal(provider.display, 'external_root_boundary');
});

test('Execution transaction is an undirected consistency relation with no invented protocol', async () => {
  const f = setup(); const bundle = await f.loader.componentRelations(f.data);
  const result = projectComponentRelations(f.data, bundle, 'execution');
  assert.deepEqual(result.componentRelations.map(item => item.id), ['worker-lease-control', 'scheduler-agentdata-lifecycle-transaction', 'resident-execution-submit']);
  const transaction = result.componentRelations[1];
  assert.equal(transaction.protocolId, null);
  assert.deepEqual(transaction.relatedProtocolIds, ['database-schema']);
  assert.equal(transaction.interaction.direction, 'undirected_consistency');
  assert.equal(transaction.interaction.ordered, false);
  assert.equal(transaction.interaction.initiator_module_id, undefined);
  assert.equal(transaction.interaction.responder_module_id, undefined);
  assert.ok(transaction.endpoints.every(endpoint => endpoint.display === 'direct_component'));
  const leaf = projectComponentRelations(f.data, bundle, 'execution.providers');
  assert.equal(leaf.reason, 'no_readable_relations'); assert.deepEqual(leaf.componentRelations, []);
});

test('each joint binding is mandatory and cannot silently reuse a different batch', async t => {
  const cases = [
    ['manifest address', data => { data.manifest.pinned.address += '-other'; }],
    ['manifest digest', data => { data.manifest.pinned.sha256 = '0'.repeat(64); }],
    ['manifest size', data => { data.manifest.pinned.size += 1; }],
    ['consumer address', data => { data.manifest.consumer_contract.address += '-other'; }],
    ['consumer digest', data => { data.manifest.consumer_contract.sha256 = '0'.repeat(64); }],
    ['consumer size', data => { data.manifest.consumer_contract.size += 1; }],
    ['commit', data => { data.manifest.current_commit = '0'.repeat(40); }],
    ['map', data => { data.mapHash = '0'.repeat(64); }],
    ['evolution', data => { data.evolutionHash = '0'.repeat(64); }],
  ];
  for (const [name, mutate] of cases) await t.test(name, async () => {
    const f = setup(); const bundle = await f.loader.componentRelations(f.data); mutate(f.data);
    assert.equal(projectComponentRelations(f.data, bundle, 'worker').reason, 'binding_mismatch');
    const result = await f.loader.componentRelations(f.data);
    assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'binding_mismatch'); assert.deepEqual(result.relations, []);
  });
});

test('history does not reuse current-observed Design evidence or read another batch', async () => {
  const f = setup(); f.data.mode = 'history'; f.data.commit = entry.binding.commit;
  const result = await f.loader.componentRelations(f.data);
  assert.equal(result.reason, 'historical_evidence_missing'); assert.deepEqual(f.reads, []); assert.deepEqual(f.resolves, []);
});

test('digest and byte size corruption are unavailable, not empty successful graphs', async t => {
  for (const artifact of [COMPONENT_RELATIONS_ENTRY, entry.artifact]) {
    await t.test(`${artifact.address}: bytes`, async () => {
      const f = setup(); f.text.set(artifact.address, f.text.get(artifact.address) + '\n');
      const result = await f.loader.componentRelations(f.data);
      assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'size_mismatch');
    });
    await t.test(`${artifact.address}: hash`, async () => {
      const f = setup(); f.text.set(artifact.address, f.text.get(artifact.address).replace('2026-10-15', '2026-10-16'));
      const result = await f.loader.componentRelations(f.data);
      assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'hash_mismatch');
    });
  }
});

test('byte cache does not cache base, original Design, frozen body or code permissions', async t => {
  for (const ref of [REPOSITORY, MAP_REF, EVOLUTION_REF, entry.base_manifest.address, entry.consumer_contract.address,
    COMPONENT_RELATIONS_ENTRY.address, entry.artifact.address, entry.map.address,
    'decision_25eb93d803a4', body.design_versions[1].body.address, codeRef('runtime-call')]) {
    await t.test(ref, async () => {
      const f = setup(); assert.equal((await f.loader.componentRelations(f.data)).relations.length, 13);
      const reads = f.reads.length; f.denied.add(ref);
      const result = await f.loader.componentRelations(f.data);
      assert.ok(f.resolves.filter(item => item === ref).length >= 2);
      assert.ok(result.relations.length < 13); assert.equal(f.reads.length, reads);
      if (result.status === 'available') assert.ok(result.relations.every(item => !item.codeEvidence.some(e => `${REPOSITORY}/commit/${e.commit}/file/${e.path}` === ref)));
    });
  }
});

test('one unreadable endpoint removes its whole relation including roles, titles and evidence', async () => {
  const f = setup(); f.denied.add(MAP_REF + '#L833');
  const result = await f.loader.componentRelations(f.data);
  assert.equal(result.relations.length, 12);
  assert.equal(JSON.stringify(result).includes('session-harness-governance'), false);
  assert.equal(JSON.stringify(result).includes('Agent Harness'), false);
  assert.equal(JSON.stringify(result).includes('should_run'), false);
  f.data.modules = f.data.modules.filter(item => item.id !== 'execution');
  const noAncestor = await f.loader.componentRelations(f.data);
  assert.ok(noAncestor.relations.every(item => item.endpoints.every(endpoint => !endpoint.module_id.startsWith('execution.') || endpoint.module_id === 'execution.providers')));
});

test('source and endpoint authorization finishes before any relations are returned', async () => {
  const f = setup(); let release; let entered;
  const started = new Promise(resolve => { entered = resolve; });
  f.hooks.set(codeRef('worker-finish'), () => { entered(); return new Promise(resolve => { release = resolve; }); });
  let returned = false; const pending = f.loader.componentRelations(f.data).then(value => { returned = true; return value; });
  await started; assert.equal(returned, false);
  f.denied.add(codeRef('worker-finish')); release();
  const result = await pending;
  assert.ok(result.relations.every(item => item.id !== 'worker-lease-control'));
});

test('unreadable related protocol hides relation; source service errors remain actionable errors', async () => {
  const f = setup(); f.denied.add(MAP_REF + '#L902');
  const result = await f.loader.componentRelations(f.data);
  assert.ok(result.relations.every(item => item.id !== 'scheduler-agentdata-lifecycle-transaction'));
  const error = new Error('transport unavailable'); f.failures.set(body.design_versions[1].body.address, error);
  const failed = await f.loader.componentRelations(f.data);
  assert.equal(failed.status, 'error'); assert.equal(failed.reason, 'read_failed'); assert.equal(failed.error, error);
  assert.deepEqual(failed.relations, []);
});

test('component parent/deployment mismatch is unavailable and abort remains cancellation', async () => {
  const f = setup(); f.data.modules.find(item => item.id === 'execution.agent-data').parent = 'worker';
  const result = await f.loader.componentRelations(f.data);
  assert.equal(result.reason, 'component_binding_mismatch'); assert.deepEqual(result.relations, []);
  f.controller.abort();
  await assert.rejects(f.loader.componentRelations(f.data), error => error.name === 'AbortError');
});

test('v1 original artifacts remain archived unchanged, without a production v1 fallback', async () => {
  const oldEntry = await readFile(new URL('./fixtures/architecture-component-relations-manifest.json', import.meta.url), 'utf8');
  const oldBody = await readFile(new URL('./fixtures/architecture-component-relations-body.json', import.meta.url), 'utf8');
  assert.equal(await sha256(oldEntry), entry.previous_manifest.sha256);
  assert.equal(await sha256(oldBody), JSON.parse(oldEntry).artifact.sha256);
  const f = setup(); f.text.set(COMPONENT_RELATIONS_ENTRY.address, oldEntry);
  assert.equal((await f.loader.componentRelations(f.data)).reason, 'size_mismatch');
});

test('Resource and Lineage use canonical scope relation IDs, preserve via and expose no parent protocol inheritance', async () => {
  const f = setup(); const bundle = await f.loader.componentRelations(f.data);
  for (const root of ['resources', 'lineage', 'execution', 'worker']) {
    const projected = projectComponentRelations(f.data, bundle, root);
    assert.deepEqual(projected.componentRelations.map(item => item.id).sort(), body.scopes.find(scope => scope.root_module_id === root).relation_ids.toSorted());
    assert.ok(projected.componentRelations.every(item => body.relations.find(relation => relation.id === item.id).scope_roots.includes(root)));
  }
  const automation = projectComponentRelations(f.data, bundle, 'lineage').componentRelations.find(item => item.id === 'automation-resident-delivery');
  assert.deepEqual(automation.interaction.via, body.relations.find(item => item.id === automation.id).interaction.via);
  assert.equal(automation.interaction.ordered, false);
  const family = projectComponentRelations(f.data, bundle, 'lineage.family');
  assert.deepEqual(family.componentRelations, []); assert.equal(family.reason, 'no_readable_relations');
  const clipped = structuredClone(bundle); clipped.scopes.find(scope => scope.root_module_id === 'resources').relation_ids = [];
  assert.deepEqual(projectComponentRelations(f.data, clipped, 'resources').componentRelations, []);
  f.denied.add(MAP_REF + '#L300');
  assert.ok((await f.loader.componentRelations(f.data)).relations.every(item => item.id !== 'automation-resident-delivery'));
});

test('two supplemental simulated pairs load under the exact pin and work with the existing playback model', async () => {
  const f = setup(); const before = structuredClone(f.data.manifest);
  for (const sourceScene of scenes) {
    const loaded = await f.loader.scenarios(f.data, sourceScene.protocol_id);
    assert.equal(loaded.length, 1); assert.deepEqual(loaded[0], sourceScene);
    validateScenario(loaded[0]);
    for (const branch of [MAIN_BRANCH, ...sourceScene.branches.map(item => item.id)]) {
      assert.ok(timelineFor(loaded[0], branch).length);
    }
    assert.ok(f.resolves.includes(sourceScene.source_versions[0].body.address));
    assert.ok(f.resolves.includes(sourceScene.source_versions[0].ref));
  }
  assert.deepEqual(f.data.manifest, before);
  f.data.manifest.pinned.sha256 = '0'.repeat(64);
  await assert.rejects(f.loader.scenarios(f.data, 'resident-execution'), {reason:'binding_mismatch'});
});

test('new scenes recheck participant, Design and frozen-source grants and never leak a denied title', async t => {
  const scene = scenes.find(item => item.protocol_id === 'resident-execution');
  const refs = [scene.source_versions[0].ref, scene.source_versions[0].body.address,
    MAP_REF + '#L' + body.components.find(item => item.module_id === 'lineage.conversation').source.line];
  for (const ref of refs) await t.test(ref, async () => {
    const f = setup(); assert.equal((await f.loader.scenarios(f.data, scene.protocol_id)).length, 1);
    f.denied.add(ref); assert.deepEqual(await f.loader.scenarios(f.data, scene.protocol_id), []);
  });
  const f = setup(); f.data.modules = f.data.modules.filter(item => item.id !== 'lineage.conversation');
  assert.deepEqual(await f.loader.scenarios(f.data, scene.protocol_id), []);
});

test('supplement scene read failures are actionable; same-byte cache does not bypass artifact revocation', async () => {
  const f = setup(); const scene = scenes[0]; const artifact = body.scenario_catalog.find(item => item.scenario_id === scene.scenario_id).artifact;
  assert.equal((await f.loader.scenarios(f.data, scene.protocol_id)).length, 1);
  f.denied.add(artifact.address);
  await assert.rejects(f.loader.scenarios(f.data, scene.protocol_id), error => error.code === 'architecture.component_relations_unavailable' && error.reason === 'scenario_unreadable');
  f.denied.clear(); f.text.set(artifact.address, sceneTexts[0] + '\n');
  await assert.rejects(f.loader.scenarios(f.data, scene.protocol_id), /摘要不匹配|大小不匹配/);
  const failure = new Error('scene service failed'); f.failures.set(entry.artifact.address, failure);
  await assert.rejects(f.loader.scenarios(f.data, scene.protocol_id), error => error === failure);
});
