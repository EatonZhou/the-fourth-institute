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
  access: { mode: 'local', port: 18084, urls: ['http://127.0.0.1:18084'], legacy_restriction: false },
  proxy: { http: 'http://proxy.example:8080', https: '', all: '', no_proxy: 'localhost,127.0.0.1,::1' },
  proxy_source: 'environment',
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
  const shown = key => root.querySelector(`[data-network-value="${key}"]`).textContent;
  const open = async () => { const opened = await panel.openSection({ view: 'network' }); await settle(); return opened; };
  return { w, root, panel, requests, replies, shown, open };
}

test('network settings load lazily and are shown without any way to change them', async t => {
  const h = await harness(t);
  assert.equal(h.requests.length, 0);
  await h.open();
  assert.deepEqual(h.requests, [{ method: 'GET', body: null }]);
  assert.equal(h.shown('access_mode'), '仅本机');
  assert.equal(h.shown('port'), '18084');
  assert.equal(h.shown('http'), 'http://proxy.example:8080');
  assert.equal(h.shown('https'), '未设置');
  assert.match(h.root.querySelector('[data-network-source]').textContent, /服务环境/);
  assert.equal(h.root.querySelector('[data-network-current-url]').textContent, 'https://portal.example');
  assert.equal(h.root.querySelector('.managementNetworkUrl input').value, 'http://127.0.0.1:18084');
  // The installation owns these settings: no form, no editable field, no save.
  assert.equal(h.root.querySelector('.managementNetwork form, .managementNetwork select, [data-network-field]'), null);
  assert.ok([...h.root.querySelectorAll('.managementNetwork input')].every(input => input.readOnly));
  assert.equal(h.panel.hasUnsavedChanges(), false);
  await h.panel.openSection({ view: 'users' }); await h.open();
  assert.equal(h.requests.length, 1);
});

test('remote access, legacy restriction and saved proxy are shown, and copy uses the shown address', async t => {
  const h = await harness(t);
  h.replies.push(response(state({ access: { mode: 'remote', port: 19084,
    urls: ['http://127.0.0.1:19084', 'http://192.168.1.4:19084'], legacy_restriction: true },
  proxy_source: 'saved', proxy: { http: '', https: '', all: 'http://all.example:3128', no_proxy: 'localhost' } })));
  await h.open();
  assert.equal(h.shown('access_mode'), '允许其他设备访问');
  assert.equal(h.shown('port'), '19084');
  assert.equal(h.shown('all'), 'http://all.example:3128');
  assert.match(h.root.querySelector('[data-network-source]').textContent, /已保存/);
  assert.equal(h.root.querySelector('[data-network-legacy-note]').hidden, false);
  assert.equal(h.root.querySelectorAll('.managementNetworkUrl').length, 2);
  let copied = '';
  Object.defineProperty(h.w.navigator, 'clipboard', { value: { writeText: async value => { copied = value; } } });
  h.root.querySelector('.managementNetworkUrl button').click(); await settle();
  assert.equal(copied, 'http://127.0.0.1:19084');
});

test('non-admin cannot load the network panel', async t => {
  const h = await harness(t, { category: 'member' });
  assert.equal(await h.open(), false);
  assert.equal(h.requests.length, 0);
});

test('a failed read reports the error and a reload shows the settings', async t => {
  const h = await harness(t);
  h.replies.push(response({ error: 'temporarily unavailable' }, 503));
  await h.open();
  assert.match(h.root.querySelector('[data-network-status]').textContent, /temporarily unavailable/);
  assert.equal(h.shown('port'), '');
  h.root.querySelector('[data-network-reload]').click(); await settle();
  assert.equal(h.shown('port'), '18084');
  assert.equal(h.root.querySelector('[data-network-status]').textContent, '');
});

test('a pending read disables reload and keeps the close guard active', async t => {
  const h = await harness(t);
  let complete;
  h.replies.push(() => new Promise(resolve => { complete = resolve; }));
  await h.open();
  assert.equal(h.panel.isBusy(), true);
  assert.equal(h.root.querySelector('[data-network-reload]').disabled, true);
  h.root.querySelector('[data-network-reload]').click(); await settle();
  assert.equal(h.requests.length, 1);
  complete(response(state()));
  await settle();
  assert.equal(h.panel.isBusy(), false);
  assert.equal(h.root.querySelector('[data-network-reload]').disabled, false);
});
