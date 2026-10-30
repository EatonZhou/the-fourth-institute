'use strict';
// The composer's Refs popover: it loads the Skills and Agents catalog when it
// first opens and offers a retry when that fails; search narrows the options;
// what a person ticks shows on the Refs button and in the summary and is sent
// with the message; Clear empties the selection and Done closes the popover.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section>', {
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
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const CATALOG = {
  skills: [{ id: 'shared/plan', name: 'Plan' }, { id: 'third-party/lint', name: 'Lint', description: 'style checks' }],
  agents: [{ id: 'agent-a', name: 'Agent A' }],
};

(async () => {
  const requests = [];
  w.PortalAPI = {
    get: async (url) => {
      requests.push(url);
      if (requests.length === 1) throw new Error('catalog unavailable');
      return CATALOG;
    },
  };
  const sent = [];
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById('fixture'), id: 'chat', historyScope: () => 'conversation-refs',
    onSend: payload => sent.push(payload),
    contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
  });
  const $ = selector => w.document.querySelector(selector);
  const toggle = $('#fixture [data-refs-toggle]');
  const panel = () => $('[data-refs-panel]');
  const options = () => [...panel().querySelectorAll('[data-portal-chat-ref]')].map(box => `${box.dataset.portalChatRef}:${box.value}`);
  const summary = () => [...$('#fixture [data-refs-summary]').querySelectorAll('.refsChip')].map(chip => chip.textContent);
  try {
    workspace.setMessages([], { history: { can_load_older: false } });
    await settle();
    assert.equal(toggle.textContent.trim(), 'Refs');
    assert.deepEqual(requests, [], 'the catalog waits until the popover opens');

    toggle.click();
    await settle();
    assert.equal(panel().getAttribute('aria-hidden'), 'false', 'the Refs button opens the popover');
    assert.deepEqual(requests, ['/api/agent-context-options']);
    assert.match(panel().querySelector('[role="alert"]')?.textContent || '', /加载失败/, 'a failed catalog says so');
    panel().querySelector('[data-refs-retry]').click();
    await settle();
    assert.equal(requests.length, 2, 'retry reads the catalog again');
    assert.deepEqual(options(), ['skills:third-party/lint', 'skills:shared/plan', 'agents:agent-a'],
      'third-party Skills, then shared Skills, then Agents');

    const search = panel().querySelector('[data-refs-search]');
    search.value = 'STYLE';
    search.dispatchEvent(new w.Event('input', { bubbles: true }));
    assert.deepEqual(options(), ['skills:third-party/lint'], 'search matches names, ids, paths and descriptions');
    search.value = '';
    search.dispatchEvent(new w.Event('input', { bubbles: true }));

    const plan = panel().querySelector('[data-portal-chat-ref="skills"][value="shared/plan"]');
    plan.checked = true;
    plan.dispatchEvent(new w.Event('change', { bubbles: true }));
    const agent = panel().querySelector('[data-portal-chat-ref="agents"][value="agent-a"]');
    agent.checked = true;
    agent.dispatchEvent(new w.Event('change', { bubbles: true }));
    await settle();
    assert.equal(toggle.textContent.trim(), 'Refs 2', 'the Refs button counts the selection');
    assert.deepEqual(summary(), ['技能: Plan', 'Agent: Agent A'], 'the summary names what was picked');

    panel().querySelector('[data-refs-done]').click();
    await settle();
    assert.equal(panel().getAttribute('aria-hidden'), 'true', 'Done closes the popover');
    toggle.click();
    await settle();
    assert.equal(requests.length, 2, 'a loaded catalog is not read again');
    assert.equal(panel().querySelector('[value="shared/plan"]').checked, true, 'reopening keeps the ticks');

    workspace.setDraftText('use these');
    await settle();
    w.document.querySelector('#fixture .composerInput')
      .dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }));
    await settle();
    assert.equal(sent.length, 1);
    assert.deepEqual([...sent[0].refs.skills], ['shared/plan'], 'the message carries the picked Skill');
    assert.deepEqual([...sent[0].refs.agents], ['agent-a'], 'and the picked Agent');

    panel().querySelector('[data-refs-clear]').click();
    await settle();
    assert.equal(toggle.textContent.trim(), 'Refs', 'Clear empties the count');
    assert.deepEqual(summary(), [], 'and the summary');
    assert.equal(workspace.payload().refs.skills.size + workspace.payload().refs.agents.size, 0, 'and what the next message carries');
    console.log('PASS the Refs popover loads, retries, searches, counts, sends and clears the picked references');
  } finally {
    workspace.destroy();
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
