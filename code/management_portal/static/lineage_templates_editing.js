(() => {
  'use strict';

  if (window.LineageTemplatesEditing) return;

  // Creating and editing templates: a new template of a chosen kind, the
  // template editor, and a template saved from a selection on the canvas.

  const DEPENDENCIES = Object.freeze([
    'behaviorHtml', 'button', 'categories', 'clone', 'dialog', 'esc', 'field', 'getContext', 'load', 'optionsHtml',
    'readField', 'renderResidentSettings', 'request', 'residentRoles', 't', 'targetLabels', 'templatePart',
    'templatePayload', 'templatesState', 'toast',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['getContext', 'request', 'toast']);

  function createEditing(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditing is missing: ${missing.join(', ')}`);
    const {
      behaviorHtml, button, categories, clone, dialog, esc, field, getContext, load, optionsHtml, readField,
      renderResidentSettings, request, residentRoles, t, targetLabels, templatePart, templatePayload, templatesState,
      toast,
    } = dependencies;

    function create(kind) {
      const defaults = {
        prompt: { message: '', activation_policy: 'queue' },
        structure: { roles: [], components: [], wires: [], function_block: true },
        script: { script: '', endpoints: ['target'] },
      };
      const metadata = templatesState.kinds.find(item => item.key === kind);
      return edit({ kind, category: kind === 'structure' ? 'structure' : 'apply', title: t('新模板'), description: '',
        targets: metadata?.targets || (kind === 'prompt' ? ['resident', 'selection'] : ['resident', 'selection', 'canvas']), parameters: [], body: defaults[kind],
        behavior: { steps: [], persistent: kind === 'structure' }, editor: metadata?.editor || { fields: [] } });
    }

    function newTemplate() {
      const view = dialog('new', t('新建模板'));
      view.content.innerHTML = `<p>${esc(t('选择模板类型'))}</p><div class="templateKindChoices">${[
        ['prompt', '施加 Prompt', '给目标 Resident 投递一段消息'], ['structure', '放入结构', '组合角色、组件与连线'], ['script', '执行脚本', '执行一次 Effect 脚本'],
      ].map(([kind, title, description]) => `<button type="button" data-kind="${kind}"><strong>${esc(t(title))}</strong><small>${esc(t(description))}</small></button>`).join('')}</div>`;
      view.content.addEventListener('click', event => { const choice = event.target.closest('[data-kind]'); if (choice) { view.close(); create(choice.dataset.kind); } });
    }

    async function openEditor(id) {
      const result = await request('GetTemplate', { template_id: id });
      if (!templatesState.disposed) edit(result.template);
    }

    // edit opens the template editor on a copy of a template. Its parts:
    //   lineage_templates_editor_draft.js  the code editor, reading the draft
    //     and checking it with a dry run
    //   lineage_templates_editor_pages.js  the body and parameters pages
    //   lineage_templates_editor_elements.js  a structure's elements and their
    //     settings fields
    //   lineage_templates_editor_inspector.js  the selected element's inspector
    //   lineage_templates_editor_actions.js  the dialog's controls
    // edit opens the dialog, creates the parts in that order and draws the
    // pages; a part that calls a function of a part created after it gets an
    // arrow forwarding the call.
    function edit(source) {
      templatesState.editor?.close();
      const draft = clone(source);
      const view = dialog('editor', `${t('模板编辑器')} · ${source.title}`, source);
      templatesState.editor = view;
      view.root.querySelector('.templateSurface')?.classList.toggle('templateSurface-structure', draft.kind === 'structure');
      // The state the parts share: the pending check and its revision, the
      // code editor and its generation, the graph and the role settings.
      const editorState = {
        timer: null, revision: 0, codeEditor: null, codeGeneration: 0, graph: null, roleSettings: null,
      };
      const templateRoles = draft.kind === 'familize' ? residentRoles(draft) : [];
      view.cleanups.push(() => { clearTimeout(editorState.timer); editorState.revision += 1; editorState.codeGeneration += 1; editorState.codeEditor?.destroy(); editorState.graph?.destroy(); });
      const supported = templatesState.kinds.find(item => item.key === draft.kind)?.targets || (draft.kind === 'prompt' ? ['resident', 'selection'] : ['resident', 'selection', 'canvas']);
      view.content.innerHTML = `<div class="templateMetadata">
          <label class="templateField templateTitle"><span>${esc(t('标题'))}</span><input data-meta="title" value="${esc(draft.title)}" required></label>
          <span class="templateKindBadge">${esc(draft.kind)}</span>
          <label class="templateField"><span>${esc(t('分组'))}</span><select data-meta="category">${optionsHtml(Object.entries(categories()).map(([value, label]) => ({ value, label })), draft.category)}</select></label>
          <label class="templateField templateDescription"><span>${esc(t('说明'))}</span><input data-meta="description" value="${esc(draft.description)}"></label>
          <fieldset class="templateTargets"><legend>${esc(t('支持的目标'))}</legend>${supported.map(kind => `<label><input type="checkbox" data-target="${kind}" ${draft.targets.includes(kind) ? 'checked' : ''}>${esc(targetLabels()[kind])}</label>`).join('')}</fieldset>
        </div>
        <div class="templateEditorLayout"><div class="templateEditorMain"><nav class="templateTabs" role="tablist">${[['body', '内容'], ['parameters', '参数'], ...(templateRoles.length ? [['objects', '内部对象']] : [])].map(([key, label], index) => `<button type="button" role="tab" aria-selected="${!index}" data-tab="${key}">${esc(t(label))}</button>`).join('')}</nav>
          <section data-page="body" role="tabpanel"></section><section data-page="parameters" role="tabpanel" hidden></section>${templateRoles.length ? '<section data-page="objects" role="tabpanel" hidden></section>' : ''}</div>
          <aside class="templatePreview" aria-live="polite"><section data-template-behavior>${behaviorHtml(draft.behavior)}</section><p class="templatePreviewStatus" data-preview-status></p></aside></div>`;
      view.footer.innerHTML = `${draft.template_id ? button('删除', 'delete', 'is-danger') : ''}${draft.builtin_key ? button('恢复内置', 'restore', 'is-quiet') : ''}<span class="modalActionSpacer"></span>${button('另存为副本', 'copy')}${button('取消', 'close')}<button type="submit" class="portalButton is-primary">${esc(t('保存'))}</button>`;
      const bodyPage = view.root.querySelector('[data-page="body"]');
      const parametersPage = view.root.querySelector('[data-page="parameters"]');
      const status = view.root.querySelector('[data-preview-status]');

      const {
        disposeCode, mountCode, readDraft, schedulePreview,
      } = templatePart('LineageTemplatesEditorDraft').createEditorDraft({
        behaviorHtml, bodyPage, clone, draft, editorState, parametersPage, readField, request, status, t,
        templatePayload, view,
      });
      const {
        listLabels, renderBody, renderParameters, renderStructureLists, showStructure,
      } = templatePart('LineageTemplatesEditorPages').createEditorPages({
        bodyPage, button, disposeCode, draft, editorState, esc, field, mountCode, optionsHtml, parametersPage,
        readDraft, renderInspector: (...args) => renderInspector(...args), schedulePreview, t,
      });
      const {
        changesHtml, elementFields, elementSettingsHtml, foundJson, graphTools, holdTokens, invokeWire, isWire,
        readElementSettings, recordUpdate,
      } = templatePart('LineageTemplatesEditorElements').createEditorElements({ button, draft, esc, field, t });
      const { renderInspector } = templatePart('LineageTemplatesEditorInspector').createEditorInspector({
        bodyPage, button, changesHtml, clone, draft, editorState, elementFields, elementSettingsHtml, esc, field,
        foundJson, graphTools, holdTokens, invokeWire, isWire, listLabels, readElementSettings, recordUpdate,
        renderResidentSettings, schedulePreview, t,
      });
      templatePart('LineageTemplatesEditorActions').createEditorActions({
        bodyPage, draft, edit, editorState, load, readDraft, renderBody, renderInspector, renderParameters,
        renderStructureLists, request, schedulePreview, showStructure, t, toast, view,
      });
      renderBody(); renderParameters();
      if (templateRoles.length) {
        const objectsPage = view.root.querySelector('[data-page="objects"]');
        objectsPage.innerHTML = `<p class="templateHelp">${esc(t('设置新建 Family 时 Router 和 Steward 的默认配置，施加模板时可以调整。'))}</p>`;
        editorState.roleSettings = renderResidentSettings(objectsPage, templateRoles, {}, draft);
      }
      schedulePreview();
    }

    // With `rewrite`, the selection becomes the existing structure the template finds.
    async function saveSelection(elementIds, { rewrite = false } = {}) {
      const result = await request('CreateTemplate', { from_selection: { lineage_id: getContext().lineage_id, element_ids: elementIds, ...(rewrite ? { rewrite: true } : {}) } });
      await load(); return openEditor(result.template.template_id);
    }

    return {
      newTemplate, openEditor, saveSelection,
    };
  }

  window.LineageTemplatesEditing = Object.freeze({ createEditing, dependencies: DEPENDENCIES });
})();
