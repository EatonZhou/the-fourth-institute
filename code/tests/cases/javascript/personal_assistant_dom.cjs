"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { JSDOM, VirtualConsole } = require("jsdom");

const repo = path.resolve(__dirname, "../../..");
const source = name => fs.readFileSync(path.join(repo, "management_portal/static", name), "utf8");
const routeScript = execFileSync("python3", ["-c", "from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())"], { cwd: repo, encoding: "utf8" });
const AMA = "resident_111111111111", FACADE = "resident_222222222222", LINEAGE = "lineage_aaaaaaaaaaaa";
const drain = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

// The browser retains pointer/focus, popup placement, touch and drag/avoidance.
// Here the real Panel, protocol, renderer and read observer run in a DOM without
// geometry. Only network responses, stream connectivity and frame timing are I/O.
function chat(t, handlers = {}, options = {}) {
  const errors = [], requests = [], observers = new Set(), frames = new Map();
  const log = new VirtualConsole();
  log.on("jsdomError", error => errors.push(error.message));
  log.on("error", (...args) => errors.push(args.map(String).join(" ")));
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body><section id="fixture"></section></body></html>', {
    url: "https://personal-assistant.fixture.invalid/settings.html", pretendToBeVisual: true, runScripts: "outside-only", virtualConsole: log,
  });
  const w = dom.window;
  const NativeMutationObserver = w.MutationObserver;
  w.MutationObserver = class extends NativeMutationObserver { constructor(callback) { super(callback); observers.add(this); } };
  let frameId = 0;
  w.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  w.cancelAnimationFrame = id => frames.delete(id);
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
  w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
  w.PortalIdentity = { username: () => "playwright" };
  w.PortalEvents = { isConnected: () => true, connect() {} };
  w.PortalAPI = { get: async url => {
    assert.match(url, /\/session-restart-options(?:\?|$)/);
    return { restart: null };
  } };
  w.PortalInstructionClient = { execute: async (instruction, args) => {
    requests.push({ instruction, args: plain(args) });
    if (instruction === "ListInputHistory") return { items: [] };
    assert.ok(handlers[instruction], `Unexpected instruction ${instruction}`);
    return handlers[instruction](args);
  } };
  w.eval(routeScript);
  for (const name of ["portal_state.js", "portal_page_state.js", "portal_text.js", "portal_ui_primitives.js", "portal_ui.js",
    "portal_overlay.js", "portal_resource_chips.js", "portal_image_editor.js", "portal_editor_open_service.js",
    "portal_command_registry.js", "portal_workbench_context.js", "portal_workbench.js", "portal_resident_service.js",
    "resident_protocol.js", "portal_chat_workspace.js"]) w.eval(source(name) + `\n//# sourceURL=${name}`);
  const panel = w.PortalChatWorkspace.panelPackage.create({ root: w.document.getElementById("fixture"), id: "assistant",
    autoLoad: false, target: { resident_id: FACADE, lineage_id: LINEAGE }, onError: error => errors.push(String(error)), ...options });
  t.after(async () => {
    await drain(); panel.destroy();
    w.dispatchEvent(new w.Event("pagehide"));
    for (const observer of observers) observer.disconnect();
    await drain(); w.close();
    assert.deepEqual(errors, [], "no production or DOM errors");
  });
  return { w, panel, requests, root: panel.workspace.els.messages,
    async frame() { const batch = [...frames.values()]; frames.clear(); batch.forEach(callback => callback(0)); await drain(); },
    load: (composer = {}) => panel.setResident(panel.target(), { detail: { resident: { resident_id: FACADE, lineage_id: LINEAGE },
      chat_workspace: { schema: "portal.chat_workspace.v3", target: { kind: "resident", id: FACADE, lineage_id: LINEAGE },
        messages: [], composer: { can_send: true, ...composer } } } }),
  };
}

test("history pages retain source identity, add one boundary and quote the original message through the facade", async t => {
  const history = { resident_id: AMA, title: "我的 AMA", before_at: "2026-10-16T00:00:00Z" };
  const segment = { ...history, before_ordinal: 30 };
  const message = (id, historical = false, role = "agent") => ({ message_id: id, role, author_id: role === "user" ? "playwright" : AMA,
    created_at: historical ? "2026-10-15T23:00:00Z" : "2026-10-16T00:01:00Z",
    content_parts: [{ type: "text", text: id }], ...(historical ? { family_history: { ...history, message_id: id } } : {}) });
  const pages = {
    "facade-older-token": { messages: [message("facade-first")], next_cursor: "personal-history:20" },
    "personal-history:20": { messages: [message("ama-later", true)], next_cursor: "personal-history:10" },
    "personal-history:10": { messages: [message("ama-earlier", true, "user")] },
  };
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE }, messages: [message("facade-latest")], family_history: segment,
      next_cursor: "facade-older-token", cursor: 1, state: { work_state: "idle" }, controls: { can_send: true }, queue: { items: [] } }),
    ListMessages: args => ({ resident_id: args.resident_id, family_history: segment, ...pages[args.cursor] }),
    DeliverMessage: args => ({ accepted: true, resident_id: args.resident_id, message_id: args.message.message_id, input_state: { state: "queued" } }),
  });
  await app.load();
  await app.panel.loadOlder();
  assert.equal(app.root.querySelectorAll("[data-family-history-boundary]").length, 0);
  await app.panel.loadOlder();
  assert.equal(app.root.querySelector("[data-family-history]").textContent, "我的 AMA 的历史");
  assert.equal(app.root.querySelectorAll("[data-family-history-boundary]").length, 1);
  await app.panel.loadOlder();
  assert.deepEqual(app.requests.filter(item => item.instruction === "ListMessages").map(item => item.args.cursor), Object.keys(pages));
  assert.deepEqual([...app.root.querySelectorAll("article[data-message-key]")].map(row => row.dataset.messageKey),
    ["ama-earlier", "ama-later", "facade-first", "facade-latest"]);
  assert.equal(app.root.querySelectorAll("[data-family-history]").length, 2);
  assert.equal(app.root.querySelectorAll("[data-family-history-boundary]").length, 1);
  assert.match(app.root.querySelector("[data-family-history-boundary]").textContent, /个人助手从此处接续对话/);
  assert.equal(app.root.querySelector('[data-message-key="ama-earlier"]').classList.contains("human"), true);
  const original = app.panel.snapshot().messages[0];
  assert.deepEqual(plain({ role: original.role, author_id: original.author_id, message_id: original.message_id, family_history: original.family_history }),
    { role: "user", author_id: "playwright", message_id: "ama-earlier", family_history: { ...history, message_id: "ama-earlier" } });
  app.root.querySelector('[data-message-key="ama-earlier"] [data-portal-message-action="quote"]').click();
  await app.panel.send({ text: "继续旧话题", refs: [], attachments: [] });
  const sent = app.requests.find(item => item.instruction === "DeliverMessage").args;
  assert.equal(sent.resident_id, FACADE);
  assert.deepEqual(sent.message.quote, { message_id: "ama-earlier" });
});

test("the composer shows how to send once, in the input, and keeps a note that says something else", async t => {
  const app = chat(t, { GetConversation: () => ({ resident: { resident_id: FACADE }, messages: [], cursor: 1,
    state: { work_state: "idle" }, controls: { can_send: true }, queue: { items: [] } }) });
  const hint = "Ctrl/⌘ + Enter 发送；右键消息可引用、转发；/portal-help 查看指令";
  const note = () => app.panel.workspace.els.composer.querySelector("[data-workspace-composer-meta]").textContent.trim();
  await app.load({ placeholder: hint, meta: hint });
  assert.equal(app.panel.workspace.els.input.dataset.placeholder, hint);
  assert.equal(note(), "");
  await app.load({ placeholder: hint, meta: "等待服务恢复…" });
  assert.equal(note(), "等待服务恢复…");
});

test("read receipts require a presented chat and resume after a hidden completion without duplicate receipts", async t => {
  const app = chat(t);
  let unread = true, revision = 1;
  const receipts = [];
  app.w.PortalConversationStore = {
    entry: () => ({ unread, completion_signature: `reply-${revision}` }),
    observeResidentRead: async args => { receipts.push(plain(args)); unread = false; return { item: { completion_signature: `reply-${revision}` } }; },
  };
  // An authoritative snapshot is the observer's input; DOM rendering and receipt
  // policy remain the production Panel and ResidentReadObserver implementation.
  app.panel.setPresented(false);
  app.panel.readObserver.update({ cursor: revision, viewer_state: { unread } });
  await app.frame();
  assert.deepEqual(receipts, []);
  app.panel.setPresented(true);
  await app.frame();
  assert.deepEqual(receipts, [{ residentId: FACADE, actor: "playwright" }]);
  assert.equal(unread, false);
  app.panel.setPresented(true);
  await app.frame();
  assert.equal(receipts.length, 1);
  app.panel.setPresented(false);
  unread = true; revision += 1;
  app.panel.readObserver.update({ cursor: revision, viewer_state: { unread } });
  await app.frame();
  assert.equal(receipts.length, 1);
  assert.equal(unread, true);
  app.panel.setPresented(true);
  await app.frame();
  assert.equal(receipts.length, 2);
  assert.equal(unread, false);
});

// The production Inbox, conversation store and Todo panel run in a DOM without
// geometry; the assistant resolution is the I/O, answered one request at a time.
function inbox(t) {
  const errors = [], pending = [];
  const log = new VirtualConsole();
  log.on("jsdomError", error => errors.push(error.message));
  log.on("error", (...args) => errors.push(args.map(String).join(" ")));
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body></body></html>', {
    url: "https://personal-assistant.fixture.invalid/settings.html", pretendToBeVisual: true, runScripts: "outside-only", virtualConsole: log,
  });
  const w = dom.window;
  let username = "playwright";
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.PortalEvents = { isConnected: () => false, connect() {} };
  w.PortalIdentity = { username: () => username, load: async () => ({ username }), userId: async () => username };
  w.PortalNav = { personalAssistantTarget: () => new Promise(resolve => pending.push(resolve)) };
  w.PortalAPI = { get: async url => {
    assert.equal(url, "/api/agent-conversations");
    return { entries: [], content_signature: "empty", ready: true, unread_count: 0 };
  } };
  w.PortalInstructionClient = { execute: async instruction => {
    assert.equal(instruction, "ListMailboxTodos");
    return { items: [], pending_count: 0 };
  } };
  for (const name of ["portal_state.js", "portal_text.js", "portal_ui_primitives.js", "portal_refresh_coordinator.js",
    "resident_protocol.js", "portal_conversation_store.js", "portal_mailbox_todos.js", "portal_agent_inbox.js"]) {
    w.eval(source(name) + `\n//# sourceURL=${name}`);
  }
  t.after(async () => {
    w.PortalAgentInbox.destroy();
    await drain(); w.close();
    assert.deepEqual(errors, [], "no production or DOM errors");
  });
  return {
    // Each call is a new page: the previous Inbox is torn down and a fresh one mounts.
    page() {
      w.PortalAgentInbox.destroy();
      w.PortalAgentInbox.mount();
      return w.document.querySelector("[data-portal-agent-inbox]");
    },
    async answer(target) { pending.shift()(target); await drain(); },
    switchUser(name) { username = name; },
  };
}

test("a new page draws the assistant next to Mailbox from the last confirmed target, then follows the server", async t => {
  const app = inbox(t);
  const target = { resident_id: FACADE, lineage_id: LINEAGE, family_id: "family_444444444444", title: "我的私人助手" };
  const trigger = root => root.querySelector("[data-personal-assistant-trigger]");
  let root = app.page();
  assert.equal(trigger(root).hidden, true, "nothing is drawn before the first confirmation");
  await app.answer(target);
  assert.equal(trigger(root).hidden, false);
  root = app.page();
  assert.equal(trigger(root).hidden, false, "drawn while the page's own request is still open");
  assert.equal(root.dataset.hasAssistant, "true");
  assert.equal(trigger(root).dataset.portalTooltip, "我的私人助手");
  await app.answer({ ...target, title: "改名后的个人空间" });
  assert.equal(trigger(root).dataset.portalTooltip, "改名后的个人空间");
  app.switchUser("someone-else");
  root = app.page();
  assert.equal(trigger(root).hidden, true, "another user of this browser never sees it");
  await app.answer(null);
  app.switchUser("playwright");
  root = app.page();
  assert.equal(trigger(root).hidden, false);
  await app.answer(null);
  assert.equal(trigger(root).hidden, true, "the server's answer replaces the remembered one");
  assert.equal(root.dataset.hasAssistant, "false");
  root = app.page();
  assert.equal(trigger(root).hidden, true);
  await app.answer(null);
});

// Workbench functions are scoped inside its page bootstrap. Execute those exact
// functions (as in resident_recommendation_state.cjs), not copies of their policy.
// Other sidebar sections are empty fixture data; actual shortcut/card rendering,
// both list renderers, async refresh and the Lineage change listener are retained.
function section(name, start, end) {
  const text = source(name), begin = text.indexOf(start), finish = text.indexOf(end, begin + start.length);
  assert.ok(begin >= 0 && finish > begin, `Production section ${start}`);
  return text.slice(begin, finish);
}

test("navigation omits the AMA shortcut, retains Which and the primary facade in both lists, and refreshes its current title", async t => {
  const { w } = chat(t);
  w.document.body.insertAdjacentHTML("beforeend", '<div id="agentList"></div><div id="favoriteAgentList"></div>');
  const state = { title: "我的私人助手", available: true, denied: false, resident_id: FACADE };
  const associations = [], navigations = [];
  w.PortalIdentity.listUIAssociations = async namespace => {
    associations.push(namespace);
    return { items: state.available ? [{ key: "primary", value: { resident_id: state.resident_id, lineage_id: LINEAGE,
      family_id: "family_444444444444", presentation: { kind: "personal-assistant" } } }] : [] };
  };
  w.PortalResidentService = { navigationTarget: async id => { navigations.push(id); return { resident_id: id, lineage_id: LINEAGE }; } };
  w.PortalInstructionClient = { execute: async (instruction, args) => {
    assert.equal(instruction, "GetLineage"); assert.equal(args.lineage_id, LINEAGE);
    if (state.denied) throw new Error("permission.denied");
    return { lineage: { title: state.title, lineage_id: LINEAGE } };
  } };
  w.eval(section("portal_nav.js", "  let personalAssistantRequest = null;", "  // Lazy loading")
    + "\nwindow.PortalNav = { personalAssistantTarget };\n");
  Object.assign(w, {
    state: { residents: [{ resident_id: AMA, title: "Ask Me Anything", lineage_id: LINEAGE }], personalAmaResidentId: AMA },
    $id: id => w.document.getElementById(id), esc: w.PortalUIPrimitives.escapeHtml,
    whichResidentCardHtml: () => "Which Resident",
    residentSelectionId: item => `resident:${item.resident_id}`,
    isPersonalAmaResident: item => item.resident_id === AMA,
    lineageFavoriteSelectionId: () => "", isFavoriteLineageSidebarItem: () => false,
    lineageIdForResident: item => item.lineage_id, lineageSidebarResidentsForItem: () => [],
    selectedResidentId: () => "", activeChatTargetId: () => "", residentGroups: () => [], favoriteResidentGroups: () => [],
    lineageStateController: { listen: (_scope, target, type, callback) => target.addEventListener(type, callback) },
    previewChangeMatches: () => false,
  });
  for (const name of ["renderProjectFilterOptions", "syncAgentTools", "renderActivityRecentRail", "bindAgentCardControls",
    "bindWhichResidentControls", "refreshRecommendationChats", "renderAgentFloatingMenu", "renderResidentGroup", "renderFavoriteResidentGroup"]) w[name] = () => {};
  w.eval("let personalAssistantRead = 0;\n"
    + section("lineage_workbench.js", "      function renderRegisteredResidentItem(", "      function ")
    + section("lineage_workbench.js", "      async function refreshPersonalAssistantShortcut(", "      // A conversation selection")
    + section("lineage_workbench.js", "      function renderFavoriteList(", "      function residentByMenuId(")
    + section("lineage_workbench.js", '      lineageStateController.listen("page", window, "portal:change",', '      lineageStateController.listen("page", window, "portal:events-ready",')
    + "\nwindow.refreshAssistantShortcut = refreshPersonalAssistantShortcut;\n");
  await w.refreshAssistantShortcut();
  const assertLists = () => {
    for (const id of ["agentList", "favoriteAgentList"]) {
      const list = w.document.getElementById(id).firstElementChild;
      assert.deepEqual([...list.children].map(node => node.className), ["whichResidentWrap", "personalAssistantResident"]);
      const assistant = list.querySelector(".personalAssistantResident");
      assert.equal(assistant.querySelector("[data-resident-select]").dataset.residentSelect, FACADE);
      assert.equal(assistant.querySelector(".agentTitle").textContent, state.title);
      assert.equal(list.querySelector(`[data-resident-select="${AMA}"]`), null);
    }
  };
  assertLists();
  assert.equal(w.state.personalAssistant.family_id, "family_444444444444");
  assert.deepEqual(associations, ["lineage-workbench.personal-assistant"]);
  assert.deepEqual(navigations, [FACADE]);
  state.title = "改名后的个人空间";
  w.dispatchEvent(new w.CustomEvent("portal:change", { detail: { changed: { lineage: [LINEAGE] } } }));
  await drain(); assertLists();
  for (const unavailable of [{ available: false }, { available: true, denied: true }, { denied: false, resident_id: "resident_invalid" }]) {
    Object.assign(state, unavailable);
    await w.refreshAssistantShortcut();
    assert.equal(w.document.querySelectorAll(".personalAssistantResident").length, 0);
    for (const id of ["agentList", "favoriteAgentList"]) {
      const list = w.document.getElementById(id);
      assert.equal(list.querySelector(`[data-resident-select="${AMA}"]`), null);
      assert.ok(list.querySelector(".whichResidentWrap"));
    }
  }
});


test("a pending host draft moves into the Resident and never returns after send", async t => {
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], cursor: 1,
      state: { work_state: "idle" }, controls: { can_send: true }, queue: { items: [] } }),
    DeliverMessage: args => ({ accepted: true, resident_id: args.resident_id, message_id: args.message.message_id, input_state: { state: "queued" } }),
  }, { target: {}, pendingHistoryScope: "wall:project:point", resolveBeforeSend: async () => ({ resident_id: FACADE, lineage_id: LINEAGE }) });
  app.w.PortalResidentProjection = { load: async () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE },
    chat_workspace: { schema: "portal.chat_workspace.v3", target: { kind: "resident", id: FACADE, lineage_id: LINEAGE },
      messages: [], composer: { can_send: true, can_edit: true } } }) };
  app.panel.showState({ schema: "portal.chat_workspace.v3", messages: [], composer: { can_send: true, can_edit: true } });
  app.panel.workspace.setDraftText("send once");
  app.panel.workspace.saveDraft({ immediate: true });
  const oldScope = app.panel.workspace.draftScope();
  assert.equal(oldScope, "wall:project:point");
  await app.panel.send();
  assert.equal(app.requests.filter(item => item.instruction === "DeliverMessage").length, 1);
  assert.equal(app.panel.workspace.draftText(), "");
  app.panel.workspace.restoreDraft(oldScope);
  assert.equal(app.panel.workspace.draftText(), "", "reopening the card cannot revive a sent draft");
  app.panel.workspace.setDraftText("new unsent input");
  app.panel.workspace.saveDraft({ immediate: true });
  assert.equal(app.panel.workspace.clearDraft(oldScope, { text: "send once" }), false);
  app.panel.workspace.restoreDraft(oldScope);
  assert.equal(app.panel.workspace.draftText(), "new unsent input", "late migration must preserve a newer draft");
});


test("Stop after sending targets the pending input, not a stale active snapshot", async t => {
  let accept;
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], cursor: 1,
      state: { work_state: "idle", active_message_id: "msg_previous" }, controls: { can_send: true, can_stop: true }, queue: { items: [] } }),
    DeliverMessage: args => new Promise(resolve => { accept = () => resolve({ accepted: true, resident_id: args.resident_id,
      message_id: args.message.message_id, input_state: { state: "queued" } }); }),
    CancelInput: args => ({ message_id: args.message_id, input_state: { state: "cancelled" } }),
  });
  await app.load();
  app.panel.workspace.setDraftText("new turn");
  const sending = app.panel.send();
  for (let i = 0; i < 10 && !accept; ++i) await drain();
  assert.ok(accept, "new input reached its owner");
  const newId = app.requests.find(r => r.instruction === "DeliverMessage").args.message.message_id;
  const stopping = app.panel.stop();
  assert.equal(app.requests.filter(r => r.instruction === "CancelInput").length, 0, "wait for the exact send acknowledgement");
  accept();
  await sending; await stopping;
  assert.equal(app.requests.find(r => r.instruction === "CancelInput").args.message_id, newId);
});

function inputEvent(app, cursor, type, payload) {
  if (type === 'resident.state.changed') payload = { ...payload,
    controls: { disabled_reason: '', send_hint: '', ...payload.controls } };
  app.w.dispatchEvent(new app.w.CustomEvent('portal:change', { detail: {
    resident_events: [{ resident_id: FACADE, cursor, event_type: type, payload }],
  } }));
}

test('Stop survives a delivery-only receipt until the exact input has a control projection', async t => {
  let cancel;
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], input_states: [], cursor: 1,
      state: { work_state: 'idle', active_message_id: '' }, controls: { can_send: true, can_stop: false }, queue: { items: [] } }),
    // DeliverMessage can accept before the execution owner creates input_state.
    DeliverMessage: args => ({ accepted: true, resident_id: args.resident_id, message_id: args.message.message_id, input_state: {} }),
    CancelInput: args => new Promise(resolve => { cancel = () => resolve({ resident_id: FACADE, cursor: 3,
      message_id: args.message_id, input_state: { message_id: args.message_id, state: 'cancelled' } }); }),
  });
  await app.load();
  await app.panel.send({ text: 'send then immediately stop', attachments: [], refs: [] });
  const id = app.requests.find(r => r.instruction === 'DeliverMessage').args.message.message_id;
  assert.equal(app.panel.workspace.state.composer.can_stop, true, 'delivery acknowledgement must not leave a Stop gap');
  assert.equal(app.panel.snapshot().state.work_state, 'idle', 'local action must not invent a server work state');
  const stopping = app.panel.stop();
  await drain();
  assert.equal(app.requests.find(r => r.instruction === 'CancelInput').args.message_id, id);
  const button = app.panel.workspace.els.stop;
  assert.equal(button.disabled, true);
  assert.match(button.getAttribute('aria-label'), /停止|Stopping/);
  assert.equal(button.getAttribute('aria-label'), button.title, 'accessible name exposes the current pending action');
  inputEvent(app, 2, 'resident.state.changed', { state: { work_state: 'idle', active_message_id: '' }, controls: { can_send: true, can_stop: false } });
  assert.equal(app.panel.workspace.state.composer.can_stop, true, 'an unrelated newer cursor cannot finish this cancellation');
  assert.equal(app.panel.workspace.els.stop, button, 'pending cancellation retains its disabled control');
  assert.equal(button.isConnected, true);
  assert.equal(button.disabled, true);
  cancel(); await stopping;
  assert.equal(app.panel.workspace.state.composer.can_stop, false);
  assert.equal(app.panel.pendingTurnSubmissions.size, 0);
});

for (const outcome of ['running', 'queued', 'completed']) {
  test(`local Stop hands over to the exact ${outcome} input projection`, async t => {
    const app = chat(t, {
      GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], input_states: [], cursor: 1,
        state: { work_state: 'idle' }, controls: { can_send: true, can_stop: false }, queue: { items: [] } }),
      DeliverMessage: args => ({ accepted: true, resident_id: FACADE, message_id: args.message.message_id, input_state: {} }),
    });
    await app.load();
    await app.panel.send({ text: 'handover', attachments: [], refs: [] });
    const id = app.requests.find(r => r.instruction === 'DeliverMessage').args.message.message_id;
    if (outcome === 'running') inputEvent(app, 2, 'resident.state.changed', {
      state: { work_state: 'running', active_message_id: id }, controls: { can_send: true, can_stop: true } });
    else if (outcome === 'queued') inputEvent(app, 2, 'resident.queue.changed', { queue: { items: [{ message_id: id }] } });
    else inputEvent(app, 2, 'resident.input.state.changed', { input_state: { message_id: id, state: 'completed' } });
    assert.equal(app.panel.pendingTurnSubmissions.size, 0, 'authoritative controls/queue/terminal own the action now');
    assert.equal(app.panel.workspace.state.composer.can_stop, outcome === 'running');
  });
}

test('Stop control stays mounted while a delivery receipt awaits its exact input projection', async t => {
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], input_states: [], cursor: 1,
      state: { work_state: 'idle', active_message_id: '' }, controls: { can_send: true, can_stop: false }, queue: { items: [] } }),
    DeliverMessage: args => ({ accepted: true, resident_id: FACADE, message_id: args.message.message_id, input_state: {} }),
    CancelInput: args => ({ resident_id: FACADE, message_id: args.message_id,
      input_state: { message_id: args.message_id, state: 'cancelled' } }),
  });
  await app.load();
  await app.panel.send({ text: 'keep this stop target', attachments: [], refs: [] });
  const id = app.requests.find(r => r.instruction === 'DeliverMessage').args.message.message_id;
  const button = app.panel.workspace.els.stop;
  button.focus();
  for (let cursor = 2; cursor < 5; cursor++) {
    inputEvent(app, cursor, 'resident.state.changed', { state: { work_state: 'idle', active_message_id: '' },
      controls: { can_send: true, can_stop: false } });
    assert.equal(app.panel.workspace.els.stop, button, 'unrelated snapshot must not replace an actionable Stop');
    assert.equal(button.isConnected, true);
    assert.equal(app.w.document.activeElement, button);
    assert.equal(button.disabled, false);
  }
  app.panel.refreshView();
  assert.equal(app.panel.workspace.els.stop, button, 'refreshing the view keeps the accepted input control');
  assert.equal(app.w.document.activeElement, button);
  inputEvent(app, 5, 'resident.state.changed', { state: { work_state: 'running', active_message_id: id },
    controls: { can_send: true, can_stop: true } });
  assert.equal(app.panel.workspace.els.stop, button, 'authoritative handoff keeps the same control');
  button.click();
  await drain();
  assert.equal(app.requests.filter(r => r.instruction === 'CancelInput').length, 1);
  assert.equal(app.requests.find(r => r.instruction === 'CancelInput').args.message_id, id);
});

test('a rejected submission cannot leave a local Stop action', async t => {
  const app = chat(t, {
    GetConversation: () => ({ resident: { resident_id: FACADE, lineage_id: LINEAGE }, messages: [], cursor: 1,
      state: { work_state: 'idle' }, controls: { can_send: true, can_stop: false }, queue: { items: [] } }),
    DeliverMessage: () => { throw Object.assign(new Error('denied'), { status: 403 }); },
  });
  await app.load();
  await assert.rejects(app.panel.send({ text: 'denied input', attachments: [], refs: [] }), /denied/);
  assert.equal(app.panel.workspace.state.composer.can_stop, false);
  assert.equal(app.panel.pendingTurnSubmissions.size, 0);
});

test('failed cancellation can retry the same accepted input before the stream catches up', async t => {
  let attempts = 0;
  const app = chat(t, {
    GetConversation: args => ({ resident: { resident_id: args.resident_id, lineage_id: LINEAGE }, messages: [], input_states: [], cursor: 1,
      state: { work_state: 'idle' }, controls: { can_send: true, can_stop: false }, queue: { items: [] } }),
    DeliverMessage: args => ({ accepted: true, resident_id: FACADE, message_id: args.message.message_id, input_state: {} }),
    CancelInput: args => {
      if (++attempts === 1) throw new Error('transport unavailable');
      return { resident_id: FACADE, message_id: args.message_id, input_state: { message_id: args.message_id, state: 'cancelled' } };
    },
  });
  await app.load();
  await app.panel.send({ text: 'cancel once', attachments: [], refs: [] });
  const id = app.requests.find(r => r.instruction === 'DeliverMessage').args.message.message_id;
  await assert.rejects(app.panel.stop(), /transport unavailable/);
  assert.equal(app.panel.workspace.state.composer.can_stop, true);
  assert.equal(app.panel.workspace.els.stop.disabled, false);
  await app.panel.stop();
  assert.deepEqual(app.requests.filter(r => r.instruction === 'CancelInput').map(r => r.args.message_id), [id, id]);
  assert.equal(app.panel.pendingTurnSubmissions.size, 0);
  assert.equal(app.requests.filter(r => r.instruction === 'DeliverMessage').length, 1, 'retry cancellation never replays input');
});
