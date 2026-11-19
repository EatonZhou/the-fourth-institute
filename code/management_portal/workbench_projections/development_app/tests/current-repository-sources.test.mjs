import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { REPOSITORY, MAP_REF, EVOLUTION_REF } from '../src/architecture/model.js';
import { sourceForLesson } from '../src/architecture/instruction-workbench.model.js';
import { REFS } from '../src/evolution/data.js';
import { REFERENCES } from '../src/progress/data.js';
import { explorationSemanticObject } from '../src/architecture/exploration-focus.js';

// Confirmed by the Workbench application declaration and authenticated Repo
// resolution; historical evidence addresses are not rewritten by this contract.
const activeRepository = 'repo_2ef1aca76139';
const map = `${activeRepository}/file/code/architecture/modules.yaml`;
const evolution = `${activeRepository}/file/code/architecture/evolution.yaml`;

test('all current region maps use the registered application repository', () => {
  assert.equal(REPOSITORY, activeRepository);
  assert.equal(MAP_REF, map);
  assert.equal(REFERENCES.modules, map);
  assert.equal(REFS.modules, map);
  assert.equal(EVOLUTION_REF, evolution);
  assert.equal(REFS.evolution, evolution);
});

test('live lesson source navigation uses the same repository, without historical substitution', () => {
  assert.ok(sourceForLesson('ReadResource').startsWith(`${activeRepository}/file/code/`));
  assert.equal(sourceForLesson('ReadResource').includes('/commit/'), false);
  assert.equal(sourceForLesson('no-such-instruction'), '');
});

test('release dependency declaration identifies the same registered repository', () => {
  const dependencies = JSON.parse(readFileSync(new URL('../release-dependencies.json', import.meta.url)));
  assert.equal(dependencies.code_repository, activeRepository);
});

test('removed protocol evidence cannot become a current feedback target', () => {
  const target = { id: 'lineage-service', kind: 'protocol', identity: {
    resource_ref: map, focus_owner_id: 'lineage-service',
    focus_kind: 'protocol:lineage-service', local_only: false,
  } };
  const bundle = { mode: 'current', model: { objects: [target] }, sourceReview: { objects: {
    'lineage-service': { status: 'current', address: map, kind: 'protocol:lineage-service', owner: 'lineage-service' },
  } } };
  assert.equal(explorationSemanticObject(bundle, target, 'protocols:\n  - id: current-protocol\n', '2026-11-19T00:00:00Z'), null);
  assert.equal(explorationSemanticObject(bundle, target, 'protocols:\n  - id: lineage-service\n', '2026-11-19T00:00:00Z').ref, `${map}#L2`);
});
