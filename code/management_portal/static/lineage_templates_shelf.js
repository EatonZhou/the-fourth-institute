(function installToolbox() {
  'use strict';
  if (window.LineageTemplatesShelf) return;
  const GROUPS = Object.freeze([
    {key: 'duties', label: '职责', kinds: ['structure', 'family_duty']},
    {key: 'roles', label: '角色', kinds: []},
    {key: 'organizations', label: '组织', kinds: ['familize']},
    {key: 'errands', label: '交办', kinds: ['prompt', 'explore', 'script']},
  ]);
  const groupFor = kind => GROUPS.find(group => group.kinds.includes(kind))?.key || 'errands';
  const storeGroupFor = kind => ({duties:'duty',roles:'role',organizations:'organization',errands:'task'})[groupFor(kind)];
  const GLYPHS = Object.freeze({
    prompt: '<path d="M24 20h52v32H48L32 66V52h-8z"/>',
    familize: '<circle cx="50" cy="26" r="9"/><circle cx="28" cy="60" r="9"/><circle cx="72" cy="60" r="9"/><path d="M50 35v10H28v6m22-6h22v6"/>',
    explore: '<circle cx="44" cy="36" r="18"/><path d="m57 49 20 20M44 26v20M34 36h20"/>',
    script: '<path d="m34 22-20 20 20 20m32-40 20 20-20 20M57 16 43 68"/>',
  });
  const DEPENDENCIES = Object.freeze([
    'MIME', 'apply', 'canvas', 'config', 'esc', 'getContext', 'openEditor', 'request',
    'resolveTarget', 'shelf', 't', 'targetLabels', 'templatesState', 'toast', 'promptText',
  ]);
  const OPTIONAL = Object.freeze(['canvas', 'getContext', 'request', 'resolveTarget', 'shelf', 'toast']);
  function thumbnail(item) {
    return item.kind === 'structure' ? window.LineageTemplateGraph.thumbnail(item.body || {})
      : `<svg class="templateThumbnail templateModuleGlyph" viewBox="0 0 100 84" aria-hidden="true">${GLYPHS[item.kind] || GLYPHS.script}</svg>`;
  }
  // One catalog per browser surface: every shelf observes the same revision,
  // identity transition and mutation refresh, while retaining its own actions.
  const subscribers = new Set();
  let dragSession=null;
  let catalog = {templates:[], kinds:[]}, pending = null, epoch = 0, catalogAbort = null;
  const catalogUser = () => window.PortalIdentity?.username() || '';
  function publishCatalog() { subscribers.forEach(subscriber => subscriber.receive(catalog)); }
  function refreshCatalog() {
    if (pending) return pending;
    const reader = subscribers.values().next().value;
    if (!reader) return Promise.resolve();
    const owner = epoch, user = catalogUser();
    const task = reader.request('ListTemplates', {}).then(result => {
      if (epoch !== owner || catalogUser() !== user) return;
      catalog = {templates:result.templates || [], kinds:result.kinds || []}; publishCatalog();
    }).finally(() => { if (pending === task) pending = null; });
    pending = task; return task;
  }
  function subscribeCatalog(subscriber) {
    subscribers.add(subscriber); subscriber.receive(catalog);
    if (!catalogAbort) {
      catalogAbort = new AbortController();
      const refresh = () => { void refreshCatalog().catch(error => subscribers.forEach(item => item.report(error))); };
      window.addEventListener('focus',refresh,{signal:catalogAbort.signal});
      window.addEventListener('portal:templates-changed',() => { epoch++; pending = null; refresh(); },{signal:catalogAbort.signal});
      window.addEventListener('portal:identity-changed',() => {
        epoch++; pending = null; catalog = {templates:[],kinds:[]}; publishCatalog(); refresh();
      },{signal:catalogAbort.signal});
    }
    return () => {
      subscribers.delete(subscriber);
      if (!subscribers.size) { catalogAbort.abort(); catalogAbort = null; epoch++; pending = null; catalog = {templates:[],kinds:[]}; }
    };
  }
  function createShelf(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesShelf is missing: ${missing.join(', ')}`);
    const {MIME, apply, canvas, config, esc, getContext, openEditor, request, resolveTarget,
      shelf, t, targetLabels, templatesState, toast, promptText} = dependencies;
    let dragged = '', picked = null, pickupTimer = null;
    let preview=null, previewId='', lastPointer=null, projectionEpoch=0;
    const projections=new Map();
    const collapsed = new Set();
    const username = () => window.PortalIdentity?.username() || '';
    const abort = new AbortController();
    const onCanvas = target => canvas?.contains(target) && (!config.isComposition || !!target?.closest('.templateGraphCanvas'));
    const listen = (node, event, handler, capture = false) => node?.addEventListener(event, handler, {signal: abort.signal, capture});
    const report = error => toast(window.PortalI18n.message(error.message || error), 'error');
    const find = id => templatesState.templates.find(item => item.template_id === id) || config.toolboxItems?.find(item => item.template_id === id) || null;
    const dropHint = document.createElement('div');
    dropHint.className = 'templateDropHint'; dropHint.hidden = true; dropHint.setAttribute('role', 'status');
    const ghost = document.createElement('div');
    ghost.className = 'templateCarryPreview'; ghost.hidden = true; ghost.setAttribute('aria-hidden', 'true');
    document.body.append(dropHint, ghost);
    function render() {
      if (templatesState.disposed) return;
      if (templatesState.application && templatesState.application.lineageId !== getContext().lineage_id) templatesState.application.close();
      if (picked && (picked.lineageId !== getContext().lineage_id || picked.user !== username())) cancelPlacement();
      if(dragSession?.owner===shelf && !dragSession.valid()) {
        cancelPlacement();window.dispatchEvent(new Event('portal:template-drag-ended'));
      }
      const user = username();
      const visible = visibleTemplates(templatesState.templates, user);
      shelf.classList.toggle('templateShelfIcons', !!config.iconOnly);
      shelf.innerHTML = GROUPS.map(group => {
        const rows = visible.filter(item => (item.toolbox_group || groupFor(item.kind)) === group.key);
        const cards = rows.map(item => `<div class="templateTileWrap"><button type="button" class="templateTile" draggable="true"
          data-template-id="${esc(item.template_id)}" aria-label="${esc(t(item.title))}"
          aria-current="${item.template_id === config.selectedTemplate?.()}"
          data-portal-tooltip="${esc([t(item.title),t(item.description)].filter(Boolean).join('\n'))}"
          title="${esc([t(item.title), t(item.description), t(config.onSelectTemplate ? '单击打开；拖入画布组合' : '单击拿起，双击在工坊中打开；按 Escape 或右键取消')].filter(Boolean).join('\n'))}">
          ${thumbnail(item)}<span class="templateTileTitle">${esc(t(item.title))}</span></button>${item.store_status?.has_update ? `<button type="button" class="templateUpdateBadge portalButton is-compact" data-store-update="${esc(item.template_id)}" aria-label="${esc(t('有更新'))}">${esc(config.iconOnly ? '↑' : t('有更新'))}</button>` : ''}</div>`).join('');
        return `<section class="templateToolboxGroup" data-toolbox-group="${group.key}"><h4>${config.collapsible ? `<button type="button" class="portalButton is-quiet is-compact" data-toolbox-collapse="${group.key}" aria-expanded="${!collapsed.has(group.key)}"><span data-portal-icon="chevron-${collapsed.has(group.key) ? 'right' : 'down'}" aria-hidden="true"></span>${esc(t(group.label))}</button>` : esc(t(group.label))}</h4>${
          rows.length ? `<div class="templateToolboxCards" ${collapsed.has(group.key) ? 'hidden' : ''}>${cards}</div>`
            : config.iconOnly ? '' : `<p class="templateEmpty">${esc(t(group.key === 'roles' ? '还没有角色模板' : '暂无模板'))}</p>`}</section>`;
      }).join('');
    }
    function load() {
      return refreshCatalog();
    }
    function unsupported(item) {
      return `${t(item.title)} · ${t('请拖到支持的目标')}：${item.targets.map(kind => targetLabels()[kind]).join(' / ')}`;
    }
    function clearDrop() {
      dropHint.hidden = true;
      canvas?.classList.remove('templateDropAllowed', 'templateDropForbidden');
      canvas?.querySelectorAll('.templateDropTarget').forEach(node => node.classList.remove('templateDropTarget'));
    }
    function clearPreview() {preview?.destroy();preview=null;previewId='';lastPointer=null;}
    function cancelPlacement() {
      if(dragSession?.owner===shelf)dragSession.cancelled=true;
      clearTimeout(pickupTimer); pickupTimer = null; picked = null; dragged='';
      for(const [key,value] of projections)if(value===null)projections.delete(key);
      projectionEpoch++;clearPreview();
      ghost.hidden = true; canvas?.classList.remove('templatePicking'); clearDrop();
    }
    // Saved recipes open in the workshop; palette entries are local draft elements.
    function openInWorkshop(id) {
      cancelPlacement();
      if (find(id)?.toolbox_group) return Promise.resolve();
      return openEditor(id);
    }
    function showTarget(event, item) {
      clearDrop();
      lastPointer={clientX:event.clientX,clientY:event.clientY,target:event.target};
      let body=item.kind==='structure' ? item.body : projections.get(item.template_id);
      if(!body && ['familize','family_duty','role'].includes(item.kind) && !projections.has(item.template_id)) {
        projections.set(item.template_id,null);
        const epoch=projectionEpoch,user=username(),lineageId=getContext().lineage_id;
        void request('GetTemplate',{template_id:item.template_id}).then(result=>{
          if(epoch!==projectionEpoch||user!==username()||lineageId!==getContext().lineage_id)return;
          projections.set(item.template_id,result.template.editor?.graph?.body || false);
          if(lastPointer)showTarget(lastPointer,item);
        }).catch(error=>{if(epoch===projectionEpoch)report(error);});
      }
      const view=config.placementView?.();
      if(body && view?.controller) {
        if(previewId!==item.template_id){clearPreview();preview=window.LineageTemplateGraph.placementPreview(view,body,event=>showTarget(event,item));previewId=item.template_id;}
      }
      const placement=preview?.move(event);
      const target = {...resolveTarget(placement?.event || event, item),...(placement ? {x:placement.x,y:placement.y} : {})};
      const needsBinding=!config.isComposition && body?.roles?.some(row=>row.from==='target');
      const supported = (!needsBinding || !!target.resident_ids?.length)
        && (config.acceptTemplate ? config.acceptTemplate(item, target) : item.targets.includes(target.kind));
      canvas.classList.add(supported ? 'templateDropAllowed' : 'templateDropForbidden');
      const highlighted=placement ? placement.hit : event.target.closest('.hatgNode');
      highlighted?.classList.add('templateDropTarget');
      dropHint.textContent = supported ? `${t('施加到')} ${targetLabels()[target.kind]}` : `⊘ ${unsupported(item)}`;
      dropHint.dataset.allowed = String(supported);
      dropHint.style.left = `${Math.max(0, Math.min(event.clientX + 18, window.innerWidth - 300))}px`;
      dropHint.style.top = `${Math.max(0, Math.min(event.clientY + 76, window.innerHeight - 70))}px`;
      dropHint.hidden = false;
      return {target, supported};
    }
    function pickup(id, event) {
      const item = find(id);
      if (!item || templatesState.disposed) return;
      cancelPlacement();
      picked = {id, lineageId: getContext().lineage_id, user: username()};
      ghost.innerHTML = thumbnail(item); ghost.hidden = Boolean(config.placementView && item.kind !== 'prompt');
      ghost.style.left = `${event.clientX || 20}px`; ghost.style.top = `${event.clientY || 20}px`;
      canvas.classList.add('templatePicking');
      shelf.dispatchEvent(new CustomEvent('portal:template-picked', {bubbles: true}));
    }
    function pickedItem() {
      if (picked && (picked.lineageId !== getContext().lineage_id || picked.user !== username())) cancelPlacement();
      return picked ? find(picked.id) : null;
    }
    const unsubscribe = subscribeCatalog({request,report,receive(value) {
      if (templatesState.disposed) return;
      dragged = ''; cancelPlacement();
      projections.clear();
      templatesState.templates = value.templates; templatesState.kinds = value.kinds;
      render(); config.onChange?.(value.templates);
    }});
    abort.signal.addEventListener('abort',unsubscribe);
    listen(shelf, 'click', event => {
      const toggle = event.target.closest('[data-toolbox-collapse]');
      if (toggle) {
        const key = toggle.dataset.toolboxCollapse, hide = !collapsed.has(key);
        if (hide) collapsed.add(key); else collapsed.delete(key);
        toggle.setAttribute('aria-expanded',String(!hide));
        toggle.querySelector('[data-portal-icon]').dataset.portalIcon = hide ? 'chevron-right' : 'chevron-down';
        const cards = toggle.closest('section').querySelector('.templateToolboxCards'); if (cards) cards.hidden = hide;
        return;
      }
      const update = event.target.closest('[data-store-update]');
      if (update) {
        event.preventDefault(); event.stopPropagation(); cancelPlacement();
        void window.PortalTemplateStore.updateTemplate(find(update.dataset.storeUpdate), {request}).catch(report); return;
      }
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      event.preventDefault();
      clearTimeout(pickupTimer); pickupTimer = null;
      if (config.onSelectTemplate) { if (event.detail < 2) void Promise.resolve(config.onSelectTemplate(tile.dataset.templateId)).catch(report); return; }
      if (event.detail >= 2) return;
      // Keep the card in place until a possible second click can open its editor.
      pickupTimer = setTimeout(() => { pickupTimer = null; pickup(tile.dataset.templateId, event); }, 260);
    });
    listen(shelf, 'dblclick', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      event.preventDefault(); if (!config.onSelectTemplate) void openInWorkshop(tile.dataset.templateId).catch(report);
    });
    listen(shelf, 'keydown', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile || !['Enter',' '].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (config.onSelectTemplate) void Promise.resolve(config.onSelectTemplate(tile.dataset.templateId)).catch(report);
      else if (event.shiftKey) void openInWorkshop(tile.dataset.templateId).catch(report);
      else pickup(tile.dataset.templateId, event);
    });
    listen(document, 'mousemove', event => {
      const item = pickedItem(); if (!item) return;
      ghost.style.left = `${event.clientX + 14}px`; ghost.style.top = `${event.clientY + 14}px`;
      if (onCanvas(event.target)) showTarget(event, item); else {clearDrop();clearPreview();}
    }, true);
    listen(document, 'pointerdown', event => {
      if (!pickedItem() || !onCanvas(event.target) || event.button !== 0) return;
      // Suppress compatibility mousedown so canvas/node drag handlers cannot
      // move the graph while the click is placing a template.
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    listen(document, 'click', event => {
      const item = pickedItem();
      if (!item || !onCanvas(event.target) || event.button !== 0) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const {target, supported} = showTarget(event, item);
      if (!supported) return;
      const id = picked.id; cancelPlacement();
      void apply(id, target).catch(report);
    }, true);
    const cancel = event => {
      if (!picked && !dragged && pickupTimer === null) return;
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation(); cancelPlacement();
    };
    listen(document, 'keydown', cancel, true);
    listen(document, 'contextmenu', cancel, true);
    listen(window,'portal:template-drag-ended',()=>{clearDrop();clearPreview();});
    for (const source of [shelf, config.palette].filter(Boolean)) listen(source, 'dragstart', event => {
      const tile = event.target.closest('[data-template-id]'); if (!tile) return;
      cancelPlacement(); dragged = tile.dataset.templateId;
      const user=username(),lineageId=getContext().lineage_id;
      dragSession={id:dragged,owner:shelf,cancelled:false,valid:()=>user===username() && lineageId===getContext().lineage_id && !templatesState.disposed};
      event.dataTransfer.setData(MIME, dragged);
      const item = find(dragged);
      if (item?.kind === 'prompt') event.dataTransfer.setData('text/plain', promptText(item));
      event.dataTransfer.effectAllowed = 'copy';
      if(config.placementView && item?.kind!=='prompt') {
        const image=document.createElement('canvas');image.width=image.height=1;
        event.dataTransfer.setDragImage(image,0,0);
      }
    });
    for (const source of [shelf, config.palette].filter(Boolean)) listen(source, 'dragend', () => {
      dragged = ''; cancelPlacement();dragSession=null;window.dispatchEvent(new Event('portal:template-drag-ended'));
    });
    listen(canvas, 'dragover', event => {
      if (!onCanvas(event.target)) return;
      if (!dragged && !Array.from(event.dataTransfer?.types || []).includes(MIME)) return;
      event.preventDefault(); event.stopPropagation();
      if(dragSession && (dragSession.cancelled || !dragSession.valid())){clearDrop();clearPreview();return;}
      const item = find(dragged || dragSession?.id); if (!item) return;
      event.dataTransfer.dropEffect = showTarget(event, item).supported ? 'copy' : 'none';
    }, true);
    listen(canvas, 'dragleave', event => { if (!canvas.contains(event.relatedTarget)) {clearDrop();clearPreview();} }, true);
    listen(canvas, 'drop', event => {
      if (!onCanvas(event.target)) return;
      const id = event.dataTransfer?.getData(MIME) || dragged; if (!id) return;
      event.preventDefault(); event.stopPropagation();
      if(dragSession && (dragSession.cancelled || !dragSession.valid())){clearDrop();clearPreview();return;}
      const item = find(id); if (!item) return;
      const {target,supported} = showTarget(event,item); cancelPlacement(); dragged = '';
      if (!supported) { report(new Error(unsupported(item))); return; }
      void apply(id, target).catch(report);
    }, true);
    abort.signal.addEventListener('abort', () => {
      cancelPlacement();ghost.remove();
      if(dragSession?.owner===shelf) {
        dragSession={id:dragSession.id,owner:null,cancelled:true,valid:()=>false};
        window.dispatchEvent(new Event('portal:template-drag-ended'));
      }
    });
    return {abort, dropHint, find, load, render, unsupported};
  }
  const visibleTemplates = (items, user) => items.filter(item => !item.toolbox_group && item.builtin_key !== 'workshop_assistant' && (item.builtin_key || (user && item.created_by === user)));
  window.LineageTemplatesShelf = Object.freeze({createShelf, visibleTemplates, groupFor, storeGroupFor, thumbnail, dependencies: DEPENDENCIES});
})();
