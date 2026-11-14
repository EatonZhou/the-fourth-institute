(() => {
  'use strict';

  if (window.LineageTemplatesShelf) return;

  // The toolbox owns grouping, personal membership and card presentation.
  // Applying a dragged template continues through the existing application flow.
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
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['canvas', 'getContext', 'request', 'resolveTarget', 'shelf', 'toast']);

  function createShelf(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesShelf is missing: ${missing.join(', ')}`);
    const {
      MIME, apply, canvas, config, esc, getContext, openEditor, request, resolveTarget, shelf,
      t, targetLabels, templatesState, toast, promptText,
    } = dependencies;

    let loading = null;
    let favoriteIds = new Set();
    let favoritesError = '';
    let favoriteRevision = 0;
    let identity = 0;
    const pending = new Set();

    let dragged = '';

    const abort = new AbortController();
    const listen = (node, event, handler, capture = false) => node?.addEventListener(event, handler, { signal: abort.signal, capture });
    const report = error => toast(window.PortalI18n.message(error.message || error), 'error');
    const find = id => templatesState.templates.find(item => item.template_id === id) || null;
    const dropHint = document.createElement('div');
    dropHint.className = 'templateDropHint';
    dropHint.hidden = true;
    dropHint.setAttribute('role', 'status');
    document.body.append(dropHint);

    function render() {
      if (templatesState.disposed) return;
      if (templatesState.application && templatesState.application.lineageId !== getContext().lineage_id) templatesState.application.close();
      const expanded = new Set([...shelf.querySelectorAll('details[open]')].map(node => node.dataset.toolboxGroup || 'more'));
      const visible = templatesState.templates.filter(item => item.builtin_key || favoriteIds.has(item.template_id));
      shelf.innerHTML = GROUPS.map(group => {
        const rows = visible.filter(item => groupFor(item.kind) === group.key);
        const content = `<div class="templateToolboxCards">
          ${rows.map(item => {
            const hint = item.kind === 'prompt'
              ? t('拖到聊天输入框或 Resident 节点，填入草稿后编辑发送')
              : window.PortalI18n.text`${t(item.behavior?.persistent ? '施加后保留为 Function Block' : '一次性操作')} · 拖到 ${(item.targets || []).map(kind => targetLabels()[kind]).join(' / ')}`;
            const image = item.kind === 'structure' ? window.LineageTemplateGraph.thumbnail(item.body || {})
              : `<svg class="templateThumbnail templateModuleGlyph" viewBox="0 0 100 84" aria-hidden="true">${GLYPHS[item.kind] || GLYPHS.script}</svg>`;
            return `<div class="templateCard"><button type="button" class="templateTile" draggable="true" data-template-id="${esc(item.template_id)}" aria-label="${esc(t(item.title))}" title="${esc([t(item.title), t(item.description), hint].filter(Boolean).join('\n'))}">
              ${image}<span class="templateTileTitle">${esc(t(item.title))}</span></button>
              <button type="button" class="templateEdit" data-template-edit="${esc(item.template_id)}">${esc(t('在工坊中打开'))}</button></div>`;
          }).join('')}</div>`;
        const empty = group.key === 'roles' ? t('还没有角色模板') : t('暂无模板');
        const body = rows.length ? content : `<p class="templateEmpty">${esc(empty)}</p>`;
        return `<section class="templateToolboxGroup" data-toolbox-group="${group.key}"><h4>${esc(t(group.label))}</h4>${body}</section>`;
      }).join('') + `<details class="templateToolboxMore"${expanded.has('more') ? ' open' : ''}><summary>${esc(t('更多模板'))}</summary>
        ${templatesState.templates.filter(item => !item.builtin_key).map(item => `<div class="templateToolboxRow"><span>${esc(t(item.title))}</span><button class="portalButton is-compact" type="button" data-template-favorite="${esc(item.template_id)}" aria-pressed="${favoriteIds.has(item.template_id)}"${pending.has(item.template_id) ? ' disabled' : ''}>${esc(t(favoriteIds.has(item.template_id) ? '移出工具箱' : '加入工具箱'))}</button></div>`).join('') || `<p class="templateEmpty">${esc(t('暂无更多模板'))}</p>`}
        </details>` + (favoritesError ? `<p class="templateEmpty" role="status">${esc(t('工具箱收藏读取失败'))} <button type="button" class="portalButton is-compact" data-toolbox-retry>${esc(t('重试'))}</button></p>` : '');
    }

    function load() {
      if (loading) return loading;
      const owner = identity, revision = favoriteRevision;
      const requestPromise = Promise.all([request('ListTemplates', {}), window.PortalAPI.get('/api/resources/favorites')
        .catch(error => { if (owner === identity) favoritesError = error.message || String(error); return null; })]).then(([result, favorites]) => {
        if (templatesState.disposed || owner !== identity) return;
        if (favorites && revision === favoriteRevision) { favoriteIds = new Set((favorites.items || []).map(item => item.address)); favoritesError = ''; }
        templatesState.templates = result.templates || [];
        templatesState.kinds = result.kinds || [];
        render();
        config.onChange?.();
      }).finally(() => { if (loading === requestPromise) loading = null; });
      loading = requestPromise;
      return loading;
    }

    // This is the single transition to replace when the workshop is available.
    function openInWorkshop(id) { return openEditor(id); }

    listen(window, 'portal:resource-favorite-changed', event => {
      const {address, favorite} = event.detail || {};
      if (!address?.startsWith('template_')) return;
      favoriteRevision++;
      if (favorite) favoriteIds.add(address); else favoriteIds.delete(address);
      render();
    });
    listen(window, 'focus', () => { void load().catch(report); });
    listen(window, 'portal:identity-changed', () => {
      identity++; favoriteRevision++; loading = null; favoriteIds.clear(); pending.clear();
      templatesState.templates = []; render(); void load().catch(report);
    });

    function unsupported(template) {
      return `${t(template.title)} · ${t('请拖到支持的目标')}：${template.targets.map(kind => targetLabels()[kind]).join(' / ')}`;
    }

    function clearDrop() {
      dropHint.hidden = true;
      canvas.classList.remove('templateDropAllowed', 'templateDropForbidden');
      canvas.querySelectorAll('.templateDropTarget').forEach(node => node.classList.remove('templateDropTarget'));
    }

    listen(shelf, 'click', event => {
      const editor = event.target.closest('[data-template-edit]');
      if (editor) { void openInWorkshop(editor.dataset.templateEdit).catch(report); return; }
      if (event.target.closest('[data-toolbox-retry]')) { void load().catch(report); return; }
      const button = event.target.closest('[data-template-favorite]');
      if (!button || button.disabled) return;
      const id = button.dataset.templateFavorite;
      pending.add(id); render();
      void window.PortalResourceFavorites.set(id, !favoriteIds.has(id)).catch(report).finally(() => { pending.delete(id); if (!templatesState.disposed) render(); });
    });
    listen(shelf, 'dragstart', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      dragged = tile.dataset.templateId;
      event.dataTransfer.setData(MIME, dragged);
      const item = find(dragged);
      if (item?.kind === 'prompt') event.dataTransfer.setData('text/plain', promptText(item));
      event.dataTransfer.effectAllowed = 'copy';
    });
    listen(shelf, 'dragend', () => { dragged = ''; clearDrop(); });
    listen(canvas, 'dragover', event => {
      if (!dragged && !Array.from(event.dataTransfer?.types || []).includes(MIME)) return;
      event.preventDefault(); event.stopPropagation();
      const item = find(dragged);
      if (!item) return;
      const target = resolveTarget(event, item);
      const supported = item.targets.includes(target.kind);
      clearDrop();
      canvas.classList.add(supported ? 'templateDropAllowed' : 'templateDropForbidden');
      event.target.closest('.hatgNode')?.classList.add('templateDropTarget');
      event.dataTransfer.dropEffect = supported ? 'copy' : 'none';
      dropHint.textContent = supported ? `${t('施加到')} ${targetLabels()[target.kind]}` : `⊘ ${unsupported(item)}`;
      dropHint.dataset.allowed = String(supported);
      dropHint.style.left = `${Math.min(event.clientX + 16, window.innerWidth - 300)}px`;
      dropHint.style.top = `${Math.min(event.clientY + 16, window.innerHeight - 70)}px`;
      dropHint.hidden = false;
    }, true);
    listen(canvas, 'dragleave', event => { if (!canvas.contains(event.relatedTarget)) clearDrop(); }, true);
    listen(canvas, 'drop', event => {
      const id = event.dataTransfer?.getData(MIME) || dragged;
      if (!id) return;
      event.preventDefault(); event.stopPropagation();
      const target = resolveTarget(event, find(id));
      clearDrop(); dragged = '';
      apply(id, target).catch(report);
    }, true);

    return {
      abort, dropHint, find, load, render, unsupported,
    };
  }

  window.LineageTemplatesShelf = Object.freeze({ createShelf, groupFor, dependencies: DEPENDENCIES });
})();
