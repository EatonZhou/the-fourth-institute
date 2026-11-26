(() => {
  'use strict';

  if (window.LineageTemplatesEditing) return;

  // Creating and editing templates: a new duty template, the
  // template editor, and a template saved from a selection on the canvas.

  const DEPENDENCIES = Object.freeze([
    'behaviorHtml', 'button', 'categories', 'clone', 'dialog', 'editorHost', 'esc', 'field', 'getContext', 'load', 'optionsHtml',
    'readField', 'renderResidentSettings', 'request', 'residentRoles', 't', 'targetLabels', 'templatePart',
    'templatePayload', 'templatesState', 'toast', 'onOpenWorkshop',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['getContext', 'request', 'toast', 'editorHost', 'onOpenWorkshop']);

  function createEditing(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditing is missing: ${missing.join(', ')}`);
    const {
      behaviorHtml, button, categories, clone, dialog, esc, field, getContext, load, optionsHtml, readField,
      renderResidentSettings, request, residentRoles, t, targetLabels, templatePart, templatePayload, templatesState,
      toast, editorHost, onOpenWorkshop,
    } = dependencies;

    function newTemplate() {
      if (!editorHost) return openWorkshop();
      const metadata = templatesState.kinds.find(item => item.key === 'structure');
      return edit({kind: 'structure', category: 'structure', title: t('新职责模板'), description: '',
        targets: ['canvas'], parameters: [],
        body: window.LineageTemplateWorkshopDraft.empty(),
        behavior: {steps: [], persistent: true}, editor: metadata?.editor || {fields: []}});
    }

    async function openEditor(id) {
      if (!editorHost) return openWorkshop(id);
      const result = await request('GetTemplate', { template_id: id });
      if (!templatesState.disposed) edit(result.template);
    }

    function openWorkshop(id = '') {
      if (!onOpenWorkshop) throw new Error(t('当前工作台无法打开模板工坊'));
      return Promise.resolve(onOpenWorkshop({kind:'workshop',template_id:id,lineage_id:getContext?.().lineage_id || ''}));
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
      const view = editorHost ? editorHost.create(source) : dialog('editor', `${t('模板编辑器')} · ${source.title}`, source);
      templatesState.editor = view;
      view.root.querySelector('.templateSurface')?.classList.toggle('templateSurface-structure', draft.kind === 'structure');
      // The state the parts share: the pending check and its revision, the
      // code editor and its generation, the graph and the role settings.
      const editorState = {
        timer: null, revision: 0, codeEditor: null, codeGeneration: 0, graph: null, roleSettings: null, workshop: !!editorHost, openTemplate:view.openTemplate,
      };
      const templateRoles = draft.kind === 'familize' && !editorHost ? residentRoles(draft) : [];
      view.cleanups.push(() => { clearTimeout(editorState.timer); editorState.revision += 1; editorState.codeGeneration += 1; editorState.codeEditor?.destroy(); editorState.recipeMenu?.destroy(); editorState.graph?.destroy(); });
      const supported = templatesState.kinds.find(item => item.key === draft.kind)?.targets || (draft.kind === 'prompt' ? ['resident', 'selection'] : ['resident', 'selection', 'canvas']);
      view.content.innerHTML = editorHost ? `<section data-page="body"></section>
        <details class="workshopInfo"><summary>${esc(t('模板信息'))}</summary>
          ${field({name: 'description', label: '说明'}, draft.description, 'data-meta="description"')}
          <section data-page="parameters"></section><section data-template-behavior></section>
          <p data-preview-status role="status"></p></details>` : `<div class="templateMetadata">
          <label class="templateField templateTitle"><span>${esc(t('标题'))}</span><input data-meta="title" value="${esc(draft.title)}" required></label>
          <span class="templateKindBadge">${esc(draft.kind)}</span>
          <label class="templateField"><span>${esc(t('分组'))}</span><select data-meta="category">${optionsHtml(Object.entries(categories()).map(([value, label]) => ({ value, label })), draft.category)}</select></label>
          <label class="templateField templateDescription"><span>${esc(t('说明'))}</span><input data-meta="description" value="${esc(draft.description)}"></label>
          <fieldset class="templateTargets"><legend>${esc(t('支持的目标'))}</legend>${supported.map(kind => `<label><input type="checkbox" data-target="${kind}" ${draft.targets.includes(kind) ? 'checked' : ''}>${esc(targetLabels()[kind])}</label>`).join('')}</fieldset>
        </div>
        <div class="templateEditorLayout"><div class="templateEditorMain"><nav class="templateTabs" role="tablist">${[['body', '内容'], ['parameters', '参数'], ...(templateRoles.length ? [['objects', '内部对象']] : [])].map(([key, label], index) => `<button type="button" role="tab" aria-selected="${!index}" data-tab="${key}">${esc(t(label))}</button>`).join('')}</nav>
          <section data-page="body" role="tabpanel"></section><section data-page="parameters" role="tabpanel" hidden></section>${templateRoles.length ? '<section data-page="objects" role="tabpanel" hidden></section>' : ''}</div>
          <aside class="templatePreview" aria-live="polite"><section data-template-behavior>${behaviorHtml(draft.behavior, {}, draft.kind)}</section><p class="templatePreviewStatus" data-preview-status></p></aside></div>`;
      view.footer.innerHTML = editorHost ? `${draft.builtin_key ? button('恢复内置','restore','is-quiet is-compact') : ''}${draft.template_id ? button('删除','delete','is-danger is-compact') : ''}<button type="submit" class="portalButton is-primary" data-workshop-save translate="no">Save</button><button type="button" class="portalButton" data-action="copy" translate="no">Save As</button>` : `${draft.template_id ? button('删除', 'delete', 'is-danger') : ''}${draft.builtin_key ? button('恢复内置', 'restore', 'is-quiet') : ''}<span class="modalActionSpacer"></span>${button('另存为副本', 'copy')}${button('取消', 'close')}<button type="submit" class="portalButton is-primary">${esc(t('保存'))}</button>`;
      const bodyPage = view.root.querySelector('[data-page="body"]');
      const parametersPage = view.root.querySelector('[data-page="parameters"]');
      const status = view.root.querySelector('[data-preview-status]');

      const {
        disposeCode, mountCode, readDraft, schedulePreview, snapshot, restoreInputs,
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
      function restore(value) {
        const mode = value.invalidMode || editorState.structureMode;
        disposeCode(); delete editorState.invalidElement; delete editorState.invalidTemplateMenu; Object.assign(draft,clone(value.content));
        if(value.invalidTemplateMenu)editorState.invalidTemplateMenu=clone(value.invalidTemplateMenu);
        view.root.querySelectorAll('[data-meta]').forEach(input => { input.value = draft[input.dataset.meta] ?? ''; });
        renderBody(); renderParameters();
        if (draft.kind === 'structure' && mode && mode !== editorState.structureMode) showStructure(mode);
        if (value.invalidCode !== undefined) {
          if (draft.kind === 'structure') showStructure('json');
          mountCode(bodyPage.querySelector('[data-code-editor]'),value.invalidCode,draft.kind === 'structure' ? 'json' : 'shell');
        }
        if (value.invalidElement) {
          if (draft.kind === 'structure') showStructure('graph');
          editorState.graph?.select(value.invalidElement.key);
          editorState.invalidElement = clone(value.invalidElement);
          const control = bodyPage.querySelector('[data-element-json]');
          if(control){control.closest('details').open=true;control.value=value.invalidElement.text;control.setCustomValidity(t('JSON 必须是对象'));}
        }
        else if(value.invalidSelection) editorState.graph?.select(value.invalidSelection);
        restoreInputs(value.invalidInputs);
        if(value.invalidTemplateMenu)editorState.recipeMenu?.open(value.invalidTemplateMenu.resident,'settings');
        schedulePreview();
      }
      editorHost?.ready({draft, view, editorState, readDraft, snapshot, restore, schedulePreview, showStructure, renderParameters, edit});
    }

    // With `rewrite`, the selection becomes the existing structure the template finds.
    async function saveSelection(elementIds, { rewrite = false } = {}) {
      const result = await request('CreateTemplate', { from_selection: { lineage_id: getContext().lineage_id, element_ids: elementIds, ...(rewrite ? { rewrite: true } : {}) } });
      await load(); return openEditor(result.template.template_id);
    }

    return {
      newTemplate, openEditor, edit, saveSelection,
    };
  }

  window.LineageTemplatesEditing = Object.freeze({ createEditing, dependencies: DEPENDENCIES });
})();
