"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { JSDOM, VirtualConsole } = require("jsdom");

const repo = process.env.TAU_CODE_ROOT || path.resolve(__dirname, "../../..");
const source = name => fs.readFileSync(name === "portal_chat_workspace.js" && process.env.TAU_CHAT_SOURCE ? process.env.TAU_CHAT_SOURCE : path.join(repo, "management_portal/static", name), "utf8");
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
    if (instruction === "ListInputHistory") return { messages: [], next_page_token: "" };
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

for (const unavailable of [{status:404}, {status:403}, {code:'resident.deleted'}]) {
  test('unavailable binding does not reload input history while rendering: '+JSON.stringify(unavailable), async t => {
    const app = chat(t, { GetConversation: args => ({resident:{resident_id:args.resident_id},
      messages:[{message_id:'msg_original',role:'user',author_id:'playwright',content_parts:[{type:'text',text:'original input'}]}],
      cursor:1,state:{work_state:'idle'},controls:{can_send:true},queue:{items:[]}}) });
    await app.load(); await drain(); await drain();
    assert.match(app.root.textContent,/original input/);
    const history = () => app.requests.filter(r => r.instruction === 'ListInputHistory');
    const before = history().length; assert.ok(before > 0, 'native Chat loaded history');
    assert.equal(app.panel.rejectUnavailableTarget(Object.assign(Error('target unavailable'), unavailable)),true);
    await drain(); await drain();
    assert.match(app.root.textContent,/Resident/);
    assert.equal(app.panel.target().resident_id,FACADE,'unavailable Chat keeps its identity');
    assert.equal(app.panel.protocol,null);
    assert.equal(history().length,before,'rendering the unavailable state must not send a new history request');
    assert.equal(await app.panel.loadInputHistory(app.panel.workspace.inputHistoryScope()),null);
    assert.equal(history().length,before,'explicit history callers also respect the unavailable binding');
    // A deliberate same-Resident reload remains possible after permission recovery.
    await app.load(); await drain();
    const reloaded = history().length;
    await app.panel.loadInputHistory(app.panel.workspace.inputHistoryScope());
    assert.equal(history().length,reloaded+1,'explicit reload has a new binding revision');
    // A different target must not inherit the unavailable state.
    const next = {resident_id:AMA,lineage_id:LINEAGE};
    await app.panel.setResident(next,{detail:{resident:next,chat_workspace:{schema:'portal.chat_workspace.v3',
      target:{kind:'resident',id:AMA,lineage_id:LINEAGE},messages:[],composer:{can_send:true}}}});
    await drain();
    const nextCount=history().length;
    await app.panel.loadInputHistory(app.panel.workspace.inputHistoryScope());
    assert.equal(history().length,nextCount+1);
    assert.equal(history().at(-1).args.resident_id,AMA);
  });
}
