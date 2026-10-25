'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section><section id="family"></section>', {
  url: 'https://chat.fixture.invalid/lineage', pretendToBeVisual: true, runScripts: 'outside-only',
});
const w = dom.window;
const observers = new Set();
const NativeMutationObserver = w.MutationObserver;
w.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) { super(callback); observers.add(this); }
};
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'viewer' };
w.eval(input.routeScript);
for (const name of [
  'portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_preview_contract.js', 'portal_file_links.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', 'portal_chat_workspace.js',
]) w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
const root = w.document.getElementById('fixture');
const family = w.document.getElementById('family');
const workspace = w.PortalChatWorkspace.createWorkspace({
  root, id: 'chat', historyScope: () => 'activity-summary', refs: false, can_attach: false,
  assistantName: 'Codex',
});
const event = (title, status, content = '') => Object.freeze({
  kind: 'tool_call', title, status, content, created_at: '2026-10-25T13:38:00Z',
});
const disclosure = () => root.querySelector('.messageRuntimeDetails');
const summary = () => disclosure().querySelector('summary').textContent;
const assertLatest = (events, title, status) => {
  workspace.setRuntime(events, true);
  const lastRow = disclosure().querySelector('.runtimeDetailItem:last-child');
  assert.equal(lastRow.querySelector('.runtimeDetailKind').textContent, title);
  assert.ok(lastRow.querySelector('.runtimeDetailMeta').textContent.includes(status));
  assert.ok(summary().includes(title), `summary must follow last row: ${summary()}`);
  assert.ok(summary().includes(status), `summary must show last event status: ${summary()}`);
  family.innerHTML = w.PortalChatWorkspace.requestProgressHtml({
    state: 'running', resident_id: 'resident_111111111111', title: 'Codex', activities: events,
  });
  assert.equal(family.querySelector('details summary').textContent, summary(),
    'Family and ordinary conversation must use the same activity summary');
};
(async () => {
  try {
    workspace.setMessages(w.PortalChatWorkspace.projectResidentConversation({}, {
      messages: [{ message_id: 'request', role: 'user', author_id: 'viewer',
        content_parts: [{ type: 'text', text: 'Inspect the file' }] }],
    }).messages);
    const started = event('Command started', 'running', '$ rg -n example file.py');
    workspace.setRuntime([started], true);
    assert.ok(summary().includes('Command started'));
    assert.equal(disclosure().open, false);
    disclosure().open = true;
    const finished = event('Command finished', 'completed', '$ rg -n example file.py exit 0');
    assertLatest([started, finished], 'Command finished', 'completed');
    assert.equal(disclosure().open, true, 'event updates preserve an expanded activity');
    assert.ok(!summary().includes('Command started'));

    disclosure().open = false;
    const thinking = event('Thinking', 'completed');
    assertLatest([started, finished, thinking], 'Thinking', 'completed');
    assert.equal(disclosure().open, false, 'event updates preserve a collapsed activity');
    assert.ok(!summary().includes('$ rg'), 'old command text must not remain in the heading');

    for (const status of ['failed', 'cancelled']) {
      assertLatest([started, event('Command ended', status)], 'Command ended', status);
    }
    const next = event('Next command', 'in_progress', '$ pwd');
    assertLatest([started, finished, thinking, next], 'Next command', 'running');
    assertLatest([event('Thinking', 'completed')], 'Thinking', 'completed');

    const unsafeTitle = '<img src=x onerror=alert(1)>';
    const unsafeContent = '<script>bad()</script>\nsecond line';
    assertLatest([started, event(unsafeTitle, 'completed', unsafeContent)], unsafeTitle, 'completed');
    assert.ok(summary().includes('<script>bad()</script>'));
    assert.ok(!summary().includes('second line'));
    assert.equal(disclosure().querySelector('img,script'), null);
    assert.equal(family.querySelector('img,script'), null);

    workspace.setRuntime([started, finished], false);
    assert.match(summary(), /活动 · 2/);
    workspace.setRuntime([], true);
    assert.equal(disclosure(), null);
    workspace.setRuntime([], false);
    assert.equal(root.querySelector('.liveRuntime'), null);
    console.log('PASS latest activity summary');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    workspace.destroy();
    for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve));
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
