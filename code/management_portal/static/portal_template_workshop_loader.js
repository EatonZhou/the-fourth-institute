(() => {
  'use strict';
  if (window.PortalTemplateWorkshopLoader) return;
  let loading = null, markup = null;
  async function load() {
    if (!loading) loading = (async () => {
      await window.PortalHatgLoader.load();
      for (const source of ['/static/lineage_templates.css','/static/lineage_function_blocks.css','/static/template_workshop.css',
        '/static/vendor/monaco/vs/nls/lang/zh-cn.js','/static/vendor/monaco/vs/loader.js','/static/portal_code_editor.js',
        '/static/lineage_template_workshop_draft.js','/static/lineage_template_workshop.js',
        '/static/lineage_duty_workshop_model.js','/static/lineage_duty_workshop.js','/static/lineage_template_workshop_chat.js']) {
        await window.PortalUIPrimitives.loadAsset(source);
      }
    })().catch(error => { loading = null; throw error; });
    return loading;
  }
  async function panelMarkup() {
    if (!markup) markup = fetch('/static/template_workshop_panel.html').then(response => {
      if (!response.ok) throw new Error(`Workshop HTTP ${response.status}`);
      return response.text();
    }).catch(error => { markup = null; throw error; });
    return markup;
  }
  async function openInWorkbench(workbench, resource = {}, sourcePanelId = '') {
    const panels = workbench.getPanelsByType('workshop');
    const same = panels.find(panel => resource.function_block_id
      ? panel.target().function_block_id === resource.function_block_id && panel.target().lineage_id === resource.lineage_id
      : resource.template_id ? panel.target().template_id === resource.template_id : resource.store_id && panel.target().store_id === resource.store_id);
    if (same) { workbench.focusPanel(same.id); return same; }
    const hidden = workbench.getState().hidden;
    const sibling = panels.find(panel => !hidden.includes(panel.id));
    return workbench.openResource({type:'workshop',mode:sibling ? 'new-tab' : 'side',side:'right',
      targetInstanceId:sibling?.id || sourcePanelId,resource:{...resource,kind:'workshop'}});
  }
  function create(options = {}) {
    const root = options.root, id = options.panelId || options.id || window.PortalUIPrimitives.randomUUID();
    let target = {...options.target}, workshop = null, chat = null, pending = null, destroyed = false;
    let visible = false, needsFit = true;
    root.classList.add('workshopPanelHost');
    async function mount() {
      if (pending) return pending;
      pending = (async () => {
        await load();
        const html = await panelMarkup();
        if (destroyed) return;
        root.innerHTML = html;
        const host = root.firstElementChild;
        host.querySelectorAll('[id]').forEach(node => { node.dataset.workshopId = node.id; node.id = `${id}-${node.id}`; });
        window.PortalI18n.renderStatic(host);
        if (root.getBoundingClientRect().width < 760) host.classList.add('workshopChatCollapsed');
        const factory = target.function_block_id ? window.LineageDutyWorkshop : window.LineageTemplateWorkshop;
        workshop = factory.mount({root:host,lineageId:target.lineage_id || '',functionBlockId:target.function_block_id || '',templateId:target.template_id || '',storeId:target.store_id || '',
          onOpenWorkshop:resource => {
            if (options.context) return options.emit?.('workshop-open',{resource});
            const url = new URL('/template-workshop',location.href);
            if (resource.template_id) url.searchParams.set('template',resource.template_id);
            if (resource.store_id) url.searchParams.set('store',resource.store_id);
            if (resource.lineage_id) url.searchParams.set('lineage',resource.lineage_id);
            if (resource.function_block_id) url.searchParams.set('duty',resource.function_block_id);
            location.assign(url);
          },
          onSaved: template => { target = {...target,store_id:'',...template}; options.emit?.('workshop-saved',target); },
          onOpened: template => { target = {...target,store_id:'',...template}; options.emit?.('workshop-saved',target); },
          onDeleted: () => {
            target = {...target,template_id:'',store_id:'',title:window.PortalI18n.t('模板工坊')};
            chat?.destroy(); workshop?.destroy(); chat = null; workshop = null; pending = null;
            root.replaceChildren();
            if (options.context) options.emit?.('workshop-deleted');
          },
          execute:options.execute});
        await workshop.load();
        if (destroyed) { workshop.destroy(); return; }
        target = {...target,title:workshop.template().title};
        options.emit?.('workshop-title',target);
        chat = window.LineageTemplateWorkshopChat.mount({root:host,workshop,execute:options.execute,listLineages:options.listLineages});
        await chat.load();
        if (destroyed) { chat.destroy(); return; }
        if (visible) { workshop.fit(); needsFit = false; }
      })().catch(error => { root.textContent = window.PortalI18n.message(error.message || error); throw error; });
      return pending;
    }
    async function show() {
      visible = true;
      await mount();
      if (needsFit && !destroyed) { workshop.fit(); needsFit = false; }
    }
    return {
      id,get ready() { return mount(); },target:() => ({...target}),
      connect() { if (!options.context) return show(); },
      show,
      hide() { visible = false; },
      disconnect() { visible = false; },
      async canClose() {
        try { await pending; } catch (_) { return true; }
        return !workshop?.dirty() || window.PortalUI.confirm({title:window.PortalI18n.t('关闭工坊？'),message:window.PortalI18n.t('有未保存的修改'),confirmLabel:window.PortalI18n.t('关闭')});
      },
      async openResource(resource) {
        return {accepted:resource.lineage_id === target.lineage_id && ['template_id','store_id','function_block_id'].every(key => (resource[key] || '') === (target[key] || ''))};
      },
      snapshot:() => ({...target,dirty:workshop?.dirty() || false}),
      restore(value) { if (!pending) target = {...target,...value}; },
      destroy() { destroyed = true; chat?.destroy(); workshop?.destroy(); root.replaceChildren(); },
    };
  }
  const panelPackage = {
    type:'workshop',version:1,component:'PortalTemplateWorkshopLoader',presentation:{title:'模板工坊',icon:'component'},
    sizing:{minWidth:320,minHeight:240},instances:'multiple',capabilities:['template-edit','snapshot','restore'],
    behavior:{canHide:true,canCollapse:true,canMove:true,closable:true},context:{channels:[]},
    commands:[{id:'open-template',title:window.PortalI18n.t('打开模板'),capability:'template-edit',execute:({panel,args}) => panel.openResource(args?.resource || args || {})}],
    persistence:{schemaVersion:1},create,
  };
  window.PortalTemplateWorkshopLoader = Object.freeze({load,create,openInWorkbench,panelPackage});
  window.PortalWorkbench.registerPanelPackage(panelPackage);
})();
