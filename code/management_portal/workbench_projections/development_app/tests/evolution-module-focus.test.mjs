import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {REFS} from '../src/evolution/data.js';

const dom = new JSDOM(''); globalThis.document = dom.window.document;
const {moduleFocusView} = await import('../src/evolution/view.js');
after(() => {dom.window.close(); delete globalThis.document;});
const focus = id => ({kind: `module:${id}`, source: REFS.modules});
const facts = {mapMatches: true, modules: [{id: 'parent', title: 'Parent'},
  {id: 'child', parent: 'parent', title: 'Child'}, {id: 'unlinked', title: 'Unlinked'}],
  directions: [{id: 'direction-one', modules: ['child']},
    {id: 'pending', modules: ['unlinked'], module_mapping: 'needs_review'}]};

test('an unlinked module has a visible identified empty state without claiming pending mappings', () => {
  const view = moduleFocusView(focus('unlinked'), facts);
  assert.equal(view.getAttribute('aria-label'), '当前架构情境');
  assert.match(view.textContent, /当前模块：Unlinked/);
  assert.match(view.textContent, /unlinked/);
  assert.match(view.textContent, /暂无已确认的关联方向/);
});
test('parent focus includes actual constituent links and replaces the previous empty context', () => {
  const host = document.createElement('main');
  host.replaceChildren(moduleFocusView(focus('unlinked'), facts));
  host.replaceChildren(moduleFocusView(focus('parent'), facts));
  assert.match(host.textContent, /当前模块：Parent/);
  assert.match(host.textContent, /关联 1 个方向/);
  assert.doesNotMatch(host.textContent, /Unlinked|暂无/);
});
test('unavailable mappings and removed modules are not presented as verified empty results', () => {
  assert.match(moduleFocusView(focus('parent'), {...facts, mapMatches: false}).textContent, /暂不能确认/);
  assert.match(moduleFocusView(focus('removed'), facts).textContent, /不在已读取的架构地图/);
});
test('cleared or unrelated semantic focus does not keep a stale module context', () => {
  for (const object of [null, {kind: 'direction:one', source: REFS.evolution},
    {kind: 'module:parent', source: 'another-resource'}]) assert.equal(moduleFocusView(object, facts), null);
});
