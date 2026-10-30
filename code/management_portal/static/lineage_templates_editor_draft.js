(() => {
  'use strict';

  if (window.LineageTemplatesEditorDraft) return;

  // The draft of the template editor: the code editor for JSON and script
  // bodies, reading the dialog's fields back into the draft, and checking
  // the draft with a dry run shortly after each change.

  const DEPENDENCIES = Object.freeze([
    'behaviorHtml', 'bodyPage', 'clone', 'draft', 'editorState', 'parametersPage', 'readField', 'request', 'status',
    't', 'templatePayload', 'view',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['request']);

  function createEditorDraft(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditorDraft is missing: ${missing.join(', ')}`);
    const {
      behaviorHtml, bodyPage, clone, draft, editorState, parametersPage, readField, request, status, t,
      templatePayload, view,
    } = dependencies;

    let codeText = '';
    let codeActive = false;

    const disposeCode = () => { editorState.codeGeneration += 1; editorState.codeEditor?.destroy(); editorState.codeEditor = null; codeActive = false; };

    async function mountCode(root, value, language) {
      disposeCode(); codeActive = true; codeText = value;
      const generation = editorState.codeGeneration;
      try {
        const instance = await window.PortalCodeEditor.mount({ root, value, language, ariaLabel: language === 'json' ? t('结构 JSON') : t('Effect 脚本'),
          onChange: value => { codeText = value; schedulePreview(); } });
        if (generation !== editorState.codeGeneration || !root.isConnected) instance.destroy();
        else editorState.codeEditor = instance;
      } catch (error) { view.error(error); }
    }

    function readDraft() {
      view.root.querySelectorAll('[data-meta]').forEach(input => { draft[input.dataset.meta] = input.value; });
      draft.targets = [...view.root.querySelectorAll('[data-target]:checked')].map(input => input.dataset.target);
      if (editorState.roleSettings) draft.body.resident_settings = clone(editorState.roleSettings);
      if (codeActive) {
        if (draft.kind === 'structure') {
          const value = JSON.parse(codeText);
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('结构 JSON 必须是对象'));
          draft.body = value;
        } else if (draft.kind === 'script') draft.body.script = codeText;
      }
      bodyPage.querySelectorAll('[data-body-field]').forEach(input => {
        draft.body[input.dataset.bodyField] = readField(input, input.dataset.fieldType || 'text');
      });
      bodyPage.querySelectorAll('[data-row-field]').forEach(input => {
        const row = input.closest('[data-structure-row]');
        const list = row.closest('[data-structure-list]').dataset.structureList;
        const value = readField(input, input.dataset.fieldType || 'text');
        draft.body[list][Number(row.dataset.structureRow)][input.dataset.rowField] = value;
      });
      parametersPage.querySelectorAll('[data-parameter-row]').forEach(row => {
        const parameter = draft.parameters[Number(row.dataset.parameterRow)];
        row.querySelectorAll('[data-parameter-field]').forEach(input => {
          const key = input.dataset.parameterField;
          if (key === 'required') parameter.required = input.checked;
          else if (key === 'options') parameter.options = input.value.split('\n').map(item => item.trim()).filter(Boolean);
          else if (key !== 'default') parameter[key] = input.value;
        });
        const value = row.querySelector('[data-parameter-field="default"]').value;
        if (parameter.type === 'boolean') parameter.default = value === 'true';
        else if (['integer', 'number', 'duration'].includes(parameter.type)) {
          if (value.trim()) { parameter.default = Number(value); if (!Number.isFinite(parameter.default)) throw new Error(t('请输入有效数值')); }
          else delete parameter.default;
        } else parameter.default = value;
      });
      return templatePayload(draft);
    }

    async function preview() {
      const serial = ++editorState.revision;
      try {
        const payload = readDraft();
        const result = await request(draft.template_id ? 'UpdateTemplate' : 'CreateTemplate', draft.template_id ? { template_id: draft.template_id, ...payload, dry_run: true } : { template: payload, dry_run: true });
        if (serial !== editorState.revision || !view.root.isConnected) return;
        view.root.querySelector('[data-template-behavior]').innerHTML = behaviorHtml(result.template.behavior);
        status.textContent = t('预览已更新'); status.dataset.state = 'valid';
      } catch (error) {
        if (serial !== editorState.revision || !view.root.isConnected) return;
        status.textContent = String(error.message || error); status.dataset.state = 'error';
      }
    }

    function schedulePreview() {
      editorState.revision += 1; clearTimeout(editorState.timer);
      status.textContent = t('正在校验…'); status.dataset.state = 'pending';
      editorState.timer = setTimeout(preview, 320);
    }

    return {
      disposeCode, mountCode, readDraft, schedulePreview,
    };
  }

  window.LineageTemplatesEditorDraft = Object.freeze({ createEditorDraft, dependencies: DEPENDENCIES });
})();
