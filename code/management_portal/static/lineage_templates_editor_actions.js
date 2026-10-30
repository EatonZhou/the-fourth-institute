(() => {
  'use strict';

  if (window.LineageTemplatesEditorActions) return;

  // What the template editor's controls do: saving, copying, deleting and
  // restoring the template, tabs and structure views, rows and parameters.
  // It binds the dialog's listeners when it is created.

  const DEPENDENCIES = Object.freeze([
    'bodyPage', 'draft', 'edit', 'editorState', 'load', 'readDraft', 'renderBody', 'renderInspector',
    'renderParameters', 'renderStructureLists', 'request', 'schedulePreview', 'showStructure', 't', 'toast', 'view',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['request', 'toast']);

  function createEditorActions(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesEditorActions is missing: ${missing.join(', ')}`);
    const {
      bodyPage, draft, edit, editorState, load, readDraft, renderBody, renderInspector, renderParameters,
      renderStructureLists, request, schedulePreview, showStructure, t, toast, view,
    } = dependencies;

    async function save(copy = false) {
      const payload = readDraft();
      if (!view.form.reportValidity()) return;
      const result = await request(draft.template_id && !copy ? 'UpdateTemplate' : 'CreateTemplate', draft.template_id && !copy ? { template_id: draft.template_id, ...payload } : { template: { ...payload, title: copy ? `${payload.title} · ${t('副本')}` : payload.title } });
      await load();
      toast(t('模板已保存'), 'success');
      view.close();
      if (copy) edit(result.template);
    }

    view.form.addEventListener('input', schedulePreview);
    view.form.addEventListener('change', schedulePreview);
    bodyPage.addEventListener('change', event => {
      const name = event.target.dataset.rowField;
      if (!name) return;
      try {
        const row = event.target.closest('[data-structure-row]');
        const list = row.closest('[data-structure-list]').dataset.structureList;
        const index = Number(row.dataset.structureRow);
        readDraft();
        const item = draft.body[list][index];
        if (name === 'key') window.LineageTemplateGraph.renameKey(draft.body, row.dataset.rowKey, item.key);
        if (name === 'from' && item.from === 'fork') delete item.provider;
        if (editorState.graph) {
          if (['key', 'from'].includes(name)) editorState.graph.select(item.key);
          else editorState.graph.render();
        } else if (['key', 'from'].includes(name)) {
          renderStructureLists();
          bodyPage.querySelector(`[data-structure-list="${list}"] [data-structure-row="${index}"]`).open = true;
        }
      } catch (error) { view.error(error); }
    });
    view.form.addEventListener('submit', event => { event.preventDefault(); view.busy(() => save()); });
    view.root.addEventListener('click', event => {
      const element = event.target.closest('button');
      if (!element) return;
      const action = element.dataset.action;
      try {
        if (element.dataset.tab) {
          readDraft();
          view.root.querySelectorAll('[data-tab]').forEach(tab => tab.setAttribute('aria-selected', String(tab === element)));
          view.root.querySelectorAll('[data-page]').forEach(page => { page.hidden = page.dataset.page !== element.dataset.tab; });
          if (element.dataset.tab === 'body' && draft.kind === 'prompt') renderBody();
          // The graph was measured while hidden; fit it again once it is visible.
          else if (element.dataset.tab === 'body') requestAnimationFrame(() => editorState.graph?.fit());
        } else if (element.dataset.structureView) {
          readDraft(); showStructure(element.dataset.structureView);
        } else if (element.dataset.insertParameter) {
          const input = bodyPage.querySelector('[data-body-field="message"]');
          input.setRangeText(`{{param.${element.dataset.insertParameter}}}`, input.selectionStart, input.selectionEnd, 'end'); input.focus(); schedulePreview();
        } else if (element.dataset.addRow) {
          readDraft(); const key = element.dataset.addRow;
          draft.body[key] ||= [];
          const base = key === 'roles' ? { from: 'new', title: '', provider: 'codex', settings: {} } : key === 'components' ? { kind: 'event_source', title: '', config: {} }
            : key === 'match' ? { kind: 'event_source', title: '', config: {}, where: {}, op: 'keep' } : { kind: 'route', source: '', target: '', config: {} };
          let index = draft.body[key].length + 1; while (draft.body[key].some(row => row.key === `${key}_${index}`)) index += 1;
          draft.body[key].push({ key: `${key}_${index}`, ...base }); renderStructureLists(); schedulePreview();
        } else if (action === 'remove-row') {
          readDraft();
          const row = element.closest('[data-structure-row]');
          const key = draft.body[row.closest('[data-structure-list]').dataset.structureList][Number(row.dataset.structureRow)].key;
          if (editorState.graph) editorState.graph.remove(key);
          else { window.LineageTemplateGraph.removeElement(draft.body, key); renderStructureLists(); schedulePreview(); }
        } else if (action === 'revert-update') {
          const row = draft.body.match.find(item => item.key === element.closest('[data-row-key]')?.dataset.rowKey);
          if (row) { row.op = 'keep'; delete row.update; schedulePreview(); editorState.graph?.render(); renderInspector(row.key); }
        } else if (action === 'add-parameter') {
          readDraft(); draft.parameters.push({ name: '', type: 'text', default: '', required: false, description: '' }); renderParameters(); schedulePreview();
        } else if (element.hasAttribute('data-remove-parameter')) {
          readDraft(); draft.parameters.splice(Number(element.dataset.removeParameter), 1); renderParameters(); schedulePreview();
        } else if (action === 'copy') view.busy(() => save(true));
        else if (action === 'delete') view.busy(async () => {
          if (!await window.PortalUI.confirm({ title: t('删除模板？'), message: t('已施加的结果会保留。'), confirmLabel: t('删除'), tone: 'danger' })) return;
          await request('DeleteTemplate', { template_id: draft.template_id }); await load(); view.close(); toast(t('模板已删除'), 'success');
        });
        else if (action === 'restore') view.busy(async () => {
          if (!await window.PortalUI.confirm({ title: t('恢复内置模板？'), message: t('当前修改将替换为内置内容。'), confirmLabel: t('恢复内置') })) return;
          const result = await request('RestoreTemplate', { template_id: draft.template_id }); await load(); view.close(); edit(result.template);
        });
      } catch (error) { view.error(error); }
    });
  }

  window.LineageTemplatesEditorActions = Object.freeze({ createEditorActions, dependencies: DEPENDENCIES });
})();
