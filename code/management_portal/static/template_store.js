(() => {
  'use strict';
  if (window.PortalTemplateStore) return;
  const t = value => window.PortalI18n.t(value);
  const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ''));
  const execute = (name, args) => window.PortalInstructionClient.execute(name, args);
  const changed = template => window.dispatchEvent(new CustomEvent('portal:templates-changed', {detail:{template}}));
  function dialog(title, content, className = '') {
    const root = document.createElement('dialog');
    root.className = `templateStore ${className}`; root.dataset.storeSkin = 'default';
    root.innerHTML = `<header class="templateStoreHeader"><strong>${esc(t(title))}</strong><button type="button" class="portalButton is-icon" data-store-close aria-label="${esc(t('关闭'))}">×</button></header>${content}`;
    document.body.append(root);
    const controller = window.PortalUI.createDialogController({modal:root, escapeClose:true, backdropClose:true, onClose() { controller.destroy(); root.remove(); }});
    root.querySelector('[data-store-close]').onclick = () => controller.close();
    controller.open();
    return {root, close:() => controller.close()};
  }
  async function conflictMode() {
    return new Promise(resolve => {
      const view = dialog('该模板有本地修改', `<div class="templateStoreChoices">${[['overwrite','覆盖为商店新版'],['copy','另存新版副本（保留我的修改）'],['cancel','取消']].map(([key,label]) => `<button type="button" class="portalButton" data-choice="${key}">${esc(t(label))}</button>`).join('')}</div>`, 'templateStoreSmall');
      view.root.addEventListener('close', () => resolve(null), {once:true});
      view.root.querySelectorAll('[data-choice]').forEach(button => { button.onclick = () => { resolve(button.dataset.choice === 'cancel' ? null : button.dataset.choice); view.close(); }; });
    });
  }
  async function installItem(item, {request = execute} = {}) {
    let mode = item.installed_template_id ? 'overwrite' : 'install';
    if (item.installed_template_id && item.locally_modified) mode = await conflictMode();
    if (!mode) return null;
    const instructions = {install:'InstallStoreTemplate', overwrite:'UpdateInstalledTemplate', copy:'InstallStoreTemplateAsNew'};
    const result = await request(instructions[mode], {
      store_id:item.store_id,
      ...(mode === 'install' ? {} : {target_template_id:item.installed_template_id}),
    });
    changed(result.template);
    window.PortalUI.toast(`${t(mode === 'install' ? '已安装' : '已更新到')} v${result.template.installed_store_version}`, 'success');
    return result.template;
  }
  async function updateTemplate(template, options) {
    const state = template.store_status;
    return installItem({...state, installed_template_id:template.template_id}, options);
  }
  async function publish(template, {request = execute} = {}) {
    const note = await window.PortalUI.prompt({title:t('发布到商店'),label:t('版本说明（可选）'),placeholder:t('版本说明（可选）'),confirmLabel:t('发布')});
    if (note === null) return null;
    const result = await request('PublishTemplate',{template_id:template.template_id,release_note:note});
    changed(result.template); window.PortalUI.toast(`${t('已发布')} v${result.item.version}`, 'success');
    return result.template;
  }
  function open({request = execute, onOpenWorkshop, lineageId = ''} = {}) {
    const view = dialog('模板商店', `<div class="templateStoreFilters"><input type="search" data-store-search aria-label="${esc(t('搜索模板'))}" placeholder="${esc(t('搜索模板'))}"><div class="templateStoreGroups">${[['','全部'],['duty','职责'],['role','角色'],['organization','组织'],['task','交办']].map(([key,label]) => `<button type="button" class="portalButton is-compact" data-store-group="${key}" aria-pressed="${!key}">${esc(t(label))}</button>`).join('')}</div></div><p class="templateStoreStatus" role="status"></p><div class="templateStoreBody"><div class="templateStoreGrid"></div><section class="templateStoreDetails" hidden></section></div>`);
    let rows = [], selected = '', group = '', busy = false, closed = false;
    const grid = view.root.querySelector('.templateStoreGrid'), details = view.root.querySelector('.templateStoreDetails');
    const search = view.root.querySelector('[data-store-search]'), status = view.root.querySelector('[role=status]');
    view.root.addEventListener('close', () => { closed = true; }, {once:true});
    const sourceOwn = item => item.is_publisher && item.source_available && !item.installed_template_id;
    const action = item => sourceOwn(item) ? '我的模板' : !item.installed_template_id ? '安装' : item.has_update ? '更新' : '已安装';
    const button = item => `<button type="button" class="portalButton is-compact is-primary" data-store-install="${esc(item.store_id)}" ${busy || sourceOwn(item) || (item.installed_template_id && !item.has_update) ? 'disabled' : ''}>${esc(t(action(item)))}</button>`;
    const meta = item => `${item.is_publisher ? t('我发布') : item.publisher_title || t('发布者')} · v${item.version}`;
    function render() {
      if (closed) return;
      const query = search.value.toLocaleLowerCase();
      const matches=rows.filter(item => [item.title,item.description,item.publisher_title,item.publisher].join(' ').toLocaleLowerCase().includes(query));
      const visible = matches.filter(item => !group || window.LineageTemplatesShelf.storeGroupFor(item) === group);
      view.root.querySelectorAll('[data-store-group]').forEach(node=>{const key=node.dataset.storeGroup;node.textContent=`${t({'':'全部',duty:'职责',role:'角色',organization:'组织',task:'交办'}[key])} ${matches.filter(item=>!key || window.LineageTemplatesShelf.storeGroupFor(item)===key).length}`;});
      grid.innerHTML = visible.map(item => `<article class="templateStoreCard ${selected===item.store_id?'is-selected':''}" data-store-id="${esc(item.store_id)}"><button type="button" class="templateStoreCardOpen" data-store-select="${esc(item.store_id)}" aria-pressed="${selected===item.store_id}">${window.LineageTemplatesShelf.thumbnail(item)}<strong>${esc(item.title)}</strong><small>${esc(meta(item))}</small></button>${button(item)}</article>`).join('');
      status.textContent = visible.length ? '' : t(query ? '没有匹配的模板' : group ? '此分组暂无模板' : '暂无上架模板');
      const item = rows.find(row => row.store_id === selected);
      details.hidden = !item; view.root.classList.toggle('has-detail', !!item);
      details.innerHTML = item ? `<button type="button" class="portalButton is-compact" data-store-back>${esc(t('返回'))}</button><h3>${esc(item.title)}</h3><small>${esc(meta(item))}</small>${item.release_note ? `<h4>${esc(t('版本说明'))}</h4><p>${esc(item.release_note)}</p>` : ''}${item.description ? `<h4>${esc(t('模板说明'))}</h4><p>${esc(item.description)}</p>` : ''}<h4>${esc(t('施加时调用的指令'))}</h4><div class="templateStoreChips">${item.instructions_used.map(name => `<code>${esc(name)}</code>`).join('')}</div>${item.application_parameters.length ? `<h4>${esc(t('参数'))}</h4><div class="templateStoreChips">${item.application_parameters.map(parameter => `<span>${esc(parameter.name)}</span>`).join('')}</div>` : ''}<footer class="templateStoreActions">${button(item)}<button type="button" class="portalButton" data-store-workshop>${esc(t('在工坊中打开'))}</button>${item.can_unpublish ? `<button type="button" class="portalButton is-danger" data-store-unpublish>${esc(t('下架'))}</button>` : ''}</footer>` : '';
    }
    async function load() { rows = (await request('ListTemplateStore',{})).items; render(); }
    search.oninput = render;
    view.root.addEventListener('click', async event => {
      const target = event.target.closest('button'); if (!target) return;
      if (target.hasAttribute('data-store-group')) { group = target.dataset.storeGroup; view.root.querySelectorAll('[data-store-group]').forEach(node => node.setAttribute('aria-pressed', String(node === target))); render(); }
      if (target.dataset.storeSelect) { selected = target.dataset.storeSelect; render(); }
      if (target.hasAttribute('data-store-back')) { selected = ''; render(); }
      const item = rows.find(row => row.store_id === (target.dataset.storeInstall || selected));
      if (!item || busy) return;
      try {
        if (target.dataset.storeInstall) {
          busy = true; window.PortalUI.setButtonBusy(target,true);
          try { await installItem(item,{request}); await load(); } finally { busy = false; render(); }
        }
        if (target.hasAttribute('data-store-unpublish') && await window.PortalUI.confirm({title:t('下架模板？'),message:item.title,confirmLabel:t('下架')})) {
          await request('UnpublishTemplate',{store_id:item.store_id}); changed(); selected = ''; await load();
        }
        if (target.hasAttribute('data-store-workshop')) {
          const templateId = item.is_publisher && item.source_available ? item.source_template_id : item.installed_template_id && !item.has_update ? item.installed_template_id : '';
          view.close();
          await onOpenWorkshop({kind:'workshop',lineage_id:lineageId,...(templateId ? {template_id:templateId} : {store_id:item.store_id}),title:item.title});
        }
      } catch (error) { window.PortalUI.toast(window.PortalI18n.message(error.message || error),'error'); }
    });
    void load().catch(error => { status.textContent = window.PortalI18n.message(error.message || error); });
    return view;
  }
  window.PortalTemplateStore = Object.freeze({open,publish,updateTemplate,installItem});
})();
