'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const { PORTAL_MANAGEMENT_SCRIPTS } = require('./portal_management_scripts.cjs');

// The panel's part scripts and portal_management.js, in the page's order.
const sources = PORTAL_MANAGEMENT_SCRIPTS.map(file => [file, fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8')]);
const state = (overrides = {}) => ({
  version: 'v1',
  access: { mode: 'local', port: 18084, urls: ['http://127.0.0.1:18084'], legacy_restriction: false },
  proxy: { http: 'http://proxy.example:8080', https: '', all: '', no_proxy: 'localhost,127.0.0.1,::1' },
  proxy_source: 'environment', activation: { access: 'router_restart', proxy: 'new_processes' },
  ...overrides,
});
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let index = 0; index < 6; index++) await tick(); }

async function harness(t, { category = 'portal_admin', view = 'users' } = {}) {
  const dom = new JSDOM('<main id="host"></main>', { url: 'https://portal.example/', runScripts: 'outside-only' });
  const w = dom.window;
  const tag = (strings, ...values) => strings.reduce((text, part, index) => text + part + (values[index] ?? ''), '');
  w.PortalI18n = { t: text => text, html: tag, text: tag };
  w.PortalIdentity = { passwordRequirements: 'Password requirements' };
  w.PortalUI = { createDialogController: () => ({ open() {}, close() {}, destroy() {} }) };
  const requests = [], replies = [];
  w.fetch = async (url, options = {}) => {
    assert.equal(url, '/api/admin/network');
    assert.equal(options.credentials, 'same-origin');
    const request = { method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null };
    requests.push(request);
    const next = replies.shift();
    return next ? (typeof next === 'function' ? next(request) : next) : response(state());
  };
  const context = dom.getInternalVMContext();
  const enrollment = new vm.SyntheticModule(['mountEnrollment'], function () {
    this.setExport('mountEnrollment', () => ({ isBusy: () => false, hasUnsavedChanges: () => false, destroy() {} }));
  }, { context });
  await enrollment.link(() => { throw Error('Unexpected enrollment dependency'); });
  await enrollment.evaluate();
  for (const [filename, source] of sources) new vm.Script(source, { filename, importModuleDynamically: async specifier => {
    assert.equal(specifier, '/static/portal_user_enrollment.js');
    return enrollment;
  } }).runInContext(context);
  const root = w.document.getElementById('host');
  let panel;
  t.after(() => { panel?.destroy(); w.close(); });
  panel = await w.PortalManagementPanel.mount(root, {
    me: { username: 'admin', access: { user_category: category, permission_system: 'open', allows: { GetExecutionSettings: true } } },
    ...(view ? { view } : {}),
  });
  const field = key => root.querySelector(`[data-network-field="${key}"]`);
  const form = group => root.querySelector(`[data-network-form="${group}"]`);
  const edit = (key, value) => { field(key).value = value; field(key).dispatchEvent(new w.Event('input', { bubbles: true })); };
  const submit = async group => { form(group).dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); };
  const open = async () => { const opened = await panel.openSection({ view: 'network' }); await settle(); return opened; };
  return { w, root, panel, requests, replies, field, form, edit, submit, open };
}

test('access addresses load lazily and drafts survive switching management tabs', async t => {
  const h = await harness(t);
  assert.equal(h.requests.length, 0);
  await h.open();
  assert.equal(h.field('access_mode').value, 'local');
  assert.equal(h.field('port').value, '18084');
  assert.equal(h.root.querySelector('[data-network-current-url]').textContent, 'https://portal.example');
  assert.equal(h.root.querySelector('.managementNetworkUrl input').value, 'http://127.0.0.1:18084');
  h.edit('access_mode', 'remote'); h.edit('port', '19084'); h.edit('https', 'https://secure.example');
  assert.equal(h.panel.hasUnsavedChanges(), true);
  await h.panel.openSection({ view: 'users' }); await h.open();
  assert.equal(h.requests.length, 1);
  assert.equal(h.field('port').value, '19084');
  assert.equal(h.field('https').value, 'https://secure.example');
});

test('access save sends mode and port, replaces legacy policy, and keeps proxy draft', async t => {
  const h = await harness(t); await h.open();
  h.edit('access_mode', 'remote'); h.edit('port', '19084'); h.edit('http', 'http://draft.example:8080');
  h.replies.push(response(state({ version: 'v2', access: {
    mode: 'remote', port: 19084, urls: ['http://127.0.0.1:19084', 'http://192.168.1.4:19084'], legacy_restriction: false,
  } })));
  await h.submit('access');
  assert.deepEqual(h.requests.at(-1), { method: 'POST', body: { version: 'v1', access: { mode: 'remote', port: 19084 } } });
  assert.equal(h.root.querySelectorAll('.managementNetworkUrl').length, 2);
  assert.equal(h.field('http').value, 'http://draft.example:8080');
  assert.equal(h.panel.hasUnsavedChanges(), true);
  assert.match(h.root.querySelector('[data-network-message="access"]').textContent, /重启/);
});

test('legacy restriction is visible until an explicit save and copy uses shown address', async t => {
  const h = await harness(t);
  h.replies.push(response(state({ access: { mode: 'remote', port: 18084,
    urls: ['http://127.0.0.1:18084'], legacy_restriction: true } })));
  await h.open();
  assert.equal(h.root.querySelector('[data-network-legacy-note]').hidden, false);
  let copied = '';
  Object.defineProperty(h.w.navigator, 'clipboard', { value: { writeText: async value => { copied = value; } } });
  h.root.querySelector('.managementNetworkUrl button').click(); await settle();
  assert.equal(copied, 'http://127.0.0.1:18084');
});

test('proxy save preserves access draft, failed save keeps draft and conflict needs reload', async t => {
  const h = await harness(t); await h.open();
  h.edit('access_mode', 'remote'); h.edit('all', 'http://all.example:3128');
  h.replies.push(response(state({ version: 'v2', proxy_source: 'saved',
    proxy: { http: 'http://proxy.example:8080', https: '', all: 'http://all.example:3128', no_proxy: 'localhost,127.0.0.1,::1' } })));
  await h.submit('proxy');
  assert.deepEqual(h.requests.at(-1).body, { version: 'v1', proxy: {
    http: 'http://proxy.example:8080', https: '', all: 'http://all.example:3128', no_proxy: 'localhost,127.0.0.1,::1',
  } });
  assert.equal(h.field('access_mode').value, 'remote');
  h.replies.push(response({ error: 'version conflict' }, 409));
  await h.submit('access');
  assert.equal(h.panel.hasUnsavedChanges(), true);
  assert.match(h.root.querySelector('[data-network-message="access"]').textContent, /重新读取/);
  const count = h.requests.length;
  await h.submit('access'); assert.equal(h.requests.length, count);
  h.replies.push(response(state({ version: 'v3' })));
  h.root.querySelector('[data-network-reload]').click(); await settle();
  assert.equal(h.field('access_mode').value, 'remote');
  h.replies.push(response(state({ version: 'v4', access: { mode: 'remote', port: 18084,
    urls: ['http://127.0.0.1:18084'], legacy_restriction: false } })));
  await h.submit('access');
  assert.equal(h.requests.at(-1).body.version, 'v3');
});

test('non-admin cannot load the network panel', async t => {
  const h = await harness(t, { category: 'member' });
  assert.equal(await h.open(), false);
  assert.equal(h.requests.length, 0);
});

test('failed initial read keeps settings disabled until a successful reload', async t => {
  const h = await harness(t);
  h.replies.push(response({ error: 'temporarily unavailable' }, 503));
  await h.open();
  assert.match(h.root.querySelector('[data-network-status]').textContent, /temporarily unavailable/);
  assert.equal(h.form('access').querySelector('fieldset').disabled, true);
  await h.submit('access');
  assert.equal(h.requests.length, 1);
  h.root.querySelector('[data-network-reload]').click(); await settle();
  assert.equal(h.form('access').querySelector('fieldset').disabled, false);
});

test('pending access save prevents duplicate submissions and keeps close guard active', async t => {
  const h = await harness(t); await h.open();
  h.edit('access_mode', 'remote');
  let complete;
  h.replies.push(() => new Promise(resolve => { complete = resolve; }));
  await h.submit('access');
  assert.equal(h.panel.isBusy(), true);
  assert.equal(h.form('access').querySelector('fieldset').disabled, true);
  await h.submit('access');
  assert.equal(h.requests.length, 2);
  complete(response(state({ version: 'v2', access: {
    mode: 'remote', port: 18084, urls: ['http://127.0.0.1:18084'], legacy_restriction: false,
  } })));
  await settle();
  assert.equal(h.panel.isBusy(), false);
});
