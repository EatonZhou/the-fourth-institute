'use strict';
// The Agent Harness panel of the Resident Menu: the settings merged over the
// server defaults, the switches and timings by what the backend supports and
// by the harness mode, each change saved as the Resident's own settings, and
// the Prompt 注入 page with its presets, categories and preview.
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
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);

const defaults = {
  inline_media_history: { enabled: true, trigger_inline_media_bytes: 2097152, keep_recent_images: 4 },
  tool_call_granularity: { enabled: false },
  tool_output_offloading: { enabled: true, offload_trigger_bytes: 65536, timing: 'both' },
  artifact_search_and_slice: { enabled: false },
  hash_aware_read_dedup: { enabled: false, min_output_tokens: 400 },
  unified_resource_recall: { enabled: false },
  early_context_compaction: { enabled: false },
  native_tool_loop_governor: { enabled: false, checkpoint_interval: 12, max_checkpoints: 3 },
  provider_error_continue: { enabled: false },
};
const capabilities = {
  harness_features: ['inline_media_history', 'tool_output_offloading', 'provider_error_continue', 'early_context_compaction'],
  harness_capabilities: ['subagents'],
};

function mergedSettings() {
  const chat = w.PortalChatWorkspace;
  assert.deepEqual(plain(chat.residentAgentHarnessSettings({}, {})), { mode: 'auto', features: {} });
  const merged = plain(chat.residentAgentHarnessSettings({
    agent_harness: { mode: ' Manual ', features: { tool_output_offloading: { enabled: false, timing: 'before_turn' }, extra_feature: { enabled: true } } },
  }, { inline_media_history: { enabled: true, keep_recent_images: 4 }, tool_output_offloading: { enabled: true, offload_trigger_bytes: 1024 }, early_context_compaction: { enabled: 'yes' } }));
  assert.deepEqual(merged, {
    mode: 'manual',
    features: {
      inline_media_history: { enabled: true, keep_recent_images: 4 },
      tool_output_offloading: { enabled: false, offload_trigger_bytes: 1024, timing: 'before_turn' },
      early_context_compaction: { enabled: false },
      extra_feature: { enabled: true },
    },
  }, 'a saved choice overrides the server default; only a true default turns a feature on');
}

function menuByModeAndSupport() {
  const chat = w.PortalChatWorkspace;
  for (const options of [{}, { harnessDefaults: {} }]) {
    assert.equal(text(new w.DOMParser().parseFromString(chat.residentHarnessMenuHtml({}, options), 'text/html').body), '正在读取扩展配置...',
      'no server defaults yet, no switches');
  }
  const menu = (settings, options) => {
    const host = w.document.createElement('div');
    host.innerHTML = chat.residentHarnessMenuHtml(settings, { harnessDefaults: defaults, runtimeCapabilities: capabilities, ...options });
    return host;
  };
  const toggle = (host, id) => host.querySelector(`[data-agent-harness-feature-toggle="${id}"]`);

  const automatic = menu({ prompt_injection: { preset: 'minimal' }, allow_subagents: true });
  assert.equal(text(automatic.querySelector('[data-prompt-injection-open] strong')), 'Prompt 注入');
  assert.match(text(automatic.querySelector('[data-prompt-injection-open] small')), /^精简 · /, 'the preset is named');
  assert.equal(automatic.querySelector('[data-sub-agent-toggle]').checked, true);
  assert.equal(automatic.querySelector('[data-agent-harness-mode-toggle]').checked, true, 'no saved mode is automatic');
  assert.equal(toggle(automatic, 'tool_output_offloading').disabled, true, 'the automatic mode chooses the features itself');
  assert.equal(toggle(automatic, 'tool_output_offloading').checked, true, 'and shows the default');
  assert.equal(toggle(automatic, 'provider_error_continue').disabled, false, 'continuing after an error stays the reader’s own switch');
  assert.equal(automatic.querySelector('[data-agent-harness-feature-timing="tool_output_offloading"]').disabled, true);

  const manual = menu({ agent_harness: { mode: 'manual', features: { tool_output_offloading: { timing: 'after_tool_batch' } } } });
  assert.equal(manual.querySelector('[data-agent-harness-mode-toggle]').checked, false);
  assert.equal(toggle(manual, 'tool_output_offloading').disabled, false);
  assert.equal(toggle(manual, 'inline_media_history').disabled, false);
  assert.equal(toggle(manual, 'tool_call_granularity').disabled, false, 'a Prompt-only feature needs no backend capability');
  for (const id of ['artifact_search_and_slice', 'hash_aware_read_dedup', 'unified_resource_recall', 'native_tool_loop_governor']) {
    assert.equal(toggle(manual, id).disabled, true, `${id} is off when the backend cannot run it`);
    assert.equal(text(toggle(manual, id).closest('label').querySelector('small')), '当前后端不具备这个组件需要的能力');
    assert.match(toggle(manual, id).dataset.portalTooltip, /当前后端不具备这个组件需要的能力，因此开关不可用。$/);
  }
  const offloading = toggle(manual, 'tool_output_offloading').closest('label');
  assert.equal(text(offloading.querySelector('small')), '单条超过 64 KiB 时，仅保留预览与可召回的精确原文');
  assert.match(toggle(manual, 'tool_output_offloading').dataset.portalTooltip, /^当单条工具输出超过 64 KiB 时/);
  assert.equal(text(toggle(manual, 'inline_media_history').closest('label').querySelector('small')),
    '超过 2048 KiB 时仅保留最新 4 张，旧图片退出活跃上下文');
  const timing = manual.querySelector('[data-agent-harness-feature-timing="tool_output_offloading"]');
  assert.equal(timing.value, 'after_tool_batch', 'the saved timing is selected');
  assert.equal(timing.disabled, false);
  const compaction = manual.querySelector('[data-agent-harness-feature-timing="early_context_compaction"]');
  assert.deepEqual([...compaction.options].map(option => option.value), ['after_tool_batch', 'before_turn', 'both']);
  assert.equal(compaction.value, 'after_tool_batch', 'compaction defaults to the agent loop');
  assert.equal(text(manual.querySelector('[data-agent-harness-timing-display="tool_call_granularity"] output')), '发送前 · Prompt 策略');

  const noSubagents = menu({}, { runtimeCapabilities: { harness_features: [], harness_capabilities: [] }, busy: true });
  assert.equal(noSubagents.querySelector('[data-sub-agent-toggle]'), null, 'a backend without subagents has no switch');
  assert.equal(noSubagents.querySelector('[data-prompt-injection-open]').disabled, true, 'nothing can be changed while saving');
  assert.equal(noSubagents.querySelector('[data-agent-harness-mode-toggle]').disabled, true);
  assert.equal(text(noSubagents.querySelector('[data-prompt-injection-open] small')).split(' · ')[0], '标准', 'no preset is the standard one');
}

async function panelSavesSettings() {
  const chat = w.PortalChatWorkspace;
  assert.throws(() => chat.mountResidentHarnessPanel({ residentId: 'resident_sample' }), /container and residentId are required/);
  const container = w.document.createElement('div');
  w.document.body.append(container);
  const loads = [];
  const executed = [];
  const invalidated = [];
  let changed = 0;
  let settings = { agent_harness: { mode: 'manual', features: { tool_output_offloading: { timing: 'after_tool_batch' } } }, allow_subagents: false };
  let failLoad = true;
  w.PortalResidentProjection = {
    load: async (residentId, options) => {
      loads.push([residentId, { ...options }]);
      if (failLoad) throw new Error('projection offline');
      return { runtime_settings: settings, agent_harness_defaults: defaults, backend_capabilities: { runtime_capabilities: capabilities } };
    },
    invalidate: residentId => invalidated.push(residentId),
  };
  let saving = null;
  w.PortalInstructionClient = {
    execute: async (instruction, args, options) => {
      executed.push([instruction, plain(args), options ? plain(options) : undefined]);
      if (saving) await saving.promise;
      return {};
    },
  };
  const panel = chat.mountResidentHarnessPanel({ container, residentId: 'resident_sample', onChanged: () => { changed += 1; } });
  try {
    assert.equal(text(container), '正在读取 Harness 设置...');
    await settle();
    assert.equal(text(container), 'Harness 设置读取失败：projection offline');
    failLoad = false;
    await panel.reload();
    assert.deepEqual(loads.map(([, options]) => options.force), [false, true], 'reload reads past the cache');
    const toggle = id => container.querySelector(`[data-agent-harness-feature-toggle="${id}"]`);
    assert.equal(toggle('tool_output_offloading').checked, true);

    saving = deferred();
    toggle('tool_output_offloading').checked = false;
    toggle('tool_output_offloading').dispatchEvent(new w.Event('change'));
    assert.equal(toggle('tool_output_offloading').disabled, true, 'the panel is locked while saving');
    assert.equal(container.querySelector('[data-agent-harness-mode-toggle]').disabled, true);
    container.querySelector('[data-agent-harness-mode-toggle]').dispatchEvent(new w.Event('change'));
    await settle();
    assert.equal(executed.length, 1, 'a change while saving is not sent');
    const expectedFeatures = Object.fromEntries(Object.entries(defaults).map(([id, value]) => [id, { ...value }]));
    expectedFeatures.tool_output_offloading = { enabled: false, offload_trigger_bytes: 65536, timing: 'after_tool_batch' };
    assert.deepEqual(executed[0], ['UpdateResidentSettings', {
      resident_id: 'resident_sample', changes: { agent_harness: { mode: 'manual', features: expectedFeatures } },
    }, undefined], 'a switch saves the whole harness with the one feature changed');
    saving.resolve();
    saving = null;
    await settle();
    assert.deepEqual(invalidated, ['resident_sample'], 'a save drops the cached projection');
    assert.equal(changed, 1, 'and tells the caller');
    assert.deepEqual(loads.at(-1)[1], { force: true });
    assert.equal(toggle('tool_output_offloading').disabled, false, 'the panel unlocks after the save');

    const timing = container.querySelector('[data-agent-harness-feature-timing="tool_output_offloading"]');
    timing.value = 'before_turn';
    timing.dispatchEvent(new w.Event('change'));
    await settle();
    assert.equal(executed.at(-1)[1].changes.agent_harness.features.tool_output_offloading.timing, 'before_turn');
    const mode = container.querySelector('[data-agent-harness-mode-toggle]');
    mode.checked = true;
    mode.dispatchEvent(new w.Event('change'));
    await settle();
    assert.equal(executed.at(-1)[1].changes.agent_harness.mode, 'auto');
    const subagents = container.querySelector('[data-sub-agent-toggle]');
    subagents.checked = true;
    subagents.dispatchEvent(new w.Event('change'));
    await settle();
    assert.deepEqual(executed.at(-1)[1], { resident_id: 'resident_sample', changes: { allow_subagents: true } });

    w.PortalInstructionClient.execute = async () => { throw new Error('not allowed'); };
    toggle('inline_media_history').dispatchEvent(new w.Event('change'));
    await settle();
    assert.deepEqual(toasts.at(-1), ['not allowed', 'error'], 'a refused save is reported');
    assert.equal(toggle('inline_media_history').disabled, false, 'and the panel unlocks');
  } finally {
    panel.destroy();
  }
  assert.equal(container.childElementCount, 0, 'destroy empties the panel');
  settings = {};
  await panel.reload();
  assert.equal(container.childElementCount, 0, 'a destroyed panel draws nothing');
  container.remove();
}

async function promptInjectionPage() {
  const chat = w.PortalChatWorkspace;
  const container = w.document.createElement('div');
  w.document.body.append(container);
  const executed = [];
  const previews = {};
  const view = preset => ({
    preset,
    presets: [{ id: 'standard', label: '标准' }, { id: 'minimal', label: '精简' }, { id: 'pure', label: '纯 TUI' }, { id: 'custom', label: '自定义' }],
    parts: [{
      id: 'rules', label: 'Portal 规则', description: '共用规则与角色规则', placement: '系统位置',
      choice: 'first', choices: [{ id: 'first', label: '第一轮' }, { id: 'every', label: '每轮' }, { id: 'never', label: '不注入' }],
    }],
  });
  let settingsView = view('standard');
  w.PortalResidentProjection = {
    load: async () => ({ runtime_settings: { prompt_injection: { preset: settingsView.preset } }, agent_harness_defaults: defaults, backend_capabilities: { runtime_capabilities: capabilities } }),
    invalidate() {},
  };
  w.PortalInstructionClient = {
    execute: async (instruction, args, options) => {
      executed.push([instruction, plain(args), options ? plain(options) : undefined]);
      if (instruction === 'GetResidentSettings') return { prompt_injection: settingsView };
      if (instruction === 'PreviewPromptInjection') {
        previews[args.turn] = deferred();
        return previews[args.turn].promise;
      }
      if (instruction === 'UpdateResidentSettings') {
        settingsView = { ...view(args.changes.prompt_injection.preset || 'custom') };
        return { prompt_injection: settingsView };
      }
      return {};
    },
  };
  const panel = chat.mountResidentHarnessPanel({ container, residentId: 'resident_sample' });
  try {
    await settle();
    container.querySelector('[data-prompt-injection-open]').click();
    await settle();
    assert.deepEqual(executed.slice(0, 2).map(([name, args]) => [name, args]), [
      ['GetResidentSettings', { resident_id: 'resident_sample' }],
      ['PreviewPromptInjection', { resident_id: 'resident_sample', turn: 'first' }],
    ], 'the page reads the settings, then previews the first turn');
    const preset = () => container.querySelector('[data-prompt-injection-preset]');
    assert.deepEqual([...preset().options].map(option => option.value), ['standard', 'minimal', 'pure'],
      'custom is offered only when it is the current preset');
    assert.equal(preset().value, 'standard');
    const part = container.querySelector('[data-prompt-injection-part="rules"]');
    assert.equal(part.value, 'first');
    assert.equal(text(part.closest('label').querySelector('.chatPromptInjectionPlace')), '位置：系统位置');
    assert.equal(text(container.querySelector('[data-prompt-injection-preview]')), '正在生成预览...');

    previews.first.resolve({ system_prompt: 'RULES', prompt: 'HELLO', parts: [{ label: 'Portal 规则', sent: true, chars: 5 }, { label: '身份', sent: false, chars: 0 }] });
    await settle();
    assert.deepEqual([...container.querySelectorAll('.chatPromptInjectionParts li')].map(text), ['发送 · Portal 规则 · 5 字', '不发送 · 身份 · 0 字']);
    assert.equal(container.querySelector('[data-prompt-injection-preview]').textContent, '【系统位置】\nRULES\n\n【本轮消息】\nHELLO');

    const turn = container.querySelector('[data-prompt-injection-preview-turn]');
    turn.value = 'later';
    turn.dispatchEvent(new w.Event('change'));
    const again = container.querySelector('[data-prompt-injection-preview-turn]');
    assert.equal(again.value, 'later');
    again.value = 'after_compaction';
    again.dispatchEvent(new w.Event('change'));
    previews.after_compaction.reject(new Error('no session'));
    await settle();
    previews.later.resolve({ system_prompt: 'STALE', prompt: '', parts: [] });
    await settle();
    assert.equal(text(container.querySelector('[data-prompt-injection-preview]')), '预览失败：no session',
      'a preview for a turn no longer chosen is dropped');

    preset().value = 'minimal';
    preset().dispatchEvent(new w.Event('change'));
    await settle();
    const update = executed.find(([name]) => name === 'UpdateResidentSettings');
    assert.deepEqual(update[1], { resident_id: 'resident_sample', changes: { prompt_injection: { preset: 'minimal' } } });
    assert.match(update[2].idempotencyKey, /^prompt-injection:[0-9a-f-]{36}$/);
    assert.equal(preset().value, 'minimal', 'the page shows the saved view');
    assert.deepEqual(executed.at(-1).slice(0, 2), ['PreviewPromptInjection', { resident_id: 'resident_sample', turn: 'after_compaction' }],
      'the preview is read again for the chosen turn');

    const rules = container.querySelector('[data-prompt-injection-part="rules"]');
    rules.value = 'every';
    rules.dispatchEvent(new w.Event('change'));
    await settle();
    assert.deepEqual(executed.filter(([name]) => name === 'UpdateResidentSettings').at(-1)[1].changes,
      { prompt_injection: { parts: { rules: 'every' } } });
    assert.deepEqual([...preset().options].map(option => [option.value, option.disabled]),
      [['standard', false], ['minimal', false], ['pure', false], ['custom', true]], 'a changed category shows as custom');

    container.querySelector('[data-prompt-injection-back]').click();
    assert.ok(container.querySelector('[data-agent-harness-mode-toggle]'), 'back returns to the Harness rows');
    assert.match(text(container.querySelector('[data-prompt-injection-open] small')), /^自定义 · /);

    w.PortalInstructionClient.execute = async () => { throw new Error('settings offline'); };
    container.querySelector('[data-prompt-injection-open]').click();
    await settle();
    assert.ok(container.querySelector('[data-agent-harness-mode-toggle]'), 'a failed read goes back to the Harness rows');
    assert.deepEqual(toasts.at(-1), ['settings offline', 'error']);
  } finally {
    panel.destroy();
    container.remove();
  }
}

(async () => {
  try {
    if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    mergedSettings();
    menuByModeAndSupport();
    await panelSavesSettings();
    await promptInjectionPage();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
