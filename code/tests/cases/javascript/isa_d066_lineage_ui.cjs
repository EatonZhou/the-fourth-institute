const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const scripts = resolve(process.env.PORTAL_D066_STATIC || 'management_portal/static');
const lineageId = 'lineage_0123456789ab';
const residentId = 'resident_0123456789ab';

function fixture(file, extras = {}) {
  const window = {
    PortalUIPrimitives: { randomUUID: () => 'isolated-test' },
    PortalI18n: { t: value => value },
    ...extras,
  };
  const context = { window, PortalUIPrimitives: window.PortalUIPrimitives, CustomEvent: class {} };
  vm.runInNewContext(readFileSync(resolve(scripts, file), 'utf8'), context);
  return window;
}

test('canvas sends versioned graph operations and retains creation receipts', async () => {
  const calls = [];
  const window = fixture('portal_society_service.js', {
    PortalResidentService: { withResidentDeletion: (id, run) => {
      calls.push(['guard', id]);
      return run();
    } },
  });
  const client = window.PortalSocietyService.createClient({
    getRevision: () => 9,
    execute: async (name, args, controls) => {
      if (name === 'GetLineage') return { lineage: { lineage_id: lineageId, revision: 10 } };
      calls.push(JSON.parse(JSON.stringify([name, args, controls])));
      return { created_ids: ['effect_0123456789ab'], deleted_ids: [residentId], revision: 10 };
    },
  });
  const created = await client.createProgramComponent({ lineage_id: lineageId, kind: 'effect', x: 22, y: 44, title: 'Report' });
  assert.equal(created.component_id, 'effect_0123456789ab');
  await client.saveProgramWire({ lineage_id: lineageId, wire_id: 'wire_0123456789ab', config: { label: 'review' } });
  await client.deleteNode({ lineage_id: lineageId, resident_id: residentId });
  assert.deepEqual(calls.filter(call => call[0] !== 'guard').map(([name, args, controls]) => [name, args, controls.expectedVersion]), [
    ['ApplyLineageChanges', { lineage_id: lineageId, x: 22, y: 44, operations: [{ op: 'create', kind: 'component', value: { kind: 'effect', title: 'Report' } }] }, 9],
    ['ApplyLineageChanges', { lineage_id: lineageId, operations: [{ op: 'update', kind: 'wire', id: 'wire_0123456789ab', changes: { config: { label: 'review' } } }] }, 9],
    ['ApplyLineageChanges', { lineage_id: lineageId, operations: [{ op: 'delete', kind: 'resident', id: residentId }] }, 9],
  ]);
  assert.deepEqual(calls.at(-2), ['guard', residentId]);
});

test('Fork, Move, Handoff and Familize send only the active contract fields', async () => {
  const calls = [];
  const window = fixture('portal_society_service.js');
  const client = window.PortalSocietyService.createClient({
    getRevision: () => 4,
    execute: async (name, args) => {
      if (name === 'GetLineage') return { lineage: { lineage_id: lineageId, revision: 5 } };
      calls.push(JSON.parse(JSON.stringify([name, args])));
      if (name === 'CreateLineage') return { lineage_id: lineageId };
      if (name === 'Familize') return { family_id: 'fbi_0123456789ab' };
      return {};
    },
  });
  await client.branchNode({ lineage_id: lineageId, source_resident_id: residentId, fork_mode: 'recent-5' });
  await client.moveObjects({ lineage_id: lineageId, object_ids: [residentId], allow_empty_source: true });
  await client.executeHandoff({ lineage_id: lineageId, handoff_id: 'handoff_example', source_resident_id: residentId });
  const created = await client.createLineage({ title: 'Project team', description: 'Goal', project_id: 'Research', familize: true });
  assert.equal(created.family_id, 'fbi_0123456789ab');
  assert.deepEqual(calls, [
    ['ForkResident', { lineage_id: lineageId, source_resident_id: residentId, fork_mode: 'recent', recent_rounds: 5 }],
    ['MoveLineageObjects', { lineage_id: lineageId, object_ids: [residentId], allow_empty_source: true }],
    ['RunWorkspaceHandoff', { lineage_id: lineageId, handoff_id: 'handoff_example' }],
    ['CreateLineage', { title: 'Project team', description: 'Goal', project_id: 'Research' }],
    ['Familize', { lineage_id: lineageId, scope: 'lineage', goal: 'Goal' }],
  ]);
});

test('template store dispatches separate install and update instructions', async () => {
  const calls = [];
  const window = fixture('template_store.js', {
    dispatchEvent() {},
    PortalUI: { toast() {} },
  });
  const options = { request: async (name, args) => {
    calls.push(JSON.parse(JSON.stringify([name, args])));
    return { template: { installed_store_version: 3 } };
  } };
  await window.PortalTemplateStore.installItem({ store_id: 'store-example' }, options);
  await window.PortalTemplateStore.installItem({ store_id: 'store-example', installed_template_id: 'template_installed' }, options);
  assert.deepEqual(calls, [
    ['InstallStoreTemplate', { store_id: 'store-example' }],
    ['UpdateInstalledTemplate', { store_id: 'store-example', target_template_id: 'template_installed' }],
  ]);
});

test('template store preserves the local copy when the user explicitly saves the update as new', async () => {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('', { runScripts: 'outside-only' });
  const { window } = dom;
  try {
    window.PortalI18n = { t: value => value };
    window.PortalUIPrimitives = { escapeHtml: value => value };
    window.PortalUI = {
      toast() {},
      createDialogController({ modal, onClose }) {
        return { open() { modal.open = true; }, close() { onClose(); }, destroy() {} };
      },
    };
    window.eval(readFileSync(resolve(scripts, 'template_store.js'), 'utf8'));
    const calls = [];
    const pending = window.PortalTemplateStore.installItem({
      store_id: 'store-example', installed_template_id: 'template_installed', locally_modified: true,
    }, { request: async (name, args) => {
      calls.push(JSON.parse(JSON.stringify([name, args])));
      return { template: { template_id: 'template_new', installed_store_version: 4 } };
    } });
    window.document.querySelector('[data-choice="copy"]').click();
    assert.equal((await pending).template_id, 'template_new');
    assert.deepEqual(calls, [
      ['InstallStoreTemplateAsNew', { store_id: 'store-example', target_template_id: 'template_installed' }],
    ]);
    assert.equal(window.document.querySelector('dialog'), null);
  } finally {
    window.close();
  }
});

test('component editor sends bindings and the corresponding edit in one versioned request', async () => {
  const calls = [];
  const window = fixture('portal_society_service.js');
  const client = window.PortalSocietyService.createClient({
    getRevision: () => 21,
    execute: async (name, args, controls) => {
      if (name === 'GetLineage') return { lineage: { lineage_id: lineageId, revision: 22,
        program: { components: [{ id: 'event_source_0123456789ab', title: 'Watch status' }] } } };
      calls.push(JSON.parse(JSON.stringify([name, args, controls.expectedVersion])));
      return { created_ids: ['wire_0123456789ab'], updated_ids: ['event_source_0123456789ab'], deleted_ids: [] };
    },
  });
  const operation = { op: 'create', kind: 'wire', value: { kind: 'watch', source: 'event_source_0123456789ab', source_port: 'watch', target: residentId } };
  const result = await client.saveProgramComponent({
    lineage_id: lineageId, component_id: 'event_source_0123456789ab', defer_refresh: true,
    component: { title: 'Watch status' }, wire_operations: [operation],
  });
  assert.equal(result.created_wire_ids[0], 'wire_0123456789ab');
  assert.equal((await result.refresh).component.title, 'Watch status');
  assert.deepEqual(calls, [['ApplyLineageChanges', {
    lineage_id: lineageId, operations: [{ op: 'update', kind: 'component', id: 'event_source_0123456789ab',
      changes: { title: 'Watch status' } }, operation],
  }, 21]]);
});

test('template editor uses read-only preview and keeps the recent round count as an integer', async () => {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM(`<main><section data-structure-list="roles"><div data-structure-row="0">
    <input data-row-field="fork_mode" value="recent"><input data-row-field="recent_rounds" data-field-type="integer" type="number" value="7">
    </div></section><div data-template-behavior></div><div id="status"></div></main>`, { runScripts: 'outside-only' });
  const { window } = dom;
  try {
    const root = window.document.querySelector('main');
    const bodyPage = root.querySelector('section');
    const status = root.querySelector('#status');
    const draft = { template_id: 'template_example', kind: 'structure', body: { roles: [{ from: 'fork' }] } };
    const calls = [];
    let preview;
    window.setTimeout = callback => { preview = callback; return 1; };
    window.eval(readFileSync(resolve(scripts, 'lineage_templates_editor_draft.js'), 'utf8'));
    const editor = window.LineageTemplatesEditorDraft.createEditorDraft({
      view: { root, workshop: false }, bodyPage, draft, parametersPage: root, status, editorState: { revision: 0 },
      behaviorHtml: () => '', clone: value => JSON.parse(JSON.stringify(value)),
      readField: (input, type) => type === 'integer' ? Number(input.value) : input.value,
      t: value => value, templatePayload: value => ({ kind: value.kind, body: value.body }),
      request: async (name, args) => {
        calls.push(JSON.parse(JSON.stringify([name, args])));
        return { template: { kind: 'structure' } };
      },
    });
    editor.schedulePreview();
    await preview();
    assert.equal(status.dataset.state, 'valid');
    assert.deepEqual(calls, [['GetTemplateUpdatePreview', { template_id: 'template_example',
      template: { kind: 'structure', body: { roles: [{ from: 'fork', fork_mode: 'recent', recent_rounds: 7 }] } },
    }]]);
    root.querySelector('[data-row-field="fork_mode"]').value = 'compacted';
    assert.equal(Object.hasOwn(editor.readDraft().body.roles[0], 'recent_rounds'), false);
  } finally {
    window.close();
  }
});

test('an Event watch target change updates the existing wire in the same edit', async () => {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM(`<form><input name="title" value="Watch"><input name="subject_resident_id" value="${residentId}"></form>`,
    { runScripts: 'outside-only' });
  const { window } = dom;
  try {
    const form = window.document.querySelector('form');
    const component = { id: 'event_source_0123456789ab', kind: 'event_source' };
    const wire = { id: 'wire_0123456789ab', kind: 'watch', source: component.id, target: 'resident_aaaabbbbcccc' };
    const config = { event_type: 'resident.completed' };
    const calls = [];
    window.PortalI18n = { t: value => value, text: String.raw };
    window.PortalLineagePrimitiveInspector = { readConfig: () => ({ config, errors: [] }) };
    window.PortalLineageProgram = { manifest: { event_types: { [config.event_type]: { watch_contract: { allowed_kinds: ['resident'] } } } } };
    window.eval(readFileSync(resolve(scripts, 'hatg_program_inspector_saving.js'), 'utf8'));
    const module = window.PortalHatgProgramInspectorSaving;
    const saving = module.createSaving({
      ...Object.fromEntries(module.dependencies.map(name => [name, () => {}])),
      canonicalProgramResidentId: value => value,
      programComponentById: () => component,
      state: { lineage: { lineage_id: lineageId, program: { wires: [wire] } } },
      definitionRevisions: new Map([[form, 8]]),
      applyProgramMutation: request => request({}),
      societyService: { saveProgramComponent: async payload => {
        calls.push(JSON.parse(JSON.stringify(payload)));
        return {};
      } },
    });
    await saving.saveProgramComponentFromInspector(component.id, form);
    assert.deepEqual(calls, [{
      lineage_id: lineageId, expected_version: 8, component_id: component.id,
      component: { title: 'Watch', config },
      wire_operations: [{ op: 'update', kind: 'wire', id: wire.id, changes: { target: residentId } }],
    }]);
  } finally {
    window.close();
  }
});

test('workshop installs prompt-only duty using a versioned template application', async () => {
  const calls = [];
  const window = fixture('lineage_template_workshop_chat.js', { PortalIdentity: { username: () => 'test-user' } });
  const { mount, DUTY_PROMPT } = window.LineageTemplateWorkshopChat;
  const chat = mount({
    root: { addEventListener() {}, querySelector: () => ({ addEventListener() {} }) },
    workshop: { sessionId: 'test', template: () => ({}) },
    listLineages: async () => ({ lineages: [{ lineage_id: lineageId, lineage_membership: 'personal', created_by: 'test-user' }] }),
    execute: async (name, args, options) => {
      calls.push(JSON.parse(JSON.stringify([name, args, options])));
      if (name === 'ListTemplates') return { templates: [{ builtin_key: 'workshop_assistant', template_id: 'template_assistant' }] };
      if (name === 'GetLineage') return { lineage: { revision: 13, function_blocks: [{
        template_id: 'template_assistant', elements: [{ id: residentId, kind: 'resident' }],
      }] } };
      if (name === 'GetResident') return { resident: { workspaces: [{ is_owned: true, workspace_id: 'workspace_assistant' }] } };
      if (name === 'CreateTemplate') return { template: { template_id: 'template_duty' } };
      return {};
    },
  });
  await chat.prepare();
  const writes = calls.filter(([name]) => !['ListTemplates', 'GetLineage', 'GetResident', 'ReadResource'].includes(name));
  assert.deepEqual(writes.map(([name]) => name), ['CreateTemplate', 'ApplyTemplate', 'DeleteTemplate']);
  assert.deepEqual(writes[0][1].template.body.duty.memory, { level: 'session', prompt: '{{param.workshop_prompt}}' });
  assert.deepEqual(writes[1][1].target, { kind: 'resident', resident_ids: [residentId] });
  assert.equal(writes[1][1].parameters.workshop_prompt, DUTY_PROMPT);
  assert.equal(writes[1][2].expectedVersion, 13);
  assert.deepEqual(writes[2][1], { template_id: 'template_duty' });
});
