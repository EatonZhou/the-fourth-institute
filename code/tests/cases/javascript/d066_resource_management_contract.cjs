'use strict';

const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../../..');
const tag = (parts, ...values) => String.raw({ raw: parts }, ...values);
const copy = value => JSON.parse(JSON.stringify(value));
const noop = () => {};

function setup(t, markup = '', url = 'http://fixture/') {
  const { window } = new JSDOM(markup, { runScripts: 'outside-only', url });
  t.after(() => window.close());
  window.PortalI18n = { t: value => value, text: tag, html: tag, locale: () => 'en' };
  window.PortalUI = { confirm: async () => true, formatDateTime: value => value };
  window.PortalUIPrimitives = { escapeHtml: value => value, randomUUID: () => 'test-key' };
  const load = (name, transform = value => value) => {
    const source = 'management_portal/static/' + name;
    const base = process.env.D066_TEST_BASE;
    const code = base
      ? execFileSync('git', ['show', `${base}:code/${source}`], { cwd: root, encoding: 'utf8' })
      : fs.readFileSync(path.join(root, source), 'utf8');
    window.eval(transform(code));
  };
  return { window, load };
}

test('device credential and connection results use the named resource APIs', async t => {
  const html = fs.readFileSync(path.join(root, 'management_portal/resource_catalog.html'), 'utf8');
  const { window, load } = setup(t, html, 'http://fixture/resource/device');
  const calls = [];
  let probe = { ok: true };
  window.PortalUIPrimitives.objectOf = value => value || {};
  window.PortalUIPrimitives.asyncStateHtml = options => options.message;
  window.PortalUI.createDialogController = () => ({ open: noop, close: noop });
  window.PortalUI.toast = noop;
  window.PortalUI.runMutation = async options => {
    try { await options.onSuccess?.(await options.run()); }
    catch (error) { if (!options.onError) throw error; options.onError(error); }
  };
  window.PortalSite = { setTitle: noop };
  window.PortalFormControls = { bindFilterDropdown: () => ({ open: noop, close: noop }) };
  window.PortalComponentRecovery = { create: () => ({ guard: fn => fn(), run: noop }) };
  window.PortalResourceDetail = {
    createDetailHistory: () => ({ acceptCurrent: noop, replace: noop }),
    createFrameHost: () => ({}), createLocalHost: () => ({ markChanged: noop }),
  };
  window.PortalInstructionClient = { execute: async (name, args) => {
    calls.push([name, copy(args)]);
    return name === 'RunResourceAction' ? { result: probe } : probe;
  } };
  load('resource_catalog.js');
  const id = 'device_0123456789ab';
  const body = window.document.getElementById('resourceDetailBody');
  body.innerHTML = `<div><button data-device-ssh-test="${id}">Test</button><span class="deviceSshTestResult"></span></div>
    <form data-device-ssh-password-form="${id}"><input type="password" name="password" value="fixture-only">
      <button type="submit">Save</button><span class="deviceSshPasswordMessage"></span></form>
    <button data-device-ssh-password-delete="${id}">Delete</button>`;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const status = body.querySelector('.deviceSshTestResult');
  const testButton = body.querySelector('[data-device-ssh-test]');
  testButton.click();
  await tick();
  assert.ok(status.classList.contains('success'));
  probe = { ok: false, error: 'fixture unreachable' };
  testButton.click();
  await tick();
  assert.ok(status.classList.contains('error'));
  assert.match(status.textContent, /fixture unreachable/);
  const form = body.querySelector('form');
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  await tick();
  body.querySelector('[data-device-ssh-password-delete]').click();
  await tick();
  assert.deepEqual(calls, [
    ['CheckDeviceSsh', { resource_id: id }],
    ['CheckDeviceSsh', { resource_id: id }],
    ['UpdateDeviceSshCredential', { resource_id: id, password: 'fixture-only' }],
    ['DeleteDeviceSshCredential', { resource_id: id }],
  ]);
  assert.equal(form.elements.password.value, '');
});

test('permission preview and instruction decision use separate read APIs', async t => {
  const { window, load } = setup(t);
  load('portal_permission_schemes_compile.js');
  load('portal_permission_schemes_check.js');
  const calls = [];
  const run = async (name, args) => { calls.push([name, copy(args)]); return { allowed: true }; };
  const state = { current: { scheme_id: 'fixture' }, checkInput: {
    principal: 'alice', instruction: 'RegisterResource', arguments: { 'fields.project_id': 'example' },
  } };
  const compile = window.PortalPermissionSchemesCompile.createCompile({
    clone: copy, dirty: () => true, may: () => true, mountState: {}, render: noop, renderStatus: noop, run, state,
  });
  const check = window.PortalPermissionSchemesCheck.createCheck({
    dirty: () => false, elementsOf: () => [], enabled: () => true, esc: String,
    instructionNames: () => [], render: noop, run, state, t: value => value, tx: tag,
  });
  const rules = { title: 'edited' };
  await compile.checkRules(rules);
  await compile.previewDiff(rules, { source_digest: 'fixture-digest' });
  await check.tryDecide();
  assert.deepEqual(calls, [
    ['GetPermissionSchemeUpdatePreview', { scheme_id: 'fixture', rules, diff: false }],
    ['GetPermissionSchemeUpdatePreview', { scheme_id: 'fixture', rules, diff_limit: 50, source_digest: 'fixture-digest' }],
    ['CheckInstructionPermission', { principal_id: 'alice', instruction: 'RegisterResource', arguments: {
      fields: { project_id: 'example' },
    } }],
  ]);
  assert.equal(state.checkResult.allowed, true);
});

test('resource type page uses the dedicated capability, preserves CAS and omits action metadata', async t => {
  const { window, load } = setup(t, '<main></main>');
  load('portal_management_resource_types.js', code => code.replace('export async function mount', 'window.mount = async function mount'));
  const calls = [];
  const row = { type: 'example', state: 'enabled', enabled: true, category: 'resource', version: 4, declaration: {} };
  window.PortalInstructionClient = { execute: async (name, args, options) => {
    calls.push([name, copy(args), options && copy(options)]);
    if (name === 'ListResourceTypes') return { types: [copy(row)] };
    if (name === 'GetResourceType') return copy(row);
    return { enabled: false, state: 'disabled', version: 5 };
  } };
  const root = window.document.querySelector('main');
  await window.mount(root, { me: { access: { permission_system: 'on', allows: { SetResourceTypeEnabled: true } } } });
  assert.equal(root.querySelector('[data-types-actions]'), null);
  root.querySelector('[data-type-details]').click();
  await new Promise(resolve => setImmediate(resolve));
  const toggle = root.querySelector('[data-type-toggle]');
  assert.ok(toggle);
  toggle.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.at(-1), ['SetResourceTypeEnabled', { resource_type: 'example', enabled: false }, { expectedVersion: 4 }]);
  assert.equal(root.querySelector('[data-type-toggle]').textContent, '启用');
});

test('permission management previews before activation and uses the dedicated system switch', async t => {
  const { window, load } = setup(t, '<section><select data-permission-scheme></select></section>');
  load('portal_management_permission_system.js');
  const calls = [];
  const panelState = { pending: 0, savedExecution: {}, permissionsOff: false };
  window.PortalInstructionClient = { execute: async (name, args) => {
    calls.push([name, copy(args)]);
    if (name === 'ListPermissionSchemes') return { schemes: [{ scheme_id: 'initial', active: true }, { scheme_id: 'next' }] };
    if (name === 'SetPermissionSystemEnabled' || name === 'UpdateExecutionSettings') {
      return { settings: { permission_system_enabled: args.enabled ?? args.permission_system_enabled } };
    }
    return { title: 'Next' };
  } };
  const section = window.document.querySelector('section');
  const permission = window.PortalManagementPermissionSystem.createPermissionSystem({
    $: noop, acceptExecution: settings => Object.assign(panelState.savedExecution, settings),
    entries: new Map(), executionSection: section, executionStatus: {}, maySwitch: true, me: {},
    panelState, permissionStatus: {}, permissionSwitches: [], root: section, showSetting: noop,
    switchMessages: { permission_system_enabled: String }, tabs: section,
  });
  permission.readSchemes();
  await new Promise(resolve => setImmediate(resolve));
  const select = section.querySelector('select');
  select.value = 'next';
  select.dispatchEvent(new window.Event('change'));
  await new Promise(resolve => setImmediate(resolve));
  await permission.savePermissionSystem(false);
  assert.deepEqual(calls.slice(1, 4), [
    ['GetPermissionSchemeActivationPreview', { scheme_id: 'next', diff_limit: 8 }],
    ['ActivatePermissionScheme', { scheme_id: 'next' }],
    ['SetPermissionSystemEnabled', { enabled: false }],
  ]);
  assert.equal(panelState.savedExecution.permission_system_enabled, false);
  assert.equal(panelState.pending, 0);
});

test('user editor separates profile and administrator writes and retries only an uncommitted field', async t => {
  const { window, load } = setup(t, `
    <div id="notice"></div><div id="empty"></div><div id="user-list"></div><div id="audit-list"></div>
    <div id="password-reset-status"></div><form id="editor"><span id="editor-name"></span><span id="editor-meta"></span>
      <input id="display-name"><input id="email">
      <select id="status"><option value="active">active</option><option value="disabled">disabled</option></select>
      <select id="category"><option value="member">member</option><option value="intern">intern</option><option value="portal_admin">portal_admin</option></select>
      <span id="admin-note"></span><button type="button" id="reset-password-button"></button>
      <button type="button" id="remove-user-button"></button><button type="submit">Save</button>
    </form>`);
  load('portal_management_users.js');
  const $ = id => window.document.getElementById(id);
  const row = { username: 'bob', display_name: 'Bob', status: 'active', category: 'member', allows: { UpdateUser: true } };
  const panelState = { pending: 0 }, userDrafts = new Map(), baselines = new Map();
  const state = { access: { users: [copy(row)] }, selectedUsername: row.username };
  const inputs = ['display-name', 'email', 'status', 'category'];
  const snapshot = () => JSON.stringify(inputs.map(name => $(name).value));
  const remember = editor => baselines.set(editor, snapshot());
  const saveUserDraft = () => {
    const baseline = baselines.get($('editor')), values = snapshot();
    if (values !== baseline) userDrafts.set(row.username, { baseline, values });
    else userDrafts.delete(row.username);
  };
  window.PortalAPI = { get: async () => ({ users: [copy(row)] }) };
  const calls = [];
  let failCategory = true;
  window.PortalInstructionClient = { execute: async (name, args) => {
    calls.push([name, copy(args)]);
    if (name === 'UpdateUser') Object.assign(row, copy(args.changes));
    if (name === 'UpdateUserStatus') row.status = args.status;
    if (name === 'UpdateUserCategory') {
      if (failCategory) throw new Error('Fixture failure');
      row.category = args.category;
    }
    return {};
  } };
  const deps = { $, baselines, invalidate: noop, openSection: noop, panelState, remember, saveUserDraft, state, userDrafts };
  const delegate = window.PortalManagementUsers.createUsers({ ...deps, me: { username: 'member', access: { user_category: 'member' } } });
  delegate.renderEditor();
  assert.equal($('display-name').disabled, false);
  assert.equal($('status').disabled, true);
  assert.equal($('category').disabled, true);
  const admin = window.PortalManagementUsers.createUsers({ ...deps, me: { username: 'admin', access: { user_category: 'portal_admin' } } });
  admin.renderEditor();
  $('display-name').value = 'Robert';
  $('status').value = 'disabled';
  $('category').value = 'intern';
  await admin.saveAccess({ preventDefault: noop });
  assert.deepEqual(calls, [
    ['UpdateUser', { user_id: 'bob', changes: { display_name: 'Robert' } }],
    ['UpdateUserStatus', { user_id: 'bob', status: 'disabled' }],
    ['UpdateUserCategory', { user_id: 'bob', category: 'intern' }],
  ]);
  assert.equal($('notice').textContent, 'Fixture failure');
  assert.equal($('category').value, 'intern');
  assert.equal(state.access.users[0].status, 'disabled');
  assert.equal(panelState.pending, 0);
  failCategory = false;
  await admin.saveAccess({ preventDefault: noop });
  assert.deepEqual(calls.slice(3), [['UpdateUserCategory', { user_id: 'bob', category: 'intern' }]]);
  assert.equal(userDrafts.size, 0);
});
