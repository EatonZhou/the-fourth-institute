'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { babelParse, traverse } = require('./babel_parse.cjs');
const { execFileSync } = require('node:child_process');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const root = path.resolve(__dirname, '../../..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

async function harness(t, { visual = false } = {}) {
  const dom = new JSDOM('<main id="host"></main>', { url: 'https://portal.example/', runScripts: 'outside-only', pretendToBeVisual: visual });
  const observers = new Set();
  const NativeMutationObserver = dom.window.MutationObserver;
  dom.window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  t.after(() => { for (const observer of observers) observer.disconnect(); dom.window.close(); });
  const w = dom.window;
  w.PortalCompatibility = { identifier: name => `portal-${name}` };
  w.__PORTAL_LANGUAGE__ = 'en';
  w.PortalIdentity = { passwordRequirements: 'Password requirements' };
  for (const file of ['portal_text.js', 'portal_translations.js', 'portal_i18n.js']) w.eval(read(`management_portal/static/${file}`));
  await new Promise(resolve => setImmediate(resolve));
  return w;
}

// The English page with the chat workspace loaded as lineage.html loads it.
let routeScript = null;
async function chatHarness(t) {
  const w = await harness(t, { visual: true });
  routeScript ??= execFileSync(process.env.TFI_TEST_PYTHON || 'python3',
    ['-c', 'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
    { cwd: root, encoding: 'utf8' });
  w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
  w.PortalIdentity.username = () => 'sample_admin';
  w.eval(routeScript);
  for (const file of ['portal_state.js', 'portal_page_state.js', 'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js',
    'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
    'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
    w.eval(read(`management_portal/static/${file}`));
  }
  return w;
}

function functionSource(file, name) {
  const source = read(file);
  let result;
  traverse(babelParse(source, file), { FunctionDeclaration(p) {
    if (p.node.id?.name === name) result = source.slice(p.node.start, p.node.end);
  }});
  assert.ok(result, name);
  return result;
}

test('late HATG forms, tooltips, and accessibility labels translate without touching input', async t => {
  const w = await harness(t), host = w.document.getElementById('host');
  const doc = new w.DOMParser().parseFromString(read('management_portal/hatg-panel.html'), 'text/html');
  for (const node of doc.body.children) if (!['SCRIPT', 'HEADER'].includes(node.tagName)) host.append(w.document.importNode(node, true));
  w.PortalI18n.renderStatic(host);
  assert.equal(host.querySelector('#lineageNameInput').placeholder, 'Leave blank to use “Untitled Lineage”.');
  assert.equal(host.querySelector('#lineageDescriptionInput').placeholder, 'What problem will this Lineage work on together?');
  assert.match(host.querySelector('#graphEmpty').textContent, /This HATG has no nodes yet/);
  assert.equal(host.querySelector('#hatgPrimitiveEvent').getAttribute('aria-label'), 'Drag an event onto the HATG canvas');
  assert.match(host.querySelector('#nodeModal').textContent, /Agent name/);
  const input = host.querySelector('#lineageNameInput');
  input.value = '取消 / 用户命名';
  const content = w.document.createElement('article');
  content.textContent = '取消'; host.append(content);
  w.PortalI18n.apply('zh-CN');
  assert.equal(input.value, '取消 / 用户命名');
  assert.equal(content.textContent, '取消');
  w.PortalI18n.apply('en');
  assert.equal(input.placeholder, 'Leave blank to use “Untitled Lineage”.');
  assert.equal(content.textContent, '取消');
  host.remove();
  w.PortalI18n.apply('zh-CN');
  w.document.body.append(host);
  w.PortalI18n.renderStatic(host);
  assert.equal(input.placeholder, '留空时使用“未命名 Lineage”');
  w.PortalI18n.apply('en');
  assert.equal(input.placeholder, 'Leave blank to use “Untitled Lineage”.');
});

test('management service descriptions and network form render full English text', async t => {
  const w = await harness(t);
  w.eval(functionSource('management_portal/static/portal_management.js', 'panelMarkup') + ';window.auditPanelMarkup=panelMarkup;');
  const host = w.document.getElementById('host');
  host.innerHTML = w.auditPanelMarkup();
  const services = host.querySelector('[data-management-telepus]').closest('.managementExecutionGroup');
  assert.ok(!/[\u3400-\u9fff]/.test(services.textContent), services.textContent);
  assert.match(services.textContent, /Services and automation/);
  const source = read('management_portal/static/portal_management.js');
  let markup;
  traverse(babelParse(source, 'portal_management.js'), { FunctionDeclaration(p) {
    if (p.node.id?.name !== 'mountNetwork') return;
    p.traverse({ AssignmentExpression(q) {
      if (q.node.left.property?.name === 'innerHTML' && q.node.right.type === 'TaggedTemplateExpression') markup = source.slice(q.node.right.start, q.node.right.end);
    }});
  }});
  assert.ok(markup);
  host.innerHTML = w.eval(markup);
  assert.match(host.textContent, /Network settings/);
  assert.match(host.textContent, /Save access settings/);
  assert.match(host.textContent, /Save network proxy/);
  assert.ok(!/[\u3400-\u9fff]/.test(host.textContent), host.textContent);
});

test('dynamic UI messages keep user values and parameter-looking text intact', async t => {
  const w = await harness(t), i = w.PortalI18n;
  assert.equal(i.text`ResourceService 当前没有返回匹配的 ${'Skill'} 资源。`, 'No matching Skill resources were returned.');
  assert.equal(i.text`ResourceService 当前没有返回匹配的 ${'Experiment'} 资源。`, 'No matching Experiment resources were returned.');
  assert.equal(i.text`${0} 项等待`, '0 pending');
  assert.equal(i.text`观测于${'89 days ago'}`, 'Observed 89 days ago');
  assert.equal(i.text`属于 ${'中文设备 {p0}'}`, 'Part of 中文设备 {p0}');
  assert.equal(i.message('Family 门面（7 位成员）'), 'Family facade (7 members)');
  const dynamic = '用户内容：取消 <b>{p1}</b>';
  assert.equal(i.html`<p>${dynamic}</p>`, `<p>${dynamic}</p>`);
  assert.equal(i.html`<section data-panel-title="设备概览">${dynamic}</section>`, `<section data-panel-title="Device overview">${dynamic}</section>`);
});

test('deadline rendering uses English singular/plural through the actual project formatter', async t => {
  const w = await harness(t);
  w.PortalUI = { formatDateTime: date => date.toISOString() };
  w.PortalUIPrimitives = { escapeHtml: value => String(value) };
  w.eval(fs.readFileSync(path.join(root, 'management_portal/static/project_common.js'), 'utf8'));
  const now = new Date(2026, 9, 20);
  for (const [day, expected] of [[19, 'Overdue by 1 day'], [18, 'Overdue by 2 days'], [21, '1 day remaining'], [22, '2 days remaining']]) {
    assert.equal(w.PortalProjectCommon.formatDeadline(`2026-10-${day}`, now).label, expected);
  }
});

test('chat header localizes system prefixes while preserving account and Resident names', async t => {
  const w = await chatHarness(t);
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById('host'), id: 'chat',
    contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
  });
  workspace.setSubtitle('', [
    { text: '账号 中文账号 {p0}', identity: 'account', copy_value: '中文账号 {p0}' },
    { text: 'Family 门面（7 位成员）', identity: 'family' },
    { text: '取消', identity: 'resident' },
  ]);
  const parts = [...w.document.querySelectorAll('[data-workspace-status] [data-workspace-identity]')].map(part => part.textContent);
  workspace.destroy();
  assert.deepEqual(parts, ['Account 中文账号 {p0}', 'Family facade (7 members)', '取消']);
});

test('shared busy buttons translate temporary labels and restore the original button', async t => {
  const w = await harness(t);
  w.eval('const busyButtons = new WeakMap(); const el = value => value; const busyLabelAttributes = ["title", "aria-label"];' + functionSource('management_portal/static/portal_ui.js', 'setButtonBusy') + ';window.auditBusy=setButtonBusy;');
  const button = w.document.createElement('button');
  button.innerHTML = '<span>Original action</span>';
  w.auditBusy(button, true, '创建中…');
  assert.equal(button.textContent, 'Creating…');
  assert.equal(button.getAttribute('title'), 'Creating…');
  w.auditBusy(button, false);
  assert.equal(button.textContent, 'Original action');
  assert.equal(button.disabled, false);
});

test('active Family header translates the member label before appending runtime status', async t => {
  const w = await chatHarness(t);
  const header = { subtitle_parts: [{ identity: 'family', text: 'Family 门面（7 位成员）' }] };
  const project = (familyFacade, familyActivity) => w.PortalChatWorkspace.projectResidentConversation(
    { chat_workspace: { header } },
    { family_facade: familyFacade, state: { work_state: 'running', family_activity: familyActivity }, messages: [] },
  ).chat_workspace.header.subtitle_parts;
  const parts = project({ title: 'Fixture' }, { work_state: 'running', running_count: 2 });
  assert.equal(parts[0].text, 'Family facade (7 members) · Running (2 nodes)', 'the server label is translated before the status is appended');
  assert.equal(project({ title: 'Fixture', members: [{ title: 'Router' }, { title: 'Steward' }] }, { work_state: 'queued', running_count: 1 })[0].text,
    'Family facade (2 members) · Queuing', 'a known member list gives the count');
  assert.ok(!/[\u3400-\u9fff]/.test(parts[0].text), parts[0].text);
});
