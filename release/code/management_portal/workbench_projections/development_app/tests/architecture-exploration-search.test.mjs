import test from 'node:test';
import assert from 'node:assert/strict';
import { explorationSearchIndex, searchExploration } from '../src/architecture/exploration-search.js';

const module = { id: 'test.engine', title: '演示引擎', kind: 'module', responsibility: '执行输入', search_aliases: ['Engine'] };
const protocol = { id: 'test.messages', title: '消息协议', kind: 'protocol', responsibility: '关联请求与回执' };
const evidence = (id, claim) => ({ id, kind: 'design', frozen: { address: 'frozen:one' }, address: 'design:one', symbol: '## 回执', claim });
test('search preserves exact object routes and supports title, identity and explicit aliases', () => {
  const index = explorationSearchIndex({ objects: [module, protocol], evidence: [] });
  assert.equal(searchExploration(index, 'Ｅｎｇｉｎｅ 执行')[0].id, module.id);
  assert.deepEqual(searchExploration(index, '消息')[0].route, { kind: 'protocol', objectId: protocol.id });
  assert.deepEqual(searchExploration(index, ''), []);
  assert.deepEqual(searchExploration(index, 'not-present'), []);
});
test('design search uses a verified frozen title and deduplicates paragraphs without losing their terms', () => {
  const index = explorationSearchIndex({ objects: [], evidence: [evidence('a', '提交入口'), evidence('b', '旧回执过滤')] }, { 'frozen:one': '演示存储设计' });
  const rows = searchExploration(index, '存储 旧回执');
  assert.equal(rows.length, 1); assert.equal(rows[0].label, '演示存储设计'); assert.equal(rows[0].id, 'a');
  assert.equal(rows[0].route, undefined);
});
test('a replacement authorized model cannot retain a revoked object or an old design title', () => {
  const old = explorationSearchIndex({ objects: [module, protocol], evidence: [evidence('a', '入口')] }, { 'frozen:one': '旧批设计' });
  const next = explorationSearchIndex({ objects: [protocol], evidence: [] });
  assert.equal(searchExploration(old, '引擎').length, 1);
  assert.deepEqual(searchExploration(next, '引擎'), []); assert.deepEqual(searchExploration(next, '旧批设计'), []);
});
