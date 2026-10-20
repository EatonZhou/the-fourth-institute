const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(staticRoot, 'lineage_element_fields.js'), 'utf8');
const tag = (parts, ...values) => parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const i18n = {html: tag, text: tag, t: value => value};
const window = {PortalI18n: i18n, PortalUIPrimitives: {escapeHtml}};
const context = vm.createContext({window, CSS: {escape: value => value}});
vm.runInContext(source, context);
const fields = window.PortalLineageElementFields;

// Execute the private production functions without exporting a test-only API.
function functions(file, names, dependencies) {
  const text = fs.readFileSync(path.join(staticRoot, file), 'utf8');
  const code = names.map(name => {
    const match = new RegExp(`^( +)function ${name}\\(`, 'm').exec(text);
    assert.ok(match, name);
    const end = text.indexOf(`\n${match[1]}}\n`, match.index);
    assert.ok(end > match.index, name);
    return text.slice(match.index, end + match[1].length + 3);
  }).join('\n');
  const scope = vm.createContext(dependencies);
  vm.runInContext(code, scope);
  return scope;
}
const {recipientResults} = functions('lineage_element_fields.js', ['recipientResults'], {});
const graph = functions('hatg_panel.js', [
  'effectRecipients', 'effectRecipientsState', 'effectRecipientsChipsHtml',
  'effectRecipientsButtonHtml', 'functionBlockRecipientsSummaryHtml',
], {window, state: {programMutationInFlight: false}, esc: escapeHtml});
const effect = (id, config) => ({id, title: id, kind: 'effect', config});

test('existing missing, explicit closed and new draft states keep distinct labels', () => {
  assert.equal(fields.recipientState({}), 'unset');
  assert.equal(fields.recipientState({failure_recipients: []}), 'off');
  assert.equal(fields.recipientState({}, {draft: true}), 'default');
  assert.equal(fields.recipientState({failure_recipients: ['alice']}), 'on');
  const unset = fields.recipientChipsHtml([], {state: 'unset'});
  assert.match(unset, /尚未配置 · 当前不通知/);
  assert.doesNotMatch(unset, /已关闭|新建时通知/);
  assert.match(fields.recipientChipsHtml([], {state: 'off'}), /已关闭/);
  assert.match(fields.recipientChipsHtml([], {state: 'default'}), /新建时通知设立它的用户/);
  assert.match(fields.effectFieldsHtml({}, false, {effectId: 'old'}), /尚未配置 · 当前不通知/);
  assert.match(fields.effectFieldsHtml({failure_recipients: []}, false, {effectId: 'off'}), /已关闭/);
  assert.match(fields.effectFieldsHtml({}), /新建时通知设立它的用户/);
});

test('module overview groups missing and closed separately and shared row/issue buttons agree', () => {
  const old = effect('old', {}), off = effect('off', {failure_recipients: []});
  const html = graph.functionBlockRecipientsSummaryHtml([old, off], false);
  assert.match(html, /data-recipients-group="unset"/);
  assert.match(html, /data-recipients-group="off"/);
  assert.equal((html.match(/<li /g) || []).length, 2);
  assert.match(graph.effectRecipientsButtonHtml(old), /尚未配置 · 当前不通知/);
  assert.doesNotMatch(graph.effectRecipientsButtonHtml(old), /已关闭|新建时通知/);
  assert.match(graph.effectRecipientsButtonHtml(off), /已关闭/);
});

test('bulk partial coverage preserves untouched missing fields; explicit close writes empty lists', () => {
  const effects = [
    {id: 'old', title: 'Old', state: 'unset', recipients: []},
    {id: 'off', title: 'Off', state: 'off', recipients: []},
    {id: 'on', title: 'On', state: 'on', recipients: ['alice']},
    {id: 'draft', title: 'Draft', state: 'default', recipients: []},
  ];
  const preserved = recipientResults(effects, new Map([['alice', 'some']]));
  assert.ok(preserved.every(item => !item.changed));
  const removed = recipientResults(effects, new Map());
  assert.equal(removed[0].state, 'unset');
  assert.equal(removed[0].changed, false);
  assert.equal(removed[3].state, 'default');
  assert.equal(removed[3].changed, false);
  assert.deepEqual(Array.from(removed.filter(item => item.changed), item => item.id), ['on']);
  const closed = recipientResults(effects, new Map(), {close: true});
  assert.ok(closed.every(item => item.state === 'off' && item.recipients.length === 0));
  assert.deepEqual(Array.from(closed.filter(item => item.changed), item => item.id), ['old', 'on', 'draft']);
  const added = recipientResults(effects, new Map([['bob', 'all']]));
  assert.ok(added.every(item => item.changed && item.recipients[0] === 'bob'));

  const preview = functions('lineage_element_fields.js', ['resultText', 'previewHtml'], {
    t: i18n.t, i18n: () => i18n, esc: escapeHtml, recipientNames: names => names.join('、'),
    total: effects.length, results: () => removed,
    sameList: (left, right) => JSON.stringify(left) === JSON.stringify(right),
  }).previewHtml();
  assert.match(preview, /data-result-state="unset".*尚未配置 · 当前不通知/);
  assert.match(preview, /data-result-state="off".*关闭失败通知/);
  assert.match(preview, /data-result-state="default".*新建时通知设立它的用户/);
});

test('saving a live script or untouched draft omits recipients, while explicit draft close keeps []', () => {
  const root = configured => ({querySelector: selector => {
    if (selector === '[name="script"]') return {value: 'return 1'};
    if (selector === '[name="endpoints"]') return {value: ''};
    if (selector === '[name="failure_recipients"]' && configured !== null) {
      return {value: '[]', dataset: {effectRecipientsConfigured: configured}};
    }
    return null;
  }});
  assert.equal(Object.hasOwn(fields.effectConfigFromFields(root(null)), 'failure_recipients'), false);
  assert.equal(Object.hasOwn(fields.effectConfigFromFields(root('false')), 'failure_recipients'), false);
  assert.equal(JSON.stringify(fields.effectConfigFromFields(root('true')).failure_recipients), '[]');
});
