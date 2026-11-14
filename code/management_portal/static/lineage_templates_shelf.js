(function installToolbox() {
  'use strict';
  if (window.LineageTemplatesShelf) return;
  const GROUPS = Object.freeze([
    {key: 'duties', label: '职责', kinds: ['structure']},
    {key: 'roles', label: '角色', kinds: []},
    {key: 'organizations', label: '组织', kinds: ['familize']},
    {key: 'errands', label: '交办', kinds: ['prompt', 'explore', 'script']},
  ]);
  const groupFor = kind => GROUPS.find(group => group.kinds.includes(kind))?.key || 'errands';
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
  function createShelf(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesShelf is missing: ${missing.join(', ')}`);
    const {MIME, apply, canvas, config, esc, getContext, openEditor, request, resolveTarget,
      shelf, t, targetLabels, templatesState, toast, promptText} = dependencies;
    let loading = null, identity = 0, dragged = '', picked = null, pickupTimer = null;
    const username = () => window.PortalIdentity?.username() || '';
    const abort = new AbortController();
    const listen = (node, event, handler, capture = false) => node?.addEventListener(event, handler, {signal: abort.signal, capture});
    const report = error => toast(window.PortalI18n.message(error.message || error), 'error');
    const find = id => templatesState.templates.find(item => item.template_id === id) || null;
    const dropHint = document.createElement('div');
    dropHint.className = 'templateDropHint'; dropHint.hidden = true; dropHint.setAttribute('role', 'status');
    const ghost = document.createElement('div');
    ghost.className = 'templateCarryPreview'; ghost.hidden = true; ghost.setAttribute('aria-hidden', 'true');
    document.body.append(dropHint, ghost);
    function thumbnail(item) {
      return item.kind === 'structure' ? window.LineageTemplateGraph.thumbnail(item.body || {})
        : `<svg class="templateThumbnail templateModuleGlyph" viewBox="0 0 100 84" aria-hidden="true">${GLYPHS[item.kind] || GLYPHS.script}</svg>`;
    }
    function render() {
      if (templatesState.disposed) return;
      if (templatesState.application && templatesState.application.lineageId !== getContext().lineage_id) templatesState.application.close();
      if (picked && (picked.lineageId !== getContext().lineage_id || picked.user !== username())) cancelPlacement();
      const user = username();
      const visible = templatesState.templates.filter(item => item.builtin_key || (user && item.created_by === user));
      shelf.innerHTML = GROUPS.map(group => {
        const rows = visible.filter(item => groupFor(item.kind) === group.key);
        const cards = rows.map(item => `<button type="button" class="templateTile" draggable="true"
          data-template-id="${esc(item.template_id)}" aria-label="${esc(t(item.title))}"
          title="${esc([t(item.title), t(item.description), t('单击拿起，双击在工坊中打开；按 Escape 或右键取消')].filter(Boolean).join('\n'))}">
          ${thumbnail(item)}<span class="templateTileTitle">${esc(t(item.title))}</span></button>`).join('');
        return `<section class="templateToolboxGroup" data-toolbox-group="${group.key}"><h4>${esc(t(group.label))}</h4>${
          rows.length ? `<div class="templateToolboxCards">${cards}</div>`
            : `<p class="templateEmpty">${esc(t(group.key === 'roles' ? '还没有角色模板' : '暂无模板'))}</p>`}</section>`;
      }).join('');
    }
    function load() {
      if (loading) return loading;
      const owner = identity, user = username();
      const task = request('ListTemplates', {}).then(result => {
        if (templatesState.disposed || owner !== identity || user !== username()) return;
        templatesState.templates = result.templates || []; templatesState.kinds = result.kinds || [];
        render(); config.onChange?.();
      }).finally(() => { if (loading === task) loading = null; });
      loading = task;
      return loading;
    }
    function unsupported(item) {
      return `${t(item.title)} · ${t('请拖到支持的目标')}：${item.targets.map(kind => targetLabels()[kind]).join(' / ')}`;
    }
    function clearDrop() {
      dropHint.hidden = true;
      canvas?.classList.remove('templateDropAllowed', 'templateDropForbidden');
      canvas?.querySelectorAll('.templateDropTarget').forEach(node => node.classList.remove('templateDropTarget'));
    }
    function cancelPlacement() {
      clearTimeout(pickupTimer); pickupTimer = null; picked = null;
      ghost.hidden = true; canvas?.classList.remove('templatePicking'); clearDrop();
    }
    // One transition to replace when the workshop becomes available.
    function openInWorkshop(id) { cancelPlacement(); return openEditor(id); }
    function showTarget(event, item) {
      clearDrop();
      const target = resolveTarget(event, item);
      const supported = item.targets.includes(target.kind);
      canvas.classList.add(supported ? 'templateDropAllowed' : 'templateDropForbidden');
      event.target.closest('.hatgNode')?.classList.add('templateDropTarget');
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
      ghost.innerHTML = thumbnail(item); ghost.hidden = false;
      ghost.style.left = `${event.clientX || 20}px`; ghost.style.top = `${event.clientY || 20}px`;
      canvas.classList.add('templatePicking');
      shelf.dispatchEvent(new CustomEvent('portal:template-picked', {bubbles: true}));
    }
    function pickedItem() {
      if (picked && (picked.lineageId !== getContext().lineage_id || picked.user !== username())) cancelPlacement();
      return picked ? find(picked.id) : null;
    }
    listen(window, 'focus', () => { void load().catch(report); });
    listen(window, 'portal:identity-changed', () => {
      identity++; loading = null; dragged = ''; cancelPlacement();
      templatesState.templates = []; render(); void load().catch(report);
    });
    listen(shelf, 'click', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      event.preventDefault();
      clearTimeout(pickupTimer); pickupTimer = null;
      if (event.detail >= 2) return;
      // Keep the card in place until a possible second click can open its editor.
      pickupTimer = setTimeout(() => { pickupTimer = null; pickup(tile.dataset.templateId, event); }, 260);
    });
    listen(shelf, 'dblclick', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      event.preventDefault(); void openInWorkshop(tile.dataset.templateId).catch(report);
    });
    listen(shelf, 'keydown', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile || event.key !== 'Enter') return;
      event.preventDefault(); event.stopPropagation();
      if (event.shiftKey) void openInWorkshop(tile.dataset.templateId).catch(report);
      else pickup(tile.dataset.templateId, event);
    });
    listen(document, 'mousemove', event => {
      const item = pickedItem(); if (!item) return;
      ghost.style.left = `${event.clientX + 14}px`; ghost.style.top = `${event.clientY + 14}px`;
      if (canvas.contains(event.target)) showTarget(event, item); else clearDrop();
    }, true);
    listen(document, 'pointerdown', event => {
      if (!pickedItem() || !canvas.contains(event.target) || event.button !== 0) return;
      // Suppress compatibility mousedown so canvas/node drag handlers cannot
      // move the graph while the click is placing a template.
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    listen(document, 'click', event => {
      const item = pickedItem();
      if (!item || !canvas.contains(event.target) || event.button !== 0) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const {target, supported} = showTarget(event, item);
      if (!supported) return;
      const id = picked.id; cancelPlacement();
      void apply(id, target).catch(report);
    }, true);
    const cancel = event => {
      if (!picked && pickupTimer === null) return;
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation(); cancelPlacement();
    };
    listen(document, 'keydown', cancel, true);
    listen(document, 'contextmenu', cancel, true);
    listen(shelf, 'dragstart', event => {
      const tile = event.target.closest('[data-template-id]'); if (!tile) return;
      cancelPlacement(); dragged = tile.dataset.templateId;
      event.dataTransfer.setData(MIME, dragged);
      const item = find(dragged);
      if (item?.kind === 'prompt') event.dataTransfer.setData('text/plain', promptText(item));
      event.dataTransfer.effectAllowed = 'copy';
    });
    listen(shelf, 'dragend', () => { dragged = ''; clearDrop(); });
    listen(canvas, 'dragover', event => {
      if (!dragged && !Array.from(event.dataTransfer?.types || []).includes(MIME)) return;
      event.preventDefault(); event.stopPropagation();
      const item = find(dragged); if (!item) return;
      event.dataTransfer.dropEffect = showTarget(event, item).supported ? 'copy' : 'none';
    }, true);
    listen(canvas, 'dragleave', event => { if (!canvas.contains(event.relatedTarget)) clearDrop(); }, true);
    listen(canvas, 'drop', event => {
      const id = event.dataTransfer?.getData(MIME) || dragged; if (!id) return;
      event.preventDefault(); event.stopPropagation();
      const target = resolveTarget(event, find(id)); clearDrop(); dragged = '';
      void apply(id, target).catch(report);
    }, true);
    abort.signal.addEventListener('abort', () => { cancelPlacement(); ghost.remove(); });
    return {abort, dropHint, find, load, render, unsupported};
  }
  window.LineageTemplatesShelf = Object.freeze({createShelf, groupFor, dependencies: DEPENDENCIES});
})();
