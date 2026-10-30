(() => {
  'use strict';

  if (window.LineageTemplatesShelf) return;

  // The template shelf: loading the templates and drawing their tiles by
  // group, opening a tile's editor, and dragging a tile onto the canvas,
  // with a hint that says whether the target under the pointer takes it.
  // It binds the shelf's and the canvas's listeners when it is created.

  const DEPENDENCIES = Object.freeze([
    'MIME', 'apply', 'canvas', 'categories', 'config', 'esc', 'getContext', 'openEditor', 'pigeonHtml', 'request',
    'resolveTarget', 'shelf', 't', 'targetLabels', 'templatesState', 'toast',
  ]);
  // Fields of mount's config, which a caller may leave out.
  const OPTIONAL = Object.freeze(['canvas', 'getContext', 'request', 'resolveTarget', 'shelf', 'toast']);

  function createShelf(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesShelf is missing: ${missing.join(', ')}`);
    const {
      MIME, apply, canvas, categories, config, esc, getContext, openEditor, pigeonHtml, request, resolveTarget, shelf,
      t, targetLabels, templatesState, toast,
    } = dependencies;

    let loading = null;

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
      shelf.innerHTML = Object.entries(categories()).map(([category, label]) => {
        const rows = templatesState.templates.filter(item => item.category === category);
        if (!rows.length) return '';
        return `<div class="templateCategory" role="group" aria-label="${esc(label)}" data-template-category="${category}"><span class="templateCategoryLabel" aria-hidden="true">${esc(label)}</span>
          ${rows.map(item => {
            const hint = window.PortalI18n.text`${t(item.behavior?.persistent ? '施加后保留为 Function Block' : '一次性操作')} · 拖到 ${item.targets.map(kind => targetLabels()[kind]).join(' / ')} · 点击编辑`;
            return `<button type="button" class="templateTile" draggable="true" data-template-id="${esc(item.template_id)}" aria-label="${esc(t(item.title))}" title="${esc([t(item.title), t(item.description), hint].filter(Boolean).join('\n'))}">
            ${pigeonHtml(item)}<span class="templateTileTitle">${esc(t(item.title))}</span></button>`;
          }).join('')}</div>`;
      }).join('');
      if (!templatesState.templates.length) shelf.insertAdjacentHTML('beforeend', `<p class="templateEmpty">${esc(t('暂无模板，点击 + 新建'))}</p>`);
    }

    function load() {
      if (loading) return loading;
      loading = request('ListTemplates', {}).then(result => {
        if (templatesState.disposed) return;
        templatesState.templates = result.templates || [];
        templatesState.kinds = result.kinds || [];
        render();
        config.onChange?.();
      }).finally(() => { loading = null; });
      return loading;
    }

    function unsupported(template) {
      return `${t(template.title)} · ${t('请拖到支持的目标')}：${template.targets.map(kind => targetLabels()[kind]).join(' / ')}`;
    }

    function clearDrop() {
      dropHint.hidden = true;
      canvas.classList.remove('templateDropAllowed', 'templateDropForbidden');
      canvas.querySelectorAll('.templateDropTarget').forEach(node => node.classList.remove('templateDropTarget'));
    }

    listen(shelf, 'click', event => {
      const tile = event.target.closest('[data-template-id]');
      if (tile) openEditor(tile.dataset.templateId).catch(report);
    });
    listen(shelf, 'dragstart', event => {
      const tile = event.target.closest('[data-template-id]');
      if (!tile) return;
      dragged = tile.dataset.templateId;
      event.dataTransfer.setData(MIME, dragged);
      event.dataTransfer.effectAllowed = 'copy';
    });
    listen(shelf, 'dragend', () => { dragged = ''; clearDrop(); });
    listen(canvas, 'dragover', event => {
      if (!dragged && !Array.from(event.dataTransfer?.types || []).includes(MIME)) return;
      event.preventDefault(); event.stopPropagation();
      const item = find(dragged);
      if (!item) return;
      const target = resolveTarget(event);
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
      const target = resolveTarget(event);
      clearDrop(); dragged = '';
      apply(id, target).catch(report);
    }, true);

    return {
      abort, dropHint, find, load, render, unsupported,
    };
  }

  window.LineageTemplatesShelf = Object.freeze({ createShelf, dependencies: DEPENDENCIES });
})();
