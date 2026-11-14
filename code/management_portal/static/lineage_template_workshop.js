(() => {
  'use strict';
  const t = text => window.PortalI18n.t(text);
  const esc = text => window.PortalUIPrimitives.escapeHtml(String(text ?? ''));
  const clone = value => JSON.parse(JSON.stringify(value));
  const request = (name, args, options) => window.PortalInstructionClient.execute(name, args, options);
  function mount({root, lineageId, templateId = '', execute = request, onSaved, onDeleted}) {
    root.querySelectorAll('[id]').forEach(node => { node.dataset.workshopId ||= node.id; });
    const element = id => root.querySelector(`[data-workshop-id="${id}"],#${id}`);
    let form = element('workshopEditor');
    const canvas = element('workshopCanvas');
    const drawer = element('workshopToolbox');
    const shelf = element('workshopShelf');
    const sessionId = window.PortalUIPrimitives.randomUUID();
    let runtime = null, templates = null, base = '', fresh = !templateId;
    let removed = false;
    let documentRevision = 0;
    const abort = new AbortController();
    const listen = (node, type, handler) => node.addEventListener(type, handler, {signal: abort.signal});
    const error = value => {
      const node = element('workshopError');
      node.hidden = !value;
      node.textContent = value ? window.PortalI18n.message(value.message || value) : '';
    };
    const dirty = () => {
      if (removed) return false;
      try { return fresh || (!!runtime && JSON.stringify(runtime.readDraft()) !== base); }
      catch (_) { return true; }
    };
    const changed = () => {
      element('workshopUnsaved').hidden = !dirty();
      root.dispatchEvent(new CustomEvent('workshop:draft-changed'));
    };
    async function save(payload, copy = false) {
      const id = runtime.draft.template_id;
      const own = !id || (!runtime.draft.builtin_key && runtime.draft.created_by === window.PortalIdentity.username());
      if (!copy && !own) throw new Error(t('请用 Save As 保存为自己的模板'));
      const name = id && !copy ? 'UpdateTemplate' : 'CreateTemplate';
      const args = name === 'UpdateTemplate' ? {template_id: id, template: payload} : {template: payload};
      await execute(name, {...args, dry_run: true});
      const result = await execute(name, args);
      fresh = false;
      if (copy) documentRevision += 1;
      templateId = result.template.template_id;
      onSaved?.(result.template);
      runtime.edit(result.template);
      await templates.load();
      changed();
      return result.template;
    }
    const editorHost = {
      create(source) {
        const previous = form;
        form = previous.cloneNode(false); previous.replaceWith(form);
        form.innerHTML = `<header class="workshopBar">
          <input data-meta="title" aria-label="${esc(t('模板标题'))}" value="${esc(source.title)}" required>
          <details class="workshopMore" ${source.kind === 'structure' || source.template_id ? '' : 'hidden'}><summary aria-label="${esc(t('更多'))}">…</summary><div>${source.kind === 'structure' ? [['graph', '图视图'], ['list', '列表视图'], ['json', 'JSON 视图']].map(([key,label]) => `<button type="button" data-structure-view="${key}">${esc(t(label))}</button>`).join('') : ''}${source.builtin_key ? `<button type="button" data-action="restore">${esc(t('恢复内置'))}</button>` : ''}${source.template_id ? `<button type="button" data-action="delete">${esc(t('删除'))}</button>` : ''}</div></details>
          <button type="button" data-workshop-chat data-portal-icon="chat" aria-label="${esc(t('工坊助手'))}"></button><footer></footer></header><main class="workshopEditorContent"></main>`;
        const cleanups = [], view = {
          root: form, form, content: form.querySelector('main'), footer: form.querySelector('footer'), cleanups,
          workshop: true, error, onChange: changed, save,
          deleted() { removed = true; onDeleted?.(); },
          close() { cleanups.forEach(cleanup => cleanup()); form.replaceChildren(); },
          async busy(work) {
            if (view.working) return;
            view.working = true; error(null);
            const buttons = [...view.footer.querySelectorAll('button')]; buttons.forEach(button => { button.disabled = true; });
            try { return await work(); } catch (exception) { error(exception); }
            finally {
              view.working = false; buttons.forEach(button => { button.disabled = false; });
              const saveButton = view.root.querySelector('[data-workshop-save]');
              if (saveButton && runtime) saveButton.disabled = !!runtime.draft.template_id && (!!runtime.draft.builtin_key || runtime.draft.created_by !== window.PortalIdentity.username());
            }
          },
        };
        return view;
      },
      ready(value) {
        runtime = value;
        root.dataset.templateKind = runtime.draft.kind;
        root.querySelector('[data-workshop-toolbox]').hidden = runtime.draft.kind !== 'structure';
        if (runtime.draft.kind !== 'structure') drawer.hidden = true;
        base = JSON.stringify(runtime.readDraft());
        const saveButton = form.querySelector('[data-workshop-save]');
        saveButton.disabled = !!runtime.draft.template_id && (!!runtime.draft.builtin_key || runtime.draft.created_by !== window.PortalIdentity.username());
        element('workshopChatSave').disabled = saveButton.disabled;
        changed();
      },
    };
    function toolboxItems() {
      const body = (list, row) => ({roles: [], components: [], wires: [], match: [], [list]: [row]});
      const items = [];
      const item = (key, title, group, content) => items.push({template_id: `workshop_${key}`, builtin_key: `workshop_${key}`, kind: 'structure', title, description: '', targets: ['canvas'], toolbox_group: group, body: content});
      for (const kind of ['event_source', 'effect', 'loop', 'filter', 'selector', 'reducer', 'seed']) {
        const definition = window.PortalLineageProgram.manifest.component_kinds[kind];
        item(kind, definition?.title || kind, 'primitives', body('components', {key: kind, kind, title: definition?.default_title || definition?.title || kind, config: clone(definition?.default_config || {})}));
      }
      for (const [from, title] of [['target','目标 Resident'], ['lineage_leader','Lineage Leader'], ['new','新建 Resident'], ['fork','Fork']]) {
        item(from, t(title), 'slots', body('roles', {key: from === 'lineage_leader' ? 'leader' : from, from, ...(from === 'new' ? {settings: {}} : {})}));
      }
      item('existing', t('已有元素'), 'slots', body('match', {key: 'existing', kind: 'event_source', config: {}, where: {}, op: 'keep'}));
      return items;
    }
    templates = window.LineageTemplates.mount({
      shelf, canvas, request: execute, getContext: () => ({lineage_id: lineageId}),
      editorHost, toast: (message, tone) => { if (tone === 'error') error(message); },
      toolboxGroups: [{key:'primitives', label:'原语'}, {key:'slots', label:'空位'}], toolboxItems: toolboxItems(),structureOnly:true,
      acceptTemplate: item => item.kind === 'structure',
      resolveTarget: event => ({kind:'canvas', ...runtime.editorState.graph.position(event)}),
      async applyTemplate(id, target) {
        if (runtime.draft.kind !== 'structure') throw new Error(t('请先打开职责模板'));
        runtime.readDraft();
        const source = clone(templates.find(id));
        const incoming = source.body;
        if (id === 'workshop_fork') {
          const origin = runtime.draft.body.roles.find(row => row.key === runtime.editorState.graph.selected()) || runtime.draft.body.roles[0];
          if (!origin) throw new Error(t('先添加一个角色，Fork 才有来源'));
          incoming.roles[0].fork_of = origin.key; incoming.roles[0].fork_mode = 'full';
        }
        const key = window.LineageTemplateWorkshopDraft.appendTemplate(runtime.draft, source, target);
        if (id === 'workshop_fork') runtime.draft.body.roles.find(row => row.key === key).fork_of = incoming.roles[0].fork_of;
        runtime.renderParameters(); runtime.editorState.graph.select(key); runtime.schedulePreview();
        drawer.hidden = true;
      },
    });
    listen(root, 'click', async event => {
      if (event.target.closest('[data-workshop-toolbox]')) drawer.hidden = !drawer.hidden;
      if (event.target.closest('[data-workshop-chat]')) root.classList.toggle('workshopChatCollapsed');
    });
    listen(drawer, 'portal:template-picked', () => { drawer.hidden = true; runtime.editorState.graph?.select(''); });
    listen(window, 'beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
    listen(document, 'keydown', event => { if (event.key === 'Escape') drawer.hidden = true; });
    const controller = {
      sessionId, lineageId, dirty, documentKey: () => `${sessionId}:${documentRevision}`,
      fit: () => runtime?.editorState.graph?.fit(),
      save: () => runtime.view.busy(() => save(runtime.readDraft())), template: () => runtime?.draft,
      async acceptDraft(value) {
        const payload = window.LineageTemplateWorkshopDraft.payload({...runtime.draft,...value});
        await execute('CreateTemplate', {template:payload, dry_run:true});
        if (dirty() && !await window.PortalUI.confirm({title:t('助手草稿已就绪'), message:t('画布有未保存的修改，是否使用助手版本？'), confirmLabel:t('使用助手版本'), cancelLabel:t('保留我的版本')})) return false;
        if (payload.kind !== runtime.draft.kind) throw new Error(t('模板 kind 不可修改；请另存为副本'));
        const savedBase = base;
        runtime.edit({...runtime.draft,...payload});
        base = savedBase; changed();
        return true;
      },
      async load() { await templates.load(); if (templateId) await templates.openEditor(templateId); else templates.newTemplate(); return controller; },
      destroy() { abort.abort(); runtime?.view.close(); templates.destroy(); },
    };
    return controller;
  }
  window.LineageTemplateWorkshop = Object.freeze({mount});
})();
