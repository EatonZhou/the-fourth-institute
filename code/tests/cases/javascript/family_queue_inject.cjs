'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const ROOT = path.resolve(__dirname, '../../..');
const FACADE = 'resident_111111111111';
const MEMBER = 'resident_222222222222';
const REQUEST = 'msg_facade_request';
const COPY = 'msg_member_copy';
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

async function main() {
  const dom = new JSDOM('<!doctype html><body><main id="chat"></main></body>', {
    url: 'https://family-queue.fixture.invalid/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  const observers = [];
  const Observer = w.MutationObserver;
  w.MutationObserver = class extends Observer { constructor(callback) { super(callback); observers.push(this); } };
  const calls = [], errors = [], notices = [];
  let supported = true, workState = 'running', activeMessageId = 'msg_current', workPhase = '';
  let rejectInjection = false, holdState = null;
  w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, c => `\\${c}`) };
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.matchMedia = media => Object.assign(new w.EventTarget(), { media, matches: false });
  w.PortalIdentity = { username: () => 'fixture-user' };
  w.PortalAPI = { get: async url => {
    assert.match(url, /session-restart/, 'only session restart configuration is requested');
    return { config: {}, restart: null };
  } };
  const template = (strings, ...values) => strings.reduce((s, v, i) => s + v + (values[i] ?? ''), '');
  w.PortalI18n = { t: x => x, message: x => x, text: template, html: template, markup: x => x };
  w.eval(require('node:child_process').execFileSync('python3', ['-c',
    'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
  { cwd: ROOT, encoding: 'utf8' }));
  // Only the instruction transport is a fixture. Service, protocol, panel,
  // rendering, DOM click delegation and action locking use production code.
  w.PortalInstructionClient = { execute: async (instruction, args) => {
    calls.push({ instruction, args: JSON.parse(JSON.stringify(args)) });
    if (instruction === 'ListInputHistory') return { entries: [] };
    if (instruction === 'ListResidentStates') {
      if (holdState) await holdState;
      return { states: [{ resident_id: MEMBER, controls: { active_turn_input: supported },
        state: { work_state: workState, active_message_id: activeMessageId, work_phase: workPhase } }] };
    }
    if (instruction === 'InjectMailboxItem') {
      if (rejectInjection) throw new Error('fixture: 当前请求已经离开队列');
      return { resident_id: args.resident_id, accepted: true, injected: false, status: 'pending', delivery_status: 'pending' };
    }
    if (instruction === 'CancelInput') return { cancelled: true };
    throw new Error(`Unexpected instruction: ${instruction}`);
  } };
  for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_ui_primitives.js', 'portal_ui.js',
    'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
    'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js',
    'portal_resident_service.js', 'resident_protocol.js', 'portal_chat_workspace.js']) {
    const sourcePath = name === 'portal_chat_workspace.js' && process.env.CHAT_SOURCE
      ? process.env.CHAT_SOURCE : path.join(ROOT, 'management_portal/static', name);
    w.eval(fs.readFileSync(sourcePath, 'utf8'));
  }
  w.PortalUI.toast = (text, kind) => notices.push({ text, kind });
  const panel = w.PortalChatWorkspace.panelPackage.create({ root: w.document.getElementById('chat'),
    id: 'familyQueueFixture', autoLoad: false, target: { resident_id: FACADE }, onError: error => errors.push(error.message) });
  const message = { message_id: REQUEST, role: 'user', author_id: 'fixture-user', content_parts: [{ type: 'text', text: 'queued request' }],
    relay: { state: 'queued', resident_id: MEMBER, message_id: COPY, title: 'Actual member', ahead: 1,
      via: [{ resident_id: 'resident_333333333333', title: 'Nested facade' }] } };
  let refreshes = 0;
  let snapshot = { family_facade: { resident_id: FACADE }, messages: [message] };
  // Healthy model roles must not conceal an unusable escalation channel.
  const metadata = { chat_workspace: { header: { subtitle_parts: [{ identity: 'family', text: 'Family' }] }, composer: {} } };
  const healthSnapshot = { ...snapshot, family_facade: { ...snapshot.family_facade,
    alert_contact: { status: 'missing' }, nodes: [{ role: 'router', availability: { status: 'available' } }] } };
  const healthLabel = () => w.PortalChatWorkspace.projectResidentConversation(metadata, healthSnapshot)
    .chat_workspace.header.subtitle_parts.find(part => part.identity === 'family_availability')?.text || '';
  assert.equal(healthLabel(), '未登记真人告警联系人');
  healthSnapshot.family_facade.nodes.push({ role: 'steward', availability: { status: 'unavailable', reason: '额度已用尽' } });
  assert.match(healthLabel(), /Steward：额度已用尽；未登记真人告警联系人/);
  healthSnapshot.family_facade.alert_contact.status = 'configured';
  healthSnapshot.family_facade.nodes.pop();
  assert.equal(healthLabel(), '', 'a configured contact and healthy roles clear the notice');
  // An open facade keeps its projection metadata while roster snapshots change.
  const rosterMetadata = { chat_workspace: { header: { subtitle_parts: [
    { identity: 'family', text: 'Family 门面（2 位成员）' },
    { identity: 'resident', text: FACADE },
  ] }, composer: {} } };
  const rosterSnapshot = { ...snapshot, family_facade: { resident_id: FACADE,
    members: [{ resident_id: MEMBER }, { resident_id: 'resident_333333333333' },
      { resident_id: 'resident_444444444444' }] } };
  const rosterParts = () => w.PortalChatWorkspace.projectResidentConversation(rosterMetadata, rosterSnapshot)
    .chat_workspace.header.subtitle_parts;
  assert.equal(rosterParts()[0].text, 'Family 门面（3 位成员）', 'new members appear without a page reload');
  assert.equal(rosterParts()[1].text, FACADE, 'unrelated identity text is retained');
  rosterSnapshot.family_facade.members.pop();
  assert.equal(rosterParts()[0].text, 'Family 门面（2 位成员）', 'removal updates the same facade');
  rosterSnapshot.family_facade.members = [];
  assert.equal(rosterParts()[0].text, 'Family 门面', 'empty roster follows the initial header contract');
  delete rosterSnapshot.family_facade.members;
  assert.equal(rosterParts()[0].text, 'Family 门面（2 位成员）', 'partial snapshots do not invent a zero roster');
  assert.equal(rosterMetadata.chat_workspace.header.subtitle_parts[0].text, 'Family 门面（2 位成员）',
    'the cached metadata is not mutated');
  panel.protocol = { snapshot: () => snapshot, load: async () => { refreshes++; return snapshot; }, destroy() {} };
  function render() {
    const projected = w.PortalChatWorkspace.projectResidentConversation({}, snapshot);
    panel.detail = { messages: projected.messages, chat_workspace: { composer: {} } };
    panel.workspace.setMessages(projected.messages);
    panel.syncActions();
  }
  const button = () => panel.workspace.root.querySelector('[data-portal-message-action="family-request-inject"]');
  const injections = () => calls.filter(c => c.instruction === 'InjectMailboxItem');
  try {
    render();
    assert.ok(button(), 'queued facade request has an immediate-injection button');
    assert.equal(button().textContent, '立即注入');
    let release;
    holdState = new Promise(resolve => { release = resolve; });
    button().click(); button().click();
    assert.equal(button().disabled, true, 'pending capability request disables duplicate clicks');
    release(); holdState = null; await tick();
    assert.deepEqual(injections(), [{ instruction: 'InjectMailboxItem', args: { resident_id: MEMBER, message_id: COPY } }],
      'nested facade injects the exact worker copy, never the facade request');
    assert.equal(refreshes, 1, 'facade refreshes after member acknowledgement');
    assert.ok(notices.some(n => /等待.*接收/.test(n.text)), 'pending is not reported as delivered');
    assert.equal(button().disabled, false);

    supported = false; button().click(); await tick();
    assert.equal(injections().length, 1);
    assert.match(errors.at(-1), /不支持|不能.*接收/);
    assert.equal(button().disabled, false, 'unsupported leaves the request actionable');
    supported = undefined; button().click(); await tick();
    assert.equal(injections().length, 1);
    assert.match(errors.at(-1), /无法确认/);
    supported = true; workState = 'idle'; button().click(); await tick();
    assert.equal(injections().length, 1);
    assert.match(errors.at(-1), /正在进行|活动|当前.*工作/);
    workState = 'running'; rejectInjection = true; button().click(); await tick();
    assert.equal(injections().length, 2);
    assert.match(errors.at(-1), /已经离开队列/);
    assert.ok(button(), 'failed injection leaves request and retry control visible');
    rejectInjection = false; button().click(); await tick();
    assert.equal(injections().length, 3, 'retry after failure succeeds');

    holdState = new Promise(resolve => { release = resolve; });
    button().click();
    message.relay = { ...message.relay, message_id: 'msg_new_member_copy' }; render();
    release(); holdState = null; await tick();
    assert.equal(injections().length, 3, 'reroute during preflight never injects stale copy');
    message.relay.message_id = COPY;
    render(); holdState = new Promise(resolve => { release = resolve; });
    button().click(); panel.targetRevision++;
    release(); holdState = null; await tick();
    assert.equal(injections().length, 3, 'conversation change cancels pending preflight');
    for (const state of ['routing', 'delivering', 'running', 'completed']) {
      message.relay.state = state; render();
      assert.equal(button(), null, `${state} is not a queued injection action`);
    }
    message.relay.state = 'queued'; delete message.relay.message_id; render();
    assert.ok(!button() || button().disabled, 'missing member message identity cannot inject');
    message.relay.message_id = COPY; render();
    const readonly = w.PortalChatWorkspace.requestProgressHtml({ ...message.relay, request_message_id: REQUEST }, {}, { actions: false });
    assert.ok(!readonly.includes('family-request-inject'), 'read-only chatroom progress has no injection action');

    // A created turn can be queued while binding or awaiting execution capacity.
    // Its durable input remains injectable; idle/held queues have no active ID.
    workState = 'queued';
    for (const phase of ['starting_session', 'waiting_capacity', 'waiting_dispatch', 'waiting_quota']) {
      workPhase = phase;
      const before = injections().length;
      const errorCount = errors.length;
      button().click(); await tick();
      assert.equal(injections().length, before + 1, `${phase} keeps the member's active turn injectable`);
      assert.deepEqual(injections().at(-1).args, { resident_id: MEMBER, message_id: COPY });
      assert.equal(errors.length, errorCount, `${phase} is not reported as having no active work`);
      assert.equal(notices.at(-1).kind, 'success');
      assert.match(notices.at(-1).text, /等待.*接收/, 'acceptance remains pending, not delivered');
    }
    const beforeUnavailable = injections().length;
    for (const [state, active] of [['queued', ''], ['idle', ''], ['stopping', 'msg_current'], ['running', '']]) {
      workState = state; activeMessageId = active;
      const errorCount = errors.length;
      button().click(); await tick();
      assert.equal(injections().length, beforeUnavailable, `${state}/${active} cannot inject`);
      assert.equal(errors.length, errorCount + 1);
    }

    snapshot = { messages: [], controls: { active_turn_input: true } };
    panel.workspace.setTarget({ resident_id: MEMBER });
    panel.workspace.applyContract({ schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: MEMBER },
      header: { title: 'Ordinary member' }, messages: [], composer: {
        queue: { items: [{ message_id: COPY, text: 'ordinary queued' }], active_turn_input_supported: true } } });
    panel.workspace.root.querySelector('[data-workspace-queue-inject]').click(); await tick();
    assert.deepEqual(injections().at(-1).args, { resident_id: MEMBER, message_id: COPY }, 'ordinary Resident queue still uses the same instruction');
    console.log(JSON.stringify({ status: 'passed', cases: ['queued-render', 'nested-member-copy', 'duplicate-click', 'pending-receipt',
      'unsupported', 'unknown-capability', 'idle', 'failure-retry', 'reroute-race', 'target-change', 'nonqueued', 'missing-id', 'readonly',
      'waiting-active-turn', 'held-queue', 'stopping', 'missing-active-id', 'ordinary-queue'], calls }));
  } finally { await tick(); panel.destroy(); for (const observer of observers) observer.disconnect(); await tick(); w.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
