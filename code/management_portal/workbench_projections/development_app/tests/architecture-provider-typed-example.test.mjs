import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {createTypedAnswerTeachingExample} from '../src/architecture/provider-mechanisms.typed-example.js';
import {readNativeOperationsFixture} from './architecture-native-operations.fixture.mjs';

test('PVD-R01 teaching payloads agree with the frozen response validator, including ID and per-row errors', {skip: !process.env.NATIVE_OPERATIONS_FACTS}, () => {
  const fixture = readNativeOperationsFixture();
  const data = fixture.content('ps.mechanism.typed-answer', 'model-services.typed-decisions', false);
  const evidence = data.evidence.find(e => e.id === 'ps.evidence.jev-response');
  const source = JSON.parse(readFileSync(process.env.NATIVE_OPERATIONS_FACTS, 'utf8')).facts[evidence.frozen.address].text;
  const good = createTypedAnswerTeachingExample('valid');
  const mismatch = createTypedAnswerTeachingExample('question_mismatch');
  const invalid = createTypedAnswerTeachingExample('invalid_value');
  const samples = [good, mismatch, ...['q_route', 'q_quality', 'q_safe'].map(id => ({questions: good.questions, answers: {...good.answers, [id]: invalid.answers[id]}}))];
  const result = spawnSync('python3', ['-c', `import ast, json, math, sys
from typing import Any, Mapping
p=json.load(sys.stdin)
tree=ast.parse(p['source'])
prob=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_probability')
klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='JevProvider')
validator=next(n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name=='_validate_response')
validator.decorator_list=[]
class JevProviderError(Exception): pass
exec(compile(ast.Module(body=[prob,validator],type_ignores=[]),'frozen-validator','exec'))
results=[]
for sample in p['samples']:
 try:
  _validate_response({'model':'local-teaching','answers':sample['answers'],'usage':{}},sample['questions'])
  results.append('accepted')
 except JevProviderError as e: results.append(str(e))
print(json.dumps(results))`], {input: JSON.stringify({source, samples}), encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  const results = JSON.parse(result.stdout);
  assert.equal(results[0], 'accepted');
  assert.match(results[1], /requested questions/);
  assert.match(results[2], /type mismatch.*q_route/);
  assert.match(results[3], /out of range.*q_quality/);
  assert.match(results[4], /q_safe.noul/);
  assert.deepEqual(mismatch.rows.filter(r => r.problem).map(r => r.id), ['q_quality', 'q_unasked']);
  assert.deepEqual(invalid.rows.filter(r => r.problem).map(r => r.id), ['q_route', 'q_quality', 'q_safe']);
  assert.ok(Object.values(good.answers.q_route.probabilities).reduce((a, b) => a + b, 0) > 1);
});
