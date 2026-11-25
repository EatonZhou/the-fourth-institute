(() => {
  'use strict';
  const t = text => window.PortalI18n.t(text);
  const esc = text => window.PortalUIPrimitives.escapeHtml(String(text ?? ''));
  const clone = value => JSON.parse(JSON.stringify(value));
  const request = (name, args, options) => window.PortalInstructionClient.execute(name, args, options);
  function mount({root, lineageId, templateId = '', storeId = '', execute = request, onSaved, onDeleted, onOpenWorkshop, onOpened}) {
    root.querySelectorAll('[id]').forEach(node => { node.dataset.workshopId ||= node.id; });
    const element = id => root.querySelector(`[data-workshop-id="${id}"],#${id}`);
    let form = element('workshopEditor');
    const canvas = element('workshopCanvas');
    const palette = element('workshopTools');
    const shelf = element('workshopShelf');
    const surface = window.TemplateWorkshopSurface.mount({root});
    const sessionId = window.PortalUIPrimitives.randomUUID();
    let runtime = null, templates = null, base = '', previewItem = null;
    let removed = false;
    let documentRevision = 0;
    let navigation = 0, editVersion = 0;
    let restoring = false, initialization = 'reset';
    const history = window.LineageTemplateWorkshopDraft.history(state => {
      root.querySelectorAll('[data-workshop-history]').forEach(button => {button.disabled = !state[button.dataset.workshopHistory] || !!runtime?.view.working;});
    });
    async function openTemplate(id) {
      if (runtime?.view.working) return;
      if (id === runtime?.draft.template_id && !previewItem) return;
      const serial = ++navigation;
      if (dirty() && !await window.PortalUI.confirm({title:t('切换模板？'), message:t('当前模板有未保存的修改，切换后将放弃这些修改。'), confirmLabel:t('放弃修改并切换'), cancelLabel:t('继续编辑')})) return;
      if (serial !== navigation || abort.signal.aborted) return;
      try {
        const previousVersion = editVersion;
        const result = await execute('GetTemplate',{template_id:id});
        if (serial !== navigation || abort.signal.aborted) return;
        if (editVersion !== previousVersion || runtime?.view.working) return;
        templateId = id; storeId = ''; previewItem = null; removed = false;
        documentRevision += 1; error(null); templates.edit(result.template);
        onOpened?.(result.template);
      } catch (exception) { if (serial === navigation) error(exception); }
    }
    const abort = new AbortController();
    const listen = (node, type, handler) => node.addEventListener(type, handler, {signal: abort.signal});
    const error = value => {
      const node = element('workshopError');
      node.hidden = !value;
      node.textContent = value ? window.PortalI18n.message(value.message || value) : '';
    };
    const dirty = () => {
      if (removed || !runtime) return false;
      // An unsaved template starts clean; only persisted draft content differs
      // from the baseline established when its editor becomes ready.
      try { return JSON.stringify(runtime.readDraft()) !== base; }
      catch (_) { return true; }
    };
    const changed = (group = '') => {
      if (!runtime) return;
      editVersion += 1;
      if (!restoring) history.record(runtime.snapshot(),group);
      element('workshopUnsaved').hidden = !dirty();
      root.dispatchEvent(new CustomEvent('workshop:draft-changed'));
    };
    root.addEventListener('workshop:review-effect',event => void runtime.view.busy(async () => {
      if (dirty()) throw new Error(t('请先 Save 保存草稿，再确认此版本'));
      const {effectId,draftHash,decision} = event.detail;
      const draft = runtime.draft.body.components.find(row => row.key === effectId)?.config?.script_draft;
      if (!draft) throw new Error(t('请重新读取候选脚本'));
      if (!await window.PortalUI.confirm({title:t(decision === 'confirm' ? '确认候选脚本？' : '取消脚本草稿？'),message:`${draft.description}\n\n${draft.script}\n\n${(draft.endpoints || []).join(', ')}`,confirmLabel:t(decision === 'confirm' ? '确认' : '取消草稿')})) return;
      const result = await execute('ReviewTemplateScript',{template_id:runtime.draft.template_id,effect_id:effectId,draft_hash:draftHash,decision},{expectedVersion:runtime.draft.version});
      runtime.edit(result.template); onSaved?.(result.template);
    }),{signal:abort.signal});
    async function save(payload, copy = false) {
      const id = runtime.draft.template_id;
      const own = !previewItem && (!id || (!runtime.draft.builtin_key && runtime.draft.created_by === window.PortalIdentity.username()));
      if (!copy && !own) throw new Error(t('请用 Save As 保存为自己的模板'));
      const name = id && !copy ? 'UpdateTemplate' : 'CreateTemplate';
      const args = name === 'UpdateTemplate' ? {template_id: id, template: payload} : {template: payload};
      await execute(name, {...args, dry_run: true});
      const result = await execute(name, args);
      if (copy) documentRevision += 1;
      templateId = result.template.template_id;
      storeId = ''; previewItem = null;
      onSaved?.(result.template);
      initialization = copy ? 'reset' : 'saved'; runtime.edit(result.template);
      window.dispatchEvent(new CustomEvent('portal:templates-changed',{detail:{template:result.template,sourceSession:sessionId}}));
      await templates.load();
      changed();
      return result.template;
    }
    const editorHost = {
      create(source) {
        runtime = null;
        const previous = form;
        form = previous.cloneNode(false); previous.replaceWith(form);
        form.innerHTML = `<header class="workshopBar">
          <input data-meta="title" aria-label="${esc(t('模板标题'))}" value="${esc(source.title)}" required>
          <button type="button" class="portalButton is-icon" data-workshop-store data-portal-icon="archive" aria-label="${esc(t('模板商店'))}" title="${esc(t('模板商店'))}"></button>
          <nav class="workshopViewGroup" aria-label="${esc(t('视图'))}" ${source.kind === 'structure' ? '' : 'hidden'}>${[['graph','图'],['list','列表'],['json','JSON']].map(([key,label])=>`<button type="button" class="portalButton is-compact" data-structure-view="${key}" aria-pressed="${key==='graph'}">${esc(t(label))}</button>`).join('')}</nav>
          <div class="workshopHistoryGroup" role="group" aria-label="${esc(t('草稿历史'))}">${['undo','redo'].map(key=>`<button type="button" class="portalButton is-icon" data-workshop-history="${key}" data-portal-icon="${key}" aria-label="${esc(t(key==='undo'?'撤销':'重做'))}" title="${esc(t(key==='undo'?'撤销':'重做'))}" disabled></button>`).join('')}</div>
          <footer aria-label="${esc(t('模板操作'))}"></footer></header><main class="workshopEditorContent"></main>`;
        const cleanups = [], view = {
          root: form, form, content: form.querySelector('main'), footer: form.querySelector('footer'), cleanups,
          workshop: true, error, onChange: changed, save,
          deleted() { removed = true; onDeleted?.(); },
          close() { cleanups.forEach(cleanup => cleanup()); form.replaceChildren(); },
          async busy(work) {
            if (view.working) return;
            view.working = true; error(null);
            view.root.querySelectorAll('[data-workshop-history]').forEach(button=>{button.disabled=true;});
            const buttons = [...view.footer.querySelectorAll('button')]; buttons.forEach(button => { button.disabled = true; });
            try { return await work(); } catch (exception) { error(exception); }
            finally {
              view.working = false; buttons.forEach(button => { button.disabled = false; });
              const saveButton = view.root.querySelector('[data-workshop-save]');
              if (saveButton && runtime) saveButton.disabled = !!previewItem || (!!runtime.draft.template_id && (!!runtime.draft.builtin_key || runtime.draft.created_by !== window.PortalIdentity.username()));
              const state = history.state();
              root.querySelectorAll('[data-workshop-history]').forEach(button=>{button.disabled=!state[button.dataset.workshopHistory];});
            }
          },
        };
        return view;
      },
      ready(value) {
        runtime = value;
        templates.render();
        root.dataset.templateKind = runtime.draft.kind;
        tools.setEnabled(runtime.draft.kind === 'structure');
        base = JSON.stringify(runtime.readDraft());
        const snapshot = runtime.snapshot();
        if (initialization === 'saved') history.saved(snapshot);
        else if (initialization !== 'keep') history.reset(snapshot);
        initialization = 'reset';
        const saveButton = form.querySelector('[data-workshop-save]');
        saveButton.disabled = !!previewItem || (!!runtime.draft.template_id && (!!runtime.draft.builtin_key || runtime.draft.created_by !== window.PortalIdentity.username()));
        element('workshopChatSave').disabled = saveButton.disabled;
        if (previewItem) {
          const install = document.createElement('button'); install.type = 'button'; install.className = 'portalButton'; install.textContent = t('安装'); install.dataset.workshopInstall = '';
          install.onclick = () => void runtime.view.busy(async () => {
            const saved = await window.PortalTemplateStore.installItem(previewItem,{request:execute});
            if (saved) { previewItem = null; storeId = ''; templateId = saved.template_id; runtime.edit(saved); onSaved?.(saved); }
          });
          runtime.view.footer.append(install);
        } else if (runtime.draft.template_id && !runtime.draft.builtin_key && runtime.draft.created_by === window.PortalIdentity.username()) {
          const publish = document.createElement('button'); publish.type = 'button'; publish.className = 'portalButton'; publish.dataset.workshopPublish = '';
          publish.textContent = t(runtime.draft.published_store?.active ? '发布新版本' : '发布到商店');
          publish.onclick = () => void runtime.view.busy(async () => {
            if (dirty()) { error(t('请先 Save 保存当前模板')); return; }
            const saved = await window.PortalTemplateStore.publish(runtime.draft,{request:execute});
            if (saved) runtime.edit(saved);
          });
          runtime.view.footer.append(publish);
        }
        changed();
      },
    };
    const authoringItems = window.LineageTemplateGraph.authoringItems();
    const tools = window.LineageTemplateGraph.mountTools({root:palette,items:authoringItems,onInsert:id=>{
      if (!runtime.editorState.graph) {error(t('请切换到图视图后插入')); return;}
      void insertTemplate(id,{kind:'canvas',...runtime.editorState.graph.insertionPosition()}).catch(error);
    }});
    async function insertTemplate(id, target) {
      if (runtime.draft.kind !== 'structure') throw new Error(t('当前编辑器不支持自由添加结构'));
      runtime.readDraft();
      const source = clone(templates.find(id));
      if (source.kind !== 'structure') throw new Error(t('此模板不支持拖入组合，请单击打开'));
      const incoming = source.body;
      if (id === 'workshop_fork') {
        const origin = runtime.draft.body.roles.find(row => row.key === runtime.editorState.graph.selected()) || runtime.draft.body.roles[0];
        if (!origin) throw new Error(t('先添加一个角色，Fork 才有来源'));
        incoming.roles[0].fork_of = origin.key; incoming.roles[0].fork_mode = 'full';
      }
      const key = window.LineageTemplateWorkshopDraft.appendTemplate(runtime.draft, source, target);
      if (id === 'workshop_fork') runtime.draft.body.roles.find(row => row.key === key).fork_of = incoming.roles[0].fork_of;
      runtime.renderParameters(); runtime.editorState.graph.select(key); runtime.schedulePreview();
    }
    templates = window.LineageTemplates.mount({
      shelf, canvas, request: execute, getContext: () => ({lineage_id: lineageId}),
      editorHost, toast: (message, tone) => { if (tone === 'error') error(message); },
      toolboxItems:authoringItems,palette,isComposition:true,iconOnly:true,
      selectedTemplate:() => runtime?.draft.template_id, collapsible:true,
      onSelectTemplate:openTemplate,
      acceptTemplate: item => runtime?.draft.kind === 'structure' && item.kind === 'structure',
      resolveTarget: event => ({kind:'canvas', ...runtime.editorState.graph.position(event)}),
      applyTemplate:insertTemplate,
    });
    listen(root, 'click', async event => {
      if (event.target.closest('[data-workshop-store]')) {
        window.PortalTemplateStore.open({request:execute,lineageId,onOpenWorkshop:resource => onOpenWorkshop?.(resource)});
      }
      const action = event.target.closest('[data-workshop-history]')?.dataset.workshopHistory;
      if (action && !runtime.view.working) {
        const value = history[action]();
        if (value) {restoring = true; try {runtime.restore(value);} finally {restoring = false;} changed();}
      }
    });
    listen(window, 'beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
    listen(window, 'portal:templates-changed', event => {
      if (event.detail?.sourceSession === sessionId) return;
      const saved = event.detail?.template;
      if (saved?.template_id === templateId && runtime && !dirty() && !runtime.view.working) runtime.edit(saved);
    });
    const controller = {
      sessionId, lineageId, dirty, openTemplate, documentKey: () => `${sessionId}:${documentRevision}`,
      fit: () => runtime?.editorState.graph?.fit(),
      save: () => runtime.view.busy(() => save(runtime.readDraft())), template: () => runtime?.draft,
      async acceptDraft(value) {
        const payload = window.LineageTemplateWorkshopDraft.payload({...runtime.draft,...value});
        await execute('CreateTemplate', {template:payload, dry_run:true});
        if (dirty() && !await window.PortalUI.confirm({title:t('助手草稿已就绪'), message:t('画布有未保存的修改，是否使用助手版本？'), confirmLabel:t('使用助手版本'), cancelLabel:t('保留我的版本')})) return false;
        if (payload.kind !== runtime.draft.kind) throw new Error(t('模板 kind 不可修改；请另存为副本'));
        const savedBase = base;
        initialization = 'keep'; runtime.edit({...runtime.draft,...payload});
        base = savedBase; changed();
        return true;
      },
      async load() {
        await templates.load();
        if (storeId) {
          previewItem = (await execute('ListTemplateStore',{store_id:storeId})).items[0];
          if (!previewItem) throw new Error(t('商店模板不存在或已下架'));
          templates.edit({...previewItem,builtin_key:'store_preview'});
        } else if (templateId) await templates.openEditor(templateId); else templates.newTemplate();
        return controller;
      },
      destroy() { surface.destroy(); tools.destroy(); abort.abort(); runtime?.view.close(); templates.destroy(); },
    };
    return controller;
  }
  window.LineageTemplateWorkshop = Object.freeze({mount});
})();
